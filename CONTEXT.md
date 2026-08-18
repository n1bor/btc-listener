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

### What we store

**Store**:
A keyed byte store offering get, put, delete and batch. One API over three
**backends**: Memory, which fixtures return; Logged, an append-only file; and
Database, a LevelDB behind the `Infra.Kv` capability. Which one a directory
uses is decided by what is in it, not by a flag.
_Avoid_: database, cache, map, persistence

**Index**:
What we keep in a Store: Block Ids to Locations, Heights to Block Ids. Content,
where a Store is mechanism, and unchanged by which backend holds it.
_Avoid_: db, catalogue, lookup, registry

**Segment**:
One size-capped file of Block bytes, and the unit pruning deletes. A Block never
straddles two of them.
_Avoid_: blk file, shard, partition, volume

**Location**:
Where a Block's bytes are: a Segment, and the line within it. Meaningless without
the Segment, so the two travel together.
_Avoid_: offset, pointer, position, address

**Prune Watermark**:
The Height below which Blocks were deliberately deleted. What separates a Block
we never fetched from one we chose to discard — the two look identical in the
Index and demand opposite responses.
_Avoid_: cutoff, floor, horizon, threshold
