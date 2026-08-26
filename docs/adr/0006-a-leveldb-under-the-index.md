# Put the Index on a LevelDB, and keep the log

The Index — Block Ids to Locations, Heights to Block Ids, Transaction Ids to
sites — was one `Map`, rebuilt on every open by replaying an append-only file.
It is now offered over three backends behind one opaque API, and a directory
holding a `kv/` is read through a LevelDB instead.

This is the decision that plan said would be made on measurement rather than on
expectation, so the numbers come first.

## Measured

Same machine, same binary, same 22 GB of Segments, and two copies of the same
index — 1,454,101 entries, 124 MB as a log and 121 MB as a database. The
`blocks` directory is hard-linked between them, so both read identical bytes.

| | log | database |
|---|---|---|
| open (`audit 1 1`) | 3.56 / 3.75 / 3.96 s, 615 MB | 0.15 / 0.16 / 0.16 s, 47.8 MB |
| one lookup (`tx`) | 4.49 / 5.52 s, 667 MB | 0.04 / 0.05 s, 19.8 MB |
| `audit 1 4000` | 7.6 s, 615 MB | 4.0 s, 67 MB |
| `audit 170000 172000` | 44.5 s, 615 MB | 49.4 s, 97 MB |
| `txindex 170000 171000` (38,545 tx) | 13.55 s, 625 MB | 10.28 s, 98 MB |
| `txindex 170000 172000` (83,902 tx) | 24.36 s, 638 MB | 18.40 s, 97 MB |

Every total was identical: `247 passed / 0 failed / 0 undecided` on the first
range, `115 passed` with `81888 unresolved` on the second. The migration itself
moved 1,454,101 entries in 6.9 s.

Three things in that table are worth separating.

**Memory is the win, and it is not close.** 615 MB is the whole Index held open
regardless of what is being read, and it grows with the chain. 20 to 97 MB is
what the work in front of it actually needs. The ceiling stops being RAM.

**Opening stops costing anything.** 3.75 s to 0.16 s. For `audit` over
thousands of Blocks that is a rounding error; for `tx` and `spend`, which do one
lookup and exit, it was the entire runtime. A lookup went from four and a half
seconds to forty milliseconds.

**Reading is not free any more, and one number is worse.** `audit 170000
172000` is 11% slower — it resolves 83,902 Transactions against an Index that
used to answer from memory. On the write path the two are level at 38,545
Transactions and the database is 12% ahead at 83,902, because the log backend
copies a growing `Map` on every batch and the database does not. Where the
database loses, it loses percentages; where it wins, it wins an order of
magnitude.

## Decided

**Keep all three backends.** The database is not retiring the log yet:

- `migrate` reads the log. Retiring the log retires the only path onto the
  database for every directory that already exists.
- Memory is what `Store.fixture` returns and what 71 verify cases across four
  modules are written against. It is not going anywhere while
  [jasisz/aver#989](https://github.com/jasisz/aver/issues/989) keeps `aver
  verify` away from providers.
- The database backend has been exercised on two audit ranges, a migration and
  two `txindex` runs. It has never done a `headers` or `bodies` download,
  because that needs a Peer. Retiring the tested path in favour of the
  half-tested one, on one day's evidence, would be the opposite of what this
  file is for.

The cost of keeping the log is a finished module that does not change.

**Which backend a directory uses is decided by the directory.** Not by a flag.
The data is in one shape or the other, and a flag that claimed otherwise would
be lying. `migrate` writes `kv/` and leaves `index.log` alone, so moving back is
`mv chain/kv chain/kv-aside` — which is how the two columns above were measured
against each other in the first place.

## Retired

**`compact` is gone**, from `Infra.Store` and from the `Infra.Kv` contract. Not
because a database compacts itself, though it does, but because
`git log -S` says it has never had a caller — exposed from its first commit and
never once invoked on any backend. It can come back the day something calls it.

**The batching stays.** `putAll` and `deleteAll` exist because
[jasisz/aver#890](https://github.com/jasisz/aver/issues/890) makes per-entry
`Map` writes quadratic: 52 ms against 24,404 ms for 40,000 entries. #890 closed
on 20 August 2026 and the numbers were re-taken on 26 August: **8 ms against
15,660 ms** at the same size. Smaller, and the same shape — because #890 fixed
a helper returning a bare `Map` and not one returning a *record* holding a
`Map`, which is exactly what `Infra.Store.put` does. The reason has not
expired. See [ADR 0003](0003-compile-rather-than-interpret.md). On the database arm the
same shape earns its keep differently — one `Kv.putAll` is one LevelDB
`WriteBatch`, which is where the all-or-nothing guarantee comes from — so there
is no version of this API that stops being batched.

## Consequences

Every module on the read path now declares `Infra.Kv.get` or one of its
siblings. That ripple was mechanical and the checker named every site, but it is
permanent: a Store read is an effect now, and ten modules say so. `size` became
a `Result` for the same reason.

Two Aver defects came out of it and both were fixed within a day of being
filed: [#994](https://github.com/jasisz/aver/issues/994), which had forced
`Kv.Handle` to be a record holding an `Int` rather than an opaque resource, and
[#995](https://github.com/jasisz/aver/issues/995), which had `aver audit`
carrying 50 warnings that tests which cannot flap would flap. Both workarounds
are gone and the warning count is back to its long-standing 26.

The next thing this makes possible is the one it was for: an output keyspace, at
two hundred million entries, which no arrangement of the log could have held.

## Amended, 23 August 2026

"Keep all three backends" held for as long as its reasons did. They ran out:
every directory this project keeps had crossed to the database, the database
arm had since done every command over two full chains including the downloads
it had not yet done above, and a text log cannot hold the binary keys the
Index is moving to (#42). [#44](https://github.com/N1bor/btc-listener/issues/44)
retired the Logged backend, `Infra.StoreLog` and `migrate` together; a
directory holding only an `index.log` is refused by name rather than read,
and is rebuilt from the network. Memory stays, for exactly the reason given
above. The title's "keep the log" is the part this amendment ends.

**Superseded in its choice of engine by [ADR 0009](0009-rocksdb-under-the-index.md)**
on 23 August 2026: the Index stays a key-value database behind `Infra.Kv`, but
`rusty-leveldb` gave way to RocksDB.
