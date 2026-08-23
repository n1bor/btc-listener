#!/usr/bin/env python3
"""Turn Bitcoin Core's sighash.json into Aver verify cases.

Five hundred random Transactions with random scripts, Input indexes and hash
types, each with the message the reference client produces for it.  They are
the only test of the legacy signature hash that was not written by the same
hand as the code, which is the whole reason to have them.

Unlike its two siblings this tool needs no middle step and asks the engine
nothing: Core supplies the expected value.  The corpus is therefore a
conformance test rather than a regression one, and a case that fails means
this engine disagrees with the reference client about a signature hash.

Two things change on the way in, both lossless and both recorded in the
corpus's own header:

  * Core writes the expected hash the way a Transaction Id is written, back to
    front.  The corpus holds it in the order the bytes are hashed.
  * Core's hash types are signed integers and half of them are negative.  The
    corpus holds the four byte value that actually gets serialised, which is
    the same bytes and the same answer.

    python3 tools/sighash_tests_to_aver.py --fetch
    python3 tools/sighash_tests_to_aver.py --emit
    python3 tools/sighash_tests_to_aver.py --check   # regenerate and diff
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "script_tests_data")
OUT = os.path.abspath(os.path.join(HERE, "..", "domain"))

CORE_JSON = "https://raw.githubusercontent.com/bitcoin/bitcoin/master/src/test/data/sighash.json"

PER_FILE = 250
WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}

HEADER = '''module SighashCases%(k)d
    intent =
        "Bitcoin Core's own sighash.json, converted into cases: part %(k)d of %(n)s."
        "Five hundred random Transactions with random scripts, input indexes and"
        "hash types, each with the message the reference client produces for it."
        "Nothing here was written by hand and nothing should be: it is a corpus,"
        "and the only edits it should ever get are regenerated ones."
        "Two files rather than one because five hundred cases do not fit inside"
        "the five hundred line limit, and one case per line is the only layout"
        "that lets a failure name the vector that failed."
        "Two things were changed on the way in, both of them lossless. Core"
        "writes the expected hash the way a Transaction Id is written, back to"
        "front, and these are in the order the bytes are hashed. And Core's hash"
        "types are signed integers, half of them negative; they are given here"
        "as the four byte value that actually gets serialised, which is the same"
        "bytes and the same answer."
    exposes [check]
    depends [Domain.Sighash]
    effects []

fn check(rawHex: String, inputIndex: Int, scriptHex: String, hashType: Int) -> Result<String, String>
    ? "One vector: a Transaction, the script being signed, which Input, and how."
    Domain.Sighash.legacyOfRaw(rawHex, inputIndex, scriptHex, hashType)

verify check'''


def fetch():
    import urllib.request
    os.makedirs(DATA, exist_ok=True)
    urllib.request.urlretrieve(CORE_JSON, os.path.join(DATA, "sighash.json"))
    print("fetched sighash.json")


def unsigned(hash_type):
    """Core's hash types are signed; what gets serialised is four bytes."""
    return hash_type + (1 << 32) if hash_type < 0 else hash_type


def hashed_order(hex_hash):
    """Core writes it as a Transaction Id is written, back to front."""
    return bytes.fromhex(hex_hash)[::-1].hex()


def rows():
    for row in json.load(open(os.path.join(DATA, "sighash.json"))):
        if len(row) < 5:
            continue                       # the column-heading row
        raw, script, index, hash_type, expected = row[:5]
        yield raw, index, script, unsigned(hash_type), hashed_order(expected)


FROMHEX_ERR = 'Result.Err("Bytes.fromHex: invalid hexadecimal character \'z\'")'


def files():
    cases = list(rows())
    parts = [cases[i:i + PER_FILE] for i in range(0, len(cases), PER_FILE)]
    out = {}
    for k, chunk in enumerate(parts, start=1):
        lines = ['    check("%s", %d, "%s", %d) => Result.Ok("%s")' % c for c in chunk]
        lines.append('    // The refusal arm, which no row of the file can reach: the raw hex must parse.')
        lines.append('    check("zz", 0, "", 1) => %s' % FROMHEX_ERR)
        out["sighashcases%d.av" % k] = (
            HEADER % {"k": k, "n": WORDS.get(len(parts), str(len(parts)))}
            + "\n" + "\n".join(lines) + "\n")
    return out


def emit():
    for name, text in files().items():
        open(os.path.join(OUT, name), "w").write(text)
        print("wrote %s" % name)
    return 0


def check():
    """Regenerate in memory and compare with what is on disk.

    The corpus already exists and passes, so a tool that reproduces it exactly
    is a tool that would have produced it -- which is the only way to test a
    generator whose output is already in the tree.
    """
    bad = 0
    for name, text in files().items():
        path = os.path.join(OUT, name)
        have = open(path).read() if os.path.exists(path) else None
        if have == text:
            print("%s matches" % name)
        else:
            bad += 1
            print("%s DIFFERS" % name)
            if have is not None:
                a, b = have.split("\n"), text.split("\n")
                for i in range(max(len(a), len(b))):
                    x = a[i] if i < len(a) else "(missing)"
                    y = b[i] if i < len(b) else "(missing)"
                    if x != y:
                        print("  line %d\n    on disk: %s\n    would write: %s" % (i + 1, x[:120], y[:120]))
                        break
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.fetch:
        fetch()
    if a.emit:
        return emit()
    if a.check:
        return check()
    if not a.fetch:
        ap.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
