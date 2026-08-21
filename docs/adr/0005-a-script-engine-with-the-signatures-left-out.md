# A Script engine with the signatures left out

Bitcoin decides whether a spend is allowed by running a small stack language.
Nothing here runs it. `domain/script.av` matches five output shapes by their
leading byte to render an address, and that is all — no opcode is evaluated, no
stack exists.

Aver has no secp256k1. Its whole cryptographic surface is `Crypto.sha256`,
`Crypto.sha256Bytes`, `Crypto.Digest32` and `Crypto.compress`, so `OP_CHECKSIG`
cannot be answered. Writing the elliptic curve arithmetic in Aver is possible —
`Int` is arbitrary-precision — but a verification is some hundreds of modular
multiplications over a 256-bit field, and Block 800000 alone would need
thousands of them. That is a different project and probably the wrong one.

So the engine is worth building without the part that needs a curve, provided it
never pretends to have decided something it has not.

## Three outcomes, not two

```aver
type Ending
    Passed
    Failed(String)

type Outcome
    Decided(Ending)
    Undecided(String)
```

This started as one flat type of three constructors, which is how the rest of
this document describes it. That version was written and did not survive
contact with the verifier: thirty-two `verify-coverage` warnings, every one of
them pointing at a function that decides how a Script *ended* — `settled`,
`topOf`, `passedIf` — and could therefore never return `Undecided`, because
`Undecided` comes from an opcode and never from settling.

Waiving them would have been the fourth time this project papered over an
unreachable arm, and the three previous times the honest fix turned out better.
The honest fix here is that `Undecided` is not an ending, and nesting says so in
the types rather than in a comment. All thirty-two warnings went away without a
single suppression.

Five opcodes need a curve — `OP_CHECKSIG`, `OP_CHECKSIGVERIFY`,
`OP_CHECKMULTISIG`, `OP_CHECKMULTISIGVERIFY`, `OP_CHECKSIGADD` — and four more
need a hash Aver has not got: `OP_HASH160`, `OP_RIPEMD160`, `OP_HASH256` and
`OP_SHA1`. RIPEMD160 is absent as surely as secp256k1 is, and `OP_HASH160` is in
every P2PKH and P2SH output, which is most of the chain. So the reach of this
engine without new primitives is smaller than it first looks, and the two gaps
should be counted apart: one is a hash somebody could add in an afternoon, the
other is elliptic curve arithmetic.

Everything else is decidable today. A script that fails on stack discipline, a
bad push, or an opcode limit still reports `Failed` — a real answer about a real
fault.

A `Bool` here would force a lie in one direction: either every unverifiable
signature passes, or every one fails. The project already refuses that trade
three times over — a body is `Missing` or `Pruned`, never merely absent; a spend
is `Resolved`, `Unknown` or `Invalid`; an audit counts `unresolved` apart from
`faults`. This is the same rule applied to the same kind of ignorance.

## Where the curve plugs in

One module, `domain/ecdsa.av`, exposing one function:

```aver
fn verify(publicKey: Bytes, signature: Bytes, message: Digest) -> Verdict
```

Its body returns `Verdict.Undecidable` and nothing else. When a curve arrives,
that body is replaced and its signature is not — the arrangement `infra/store.av`
already uses for the key-value store it expects to swap for a database. Unlike
that one, this seam has no representation to leak, so `exposes opaque` is not
needed; the whole contract is the one signature.

Everything above it is written against `Verdict` rather than against a curve, so
the day secp256k1 lands the diff is one file and the test suite is what says
whether it worked.

### Update — what the seam turned out to be

Three things about the sketch above were wrong, and the code says so.

`verify` is a reserved word in Aver. The function is `decide`.

`message: Digest` cannot be written. Aver has no way to build a `Digest32` from
bytes it did not hash itself, so a `Digest` parameter would mean the module can
only be handed a hash it computed — the opposite of a seam. It takes `Bytes`.

And there is no `Verdict.Valid`. Nothing in the module can return it, so it
would be a constructor nothing produces, which is the thing this project has now
refused four times. It arrives in the same change that gives `decide` a body,
and because Aver matches exhaustively, adding it will name every caller that has
to start thinking about success instead of quietly treating it as failure. That
is a better property than the one a three-valued stub would have had.

What the seam does answer today is more than nothing. An empty signature always
fails and a public key that is not a public key always fails — the reference
client returns false for both before it does any arithmetic — so both are
decidable, and `Domain.Checksig` uses them. Strict DER and low-S are computed
but offered rather than applied: they are flag-gated, and Blocks below 363,725
contain signatures they refuse.

## What can be finished before then

Most of it, and the hard part especially.

`domain/sighash.av` computes the message a signature is over — legacy, BIP143
for segwit v0, BIP341 for taproot. It is the subtlest code in the whole
undertaking, it is pure, it needs no curve, and BIP test vectors verify it
completely today. The same is true of the opcode table, the parser, the stack
element encoding and every non-cryptographic opcode.

The legacy half of that is now written, and the claim held: all 500 of Bitcoin
Core's `sighash.json` vectors pass, and they are in the repo as cases rather
than as a number in a commit message. The oracle question — how do you know your
reference is right, when your reference is your own understanding twice over —
was settled by writing secp256k1 verification in about thirty lines of Python
and checking that the signature in Block 170 verifies against the message this
produces. A wrong message would not have verified. That is a stronger statement
than any vector file, and it cost less than an hour.

## Update — the segwit message, and a chain we have not got

BIP143 is in, and all nine of the BIP's own examples reproduce exactly: hash
types 0x01, 0x02, 0x03, 0x81, 0x82 and 0x83, native P2WPKH, P2SH-P2WPKH, native
P2WSH and P2SH-P2WSH. Ten of the BIP's stated sighashes were confirmed against
the signatures published beside them, using the same thirty lines of Python
that settled the legacy algorithm, so the vectors are cryptographically sound
rather than merely copied.

What could not be done is the other half of the plan. Issue #6 said to check
against "real segwit transactions from Block 800000 and similar, which we
already hold", and we do not hold them: the Index names heights up to 962,432
but only 262,076 Blocks have bodies, and segwit activated at 481,824. There is
not one segwit Transaction in the directory — confirmed by sampling segments at
50%, 70%, 90% and 99% of the way through and finding no witness marker in any
of them.

That also puts the interpreter's 53 million Input Scripts in perspective. Every
one of them was pre-segwit, which is why only fourteen came back Undecided. The
engine has never been shown a witness.

## Two oracles

Bitcoin Core's `script_tests.json` is the canonical corpus and is adversarial in
a way nothing written here would be. Aver cannot read JSON at verify time, so it
needs converting into cases.

The second is free and specific to this project: `audit` already walks a hundred
thousand Blocks. Every script in them was accepted by the network, so **any
`Failed` on mainnet data is a defect in us**. That is the same shape of argument
as the prefix oracle that made the spend soak worth running — a property the
data guarantees, turned into a test.

## Update — the chain oracle, run

53,317,573 non-coinbase Input Scripts, over every Block held. Four `Failed`, and
all four traced by hand:

| script | what it is |
|---|---|
| `010075` | a push and an `OP_DROP`, leaving nothing |
| `516352676a675168948c` | `OP_IF`/`OP_ELSE`/`OP_ELSE`/`OP_ENDIF` then `OP_SUB` `OP_1SUB`, which comes to zero |
| `4c50…518c` | the genesis Block header pushed as data, then `OP_1` `OP_1SUB` |
| `0101493046…0100` | a signature and some pushes, ending `OP_0` |

Not one of them is a defect. A scriptSig is allowed to end with a false or empty
stack, because consensus never runs one on its own — it runs it followed by the
Output script, and only the end of that is checked. The oracle was over-strict,
not the engine.

That is the second time this oracle was wrong before the engine was. The first
was coinbase Input Scripts, which are arbitrary data that consensus never
executes at all, and which produced 15,000 confident failures. Both corrections
made the number smaller; neither changed a line of the engine. Worth remembering
when the next big number arrives.

Fourteen `Undecided`, every one a scriptSig containing a signature-checking
opcode, which is the right answer and not a gap.

## Update — the engine, run where it belongs

The spike ran Output Scripts on an empty stack, which meant a P2PKH Script died
at its first `OP_DUP` and the reach it reported was fiction. `Domain.SpendScript`
does it properly: the Input's Script, then the Output's on the stack it left,
with a fresh opcode count and a condition stack that has to be empty in between.

Over the first 100,000 Blocks, with the `t:` index built so every parent
resolves:

```
blocks 100000  transactions 216576  spends resolved 116576  coinbase 100000
unresolved 0   scripts 0 passed / 0 failed / 192363 undecided   FAULTS 0
```

192,363 real Input and Output Script pairs, run together the way Bitcoin runs
them. Nothing unresolved, no faults, and not one `Failed`.

All undecided is the right answer and not a disappointment. Every Output in
that range is P2PK or P2PKH, so evaluation runs to `OP_CHECKSIG` or to
`OP_HASH160` and stops there — which is precisely the claim this design was
built to be able to make. The number to watch is `Failed`, because those would
be ours, and it is nought across all 192,363.

It is also the measure this ADR promised: the count of `Undecided` is how much
of consensus is not being checked, and today it is all of it. Nothing here
should be read as saying these spends are valid. It says the engine got as far
as the signature and knows that it cannot go further.

P2SH is not implemented and cannot be reached: a P2SH Output Script begins with
`OP_HASH160`, so it stops on the first opcode for want of RIPEMD160 and the
redeem script is never run. Writing that branch now would be writing something
nothing can enter; it belongs in #10.

## Update — Core's corpus, and what disagreeing with it means

`script_tests.json` is in, converted by `tools/script_tests_to_aver.py` and kept
as `domain/scriptcases1.av` through `scriptcases5.av`. The generator assembles;
the engine answers; Python compares. Keeping those three apart is what stops
this from being a test that agrees with itself.

Of 1,288 rows, 1,120 assemble into Script pairs — the rest are comments or
segwit cases carrying a witness, which this engine does not run. Of those 1,120:

| | |
|---|---|
| agree with Core | 865 |
| undecided — needs a primitive | 159 |
| **we refuse what Core accepts** | **0** |
| we accept what Core refuses | 96 |

The nought is the number that matters. Not one case in Core's adversarial
corpus is refused by this engine and accepted by the reference client, which is
the direction a defect would show up in.

*(Superseded. The table below was hand-maintained and went stale; the corpus now
records Core's expected error and the flags beside every case, so the report is
computed from the file. See the last update in this document.)*

The 96 in the other direction are each attributable to a verification flag this
engine deliberately does not apply:

| count | Core's error | flag |
|---|---|---|
| 54 | `SCRIPTNUM` | `MINIMALDATA` |
| 21 | `MINIMALDATA` | `MINIMALDATA` |
| 9 | `DISCOURAGE_UPGRADABLE_NOPS` | policy, not consensus |
| 8 | `SIG_DER` | `DERSIG` |
| 2 | `NULLFAIL` | `NULLFAIL` |
| 1 | `SIG_NULLDUMMY` | `NULLDUMMY` |
| 1 | `WITNESS_PROGRAM_WITNESS_EMPTY` | segwit, out of scope |

Every one of those rules was switched on by a soft fork after Blocks that
break it were already valid, which is why applying them unconditionally would
reject history. `Domain.StackItem` and `Domain.Ecdsa` compute them and offer
them as questions; nothing asks yet, and what will ask is a flags argument.

The corpus found one real defect, which is what it is for. `Domain.SpendScript`
did not apply the ten-thousand-byte Script limit, because it does not go through
`Domain.Interp.run` — where the limit had been put, with a note saying that a
caller reaching past it has to apply the limit itself. A caller promptly did
not. It is applied in both places now.

Two cases are left out of the generated corpus: Core's maximum-size pair, at
ten thousand bytes each, exhausts the verify VM's million-step budget. The
compiled engine runs both and agrees with Core on both — `OK` for the one at the
limit, a size refusal for the one over it.

## Update — six thousand passes that meant nothing

Asked to run the spend check over recent Blocks rather than early ones, on the
three Blocks the directory holds from August 2023 (799,999 to 800,001):

```
blocks 3  transactions 13018  spends resolved 6332  coinbase 3  unresolved 6683
scripts 6363 passed / 0 failed / 130 undecided   FAULTS 0
```

Six thousand three hundred and sixty-three `Passed`, from an engine that has
never verified a signature. Every one of them was wrong, and not by accident.

Segwit was deployed as a soft fork, which required that a witness program look
valid to every node that could not read it. A P2WPKH Output is `OP_0` and a push
of twenty bytes; run under the old rules that leaves the hash on the stack and
the hash is not zero, so the Script comes out **true**. The same holds for P2WSH
and for Taproot. An engine with no witness evaluation does not fail on segwit —
it passes, silently and confidently, and on a recent Block that is nine spends
in ten.

So the engine was behaving exactly like a 2016 node, which is correct behaviour
for a 2016 node and a lie in a report headed `passed`. This is the failure mode
the three-valued discipline exists to prevent, and it got in anyway, because
`Undecided` only ever came from an opcode refusing to answer and here no opcode
refused: every one of them ran and the answer was true.

With the shape recognised, the same three Blocks read:

```
blocks 3  transactions 13018  spends resolved 6332  coinbase 3  unresolved 6683
scripts 0 passed / 0 failed / 6493 undecided   FAULTS 0
```

Every one of the 6,363 passes became `Undecided`, and 6,493 is exactly the count
of Inputs in those Blocks whose parent the `t:` index holds — counted
independently, outside the engine, before the fix was written. Nought passed and
nought failed, which for a 2023 Block is the whole truth.

`Domain.Script.isAnyWitnessProgram` now asks BIP141's question — any version
from nought to sixteen, any push from two bytes to forty, including versions
nobody has defined — and `Domain.SpendScript` answers `Undecided` before running
such a pair rather than after.

Two things worth taking from it. The first is that a Script engine cannot be
trusted to notice its own ignorance from the inside: soft forks are designed so
that old rules accept what they cannot read, so every future soft fork will look
like a pass to this code, and the only defence is to recognise the shape and
refuse. The second is about the corpus: this cost one case in Core's
`script_tests.json`, which moved out of "we accept what Core refuses" and into
"undecided" — the only case in 1,120 that had been covering this, and it had
been dismissed as an out-of-scope segwit case rather than read.

## Consequences

The engine will never call a Block fully valid, and should not be read as doing
so. `Undecided` is the answer for every real spend until there is a curve, and
the count of them is the honest measure of how much of consensus is being
checked.

Taproot is out of scope for a first version: BIP341 and BIP342 are a different
execution model with a different signature scheme, and folding them in early
would double the surface before any of it is proven.

Speed is the risk that could invalidate the design rather than delay it.
Resolving 116,576 Inputs already takes about three hours, and that is only
fetching parents. If evaluation makes a full soak impractical the shape has to
change, so the interpreter gets measured on a thousand Blocks before the sighash
work starts rather than after.

Two things have to happen first, and neither is part of the engine.
`Domain.Transaction` keeps `witnessItems` as a count and discards the items, so
no segwit script could be evaluated at all. And `Domain.Script` reports P2PK as
`nonstandard`, which is most of the early chain — a classification bug worth
fixing on its own, independent of anything above.

## Update, 16 August 2026 — the spike

A throwaway parser and stack machine, run over real Blocks, to find out whether
speed forces a different design. It does not.

| range | scripts | ops | ms |
|---|---|---|---|
| Blocks 1–1,000 | 2,062 | 4,095 | 1,332 |
| Blocks 90,000–91,000 | 5,858 | 14,172 | 1,634 |

Three and a half times the ops cost a quarter more time, so it is not the ops
that scale — reading and decoding the Blocks is. Against the three hours that
resolving 116,576 Inputs already takes, evaluating their scripts is noise. The
risk this ADR flagged as the one that could invalidate the design is not there.

The same spike measured its own reach badly, in a way worth writing down.
Running an Output script on an empty stack, as it did, means a P2PKH script dies
at its first `OP_DUP` long before reaching `OP_HASH160`, so it reported no
RIPEMD160 problem at all. Real evaluation runs the Input script first and the
Output script on what it leaves. Until the engine does that, any count of what
it can and cannot decide is measuring the wrong thing.

## Update — the flags, and what they were worth

`Domain.Rules` was a Height and the soft forks in force at it. Core runs a
Script under a set of verification flags, which is a different and finer thing,
and every corpus here was being run under `Domain.Rules.latest()` — every rule
on, for every case, whatever the case asked for. n1bor/btc-listener#52.

Two records now, and the split is structural rather than a convention:

* `Domain.Rules` carries the soft forks, because a soft fork has a Height and
  `at(network, height)` can answer for it. P2SH, the two timelock opcodes,
  SegWit, BIP147 null dummy, Taproot.
* `Domain.Policy` carries Policy — rules a node applies over and above what
  the chain enforces — and `Domain.Rules.at` **cannot return
  anything but `none()`** of it. Only `underFlags` and `exceptFlags`, which take
  Core's own flag names, can return anything else, and only a corpus calls
  those. A Policy rule reachable from a Height would make the auditor
  reject Transactions that are in the chain, which is the one outcome that must
  not happen.

What that was worth, measured before and after:

| corpus | cases | disagreements before | after |
|---|---|---|---|
| `tx_valid` + `tx_invalid` | 213 | 19 | **3** |
| `script_tests` Witness rows | 108 | 23 | **17** |

The three left in the Transaction corpus are one `DERSIG` and two
`DISCOURAGE_UPGRADABLE_WITNESS_PROGRAM`; the seventeen are ten
`WITNESS_PUBKEYTYPE`, six `MINIMALIF` and one `DISCOURAGE_UPGRADABLE_WITNESS_PROGRAM`.
Every one is a flag with no field yet. **Nought in either corpus is this engine
refusing what Core accepts**, which is the direction a defect shows up in, and
the tools now report the two directions separately rather than summing them.

Three rules landed with the plumbing. `CONST_SCRIPTCODE` refuses
OP_CODESEPARATOR in a legacy Script and refuses a FindAndDelete that actually
bit; `NULLDUMMY` is BIP147; `DERSIG` is left for #22, which gives it a Height as
well as a flag rather than half of each.

One thing the corpus caught about itself. BIP147 came into force with SegWit on
every Network, so it started as a derived field rather than one of its own —
and `tx_valid.json` has three cases that turn NULLDUMMY off while leaving
WITNESS on, which Core can do because they are independent flags there. Folding
them together made this engine **refuse all three**: strict where Core was
lenient, which is the rarer and more alarming of the two ways to be wrong. One
field per rule now, even where two rules have had the same answer on every
Block ever mined.

## Update — the Script corpus can be audited from the file

n1bor/btc-listener#70. `script_tests.json` carries Core's answer — element five
of each row is the expected script error, `OK` for a pair that verifies — and
the generator was throwing it away along with the flags. So the agreement lived
in the hand-maintained table above and went stale, and 1118 rows collapsed to
997 distinct pairs because rows differing only by their flags became literal
duplicates.

Both are now on every case. Recomputed from the file rather than from memory:

```
cases        1118
agree         932
disagree       99
undecided      87  (this engine cannot answer; not a disagreement)

  we accept what Core refuses  99  (a rule not implemented)
  we refuse what Core accepts   0  (the direction a defect shows up in)

disagreements by the error Core expected:
  SCRIPTNUM                     54
  MINIMALDATA                   21
  DISCOURAGE_UPGRADABLE_NOPS    10
  SIG_DER                        8
  SIG_PUSHONLY                   4
  NULLFAIL                       2
```

The stale table said 96 and named `WITNESS_PROGRAM_WITNESS_EMPTY` and
`SIG_NULLDUMMY`, neither of which is a disagreement any more, and did not
mention `SIG_PUSHONLY` at all.

## Update — a list literal that lost 92 percent of its cases

Getting the Script probe to run at all turned up an Aver bug worth recording,
because of the shape of it rather than the size. jasisz/aver#1054.

The probe walks a list literal of every case. Run under `aver run`, 1118 cases
printed 94 answers. Exit 0, nothing on stderr, `aver check` clean. The list
literal itself was short: `LIST_NEW` carries an 8-bit element count and the
compiler emits `items.len() as u8`, so a literal of *n* elements becomes *n mod
256* — 1118 mod 256 is 94, 400 gives 144, 256 gives nought. The compiled Rust
backend is correct, so the VM and the binary disagree about the value of a
constant.

The only reason it was caught is that the tool refuses to write answers back
when the count does not match the case count. Without that it would have
recorded 94 answers against 1118 cases and printed a cheerful agreement report
about the 94. The probes are written in parts under the limit now, joined by
`List.concat`, with a `verify assembled` case that counts the result — so a part
over the limit fails loudly instead of quietly shortening the corpus.

## Update — MINIMALDATA, and the 75 cases it was worth

n1bor/btc-listener#69. `Domain.StackItem.isMinimalNumber` and `isMinimalPush`
were written, exposed and **called by nothing** — the same shape
`Domain.Ecdsa.isValidEncoding` is still in. Between them they account for 75 of
the 99 Script disagreements: 54 recorded by Core as `SCRIPTNUM` and 21 as
`MINIMALDATA`.

Two rules, and Core has two error names for them because they catch different
mistakes:

* **The push.** `CheckMinimalPush` in `EvalScript`, under `fExec` — a push
  written the long way *inside a branch that is not taken* is not an error.
  That is the opposite of CONST_SCRIPTCODE two lines above it, which is refused
  in an untaken branch, and getting the two the same way round matters.
* **The number.** `CScriptNum(vch, fRequireMinimal)` wherever a stack item is
  read as a number. The order inside that constructor is load-bearing: it
  throws on the width *before* it looks at the encoding, so a five-byte operand
  that is also written the long way is refused for its width. Asking the other
  way round gives a different error name and Core's corpus notices.

Everywhere a number is read now goes through
`Domain.StackItem.arithmeticFault`, which answers both in that order: the
arithmetic opcodes, OP_WITHIN, the OP_PICK and OP_ROLL depth, the
OP_CHECKMULTISIG key and signature counts, the OP_CHECKSIGADD count, and the
timelock number — which is five bytes wide rather than four, and Core says why
in as many words: a four-byte lock time would give Bitcoin a year 2038 problem.

```
cases 1118   agree 932 → 1009   disagree 99 → 24   undecided 87 → 85

  we accept what Core refuses  24
  we refuse what Core accepts   0

  DISCOURAGE_UPGRADABLE_NOPS  10
  SIG_DER                      8
  SIG_PUSHONLY                 4
  NULLFAIL                     2
```

**Two of the multisig sites had no width check at all**, flag or no flag. The
key count and the signature count were read straight off the stack with
`asNumber`, so a five-byte count would have been read as a number where Core
refuses it outright — and that one is not a flag, `CScriptNum` throws whatever
the flags say. It is a consensus rule this engine did not have, found by
implementing a Policy one next to it.

Two files had to be split to make room, and both cuts name something real.
`Domain.ScriptNumOps` holds the opcodes that read a number off the stack, which
is the machinery `Domain.ScriptMath`'s intent explicitly says is not its.
`Domain.Multisig` holds OP_CHECKMULTISIG, which is a different shape from every
other opcode — variable depth, its own opcode charge, and a matching loop with
consensus written into its order.

## Update — the rest of the Script flags, and a consensus rule they found

DISCOURAGE_UPGRADABLE_NOPS, SIGPUSHONLY and NULLFAIL, which between them are
16 of the 24 disagreements left after MINIMALDATA.

```
cases 1118   agree 1009 → 1026   disagree 24 → 8   undecided 85 → 84

  we accept what Core refuses  8
  we refuse what Core accepts  0
```

**Every disagreement left is `SIG_DER`**, which is #22.

The three rules are small. DISCOURAGE_UPGRADABLE_NOPS refuses OP_NOP1 and
OP_NOP4 to OP_NOP10 — not OP_NOP, which is spelled for doing nothing and is not
going to become anything, and Core has the two in separate cases of the same
switch for that reason. SIGPUSHONLY refuses an Input Script that does anything
but push, asked first of everything because Core asks it at the top of
`VerifyScript`. NULLFAIL requires a signature check that came out false to have
been handed the empty push; for OP_CHECKMULTISIG that is *every* signature, not
just the one that failed, which is what the `ikey2` counter in Core's cleanup
loop is for.

**What they found is worth more than the three of them.** SIGPUSHONLY only
accounted for one of the four cases Core marks `SIG_PUSHONLY`. The other three
carry flags `P2SH` and `P2SH,STRICTENC` — they never turn SIGPUSHONLY on at
all. They are BIP16:

```cpp
if ((flags & SCRIPT_VERIFY_P2SH) && scriptPubKey.IsPayToScriptHash())
{
    // scriptSig must be literals-only or validation fails
    if (!scriptSig.IsPushOnly())
        return set_error(serror, SCRIPT_ERR_SIG_PUSHONLY);
```

This engine knew the rule and drew the wrong conclusion from it. It used
push-only as one of the conditions for *deciding whether to re-run the redeem
Script* — so an Input Script that broke it was quietly treated as not a P2SH
spend at all, and the answer stopped at the outer run, which hashes the top
item, compares it, leaves true and passes. Core fails it. A consensus rule,
misread as a classification rather than a refusal, and the third one this
sequence has turned up by implementing a Policy rule next to it.

## Update — BIP66, and the measurement that earned it

n1bor/btc-listener#22 held strict DER back for two years on the ground that
the chain here stopped below Height 363,725, so wiring it in would ship
consensus code no measurement could reach. That is no longer true: the
directory holds Block 363,725 and the ones either side, and `txindex` resolves
across the activation.

`Domain.Ecdsa.isValidEncoding` is called now, from both signature paths, as a
refusal rather than a false answer — Core has `CheckSignatureEncoding` above
`CheckECDSASignature` and returns false out of `EvalScript`, which is not the
same as pushing an empty item.

**The Script corpus now disagrees with Core about nothing.**

```
cases 1118   agree 1026 → 1048   disagree 8 → 0   undecided 84 → 70
```

The undecided count fell too: fourteen cases that used to answer *needs the
transaction* are settled by the encoding refusal, which happens before any
message is needed.

Two things about the placement are Core's and not obvious.

**The empty push is exempt**, and not as a special case — `CheckSignatureEncoding`
returns true for a zero-length signature before it looks at anything. An empty
signature is how a Script says it has none; it goes on to fail the check and
push false rather than failing the Script.

**In OP_CHECKMULTISIG the encoding is asked inside the matching loop**, once
there is a key to try the signature against, and never for a signature left
over when the keys run out. Core exits that loop on `fSuccess` before it looks
at the encoding. Checking them all up front would have been simpler and would
have refused Scripts Core accepts, which is the direction that matters.

### Does the Height carry any weight?

A field that never changes an answer is decoration, so it was measured
directly: `strictDerHeight(Network.Mainnet)` pinned to 1, recompiled, and four
slices below the real activation re-run.

| slice | scripts | failed |
|---|---|---|
| 170,000–170,050 | 3,596 | **56** |
| 250,000–250,050 | 39,262 | **105** |
| 300,001–300,050 | 34,964 | 0 |
| 363,000–363,050 | 117,233 | 0 |

So the chain really does contain signatures that BIP66 refuses — a hundred and
sixty-one of them in two slices of fifty Blocks — and applying today's rule to
2012 and 2013 would reject spends the network accepted. They have died out by
Height 300,000, which is why the two slices nearest activation are clean and
why the soft fork could be scheduled where it was.

With the real Height restored, either side of activation:

```
363,000–363,724   1,884,727 scripts   0 failed   0 faults   (lax)
363,725–364,400     863,901 scripts   0 failed   0 faults   (strict)
```

That experiment also turned up n1bor/btc-listener#76: the summary line ends
`FAULTS 0` in capitals while a hundred and five failed Scripts sit earlier in
the same line, and the first read of it was *good, no faults*.

## Update — every case, and a consensus rule that was never there

MINIMALIF, WITNESS_PUBKEYTYPE and DISCOURAGE_UPGRADABLE_WITNESS_PROGRAM, which
were the last nineteen disagreements across the three corpora.

| corpus | cases | agree | disagree | undecided |
|---|---|---|---|---|
| `script_tests.json` Script pairs | 1118 | 1048 | **0** | 70 |
| `script_tests.json` Witness rows | 108 | 108 | **0** | 0 |
| `tx_valid` + `tx_invalid` | 213 | 213 | **0** | 0 |

**This engine and Bitcoin Core now agree about every case in every corpus read
here**, in both directions. The seventy undecided are Script pairs whose answer
needs a Transaction the row does not carry, which is the honest answer and not
a disagreement.

**MINIMALIF was not implemented at all, and half of it is consensus.** Under
witness v0 it is Policy and the flag asks for it. In tapscript BIP342 wrote it
into consensus — there was no history to protect — so a taproot spend whose
OP_IF takes anything but the empty push or a single one is invalid, flag or no
flag. This engine accepted them. Signet has had Taproot since Height 1 and the
ranges audited here contain none, which is why nothing caught it: absence of
evidence, and the corpus was the evidence.

The rule splits cleanly in two and the two halves went to two places.
`Domain.SpendContext` answers whether it is in force, because that turns on the
Script version; `Domain.StackItem` answers whether the item satisfies it. It is
narrower than minimal number encoding — OP_IF casts to a Bool and every
non-zero item casts to true, so without it a Witness could carry any of a great
many items where a one is meant and change the Transaction Id by choosing
between them.

Two placements were got wrong first and Core's corpus said so.

**The uncompressed key is measured on the turn of the loop that reaches it.**
Checking only the first key missed two cases: a P2WSH multisig whose *second*
key is uncompressed and whose signature is the second key's, so the loop
reaches the bad key only after the first has failed. Core's `ikey++` is
unconditional and `CheckPubKeyEncoding` sits at the top of the same `while`,
so the key measured is that turn's key.

**A version one program below the Taproot Height is not discouraged.** Core
returns success from that branch before the flag is consulted: the shape is
known there, only the rules are not in force yet, and refusing it would refuse
a Transaction the chain contains. Only genuinely unknown versions — and a
version one program of the wrong width, or nested inside P2SH — reach the
discourage.
