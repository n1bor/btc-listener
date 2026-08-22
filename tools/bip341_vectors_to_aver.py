#!/usr/bin/env python3
"""Turn BIP341's wallet-test-vectors.json into Aver verify cases.

Replaces the hand-extraction that produced an earlier domain/bip341cases.av,
and reads all three sections rather than one.

A conformance corpus.  Every expected value here is the BIP's own, so a case
that fails means this engine disagrees with BIP341 -- not that its behaviour
changed.  Python does no cryptography: it rearranges the file's own fields into
Aver expressions and copies the BIP's answers across unchanged.  The one thing
it does compute is the *shape* of a script tree, which it turns into nested
`joined` and `leafHashOf` calls for the engine to evaluate.

    python3 tools/bip341_vectors_to_aver.py --fetch
    python3 tools/bip341_vectors_to_aver.py --emit
    python3 tools/bip341_vectors_to_aver.py --check    # regenerate and diff
"""

import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "script_tests_data")
OUT = os.path.abspath(os.path.join(HERE, "..", "domain"))
NAME = "bip341-wallet-test-vectors.json"
URL = ("https://raw.githubusercontent.com/bitcoin/bips/master"
       "/bip-0341/wallet-test-vectors.json")


def fetch():
    import urllib.request
    os.makedirs(DATA, exist_ok=True)
    urllib.request.urlretrieve(URL, os.path.join(DATA, NAME))
    print("fetched %s" % NAME)


def vectors():
    return json.load(open(os.path.join(DATA, NAME)))


def spent_list(utxos):
    """The prevouts as an Aver list literal."""
    return "[" + ", ".join(
        'Spent(amount = %d, script = "%s")' % (u["amountSats"], u["scriptPubKey"])
        for u in utxos) + "]"


def tree_expression(node):
    """A script tree as nested Aver calls, or None for an empty tree.

    A leaf is `leafHashOf`; a pair is `joined` over its two sides.  That is
    the construction direction -- the engine has only ever been asked to walk
    a path upward from a leaf, and these are the vectors that ask it to build
    the tree in the first place.  `joined` sorts its pair, so an implementation
    that kept the positions would come apart here on the unbalanced trees.
    """
    if node is None:
        return None
    if isinstance(node, dict):
        return 'Domain.Taproot.leafHashOf(%d, "%s")' % (node["leafVersion"], node["script"])
    left, right = node
    return "Domain.Taproot.joined(%s, %s)" % (tree_expression(left), tree_expression(right))


HEADER = '''module Bip341Cases
    intent =
        "BIP341's own wallet test vectors, all three sections."
        "A conformance corpus, and one of only three in the project -- the"
        "expected values are the BIP's, not this engine's, so a case failing"
        "here is a disagreement with BIP341 rather than a change of behaviour."
        "Six things are asked, and they are deliberately not all the same"
        "question. The finished sighash is the one that matters, but a hash is"
        "the worst possible place to look for a mistake: every field goes in"
        "and one number comes out, so two errors that cancel look exactly like"
        "no errors at all. The five intermediary hashes and the SigMsg"
        "preimage sit underneath it and come apart where the sighash would"
        "not."
        "The scriptPubKey section carries the other half. Its tweak arithmetic"
        "needs secp256k1 and is checked in Rust, in providers/primitives; what"
        "is checked here is what Aver can answer -- the Merkle root built from"
        "the script tree, and the Bech32m address the resulting Output Script"
        "pays to, both ways. Those seven addresses are the first published"
        "vectors this project's Bech32m encoder has ever been held to."
        "Nothing here was written by hand and nothing should be. Regenerate it"
        "with tools/bip341_vectors_to_aver.py."
    exposes [sighash, sigMsg, hashPrevouts, hashAmounts, hashScriptPubKeys, hashSequences, hashOutputs, merkleRoot, address, script]
    depends [Bytes, Domain.Bip341, Domain.Network, Domain.Payto, Domain.ReadAddress, Domain.Taproot, Domain.Transaction]
    effects []

fn sighash(rawHex: String, inputIndex: Int, prevouts: List<Spent>, hashType: Int) -> Result<String, String>
    ? "One vector: the Transaction, which Input, what it spends, and how."
    Domain.Bip341.message(decoded(rawHex)?, inputIndex, prevouts, hashType, Option.None)

verify sighash
%(sighash)s

fn sigMsg(rawHex: String, inputIndex: Int, prevouts: List<Spent>, hashType: Int) -> Result<String, String>
    ? "The same vector's SigMsg, which is what gets hashed."
      "The BIP prints it for every one of the seven, and it is worth more than"
      "the hash beside it: a wrong field shows up here as a wrong field, in"
      "the position it occupies, rather than as a different number."
      "The leading zero is the epoch byte. BIP341 hashes 0x00 || SigMsg and"
      "the vectors include it, so this does too."
    Result.Ok(hexOf(Domain.Bip341.sigMsg(decoded(rawHex)?, inputIndex, prevouts, hashType, Option.None, Option.None)))

verify sigMsg
%(sigmsg)s

fn hashPrevouts(rawHex: String) -> Result<String, String>
    ? "Every outpoint, hashed once."
    Result.Ok(hexOf(Domain.Bip341.hashPrevouts(Domain.Transaction.inputsOf(decoded(rawHex)?))))

verify hashPrevouts
%(prevouts)s

fn hashAmounts(prevouts: List<Spent>) -> String
    ? "Every spent amount, hashed once."
      "New in BIP341, and the reason a Taproot signer cannot be lied to about"
      "what it is spending."
    hexOf(Domain.Bip341.hashAmounts(prevouts))

verify hashAmounts
%(amounts)s

fn hashScriptPubKeys(prevouts: List<Spent>) -> String
    ? "Every spent Output Script, hashed once."
    hexOf(Domain.Bip341.hashScriptPubKeys(prevouts))

verify hashScriptPubKeys
%(scriptpubkeys)s

fn hashSequences(rawHex: String) -> Result<String, String>
    ? "Every sequence, hashed once."
    Result.Ok(hexOf(Domain.Bip341.hashSequences(Domain.Transaction.inputsOf(decoded(rawHex)?))))

verify hashSequences
%(sequences)s

fn hashOutputs(rawHex: String) -> Result<String, String>
    ? "Every Output, hashed once."
    Result.Ok(hexOf(Domain.Bip341.hashOutputs(Domain.Transaction.outputsOf(decoded(rawHex)?))))

verify hashOutputs
%(outputs)s

fn merkleRoot(root: List<Int>) -> String
    ? "A script tree's root, built from its leaves rather than walked up to."
      "The argument is the tree itself, spelled as nested Domain.Taproot calls"
      "by the generator, so what each case tests is leafHashOf and the"
      "smaller-first joined. Verification only ever walks a path upward from"
      "one leaf; these build the whole tree, which is the direction a wallet"
      "uses and the one nothing here had exercised."
      "The two vectors with no tree are left out: they have no root."
    hexOf(root)

verify merkleRoot
%(merkle)s

fn address(scriptHex: String) -> Result<String, String>
    ? "The Bech32m address an Output Script pays to."
      "BIP350 addresses, from the BIP that defines them. Domain.Bech32 has"
      "chosen Bech32m for every version above zero since it was written and no"
      "published vector had ever contradicted it; these seven are the first"
      "that could."
    Domain.Payto.addressFor(Network.Mainnet, scriptHex)

verify address
%(address)s

fn script(text: String) -> Result<String, String>
    ? "And the Output Script that address pays to, back again."
    Domain.ReadAddress.scriptFor(Network.Mainnet, text)

verify script
%(script)s

fn decoded(rawHex: String) -> Result<Transaction, String>
    ? "The Transaction the vector gives, as bytes then as fields."
    Domain.Transaction.decode(Bytes.toList(Bytes.fromHex(rawHex)?))

verify decoded
    Domain.Transaction.versionOf(decoded("%(sample)s")?) => 2

fn hexOf(bytes: List<Int>) -> String
    ? "Bytes as text."
      "Total: every list here comes from a hash or a preimage builder and is"
      "octets by construction, so Bytes.fromList cannot refuse it."
    match Bytes.fromList(bytes)
        Result.Err(message) -> ""
        Result.Ok(raw) -> Bytes.toHex(raw)

verify hexOf
    hexOf([170, 187]) => "aabb"
    hexOf([]) => ""
'''


def emitted():
    data = vectors()
    key_path = data["keyPathSpending"][0]
    raw = key_path["given"]["rawUnsignedTx"]
    utxos = key_path["given"]["utxosSpent"]
    spents = spent_list(utxos)
    inter = key_path["intermediary"]

    sighash, sigmsg = [], []
    for entry in key_path["inputSpending"]:
        given, mid = entry["given"], entry["intermediary"]
        args = '"%s", %d, %s, %d' % (raw, given["txinIndex"], spents, given["hashType"])
        sighash.append('    sighash(%s) => Result.Ok("%s")' % (args, mid["sigHash"].lower()))
        sigmsg.append('    sigMsg(%s) => Result.Ok("%s")' % (args, mid["sigMsg"].lower()))

    merkle, address, script = [], [], []
    for entry in data["scriptPubKey"]:
        tree = tree_expression(entry["given"]["scriptTree"])
        root = entry["intermediary"]["merkleRoot"]
        if tree is not None and root is not None:
            merkle.append('    merkleRoot(%s) => "%s"' % (tree, root.lower()))
        expected = entry["expected"]
        address.append('    address("%s") => Result.Ok("%s")'
                       % (expected["scriptPubKey"], expected["bip350Address"]))
        script.append('    script("%s") => Result.Ok("%s")'
                      % (expected["bip350Address"], expected["scriptPubKey"]))

    body = HEADER % {
        "sighash": "\n".join(sighash),
        "sigmsg": "\n".join(sigmsg),
        "prevouts": '    hashPrevouts("%s") => Result.Ok("%s")' % (raw, inter["hashPrevouts"].lower()),
        "amounts": '    hashAmounts(%s) => "%s"' % (spents, inter["hashAmounts"].lower()),
        "scriptpubkeys": '    hashScriptPubKeys(%s) => "%s"' % (spents, inter["hashScriptPubkeys"].lower()),
        "sequences": '    hashSequences("%s") => Result.Ok("%s")' % (raw, inter["hashSequences"].lower()),
        "outputs": '    hashOutputs("%s") => Result.Ok("%s")' % (raw, inter["hashOutputs"].lower()),
        "merkle": "\n".join(merkle),
        "address": "\n".join(address),
        "script": "\n".join(script),
        "sample": raw,
    }
    counts = {"sighash": len(sighash), "sigMsg": len(sigmsg), "intermediary": 5,
              "merkleRoot": len(merkle), "address": len(address), "script": len(script)}
    return body, counts


def emit():
    body, counts = emitted()
    path = os.path.join(OUT, "bip341cases.av")
    open(path, "w").write(body)
    total = sum(counts.values())
    print("wrote %s with %d cases: %s" % (
        path, total, ", ".join("%s %d" % (k, v) for k, v in counts.items())))
    return 0


def check():
    body, counts = emitted()
    path = os.path.join(OUT, "bip341cases.av")
    if not os.path.exists(path):
        print("%s does not exist" % path)
        return 1
    if open(path).read() == body:
        print("%s matches what this tool would write (%d cases)" % (path, sum(counts.values())))
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
