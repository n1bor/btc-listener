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
| BIP341 `wallet-test-vectors.json` | `domain/bip341cases.av` | 39 | `tools/bip341_vectors_to_aver.py` |
| `key_io_valid.json` | `domain/keyiocases.av` | 108 | `tools/key_io_to_aver.py` |
| `key_io_invalid.json` | `domain/keyioinvalidcases.av` | 70 | `tools/key_io_invalid_to_aver.py` |
| `base58_encode_decode.json` | `domain/base58cases.av` | 42 | `tools/base58_to_aver.py` |
| `script_assets_test.json` | `domain/assetcases1..9.av` | 3737 | `tools/script_assets_to_aver.py` |

Between them **5,938 verify cases**, and every entry these files hold is either
read or excluded for a reason with an issue against it:

| file | entries | read | left out |
|---|---|---|---|
| `script_tests.json`, Script pairs | 1120 | 1119 | 1 over the verify VM's step budget, answered by the compiled engine and recorded in the module, #75 |
| `script_tests.json`, Witness rows | 113 | 108 | 5 carrying `TAPROOT`, which are BIP342 leaf cases Core's own note sends to the taproot asset tests, #74 |
| `sighash.json` | 500 | 500 | — |
| `tx_valid.json`, `tx_invalid.json` | 214 | 213 | 1 over the step budget, answered by the compiled engine and recorded in the module, #75 |
| BIP341 `wallet-test-vectors.json` | 3 sections | all 3 | — |
| `key_io_valid.json` | 70 | 54 | 16 WIF private keys, which this project has no notion of, #71 |
| `key_io_invalid.json` | 70 | 70 | — |
| `base58_encode_decode.json` | 21 | 21 | — |

The case count exceeds the entry count because two of these files are read in
both directions: an address is written and read back, and a base58 pair is
encoded and decoded.

## The discipline, and what these corpora are not

The three Script and Transaction corpora — `script_tests.json` in both its
shapes, `sighash.json`, and the `tx_*` pair — are generated in three steps that
are deliberately kept apart. The three address corpora are not, and the
difference is set out at the end of this section.

1. **Python assembles.** It reads Core's JSON and turns each case into
   something the engine can be asked. It does not decide what the answer is.
2. **The engine answers.** A compiled Aver program runs every case and prints
   what it makes of each.
3. **Python writes the answers back** into the `.av` files as the expected
   values.

The expected value of every case in those three is therefore **this engine's
own answer**, never Core's and never the tool author's. That makes them
**regression** corpora: they pin behaviour so that a change is visible. They are not
conformance corpora, and on their own they cannot tell you the engine is
right — a wrong answer recorded is a wrong answer preserved.

What carries the conformance claim is the **agreement report**, printed by the
tool and, for the Transaction corpus, written into the module intent of each
generated file:

```
cases        213
agree        213
disagree      0
undecided     0  (this engine cannot answer; not a disagreement)
```

`undecided` is not failure. It is the third value: the engine saying it cannot
judge, which for an unimplemented opcode is the honest answer. A disagreement
is either a rule not implemented — and then it should have an issue — or a bug.
There should never be one that is neither.

**The three address corpora work the other way round.** `key_io_valid.json`,
`key_io_invalid.json` and `base58_encode_decode.json` all carry Core's own
answer in the file — an address beside its Script, a string asserted not to be
an address, a byte string beside its base58 — so there is nothing for the
engine to be asked first. Those are **conformance** corpora, and a case failing
in one of them is a disagreement with Bitcoin Core rather than a change of
behaviour here. They are also the only ones where the expected value was not
produced by this engine, which is why the tools for them are three steps
shorter.

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
cd /tmp/p && aver run main.av --module-root . > /tmp/answers.txt
python3 tools/script_tests_to_aver.py --answers /tmp/answers.txt
```

`--emit` on its own writes the cases with placeholder answers; `--answers`
writes the real ones back and prints the agreement report, split by direction:

```
cases        1118
agree        1048
disagree        0
undecided      70  (this engine cannot answer; not a disagreement)

  we accept what Core refuses   0  (a rule not implemented)
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
cd /tmp/p && aver run main.av --module-root . > /tmp/answers.txt

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

## `key_io_valid.json` is the second conformance corpus

`tools/key_io_to_aver.py` writes the 54 address entries into
`domain/keyiocases.av`. Like `sighash.json` and unlike the other three, **Core
supplies the answer** — each entry is an address, the Output Script hex it
stands for, and metadata naming the chain — so this is a conformance test, and
a case that fails means this engine and Bitcoin Core disagree about an address.

```
python3 tools/key_io_to_aver.py --fetch
python3 tools/key_io_to_aver.py --emit
python3 tools/key_io_to_aver.py --check    # regenerate and diff
```

All 54 pass. Two things are worth saying about the split:

* **Every Network in the file is read**, not just mainnet. That is most of the
  value: base58 cannot tell testnet, signet and regtest apart — they share both
  version bytes — so the prefixes are only really tested by the Bech32 entries,
  and those are the forty this corpus adds.
* Core's `testnet4` reads as `Network.Testnet` here. The fork changed the
  genesis Block and the difficulty rules, not the letters at the front of an
  address.

The 16 private-key entries are skipped and the count is in the module intent.
They are WIF, and this project has no notion of a key — only of Scripts.

## The probe counts its own cases, and why

Every probe ends with

```
verify assembled
    List.len(assembled()) => 1118
```

which looks like belt and braces and is not. **The VM used to truncate a list
literal to `len mod 256` elements, silently, with exit 0** — jasisz/aver#1054,
found here. The Script probe with 1118 cases printed 94 answers; `aver check`
was clean, stderr was empty, the exit code was nought, and the compiled Rust
backend got it right, so the VM and the binary disagreed about the value of a
constant.

It is fixed upstream and the probes are back to one literal each. The count
stays, because it is what caught it: a corpus that cannot say how many cases it
holds is one that can lose some without saying so.

The other guard stays too, and it is the one that actually stopped the bad data
reaching disk: **every tool refuses to write answers back when the answer count
does not match the case count.** Without that the corpus would have recorded 94
answers against 1118 cases and printed a cheerful agreement report about the 94.

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
| `bip341_wallet_vectors.json` | Taproot key and script paths | Taproot, #12 |
| `siphash.json` | SipHash-2-4 vectors | nothing uses SipHash until compact Blocks, #29 |
| `blockfilters.json` | BIP157/158 compact Block filters | not on the roadmap |
| `asmap.raw` | peer ASN mapping | peer diversity, #27 at the earliest |

`script_assets_test.json` is the one worth taking next. It is the only
published corpus that tests tapscript execution at all, it does not live in
bitcoin/bitcoin, and picking a defensible subset of it is most of the work.
#74, and #68 waits on it.

## The BIP341 vectors ask six questions, on purpose

`bip341cases.av` used to read seven of the `keyPathSpending` vectors through
the finished sighash. It now reads all three sections, and the reason it asks
six different things rather than one is that **a hash is the worst possible
place to look for a mistake**: every field goes in and one number comes out, so
two errors that cancel are indistinguishable from none.

| asked | cases | what it would catch that the sighash would not |
|---|---|---|
| `sighash` | 7 | — this is the one that matters |
| `sigMsg` | 7 | a wrong field, in the position it occupies, before anything is hashed |
| the five intermediary hashes | 5 | one of the five sub-hashes wrong in a way the sixth compensates for |
| `merkleRoot` | 6 | a script tree built rather than walked — `leafHashOf` and smaller-first `joined` |
| `address` | 7 | Bech32m chosen for the wrong versions |
| `script` | 7 | the same, read back |

The `merkleRoot` cases are the ones that reach code from an unfamiliar
direction. Verification only ever walks a path *upward* from a single leaf;
these build a whole tree from its leaves, which is what a wallet does. The
generator turns each tree into nested `Domain.Taproot.joined` and `leafHashOf`
calls and lets the engine evaluate them — it does no hashing itself. Two of the
six trees are unbalanced, so an implementation that recorded which side a node
came from instead of sorting the pair would come apart on them.

The seven addresses are the first published vectors this project's Bech32m
encoder has ever been held to. It has chosen Bech32m for every witness version
above zero since it was written, and until now nothing had contradicted it.

The tweak arithmetic in the `scriptPubKey` section needs secp256k1 and is
checked in Rust, in `providers/primitives` as `every_bip341_commitment_vector`.
What is here is what Aver can answer.

## Reading an address back, and why the refusals had to be counted

`key_io_invalid.json` is 70 strings that must not decode. It is the cheapest
corpus here to score and the easiest to fake: **a decoder that refused
everything would pass all 70 of them.** It is worth something only alongside
`key_io_valid.json`, where the same function has to succeed 54 times and return
the exact Output Script Core records — a decoder that refused everything would
fail every one of those. Neither half constrains anything alone, which is why
`tools/key_io_to_aver.py` now emits both directions from the same entry.

That still leaves the weaker version of the same worry: all 70 might be
bouncing off one cheap guard, with the rest of the decoder never reached. So
the reasons were counted rather than assumed. Across the 70:

| refused because | entries |
|---|---|
| bech32 checksum does not match | 13 |
| the base58 payload is not a 20 byte hash | 18 |
| that address is not for this network | 9 |
| upper and lower case are mixed | 8 |
| no prefix, or no separator | 5 |
| bits carried that are not part of the program | 5 |
| the witness program is not 2 to 40 bytes | 6 |
| a version zero program that is not 20 or 32 bytes | 3 |
| the witness version is above sixteen | 3 |

Thirteen distinct reasons, and every guard in the decoder is reached by at
least three entries. The nine wrong-network refusals are the ones #34 made
possible: until a directory could say which chain filled it, "this address is
not for this chain" was not a sentence this project could say.

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


## The cases verify cannot run, and how they stopped being invisible

`aver verify` runs on a VM with a per-case budget of a million steps
(`VERIFY_VM_STEP_LIMIT`, `src/diagnostics/vm_verify.rs`). It is there to turn a
fuzz-discovered infinite loop into a clean error rather than a hang, and 1M is
far above what an ordinary case needs. Two of Core's cases exceed it anyway,
and they are the ones most worth running — the largest input is where a
quadratic hash or a rope shows itself.

Both tools used to drop oversized cases on a plain length test and say nothing.
Two things were wrong with that.

**The length test was measuring the wrong thing.** A Script *over* the
consensus limit of 10,000 bytes is refused on its size before a single opcode
runs, so it is as cheap to answer as an empty one. The 10,001 byte
`SCRIPT_SIZE` case had been excluded for as long as the rule existed, and it
answers in milliseconds. What is expensive is a Script that is long *and*
executed — the band between the VM's reach and the consensus limit. The tools
now exclude on that band, which recovers the `SCRIPT_SIZE` boundary case: the
one case in the file that tests the 10,000 byte consensus limit itself.

**An excluded case was invisible.** The probe step runs under the compiled
engine, which has no step budget, so it always could answer these. Now it does,
and the answer is written into the generated module's `intent`:

```
"1 case(s) are answered by the compiled engine and not by verify."
"aver verify runs on a VM with a million-step budget and a Script"
"that is both long and executed exhausts it. Each is named here with"
"the answer the compiled engine gave it, because a case left out"
"silently is a case nobody sees. n1bor/btc-listener#75:"
"  10000 byte Script, flags P2SH,STRICTENC -- Core expects OK, this engine"
"  answers Outcome.Decided(Ending.Passed)."
```

The agreement report counts them too, so `script_tests.json` reads 1120 cases
rather than 1118 and `tx_valid`/`tx_invalid` reads 214 rather than 213. Both
recovered cases agree with Core. The 1,911 byte twelve-input Transaction — the
largest in Core's corpus, and the one whose twelve signature checks each hash
the whole Transaction — had never been checked in either direction before.

Both are re-checked on every run now. What was missing was a way to raise the
budget for a case known to be expensive rather than runaway; asked for upstream
as jasisz/aver#1071, which closed with `[[verify.costly]]` — an `fn`, a file
glob, a `step-limit` and, required, a `reason`. `aver.toml` carries one entry
per corpus that needs it, and the reason field is where the case earns its
budget: the 10,000 byte Script and the 1,911 byte twelve-input Transaction are
each named there as the case they are.

## Refreshing, when Core moves

One command fetches every corpus above and regenerates the case files for any
that changed upstream:

```bash
tools/refresh_corpora.sh          # fetch, compare, regenerate what moved
tools/refresh_corpora.sh --all    # regenerate everything regardless
```

The two families keep their disciplines. The pinned family (sighash, key_io,
base58, BIP341, script_assets) re-emits from Core's file, whose values are the
expectations. The probe family (script_tests, its Witness rows, tx_valid and
tx_invalid) records what this engine answers today, so regenerating it runs
the engine over the fetched rows — ADR 0005's regression discipline.

The script only regenerates; judging the result is the ordinary gates plus
the diff. A new Core row this engine refuses is a finding — the invariant is
0 cases where we refuse what Core accepts — and a changed expectation in the
probe family means the engine changed, which the diff should already explain.
