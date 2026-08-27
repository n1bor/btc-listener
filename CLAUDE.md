# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Bitcoin P2P listener, chain downloader and auditor written in **Aver** — and a
driving project for the Aver language itself, which is being developed in the
sibling repo `../aver`. Friction found here regularly becomes an Aver issue or
PR; drafts of those live in `../test` (ISSUE-*.md / PR-*.md). Compiled output
goes to `../btc-listener-build`.

## Writing Aver

Aver is not a language you already know. Before writing any `.av` code, read
`../aver/llms.txt` — it is the curated rule sheet for exactly this situation
(single-line match arms, qualified constructors like `Result.Ok`, no `if`/`else`,
no lambdas, no type parameters, `verify` blocks colocated with pure functions,
explicit effect lists on both the module and each effectful function).

**An effect list has to be exact, not merely sufficient.** Under-declaring has
always been an error; since the `8a45a945` pin, declaring an effect the
function does not use is a `warning[unused-effect]` naming the correct set in
its `used:` clause, so `aver check` will tell you what the list should say
rather than only that it is wrong. Adding a genuinely new effect propagates one
function at a time up every caller and then through the module boundary, which
fails separately — loop `aver check` until it is quiet rather than trying to
predict the fan-out. n1bor/btc-listener#178 narrowed 58 lists this way and
every one of them was a copy-paste of a caller's list.
`../aver/docs/` has the deep dives (`language.md`, `types.md`, `effects.md`,
`cli.md`). The `aver` on PATH is `cargo install`ed from `../aver`, so it is
whatever that checkout was at when it was last installed — and **`aver
--version` cannot tell you which**, because the version string does not move
between releases. CI builds the commit named in `.aver-version`. When
something verifies and will not compile, or the VM and the compiled binary
disagree, check `git log upstream/main` before believing it is a live bug.
**Moving the pin is a routine** — README "Moving the Aver pin": pull
`../aver`, reinstall, write the SHA to `.aver-version`, run the gates, then
grep the repo for `jasisz/aver#` and retire every workaround whose issue has
closed, in the same PR. **But read each citation before retiring**: a line
recording *where something came from* looks identical to one recording *what
is worked around*, and only the second retires with its issue —
`connect_timeout_secs` in `aver.toml` cites #1118 and #1125, both now closed,
and stays: it is a dial's deadline, which the dial still needs now that the
dial is a key in `Tcp.poll` rather than a five-second stall.

**Test against a real node before you commit.** `aver verify` checks this
program against fixtures its own authors wrote, and a fixture cannot disagree
with the assumption that produced it. `docs/regtest-testing.md` is the standing
end-to-end test on a local regtest node: every command in order, real spending
Transactions across all four address types, a reorganisation summoned with
`invalidateblock`, and a Block-Id comparison against Core itself. It takes a few
minutes and it is the only evidence here that comes from outside the project. A
change that passes every gate and breaks the reorganisation path is a change
that passes every gate. **When you test something that document does not cover,
add it there** — the coverage only grows if each person leaves behind what they
had to work out.

**Failures that only `cargo build` finds.** All survive `check`, `verify`
and `compile`.

- ~~**A binding whose name two depended-on modules both expose** is ambiguous
  in the emitted Rust (`E0659`)~~ — **fixed upstream, both forms.** Match
  binders went with jasisz/aver#1043 and `let` bindings with jasisz/aver#1152,
  which closed the issue this project filed as jasisz/aver#1151. The pin from
  `f6d7992c` carries it, and the reproducer that failed on `794f4af3` now
  builds clean. It cost this project four renames in a day, so it is left here
  struck through rather than deleted: if a binding ever goes ambiguous again,
  this is what it was and `cargo build` is still the only gate that sees it.
- A type named after a builtin is silently resolved to the builtin
  (`Connection` against `Tcp.Connection`, #25). And **`aver check` passing is
  not proof a module compiles** — only reachability from `main.av` is, so
  wiring a new module into the CLI is part of writing it, not a step after.
- A record used as a `Map` value has `Eq` and `Hash` derived for it, and every
  field must satisfy them — so no opaque record (`PeerAddress`) and no record
  that lacks the derives (`Frame`) can live inside one. #27 lost a per-Peer
  Frame queue to this and was better for it.
- Every module in a `depends` list is glob-imported into the generated Rust,
  and two of them defining the same name is harmless — this repo has 157 such
  pairs. It **used** to become `E0659` the moment a parameter or binding took
  that name (`Infra.Rewind.standing` against `Domain.AssumeValid.standing`
  through `Infra.ChainState`, #26); the entry above is why it no longer does.
  **A function parameter is the form still live.** `Domain.Addr` exposing a
  `payload` beside `Domain.Version`'s, with `Infra.Follow` depending on both
  and having a parameter called `payload`, is `E0659` on a pin that carries
  #1043 and #1152 — those fixed match binders and `let` bindings, and a
  parameter is neither. `aver check`, `verify` and `compile` are all quiet.
  Filed as jasisz/aver#1162, still live on `6afb0ff3`. The workaround is a
  rename: the encoder is `Domain.Addr.offering`.

## Commands

```bash
aver audit  .                                       # check + verify + format, the CI gate
aver check  . --module-root .                        # contracts, coverage, lints
aver verify . --module-root .                        # all ~6,000 cases, parallel, ~seconds once the host is cached
aver verify domain/script.av --module-root .         # one file's cases
aver format . --check
```

Run interpreted (the provider host is built automatically now; plain
`aver run` cannot start this program):

```bash
aver run main.av --module-root . -- [args...]
```

Compile (what the long-running commands should use — several times faster):

```bash
aver compile main.av --module-root . -o ../btc-listener-build
cd ../btc-listener-build && cargo build --release
./target/release/main [args...]
```

Three flags are load-bearing and easy to drop:

- `--module-root .` — without it no `depends [...]` resolves.
- `--` before program args under `aver run` — without it `aver` eats them.
- The provider host is no longer asked for. `--providers` is gone from
  `verify`, `audit` and `run` as of aver 0.29: the bindings in `aver.toml`
  are built and installed automatically. A probe project copied to /tmp
  still needs its own `aver.toml` with an **absolute** provider path, or
  every case that reaches the curve fails.

The fifteen CLI commands (`headers`, `bodies`, `txindex`, `outputs`, `utxo`,
`assumevalid`, `follow`, `show`, `tx`, `spend`, `audit`, `prune`, `reindex`,
listen, `help`), their ordering constraints and their output formats are
documented in README.md.

## Architecture

**The split is the architecture**: `domain/` is pure and fully covered by
verify blocks; `infra/` touches the network and the disk and is only an
arrangement of `domain/` parts; `app/` adapts argv to those; `main.av` is
deliberately thin. A failure against a live peer is therefore a socket
problem, never an ambiguity in the pure code.

**Capability providers.** Two contracts are declared in Aver with no bodies and
supplied at run time by Rust crates named in `aver.toml`:

- `domain/primitives.av` → `providers/primitives` — RIPEMD-160 and secp256k1
  (libsecp256k1, the same code Bitcoin Core runs). The curve is a provider on
  purpose: its edge cases are consensus rules.
- `infra/kv.av` → `providers/kv` — a RocksDB (`rocksdb` crate; needs clang +
  libclang-dev to build, ADR 0009). Effectful, so
  each operation declares an Oracle dimension. `Handle` is an opaque capability
  resource Aver code cannot construct or serialise.

The providers carry their own Rust tests; everything else is tested from Aver.

**The Store and the Index** (`infra/store.av`): one opaque keyed-store API over
two backends — Memory (fixtures), Database (RocksDB in `kv/`, made on first
open). The log backend and `migrate` are gone (#44); a directory holding an
`index.log` and no `kv/` is refused. The Index is derived: `reindex` rebuilds
every `b:` Location from the Segments (#93), which is the recovery path after a
crash (#92, the rusty-leveldb era; RocksDB syncs every batch).
Key prefixes: `b:` Block Id → Location, `h:` Height →
Block Id, `t:` Transaction Id → site, `n:` Block Id → Height, `k:` Block Id →
Header plus its Height and Chain Work, `o:<txid>:<index>` → Output (what lets a
spend resolve in one lookup), `u:<txid>:<index>` → unspent Output, `d:` Block Id
→ Undo Data. `h:` is rewritten by a Reorganisation and `u:` shrinks on every
spend; `b:`, `t:`, `n:`, `k:` and `o:` are append-only, because a Block Id
always names the same bytes.

**The UTXO Set** (#25, Stage 2): `u:` is deliberately not `o:`. `o:` answers
*what did this Input spend*, which a reorganised Block still has to be able to
ask, and only grows; `u:` answers *may this Input spend it*, which only the
current chain can answer, and shrinks. Decision D3. Every entry carries the
Height that made it and whether it came from a coinbase, because that is the
only way to check the hundred-Block maturity. `d:` is the Undo Data, keyed by
Block Id rather than Height — a Reorganisation is exactly when it is read, and
exactly when a Height stops naming the Block it named before.

**The Header tree** (#24, Stage 1 of the full-node plan): `k:` holds every
Header seen, not just the ones on the chain we follow. A Header whose parent is
not the tip is a Branch, and the chain followed is the Branch with the most
Chain Work — never the longest. `Domain.Chainwork` is the arithmetic (pinned
against Core's chainwork for mainnet Block 0), `Domain.HeaderTree` is the tree,
`Domain.Reorg` tells growth from a Reorganisation, and `Infra.Headers` is the
only part that touches a Store.

**Several Peers on one loop** (#27, Stage 5): `infra/peers.av` owns every
socket. Bytes are read as they arrive with `Tcp.readSome`, kept per Peer, and
Messages cut off the front of the buffer by `domain/inbox.av` — exact-length
reads are gone, because one of them holds the whole loop until it completes.
Readiness is one `Tcp.poll` over every connection; the caller owns the `Int`
keys, so they key the standing too. `awaitFrom(pool, key, wanted)` keeps a
conversation straight-line while every other Peer is read and pinged. Every
phase is a pool — `headers`, `bodies` and `listen` are pools of one. Two
deadlines, not one: 150s of silence from the whole pool, and 60s for a Peer to
answer the question it was asked, because with several Peers those stopped
being the same fact.

**A Peer that misbehaves costs itself** (#27, Stage 5): every frame's magic
bytes and checksum are verified in `domain/inbox.av` — neither was checked
anywhere before, so a corrupted payload reached the decoders and surfaced as
whatever they made of the wreckage. A Block body must hash to the Block Id it
was requested under (`Domain.Block.idOfWholeBlock`, not `blockIdOf` — the
former takes a whole Block, and getting that wrong accuses every honest Peer).
A Peer that breaks the protocol is dropped and the node carries on; a catch-up
that fails against a Peer drops it and retries on whoever is left, because
otherwise the first Peer named on the command line can end the node by lying
once. No banscore counter: every fault detectable here is one Core disconnects
on outright, so a threshold would only ever count to one.

**The Address Book** (#27, Stage 5): `follow` sends `getaddr` on joining and
folds arriving `addr` Messages into `domain/addressbook.av` as **Candidates** —
Peer Addresses claimed to exist, which become Peers only on a completed
Handshake (CONTEXT.md is binding on both words). In memory only; the plan puts
persisted Peer state out of scope. Target 8 Peers, one dial per turn. The pool
keeps Messages nobody asked for (bounded at 64, newest wins) because Core
answers a `getaddr` while the Header phase is waiting for `headers` — draining
that is where the Book is filled, and it happens between the Header and body
phases as well as in the listen loop, because a syncing node reaches the listen
loop hours late. Two known gaps, both filed: selection is first-untried and so
no defence against eclipse (#118), and a dead Candidate blocks the loop for the
OS connect timeout (#119).

**Following the tip** (#26, Stage 4): `follow` is the header, body and Set
phases run again every time a Peer announces a Block. An `inv` naming a Block
is answered with `getheaders`, not `getdata` — a Block whose Header the tree
has not placed cannot be connected and cannot be told from one on a Branch we
do not follow; the `getdata` is the body phase, one Header later. The Set
therefore has to know which Block it stands on and not merely which Height, so
`meta:setTo` holds `{height}:{blockId}`; a record holding a bare Height was
written before #26 and is **refused**, because a Height alone cannot say
whether `h:` was re-pointed underneath it. `Domain.Rewind` plans the walk back
to the fork and `Infra.Rewind` carries it out, reading the Set's own Branch out
of `k:` because `h:` no longer leads there. A fork below the 288-Block Undo
window stops the node with a report (D8) rather than being guessed past.

**Three-valued answers, everywhere.** The project's central discipline is
never collapsing "cannot tell" into pass or fail:

- Scripts settle as passed / failed / **undecided** (undecided = needs a
  primitive we lack, or is a witness/Taproot program refused before running —
  segwit soft-fork design makes those *look* valid to an engine that can't
  read them, which is why they must be refused, not run).
- Spends resolve as valid / invalid / **cannot tell** (parent not indexed).
- `audit` counts **unresolved** (a gap in what we hold, expected after
  pruning) separately from **FAULTS** (the data is wrong).

**Rules by Height** (`domain/rules.av`): a Block is checked under the rules it
was mined under, not today's. `Infra.Audit` resolves the rules once per Height
and the Context carries them into the Script engine. Currently only P2SH
activation is carried; BIP66 code exists (`Domain.Ecdsa.isValidEncoding`) but
is unwired because the held chain stops below its activation.

**The seam for what's missing**: `domain/ecdsa.av` has no `Valid` constructor
for outcomes it cannot yet produce, so adding Schnorr/witness evaluation will
make the compiler name every caller that must change.

**Test corpus**: Bitcoin Core's published test data — `script_tests.json`,
`sighash.json`, `tx_valid`/`tx_invalid`, `key_io`, `base58`, the BIP341
vectors, and `script_assets_test.json` (3,737 tapscript tests) — is compiled
into generated `domain/*cases*.av` files by the `tools/*_to_aver.py`
generators. Python assembles, the engine answers — a generator never decides
expected values. **`tools/refresh_corpora.sh` fetches every corpus and
regenerates whatever changed upstream** (docs/core-corpora.md has the
per-corpus detail). The invariant to protect: **0 cases where we refuse what
Core accepts**; refusals in the other direction are deliberate (ADR 0005).

## Conventions

- **CONTEXT.md is the glossary and it is binding.** Domain terms are
  capitalized in prose and comments (Peer, Block, Height, Segment, Location,
  Prune Watermark, Index, Store...), and each entry lists words to avoid
  (say Peer, not node/host; Block Id, not block hash; Height, not index).
  New concepts get an entry.
- Architecture decisions go in `docs/adr/` (nine so far — P2P not RPC, no fees,
  compile don't interpret, hex-text block format, signatures-left-out engine,
  LevelDB under the Index, two claims two tools, the single-writer loop,
  RocksDB under the Index).
- Verify-case expectations are pinned against sources *outside* this
  implementation (published vectors, Core's test data, spec-computed values) —
  never captured from the code under test.
- Commit messages are single sentences describing what changed in domain
  vocabulary (see `git log`).
