#!/usr/bin/env python3
"""Turn a captured compact Block into Aver verify cases.

Domain.CompactBlock's own cases are fixtures this project wrote.  These are
not: every number here came off the wire from Bitcoin Core, captured by
tools/regtest/cmpctblock-capture.py against a regtest node.

What it pins is the part that cannot be checked any other way -- the SipHash
key both nodes derive from the Header and the nonce, and the short ids that
follow from it.  Those are the whole of BIP152's naming scheme, and an
implementation that gets them subtly wrong reconstructs nothing while
reporting no fault at all.

    python3 tools/regtest/cmpctblock-capture.py 127.0.0.1 18444 cap.json
    # mine a Block on that node while it waits
    python3 tools/cmpct_oracle_to_aver.py cap.json
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "domain", "compactblockcases.av")


def octets(hexstr):
    return "[" + ", ".join(str(b) for b in bytes.fromhex(hexstr)) + "]"


def main(path):
    d = json.load(open(path))
    payload = octets(d["payload"])
    header = octets(d["header"])
    reversed_wtxids = [
        "[" + ", ".join(str(b) for b in bytes.fromhex(w)[::-1]) + "]"
        for w in d["wtxids"]
    ]
    lines = [
        "module CompactBlockCases",
        "    intent =",
        '        "One real compact Block, as Bitcoin Core sent it."',
        '        "Captured off the wire on regtest by"',
        '        "tools/regtest/cmpctblock-capture.py, which speaks just enough of the"',
        '        "protocol to be sent one. Every number below is Core\'s, not ours."',
        '        "What it is here to catch is the naming. The SipHash key is derived"',
        '        "from the Header and a nonce, and the six-byte short ids follow from"',
        '        "that key; an implementation that derives either subtly wrongly --"',
        '        "the double SHA-256 instead of the single, the Transaction Id instead"',
        '        "of the Witness Transaction Id, the bytes in reading order instead of"',
        '        "wire order -- matches nothing, reconstructs nothing, and reports no"',
        '        "fault while doing it. Our own fixtures cannot catch that. Core can."',
        f'        "The Block is {d["blockid"][:16]}..., {d["ntx"]} Transactions: a coinbase"',
        f'        "sent whole and {len(d["wtxids"])} named by short id."',
        '        "Regenerate with tools/cmpct_oracle_to_aver.py; the capture needs a"',
        '        "regtest node, so this file is committed rather than rebuilt in CI."',
        "    exposes [key, shortIdFor, decodedNonce, decodedShortIds, decodedPrefilled, rebuiltRoot, wantedNothingHeld]",
        "    depends [Domain.CompactBlock]",
        "    effects []",
        "",
        "fn payload() -> List<Int>",
        '    ? "The cmpctblock Message body, exactly as it arrived."',
        f"    {payload}",
        "",
        "verify payload",
        f"    List.len(payload()) => {len(bytes.fromhex(d['payload']))}",
        "",
        "fn header() -> List<Int>",
        '    ? "Its eighty Header bytes."',
        f"    {header}",
        "",
        "verify header",
        "    List.len(header()) => 80",
        "",
        "fn key() -> Tuple<Int, Int>",
        '    ? "The SipHash key this Block names Transactions under."',
        f"    Domain.CompactBlock.keyOf(header(), {d['nonce']})",
        "",
        "verify key",
        f"    key() => ({d['k0']}, {d['k1']})",
        "",
        "fn shortIdFor(wtxid: List<Int>) -> Int",
        '    ? "The short id this Block gives one Witness Transaction Id."',
        "    Domain.CompactBlock.shortIdOf(key(), wtxid)",
        "",
        "verify shortIdFor",
    ]
    for wtxid, want in zip(reversed_wtxids, d["shortids"]):
        lines.append(f"    shortIdFor({wtxid}) => {want}")
    lines += [
        "",
        "fn decodedNonce(body: List<Int>) -> Int",
        '    ? "The nonce, read back out of the Message."',
        "    nonceIn(Domain.CompactBlock.decodeCompact(body))",
        "",
        "verify decodedNonce",
        f"    decodedNonce(payload()) => {d['nonce']}",
        "    decodedNonce([]) => 0",
        "",
        "fn nonceIn(decoded: Result<Compact, String>) -> Int",
        '    ? "Zero for a Message that would not decode, which the cases above"',
        '      "distinguish by asking a Message that does."',
        "    match decoded",
        "        Result.Err(why) -> 0",
        "        Result.Ok(compact) -> compact.nonce",
        "",
        "verify nonceIn",
        '    nonceIn(Result.Err("no")) => 0',
        f"    nonceIn(Domain.CompactBlock.decodeCompact(payload())) => {d['nonce']}",
        "",
        "fn decodedShortIds(body: List<Int>) -> List<Int>",
        '    ? "The short ids, read back out of the Message."',
        "    shortIdsIn(Domain.CompactBlock.decodeCompact(body))",
        "",
        "verify decodedShortIds",
        f"    decodedShortIds(payload()) => {d['shortids']}",
        "    decodedShortIds([]) => []",
        "",
        "fn shortIdsIn(decoded: Result<Compact, String>) -> List<Int>",
        '    ? "Empty for a Message that would not decode."',
        "    match decoded",
        "        Result.Err(why) -> []",
        "        Result.Ok(compact) -> compact.shortIds",
        "",
        "verify shortIdsIn",
        '    shortIdsIn(Result.Err("no")) => []',
        f"    shortIdsIn(Domain.CompactBlock.decodeCompact(payload())) => {d['shortids']}",
        "",
        "fn decodedPrefilled(body: List<Int>) -> Int",
        '    ? "How many Transactions came whole, which is at least the coinbase."',
        "    prefilledIn(Domain.CompactBlock.decodeCompact(body))",
        "",
        "verify decodedPrefilled",
        "    decodedPrefilled(payload()) => 1",
        "    decodedPrefilled([]) => 0",
        "",
        "fn prefilledIn(decoded: Result<Compact, String>) -> Int",
        '    ? "Zero for a Message that would not decode."',
        "    match decoded",
        "        Result.Err(why) -> 0",
        "        Result.Ok(compact) -> List.len(compact.prefilled)",
        "",
        "verify prefilledIn",
        '    prefilledIn(Result.Err("no")) => 0',
        "    prefilledIn(Domain.CompactBlock.decodeCompact(payload())) => 1",
        "",
        "fn mempool() -> List<List<Int>>",
        '    ? "The Transactions a node would have been holding unconfirmed."',
        '      "These are the very ones Core expected us to have, which is why it"',
        '      "sent their names rather than their bytes."',
        "    [" + ", ".join(octets(raw) for raw in d["txs_raw"][1:]) + "]",
        "",
        "verify mempool",
        f"    List.len(mempool()) => {len(d['shortids'])}",
        "",
        "fn held() -> Map<Int, List<Int>>",
        '    ? "That Mempool, renamed under this Block\'s key."',
        '      "Built the way the node will build it rather than written down:"',
        '      "each Transaction hashed to its Witness Transaction Id, and that"',
        '      "hashed to a short id under the key. If any step of that is wrong"',
        '      "the keys do not match the ones Core sent, nothing is found, and"',
        '      "the Root case below fails -- which is exactly why this is computed"',
        '      "here rather than pasted in."',
        "    Domain.CompactBlock.holdingsFor(key(), mempool())",
        "",
        "verify held",
        f"    Map.len(held()) => {len(d['shortids'])}",
    ] + [
        f"    Map.has(held(), {sid}) => true" for sid in d["shortids"]
    ] + [
        "",
        "fn rebuiltRoot() -> String",
        '    ? "The Merkle Root of the Block rebuilt from this compact one."',
        '      "The whole of reconstruction in one line: decode what arrived, fill"',
        '      "every position from the prefilled Transactions and the ones we hold,"',
        '      "and hash the result. It has to equal the Root in the Header Core"',
        '      "sent, or the Block we rebuilt is not the Block it meant."',
        "    rootIn(Domain.CompactBlock.decodeCompact(payload()))",
        "",
        "verify rebuiltRoot",
        f'    rebuiltRoot() => "{d["merkleroot"]}"',
        "",
        "fn rootIn(decoded: Result<Compact, String>) -> String",
        '    ? "The Root, or the reason there is not one."',
        "    match decoded",
        "        Result.Err(why) -> why",
        "        Result.Ok(compact) -> rootFrom(Domain.CompactBlock.assembled(compact, held(), []))",
        "",
        "verify rootIn",
        '    rootIn(Result.Err("no")) => "no"',
        "",
        "fn rootFrom(assembled: Result<List<List<Int>>, String>) -> String",
        '    ? "Every position filled, then hashed."',
        "    match assembled",
        "        Result.Err(why) -> why",
        "        Result.Ok(transactions) -> Result.withDefault(Domain.CompactBlock.rootOf(transactions), \"no Root\")",
        "",
        "verify rootFrom",
        '    rootFrom(Result.Err("no")) => "no"',
        "",
        "fn wantedNothingHeld() -> List<Int>",
        '    ? "The positions a node holding nothing would have to ask for."',
        '      "Positions in the Block, which is what a getblocktxn names -- not"',
        '      "offsets into the short id list. Core prefills the coinbase at"',
        '      "position 0, so the short ids here start at 1, and a node that"',
        '      "confused the two would ask for the coinbase and never receive what"',
        '      "was actually missing."',
        "    indexesIn(Domain.CompactBlock.decodeCompact(payload()))",
        "",
        "verify wantedNothingHeld",
        f"    wantedNothingHeld() => {d['wanted_if_none_held']}",
        "",
        "fn indexesIn(decoded: Result<Compact, String>) -> List<Int>",
        '    ? "Empty for a Message that would not decode."',
        "    match decoded",
        "        Result.Err(why) -> []",
        "        Result.Ok(compact) -> Domain.CompactBlock.wantedFrom(compact, []).indexes",
        "",
        "verify indexesIn",
        '    indexesIn(Result.Err("no")) => []',
        f"    indexesIn(Domain.CompactBlock.decodeCompact(payload())) => {d['wanted_if_none_held']}",
    ]
    open(OUT, "w").write("\n".join(lines) + "\n")
    print(f"wrote {OUT}")


main(sys.argv[1])
