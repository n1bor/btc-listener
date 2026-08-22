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
`../aver/docs/` has the deep dives (`language.md`, `types.md`, `effects.md`,
`cli.md`). The `aver` on PATH is `cargo install`ed from `../aver`, so it is
whatever that checkout was at when it was last installed — and **`aver
--version` cannot tell you which**, because the version string does not move
between releases. Two fixes this project reported were in `upstream/main` and
absent from a PATH binary reporting the same `0.28.1`. When something verifies
and will not compile, or the VM and the compiled binary disagree, check
`git log upstream/main` before believing it is a live bug.

## Commands

```bash
aver audit  .                                       # check + verify + format, the CI gate
aver check  . --module-root . --deps                 # contracts, coverage, lints
aver verify . --module-root . --deps                 # all verify cases (~6,000)
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
aver compile main.av --module-root . -o ../btc-listener-build \
  2> >(grep -v "non-law verify block" >&2)     # filters one known noisy warning
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

The twelve CLI commands (`headers`, `bodies`, `txindex`, `outputs`, `show`,
`tx`, `spend`, `audit`, `prune`, `migrate`, listen, `help`), their ordering
constraints and their output formats are documented in README.md.

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
- `infra/kv.av` → `providers/kv` — a LevelDB (`rusty-leveldb`). Effectful, so
  each operation declares an Oracle dimension. `Handle` is an opaque capability
  resource Aver code cannot construct or serialise.

The providers carry their own Rust tests; everything else is tested from Aver.

**The Store and the Index** (`infra/store.av`): one opaque keyed-store API over
three backends — Memory (fixtures), Logged (append-only `index.log`, whole
index in RAM), Database (LevelDB in `kv/`). Which backend a chain directory
uses is a fact about the directory's contents, not a flag. `migrate` moves log
→ database one way. Key prefixes: `b:` Block Id → Location, `h:` Height →
Block Id, `t:` Transaction Id → site, `o:<txid>:<index>` → Output (what lets a
spend resolve in one lookup).

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

**Test corpus**: Bitcoin Core's `script_tests.json` and `sighash.json` are
compiled into `domain/scriptcases1-5.av` / `sighashcases1-2.av` by
`tools/script_tests_to_aver.py`. Python assembles, the engine answers — the
generator never decides expected values. Regenerate with `--fetch` /
`--assemble` / `--emit` (see its docstring). The invariant to protect: **0
cases where we refuse what Core accepts**; the 96 in the other direction are
deliberate (post-hoc verification flags this engine doesn't apply — ADR 0005).

## Conventions

- **CONTEXT.md is the glossary and it is binding.** Domain terms are
  capitalized in prose and comments (Peer, Block, Height, Segment, Location,
  Prune Watermark, Index, Store...), and each entry lists words to avoid
  (say Peer, not node/host; Block Id, not block hash; Height, not index).
  New concepts get an entry.
- Architecture decisions go in `docs/adr/` (six so far — P2P not RPC, no fees,
  compile don't interpret, hex-text block format, signatures-left-out engine,
  LevelDB under the Index).
- Verify-case expectations are pinned against sources *outside* this
  implementation (published vectors, Core's test data, spec-computed values) —
  never captured from the code under test.
- Commit messages are single sentences describing what changed in domain
  vocabulary (see `git log`).
