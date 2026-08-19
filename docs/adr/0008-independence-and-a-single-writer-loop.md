# Fan out with independent products, and let one loop do the writing

Decided 19 August 2026, the day the question it depends on was answered.
[jasisz/aver#1007](https://github.com/jasisz/aver/issues/1007) asked whether
Aver's concurrency would be real threading or a poll-shaped event loop; the
maintainer's answer was a third thing that was already partly shipped, and the
full-node plan's open decision D2 closes around it.

## What Aver gives us

**Independent products.** `(a, b)!` declares two computations independent of
each other; `?!` is the same with `Result` unwrapping and leftmost-error
priority; fanning out over a list is the construct applied recursively, so
eight Peers is the same code shape as two. The properties that matter here:

- a branch can only work on what was passed into it — the construct removes
  the mechanical ways to share
- the VM runs branches sequentially, which keeps `verify` and `proof`
  deterministic; **compiled Rust runs a thread per branch**, with effect
  recording scoped per branch (`BranchPath`), so replay survives the threads
- `verify` reruns products with branches in reverse order and demands the same
  answer — a falsifier, not a decision procedure: it can catch a false
  independence claim and can never certify a true one
- the ceiling is explicit: a product is a tree of work that starts and
  finishes inside one call; nothing outlives it, nothing waits on a mailbox

The specification, such as it is, is two example files rather than prose:
`examples/formal/oracle_independent_products.av` and
`examples/core/independent_fanout.av` in the Aver repository.

**And the loop is not a compromise.** The maintainer was explicit that
poll-shaped primitives are ordinary effects — readiness is *data*, recorded
and replayed like any other effect result — so an event loop over them is a
first-class design, not an interim one.
[jasisz/aver#782](https://github.com/jasisz/aver/issues/782) (the configurable
read deadline) is already scoped as the first piece.

## The decision

Three rules, one architecture:

1. **The node is one long-lived event loop.** It owns the schedule: poll the
   Peers, take what is ready, act, repeat. The loop is ordinary sequential
   Aver, so the node's spine records and replays exactly as the listener
   always has.
2. **Fan-out happens inside a tick, through independent products.** Decoding
   several Peers' Messages, running the Script pairs of a Block's Inputs,
   validating Transactions against a UTXO view already read — work that is
   genuinely independent, fanned out and joined within one call, threaded in
   the compiled node and sequential under `verify`.
3. **Branches never write.** The UTXO Set, the Mempool, the Index — every
   piece of shared state is written only by the loop, after the join, batched
   through `putAll` as every writer here already is. Branches take values in
   and hand values back.

## Why branches never write

The maintainer put the hazard in front of us rather than letting us find it: a
Mempool and a UTXO Set written by eight Peers is precisely where the
independence claim gets hard to make honestly. Two branches writing one store
are not independent, and the reverse-order rerun may or may not say so — that
is what a falsifier means, and underestimating it is how this goes wrong.

The alternative considered was sharded branch writes: branches writing
disjoint keyspaces, merged afterwards. The disjointness of two keyspaces is
exactly the kind of semantic claim the falsifier cannot certify, so every
sharded write would carry an honesty obligation no tool checks. This project
counts answers it cannot earn as undecided rather than passed; the same
discipline applied to concurrency says: do not make claims whose only
witness is the absence of a caught lie.

What single-writing costs is write parallelism, and the batching already made
that cheap — measured on this Index, one `putAll` of 40,000 entries is 52 ms
where 40,000 `put`s are 24 s. The loop folding a tick's results into one batch
is the shape `infra/download.av` and `infra/txindex.av` already have.

## Consequences

- `infra/lock.av`'s one-writer-per-directory assumption survives the node
  unchanged, because the node really is one writer.
- Stage 5 of the full-node plan is gated on small primitives (a readiness
  poll, the #782 deadline), not on a language project. `Tcp.listen` /
  `Tcp.accept` for Stage 8 are the same family.
- Per-Input Script checking inside Block validation becomes the first natural
  product in the codebase — pure, embarrassingly parallel, and measurable
  against the sequential walk `audit` uses today.
- Anything that must outlive a call — a background sync alongside a serving
  loop, say — has no home in this model yet. The maintainer named that as the
  conversation not yet had; if a stage needs it, the ask goes upstream before
  the design leans on it.

## What would retire this

Aver growing a construct for work that outlives a call would reopen rule 1.
Rule 3 stands on its own: even with such a construct, shared state written
from one place is the version of this node whose claims stay checkable.
