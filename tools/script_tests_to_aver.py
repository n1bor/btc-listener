#!/usr/bin/env python3
"""Turn Bitcoin Core's script_tests.json into Aver verify cases.

Aver cannot read JSON at verify time, so the corpus has to be compiled into
source.  This does that, and nothing else: it does not decide what the answer
should be.  The expected answers come from running the engine over the
assembled scripts, and the point of the exercise is the report this prints —
how many of Core's cases we agree with, and exactly which ones we do not and
why.

Usage:
    python3 tools/script_tests_to_aver.py --fetch          # refresh the inputs
    python3 tools/script_tests_to_aver.py --assemble OUT   # write cases.tsv
    python3 tools/script_tests_to_aver.py --emit RESULTS   # write the .av files

The middle step is a compiled Aver program: assembling is Python's job,
answering is the engine's, and keeping them apart is what stops this from
being a test that agrees with itself.
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "script_tests_data")

CORE_JSON = "https://raw.githubusercontent.com/bitcoin/bitcoin/master/src/test/data/script_tests.json"
CORE_HEADER = "https://raw.githubusercontent.com/bitcoin/bitcoin/master/src/script/script.h"

# Scripts longer than this exhaust the verify VM's step budget.  Nothing to do
# with consensus, which allows ten thousand bytes.
VM_SCRIPT_LIMIT = 1000


def fetch():
    import urllib.request
    os.makedirs(DATA, exist_ok=True)
    for url, name in ((CORE_JSON, "script_tests.json"), (CORE_HEADER, "script.h")):
        with urllib.request.urlopen(url) as r:
            open(os.path.join(DATA, name), "wb").write(r.read())
        print("fetched", name)


def opcode_names():
    """Core's own enum, so the names come from the source rather than memory."""
    txt = open(os.path.join(DATA, "script.h")).read()
    body = re.search(r"enum opcodetype\s*\{(.*?)\n\};", txt, re.S).group(1)
    ops, cur = {}, None
    for line in body.split("\n"):
        line = line.split("//")[0].strip().rstrip(",")
        if not line:
            continue
        m = re.match(r"^(OP_[A-Z0-9_]+)\s*=\s*(0x[0-9a-fA-F]+|\d+)$", line)
        if m:
            cur = int(m.group(2), 0)
            ops[m.group(1)] = cur
            continue
        m = re.match(r"^(OP_[A-Z0-9_]+)$", line)
        if m and cur is not None:
            cur += 1
            ops[m.group(1)] = cur
    # ParseScript only accepts names from OP_NOP upward, plus OP_RESERVED, and
    # accepts them with or without the prefix.  Everything below that is
    # written as a number or as raw hex.
    names = {}
    for name, code in ops.items():
        if code < ops["OP_NOP"] and code != ops["OP_RESERVED"]:
            continue
        names[name] = code
        names[name[3:]] = code
    return names


def push(data):
    """Core's CScript::operator<<(vector), which is the minimal push."""
    n = len(data)
    if n < 76:
        return bytes([n]) + data
    if n <= 0xFF:
        return bytes([76, n]) + data
    if n <= 0xFFFF:
        return bytes([77]) + n.to_bytes(2, "little") + data
    return bytes([78]) + n.to_bytes(4, "little") + data


def script_num(n):
    """CScriptNum::serialize: little-endian, sign in the top bit of the last byte."""
    if n == 0:
        return b""
    out, neg, abs_n = bytearray(), n < 0, abs(n)
    while abs_n:
        out.append(abs_n & 0xFF)
        abs_n >>= 8
    if out[-1] & 0x80:
        out.append(0x80 if neg else 0x00)
    elif neg:
        out[-1] |= 0x80
    return bytes(out)


def parse_script(text, names):
    """Core's ParseScript, from core_read.cpp."""
    out = bytearray()
    for word in text.split():
        if re.fullmatch(r"-?\d+", word):
            n = int(word)
            if n == -1 or 1 <= n <= 16:
                out.append(n + 0x50 if n > 0 else 0x4F)
            elif n == 0:
                out.append(0x00)
            else:
                out += push(script_num(n))
        elif word.startswith("0x") and re.fullmatch(r"0x[0-9a-fA-F]*", word):
            out += bytes.fromhex(word[2:])
        elif len(word) >= 2 and word[0] == "'" and word[-1] == "'":
            out += push(word[1:-1].encode())
        elif word in names:
            out.append(names[word])
        else:
            raise ValueError("cannot parse word %r" % word)
    return bytes(out)


def rows():
    data = json.load(open(os.path.join(DATA, "script_tests.json")))
    names = opcode_names()
    for row in data:
        if len(row) < 4 or isinstance(row[0], list):
            # A comment line, or a segwit case carrying a witness and an amount.
            # Segwit needs the witness program run, which this engine does not
            # do, so those are out of scope rather than skipped quietly.
            if isinstance(row[0], list):
                yield None, None, None, None, "witness"
            continue
        sig, pubkey, flags, expected = row[0], row[1], row[2], row[3]
        try:
            yield parse_script(sig, names).hex(), parse_script(pubkey, names).hex(), flags, expected, None
        except ValueError as e:
            yield None, None, flags, expected, str(e)


def assemble(path):
    kept = skipped = 0
    with open(path, "w") as f:
        for sig, pubkey, flags, expected, why in rows():
            if why is not None:
                skipped += 1
                continue
            f.write("%s\t%s\t%s\t%s\n" % (sig, pubkey, flags, expected))
            kept += 1
    print("assembled %d cases, skipped %d" % (kept, skipped))


HEADER = '''module ScriptCases%(k)d
    intent =
        "Bitcoin Core's own script_tests.json, converted into cases: part %(k)d of %(n)d."
        "Adversarial in a way nothing written here would be. Every case is a"
        "pair of Scripts that Core runs together, and the answer recorded is"
        "this engine's, not Core's -- see tools/script_tests_to_aver.py and the"
        "agreement report in ADR 0005 for what the difference between the two"
        "amounts to and why."
        "Nothing here was written by hand and nothing should be. Regenerate it."
    exposes [case]
    depends [Domain.SpendScript]
    effects []

fn case(inputScriptHex: String, outputScriptHex: String) -> Outcome
    ? "One pair of Scripts, run the way a spend runs them."
    Domain.SpendScript.check(inputScriptHex, outputScriptHex)

verify case'''


def emit(results_path, out_dir, per_file=250):
    """results_path is sigHex<TAB>pubkeyHex<TAB>verdict<TAB>detail from the engine."""
    lines = [l.rstrip("\n").split("\t") for l in open(results_path)
             if l.strip() and not l.startswith("#")]
    cases, oversized = [], 0
    for sig, pubkey, verdict, detail in lines:
        # `aver verify` runs on the VM, which has a million-step budget, and a
        # Script of several thousand bytes exceeds it.  The compiled engine has
        # no such limit and answers these; they are left out of the corpus
        # rather than left in it failing.  See the agreement report in ADR 0005.
        if max(len(sig), len(pubkey)) // 2 > VM_SCRIPT_LIMIT:
            oversized += 1
            continue
        if verdict == "PASSED":
            expect = "Outcome.Decided(Ending.Passed)"
        elif verdict == "FAILED":
            expect = 'Outcome.Decided(Ending.Failed("%s"))' % detail
        else:
            expect = 'Outcome.Undecided("%s")' % detail
        cases.append('    case("%s", "%s") => %s' % (sig, pubkey, expect))
    if oversized:
        print("left out %d case(s) too long for the verify VM" % oversized)
    parts = [cases[i:i + per_file] for i in range(0, len(cases), per_file)]
    for k, chunk in enumerate(parts, start=1):
        path = os.path.join(out_dir, "scriptcases%d.av" % k)
        open(path, "w").write(HEADER % {"k": k, "n": len(parts)} + "\n" + "\n".join(chunk) + "\n")
        print("wrote %s with %d cases" % (path, len(chunk)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--assemble")
    ap.add_argument("--emit")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "domain"))
    a = ap.parse_args()
    if a.fetch:
        fetch()
    if a.assemble:
        assemble(a.assemble)
    if a.emit:
        emit(a.emit, a.out)
    if not (a.fetch or a.assemble or a.emit):
        ap.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
