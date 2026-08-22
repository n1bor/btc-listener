#!/usr/bin/env python3
"""Turn Bitcoin Core's key_io_valid.json into Aver verify cases.

Each entry is an address string, the Output Script hex it stands for, and
metadata naming the chain.  `Domain.Payto.addressFor` and
`Domain.ReadAddress.scriptFor` are exactly the functions under test, so unlike
the Script and Transaction corpora this one **carries Core's own answer**: it
is a conformance corpus, not a regression one, and a case that fails means this
engine and Bitcoin Core disagree about an address.

Both directions are emitted from the same entry.  The reading half is also what
makes key_io_invalid.json worth anything -- a decoder that refused everything
would pass all 70 of those and fail all 54 of these.

The private-key entries are skipped.  They are WIF, which this project has no
notion of -- there are no keys here, only Scripts -- and skipping them is
counted and reported rather than done quietly.

    python3 tools/key_io_to_aver.py --fetch
    python3 tools/key_io_to_aver.py --emit
    python3 tools/key_io_to_aver.py --check    # regenerate and diff
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "script_tests_data")
OUT = os.path.abspath(os.path.join(HERE, "..", "domain"))
URL = "https://raw.githubusercontent.com/bitcoin/bitcoin/master/src/test/data/key_io_valid.json"

# Core's chain names against this project's Network.  testnet4 shares testnet3's
# address prefixes -- the fork changed the genesis and the difficulty rules, not
# the letters at the front of an address -- so both read as Testnet here.
NETWORKS = {"main": "Mainnet", "test": "Testnet", "testnet": "Testnet",
            "testnet4": "Testnet", "signet": "Signet", "regtest": "Regtest"}


def fetch():
    import urllib.request
    os.makedirs(DATA, exist_ok=True)
    urllib.request.urlretrieve(URL, os.path.join(DATA, "key_io_valid.json"))
    print("fetched key_io_valid.json")


def rows():
    """(address, scriptHex, Network) for every entry that names a Script."""
    out, keys, unknown = [], 0, 0
    for entry in json.load(open(os.path.join(DATA, "key_io_valid.json"))):
        address, payload, meta = entry[0], entry[1], entry[2]
        if meta.get("isPrivkey"):
            keys += 1
            continue
        chain = meta.get("chain")
        if chain not in NETWORKS:
            unknown += 1
            continue
        out.append((address, payload, NETWORKS[chain]))
    return out, keys, unknown


HEADER = '''module KeyIoCases
    intent =
        "Bitcoin Core's key_io_valid.json: an address beside the Output Script"
        "it stands for."
        "A conformance corpus, unlike the Script and Transaction ones. Those"
        "record this engine's own answer and are read for regressions; this"
        "one records Core's, because Core supplies it. A case that fails here"
        "means this engine and Bitcoin Core disagree about an address, which"
        "is not a difference of opinion -- an address is a thing a wallet"
        "pastes into a payment."
        "Both directions, from the same entry. Writing an address and reading"
        "one back are different functions and only one of them has to tell a"
        "correct string from a near miss; key_io_invalid.json is the file full"
        "of near misses, and it means nothing without this half succeeding."
        "Every Network the file names is here, which is what makes it worth"
        "more than the mainnet third: base58 cannot tell testnet, signet and"
        "regtest apart, and Bech32 can, so the prefixes are only really tested"
        "by the entries that use them."
        "The %(keys)d private-key entries are left out. They are WIF, and this"
        "project has no notion of a key -- only of Scripts."
        "Nothing here was written by hand and nothing should be. Regenerate it"
        "with tools/key_io_to_aver.py."
    exposes [case, script]
    depends [Domain.Network, Domain.Payto, Domain.ReadAddress]
    effects []

fn case(network: Network, scriptHex: String) -> Result<String, String>
    ? "The address this Output Script pays to, on this Network."
    Domain.Payto.addressFor(network, scriptHex)

verify case
%(written)s

fn script(network: Network, address: String) -> Result<String, String>
    ? "And the Output Script that address pays to, back again."
      "The half that catches an encoder agreeing with itself. It also decides"
      "what keyioinvalidcases.av is worth: a decoder that refused everything"
      "would pass every case there and none here."
    Domain.ReadAddress.scriptFor(network, address)

verify script
%(read)s'''


def emitted():
    got, keys, unknown = rows()
    written = "\n".join('    case(Network.%s, "%s") => Result.Ok("%s")'
                        % (network, script, address) for address, script, network in got)
    read = "\n".join('    script(Network.%s, "%s") => Result.Ok("%s")'
                     % (network, address, script) for address, script, network in got)
    if unknown:
        print("skipped %d entry(s) naming a chain this project has no Network for" % unknown)
    body = HEADER % {"keys": keys, "written": written, "read": read} + "\n"
    return body, len(got), keys


def emit():
    body, n, keys = emitted()
    path = os.path.join(OUT, "keyiocases.av")
    open(path, "w").write(body)
    print("wrote %s with %d entries, %d cases (%d private keys skipped)" % (path, n, n * 2, keys))
    return 0


def check():
    body, n, keys = emitted()
    path = os.path.join(OUT, "keyiocases.av")
    if not os.path.exists(path):
        print("%s does not exist" % path)
        return 1
    on_disk = open(path).read()
    if on_disk == body:
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
    sys.exit(main())
