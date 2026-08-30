# Did Aver make btc-listener easier or harder than Go, C++, Java, Scala or Python?

An assessment written on 2026-08-27 after reading this repository, the Aver
language (`../aver`, pinned in `.aver-version`), the friction drafts in
`../test`, and the git history. It is an opinion, but every claim about this
project is backed by a file, a commit or a number below.

## Short verdict

**Harder to type, easier to trust — and much of the "harder" was the
language's youth, not its design.**

- 34,962 hand-written lines of Aver in 140 files, 2,777 functions (median
  body **3 lines**), 2,189 verify blocks holding 11,752 cases (6,050 of them
  generated from Bitcoin Core's corpora), written in **18 days / 287
  commits** (2026-08-10 to 2026-08-27).
- **100 of those 287 commits cite a `jasisz/aver#` issue** (68 distinct
  upstream issues). The author contributed `Tcp.sendBytes`, `Tcp.readBytes`,
  `Tcp.writeBytes` and `Crypto.sha256` upstream because on aver 0.27.1 the
  project could not send a Bitcoin magic byte. Ten "move the Aver pin"
  commits in five days. No mature language charges that tax.
- Against that: the consensus-critical parts (script engine, sighash,
  chainwork, header tree, reorganisation, UTXO Set) were built with **0 cases
  where we refuse what Core accepts** across ~6,000 Core vectors, and the log
  records the compiler and verifier catching real faults repeatedly — seven
  tapscript faults, 6,363 false witness passes, a leaked listener port found
  by the `unused-effect` lint, the author's own wrong vectors twice.

A fluent Go or Python programmer would have had a *working* P2P listener and
chain downloader in a fraction of the time. Reaching the same **evidenced
correctness** on the script engine and the reorganisation path in Go or
Python would have cost most of that time back in hand-written tests, and
would still lack the effect ledger and the exhaustiveness seam. Scala is the
closest peer; C++ and Java are the furthest.

## Feature by feature

Each section says what the feature did *here*, then how each language
compares.

### 1. `match` only — no `if`/`else`, single-line arms, no guards, no early return

**Here.** Every conditional is `match cond / true -> / false ->`; every "then
do the next step" is a new named function. `domain/inbox.av:292-316` needs
three functions (`firstWith → orNext → orNextOk`) to unwrap a Result and
then an Option; `app/cli.av:130-213` is a five-function chain that would be
one function with early returns. The upside: 2,777 functions each small
enough to carry a verify block, and the checker forced `Domain.Inbox` pure
"because none could have a verify block with a Tcp.Connection inside the
record — both made it smaller".

| Language | Compared with Aver |
|---|---|
| Go | `if err != nil { return }` is exactly the early return Aver forbids; far less ceremony. No exhaustiveness on `switch`, so the three-valued discipline (passed / failed / undecided) is a convention, not a check. |
| C++ | As Go, plus `std::variant` + `std::visit` for exhaustiveness (clumsy). Least readable of the five for this code. |
| Java | `switch` on sealed interfaces (21+) gives exhaustiveness; still verbose; guards exist. Easier than Aver. |
| Scala | Pattern matching with guards, multi-line arms and exhaustiveness warnings. Strictly more expressive than Aver's `match` at no loss — the best comparison point. |
| Python | `match` (3.10) is unchecked. Fastest to write, zero exhaustiveness: the "seam that names every caller" (`domain/ecdsa.av`) does not exist. |

### 2. No loops, no closures, no `map`/`filter`/`fold`

**Here.** 152 `List.reverse` and 228 `List.prepend` calls; every fold is a
bespoke recursive pair. `domain/headertree.av:234-267` is four functions for
"tips = nodes nobody names as parent", and O(n²) because `List.contains` is
O(n). Mempool eviction scans every entry. Non-tail recursion is a lint; tail
calls are optimised automatically and reliably. The upside is real but
indirect: every traversal is a named, verified function, and with nothing to
capture the `?!` independent products are safe by construction.

| Language | Compared with Aver |
|---|---|
| Go | `for` and slices. Three to five times less code for the same traversal; no verification story. |
| C++ | Ranges and algorithms with lambdas; fastest at run time. Same code-size win as Go. |
| Java | Streams and lambdas — the "clever higher-order helpers" Aver's style guide rejects; fine here. |
| Scala | `foldLeft`, `collect`, immutable collections with structural sharing. Would remove perhaps a third of the functions in `domain/`. |
| Python | Comprehensions. Fastest to write; the recursion depth limit would bite where Aver's TCO does not. |

### 3. No user generics

**Here.** Less painful than expected: the domain is monomorphic (bytes, ints,
hex) and `List`/`Map`/`Option`/`Result` are generic internally. It surfaced in
the abandoned Postgres design (`../test/ISSUE-postgres-design.md`: a
`query<T>` cannot exist) and in duplicated per-type `show`/`render` helpers.

| Language | Compared with Aver |
|---|---|
| Go | Had the same gap until 1.18; generics are now light. Roughly equal. |
| C++ | Templates — a large win on reuse, a cost in error messages and compile time. |
| Java / Scala | Generics standard; Scala typeclasses would have deduplicated the encoders. |
| Python | Duck typing makes it a non-issue. |

### 4. Immutable records, no update syntax, state threaded through

**Here.** `Pool` (`infra/peers.av:36-51`) has 15 fields and is threaded
through about 120 functions; `Standing` is rebuilt spelling all seven fields
to change one (`domain/inbox.av:235,269,362,373`); 124 functions have six or
more parameters and one (`infra/metrics.av:83`) has 14. In return: no
aliasing bugs, every state transition is a pure function with a verify
block, and the single-writer loop (ADR 0008) was easy to keep honest.

| Language | Compared with Aver |
|---|---|
| Go | Mutable structs and pointers: trivial to update; the reorganisation and UTXO code would need care to avoid shared-state bugs. |
| C++ | As Go plus lifetime hazards; this is the kind of code that gets a use-after-free. |
| Java | Records (16+) with builder-style copies; mutability is the default culture. |
| Scala | `case class` with `.copy(field = x)` is exactly what Aver lacks — immutability with none of the seven-field ceremony. The biggest single ergonomic gap against Scala. |
| Python | `dataclasses.replace`; mutation is trivial and unchecked. |

### 5. Exact effect lists on every function and every module

**Here.** 591 function-level lists, 2,127 lines of effect declarations;
`app/cli.av:32-62` repeats 35 effects three times. Adding `Infra.Kv.get`
rippled through ten modules "and the checker named every site" (ADR 0006).
Fixing `closeAll` "would propagate two effects into 115 functions", so a
separate path was designed instead (`14388aa`) — a design decision shaped by
the effect system. Payoff: `unused-effect` found 59 stale lists and one real
bug (`Tcp.closeListener` declared and never called, so the port leaked —
n1bor/btc-listener#181). The domain/infra split is enforced, not aspired to.

| Language | Compared with Aver |
|---|---|
| Go | Nothing; `context.Context` is the nearest convention. Purity of `domain/` would be a code-review promise. |
| C++ | `const`, `constexpr`, `noexcept` are the only static effect facts. |
| Java | Checked exceptions are the disliked ancestor; no I/O tracking. |
| Scala | ZIO and cats-effect give an effect *type* but not a per-method ledger; Scala 3's experimental capture checking is the only true peer, with far heavier machinery. |
| Python | Nothing. |

Unique to Aver among the five. The cost is real but mechanical, and the
ledger is what lets a reader believe "a failure against a live Peer is a
socket problem, never an ambiguity in the pure code" (CLAUDE.md).

### 6. Colocated `verify` blocks, mandatory coverage, laws, `--hostile`, step budget

**Here.** The standout. Every pure non-trivial function must carry cases and
the coverage lint asks for each return shape. Cases are one line, so a
1,168-character literal appears in `domain/block.av`. Core's corpora compile
to 6,050 `verify case` lines. `[[verify.costly]]` in `aver.toml` re-admitted
the two largest Core rows that had been silently dropped as "case aborted"
(jasisz/aver#1071). Parallel verify took the suite from 8–15 minutes to
seconds — after the author filed for it (#1095). Providers under verify at
first tested `given` stubs rather than the curve, 81 cases' worth, until
#989.

| Language | Compared with Aver |
|---|---|
| Go | `go test` table tests are close in spirit, in a separate file, optional, with no coverage-by-shape lint. Doable; nobody would have written 11,752 cases. |
| C++ | GoogleTest / Catch2 — heavy, separate files, no colocation. Worst of the five. |
| Java | JUnit parameterised tests; as Go with more boilerplate. |
| Scala | ScalaCheck ≈ Aver's `law` with `given` domains, plus genuinely random generators (Aver's laws are enumerated). Closest peer; not mandatory, not colocated. |
| Python | pytest parametrize with Hypothesis is arguably more powerful for properties and far quicker to write; zero enforcement. |

The *mandatoriness* and *colocation* are the value, not the mechanism. In
any other language the corpus would be equally possible and several times
less likely to have been done.

### 7. Integers: arbitrary precision, `Int.div`/`Int.mod` → `Result`, `Bits` namespace, no operators

**Here.** Chain Work is written as `2^256 / (target + 1)` literally
(`domain/chainwork.av`) and pinned against Core's `0x100010001` — cleaner
than Core's own `~target / (target + 1) + 1`. Cost: no bitwise operators at
all until aver 0.29 — `domain/bits.av` simulated xor via div/mod at 7.5 µs a
call (about 4 ms per Bech32 address), and SHA-256 and RIPEMD-160 could not be
written in Aver, becoming an upstream PR and a provider respectively.
`Int.div` returns `Result`, so 48 call sites carry `withDefault` with a
"Total:" note the checker cannot see.

| Language | Compared with Aver |
|---|---|
| Go | `math/big` for Chain Work (verbose), native bit operations. Much easier for SipHash, Bech32 and hashing. |
| C++ | Fixed-width ints and `arith_uint256`, which is what Core does: fastest, most error-prone (the overflows Aver's ℤ makes impossible). |
| Java | `BigInteger` plus native operators. Fine. |
| Scala | `BigInt` with operators — best of both. |
| Python | Native bigints with operators — the closest to Aver's semantics with none of the ceremony. |

### 8. Bytes, hex, and the stdlib surface (Tcp, Disk, Crypto)

**Here.** The single largest source of lost time. On 0.27.1 there was no
binary TCP, no byte Disk, no SHA-256, no `poll`, no `listen`, no
non-blocking connect. Consequences: a hex-text Block format (ADR 0004,
Segments capped at 2 MiB against Core's 128 MiB, since retired);
`Bytes.fromHex` at 0.8 MB/s (fixed 44×, #911); 310 `Bytes.fromHex`, 277
`Bytes.octets` and 191 `Bytes.fromList` conversions still in the code;
Transaction Ids and Scripts travelling as hex Strings. `Bytes.fromList`
returns `Result` even for bytes that are octets by construction.

| Language | Compared with Aver |
|---|---|
| Go | `[]byte`, `encoding/binary`, `crypto/sha256`, `net` with deadlines and a goroutine per connection — every primitive missing here is in the standard library on day one. The biggest gap of any language in this comparison. |
| C++ | Boost.Asio, libsecp256k1, OpenSSL — all there, more setup. |
| Java | NIO selectors ≈ `Tcp.poll`, `MessageDigest`, `ByteBuffer`. All there, verbose. |
| Scala | Same JVM library; Akka or fs2 for the loop. |
| Python | `socket` and `select`, `hashlib`, the `bytes` type. All there. |

### 9. Capability providers (Rust crates) and opaque resources

**Here.** `Domain.Primitives` (libsecp256k1, RIPEMD-160) and `Infra.Kv`
(RocksDB) are Aver contracts with Rust bodies, pinned by `contract_hash`.
Swapping rusty-leveldb for RocksDB "changed one Rust file and no Aver"
(`providers/kv/src/lib.rs`). `Handle` is a resource Aver code cannot forge.
A Message addressed to a Listener does not typecheck. Cost: the mechanism was
built upstream over five days for this project (jasisz/aver#962–#981), and
changing a contract invalidated 47 files until the provider re-agreed.

| Language | Compared with Aver |
|---|---|
| Go | cgo (painful) or pure-Go secp256k1 (btcec). No enforced boundary — anything can call the curve. |
| C++ | Link the library. Native; no sandbox. |
| Java / Scala | JNI or Panama, or BouncyCastle. Heavy. |
| Python | `ctypes`, `coincurve`, `python-rocksdb` — easiest FFI. No boundary. |

Every language links libraries; only Aver makes the linkage a typed, hashed,
verifiable seam. Ergonomically Python wins; architecturally Aver wins.

### 10. Compilation model: VM, Rust transpile, wasm — and cliffs only `cargo build` finds

**Here.** CLAUDE.md carries a standing section of things that pass `check`,
`verify` and `compile` and fail `cargo build`: E0659 glob-import ambiguity in
three forms (all three now fixed upstream, the parameter form last, as
jasisz/aver#1162 — but "four renames in a day" while they were not), `R#await` keyword trampolines (#899), Result
pass-through E0308 (#901), packed-bytes equality (#1065), an opaque resource
in a record (#994), E0505 borrow moves (#1130). `aver compile` exits 0 on
Rust that does not build. Performance cliffs: a `Map` returned from a helper
was cloned (3,400× slower; opening a Store "took hours instead of two
seconds", #890, fixed); a record holding a Map still is (2,200×, #1160,
open); the VM was quadratic on `[h, ..t]` with an accumulator (29 s / 16 GB
against 14 ms compiled, #886) and on Map building (#900). ADR 0003 "compile,
don't interpret" exists because of these. The VM once silently truncated
list literals to `len mod 256` (#1054), caught only because the corpus tool
counts its cases.

| Language | Compared with Aver |
|---|---|
| Go | One compiler, one binary, no second gate, predictable performance. Decisively easier. |
| C++ | One compiler; performance predictable, correctness not (undefined behaviour). |
| Java / Scala | JIT; no hidden second-stage failures; GC pauses are the only cliff. Scala's compile times are the pain. |
| Python | No compile step; predictable slowness — the 1.45 M-entry Index open and the twelve-input sighash cases would be painful but not surprising. |

### 11. Concurrency: independent products `(a, b)?!`, `Tcp.poll`, a single-writer loop

**Here.** No threads, async or channels. Several Peers on one loop over
`Tcp.poll`; `awaitFrom` keeps one conversation straight-line while the other
Peers are pumped. `?!` gives a thread per branch when compiled, runs
sequentially under verify, and reverse-order rerun is a falsifier (ADR
0008). The design is clean and verifiable, but it existed only because the
author asked for `poll`, `readSome`, `beginConnect`/`dialled` and
`listen`/`accept` in turn (#1013, #1125, #1131) — each a blocked stage until
upstream shipped it, usually within a day.

| Language | Compared with Aver |
|---|---|
| Go | A goroutine per Peer and channels — the idiomatic fit for this program; would have removed `awaitFrom` and most of `infra/peers.av` (1,602 lines). Determinism under test is what it costs. |
| C++ | Asio or epoll; Core itself is a select loop. Powerful, hard to get right. |
| Java | NIO selectors or virtual threads (21+); verbose. |
| Scala | Akka actors or fs2 streams — natural for a Peer pool. |
| Python | asyncio — a good fit, easy; the GIL is irrelevant for I/O. |

Every alternative is easier to write; none gives the reverse-and-reverify
falsifier or the single-writer invariant for free.

### 12. Module system: glob imports, `exposes opaque`, `depends`

**Here.** `exposes opaque [Store]` let the backend sum gain a Database arm
and lose a Log arm without any caller changing. But every `depends` module is
glob-imported into the emitted Rust, so adding a dependency can break the
build through a name two dependencies share — `Domain.Addr.offering` is "a
worse name chosen so the emitted Rust compiles". A type named after a
builtin was silently resolved to the builtin (`Connection` → `Tcp.Connection`).

| Language | Compared with Aver |
|---|---|
| Go | Package-qualified names, no glob ambiguity, unexported is opaque. Cleaner. |
| C++ | Namespaces and `using` — glob ambiguity is a known C++ disease too, but one compiler diagnoses it. |
| Java / Scala | Explicit imports; sealed and private for opacity. Cleaner. |
| Python | `from x import *` is the same hazard, rarely used. |

### 13. Tooling: `check`, `audit`, `format`, LSP, versioning

**Here.** `aver audit` as a single CI gate is good. `unused-effect` with its
`used:` clause, the coverage lints, `context` and `decision` blocks are
genuinely useful to a reviewer. Against: `aver --version` does not change
between commits (two bugs re-reported against a stale binary, one withdrawn —
`29f87bf`), `compile` exits 0 on unbuildable output, the formatter rejected
`Tuple<A, B>` while recommending it (#891), and the step budget hid corpus
rows as "case aborted".

| Language | Compared with Aver |
|---|---|
| Go | gofmt, vet, staticcheck, gopls, pprof — mature, boring, complete. |
| C++ | clang-tidy, sanitizers, CMake — powerful, fragmented. |
| Java / Scala | The best IDEs of the five; sbt is slow. |
| Python | ruff, mypy, pytest — excellent, optional. |

### 14. The language as a review target: mandatory `?` descriptions, `intent`, `decision` blocks

**Here.** Every function has a description, every module a multi-paragraph
intent, and decisions are syntax. The codebase is unusually legible — its
design can be reconstructed from the source alone. Cost: prose is roughly a
third of the line count; a thirty-line `intent` on a hundred-line module is
common.

No other language enforces this; all five allow it as convention (Go doc
comments, Javadoc, Scaladoc, docstrings). It is the feature most aligned
with Aver's stated purpose — "the optimization target is the reviewer, not
the generator" — and it visibly worked here.

### 15. Maturity: a moving target

**Here.** 68 upstream issues in 18 days, most closed the same or the next
day, several redesigned "better than what I proposed"
(`../test/ISSUE-postgres-design.md`, ADR 0008). Every one this project has
cited is now closed, the last two — #1160 (a record holding a Map is copied
on return) and #1162 (E0659 on a function parameter) — with the `7134af7a`
pin. The pin routine, `.aver-version`, the `git log upstream/main`
discipline and the provenance-versus-workaround citation trap
(`aver.toml:49-56`) exist only because the language moved under the project
daily.

All five comparison languages are ten to forty years old. This is not a
design drawback, but it is probably the largest single component of "harder"
in this project, and it will shrink.

## Weighing it

| Axis | Easiest → hardest |
|---|---|
| A listener talking to a Peer on day one | Go ≈ Python > Java ≈ Scala > C++ ≫ Aver 0.27 |
| Writing the script engine, sighash, Chain Work | Python ≈ Scala > Go ≈ Java > Aver > C++ |
| **Evidencing** the script engine against Core's 6,000 vectors | Aver > Scala (ScalaCheck) > Python (Hypothesis) > Go ≈ Java > C++ |
| Keeping `domain/` pure and knowing it | Aver ≫ Scala > the rest (convention only) |
| The reorganisation / UTXO state machine without aliasing bugs | Aver ≈ Scala > Java > Go > Python > C++ |
| Several Peers on one loop | Go > Python (asyncio) > Scala (actors) > Java > C++ > Aver |
| Run-time performance, predictably | C++ > Go ≈ Java/Scala > Aver compiled > Python > Aver VM |
| Knowing six months later why a design exists | Aver ≫ all |

**If the goal were a Bitcoin node:** Go — btcd exists for a reason. The
whole `infra/` layer is where Aver fought hardest and Go is strongest.

**If the goal were a Bitcoin auditor whose refusals must be defensible** —
which is what this project is — Aver's enforced purity, mandatory colocated
cases, exhaustiveness seam and effect ledger deliver something none of the
five give without a discipline nobody sustains across 2,777 functions. Scala
is the only one within reach, and it gets there with optional tools.

**The cost accounting.** Roughly a third of the project's commits were spent
on the language rather than on Bitcoin. Most of that was stdlib absence
(bytes, TCP, crypto, poll) and transpiler bugs — maturity, not design. The
design costs that will not go away are single-line arms and the
function-per-branch explosion, no record update, no fold or map, seven-field
record rebuilds, and effect-list fan-out on every new I/O. Those add perhaps
1.5–2× to the volume of `domain/` code against Scala and 3× against Python,
in exchange for every one of those lines carrying a checked description and
a pinned case.

## Sources

- This repository: CLAUDE.md, CONTEXT.md, README.md, `docs/adr/0001`–`0009`,
  `docs/full-node-plan.md`, `aver.toml`, and the files cited inline.
- `../aver`: `llms.txt`, `docs/language.md`, `docs/effects.md`,
  `docs/oracle.md`, `docs/transpilation.md`, `docs/vm.md`,
  `docs/pushback.md`, `CHANGELOG.md` (0.28.0, 0.28.1, 0.29.0).
- `../test`: the `ISSUE-*.md` and `PR-*.md` drafts and
  `bitcoin-p2p-blockers.md`.
- `git log` of this repository: 287 commits, of which 100 cite
  `jasisz/aver#`.
- Counts were produced by `grep`/`wc` over the non-generated `.av` files on
  2026-08-27 and by one local `aver verify` run.
