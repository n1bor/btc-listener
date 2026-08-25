# Testing against a real node, on regtest

`aver verify` checks this program against data the program's own authors chose.
That is most of the evidence this repository has, and it is not enough on its
own: every fixture in the tree is something someone here wrote down, and a
fixture cannot disagree with the assumption that produced it. A Block from a
real node can.

This document is the standing end-to-end test. **Run it before you commit a
change**, and when you find something it does not cover, add the new test here
so the next person inherits it. The point is that coverage only ever grows.

## Why regtest, and not signet

Signet is real data and worth using, but two things it cannot do:

- **A reorganisation cannot be summoned on signet.** You wait for one, and it
  may never come. On regtest, `invalidateblock` produces one on demand. This
  matters more than it sounds: disconnecting a Block is the only path that
  reads **Undo Data** back, rewrites the `h:` keyspace, and rolls the UTXO Set
  backwards. A change can break all three and pass every other test.
- **Blocks cost nothing.** A signet body download runs at about 6 Blocks/s, so
  10,000 Blocks is half an hour. Regtest mines 150 Blocks instantly, which is
  the difference between a test you run every time and a test you skip.

What regtest cannot do is misbehave — see [A Peer that
lies](#a-peer-that-lies) below.

## Setting up the node

Bitcoin Core is not on `PATH` and is not vendored. Fetch it once (48 MB from
bitcoincore.org) into a scratch directory:

```bash
curl -sLO https://bitcoincore.org/bin/bitcoin-core-27.0/bitcoin-27.0-x86_64-linux-gnu.tar.gz
tar xf bitcoin-27.0-x86_64-linux-gnu.tar.gz
```

Use an explicit `rpcuser`/`rpcpassword` rather than the cookie file. The cookie
is written on startup and removed on shutdown, so a node that died badly leaves
a datadir you cannot authenticate to and can only replace:

```bash
RT=$PWD/rt-a; mkdir -p $RT
cat > $RT/bitcoin.conf <<'EOF'
regtest=1
server=1
listen=1
fallbackfee=0.0001
rpcuser=av
rpcpassword=av
[regtest]
port=18444
rpcport=18443
EOF
bitcoin-27.0/bin/bitcoind -datadir=$RT -daemon
C="bitcoin-27.0/bin/bitcoin-cli -datadir=$RT -rpcuser=av -rpcpassword=av -regtest"
```

**Check the ports first.** Another agent may have a node up, and its datadir
takes an exclusive lock. `ss -ltnp | grep 1844` says who holds what; match
`bitcoind` processes by their full `-datadir=` argument before you kill one,
the same way you would match a `main` process by its full arguments.

## The test

### 1. A chain with something in it

150 Blocks makes the first 50 coinbases mature, so they can be spent:

```bash
$C -named createwallet wallet_name=w
ADDR=$($C getnewaddress)
$C generatetoaddress 150 "$ADDR"
```

**Coinbase-only Blocks prove almost nothing.** An audit over them reports
`0 spends, 0 scripts` and says CLEAN, which is a clean bill of health for code
that was never run. Spend to each address type so the Script engine is actually
asked something:

```bash
A=$($C getnewaddress)                  # P2WPKH
B=$($C getnewaddress "" bech32m)       # P2TR
L=$($C getnewaddress "" legacy)        # P2PKH
P=$($C getnewaddress "" p2sh-segwit)   # P2SH-P2WPKH
for t in 1 2 3 4 5; do
  $C sendtoaddress $A 1.0;  $C sendtoaddress $B 0.5
  $C sendtoaddress $L 0.25; $C sendtoaddress $P 0.1
done
$C generatetoaddress 10 "$($C getnewaddress)"
```

### 2. Every command, in order

```bash
BIN=../btc-listener-build/target/release/main
D=$PWD/chain-rt
$BIN regtest headers 127.0.0.1 $D
$BIN regtest bodies  127.0.0.1 $D 1 160
$BIN regtest txindex $D 1 160
$BIN regtest outputs $D 1 160
$BIN regtest utxo    $D 160
$BIN regtest audit   $D 1 160
```

The audit line is the one that matters. It should end:

```
blocks 160  transactions 180  spends resolved 20  coinbase 160  unresolved 0
scripts 20 passed / 0 failed / 0 undecided  CLEAN (faults 0, script failures 0)
```

**Read the counts, not the word CLEAN.** `spends resolved 0` or `scripts 0
passed` means the run proved nothing, however clean it claims to be.

### 3. The reorganisation

This is the part signet cannot give you. Invalidate a Block, build a longer
branch, and let the listener discover it:

```bash
$C invalidateblock $($C getblockhash 156)
$C generatetoaddress 12 "$($C getnewaddress)"     # now longer than the branch it left
timeout 90 $BIN regtest follow 127.0.0.1 $D
```

Expect, in order: the Header walk noticing, the Set being taken back, one line
per disconnected Height, the new bodies arriving, and the Set reconnecting.

```
headers to 167  REORGANISED: 5 Height(s) re-pointed above 155
REORGANISED: the Set stands on a branch the chain has left; taking it back to
  Height 155, disconnecting 5 Block(s)
  height 160 disconnected: set +0 -1
  ...
following at Height 167: 12 connected, 0 disconnected, set +12 -0
```

`follow` never returns on its own, so run it under `timeout`. Exit 124 is the
expected outcome, and the polite stop should print `stop requested; closing the
pool and releasing the claim` on the way out.

### 4. Agree with Core, hash for hash

A listener can be internally consistent and still wrong. The only external
oracle here is Core itself, so compare Block Ids — at the tip and at a Height
the reorganisation moved:

```bash
$BIN regtest audit $D 1 167              # CLEAN again, over the new chain
$BIN regtest show  $D 167 summary        # block ... must equal:
$C getblockhash 167
$BIN regtest show  $D 156 summary        # and at the reorganised Height:
$C getblockhash 156
```

If 156 still shows the abandoned Block, the `h:` keyspace was not re-pointed
and everything downstream of it is reading a chain that no longer exists.

### 5. The Mempool, and relay

Everything above is about Blocks. The Mempool is the one thing this node holds
that no Block contains, and regtest is the only place its whole life can be
watched: admitted, announced, asked for, and then confirmed away.

Core announces a Transaction only to Peers that were connected when it arrived,
and it never re-offers what is already in its Mempool. So `follow` has to be up
first, and the Transaction has to be a new one:

```bash
$BIN regtest follow 127.0.0.1 $D &
sleep 20
$C sendtoaddress $($C getnewaddress "" bech32) 0.5
```

**Then wait, and wait longer than feels right.** Core spreads Transaction
Announcements to inbound Peers over a Poisson delay averaging five seconds, so
the line below has arrived anywhere between two and forty seconds later. An
eight-second window looks exactly like a broken listener, which is how the
first run of this test was misread:

```
mempool admitted 1a63298f…8f9f: 219 bytes, 2190 sat at 10000/kB; holding 1 (219 bytes)
```

Three things are being proved by that line and each is worth checking
separately. The **identifier** must equal what Core says:

```bash
$C getrawmempool          # the same txid, and only it
```

The **fee** is the one number here that is not in the Transaction: it is what
the Inputs were worth minus what the Outputs pay, and the Inputs are knowable
only from the UTXO Set. It is therefore the number that catches an admission
that resolved the wrong Output, or resolved it from the wrong keyspace:

```bash
$C getmempoolentry <txid> | grep base       # "base": 0.00002190
```

`2190 sat` is `0.00002190`. A mismatch means the Set lookup answered about
something other than what this Transaction spends.

### 6. Relay, with a second node that has no other Peer

An admission that is never passed on is half a Mempool. The only way to prove
relay is a node that could not have heard it from anywhere else:

```bash
RT2=$PWD/rt-b; mkdir -p $RT2      # same bitcoin.conf, ports 18463/18464
bitcoin-27.0/bin/bitcoind -datadir=$RT2 -daemon
C2="bitcoin-27.0/bin/bitcoin-cli -datadir=$RT2 -rpcuser=av -rpcpassword=av -regtest"
$C2 addnode "127.0.0.1:18444" onetry     # borrow the chain
$C2 disconnectnode "127.0.0.1:18444"     # then cut it off
$C2 getconnectioncount                   # 0 before the listener joins
```

Point `follow` at both, make a new Transaction on the first node, and the
second one must end up holding it:

```bash
$BIN regtest follow 127.0.0.1:18444,127.0.0.1:18464 $D &
$C sendtoaddress $($C getnewaddress "" bech32) 0.12
$C2 getrawmempool                        # the same txid, arrived only through us
```

**Both nodes must be at the same Height first.** A Transaction spending an
Output from a Block the second node has not got is an orphan there: it accepts
the Transaction, logs nothing, and never puts it in its Mempool. That looks
identical to a relay failure and is not one. `getblockcount` on both before
starting, and re-borrow the chain if they differ.

With `$C2 logging '["net"]'` the second node's `debug.log` shows the whole
exchange, which is what to read when the Mempool stays empty:

```
[net] got inv: tx 1a63298f…  new peer=2
[net] Requesting tx 1a63298f… peer=2
[net] sending getdata (37 bytes) peer=2
[net] received: tx (219 bytes) peer=2
```

That sequence is the proof: we announced, it asked, we served. A node that
announces and then cannot answer wastes the round trip of every Peer that
asked, and Peers drop one that does it.

### 7. What a Block takes back

```bash
$C generatetoaddress 1 "$($C getnewaddress)"
```

```
following at Height 169: 1 connected, 0 disconnected, set +11 -10
mempool: 2 Transaction(s) left, taken by the chain; holding 0 (0 bytes)
```

The count must reach zero. What this catches is an admission built on the wrong
keyspace: the `o:` Outputs index holds every Output the chain ever made,
including ones spent years ago, while the UTXO Set holds only what is unspent.
A Mempool resolving Inputs through `o:` accepts double spends, relays them, and
passes every other test in this document — and its held count would not fall to
zero here, because what it holds was never really spendable.

### 8. A compact Block, from Core's own hand

BIP152 names Transactions by six-byte short ids: a slice of a SipHash under a
key both nodes derive from the Header and a nonce the sender chose. Nothing in
this repository can check that we derive it the way everyone else does —
`Domain.CompactBlock`'s own cases are fixtures we wrote, and a wrong key
matches nothing, reconstructs nothing, and reports no fault while doing it.

`tools/regtest/cmpctblock-capture.py` speaks just enough of the protocol to be
sent a real one. It handshakes, says `sendcmpct`, and waits:

```bash
python3 tools/regtest/cmpctblock-capture.py 127.0.0.1 18444 cap.json &
sleep 2
$C sendtoaddress $($C getnewaddress "" bech32) 0.6     # something to name
$C sendtoaddress $($C getnewaddress "" bech32m) 0.7
$C generatetoaddress 1 "$($C getnewaddress)"
```

```
captured a compact Block for 7e2eb275b31ac84e…: 2 short id(s), 1 prefilled
```

Two short ids and one prefilled is the shape to expect: the two spends are
named, and the coinbase is sent whole because it cannot be in anybody's
Mempool. Then turn the capture into cases and run them:

```bash
python3 tools/cmpct_oracle_to_aver.py cap.json
aver verify domain/compactblockcases.av --module-root .
```

The cases pin four things against Core: the key derived from the Header and
nonce, the short id of each Witness Transaction Id under that key, the
decoding of the Message itself, and — the one that ties it together — **the
Merkle Root of the Block rebuilt from it**. That last case fills every
position from the prefilled Transactions and the ones a node would have held,
hashes the result, and compares it with the Root in the Header Core sent. If
reconstruction puts one Transaction in the wrong place, the Root says so.

Check that case can fail before believing it: take one Transaction out of
`held()` and re-run, and `rebuiltRoot` must fail. A reconstruction test that
cannot fail is worse than none, because it reads like proof. **Do not skip the key case.** Four plausible
mistakes all produce a decoder that looks like it works — the double SHA-256
instead of the single, the Transaction Id instead of the Witness Transaction
Id, the digest read big-endian, the id in reading order rather than wire
order — and each of them silently names every Transaction wrongly.

**This capture needs a node, so the generated file is committed** rather than
rebuilt in CI, the same way the Core corpora are.

## A Peer that lies

Bitcoin Core is cooperative by construction: you cannot ask it for a bad
checksum or another Network's magic, so the paths that exist for Peers that lie
need a Peer built to lie. `tools/regtest/liar.py` is about forty lines — it
binds a port, answers the version handshake, then sends one bad frame:

```bash
python3 tools/regtest/liar.py 18455 checksum &     # or: network
$BIN regtest follow 127.0.0.1:18455,127.0.0.1:18444 $D
```

The question is not whether the offender is noticed. It is whether it is
**dropped while the node keeps working**:

```
dropping peer 0: peer 0: a Message did not match the checksum its own header carries
following at Height 107: 107 connected, 0 disconnected, set +107 -0
```

This found two real bugs in #27 within ten minutes. **Run the honest baseline
first** — a validity check that has never seen real data can be a permanent
false accusation, and only the honest run shows it.

## Before you commit

The language gates come first, and none of them is optional:

```bash
aver format .
aver check   . --module-root .
aver verify  . --module-root .
aver compile main.av --module-root . -o ../btc-listener-build
cd ../btc-listener-build && cargo build --release
cargo test --manifest-path providers/primitives/Cargo.toml
cargo test --manifest-path providers/kv/Cargo.toml
```

`cargo build` is not a formality: three failure classes survive `check`,
`verify` **and** `compile`, and are listed in CLAUDE.md.

**Then run this document.** Sections 1–4 take a few minutes on regtest and are
the only evidence in the project that comes from outside the project. A change
that passes every gate and breaks the reorganisation path is a change that
passes every gate.

Scale the range to the change. A change to the Script engine deserves a signet
audit over thousands of real Blocks; a change to the Store needs the reorg
above and little else.

## Adding to this document

When you test something this file does not cover, **add it here**. Two rules:

- **Write down what the output should look like**, not just the command. A test
  whose passing condition is "it didn't crash" is a test that will be quietly
  broken for months.
- **Say what it would have caught.** Every section above exists because
  something went wrong: the reorg section because a reorganisation is the only
  reader of Undo Data, the spending transactions because an audit over coinbase
  Blocks says CLEAN while proving nothing, the liar because Core will not
  misbehave on request.

Coverage here only grows. Nothing in this file should be deleted because it
seems unlikely — it is in the file because it already happened once.
