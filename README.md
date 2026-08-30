# Bitcoin Peer Listener, Chain Downloader and Auditor

A Bitcoin node that speaks the peer-to-peer protocol directly. It started as
a listener that printed each announced Transaction decoded, and it still does
that; it now also downloads the chain from several Peers at once, builds the
UTXO Set, runs every Script, holds a Mempool, takes compact Blocks, follows
the tip through reorganisations, and serves the chain to Peers that dial it —
a Bitcoin Core node with no other Peer has synced its whole chain from this
one. Every check it makes is against its own Index and Segments, and every
answer it gives keeps *cannot tell* apart from pass and fail.

Written in [Aver](https://averlang.dev). [docs/architecture.md](docs/architecture.md)
is how it is put together and why; this file is how to use it.

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

Fifteen of them. Only the ones marked below need a Peer; everything else reads
what those wrote and works offline. Put `signet` or `regtest` in front of any
of them to speak that Network instead of mainnet ([Signet](#signet)).

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
| `utxo <dir> <height>` | [connect](#the-utxo-set) Blocks into the UTXO Set up to a Height | no |
| `assumevalid <dir> <height>` | [take](#the-utxo-set) Scripts at or below a Height as settled | no |
| `follow <peers> <dir> [screen] [serve[:port]] [log[:path]] [http[:port]]` | [stay](#following-the-tip) on the tip, and never stop | yes |
| `follow <dir> [screen] [serve[:port]] [log[:path]] [http[:port]]` | the same, finding its Peers through the DNS seeds | yes |
| `prune <dir> <height>` | [delete](#reclaiming-space) the Blocks below a Height | no |
| `reindex <dir>` | [rebuild](#recovering-a-lost-index) every Block's Location from the Segments | no |
| `help` | print the usage | no |

The three fetching commands have to run in this order, because each needs what
the one before it wrote:

```
headers ─→ bodies ─→ txindex
```

`show` and `prune` then need `bodies`. `tx`, `spend` and the spend half of
`audit` need `txindex` as well — without it every Input reads as unresolved,
which is an answer rather than an error.

`follow` is the first two plus `utxo`, run again every time a Peer says a
Block arrived, so it both catches an empty directory up and keeps a caught-up
one current — and, once caught up, holds a Mempool, relays Transactions and
answers Peers that dial it.

Everything but the listener takes a `<dir>`, where the Index and the Segments
live. Point them all at the same one.

All of them run either way. `aver run` builds the provider host once and caches
it, so the interpreted path works; the examples below use a compiled binary
because anything that opens the Index is several times faster that way. See
[Providers](#providers).

## Requirements

- **Aver at the commit in [`.aver-version`](.aver-version).** The version
  string does not move between upstream commits, so the project pins a SHA
  rather than a number, and CI builds exactly that one. Anything older than
  0.29 lacks what this program needs (byte-clean `Disk`, `Tcp.poll`, the
  automatic provider host). See [Moving the Aver pin](#moving-the-aver-pin).
- `clang` and `libclang-dev`, for the RocksDB bindings.
- A reachable Bitcoin node. Any peer will do; one you run yourself is easier to
  debug against.

A machine that only needs to *run* this needs none of that — not Aver, not
Rust, not `clang`. CI publishes a compiled binary for it. See [Running it on a
server](#running-it-on-a-server).

## Running

```bash
cd btc-listener
aver run main.av --module-root . -- [peer-address] [port]
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

It will also stop if a read fails, and if the peer falls silent. A session
read still has no deadline of its own —
[jasisz/aver#782](https://github.com/jasisz/aver/issues/782) was answered by
removing one rather than making it configurable, on the grounds that timing
out part way through a frame leaves the stream silently desynchronised — but
every frame now starts with `Tcp.poll` at the message boundary, the one place
a timeout abandons nothing
([#55](https://github.com/n1bor/btc-listener/issues/55)). Bitcoin Core pings
an otherwise-quiet connection every two minutes, so a Peer that has said
nothing for two and a half is gone, and the session ends with `Peer said
nothing for 150 seconds` instead of blocking while looking like it is
working. Nothing blocks mid-frame any more either: since #27 every socket,
the listener included, is read as bytes arrive (`Tcp.readSome`) and Messages
are cut off the front of a per-Peer buffer, so a Peer that stalls part way
through a frame costs only itself. The listener is a pool of one on the same
machinery `follow` runs eight Peers on.

`aver compile` used to print one warning per dependency module — 45 lines
saying the module's verify blocks were not sampled — and the README carried a
`grep -v` to filter them. [jasisz/aver#857](https://github.com/jasisz/aver/issues/857)
closed and the warning is gone; compile is quiet, and so is the compiled
binary: an unparseable address still says `error: octet 999 is out of range
(0-255)`.

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

One honesty note. The signet challenge — the block signature signet carries
in its coinbase witness commitment — is not checked, the same way every rule
this engine does not carry is not checked: the absence of a check is never
reported as a pass. A directory does record which Network filled it, so its
addresses render with that Network's prefixes (`tb1…` on signet) whichever
command reads them ([#34](https://github.com/n1bor/btc-listener/issues/34)).

### Regtest

`regtest` works the same way — magic, port 18444 and genesis follow, every
consensus rule is in force from Height 1, and the subsidy halves every 150
Blocks — and it is the Network the end-to-end test runs on, because a
reorganisation on regtest can be summoned with `invalidateblock` rather than
waited for. See [Reorganisations, on demand](#reorganisations-on-demand) and
[docs/regtest-testing.md](docs/regtest-testing.md).

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

`headers` must run first. A `getdata` names Block Ids and never Heights, so the
chain has to be known before any Block can be asked for. It fetches every Header
— around 962,000 at the time of writing, roughly 24 minutes — keeping each one
and working out where it sits. Asking for bodies above the Height it has reached
gives `no header for height N`.

It does not assume the Peer is sending one chain. Every Header goes into a tree
keyed by Block Id, carrying the Height it sits at and the Chain Work of the
Branch below it; the Heights are then pointed at whichever Branch has the most
work. A Header whose parent is not the tip is a Branch and not an error, and a
Header whose parent has not arrived is an Orphan — counted, not refused.

Growth says so quietly and a Reorganisation says so loudly, because they are not
the same event: a Height that held nothing is the chain getting longer, and a
Height that held a different Block Id is the chain changing its mind.

```
headers to 318986
headers to 318987  REORGANISED: 2 Height(s) re-pointed above 318985
```

Re-running `headers` on a directory that is already up to date places nothing
and stops, rather than asking the same question again.

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
body   segment 0 offset 1273, 490 bytes
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
body   segment 0 offset 4181, 1634536 bytes
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
Transactions. The Store that came before the database kept every entry in
memory, which is why the command takes a range; on RocksDB the ceiling is the
disk instead, and the keyspace was always the one a database would be given.

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
unresolved. That is the point of putting the Index on a database.

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
check(input, output, Domain.Rules.at(Network.Mainnet, 170060), context) => Decided(Passed)
check(input, output, Domain.Rules.at(Network.Mainnet, 173805), context) => Decided(Failed(…))
```

Both of those are real: the same spend, from Block 170060, on either side of the
BIP16 activation Height. Below it, `a914<hash>87` is an ordinary Script — hash
the top item, compare, leave true — and the redeem Script is data that never
runs. That is exactly the hole BIP16 closed, and those coins really were
spendable by anyone who knew the preimage.

`Domain.Rules.at` carries every soft fork the engine implements, each at its
activation Height per Network: P2SH (BIP16, mainnet 173,805), strict DER
(BIP66, 363,725), `OP_CHECKLOCKTIMEVERIFY` (BIP65, 388,381),
`OP_CHECKSEQUENCEVERIFY` (BIP112, 419,328), SegWit and NULLDUMMY (BIP141/147,
481,824) and Taproot (BIP341, 709,632). Signet and regtest have had all of
them since Height 1, which is why `audit` needs the Network word too: auditing
a signet directory as mainnet judges the whole chain by a timetable it never
followed. Policy — what this node will *relay* — is a separate thing
([Domain.Standard](domain/standard.av)) and is never applied to a mined Block.

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

What remains undecided is small: a Script pair whose answer needs a
Transaction the case does not carry. SHA-1 arrived with the providers, so
`OP_SHA1` runs; version 0 witness programs run under BIP141 and BIP143, and
Taproot key-path and tapscript spends under BIP341 and BIP342 — the signet
audit below reaches zero undecided over 2.1 million Scripts.

Counting it apart from **failed** is the whole discipline. An engine that called
"cannot tell" invalid would be worthless over a chain held in part — and one that
called it valid would be worse. That second one is not hypothetical: running
three Blocks from 2023 produced **6,363 passes from an engine that has never
verified a signature**, and every one was wrong. Segwit was deployed as a soft
fork, which *required* a witness program to look valid to nodes that could not
read it — a version byte and a push of a hash leaves the hash on the stack and
comes out true. The fix, before the witness evaluator existed, was to refuse a
witness program rather than run it, and those Blocks read
`0 passed / 0 failed / 6493 undecided`; now that the evaluator exists the rule
survives as the seam it guards — anything the engine cannot read is undecided,
never passed.

Bitcoin Core's own test data is the adversarial test, and nothing written here
would be half as unkind. Eleven files are read into `corpus/`, 6,050 cases
between them, and every Script case carries the verification flags Core ran it
under:

| corpus | cases | agree | disagree | undecided |
|---|---|---|---|---|
| `script_tests.json` Script pairs | 1120 | 1050 | 0 | 70 |
| `script_tests.json` Witness rows | 108 | 108 | 0 | 0 |
| `tx_valid` + `tx_invalid` | 214 | 214 | 0 | 0 |
| `sighash.json` | 500 | 500 | 0 | 0 |
| `key_io_valid.json`, both directions | 108 | 108 | 0 | 0 |
| `key_io_invalid.json` | 70 | 70 | 0 | 0 |
| `base58_encode_decode.json`, both directions | 42 | 42 | 0 | 0 |
| BIP341 `wallet-test-vectors.json` | 39 | 39 | 0 | 0 |
| `script_assets_test.json`, tapscript spends | 3737 | 3737 | 0 | 0 |
| SipHash-2-4 reference vectors | 64 | 64 | 0 | 0 |
| one compact Block, captured from Core | 31 | 31 | 0 | 0 |

**Nothing disagrees, in either direction.** The seventy undecided are Script
pairs whose answer needs a Transaction the row does not carry, which is the
honest answer rather than a disagreement — and the direction that would matter,
refusing what Core accepts, is nought and has always been nought.

Two of those cases — the 10,000 byte Script and the 1,911 byte twelve-input
Transaction — used to be answered by the compiled engine alone, because
`aver verify`'s VM stops a case at a million steps and raising that budget
needed a flag it did not have. It has one now:
[jasisz/aver#1071](https://github.com/jasisz/aver/issues/1071) closed with
`[[verify.costly]]`, `aver.toml` carries an entry for `corpus/scriptcases*.av`
and one for `corpus/txcases*.av`, and both cases are checked on every run like
everything else. The counts in the table above are what says so: 1120 for
`script_tests.json` and 214 for `tx_valid`/`tx_invalid`, each generated
module's `intent` carrying the count it was generated with. A corpus that
cannot say how many cases it holds is one that can lose some without saying
so, which is the habit worth keeping — not the exemption that made it
necessary.

`script_assets_test.json` was the odd one out: nine megabytes and 3,737 tests,
once too large and too slow for verify, so it lived behind a `taproottest`
command. jasisz/aver#1104 made verify parallel and `[[verify.costly]]` carries
the step budget its largest entries need, so it is `corpus/assetcases1..9.av`
now, generated by `tools/script_assets_to_aver.py`, run with everything else
([#101](https://github.com/n1bor/btc-listener/issues/101)). When Core moves,
`tools/refresh_corpora.sh` fetches every corpus and regenerates whatever
changed — [docs/core-corpora.md](docs/core-corpora.md) has the disciplines. It is the
only published corpus that tests tapscript execution, and reading it found
**seven consensus faults**, every one of them lax: this engine accepting what
Core refuses. The largest made any Script holding a 32-byte public key with an
`0xea` in it succeed without running a single opcode.

Three of the others are read **both ways**, which matters more than the case
count suggests. An encoder only ever asked to produce output agrees with itself
forever; `key_io_invalid.json` is 70 strings that must not decode, and on its
own a decoder that refused everything would pass all of them. It is worth
something only because the same function has to succeed 54 times next door and
return the exact Script Core records. See
[ADR 0005](docs/adr/0005-a-script-engine-with-the-signatures-left-out.md) and
[docs/core-corpora.md](docs/core-corpora.md).

## Segwit and Taproot on a real chain

Signet has had SegWit and Taproot since Height 1, which makes it the evidence
mainnet cannot be until it is downloaded past 481824:

```
$ main signet audit signet-probe 1 96000
blocks 96000  transactions 1749336  spends resolved 1653336  coinbase 96000
unresolved 0  scripts 2133120 passed / 0 failed / 0 undecided
CLEAN (faults 0, script failures 0)
```

Not one undecided script in 2.1 million. Six months ago the same walk left
13.8 million of them, every one the same sentence — *output is a witness
program and needs the witness evaluated*.

## Reorganisations, on demand

A signet reorganisation cannot be summoned; you wait, and it may never come.
`bitcoin-cli -regtest invalidateblock` produces one whenever you like, which
matters because disconnecting a Block is the only path that reads **Undo Data**
back, rewrites the `h:` keyspace and rolls the UTXO Set backwards. A change can
break all three and pass every other test in this repository.

```
headers to 167  REORGANISED: 5 Height(s) re-pointed above 155
REORGANISED: the Set stands on a branch the chain has left; taking it back to
  Height 155, disconnecting 5 Block(s)
  height 160 disconnected: set +0 -1
  ...
following at Height 167: 12 connected, 0 disconnected, set +12 -0
```

[docs/regtest-testing.md](docs/regtest-testing.md) is the standing end-to-end
test: every command in order against a local node, real spending Transactions
across all four address types, the reorganisation above, and a Block-Id
comparison against Core itself. It also carries `tools/regtest/liar.py`, a Peer
built to lie — Core is cooperative by construction, so the paths that exist for
Peers that misbehave need one that will. Run it before committing, and when you
test something it does not cover, add it there.

One warning it repeats, because it is easy to fool yourself: an audit over
coinbase-only Blocks reports `0 spends, 0 scripts` and says **CLEAN**. Read the
counts, not the word.

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

## The UTXO Set

`outputs` records every Output ever created and never forgets one. `utxo` keeps
the other kind of record — the Outputs nobody has spent yet — and it is the one
that can say value was conserved rather than only that Outputs existed.

```bash
./target/release/main signet utxo ~/chain 3000
```

It takes a Height rather than a range, because a UTXO Set is the state after
connecting every Block up to one Height; there is nothing a start Height could
mean. It resumes from whatever it last recorded.

Three rules are checked as each Block connects, and none of them is about
signatures:

- every Input finds an unspent Output it is allowed to spend — including the
  hundred-Block wait on anything a coinbase minted
- no Transaction pays out more than it takes in
- the coinbase claims no more than the subsidy plus the fees the rest of the
  Block left behind

A Block that breaks one of them stops the walk with the reason. So does a
Height whose body is not held: stepping over it would connect the next Block
against a Set that never existed, and every Block after it would be checked
against a lie. That is the opposite of what `outputs` does with a gap, and
deliberately so — each `o:` entry stands alone, and a UTXO Set does not.

Connecting a Block also writes its **Undo Data**: what it took out of the Set,
so the removal can be reversed if that Block leaves the chain. That is kept for
288 Blocks — two days, and deeper than any reorganisation Bitcoin has had. A
reorganisation reaching below the window cannot be undone, and the node says so
and stops rather than guessing.

How fast it goes is a matter of shape, and the shape is measured before it is
kept. The next Block is read and decoded while this one's Inputs are resolved
against the Store, as two branches of one product, and every write happens
after the product on the one thread that owns the Store
([ADR 0008](docs/adr/0008-independence-and-a-single-writer-loop.md)). Recently
made and spent Outputs are held in a write-back window and asked of the Store
only when the window cannot answer — most Outputs are spent within a few
thousand Blocks — and the window is written through in one batch every
200,000 entries ([#247](https://github.com/n1bor/btc-listener/issues/247),
[#253](https://github.com/n1bor/btc-listener/issues/253)).
Each of those is a measured multiple on a mainnet snapshot; two later ideas
that measured as losses are closed with their numbers
([#251](https://github.com/n1bor/btc-listener/issues/251),
[#252](https://github.com/n1bor/btc-listener/issues/252)).

Genesis is not connected. It is the base the chain is measured from: no Peer
sends its body and its Output cannot be spent, which is why the fifty coins of
Block 0 are missing from the supply that can ever move. Bitcoin Core leaves it
out of the UTXO Set for the same reason.

**Provably unspendable Outputs are declined**, on Core's own rule. Every SegWit
coinbase carries a witness commitment paying zero to an `OP_RETURN`, which
nothing can ever spend, and an Output nothing can spend has no business in a
set of Outputs that can be spent. This was measured before it was fixed: on a
regtest chain at Height 103, Core's `gettxoutsetinfo` reported 103 entries
where this held 206 — the same 5150 BTC of value, and one extra zero-value
entry per Block, which is roughly double the entry count on a SegWit chain.
[#111](https://github.com/n1bor/btc-listener/issues/111).

The Set records **which Block it stands on**, not only which Height. A Height
alone means "built along whatever the Index named at the time", and the Index
is the one thing a reorganisation rewrites — so a Height alone stops being true
the moment the chain changes its mind, and nothing on disk can say that it has.
A Set written before this was kept is refused with an explanation rather than
read as though the question never mattered.

### The Assume-valid Height

```bash
./target/release/main signet assumevalid ~/chain 2000
```

Below the Height, Scripts are not run; merkle roots, parent links, work, value
accounting and the UTXO Set are all still checked. Above it, everything is
verified. [ADR 0007](docs/adr/0007-two-claims-two-tools.md) has the reasoning,
and `audit` is the other half of it: the tool that goes back and fully verifies
any range, at whatever pace the engine and the disk allow.

The claim is pinned to a **Block Id**, not a bare Height — the one the Index
held there when the claim was made. If a reorganisation later moves that Height
onto a different Block, the Blocks that were skipped are not the Blocks sitting
there now, and every `utxo` run says so:

```
ASSUME-VALID BROKEN at Height 2000: the Index now names 000000... there.
The skipped Blocks are not the Blocks there now; re-run audit over that range
or clear the claim
```

There is no default. Bitcoin Core ships a constant per Network; this does not,
because a constant nobody here can check is the kind of borrowed claim the rest
of the project refuses. Unset means every Script runs, and every `utxo` run says
that too.

## Following the tip

Everything above is a chain you were handed. `follow` is the one that stays
current:

```bash
./target/release/main signet follow 192.0.2.1,192.0.2.2:38333 ~/chain
./target/release/main signet follow ~/chain                     # Peers from the DNS seeds
```

One Peer Address or several separated by commas, each with an optional
`:port` — or none, and the seeds are asked. It handshakes with all of them,
catches the directory up — Headers, then bodies, then the UTXO Set — and then
waits. When a Peer announces a Block it does the same three phases again, and
the run says where that left it:

```
peer 0 is 192.0.2.1:38333
peer 1 is 192.0.2.2:38333
following at Height 318980: 4 connected, 0 disconnected, set +9 -6
2026-08-24 11:42:07  1 Block(s) announced by peer 1
following at Height 318981: 1 connected, 0 disconnected, set +2 -1
```

Whichever Peer announces a Block is the one asked for it. A second Peer
announcing the same Block a moment later is not a special case and is not
suppressed: the Header phase places what it already holds, nothing moves, and
the run says `0 connected`.

An `inv` naming a Block is answered with **getheaders**, not `getdata`. A Block
whose Header the tree has not placed cannot be connected, and cannot even be
told apart from a Block on a Branch we are not following — so asking for the
body first buys nothing and loses the ordering. The `getdata` still happens; it
is what the body phase is, one Header later. Bitcoin Core answers an unexpected
`inv` the same way, for the same reason.

A Transaction announced down the same `inv` is asked for, checked and, if it
passes, held and announced onward — see [The Mempool, and relay](#the-mempool-and-relay).

### When the chain changes its mind

This is the part the earlier stages could only detect. The Header tree picks
the Branch with the most Chain Work and re-points the Index at it; the Set is
then standing on Blocks that are no longer on the chain, and they have to come
off before anything new goes on.

The walk back reads the Set's own Branch out of `k:`, parent by parent, because
the Index no longer leads there — and stops at the first Height where the two
still agree. That Height is the fork. Everything above it is disconnected,
highest first, each Block from the Undo Data it wrote on the way in; then the
bodies of the new Branch are fetched and connected:

```
REORGANISED: the Set stands on a branch the chain has left; taking it back to
Height 318979, disconnecting 2 Block(s)
  height 318981 disconnected: set +3 -4
  height 318980 disconnected: set +2 -3
following at Height 318982: 3 connected, 2 disconnected, set +7 -5
```

Highest first is not a detail: each Block's Undo Data restores what its own
Inputs spent, so the Set has to reach a Block in the state that Block left it.

A fork **below the Undo window** stops the node:

```
error: the chain forked below Height 318693, which is further back than the
Undo window reaches; the UTXO Set cannot be taken back that far and has to be
rebuilt
```

That is the price of a bounded window, and it is deliberate (decision D8). 288
Blocks is two days. The deepest reorganisation Bitcoin has ever had was 24
Blocks, in March 2013, and that was a consensus split rather than mining.

`follow` holds the directory for as long as it runs, so a `prune` or a second
follower cannot write the same keys from a different idea of where the tip is.

### Why several Peers needed the reading to change

The single-Peer code asked a socket for exactly twenty-four bytes and then for
exactly the payload they announced. That is the clearest thing to write for one
Peer and the wrong thing for several: a read of an exact length holds the loop
until that length arrives, and every other Peer waits behind it — including
their pings, which go unanswered until they drop us. A body download takes
hours, so this is not a corner case.

So bytes are taken as they come and kept per Peer, and Messages are cut off the
front of what has accumulated. Readiness comes from one `Tcp.poll` over every
connection at once. A caller still writes its half of a conversation as
straight-line code — ask this Peer for Headers, wait for them — and every other
Peer is read, buffered and answered while it waits.

A header announcing more than **4,000,000 bytes** — Bitcoin Core's
`MAX_PROTOCOL_MESSAGE_LENGTH` — ends the connection. Reading exactly what a
socket is told to read had a ceiling underneath it; a buffer this program fills
itself does not, so the ceiling had to be named.

Everything goes through the same pool: `headers`, `bodies` and the plain
listener are pools of one. A Message is read off a wire in one place in this
program rather than two, so a bulk download and a following node cannot drift
into different ideas of what a Message is.

### Finding its own Peers

A node that only knows the Peers it was told about on the command line has one
way in and one way to be cut off. So `follow` asks each Peer, once, for the
Peers it knows — `getaddr` — and listens for the `addr` Messages that arrive
unasked afterwards. What comes back are **Candidates**: Peer Addresses somebody
claimed exist. A Candidate becomes a Peer when a Handshake completes and not
before, which is the whole reason the two words are different.

```
peer 0 is 135.180.99.74:38333
headers complete: 319148 known
the Address Book holds 486 Candidate(s), up 486
peer 1 is 88.99.167.175:38333, from the Address Book
```

The node keeps up to **eight** Peers, which is Bitcoin Core's outbound default
and chosen for the same reason: enough that losing one is routine and that no
single Peer decides what chain we see, few enough not to be a burden on the
Network. It dials at most one Candidate per turn of the loop, because a
Handshake is a conversation and this loop is the only thing that can have one.

Only IPv4 is read. An `addr` entry carries sixteen bytes of address and a
`PeerAddress` is four, so IPv4 arrives as an IPv4-mapped IPv6 address; anything
else is skipped rather than refused, because a Peer offering an IPv6 address is
being helpful rather than wrong.

The Address Book is **not persisted**. A node that restarts asks its Peers
again, which costs one round trip and is how it would learn about changes
anyway.

Two things it does not do, both recorded as issues rather than left to be
assumed:

- **Selection is first-untried, so it is a convenience and not a defence.**
  Core buckets addresses by where they were heard from and picks randomly
  within a bucket, precisely so a Peer answering `getaddr` with a thousand
  addresses it controls cannot choose who you connect to next. That is
  [#118](https://github.com/n1bor/btc-listener/issues/118).
- ~~**A Candidate that does not answer stalls the loop for five seconds.**~~
  **Fixed.** A dial is now one more key in the same `Tcp.poll` as the Peers
  ([jasisz/aver#1125](https://github.com/jasisz/aver/issues/1125),
  `Tcp.beginConnect`/`Tcp.dialled`, wired in `Infra.Peers.joined`), so the
  five seconds a dead address costs are five seconds every other Peer spends
  being read rather than five seconds nobody spends. The deadline itself has
  not moved and does not need to: `[effects.Tcp] connect_timeout_secs` in
  `aver.toml` is still 5, still deployment policy rather than a parameter,
  and is what ends the attempt.

  The history is worth keeping, because the first version of this bullet was
  wrong. The "about three minutes" it used to claim was a misread of two log
  lines with no timestamps: the gap between them was the loop's own
  150-second idle poll plus the 5-second dial. Measured properly, a dead
  address cost 5.0 s ([#119](https://github.com/n1bor/btc-listener/issues/119),
  corrected in [jasisz/aver#1118](https://github.com/jasisz/aver/issues/1118))
  — which is the number that is no longer paid by anyone but the Candidate.

### When a Peer does not play fair

Every Message is checked before anything is made of it. The four magic bytes
must be this Network's, and the payload must hash to the checksum its own
header carries — four bytes of double-SHA256, which every other Bitcoin
implementation checks and this one did not until Stage 5. A Block body must
also hash to the Block Id it was asked for; without that, a `getdata` names a
Block Id and a Peer answers with bytes, and nothing makes those the same thing.

A Peer that fails one of those is dropped, and the node goes on with the
others. Run against a deliberately hostile Peer beside an honest one:

```
peer 0 is 127.0.0.1:18455
peer 1 is 127.0.0.1:18444
dropping peer 0: a Message did not match the checksum its own header carries
following at Height 107: 107 connected, 0 disconnected, set +107 -0
```

A catch-up that fails against a Peer drops it and tries the next, so the first
Peer named on the command line cannot end the node by lying once — which is
what it did before this, and what the hostile-Peer test found.

A Transaction is decoded only as far as its bytes go
([#282](https://github.com/n1bor/btc-listener/issues/282)). Every count in
it — Inputs, Outputs, each Witness stack's items — is a CompactSize the Peer
chose, up to 2^64, and every script and Witness item carries its own length;
each is refused when the bytes that follow could not hold it, for the price
of walking those bytes once, before anything is built from it. Before this a
thirteen-byte `tx` claiming 2^64−1 Inputs held the loop for ever, one phantom
Input per turn.

A fault in the chain directory ends the run with every Peer still connected
([#290](https://github.com/n1bor/btc-listener/issues/290)). A catch-up asks
one Peer for Headers and bodies, and a fault in what that Peer sent costs it
its place; but a fork below the Undo window, a body that was pruned, a Segment
that will not read or whose bytes hash to some other Block are facts about
this node's own disk, and used to be charged to that Peer, then the next, until
the pool was empty. `Infra.Rewind` and the Set phase now end the run as
`ChainStopped` with the reason, and a body coming off the Set is hashed
against its Block Id on the way out as it has been on the way in since #283.

Disconnecting a Block is one batch
([#289](https://github.com/n1bor/btc-listener/issues/289)), as connecting one
has been since #247: the Outputs it spent go back, the Outputs it made and its
Undo Data go, and `meta:setTo` moves to its parent, in one RocksDB write. It
was three, and a crash between the second and the third left the Set standing
on a Block whose Undo Data had just been deleted — which nothing could take
back off, so the Set had to be rebuilt.

A Handshake gets one deadline, not one per frame
([#284](https://github.com/n1bor/btc-listener/issues/284)). The greeting
still runs inline — the loop waits on it, which
[#30](https://github.com/n1bor/btc-listener/issues/30) wants driven by the
loop instead — but the wait is now ten seconds for the whole Handshake, fixed
when the caller is seated, where before every frame that was not a `verack`
bought another sixty. A caller that sent a `ping` every fifty-nine seconds and
never a `verack` held the loop for ever, and a silent one held it until some
other Peer happened to speak, up to the 150-second idle deadline. Now a
silent caller costs ten seconds, nothing at all is accepted before `version`
(Core's rule), and [Domain.Handshake](domain/handshake.av) allows eight kept
Messages between `version` and `verack` — Core sends three there — before
the caller is dropped. `tools/regtest/caller.py` is the caller, four ways.

A Block body has to be the Block before it is kept
([#283](https://github.com/n1bor/btc-listener/issues/283)). Hashing its first
eighty bytes proved only that a Peer answered under the right Block Id;
[Domain.Body](domain/body.av) now proves the rest is that Block before a byte
of it reaches a Segment — the Transactions decode, the first and only the first
is a coinbase, each passes `TxCheck`, none repeats, and they build the Merkle
Root the Header commits to without a duplicated pair (CVE-2012-2459). A body
that fails is the fault of the Peer that sent it: that Peer is dropped and the
Height goes back on the list for whoever is left. A compact Block rebuilt from
the Mempool goes through the same check, and is kept only if it is the Block
being rebuilt and its Header is in the tree. A body read back from disk is
hashed against the Block Id the Index filed it under before its Transactions
are connected, so a torn Segment is a report naming the Height rather than the
wrong Block under the right Id. Until this, any Peer handed a `getdata` could
answer with the honest Header and a body of its choosing, and `b:` being
append-only meant the honest body was never asked for again.

A Header has to prove its work before it is placed
([#281](https://github.com/n1bor/btc-listener/issues/281)). Its Block Id must
fall under the target its own bits name; those bits must be what the Network's
retarget rule gives for its Height — Core's, pinned in
[Domain.Target](domain/target.av) to the bits Core published for mainnet's
first retarget at Block 32256, with the twenty-minute rule on testnet and
regtest; and its timestamp must be after the median of the eleven beneath it
and no more than two hours past the clock. Until this, the tree credited every
Header the work its bits claimed and asked `meetsTarget` only in `audit`, so a
Peer sending one Header with a target of one outweighed the whole chain, `h:`
followed it, and the Set was stranded below the undo window. A batch with a
Header that fails is refused whole, before anything is written, and the Peer
that sent it is dropped:

```
dropping peer 0: Header cfbd2ff9…4ed42 does not meet the target its bits 16842752 name
following at Height 167: 0 connected, 0 disconnected, set +0 -0
```

There is no misbehaviour score. Bitcoin Core keeps one because it has graded
offences; every fault this program can currently detect is one Core disconnects
on outright, so a counter with a threshold would be a counter that only ever
reaches one. The Mempool's refusals are not offences either: a Transaction that
fails Policy is simply not held, because a Peer relaying what its own Policy
admits is doing nothing wrong.

### The Mempool, and relay

Once caught up, a Transaction a Peer announces is asked for and put through the
same questions in a fixed order, cheapest first: its shape against Policy
([Domain.Standard](domain/standard.av), which refuses most of what should be
refused for free), the Outputs it spends looked up in the **UTXO Set** — not
the `o:` keyspace, which would resolve an Output spent years ago and admit a
double spend — then value, then the fee floor, then its Scripts. What passes
is held and announced to every other Peer; what fails is dropped with the
reason. The Mempool is capped at 300 MB of Transactions, Core's default, and
what leaves first when it fills is what pays least, so filling it costs the
sender more than it costs us. Conflicts are first-seen-wins; BIP125
replacement, the orphan pool and package relay are deferred by
[#28](https://github.com/n1bor/btc-listener/issues/28), so a Transaction
spending an unconfirmed parent is refused rather than admitted on a guess. A
Block confirms what it contains out of the Mempool; a disconnected Block's
Transactions go back through admission rather than straight back in, because
what was valid on an abandoned Branch may be a double spend on the one that
replaced it.

### Compact Blocks

A Block at the tip is one to two megabytes, almost all of it Transactions the
Mempool already holds. With `sendcmpct` agreed, a Peer sends the Header, a
six-byte short Id per Transaction and the few it guesses we lack;
[Domain.CompactBlock](domain/compactblock.av) rebuilds the Block from the
Mempool, names the positions still missing, and the loop asks for those with
`getblocktxn`. The short Ids are SipHash-2-4 under a key derived from the
Header and a nonce the sender chose, pinned against the reference vectors. A
Block that cannot be rebuilt is fetched whole, as before.

### Peers that dial us

`serve` binds the Network's own port (`serve:PORT` another) and accepts
inbound Peers, up to **eight** — a bound on the slots anybody at all can take,
kept separate from the eight outbound ones we choose. They are handshaken and
served: `getheaders`, `getdata` for Blocks out of the Segments and for
Transactions out of the Mempool, `getaddr` from the Address Book. A Bitcoin
Core node with this as its only Peer synced its whole regtest chain from it,
validated it and left initial block download — the sentence the full-node
plan was written to earn — and [docs/regtest-testing.md](docs/regtest-testing.md)
§11–12 is how to repeat that.

### The company is kept while the node walks

A catch-up used to tend its Peers only between chunks of the Set walk, ten
seconds apart, and a dial begun between two chunks was first looked at after
the next one — past its deadline — so a catching-up node gained no Peers
(#275: 134 dials in a night, 134 called dead). The walk's Eye now carries
the pool, the Address Book and the next key (**Kept**, CONTEXT.md) and tends
them once a second from inside the walk: what Peers sent is read and kept
for the loop, the dial is asked what it became, a Candidate that answered is
seated and asked who it knows, and the next dial is begun. `debug.log` says
`seated N at … from the Book while walking`. The Peers Panel and the page
show the company as it stands, chunk by chunk. And a pool that empties with
nobody left to dial asks the DNS seeds again rather than ending the run
(#272).

### When something takes too long: the watchdog lines

`debug.log` also says when the node has overrun itself (#268), because the
one fault that hid for hours (#266) hid as a *missing* line. Three lines,
each written once, under the kind `watchdog`:

- `slow chunk: connecting a..b has run 61s against a budget of 50s, at
  Height h` — a chunk of the Set walk past five times the driver's chunk
  budget, said from inside the walk while it is still running; and `slow
  chunk done: … reached r after 75s` when it ends.
- `slow block: Height h <id> took 45s, n Transaction(s), b bytes` — one
  Block over thirty seconds, said after it, because a Block is one call.
- `slow stop: 480s from the stop being asked to the loop leaving` — a `q`
  or a SIGINT that took more than ten seconds to land, said when it does.

Reporting only: nothing is killed or dropped on an overrun. A healthy run
writes none of them. The budgets are [Domain.Watchdog](domain/watchdog.av);
the clocks ride on the walk's Eye.

### Reading it from a browser: `http`

`http` binds port 8330 (`http:PORT` another) and answers `GET /` with the
five Panels — Overview, Peers, Blocks, Transactions, Mempool — as one plain
HTML page, each Panel a heading over the same lines the Screen would draw,
so a phone can read what a tmux pane shows. Nothing on it moves: the page is
the Snapshot as it stood when the request arrived, and you press refresh to
ask again. Any other path is a 404, anything that is not a GET a 400, and
every answer closes the connection. It is the loop's own turn that answers,
once a second -- from inside the Set walk too, through the Eye every walk
carries, so a page asked for during a catch-up is a second away rather than
a chunk away -- and it never waits: the
listener is asked with no timeout, a Reader is given a tenth of a second to
have sent its request line and is closed unanswered if it has not, and at
most four are answered a turn. Two nodes on one machine name different
ports — the server runs mainnet on 8330 and signet on 8331.

```bash
./target/release/main follow ~/chain serve log http          # page on :8330
./target/release/main signet follow ~/signet serve log http:8331
curl -s localhost:8330/
```

The request line is read and the page is built in pure
[Domain.Page](domain/page.av); [Infra.Board](infra/board.av) is the socket
work (#261).

### Watching it: `screen` and `log`

`screen` puts a live terminal Screen up once the node is caught up: an
Overview, and `p` `b` `t` `m` switch to the Peers, Blocks, Transactions and
Mempool Panels, `o` back, `q` quits after confirming. Every character of it
is computed in pure [Domain.Screen](domain/screen.av) from a
[Snapshot](domain/snapshot.av) the loop folds up each tick; only the drawing
touches a terminal.

A Screen run leaves nothing behind, so `log` writes what it would have said
where it survives: `<dir>/metrics.log` (`log:PATH` puts it elsewhere) gets one
line a minute and one at every phase boundary — timestamp, phase, Height,
target, Blocks, bytes, milliseconds inside `Tcp.poll` and outside it, Peers,
Candidates — in a fixed order that `awk` reads without a parser. The same word
turns on `<dir>/debug.log`, one line per *decision* — a phase started, a Peer
seated or dropped and why, a fault and what it was charged to — so a run that
ends under the Screen can be read afterwards rather than re-run in plain mode
to see what stopped it ([#218](https://github.com/n1bor/btc-listener/issues/218)).

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

## Running it on a server

A download measured in days belongs on a machine of its own, and that machine
does not need a toolchain. It needs one executable and a peer.

### The binary

Every push to `main` that gets past `format`, `check`, both `verify` jobs, the
provider tests, the wasm-gc host and the build publishes what it just proved to
the
[`main-build`](https://github.com/n1bor/btc-listener/releases/tag/main-build)
release, replacing what was there. The repository is public, so this needs no
credentials:

```bash
curl -sL https://github.com/n1bor/btc-listener/releases/download/main-build/main -o main
curl -sL https://github.com/n1bor/btc-listener/releases/download/main-build/SHA256SUMS -o SHA256SUMS
sha256sum -c --ignore-missing SHA256SUMS
chmod +x main && ./main help
```

### The same program as wasm-gc

The same release carries `main.wasm`: the whole listener compiled to
WebAssembly, and **the exact module CI ran a Peer handshake through** rather
than a second build of it.

```bash
curl -sL https://github.com/n1bor/btc-listener/releases/download/main-build/main.wasm -o main.wasm
```

It is not a second way to run a node. A wasm module has no capabilities of its
own, so it needs a host to supply them; [`wasm/host.mjs`](wasm/host.mjs) is
one, on Node 26 — or Node 24 with `--experimental-wasm-jspi`, because it needs
JSPI for `Tcp.poll` to suspend.

```bash
node wasm/host.mjs main.wasm                                  # self-test against a fake Peer
node wasm/host.mjs main.wasm regtest 127.0.0.1 18444          # a real one
node wasm/host.mjs main.wasm regtest headers 127.0.0.1:18444 chain-a
```

**That host sandboxes `Disk` to a temporary directory and deletes it on exit**,
so directories must be relative and nothing survives the run — an absolute path
is refused by name (`Disk path escapes host sandbox`). It is a conformance
harness: proof the same source runs on a second backend, not somewhere to keep
a chain. The binary above is what a server wants.

`--ignore-missing` because `SHA256SUMS` covers both the binary and
`main.wasm`, and most people want only one of them; without it, `sha256sum`
fails on the file you deliberately did not fetch.

**Check the hash rather than skipping it.** Nothing inside the binary says
which commit produced it; `SHA256SUMS` and the release title are the only
provenance there is, and three days into a download is exactly when someone
asks which build wrote the directory.

Picking up a later build is the same two `curl`s. There is no upgrade path for
a chain directory and deliberately is not one — a format change means
downloading again, which is what [#48](https://github.com/n1bor/btc-listener/issues/48)
is.

### What it runs on

18 MB, x86-64, dynamically linked against four libraries that are stock on any
Ubuntu:

```
libstdc++.so.6   libgcc_s.so.1   libm.so.6   libc.so.6
```

RocksDB and libsecp256k1 are C and C++ compiled *into* it rather than linked at
run time; `libstdc++` appears at all only because RocksDB is C++.

The floor is glibc, and the compile job builds on `ubuntu-22.04` rather than on
`ubuntu-latest` precisely so that floor stays low -- the release publishes the
binary that job built, on the same runner image, after running its `help` on
it (#219) -- because a 24.04 runner would raise
it above 22.04 servers without anything saying so. **Ubuntu 22.04 or newer**,
therefore — glibc is backward compatible, so newer is always safe, and the CI
log prints the required version on every publish rather than leaving it to be
discovered.

Building on the server instead is the [Requirements](#requirements) above, and
is worth it only if the machine is not x86-64, is older than 22.04, or is going
to be developed on.

### Prove the machine before trusting it with four days

Nothing is compiled there, so there is no toolchain to check. What is worth
checking is this binary, on this machine, against a real node — which is
[`docs/regtest-testing.md`](docs/regtest-testing.md), sections 1 to 4 — and 5
to 8 if the change reaches the Mempool, relay or compact Blocks. A few
minutes, and it needs Bitcoin Core, `python3`, and the repository for
`tools/regtest/`. It exercises the reorganisation path, which a download will
never tell you is broken.

### File descriptors

RocksDB keeps many SSTs open and Ubuntu's default is 1024:

```bash
printf '* soft nofile 65535\n* hard nofile 65535\n' | sudo tee -a /etc/security/limits.conf
```

Log out and back in for it to apply.

### Choosing a peer

`headers` and `bodies` want an address; only the bare listener asks a DNS seed
for one. Two things matter:

- **Archival, not pruned.** A pruned peer will not serve old Blocks, and what
  that looks like is a download that stalls rather than one that says why.
- **Prove it on a small range first**, before committing days to it:

```bash
./main headers <peer> ~/chains/mainnet
./main bodies  <peer> ~/chains/mainnet 1 500
```

Running Bitcoin Core on the same server is the more debuggable option and the
more expensive one: a non-pruned mainnet Core is its own full initial sync,
larger than everything else on the machine put together.

### Long runs

- **Use `tmux`.** These outlive an SSH session, and a disconnect that kills a
  three-day `bodies` is an avoidable way to lose three days.
- **One command per directory.** The database takes an exclusive lock, so a
  second command against the same directory fails rather than waits.
- **Everything resumes**, and stopping costs at most the batch in flight. A
  `utxo` run killed at Height 5,934 resumed at 5,935: the Set records where it
  stands durably, so a kill is not a rollback.
- **A run that dies leaves `<dir>/.writing`** behind — the claim from
  `infra/lock.av` — and the next command refuses to start rather than write
  into a directory something else may still hold. Delete it only once you have
  confirmed nothing is actually running.
- **Measure a rate over a window before believing an ETA.** The first minute of
  a `bodies` run reads far slower than the steady state, because it covers the
  connect and the handshake: 0.6 Blocks/s against a steady 5.9 on signet, which
  is the difference between a 28-minute estimate and a false 4.6-hour one.

Sizing the disk for a particular chain, and the two figures that are still
arithmetic rather than measurements, are in
[#138](https://github.com/n1bor/btc-listener/issues/138).

## The Index and its backends

The Index is a keyed store — Block Ids to Locations, Heights to Block Ids,
Transaction Ids to sites — and `infra/store.av` offers it over two backends
behind one API. Callers cannot tell them apart: `Store` is opaque, so the same
`get`, `getAll`, `putAll`, `applyAll` and `deleteAll` reach whichever is there.

| backend | what it is | where it comes from |
|---|---|---|
| Memory | a `Map`, holding exactly what it was handed | `Store.fixture`, used by the verify cases |
| Database | a RocksDB, read a key at a time | a directory's `kv/`, made on first open |

There was a third: Logged, an append-only text file replayed into memory on
every open — the format that came before the database, kept so the one-shot
`migrate` command had something to move from. Both were retired together
(#44): a text log cannot hold the binary keys the Index is moving to, and a
directory is rebuilt from the network rather than migrated. A directory still
holding an `index.log` and no `kv/` is refused by name, the same way a
directory of hex Segments is.

### Recovering a lost Index

The Index is derived and the Segments are the source. On 23 August 2026 a hard
crash mid-`outputs` left 57 GB of intact Segments beside a database that would
not open — `Corruption: missing live files`, because the rusty-leveldb of the
day never fsynced ([#92](https://github.com/n1bor/btc-listener/issues/92); the
RocksDB that replaced it syncs every batch, [ADR 0009](docs/adr/0009-rocksdb-under-the-index.md)). `reindex`
exists so that costs minutes rather than a download
([#93](https://github.com/n1bor/btc-listener/issues/93)):

```bash
mv ~/chain/kv ~/chain/kv-lost            # or delete it; a fresh database is made on open
rm -f ~/chain/index.log                  # a log beside no database is refused
./target/release/main headers 192.168.1.10 ~/chain     # h:  Height → Block Id, from a Peer
./target/release/main reindex ~/chain                  # b:  Block Id → Location, from the Segments
./target/release/main txindex ~/chain 1 400000         # t:, n:  as before
./target/release/main outputs ~/chain 1 400000         # o:      as before
```

`reindex` walks every Segment record to record, reads the 80-byte Header that
opens each payload, hashes it, and writes `b:<Block Id> → segment:offset:length`.
It never reads a Block body, so 400,000 Blocks is 400,000 reads of 84 bytes.
It rebuilds only `b:` — Heights are `headers`' job and a Peer's answer, and
deriving them here by chaining `previousBlockId` would make `reindex` decide
which chain the directory holds. It locates bytes and hashes them, and decides
nothing.

```
400000 Blocks located across 454 Segments
```

Memory is the point, not speed. 615 MB is the whole Index held open regardless
of what is being read; 67 MB is what the audit itself needs. The sharpest
difference is on the small commands: `tx` does one lookup and exits, and goes
from 4.5 s and 667 MB to 0.04 s and 19.8 MB, because opening stopped costing
anything. The second audit range is 11% slower, which is what a lookup costs
once it stops being free. What this buys is a ceiling set by the disk rather
than by RAM, which is what an output keyspace at two hundred million entries
needs. The full table is in
[ADR 0006](docs/adr/0006-a-leveldb-under-the-index.md); the log that ADR kept
alongside has since gone (#44), and the LevelDB it chose became RocksDB
([ADR 0009](docs/adr/0009-rocksdb-under-the-index.md)).

## Checking the code

```bash
aver audit   .                          # check + verify + format, the CI gate
aver check   . --module-root .          # contracts, coverage, lints
aver verify  main.av --module-root .    # the program graph: every hand-written case, in seconds
aver verify  corpus --module-root .     # the Core corpus, on its own
aver verify  . --module-root .          # both, which is what audit runs
aver format  . --check                  # formatting
aver compile main.av --module-root . -o ../btc-listener-build --check   # and cargo check what it emitted
```

0 check errors, 0 format issues, about **5,500 hand-written verify cases** on
the program graph and **6,050** more in the Core corpus. Everything except the
socket is pure and covered.

The corpus lives in its own directory and CI runs it as its own job, in four
shards, because it is 6,050 cases with 200 M-step budgets that change only
when Core's data does ([#219](https://github.com/n1bor/btc-listener/issues/219));
`aver verify main.av` is what to run after every change and takes seconds.
`aver compile --check` is the gate that sees what `check`, `verify` and a
plain `compile` cannot — a program that verifies and will not build
([jasisz/aver#1172](https://github.com/jasisz/aver/issues/1172)); it runs
`cargo check` on the emitted Rust, seconds once the target is warm.

The provider host is built and installed automatically: the verify cases that
reach RIPEMD-160, a signature check or the database run the real
implementations through the providers, never a fixture — a suite that quietly
substituted a stub for the curve would report passes it had not earned. The
first `verify` after a toolchain or provider change rebuilds that host (RocksDB
included, several minutes); after that the whole suite runs in seconds —
[jasisz/aver#1095](https://github.com/jasisz/aver/issues/1095) took it from
eight to fifteen minutes down to about four seconds, and `-j N` picks the
worker count.

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

141 Aver modules — 79 in `domain/`, 26 in `infra/`, 6 in `app/`, 29 generated
in `corpus/`, and `main.av`. Grouped by what they are for rather than listed;
[docs/architecture.md](docs/architecture.md) walks the same ground with
diagrams.

```
CONTEXT.md          glossary — the vocabulary this project commits to
CLAUDE.md           how to write Aver here, the gates, what only Cargo finds
aver.toml           the provider bindings — see Providers
.aver-version       the Aver commit CI builds — see Moving the Aver pin
docs/architecture.md          how it is put together, and why
docs/aver-vs-other-languages.md  what the language cost and bought
docs/adr/           architecture decisions, nine of them
docs/full-node-plan.md  the stages, all shipped, and what each asked of Aver
docs/regtest-testing.md the end-to-end test against a real Core node
docs/core-corpora.md    which of Core's test files are read, and how
tools/              the generators that turn Core's test vectors into corpus/,
                    refresh_corpora.sh, and regtest/ (liar.py, the Peer that lies)
providers/          kv (RocksDB) and primitives (libsecp256k1, RIPEMD-160, SHA-1)
wasm/               host.mjs, the Node wasm-gc host CI runs a handshake through
main.av             argv entrypoint, deliberately thin

app/                cli.av argument handling, usage.av the help text,
                    show.av / lookup.av / maintain.av one per group of
                    commands, node.av the commands that never stop

domain/  the wire
  address.av network.av message.av version.av inventory.av dns.av
  transaction.av    the SegWit-aware decoder
  inbox.av          what a Peer has said that has not been read yet
  addr.av           addr and getaddr, and what an entry is worth
  addressbook.av    Candidates: Peer Addresses heard about, not yet Peers
  compactblock.av   BIP152, rebuilt from the Mempool
  siphash.av        the short Ids' hash
  compactsize.av hash.av text.av json.av

domain/  addresses
  script.av         recognising output scripts, naming who they pay
  readaddress.av payto.av base58.av bech32.av bech32decode.av

domain/  the chain
  block.av          Block Headers: reading, naming, asking for more
  headertree.av     every Header seen, and which Branch has the most work
  chainwork.av blockwork.av reorg.av rewind.av
  headerbytes.av treestore.av index.av indexkeys.av segment.av
  checks.av rules.av locktime.av spend.av txcheck.av

domain/  the UTXO Set
  connect.av        a Block into the Set: spends out, Outputs in, fees
  disconnect.av     a Block back out of it, from its Undo Data
  utxostore.av subsidy.av assumevalid.av

domain/  the Mempool
  mempool.av        what is held, what conflicts, what leaves when
  standard.av       Policy: what this node will hold and relay
  policy.av

domain/  Script
  opcode.av scriptparse.av stackitem.av scriptstate.av scriptmath.av
  scriptnumops.av scriptstep.av scriptops.av scriptwork.av
  interp.av         the walk: one recursion over a Script, and only one
  spendscript.av    the Input's Script then the Output's, in that order
  spendcontext.av   what a signature commits to, carried to the opcode
  witness.av witnessuse.av taproot.av tapsig.av tagged.av
  sighash.av bip143.av bip341.av  what a signature is actually over
  ecdsa.av schnorr.av checksig.av multisig.av checkwork.av
  primitives.av     the seam the curve, RIPEMD-160 and SHA-1 plug into
  scriptassets.av assetsweep.av txcase.av  readers for the generated corpus

domain/  the Screen
  snapshot.av       one picture of the node, folded up each tick
  screen.av         every character and key of the Screen, no terminal
  screenlines.av

corpus/  Core's own vectors, generated
  scriptcases1-5 witnesscases1-3 txcases1-4 sighashcases1-2 assetcases1-9
  keyiocases keyioinvalidcases base58cases bip341cases siphashcases
  compactblockcases

infra/  the network
  peers.av          several Peers on one loop: sockets, Handshakes, readiness,
                    dials, the listener and inbound Peers
  peer.av           the listen command: one Peer, its Transactions printed
  download.av       the Header phase
  bodies.av         the body phase
  follow.av         the node that stays on the tip
  mempool.av        admission: the questions, in cost order
  resolver.av       the DNS seeds

infra/  the disk
  store.av          keyed store over two backends, one opaque API
  kv.av             the key-value database capability contract
  reindex.av        every Block's Location, rebuilt from the Segments
  headers.av        the Header tree, written down
  chainstate.av     the UTXO Set, built forward
  rewind.av         the UTXO Set, taken back to a fork
  utxo.av blocks.av chain.av txindex.av outputs.av lock.av prune.av
  spends.av audit.av pace.av

infra/  the terminal and the record
  screen.av         the one piece of the Screen that touches a terminal
  metrics.av        metrics.log, one line a minute
  debug.av          debug.log, one line per decision
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

The listener reports no fees. A Transaction states its output amounts but not
its input amounts, and a peer will not serve the confirmed transactions those
inputs spend — measured on mainnet, 29 of 29 such requests came back
`notfound`; see [ADR 0002](docs/adr/0002-no-fees-over-p2p.md). A directory
that holds the UTXO Set can answer the question the wire cannot, which is why
`spend`, `utxo` and `follow` do report fees and the listener still does not.

What this engine once lacked — RIPEMD-160, SHA-1, the curve, Schnorr, a
witness evaluator, an output index — has all arrived: the first four as a
**capability provider** ([Providers](#providers)), the evaluator as
`domain/witness.av` and `domain/taproot.av`, the index as `outputs` and the
Set. What is deliberately not here is listed where it is deferred: BIP125
replacement, the orphan pool and package relay
([#28](https://github.com/n1bor/btc-listener/issues/28)), Address Book
bucketing against eclipse ([#118](https://github.com/n1bor/btc-listener/issues/118)),
IPv6, and the signet challenge. The seam that keeps the list honest is
`domain/ecdsa.av`, which has no `Valid` constructor for anything it cannot
decide, so the compiler names every caller when a capability arrives.

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

### The same contracts through a Node wasm-gc host

[`wasm/host.mjs`](wasm/host.mjs) is a lightweight second host for the complete
application. It instantiates the `main.av` wasm-gc artifact, supplies the
standard capabilities through Node, and binds both project contracts through
Aver's public custom-capability ABI. `Disk` is confined to a fresh temporary
directory, `Infra.Kv` uses an in-memory Map, and `Tcp` implements the full
listener/dial/connection reactor over Node sockets and JSPI. The ABI carries
`Bytes`, `Result`, `Option`, lists, tuples and opaque resources without JSON.

CI starts a local regtest Bitcoin Peer and invokes the real CLI as
`regtest 127.0.0.1 <port>`. The actual program performs its non-blocking dial,
`Tcp.poll` loop and `version`/`verack` handshake. The Peer then sends a `ping`
whose checksum is deliberately wrong; the test succeeds only after the real
framing and inbox code diagnoses it, drops the Peer, closes the connection and
returns through `main`. This exercises the application graph rather than a
purpose-built smoke guest. Node 26 is used because that is the first Node line
this host supports without experimental JSPI flags.

```bash
aver compile main.av --module-root . --target wasm-gc -o /tmp/btc-listener-wasm
npm ci --prefix wasm
npm --prefix wasm run test:node -- /tmp/btc-listener-wasm/main.wasm
```

The final line prints `full btc-listener listener path: ok (...)`. This proves
the complete listener path through the real application artifact, not parity
with the native deployment or every CLI command. Its KV token owns an in-memory
Map and its Disk root is deleted after each run, whereas the native production
provider below owns durable RocksDB and ordinary files. The host does use real
RIPEMD-160, SHA-1 and secp256k1 implementations (`node:crypto` and
`@noble/curves`), rather than canned provider answers.

For the byte-pipeline regression workload, set
`BTC_LISTENER_FAKE_FRAME_BYTES=3500000` on the final command. The fake Peer then
sends one large, deliberately corrupt `ping` frame through the same TCP,
framing, hashing and teardown path, and the host prints the transferred byte
counts and elapsed time.

The curve is a provider on purpose. RIPEMD-160 was written in Aver first and
passed all eight published vectors, but a curve is not a hash: 256 bits of field
arithmetic whose edge cases *are* consensus rules, where a wrong answer is a
false audit rather than a slow one. The provider hands the question to
`libsecp256k1`, which is what Bitcoin Core itself runs. RIPEMD-160 then followed
the curve behind the same contract, so there is one boundary rather than two.

`Infra.Kv` is the second contract and the newer one. The Store that came
before it kept the whole index in a Map, which cannot be stretched to the two
hundred million entries an output keyspace would need. That is a database, and Aver has not got
one. Unlike the primitives it is **effectful**, so every operation declares an
Oracle dimension and what a replay does with it. The provider is RocksDB,
through the `rocksdb` crate, and every batch is written with `sync = true` so
a `putAll` returns only once the write-ahead log is on the disk. It was
`rusty-leveldb`, a pure-Rust port chosen because it needed no C++ toolchain,
until two defects in the port's own code — a table cache that panicked on
eviction ([#33](https://github.com/n1bor/btc-listener/issues/33)) and no
`fsync` anywhere ([#92](https://github.com/n1bor/btc-listener/issues/92)) —
called the provision in;
[ADR 0009](docs/adr/0009-rocksdb-under-the-index.md). The swap changed one
Rust file and no Aver at all, which is what the contract was for. Building it
needs `clang` and `libclang-dev`, and the first build compiles RocksDB from
source.

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
closed, `aver audit .` runs the whole project against the real
implementations, and the stub table is deleted. Every expectation the fixtures
had been standing in for holds against the real thing.

What remains:

- The implementations are still tested where they live, in the providers' own
  Rust tests: the eight published RIPEMD-160 vectors and the first spend
  Bitcoin ever made; and for the database, round trip, overwrite, delete,
  reopen, prefix order, a handle it never issued, and a batch whose write-ahead
  record is deliberately torn, which comes back absent rather than half applied.
- **Nothing runs without the providers**, and that is the safe failure: an
  interpreter running the audit with no crypto would report passes it had not
  earned. Aver builds a thin Rust host from the `[providers]` composition in
  `aver.toml`, caches it, and runs the ordinary VM with the binding installed —
  automatically, since 0.29; `--providers` is gone.

  ```bash
  aver run main.av --module-root . -- audit chain 1 2000
  ```

`aver check`, `aver format` and `aver capabilities` need no provider. `aver
verify`, `aver run` and `aver audit` build one.

## Moving the Aver pin

Aver is a moving target and this project is one of its driving projects, so
fixes we ask for land often. The toolchain CI tests with is whatever commit
[`.aver-version`](.aver-version) names, and a developer machine's `aver` is
whatever `../aver` was at when it was last `cargo install`ed — `aver
--version` cannot tell you which. Moving forward is a routine, and the
[canary workflow](.github/workflows/canary.yml) runs the cheap gates against
upstream's tip nightly so the routine is rarely a surprise:

```bash
cd ../aver && git fetch upstream && git log --oneline $(cat ../btc-listener/.aver-version)..upstream/main
git merge --ff-only upstream/main && cargo install --path . aver-lang --locked
cd ../btc-listener && git -C ../aver rev-parse HEAD > .aver-version
aver check . --module-root . && aver verify . --module-root . && aver compile main.av --module-root . -o ../btc-listener-build --check
cargo build --release --manifest-path ../btc-listener-build/Cargo.toml
```

Then, before opening the PR, **look for workarounds the move retires**:

```bash
grep -rhoE "jasisz/aver#[0-9]+" --include="*.av" --include="*.md" --include="*.yml" . | sort -u
```

**Check the sentence, not just the number.** That list tells you which issues
are cited; it does not tell you what the prose around them claims. A paragraph
saying an issue is *still open* passes the grep looking exactly like one saying
it closed, and two of them in `docs/adr/0003` went six days out of date that
way. Grep the claim as well:

```bash
grep -rniE "(still |remains? |is )(open|outstanding|unfixed|pending|blocked)" --include="*.md" --include="*.av" .
```

Every upstream issue this repository cites is either history (a docstring
saying what was once worked around and why the shape survived on merit) or a
live workaround — a filter, a flag, a module kept whole, a cache oversized.
Check each issue's state; a closed one with a live workaround means code or
documentation to simplify, and that belongs in the same PR as the pin. The PR
is what proves the pin: every CI job builds the named commit from source.
