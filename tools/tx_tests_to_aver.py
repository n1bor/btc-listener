#!/usr/bin/env python3
"""Turn Bitcoin Core's tx_valid.json and tx_invalid.json into Aver verify cases.

The sibling tool does this for script_tests.json, which is pairs of Scripts.
This one does it for whole Transactions, and the difference is not cosmetic:
script_tests.json's harness builds the crediting Transaction out of the Output
Script, so the Transaction Id -- and therefore the signature hash -- depends on
that Script.  A signature embedded in it would have to sign a digest that
depends on itself.  Everything that turns on the Transaction rather than the
Script therefore lives in these two files instead: FindAndDelete, the
SIGHASH_SINGLE bug, hash types, sequence and lock time.

Same discipline as the sibling: Python assembles, the engine answers.  The
expected values are written by --reconcile from what `aver verify` actually
reported, never by this file.

    python3 tools/tx_tests_to_aver.py --fetch
    python3 tools/tx_tests_to_aver.py --emit                    # placeholders
    python3 tools/tx_tests_to_aver.py --probe /tmp/p/main.av    # the middle step
    aver run /tmp/p/main.av --module-root . --providers > answers.txt
    python3 tools/tx_tests_to_aver.py --answers answers.txt
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "script_tests_data")
OUT = os.path.abspath(os.path.join(HERE, "..", "domain"))

VALID = "https://raw.githubusercontent.com/bitcoin/bitcoin/master/src/test/data/tx_valid.json"
INVALID = "https://raw.githubusercontent.com/bitcoin/bitcoin/master/src/test/data/tx_invalid.json"

sys.path.insert(0, HERE)
from script_tests_to_aver import opcode_names, parse_script  # noqa: E402


def fetch():
    import urllib.request
    os.makedirs(DATA, exist_ok=True)
    for url in (VALID, INVALID):
        name = url.rsplit("/", 1)[-1]
        urllib.request.urlretrieve(url, os.path.join(DATA, name))
        print("fetched", name)


def cases(path, core_says_valid):
    """(rawTxHex, [(txid, index, scriptHex, amount)], flags, coreValid, why-skipped)."""
    names = opcode_names()
    for row in json.load(open(path)):
        if len(row) < 3 or isinstance(row[0], str):
            continue                      # a comment line
        prevouts, raw, flags = row[0], row[1], row[2]
        try:
            supplied = []
            for p in prevouts:
                amount = p[3] if len(p) > 3 else 0
                supplied.append((p[0], p[1], parse_script(p[2], names).hex(), amount))
        except ValueError as e:
            yield None, None, flags, core_says_valid, str(e)
            continue
        yield raw, supplied, flags, core_says_valid, None


HEADER = '''module TxCases%(k)d
    intent =
        "Bitcoin Core's tx_valid.json and tx_invalid.json, converted into"
        "cases: part %(k)d of %(n)d."
        "A whole Transaction and the Outputs it spends, which is the shape"
        "script_tests.json cannot express -- its harness derives the crediting"
        "Transaction from the Output Script, so a signature embedded there"
        "would have to sign a digest that depends on itself. FindAndDelete,"
        "the SIGHASH_SINGLE bug and the hash types live here instead."
        "The answer recorded is this engine's, not Core's, so this is a"
        "regression corpus and not a conformance one. What Core says is on the"
        "comment above each case, and where the two differ the case records"
        "what this engine does today -- a disagreement is pinned, not fixed."
        "Each case carries the rules Core ran it under, built from the flag"
        "names in the file. The two files mean opposite things by a flag:"
        "src/test/transaction_tests.cpp verifies tx_valid.json with"
        "~verify_flags and tx_invalid.json with verify_flags, so a valid case"
        "reads Domain.Rules.exceptFlags and an invalid one Domain.Rules"
        ".underFlags. Running both under every rule, which is what this corpus"
        "did before n1bor/btc-listener#52, made a case that had deliberately"
        "turned SegWit off pass here and fail in Core."
%(report)s        "Nothing here was written by hand and nothing should be. Regenerate it."
    exposes [case]
    depends [Domain.Rules, Domain.ScriptState, Domain.TxCase]
    effects []

fn case(rawHex: String, prevouts: List<Prevout>, rules: Rules) -> Outcome
    ? "One whole Transaction, every Input run against the Output it names,"
      "under the rules Core's flag names ask for."
    Domain.TxCase.verdict(rawHex, prevouts, rules)

verify case'''

PLACEHOLDER = "Outcome.Decided(Ending.Passed)"


PROBE = '''module Main
    intent =
        "Prints this engine's answer for every Transaction case, one a line."
        "The middle step: Python assembles the cases, the engine answers them,"
        "and Python writes the answers back. Keeping the three apart is what"
        "stops the corpus from being a test that agrees with itself."
    exposes [main]
    depends [Domain.Rules, Domain.ScriptState, Domain.TxCase]
    effects [Console.print]

fn shown(outcome: Outcome) -> String
    ? "The answer in the form the corpus writes it."
    match outcome
        Outcome.Undecided(why) -> "Outcome.Undecided(\\"{why}\\")"
        Outcome.Decided(ending) -> shownEnding(ending)

verify shown
    shown(Outcome.Decided(Ending.Passed)) => "Outcome.Decided(Ending.Passed)"

fn shownEnding(ending: Ending) -> String
    ? "Passed has nothing to say; Failed says why."
    match ending
        Ending.Passed -> "Outcome.Decided(Ending.Passed)"
        Ending.Failed(why) -> "Outcome.Decided(Ending.Failed(\\"{why}\\"))"

verify shownEnding
    shownEnding(Ending.Passed) => "Outcome.Decided(Ending.Passed)"

fn eachCase(cases: List<Tuple<String, List<Prevout>, Rules>>) -> Unit
    ? "Every case in turn, in the order they were assembled."
    ! [Console.print]
    match cases
        [] -> Unit
        [head, ..tail] -> oneThenRest(head, tail)

fn oneThenRest(one: Tuple<String, List<Prevout>, Rules>, rest: List<Tuple<String, List<Prevout>, Rules>>) -> Unit
    ? "Answer this one, then the rest."
    ! [Console.print]
    _said = printOne(one)
    eachCase(rest)

fn printOne(one: Tuple<String, List<Prevout>, Rules>) -> Unit
    ? "One line, matched apart."
    ! [Console.print]
    match one
        (rawHex, prevouts, rules) -> Console.print(shown(Domain.TxCase.verdict(rawHex, prevouts, rules)))

fn main() -> Unit
    ? "The whole corpus, answered."
    ! [Console.print]
    eachCase(assembled())

%(parts)s'''


# The VM truncates a list literal to `len mod 256` elements, silently and with
# exit 0 -- jasisz/aver#1054.  This corpus is under the limit today, but the
# Script one was not and lost 92 percent of its cases without a word, so the
# probe is written in parts here too.  The `verify assembled` case counts what
# comes out, so a part over the limit fails loudly.
PROBE_PART = 200


def parts_source(lines, element):
    """`assembled` plus the numbered parts it joins, as Aver source."""
    chunks = [lines[i:i + PROBE_PART] for i in range(0, len(lines), PROBE_PART)]
    joined = "[]"
    for k in range(len(chunks), 0, -1):
        joined = "part%d()" % k if joined == "[]" else "List.concat(part%d(), %s)" % (k, joined)
    out = ['fn assembled() -> List<%s>' % element,
           '    ? "Every case Core supplies, as this tool wrote them."',
           '      "In parts of at most %d because the VM truncates a list literal to"' % PROBE_PART,
           '      "len mod 256 elements without saying so -- jasisz/aver#1054. The"',
           '      "verify case counts what comes out, so a part over the limit fails"',
           '      "here rather than quietly shortening the corpus."',
           '    %s' % joined,
           '',
           'verify assembled',
           '    List.len(assembled()) => %d' % len(lines),
           '']
    for k, chunk in enumerate(chunks, start=1):
        out += ['fn part%d() -> List<%s>' % (k, element),
                '    ? "Cases %d to %d."' % ((k - 1) * PROBE_PART + 1, (k - 1) * PROBE_PART + len(chunk)),
                '    [',
                "\n".join(chunk).rstrip(","),
                '    ]',
                '']
    return "\n".join(out)


def probe(path):
    rows = collected()
    lines = []
    for raw, supplied, flags, core_valid in rows:
        lines.append('        ("%s", [%s], %s),' % (raw, ", ".join(prevout_literal(p) for p in supplied), rules_literal(flags, core_valid)))
    open(path, "w").write(PROBE % {"parts": parts_source(lines, "Tuple<String, List<Prevout>, Rules>")})
    print("wrote probe with %d cases to %s" % (len(rows), path))
    return 0


# `aver verify` runs on the VM, which has a million-step budget.  One case in
# tx_valid.json is a 1911 byte Transaction with twelve Inputs and exceeds it;
# the median case is 135 bytes and the next largest is under 500.  The compiled
# engine has no such limit and answers it.  It is left out of the corpus rather
# than left in it aborting, the same way the Script corpus leaves out its own
# oversized cases.
VM_TX_LIMIT = 1000


def collected():
    rows = []
    for name, valid in (("tx_valid.json", True), ("tx_invalid.json", False)):
        full = os.path.join(DATA, name)
        for raw, supplied, flags, core_valid in [c[:4] for c in cases(full, valid) if c[4] is None]:
            if len(raw) // 2 > VM_TX_LIMIT:
                continue
            rows.append((raw, supplied, flags, core_valid))
    return rows


def rules_literal(flags, core_valid):
    """The Rules expression for one case, in Core's own flag names.

    src/test/transaction_tests.cpp verifies tx_valid.json with ~verify_flags
    and tx_invalid.json with verify_flags, so the same list means opposite
    things in the two files.  NONE is Core's spelling of the empty set.
    """
    names = [w.strip() for w in flags.split(",") if w.strip() not in ("", "NONE", "BADTX")]
    listed = "[%s]" % ", ".join('"%s"' % n for n in names)
    return "Domain.Rules.%s(%s)" % ("exceptFlags" if core_valid else "underFlags", listed)


def prevout_literal(p):
    txid, index, script, amount = p
    # Core writes -1 for a coinbase prevout.  COutPoint::n is a uint32_t, so
    # what the Transaction actually carries is 0xffffffff; a signed -1 here
    # would match no Input at all.
    if index < 0:
        index += 1 << 32
    return 'Prevout(txid = "%s", index = %d, script = "%s", amount = %d)' % (
        txid, index, script, amount)


def emit(per_file=60, report=""):
    rows = collected()
    print("assembled %d cases" % len(rows))
    parts = [rows[i:i + per_file] for i in range(0, len(rows), per_file)]
    for k, chunk in enumerate(parts, start=1):
        lines = []
        for raw, supplied, flags, core_valid in chunk:
            lines.append("    // Core: %s  flags %s" % ("valid" if core_valid else "invalid", flags))
            lines.append('    case("%s", [%s], %s) => %s'
                         % (raw, ", ".join(prevout_literal(p) for p in supplied),
                            rules_literal(flags, core_valid), PLACEHOLDER))
        path = os.path.join(OUT, "txcases%d.av" % k)
        open(path, "w").write(HEADER % {"k": k, "n": len(parts), "report": report}
                              + "\n" + "\n".join(lines) + "\n")
        print("wrote %s with %d cases" % (path, len(chunk)))
    return 0


def answers(answers_path):
    """Write the engine's own answers into the corpus, in the order it gave them."""
    got = [l.rstrip("\n") for l in open(answers_path) if l.startswith("Outcome.")]
    rows = collected()
    if len(got) != len(rows):
        print("have %d answers for %d cases -- refusing to guess which is which"
              % (len(got), len(rows)))
        return 1
    agree = undecided = lax = strict = 0
    it = iter(got)
    for name in sorted(os.listdir(OUT), key=lambda n: int(re.findall(r"\d+", n)[0]) if re.fullmatch(r"txcases\d+\.av", n) else 0):
        if not re.fullmatch(r"txcases\d+\.av", name):
            continue
        path = os.path.join(OUT, name)
        out = []
        for line in open(path).read().split("\n"):
            m = re.match(r"    (case\(.*\)) => ", line)
            if m:
                out.append("    %s => %s" % (m.group(1), next(it)))
            else:
                out.append(line)
        open(path, "w").write("\n".join(out))
    # the report: what Core says against what this engine says
    for (raw, supplied, flags, core_valid), answer in zip(rows, got):
        ours_valid = answer == "Outcome.Decided(Ending.Passed)"
        if answer.startswith("Outcome.Undecided"):
            undecided += 1
        elif ours_valid == core_valid:
            agree += 1
        elif ours_valid:
            lax += 1
        else:
            strict += 1
    disagree = lax + strict
    print("cases        %d" % len(rows))
    print("agree        %d" % agree)
    print("disagree     %d" % disagree)
    print("undecided    %d  (this engine cannot answer; not a disagreement)" % undecided)
    print()
    print("  we accept what Core refuses  %d  (a rule not implemented)" % lax)
    print("  we refuse what Core accepts  %d  (the direction a defect shows up in)" % strict)
    report = ('        "Standing as this was generated: %d cases, %d agree with Core,"\n'
              '        "%d disagree, %d this engine cannot answer. Of the disagreements,"\n'
              '        "%d are a rule this engine does not implement and %d are this engine"\n'
              '        "refusing what Core accepts -- which is the direction a defect shows"\n'
              '        "up in, so any of those is a bug until shown otherwise."\n'
              % (len(rows), agree, disagree, undecided, lax, strict))
    emit(report=report)
    # emit rewrote the files with placeholders; put the answers back
    it = iter(got)
    for name in sorted(os.listdir(OUT), key=lambda n: int(re.findall(r"\d+", n)[0]) if re.fullmatch(r"txcases\d+\.av", n) else 0):
        if not re.fullmatch(r"txcases\d+\.av", name):
            continue
        path = os.path.join(OUT, name)
        out = []
        for line in open(path).read().split("\n"):
            m = re.match(r"    (case\(.*\)) => ", line)
            out.append("    %s => %s" % (m.group(1), next(it)) if m else line)
        open(path, "w").write("\n".join(out))
    return 0


def qualify(shown):
    """aver prints Decided(Passed); the source form is Outcome.Decided(Ending.Passed)."""
    s = shown.strip()
    s = re.sub(r"^Decided\(Passed\)$", "Outcome.Decided(Ending.Passed)", s)
    s = re.sub(r"^Decided\(Failed\((.*)\)\)$", r"Outcome.Decided(Ending.Failed(\1))", s)
    s = re.sub(r"^Undecided\((.*)\)$", r"Outcome.Undecided(\1)", s)
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--probe")
    ap.add_argument("--answers")
    a = ap.parse_args()
    if a.fetch:
        fetch()
    if a.emit:
        return emit()
    if a.probe:
        return probe(a.probe)
    if a.answers:
        return answers(a.answers)
    if not a.fetch:
        ap.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
