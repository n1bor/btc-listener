#!/usr/bin/env python3
"""Repeatable Bitcoin Core acceptance on fresh, isolated regtest nodes.

No public network, shared node data or hard-coded chain heights. All process
logs and the machine-readable report remain in --output after cleanup.
"""
import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from suite_support import Core, Follow, Suite, wait_until, free_port, wire, receive, greeting


def baseline(s, a):
    a.rpc("createwallet", "suite")
    a.mine(150)
    for kind, amount in [("bech32", "1.0"), ("bech32m", "0.5"), ("legacy", "0.25"), ("p2sh-segwit", "0.1")]:
        address = a.rpc("getnewaddress", "", kind)
        for _ in range(5):
            a.rpc("sendtoaddress", address, amount)
    a.mine(10)
    for name, args in [
        ("headers", [a.peer, s.data]),
        ("bodies", [a.peer, s.data, 1, 160]),
        ("txindex", [s.data, 1, 160]),
        ("outputs", [s.data, 1, 160]),
        ("utxo", [s.data, 160]),
    ]:
        s.cli("baseline-" + name, name, *args)
    audit = s.cli("baseline-audit", "audit", s.data, 1, 160)
    assert "transactions 180" in audit and "scripts 20 passed / 0 failed / 0 undecided" in audit
    s.passed("commands-and-four-script-types", blocks=160, spends=20)


def reorg(s, a):
    a.rpc("invalidateblock", a.rpc("getblockhash", 156))
    a.mine(12)
    with s.follow(a.peer, label="reorg") as live:
        live.tip(167)
        assert "disconnecting 5 Block(s)" in live.text()
    s.hashes(a, [156, 167])
    assert "scripts 20 passed / 0 failed / 0 undecided" in s.cli("reorg-audit", "audit", s.data, 1, 167)
    s.passed("five-block-reorg-and-undo", tip=167)


def relay_and_compact(s, a, b, c):
    b.sync(a)
    b.disconnect_all()
    assert b.rpc("getconnectioncount") == 0
    served = free_port()
    board = free_port()
    with s.follow(a.peer + "," + b.peer, "serve:" + str(served), "http:" + str(board), "log", label="relay") as live:
        live.tip(a.height())
        # Wait for both startup handshakes before creating transactions.
        live.wait("peer 1 is")
        txids = [a.rpc("sendtoaddress", a.rpc("getnewaddress", "", "bech32"), str(.11 + i * .01)) for i in range(3)]
        for txid in txids:
            live.wait("mempool admitted " + txid[:8], timeout=90)
        wait_until(lambda: all(t in b.rpc("getrawmempool") for t in txids), 90, "transaction relay to isolated Core B")
        s.passed("mempool-relay", transactions=3)
        with socket.create_connection(("127.0.0.1", board), timeout=5) as http:
            http.sendall(b"GET / HTTP/1.1\r\n")
            time.sleep(.05)
            http.sendall(b"Host: localhost\r\n\r\n")
            response = b""
            while chunk := http.recv(65536):
                response += chunk
        header, body = response.split(b"\r\n\r\n", 1)
        assert header.startswith(b"HTTP/1.1 200")
        length = next(line for line in header.split(b"\r\n") if line.lower().startswith(b"content-length:"))
        assert len(body) == int(length.split(b":")[1])
        s.passed("fragmented-dashboard", bytes=len(body))
        a.mine(1)
        live.tip(a.height())
        live.wait("holding 0 (0 bytes)", timeout=30)
        live.wait("0 fetched", timeout=30)
        s.passed("compact-block-and-mempool-confirmation")
        # Core C has no blocks and can learn them only from our inbound server.
        c.rpc("addnode", "127.0.0.1:" + str(served), "onetry")
        wait_until(lambda: c.rpc("getbestblockhash") == a.rpc("getbestblockhash"), 90, "inbound full-chain serving")
        s.passed("inbound-core-sync", blocks=c.height())
        # Release the existing same-host inbound before testing a pending greeting.
        closed_before = live.text().count("closed the connection")
        c.disconnect_all()
        wait_until(lambda: live.text().count("closed the connection") > closed_before, 10, "served Core disconnected")
        admissions = live.text().count("dialled us from")
        # A silent pending greeting and an established peer disappearing together (#304).
        with socket.create_connection(("127.0.0.1", served), timeout=5) as pending:
            wait_until(lambda: live.text().count("dialled us from") > admissions, 10, "silent inbound admitted")
            closed = live.text().count("closed the connection")
            b.disconnect_all()
            wait_until(lambda: live.text().count("closed the connection") > closed, 10, "established peer disconnected")
            a.mine(1)
            live.tip(a.height())
            assert "unknown connection" not in live.text()
        s.passed("disconnect-during-pending-handshake")
    s.hashes(a, [a.height()])


def restored_mempool(s, a, b):
    b.sync(a)
    b.disconnect_all()
    b.rpc("setnetworkactive", False)
    start = a.height()
    with s.follow(a.peer, label="abandoned-tx") as live:
        live.tip(start)
        txid = a.rpc("sendtoaddress", a.rpc("getnewaddress"), "0.55")
        live.wait("mempool admitted " + txid[:8], timeout=90)
        a.mine(1)
        live.tip(start + 1)
        live.wait("holding 0 (0 bytes)")
    b.rpc("createwallet", "fork")
    b.mine(2)
    b.rpc("setnetworkactive", True)
    # A is stale; B must trigger catch-up from the height retained at handshake.
    with s.follow(a.peer + "," + b.peer, label="restore-tx") as live:
        live.tip(start + 2)
        live.wait("offered back from disconnected Block(s), 1 admitted", timeout=30)
    s.hashes(b, [start + 1, start + 2])
    s.passed("abandoned-branch-transaction-restored")
    # Rejoin A through our server while it is still on the abandoned fork.
    port = free_port()
    with s.follow(b.peer, "serve:" + str(port), label="serve-fork") as live:
        live.tip(b.height())
        a.rpc("addnode", "127.0.0.1:" + str(port), "onetry")
        wait_until(lambda: a.rpc("getbestblockhash") == b.rpc("getbestblockhash"), 90, "serving winning branch to old-fork Core")
    s.passed("serve-abandoned-fork")


def catchup(s, a, b, c):
    a.mine(2000 - a.height())
    b.sync(a)
    c.sync(a)
    b.disconnect_all()
    c.disconnect_all()
    data = s.root / "catchup"
    peers = ",".join(n.peer for n in (a, b, c))
    s.cli("catchup-headers", "headers", a.peer, data)
    with s.follow(peers, data=data, label="catchup") as live:
        live.wait("utxo    connecting", timeout=120)
        # The only announcement is sent while the owner is in its Set walk.
        assert "following at Height 2000" not in live.text(), "catch-up already finished before stimulus"
        a.mine(1)
        b.disconnect_all()
        live.tip(2001, timeout=180)
        assert "unknown connection" not in live.text()
    s.hashes(a, [2001], data)
    s.cli("catchup-txindex", "txindex", data, 1, 2001, timeout=180)
    audit = s.cli("catchup-audit", "audit", data, 1, 2001, timeout=180)
    assert "unresolved 0" in audit and "0 failed / 0 undecided" in audit
    s.passed("multi-peer-catchup-announcement-and-disconnect", tip=2001)
    # Retain this fresh full-chain store for storage guard checks.
    return data


def storage(s, a, data):
    height = a.height()
    floor = height - 287
    s.cli("prune-above-set", "prune", data, height + 1, error="has not reached")
    s.cli("prune-inside-undo", "prune", data, floor + 1, error="Undo Data")
    s.cli("prune-floor", "prune", data, floor)
    headers = s.root / "pruned-gap"
    s.cli("gap-headers", "headers", a.peer, headers)
    s.cli("gap-prune", "prune", headers, 200)
    s.cli("gap-utxo", "utxo", headers, 300, error="was pruned")
    s.passed("prune-set-and-undo-boundaries")
    text = s.cli("assumevalid", "assumevalid", data, height - 10)
    assert "Nothing runs Scripts on the connect path" in text
    text = s.cli("assumevalid-utxo", "utxo", data, height)
    assert "runs no Scripts at any Height" in text
    s.passed("assumevalid-description")


def hostile(s, a):
    from suite_hostile import exercise
    exercise(s, a)


def serving(s, a, data):
    from suite_serving import exercise
    exercise(s, a, data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--core-bin", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    with ExitStack() as stack:
        s = Suite(args.binary.resolve(), args.output.resolve())
        (s.root / "environment.json").write_text(json.dumps({
            "binary_sha256": hashlib.sha256(s.binary.read_bytes()).hexdigest(),
            "core_version": subprocess.check_output([str(args.core_bin / "bitcoind"), "-version", "-nosettings"], text=True).splitlines()[0],
        }, indent=2) + "\n")
        nodes = [stack.enter_context(Core(args.core_bin.resolve(), s.root / name)) for name in ("core-a", "core-b", "core-c")]
        a, b, c = nodes
        try:
            baseline(s, a)
            reorg(s, a)
            relay_and_compact(s, a, b, c)
            restored_mempool(s, a, b)
            data = catchup(s, a, b, c)
            s.data = data
            hostile(s, a)
            from suite_screen import screen, chain_fault
            screen(s, a)
            chain_fault(s, a)
            storage(s, a, data)
            serving(s, a, data)
            s.passed("suite-complete")
        except BaseException as error:
            s.report.append({"case": "suite", "status": "failed", "error": str(error)})
            raise
        finally:
            (s.root / "report.json").write_text(json.dumps(s.report, indent=2) + "\n")


if __name__ == "__main__":
    main()
