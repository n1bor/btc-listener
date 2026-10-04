# Proofs, and what the proof job in CI proves

`aver proof` exports this program's laws — every `verify ... law` reachable
from `main.av`, and the functions they reach — to a Lean 4 project, turns each
law into a theorem, and asks the Lean kernel to check the lot. CI runs that on
every push as the `proof` job. **One entry covers the whole program**
(n1bor/btc-listener#350); until Aver 0.30 it took two, and the section below
says why and what each measured. This page says what a green run means, what it
does not mean, and how to move the two files it is measured against.

## What the job runs

```bash
aver proof main.av --module-root . -o "$RUNNER_TEMP/proof" \
  --check-json \
  --declined-budget "$(cat proof/main.declined)" \
  --sorry-budget 0 \
  --gate proof/main.manifest.json
```

The exit code is the verdict: 0 within every budget and no regression against
the baseline, 1 over a budget or a regression, 2 when the harness itself
failed (no `lake`, an unreadable baseline). The JSON summary is in the job log
and attached as an artifact, followed by one text line from the gate
(`--gate: 0 regression(s) vs baseline (166 baseline laws, 166 current)`), so a
red run says which claim moved.

Locally, with Lean on the machine (`elan`; `lake` fetches the pinned
toolchain on first use), the same command against a scratch directory takes a few minutes the
first time and seconds after, because `lake` caches under `<out>/.lake`:

```bash
aver proof main.av --module-root . -o ../btc-listener-proof \
  --check-json --declined-budget "$(cat proof/main.declined)" --sorry-budget 0 \
  --gate proof/main.manifest.json
```

## One entry, and the two it replaced

**Now.** `aver proof main.av` is the entry and `proof/main.declined` and
`proof/main.manifest.json` are the two files it is measured against. Measured
at the `b82939cb` pin (the tagged Aver 0.30.0): **153 universal, 13 bounded,
0 sorries, 13 declined**,
166 laws in the manifest, `build_errors: 0`. Four recursions were reshaped
along the way to make it possible — a countdown in `Bech32.checksumDigits` and
`foldGenerators`, a countdown over eras in `Subsidy.minted`, a fuel of the
tree's size in `HeaderTree.ancestryOf`, one function on a fuel in
`UtxoStore.eachUndo` — no value changing.

**Why it was two, and what that cost.** `aver proof` could only ever be given
a module, and `main.av` would not export: it panicked in Lean codegen on the
capability resource inside `Store.Database(Infra.Kv.Handle)` (closed upstream
as jasisz/aver#1449) and then, once it exported, its `lake build` failed on 22
effectful `verify` cases in `Infra.Headers` and `Infra.Utxo` plus one
`Infra.Utxo` law that landed on `sorry` (jasisz/aver#1462). So the engine
reachable from `domain/interp.av` was gated by the `proof` job, and
n1bor/btc-listener#349 added a leaf, `domain/laws.av`, that depended on the
fifteen law-carrying modules outside that cone and defined nothing, gated by a
second `proof-laws` job. The chain, the stores and the codecs were otherwise
gated by nothing but `aver verify`; Chainwork's laws had been from the day they
were written.

**Why one entry now, and why it is not a weakening.** jasisz/aver#1485 made
the law cone what `aver proof` exports by default, and all three failing
shapes lived in the `verify` **example** exports. With those gone the whole
program builds. Comparing the law names in the single manifest against the two
it replaced: the engine cone held 130 laws, the leaf 138, their union 163, and
**not one of them is absent** from `main.av`'s 166. The three it gains are
`Domain.Stamp.isoOf.alwaysTwentyFourCharacters`,
`Domain.Stamp.isoOf.dayAndClockAreIndependent`, and — the one worth naming —
`Infra.Utxo.fromScanOrder.isByteFieldNumberIn`, the law that used to land on
`sorry` and is proved now. A law added to any module is reached without
anybody editing a `depends` list, which is the other thing the leaf cost: it
had to be told.

Historic measurements of the two cones stay where they were written, in
`docs/script-laws.md` under #354, #355, #356 and #358, each against the pin it
was taken at. They describe the leaf as a live thing because it was one.

## What green means

Three numbers in the summary, and a manifest.

- **`universal_laws`** — laws whose theorem the Lean kernel checked in full,
  over every value of their `given`s, with `#print axioms` inside Lean's core
  three (`propext`, `Classical.choice`, `Quot.sound`). Nothing `native_decide`
  proves counts here, because that trusts the compiler's evaluator. **153 at
  the `b82939cb` pin.** The engine cone alone stood at 119 when it was its own
  entry, at the `c4b08179` pin.
- **`bounded_laws`** — laws stated only over an enumerated domain.
  **Thirteen today**: the three `Domain.Target` laws, the two `TreeStore`
  codec round trips, `UtxoStore.decodeReached`, `IndexKeys.scanOrder`,
  `Segment.nameOf`, both `Stamp.isoOf` laws, both `Domain.Connect` laws and
  `ScriptState.rearranged.staysWithinDeclaredDepth`. A law that cites a
  bounded law in `using` can never be universal. (One, when the engine cone
  was its own entry.)
- **`sorries`** — obligations that no strategy closed. Budget 0: a law that
  lands on `sorry` is a red run, by design.
- **`declined`** — claims the exporter refused to state at all, so no theorem,
  no `sorry` and no error stands in for them. They need their own budget
  precisely because nothing else would notice them. **Thirteen**, and since
  the `6eddd964` pin they are all
  one cause:

  - Every remaining decline **reaches a mutual recursion the exporter cannot
    bound**. In the engine cone that is `Domain.Transaction.decode`'s two laws
    and `decodeNext.sizeIsWhatItConsumed`; in the leaf it is those three again
    plus `Domain.Connect.connected`'s three (`valueIsConserved`,
    `noOutputSpentTwice`, `feesAgreeWithConfirmed`) and
    `Domain.Disconnect.reversal`'s two. Thirteen entries for eight distinct
    laws: the Connect and Disconnect five are listed under both their
    qualified and their bare names, the Transaction three only qualified. The cause is written up in `docs/script-laws.md` under
    #354: the groups of `inputFrom`/`inputWhole`/`readInputAfter`/
    `readInputsInto`/`readOneInput` and their Output twins pass `remaining - 1`
    with no guard showing it smaller. Bounding one group retires several laws
    at once, and it is the single highest-value follow-up this file names.

  **The provider family is gone, and it was never about the laws.** The two
  cones at pin `d8bf3e01`, counted off their committed manifests:

  | cone | declined | reaching a provider | reaching a recursion | other |
  |---|---|---|---|---|
  | engine | 136 | 74 | 62 | 0 |
  | laws leaf | 111 | 3 | 98 | 10 |

  Every one of those 77 provider declines — the claims on `evaluate`, `run`,
  `walked` and `stepped`, which this file used to describe as permanently
  declined because the curve is a provider on purpose — was a `verify`
  **example** claim. Not one was a law. jasisz/aver#1485 made the law cone the
  default, and once the examples stopped being exported the provider opacity
  stopped costing anything: **no law in either cone needs the curve to be
  transparent.** The same goes for most of the recursion declines, 59 of the
  engine's 62 being examples too. That is why the budgets fell from 136 and 111
  to 3 and 13 with no change whatever to the law classification — 129 universal
  and 1 bounded in the engine, 127 and 11 in the leaf, the same numbers the
  gate held before, and the same 130 and 138 laws in the manifests with none
  lost and none gained.

  **What that default costs, and how to get it back.** `aver proof` without
  `--examples` no longer emits each verify case as a Lean example checked by
  `native_decide`, so the second run of the cases through the translation —
  rather than through the VM — is no longer part of either gate. The cases
  themselves are checked by `aver verify`, which is their gate and always was;
  what is lost is the cross-check that the Lean translation agrees with the VM
  about them. Pass `--examples` to restore it. It is worth one run by hand when
  the Aver pin moves, which is exactly when a translation could start
  disagreeing, and it is not worth carrying every example decline in every CI run — the engine
cone alone brought 133 of them.

## What green does not mean

Kernel-genuine is a narrow claim: the kernel checked the proof of *the theorem
as translated*. It certifies the tactics, not the Aver-to-Lean translator,
which is part of the trusted base. What pins the translation to the runtime is
the dual run: every `verify` case running on the VM under `aver verify` and
also as a Lean example under `aver proof`, so each was one point where the two
had to agree. **That is opt-in now.** Since the law cone became `aver proof`'s
default the cases are not exported unless `--examples` is passed, which
CLAUDE.md's pin routine asks for by hand on a pin move — exactly when a
translation could begin to disagree. And it never covered the corpus: nothing
`depends` on `corpus/*.av`, so those 6,050 cases were never in this gate.
`aver verify` is their gate, on every push.

Nothing here says the engine agrees with Bitcoin Core. There is no formal
statement of Script to prove against; Core's C++ is the specification, and the
corpus is the only bridge to it.

## The two committed files, and who may change them

- **`proof/main.declined`** — the declined budget, a number. It only goes
  down. A PR that lifts a decline (a recursion given a measure, a cone that no
  longer reaches a provider) lowers it in the same PR. A PR that raises it has
  to say why in its own diff; CI will not raise it for you. **13 today, and
  they are eight laws, all one cause**: the `Domain.Transaction` and
  `Domain.Connect`/`Domain.Disconnect` mutual recursions the exporter cannot
  bound, the Connect and Disconnect five listed under both their qualified and
  their bare names. Every claim on `decode` is declined by construction (the
  three mutual-recursion groups), and a refusal law about the decoder cannot
  live anywhere else; they run under `aver verify` and `--hostile` and are the
  test the fix is measured by. The history of the number is worth keeping: the
  two budgets it replaced stood at 136 and 111, raised over time for written
  reasons, and collapsed to 3 and 13 at the `6eddd964` pin when the examples
  stopped being exported.
- **`proof/main.manifest.json`** — the per-law baseline: for every law, its
  tier, its theorem and its axiom set. CI runs `--gate` against it and fails on
  any law removed, demoted (universal > bounded > sampled > failed), whose
  axiom set grew, or whose backend changed. New laws are allowed and do not
  need a new baseline. To regenerate it, after a change that legitimately
  removes or weakens a law:

  ```bash
  aver proof main.av --module-root . -o ../btc-listener-proof \
    --write-baseline proof/main.manifest.json
  ```

  and commit the result in the same PR, where the diff shows exactly what was
  given up. CI never runs `--write-baseline`; that would let any regression
  acknowledge itself.

## Reading a red run

`--check-json` names the law. For the goal it could not close, run locally
with `--explain`: each open law's residual goal is printed and recorded in the
manifest under `open_goal`. The documented escalation is more Aver, not Lean:
split the law into helper laws (`because` explanations and `using`
citations, see `docs/script-laws.md`), each of which is also a millisecond
test under `aver verify`. A proven helper law is a rewrite rule for every law
below it.

## The proof-composition fix, and what it cost at the time

Aver’s certificate-wall change (#1368) exposed default-heartbeat timeouts in
this project’s heavy `because` laws, tracked in
[jasisz/aver#1386](https://github.com/jasisz/aver/issues/1386).
[jasisz/aver#1387](https://github.com/jasisz/aver/pull/1387) composes checked
equations and citations before expanding helpers, and the pin has carried it
for several moves. **Measured then, on the engine cone when it was its own
entry: 117 universal, 3 bounded, 0 open and 134 declined, without raising the
heartbeat or admission budgets.** The gate today is the figures at the top of
this page.

## The elan default, a closed chapter

Before jasisz/aver#1336, `aver proof` probed `lake --version` in the working
directory before deciding whether to attempt the guarded (`when`) laws, and an
`elan` with the pinned Lean installed but no default toolchain failed that
probe: every guarded law was silently emitted as bounded, the build still
passed, and the only symptom was a manifest twelve laws short (87/8/6 instead
of 99/2/0). That is how #338's numbers went unreproduced for a day. The pin
carries the fix, the CI job deliberately runs without an elan default, and
the manifest it produces is the one to trust.
