# Transaction fees are not reported, because the P2P protocol does not expose them

A fee is the difference between what a Transaction's Inputs are worth and what
its Outputs are worth. A Transaction states its Output amounts but not its Input
amounts — an Input names only the Transaction and index it spends — so a fee
cannot be computed from a Transaction alone. The values of the Outputs being
spent have to come from somewhere else.

Over the peer-to-peer protocol they cannot. A Peer answers `getdata` for a
Transaction from its mempool and a short relay cache; Transactions already
confirmed in blocks are not served that way. Since Inputs almost always spend
confirmed Outputs, the lookup fails almost always.

Measured against Bitcoin Core 27.0.0 on mainnet rather than assumed:

- 29 `getdata` requests for the previous Transactions of live mempool Inputs:
  **29 answered `notfound`**, none served.
- Across 111 decoded Transactions carrying 441 Inputs, the number whose spent
  Transaction also appeared in the same stream: **0**.

The second measurement rules out the obvious workaround. Caching the Outputs of
every Transaction we decode and resolving Inputs against that cache would have
computed a fee for none of the 111. It would also put state into a loop that
deliberately carries none, in exchange for a number that would essentially never
be printed.

Fees are therefore obtainable only by asking the node directly, outside the
peer-to-peer protocol — `getmempoolentry <txid>` returns one per Transaction
without needing any Input resolution or `txindex`. That means JSON-RPC, and so a
JSON parser and base64 authentication written in Aver, neither of which the
language provides, plus RPC credentials as configuration. It would also amend
[ADR 0001](0001-speak-p2p-not-json-rpc.md), which records that this program
speaks the wire protocol and not RPC.

That is a reasonable thing to build and not a reasonable thing to build by
accident, so fees are out of scope until someone wants them enough to accept
that cost. This note exists so the question is answered from measurement rather
than re-investigated from scratch.
