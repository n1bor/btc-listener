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

Over the first 30,000 Blocks, with the `t:` index built so every parent
resolves: **2,869 spends, 0 failed, 0 passed, 2,869 undecided.** Nothing
unresolved and no faults.

All undecided is the right answer and not a disappointment. Almost every Output
in that range is P2PK — a public key and `OP_CHECKSIG` — so evaluation gets all
the way to the signature check and stops there, which is precisely the claim
this design was built to be able to make. The number to watch is the count of
`Failed`, because those would be ours, and it is nought.

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
