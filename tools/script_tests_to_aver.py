#!/usr/bin/env python3
"""Turn Bitcoin Core's script_tests.json into Aver verify cases.

Aver cannot read JSON at verify time, so the corpus has to be compiled into
source.  This does that, and nothing else: it does not decide what the answer
should be.  The expected answers come from running the engine over the
assembled scripts, and the point of the exercise is the report this prints —
how many of Core's cases we agree with, and exactly which ones we do not and
why.

Usage:
    python3 tools/script_tests_to_aver.py --fetch                 # refresh the inputs
    python3 tools/script_tests_to_aver.py --emit                  # placeholders
    python3 tools/script_tests_to_aver.py --probe /tmp/p/main.av  # the middle step
    aver run /tmp/p/main.av --module-root . --providers > answers.txt
    python3 tools/script_tests_to_aver.py --answers answers.txt

The middle step is an Aver program: assembling is Python's job, answering is
the engine's, and keeping them apart is what stops this from being a test that
agrees with itself.

Two things each case carries that it did not before n1bor/btc-listener#70:

* **The flags.**  Core hands `script_tests.json`'s flag field straight to
  VerifyScript, so a row marked P2SH,WITNESS is a row Core deliberately ran
  with only those rules on.  Running every row under every rule -- which this
  tool did -- meant the agreement was partly luck, and it also collapsed 1118
  rows into 997 distinct pairs, because the 121 rows that differ only by their
  flags became literal duplicates.
* **Core's answer.**  It is in the file: element five of each row is the
  expected script error, `OK` for a pair that verifies.  Without it the
  agreement could not be recomputed from the corpus and lived in a
  hand-maintained table in ADR 0005 that went stale.
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

# The consensus limit on a Script, which is also what makes an over-long Script
# cheap: Core refuses it on size before running a single opcode, and so does
# this engine, so a 10001 byte Script costs nothing to answer.
CONSENSUS_SCRIPT_LIMIT = 10000

# A Script this long or longer is *executed*, and `aver verify` runs on a VM
# with a million-step budget that a few thousand opcodes exhaust.  Nothing to
# do with consensus.  n1bor/btc-listener#75.
VM_SCRIPT_LIMIT = 1000


def too_slow_to_verify(sig, pubkey):
    """Nothing is, any more.

    `aver verify` takes a per-function step budget from aver.toml as of
    jasisz/aver#1071, and this project raises it for the two corpus `case`
    functions.  The band this used to exclude -- a Script long enough to be
    expensive and short enough to be executed rather than refused on size --
    now runs like any other case.  Kept as a function rather than deleted
    because the shape of the question is worth keeping if a budget ever
    binds again.
    """
    return False


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


def collected():
    """Every row that assembles, as (sigHex, pubkeyHex, flags, expected).

    Every one, including those the verify VM cannot finish.  The probe runs
    under the compiled engine, which has no step budget, so it answers all of
    them; `verifiable` is what decides which become verify cases.
    """
    return [(sig, pubkey, flags, expected)
            for sig, pubkey, flags, expected, why in rows() if why is None]


def verifiable(row):
    """Whether this row becomes a verify case rather than a recorded exclusion."""
    return not too_slow_to_verify(row[0], row[1])


def rules_literal(flags):
    """The Rules expression for one row, in Core's own flag names.

    script_tests.json hands its flags straight to VerifyScript, so the list is
    read as it stands -- unlike tx_valid.json, which names the flags to leave
    out.  NONE is Core's spelling of the empty set.
    """
    names = [w.strip() for w in flags.split(",") if w.strip() not in ("", "NONE")]
    return "Domain.Rules.underFlags([%s])" % ", ".join('"%s"' % n for n in names)


HEADER = """module ScriptCases%(k)d
    intent =
        "Bitcoin Core's own script_tests.json, converted into cases: part %(k)d of %(n)d."
        "Adversarial in a way nothing written here would be. Every case is a"
        "pair of Scripts that Core runs together, and the answer recorded is"
        "this engine's, not Core's -- so this is a regression corpus and not a"
        "conformance one. What Core says is on the comment above each case,"
        "and where the two differ the case records what this engine does"
        "today."
        "Each case also carries the rules Core ran it under. script_tests.json"
        "hands its flag field straight to VerifyScript, so a row marked"
        "P2SH,WITNESS is one Core deliberately ran with only those rules on."
        "Running every row under every rule, which this corpus did before"
        "n1bor/btc-listener#70, made the agreement partly luck -- and it"
        "collapsed the file, because rows that differ only by their flags"
        "became literal duplicates."
%(report)s        "Nothing here was written by hand and nothing should be. Regenerate it"
        "with tools/script_tests_to_aver.py."
    exposes [case]
    depends [Domain.Rules, Domain.ScriptState, Domain.SpendContext, Domain.SpendScript]
    effects []

fn case(inputScriptHex: String, outputScriptHex: String, rules: Rules) -> Outcome
    ? "One pair of Scripts, run the way a spend runs them, but bare: these"
      "cases are about what the engine does with a Script and not about any"
      "Transaction, so a signature in one has nothing to have committed to."
    Domain.SpendScript.check(inputScriptHex, outputScriptHex, Domain.SpendContext.bare(rules))

verify case"""

PLACEHOLDER = "Outcome.Decided(Ending.Passed)"

PROBE = """module Main
    intent =
        "Prints this engine's answer for every Script pair, one a line."
        "The middle step: Python assembles the cases, the engine answers them,"
        "and Python writes the answers back. Keeping the three apart is what"
        "stops the corpus from being a test that agrees with itself."
    exposes [main]
    depends [Domain.Rules, Domain.ScriptState, Domain.SpendContext, Domain.SpendScript]
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

fn eachCase(cases: List<Tuple<String, String, Rules>>) -> Unit
    ? "Every case in turn, in the order they were assembled."
    ! [Console.print]
    match cases
        [] -> Unit
        [head, ..tail] -> oneThenRest(head, tail)

fn oneThenRest(one: Tuple<String, String, Rules>, rest: List<Tuple<String, String, Rules>>) -> Unit
    ? "Answer this one, then the rest."
    ! [Console.print]
    _said = printOne(one)
    eachCase(rest)

fn printOne(one: Tuple<String, String, Rules>) -> Unit
    ? "One line, matched apart."
    ! [Console.print]
    match one
        (inputScriptHex, outputScriptHex, rules) -> Console.print(shown(Domain.SpendScript.check(inputScriptHex, outputScriptHex, Domain.SpendContext.bare(rules))))

fn main() -> Unit
    ? "The whole corpus, answered."
    ! [Console.print]
    eachCase(assembled())

%(parts)s"""


def probe(path):
    rows = collected()
    lines = ['        ("%s", "%s", %s),' % (sig, pubkey, rules_literal(flags))
             for sig, pubkey, flags, expected in rows]
    open(path, "w").write(PROBE % {"parts": parts_source(lines)})
    print("wrote probe with %d cases to %s" % (len(rows), path))
    return 0


def parts_source(lines, element="Tuple<String, String, Rules>"):
    """`assembled`, plus a verify case counting what it holds.

    One literal again: jasisz/aver#1054 -- the VM truncating a list literal to
    `len mod 256` elements, silently and with exit 0 -- is fixed upstream.  The
    count stays, because it is what caught that bug: a corpus that cannot say
    how many cases it holds is one that can lose some without saying so.
    """
    out = ['fn assembled() -> List<%s>' % element,
           '    ? "Every case Core supplies, as this tool wrote them."',
           '      "The verify case counts them. A corpus that cannot say how many"',
           '      "cases it holds can lose some quietly, which jasisz/aver#1054 did"',
           '      "to 92 percent of this one before it was fixed."',
           '    [',
           "\n".join(lines).rstrip(","),
           '    ]',
           '',
           'verify assembled',
           '    List.len(assembled()) => %d' % len(lines),
           '']
    return "\n".join(out)



def emit(out_dir, per_file=250, report=""):
    rows = [r for r in collected() if verifiable(r)]
    print("assembled %d cases" % len(rows))
    parts = [rows[i:i + per_file] for i in range(0, len(rows), per_file)]
    for k, chunk in enumerate(parts, start=1):
        lines = []
        for sig, pubkey, flags, expected in chunk:
            lines.append("    // Core: %s  flags %s" % (expected, flags.strip() or "NONE"))
            lines.append('    case("%s", "%s", %s) => %s'
                         % (sig, pubkey, rules_literal(flags), PLACEHOLDER))
        path = os.path.join(out_dir, "scriptcases%d.av" % k)
        open(path, "w").write(HEADER % {"k": k, "n": len(parts), "report": report}
                              + "\n" + "\n".join(lines) + "\n")
        print("wrote %s with %d cases" % (path, len(chunk)))
    return 0


def written_back(out_dir, got):
    """Put the engine's answers into the emitted files, in the order given."""
    it = iter(got)
    for name in sorted(os.listdir(out_dir),
                       key=lambda n: int(re.findall(r"\d+", n)[0])
                       if re.fullmatch(r"scriptcases\d+\.av", n) else 0):
        if not re.fullmatch(r"scriptcases\d+\.av", name):
            continue
        path = os.path.join(out_dir, name)
        out = []
        for line in open(path).read().split("\n"):
            m = re.match(r"    (case\(.*\)) => ", line)
            out.append("    %s => %s" % (m.group(1), next(it)) if m else line)
        open(path, "w").write("\n".join(out))


def excluded_note(pairs):
    """The cases verify cannot run, written into the module rather than dropped.

    A case left out silently is a case nobody sees.  Each one here names its
    size, what Core expects, and what the compiled engine actually answered --
    so the exclusion costs the corpus its verify case and not its coverage.
    """
    if not pairs:
        return ""
    lines = ['        "%d case(s) are answered by the compiled engine and not by verify."\n'
             '        "aver verify runs on a VM with a million-step budget and a Script"\n'
             '        "that is both long and executed exhausts it. Each is named here with"\n'
             '        "the answer the compiled engine gave it, because a case left out"\n'
             '        "silently is a case nobody sees. n1bor/btc-listener#75:"\n' % len(pairs)]
    for (sig, pubkey, flags, expected), answer in pairs:
        lines.append('        "  %d byte Script, flags %s -- Core expects %s, this engine"\n'
                     '        "  answers %s."\n'
                     % (max(len(sig), len(pubkey)) // 2, flags.strip() or "NONE",
                        expected, answer.replace('"', "'")))
    return "".join(lines)


def answers(answers_path, out_dir):
    """Write the engine's own answers into the corpus, and report the agreement."""
    got = [l.rstrip("\n") for l in open(answers_path) if l.startswith("Outcome.")]
    rows = collected()
    if len(got) != len(rows):
        print("have %d answers for %d cases -- refusing to guess which is which"
              % (len(got), len(rows)))
        return 1
    left_out = [(r, a) for r, a in zip(rows, got) if not verifiable(r)]
    agree = undecided = lax = strict = 0
    by_error = {}
    for (sig, pubkey, flags, expected), answer in zip(rows, got):
        core_valid = expected == "OK"
        if answer.startswith("Outcome.Undecided"):
            undecided += 1
            continue
        ours_valid = answer == "Outcome.Decided(Ending.Passed)"
        if ours_valid == core_valid:
            agree += 1
        elif ours_valid:
            lax += 1
            by_error[expected] = by_error.get(expected, 0) + 1
        else:
            strict += 1
            by_error["(we refuse, Core accepts)"] = by_error.get("(we refuse, Core accepts)", 0) + 1
    disagree = lax + strict
    print("cases        %d" % len(rows))
    print("agree        %d" % agree)
    print("disagree     %d" % disagree)
    print("undecided    %d  (this engine cannot answer; not a disagreement)" % undecided)
    print()
    print("  we accept what Core refuses  %d  (a rule not implemented)" % lax)
    print("  we refuse what Core accepts  %d  (the direction a defect shows up in)" % strict)
    if by_error:
        print("\ndisagreements by the error Core expected:")
        for name, n in sorted(by_error.items(), key=lambda kv: -kv[1]):
            print("  %-42s %d" % (name, n))
    report = ('        "Standing as this was generated: %d cases, %d agree with Core,"\n'
              '        "%d disagree, %d this engine cannot answer. Of the disagreements,"\n'
              '        "%d are a rule this engine does not implement and %d are this engine"\n'
              '        "refusing what Core accepts -- which is the direction a defect shows"\n'
              '        "up in, so any of those is a bug until shown otherwise. Run the tool"\n'
              '        "for the breakdown by Core error."\n'
              % (len(rows), agree, disagree, undecided, lax, strict)) + excluded_note(left_out)
    if left_out:
        print("\n%d case(s) answered by the compiled engine but not emitted as verify cases:"
              % len(left_out))
        for (sig, pubkey, flags, expected), answer in left_out:
            print("  %d bytes, Core expects %-12s this engine answers %s"
                  % (max(len(sig), len(pubkey)) // 2, expected, answer))
    emit(out_dir, report=report)
    written_back(out_dir, [a for r, a in zip(rows, got) if verifiable(r)])
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--probe")
    ap.add_argument("--answers")
    ap.add_argument("--out", default=os.path.abspath(os.path.join(HERE, "..", "corpus")))
    a = ap.parse_args()
    if a.fetch:
        fetch()
    if a.emit:
        return emit(a.out)
    if a.probe:
        return probe(a.probe)
    if a.answers:
        return answers(a.answers, a.out)
    if not a.fetch:
        ap.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
