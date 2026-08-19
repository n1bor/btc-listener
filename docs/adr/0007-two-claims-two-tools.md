# Follow the chain with an Assume-valid Height, and leave history to audit

Decided 19 August 2026, at the start of the full-node work, before any of it is
built. The question it answers: when this program syncs the chain as a
validating node, does "synced" mean every Script from genesis has run, or does
the node take old Scripts as settled the way Bitcoin Core's `assumevalid`
does?

The answer is both, from two tools with two different claims:

- **The node** follows the chain with an **Assume-valid Height**. Below it,
  Scripts are not run; merkle roots, parent links, work, value accounting and
  the UTXO Set are still checked in full. Above it, everything is verified.
  Its claim is *this is the chain, and value was conserved on it*.
- **`audit`** stays what it is: the tool that fully verifies any range of
  Heights and counts the answers three ways — passed, failed, undecided. Its
  claim is *these Blocks were checked, and this is exactly what could and
  could not be decided*.

Neither claim borrows the other's. The node never says "verified" about
Heights it skipped, and audit never needs to keep up with the tip.

## Why not verify everything

It was a genuine alternative, and the more attractive one for a project whose
identity is counting answers honestly. It fails on arithmetic and on
sequencing:

- Bitcoin Core, running libsecp256k1 in parallel C, takes hours over the
  signatures when `assumevalid` is off. This engine reaches the same
  libsecp256k1 through a provider but runs the Script walk single-threaded in
  a VM or generated Rust; an initial sync gated on it would be measured in
  weeks.
- The engine's Script coverage arrives in stages —
  [#20](https://github.com/n1bor/btc-listener/issues/20) for segwit v0,
  [#12](https://github.com/n1bor/btc-listener/issues/12) for Taproot. A node
  that cannot sync until it can run every historical Script cannot exist
  until both land. A node with an Assume-valid Height can exist first and
  have its claim grow as the engine does.

The permanent version — assume-valid forever, never re-verify — was also
considered and rejected: it retires the auditor rather than extending it, and
the auditor is the point of this project.

## Consequences

- "Synced" is a claim with a Height in it, and the Height is reportable. The
  node must be able to say what it did not check, the same way `show` says
  `discarded by pruning` rather than `missing`.
- The Assume-valid Height is a pinned Block Id, not a bare Height, so a chain
  that reorganises under it is detected rather than trusted.
- `audit` gains a purpose rather than losing one: run behind the node,
  narrowing the unverified span at whatever pace the engine and the disk
  allow, on the same directory.
- The discipline the glossary already carries extends unchanged: like the
  Prune Watermark, the Assume-valid Height exists so that two kinds of
  not-checked — deferred on purpose, and failed — can never be confused.

## What would retire this

An engine and a machine fast enough that full verification from genesis is an
overnight job rather than a season. Real concurrency in Aver
([jasisz/aver#1007](https://github.com/jasisz/aver/issues/1007)) plus both
Script issues closing would reopen the question; until then the trade stands.
