# Speak the Bitcoin P2P protocol directly, not bitcoind's JSON-RPC

This program connects to a Bitcoin node over the peer-to-peer wire protocol and
decodes transactions itself, rather than asking a local `bitcoind` for
already-decoded transactions over JSON-RPC.

RPC was genuinely the easier path. The node would have done the framing, the
checksums and the parsing, and Aver could have used `Http.post`, which already
worked. P2P needed things the language did not have at all: byte-clean sockets
(`Tcp.send` corrupted every non-UTF-8 byte via `from_utf8_lossy`, and
`readLine`/`writeLine` could neither read nor write arbitrary bytes) and
SHA-256, which cannot be implemented in Aver because it has no bitwise
operators.

We chose P2P because transaction decoding is the point of the exercise — pure,
total, recursive parsing over a well-specified binary format is what Aver is
good at, and RPC would have handed that part to the node. The missing runtime
capabilities were worth closing on their own merits, not just for this program.
They were: `Tcp.sendBytes`, `Tcp.readBytes`, `Tcp.writeBytes` and
`Crypto.sha256`, contributed upstream as jasisz/aver#764, #777, #779 and #781
and released in Aver 0.28.0 "Oktet".

The cost is real and worth stating: this program implements framing, checksums,
the handshake and the transaction parser itself, and each is a place to be
wrong in ways RPC could not have been. It is also pinned to Aver 0.28.0 or
later.

If this is ever revisited — because the P2P surface proves too large to
maintain, or because decoded output is wanted for shapes the parser does not
cover — JSON-RPC remains available and would need a JSON parser and base64 for
HTTP Basic auth, neither of which Aver has today.
