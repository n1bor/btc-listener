# Script laws and executable explanations

This file is the register of this project's **laws**: the claims it states once
and has a machine check for every value, rather than for the examples somebody
thought of. Most of them are about the Script engine, which is where the file
started, but laws have since spread to the chain, the stores, the codecs and
the clock, and they are all gated together.

```sh
aver proof main.av --module-root . -o ../btc-listener-proof \
  --check-json --declined-budget "$(cat proof/main.declined)" \
  --sorry-budget 0 --gate proof/main.manifest.json
```

At pin `b82939cb` (Aver 0.30.0), that is **166 laws over 25 modules: 153
universal, 13 bounded, 0 sorries, 13 declined**, and it exits 0. It is the
`proof` job in CI, one entry for the whole program since
n1bor/btc-listener#350; before that it took two, and `docs/proofs.md` tells
that story. `proof/main.manifest.json` records every law's tier, theorem and
axiom set, and `--gate` fails on any law removed, demoted or grown an axiom.

**Every law named below links to the `verify ... law ...` line that states
it**, on `main`. The line anchor is a convenience and can drift as the file
around it changes; the law's name is the exact identifier, so
`grep -rn "law <name>" domain/ infra/` always finds it.

**Counts in the sections below are historical.** Each says the pin it was
measured at and the issue that added it, and several describe the retired
second entry `domain/laws.av` in the present tense because it existed when
they were written. The current numbers are the ones above.

## What a law is, and what proving one buys

A **verify case** is one input and one expected answer:

```aver
verify isoOf
    String.len(isoOf(1789427176648)) => 24
```

`aver verify` runs it. It proves something about 1789427176648 and nothing
about any other instant.

A **law** names the inputs it ranges over and states an equation that must
hold across them:

```aver
verify isoOf law alwaysTwentyFourCharacters
    given ms: Int = [0, 1, 999, 1000, 86399999, 86400000, 951782400000, 1789427176648, 253402300799999]
    when Bool.and(ms >= 0, ms < 253402300800000)
    String.len(isoOf(ms)) => 24
```

The `given` list is **samples, not the claim**. `aver verify` runs the law on
them, which is a cheap test. `aver proof` does something else: it translates
the law into a Lean 4 theorem quantified over the *type* — every `Int`
satisfying the `when` guard, not the nine listed — and asks the Lean kernel to
check it. A law can cite other laws with `because` and `using`, so a long
argument is assembled from named steps rather than one opaque proof.

**The tiers are the whole point, and the manifest records them per law.**

- **universal** — the kernel checked the theorem over every value of the
  law's `given`s, with `#print axioms` inside Lean's core three (`propext`,
  `Classical.choice`, `Quot.sound`). 153 laws today. This is the only tier
  that means "for all inputs".
- **bounded** — stated only over the enumerated samples. 13 laws today, and
  each has a reason: a `when` guard the exporter cannot lift, or a helper it
  cannot bound. A law that cites a bounded law through `using` can never be
  universal.
- **declined** — the exporter refused to state the claim at all, so there is
  no theorem, no `sorry` and no error standing in for it. 13 entries today for
  8 distinct laws, all one cause: the `Domain.Transaction` and
  `Domain.Connect`/`Domain.Disconnect` mutual recursions it cannot find a
  decreasing measure for. **A declined law is not proved.** It still runs under
  `aver verify` on its samples, which is why `proof/main.declined` is a budget
  that only goes down.
- **sorry** — an obligation no strategy closed. The budget is zero, so one is
  a red run by design.

**What a universal law buys, concretely.** The round-trip laws are the clearest
case: [`Domain.TreeStore.decodeHeld.readsWhatEncodeHeldWrote`](https://github.com/n1bor/btc-listener/blob/main/domain/treestore.av#L76) says that for
*every* Header record the writer can produce, the reader gives back exactly
what went in. That is not a statement about the fixtures; it is a statement
about the pair of functions, and it closes off the entire class of bug where
a record encodes fine and reads back subtly wrong. Likewise
`Domain.ScriptParse`'s parse/serialise laws mean no Script can round-trip to
different bytes, and `Domain.StackItem`'s arithmetic-width laws mean the
boundary where Script numbers stop being exact is where the code says it is,
for every number, not for the handful anybody tried.

**What they do not buy, which matters more.**

- Laws cover the pure code reachable from `main.av`. The network, the disk and
  the Screen are not in the cone; nothing here says a socket is read
  correctly. That is what `docs/regtest-testing.md` is for.
- The curve and the hashes are **providers**, opaque by construction, because
  their edge cases are consensus rules. No law reaches inside `verifySignature`
  or `ripemd160`. Interestingly, no law *needs* to: every decline that touched
  a provider turned out to be a verify example rather than a law (see
  `docs/proofs.md`).
- A law is only as good as its statement. `aver proof` checks that the
  statement follows from the code, not that the statement is the right one to
  make. The project's defence against a wrong statement is that law
  expectations are pinned to sources outside this implementation — published
  vectors, Core's own test data, spec-computed values — never captured from
  the code under test.
- Agreement with Bitcoin Core is **not** established by laws. It is
  established by the corpus: 6,050 cases of Core's published test data in
  `corpus/*.av`, verified as its own CI job. The laws say the engine is
  self-consistent; the corpus says it matches Core.
- A green proof run says nothing about Scripts on the connect path, which
  `Domain.Connect` deliberately does not run (ADR 0007).

## Number guarantees

These laws quantify over all Aver integers, including values larger than the
four-byte arithmetic operand range:

- [`asNumber.readsWhatFromNumberWrote`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L42): reading an encoded number gives the
  original number, for either sign and across sign-byte boundaries.
- [`isMinimalNumber.acceptsWhatFromNumberWrites`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L571): the encoding has no
  redundant top byte.
- [`fromNumber.encodingIdentifiesTheNumber`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L158): two encodings are equal exactly
  when their numbers are equal. Different numbers cannot collide.
- [`asNumber.rewritingPreservesTheNumber`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L48): reading an item, writing its number
  minimally and reading it again preserves the value. This includes redundant
  zero bytes and negative zero; it does not claim the original bytes survive.

The first two use executable explanations which split zero from nonzero,
name the most significant digit, and connect its properties to sign placement.
The next two cite the proved roundtrip with `using`; no new justification
function is needed for either consequence.

## Canonical Script numbers

For every list whose elements are octets (`0 <= byte < 256`),
[`fromNumber.canonicalExactlyWhenMinimal`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L179) proves:

```text
fromNumber(asNumber(bytes)) == bytes  iff  isMinimalNumber(bytes)
```

Thus the minimality checker recognizes exactly the fixed points of number
normalization. Negative zero `[128]` normalizes to `[]`; redundant `[1, 0]`
normalizes to `[1]`; the necessary sign byte in `[128, 0]` survives.
The octet premise matters: the out-of-domain list `[256]` passes the minimality
predicate but normalizes to `[128, 128]`, so the theorem deliberately excludes it.

The hard direction is [`fromNumber.minimalItemsAreFixedPoints`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L171). Its two
explanations first recover the magnitude digits, then restore the top byte or
separate sign byte. [`bigEndian.writingReadDigitsPreservesThem`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L116) supplies checked
list induction: the recursive explanation consumes one byte and updates the
positive prefix. Each explanation and the final implication are independently
universal and kernel-audited. The Aver pin includes generic compiler fixes found
while checking these proofs; no Bitcoin-specific compiler logic or handwritten
Lean is used.

The reverse direction cites the already-proved minimality of every encoder
output. [`asNumber.minimalEncodingIdentifiesBytes`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L53) then proves that two minimal byte
encodings are equal exactly when they decode to the same number.

## An induction written in Aver

[`bigEndian.largerPrefixStaysLarger`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L107) says that reading the same list preserves
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

[`fitsArithmetic.acceptsCoreOperandRange`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L547) is now universal:

```text
fitsArithmetic(fromNumber(value))  iff  -2147483648 < value < 2147483648
```

This includes both signs and excludes both endpoints. A magnitude of 2147483648
needs another byte for its sign, so even -2147483648 is outside the four-byte
operand range. The proof composes the exact one-, two-, three-, and four-byte
thresholds: 128, 32768, 8388608 and 2147483648.

The common step is [`signedBytes.lengthStep`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L235): above a single unsigned byte,
the low byte adds one to the length of the quotient's signed encoding.
`sizeRecurrenceReason` is the same executable explanation at each threshold.
The sign-placement law preserves a prepended byte whenever the tail is nonempty;
zero and magnitudes below 256 provide the base cases. Existing production
function bodies and the public API stay unchanged.

This exposed a generic Aver bug: `using` lemmas disappeared from the final
implication when `because` was present. Aver PR #1296 removes that exception.
No width-specific compiler rule or handwritten Lean is involved.

## CompactSize preserves the following field

[`CompactSize.encode.readsBack`](https://github.com/n1bor/btc-listener/blob/main/domain/compactsize.av#L131) is universal for every unsigned 64-bit value
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
the target bytes. [`without.stableFilter`](https://github.com/n1bor/btc-listener/blob/main/domain/scriptparse.av#L351) proves that the production accumulator
implementation is exactly `reverse(acc) ++ retained(ops, target)`. The remaining
lemmas establish single-deletion idempotence and commutation, move one deletion
past a batch, and then induct over entire batches. The Bool explanations are
ordinary private functions with executable examples; no public API is added.

The distinction between encodings matters: deleting the minimal push `[1, 171]`
removes `Op.Push(1, [171])`, while `Op.Push(76, [171])` survives. Equal payloads do
not imply equal serialized operations. These internal laws concern `withoutEach`
on arbitrary operation lists.

The public API now has [`withoutPushes.repeatedItemsHaveNoFurtherEffect`](https://github.com/n1bor/btc-listener/blob/main/domain/scriptparse.av#L294):

```text
withoutPushes(scriptHex, items ++ items) == withoutPushes(scriptHex, items)
```

This equality quantifies over every String and every finite list of integer
payload lists, with no validity or length premise. Both sides return exactly
the same `Result`: malformed hex and truncated pushes retain their error;
successful outputs have identical lowercase hex, preserving every surviving
operation's encoding. For example, deleting `[171]` twice from
`"5101AB4C01AB52"` yields `Ok("514c01ab52")`: the nonminimal push survives.

The helper law [`withoutEach.concatenatedBatches`](https://github.com/n1bor/btc-listener/blob/main/domain/scriptparse.av#L310) proves that processing
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

For every finite list of octets, [`ScriptParse.parse.preservesExactBytes`](https://github.com/n1bor/btc-listener/blob/main/domain/scriptparse.av#L37)
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
[`preservesBytes`](https://github.com/n1bor/btc-listener/blob/main/domain/scriptparse.av#L716) proves the explanation for every valid octet list and
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

- [`over.preservesDifference`](https://github.com/n1bor/btc-listener/blob/main/domain/chainwork.av#L195): processing the same contributions preserves the
  exact difference between two initial totals.
- [`over.chunksCompose`](https://github.com/n1bor/btc-listener/blob/main/domain/chainwork.av#L203): processing `prefix ++ suffix` equals processing the
  prefix and then continuing with the suffix from its resulting total.
- [`heavier.sameWorkPreservesChoice`](https://github.com/n1bor/btc-listener/blob/main/domain/chainwork.av#L226): adding the same contributions to candidate
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

- [`ofBits.zeroTargetProvesNothing`](https://github.com/n1bor/btc-listener/blob/main/domain/chainwork.av#L73): `when zeroTarget(bits)`, the work is 0.
- `ofBits.neverNegative`: the work is never below zero, on any Int, which is
  what makes `added` and `over` monotone along a branch.
- [`overflowing.isAnExponentAboveThirtyFour`](https://github.com/n1bor/btc-listener/blob/main/domain/chainwork.av#L166): the exponent refusal against the
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

- [`place.recordEndsAtUsed`](https://github.com/n1bor/btc-listener/blob/main/domain/segment.av#L100): the record placed ends exactly at the new
  `used`, in the Segment the state names, as long as the Block, past a header.
- [`place.consecutiveRecordsTile`](https://github.com/n1bor/btc-listener/blob/main/domain/segment.av#L106): two consecutive placements either open the
  next Segment at its first record or start one header past where the first
  ended -- no gap, no overlap.
- [`place.staysUnderCap`](https://github.com/n1bor/btc-listener/blob/main/domain/segment.av#L113): a record that fits under the cap never takes `used`
  past it.
- [`place.agreesWithTheReader`](https://github.com/n1bor/btc-listener/blob/main/domain/segment.av#L119): when the Segment does not roll, the offset is
  `payloadAt(used)`, the state after is `nextHeader(used, n)`, and `complete`
  holds for the record against a Segment that size.
- [`headerFor.readsBack`](https://github.com/n1bor/btc-listener/blob/main/domain/compactsize.av#L131): `lengthOf(headerFor(n)) == Ok(n)` for every
  `0 <= n < 2^32`, citing [`Domain.Message.littleEndian.fourBytesReadBack`](https://github.com/n1bor/btc-listener/blob/main/domain/message.av#L49).
- [`nameOf.sortsWithSegment`](https://github.com/n1bor/btc-listener/blob/main/domain/segment.av#L68): the file names sort as the Segment numbers do,
  below a million.

A seventh pair pins the effectful fix: [`agreesWithDisk.theDiskItCountedAgrees`](https://github.com/n1bor/btc-listener/blob/main/domain/segment.av#L231)
(the size the count says is accepted) and [`agreesWithDisk.anyOtherSizeRefuses`](https://github.com/n1bor/btc-listener/blob/main/domain/segment.av#L235)
(any other size is refused by name, with both numbers and the Segment).

**Segment is outside the interpreter's proof cone**, so these are not in the
CI proof job's count and have no Lean tier yet. They are checked by
`aver verify domain/segment.av --module-root .` and by the same command with
`--hostile`, which is where the `when` guards come from: a negative `used`
or a negative Block length is not a world the writer is ever in, and a
`rolls` case is exactly the one [`agreesWithTheReader`](https://github.com/n1bor/btc-listener/blob/main/domain/segment.av#L119) is not about.

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

Two laws retain bounded credit: [`ScriptState.rearranged.staysWithinDeclaredDepth`](https://github.com/n1bor/btc-listener/blob/main/domain/scriptstate.av#L487)
and [`StackItem.isMinimalPush.directPushIsMinimalUnlessSmallNumber`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L625). All previously
universal laws retain their credit.

## Laws added after #338 (n1bor/btc-listener#337)

Nine laws over the engine's bookkeeping and its arithmetic table, landed with
the proof job (#341) gating them. Tiers as the gate measured them at pin
`b6a37c82`: **107 universal, 3 bounded, 0 open, 130 declined**.

| law | pins | tier |
|---|---|---|
| [`ScriptMath.unaryValue.unaryValueSpec`](https://github.com/n1bor/btc-listener/blob/main/domain/scriptmath.av#L69) | the unary table against a spec whose `?` block quotes Core's `EvalScript` line per opcode | universal |
| [`ScriptMath.binaryValue.binaryValueSpec`](https://github.com/n1bor/btc-listener/blob/main/domain/scriptmath.av#L142) | the binary table the same way, `a` the deeper operand | universal |
| [`ScriptMath.binaryValue.commutative`](https://github.com/n1bor/btc-listener/blob/main/domain/scriptmath.av#L148) | ADD, BOOLAND, BOOLOR, NUMEQUAL, NUMNOTEQUAL, MIN, MAX commute | universal |
| [`ScriptState.executing.executingSpec`](https://github.com/n1bor/btc-listener/blob/main/domain/scriptstate.av#L234) | executing is "every open branch is taken" | universal |
| [`ScriptState.settled.settledSpec`](https://github.com/n1bor/btc-listener/blob/main/domain/scriptstate.av#L271) | an open OP_IF fails the Script; otherwise the top decides | universal |
| [`ScriptState.spent.onlyOpcodesCount`](https://github.com/n1bor/btc-listener/blob/main/domain/scriptstate.av#L207) | only opcodes above OP_16 count against the limit | universal |
| [`ScriptState.rearranged.lengthSpec`](https://github.com/n1bor/btc-listener/blob/main/domain/scriptstate.av#L508) | how many items each of the thirteen shuffles leaves | universal |
| [`ScriptStep.landed.neverOverLimit`](https://github.com/n1bor/btc-listener/blob/main/domain/scriptstep.av#L28) | a Step continues exactly when both stacks together fit in 1000 | universal |
| [`ScriptParse.parse.directPushRunsPastTheEnd`](https://github.com/n1bor/btc-listener/blob/main/domain/scriptparse.av#L44) | the error string for a direct push with no data, for `1 <= n <= 75` | bounded (`when`) |

## Laws added with the Regtest P2SH Height (n1bor/btc-listener#346)

`Domain.Rules.at(Regtest, 0)` had SegWit on and P2SH off, because the P2SH
Height table said 1 for regtest where the SegWit table said 0, and Core's
`VerifyScript` asserts WITNESS never comes without P2SH. The table entry is
now 0, and three laws over `at` pin the shape of every Height table against
the rules Core states rather than against a value someone copied:

| law | pins | tier |
|---|---|---|
| [`Rules.at.witnessImpliesPayToScriptHash`](https://github.com/n1bor/btc-listener/blob/main/domain/rules.av#L120) | `segWit ⇒ payToScriptHash` on all four Networks at every activation Height and its neighbour; fails on the old table at (Regtest, 0) | universal |
| [`Rules.at.rulesOnlyTurnOn`](https://github.com/n1bor/btc-listener/blob/main/domain/rules.av#L126) | every rule in force at `h` is in force at `h + 1` (`eachRuleOn`, seven fields); Core's `DeploymentActiveAt` is monotone and nothing turns a soft fork off | universal |
| [`Rules.at.auditorGetsNoPolicy`](https://github.com/n1bor/btc-listener/blob/main/domain/rules.av#L131) | `at(n, h).policy == Policy.none()` on every Network: the #52 guarantee the module intent relies on, stated executably | universal |
| [`Rules.onOrStays.isImplication`](https://github.com/n1bor/btc-listener/blob/main/domain/rules.av#L164) | the one-field helper is material implication | universal |

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
| [`LockTime.lockTimeChecked.agreesWithCoreCheckLockTime`](https://github.com/n1bor/btc-listener/blob/main/domain/locktime.av#L140) | `Continue` exactly when `cltvSpec`: same side of 500,000,000, `value <= lockTime`, sequence not final (Core's `CheckLockTime`) | universal |
| [`LockTime.sequenceChecked.agreesWithCoreCheckSequence`](https://github.com/n1bor/btc-listener/blob/main/domain/locktime.av#L233) | `Continue` exactly when `csvSpec`: disable bit on the value passes; else version >= 2 as uint32, Input's disable bit clear, same type bit, masked value <= masked sequence (Core's `CheckSequence`, BIP68/112) | universal |
| [`LockTime.checked.isANopBelowTheFork`](https://github.com/n1bor/btc-listener/blob/main/domain/locktime.av#L27) | under rules where `stillNop`, the opcode is `Continue(state)` even on an empty stack | universal |
| [`LockTime.checked.neverPops`](https://github.com/n1bor/btc-listener/blob/main/domain/locktime.av#L34) | a `Continue` carries the State untouched (BIP65: the top item is not popped) | universal |

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
| [`StackItem.isTruthy.zeroIsFalse`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L478) | `isTruthy(fromNumber(n)) == (n != 0)`; `because truthinessReason` names the most significant digit and follows it through sign placement, citing [`mostSignificantDigitIsNotZero`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L322), [`outsideByteRangeIsNeverAdded`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L310), [`zeroDigitWindow`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L317) and [`placed.positiveTopIsTruthy`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L429) | universal |
| [`StackItem.isTruthy.negativeZeroIsFalse`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L484) | `isTruthy(item ++ [128]) == anyNonZero(item)`: a sign byte on nothing is false (BIP62 rule 3); `because negativeZeroReason` reads the reversal, citing [`anyNonZero.reversal`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L518) | universal |
| [`StackItem.placed.positiveTopIsTruthy`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L429) | a positive top digit survives placement as a set byte | universal |
| [`StackItem.anyNonZero.setHeadIsEnough`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L505) | a set head decides | universal |
| [`StackItem.anyNonZero.concatenation`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L511) | set-in-the-join is set-on-either-side, by induction on the left | universal |
| [`StackItem.anyNonZero.reversal`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L518) | reversal does not change whether anything is set | universal |

The `simp` heartbeat timeout #344 recorded for the second law did not
recur once the reversal was stated as its own law and cited with `using`
rather than left to the structural induction.

The remaining #337 proposal, that `minimalPush` is `isMinimalPush`-minimal, is false as stated:
`CScript() << vch` writes `[1]` as a one-byte push, which MINIMALDATA refuses
in favour of OP_1, and [`isMinimalPush.directPushIsMinimalUnlessSmallNumber`](https://github.com/n1bor/btc-listener/blob/main/domain/stackitem.av#L625)
already pins exactly that.

## Connect, Undo and BIP30 (n1bor/btc-listener#354)

Nine laws over `Domain.Connect` and `Domain.Disconnect`, gathered by the
second proof entry `domain/laws.av`, which now depends on both. (**That leaf
is gone.** n1bor/btc-listener#350 retired it: `aver proof main.av` is the one
entry, and every law named in this file is reached without any `depends` list
being told about it. The sections below keep the leaf in the present tense
because each is a measurement taken when it existed, against the pin it names.) Writing the
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
| [`Connect.connected.valueIsConserved`](https://github.com/n1bor/btc-listener/blob/main/domain/connect.av#L149) | an applied Block adds at most the subsidy plus what it removed (`conservedIn`); `when` bounds the Height to the subsidy's last halving | declined |
| [`Connect.connected.noOutputSpentTwice`](https://github.com/n1bor/btc-listener/blob/main/domain/connect.av#L156) | `removed` has distinct keys and is disjoint from `added`, across the store and in-Block paths | declined |
| [`Connect.connected.feesAgreeWithConfirmed`](https://github.com/n1bor/btc-listener/blob/main/domain/connect.av#L161) | `fees` is the sum of the confirmed fees, and `confirmed` leads with the coinbase at fee 0 and holds no other coinbase | declined |
| [`Connect.duplicateOutputs.nothingHeldIsNeverRefused`](https://github.com/n1bor/btc-listener/blob/main/domain/connect.av#L567) | a Block whose created keys the Set does not hold is never refused for BIP30 | universal |
| [`Connect.duplicateOutputs.heldIsRefusedExceptCoreTwo`](https://github.com/n1bor/btc-listener/blob/main/domain/connect.av#L572) | any held key is refused, unless the Block is mainnet 91842 or 91880 | bounded (`when held != []`) |
| [`Disconnect.reversal.undoesConnected`](https://github.com/n1bor/btc-listener/blob/main/domain/disconnect.av#L113) | applying a Block's Change and then its Reversal is the identity on the Set (`roundTrip`) | declined |
| [`Disconnect.reversal.deletesEverythingConnectAdds`](https://github.com/n1bor/btc-listener/blob/main/domain/disconnect.av#L120) | every key `connected` adds is in the Reversal's `deleted` (one direction: an Output created and spent in-Block is deleted and never added) | declined |
| [`Disconnect.undoable.windowIsExactly288Deep`](https://github.com/n1bor/btc-listener/blob/main/domain/disconnect.av#L88) | a fork is reversible against `windowFrom(tip)` exactly when it disconnects at most `windowSize()` Blocks | universal |
| [`Disconnect.prunable.neverTakesUndoData`](https://github.com/n1bor/btc-listener/blob/main/domain/disconnect.av#L318) | pruning below `h` is permitted exactly when `h <= windowFrom(standing)` | universal |

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
## Transaction finality: IsFinalTx, BIP113 and BIP68 (n1bor/btc-listener#401)

No new law. `Domain.Finality` is cases only, pinned to the BIPs' own
arithmetic: the 500,000,000 threshold, strict `<` against the Height and the
cutoff, the sequence bits 31 and 22 and the 512-second unit, and the BIP68
inequalities written as `coinHeight + N ≤ height` and `median + N·512 ≤
previousMedian` (Core's `−1` and `>=` folded together). Every function in it
exports and proves as cases.

**Why the leaf budget rose, 109 → 111.** `Domain.Connect.connectedFinal` and
`connectedSequenced`, the two new steps on the connect path, are declined for
the reason every `connected` claim is: their cones reach the fuel-lowered
Set walk. The engine cone is unchanged at **129 universal, 1 bounded, 0 open,
136 declined**; the leaf at **127 universal, 11 bounded, 0 open, 111
declined**, `--gate` 0 regressions in both.

## Block weight and signature-operation cost (n1bor/btc-listener#400)

No new law. `Domain.BlockLimits` is cases only, pinned outside the code: the
weights Core reports for genesis (1140) and the regtest fixture (1453), and
Core's `sigopcount_tests` vectors (`s1` 20 inaccurate / 2 accurate, `s2` 21 /
3, the P2SH scriptSig 3). The counter, `sigopsIn`, exports and its cases prove;
so do the P2SH and witness cost helpers.

**Why the leaf budget rose, 105 → 109.** Four claims decline, none of them a
law: `Domain.BlockLimits.genesisCoinbase` (one case checks the literal record
against `Domain.Transaction.decode`, whose recursion is fuel-lowered);
`Domain.TxCheck.strippedSize` (newly exposed for the weight; its own cases
decode); `Domain.Connect.connectedCosted` (reaches the fuel-lowered Set walk
like every `connected` claim); and `Domain.Connect.sigopCostOf`, whose cases
iterate a `Map` with `Bytes` keys — the proof model carries no ordering for
such keys, so the exporter declines the claim although the program orders them
the same way on every backend. The engine cone is unchanged at **129
universal, 1 bounded, 0 open, 136 declined**; the leaf at **127 universal, 11
bounded, 0 open, 109 declined**, `--gate` 0 regressions in both.

## The coinbase's Height and the witness commitment (n1bor/btc-listener#399)

No new law. `Domain.Connect.heightPush` was written with one — the push reads
back as the Height through `Domain.StackItem.asNumber` — and it landed on
sorry in the laws leaf over `asNumber`'s recursion, where the same reader's own
laws prove in the engine cone; it is `readsBackAsTheHeight`, a Bool-valued
function with twelve rows from 17 to 16,777,216, under the same name. The first
cut also made `Domain.Connect` depend on `Domain.Rules`, which brought the Rules
laws into the leaf where `at.witnessImpliesPayToScriptHash.implication` landed
on sorry too, and adding a `bip34` field to `Rules` moved that same law off
universal in the engine cone. Both are undone: BIP34 is `Domain.Rules.bip34At`
beside `Rules`, and `connectedUnless` takes the switch as a Bool the way
`Domain.Body.fault` takes `witnessed`.

**Why the leaf budget rose, 104 → 105.** `Domain.Connect.connectedOpening`, the
one new function on the connect path (BIP34's answer, then the walk), is
declined for the reason every `connected` claim is: its cone reaches the
fuel-lowered mutual recursion of the Set walk (`conserving`, `walked`, …). The
engine cone is unchanged at **129 universal, 1 bounded, 0 open, 136 declined**;
the leaf at **127 universal, 11 bounded, 0 open, 105 declined**, `--gate` 0
regressions in both.

## A Transaction's size is what its decoder consumed (n1bor/btc-listener#280 item 17)

One law over `Domain.Transaction.decodeNext`: [`sizeIsWhatItConsumed`](https://github.com/n1bor/btc-listener/blob/main/domain/transaction.av#L348) — a
decoded Transaction's `size` equals the bytes `decodeNext` took off the
front, and bytes that do not decode consume nothing and claim nothing. It is
what lets `Domain.Block.oneCarried` and `Domain.CompactBlock.carried` cut a
Transaction's bytes by its size in one `List.take` rather than by the
difference of two list lengths per Transaction, which was O(bytes × txs) on a
4 MB Block. The law lands as a Bool-valued function with cases under the same
name, and its rows are the witness-serialised and legacy shapes plus an
empty and a short input.

**Why the budgets rose, 134 → 136 for the engine cone and 102 → 104 for the
laws leaf.** Both the law and its case function are declined in both cones
— the leaf reaches `Domain.Transaction` through `Domain.Block` — for the
reason every claim reaching `decodeNext` already is: the
Transaction decoder's mutual recursion (`inputFrom`, `inputWhole`,
`readInputsInto`, `readOneInput`, `takeWitnessItems`, …) is fuel-lowered in
the Lean export because `remaining - 1` has no guard the exporter can read as
a decreasing measure, and `native_decide` could turn fuel exhaustion into a
default value. `Domain.Transaction.decode`, `Domain.Bip143.hashed`,
`Domain.Sighash.hashed` and the two `decode` laws from #358 are declined on the
same sentence, fifty claims in all. The verify block and the hostile lane are the evidence the
law stands on until that recursion is reshaped, which is the standing note on
every decodeNext claim and not a new fact about this one. Measured at pin
`c4b08179`: **129 universal, 1 bounded, 0 open, 136 declined** for the engine
cone and **127 universal, 11 bounded, 0 open, 104 declined** for the leaf,
`--gate` reporting 0 regressions and no new exported law in either; both
baselines are regenerated so the two declines are named in them.

## Signature-hash laws (n1bor/btc-listener#353)

Six laws over `Domain.Sighash` and `Domain.Bip341`, the two modules that
turn a hash type byte into what a signature commits to. Both are inside the
`domain/interp.av` cone, so the proof job gates them. Measured at pin
`c4b08179`: **129 universal, 1 bounded, 0 open, 134 declined** for the cone
(the bounded one is still [`ScriptParse.parse.directPushRunsPastTheEnd`](https://github.com/n1bor/btc-listener/blob/main/domain/scriptparse.av#L44));
`--gate` reports ten new laws against the committed baseline — these six
and #352's four, which had not been written into it — and no regression, so
the baseline is regenerated with exactly those ten added.

| law | pins | tier |
|---|---|---|
| [`Sighash.baseOf.lowFiveBitsDecide`](https://github.com/n1bor/btc-listener/blob/main/domain/sighash.av#L63) | `baseOf(t) == baseOf(t & 31)`: only the low five bits choose ALL, NONE or SINGLE (Core's `nHashType & 0x1f`) | universal |
| [`Sighash.baseOf.anyoneCanPayDoesNotChangeTheBase`](https://github.com/n1bor/btc-listener/blob/main/domain/sighash.av#L67) | `baseOf(t + 128) == baseOf(t)`: setting ANYONECANPAY leaves the base alone | universal |
| [`Sighash.isAnyoneCanPay.isBitSeven`](https://github.com/n1bor/btc-listener/blob/main/domain/sighash.av#L84) | `isAnyoneCanPay(t) == (t & 128 == 128)` | universal |
| [`Sighash.withoutSeparators.isStableFilter`](https://github.com/n1bor/btc-listener/blob/main/domain/sighash.av#L256) | the OP_CODESEPARATOR strip is the accumulator reversed followed by every retained non-separator, `because separatorReason` | universal |
| [`Bip341.validHashType.isBip341Set`](https://github.com/n1bor/btc-listener/blob/main/domain/bip341.av#L149) | valid exactly on `0..3` and `129..131`, the set BIP341 names, with negatives refused | universal |
| [`Bip341.spendType.isTwiceTheExtensionPlusTheAnnex`](https://github.com/n1bor/btc-listener/blob/main/domain/bip341.av#L176) | `spendType(annex, ext) == 2 * ext + annex`, the byte BIP341 writes into the signature message | universal |

The seventh, that a SINGLE hash type signing an Input past the last Output
yields the value one (the SIGHASH_SINGLE bug Core's `SignatureHash`
preserves), landed on `sorry` as a law over every SINGLE type — the digest is
opaque to the prover past the branch that returns it — and is pinned by
seven cases in `verify legacy` instead: Inputs one and two under types 3, 35,
131, 163 and `0xffffffe3`, and the two negatives (Input zero, and type 1).
## The on-disk records have one shape each (n1bor/btc-listener#355)

Four laws over the two decoders that used to take a record shorter than
what the encoder writes. `Domain.UtxoStore.reachedIn` accepted anything from
four bytes up, so a `meta:setTo` of four bytes read as a Height with an empty
Block Id — the bare Height CLAUDE.md says is refused; `Domain.TreeStore.heldIn`
took a `k:` record cut anywhere past its Height and handed back a short parent
and an empty Header. Both now refuse by their numbers, `encodeReached`
refuses an Id that is not sixty-four characters, and `domain/laws.av` gathers
`Domain.TreeStore` so the proof job sees these too. Measured at pin
`c4b08179` with the leaf at eleven modules: **62 universal, 5 bounded, 0
open, 95 declined** — the three `when`-guarded laws below are the bounded
ones added; `--gate` reports them as new and no regression, and the baseline
is regenerated with exactly those three.

| law | pins | tier |
|---|---|---|
| [`UtxoStore.decodeReached.readsWhatEncodeReachedWrote`](https://github.com/n1bor/btc-listener/blob/main/domain/utxostore.av#L261) | `decode(encode(Reached(h, id))) == Ok(Reached(h, id))` for Heights `0`, `4000`, `2^32 - 1` and three real Ids; `when` a 32-bit Height and a 64-hex Id | bounded (`when`) |
| [`TreeStore.decodeHeld.readsWhatEncodeHeldWrote`](https://github.com/n1bor/btc-listener/blob/main/domain/treestore.av#L76) | `decode(encode(held)) == Ok(held)` over Heights and Chain Work up to 2^256, with a real parent and Header | bounded (`when`) |
| [`TreeStore.heldIn.refusesAnythingCutShort`](https://github.com/n1bor/btc-listener/blob/main/domain/treestore.av#L119) | the 123-byte sample record cut to `n` bytes decodes exactly when `n == 123` | bounded (`when`) |

The fourth, that a Set standing of `n` zero bytes decodes exactly when
`n == 36`, landed on `sorry` as a law over the count — the prover does not
see through the zero-filling recursion to a length — and is eight cases in
`verify refusesAnythingButOneShape` instead, either side of the one shape.

`Domain.IndexKeys.idBytes` is left as it was, on purpose. It builds keys
from Ids the program has computed itself — a Header's hash, a Transaction's
— never from an Id read off the disk, and the 117 verify cases across ten
modules that build a key from `"aa"` are the house's fixture style; a strict
`idBytes` would rewrite them to close no path a record can take. The two
decoders above are the ones a corrupted or truncated directory reaches.

## The retarget rule against Core (n1bor/btc-listener#356)

`Domain.Target` had cases only: one mainnet window (Block 32256) and the four
genesis values. Eleven statements now pin the rule #281 puts in front of every
Header, and reading for them found a Header check that was wrong: `Domain.Block.meetsTarget`
read the compact mantissa's sign bit as part of the mantissa, so bits
`0x1d80ffff` named a target 128 times easier than `0x1d00ffff` and a Block Id
under it was accepted, where Core's `SetCompact` flags the word negative and
`CheckProofOfWork` fails. `meetsTarget` now refuses the sign bit, the
overflow Core flags, and bits that are not four bytes, before it unpacks
anything. Measured at pin `c4b08179` on top of #355: the laws entry goes to
**66 universal, 8 bounded, 0 open, 98 declined** (`--gate` reports seven new laws and no
regression; the three extra declines are the unqualified duplicates of the
three `Connect.connected` laws already declined for `Map` order, which the
export now lists under both spellings), and the baseline is regenerated.

| law | pins | tier |
|---|---|---|
| [`Block.meetsTarget.signBitProvesNothing`](https://github.com/n1bor/btc-listener/blob/main/domain/block.av#L394) | with bit 23 set no Block Id meets the target (Core: `fNegative`, or a zero target) | universal |
| [`Block.meetsTarget.overflowProvesNothing`](https://github.com/n1bor/btc-listener/blob/main/domain/block.av#L400) | with the mantissa shifted past 256 bits no Block Id meets the target (Core: `fOverflow`) | universal |
| `Target.compactOf.roundTripsTheLimitOfEveryNetwork` | `compactOf(targetOf(limitBits(n))) == limitBits(n)` on all four Networks (Core: `GetCompact(SetCompact(x)) == x`) | cases |
| [`Target.compactOf.truncatesAndIsIdempotent`](https://github.com/n1bor/btc-listener/blob/main/domain/target.av#L128) | `targetOf(compactOf(t)) <= t` and `compactOf` is idempotent through `targetOf`, for `0 < t < 2^256` | bounded (`when`) |
| [`Target.ruleFor.windowsRepeatAndRegtestNeverRetargets`](https://github.com/n1bor/btc-listener/blob/main/domain/target.av#L75) | the rule at `h` is the rule at `h + 2016`, and regtest is never `Retarget` (`fPowNoRetargeting`) | universal |
| [`Target.retargeted.clampsAndNeverExceedsTheLimit`](https://github.com/n1bor/btc-listener/blob/main/domain/target.av#L212) | for canonical parent bits and spans either side of the clamp: the new target is at most four times the old, never above the Network's limit, and the old bits at exactly one window when the old target is under the limit (`retargetSpec`) | bounded (`when`) |
| [`Target.minOrLast.twentyMinutesIsStrict`](https://github.com/n1bor/btc-listener/blob/main/domain/target.av#L283) | `parentTime + 1200` keeps the last bits; `+ 1201` gives the limit | universal |
| `Target.refusal.acceptsExactlyWhatTheRuleGives` | `refusal == None` exactly when `placeable`: the rule's bits, at most two hours ahead, strictly after the median | cases |
| `Target.medianOf.isOrderFree` | invariant under reversal and sorting | cases |
| [`Target.medianOf.isOneOfItsInputs`](https://github.com/n1bor/btc-listener/blob/main/domain/target.av#L459) | an element of a non-empty input | bounded (`when`) |
| `Target.medianOf.takesTheUpperMiddleOfAnEvenList` | `medianOf([a, b]) == max(a, b)`, Core's `pbegin[(pend - pbegin) / 2]`; a lower middle would shift MTP by one Header | cases |

The `canonical` guard on the retarget law is a `match`, not a `Bool.and`:
the hostile lane's `2^63` as parent bits is an exponent of `2^39`, and
`targetOf` on it is a power loop that long — the lane was killed for memory
before the guard was made lazy.

Four of the eleven are cases rather than laws. The limit round trip,
`isOrderFree` and the upper-middle pair landed on `sorry` (the `byteLength`
countdown over a 256-bit target and the insertion sort are beyond the
prover), and `acceptsExactlyWhatTheRuleGives` was the one claim in the entry
the Lean build could not compile at all — its refusal strings interpolate
several values — so each is a Bool-valued function with the cases the law's
`given` rows would have been, and the same name.

Four of the eleven are cases rather than laws. The limit round trip,
`isOrderFree` and the upper-middle pair landed on `sorry` (the `byteLength`
countdown over a 256-bit target and the insertion sort are beyond the
prover), and `acceptsExactlyWhatTheRuleGives` was the one claim in the entry
the Lean build could not compile at all — its refusal strings interpolate
several values — so each is a Bool-valued function with the cases the law's
`given` rows would have been, and the same name.

## Thirteen smaller findings (n1bor/btc-listener#358)

The September 2026 survey's smaller findings, landed together. The fixes:
`Infra.Peers.hostText` splits on the last colon and strips the brackets, so
IPv6 callers no longer share one inbound slot as host `"["`;
`Domain.Snapshot.notedArrival` goes through `ringWith`, so a Transaction
re-admitted after a disconnect keeps its one row; `Domain.SpendContext.inputAt`
answers `None` below zero; `Domain.TxCheck` refuses `bad-txns-oversize` on the
non-witness size, worked out from the record; `Domain.Address.routable` is
Core's `IsRoutable` for IPv4 (255/8 is routable but for `255.255.255.255`);
`Domain.Inventory.collect` and `Domain.Block.hashesIn` take one length and
carry it, and an `inv` naming more than `MAX_INV_SZ` (50,000) entries costs
the Peer before an entry is read; `Domain.ScriptWork.intoLightest` with no
branches makes one rather than dropping the Piece; a full Mempool asks its
floor plus the incremental relay fee before it evicts anything, so a
newcomer at the floor with a smaller Id no longer churns it for free; and
`Domain.Network` says which testnet it is. The sign bit (item 3) landed with
#356, the loose `k:` record and the Id key (item 4) with #355. The second
proof entry gathers `Domain.Address`, `Domain.Inventory` and
`Domain.Snapshot` too, and stands at **68 universal, 9 bounded, 0 open, 102
declined** at pin `c4b08179` — three laws added, the declines up from 98 with
the three modules' cones (cases under fuel-lowered recursions, none of them
laws), baseline regenerated, `--gate` 0 regressions.

| law | pins | tier |
|---|---|---|
| `Snapshot.ringWith.holdsEachIdOnce` | after `ringWith(views, view)` exactly one row names `view.txId` | cases |
| `SpendContext.inputAt.someExactlyWhenTheInputExists` | `Some` exactly for `0 <= index < inputs`; the negative side fails before the fix and was the hostile lane's alone until jasisz/aver#1451 closed on the `8fb5d98e` pin, when a `0 - 1` row joined the cases | cases |
| `Address.routableOctets.agreesWithCoreIsRoutable` | equals `coreIsRoutable`, Core's `IsRoutable` written flat, over a 12×10×5×3 grid of octets | cases |
| [`Inventory.countPrefix.isCompactSize`](https://github.com/n1bor/btc-listener/blob/main/domain/block.av#L630) | the inv count prefix is `CompactSize.encode` below 65536 | universal |
| [`Block.countPrefix.isCompactSize`](https://github.com/n1bor/btc-listener/blob/main/domain/block.av#L630) | the Locator count prefix is `CompactSize.encode` below 65536 | universal |
| [`IndexKeys.scanOrder.isByteFieldNumber`](https://github.com/n1bor/btc-listener/blob/main/domain/indexkeys.av#L104) | the big-endian key field is `ByteField.number` | bounded (`when`) |
| `ScriptWork.intoLightest.keepsEveryPiece` | one more Piece among the branches, whatever the branches | cases (verify only) |
| `Infra.Utxo.txidIn.agreesWithUtxoStore` | the Set-key Id slice agrees with `UtxoStore.txidInKey` (verify only: `infra/` is outside both proof entries) | verify |
| [`Infra.Utxo.fromScanOrder.isByteFieldNumberIn`](https://github.com/n1bor/btc-listener/blob/main/infra/utxo.av#L647) | the big-endian reader is `ByteField.numberIn` (verify only) | verify |

Four of the statements are cases rather than laws: `agreesWithCoreIsRoutable`
(two flat Bool trees over four octets), `holdsEachIdOnce`,
`someExactlyWhenTheInputExists` and `keepsEveryPiece` landed on `sorry`; each
is a Bool-valued function with the rows the `given` would have carried.
`Domain.ScriptWork` is not gathered into the second entry: its cone reaches
the engine's, and trying it took the entry's declines from 102 to 123. The `inv` cap has no
law, having a liar instead: the `invflood` section of `docs/regtest-testing.md`.

Item 12's remaining duplicates — the witness-version byte reader in Script,
Witness and ReadAddress, and the second Script tokenizer — are left as they
are: each is a few lines, and a law pinning them equal would be longer than
the duplication it pins.

## The Handshake refuses exactly three things (n1bor/btc-listener#280 item 16)

`Domain.Handshake.versionFault` reads a Peer's `version` for what it says, and
one law pins the whole of its behaviour rather than its three messages:

```aver
verify versionFault law refusesExactlyTheThree
    given version: Int = [0, 209, 70015, 70016, 70017]
    given services: Int = [0, 1, 8, 9, 1033]
    given ourNonce: Bool = [true, false]
    given inbound: Bool = [true, false]
    isNone(versionFault(version, services, ourNonce, inbound)) => ...
```

Universal. The right-hand side is the acceptance condition spelled out
independently — not our own nonce, at or above the minimum protocol, and
either inbound or offering `NODE_WITNESS` — so the law says the function
refuses **exactly** that set and nothing else. A fourth refusal added without
updating the condition is a red law, which is the property worth having: the
failure mode for a handshake check is refusing an honest Peer, and that is
indistinguishable from a deadline expiring unless something states the
boundary. Twenty combinations of the four arguments, and the samples include
`70015` and `70016` either side of the floor, and services `8` and `9` either
side of the witness bit.

## A refusal survives the round trip (n1bor/btc-listener#376)

When a Block proves its work and then fails consensus, the reason travels as
one sentence that `Domain.Connect.cannotConnect` writes and `refusalOf` reads
back. The law says the reader inverts the writer:

```aver
verify refusalOf law readsWhatCannotConnectWrote
    given blockId: String = [...]
    given why: String = [...]
    when Bool.and(Bool.not(String.contains(blockId, " ")), Bool.not(String.contains(why, " cannot connect: ")))
    refusalOf(cannotConnect(blockId, why)) => Option.Some((blockId, why))
```

Bounded, by the `when` guard on the separator. What it protects is a string
the walk has to parse back to decide whether a stop is the chain's own or a
Peer's fault: a message that is not that sentence is treated as the chain
stopping, so a writer and reader that disagreed would turn a Peer's bad Block
into a node that stops. The guard is the honest part of the statement — a
Block Id with a space in it, or a reason containing the separator, is outside
what the writer can produce.

## The debug log's instant (n1bor/btc-listener#360)

Two laws over `Domain.Stamp.isoOf`, which turns milliseconds into the date and
clock every `debug.log` line opens with. Both bounded, by `when` guards on the
range:

- [`alwaysTwentyFourCharacters`](https://github.com/n1bor/btc-listener/blob/main/domain/stamp.av#L48) — the result is always 24 characters, for every
  instant from the epoch to the year 9999. A format that silently narrows,
  dropping a leading zero or the milliseconds, makes every log line after it
  misalign; this is the law that says it cannot.
- [`dayAndClockAreIndependent`](https://github.com/n1bor/btc-listener/blob/main/domain/stamp.av#L53) — `isoOf(day * 86400000 + inDay)` is
  `"{civilOf(day)}T{clockOf(inDay)}Z"` for every day and every instant within
  it. That is the real content: the date half and the clock half do not
  interfere, so there is no hour that rolls the date or millisecond that
  rolls the hour.

## The pool's silence rule (n1bor/btc-listener#268)

Two laws over `Domain.Watchdog.unheard`, which decides when the pool's silence
ends the run. Both universal:

- [`emptyDiallingPoolIsNeverUnheard`](https://github.com/n1bor/btc-listener/blob/main/domain/watchdog.av#L88) — a pool with no Peers that is still
  dialling is never unheard, at any silence and any deadline. Without it the
  node could end its own run during startup, before anybody has had a chance
  to answer.
- [`aPoolWithSomebodyKeepsTheRule`](https://github.com/n1bor/btc-listener/blob/main/domain/watchdog.av#L93) — with at least one Peer, silence is
  tolerated **if and only if** it is under the deadline. Stated as an
  equivalence rather than an implication, so neither a node that gives up
  early nor one that waits for ever satisfies it.

The pair is worth reading together: the first is the exception, the second is
the rule, and between them they cover the pool states that matter. The
companion question — one quiet Peer rather than a quiet pool — is a different
rule and has no law, because it is a sweep over per-Peer clocks rather than an
arithmetic claim (#330).
