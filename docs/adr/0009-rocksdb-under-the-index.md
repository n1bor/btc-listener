# Move the Index to RocksDB

Amends [ADR 0006](0006-a-leveldb-under-the-index.md), which put the Index on a
LevelDB and chose `rusty-leveldb` — a pure-Rust port — to supply it, "because
it needs no C++ toolchain to build" and with the choice marked provisional.
The provision is now called.

## What forced it

Two defects in the port's own code, neither in LevelDB's design, each of
which cost a real run:

- **Its table cache panicked on eviction**
  ([#33](https://github.com/n1bor/btc-listener/issues/33)). An intrusive LRU
  list built on raw pointers, whose `remove` never updated the tail pointer;
  once the database held more tables than `max_open_files`, roughly one audit
  in three died in `TableCache::get_table`. The mitigation was a cache sized
  past the table count, which is a cache that never evicts.
- **It never called `fsync`**
  ([#92](https://github.com/n1bor/btc-listener/issues/92)). There is no
  `sync_all` anywhere in the crate. A hard crash on 23 August 2026 lost 36
  live tables out from under the MANIFEST and left ten more at zero length;
  the Index had to be rebuilt from the Segments.

And one property that was merely expensive: compaction on the writer's thread,
which is where `txindex` (5.9 h) and `outputs` (about 7 h) over 400,000 Blocks
spent their time, against Segments that `reindex` walks in 73 s.

## Decided

**The Index moves to RocksDB**, through the `rocksdb` crate that binds the C++
engine. The `Infra.Kv` contract does not change — the same six operations, the
same Oracle dimensions — and `providers/kv/src/lib.rs` is the whole change.

**Every batch is durable.** `putAll` and `deleteAll` write with `sync = true`
and return only once the write-ahead log is on the disk. One batch is one
fsync, which is the cost the callers' batching already pays for.

**Tuned for this program's shape**, and no more: LZ4 on every level, a 128 MB
write buffer, compaction on up to eight background threads, a 10-bit bloom
filter over a 256 MB block cache. The `o:` read on every spend is an exact-key
lookup and the bloom filter is what makes a miss free.

**No migration.** The on-disk format changes and nothing reads the old one.
`headers` + `reindex` + `txindex` + `outputs` rebuild any directory — the
procedure [#93](https://github.com/n1bor/btc-listener/issues/93) made cheap
and [#92](https://github.com/n1bor/btc-listener/issues/92) already forced.

## Why not Google's LevelDB

ADR 0006 leaned on LevelDB being what Bitcoin Core runs. That argument is
load-bearing for the curve, where an edge case is a consensus rule, and
weightless for a key-value store holding derived data — which `reindex` made
literal: the entire Index is rebuilt from the Segments in minutes. The `leveldb`
Rust binding to Google's library is also effectively unmaintained. The
`rocksdb` crate is maintained, and the engine under it is the one that has
been hammered hardest.

## What it costs

`librocksdb-sys` compiles RocksDB's C++ from source: ten to fifteen minutes
cold, cached by `rust-cache` afterwards, and it needs libclang for bindgen.
The provider-test, verify and compile jobs each pay the cold build once per
cache key. A developer machine needs `clang` and `libclang-dev`.

## What it closes

[#33](https://github.com/n1bor/btc-listener/issues/33) and
[#92](https://github.com/n1bor/btc-listener/issues/92) outright;
[#96](https://github.com/n1bor/btc-listener/issues/96) is this decision. The
compression half of [#42](https://github.com/n1bor/btc-listener/issues/42) —
"LevelDB compression is off" — stops being a thing to fix by hand.
