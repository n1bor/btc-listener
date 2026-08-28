#!/usr/bin/env python3
"""Turn Bitcoin Core's base58_encode_decode.json into Aver verify cases.

Each entry is a pair: some bytes as hex, and the base58 they are written as.
No checksum and no version byte -- this is the raw encoding underneath
Base58Check, tested in both directions.

A conformance corpus.  Core supplies the answer, so a case that fails means
this engine and Bitcoin Core disagree about base58 itself, which is a
disagreement about arithmetic rather than about Bitcoin.

Both directions are emitted from the same pair, which is the point: an encoder
checked only against its own output would agree with itself forever.

    python3 tools/base58_to_aver.py --fetch
    python3 tools/base58_to_aver.py --emit
    python3 tools/base58_to_aver.py --check    # regenerate and diff
"""

import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "script_tests_data")
OUT = os.path.abspath(os.path.join(HERE, "..", "corpus"))
URL = ("https://raw.githubusercontent.com/bitcoin/bitcoin/master"
       "/src/test/data/base58_encode_decode.json")


def fetch():
    import urllib.request
    os.makedirs(DATA, exist_ok=True)
    urllib.request.urlretrieve(URL, os.path.join(DATA, "base58_encode_decode.json"))
    print("fetched base58_encode_decode.json")


def rows():
    """(byteList, base58) for every pair."""
    out = []
    for hexed, text in json.load(open(os.path.join(DATA, "base58_encode_decode.json"))):
        out.append((list(bytes.fromhex(hexed)), text))
    return out


HEADER = '''module Base58Cases
    intent =
        "Bitcoin Core's base58_encode_decode.json: bytes beside the base58"
        "they are written as, both ways."
        "A conformance corpus. Core supplies the answers, so a case failing"
        "here is a disagreement with Bitcoin Core about base58 itself -- about"
        "arithmetic rather than about Bitcoin."
        "Both directions come off the same pair, which is the reason the file"
        "is worth having at all: an encoder checked only against its own"
        "output agrees with itself forever. The pairs with leading zero bytes"
        "are the ones that earn their place, because a leading zero adds"
        "nothing to the number and has to be carried as a character instead."
        "There is no version byte and no checksum here. That is Base58Check,"
        "which key_io_valid.json and key_io_invalid.json cover."
        "Nothing here was written by hand and nothing should be. Regenerate it"
        "with tools/base58_to_aver.py."
    exposes [written, read]
    depends [Domain.Base58]
    effects []

fn written(bytes: List<Int>) -> String
    ? "The bytes in base 58."
    Domain.Base58.encode(bytes)

verify written
%(written)s

fn read(text: String) -> Result<List<Int>, String>
    ? "The base 58 back as bytes."
    Domain.Base58.decode(text)

verify read
%(read)s
    // The refusal arm, which no row of Core's file can reach: "0" is not in the alphabet.
    read("0") => Result.Err("'0' is not a base 58 character")
'''


def emitted():
    got = rows()
    written = "\n".join('    written(%s) => "%s"' % (aver_list(b), t) for b, t in got)
    read = "\n".join('    read("%s") => Result.Ok(%s)' % (t, aver_list(b)) for b, t in got)
    return HEADER % {"written": written, "read": read}, len(got)


def aver_list(byte_list):
    return "[" + ", ".join(str(b) for b in byte_list) + "]"


def emit():
    body, n = emitted()
    path = os.path.join(OUT, "base58cases.av")
    open(path, "w").write(body)
    print("wrote %s with %d pairs (%d cases)" % (path, n, n * 2))
    return 0


def check():
    body, n = emitted()
    path = os.path.join(OUT, "base58cases.av")
    if not os.path.exists(path):
        print("%s does not exist" % path)
        return 1
    if open(path).read() == body:
        print("%s matches what this tool would write (%d pairs)" % (path, n))
        return 0
    print("%s differs from what this tool would write" % path)
    return 1


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
    raise SystemExit(main())
