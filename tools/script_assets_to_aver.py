#!/usr/bin/env python3
"""Turn Bitcoin Core's script_assets_test.json into Aver verify cases.

The only published corpus that tests tapscript execution: 2,244 entries, each
a Transaction, its prevouts, the Input to run, the flags Core ran it under,
and one or two spenders -- `success`, which must pass, and `failure`, which
must fail.  This emits one verify case per spender, and the expected value of
every case is the same word: PASS, meaning the engine agreed with the file.
The claim is Core's; this tool never asks the engine anything.

It used to be the `taproottest` command, which fetched the file at run time
and printed a report, because the corpus was too slow and too big for the
verify VM.  jasisz/aver#1104 made verify fast and jasisz/aver#1071 gave
[[verify.costly]] a per-file step budget, so it is a corpus like the others
now.  n1bor/btc-listener#101.

Usage:
    python3 tools/script_assets_to_aver.py --fetch   # the file, to /tmp
    python3 tools/script_assets_to_aver.py --emit    # domain/assetcases*.av

The JSON is 9 MB and is not committed; the generated cases are.
"""

import argparse
import json
import os
import sys
import urllib.request

URL = "https://raw.githubusercontent.com/bitcoin-core/qa-assets/main/unit_test_data/script_assets_test.json"
CACHE = "/tmp/btc-listener-script-assets.json"
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "domain")
PER_FILE = 250


def fetch():
    data = urllib.request.urlopen(URL, timeout=120).read()
    with open(CACHE, "wb") as f:
        f.write(data)
    print("fetched %d bytes to %s" % (len(data), CACHE))


def aver_str(text):
    assert '"' not in text and "\\" not in text and "{" not in text, text
    return '"%s"' % text


def aver_list(items):
    return "[%s]" % ", ".join(aver_str(x) for x in items)


def case_lines(entry, number):
    lines = []
    comment = entry.get("comment", "(no comment)").replace("\n", " ")
    tx = entry["tx"]
    prevouts = entry["prevouts"]
    index = entry["index"]
    flags = entry["flags"]
    for name, must_pass in (("success", "true"), ("failure", "false")):
        spender = entry.get(name)
        if spender is None:
            continue
        lines.append("    // entry %d %s: %s  flags %s" % (number, name, comment, flags))
        lines.append("    case(%s, %s, %d, %s, %s, %s, %s) => \"PASS\"" % (
            aver_str(tx), aver_list(prevouts), index, aver_str(flags),
            aver_str(spender["scriptSig"]), aver_list(spender["witness"]), must_pass))
    return lines


def emit():
    entries = json.load(open(CACHE))
    parts = [entries[i:i + PER_FILE] for i in range(0, len(entries), PER_FILE)]
    total = 0
    for k, chunk in enumerate(parts, start=1):
        first = (k - 1) * PER_FILE + 1
        last = first + len(chunk) - 1
        path = os.path.join(OUT_DIR, "assetcases%d.av" % k)
        cases = []
        for offset, entry in enumerate(chunk):
            cases.extend(case_lines(entry, first + offset))
        total += sum(1 for line in cases if line.lstrip().startswith("case("))
        with open(path, "w") as f:
            f.write("module AssetCases%d\n" % k)
            f.write("    intent =\n")
            f.write('        "Bitcoin Core\'s own script_assets_test.json, entries %d to %d of %d, as"\n' % (first, last, len(entries)))
            f.write('        "verify cases: part %d of %d."\n' % (k, len(parts)))
            f.write('        "The only published corpus that tests tapscript execution. Two spenders"\n')
            f.write('        "per entry and they are opposite claims: success must pass and failure"\n')
            f.write('        "must fail, so every case expects the same word, PASS, meaning this"\n')
            f.write('        "engine agreed with the file. The claim is Core\'s, never the engine\'s."\n')
            f.write('        "Nothing here was written by hand and nothing should be. Regenerate it"\n')
            f.write('        "with tools/script_assets_to_aver.py. n1bor/btc-listener#101."\n')
            f.write("    exposes [case]\n")
            f.write("    depends [Domain.AssetSweep]\n")
            f.write("    effects []\n\n")
            f.write("fn case(tx: String, prevouts: List<String>, index: Int, flags: String, scriptSig: String, witness: List<String>, mustPass: Bool) -> String\n")
            f.write('    ? "One spender of one entry, judged against the file\'s claim about it."\n')
            f.write("    Domain.AssetSweep.judged(tx, prevouts, index, flags, scriptSig, witness, mustPass)\n\n")
            f.write("verify case\n")
            f.write("\n".join(cases) + "\n")
        print("wrote %s: entries %d..%d" % (path, first, last))
    print("%d cases across %d files" % (total, len(parts)))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--emit", action="store_true")
    args = parser.parse_args()
    if args.fetch:
        fetch()
    if args.emit:
        if not os.path.exists(CACHE):
            sys.exit("no %s; run with --fetch first" % CACHE)
        emit()
    if not (args.fetch or args.emit):
        parser.print_help()


if __name__ == "__main__":
    main()
