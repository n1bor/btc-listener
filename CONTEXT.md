# Bitcoin Peer Listener

Connects to a single Bitcoin node over the peer-to-peer protocol, listens for
transaction announcements, and prints each transaction's decoded structure.

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
