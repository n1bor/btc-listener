# Script laws and executable explanations

The Aver pin in `.aver-version` includes `because`, `using` and checked list
induction. The Script laws can be checked together, including their imported
helpers, from the interpreter entry:

```sh
aver proof domain/interp.av --module-root . --check-json -o /tmp/script-laws
```

At pin `c5e6faddec2ce61cfca9f7521677a72335bf49af`, Lean reports **99 universal
laws, 2 bounded laws and no open laws**. The strict command still exits 1
because 130 non-law claims in the wider graph are deliberately declined. The generated `proof_manifest.json`
records the tier and kernel dependencies of each law.

Of the original twenty laws in PR #338, eighteen now close universally, up
from fourteen. The extra eighty-one laws are counted separately: eight
roundtrip and induction additions, seventeen canonical-encoding laws, twelve
helpers that establish the exact arithmetic-width boundary, and fifteen
helpers that establish the CompactSize roundtrip, eight laws for stable
Script filtering and deletion algebra, and nineteen laws establishing exact
Script parse/serialize roundtrip, plus two laws connecting concatenated deletion
batches to the public hex wrapper. The existing function bodies and public API are unchanged apart
from the original PR's `littleEndian` guard repair.

## Number guarantees

These laws quantify over all Aver integers, including values larger than the
four-byte arithmetic operand range:

- `asNumber.readsWhatFromNumberWrote`: reading an encoded number gives the
  original number, for either sign and across sign-byte boundaries.
- `isMinimalNumber.acceptsWhatFromNumberWrites`: the encoding has no
  redundant top byte.
- `fromNumber.encodingIdentifiesTheNumber`: two encodings are equal exactly
  when their numbers are equal. Different numbers cannot collide.
- `asNumber.rewritingPreservesTheNumber`: reading an item, writing its number
  minimally and reading it again preserves the value. This includes redundant
  zero bytes and negative zero; it does not claim the original bytes survive.

The first two use executable explanations which split zero from nonzero,
name the most significant digit, and connect its properties to sign placement.
The next two cite the proved roundtrip with `using`; no new justification
function is needed for either consequence.

## Canonical Script numbers

For every list whose elements are octets (`0 <= byte < 256`),
`fromNumber.canonicalExactlyWhenMinimal` proves:

```text
fromNumber(asNumber(bytes)) == bytes  iff  isMinimalNumber(bytes)
```

Thus the minimality checker recognizes exactly the fixed points of number
normalization. Negative zero `[128]` normalizes to `[]`; redundant `[1, 0]`
normalizes to `[1]`; the necessary sign byte in `[128, 0]` survives.
The octet premise matters: the out-of-domain list `[256]` passes the minimality
predicate but normalizes to `[128, 128]`, so the theorem deliberately excludes it.

The hard direction is `fromNumber.minimalItemsAreFixedPoints`. Its two
explanations first recover the magnitude digits, then restore the top byte or
separate sign byte. `bigEndian.writingReadDigitsPreservesThem` supplies checked
list induction: the recursive explanation consumes one byte and updates the
positive prefix. Each explanation and the final implication are independently
universal and kernel-audited. The Aver pin includes generic compiler fixes found
while checking these proofs; no Bitcoin-specific compiler logic or handwritten
Lean is used.

The reverse direction cites the already-proved minimality of every encoder
output. `asNumber.minimalEncodingIdentifiesBytes` then proves that two minimal byte
encodings are equal exactly when they decode to the same number.

## An induction written in Aver

`bigEndian.largerPrefixStaysLarger` says that reading the same list preserves
the strict order of two accumulators. It holds for every integer list, so it
also holds for byte lists of any length:

```aver
fn largerPrefixReason(bytes: List<Int>, lower: Int, upper: Int) -> Bool
    match bytes
        [] -> lower < upper
        [head, ..tail] -> Bool.and(
            largerPrefixReason(tail, lower * 256 + head, upper * 256 + head),
            bigEndian(bytes, lower) < bigEndian(bytes, upper)
        )
```

The law assumes `lower < upper`, cites this function with `because`, and has
`using []`. Both accumulators advance together; the checked decreasing
argument is the list tail. Lean checks the recursive step under the original
assumption, checks that assumption at the recursive call, and proves the
original law from the explanation. The function is also run by ordinary
`verify` and `verify --hostile`.

Measured with the same compiler and source, this law is bounded without proof
annotations, open with `using []` alone, and universal with the recursive
explanation. Both explanation and implication receive universal credit.
The source theorem uses only `Classical.choice`, `Quot.sound` and `propext`;
it has no `sorryAx` or dependency on sample evaluation.

## Exact arithmetic-width boundary

`fitsArithmetic.acceptsCoreOperandRange` is now universal:

```text
fitsArithmetic(fromNumber(value))  iff  -2147483648 < value < 2147483648
```

This includes both signs and excludes both endpoints. A magnitude of 2147483648
needs another byte for its sign, so even -2147483648 is outside the four-byte
operand range. The proof composes the exact one-, two-, three-, and four-byte
thresholds: 128, 32768, 8388608 and 2147483648.

The common step is `signedBytes.lengthStep`: above a single unsigned byte,
the low byte adds one to the length of the quotient's signed encoding.
`sizeRecurrenceReason` is the same executable explanation at each threshold.
The sign-placement law preserves a prepended byte whenever the tail is nonempty;
zero and magnitudes below 256 provide the base cases. Existing production
function bodies and the public API stay unchanged.

This exposed a generic Aver bug: `using` lemmas disappeared from the final
implication when `because` was present. Aver PR #1296 removes that exception.
No width-specific compiler rule or handwritten Lean is involved.

## CompactSize preserves the following field

`CompactSize.encode.readsBack` is universal for every unsigned 64-bit value
and every trailing list:

```text
read(encode(value) ++ rest)
  == Count(value, rest, length(encode(value)))
```

The decoder recovers the value, consumes exactly the encoded field, and leaves
the complete suffix untouched. This covers every marker boundary (253, 65536,
4294967296), including the maximum value 18446744073709551615. It asserts a
roundtrip for encoder output; it does not claim that the decoder rejects
noncanonical external encodings.

Fifteen helper laws establish fixed-width little-endian readback, field length,
and suffix preservation. `readBackReason` is an ordinary private Bool function
splitting the four wire widths: 1, 3, 5 and 9 bytes. The helper width guards cover
up to eight payload bytes, so hostile checks stay executable even when they
try extreme integers. The original roundtrip domain is unchanged.

Aver PR #1298 derives a native countdown measure from existing guard and shrink
checks and makes its equations available to `using`. This removes law-family
special cases in the compiler. It also fixes imported record identities inside
explanations. The complete proof is Aver source; no handwritten Lean is needed.

## Signature deletion is stable under repetition and reordering

For arbitrary operation lists and arbitrary lists of signature payloads,
`ScriptParse.withoutEach` now has two universal laws:

```text
withoutEach(withoutEach(ops, items), items) == withoutEach(ops, items)
withoutEach(withoutEach(ops, left), right)
  == withoutEach(withoutEach(ops, right), left)
```

Repeating a batch of deletions has no further effect, and two batches can be
applied in either order. These quantify over lists of any length, including
repeated payloads. They preserve the exact surviving operations and their order.

The reference function `retained` compares each complete encoded operation with
the target bytes. `without.stableFilter` proves that the production accumulator
implementation is exactly `reverse(acc) ++ retained(ops, target)`. The remaining
lemmas establish single-deletion idempotence and commutation, move one deletion
past a batch, and then induct over entire batches. The Bool explanations are
ordinary private functions with executable examples; no public API is added.

The distinction between encodings matters: deleting the minimal push `[1, 171]`
removes `Op.Push(1, [171])`, while `Op.Push(76, [171])` survives. Equal payloads do
not imply equal serialized operations. These internal laws concern `withoutEach`
on arbitrary operation lists.

The public API now has `withoutPushes.repeatedItemsHaveNoFurtherEffect`:

```text
withoutPushes(scriptHex, items ++ items) == withoutPushes(scriptHex, items)
```

This equality quantifies over every String and every finite list of integer
payload lists, with no validity or length premise. Both sides return exactly
the same `Result`: malformed hex and truncated pushes retain their error;
successful outputs have identical lowercase hex, preserving every surviving
operation's encoding. For example, deleting `[171]` twice from
`"5101AB4C01AB52"` yields `Ok("514c01ab52")`: the nonminimal push survives.

The helper law `withoutEach.concatenatedBatches` proves that processing
`left ++ right` equals processing `left` and then `right`. Its executable
`batchConcatReason` follows the left batch. The wrapper law selects that
lemma and the existing idempotence law with `using`; the existing Aver pin
closes the composition without compiler changes or handwritten Lean.

The public law repeats the payload batch in one call. Feeding the output hex
back into a second `withoutPushes` call is a separate claim: it additionally
requires proving that decoding and parsing the filtered serialization recover
the surviving operations. The byte roundtrip below goes in the other direction
and does not establish that premise.

Aver PR #1303 supplies generic structural-equality reflection and equations for
mutually recursive functions whose termination is already checked. Independent
record, container, import-collision and packet-filter tests cover these fixes;
Float and structures containing Float retain their nonreflexive NaN semantics.

## Parsing preserves exact Script bytes

For every finite list of octets, `ScriptParse.parse.preservesExactBytes`
proves:

```text
parse(bytes) == Ok(ops)  implies  bytesOf(ops) == bytes
```

This preserves the original representation, including nonminimal pushes and
zero bytes in multi-byte length fields. For example `[76, 1, 170]`,
`[77, 1, 0, 170]`, and `[78, 1, 0, 0, 0, 170]` each survive byte-for-byte;
none is shortened to `[1, 170]`. This matters because changing the serialized
Script changes what the signature machinery hashes.

The theorem assumes `validOctets(bytes)`, not a bound on the list's length.
The byte premise is necessary: `[77, 256, -1]` parses but serializes as
`[77, 0, 0]`; an explicit negative example checks this excluded input.
It makes a claim about successful parses; truncated pushes retain their
existing errors. It does not assert Script execution validity or a roundtrip
for arbitrary manually constructed `Op` values.

Eighteen helper laws establish serializer composition, preservation of valid
slices, length-field read/write identity, complete and truncated parser steps,
and the final induction. `parseReason(bytes, acc)` is executable Aver that
follows the actual remaining input, including named and nested slices.
`preservesBytes` proves the explanation for every valid octet list and
accumulator. Its induction hypothesis applies to shorter lists; recursive
calls must still establish the octet premise. There is no separate step-list
parameter or guide-length premise.
The internal length-field codec theorem is limited to eight bytes; supported
push length fields are at most four. The original Script theorem has no
length bound.

Aver PRs #1305, #1307 and #1310 reuse checked function equations, preserve
sample types, and split nested explanation cases before ordered facts. The
last fix is covered by an independent integer-list traversal and a false
strict-positivity control. All Bitcoin-specific facts remain ordinary Aver
laws. Aver PR #1314 shares alias and nested-slice
analysis across singleton and mutual recursion, and derives the fallback
induction from the checked list length. This removes the parser explanation's
step-list guide while preserving the original roundtrip claim. No handwritten
Lean or Bitcoin-specific compiler recognition is used.

The ScriptParse export alone reports 39 universal laws, zero open laws and
zero build errors. Its three provider-related non-law refusals remain explicit.

## Chainwork composes and preserves comparison

Three additional laws live in `domain/chainwork.av`, outside the interpreter
entry's law count:

- `over.preservesDifference`: processing the same contributions preserves the
  exact difference between two initial totals.
- `over.chunksCompose`: processing `prefix ++ suffix` equals processing the
  prefix and then continuing with the suffix from its resulting total.
- `heavier.sameWorkPreservesChoice`: adding the same contributions to candidate
  and incumbent preserves the strict heavier decision, including ties.

Two private recursive Bool explanations supply list induction with changing
accumulators. The comparison law then cites the exact-difference theorem. This
needed no new compiler mechanism. The claims concern identical contributions;
they do not establish header validity or the correctness of compact-target work
arithmetic, and do not assert that two different tips can share a valid extension.

Four more landed with n1bor/btc-listener#345, which found `usable` asking
whether the *mantissa* was zero where Core's `GetBlockProof` asks whether the
*target* is: bits `0x01000001` have a mantissa of one and a target of zero, and
were credited with the whole space. `usable` now refuses on the target, after
the cheap refusals (a negative field, a negative mantissa, an overflowing
exponent), so every Int is answered without unpacking it:

- `ofBits.zeroTargetProvesNothing`: `when zeroTarget(bits)`, the work is 0.
- `ofBits.neverNegative`: the work is never below zero, on any Int, which is
  what makes `added` and `over` monotone along a branch.
- `overflowing.isAnExponentAboveThirtyFour`: the exponent refusal against the
  form Core's `SetCompact` uses. The mantissa-bit refusal and "the work is
  never negative" are cases: relating `Bits.and` to `Int.mod` and the
  division to its sign are beyond the auto-prover, and stated as laws they
  land on `sorry`, which the second proof entry (#349) forbids.

Check this cone separately:

```sh
aver proof domain/chainwork.av --module-root . --check-json -o /tmp/chainwork-laws
```

It reports 46 universal laws (43 imported and these three), no bounded or open
laws and zero build errors. Its 61 declined non-law claims remain explicit, so
the unbudgeted strict command exits 1.

## Segment placement: the writer's arithmetic is the reader's

Six laws in `domain/segment.av` (n1bor/btc-listener#357), the pure half of
#327. `place` derives a Location from the count the process carries, and
`nextHeader` / `payloadAt` / `complete` are what `reindex` reads a Segment
back with after a crash; nothing stated that the two agree until these.

- `place.recordEndsAtUsed`: the record placed ends exactly at the new
  `used`, in the Segment the state names, as long as the Block, past a header.
- `place.consecutiveRecordsTile`: two consecutive placements either open the
  next Segment at its first record or start one header past where the first
  ended -- no gap, no overlap.
- `place.staysUnderCap`: a record that fits under the cap never takes `used`
  past it.
- `place.agreesWithTheReader`: when the Segment does not roll, the offset is
  `payloadAt(used)`, the state after is `nextHeader(used, n)`, and `complete`
  holds for the record against a Segment that size.
- `headerFor.readsBack`: `lengthOf(headerFor(n)) == Ok(n)` for every
  `0 <= n < 2^32`, citing `Domain.Message.littleEndian.fourBytesReadBack`.
- `nameOf.sortsWithSegment`: the file names sort as the Segment numbers do,
  below a million.

A seventh pair pins the effectful fix: `agreesWithDisk.theDiskItCountedAgrees`
(the size the count says is accepted) and `agreesWithDisk.anyOtherSizeRefuses`
(any other size is refused by name, with both numbers and the Segment).

**Segment is outside the interpreter's proof cone**, so these are not in the
CI proof job's count and have no Lean tier yet. They are checked by
`aver verify domain/segment.av --module-root .` and by the same command with
`--hostile`, which is where the `when` guards come from: a negative `used`
or a negative Block length is not a world the writer is ever in, and a
`rolls` case is exactly the one `agreesWithTheReader` is not about.

## Validation of the latest additions

The final source passes `aver check . --module-root .` for all 149 modules and
whole-project formatting. Native `verify main.av` checks 111 reachable modules:
8958 passing cases, 464 guard skips, no failures. ScriptParse hostile checks
pass 2502 cases with 667 guard skips and no failures. Local provider paths were rebound only in a disposable runtime copy.
The complete application compiles to wasm-gc and passes `wasm-tools validate`.

All universal laws and their explanation obligations were checked against the
manifest: only `Classical.choice`, `Quot.sound` and `propext` occur. Every existing
interpreter law retains its previous tier.

## Remaining limits

There are no open laws in this export. The command still reports 130 declined
non-law claims in the wider interpreter dependency cone; they are neither exported
nor proved and are separate from the law counts.

Two laws retain bounded credit: `ScriptState.rearranged.staysWithinDeclaredDepth`
and `StackItem.isMinimalPush.directPushIsMinimalUnlessSmallNumber`. All previously
universal laws retain their credit.

## Laws added after #338 (n1bor/btc-listener#337)

Nine laws over the engine's bookkeeping and its arithmetic table, landed with
the proof job (#341) gating them. Tiers as the gate measured them at pin
`b6a37c82`: **107 universal, 3 bounded, 0 open, 130 declined**.

| law | pins | tier |
|---|---|---|
| `ScriptMath.unaryValue.unaryValueSpec` | the unary table against a spec whose `?` block quotes Core's `EvalScript` line per opcode | universal |
| `ScriptMath.binaryValue.binaryValueSpec` | the binary table the same way, `a` the deeper operand | universal |
| `ScriptMath.binaryValue.commutative` | ADD, BOOLAND, BOOLOR, NUMEQUAL, NUMNOTEQUAL, MIN, MAX commute | universal |
| `ScriptState.executing.executingSpec` | executing is "every open branch is taken" | universal |
| `ScriptState.settled.settledSpec` | an open OP_IF fails the Script; otherwise the top decides | universal |
| `ScriptState.spent.onlyOpcodesCount` | only opcodes above OP_16 count against the limit | universal |
| `ScriptState.rearranged.lengthSpec` | how many items each of the thirteen shuffles leaves | universal |
| `ScriptStep.landed.neverOverLimit` | a Step continues exactly when both stacks together fit in 1000 | universal |
| `ScriptParse.parse.directPushRunsPastTheEnd` | the error string for a direct push with no data, for `1 <= n <= 75` | bounded (`when`) |

## Laws added with the Regtest P2SH Height (n1bor/btc-listener#346)

`Domain.Rules.at(Regtest, 0)` had SegWit on and P2SH off, because the P2SH
Height table said 1 for regtest where the SegWit table said 0, and Core's
`VerifyScript` asserts WITNESS never comes without P2SH. The table entry is
now 0, and three laws over `at` pin the shape of every Height table against
the rules Core states rather than against a value someone copied:

| law | pins | tier |
|---|---|---|
| `Rules.at.witnessImpliesPayToScriptHash` | `segWit ⇒ payToScriptHash` on all four Networks at every activation Height and its neighbour; fails on the old table at (Regtest, 0) | universal |
| `Rules.at.rulesOnlyTurnOn` | every rule in force at `h` is in force at `h + 1` (`eachRuleOn`, seven fields); Core's `DeploymentActiveAt` is monotone and nothing turns a soft fork off | universal |
| `Rules.at.auditorGetsNoPolicy` | `at(n, h).policy == Policy.none()` on every Network: the #52 guarantee the module intent relies on, stated executably | universal |
| `Rules.onOrStays.isImplication` | the one-field helper is material implication | universal |

`Domain.Rules` is inside the `domain/interp.av` cone, so these are checked by
the existing proof job and ratcheted by `proof/interp.manifest.json`. Measured
locally at pin `b6a37c82` with the committed budgets: **111 universal, 3
bounded, 0 open, 130 declined**, `--gate` reporting 0 regressions and the four
as new. The implication law needed stating through `onOrStays` with
`using [onOrStays.isImplication]`; written as a bare `Bool.or` over the two
projections it opened at the implication and landed on `sorry`.

## CLTV and CSV against Core (n1bor/btc-listener#352)

Four laws over `Domain.LockTime`, all universal at pin `c4b08179` (**123
universal, 1 bounded, 0 open, 134 declined** for the cone, 0 regressions):

| law | pins | tier |
|---|---|---|
| `LockTime.lockTimeChecked.agreesWithCoreCheckLockTime` | `Continue` exactly when `cltvSpec`: same side of 500,000,000, `value <= lockTime`, sequence not final (Core's `CheckLockTime`) | universal |
| `LockTime.sequenceChecked.agreesWithCoreCheckSequence` | `Continue` exactly when `csvSpec`: disable bit on the value passes; else version >= 2 as uint32, Input's disable bit clear, same type bit, masked value <= masked sequence (Core's `CheckSequence`, BIP68/112) | universal |
| `LockTime.checked.isANopBelowTheFork` | under rules where `stillNop`, the opcode is `Continue(state)` even on an empty stack | universal |
| `LockTime.checked.neverPops` | a `Continue` carries the State untouched (BIP65: the top item is not popped) | universal |

The five-byte operand width is pinned by cases either side of five: stated as
a law over the whole Step for any item, the export landed on `sorry`.

## The two truthiness laws (n1bor/btc-listener#344)

Both #337 proposals that did not land the first time now close universally,
with core axioms only, measured locally at pin `b6a37c82` on top of the
Rules laws: **117 universal, 3 bounded, 0 open, 130 declined** for the cone
(113 before the Rules laws are counted; `--gate` reports six new laws and
no regression).

| law | pins | tier |
|---|---|---|
| `StackItem.isTruthy.zeroIsFalse` | `isTruthy(fromNumber(n)) == (n != 0)`; `because truthinessReason` names the most significant digit and follows it through sign placement, citing `mostSignificantDigitIsNotZero`, `outsideByteRangeIsNeverAdded`, `zeroDigitWindow` and `placed.positiveTopIsTruthy` | universal |
| `StackItem.isTruthy.negativeZeroIsFalse` | `isTruthy(item ++ [128]) == anyNonZero(item)`: a sign byte on nothing is false (BIP62 rule 3); `because negativeZeroReason` reads the reversal, citing `anyNonZero.reversal` | universal |
| `StackItem.placed.positiveTopIsTruthy` | a positive top digit survives placement as a set byte | universal |
| `StackItem.anyNonZero.setHeadIsEnough` | a set head decides | universal |
| `StackItem.anyNonZero.concatenation` | set-in-the-join is set-on-either-side, by induction on the left | universal |
| `StackItem.anyNonZero.reversal` | reversal does not change whether anything is set | universal |

The `simp` heartbeat timeout #344 recorded for the second law did not
recur once the reversal was stated as its own law and cited with `using`
rather than left to the structural induction.

The remaining #337 proposal, that `minimalPush` is `isMinimalPush`-minimal, is false as stated:
`CScript() << vch` writes `[1]` as a one-byte push, which MINIMALDATA refuses
in favour of OP_1, and `isMinimalPush.directPushIsMinimalUnlessSmallNumber`
already pins exactly that.

## Connect, Undo and BIP30 (n1bor/btc-listener#354)

Nine laws over `Domain.Connect` and `Domain.Disconnect`, gathered by the
second proof entry `domain/laws.av`, which now depends on both. Writing the
round-trip law found that BIP30 was not enforced — a Block re-creating an
Output the Set held overwrote it and its Undo Data later deleted the original
— so `connectedUnless` refuses such a Block first, with Core's two mainnet
exceptions at 91842 and 91880 excused, and the law is stated without a
`when` for it. Measured at pin `c4b08179`: the entry goes from **59
universal, 1 bounded, 0 open, 66 declined** to **62 universal, 2 bounded, 0
open, 95 declined**; `--gate` reports four new laws and no regression, and
`proof/laws.declined` rises from 66 to 95 for a written reason (below).

| law | pins | tier |
|---|---|---|
| `Connect.connected.valueIsConserved` | an applied Block adds at most the subsidy plus what it removed (`conservedIn`); `when` bounds the Height to the subsidy's last halving | declined |
| `Connect.connected.noOutputSpentTwice` | `removed` has distinct keys and is disjoint from `added`, across the store and in-Block paths | declined |
| `Connect.connected.feesAgreeWithConfirmed` | `fees` is the sum of the confirmed fees, and `confirmed` leads with the coinbase at fee 0 and holds no other coinbase | declined |
| `Connect.duplicateOutputs.nothingHeldIsNeverRefused` | a Block whose created keys the Set does not hold is never refused for BIP30 | universal |
| `Connect.duplicateOutputs.heldIsRefusedExceptCoreTwo` | any held key is refused, unless the Block is mainnet 91842 or 91880 | bounded (`when held != []`) |
| `Disconnect.reversal.undoesConnected` | applying a Block's Change and then its Reversal is the identity on the Set (`roundTrip`) | declined |
| `Disconnect.reversal.deletesEverythingConnectAdds` | every key `connected` adds is in the Reversal's `deleted` (one direction: an Output created and spent in-Block is deleted and never added) | declined |
| `Disconnect.undoable.windowIsExactly288Deep` | a fork is reversible against `windowFrom(tip)` exactly when it disconnects at most `windowSize()` Blocks | universal |
| `Disconnect.prunable.neverTakesUndoData` | pruning below `h` is permitted exactly when `h <= windowFrom(standing)` | universal |

**Why the budget rose.** Five of the nine are declined, not open: the Lean
call cone of `connected` reaches the Block walk (`conserving`, `found`,
`fromTheStore`, `notYetSpent`, `resolving`, `spentBy`, `takenFromBlock`,
`takenFromStore`, `walked`), a mutual recursion whose seed is not a proven
recursion bound, and the two round-trip laws also iterate a `Map` with
`Bytes` keys, whose order the proof model does not carry. Bringing
`Domain.Connect` and `Domain.Disconnect` into the entry brings their cones'
non-law claims with them — 86 declined cases, 29 more than before, all
under the same recursions and `Domain.Transaction`'s decoder — which is
what the budget now counts. Every one of the nine passes both verify lanes
(`aver verify domain/connect.av --hostile`, `domain/disconnect.av` likewise),
which is the evidence the declined five stand on until the walk is reshaped
into a proven recursion, as #349 did for four others. The `when` guards on
the Height come from the hostile lane: at `2^63` the subsidy's halving walk
exhausts the step budget, and a `forkHeight` below zero is not a fork. The
regtest run for the refusal is the `bip30` liar section of
`docs/regtest-testing.md`.
