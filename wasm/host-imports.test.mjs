import { test } from "node:test";
import assert from "node:assert/strict";
import { missingImports } from "./imports.mjs";

// A minimal wasm module whose only content is one function import,
// `aver.no_such_thing`, built byte by byte so the test needs no toolchain.
function moduleImporting(from, name) {
  const utf8 = (text) => Array.from(new TextEncoder().encode(text));
  const section = (id, bytes) => [id, bytes.length, ...bytes];
  const type = section(1, [1, 0x60, 0, 0]);                    // one type: () -> ()
  const imp = section(2, [1, from.length, ...utf8(from), name.length, ...utf8(name), 0x00, 0]); // func import, type 0
  return new WebAssembly.Module(new Uint8Array([0x00, 0x61, 0x73, 0x6d, 1, 0, 0, 0, ...type, ...imp]));
}

test("an import the host has no function for is named, not left to LinkError", () => {
  const module = moduleImporting("aver", "no_such_thing");
  assert.deepEqual(missingImports(module, { aver: {} }), ["aver.no_such_thing"]);
  assert.deepEqual(missingImports(module, {}), ["aver.no_such_thing"]);
});

test("an import the host provides is not reported", () => {
  const module = moduleImporting("aver", "tcp_close_listener");
  assert.deepEqual(missingImports(module, { aver: { tcp_close_listener() {} } }), []);
});

test("a host value that is not a function does not count as providing it", () => {
  const module = moduleImporting("aver", "x");
  assert.deepEqual(missingImports(module, { aver: { x: 1 } }), ["aver.x"]);
});
