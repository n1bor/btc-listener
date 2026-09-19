"""Isolated process ownership and RPC helpers for suite.py."""
import hashlib
import json
from pathlib import Path
import signal
import socket
import struct
import subprocess
import time


def free_port():
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        return held.getsockname()[1]


def wait_until(predicate, timeout, description):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError("timed out: " + description)


class Core:
    def __init__(self, binaries, data, reuse=False, stop_timeout=30):
        self.binaries, self.data, self.reuse = binaries, data, reuse
        self.stop_timeout = stop_timeout
        self.port, self.rpc_port = free_port(), free_port()
        self.peer = "127.0.0.1:" + str(self.port)

    def __enter__(self):
        self.data.mkdir(exist_ok=self.reuse)
        self.log = (self.data / "process.log").open("w")
        self.process = subprocess.Popen([
            str(self.binaries / "bitcoind"), "-datadir=" + str(self.data),
            "-regtest", "-server", "-listen", "-bind=127.0.0.1:" + str(self.port),
            "-port=" + str(self.port), "-rpcport=" + str(self.rpc_port),
            "-connect=0", "-dnsseed=0", "-discover=0", "-listenonion=0",
            "-fallbackfee=0.0001", "-dbcache=32", "-debug=net",
        ], stdout=self.log, stderr=subprocess.STDOUT)
        def ready():
            assert self.process.poll() is None, (self.data / "process.log").read_text()
            try:
                self.rpc("getblockcount")
                return True
            except subprocess.CalledProcessError:
                return False
        try:
            wait_until(ready, 30, "Core startup")
        except BaseException:
            if self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=30)
            self.log.close()
            raise
        return self

    def rpc(self, *args, stdin=()):
        # bitcoin-cli -stdin reads further arguments one per line, which is how
        # a megabyte of transaction hex reaches an RPC: the exec argument limit
        # is well below that on both Linux and macOS.
        result = subprocess.run([str(self.binaries / "bitcoin-cli"), "-datadir=" + str(self.data),
            "-regtest", "-rpcport=" + str(self.rpc_port), *(["-stdin"] if stdin else []),
            *(json.dumps(a) if isinstance(a, bool) else str(a) for a in args)],
            input="\n".join(stdin) if stdin else None,
            text=True, capture_output=True, timeout=180 if args[0] == "generatetoaddress" else 30)
        if result.returncode:
            raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
        try:
            return json.loads(result.stdout)
        except ValueError:
            return result.stdout.strip()

    def height(self):
        return self.rpc("getblockcount")

    def mine(self, count):
        return self.rpc("generatetoaddress", count, self.rpc("getnewaddress"))

    def disconnect_all(self):
        for peer in self.rpc("getpeerinfo"):
            try:
                self.rpc("disconnectnode", "", peer["id"])
            except subprocess.CalledProcessError:
                pass
        wait_until(lambda: not self.rpc("getpeerinfo"), 10, "Core disconnect")

    def sync(self, other):
        self.rpc("addnode", other.peer, "onetry")
        wait_until(lambda: self.rpc("getbestblockhash") == other.rpc("getbestblockhash"), 120, "Core chain sync")

    def __exit__(self, *_):
        if self.process.poll() is None:
            self.rpc("stop")
            self.process.wait(timeout=self.stop_timeout)
        self.log.close()


class Follow:
    def __init__(self, binary, log, peers, data, extras):
        self.log, self.stream = log, log.open("w")
        self.process = subprocess.Popen([str(binary), "regtest", "follow", peers, str(data), *extras],
            stdout=self.stream, stderr=subprocess.STDOUT)

    def text(self):
        return self.log.read_text()

    def wait(self, text, timeout=60):
        def seen():
            assert self.process.poll() is None, self.text()
            return text in self.text()
        wait_until(seen, timeout, text + " in " + str(self.log))

    def tip(self, height, timeout=90):
        self.wait("following at Height " + str(height) + ":", timeout)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        try:
            if self.process.poll() is None:
                self.process.send_signal(signal.SIGINT)
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
                    raise AssertionError("follow did not stop within 5s: " + self.text())
            assert self.process.returncode == 0, self.text()
        finally:
            self.stream.close()


class Suite:
    def __init__(self, binary, root):
        self.binary, self.root = binary, root
        self.data, self.report = root / "chain", []

    def cli(self, label, command, *args, error=None, timeout=120):
        result = subprocess.run([str(self.binary), "regtest", command, *map(str, args)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        (self.root / (label + ".log")).write_text(result.stdout)
        assert (result.returncode != 0) if error else (result.returncode == 0), result.stdout
        if error:
            assert error in result.stdout, result.stdout
        return result.stdout

    def follow(self, peers, *extras, data=None, label="follow"):
        return Follow(self.binary, self.root / (label + ".log"), peers, data or self.data, extras)

    def hashes(self, core, heights, data=None):
        for height in heights:
            expected = core.rpc("getblockhash", height)
            assert expected in self.cli("hash-" + str(height), "show", data or self.data, height, "summary")

    def passed(self, name, **details):
        row = {"case": name, "status": "passed", **details}
        self.report.append(row)
        (self.root / "report.json").write_text(json.dumps(self.report, indent=2) + "\n")
        print(json.dumps(row), flush=True)


def wire(command, body=b""):
    checksum = hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4]
    return bytes.fromhex("fabfb5da") + command.encode().ljust(12, b"\0") + struct.pack("<I", len(body)) + checksum + body


def receive(peer):
    def exact(size):
        data = b""
        while len(data) < size:
            chunk = peer.recv(size - len(data))
            assert chunk, "peer closed"
            data += chunk
        return data
    header = exact(24)
    size = struct.unpack("<I", header[16:20])[0]
    assert size <= 4_000_000
    body = exact(size)
    command = header[4:16].rstrip(b"\0").decode()
    assert wire(command, body) == header + body
    return command, body


def greeting(peer):
    version = struct.pack("<iQq", 70016, 0, int(time.time())) + bytes(52) + struct.pack("<Q", 4242) + b"\x06/suite" + struct.pack("<i", 0) + b"\0"
    peer.sendall(wire("version", version) + wire("verack"))
    while receive(peer)[0] != "verack":
        pass
