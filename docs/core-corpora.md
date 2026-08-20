# Corpora taken from Bitcoin Core

Bitcoin Core keeps twelve files in `src/test/data`. Four of them are the
adversarial cases nobody would write for themselves, and they are the closest
thing to a conformance oracle this project has short of the chain itself. This
says which we read, how to regenerate them, which we do not read and what
blocks each one.

## What is generated today

| Core file | ours | cases | tool |
|---|---|---|---|
| `script_tests.json` | `domain/scriptcases1..5.av` | 1118 | `tools/script_tests_to_aver.py` (middle step missing) |
| `sighash.json` | `domain/sighashcases1..2.av` | 500 | `tools/sighash_tests_to_aver.py` |
| `tx_valid.json`, `tx_invalid.json` | `domain/txcases1..4.av` | 213 | `tools/tx_tests_to_aver.py` |

Between them, 1831 cases out of a corpus of 4327.

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
agree         70
disagree      22
undecided    121  (this engine cannot answer; not a disagreement)
```

`undecided` is not failure. It is the third value: the engine saying it cannot
judge, which for an unimplemented opcode is the honest answer. A disagreement
is either a rule not implemented — and then it should have an issue — or a bug.
There should never be one that is neither.

## Regenerating

### Scripts

```
python3 tools/script_tests_to_aver.py --fetch
python3 tools/script_tests_to_aver.py --assemble /tmp/cases.tsv
#   step 2 -- see the warning below
python3 tools/script_tests_to_aver.py --emit /tmp/results.tsv
```

`--emit` wants a file of `sigHex<TAB>pubkeyHex<TAB>verdict<TAB>detail`, one a
line, which is step 2's output. **Whatever produced it is not in this
repository.** The tool's own docstring says "The middle step is a compiled Aver
program", and there is no such program in `tools/`. Regenerating the Script
corpus today therefore means writing that program first; the Transaction tool's
`--probe` is the shape to copy.

Core writes its Scripts in the assembly language `core_read.cpp` parses:
opcode names, `0x..` byte blobs, decimal numbers that become minimal pushes,
`'quoted'` strings. `parse_script` in the tool is that reader.

### Transactions

```
python3 tools/tx_tests_to_aver.py --fetch
python3 tools/tx_tests_to_aver.py --emit                       # placeholders
python3 tools/tx_tests_to_aver.py --probe /tmp/p/main.av       # the middle step
aver run /tmp/p/main.av --module-root . --providers > /tmp/answers.txt
python3 tools/tx_tests_to_aver.py --answers /tmp/answers.txt
```

`--probe` writes a complete Aver program with every case in it and a `main`
that prints one answer a line; `--answers` reads those back and rewrites the
corpus, then prints the agreement report and bakes it into the headers.

Running the probe needs a directory with `domain/`, `infra/`, `app/` and
`providers/` symlinked and an `aver.toml` naming only the `Domain.Primitives`
binding — a project-wide `aver.toml` that also names `Infra.Kv` is refused for
a program that does not reach it.

Two shapes in Core's Transaction data need care and the tool handles both:

* A prevout index of `-1` means a coinbase. `COutPoint::n` is a `uint32_t`, so
  what the Transaction actually carries is `4294967295`; a signed `-1` matches
  no Input at all.
* A prevout may carry a fourth element, the amount, which SegWit needs. It
  defaults to zero.

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

They are left out rather than left in failing, and the count is printed when
the corpus is regenerated.

## When a case changes answer

A regenerated corpus with a different answer in it is either a fix or a
regression, and the diff will not tell you which. What tells you is the
agreement report moving in the right direction, and every disagreement having
a reason. The open ones are #22 (BIP66), #51 (CLTV and CSV), #52 (verification
flags), #53 (CheckTransaction), #54 (unknown witness versions), #20 (SegWit)
and #12 (Taproot).
