"""Hostile wire and admission cases for the native acceptance suite."""
from contextlib import contextmanager
import socket
import struct
import threading
import time

from suite_support import free_port, greeting, receive, wait_until, wire

GENESIS = bytes.fromhex("0f9188f13cb7b2c71f2a335e3a4fc328bf5beb436012afca590b1a11466e2206")[::-1]


def compact_size(n):
    return bytes([n]) if n < 253 else b"\xfd" + struct.pack("<H", n)


@contextmanager
def liar(core, mode):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    server.settimeout(1)
    done = threading.Event()
    status = {"sent": 0, "errors": []}
    peers = []

    def answer():
        try:
            while not done.is_set():
                try:
                    peer, _ = server.accept()
                    break
                except socket.timeout:
                    continue
            else:
                return
            peers.append(peer)
            peer.settimeout(2)
            assert receive(peer)[0] == "version"
            agent = b"/suite/" if mode != "escape" else b"\x1b[2J\x1b]0;pwned\x07/suite/"
            version = struct.pack("<iQq", 70016, 0, int(time.time())) + bytes(52) + struct.pack("<Q", 789) + bytes([len(agent)]) + agent + struct.pack("<i", core.height()) + b"\0"
            peer.sendall(wire("version", version) + wire("verack"))
            while receive(peer)[0] != "verack":
                pass
            poisoned = False
            while not done.is_set():
                try:
                    command, body = receive(peer)
                except socket.timeout:
                    continue
                if command == "ping":
                    peer.sendall(wire("pong", body))
                if command == "getheaders":
                    payload = b"\0"
                    if mode == "lowbits":
                        header = struct.pack("<i", 1) + GENESIS + bytes(32) + struct.pack("<III", int(time.time()), 0x01010000, 0)
                        payload = b"\1" + header + b"\0"
                    if mode == "wrongbody":
                        first = body[5:37][::-1].hex()
                        start = core.rpc("getblockheader", first)["height"]
                        headers = [bytes.fromhex(core.rpc("getblockheader", core.rpc("getblockhash", h), "false")) for h in range(start + 1, core.height() + 1)]
                        payload = compact_size(len(headers)) + b"".join(h + b"\0" for h in headers)
                    peer.sendall(wire("headers", payload))
                if mode == "wrongbody" and command == "getdata":
                    # This test adds three blocks, so the inventory count fits one byte.
                    for index in range(body[0]):
                        at = 1 + index * 36
                        kind = struct.unpack("<I", body[at:at + 4])[0]
                        if kind & 2:
                            block = bytes.fromhex(core.rpc("getblock", body[at + 4:at + 36][::-1].hex(), 0))
                            # Honest Header, empty transaction list: invalid before persistence.
                            peer.sendall(wire("block", block[:80] + b"\0"))
                            status["sent"] += 1
                if not poisoned and command in ("getaddr", "getheaders"):
                    poisoned = True
                    if mode == "checksum":
                        bad = bytearray(wire("ping", bytes(8)))
                        bad[20] ^= 1
                        peer.sendall(bad)
                        status["sent"] += 1
                    elif mode == "network":
                        peer.sendall(bytes.fromhex("f9beb4d9") + wire("ping", bytes(8))[4:])
                        status["sent"] += 1
                    elif mode == "hugetx":
                        peer.sendall(wire("tx", struct.pack("<i", 1) + b"\xff" * 9))
                        status["sent"] += 1
                    elif mode == "addrflood":
                        for batch in range(10):
                            addresses = []
                            for i in range(1000):
                                n = batch * 1000 + i
                                addresses.append(struct.pack("<IQ", int(time.time()), 1) + bytes(10) + b"\xff\xff" + bytes([8, 8 + n // 65536, (n >> 8) & 255, n & 255]) + struct.pack(">H", 8333))
                            peer.sendall(wire("addr", compact_size(1000) + b"".join(addresses)))
                        status["sent"] = 10000
                    elif mode == "headerflood":
                        # Already-known and empty claims, all delivered before the
                        # fresh honest announcement. The stored tree must not move.
                        genesis = bytes.fromhex(core.rpc("getblockheader", core.rpc("getblockhash", 0), "false"))
                        for i in range(40):
                            peer.sendall(wire("headers", b"\0" if i % 2 else b"\1" + genesis + b"\0"))
                            status["sent"] += 1
        except (OSError, AssertionError) as error:
            if not done.is_set():
                status["errors"].append(str(error))
        finally:
            for peer in peers:
                peer.close()

    thread = threading.Thread(target=answer, daemon=True)
    thread.start()
    try:
        yield "127.0.0.1:" + str(server.getsockname()[1]), status
    finally:
        done.set()
        for peer in peers:
            try:
                peer.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        server.close()
        thread.join(timeout=3)
        assert not thread.is_alive(), "liar helper failed to stop"


def exercise(s, a):
    for mode, expected in [("checksum", "checksum"), ("network", "Network"), ("lowbits", "target"), ("hugetx", "Transaction")]:
        with liar(a, mode) as (address, status):
            with s.follow(address + "," + a.peer, "log", label="liar-" + mode) as live:
                live.tip(a.height())
                wait_until(lambda: expected.lower() in live.text().lower(), 30, mode + " diagnosed")
                assert "unknown connection" not in live.text()
        s.passed("hostile-" + mode)

    with liar(a, "headerflood") as (address, status):
        with s.follow(address + "," + a.peer, "log", label="headerflood") as live:
            live.tip(a.height())
            wait_until(lambda: status["sent"] == 40, 10, "header claims sent")
            a.mine(1)
            live.tip(a.height(), timeout=100)
            assert live.text().count("headers complete") <= 4, live.text()
    s.passed("empty-and-known-header-flood", claims=40)
    a.mine(3)
    with liar(a, "wrongbody") as (address, status):
        with s.follow(address + "," + a.peer, label="wrongbody") as live:
            live.tip(a.height(), timeout=120)
            assert status["sent"] > 0, status
            assert "dropping peer" in live.text(), live.text()
    s.hashes(a, [a.height()])
    s.passed("invalid-body-refetched-from-honest-peer")

    # Hold all eight outbound slots before flooding the Book, so this test
    # never dials the synthetic public addresses carried in addr.
    with liar(a, "addrflood") as (address, status):
        peers = ",".join([address] + [a.peer] * 7)
        with s.follow(peers, "log", label="addrflood") as live:
            live.tip(a.height(), timeout=120)
            wait_until(lambda: status["sent"] == 10000, 20, "address flood")
            time.sleep(2)
            assert "candidate " not in live.text(), "test must not dial synthetic addresses"
            # The cap is also covered by AddressBook verify cases. The live
            # assertion is that gossip cannot interrupt an honest announcement.
            a.mine(1)
            live.tip(a.height(), timeout=120)
    s.passed("address-flood-beside-full-outbound-pool", addresses=10000)

    s.cli("no-dns", "follow", s.root / "no-seeds", error="has no DNS seeds")
    s.cli("bad-inbound", "follow", a.peer, s.data, "inbound:lots", error="inbound")
    s.passed("explicit-bootstrap-and-option-errors")
    refused = free_port()  # Nothing listens on this loopback port.
    with s.follow("127.0.0.1:" + str(refused) + "," + a.peer, label="refused-dial") as live:
        live.tip(a.height())
        assert "peer 0 is " + a.peer in live.text()
    s.passed("refused-candidate-does-not-consume-peer-key")

    port = free_port()
    with s.follow(a.peer, "serve:" + str(port), "inbound:1", label="admission") as live:
        live.tip(a.height())
        for index in range(5):
            socket.create_connection(("127.0.0.1", port), timeout=3).close()
            wait_until(lambda: live.text().count("handshake failed") >= index + 1, 10, "hangup released")
        with socket.create_connection(("127.0.0.1", port), timeout=3) as first:
            wait_until(lambda: live.text().count("dialled us from") >= 6, 10, "pending admission seated")
            with socket.create_connection(("127.0.0.1", port), timeout=3) as second:
                second.settimeout(5)
                assert second.recv(1) == b""
            live.wait("refused an inbound")
        # The next healthy inbound caller must still be serviceable.
        wait_until(lambda: live.text().count("handshake failed") >= 6, 10, "pending admission released")
        with socket.create_connection(("127.0.0.1", port), timeout=3) as good:
            good.settimeout(5)
            greeting(good)
            nonce = struct.pack("<Q", 42)
            good.sendall(wire("ping", nonce))
            while (reply := receive(good))[0] != "pong":
                pass
            assert reply[1] == nonce
    s.passed("hangups-pending-host-cap-and-subsequent-peer")
