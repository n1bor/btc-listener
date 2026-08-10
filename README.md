# Bitcoin Peer Listener

Connects to a single Bitcoin node over the peer-to-peer protocol, listens for
transaction announcements, and prints each transaction's decoded structure.

Written in [Aver](https://averlang.dev).

```
listening to peer 172.26.224.1:8333
handshake complete: protocol 70016, agent /Satoshi:27.0.0/
tx 3a38d7b2bcb0c17f0210979756b2a04aab364532e429152b20ad8ab8b7c05e84
   segwit, version 2, 2 in / 2 out, 371 bytes, locktime 0
   in  386aa7568db84f77398a4635e332557b30f18661cbc4302a3a1f5fbc5bec6f68:0 seq 4294967293 witness 2
   in  3af60d27c78ab9b8edb15372a15a1ebb43e368f1a8a718828046e9a9691dca84:3 seq 4294967293 witness 2
   out 0.00000606 BTC  0014cdd11abc6b62fb60ae88132c70570820f2ae8949
   out 0.00841400 BTC  0014e8d3e213ee0e2c02036e069cb6dc59a0300a5c1f
```

## Requirements

- **Aver 0.28.0 or later.** Earlier versions cannot do this at all: byte-clean
  TCP (`Tcp.sendBytes`, `Tcp.readBytes`, `Tcp.writeBytes`) and `Crypto.sha256`
  all landed in 0.28.0 "Oktet".
- A reachable Bitcoin node. Any peer will do; one you run yourself is easier to
  debug against.

## Running

```bash
cd btc-listener
aver run main.av --module-root . -- <peer-address> [port]
```

The port defaults to 8333.

```bash
aver run main.av --module-root .                        # usage
aver run main.av --module-root . -- 192.168.1.10        # default port
aver run main.av --module-root . -- 192.168.1.10 8333   # explicit port
```

Two things are easy to leave off:

- **`--module-root .`** — without it the `depends [...]` declarations cannot
  resolve, and every module fails to load.
- **`--`** — everything after it becomes `Args.get()`. Without it, `aver`
  consumes the address itself.

The program connects, completes the handshake, prints five transactions, and
exits. That count is `wantedTransactions()` in `app/cli.av`. The first
transaction can take anywhere from a second to a minute depending on how busy
the peer's mempool is.

`aver run` prints a warning that verify blocks in dependency modules are not
sampled. That is expected — `aver verify --deps` is what checks them.

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
infra/peer.av       the only module that touches the network
```

The split is deliberate: `infra/peer.av` is the sole holder of effects, and
everything it does is an arrangement of pure parts from `domain/`. That is why
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
- **`ping` must be answered.** A peer that is not answered drops the connection
  after a couple of minutes, which presents as "it stopped working after a
  while".

## Scope

Decoding covers the transaction structure, SegWit included: version, inputs
with their outpoints and sequences, outputs with amounts and scripts, witness
item counts, locktime, size, and the transaction id.

Not covered: script classification and addresses (scripts print as hex), fees
(which would need the spent outputs, and so a UTXO cache or extra round trips),
and running until interrupted rather than stopping after a fixed count.
