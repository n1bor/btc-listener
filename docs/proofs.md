# Proofs, and what the proof job in CI proves

`aver proof` exports the pure Script engine — every module reachable from
`domain/interp.av` that touches no effect — to a Lean 4 project, turns every
`verify` case into a Lean example and every `verify ... law` into a theorem,
and asks the Lean kernel to check the lot. CI runs that on every push as the
`proof` job. This page says what a green run means, what it does not mean, and
how to move the two files it is measured against.

## What the job runs

```bash
aver proof domain/interp.av --module-root . -o "$RUNNER_TEMP/proof" \
  --check-json \
  --declined-budget "$(cat proof/interp.declined)" \
  --sorry-budget 0 \
  --gate proof/interp.manifest.json
```

The exit code is the verdict: 0 within every budget and no regression against
the baseline, 1 over a budget or a regression, 2 when the harness itself
failed (no `lake`, an unreadable baseline). The JSON summary is in the job log
and attached as an artifact, followed by one text line from the gate
(`--gate: 0 regression(s) vs baseline (101 baseline laws, 101 current)`), so a
red run says which claim moved.

Locally, with Lean on the machine (`elan`; `lake` fetches the pinned
toolchain on first use), the same command against a scratch directory takes a few minutes the
first time and seconds after, because `lake` caches under `<out>/.lake`:

```bash
aver proof domain/interp.av --module-root . -o ../btc-listener-proof \
  --check-json --declined-budget "$(cat proof/interp.declined)" --sorry-budget 0 \
  --gate proof/interp.manifest.json
```

## The second entry: the laws outside the engine

The engine's cone is 34 modules; the chain, the stores and the codecs are
outside it, and Chainwork's laws were gated by nothing but `aver verify` from
the day they were written. Since n1bor/btc-listener#349 a leaf module,
`domain/laws.av`, depends on every law-carrying module outside that cone —
Address, Block, Chainwork, Connect, Disconnect, HeaderTree, Inventory,
Segment, Snapshot, Subsidy, Target, TreeStore, Watchdog, UtxoStore, fourteen
as of #358 — and defines nothing, so it cannot make a cycle; the `proof-laws` job exports
it with the same flags against `proof/laws.declined` and
`proof/laws.manifest.json`. A law added to a module the leaf does not yet
name is added to its `depends` in the same PR. Measured at pin `c4b08179`
with `Domain.Connect` and `Domain.Disconnect` (#354), `Domain.TreeStore`
(#355), the Target and Block laws of #356 and the Address, Inventory and
Snapshot modules of #358 in the leaf: **68 universal, 9 bounded, 0 open,
102 declined** (the bounded are
`Segment.nameOf.sortsWithSegment`, over `String` order,
`Connect.duplicateOutputs.heldIsRefusedExceptCoreTwo`, under `when held !=
[]`, the three on-disk-record round trips of #355 and the three
`when`-guarded Target laws of #356 and `IndexKeys.scanOrder.isByteFieldNumber`
of #358; the declined count rose from 66 with the #354 modules, whose cones`Connect.duplicateOutputs.heldIsRefusedExceptCoreTwo`, under `when held !=
[]`, and the three on-disk-record round trips of #355, under `when` on the
Height; the declined count rose from 66 with the #354 modules, whose cones
bring the Block walk's mutual recursion and the Transaction decoder — the
reason is written up in `docs/script-laws.md` under #354. The pin move from
`600b3551` promoted two `when`-guarded laws to universal, which the gate at
this pin reads as grown axiom sets, so the baseline was regenerated with the
diff showing exactly those two moving up). Four recursions were reshaped
for it (a countdown in `Bech32.checksumDigits` and `foldGenerators`, a
countdown over eras in `Subsidy.minted`, a fuel of the tree's size in
`HeaderTree.ancestryOf`, one function on a fuel in `UtxoStore.eachUndo`), no
value changing. `aver proof main.av` would be the whole program and panics
on a resource inside a sum type (n1bor/btc-listener#350); the leaf reaches no
`infra/` module and sidesteps it.

```bash
aver proof domain/laws.av --module-root . -o ../btc-listener-proof-laws --check-json --declined-budget $(cat proof/laws.declined) --sorry-budget 0 --gate proof/laws.manifest.json
```

## What green means

Three numbers in the summary, and a manifest.

- **`universal_laws`** — laws whose theorem the Lean kernel checked in full,
  over every value of their `given`s, with `#print axioms` inside Lean's core
  three (`propext`, `Classical.choice`, `Quot.sound`). Nothing `native_decide`
  proves counts here, because that trusts the compiler's evaluator. At the
  `c4b08179` pin there are 119.
- **`bounded_laws`** — laws stated only over an enumerated domain. One
  today, `when`-guarded (`ScriptState.rearranged.staysWithinDeclaredDepth`). A law that
  cites a bounded law in `using` can never be universal.
- **`sorries`** — obligations that no strategy closed. Budget 0: a law that
  lands on `sorry` is a red run, by design.
- **`declined`** — claims the exporter refused to state at all, so no theorem,
  no `sorry` and no error stands in for them. They need their own budget
  precisely because nothing else would notice them. 130 today, in two
  families:
  - **74 reach a provider operation** (`ripemd160`, `sha1`, `verifySignature`,
    `verifySchnorr`). Every claim on `evaluate`, `run`, `walked`, `stepped` and
    everything downstream of CHECKSIG or HASH160. Opaque on purpose (the curve
    is a provider because its edge cases are consensus rules), so these stay
    declined; engine-level invariants over the evaluator need hand-written
    Lean over the export, not sampled laws.
  - **56 reach a mutual recursion the exporter cannot bound**:
    `Domain.Transaction`'s three groups and `Domain.Bech32`'s
    `drain`/`regroup`. Each is a follow-up; fixing one lowers the number.

  Every verify case in the cone is also emitted as a Lean example and checked
  by `native_decide`, which is a second run of the same cases through the
  translation rather than the VM.

## What green does not mean

Kernel-genuine is a narrow claim: the kernel checked the proof of *the theorem
as translated*. It certifies the tactics, not the Aver-to-Lean translator,
which is part of the trusted base. What pins the translation to the runtime is
the dual run: every `verify` case runs on the VM under `aver verify` and as a
Lean example under `aver proof`, so each is one point where the two must
agree. The 6,050 Core corpus cases are the largest such set, which is one more
reason the corpus is verified on every push.

Nothing here says the engine agrees with Bitcoin Core. There is no formal
statement of Script to prove against; Core's C++ is the specification, and the
corpus is the only bridge to it.

## The two committed files, and who may change them

- **`proof/interp.declined`** — the declined budget, a number. It only goes
  down. A PR that lifts a decline (a recursion given a measure, a cone that no
  longer reaches a provider) lowers it in the same PR. A PR that raises it has
  to say why in its own diff; CI will not raise it for you. Raised once so far,
  130 → 134 with n1bor/btc-listener#347: two laws over `Domain.Transaction.decode`
  and two fixture helpers whose cases call it. Every claim on `decode` is
  declined by construction (the three mutual-recursion groups, #349), and a
  refusal law about the decoder cannot live anywhere else; they run under
  `aver verify` and `--hostile` and are the test the fix is measured by.
- **`proof/interp.manifest.json`** — the per-law baseline: for every law, its
  tier, its theorem and its axiom set. CI runs `--gate` against it and fails on
  any law removed, demoted (universal > bounded > sampled > failed), whose
  axiom set grew, or whose backend changed. New laws are allowed and do not
  need a new baseline. To regenerate it, after a change that legitimately
  removes or weakens a law:

  ```bash
  aver proof domain/interp.av --module-root . -o ../btc-listener-proof \
    --write-baseline proof/interp.manifest.json
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

## The pinned proof-composition fix

Aver’s certificate-wall change (#1368) exposed default-heartbeat timeouts in
this project’s heavy `because` laws, tracked in
[jasisz/aver#1386](https://github.com/jasisz/aver/issues/1386).
The pin includes [jasisz/aver#1387](https://github.com/jasisz/aver/pull/1387),
which composes checked equations and citations before expanding helpers.
The existing gate passes with **117 universal, 3 bounded, 0 open and 134 declined**,
without increasing the heartbeat or admission budgets.

## The elan default, a closed chapter

Before jasisz/aver#1336, `aver proof` probed `lake --version` in the working
directory before deciding whether to attempt the guarded (`when`) laws, and an
`elan` with the pinned Lean installed but no default toolchain failed that
probe: every guarded law was silently emitted as bounded, the build still
passed, and the only symptom was a manifest twelve laws short (87/8/6 instead
of 99/2/0). That is how #338's numbers went unreproduced for a day. The pin
carries the fix, the CI job deliberately runs without an elan default, and
the manifest it produces is the one to trust.
