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
