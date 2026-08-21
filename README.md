# Bitcoin Peer Listener, Chain Downloader and Auditor

Connects to a single Bitcoin node over the peer-to-peer protocol, listens for
transaction announcements, and prints each transaction's decoded structure.

It also downloads the chain from that node and checks what it holds: Blocks
against their Headers, Transactions against the Outputs they claim to spend, and
Scripts as far as they can be run without a curve to verify signatures against.

Written in [Aver](https://averlang.dev).

```
listening to peer 172.26.224.1:8333
handshake complete: protocol 70016, agent /Satoshi:27.0.0/
tx d2408438d0d7032c09aea47e1284dd5843ad769f2512757440c15e43ba696dfa
   segwit, version 2, 1 in / 2 out, 222 bytes, locktime 961919
   in  f398e4689b791f5df72d1e3c5b2a7ac4f6f15321afe1265fe817d29646cda6d6:1 seq 4294967293 witness 2
   out 0.00015448 BTC  P2WPKH bc1qrwwaqv2fvhhu67tp6pqc0uy83sn0aw3gxgmeuz
   out 7.46148541 BTC  P2WPKH bc1qywkyxsjrcuj06m47dywvlz68evfvagr3tqe0cs
```

## Commands

Twelve of them. Only the first three need a Peer; everything else reads what
those wrote and works offline.

| command | what it does | Peer |
|---|---|---|
| `[peer-address] [port]` | [listen](#running) for Transactions and print each one decoded | yes |
| `headers <peer> <dir>` | [fetch](#downloading-the-chain) every Block Header, in Height order | yes |
| `bodies <peer> <dir> <a> <b>` | [fetch](#downloading-the-chain) the Blocks for Heights a..b | yes |
| `txindex <dir> <a> <b>` | [record](#finding-a-transaction) where each Transaction in a..b sits | no |
| `outputs <dir> <a> <b>` | [record](#resolving-a-spend-in-one-lookup) the Outputs in a..b, so spends resolve | no |
| `show <dir> <height> [summary]` | [read](#looking-at-one-block) one Block back off disk and check it four ways | no |
| `tx <dir> <txid>` | [find](#finding-a-transaction) one Transaction by its Id | no |
| `spend <dir> <txid>` | [check](#checking-a-spend) what one Transaction spends against what it pays, and [run its Scripts](#running-the-scripts) | no |
| `audit <dir> <a> <b>` | [run every check above](#checking-a-range) over a whole range of Heights | no |
| `prune <dir> <height>` | [delete](#reclaiming-space) the Blocks below a Height | no |
| `migrate <dir>` | [move](#the-index-and-its-backends) the Index from the log into a LevelDB, once | no |
| `help` | print the usage | no |

The three fetching commands have to run in this order, because each needs what
the one before it wrote:

```
headers ─→ bodies ─→ txindex
```

`show` and `prune` then need `bodies`. `tx`, `spend` and the spend half of
`audit` need `txindex` as well — without it every Input reads as unresolved,
which is an answer rather than an error.

Everything but the listener takes a `<dir>`, where the Index and the Segments
live. Point them all at the same one.

All of them run either way. `aver run --providers` builds the provider host once
and caches it, so the interpreted path works; the examples below use a compiled
binary because anything that opens the Index is several times faster that way.
Plain `aver run`, with no flag, cannot start this program — see
[Providers](#providers).

## Requirements

- **Aver 0.28.1 or later.** Earlier versions cannot do this at all: byte-clean
  TCP (`Tcp.sendBytes`, `Tcp.readBytes`, `Tcp.writeBytes`) and `Crypto.sha256`
  landed in 0.28.0 "Oktet", and the `Bits` namespace the Bech32 checksum needs
  landed in 0.28.1.
- A reachable Bitcoin node. Any peer will do; one you run yourself is easier to
  debug against.

## Running

```bash
cd btc-listener
aver run main.av --module-root . --providers -- [peer-address] [port]
```

or compiled, which is what the rest of these examples use:

```bash
aver compile main.av --module-root . -o ../btc-listener-build
cd ../btc-listener-build && cargo build --release
./target/release/main [peer-address] [port]
```

With no address, the listener asks one of Bitcoin's DNS seeds for reachable
peers and connects to one of them at random. The port defaults to 8333.

```bash
./target/release/main                        # find a peer via DNS
./target/release/main 192.168.1.10           # named peer
./target/release/main 192.168.1.10 8333      # explicit port
```

```
no peer given; asking a Bitcoin DNS seed
listening to peer 172.97.131.195:8333
handshake complete: protocol 70016, agent /Satoshi:29.0.0/
```

Two things are easy to leave off:

- **`--module-root .`** — without it the `depends [...]` declarations cannot
  resolve, and every module fails to load.
- **`--`** — everything after it becomes `Args.get()`. Without it, `aver`
  consumes the address itself.

The program connects, completes the handshake, and then prints transactions
until you stop it with Ctrl-C. The listen loop's recursive call is in tail
position, so it runs indefinitely without growing the stack. On a busy mainnet
peer expect a few transactions per second.

It will also stop if a read fails. A session read has no deadline at all:
[jasisz/aver#782](https://github.com/jasisz/aver/issues/782) was answered by
removing one rather than making it configurable, on the grounds that timing out
part way through a frame leaves the stream silently desynchronised, which is
worse than waiting. So a peer that says nothing does not end the session — it
stops the session making progress, and `Tcp.poll` is the operation that would
notice. See [#55](https://github.com/n1bor/btc-listener/issues/55).

`aver compile` prints one warning per dependency module — 45 of them — each
saying that module's verify blocks are not sampled and suggesting you move them
to the entry module. Ignore the suggestion: it would mean collapsing 3,777 cases
into a `main.av` that is deliberately thin. The blocks are checked, by
`aver verify --deps`.

There is no flag to quiet it
([jasisz/aver#857](https://github.com/jasisz/aver/issues/857)), so filter that
one line off stderr and leave the rest alone:

```bash
aver compile main.av --module-root . -o ../btc-listener-build \
  2> >(grep -v "non-law verify block" >&2)
```

Real errors still come through, and the compiled binary itself is quiet: an
unparseable address still says `error: octet 999 is out of range (0-255)`.

### Signet

Everything here speaks mainnet unless told otherwise. Put `signet` in front of
any command to change that:

```bash
./target/release/main signet                                   # find a signet peer via DNS
./target/release/main signet headers 157.180.3.223 ~/signet
./target/release/main signet bodies  157.180.3.223 ~/signet 1 200
```

One word changes four facts: the magic bytes on every Message, the default
port (38333), the DNS seeds asked, and the genesis Block the Locator starts
from. The offline commands — `show`, `audit`, `tx`, `spend` and the rest —
take no `signet`: a directory answers from what it holds.

Two honesty notes. The signet challenge — the block signature signet carries
in its coinbase witness commitment — is not checked, the same way every rule
this engine does not carry is not checked: the absence of a check is never
reported as a pass. And addresses in a signet directory still render with
mainnet prefixes (`bc1…` where signet spells `tb1…`), because the offline
commands do not yet ask which Network filled the directory —
[#34](https://github.com/n1bor/btc-listener/issues/34).

### Running under WSL

If the node is on the Windows host, `127.0.0.1` inside WSL will not reach it —
WSL2 has its own network namespace. Use the gateway address:

```bash
./target/release/main $(ip route show default | awk '{print $3}')
```

That address is reassigned when Windows reboots, so resolve it rather than
writing it down.

## Downloading the chain

`headers` and `bodies` fetch the chain rather than listen to it, and they are the
last two commands that need a Peer. **Compile first** — everything from here on
is shown running from a compiled binary, and this is the build step that makes
one. They will run interpreted — [jasisz/aver#900](https://github.com/jasisz/aver/issues/900)
closed on 17 August 2026, and the Index that could not be opened under `aver run`
at 40,000 entries opens at 1,454,101 in 14 seconds — but compiled is four times
faster on the same log and wants a third of the memory, and a download measured
in hours is not the place to give that up. See
[ADR 0003](docs/adr/0003-compile-rather-than-interpret.md).

```bash
aver compile main.av --module-root . -o ../btc-listener-build
cd ../btc-listener-build && cargo build --release
```

Then, from that directory:

```bash
./target/release/main headers 192.168.1.10 ~/chain        # every Block Header
./target/release/main bodies  192.168.1.10 ~/chain 1 500  # Blocks for Heights 1..500
```

`headers` must run first, and it runs in Height order. A `getdata` names Block
Ids and never Heights, so the chain has to be known before any Block can be
asked for. It fetches every Header — around 962,000 at the time of writing,
roughly 24 minutes — recording a Height against a Block Id for each. Asking for
bodies above the Height it has reached gives `no header for height N`.

`bodies` then fetches the Blocks for a range, writing them into Segments and
recording where each went.

```
block 1  215 bytes  segment 0
block 2  215 bytes  segment 0
2 Blocks stored, last Segment 0
```

Both resume, and both are safe to interrupt. Stopping costs at most the batch or
the Block in flight. A Block already held is skipped, so re-running a range
stores nothing and widening one fetches only the difference:

```bash
./target/release/main bodies 192.168.1.10 ~/chain 1 20   # stores 20
./target/release/main bodies 192.168.1.10 ~/chain 1 20   # stores 0
./target/release/main bodies 192.168.1.10 ~/chain 1 30   # stores 10
```

Heights 1–20 are 215-byte Blocks from 2009. Modern Blocks are 1–2 MB, and
reaching them means letting `headers` finish first.

## Looking at one Block

`show` reads a Block back off disk, checks it, and prints its Transactions the
same way the listener prints loose ones. It needs no Peer.

```bash
./target/release/main show ~/chain 170
height 170
block  00000000d1145790a8694403d4063f323d499e655c83426834d4ce2f8dd4a2ee
body   segment 0 line 5, 490 bytes
check  header hashes to the recorded Block Id
merkle Transactions build the Root in the Header
parent follows the Block below it in the Index
work   Block Id is under the target in the Header
txs    2
tx b1fea52486ce0c62bb442b530a3f0132b826c74e473d1f2c220bfa78111c5082
   legacy, version 1, 1 in / 1 out, 134 bytes, locktime 0
   in  0000000000000000000000000000000000000000000000000000000000000000:4294967295 seq 4294967295 witness 0
   out 50.00000000 BTC  nonstandard 4104d46c4968bde02899d2aa0963367c7a6…
tx f4184fc596403b9d638783cf57adfe4c75c605f6356fbc91338530e9831e9e16
   legacy, version 1, 1 in / 2 out, 275 bytes, locktime 0
   in  0437cd7f8525ceed2324359c2d0ba26006d92d856a9c20fa0241106ee5a597c9:0 seq 4294967295 witness 0
   out 10.00000000 BTC  nonstandard 4104ae1a62fe09c5f51b13905f07f06b99a…
   out 40.00000000 BTC  nonstandard 410411db93e1dcdb8a016b49840f8c53bc1…
```

That is Block 170: the first payment anyone made in Bitcoin, ten coins from
Satoshi to Hal Finney with forty back as change. Those outputs pay a public key
directly rather than a hash of one, which no longer has a standard address form,
so they are shown as their raw script.

A Block near the tip carries a few thousand Transactions and each takes several
lines. `summary` stops before them:

```bash
./target/release/main show ~/chain 800000 summary
height 800000
block  00000000000000000002a7c4c1e48d76c5a37902165a270156b7a8d72728a054
body   segment 0 line 7, 1634536 bytes
check  header hashes to the recorded Block Id
merkle Transactions build the Root in the Header
parent follows the Block below it in the Index
work   Block Id is under the target in the Header
txs    3721
```

`full` is the default and says so explicitly; anything else prints the usage.

`summary` saves the printing, not the work: the count means every Transaction
has already been decoded. That Block takes about three and a half seconds.

Four things are checked, and each says which one failed:

| line | what it proves |
|---|---|
| `check` | the bytes on disk hash to the Block Id the Index recorded |
| `merkle` | those bytes hold the Transactions the Header committed to |
| `parent` | this Header names the Block the Index holds one Height below |
| `work` | the Block Id falls under the target the Header claims |

`check` is the round trip: Header decoded from the wire, bytes written to a
Segment, Location recorded against the Block Id, and hashing what comes back has
to give the Block Id that was asked for. `merkle` is the one that catches a
damaged Transaction, which `check` cannot see because it only hashes the Header:

```
check  header hashes to the recorded Block Id
merkle MISMATCH: Transactions build 4d1b53876296a682305dcfedee667b567b2bffb…
```

These need no Peer and no signatures. What they cannot check is whether the
Transactions were allowed to spend what they spent — that needs the outputs they
are spending, which means a UTXO set.

A body that is absent is reported as one of two different things, because they
are two different situations:

```
body   not fetched                                    # never asked for
body   discarded by pruning, Prune Watermark 100      # deliberately deleted
```

## Finding a Transaction

`txindex` records where each Transaction sits, and `tx` looks one up. Neither
needs a Peer, and `bodies` must have run over the range first.

```bash
./target/release/main txindex ~/chain 1 200
7 Transactions recorded from 6 Blocks

./target/release/main tx ~/chain f4184fc596403b9d638783cf57adfe4c75c605f6356fbc91338530e9831e9e16
txid   f4184fc596403b9d638783cf57adfe4c75c605f6356fbc91338530e9831e9e16
block  00000000d1145790a8694403d4063f323d499e655c83426834d4ce2f8dd4a2ee position 1
tx f4184fc596403b9d638783cf57adfe4c75c605f6356fbc91338530e9831e9e16
   legacy, version 1, 1 in / 2 out, 275 bytes, locktime 0
   in  0437cd7f8525ceed2324359c2d0ba26006d92d856a9c20fa0241106ee5a597c9:0 seq 4294967295 witness 0
   out 10.00000000 BTC  nonstandard 4104ae1a62fe09c5f51b13905f07f06b99a…
   out 40.00000000 BTC  nonstandard 410411db93e1dcdb8a016b49840f8c53bc1…
```

The entry names a **Block**, not a Height, so a reorganisation that moves a
Height leaves it true.

**Index a range, not the chain.** Bitcoin holds well over a billion
Transactions. On the file backends the Store keeps every entry in memory, so a
whole-chain Transaction index is exactly what `infra/store.av` says it cannot
back. On the database backend the ceiling is the disk instead, and the keyspace
was always the one a database would be given.

## Resolving a spend in one lookup

An Input names its parent by Transaction Id and position. Answering that used
to mean a `t:` lookup for the Transaction, a `b:` lookup for its Block, and the
whole Block read and decoded to take one Output out of it — so it only worked
where someone had run `txindex`, and the parents of a modern Transaction are
scattered over the entire chain. Over Blocks 170000–172000:

```
spends resolved 13  unresolved 81888
```

`outputs` writes the Output down under the name the Input calls it by,
`o:<txid>:<index>`, so following an Input back is one lookup. It is one entry
per Output rather than one per Transaction, which is why it waits for a Store
that keeps its keys on disk.

```bash
./target/release/main outputs ~/chain 1 172000
6196698 Outputs recorded from 172000 Blocks
```

11 minutes, 149 MB peak, and the database grew from 121 MB to 773 MB. Over the
same 105 Blocks, with and without it:

| | resolved | unresolved | scripts run |
|---|---|---|---|
| without | 2839 | 1970 | 4627 |
| with | 4809 | **0** | 11201 |

Two and a half times as many Scripts actually run, and nothing is left
unresolved. That is the point of the whole LevelDB move.

It also made a defect visible that had been there all along: about 1% of those
Scripts **failed**, on real mainnet spends that must be valid. The engine could
not have been noticeably wrong before, because it was only ever asked 115
questions.

That turned out to be the signature parser, not the keyspace
([#18](https://github.com/n1bor/btc-listener/issues/18)). Before BIP66, Bitcoin
accepted DER encodings a strict parser will not, and the strict parser here did
not *refuse* them — it reported success and silently returned a zeroed `s`,
which never verifies. Over the four 51-Block slices that showed it, **210
failures became 1**:

| Blocks | strict DER | lax DER | rules by Height |
|---|---|---|---|
| 170000–170050 | 56 failed | 0 | 0 |
| 170050–170100 | 41 failed | 1 | 0 |
| 170100–170150 | 47 failed | 0 | 0 |
| 170150–170200 | 66 failed | 0 | 0 |

The one that survived the parser fix was a different bug, and the more
interesting one: a P2SH-shaped Output in Block 170060, 3,745 Blocks before BIP16
came into force. See below.

## Which rules were in force

A soft fork is not a correction to Bitcoin. It changes the rules from one Block
onwards, and the Blocks below it are still valid under the rules they were mined
under. An engine that applies today's rules to the whole chain rejects spends
the network accepted — not a stricter check, a wrong one.

`domain/rules.av` answers one question: which rules was a Block at this Height
mined under. `Infra.Audit` asks it once per Height and carries the answer down
to the Script engine, so `check` decides by the rules of the day rather than by
today's.

```aver
check(input, output, Domain.Rules.at(170060), context) => Decided(Passed)
check(input, output, Domain.Rules.at(173805), context) => Decided(Failed(…))
```

Both of those are real: the same spend, from Block 170060, on either side of the
BIP16 activation Height. Below it, `a914<hash>87` is an ordinary Script — hash
the top item, compare, leave true — and the redeem Script is data that never
runs. That is exactly the hole BIP16 closed, and those coins really were
spendable by anyone who knew the preimage.

Only P2SH is carried today, because it is the only rule this engine both
implements and can check against Blocks it holds. `Domain.Ecdsa.isValidEncoding`
is already written for BIP66 at Height 363,725, but the chain here stops below
that, so wiring it in would ship consensus code no measurement could reach.

## Checking a spend

`spend` follows each Input back to the Output it spends and reports whether the
Transaction adds up. No Peer, and `txindex` must cover the Blocks the parents
are in.

```bash
./target/release/main spend ~/chain f4184fc596403b9d638783cf57adfe4c75c605f6356fbc91338530e9831e9e16
txid   f4184fc596403b9d638783cf57adfe4c75c605f6356fbc91338530e9831e9e16
in     50.00000000 BTC over 1 inputs
out    50.00000000 BTC over 2 outputs
spend  inputs cover outputs, fee 0.00000000 BTC
script 0 passed, 0 failed, 1 undecided
```

There are **three** answers, not two, and the difference matters more than the
check does:

```
spend  inputs cover outputs, fee 0.00000000 BTC       # it adds up
spend  INVALID: outputs exceed inputs by 0.5 BTC      # value was created
spend  cannot tell: transaction 0437cd7f… is not indexed
```

Over a chain held in part most lookups fail for want of coverage. Reporting
that as invalid would make the whole check worthless, so a gap in what we hold
and a fault in the Transaction are never the same answer. A coinbase is
answered separately again, because its Input spends nothing.

**This is not full validation.** It checks that the Outputs being spent exist,
that value is not created, and that the Scripts run — but the Scripts mostly do
not finish, for the reason below.

## Running the Scripts

`spend` and `audit` both run the Script pair behind each Input: the Input's
Script, then the Output's Script on the stack the first one left. That is one
answer per Input, and again there are three of them rather than two:

```
script 0 passed, 0 failed, 1 undecided
```

**undecided** used to be by far the most common answer. It is now rare on the
scripts this project can reach: over Blocks 1–4000, **247 passed, 0 failed, 0
undecided**, where before the curve arrived it was 0 / 0 / 247.

What remains undecided is `OP_SHA1`, for want of SHA-1, and every witness or
Taproot Script, which is refused before it runs rather than after. On a modern
Block that is still most Inputs.

Counting it apart from **failed** is the whole discipline. An engine that called
"cannot tell" invalid would be worthless over a chain held in part — and one that
called it valid would be worse. That second one is not hypothetical: running
three Blocks from 2023 produced **6,363 passes from an engine that has never
verified a signature**, and every one was wrong. Segwit was deployed as a soft
fork, which *required* a witness program to look valid to nodes that could not
read it — a version byte and a push of a hash leaves the hash on the stack and
comes out true. Witness programs are refused before they are run now, and those
Blocks read `0 passed / 0 failed / 6493 undecided`.

Bitcoin Core's own test data is the adversarial test, and nothing written here
would be half as unkind. Four files are read, 1,946 cases between them, and
every case carries the verification flags Core ran it under:

| corpus | cases | agree | disagree | undecided |
|---|---|---|---|---|
| `script_tests.json` Script pairs | 1118 | 1048 | 0 | 70 |
| `script_tests.json` Witness rows | 108 | 108 | 0 | 0 |
| `tx_valid` + `tx_invalid` | 213 | 213 | 0 | 0 |
| `sighash.json` | 500 | 500 | 0 | 0 |

**Nothing disagrees, in either direction.** The seventy undecided are Script
pairs whose answer needs a Transaction the row does not carry, which is the
honest answer rather than a disagreement — and the direction that would matter,
refusing what Core accepts, is nought and has always been nought. See
[ADR 0005](docs/adr/0005-a-script-engine-with-the-signatures-left-out.md) and
[docs/core-corpora.md](docs/core-corpora.md).

## Checking a range

`audit` runs every check over a range of Heights: each Block against its Header,
its parent and its target, each Transaction against what its Inputs spend, and
each Input's Script pair as far as it runs.

```bash
./target/release/main audit ~/chain 1 20000
  ... height 18001: CLEAN (faults 0, script failures 0), 18000 blocks, 18129 transactions, 129 spends, 1008 undecided scripts
blocks 20000  transactions 20136  spends resolved 136  coinbase 20000  unresolved 0  scripts 0 passed / 0 failed / 1157 undecided  CLEAN (faults 0, script failures 0)
```

**CLEAN** is the word to read first, and it is `FAILED` if *either* a fault or
a failed Script was found. It used to be `FAULTS 0`, which was last on the line
and the only capitals and answered a narrower question than it looked like: a
failed Script is not a fault, so a run could report a hundred of them and still
end `FAULTS 0`. It did, once, and was read as good news (#76).

The two counts are over different things, which is why they differ so widely.
**spends** counts Transactions — 136 of the 20,136 here are not coinbases.
**scripts** counts Inputs, one Script pair each, and those 136 Transactions have
1,157 Inputs between them.

**unresolved** and **faults** are separate on purpose. Over a prefix of the
chain every Input's parent is held, so `unresolved 0` is a real claim and
anything else is a defect. Once a directory has been pruned that stops being
true — a parent below the Watermark is gone deliberately — so unresolved counts
those and the faults stay clean. A Transaction that is *wrong*, paying out more
than it spends or naming an Output that cannot exist, is a fault either way.

## Reclaiming space

`prune` deletes the Blocks below a Height. It needs no Peer.

```bash
./target/release/main prune ~/chain 500000
2 Segments deleted, 4 Locations dropped, Prune Watermark now 500000
```

The Height becomes the **Prune Watermark**: below it, a Block that is absent was
discarded on purpose rather than never fetched. The Index keeps that distinction
because the two demand opposite responses — one is a gap to fill, the other is a
decision to respect.

A Segment is the unit of deletion and holds many Blocks, so pruning frees
nothing until a Segment holds none you are still keeping. Two rules follow, and
both are why the numbers above are often smaller than expected:

- The Segment currently being appended to is never deleted. It is where the next
  Block goes.
- Blocks are stored in the order they were fetched, not in Height order, so a
  range fetched out of order can leave a low Height sitting in a high Segment.
  Such a Block survives, and keeps its Location.

Pruning is safe to repeat and the Watermark only ever rises: pruning below a
Height already passed leaves it where it is. There is no command to lower it,
and `bodies` will re-fetch a pruned range if you ask for it — the Watermark
records what was discarded, it does not refuse to fetch it again.

Worked example, on 20,000 Blocks in three Segments:

```bash
./target/release/main prune ~/chain 15000
1 Segments deleted, 9355 Locations dropped, Prune Watermark now 15000
```

Only Segment 0 goes, because Segment 1 still holds Blocks above the Height.
8.6 MB becomes 4.6 MB. Afterwards `show` on a discarded Height says so rather
than reporting it missing, and `audit` over the range counts the Inputs it can
no longer follow as unresolved rather than as faults.

## The Index and its backends

The Index is a keyed store — Block Ids to Locations, Heights to Block Ids,
Transaction Ids to sites — and `infra/store.av` offers it over three backends
behind one API. Callers cannot tell them apart: `Store` is opaque, so the same
`get`, `putAll` and `deleteAll` reach whichever is there.

| backend | what it is | where it comes from |
|---|---|---|
| Memory | a `Map`, holding exactly what it was handed | `Store.fixture`, used by 71 verify cases |
| Logged | that `Map`, rebuilt on open from an append-only file | a directory with `index.log` and no `kv/` |
| Database | a LevelDB, read a key at a time | a directory with a `kv/` in it |

Which one a directory uses is a fact about the directory rather than about the
invocation, because the data really is in one shape or the other. `migrate`
makes the database and leaves the log alone, so going back is renaming a
directory:

```bash
./target/release/main migrate ~/chain
1454101 entries moved into ~/chain/kv; the log is still there
```

The log backend holds every entry in memory. Measured on the same 124 MB index,
the same binary, and the same two ranges:

| | log | database |
|---|---|---|
| `audit chain 1 4000` | 7.6 s, 615 MB | 4.0 s, 67 MB |
| `audit chain 170000 172000` | 44.5 s, 615 MB | 49.4 s, 97 MB |
| totals | identical | identical |

Memory is the point, not speed. 615 MB is the whole Index held open regardless
of what is being read; 67 MB is what the audit itself needs. The sharpest
difference is on the small commands: `tx` does one lookup and exits, and goes
from 4.5 s and 667 MB to 0.04 s and 19.8 MB, because opening stopped costing
anything. The second audit range is 11% slower, which is what a lookup costs
once it stops being free. What this buys is a ceiling set by the disk rather
than by RAM, which is what an output keyspace at two hundred million entries
needs. The full table, and why the log is being kept anyway, are in
[ADR 0006](docs/adr/0006-a-leveldb-under-the-index.md).

## Checking the code

```bash
aver audit   . --providers              # all three of the below, in one pass
aver check   . --module-root . --deps   # contracts, coverage, lints
aver verify  . --module-root . --providers --deps   # every verify block
aver format  . --check                  # formatting
```

60 files, 0 check errors, 0 format issues, and **3,889 verify cases**.
Everything except the socket is pure and covered.

`--providers` is not optional. The verify cases that reach RIPEMD-160 or a
signature check run the real implementations through the provider, so without
it 50 of them fail rather than passing on a fixture. That is the right way
round: a suite that quietly substituted a stub for the curve would report
passes it had not earned.

Values are pinned against sources outside this implementation rather than
captured from it. The wire format: a `verack` and a `ping` frame, a full
`version` payload, the genesis coinbase transaction id (which is published), and
a SegWit transaction id computed from the specification. The Script engine, the
signing messages and whole Transactions against the Outputs they spend: Bitcoin
Core's `script_tests.json`, `sighash.json`, `tx_valid.json` and
`tx_invalid.json`, converted into 1831 cases and answered by the engine rather
than by the file — see [docs/core-corpora.md](docs/core-corpora.md) for which of
Core's twelve test files are read, how to regenerate each, and what blocks the
rest. And Block 170's real signature, which was verified against the message
this code computes using thirty lines of Python secp256k1 written for the
purpose — because a reference implementation and the code under test, written by
the same hand, agree with each other whether or not they agree with Bitcoin.

## Layout

Fifty-four files. Grouped by what they are for rather than listed:

```
CONTEXT.md          glossary — the vocabulary this project commits to
aver.toml           the provider bindings — see Providers
docs/adr/           architecture decisions
tools/              the generator that turns Core's test vectors into cases
main.av             argv entrypoint, deliberately thin

app/                cli.av argument handling, show.av / lookup.av /
                    maintain.av one per group of commands

domain/  the wire
  address.av network.av message.av version.av inventory.av dns.av
  transaction.av    the SegWit-aware decoder
  compactsize.av hash.av text.av

domain/  addresses
  script.av         recognising output scripts, naming who they pay
  base58.av bech32.av

domain/  the chain
  block.av          Block Headers: reading, naming, asking for more
  checks.av segment.av index.av spend.av

domain/  Script
  opcode.av scriptparse.av stackitem.av scriptstate.av scriptmath.av
  scriptstep.av scriptops.av
  interp.av         the walk: one recursion over a Script, and only one
  spendscript.av    the Input's Script then the Output's, in that order
  sighash.av bip143.av   what a signature is actually over
  ecdsa.av checksig.av   the seam a curve will plug into
  scriptcases1-5.av sighashcases1-2.av  Core's vectors, 1,618 of them

infra/  the network
  peer.av           the Peer session: handshake and listen loop
  resolver.av download.av

infra/  the disk
  store.av          keyed store over three backends, one opaque API
  storelog.av       the append-only log's record format and replay
  kv.av             the key-value database capability contract
  migrate.av        one-shot: the log into the database beside it
  blocks.av chain.av txindex.av lock.av prune.av
  spends.av audit.av
```

The split is deliberate: only `infra/` touches the network or the disk, and
everything it does is an arrangement of pure parts from `domain/`. That is why
the wire format can be tested without a peer, why the whole Script engine is
checked without one, and why a failure against a live node is a socket problem
rather than an ambiguity.

## Protocol notes

Three details each produce output that looks right but is not:

- **`getdata` asks with inventory type `0x40000001`, not `1`.** Type 1 returns
  the legacy serialisation with witnesses stripped, so every input would report
  `witness 0` and nothing would appear wrong.
- **The transaction id is hashed over the legacy serialisation**, excluding the
  marker, flag and witnesses. Hashing the full bytes yields the wtxid — a
  plausible 64-character hex string that no explorer recognises.
- **Bech32 and Bech32m differ only in one constant.** Encoding a Taproot output
  with the v0 constant yields a well-formed-looking `bc1p…` address that no
  wallet will accept.
- **DNS over TCP is not DNS over UDP.** The message is identical but carries a
  two-byte length in front of it. Aver has no UDP service, and every recursive
  resolver is required to accept TCP, so this is the form the seed lookup uses.
- **Answer records point back at the question name** with a `0xC0` marker
  instead of repeating it. You do not need to follow the pointer to read an
  address, but you must recognise it to skip two bytes rather than a label
  sequence — otherwise every record after the first is misaligned.
- **`ping` must be answered.** A peer that is not answered drops the connection
  after a couple of minutes, which presents as "it stopped working after a
  while".

## Scope

Decoding covers the transaction structure, SegWit included: version, inputs
with their outpoints and sequences, outputs with amounts and scripts, witness
item counts, locktime, size, and the transaction id.

Standard output scripts are recognised and rendered as addresses — P2PKH and
P2SH through Base58Check, P2WPKH and P2WSH through Bech32, Taproot through
Bech32m. `OP_RETURN` is named, and anything unrecognised keeps its hex rather
than being guessed at.

Deriving an address needs no hashing at all: a script already contains the hash,
so the work is pattern-matching plus an encoding, not hashing a public key. That
was true before this project had RIPEMD-160 and is still true now.

Fees are deliberately not reported. A Transaction states its output amounts but
not its input amounts, and a peer will not serve the confirmed transactions
those inputs spend — measured on mainnet, 29 of 29 such requests came back
`notfound`, and across 441 inputs none had its parent in the same stream.
Getting fees means asking the node over RPC instead, which is a different
program; see [ADR 0002](docs/adr/0002-no-fees-over-p2p.md).

RIPEMD-160 and secp256k1 used to head this list. Both are supplied now by a
**capability provider** — see [Providers](#providers) below. What is left is:

| | needs | what it would unlock |
|---|---|---|
| `OP_SHA1` | SHA-1 | one opcode nothing in practice uses |
| witness and Taproot Scripts | Schnorr, and a witness evaluator | 90% of a modern Block |
| most modern spends | an output index, so parents resolve | see the `unresolved` count |

The signing messages both Scripts would need are already written and checked
against Core's vectors, legacy and BIP143 alike. What is missing is the
arithmetic, and the seam it plugs into — `domain/ecdsa.av` — deliberately has no
`Valid` constructor, so the day a curve arrives the compiler names every caller
that has to change.

## Licence

MIT — see [LICENSE](LICENSE).

## Providers

Some things this program needs are not written here and are not written in Aver.
They are declared as **capability contracts** — operations with no body — and
supplied at run time by Rust providers named in [`aver.toml`](aver.toml).

| Contract | What it is | Provider |
| --- | --- | --- |
| [`domain/primitives.av`](domain/primitives.av) | RIPEMD-160 and secp256k1 signature verification | [`providers/primitives`](providers/primitives) |
| [`infra/kv.av`](infra/kv.av) | a key-value database | [`providers/kv`](providers/kv) |

```toml
[[providers.bindings]]
capability = "Domain.Primitives"
crate = "btc_listener_primitives"
path = "providers/primitives"
factory = "primitives_binding"
```

`aver compile` emits the Cargo dependency and a bootstrap that installs the
binding and preflights the complete operation set before any Aver code runs, so
the ordinary generated binary is the host — `aver compile` then `cargo build`, as
before. A provider declaring the wrong `contract_hash` is refused at startup
rather than called.

The curve is a provider on purpose. RIPEMD-160 was written in Aver first and
passed all eight published vectors, but a curve is not a hash: 256 bits of field
arithmetic whose edge cases *are* consensus rules, where a wrong answer is a
false audit rather than a slow one. The provider hands the question to
`libsecp256k1`, which is what Bitcoin Core itself runs. RIPEMD-160 then followed
the curve behind the same contract, so there is one boundary rather than two.

`Infra.Kv` is the second contract and the newer one. The file backends keep the
whole index in a Map, which cannot be stretched to the two hundred million
entries an output keyspace would need. That is a database, and Aver has not got
one. Unlike the primitives it is **effectful**, so every operation declares an
Oracle dimension and what a replay does with it. The provider is
`rusty-leveldb`, chosen over RocksDB because it needs no C++ toolchain, and the
choice is provisional: swapping the crate changes one Rust file and no Aver at
all.

`Handle` is an opaque capability resource: only `open` produces one, only the
contract's operations consume one, and Aver cannot construct, name or serialise
it. It was a record holding an `Int` for a day, while
[jasisz/aver#994](https://github.com/jasisz/aver/issues/994) was open — an
opaque resource could be passed and returned but not held as a field of a user
record or sum, and the Store is a sum whose database arm has to hold the open
database. That is fixed and the workaround is gone.

### What this costs

For a while it cost the corpus its teeth. `aver verify` had no provider, so
every case that reached RIPEMD-160 or a signature check bound an Aver stub
through `given` — a hand-built table of published digests, which tested that
the engine pushes, hashes, compares and settles, and no longer tested whether
RIPEMD-160 was RIPEMD-160.

That is over. [jasisz/aver#989](https://github.com/jasisz/aver/issues/989)
closed, `aver audit . --providers` runs the whole project against the real
implementations, and the stub table is deleted. Every expectation the fixtures
had been standing in for holds against the real thing.

What remains:

- The implementations are still tested where they live, in the providers' own
  Rust tests: the eight published RIPEMD-160 vectors and the first spend
  Bitcoin ever made; and for the database, round trip, overwrite, delete,
  reopen, prefix order, a handle it never issued, and a batch whose write-ahead
  record is deliberately torn, which comes back absent rather than half applied.
- **Plain `aver run` cannot start this program**, and that is the safe failure:
  an interpreter running the audit with no crypto would report passes it had not
  earned. Pass `--providers` and it works — Aver builds a thin Rust host from the
  `[providers]` composition, caches it, and runs the ordinary VM with the binding
  installed.

  ```bash
  aver run main.av --module-root . --providers -- audit chain 1 2000
  ```

`aver check`, `aver format` and `aver capabilities` need no provider. `aver
verify` and `aver audit` do, and take `--providers` to get one.
