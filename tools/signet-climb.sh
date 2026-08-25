#!/bin/bash
# Take a signet chain up in 10,000-Block chunks, auditing each one as it lands.
#
#     tools/signet-climb.sh          # edit DIR, PEER, FROM and TOP below first
#
# Each chunk is fetched, indexed, connected to the Set and then audited before
# the next begins, so a fault is found within ten thousand Blocks of where it
# started rather than at the end of a run measured in days. The log is one
# file: every chunk's own lines, and the audit summary that closes it.
#
# Why chunks rather than one long range: an audit over the whole chain is a
# single process holding a single claim for hours, and anything that stops it
# -- a kill, a full disk, a bug at Height 240,000 -- loses the lot. A chunk
# that dies loses one chunk, and the next run picks up from the Height the
# Set actually reached, because everything here resumes.
#
# Read the audit counts, not the word CLEAN: `scripts 0 passed` over ten
# thousand Blocks means the run proved nothing. docs/regtest-testing.md says
# more about that.
set -uo pipefail
BIN=/home/owensr/aver/btc-listener-build/target/release/main
DIR=/home/owensr/aver/chains/values-test
PEER=135.180.99.74
LOG=/home/owensr/aver/chains/signet-climb.log
FROM=10001
TOP=310000

say() { printf '\n=== %s  %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a $LOG; }
run() { echo "--- $*" >> $LOG; nice -n 10 "$@" >> $LOG 2>&1; }

while [ $FROM -lt $TOP ]; do
    TO=$((FROM + 9999))
    say "chunk $FROM..$TO: bodies"
    run $BIN signet bodies $PEER $DIR $FROM $TO   || { say "bodies failed at $FROM"; break; }
    say "chunk $FROM..$TO: txindex"
    run $BIN signet txindex $DIR $FROM $TO        || { say "txindex failed"; break; }
    say "chunk $FROM..$TO: outputs"
    run $BIN signet outputs $DIR $FROM $TO        || { say "outputs failed"; break; }
    say "chunk $FROM..$TO: utxo to $TO"
    run $BIN signet utxo $DIR $TO                 || { say "utxo failed"; break; }
    say "chunk $FROM..$TO: audit"
    run $BIN signet audit $DIR $FROM $TO          || { say "audit failed"; break; }
    say "chunk $FROM..$TO done; directory now $(du -sh $DIR | cut -f1)"
    FROM=$((TO + 1))
done
say "climb stopped, reached $FROM"
