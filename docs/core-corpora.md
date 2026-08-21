# Corpora taken from Bitcoin Core

Bitcoin Core keeps twelve files in `src/test/data`. Four of them are the
adversarial cases nobody would write for themselves, and they are the closest
thing to a conformance oracle this project has short of the chain itself. This
says which we read, how to regenerate them, which we do not read and what
blocks each one.

## What is generated today

| Core file | ours | cases | tool |
|---|---|---|---|
| `script_tests.json` | `domain/scriptcases1..5.av` | 1118 | `tools/script_tests_to_aver.py` |
| `sighash.json` | `domain/sighashcases1..2.av` | 500 | `tools/sighash_tests_to_aver.py` |
| `tx_valid.json`, `tx_invalid.json` | `domain/txcases1..4.av` | 213 | `tools/tx_tests_to_aver.py` |
| `script_tests.json`, the Witness rows | `domain/witnesscases1..3.av` | 108 | `tools/witness_tests_to_aver.py` |
| BIP341 `wallet-test-vectors.json` | `domain/bip341cases.av` | 7 | hand-extracted, keyPathSpending |

Between them, 1946 cases out of a corpus of 4327.

## The discipline, and what these corpora are not

Every one of these is generated in three steps that are deliberately kept
apart:

1. **Python assembles.** It reads Core's JSON and turns each case into
   something the engine can be asked. It does not decide what the answer is.
2. **The engine answers.** A compiled Aver program runs every case and prints
   what it makes of each.
3. **Python writes the answers back** into the `.av` files as the expected
   values.

The expected value of every case is therefore **this engine's own answer**,
never Core's and never the tool author's. That makes these **regression**
corpora: they pin behaviour so that a change is visible. They are not
conformance corpora, and on their own they cannot tell you the engine is
right — a wrong answer recorded is a wrong answer preserved.

What carries the conformance claim is the **agreement report**, printed by the
tool and, for the Transaction corpus, written into the module intent of each
generated file:

```
cases        213
agree        210
disagree      3
undecided     0  (this engine cannot answer; not a disagreement)
```

`undecided` is not failure. It is the third value: the engine saying it cannot
judge, which for an unimplemented opcode is the honest answer. A disagreement
is either a rule not implemented — and then it should have an issue — or a bug.
There should never be one that is neither.

## Each case carries the flags Core ran it under

Core does not run a Script under a Height. It runs one under a set of
verification flags, and `Domain.Rules` is a Height and the soft forks in force
at it — a different and coarser thing. Until n1bor/btc-listener#52 the corpora
ran every case under `Domain.Rules.latest()`, which is every rule on, and that
is not what Core did to any of them.

Two things now cross the gap:

* `Domain.Rules.underFlags(names)` — the rules Core's flag names ask for, and
  no others.
* `Domain.Rules.exceptFlags(names)` — every rule **except** the ones named.

The asymmetry is Core's, not a choice made here. `src/test/transaction_tests.cpp`
verifies `tx_valid.json` with `~verify_flags` and `tx_invalid.json` with
`verify_flags`, so the same word means opposite things in the two files;
`script_tests.json` uses its flags as they stand. A generated case therefore
reads:

```
    // Core: invalid  flags P2SH,WITNESS  -- Invalid witness script
    case("0100…", [Prevout(…)], Domain.Rules.underFlags(["P2SH", "WITNESS"])) => …
```

Flags divide into two kinds and land in two places:

* Soft forks — `P2SH`, `WITNESS`, `CHECKLOCKTIMEVERIFY`, `CHECKSEQUENCEVERIFY`,
  `TAPROOT`, `NULLDUMMY` — are consensus and have a Height, so they are fields
  of `Domain.Rules` and `at()` answers for them.
* Policy — `CONST_SCRIPTCODE` and the rest, rules a node applies over and
  above what the chain enforces — are `Domain.Policy`, which
  `Domain.Rules.at()` **cannot** return anything but `none()` of. That is
  structural rather than a convention: a Policy rule reachable from a
  Height would make the auditor reject Transactions that are in the chain.

A flag `Domain.Policy` has no field for is not modelled, and the engine is then
lenient about it. A case that turns on such a flag will disagree with Core, and
that disagreement is the honest record of the gap rather than a silent pass.

## Regenerating

### Scripts

Same three steps as the Transaction corpus below, and the same probe project:

```
python3 tools/script_tests_to_aver.py --fetch
python3 tools/script_tests_to_aver.py --probe /tmp/p/main.av
cd /tmp/p && aver run main.av --module-root . --providers > /tmp/answers.txt
python3 tools/script_tests_to_aver.py --answers /tmp/answers.txt
```

`--emit` on its own writes the cases with placeholder answers; `--answers`
writes the real ones back and prints the agreement report, split by direction:

```
cases        1118
agree         932
disagree       99
undecided      87  (this engine cannot answer; not a disagreement)

  we accept what Core refuses  99  (a rule not implemented)
  we refuse what Core accepts   0  (the direction a defect shows up in)
```

The second of those two numbers is the one that matters, because it is the
direction a defect shows up in. A rule not implemented makes this engine
lenient; a rule implemented wrongly makes it strict, and strict is what would
reject a Block.

Core writes its Scripts in the assembly language `core_read.cpp` parses:
opcode names, `0x..` byte blobs, decimal numbers that become minimal pushes,
`'quoted'` strings. `parse_script` in the tool is that reader.

### The Witness rows of `script_tests.json`

The 113 rows carrying a Witness cannot be run as a Script pair: a Witness
belongs to an Input of a Transaction and the row does not carry one. Core's own
harness builds two, and `tools/witness_tests_to_aver.py` reproduces exactly that
construction from `src/test/util/transaction_utils.cpp`:

```
BuildCreditingTransaction(scriptPubKey, nValue)
    version 1, locktime 0
    one input:  prevout null, scriptSig OP_0 OP_0, sequence 0xffffffff
    one output: the row's scriptPubKey, the row's amount

BuildSpendingTransaction(scriptSig, scriptWitness, txCredit)
    version 1, locktime 0
    one input:  prevout (txCredit txid, 0), the row's scriptSig,
                sequence 0xffffffff, the row's Witness
    one output: empty script, the same amount
```

That makes each row a whole Transaction spending one known Output, which is the
shape `Domain.TxCase` runs -- so these join the corpus beside the Transaction
cases rather than beside the Script pairs. Same four steps as the Transactions
below, with `--check` proving the assembly is deterministic.

Getting the construction wrong would look exactly like getting the engine
wrong, which is the risk this tool carries. It is self-checking in one
important way: BIP143 and BIP341 both commit to the Transaction Id and to the
amount, so a harness that built either wrongly would fail **every** signed case
rather than some of them. Fifty-six of the 108 pass, which is evidence about
the harness and not only about the engine.

Five rows are left out: the ones carrying the `TAPROOT` flag, which are
tapscript leaf cases and belong with Core's `script_assets_test.json`.

### Transactions

```
python3 tools/tx_tests_to_aver.py --fetch
python3 tools/tx_tests_to_aver.py --emit                       # placeholders
python3 tools/tx_tests_to_aver.py --probe /tmp/p/main.av       # the middle step

# the probe needs its own project; see below for why
mkdir -p /tmp/p && cp -r domain /tmp/p/domain
cat > /tmp/p/aver.toml <<'TOML'
[providers]
schema = 1

[[providers.bindings]]
capability = "Domain.Primitives"
crate = "btc_listener_primitives"
package = "btc-listener-primitives"
path = "/absolute/path/to/btc-listener/providers/primitives"
factory = "primitives_binding"
TOML
cd /tmp/p && aver run main.av --module-root . --providers > /tmp/answers.txt

python3 tools/tx_tests_to_aver.py --answers /tmp/answers.txt
```

`--probe` writes a complete Aver program with every case in it and a `main`
that prints one answer a line; `--answers` reads those back and rewrites the
corpus, then prints the agreement report and bakes it into the headers.

The probe needs a project of its own and the reason is worth stating, because
the obvious command does not work:

```
$ aver run /tmp/p/main.av --module-root . --providers
aver.toml: [[providers.bindings]] index 1 capability 'Infra.Kv' has no
capability contract in this project
```

`aver.toml` is read from the module root, but the *project* is whatever the
entry program reaches — and a corpus probe reaches `Domain.TxCase` and nothing
under `infra/`. So the repository's own `aver.toml`, which names `Infra.Kv`, is
refused: it binds a capability the program never uses. Pointing `--module-root`
at the repository does not help, because that is where the offending file is.

What works is a directory holding a copy of `domain/` alone, `main.av`, and an
`aver.toml` naming only the `Domain.Primitives` binding with an **absolute**
`path` back to `providers/primitives`. `infra/` and `app/` are not needed and
must not be copied — copying them is what drags `Infra.Kv` back in.

Two shapes in Core's Transaction data need care and the tool handles both:

* A prevout index of `-1` means a coinbase. `COutPoint::n` is a `uint32_t`, so
  what the Transaction actually carries is `4294967295`; a signed `-1` matches
  no Input at all.
* A prevout may carry a fourth element, the amount, which SegWit needs. It
  defaults to zero.

## The probe is written in parts, and why

Every probe splits its cases into `part1()`, `part2()` … joined by
`List.concat`, rather than one list literal. That is not style. **The VM
truncates a list literal to `len mod 256` elements, silently, with exit 0** —
jasisz/aver#1054. `LIST_NEW` carries an 8-bit count and the compiler emits
`items.len() as u8`.

The Script probe with 1118 cases printed 94 answers. Nothing said so: `aver
check` was clean, stderr was empty, the exit code was nought. The compiled Rust
backend gets it right, so the VM and the binary disagree about the value of a
constant.

Two things guard against it now:

* Each part is at most 200 cases, and `verify assembled` counts the joined
  result against the number the tool wrote. A part over the limit fails there
  rather than quietly shortening the corpus.
* Every tool refuses to write answers back when the answer count does not match
  the case count. That is what caught this one; without it the corpus would
  have recorded 94 answers against 1118 cases and reported a cheerful agreement
  about the 94.

## `sighash.json` is the one conformance corpus

`tools/sighash_tests_to_aver.py` writes the 500 cases in
`domain/sighashcases1..2.av`.

It is the only one of the three that needs no middle step, because Core
supplies the expected value: each row is a Transaction, the Script being
signed, an Input index, a hash type and **the message the reference client
produces**. So this corpus is a conformance test rather than a regression one,
and a case that fails means this engine disagrees with Bitcoin Core about a
signature hash.

```
python3 tools/sighash_tests_to_aver.py --fetch
python3 tools/sighash_tests_to_aver.py --emit
python3 tools/sighash_tests_to_aver.py --check   # regenerate and diff
```

Two things change on the way in, both lossless, and the corpus's own header
records them:

* Core writes the expected hash the way a Transaction Id is written, back to
  front. The corpus holds it in the order the bytes are hashed.
* Core's hash types are signed integers and half of them are negative. The
  corpus holds the four byte value that actually gets serialised.

Core's row order is `[raw, script, index, hashType, hash]` and `check` takes
`(raw, index, script, hashType)`, so the middle two swap.

`--check` regenerates in memory and diffs against what is on disk. That is how
the tool was tested: the corpus already existed and passed, so a generator that
reproduces it byte for byte is a generator that would have produced it. Both
files match.

## Not read yet

| Core file | what it is | what blocks it |
|---|---|---|
| `key_io_valid.json` | addresses beside the Output Script they stand for | **nothing** — 14 mainnet entries run today, 12 match, 2 do not (#54) |
| `key_io_invalid.json` | strings that must not decode as addresses | `Domain.Base58` and `Domain.Bech32` are encode-only; there is no decoder to point it at |
| `base58_encode_decode.json` | raw base58, no checksum | `Domain.Base58` exposes only `encodeCheck`; the inner encoder is private |
| `bip341_wallet_vectors.json` | Taproot key and script paths | Taproot, #12 |
| `siphash.json` | SipHash-2-4 vectors | nothing uses SipHash until compact Blocks, #29 |
| `blockfilters.json` | BIP157/158 compact Block filters | not on the roadmap |
| `asmap.raw` | peer ASN mapping | peer diversity, #27 at the earliest |

`key_io_valid.json` is the one worth taking now. Core gives each entry as an
address string, the Output Script hex it stands for, and metadata naming the
chain — so `Domain.Script.describe` is exactly the function under test, and the
entries are already split by network for when #34 gives a directory one.

## What each corpus can and cannot express

This is not a matter of Core's thoroughness; it is structural, and it is why
there are two Script corpora rather than one.

`script_tests.json` is pairs of Scripts. Its harness builds the crediting
Transaction **out of the Output Script**, so the spending Transaction's prevout
hash — and therefore the signature hash — depends on that Script. A signature
embedded in the Output Script would have to sign a digest that depends on
itself, and there is no fixed point to find.

So everything that turns on the Transaction rather than the Script lives in
`tx_valid.json` and `tx_invalid.json`: FindAndDelete, the SIGHASH_SINGLE bug,
the hash types, sequence and lock time. The one mainnet Transaction that
proves FindAndDelete is in there, and escapes the circularity only because its
embedded signature uses the SIGHASH_SINGLE bug and therefore signs the constant
one, which depends on nothing.

Reading only the first file for as long as we did is why a missing FindAndDelete
was found by auditing 40 million Scripts on the chain rather than by a test that
had been sitting in Core's repository for years.

## Cases left out

Both tools drop cases the verify VM cannot finish. `aver verify` runs on the VM
with a million-step budget; the compiled engine has no such limit and answers
them.

* Scripts: anything over 1000 bytes on either side.
* Transactions: one case, a 1911 byte Transaction with twelve Inputs. The
  median case is 135 bytes and the next largest is under 500.
* Witness rows: the five carrying the `TAPROOT` flag, which are tapscript leaf
  cases rather than BIP141 ones.

They are left out rather than left in failing, and the count is printed when
the corpus is regenerated.

## When a case changes answer

A regenerated corpus with a different answer in it is either a fix or a
regression, and the diff will not tell you which. What tells you is the
agreement report moving in the right direction, and every disagreement having
a reason. The open ones are #22 (BIP66), #51 (CLTV and CSV), #52 (verification
flags), #53 (CheckTransaction), #54 (unknown witness versions), #20 (SegWit)
and #12 (Taproot).
