# Bitcoin Peer Listener, Chain Downloader and Auditor

Connects to a single Bitcoin node over the peer-to-peer protocol, listens for
transaction announcements, and prints each transaction's decoded structure. It
also downloads the chain — every Block Header, then Block bodies for a chosen
range — keeping the bytes in Segments on disk and their whereabouts in an Index.

What it holds it then checks: each Block against its Header, its parent and its
target, each Transaction against the Outputs its Inputs claim to spend, and each
Input's pair of Scripts, signatures included.

## Language

### The connection

**Peer**:
A Bitcoin node this program has connected to and completed a handshake with.
This program is itself a node; a Peer is what it talks to.
_Avoid_: node, server, host, endpoint, remote

**Peer Address**:
The validated network location of a Peer — four octets and a port. Constructed
only by parsing, so an unvalidated address cannot reach the rest of the program.
_Avoid_: IP, host, target, addr

**Candidate**:
A Peer Address this program has heard about but not connected to. It becomes a
Peer only when a Handshake completes; until then nothing is known about it
beyond where it claims to be, and the claim came from a Peer with no obligation
to be honest.
_Avoid_: peer, node, entry, address

**Address Book**:
The Candidates this program is holding, and what it knows about each — where it
was heard from and when it was last reachable. Distinct from the DNS seeds,
which are the bootstrap and not part of it.
_Avoid_: addrman, peer table, address manager, cache

**Network**:
Which Bitcoin network a Peer belongs to. Determines the magic bytes that prefix
every Message, so a Message is only meaningful with respect to one Network.
_Avoid_: chain, environment, mode

**Handshake**:
The version / verack exchange that must complete before either side sends
anything else. A connection that has not completed it is not yet a Peer.
_Avoid_: negotiation, greeting, connect

### The wire

**Message**:
One framed unit on the wire: magic, command, length, checksum, and payload.
The unit both sides count in — never a partial read.
_Avoid_: packet, frame, envelope, datagram

**Command**:
The kind of a Message, carried in its header as twelve ASCII bytes —
`version`, `verack`, `inv`, `getdata`, `tx`, `ping`, `pong`.
_Avoid_: type, opcode, verb, method

**Checksum**:
The first four bytes of the double-SHA256 of a Message's payload. Proves the
payload arrived intact; a mismatch means the stream is no longer trustworthy.
_Avoid_: hash, digest, crc

**Announcement**:
An `inv` Message offering transactions the Peer holds. It carries identifiers
only — the transactions themselves must be requested.
_Avoid_: notification, advertisement, inv

### What we decode

**Transaction**:
A Bitcoin transaction as it arrives on the wire: version, Inputs, Outputs,
Witnesses, and locktime.
_Avoid_: tx, payment, transfer

**Transaction Id**:
The double-SHA256 of a Transaction's serialised form, displayed in reverse byte
order by long-standing convention. The reversal is a display concern, not an
identity one.
_Avoid_: txid, hash, id

**Input**:
A reference to an Output being spent, by Transaction Id and index.
_Avoid_: vin, source, spend

**Output**:
An amount and the script that controls who may spend it.
_Avoid_: vout, destination, payment

**Witness**:
Signature data carried outside the Inputs, present only in SegWit-serialised
Transactions. Its presence changes how a Transaction is framed, so it must be
detected before the Inputs can be read at all.
_Avoid_: segwit data, signature, script witness

### The chain

**Block**:
A Block Header and the Transactions it commits to, as it arrives on the wire.
_Avoid_: blk, chunk, batch

**Block Header**:
The eighty bytes that identify a Block and name its predecessor. Small enough to
hold the whole chain's worth, which is why they are fetched before any body.
_Avoid_: head, metadata, preamble

**Block Id**:
The double-SHA256 of a Block Header, displayed in reverse byte order, exactly as
a Transaction Id is. A Block's only stable name.
_Avoid_: block hash, hash, digest

**Branch**:
A run of Headers each naming the one below it, ending at a Tip. Every Header we
hold sits on one; the chain this program follows is the Branch with the most
Chain Work behind it. A Header whose parent is not the Tip starts a Branch, and
that is ordinary rather than an error.
_Avoid_: fork, side chain, alternate chain, candidate

**Catch-up**:
The run from where the node stands to the Tip it is following: the Header
phase, then the body phase, then the Set phase. Bounded at both ends — it
starts from what the directory already holds and ends when the UTXO Set stands
on the Tip, not merely when the last body is on disk. A node that has caught up
still catches up again every time a Peer announces a Block; the same three
phases over a range of one.
_Avoid_: sync, IBD, initial block download, backfill, bootstrap

**Chain Work**:
How much hashing a Branch represents: for each Header, the whole 256-bit space
divided by the values its target admits, summed from the first Block. What
decides which Branch to follow — Height does not, because a longer Branch of
easier Blocks is worth less than a shorter Branch of harder ones.
_Avoid_: difficulty, total difficulty, weight, score, length

**Fork Height**:
The highest Height at which two Branches still name the same Block — the last
thing they agree about, and so the point a Reorganisation has to take
everything derived from the chain back to. Below it nothing changed and nothing
needs undoing.
_Avoid_: split point, common ancestor, base, divergence

**Height**:
A Block's distance from the first Block. A position rather than an identity: the
chain may reassign one, so a Height names a slot and never a Block.
_Avoid_: index, number, sequence, depth

**Locator**:
The descending list of Block Ids sent with `getheaders` to tell a Peer how far we
have got. Sparse by design — recent Ids in full, then exponentially thinning.
_Avoid_: checkpoint, cursor, bookmark

**Orphan Header**:
A Header whose parent we do not hold. It gets no Height and no Chain Work,
because both are counted from the parent, so it cannot join the tree yet.
Not refused — nothing is wrong with it — just not placed.
_Avoid_: orphan block, stray, floating, invalid

**Placement**:
What a Header becomes once its parent is known: the Height it sits at and the
Chain Work of the Branch below it. Neither is carried by the Header itself,
which is why a Header without a parent has no Placement.
_Avoid_: node, block index, entry, record

**Reorganisation**:
The chain changing which Branch it follows, so Heights that named one Block now
name another. Distinct from the chain merely growing: growth writes Heights that
held nothing, a Reorganisation displaces Heights that held something else, and
everything derived from the displaced Blocks is now describing Blocks that are
no longer on the chain.
_Avoid_: rollback, revert, chain switch, fork

**Tip**:
The Header at the end of a Branch — one that no other Header we hold names as
its parent. A tree has as many Tips as Branches, and the one with the most
Chain Work is the chain this program follows.
_Avoid_: head, top, leaf, best block, latest

### What we hold unconfirmed

**Mempool**:
The Transactions this node has validated against the UTXO Set and holds while
no Block contains them. Admission is bounded by Policy as well as by
consensus, and by a size cap: what is evicted first is what pays least.
_Avoid_: memory pool, tx pool, unconfirmed set, queue

**Policy**:
Rules this node applies to what it will hold and relay, over and above what
the chain enforces. A Transaction refused by Policy may still be valid; one
refused by consensus never is. Peers enforce Policy on each other socially —
by dropping and banning — which is why a node cannot opt out alone.
_Avoid_: standardness (that is one Policy, not the boundary), relay rules

### The Screen

**Screen**:
The live terminal view `follow` runs when asked to (the `screen` word): raw
mode, a frame redrawn from the Snapshot, keys switching Panels, and a stop
question behind `q`. Owned by the loop that owns everything else, and every
way out — quit, polite stop, error — gives the terminal back.
_Avoid_: GUI, TUI, dashboard, display, console

**Panel**:
One of the Screen's five views — Overview, Peers, Blocks, Transactions,
Mempool — each a pure rendering of the Snapshot, switched by a single key. A
Panel not yet built says which issue it is waiting for, because a blank Panel
and an unbuilt Panel should not look alike.
_Avoid_: screen (that is the whole), tab, page, view, widget

**Snapshot**:
One picture of the running node, assembled every tick of `follow`'s loop:
the tip, catch-up progress, who it is talking to, what the Mempool holds, and
the retention rings of the newest few Blocks, Reorganisations and
Transactions. Everything shown — Panel or log line — is a rendering of it,
and it is never a log: the rings forget on purpose.
_Avoid_: state, status, metrics, telemetry, model

### What we store

**Store**:
A keyed byte store offering get, put, delete and batch. One API over two
**backends**: Memory, which fixtures return, and Database, a RocksDB behind
the `Infra.Kv` capability. A third, Logged — an append-only file — was
retired with `migrate` (#44); a directory holding only its `index.log` is
refused rather than read. The Store is derived data: `reindex` rebuilds every
Block's Location from the Segments, which are the source.
_Avoid_: database, cache, map, persistence

**Index**:
What we keep in a Store: Block Ids to Locations, Heights to Block Ids,
Transaction Ids to sites, and Outputs to what they are worth. Content, where a
Store is mechanism, and unchanged by which backend holds it.
_Avoid_: db, catalogue, lookup, registry

**Segment**:
One size-capped file of length-framed Block records — four bytes of
little-endian length, then the Block — and the unit pruning deletes. A Block
never straddles two of them.
_Avoid_: blk file, shard, partition, volume

**Location**:
Where a Block's bytes are: a Segment, the byte offset within it, and how many
bytes they are. Meaningless without the Segment, so the three travel together.
_Avoid_: pointer, position, address

**UTXO Set**:
The Outputs no Transaction has yet spent — what a validating node checks each
Input against. Grows as Blocks create Outputs, shrinks as they spend them, and
is rolled back by Undo Data when the chain reorganises. Distinct from the
Index's record of Outputs, which only ever grows.
_Avoid_: chainstate, coins, coin database, unspent set

**Undo Data**:
The Outputs a Block removed from the UTXO Set, kept so the removal can be
reversed if that Block leaves the chain. Without it the UTXO Set cannot go
backwards, and a reorganisation would mean rebuilding it from the start.
_Avoid_: rev data, rollback log, spent journal

**Undo Window**:
How far back the Undo Data still reaches — 288 Blocks, two days. A
Reorganisation whose Fork Height is inside it can be undone; one below it
cannot, and the node stops and says so rather than guessing. The bound is a
deliberate trade: keeping Undo Data for the whole chain would cost more than
the chain.
_Avoid_: reorg depth, rollback limit, history window

**Assume-valid Height**:
The Height below which a syncing node takes Scripts as settled and does not run
them; merkle, parent, work and value accounting are still checked. Above it,
everything is verified. What was skipped is exactly what `audit` exists to
revisit — a claim deferred, never a claim made.
_Avoid_: checkpoint, assumevalid, trusted height

**Prune Watermark**:
The Height below which Blocks were deliberately deleted. What separates a Block
we never fetched from one we chose to discard — the two look identical in the
Index and demand opposite responses.
_Avoid_: cutoff, floor, horizon, threshold
