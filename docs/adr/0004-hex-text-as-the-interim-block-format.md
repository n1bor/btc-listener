# Store Blocks as hex text, for now

Segments hold Blocks as newline-delimited hexadecimal rather than as the bytes
that arrived on the wire. A blockchain stored in a text file is an odd enough
sight to be worth explaining: it is not a preference, it is the only thing Aver
can currently do.

`Disk` is text-only — `readText`, `writeText`, `appendText` and nothing else.
There is no byte-oriented file API, and `aver_rt::read_text` is
`std::fs::read_to_string`, which rejects non-UTF-8 input outright. So a Block
cannot be written as bytes, and could not be read back if it were. Hex through
`Disk.appendText` is the only route to durable binary data, and it has the
incidental virtue of being self-framing: one Block per line needs no length
prefix, where a binary format would need explicit framing as Bitcoin Core's
`blk*.dat` does.

The maintainer is already working on byte-oriented `Disk`, so this is a bridge
rather than a destination, and we chose not to block on it.

The cost is real and worth stating plainly, because it is larger than the obvious
one. Doubling the bytes on disk is the cheap part. The expensive part is decode
on every read: compiled `Bytes.fromHex` runs at 752 ms and 233 MB per MiB, so a
1.6 MB Block costs about 1.2 s and 370 MB before a single field is parsed. Across
962,000 Blocks that is on the order of thirteen days spent purely turning text
back into bytes — on precisely the path a future UTXO scan depends on.

## Consequences

The decode cost sets the Segment cap. A Segment must be read whole, since Aver
has no positional reads, and `fromHex` peaks around 927 MB for 4 MiB of Block
data. Segments are therefore capped near 2 MiB of Blocks rather than at Bitcoin
Core's 128 MiB — a cap imposed by the hex read path, not by anything about
Blocks. When byte I/O lands, the cap can rise.

Segments written by this version will not be readable by the binary one: hex is
newline-framed, binary will be length-framed. For a bounded range of Blocks,
re-downloading is cheaper than a migration, so no upgrade path is provided.

## Update, 16 August 2026 — the decode cost is largely gone

[jasisz/aver#911](https://github.com/jasisz/aver/issues/911) is fixed. `fromHex`
was building a `String` per hexadecimal character before looking at any of them;
the compiler's fusion pass now walks the string with a cursor instead. Measured
on the same reproduction, decoding a mebibyte:

| | before | after |
|---|---|---|
| `Bytes.fromHex`, 1 MiB | 1,239 ms | **28 ms** |

Forty-four times, and about 37 MB/s against 0.81. The thirteen-day figure above
was arithmetic on the old rate; on the new one the same sum is hours rather than
days.

That does not mean hex is now free, and the interesting number is smaller than
the ratio suggests. Reading Block 800000 back and checking it — 1.6 MB, 3,721
Transactions — went from 5.6 s to 3.3 s, not from 5.6 s to 0.2 s. Decoding the
hexadecimal is no longer what dominates; reading the whole Segment and decoding
the Transactions inside it now is.

So the decision stands, but its reason has moved. Hex text was chosen because
`Disk` cannot write binary, and that is still true. It was defended by arguing
the decode cost was tolerable, and that argument is now much stronger than when
it was made — while the case for binary rests on the Segment read and the
storage doubling rather than on `fromHex`.
