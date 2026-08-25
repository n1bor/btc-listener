#!/usr/bin/env python3
"""Turn the SipHash authors' own vectors into Aver verify cases.

The vectors come from veorq/SipHash, the reference implementation by the
algorithm's authors: vectors.h holds the 64-bit SipHash-2-4 of the first N
bytes of 00 01 02 ... under the key 00 01 02 ... 0f, for N of 0 to 63.

A conformance corpus in the sense docs/core-corpora.md means it: the answers
come from outside this project, so a case that fails is a disagreement with
the specification rather than with ourselves.  A hash checked only against its
own output agrees with itself forever, and BIP152 short ids are precisely the
place where agreeing with yourself is worthless -- the other node computes the
same short id or reconstruction fails.

    python3 tools/siphash_vectors_to_aver.py --fetch
    python3 tools/siphash_vectors_to_aver.py --emit
    python3 tools/siphash_vectors_to_aver.py --check    # regenerate and diff
"""

import argparse
import os
import re
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "script_tests_data")
RAW = os.path.join(DATA, "siphash_vectors.h")
OUT = os.path.join(HERE, "..", "domain", "siphashcases.av")
URL = "https://raw.githubusercontent.com/veorq/SipHash/master/vectors.h"

KEY = bytes(range(16))
K0 = int.from_bytes(KEY[:8], "little")
K1 = int.from_bytes(KEY[8:], "little")


def fetch():
    os.makedirs(DATA, exist_ok=True)
    with urllib.request.urlopen(URL) as response:
        open(RAW, "wb").write(response.read())
    print(f"fetched {URL} -> {RAW}")


def vectors():
    body = open(RAW).read()
    body = body[body.index("vectors_sip64"):]
    octets = [int(x, 16) for x in re.findall(r"0x([0-9a-fA-F]{2})", body)][: 64 * 8]
    if len(octets) != 64 * 8:
        sys.exit(f"expected 64 vectors of 8 bytes, found {len(octets)} bytes")
    return [
        int.from_bytes(bytes(octets[i * 8:(i + 1) * 8]), "little") for i in range(64)
    ]


def emit():
    want = vectors()
    lines = [
        "module SipHashCases",
        "    intent =",
        '        "The SipHash-2-4 reference vectors, from the authors\' own"',
        '        "implementation."',
        '        "Every case is the hash of the first N bytes of 00 01 02 ... under"',
        '        "the key 00 01 02 ... 0f, for N of 0 to 63 -- which is the whole of"',
        '        "what veorq/SipHash publishes as vectors.h, converted rather than"',
        '        "summarised. The interesting ones are near the block boundaries: at"',
        '        "N of 7, 8 and 9 the padding word is the only thing that differs,"',
        '        "and an implementation that pads wrongly passes every other case."',
        '        "A conformance corpus. The answers come from outside this project,"',
        '        "so a failure here is a disagreement with the specification, not a"',
        '        "regression against ourselves. That matters more for this hash than"',
        '        "for most: a BIP152 short id is only useful if the node at the other"',
        '        "end computes the same one."',
        '        "Nothing here was written by hand and nothing should be. Regenerate"',
        '        "it with tools/siphash_vectors_to_aver.py."',
        "    exposes [hashed]",
        "    depends [Domain.SipHash]",
        "    effects []",
        "",
        "fn hashed(message: List<Int>) -> Int",
        '    ? "The 64-bit SipHash-2-4 under the reference key."',
        f"    Domain.SipHash.of({K0}, {K1}, message)",
        "",
        "verify hashed",
    ]
    for n in range(64):
        message = "[" + ", ".join(str(b) for b in range(n)) + "]"
        lines.append(f"    hashed({message}) => {want[n]}")
    open(OUT, "w").write("\n".join(lines) + "\n")
    print(f"wrote {OUT}: 64 cases")


def check():
    before = open(OUT).read() if os.path.exists(OUT) else ""
    emit()
    after = open(OUT).read()
    if before != after:
        subprocess.run(["git", "--no-pager", "diff", "--stat", OUT])
        sys.exit("regenerated file differs from the one committed")
    print("up to date")


parser = argparse.ArgumentParser()
parser.add_argument("--fetch", action="store_true")
parser.add_argument("--emit", action="store_true")
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
if args.fetch:
    fetch()
if args.emit:
    emit()
if args.check:
    check()
if not (args.fetch or args.emit or args.check):
    parser.print_help()
