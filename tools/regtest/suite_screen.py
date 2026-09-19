"""Real PTY acceptance for the owner Screen, plus on-disk fault attribution."""
import fcntl
import os
import pty
import re
import shutil
import signal
import socket
import struct
import subprocess
import termios
import threading
import time

from suite_support import free_port, greeting, wait_until


def screen(s, core):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 180, 0, 0))
    before = termios.tcgetattr(slave)
    capture = bytearray()
    stopped = threading.Event()
    os.set_blocking(master, False)
    port = free_port()
    process = subprocess.Popen([str(s.binary), "regtest", "follow", core.peer, str(s.data), "screen", "serve:" + str(port), "log"],
                               stdin=slave, stdout=slave, stderr=slave)
    def read():
        while not stopped.wait(.01):
            try:
                capture.extend(os.read(master, 65536))
            except BlockingIOError:
                pass
            except OSError:
                return
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    def text():
        assert process.poll() is None, bytes(capture).decode(errors="replace")
        return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", bytes(capture).decode(errors="replace"))
    try:
        wait_until(lambda: "o overview" in text(), 30, "Screen frame")
        # Wait for live phase; a transaction received during catch-up is a different test.
        wait_until(lambda: "listen" in text().lower(), 30, "Screen live phase")
        with socket.create_connection(("127.0.0.1", port), timeout=5) as inbound:
            inbound.settimeout(5)
            greeting(inbound)
            wait_until(lambda: "1 out, 1 in" in text(), 10, "inbound peer visible on Screen")
            txid = core.rpc("sendtoaddress", core.rpc("getnewaddress"), "0.07")
            wait_until(lambda: txid in text() and "in Mempool" in text(), 90, "unconfirmed Screen row")
            marker = len(capture)
            core.mine(1)
            wait_until(lambda: txid in bytes(capture[marker:]).decode(errors="replace")
                       and re.search(re.escape(txid) + r" fee [0-9]+ sat(?!  in Mempool)", text()[len(re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", bytes(capture[:marker]).decode(errors="replace"))):]) is not None,
                       60, "same Screen row confirmed")
        # Each quiet frame must arrive within a bounded deadline. A fixed
        # three-second sleep races the one-second poll, provider work and the
        # PTY reader on a shared CI runner; two frames can straddle its edge.
        for _ in range(2):
            frames = text().count("o overview")
            wait_until(lambda: text().count("o overview") > frames,
                       5, "next periodic Screen frame")
        for forbidden in ("mempool admitted", "mempool refused", "dropping peer", "closed the connection", "did not answer", "compact Block"):
            assert forbidden not in text(), forbidden + " leaked into Screen"
        os.write(master, b"q")
        wait_until(lambda: "Stop the node? y/n" in text(), 5, "quit confirmation")
        os.write(master, b"y")
        assert process.wait(timeout=5) == 0
        assert termios.tcgetattr(slave) == before, "terminal settings were not restored"
        s.passed("pty-screen-peer-mempool-confirmation-redraw-and-restoration")
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        stopped.set()
        reader.join()
        (s.root / "screen.log").write_bytes(capture)
        os.close(master)
        os.close(slave)


def chain_fault(s, core):
    broken = s.root / "broken-chain"
    shutil.copytree(s.data, broken)
    height = core.height()
    raw = bytes.fromhex(core.rpc("getblock", core.rpc("getblockhash", height), 0))
    changed = False
    for segment in sorted((broken / "blocks").glob("blk*.dat")):
        data = segment.read_bytes()
        at = data.find(raw)
        if at >= 0:
            corrupt = bytearray(data)
            corrupt[at + 4] ^= 1  # Change Header parent: stored hash must no longer match.
            segment.write_bytes(corrupt)
            changed = True
            break
    assert changed, "tip Block not found in cloned Segment"
    core.rpc("invalidateblock", core.rpc("getblockhash", height))
    core.mine(2)
    text = s.cli("chain-fault", "follow", core.peer, broken, error="no Peer is at fault")
    assert "dropping peer" not in text
    with s.follow(core.peer, label="healthy-after-disk-fault") as live:
        live.tip(core.height())
    s.hashes(core, [core.height()])
    s.passed("corrupt-local-body-does-not-blame-honest-peer")
