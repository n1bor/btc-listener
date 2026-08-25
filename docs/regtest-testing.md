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

**Use a genuinely empty node.** Not one left over from an earlier session —
`getblockcount` must say 0. A node holding a chain that forked from ours asks
a question this one cannot yet answer and gets non-connecting Headers back
forever ([#171](https://github.com/n1bor/btc-listener/issues/171)); you will
see thousands of `served 8 Header(s) to peer 1` and two processes at 100%.
An empty node's Locator is just genesis, which is on every chain, so this
test passes straight through that bug — which is exactly why it is not the
only test §12 should have.

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
