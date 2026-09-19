"""One getdata larger than a peer's outbox, for the native acceptance suite.

Framed messages wait in a per-peer FIFO bounded to 8 MiB (#361), and a getdata
that named more Blocks than that used to be answered by framing all of them in
one turn: the queue overflowed and the peer that asked was dropped for it. Core
in initial block download asks one peer for sixteen Blocks at a time, so on
mainnet that is three to six Blocks in and the sync partner disappears. Regtest
Blocks are a few hundred bytes, which is why every other case here passes
through the same path without noticing, so this one mines Blocks big enough to
fill the queue and asks for all of them at once.
"""
import json
import hashlib
import socket
import struct
import time

from suite_support import free_port, greeting, receive, wire

# Twelve Blocks of about 960 KB come to some 11 MiB, comfortably past the 8 MiB
# a peer's outbox holds. The size per Block is what a Block can be: consensus
# weighs every non-witness byte four times, so a Block whose payload is output
# scripts cannot pass one million bytes however many of them there are.
BLOCKS = 12
OUTPUTS = 120
OUTPUT_BYTES = 8000


def compact_size(n):
    return bytes([n]) if n < 253 else b"\xfd" + struct.pack("<H", n)


def op_return(size):
    # OP_RETURN is never executed, so the script size limits that apply to a
    # spend do not apply here and the output is provably unspendable.
    return b"\x6a\x4d" + struct.pack("<H", size) + bytes(size)


def raw_transaction(txid, vout, scripts):
    body = struct.pack("<i", 2) + compact_size(1) + bytes.fromhex(txid)[::-1] + struct.pack("<I", vout)
    body += compact_size(0) + struct.pack("<I", 0xFFFFFFFD) + compact_size(len(scripts))
    for script in scripts:
        body += struct.pack("<q", 0) + compact_size(len(script)) + script
    return (body + struct.pack("<I", 0)).hex()


def block_id(payload):
    return hashlib.sha256(hashlib.sha256(payload[:80]).digest()).digest()[::-1].hex()


def wide_blocks(core):
    """Mine Blocks that are large the only way a regtest chain can make them.

    generateblock takes raw transactions and validates them under consensus
    rules alone, so a transaction whose outputs are one large OP_RETURN each
    goes in without meeting any standardness rule about data carriers. The
    whole input value becomes fee, which no rule minds on a chain whose
    coinbases are worth nothing.
    """
    address = core.rpc("getnewaddress")
    spendable = [u for u in core.rpc("listunspent") if u["confirmations"] >= 100][:BLOCKS]
    assert len(spendable) == BLOCKS, "not enough mature coinbases to fill the outbox"
    mined = []
    for utxo in spendable:
        raw = raw_transaction(utxo["txid"], utxo["vout"], [op_return(OUTPUT_BYTES)] * OUTPUTS)
        signed = core.rpc("signrawtransactionwithwallet", stdin=[raw])
        assert signed["complete"], signed
        mined.append(core.rpc("generateblock", address, stdin=[json.dumps([signed["hex"]])])["hash"])
    return mined


def exercise(s, core, data):
    mined = wide_blocks(core)
    total = sum(len(core.rpc("getblock", one, 0)) // 2 for one in mined)
    assert total > 8 * 1024 * 1024, total
    # Asked for newest first, which is not the order they are stored in, so the
    # reply order is the one the getdata gave rather than a coincidence.
    asked = list(reversed(mined))
    served = free_port()
    with s.follow(core.peer, "serve:" + str(served), "log", data=data, label="backpressure") as live:
        live.tip(core.height(), timeout=180)
        with socket.create_connection(("127.0.0.1", served), timeout=10) as peer:
            peer.settimeout(60)
            greeting(peer)
            # The witness Block type, which is the one Core asks with.
            body = compact_size(len(asked)) + b"".join(
                struct.pack("<I", 1073741826) + bytes.fromhex(one)[::-1] for one in asked)
            started = time.monotonic()
            peer.sendall(wire("getdata", body))
            arrived, bytes_read = [], 0
            while len(arrived) < len(asked):
                command, payload = receive(peer)
                if command == "ping":
                    peer.sendall(wire("pong", payload))
                elif command == "getheaders":
                    peer.sendall(wire("headers", b"\0"))
                elif command == "block":
                    arrived.append(block_id(payload))
                    bytes_read += len(payload)
            assert arrived == asked, (arrived, asked)
            took = time.monotonic() - started
            # Still seated: the outbox emptied rather than overflowing, and the
            # peer that asked for 11 MiB is the one answering pings afterwards.
            nonce = struct.pack("<Q", 361)
            peer.sendall(wire("ping", nonce))
            while (answer := receive(peer))[0] != "pong":
                pass
            assert answer[1] == nonce
        assert "peer outbox is full" not in live.text(), live.text()
    s.passed("one-getdata-larger-than-the-peer-outbox", blocks=len(asked), bytes=bytes_read,
             seconds=round(took, 2))
