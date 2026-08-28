#!/usr/bin/env python3
"""Turn Bitcoin Core's key_io_invalid.json into Aver verify cases.

Every entry is a string that must not decode as an address on any chain.  The
file has no expected column to disagree with, which makes it unusually cheap to
score: an entry that decodes is a bug.

It is also the corpus most easily faked.  A decoder that refuses everything
passes all of it, so this file is only worth anything read together with
key_io_valid.json, where the same function has to *succeed* on 54 entries and
return the exact Output Script Core records.  Neither half means much alone.

The question asked is "does any Network read this", not "does mainnet read
this".  Several entries are well formed addresses for the wrong chain, and
asking one Network at a time would let those pass for the wrong reason.

Some entries are WIF private keys, which this project has no notion of.  They
are kept rather than skipped: a string that is a private key is still a string
that must not read as an address, and that is exactly what is asserted.

    python3 tools/key_io_invalid_to_aver.py --fetch
    python3 tools/key_io_invalid_to_aver.py --emit
    python3 tools/key_io_invalid_to_aver.py --check    # regenerate and diff
"""

import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "script_tests_data")
OUT = os.path.abspath(os.path.join(HERE, "..", "corpus"))
URL = ("https://raw.githubusercontent.com/bitcoin/bitcoin/master"
       "/src/test/data/key_io_invalid.json")


def fetch():
    import urllib.request
    os.makedirs(DATA, exist_ok=True)
    urllib.request.urlretrieve(URL, os.path.join(DATA, "key_io_invalid.json"))
    print("fetched key_io_invalid.json")


def rows():
    """The string from every entry."""
    return [entry[0] for entry in
            json.load(open(os.path.join(DATA, "key_io_invalid.json")))]


HEADER = '''module KeyIoInvalidCases
    intent =
        "Bitcoin Core's key_io_invalid.json: strings that are not addresses."
        "A conformance corpus, and the counterweight to keyiocases.av. That"
        "one asks whether a Script comes back as the address Core records;"
        "this one asks whether a string that is nearly an address is refused."
        "Neither is worth much alone. A decoder that refused everything would"
        "pass all of this file and fail every case in the other, and one that"
        "accepted everything would do the reverse. Read together they pin the"
        "decoder from both sides."
        "The question is whether *any* Network reads the string, not whether"
        "mainnet does. Several entries are well formed addresses for the wrong"
        "chain, and asking one Network at a time would pass those for the"
        "wrong reason."
        "The WIF private keys are kept rather than skipped. This project has"
        "no notion of a key, but a string that is a private key is still a"
        "string that must not read as an address."
        "Nothing here was written by hand and nothing should be. Regenerate it"
        "with tools/key_io_invalid_to_aver.py."
    exposes [readsAnywhere]
    depends [Domain.ReadAddress]
    effects []

fn readsAnywhere(address: String) -> Bool
    ? "Whether any of the four Networks reads this string as an address."
    Domain.ReadAddress.readsAnywhere(address)

verify readsAnywhere
%(cases)s
    // The true arm, from key_io_valid.json: a decoder that refused everything would pass every case above.
    readsAnywhere("1BgGZ9tcN4rm9KBzDn7KprQz87SZ26SAMH") => true
'''


def emitted():
    got = rows()
    cases = "\n".join('    readsAnywhere("%s") => false' % escaped(s) for s in got)
    return HEADER % {"cases": cases}, len(got)


def escaped(text):
    return text.replace("\\", "\\\\").replace('"', '\\"')


def emit():
    body, n = emitted()
    path = os.path.join(OUT, "keyioinvalidcases.av")
    open(path, "w").write(body)
    print("wrote %s with %d cases" % (path, n))
    return 0


def check():
    body, n = emitted()
    path = os.path.join(OUT, "keyioinvalidcases.av")
    if not os.path.exists(path):
        print("%s does not exist" % path)
        return 1
    if open(path).read() == body:
        print("%s matches what this tool would write (%d cases)" % (path, n))
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
