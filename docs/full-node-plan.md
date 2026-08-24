# The full-node plan

Decided 19 August 2026, in one interview, before any of it was built. This
document records what was decided and the order the work goes in; the issues
carry the detail, and [ADR 0007](adr/0007-two-claims-two-tools.md) carries the
one decision that earned an ADR.

The destination: this program becomes a full validating Bitcoin node — chain
download and validation, a UTXO Set, a Mempool, Transaction and Block relay,
compact Blocks, and finally inbound Peers.

## The decisions

| # | decision |
|---|---|
| D1 | The finish line is a node other nodes sync from — inbound Peers, serving Blocks — but every stage before the last ships outbound-only. |
| D2 | **Closed 19 August 2026** by the answer on [jasisz/aver#1007](https://github.com/jasisz/aver/issues/1007): the node is one poll-shaped event loop, fan-out within a tick uses Aver's independent products (threaded when compiled, sequential under `verify`), and branches never write — the loop is the single writer. [ADR 0008](adr/0008-independence-and-a-single-writer-loop.md). |
| D3 | Two keyspaces: the UTXO Set shrinks on spend; the `o:` audit index of [#21](https://github.com/n1bor/btc-listener/issues/21) keeps growing history. Where both are wanted, both are written. |
| D4, D5 | The glossary names: **UTXO Set** (the universal term over house purity) and **Undo Data**. In CONTEXT.md. |
| D6 | Two claims, two tools: the node follows the chain with an **Assume-valid Height**; `audit` fully verifies ranges behind it. [ADR 0007](adr/0007-two-claims-two-tools.md). |
| D7 | Signet first, mainnet as the goal. |
| D8 | A full tree of Headers, a bounded Undo window; a reorganisation past the window stops the node with a report. |
| D9 | The first Mempool enforces consensus plus basic Policy — standardness, a fee-rate floor, size-capped eviction, first-seen conflicts. RBF, orphans and packages deferred. |
| D10 | Segments move from hex text to binary when the runtime can hold bytes — the move [ADR 0004](adr/0004-hex-text-as-the-interim-block-format.md) always expected. |

Out of scope until a stage forces one in: an RPC interface, fee estimation,
persisted Peer or Mempool state.

## The stages

Each stage ships something measurable on its own. Single-Peer stages come
before anything gated on the concurrency answer; nothing depends on a decision
not yet made.

| stage | what | issue |
|---|---|---|
| 0 | signet, the Network the node grows up on | [#23](https://github.com/n1bor/btc-listener/issues/23) — **shipped** |
| 1 | a tree of Headers, and the most-work tip | [#24](https://github.com/n1bor/btc-listener/issues/24) — **shipped** |
| 2 | the UTXO Set, its Undo Data, and the Assume-valid Height | [#25](https://github.com/n1bor/btc-listener/issues/25) — **shipped** |
| 3 | Script completeness above the Assume-valid Height | [#20](https://github.com/n1bor/btc-listener/issues/20), [#12](https://github.com/n1bor/btc-listener/issues/12) — **shipped** |
| 4 | following the tip on one Peer | [#26](https://github.com/n1bor/btc-listener/issues/26) — **shipped** |
| 5 | many Peers, and the Peer Address gossip | [#27](https://github.com/n1bor/btc-listener/issues/27) — **shipped** |
| 6 | the Mempool, and Transaction relay | [#28](https://github.com/n1bor/btc-listener/issues/28) |
| 7 | compact Blocks | [#29](https://github.com/n1bor/btc-listener/issues/29) |
| 8 | inbound Peers, and serving the chain | [#30](https://github.com/n1bor/btc-listener/issues/30) |

Stages 3 and 4 are independent of each other; both need Stage 2. Stage 5 is
where the readiness poll becomes blocking. Stage 8 is the finish line and is
last on purpose.

## The parallel track

Independent of the stages, proceeding as upstream and appetite allow:

- **Binary Segments** — [#31](https://github.com/n1bor/btc-listener/issues/31),
  blocked on [jasisz/aver#1009](https://github.com/jasisz/aver/issues/1009)
- **Outputs over the whole chain** — [#21](https://github.com/n1bor/btc-listener/issues/21),
  the audit side's mainnet-scale work
- **BIP66 into Rules** — [#22](https://github.com/n1bor/btc-listener/issues/22),
  once the held chain reaches its activation
- **Parallel Script checking in audit** — [#32](https://github.com/n1bor/btc-listener/issues/32),
  the first independent product; measured to be where audit's time goes once
  spends resolve

## What the plan asks of Aver

This project has always been a driving project for the language, and the plan
continues that on purpose: each stage names what it needs before it needs it.

| ask | upstream | gates |
|---|---|---|
| a concurrency direction | [jasisz/aver#1007](https://github.com/jasisz/aver/issues/1007) — **answered**: independent products plus poll-shaped effects; see [ADR 0008](adr/0008-independence-and-a-single-writer-loop.md) | nothing any more |
| ~~a configurable TCP read deadline~~ | **delivered** as [`Tcp.poll`](https://github.com/jasisz/aver/issues/782) and `Tcp.readSome`; session reads have no deadline mid-frame | **wired** (n1bor/btc-listener#55): `Infra.Peer` polls at every Message boundary, so a silent Peer ends the session; the loop over many Peers stays with #27 |
| a readiness poll over connections | **delivered and closed** as `Tcp.poll` over a caller-keyed `Map<Int, Tcp.Connection>` | **wired** (n1bor/btc-listener#27): `Infra.Peers` polls every socket at once and reads with `Tcp.readSome` |
| byte-oriented `Disk`, with a positional read | [jasisz/aver#1009](https://github.com/jasisz/aver/issues/1009) | binary Segments |
| a bounded dial | [jasisz/aver#1118](https://github.com/jasisz/aver/issues/1118) — `Tcp.connect` has no deadline, so one dead address stalls the loop for minutes (n1bor/btc-listener#119) | Stage 5's gossip, and Stage 6's scoring |
| `Tcp.listen` / `Tcp.accept` | to be filed; same family as the poll | Stage 8 |

## The disciplines that carry over

Nothing in this plan relaxes what the auditor half of the project established:

- three-valued answers everywhere — a thing not checked is never a thing that
  passed, and the Assume-valid Height, like the Prune Watermark, exists so
  that deferred and failed can never be confused
- the glossary is binding, and grows in the moment a term crystallises —
  UTXO Set, Undo Data, Assume-valid Height, Mempool and Policy all entered
  CONTEXT.md the day they were decided
- expectations pin against sources outside this implementation; signet makes
  that cheaper, not optional
- `domain/` stays pure and `infra/` stays thin, which is what will let a
  Mempool's admission logic or a Header tree's work comparison carry verify
  blocks the way the Script engine's 3,889 do
