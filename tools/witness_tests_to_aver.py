#!/usr/bin/env python3
"""Turn the Witness rows of Bitcoin Core's script_tests.json into Aver cases.

The sibling tool reads the plain rows of that file, which are pairs of Scripts
and can be run as a pair.  The 113 rows carrying a Witness cannot: a Witness
belongs to an Input of a Transaction, and the row does not carry one.  Core's
harness builds two, and this reproduces exactly that construction from
src/test/util/transaction_utils.cpp:

    BuildCreditingTransaction(scriptPubKey, nValue)
        version 1, locktime 0
        one input:  prevout null, scriptSig OP_0 OP_0, sequence 0xffffffff
        one output: the row's scriptPubKey, the row's amount

    BuildSpendingTransaction(scriptSig, scriptWitness, txCredit)
        version 1, locktime 0
        one input:  prevout (txCredit txid, 0), the row's scriptSig,
                    sequence 0xffffffff, the row's Witness
        one output: empty script, the same amount

The spending Transaction is then a whole Transaction spending one known Output,
which is what Domain.TxCase already runs -- so these rows join the corpus in the
shape the Transaction cases use rather than the shape the Script cases use.

Getting the construction wrong would look exactly like getting the engine
wrong, which is the risk this tool carries.  It is self-checking in one
important way: BIP143 and BIP341 both commit to the Transaction Id and to the
amount, so a harness that built either wrongly would fail *every* signed case
rather than some of them.  A high agreement rate is therefore evidence about
the harness and not only about the engine.

Same discipline as the siblings: Python assembles, the engine answers.

    python3 tools/witness_tests_to_aver.py --emit                 # placeholders
    python3 tools/witness_tests_to_aver.py --probe /tmp/p/main.av
    # run the probe, see docs/core-corpora.md for the project it needs
    python3 tools/witness_tests_to_aver.py --answers answers.txt
    python3 tools/witness_tests_to_aver.py --check                # regeneration
"""

import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "script_tests_data")
OUT = os.path.abspath(os.path.join(HERE, "..", "domain"))

sys.path.insert(0, HERE)
from script_tests_to_aver import opcode_names, parse_script  # noqa: E402

SEQUENCE_FINAL = 0xFFFFFFFF
PER_FILE = 40


def compact_size(n):
    if n < 0xFD:
        return bytes([n])
    if n <= 0xFFFF:
        return b"\xfd" + n.to_bytes(2, "little")
    if n <= 0xFFFFFFFF:
        return b"\xfe" + n.to_bytes(4, "little")
    return b"\xff" + n.to_bytes(8, "little")


def with_length(payload):
    return compact_size(len(payload)) + payload


def double_sha(payload):
    return hashlib.sha256(hashlib.sha256(payload).digest()).digest()


def serialise(version, inputs, outputs, locktime, witnesses=None):
    """inputs: (txid_internal, index, script, sequence).  witnesses: list of item lists."""
    out = version.to_bytes(4, "little")
    carries = witnesses is not None and any(len(w) for w in witnesses)
    if carries:
        out += b"\x00\x01"
    out += compact_size(len(inputs))
    for txid, index, script, sequence in inputs:
        out += txid + index.to_bytes(4, "little") + with_length(script)
        out += sequence.to_bytes(4, "little")
    out += compact_size(len(outputs))
    for value, script in outputs:
        out += value.to_bytes(8, "little") + with_length(script)
    if carries:
        for items in witnesses:
            out += compact_size(len(items))
            for item in items:
                out += with_length(item)
    out += locktime.to_bytes(4, "little")
    return out


def crediting(script_pubkey, value):
    """Core's BuildCreditingTransaction.  Never carries a Witness."""
    # prevout.SetNull() is a zero hash and an index of 0xffffffff.
    # scriptSig is CScript() << CScriptNum(0) << CScriptNum(0); CScriptNum(0)
    # serialises to nothing and pushing nothing is OP_0, so it is two zero bytes.
    inputs = [(b"\x00" * 32, 0xFFFFFFFF, b"\x00\x00", SEQUENCE_FINAL)]
    return serialise(1, inputs, [(value, script_pubkey)], 0)


def spending(script_sig, witness, credit_raw, value):
    """Core's BuildSpendingTransaction."""
    txid_internal = double_sha(credit_raw)
    inputs = [(txid_internal, 0, script_sig, SEQUENCE_FINAL)]
    return serialise(1, inputs, [(value, b"")], 0, [witness])


def satoshis(amount):
    """Core reads the amount as a real and multiplies by COIN."""
    return int(round(float(amount) * 100000000))


def collected():
    """(rawSpendHex, creditTxidDisplay, scriptPubKeyHex, value, flags, coreValid, comment)."""
    data = json.load(open(os.path.join(DATA, "script_tests.json")))
    names = opcode_names()
    for row in data:
        if len(row) < 5 or not isinstance(row[0], list):
            continue
        stack, sig_text, pubkey_text, flags, expected = row[0], row[1], row[2], row[3], row[4]
        comment = row[5] if len(row) > 5 else ""
        if "TAPROOT" in flags:
            # BIP342 leaf cases; Core's own note is that these belong with the
            # taproot asset tests rather than here.  Left out on purpose.
            continue
        try:
            witness = [bytes.fromhex(item) for item in stack[:-1]]
            value = satoshis(stack[-1])
            script_sig = parse_script(sig_text, names)
            script_pubkey = parse_script(pubkey_text, names)
        except ValueError:
            continue
        credit_raw = crediting(script_pubkey, value)
        spend_raw = spending(script_sig, witness, credit_raw, value)
        txid_display = double_sha(credit_raw)[::-1].hex()
        yield (spend_raw.hex(), txid_display, script_pubkey.hex(), value,
               flags, expected == "OK", comment)


HEADER = '''module WitnessCases%(n)d
    intent =
        "The Witness rows of Bitcoin Core's script_tests.json: part %(n)d of %(total)d."
        "A Witness belongs to an Input and these rows do not carry one, so"
        "Core's harness builds two Transactions around each pair and this"
        "reproduces that construction exactly -- BuildCreditingTransaction and"
        "BuildSpendingTransaction from src/test/util/transaction_utils.cpp."
        "That makes them whole Transactions spending one known Output, which"
        "is the shape Domain.TxCase runs, so they join the corpus there rather"
        "than beside the Script pairs."
        "The answer recorded is this engine's, not Core's, so this is a"
        "regression corpus and not a conformance one. What Core says is on the"
        "comment above each case, and where the two differ the case records"
        "what this engine does today."
        "Standing as this was generated: %(cases)d cases, %(agree)d agree with"
        "Core, %(disagree)d disagree, %(undecided)d this engine cannot answer."
        "Nothing here was written by hand and nothing should be. Regenerate it"
        "with tools/witness_tests_to_aver.py."
    exposes [case]
    depends [Domain.ScriptState, Domain.TxCase]
    effects []

fn case(rawHex: String, prevouts: List<Prevout>) -> Outcome
    ? "One whole Transaction, its single Input run against the Output Core's"
      "harness credited it with."
    Domain.TxCase.verdict(rawHex, prevouts)

verify case
'''


def prevout_literal(txid, index, script, value):
    return 'Prevout(txid = "%s", index = %d, script = "%s", amount = %d)' % (
        txid, index, script, value)


def shown(outcome):
    return outcome


def emit(answers, report):
    rows = list(collected())
    total = (len(rows) + PER_FILE - 1) // PER_FILE
    for part in range(total):
        chunk = rows[part * PER_FILE:(part + 1) * PER_FILE]
        body = []
        for i, (raw, txid, pubkey, value, flags, core_ok, comment) in enumerate(chunk):
            answer = answers[part * PER_FILE + i] if answers else "Outcome.Undecided(\"not yet answered\")"
            note = "%s  flags %s" % ("valid" if core_ok else "invalid", flags)
            if comment:
                note += "  -- " + comment.replace("\n", " ")
            body.append("    // Core: %s" % note)
            body.append('    case("%s", [%s]) => %s' % (
                raw, prevout_literal(txid, 0, pubkey, value), answer))
        header = HEADER % dict(n=part + 1, total=total, cases=report["cases"],
                               agree=report["agree"], disagree=report["disagree"],
                               undecided=report["undecided"])
        path = os.path.join(OUT, "witnesscases%d.av" % (part + 1))
        with open(path, "w") as f:
            f.write(header + "\n".join(body) + "\n")
        print("wrote %s with %d cases" % (path, len(chunk)))
    return total


PROBE = '''module Main
    intent =
        "Answer every Witness case, one to a line, so the tool can read them"
        "back. Generated by tools/witness_tests_to_aver.py."
    exposes [main]
    depends [Domain.ScriptState, Domain.TxCase]
    effects [Console.print]

fn shown(outcome: Outcome) -> String
    ? "One answer, in the syntax a verify case is written in."
    match outcome
        Outcome.Undecided(why) -> "Outcome.Undecided(\\"{why}\\")"
        Outcome.Decided(ending) -> shownEnding(ending)

verify shown
    shown(Outcome.Undecided("x")) => "Outcome.Undecided(\\"x\\")"

fn shownEnding(ending: Ending) -> String
    ? "Passed or the reason it did not."
    match ending
        Ending.Passed -> "Outcome.Decided(Ending.Passed)"
        Ending.Failed(why) -> "Outcome.Decided(Ending.Failed(\\"{why}\\"))"

verify shownEnding
    shownEnding(Ending.Passed) => "Outcome.Decided(Ending.Passed)"

fn eachCase(cases: List<Tuple<String, List<Prevout>>>) -> Unit
    ? "Every case in turn, in the order they were assembled."
    ! [Console.print]
    match cases
        [] -> Unit
        [head, ..tail] -> oneThenRest(head, tail)

fn oneThenRest(one: Tuple<String, List<Prevout>>, rest: List<Tuple<String, List<Prevout>>>) -> Unit
    ? "Answer this one, then the rest."
    ! [Console.print]
    _said = printOne(one)
    eachCase(rest)

fn printOne(one: Tuple<String, List<Prevout>>) -> Unit
    ? "One line, matched apart."
    ! [Console.print]
    match one
        (rawHex, prevouts) -> Console.print(shown(Domain.TxCase.verdict(rawHex, prevouts)))

fn main() -> Unit
    ? "The whole corpus, answered."
    ! [Console.print]
    eachCase(assembled())

fn assembled() -> List<Tuple<String, List<Prevout>>>
    ? "Every case Core supplies, as this tool wrote them."
    [
%(cases)s
    ]
'''


def probe(path):
    lines = []
    for raw, txid, pubkey, value, flags, core_ok, comment in collected():
        lines.append('        ("%s", [%s]),' % (raw, prevout_literal(txid, 0, pubkey, value)))
    body = "\n".join(lines).rstrip(",")
    with open(path, "w") as f:
        f.write(PROBE % dict(cases=body))
    print("wrote probe with %d cases to %s" % (len(lines), path))


def reconcile(answers_path):
    answers = [l.rstrip("\n") for l in open(answers_path) if l.strip()]
    rows = list(collected())
    if len(answers) != len(rows):
        raise SystemExit("got %d answers for %d cases" % (len(answers), len(rows)))
    agree = disagree = undecided = 0
    for (raw, txid, pubkey, value, flags, core_ok, comment), answer in zip(rows, answers):
        if answer.startswith("Outcome.Undecided"):
            undecided += 1
        elif ("Ending.Passed" in answer) == core_ok:
            agree += 1
        else:
            disagree += 1
    report = dict(cases=len(rows), agree=agree, disagree=disagree, undecided=undecided)
    print("cases        %d" % len(rows))
    print("agree        %d" % agree)
    print("disagree     %d" % disagree)
    print("undecided    %d  (this engine cannot answer; not a disagreement)" % undecided)
    emit(answers, report)


def check():
    """Prove the assembly is deterministic: same inputs, same bytes."""
    first = list(collected())
    second = list(collected())
    if first != second:
        raise SystemExit("assembly is not deterministic")
    print("assembly is deterministic over %d cases" % len(first))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--probe")
    ap.add_argument("--answers")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.probe:
        probe(args.probe)
    elif args.answers:
        reconcile(args.answers)
    elif args.check:
        check()
    elif args.emit:
        rows = list(collected())
        emit(None, dict(cases=len(rows), agree=0, disagree=0, undecided=len(rows)))
    else:
        ap.error("pick one of --emit, --probe, --answers, --check")


if __name__ == "__main__":
    main()
