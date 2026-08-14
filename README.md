# Bitcoin Peer Listener

Connects to a single Bitcoin node over the peer-to-peer protocol, listens for
transaction announcements, and prints each transaction's decoded structure.

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
aver run main.av --module-root . -- [peer-address] [port]
```

With no address, the listener asks one of Bitcoin's DNS seeds for reachable
peers and connects to one of them at random. The port defaults to 8333.

```bash
aver run main.av --module-root .                        # find a peer via DNS
aver run main.av --module-root . -- 192.168.1.10        # named peer
aver run main.av --module-root . -- 192.168.1.10 8333   # explicit port
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

It will also stop if a read fails. Aver's TCP read deadline is a hardcoded 30
seconds ([jasisz/aver#782](https://github.com/jasisz/aver/issues/782)), so a
peer that says nothing for that long ends the session — unlikely on mainnet,
where announcements arrive constantly, but worth knowing.

`aver run` prints a warning that verify blocks in dependency modules are not
sampled. That is expected — `aver verify --deps` is what checks them.

### Downloading the chain

Two further subcommands fetch the chain rather than listen to it. **Both must be
compiled** — building a `Map` is quadratic under `aver run`
([jasisz/aver#900](https://github.com/jasisz/aver/issues/900)), so an
interpreted Index stops making progress at any real size. See
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

### Looking at one Block

`show` reads a Block back off disk, checks it, and prints its Transactions the
same way the listener prints loose ones. It needs no Peer.

```bash
./target/release/main show ~/chain 170
height 170
block  00000000d1145790a8694403d4063f323d499e655c83426834d4ce2f8dd4a2ee
body   segment 0 line 5, 490 bytes
check  header hashes to the recorded Block Id
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
txs    3721
```

`full` is the default and says so explicitly; anything else prints the usage.

**That took six and a half minutes.** `summary` saves the printing, not the work:
the count means every Transaction has already been decoded. Fetching that same
Block over the network took three and a half seconds, so reading it back is a
hundredfold slower than downloading it. Early Blocks are small enough that this
does not show — Block 170 returns instantly — and it is worth knowing before
reaching for `show` on anything modern.

That last line is the only thing that tests the whole round trip inside Aver:
the Header was decoded from the wire, the bytes written to a Segment, the
Location recorded against the Block Id, and hashing what comes back has to
produce the Block Id the Index was asked for. Flip one hexadecimal digit in a
Segment file and it says so:

```
check  MISMATCH: bytes on disk hash to 487a8a8c80feb555efbe5b6fc63884c4…
```

A body that is absent is reported as one of two different things, because they
are two different situations:

```
body   not fetched                                    # never asked for
body   discarded by pruning, Prune Watermark 100      # deliberately deleted
```

### Reclaiming space

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

### Running under WSL

If the node is on the Windows host, `127.0.0.1` inside WSL will not reach it —
WSL2 has its own network namespace. Use the gateway address:

```bash
aver run main.av --module-root . -- $(ip route show default | awk '{print $3}')
```

That address is reassigned when Windows reboots, so resolve it rather than
writing it down.

## Checking

```bash
aver check  . --module-root . --deps    # contracts, coverage, lints
aver verify . --module-root . --deps    # every verify block
```

Everything except the socket is pure and covered. The wire format is pinned
against values derived independently rather than captured from this
implementation: a `verack` and a `ping` frame, a full `version` payload, the
genesis coinbase transaction id (which is published), and a SegWit transaction
id computed from the specification.

## Layout

```
CONTEXT.md          glossary — the vocabulary this project commits to
aver.toml           two lint suppressions, each with its reasoning
docs/adr/           architecture decisions
main.av             argv entrypoint, deliberately thin
app/cli.av          argument handling
domain/
  address.av        opaque PeerAddress; parsing is the only way in
  network.av        mainnet/testnet/regtest, and their magic bytes
  message.av        framing: magic, command, length, checksum
  version.av        the version payload, built purely
  inventory.av      reading announcements, building getdata
  transaction.av    the SegWit-aware decoder
  dns.av            DNS questions and answers, for seed lookups
  script.av         recognising output scripts, naming who they pay
  base58.av         Base58Check, for pre-SegWit addresses
  bech32.av         Bech32 and Bech32m, for SegWit addresses
infra/
  peer.av           the Peer session: handshake and listen loop
  resolver.av       seed lookups over DNS
```

The split is deliberate: only `infra/` touches the network, and everything it
does is an arrangement of pure parts from `domain/`. That is why
the wire format can be tested without a peer, and why a failure against a live
node is a socket problem rather than an ambiguity.

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

Deriving an address needs no RIPEMD-160, which Aver does not have: a script
already contains the hash, so the work is pattern-matching plus an encoding, not
hashing a public key.

Fees are deliberately not reported. A Transaction states its output amounts but
not its input amounts, and a peer will not serve the confirmed transactions
those inputs spend — measured on mainnet, 29 of 29 such requests came back
`notfound`, and across 441 inputs none had its parent in the same stream.
Getting fees means asking the node over RPC instead, which is a different
program; see [ADR 0002](docs/adr/0002-no-fees-over-p2p.md).

## Licence

MIT — see [LICENSE](LICENSE).
