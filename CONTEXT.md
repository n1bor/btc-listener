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

**Height**:
A Block's distance from the first Block. A position rather than an identity: the
chain may reassign one, so a Height names a slot and never a Block.
_Avoid_: index, number, sequence, depth

**Locator**:
The descending list of Block Ids sent with `getheaders` to tell a Peer how far we
have got. Sparse by design — recent Ids in full, then exponentially thinning.
_Avoid_: checkpoint, cursor, bookmark

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
