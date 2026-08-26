import { createHash, randomBytes } from "node:crypto";
import { once } from "node:events";
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import net from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";

import { schnorr, secp256k1 } from "@noble/curves/secp256k1.js";

const PRIMITIVES_MODULE =
  "aver:user/cap-n446f6d61696e2e5072696d697469766573-c3354d53ba72a2929c13c737ebe90ec182fed1f7fa4db4a50a99910f10c82678b";
const KV_MODULE =
  "aver:user/cap-n496e6672612e4b76-cb502cc65850614dfd4e4950b3493eb857d456d7cfbad23279512ada5fdfdfc32";
const REGTEST_MAGIC = Buffer.from([0xfa, 0xbf, 0xb5, 0xda]);
const TCP_BODY_LIMIT = 10 * 1024 * 1024;

const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });
const scratch = mkdtempSync(path.join(tmpdir(), "btc-listener-wasm-gc-"));
const resources = new Map();
const output = [];
const errors = [];
let nextResource = 1;
let guest = null;
let args = [];
let stopAt = Number.POSITIVE_INFINITY;
let wireBytesRead = 0;
let wireBytesWritten = 0;

const hexName = (text) => Buffer.from(text, "utf8").toString("hex");
const operation = (name) => `op-n${hexName(name)}`;
const helper = (type, suffix) => {
  const name = `__cap_abi_n${hexName(type)}_${suffix}`;
  const fn = guest[name];
  if (typeof fn !== "function") throw new Error(`missing guest ABI helper ${name}`);
  return fn;
};

function memory() {
  return new Uint8Array(guest.memory.buffer);
}

function ensureBytes(byteLength) {
  const wanted = Math.max(1, Math.ceil(byteLength / 65536));
  const held = guest.__rt_memory_pages();
  if (wanted > held) guest.__rt_memory_grow(wanted - held);
}

function jsToAver(text) {
  const bytes = encoder.encode(String(text));
  ensureBytes(bytes.length);
  memory().set(bytes, 0);
  return guest.__rt_string_from_lm(bytes.length);
}

function averToJs(value) {
  const length = guest.__rt_string_to_lm(value);
  return decoder.decode(memory().subarray(0, length));
}

function averIntToBigInt(value) {
  return BigInt(averToJs(helper("Int", "to_decimal")(value)));
}

function bigIntToAver(value) {
  if (value >= -(1n << 63n) && value <= (1n << 63n) - 1n) {
    return guest.__rt_aint_from_i64(value);
  }
  const parsed = helper("Int", "from_decimal")(jsToAver(value.toString()));
  const resultType = "Result<Int, String>";
  if (helper(resultType, "tag")(parsed) !== 1) {
    throw new Error(averToJs(helper(resultType, "err_value")(parsed)));
  }
  return helper(resultType, "ok_value")(parsed);
}

function listToArray(type, list, convert = (value) => value) {
  const listType = `List<${type}>`;
  const empty = helper(listType, "is_empty");
  const head = helper(listType, "head");
  const tail = helper(listType, "tail");
  const values = [];
  while (!empty(list)) {
    values.push(convert(head(list)));
    list = tail(list);
  }
  return values;
}

function arrayToList(type, values, convert = (value) => value) {
  const listType = `List<${type}>`;
  const cons = helper(listType, "cons");
  let list = helper(listType, "nil")();
  for (let i = values.length - 1; i >= 0; i -= 1) {
    list = cons(convert(values[i]), list);
  }
  return list;
}

function averBytesToJs(value) {
  const octets = helper("Bytes", `field_n${hexName("values")}`)(value);
  return Uint8Array.from(
    listToArray("Int", octets, (integer) => Number(averIntToBigInt(integer))),
  );
}

function jsBytesToAver(value) {
  const octets = arrayToList("Int", value, (byte) => bigIntToAver(BigInt(byte)));
  return helper("Bytes", "make")(octets);
}

function resultOk(payloadType, value) {
  if (payloadType === "Bytes") {
    const octets = helper("Bytes", `field_n${hexName("values")}`)(value);
    return guest.__rt_result_bytes_string_ok(octets);
  }
  if (payloadType === "Unit") return guest.__rt_result_unit_string_ok();
  return helper(`Result<${payloadType}, String>`, "ok")(value);
}

function resultErr(payloadType, message) {
  if (payloadType === "Bytes") {
    return guest.__rt_result_bytes_string_err(jsToAver(message));
  }
  if (payloadType === "Unit") {
    return guest.__rt_result_unit_string_err(jsToAver(message));
  }
  return helper(`Result<${payloadType}, String>`, "err")(jsToAver(message));
}

function safeDiskPath(requested) {
  const resolved = path.resolve(scratch, requested);
  if (resolved !== scratch && !resolved.startsWith(`${scratch}${path.sep}`)) {
    throw new Error(`Disk path escapes host sandbox: ${requested}`);
  }
  return resolved;
}

function hash(name, input) {
  return Uint8Array.from(createHash(name).update(input).digest());
}

function derScalar(bytes, cursor) {
  if (bytes[cursor.offset++] !== 0x02) throw new Error("expected DER integer");
  let length = bytes[cursor.offset++];
  if ((length & 0x80) !== 0) {
    const lengthBytes = length & 0x7f;
    if (lengthBytes === 0 || lengthBytes > 4) throw new Error("bad DER length");
    length = 0;
    for (let i = 0; i < lengthBytes; i += 1) {
      length = length * 256 + bytes[cursor.offset++];
    }
  }
  if (cursor.offset + length > bytes.length) throw new Error("truncated DER integer");
  let value = bytes.subarray(cursor.offset, cursor.offset + length);
  cursor.offset += length;
  while (value.length > 0 && value[0] === 0) value = value.subarray(1);
  if (value.length === 0 || value.length > 32) throw new Error("DER scalar out of range");
  const padded = new Uint8Array(32);
  padded.set(value, 32 - value.length);
  return padded;
}

function laxDerToCompact(bytes) {
  try {
    const cursor = { offset: 0 };
    if (bytes[cursor.offset++] !== 0x30) return null;
    let sequenceLength = bytes[cursor.offset++];
    if ((sequenceLength & 0x80) !== 0) {
      const lengthBytes = sequenceLength & 0x7f;
      if (lengthBytes === 0 || lengthBytes > 4) return null;
      sequenceLength = 0;
      for (let i = 0; i < lengthBytes; i += 1) {
        sequenceLength = sequenceLength * 256 + bytes[cursor.offset++];
      }
    }
    if (cursor.offset + sequenceLength > bytes.length) return null;
    const r = derScalar(bytes, cursor);
    const s = derScalar(bytes, cursor);
    const compact = new Uint8Array(64);
    compact.set(r, 0);
    compact.set(s, 32);
    return compact;
  } catch {
    return null;
  }
}

function verifyEcdsa(keyRef, signatureRef, messageRef) {
  const key = averBytesToJs(keyRef);
  const signature = laxDerToCompact(averBytesToJs(signatureRef));
  const message = averBytesToJs(messageRef);
  if (signature === null || message.length !== 32) return 0;
  try {
    return secp256k1.verify(signature, message, key, { prehash: false, lowS: false }) ? 1 : 0;
  } catch {
    return 0;
  }
}

function verifySchnorr(keyRef, signatureRef, messageRef) {
  const key = averBytesToJs(keyRef);
  const signature = averBytesToJs(signatureRef);
  const message = averBytesToJs(messageRef);
  if (key.length !== 32 || signature.length !== 64 || message.length !== 32) return 0;
  try {
    return schnorr.verify(signature, message, key) ? 1 : 0;
  } catch {
    return 0;
  }
}

function bytesToBigInt(bytes) {
  let value = 0n;
  for (const byte of bytes) value = value * 256n + BigInt(byte);
  return value;
}

function tweakMatches(internalRef, tweakRef, outputRef, parityOdd) {
  const internal = averBytesToJs(internalRef);
  const tweak = averBytesToJs(tweakRef);
  const outputKey = averBytesToJs(outputRef);
  if (internal.length !== 32 || tweak.length !== 32 || outputKey.length !== 32) return 0;
  try {
    const scalar = bytesToBigInt(tweak);
    if (scalar >= secp256k1.Point.Fn.ORDER) return 0;
    const compressed = new Uint8Array(33);
    compressed[0] = 0x02;
    compressed.set(internal, 1);
    const point = secp256k1.Point.fromBytes(compressed).add(
      secp256k1.Point.BASE.multiply(scalar),
    );
    if (point.is0()) return 0;
    const encoded = point.toBytes(true);
    if ((encoded[0] === 0x03) !== Boolean(parityOdd)) return 0;
    return encoded.subarray(1).every((byte, index) => byte === outputKey[index]) ? 1 : 0;
  } catch {
    return 0;
  }
}

function kvHandle(value) {
  if (value?.kind !== "btc-listener-node-kv") {
    throw new Error("malformed Infra.Kv.Handle externref");
  }
  return value;
}

function decodePairs(list) {
  return listToArray("Tuple<Bytes, Bytes>", list, (pair) => [
    averBytesToJs(helper("Tuple<Bytes, Bytes>", "field_0")(pair)),
    averBytesToJs(helper("Tuple<Bytes, Bytes>", "field_1")(pair)),
  ]);
}

function encodePairs(pairs) {
  return arrayToList("Tuple<Bytes, Bytes>", pairs, ([key, value]) =>
    helper("Tuple<Bytes, Bytes>", "make")(jsBytesToAver(key), jsBytesToAver(value)),
  );
}

function notify(state) {
  for (const resolve of [...state.waiters]) resolve();
  state.waiters.clear();
}

function attachSocket(id, host, port, socket, kind) {
  const state = {
    id,
    host,
    port,
    socket,
    kind,
    connected: kind === "connection",
    chunks: [],
    ended: false,
    error: null,
    waiters: new Set(),
  };
  socket.on("connect", () => {
    state.connected = true;
    notify(state);
  });
  socket.on("data", (chunk) => {
    wireBytesRead += chunk.length;
    state.chunks.push(Buffer.from(chunk));
    notify(state);
  });
  socket.on("end", () => {
    state.ended = true;
    notify(state);
  });
  socket.on("close", () => {
    state.ended = true;
    notify(state);
  });
  socket.on("error", (error) => {
    state.error = error;
    notify(state);
  });
  resources.set(id, state);
  return state;
}

function resourceById(id, expected) {
  const state = resources.get(id);
  if (!state) throw new Error(`unknown Tcp.${expected} ${id}`);
  return state;
}

function connectionState(connectionRef) {
  const id = averToJs(guest.__rt_tcp_connection_id(connectionRef));
  const state = resourceById(id, "Connection");
  if (state.kind !== "connection") throw new Error(`${id} is not connected`);
  return state;
}

function dialState(dialRef) {
  const id = averToJs(guest.__rt_tcp_dial_id(dialRef));
  const state = resourceById(id, "Dial");
  if (state.kind !== "dial") throw new Error(`${id} is not dialing`);
  return state;
}

function listenerState(listenerRef) {
  const id = averToJs(guest.__rt_tcp_listener_id(listenerRef));
  const state = resourceById(id, "Listener");
  if (state.kind !== "listener") throw new Error(`${id} is not listening`);
  return state;
}

async function waitForState(state) {
  await new Promise((resolve) => state.waiters.add(resolve));
}

function takeAvailable(state, maximum) {
  if (state.chunks.length === 0) return new Uint8Array();
  const first = state.chunks[0];
  const taken = first.subarray(0, maximum);
  if (taken.length === first.length) state.chunks.shift();
  else state.chunks[0] = first.subarray(taken.length);
  return Uint8Array.from(taken);
}

async function readSome(state, maximum) {
  while (state.chunks.length === 0 && !state.ended && state.error === null) {
    await waitForState(state);
  }
  if (state.error !== null) throw state.error;
  return takeAvailable(state, maximum);
}

async function readExactly(state, count) {
  const chunks = [];
  let held = 0;
  while (held < count) {
    while (state.chunks.length === 0 && !state.ended && state.error === null) {
      await waitForState(state);
    }
    if (state.error !== null) throw state.error;
    if (state.chunks.length === 0) {
      throw new Error(`unexpected EOF after ${held} of ${count} bytes`);
    }
    const part = takeAvailable(state, count - held);
    chunks.push(Buffer.from(part));
    held += part.length;
  }
  return Uint8Array.from(Buffer.concat(chunks));
}

function pollEntries(waitset) {
  const entries = [];
  const capacity = guest.__rt_tcp_poll_capacity(waitset);
  for (let index = 0; index < capacity; index += 1) {
    const socketRef = guest.__rt_tcp_poll_socket_at(waitset, index);
    if (socketRef === null) continue;
    const keyRef = guest.__rt_tcp_poll_key_at(waitset, index);
    const id = averToJs(guest.__rt_tcp_socket_id(socketRef));
    const kind = guest.__rt_tcp_socket_kind(socketRef);
    entries.push({ keyRef, key: averIntToBigInt(keyRef), id, kind });
  }
  return entries;
}

function entryReady(entry) {
  const state = resourceById(entry.id, "Socket");
  if (entry.kind === 0) {
    return state.kind === "listener" && (state.pending.length > 0 || state.error !== null);
  }
  if (entry.kind === 1) {
    return state.kind === "dial" && (state.connected || state.error !== null);
  }
  if (entry.kind === 2) {
    return state.kind === "connection"
      && (state.chunks.length > 0 || state.ended || state.error !== null);
  }
  throw new Error(`unknown Tcp.Socket kind ${entry.kind}`);
}

async function waitForAny(entries, milliseconds) {
  await new Promise((resolve) => {
    let timer = null;
    const wake = () => {
      if (timer !== null) clearTimeout(timer);
      for (const entry of entries) resources.get(entry.id)?.waiters.delete(wake);
      resolve();
    };
    for (const entry of entries) resourceById(entry.id, "Socket").waiters.add(wake);
    timer = setTimeout(wake, milliseconds);
  });
}

async function readyEntries(waitset, timeoutMs) {
  const entries = pollEntries(waitset);
  const deadline = Date.now() + timeoutMs;
  while (true) {
    const ready = entries.filter(entryReady);
    if (ready.length > 0) return ready.sort((left, right) => (left.key < right.key ? -1 : 1));
    const remaining = deadline - Date.now();
    if (remaining <= 0 || entries.length === 0) return [];
    await waitForAny(entries, remaining);
  }
}

function intArgument(value, operationName) {
  const integer = averIntToBigInt(value);
  if (integer < BigInt(Number.MIN_SAFE_INTEGER) || integer > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new Error(`${operationName}: ${integer} is outside the JavaScript host range`);
  }
  return Number(integer);
}

function validatePort(port, operationName) {
  if (port < 1 || port > 65535) throw new Error(`${operationName}: port ${port} is outside 1..=65535`);
}

function randomInclusive(minimum, maximum) {
  const span = maximum - minimum + 1n;
  const bits = span.toString(2).length;
  const bytes = Math.ceil(bits / 8);
  const mask = (1n << BigInt(bits)) - 1n;
  while (true) {
    const candidate = bytesToBigInt(randomBytes(bytes)) & mask;
    if (candidate < span) return minimum + candidate;
  }
}

function doubleSha(input) {
  return createHash("sha256").update(createHash("sha256").update(input).digest()).digest();
}

function bitcoinMessage(command, payload, corrupt = false) {
  const commandField = Buffer.alloc(12);
  commandField.write(command, "ascii");
  const length = Buffer.alloc(4);
  length.writeUInt32LE(payload.length);
  const checksum = Buffer.from(doubleSha(payload).subarray(0, 4));
  if (corrupt) checksum[0] ^= 1;
  return Buffer.concat([REGTEST_MAGIC, commandField, length, checksum, payload]);
}

function versionPayload() {
  const fixed = Buffer.alloc(4 + 8 + 8 + 26 + 26 + 8);
  fixed.writeInt32LE(70016, 0);
  fixed.writeBigUInt64LE(0n, 4);
  fixed.writeBigInt64LE(BigInt(Math.floor(Date.now() / 1000)), 12);
  fixed.writeBigUInt64LE(12345n, fixed.length - 8);
  const agent = Buffer.from("/node-wasm:1/", "ascii");
  const height = Buffer.alloc(4);
  height.writeInt32LE(0);
  return Buffer.concat([fixed, Buffer.from([agent.length]), agent, height, Buffer.from([0])]);
}

async function startFakePeer() {
  const state = { commands: [], handshake: false, corruptSent: false, sockets: new Set() };
  const server = net.createServer((socket) => {
    state.sockets.add(socket);
    let buffered = Buffer.alloc(0);
    socket.on("close", () => state.sockets.delete(socket));
    socket.on("data", (chunk) => {
      buffered = Buffer.concat([buffered, chunk]);
      while (buffered.length >= 24) {
        const length = buffered.readUInt32LE(16);
        if (buffered.length < 24 + length) return;
        const frame = buffered.subarray(0, 24 + length);
        buffered = buffered.subarray(24 + length);
        const command = frame.subarray(4, 16).toString("ascii").replaceAll("\0", "");
        state.commands.push(command);
        if (command === "version") {
          socket.write(Buffer.concat([
            bitcoinMessage("version", versionPayload()),
            bitcoinMessage("verack", Buffer.alloc(0)),
          ]));
        } else if (command === "verack" && !state.handshake) {
          state.handshake = true;
          setTimeout(() => {
            if (!socket.destroyed) {
              const nonce = Buffer.alloc(8);
              nonce.writeBigUInt64LE(7n);
              socket.write(bitcoinMessage("ping", nonce, true));
              state.corruptSent = true;
            }
          }, 25);
        }
      }
    });
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (address === null || typeof address === "string") throw new Error("fake Peer has no port");
  return { server, state, port: address.port };
}

async function closeServer(server, sockets = []) {
  for (const socket of sockets) socket.destroy();
  if (!server.listening) return;
  await new Promise((resolve) => server.close(resolve));
}

function providerImports() {
  const primitives = {
    [operation("ripemd160")]: (input, _caller) =>
      jsBytesToAver(hash("ripemd160", averBytesToJs(input))),
    [operation("sha1")]: (input, _caller) =>
      jsBytesToAver(hash("sha1", averBytesToJs(input))),
    [operation("taprootTweakMatches")]: (internal, tweak, outputKey, odd, _caller) =>
      tweakMatches(internal, tweak, outputKey, odd),
    [operation("verifySchnorr")]: (key, signature, message, _caller) =>
      verifySchnorr(key, signature, message),
    [operation("verifySignature")]: (key, signature, message, _caller) =>
      verifyEcdsa(key, signature, message),
  };

  const kv = {
    [operation("open")]: (dirRef, _caller) =>
      resultOk("Infra.Kv.Handle", {
        kind: "btc-listener-node-kv",
        name: averToJs(dirRef),
        values: new Map(),
      }),
    [operation("get")]: (handleRef, keyRef, _caller) => {
      const handle = kvHandle(handleRef);
      const key = Buffer.from(averBytesToJs(keyRef)).toString("hex");
      const found = handle.values.get(key);
      const option = found === undefined
        ? helper("Option<Bytes>", "none")()
        : helper("Option<Bytes>", "some")(jsBytesToAver(found));
      return resultOk("Option<Bytes>", option);
    },
    [operation("putAll")]: (handleRef, entriesRef, _caller) => {
      const handle = kvHandle(handleRef);
      for (const [key, value] of decodePairs(entriesRef)) {
        handle.values.set(Buffer.from(key).toString("hex"), Uint8Array.from(value));
      }
      return resultOk("Unit");
    },
    [operation("deleteAll")]: (handleRef, keysRef, _caller) => {
      const handle = kvHandle(handleRef);
      for (const key of listToArray("Bytes", keysRef, averBytesToJs)) {
        handle.values.delete(Buffer.from(key).toString("hex"));
      }
      return resultOk("Unit");
    },
    [operation("count")]: (handleRef, _caller) =>
      resultOk("Int", bigIntToAver(BigInt(kvHandle(handleRef).values.size))),
    [operation("prefixed")]: (handleRef, prefixRef, _caller) => {
      const handle = kvHandle(handleRef);
      const prefix = Buffer.from(averBytesToJs(prefixRef));
      const pairs = [...handle.values.entries()]
        .map(([key, value]) => [Buffer.from(key, "hex"), value])
        .filter(([key]) => key.subarray(0, prefix.length).equals(prefix))
        .sort(([left], [right]) => Buffer.compare(left, right));
      return resultOk("List<Tuple<Bytes, Bytes>>", encodePairs(pairs));
    },
  };
  return { [PRIMITIVES_MODULE]: primitives, [KV_MODULE]: kv };
}

const suspending = (fn) => new WebAssembly.Suspending(fn);

function standardImports() {
  const unitOk = () => guest.__rt_result_unit_string_ok();
  const unitErr = (name, error) =>
    guest.__rt_result_unit_string_err(jsToAver(`${name}: ${error.message}`));
  return {
    args_len: (_caller) => BigInt(args.length),
    args_get: (index, _caller) => jsToAver(args[Number(index)] ?? ""),
    console_print: (message, _caller) => {
      const text = averToJs(message);
      output.push(text);
      console.log(text);
    },
    console_error: (message, _caller) => {
      const text = averToJs(message);
      errors.push(text);
      console.error(text);
    },
    record_enter_group: (_caller) => {},
    record_set_branch: (_branch, _caller) => {},
    record_exit_group: (_caller) => {},
    provider_contract_violation: (message, _caller) => {
      throw new Error(`provider contract violated: ${averToJs(message)}`);
    },
    process_stop_requested: (_caller) => Date.now() >= stopAt ? 1 : 0,
    time_unix_ms: (_caller) => BigInt(Date.now()),
    time_now: (_caller) => jsToAver(new Date().toISOString()),
    random_int: (minimumRef, maximumRef, _caller) => {
      try {
        const minimum = averIntToBigInt(minimumRef);
        const maximum = averIntToBigInt(maximumRef);
        if (minimum > maximum) throw new Error(`minimum ${minimum} exceeds maximum ${maximum}`);
        return guest.__rt_result_int_string_ok(bigIntToAver(randomInclusive(minimum, maximum)));
      } catch (error) {
        return guest.__rt_result_int_string_err(jsToAver(`Random.int: ${error.message}`));
      }
    },
    terminal_enable_raw_mode: (_caller) => unitOk(),
    terminal_disable_raw_mode: (_caller) => unitOk(),
    terminal_hide_cursor: (_caller) => unitOk(),
    terminal_show_cursor: (_caller) => unitOk(),
    terminal_clear: (_caller) => unitOk(),
    terminal_flush: (_caller) => unitOk(),
    terminal_move_to: (_x, _y, _caller) => unitOk(),
    terminal_print: (message, _caller) => {
      process.stdout.write(averToJs(message));
      return unitOk();
    },
    terminal_read_key: (_caller) =>
      guest.__rt_result_option_string_string_ok(guest.__rt_option_string_none()),
    disk_make_dir: (pathRef, _caller) => {
      try {
        mkdirSync(safeDiskPath(averToJs(pathRef)), { recursive: true });
        return unitOk();
      } catch (error) {
        return unitErr("Disk.makeDir", error);
      }
    },
    disk_list_dir: (pathRef, _caller) => {
      try {
        const names = readdirSync(safeDiskPath(averToJs(pathRef))).sort();
        let list = guest.__rt_list_string_nil();
        for (let index = names.length - 1; index >= 0; index -= 1) {
          list = guest.__rt_list_string_cons(jsToAver(names[index]), list);
        }
        return guest.__rt_result_list_string_string_ok(list);
      } catch (error) {
        return guest.__rt_result_list_string_string_err(jsToAver(`Disk.listDir: ${error.message}`));
      }
    },
    disk_size: (pathRef, _caller) => {
      try {
        const size = BigInt(statSync(safeDiskPath(averToJs(pathRef))).size);
        return guest.__rt_result_int_string_ok(bigIntToAver(size));
      } catch (error) {
        return guest.__rt_result_int_string_err(jsToAver(`Disk.size: ${error.message}`));
      }
    },
    disk_read_bytes_at: (pathRef, offsetRef, countRef, _caller) => {
      try {
        const offset = intArgument(offsetRef, "Disk.readBytesAt");
        const count = intArgument(countRef, "Disk.readBytesAt");
        if (offset < 0 || count < 0) throw new Error("offset and count must be non-negative");
        const bytes = readFileSync(safeDiskPath(averToJs(pathRef))).subarray(offset, offset + count);
        return resultOk("Bytes", jsBytesToAver(bytes));
      } catch (error) {
        return resultErr("Bytes", `Disk.readBytesAt: ${error.message}`);
      }
    },
    disk_read_text: (pathRef, _caller) => {
      try {
        const text = decoder.decode(readFileSync(safeDiskPath(averToJs(pathRef))));
        return guest.__rt_result_string_string_ok(jsToAver(text));
      } catch (error) {
        return guest.__rt_result_string_string_err(jsToAver(`Disk.readText: ${error.message}`));
      }
    },
    disk_write_text: (pathRef, textRef, _caller) => {
      try {
        writeFileSync(safeDiskPath(averToJs(pathRef)), averToJs(textRef), "utf8");
        return unitOk();
      } catch (error) {
        return unitErr("Disk.writeText", error);
      }
    },
    disk_append_text: (pathRef, textRef, _caller) => {
      try {
        appendFileSync(safeDiskPath(averToJs(pathRef)), averToJs(textRef), "utf8");
        return unitOk();
      } catch (error) {
        return unitErr("Disk.appendText", error);
      }
    },
    disk_write_bytes: (pathRef, bytesRef, _caller) => {
      try {
        writeFileSync(safeDiskPath(averToJs(pathRef)), averBytesToJs(bytesRef));
        return unitOk();
      } catch (error) {
        return unitErr("Disk.writeBytes", error);
      }
    },
    disk_append_bytes: (pathRef, bytesRef, _caller) => {
      try {
        appendFileSync(safeDiskPath(averToJs(pathRef)), averBytesToJs(bytesRef));
        return unitOk();
      } catch (error) {
        return unitErr("Disk.appendBytes", error);
      }
    },
    disk_exists: (pathRef, _caller) => existsSync(safeDiskPath(averToJs(pathRef))) ? 1 : 0,
    disk_delete: (pathRef, _caller) => {
      try {
        unlinkSync(safeDiskPath(averToJs(pathRef)));
        return unitOk();
      } catch (error) {
        return unitErr("Disk.delete", error);
      }
    },
    tcp_begin_connect: (hostRef, portRef, _caller) => {
      try {
        const host = averToJs(hostRef);
        const port = intArgument(portRef, "Tcp.beginConnect");
        validatePort(port, "Tcp.beginConnect");
        const id = `node-tcp-${nextResource++}`;
        attachSocket(id, host, port, net.createConnection({ host, port }), "dial");
        return guest.__rt_result_tcp_dial_string_ok(jsToAver(id));
      } catch (error) {
        return guest.__rt_result_tcp_dial_string_err(jsToAver(`Tcp.beginConnect: ${error.message}`));
      }
    },
    tcp_dialled: (dialRef, _caller) => {
      try {
        const state = dialState(dialRef);
        if (state.error !== null) {
          resources.delete(state.id);
          return guest.__rt_result_option_tcp_connection_string_err(
            jsToAver(`Tcp.dialled: ${state.error.message}`),
          );
        }
        if (!state.connected) return guest.__rt_result_option_tcp_connection_string_none();
        state.kind = "connection";
        const connection = guest.__rt_record_tcp_connection_make(
          jsToAver(state.id),
          jsToAver(state.host),
          BigInt(state.port),
        );
        return guest.__rt_result_option_tcp_connection_string_some(connection);
      } catch (error) {
        return guest.__rt_result_option_tcp_connection_string_err(jsToAver(`Tcp.dialled: ${error.message}`));
      }
    },
    tcp_close_dial: (dialRef, _caller) => {
      try {
        const state = dialState(dialRef);
        state.socket.destroy();
        resources.delete(state.id);
        return unitOk();
      } catch (error) {
        return unitErr("Tcp.closeDial", error);
      }
    },
    tcp_listen: suspending(async (portRef, backlogRef, _caller) => {
      try {
        const port = intArgument(portRef, "Tcp.listen");
        const backlog = intArgument(backlogRef, "Tcp.listen");
        validatePort(port, "Tcp.listen");
        if (backlog <= 0) throw new Error(`backlog ${backlog} must be positive`);
        const id = `node-listener-${nextResource++}`;
        const server = net.createServer({ pauseOnConnect: true });
        const state = { id, kind: "listener", server, pending: [], error: null, waiters: new Set() };
        server.on("connection", (socket) => {
          state.pending.push(socket);
          notify(state);
        });
        server.on("error", (error) => {
          state.error = error;
          notify(state);
        });
        resources.set(id, state);
        server.listen({ host: "127.0.0.1", port, backlog });
        await once(server, "listening");
        return guest.__rt_result_tcp_listener_string_ok(jsToAver(id));
      } catch (error) {
        return guest.__rt_result_tcp_listener_string_err(jsToAver(`Tcp.listen: ${error.message}`));
      }
    }),
    tcp_accept: (listenerRef, _caller) => {
      try {
        const listener = listenerState(listenerRef);
        if (listener.error !== null) throw listener.error;
        const socket = listener.pending.shift();
        if (socket === undefined) return guest.__rt_result_option_tcp_connection_string_none();
        const host = socket.remoteAddress ?? "";
        const port = socket.remotePort ?? 0;
        const id = `node-tcp-${nextResource++}`;
        attachSocket(id, host, port, socket, "connection");
        socket.resume();
        const connection = guest.__rt_record_tcp_connection_make(
          jsToAver(id),
          jsToAver(host),
          BigInt(port),
        );
        return guest.__rt_result_option_tcp_connection_string_some(connection);
      } catch (error) {
        return guest.__rt_result_option_tcp_connection_string_err(jsToAver(`Tcp.accept: ${error.message}`));
      }
    },
    tcp_peer_address: (connectionRef, _caller) => {
      try {
        const state = connectionState(connectionRef);
        const host = state.socket.remoteAddress ?? state.host;
        const port = state.socket.remotePort ?? state.port;
        const rendered = host.includes(":") ? `[${host}]:${port}` : `${host}:${port}`;
        return guest.__rt_result_string_string_ok(jsToAver(rendered));
      } catch (error) {
        return guest.__rt_result_string_string_err(jsToAver(`Tcp.peerAddress: ${error.message}`));
      }
    },
    tcp_poll: suspending(async (waitset, timeoutRef, _caller) => {
      try {
        const timeoutMs = intArgument(timeoutRef, "Tcp.poll");
        if (timeoutMs < 0) throw new Error(`timeoutMs ${timeoutMs} must be non-negative`);
        const ready = await readyEntries(waitset, timeoutMs);
        let list = guest.__rt_list_int_nil();
        for (let index = ready.length - 1; index >= 0; index -= 1) {
          list = guest.__rt_list_int_cons(ready[index].keyRef, list);
        }
        return guest.__rt_result_list_int_string_ok(list);
      } catch (error) {
        return guest.__rt_result_list_int_string_err(jsToAver(`Tcp.poll: ${error.message}`));
      }
    }),
    tcp_write_bytes: suspending(async (connectionRef, bytesRef, _caller) => {
      try {
        const state = connectionState(connectionRef);
        const bytes = averBytesToJs(bytesRef);
        wireBytesWritten += bytes.length;
        await new Promise((resolve, reject) => {
          state.socket.write(bytes, (error) => (error ? reject(error) : resolve()));
        });
        return unitOk();
      } catch (error) {
        return unitErr("Tcp.writeBytes", error);
      }
    }),
    tcp_read_some: suspending(async (connectionRef, maximumRef, _caller) => {
      try {
        const maximum = intArgument(maximumRef, "Tcp.readSome");
        if (maximum <= 0 || maximum > TCP_BODY_LIMIT) {
          throw new Error(`maxBytes ${maximum} must be within 1..=${TCP_BODY_LIMIT}`);
        }
        return resultOk("Bytes", jsBytesToAver(await readSome(connectionState(connectionRef), maximum)));
      } catch (error) {
        return resultErr("Bytes", `Tcp.readSome: ${error.message}`);
      }
    }),
    tcp_connect: suspending(async (hostRef, port, _caller) => {
      try {
        const host = averToJs(hostRef);
        const numericPort = Number(port);
        validatePort(numericPort, "Tcp.connect");
        const id = `node-tcp-${nextResource++}`;
        const state = attachSocket(id, host, numericPort, net.createConnection({ host, port: numericPort }), "dial");
        while (!state.connected && state.error === null) await waitForState(state);
        if (state.error !== null) throw state.error;
        state.kind = "connection";
        const connection = guest.__rt_record_tcp_connection_make(
          jsToAver(id),
          jsToAver(host),
          BigInt(numericPort),
        );
        return guest.__rt_result_tcp_connection_string_ok(connection);
      } catch (error) {
        return guest.__rt_result_tcp_connection_string_err(jsToAver(`Tcp.connect: ${error.message}`));
      }
    }),
    tcp_read_bytes: suspending(async (connectionRef, countRef, _caller) => {
      try {
        const count = intArgument(countRef, "Tcp.readBytes");
        if (count < 0 || count > TCP_BODY_LIMIT) {
          throw new Error(`count ${count} must be within 0..=${TCP_BODY_LIMIT}`);
        }
        return resultOk("Bytes", jsBytesToAver(await readExactly(connectionState(connectionRef), count)));
      } catch (error) {
        return resultErr("Bytes", `Tcp.readBytes: ${error.message}`);
      }
    }),
    tcp_close: (connectionRef, _caller) => {
      try {
        const state = connectionState(connectionRef);
        state.socket.destroy();
        resources.delete(state.id);
        return unitOk();
      } catch (error) {
        return unitErr("Tcp.close", error);
      }
    },
    // Suspending, because releasing a bound port is asynchronous in Node and
    // the contract is that the port is free when this returns. Sockets that
    // arrived but were never accepted go with it; ones already accepted are
    // their own resources and stay live, which is what the contract says.
    tcp_close_listener: suspending(async (listenerRef, _caller) => {
      try {
        const state = listenerState(listenerRef);
        for (const socket of state.pending) socket.destroy();
        await closeServer(state.server);
        resources.delete(state.id);
        return unitOk();
      } catch (error) {
        return unitErr("Tcp.closeListener", error);
      }
    }),
  };
}

async function main() {
  if (typeof WebAssembly.Suspending !== "function" || typeof WebAssembly.promising !== "function") {
    throw new Error("Node.js with WebAssembly JSPI support is required");
  }
  const requestedArgs = process.argv.slice(3).filter((argument) => argument !== "--");
  const fake = requestedArgs.length === 0 ? await startFakePeer() : null;
  args = fake === null ? requestedArgs : ["regtest", "127.0.0.1", String(fake.port)];
  const sampleMs = Number(process.env.BTC_LISTENER_SAMPLE_MS ?? 20000);
  const startedAt = Date.now();
  if (fake === null) stopAt = Date.now() + sampleMs;
  try {
    const wasm = readFileSync(process.argv[2]);
    const imports = { aver: standardImports(), ...providerImports() };
    const instantiated = await WebAssembly.instantiate(wasm, imports);
    guest = instantiated.instance.exports;
    const run = WebAssembly.promising(guest.main);
    let timeout = null;
    const deadline = new Promise((_, reject) => {
      const timeoutMs = fake === null ? sampleMs + 120000 : 15000;
      timeout = setTimeout(() => reject(new Error("listener path timed out")), timeoutMs);
    });
    const result = await Promise.race([run(), deadline]).finally(() => clearTimeout(timeout));
    const resultType = "Result<Unit, String>";
    const succeeded = helper(resultType, "tag")(result) === 1;
    if (fake === null) {
      if (!succeeded) {
        throw new Error(averToJs(helper(resultType, "err_value")(result)));
      }
      console.log("btc-listener wasm-gc command: ok");
      return;
    }
    if (succeeded) {
      throw new Error("listener path accepted the deliberately corrupt Peer frame");
    }
    const failure = averToJs(helper(resultType, "err_value")(result));
    if (!output.some((line) => line.startsWith("listening to peer 127.0.0.1:"))) {
      throw new Error("full CLI did not select and report the local regtest Peer");
    }
    if (!output.includes("handshake complete") || !fake.state.handshake) {
      throw new Error("full Peer handshake did not complete in both directions");
    }
    if (!fake.state.corruptSent || !errors.some((line) => line.includes("checksum"))) {
      throw new Error(`listener path did not diagnose the corrupt checksum; returned: ${failure}`);
    }
    console.log(`full btc-listener listener path: ok (${failure})`);
  } finally {
    if (fake === null) {
      const seconds = Math.max(0.001, (Date.now() - startedAt) / 1000);
      console.log(
        `Node host wire sample: ${wireBytesRead} B read, ${wireBytesWritten} B written in ${seconds.toFixed(1)} s`,
      );
    }
    for (const state of resources.values()) {
      if (state.kind === "listener") {
        for (const socket of state.pending) socket.destroy();
        await closeServer(state.server);
      } else {
        state.socket.destroy();
      }
    }
    resources.clear();
    if (fake !== null) await closeServer(fake.server, fake.state.sockets);
    rmSync(scratch, { recursive: true, force: true });
  }
}

await main();
