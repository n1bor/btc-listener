#!/usr/bin/env python3
"""Capture a real cmpctblock from Bitcoin Core, so BIP152 has an oracle.

Domain.CompactBlock's own verify cases are fixtures this project wrote, and a
fixture cannot disagree with the assumption that produced it.  This speaks
just enough of the protocol to make Core send a genuine compact Block, and
writes the payload out as hex beside what Core says the Block contains.

    python3 tools/regtest/cmpctblock-capture.py 127.0.0.1 18444 out.json

Then mine a Block on that node while this is waiting.  What comes back is
Core's own encoding: header, nonce, short ids and the prefilled coinbase.
"""

import hashlib
import json
import socket
import struct
import sys
import time

MAGIC = bytes.fromhex("fabfb5da")          # regtest


def sha256d(b):
    return hashlib.sha256(hashlib.sha256(b).digest()).digest()


def frame(command, payload):
    name = command.encode() + b"\x00" * (12 - len(command))
    return MAGIC + name + struct.pack("<I", len(payload)) + sha256d(payload)[:4] + payload


def netaddr():
    return struct.pack("<Q", 0) + b"\x00" * 10 + b"\xff\xff" + b"\x00" * 4 + struct.pack(">H", 0)


def version_payload():
    return (struct.pack("<iQq", 70016, 0, int(time.time()))
            + netaddr() + netaddr()
            + struct.pack("<Q", 0x1234)
            + bytes([len(b"/cmpct-capture/")]) + b"/cmpct-capture/"
            + struct.pack("<i", 0) + b"\x01")


def read_message(sock):
    head = b""
    while len(head) < 24:
        chunk = sock.recv(24 - len(head))
        if not chunk:
            raise SystemExit("peer closed during the header")
        head += chunk
    command = head[4:16].rstrip(b"\x00").decode()
    length = struct.unpack("<I", head[16:20])[0]
    body = b""
    while len(body) < length:
        chunk = sock.recv(min(65536, length - len(body)))
        if not chunk:
            raise SystemExit("peer closed mid-body")
        body += chunk
    return command, body


def compact_size(data, at):
    first = data[at]
    if first < 253:
        return first, at + 1
    if first == 253:
        return struct.unpack("<H", data[at + 1:at + 3])[0], at + 3
    if first == 254:
        return struct.unpack("<I", data[at + 1:at + 5])[0], at + 5
    return struct.unpack("<Q", data[at + 1:at + 9])[0], at + 9


host, port, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
sock = socket.create_connection((host, port), timeout=120)
sock.sendall(frame("version", version_payload()))

seen_verack = False
while not seen_verack:
    command, body = read_message(sock)
    if command == "version":
        sock.sendall(frame("verack", b""))
    elif command == "verack":
        seen_verack = True

# Low bandwidth: announce first, and we will ask.  version 2 = short ids over
# the Witness Transaction Id, which is what a SegWit node must speak.
sock.sendall(frame("sendcmpct", b"\x00" + struct.pack("<Q", 2)))
print("handshake done; mine a Block now", flush=True)

deadline = time.time() + 120
while time.time() < deadline:
    command, body = read_message(sock)
    if command == "ping":
        sock.sendall(frame("pong", body))
    elif command in ("inv", "headers"):
        # An announcement of any kind: ask for it as a compact Block.
        # MSG_CMPCT_BLOCK is inventory type 4.
        if command == "inv":
            count, at = compact_size(body, 0)
            wanted = b""
            asked = 0
            for _ in range(count):
                kind = struct.unpack("<I", body[at:at + 4])[0]
                digest = body[at + 4:at + 36]
                at += 36
                if kind == 2:                       # MSG_BLOCK
                    wanted += struct.pack("<I", 4) + digest
                    asked += 1
            if asked:
                sock.sendall(frame("getdata", bytes([asked]) + wanted))
        else:
            count, at = compact_size(body, 0)
            header = body[at:at + 80]
            digest = sha256d(header)
            sock.sendall(frame("getdata", b"\x01" + struct.pack("<I", 4) + digest))
    elif command == "cmpctblock":
        header = body[:80]
        blockid = sha256d(header)[::-1].hex()
        nonce = struct.unpack("<Q", body[80:88])[0]
        count, at = compact_size(body, 88)
        shortids = [int.from_bytes(body[at + i * 6:at + (i + 1) * 6], "little") for i in range(count)]
        at += count * 6
        prefilled_count, at = compact_size(body, at)
        json.dump({
            "payload": body.hex(),
            "blockid": blockid,
            "header": header.hex(),
            "nonce": nonce,
            "shortids": shortids,
            "prefilled_count": prefilled_count,
        }, open(out, "w"), indent=2)
        print(f"captured a compact Block for {blockid}: "
              f"{count} short id(s), {prefilled_count} prefilled -> {out}")
        break
sock.close()
