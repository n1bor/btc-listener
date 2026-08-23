#!/bin/bash
# Fetch every Core corpus this project tests against, and regenerate the
# generated domain/*.av case files for any corpus that changed upstream.
#
#     tools/refresh_corpora.sh           # fetch, compare, regenerate what moved
#     tools/refresh_corpora.sh --all     # regenerate everything regardless
#
# Two families, and the difference is the discipline:
#
#   * The pinned family (sighash, key_io x2, base58, BIP341 vectors,
#     script_assets): the expected values are in Core's file, so --fetch and
#     --emit are the whole job and the tool never asks the engine anything.
#   * The probe family (script_tests, tx_tests, witness rows): the expected
#     values are this engine's own answers -- a regression corpus, ADR 0005 --
#     so regeneration runs the engine over the fetched rows (--probe, then
#     `aver run`, then --answers) and records what it said today.
#
# After running this, the ordinary gates decide whether what changed is
# acceptable: `aver check . && aver verify .` and read the diff. A new Core
# row this engine refuses is a finding, not a formatting change.
set -euo pipefail
cd "$(dirname "$0")/.."
DATA=tools/script_tests_data
ASSETS=/tmp/btc-listener-script-assets.json
PROBE_DIR=$(mktemp -d)
trap 'rm -rf "$PROBE_DIR"' EXIT

snapshot() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1 || echo absent; }
changed() { [ "$1" != "$(snapshot "$2")" ]; }

probe_family() {  # tool, corpus files...
  local tool=$1; shift
  python3 "tools/$tool" --probe "$PROBE_DIR/main.av"
  aver run "$PROBE_DIR/main.av" --module-root . > "$PROBE_DIR/answers.txt"
  python3 "tools/$tool" --answers "$PROBE_DIR/answers.txt"
}

ALL=${1:-}
regen=0

# --- the pinned family -------------------------------------------------------
for tool in sighash_tests_to_aver.py key_io_to_aver.py key_io_invalid_to_aver.py \
            base58_to_aver.py bip341_vectors_to_aver.py; do
  before=$(cat $DATA/*.json 2>/dev/null | sha256sum | cut -d' ' -f1)
  python3 "tools/$tool" --fetch
  after=$(cat $DATA/*.json 2>/dev/null | sha256sum | cut -d' ' -f1)
  if [ "$ALL" = "--all" ] || [ "$before" != "$after" ]; then
    python3 "tools/$tool" --emit; regen=1
  fi
done

before=$(snapshot "$ASSETS")
python3 tools/script_assets_to_aver.py --fetch
if [ "$ALL" = "--all" ] || changed "$before" "$ASSETS"; then
  python3 tools/script_assets_to_aver.py --emit; regen=1
fi

# --- the probe family --------------------------------------------------------
before=$(snapshot "$DATA/script_tests.json")
python3 tools/script_tests_to_aver.py --fetch
if [ "$ALL" = "--all" ] || changed "$before" "$DATA/script_tests.json"; then
  probe_family script_tests_to_aver.py
  probe_family witness_tests_to_aver.py   # reads the same file's Witness rows
  regen=1
fi
before=$(cat $DATA/tx_valid.json $DATA/tx_invalid.json 2>/dev/null | sha256sum | cut -d' ' -f1)
python3 tools/tx_tests_to_aver.py --fetch
after=$(cat $DATA/tx_valid.json $DATA/tx_invalid.json 2>/dev/null | sha256sum | cut -d' ' -f1)
if [ "$ALL" = "--all" ] || [ "$before" != "$after" ]; then
  probe_family tx_tests_to_aver.py; regen=1
fi

if [ "$regen" = 1 ]; then
  echo "regenerated; now: aver check . --module-root . && aver verify . --module-root . && read the diff"
else
  echo "every corpus is unchanged upstream; nothing regenerated"
fi
