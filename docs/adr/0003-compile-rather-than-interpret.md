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

> **Superseded by the 14 August update below.** "A considered preference rather
> than a necessity" was written from the decoding measurements alone, before the
> Index existed and before anyone opened a Store under `aver run`. It is wrong,
> and the README was right to say the downloader must be compiled.

Note that [#890](https://github.com/jasisz/aver/issues/890) — a `Map` returned
from a function is copied — is a separate defect. It is the reason
`infra/store.av` folds the way it does, and it is unaffected by any of the
above. **It closed on 20 August 2026**; what that means for the folds is at
the end of this document.

## Update, 14 August 2026 — a necessity again, for a different reason

> **Superseded by the 17 August update below.** #900 is closed. The interpreted
> Store now opens the whole real Index, so the "unable to finish" argument in
> this section is spent. #890, the other half, is untouched and still is.

The decision stands, and not as a preference. What restores it is not the list
defect that prompted it but two `Map` defects that arrived with the Index:
[#890](https://github.com/jasisz/aver/issues/890), a `Map` handed back from a
function is copied, and [#900](https://github.com/jasisz/aver/issues/900),
building a `Map` is quadratic under `aver run`. Both are open on `d6b72152`.

[#905](https://github.com/jasisz/aver/pull/905) reworked `Map.fromList` and says
plainly why this is not over: *"filling a map is still quadratic in time,
because a map has no equivalent of a list body's all-immediate flag."*

### Opening a Store, on real Index data

`Infra.Store.open` replays its log into a `Map`. Truncated prefixes of a real
mainnet Index, timed end to end:

| entries | compiled | `aver run` |
|---|---|---|
| 10,000 | 30 ms / 6 MB | 3,480 ms / 2,059 MB |
| 20,000 | 60 ms / 10 MB | 17,710 ms / 8,189 MB |
| 40,000 | 140 ms / 19 MB | 107,950 ms / 30,425 MB |

Compiled is linear in both. Interpreted is quadratic in both, and at 40,000
entries it wants 30 GB on a 32 GB machine. The whole 60,001-entry Index opens
compiled in 0.26 s and 33 MB. A finished Index is 962,268 entries.

So this is the 1 MiB `Bytes.fromHex` situation again: not slower, but unable to
finish. Unlike the decoding numbers above, no amount of patience substitutes for
the build step.

### Why the fold in `infra/store.av` is shaped as it is

#890 is the compiled half, and it is untouched. Building a 80,000-entry `Map`
three ways, all compiled:

| fold | time |
|---|---|
| `Map.set` inline in the recursive call | 58 ms |
| branch, then tail-call — what `store.av` does | 55 ms |
| via a helper that returns a `Map` | 101,496 ms |

Roughly 1,800× for writing the obvious helper. `applyChanges` and `applyNext`
are split the way they are for this reason alone, and `putAll`/`deleteAll` exist
so a batch costs one copy of the Store rather than one per entry.

### What would retire this

#900 closing would make `aver run` viable for the downloader. #890 closing would
let `infra/store.av` be written the natural way. They are independent: either can
land without the other, and only the first bears on this decision. The compiled
path stays valid either way, so nothing depends on this beyond the build
instructions and the note in the README.

## Update, 17 August 2026 — a preference again, and this time it holds

[#961](https://github.com/jasisz/aver/pull/961) closed
[#900](https://github.com/jasisz/aver/issues/900), and
[#960](https://github.com/jasisz/aver/pull/960) closed
[#955](https://github.com/jasisz/aver/issues/955) and #898. The toolchain here is
`7f7bf00a`, still version 0.28.1.

### The table from 14 August, re-measured

Same truncated prefixes of the same mainnet Index, same machine, `aver run`:

| entries | 14 August | 17 August |
|---|---|---|
| 10,000 | 3,480 ms / 2,059 MB | 67 ms / 23 MB |
| 20,000 | 17,710 ms / 8,189 MB | 146 ms / 37 MB |
| 40,000 | 107,950 ms / 30,425 MB | 312 ms / 67 MB |

Linear, and 346× faster at the size where it used to want 30 GB. So the whole
Index can be opened interpreted now, and was: a 1,484,520-record log holding
1,454,101 live entries takes 13.9 s and 1.7 GB under `aver run`, against 3.2 s
and 615 MB compiled.

That is 4× on time and 2.8× on memory. Real, worth having for a download that
takes hours, and nothing like the difference between finishing and not. So the
README no longer says the downloader *must* be compiled; it says compile it, and
gives the ratio as the reason.

### #890 is untouched, so the batching stays

Building an 80,000-entry `Map` three ways, compiled, against the same three
measured on 14 August:

| fold | 14 August | 17 August |
|---|---|---|
| `Map.set` inline in the recursive call | 58 ms | 48 ms |
| branch, then tail-call — what `store.av` does | 55 ms | 49 ms |
| via a helper that returns a `Map` | 101,496 ms | 105,633 ms |

Unchanged: roughly 2,150× for writing the obvious helper. The Store's own writes
say the same thing, compiled, 40,000 entries: **52 ms** through one `putAll`
against **24,404 ms** one `put` at a time, because each `put` hands back a Store
holding a copy of the whole `Map`. Every batch in `infra/download.av`,
`infra/txindex.av` and `infra/prune.av` earns its keep, and `absorbAll`,
`forgetAll` and `replayApplied` still have to keep `Map.set` out of a helper.

### Re-measured 26 August 2026, and half of it has gone

[#890](https://github.com/jasisz/aver/issues/890) closed on 20 August, so the
numbers above were re-taken on the `f6d7992c` pin, same machine, same shapes.
The result is a split, and the split is the useful part.

**A helper returning a bare `Map` is fixed.** Same three folds, compiled:

| fold | 17 August, 80,000 | 26 August, 80,000 |
|---|---|---|
| `Map.set` inline in the recursive call | 48 ms | 14 ms |
| branch, then tail-call | 49 ms | 14 ms |
| via a helper that returns a `Map` | 105,633 ms | **13 ms** |

Not 2,150× any more. Not measurable: the three are indistinguishable across
repeated runs (11–16 ms), and the obvious helper is as fast as anything else.

**A helper returning a record that holds a `Map` was not fixed, and now is.**
Same machine, same size, compiled, 40,000 entries:

| shape | 26 August | 28 August, `b63214d6` |
|---|---|---|
| helper returns a bare `Map` | 7 ms | 8 ms |
| helper returns a record holding a `Map` — what `put` does | **15,660 ms** | **6 ms** |

Filed as [jasisz/aver#1160](https://github.com/jasisz/aver/issues/1160), the
sibling of #890, and closed by
[#1163](https://github.com/jasisz/aver/pull/1163) — "Fix record-owned map
updates and Rust parameter binders". The record wrapper costs nothing now; the
two shapes are indistinguishable, and the one that was 2,200× slower is if
anything the faster of the pair.

**Which means the batching has lost one of its two reasons and keeps the
other.** For two days it had both: `Infra.Store.put` hands back a `Store`, a
`Store` is a record holding a `Map`, and so writing one key at a time paid the
copy every time. That is gone.

What is not gone is [ADR 0006](0006-a-leveldb-under-the-index.md)'s argument:
one `Kv.putAll` is one RocksDB `WriteBatch`, and a batch is what makes a set
of changes land together or not at all. A Block's Locations, its Index entries
and its Undo record are one such set — a crash between them leaves an Index
naming a Block the Segment does not hold.

**So the batching stays, and this is not a workaround left behind.** It is
worth writing down which reason a thing rests on, because the temptation on
seeing a cliff close is to retire everything that was ever shaped by it. Half
of what shaped the batches was a performance bug and half was correctness, and
only the first half has an expiry date.

Curiously the three folds are now *equal* under `aver run` — 6,423 / 6,666 /
6,055 ms at 40,000 — because all three are equally slowed by something else. See
below. **Re-measured 26 August: 28 / 27 / 28 ms at the same size**, so they are
still equal and the something else has gone too.

### What did come out: the two-pass replay

`Infra.Store.open` parsed its log into a `List<Change>` and then applied the
list, because threading a `Map` through a `Result`-returning recursion used to be
quadratic while either half alone was linear. That is what #961 fixed. Compiled,
fused against split:

| records | split | fused |
|---|---|---|
| 20,000 | 20 ms | 17 ms |
| 40,000 | 45 ms | 41 ms |
| 80,000 | 98 ms | 103 ms |
| 160,000 | 219 ms | 216 ms |

Equal at every size, and on the real 1,454,101-entry Index equal on time and
52 MB cheaper, the intermediate list being gone. `parseAll`, `parseNext`,
`applyChanges` and `applyNext` are replaced by `replayed`, `replayNext` and
`replayApplied`, which read the log in one walk. This is the first workaround in
this project that a fix upstream has actually retired.

### The residue, filed as #963

One shape is still quadratic under `aver run`: a `Map` filled with keys
**interpolated in the loop that inserts them**. 40,000 inserts take 7,023 ms that
way, 27 ms when the keys come from a list built beforehand, and 29 ms when they
are sliced out of one large String. Compiled, all three are linear.

**Re-measured 26 August: 54 ms interpolated against 39 ms from a list**, under
`aver run` at the same size. #963's residue is gone, which is also why the fold
table above went from about 6,000 ms to about 28 ms — those folds interpolate.

That is why the fold table above went flat interpreted — all three folds
interpolate, so all three pay it — and it is
[#963](https://github.com/jasisz/aver/issues/963).

It does not touch this project. `Infra.Store` slices its keys out of the log
text, which is the fast column, which is why the real Index opens linearly at a
million and a half entries while a 40,000-iteration benchmark does not.

### What is left to retire

**Both of the issues this section used to be waiting on closed on 20 August
2026, and this section did not notice for six days.** That is worth recording
as a failure of the pin-moving routine rather than quietly editing away: the
routine greps for `jasisz/aver#` and checks whether each issue is closed, and
a paragraph that says "still open" in prose passes that grep looking exactly
like a paragraph that says "closed".

[#782](https://github.com/jasisz/aver/issues/782), the 30-second TCP read
deadline, was answered by **removing** the deadline rather than making it
configurable — timing out part way through a frame leaves the stream silently
desynchronised — and every frame now starts with `Tcp.poll` at the message
boundary, which is the one place a timeout abandons nothing. The README has
said so correctly all along; only this section was stale.

[#890](https://github.com/jasisz/aver/issues/890), a `Map` returned from a
function being copied, closed too. **It retires no code here**, which is worth
being plain about:

- `absorbAll`, `forgetAll` and `replayApplied` are already written the natural
  way — a tail-recursive walk with `Map.set` inline in the recursive call. The
  workaround #890 forced was a constraint on *how* to write them, not an extra
  layer to take out. There is nothing to delete.
- `putAll` and `deleteAll` keep their batching, and [ADR
  0006](0006-a-leveldb-under-the-index.md) already gives the reason that
  outlives #890: one `Kv.putAll` is one RocksDB `WriteBatch`, which is where
  the all-or-nothing guarantee comes from. A batched API that exists for
  atomicity does not stop being wanted because the copy it also avoided is
  gone.

The arithmetic was re-measured on 26 August 2026 (#188) and the result is
above, under *Re-measured 26 August 2026, and half of it has gone*. Half the
document's numbers were stale and half were not, which is why they were left
standing until somebody ran them rather than adjusted by guesswork: the
2,150× for a helper returning a bare `Map` is gone entirely, and the Store's
own penalty for a helper returning a *record* holding one is intact.
