#!/usr/bin/env python3
"""Production Infra.Working/BlockWorkJob acceptance; synthetic decode input, no database.

Build tools/working_probe.av with --target rust --with-replay, then pass its
binary here. Tests fragmented pings, deferred inv, delivery and SIGINT during
work. The repeated transaction payload is deliberately not a valid consensus
block: this exercises scheduling and typed decoding, not consensus acceptance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import signal
import socket
import struct
import subprocess
import tempfile
import threading
import time


def frame(command, body):
    checksum = hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4]
    return bytes.fromhex("fabfb5da") + command.encode().ljust(12, b"\0") + struct.pack("<I", len(body)) + checksum + body


def exact(connection, count):
    result = b""
    while len(result) < count:
        part = connection.recv(count - len(result))
        assert part, "peer closed before a complete frame"
        result += part
    return result


def receive(connection):
    header = exact(connection, 24)
    assert header[:4] == bytes.fromhex("fabfb5da"), header.hex()
    size = struct.unpack("<I", header[16:20])[0]
    assert size <= 4_000_000, size
    body = exact(connection, size)
    command = header[4:16].rstrip(b"\0").decode()
    assert frame(command, body) == header + body, "bad checksum"
    return command, body


def payload(path, count):
    source = (Path(__file__).resolve().parents[2] / "domain/block.av").read_text()
    genesis = bytes.fromhex(re.search(r'List.len\(transactionsOf\(Bytes.fromHex\("([0-9a-f]+)"', source)[1])
    assert genesis[80] == 1 and 0 < count < 65536
    path.write_bytes(genesis[:80] + b"\xfd" + struct.pack("<H", count) + genesis[81:] * count)


def trial(binary, count, cancel=False, record=False):
    with tempfile.TemporaryDirectory(prefix="btc-work-wait-") as directory, socket.socket() as listener:
        directory = Path(directory)
        input_file = directory / "payload.bin"
        payload(input_file, count)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(20)
        port = listener.getsockname()[1]
        recording = directory / "session.json"
        env = dict(os.environ)
        if record:
            env["AVER_REPLAY_RECORD"] = str(recording)
        process = subprocess.Popen([binary, f"127.0.0.1:{port}", str(input_file)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        events = queue.Queue()
        def reading():
            for line in process.stdout:
                events.put((time.monotonic(), line.strip()))
            events.put((time.monotonic(), None))
        reader = threading.Thread(target=reading, daemon=True)
        reader.start()
        lines = []
        try:
            with listener.accept()[0] as peer:
                peer.settimeout(20)
                assert receive(peer)[0] == "version"
                version = struct.pack("<iQq", 70016, 0, int(time.time())) + bytes(52) + struct.pack("<Q", 4242) + b"\x0a/worktest/" + struct.pack("<i", 0) + b"\0"
                peer.sendall(frame("version", version) + frame("verack", b""))
                while receive(peer)[0] != "verack":
                    pass
                while True:
                    at, line = events.get(timeout=30)
                    assert line is not None, lines
                    lines.append(line)
                    if line == "work starting":
                        start = at
                        break
                nonce = struct.pack("<Q", 123456789)
                request = frame("ping", nonce)
                sent = time.monotonic()
                peer.sendall(request[:7])
                time.sleep(0.02)
                announcement = b"\x01" + struct.pack("<I", 2) + bytes.fromhex("42" * 32)
                peer.sendall(request[7:] + frame("inv", announcement))
                while True:
                    command, body = receive(peer)
                    if command == "pong":
                        assert body == nonce
                        pong = time.monotonic()
                        break
                stop_at = None
                if cancel:
                    stop_at = time.monotonic()
                    process.send_signal(signal.SIGINT)
                result_at = None
                while True:
                    at, line = events.get(timeout=60)
                    if line is None:
                        break
                    lines.append(line)
                    if line.startswith("decoded ") or line == "cancelled":
                        result_at = at
                assert process.wait(timeout=5) == 0, lines
                assert result_at is not None and pong < result_at, lines
                assert ("cancelled" if cancel else f"decoded {count}") in lines, lines
                assert any("0:inv:37" in line for line in lines), lines
                measured = {"mode": "cancel" if cancel else "deliver", "transactions": count,
                    "pong_ms": round((pong - sent) * 1000, 2),
                    "owner_result_ms": round((result_at - start) * 1000, 2),
                    "work_remaining_after_pong_ms": round((result_at - pong) * 1000, 2),
                    "announcement_retained": True}
                if stop_at is not None:
                    measured["cancel_result_ms"] = round((result_at - stop_at) * 1000, 2)
                    assert result_at - stop_at < 2, measured
                if record:
                    assert recording.exists(), lines
                    replay = subprocess.run([binary], env={**os.environ, "AVER_REPLAY_REPLAY": str(recording)}, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
                    assert replay.returncode == 0, replay.stdout
                    for marker in (f"decoded {count}", "0:inv:37"):
                        assert marker in replay.stdout, replay.stdout
                    measured["native_replay"] = True
                return measured
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            reader.join(timeout=1)
            process.stdout.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", nargs="?")
    parser.add_argument("--payload", type=Path, help="write only the synthetic workload for another host")
    parser.add_argument("--count", type=int, default=20000)
    parser.add_argument("--mode", choices=["deliver", "cancel", "record"], default="deliver")
    args = parser.parse_args()
    if args.payload:
        payload(args.payload, args.count)
    else:
        if not args.binary:
            parser.error("binary is required unless --payload is supplied")
        print(json.dumps(trial(str(Path(args.binary).resolve()), args.count, args.mode == "cancel", args.mode == "record")), flush=True)
