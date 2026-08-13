# Ship the downloader compiled, not interpreted

Every other Aver program in this repository runs under `aver run`. The chain
downloader cannot, and has to be built with `aver compile --target rust` followed
by `cargo build --release`. This is deliberate, and the reason is a defect in the
interpreter rather than a preference.

`aver run` is quadratic in time and memory whenever a `match` destructures a list
while an accumulator list grows. That shape is not incidental — Aver has no
loops, so consuming a list means recursing on `[head, ..tail]`, and producing one
means threading an accumulator. It is how every parser here is written, including
the transaction decoder that predates this decision and `stdlib/bytes.av`'s own
`parseHexChars`.

Measured on this machine, copying a 64,000-element list:

| | time | peak RSS |
|---|---|---|
| `aver run` | 29,369 ms | 16.0 GB |
| compiled | 14 ms | 5.7 MB |

Roughly 2,100× the time on 2,800× the memory, and the gap widens with size
because one curve is quadratic and the other is not. `Bytes.fromHex` inherits the
same shape: 64 KiB of hex takes 68.8 s and 31 GB interpreted, 52 ms and 16 MB
compiled, and 1 MiB does not complete at all on a 32 GB machine.

Blocks near the chain tip are 1–2 MB and there are 962,000 of them, so the
interpreter is not slow here — it is unusable, by several orders of magnitude.

Reported upstream as [jasisz/aver#886](https://github.com/jasisz/aver/issues/886),
with a thirty-line reproduction and the narrowing that isolates the trigger: two
large lists being live at once is fine, and growing a list is fine; destructuring
one while growing another is not.

## Consequences

Running the downloader takes a build step, and `aver run` remains correct but
useful only for small inputs — worth remembering when reaching for it to try
something out, because the failure is a machine that swaps rather than an error
message. The transaction listener is unaffected in practice, since transactions
are small enough that the quadratic term stays invisible.

If #886 is fixed, this decision can simply be dropped: the compiled path stays
valid either way, so nothing depends on it beyond the build instructions.

## Update, 13 August 2026 — the premise no longer holds

#886 is fixed, by [jasisz/aver#897](https://github.com/jasisz/aver/pull/897). The
cost was in the collector rather than in the instruction stream:
`rewrite_list_with` rebuilt a fresh backing vector on every `Flat` and `Segments`
node it walked, discarding both the sharing and the offset that `list_uncons`
hands out, and the input list is live across every step — so one traversal
copied n²/2 elements. Element storage now records whether every element is
immediate, and a body that cannot contain anything relocatable is returned
untouched.

Measured on the same reproductions:

| | before | after |
|---|---|---|
| copy a 64,000-element list | 29,369 ms / 16.0 GB | 24 ms / 15 MB |
| `Bytes.fromHex`, 64 KiB | 68.8 s / 31 GB | 103 ms / 17 MB |
| `Bytes.fromHex`, 1 MiB | did not complete | 1.9 s / 143 MB |

So the reason recorded above is gone: the interpreter is no longer unusable, it
is merely slower. On time the margin is modest — decoding a mebibyte of hex takes
1,924 ms interpreted against 752 ms compiled, about 2.5×.

Memory is the half that still argues for compiling, and it does not show up in a
microbenchmark. Decoding a real 2,000-header message of 162,003 bytes:

| | time | peak RSS |
|---|---|---|
| `aver run` | 1,891 ms | 1,295 MB |
| compiled | 1,364 ms | 37 MB |

Thirty-five times the memory for the same work, and the same Block Ids out of
both. A hex microbenchmark suggests the opposite — 143 MB interpreted against
233 MB compiled for a mebibyte — so the comparison has to be made on real
decoding rather than on `Bytes.fromHex` alone.

That is what keeps this decision standing. A Block near the tip is ten times the
size of that header message, and there are 962,268 of them; a gigabyte of
resident memory per batch is not something to design around when a build step
removes it. Compiling is now a considered preference rather than a necessity,
and the original consequence — that `aver run` is useless for trying something
out — no longer holds. Small experiments interpret perfectly well.

Note that [#890](https://github.com/jasisz/aver/issues/890) — a `Map` returned
from a function is copied — is a separate defect and is still open. It is the
reason `infra/store.av` folds the way it does, and it is unaffected by any of
the above.
