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

**If you reused a datadir, generate past the old tip and not past the fork.**
The twelve above assume Core is at 160, so invalidating 156 and building
twelve puts the new branch at 167 — ahead of the 160 it left. A datadir left
over from a previous session may be at 179, and then twelve Blocks build a
*shorter* branch than the one abandoned. This node will refuse it, correctly,
and print nothing that looks like a reorganisation:

```
have 180 headers; asking for more
headers complete: 180 known
```

That is the right answer to less work, not a broken Header walk — and the way
to tell the difference is Core's own net log, which will say it sent them
(`sending headers (2188 bytes)`) while our count does not move. Count from
`$C getblockcount` before you invalidate, and build past *that*.

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
aver verify corpus/compactblockcases.av --module-root .
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

### 9. A Transaction that only the abandoned branch ever had

The sweep in section 7 removes what a Block confirms. This is the other half:
what a Block **un**-confirms has to come back, and the only way to see it is a
Transaction the winning branch has never heard of.

Core will not give you one by itself. Invalidate a Block and Core puts its
Transactions back in its own Mempool and mines them straight into the
replacement, so the Transaction is confirmed again and nothing was restored.
The trick is a second node that never saw it:

```bash
# both nodes on the same chain, then cut node 2 off
$C2 addnode "127.0.0.1:18444" onetry; sleep 10
$C2 disconnectnode "127.0.0.1:18444"

$BIN regtest follow 127.0.0.1:18444,127.0.0.1:18464 $D &

TX=$($C sendtoaddress $($C getnewaddress "" bech32) 0.55)   # node 1 only
$C generatetoaddress 1 "$($C getnewaddress)"                # into node 1's branch
$C2 generatetoaddress 2 "$($C2 getnewaddress)"              # a longer branch without it
```

Node 2's branch is longer, so the node follows it, and the Block holding the
Transaction is disconnected. Expect, in order:

```
mempool admitted 60876dcb…ba64: 245 bytes, 1641 sat at 6697/kB; holding 1 (245 bytes)
following at Height 176: 1 connected, 0 disconnected, set +3 -1
mempool: 1 Transaction(s) left, taken by the chain; holding 0 (0 bytes)
headers to 177  REORGANISED: 1 Height(s) re-pointed above 175
REORGANISED: the Set stands on a branch the chain has left; taking it back to
  Height 175, disconnecting 1 Block(s)
  height 176 disconnected: set +1 -3
following at Height 177: 2 connected, 0 disconnected, set +2 -0
mempool: 1 Transaction(s) offered back from disconnected Block(s), 1 admitted; holding 1 (245 bytes)
```

**Read both numbers on the last line.** Offered and admitted are reported
separately because they differ for a good reason: a reorganisation whose new
Blocks spend the same Outputs offers Transactions that are then correctly
refused, and `3 offered, 0 admitted` is a right answer. A silence would be
ambiguous between that and nothing having been carried at all — which is
exactly the ambiguity that cost an hour here.

**Confirm the binary you are running contains the change** before believing
this — or any — negative result. See [Prove the binary is the one you
built](#prove-the-binary-is-the-one-you-built) below; this test was run three
times against a stale binary before anyone noticed.

### 10. A Block that never crossed the wire

Section 8 proves the compact Block *format* against Core. This proves the
point of it: a Block arriving as a Header and a list of six-byte names, and
being rebuilt from Transactions the node already had.

Fill the Mempool first — a compact Block whose Transactions we do not hold
saves nothing and falls back to fetching them:

```bash
$BIN regtest follow 127.0.0.1:18444 $D &
sleep 10
for i in 1 2 3; do $C sendtoaddress $($C getnewaddress "" bech32) 0.1$i; done
# wait for three "mempool admitted" lines, then:
$C generatetoaddress 1 "$($C getnewaddress)"
```

```
compact Block 339bd1ed…b162: 4 Transaction(s), 0 fetched, 898 bytes rebuilt from the Mempool
following at Height 177: 2 connected, 0 disconnected, set +10 -4
mempool: 3 Transaction(s) left, taken by the chain; holding 0 (0 bytes)
```

**`0 fetched` is the number to read.** Four Transactions: the coinbase, which
the sender always sends whole because it cannot be in anybody's Mempool, and
three we already held. No `getblocktxn` round trip happened at all.

Then confirm the rebuilt Block is the Block — stop `follow` first, since it
holds the directory:

```bash
$C getblockhash 177                       # 339bd1ed…b162
$BIN regtest show $D 177 summary
```

```
height 177
block  339bd1ed0687d50dcbc5d9fab148772627f126775d8bd311b020b31bc05db162
body   segment 0 offset 56063, 898 bytes
check  header hashes to the recorded Block Id
merkle Transactions build the Root in the Header
parent follows the Block below it in the Index
work   Block Id is under the target in the Header
txs    4
```

All four checks pass on bytes that were never sent to us. `merkle` is the one
that matters here: it is the same check the reconstruction already made before
storing, made again by a different path over what actually reached the disk.

**What to try when it does not happen.** The line is absent, not wrong, in
every failure — the node falls back to fetching the Block whole and says so:

- `fetching it whole instead` names the reason. A short id collision, a Peer
  that answered oddly, a Mempool that had moved on: all of them end here, and
  the node is exactly where it would have been without compact Blocks.
- No compact line at all means Core did not send one. It only serves
  `MSG_CMPCT_BLOCK` for Blocks near its tip, and only to Peers that sent
  `sendcmpct` — which this node does on connect, for every Peer, version 2.
- A Mempool with nothing in it rebuilds nothing. `0 of 4 already held, asking
  for 3` is the honest version of that, and still correct; it just saves
  nothing.

### 11. A Peer that dialled us

Everything above this node did by dialling out. This is the other direction:
`serve` binds a port and answers callers (#30).

Bitcoin Core already holds the regtest port, so give the listener another —
which is what `serve:PORT` is for, and what any second node on one machine
needs:

```bash
$BIN regtest follow 127.0.0.1:18444 $D serve:18455 &
```

```
listening on port 18455 for up to 8 inbound Peer(s)
```

Then have the *second* node dial it. It must be a node with no other route to
us, or you have proved nothing about the inbound path:

```bash
$C2 addnode "127.0.0.1:18455" onetry
```

```
peer 1 dialled us from 127.0.0.1:48492
```

**Check the handshake from the caller's side**, because that is the half this
node has never done before — it has always been the one to open with
`version`, and an inbound Peer expects ours in reply before its `verack`:

```bash
$C2 getpeerinfo
```

```
id 17  127.0.0.1:18455  '/aver-btc-listener:0.1/'  outbound  v70016
```

#### The first connection is dropped, and that is Core, not us

Expect this in our log, every time, before the one that works:

```
peer 1 dialled us from 127.0.0.1:52080
dropping inbound peer 1 from 127.0.0.1:52080: peer 1: a Message announced
  2918162963 payload bytes, which is more than the 4000000 the protocol allows
peer 1 dialled us from 127.0.0.1:52092
```

Two connections from one `onetry`, and a first frame that announces nearly
three gigabytes. Nothing is wrong. Core's own net log says what it is:

```
[net] trying v2 connection 127.0.0.1:18455 lastseen=0.0hrs
[net] disconnecting peer=2
[net] trying v1 connection 127.0.0.1:18455 lastseen=0.0hrs
New manual v1 peer connected: version: 70016, blocks=183, peer=3
```

The first attempt is **BIP324 v2 encrypted transport**, which opens with a
64-byte ellswift public key and random padding rather than a Message header.
Read as v1 that is a length field of noise, and refusing it is the right
answer — a node that trusted it would try to allocate what it announced.
Core notices the disconnect, downgrades, and the v1 connection syncs.

So the alarming line is a *pass*, not a fail. What would be a fail is the
second connection never arriving, or `transport_protocol_type` reading
anything but `v1` in `getpeerinfo`. Speaking v2 is not implemented and is not
pretended: it is a separate piece of work, and until it exists every Core Peer
costs one refused connection on the way in.

Core knowing our user agent and protocol version is the proof: it only learns
those from a `version` it received and acknowledged.

**Then prove the Peer is first-class, not merely connected.** An inbound Peer
that cannot be relayed to is a socket, not a Peer. Make a Transaction on the
first node and read the *second* node's log:

```bash
$C2 logging '["net"]'
$C sendtoaddress $($C getnewaddress "" bech32) 0.31
```

```
[net] received: inv (37 bytes) peer=17
[net] got inv: tx a5352641…  new peer=17
[net] Requesting tx a5352641… peer=17
[net] received: tx (222 bytes) peer=17
```

Announced to it, asked for by it, served to it — over a connection it opened.
Nothing in the relay path knows which side dialled, which is the point: an
inbound Peer is seated in the same pool, under the same standing, with the
same key space.

**It will not enter the second node's Mempool unless both nodes are at the
same Height** — same trap as section 6, and it looks identical to a relay
failure. Check `getblockcount` on both before concluding anything.

#### What this does not yet do

Serving the chain. A Peer that dials us can talk to us, but `getheaders` and
`getdata` for Blocks are not answered yet, so nobody can sync from this node —
which is the rest of #30, along with the DoS work the issue asks for. What is
here is the accept loop, the handshake from the answering side, an inbound
cap, and a caller that misbehaves costing itself its slot and nothing else.

### 12. A node that syncs from us

This is the finish line the full-node plan named first, and the only test that
proves it: an empty Bitcoin Core, whose **only** Peer is this node, building
the whole chain from what we serve.

**Use a genuinely empty node** for this half of the test — `getblockcount`
must say 0. An empty node's Locator is just genesis, which is on every chain,
so it agrees with us about the one Block that cannot be in dispute. A node
holding a chain that forked from ours asks a harder question, and §12b is
where that question is asked; running only the empty case passed straight
through [#171](https://github.com/n1bor/btc-listener/issues/171) for as long
as that bug existed.

```bash
RT3=$PWD/rt-c; mkdir -p $RT3      # same bitcoin.conf, ports 18473/18474
bitcoin-27.0/bin/bitcoind -datadir=$RT3 -daemon
C3="bitcoin-27.0/bin/bitcoin-cli -datadir=$RT3 -rpcuser=av -rpcpassword=av -regtest"
$C3 getblockcount                  # 0

$BIN regtest follow 127.0.0.1:18444 $D serve:18455 &
$C3 addnode "127.0.0.1:18455" onetry
```

```
listening on port 18455 as NODE_NETWORK|NODE_WITNESS at Height 179, for up to 8 inbound Peer(s)
peer 1 dialled us from 127.0.0.1:40228
served 179 Header(s) to peer 1
served a Block of 898 bytes to peer 1
...
```

```bash
$C3 getblockchaininfo
```

```
blocks 179  headers 179  verificationprogress 1  initialblockdownload False
```

**Check the tip hash matches, not just the height** — a node can be at the
right height on the wrong chain:

```bash
$C3 getbestblockhash        # must equal the first node's getblockhash 179
```

#### Two service bits, and what happens without them

Both of these were found here, and both look identical to a node nobody wants
to talk to:

- **Without `start_height`**, Core reads our `version` as a Peer with no
  chain and never sends a `getheaders`. Watch for `startingheight` in the
  other node's `getpeerinfo`: if it says 0 while we hold a chain, we are
  advertising ourselves as useless.
- **Without `NODE_WITNESS`**, Core takes our Headers and then **never asks for
  a Block**. With SegWit active it will not request Blocks from a Peer that
  cannot serve Witnesses, so the sync stops silently at `synced_headers 179,
  synced_blocks 0`. `getpeerinfo`'s `servicesnames` must show `WITNESS`
  beside `NETWORK`.

A getdata for a Block also arrives as the **witness** Block type, not the
plain one — answering only the plain type serves nothing to any modern Peer,
which looks exactly like a node that has the Blocks and will not part with
them. `inflight` on the asking node fills up and never empties.

### 12b. A node on a branch we abandoned

The empty node in §12 agrees with us about genesis and nothing else is at
stake. This is the case that has something at stake: a Core left on a branch
this node walked away from, which must be told where the two chains actually
part company. It was [#171](https://github.com/n1bor/btc-listener/issues/171),
and it went unnoticed because §12 alone cannot see it.

Take a second Core all the way up our chain, then cut it loose so it keeps
holding that branch while the first node builds a longer one:

```bash
RT2=$PWD/rt-b; mkdir -p $RT2      # same bitcoin.conf, ports 18454/18453
bitcoin-27.0/bin/bitcoind -datadir=$RT2 -daemon
C2="bitcoin-27.0/bin/bitcoin-cli -datadir=$RT2 -rpcuser=av -rpcpassword=av -regtest"
$C2 addnode "127.0.0.1:18444" onetry
# wait until $C2 getblockcount equals $C getblockcount, then strand it
$C2 disconnectnode "127.0.0.1:18444"
$C2 setnetworkactive false
$C2 getbestblockhash               # remember this: the abandoned tip
```

Follow the first node up to that same tip, then make the first node abandon
the branch and build a longer one:

```bash
$BIN regtest follow 127.0.0.1:18444 $D     # up to 160, then Ctrl-C
$C invalidateblock $($C getblockhash 156)
$C generatetoaddress 32 "$($C getnewaddress)"      # note: a FRESH address
$BIN regtest follow 127.0.0.1:18444 $D     # onto the new branch, 187
```

**Mine to a fresh address.** `generatetoaddress` to the same address you
invalidated from rebuilds the *identical* Block — same parent, same coinbase
output, and an empty Mempool leaves nothing else to vary — so Core recognises
the hash it has just marked invalid and refuses it:

```
error code: -32603
error message:
ProcessNewBlock, block not accepted
```

Nothing in that message points at the address, and the node sits at 155
refusing every attempt. A new address changes the coinbase, and with it the
Merkle root and the Block Id.

Now serve, and let the stranded node ask:

```bash
$BIN regtest follow 127.0.0.1:18444 $D serve:18455 &
$C2 setnetworkactive true
$C2 addnode "127.0.0.1:18455" onetry
```

```
listening on port 18455 as NODE_NETWORK|NODE_WITNESS at Height 187, for up to 8 inbound Peer(s)
peer 1 dialled us from 127.0.0.1:55206
served 32 Header(s) to peer 1
served a Block of 250 bytes to peer 1
...
```

**Count the Headers.** Thirty-two is 156..187 — everything above the Height
the two chains genuinely share. Twenty-eight would be 160..187, counted from
the caller's own abandoned tip, and that is the bug: Headers whose parent the
caller has never seen. It rejects them, asks the same question again, and
neither side ever moves.

```bash
$C2 getblockcount          # 187
$C2 getbestblockhash       # must equal the first node's getblockhash 187
$C2 getpeerinfo | grep synced
```

```
"synced_headers": 187,
"synced_blocks": 187,
```

`synced_headers: -1` is the tell that this is failing: it means the caller has
accepted no Header at all from us, however many we think we served.

### 13. A Candidate that will not answer

The dial stopped being a special case in the schedule when
[jasisz/aver#1125](https://github.com/jasisz/aver/issues/1125) landed:
`Infra.Peers.joined` calls `Tcp.beginConnect`, keeps the `Tcp.Dial` as a
local, and polls it as one more key beside every Peer. What that buys is not
speed — a dead address still costs its five seconds — but *whose* five seconds
they are. They used to be the whole pool's.

Prove the deadline still fires, and that one Candidate refusing is not the end
of the node. `198.51.100.1` is TEST-NET-2 and routes nowhere, so it never
answers rather than refusing quickly, which is the case that matters:

```bash
timeout 60 $BIN regtest follow 198.51.100.1,127.0.0.1:18444 $D
```

```
peer 198.51.100.1:18444 refused: Tcp.beginConnect: socket establishment timed
  out (deadline: 5000 ms)
peer 0 is 127.0.0.1:18444
headers complete: 184 known
following at Height 183: 0 connected, 0 disconnected, set +0 -0
```

Three things to read, not one:

- **`deadline: 5000 ms`** is `connect_timeout_secs` from `aver.toml` reaching
  the log. A dial with no deadline hangs the startup walk instead.
- **The live Peer takes key 0**, not key 1. A Candidate that never seats does
  not consume a key, which is what makes an Address Book full of dead
  addresses cost keys rather than Peers.
- **The node reaches the tip anyway.** A refused Candidate prints and is
  stepped past; it is a fact about that address, not about the run.

Note the **comma**. `follow` takes one argument for all its Peers, so
`follow a b $D` reads `b` as the directory and `$D` as an extra — which fails
several steps later, saying `no Peer is held under identifier 0`, and looks
nothing like the argument mistake it is.

The half this cannot show is the part worth having: that while the dial is in
flight the other Peers are still being read. It needs a Peer talking during
the five seconds and a way to see that its bytes arrived, which regtest gives
no comfortable handle on. `Infra.Peers.settlingOn` is where it happens —
`reading(pool, ready)` before the dial is looked at — and the shape of the
code is the argument until someone finds a way to test it.

### 14. The Screen, without a terminal to watch it in

The Screen only draws to a real terminal — `Terminal.enableRawMode` needs a
pty — so a headless check has to give it one. `script` does, and its capture
is an ordinary file with the escape codes left in, which is enough to grep:

```bash
script -qf -c "timeout 150 $BIN regtest follow 127.0.0.1:18444 $D screen serve:18455" screen.txt
```

The Overview is the frame it opens on, and it carries both halves of #164 at
once, so no keypresses have to be fed in. Read it back by pattern rather than
by eye:

```bash
grep -ao "[0-9]* Peers ([0-9]* out, [0-9]* in)" screen.txt | sort -u
grep -ao "tx $TXID fee [0-9]* sat\(  in Mempool\)\?" screen.txt | sort | uniq -c
```

```
1 Peers (1 out, 0 in)
2 Peers (1 out, 1 in)

     44 tx b038e27f... fee 2760 sat  in Mempool
     39 tx b038e27f... fee 2760 sat
```

Two counts of one txid, one marked and one not, is the whole of the Mempool
half: the row was on the Panel before a Block held it, and the **same** row
flipped rather than a second appearing.

**Wait for the tip before sending the Transaction.** A relayed Transaction
only reaches the Mempool once the node is in its listen loop; sent during a
catch-up it arrives inside the Block instead, the row appears already
confirmed, and nothing about the Panel is wrong — the test simply asked the
question too early. `Mempool: 0 Transaction(s)` in every frame is the tell.

**Frames are chronological, so byte offsets order events.** When a run
disagrees with what a log line says happened, `grep -abo` on the capture
settles which came first:

```bash
grep -abo "mempool admitted $TXID" screen.txt | head -1   # 13220
grep -abo "$TXID fee" screen.txt | head -1                # 22454
```

That gap is what showed the Screen was in the catching-up form for the whole
time the Transaction was held — the phase bug `backAtTip` fixes.

### 15. Starting with no address at all

`follow <dir>` with no Peer named bootstraps from the Network's DNS seeds
(#166). Regtest cannot test the happy path — `seedsOf(Regtest)` is `[]` by
definition — but it tests the two things regtest *can* say:

```bash
$BIN regtest follow $D
```

```
no Peer given; asking the regtest DNS seeds
error: the regtest Network has no DNS seeds
```

That is a usage error naming the reason, not a lookup that fails. And a run
that **does** name Peers must not quietly widen to DNS — check the first line
is the Peer, not a seed lookup:

```bash
$BIN regtest follow 127.0.0.1:18444 $D
```

```
peer 0 is 127.0.0.1:18444
```

The happy path needs a Network that has seeds. On signet, into a scratch
directory that is not the one any long climb is using:

```
no Peer given; asking the signet DNS seeds
20 Candidate(s) from the DNS seeds
peer 0 is 109.160.122.56:38333, from the Address Book
headers to 2000
```

**`from the Address Book` is the line that matters.** It says the Peer came
out of the Book rather than off the command line, which is the whole of the
change: the seeds fill the Book with Candidates and the ordinary dial loop
does the rest.

**The grammar is arity plus two words, not a flag.** `follow <dir>`,
`follow <dir> screen` and `follow <dir> serve:18455` all read the first
argument as the directory, because nothing following it is anything but
`screen`, `serve` or `serve:PORT`. A directory actually named `screen` would
be read as the word — the price of a grammar with no flags in it.

**The Book is not persisted.** `domain/addressbook.av` keeps it in memory by
design, so it is empty at every start and the seeds are asked on the first
turn of every run that names no Peer. Once gossip has filled it the dial loop
takes Candidates from the Book instead, which is the rule either way: ask the
seeds when the Book cannot supply.

### 16. Bodies from several Peers at once

The body phase asks every Peer at once and takes Blocks from whoever answers
(#168). Three Cores serving the same chain is enough to see it. Give them
distinct ports so nothing collides with the pair §1 already uses:

```bash
# pa 19444/19443, pb 19454/19453, pc 19464/19463, same bitcoin.conf shape
$A generatetoaddress 2000 "$($A getnewaddress)"
$B addnode "127.0.0.1:19444" onetry; $C addnode "127.0.0.1:19444" onetry
$BIN regtest follow 127.0.0.1:19444,127.0.0.1:19454,127.0.0.1:19464 $D
```

**Read the proof off Core, not off our own log.** Turn net logging on at
runtime — no restart needed — and the counterparty records both halves:

```bash
$A logging '["net"]'          # and on the other two
grep -o "received getdata ([0-9]* invsz" pa/regtest/debug.log | sort | uniq -c
grep -c "sending block " pa/regtest/debug.log
```

```
122x1  1x16          <- batch sizes: one of sixteen, then top-ups of one
pa 138  pb 144  pc 118 blocks       <- 400 blocks, split three ways
```

`16 invsz` is the per-Peer ceiling being filled; the `1 invsz` top-ups that
follow are the walk keeping it full as each Block lands, which is what
Bitcoin Core's own downloader does. Sixteen outstanding per Peer across three
Peers is 48 Blocks in flight at all times.

The Peers Panel says the same thing from our side — every row's `received`
counter moves, where a one-Peer walk moves only one:

```
0    out  127.0.0.1:19444    70016    9019    70501
1    out  127.0.0.1:19454    70016    8594    39429
2    out  127.0.0.1:19464    70016    7130    32864
```

#### Killing a Peer mid-download

The point of the in-flight bookkeeping is that a Peer dying does not strand
the Blocks it was owing. Stop one while the walk is running:

```bash
$BIN regtest follow 127.0.0.1:19444,127.0.0.1:19454,127.0.0.1:19464 $D &
sleep 12
bitcoin-cli -datadir=$PWD/pb ... stop
```

```
peer 1 closed the connection
...
following at Height 2000: 2000 connected, 0 disconnected, set +2000 -0
```

The walk finishes. What the dead Peer was owing goes back on the wanted list
and is asked of whoever is left. **A hang here is the failure mode** — the
walk waiting forever for a Block nothing is going to send.

#### And then audit it

This is the test that matters, because it is the one that would catch a
Segment written wrong:

```bash
$BIN regtest audit $D 1 2000
```

```
blocks 2000  transactions 2000  ...  CLEAN (faults 0, script failures 0)
```

Every Block is read back by the Location the Index holds for it. **Blocks are
appended in the order they arrive and never in Height order** — that was
already true of the single-Peer walk and `infra/prune.av` says so in as many
words — so fetching from three Peers at once needs no reordering before the
write, and this audit is what proves the Locations are right anyway.

### 16b. Nothing prints into the Screen

The Screen owns the terminal in raw mode, so a `Console.print` while it is up
lands wherever the cursor happens to be — and with no carriage return after
it, the next frame line starts from that column. One escaped line turns the
whole frame into a staircase, and it looks like a redraw bug rather than a
print.

It only shows on a node with something to say, which is why it survived: on
regtest with one Peer and an empty Mempool, nothing ever escapes. Give it
something:

```bash
script -qf -c "timeout 80 $BIN regtest follow 127.0.0.1:19444 $D screen" screen.txt &
sleep 45
$A sendtoaddress "$($A getnewaddress)" 0.11      # a Mempool arrival to announce
```

Then grep the capture for lines that should never be in it:

```bash
for p in "mempool admitted" "mempool refused" "dropping peer" \
         "closed the connection" "did not answer" "compact Block"; do
  printf "%-24s %s\n" "$p" "$(grep -aoc "$p" screen.txt)"
done
```

**Every count must be 0**, and `grep -aoc "o overview" screen.txt` must not
be, or the Screen has stopped drawing rather than stopped leaking.

**Check the plain run in the same pass.** The guards are `Option.None` on the
View, so a bug that silences a Screen run silences the log run too, and that
is the worse failure — a node that says nothing for hours.

```bash
$BIN regtest follow 127.0.0.1:19444 $D > plain.log 2>&1 &
grep -c "mempool admitted" plain.log     # must be non-zero
```

`Infra.Peers` prints too and knows nothing about Screens. It carries a `plain`
flag on the Pool instead, set false by `drawnElsewhere` when the Screen takes
the terminal — so the module's question is "may I speak", not "is there a
Screen", which is the only thing it can sensibly know.

### 16c. The Screen keeps its clock while the loop is busy

The frame, the keys, the metrics record and an inbound caller all used to
happen on one arm of one match: the arm `awaitTick` takes when **no Message
arrived**. A loop that never goes quiet therefore never drew, never answered a
key and never wrote a record — and a node short of Peers is exactly that loop,
because it dials a Candidate before every tick and the dial spends five
seconds reading every Peer it has (#201, #202).

Regtest hides this by default, and it hid it through two whole runs of this
section before the Book was looked at. **One Peer and an empty Address Book
means nothing is ever dialled**, so the loop goes quiet every tick and the
Screen draws once a second whatever the code does. You have to give it a Book
full of addresses that will not answer.

`addpeeraddress` is the handle, but Core will not store a documentation range
— `198.51.100.0/24`, `192.0.2.0/24` and `203.0.113.0/24` are all RFC5737 and
`IsRoutable()` refuses them, silently, with `{"success": false}`. **`240.0.0.0/4`
is the range that works**: reserved and unallocated, so Core gossips it and a
SYN to it blackholes rather than being refused. `192.88.99.0/24` looks
promising and is not — it is refused in microseconds, which is the case that
does *not* stall a dial.

```bash
for x in $(seq 0 15); do for y in $(seq 0 15); do for z in $(seq 1 8); do
  $C addpeeraddress 240.$x.$y.$z 18444 >/dev/null
done; done; done
$C getnodeaddresses 0 | grep -c '"address"'          # ~900 of 2048 survive bucketing
```

**Then restart Core.** `getaddr` replies are cached per network for a day, so
a node that asked while the addrman held forty addresses gets forty back for
the rest of the day however many you add afterwards. Restarting rebuilds the
cache; the reply is ~23% of the addrman, so ~900 stored gives a Book of ~208.

Now run it with a Peer to talk to and something to say, and count the frames
in the capture. `\033[2J` is drawn once and never again — the frame moves the
cursor rather than clearing — so count the key line instead, and count
occurrences rather than lines: the whole capture is one line with no newline
in it, and `grep -c` will answer 1 for a frame drawn a hundred times.

```bash
script -qf -c "timeout 150 $BIN regtest follow 127.0.0.1:18444 $D screen log" screen.txt &
for i in $(seq 1 50); do $C sendtoaddress "$($C getnewaddress)" 0.01 >/dev/null; sleep 3; done
```

```bash
strings screen.txt | grep -c "o overview  p peers"     # frames drawn
grep -c listen $D/metrics.log                          # records written
ss -tn | grep -c 'SYN-SENT.*:18444'                    # sample this during the run
```

Measured across a 150-second run with a dial to a dead Candidate in flight on
every one of thirty samples:

| | before #201 | after #201 | after #202 |
|---|---|---|---|
| frames drawn in 150s | **3** | **31** | **143** |
| `listen` records | **0** | **2** | **2** |
| `q` answered | no — ran to the timeout | yes, within 10s | yes, within 5s |

Thirty-one and not a hundred and fifty after #201 because `minding` is looked
at once per **turn**, and while the Book had dead Candidates in it a turn was
five seconds long. That was #202, and the third column is it fixed.

**Read the `polledMs` column while you are here.** The dial's poll is counted
now (`Infra.Peers.settlingOn`), and it is the one poll in the module that used
not to be. `workedMs` is the window less the poll, so an uncounted wait was
reported as work: the same minute reads `polledMs 30835 workedMs 30015` before
and `polledMs 60000 workedMs 7` after. The second is the truth — the node was
asleep on a socket for the whole minute — and it is what makes a starved loop
legible from `metrics.log` without going near `/proc`.

**Check `q` with the traffic running**, and mind two traps that between them
will tell you the opposite of the truth.

```bash
( sleep 20; printf 'q'; sleep 3; printf 'y'; sleep 600 ) \
  | script -qf -c "timeout 120 $BIN regtest follow 127.0.0.1:18444 $D screen" q.txt &
```

- **Hold stdin open far longer than the run.** When the feeding subshell ends
  the pipe closes, `script` goes, and the node goes with it — which looks
  exactly like a `q` that worked, at exactly the moment the sleeps add up to.
  A first pass at this section read a quit at 47 seconds that was `25 + 2 +
  20`. Give the last sleep ten minutes and let the `timeout` be the only other
  way out.
- **Send the Transactions.** With nothing arriving the pool goes quiet every
  tick and `q` is answered on every build including the broken one — 30
  seconds for a node before #201 as readily as after it. The starvation is the
  point, so it has to be present for the test to mean anything.

With both: before #201 the node runs to the `timeout` and never answers; after
it, it stops about ten seconds after the key; after #202, about five. Read the
elapsed time, not the last line — the SIGTERM path prints the same
`stopped following`.

**And check the plain run in the same pass**, for the reason 16b gives. It
still says `candidate ... did not answer` once per dial. Do not expect a
`Peers (...)` line in a short run: that is on a five-minute clock, not a
one-minute one.

One number worth keeping from this, because it is about the node and not the
Screen: over the same 150 seconds and the same Transactions sent, a node with
**8** Candidates admitted **46** of them and a node with **208** admitted
**none**. A loop that spends five of every six seconds dialling is not a loop
that is doing its job. That is the whole of #202 in one line.

### 16d. A dial the loop does not stand in

Same setup as 16c — Core restarted, its addrman seeded with `240.0.0.0/4`, our
Book filled with ~208 addresses that will never answer. 16c measured what the
Screen did while that was going on. This measures what the **node** did, which
is the part that matters.

Before #202, `toppedUp` called `Infra.Peers.joined`, which starts a dial and
then stands in a poll until it settles: five seconds for an address out of an
Address Book, against a tick of one. Every Peer was read throughout — that is
what the non-blocking dial bought — but nothing they said was acted on, so the
loop got through one Message every five seconds.

The test is throughput, not appearance. Mine a Block first, so the sends spend
confirmed Outputs rather than chaining onto Core's own unconfirmed Mempool:

```bash
$C generatetoaddress 1 "$($C getnewaddress)" >/dev/null
for i in $(seq 1 23); do $C sendtoaddress "$($C getnewaddress)" 0.01 >/dev/null; sleep 3; done &
timeout 70 $BIN regtest follow 127.0.0.1:18444 $D > plain.log 2>&1
grep -c 'mempool admitted' plain.log       # how many of the 23 it got to
grep -c 'did not answer' plain.log         # how many Candidates it tried
```

**Mine the Block.** Skip it and the node correctly refuses every Transaction
with `it spends an Output we cannot account for` — Core's Mempool chains each
new send onto the change of the last one, and our Set holds only what is
confirmed. That reads as a node that is broken and is a node that is right.

Twenty-three Transactions sent over seventy seconds, two independent pairs:

| | before #202 | after |
|---|---|---|
| Transactions judged | **5**, then **8** | **21**, then **22** |
| Candidates dialled | 14, then 14 | 12, then 13 |

The second row is the one that makes the first mean something: the dial rate
did not change. The node is not getting through more Transactions because it
gave up on filling its pool — it is dialling just as often and no longer
waiting for it.

Two more things the same change should have moved, both visible in
`metrics.log`:

```
1787783000613 headers 201      0   0     0    37   30 1   0 0 0
1787783005614 bodies    1    200   0     0  5000    1 1 208 0 0      <- before
1787783005648 bodies  200    200 200 49857     1   33 1 208 0 0
1787783011623 set     200    200   0     0  4999  976 1 208 0 0
```

```
1787787728138 headers 205      0   0      0    41   35 1   0 0 0
1787787728140 bodies    1    204   0      0     0    2 1 208 0 0     <- after
1787787728169 bodies  204    204 204 184639     0   29 1 208 0 0
1787787729340 set     204    204   0      0     0 1171 1 208 0 0
```

The catch-up tops the pool up between phases, and those two calls were five
seconds each — the `5000` and `4999` in the first block, present in every
metrics log this project has recorded and easy to read as the phase's own
cost. They are 0 and 0 after. A whole catch-up at the tip went from about
eleven seconds to about one and a half.

**What regtest still cannot show.** Every Candidate in the Book is a blackhole
by construction, so the path where a parked dial *succeeds* —
`Infra.Peers.advanced` getting `Ok(Some(connection))` back and greeting the
Peer — is never taken here. It shares `seatedOut` with the startup walk, which
section 13 does exercise, but the fold back into the pool is its own code.
Core will not gossip a loopback or an RFC1918 address, so there is no way to
put a reachable Candidate in the Book on regtest without root. It needs a
Network with seeds; section 15 says the same thing about its own happy path.

### 16e. Giving an address back

Until #210 this node took addresses and never gave one: it sent `getaddr`,
folded the answers into the Book, and had no code anywhere that *wrote* an
`addr`. So no Peer could learn where it was, and any Peer that did find it
learned nothing from it.

Two halves, and regtest can show one of them properly and the other only by
its refusal.

**Answering `getaddr`.** Bind a port and have Core dial in. Core will not
gossip a loopback address, so it cannot find us on its own — `addnode` is the
way to put the two together:

```bash
timeout 75 $BIN regtest follow 127.0.0.1:18444 $D serve:18455 > serve.log 2>&1 &
sleep 25
ss -ltn | grep 18455                       # we are bound
$C addnode "127.0.0.1:18455" onetry        # Core dials us
sleep 30
grep -E "dialled us|offered" serve.log
```

```
peer 7 dialled us from 127.0.0.1:34034
offered 208 Candidate(s) to peer 7
```

Both lines matter. The first is the accept path, which section 11 already
covers. The second is the new one, and before #210 it did not exist —
`getaddr` fell into the arm of `handle` that ignores everything.

`$C getpeerinfo` should show the pair from Core's side: one `"inbound": true`
entry that is our dial out to it, and one `"inbound": false` on port 18455
that is its dial in to us, both with `"subver": "/aver-btc-listener:0.1/"`.

**The listener is bound before the Handshakes.** The first line of the run
must be the listener, not the Peer:

```
listening on port 18455 as NODE_NETWORK|NODE_WITNESS at Height 0, for up to 8 inbound Peer(s)
peer 0 is 127.0.0.1:18444
```

The other order is #214: `noticedSelf` will not count an `addr_recv` while the
port is zero, and the port was bound after the startup Handshakes -- so every
Peer named on the command line contributed nothing to the tally, and only
Peers seated later out of the Book did. A node whose Book dials all fail would
sit with four good Peers and never learn its own address.

**Self-advertisement, which regtest can only show refusing.** The node learns
its own address from `addr_recv` in a Peer's `version` — the only way a node
behind NAT or on a hosting box can know it — and everything on regtest is on
loopback. `Domain.Address.routable` refuses `127.0.0.0/8` along with RFC1918,
RFC3927, RFC5737, RFC6598 and RFC2544, so the observation is never counted and
nothing is ever advertised.

That refusal is the correct behaviour and is worth checking rather than
assuming:

```bash
$C getnodeaddresses 0 | grep -c '"address"'    # same before and after the run
```

It must not move, and `grep "told .* Peer" ` must find nothing. A node that
advertised `127.0.0.1` would be handing the Network an address that reaches
nobody, and on a real host the same bug hands out the operator's private
network.

The positive is the same line seen: `told 8 Peer(s) this node is at
62.210.113.61:38333` in a plain run, and `advertising 62.210.113.61:38333` on
the end of the Peers Panel line under a Screen. Both exist because neither
alone was enough (#214) -- the plain line is invisible on the node that most
wanted it, which runs with a Screen.

**The positive path needs a routable address, so it needs signet.** Two Peers
have to agree before the address is believed (`agreementNeeded`), the port
advertised is the bound one rather than the socket's, and the first
advertisement goes out as soon as those agree. None of that can happen on a
machine talking to itself. Section 15 records the same shape of gap.

### 16f. A walk that stops says where it stopped, and stops when asked

**Ctrl-C must end the Set phase.** `Process.stopRequested` is cooperative: the
signal sets a flag and each walk is expected to notice. The Set walk asked
only `eye.stopped` -- which is `q` on the Screen -- so a plain-log run had
nothing to stop it (#206), and on mainnet that phase runs for hours.

Test it without waiting for a `follow` to reach the phase: the `utxo` command
*is* the Set walk, standalone.

```bash
$BIN signet utxo $D 12000 &
sleep 8 && kill -TERM $(pgrep -f "signet utxo")
```

```
188 Blocks connected to Height 5020; Set +188 -0 Outputs, 0 satoshis in fees
```

It must stop within a turn -- about two seconds -- and **report the Height it
reached, not the one it was asked for**. A run that keeps going needs
`kill -9`, which is the ending that tears a Segment and the one a cooperative
stop exists to avoid.

**A phase-boundary record must not claim the target.** Walk rows report the
low-water mark; the boundary row used to report `tipHeight` whatever happened
(#205), so a stopped phase looked complete.

```bash
grep -v '^#' $D/metrics.log | awk '{printf "%-8s height %-9s blocks %s\n", $2, $3, $5}'
```

**Sum the blocks column and compare it against the final row's height.** If
the blocks fetched across every window come to far less, the boundary row is
reporting the target. That is how this was found: a run whose blocks summed to
112,113 signed off at 319,491.

### 16g. The walk takes a turn at dialling

A body phase holds the pool for hours and, before #199, did nothing with it
but fetch. Every dial the node makes is in the follow loop, and the loop is
not running: the pool could only shrink for the whole of a catch-up. Mainnet
`metrics.log` shows it going 4, 4, 3, 3 against a target of 8.

Sixteen Blocks in flight per Peer (#168) is what makes that expensive. A Peer
lost mid-walk is not a share of a serial download any more, it is a sixteenth
of the parallelism, and the walk hands what it was owing to whoever is left.

**The body phase has to be long enough to dial in.** A dial costs its five
seconds whether or not anyone waits for it, so a phase of three seconds
proves nothing either way. Ten thousand regtest Blocks is a body phase of
3.5 seconds; **fifty thousand is about seventy seconds**, which is fourteen
dials' worth. Generating them takes about half an hour at ~60 Blocks/s:

```bash
for i in $(seq 1 5); do $A generatetoaddress 10000 "$($A getnewaddress)" >/dev/null; done
```

Then the Book, exactly as 16c builds it — `240.0.0.0/4`, then **restart Core**
so the `getaddr` cache is rebuilt — and one run per binary:

```bash
timeout 300 $BIN regtest follow 127.0.0.1:19444 $D log > run.log 2>&1
grep -c 'Address Book holds' run.log      # 1 = the Book filled; 0 = the run proves nothing
grep -c 'did not answer'      run.log     # Candidates dialled
```

**Check the Book line before you read the count.** Two runs of this section
recorded `candidates 0` in `metrics.log` and no dial from either binary, and
they looked like a fix that did nothing. Core had not sent the `addr` at all,
so there was nothing in the Book to dial and the two builds were being
compared on an empty one. The `ss -tn | grep 240\.` sample that seemed to
confirm it was reading **Core's own** outbound attempts to its addrman, not
ours — same addresses, wrong process. A run whose Book never filled is a run
to throw away.

Three pairs, same chain, same seeded Book, ~49,500 Blocks:

| | before #199 | after |
|---|---|---|
| Candidates dialled during the body phase | **1**, **1**, **1** | **15**, **14**, **13** |
| body phase, `polledMs + workedMs` | 65.7s, 67.6s, 61.5s | 72.4s, 68.0s, 61.2s |

The **1** is not zero because the catch-up tops up at the headers/bodies
boundary: that dial is started and then sits parked for the whole phase,
because nothing asks it what it became. After, it settles inside the walk and
another follows it — thirteen to fifteen over seventy seconds is one per five,
which is the dial deadline and the same rate the loop uses. It is not a rush:
`Infra.Peers.dialingNow` still allows one at a time, and a turn comes round
about seven hundred times a second at this Block rate.

The second row is what makes the first safe. Two of the three pairs are
within half a percent; the first pair's ten percent is run-to-run variance and
not a cost, which is worth knowing because the poll and the top-up now happen
on **every** turn of the walk rather than on a clock.

And audit both, which is what proves the Blocks still landed where the Index
says:

```bash
$BIN regtest audit $D 1 49538
```

```
blocks 49538  transactions 49543  ...  unresolved 5  CLEAN (faults 0, script failures 0)
```

Byte for byte the same line from both builds. The five unresolved are the
`sendtoaddress` Transactions this datadir collected in earlier sections
spending parents outside the audited range — a property of the chain, not of
either binary, which is why the control run matters.

**What regtest still cannot show** is the half worth having: a Candidate the
walk dials that *answers*. Every address in the Book is a blackhole by
construction, and 16d says why there is no way around that without a Network
with real seeds. What is exercised here is that the walk starts dials, polls
them, retires them on the deadline and carries on fetching — the seating
itself is `Infra.Peers.advanced`, shared with the loop, which section 13
covers.
### 16h. A caller that hangs up, and a pool that empties

Both cloud nodes died on the same day (#217): signet from a caller on the
served port that hung up before it could be asked its address, mainnet from
a Set catch-up long enough for every Peer to give up on us and an empty pool
that stopped the node with hundreds of Candidates in the Book. Three things
to prove, in one run against a node with a few hundred Blocks more than the
directory holds -- `generatetoaddress 400` is enough:

```bash
$BIN regtest headers 127.0.0.1:18444 $D
$BIN regtest follow 127.0.0.1:18444 $D serve:18468 log &
```

**The Set is built in chunks, and the pool is read between them.** The
catch-up prints one range line per 250 Blocks rather than one for the whole
walk:

```
utxo    connecting 1..250
utxo    connecting 251..500
utxo    connecting 501..607
following at Height 607: 607 connected, 0 disconnected, set +1359 -556
```

That is what keeps Peers through a mainnet Set walk of hours: between two
chunks every Peer is read, pings answered, the dial asked and the pool topped
up. Before this the whole walk was one call with no socket in it, and the
metrics log showed it -- `polledMs 0` in every minute-long `set` window.

**A caller that hangs up costs itself and nothing else.** Empty the pool
first, so nobody else is talking (`disconnectnode` for each of Core's peers),
then hang up on the served port five times:

```bash
for i in 1 2 3 4 5; do nc -z 127.0.0.1 18468; sleep 1; done
```

Each one is dropped on the spot:

```
peer 6 dialled us from 127.0.0.1:45364
peer 6 closed the connection
dropping inbound peer 6 from 127.0.0.1:45364: peer 6 closed the connection before answering
```

and the node is still running. Before #217 the answering-side Handshake went
on waiting for a Peer the pump had already forgotten -- sixty seconds per
caller with somebody else talking, and with nobody else talking the pool's
150-second idle deadline, whose `Err` ended the run:
`error: no Peer said anything for 150 seconds`, exit 1. A caller that resets
before `getpeername` is answered (`os error 107`) is the same fix one call
earlier; `nc` closes cleanly and does not force that race, so the strace
line to look for on a hang-up is `recvfrom(N, "", ...) = 0` followed by the
drop, not by silence.

**An empty pool with a Book is a node between Peers.** With Core
disconnected the pool is empty and the Book holds Core's `addr` gossip
(unroutable `240.0.0.0/8` addresses on regtest, each costing its five-second
dial). The node keeps dialling rather than stopping. Now have Core dial in
and mine:

```bash
$C addnode 127.0.0.1:18468 onetry
$C generatetoaddress 1 $($C getnewaddress)
```

The first Peer seated after none is asked to bring the chain up to date,
and the new Block follows:

```
peer 11 dialled us from 127.0.0.1:51816
headers complete: 611 known
block 610 2282059a... 1 tx 250 bytes
following at Height 610: 1 connected, 0 disconnected, set +1 -0
```

What would be a fail: a `dropping inbound peer` line arriving a minute after
its `closed the connection`; `error: no Peer could bring the chain up to
date` or `every Peer is gone and the Address Book has nothing left to dial`
while the Book is not empty; or the node sitting at the old Height after
Core has dialled in and mined. Only a pool that is empty with nobody left
to dial ends the run now, and the message names both facts.

Left for its own issue: a seated Peer that closes on us is forgotten
without `Tcp.close`, so `ss -tn` shows its socket in `CLOSE-WAIT` for the
life of the process.

### 16i. A Block that cannot connect costs no Peer

Until #183 every failure of a catch-up was charged to the Peer being asked.
That is right for a Peer fault — a bad checksum, the wrong Network, a Block
that is not the one asked for, no answer before the deadline — and wrong for
the three things `connected` carries underneath it: a consensus refusal from
`Infra.Utxo.written`, the D8 stop when a reorganisation reaches below the Undo
window, and a Store or Disk error under either. All three came back as the
same `Result.Err`, so the node dropped a Peer, retried on the next one, failed
at exactly the same Height, and terminated having lost every Peer while
reporting the consensus reason as though a Peer had said it.

**Force a chain-side refusal without touching the Peer.** Sync until bodies
are on disk but the Set is still behind, then take a body out from under it:

```bash
timeout 75 $BIN regtest follow 127.0.0.1:19444 $D log      # Set reaches ~9,367 of 49,538
truncate -s $(( $(stat -c%s $D/blocks/blk000000.dat) * 60 / 100 )) $D/blocks/blk000000.dat
rm -f $D/.writing
timeout 100 $BIN regtest follow 127.0.0.1:19444 $D log
```

**Truncate rather than delete.** Segments are append-only in *arrival* order
and never in Height order (§16), so cutting the file removes an arbitrary set
of Heights above the Set's standing — which is the point: the Set walks into
one of them. Deleting the whole segment fails much earlier and tests the open
path instead.

Same directory, same Peer, two binaries:

| | Peers dropped | last line |
|---|---|---|
| before #183 | **1** | `no Peer could bring the chain up to date and the Address Book has nobody left to dial; the last said: Segment 0 holds 0 of the 250 bytes at offset 7560983` |
| after | **0** | `the chain cannot be followed past here, and no Peer is at fault: Segment 0 holds 0 of the 250 bytes at offset 7560983` |

The reason is identical and it is the wording either side of it that changed:
the node now stops where it stands with its Peers still connected, and the
last line says which of the two kinds of stop it was.

**And check the other half in the same pass** — a genuine Peer fault must
still cost that Peer and no more, which is what #168's own section demands:

```bash
$BIN regtest follow 127.0.0.1:19444,127.0.0.1:19454 $D log &
sleep 40
bitcoin-cli -datadir=$PWD/pb ... stop
```

```
peer 1 closed the connection
...
stopped following
```

One Peer gone, the walk finished on the other. A run that reported
`the chain cannot be followed past here` for a Peer that merely hung up would
be this change overshooting, and it is the case worth watching for.

### 16j. Selection a hostile Peer cannot steer

`nextUntried` walked the Book in whatever order it happened to hold and took
the first Candidate not yet tried. That made the gossip useful and it made a
Peer answering our `getaddr` with a thousand addresses it controls the Peer
that decides who we dial next, which is the shape of an eclipse (#118).

Since the fix a **source** is chosen uniformly among those holding anything
worth dialling, then an **address** uniformly within it. `heardFrom` is the
source and was already recorded against every Candidate.

**Regtest shows the randomness, not the defence.** Every Candidate in a
regtest Book comes from the one Core we are talking to, so there is a single
source and the multi-source property cannot appear. What is visible is the
second half of the choice, and it is visible plainly — build the Book the way
16c does (`240.0.0.0/4` into the addrman, restart Core) and read the order the
Candidates are dialled in:

```bash
timeout 110 $BIN regtest follow 127.0.0.1:19444 $D log > run.log 2>&1
grep -o "candidate 240\.[0-9.]*" run.log | head -6
```

```
before   240.0.1.2  240.0.1.8  240.0.10.2  240.0.13.2  240.0.15.6  240.0.5.5
after a  240.8.1.2  240.2.2.7  240.11.6.2  240.10.0.6  240.15.15.3 240.0.1.2
after b  240.6.3.1  240.2.4.6  240.14.3.2  240.2.7.4   240.9.11.8  240.4.5.3
```

The first row is the Book's own order, ascending and identical on every run.
The second and third are two runs of the same build: scattered, and different
from each other. Twenty dials in each of the three, so the rate is unchanged —
which matters, because #222 is about that rate and this must not undo it.

**The multi-source property is carried by the verify cases**, in
`domain/addressbook.av`, because regtest cannot produce a second gossip source
without a second Core that our node also treats as a Peer. A Book with three
addresses from source 7 and one from source 8 gives:

```
roll 0 -> 1.1.1.1    roll 1 -> 9.9.9.9
roll 2 -> 2.2.2.2    roll 3 -> 9.9.9.9
roll 4 -> 3.3.3.3    roll 5 -> 9.9.9.9
```

Source 8 owns a quarter of the Book and wins half the rolls; source 7's three
rotate between them. A source that had named a thousand would still win every
second roll. **Those addresses were read off a probe rather than reasoned out**
— which one lands on which roll depends on the order the Book holds them, and
the share is the thing being asserted.

### 16k. The status Board answers a browser and never holds the loop

`http` (#261) puts the five Panels on a port as one page. Three things to
prove: the page shows what the Screen would, a bad request costs nothing,
and a caller that says nothing does not stop a Block from connecting.

```bash
./target/release/main regtest follow 127.0.0.1:18444 $F serve:18470 log http:18330 &
sleep 5
curl -s localhost:18330/ | grep -c '<h2>'          # 5: one heading per Panel
curl -s localhost:18330/ | grep -A3 'Overview'      # the tip line the Screen shows
curl -s -o /dev/null -w '%{http_code}\n' localhost:18330/x   # 404
printf 'junk\r\n\r\n' | nc -q1 localhost 18330 | head -1      # HTTP/1.1 400 Bad Request
for i in 1 2 3 4 5; do nc localhost 18330 < /dev/null & done  # five callers that say nothing
$C generatetoaddress 1 "$($C getnewaddress)"; sleep 3
tail -2 $F/metrics.log                              # the Block connected
curl -s localhost:18330/ | grep -c '<h2>'          # still 5
```

A Reader that hangs up costs the loop a tenth of a second once; the node's
own metrics line shows the turn went on.

The page must also answer *during* a catch-up, from inside the Set walk,
not only at chunk boundaries (a mainnet chunk is 250 Blocks, minutes at
2 Blocks/s). Mine enough Blocks that the walk takes a while, follow a fresh
directory, and ask while it is walking:

```bash
$C generatetoaddress 3000 "$($C getnewaddress)" > /dev/null
rm -rf $F2; mkdir -p $F2
./target/release/main regtest follow 127.0.0.1:18444 $F2 log http:18331 &
sleep 4; time curl -s -m 5 localhost:18331/ | grep -A3 '<h2>Overview'   # answers in about a second, mid-walk
```

### 16k. The Set runs inside the download

Until #185 a catch-up did the whole download and then the whole Set, one
after the other, over the same range. The download is mostly waiting on
Peers and the Set is entirely computing, so on one thread they fill each
other's gaps: a slice of download, then a chunk of Set over what has landed,
round again.

**The check that matters is that the Set is identical, not that it is
faster.** Same chain, same Peer, one build each:

```bash
timeout 600 $BIN_BEFORE regtest follow 127.0.0.1:19444 $D_SEQ log
timeout 600 $BIN_AFTER  regtest follow 127.0.0.1:19444 $D_PAR log
$BIN_AFTER regtest audit $D_SEQ 1 49538
$BIN_AFTER regtest audit $D_PAR 1 49538
```

```
blocks 49538  transactions 49543  spends resolved 0  coinbase 49538
unresolved 5  scripts 0 passed / 0 failed / 0 undecided  CLEAN (faults 0, script failures 0)
```

Byte for byte the same line from both, and both end with the Set standing at
49538 of 49538.

**Read the `connected` count to see that the alternation actually ran**, since
a correct result alone does not prove it did:

```
sequential   following at Height 49538: 49538 connected
parallel     following at Height 49538:  7538 connected
```

The final Set walk had 42,000 fewer Blocks to do, because they were connected
during the download. Do not look for this in `metrics.log` — a `set` record is
due once a minute and the regtest chunks are far quicker than that, so both
runs write two of them and the log shows nothing.

Catch-up wall clock, first record to the Set reaching the tip: **146.0 s
before, 108.2 s after**. That is a LAN Peer and tiny Blocks, which is the
*least* favourable case for this change — see #185 for what it is worth
against random Peers.

#### The trap this section exists for

The first parallel build came back **1,538 Blocks short of 49,538** and the
audit agreed with it, reporting a clean chain of 48,000. Nothing was corrupt:
Locations are held back `locationBatch()` = 2000 at a time, and the driver
handed its `Fetched` back without the final `settled`, so one part-full batch
of `b:` keys was never written. The bodies were on disk and the Index did not
name them.

`bodiesOn` did that settle for the sequential path and the driver has to do it
too. **A short range and a CLEAN audit together are the signature** — the audit
only reads what the Index names, so it cannot see Blocks the Index forgot.
Always compare the Block count against the chain's own height, not just the
audit's verdict.

### 16l. The watchdog lines say when the node overran itself

No knob is needed to provoke them: freeze the process with a signal. A
`SIGSTOP` inside the Set walk stops its clocks mid-chunk; on `SIGCONT` the
next Block's clock spans the pause (over thirty seconds → `slow block`) and
so does the chunk's (over five times `chunkMs()` → `slow chunk`, said from
inside the walk, then `slow chunk done` when the chunk ends). Then ask it
to stop: a stop that lands at the next Block writes no `slow stop`, which
is the point of that line — only a stop that really overran writes it.

```bash
$C generatetoaddress 3000 "$($C getnewaddress)" > /dev/null
rm -rf $F2; mkdir -p $F2
./target/release/main regtest follow 127.0.0.1:18444 $F2 log &
P=$!; until grep -q 'set connecting' $F2/debug.log 2>/dev/null; do sleep 0.05; done
kill -STOP $P; sleep 65; kill -CONT $P; sleep 5; kill -INT $P; wait $P
grep watchdog $F2/debug.log
```

The freeze has to land inside the Set walk once a Block has been recorded
-- the first `set connecting` note is that moment; a freeze during the
download spans no Block clock, because its chunks had nothing landed yet.
Expect `slow chunk`, `slow block` and `slow chunk done`, then `end stopped
cleanly` a moment after the SIGINT. A run left alone writes none.

### 17. The metrics log

A run with a Screen open leaves no record of itself: every walk asks the Eye
whether it may speak and stays silent when a View is present. `log` writes the
same numbers where they survive the run (#180).

```bash
$BIN regtest follow $PEERS $D log            # writes $D/metrics.log
$BIN regtest follow $PEERS $D screen log     # the same, with a Screen
$BIN regtest follow $PEERS $D log:/tmp/m.log # somewhere else
```

```
# atMs phase height target blocks bytes polledMs workedMs peers candidates
... headers 2001    0    0      0     40    518  3 0
... bodies     1 2000    0      0      0      0  3 0
... bodies  2000 2000 2000 499857      0    702  3 0
... set     2000 2000    0      0      0  13788  3 0
... listen  2000 2000    0      0  60014      3  3 0
```

**`polledMs` and `workedMs` are the pair worth having.** Everything else is
already on the Overview or recoverable from the store afterwards; the split
between waiting for a Peer and working on what it sent exists only while it is
happening. `Tcp.poll` is bracketed by two clock readings, so `polledMs` is
wall clock inside the poll and `workedMs` is the rest of the window.

Read the shape of a regtest run and you can see what it is telling you:

- `set 0 / 13788` — the Set phase waits for nothing. That is pure CPU, and it
  is the number a "build the Set during the download" question needs.
- `bodies 0 / 702` — barely any waiting, **because these Peers are loopback**.
  On real Peers this inverts, which is why a measurement that matters has to
  be run against seed Peers rather than a node on the same machine.
- `listen 60014 / 3` — an idle node at the tip is all poll, which is the
  sanity check that the two readings bracket the right thing.

#### What to check

```bash
grep -vc '^#' $D/metrics.log                 # records written
awk '/^#/{next} NF != 10 {bad++} END {print bad+0}' $D/metrics.log
```

- **A Screen run writes records and leaks nothing onto the frame.** Grep the
  pty capture for a record line — it must find none.
- **A plain run keeps its stdout lines as well**, so `log` adds a file rather
  than replacing what was already printed.
- **Ctrl-C leaves the log whole.** Every line is ten fields and the file ends
  with a newline, because records are appended one line at a time and a run
  that stops mid-phase simply has no record for it.

**Records come from inside the walks, not only at their edges.** Each of the
three long walks -- `Infra.Download`, `Infra.Bodies`, `Infra.ChainState` --
writes at the same point it already decides to redraw, so a phase that runs
for hours writes a line a minute throughout it. The first version of this
wrote only at phase boundaries and in the listen tick, which meant a mainnet
body phase produced **nothing at all** for hours; the tell is a log whose last
line is `bodies <from> <to>` with zeros in every count.

**Testing that on regtest needs the window shortened.** A regtest walk
finishes in under a minute, so the real 60-second gate never fires and only
the boundary records appear -- which is exactly what the broken version
produced. Drop `Infra.Metrics.windowMs` to `250` temporarily, run, and count:

```bash
awk '/^#/{next} {n[$2]++} END {for (p in n) printf "%-8s %d\n", p, n[p]}' $D/metrics.log
```

```
headers  2
bodies   3
set      27
listen   83
```

Anything other than 1 for `set` and 2 for `bodies` means the walks are
writing. Put `windowMs` back to `60000` before committing.

**`peers` and `polledMs` are 0 for the set phase.** That walk holds no pool:
`polledMs` is genuinely zero because it does no network I/O -- which is the
finding, not a gap -- but `peers` is *unknown* rather than none, so do not
read a zero there as a node with no Peers.

**A phase boundary resets the Block and byte counters.** `Infra.Bodies` counts
its own walk and the phases either side count nothing, so a record's counters
can go backwards; `sinceLast` reports the raw value when they do. Written the
obvious way it emitted `-2000` at every boundary, which reads as a fault and
would sum to nonsense under `awk`.

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

`cargo build` is not a formality: the failure classes that survive `check`,
`verify` **and** `compile` are listed in CLAUDE.md.

### Prove the binary is the one you built

**A negative result is worth nothing until the thing under test is known to be
running.** In one night this caught two false conclusions, in two different
sessions, both from the same shell mistake:

```bash
aver compile main.av --module-root . -o ../btc-listener-build | grep -c "^error" && cargo build ...
```

`grep -c` exits **1** when it matches nothing, so the `&&` skips the build
**precisely when the compile is clean**. The run prints `0`, looks healthy,
and leaves the previous binary in place. One session then spent an hour
concluding a feature "did not fire" against a binary that did not contain it;
the other blamed two vanished builds on memory pressure.

Use `;` rather than `&&` between gate steps, and check the artefact rather
than the exit status:

```bash
strings ../btc-listener-build/target/release/main | grep -c "a string you just added"
ls -la ../btc-listener-build/target/release/main      # newer than your edit?
```

A running `follow` also holds `<dir>/.writing`, so a second run refuses to
start — which looks like a broken test rather than a busy directory. Confirm
no process is going before deleting the claim.

**Then run this document.** Sections 1 to 4 take a few minutes on regtest and
are the only evidence in the project that comes from outside the project.
Sections 5 to 8 cost a few minutes more and cover the Mempool, relay to a
second node, and a compact Block captured from Core; run the ones your change
can reach. A change
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
