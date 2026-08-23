# `LRUList::remove` leaves the tail pointer addressing a freed node

A reproducer for a memory-safety bug in [rusty-leveldb] 4.0.1, the current
release. Everything in this directory is generic: it uses the crate's own
public API and its own test helpers, and nothing from the project it was found
in.

[rusty-leveldb]: https://github.com/dermesser/leveldb-rs

## The bug

`Cache<T>` keeps its LRU ordering in `LRUList<T>`, an intrusive doubly linked
list of `Box`ed nodes. `head.next` is the front; `head.prev` is a raw pointer
to the tail.

`LRUList::remove` unlinks a node by handle and drops it. It fixes up the
neighbours' pointers, but when the node it removes *is* the tail it never
updates `head.prev`:

```rust
// src/cache.rs
fn remove(&mut self, node_handle: LRUHandle<T>) -> T {
    unsafe {
        let d = (*node_handle.0).data.take().unwrap();
        let mut current = (*(*node_handle.0).prev.unwrap()).next.take().unwrap();
        let prev = current.prev.unwrap();
        if current.next.is_some() {
            current.next.as_mut().unwrap().prev = current.prev.take();
        }
        //  ^ no else: if there is no next node, this was the tail, and
        //    self.head.prev still points at `current` -- which is dropped
        //    when this function returns.
        (*prev).next = current.next.take();
        self.count -= 1;
        d
    }
}
```

`head.prev` now addresses freed memory. Nothing reads it until the cache next
evicts, because eviction is the only operation that starts from the tail:

```rust
pub fn insert(&mut self, key: &CacheKey, elem: T) {
    if self.list.count() >= self.cap {
        if let Some(removed_key) = self.list.remove_last() { ... }
```

and `remove_last` dereferences it twice:

```rust
let mut lasto = unsafe { (*((*self.head.prev.unwrap()).prev.unwrap())).next.take() };
```

From there it is ordinary heap corruption. If the freed node's memory has been
reused, the first `.prev` read yields a plausible-looking pointer and the
`.next.take()` unlinks *the wrong node* — often the front one, which owns the
rest of the chain, so the whole list is dropped while its handles are still
live in `Cache::map`. Later `Cache::get` calls then reach freed nodes through
those handles, and `reinsert_front` panics on `(*node_handle.0).prev.unwrap()`.

So a single `Cache::remove` of the least recently used entry can take out the
entire cache, and the crash surfaces arbitrarily far away from it.

## Reachable from the public API

`TableCache::evict` is a plain `Cache::remove`:

```rust
pub fn evict(&mut self, file_num: FileNum) -> Result<()> {
    if self.cache.remove(&filenum_to_key(file_num)).is_some() { ... }
```

and `DB::delete_obsolete_files` calls it for every table file it deletes.
That runs on open and after every compaction, so nothing beyond `put` and
`get` is needed to get there. The requirement is only that the table being
deleted is the least recently used one still in the cache, which needs the
cache to be at capacity — that is, more tables than `max_open_files - 10`.

## Running it

`cache_tests.rs` holds three tests to paste into the `mod tests` block at the
bottom of `src/cache.rs`; they use the `make_key` helper already defined
there. Against 4.0.1:

| test | result |
| --- | --- |
| `repro_lru_remove_tail_leaves_stale_tail_pointer` | panics, `cache.rs:72`, `unwrap()` on a `None` value |
| `repro_cache_remove_lru_then_evict` | fails: the entry inserted *second to last* is the one evicted |
| `repro_cache_get_after_corruption` | SIGSEGV |

`public-api/` is a standalone crate depending only on `rusty-leveldb = "4.0"`
that reaches the same place through `DB::put`/`DB::get` on an in-memory
database. It sets `max_open_files = 20` and small table sizes purely so the
table cache fills in seconds; the sizes are not otherwise load-bearing.

```
$ cargo run --release
wrote 0
free(): invalid pointer          # or "free(): invalid size", or SIGSEGV
```

Three runs of its 50,000-write workload, on the crate as published: killed
every time, twice by SIGSEGV and once by a glibc heap assertion, all within a
tenth of a second.

## The fix

`fix.patch` adds the missing `else`. It applies cleanly to 4.0.1:

```rust
} else {
    // This was the last node, so the node before it is the new
    // tail. Without this the tail pointer keeps addressing the
    // node that is about to be dropped.
    self.head.prev = current.prev.take();
}
```

Removing the only node leaves `head.prev` pointing at `head` itself, which is
the state `remove_last` already leaves behind when it empties the list, and
`insert` resets it on the next insertion.

With the patch applied, the same three runs each finish all 50,000 writes
without crashing, and the crate's own 146 tests plus the three reproducers
above all pass (149 passed, 0 failed).

## A separate finding: `LRUHandle` is unsound under Miri

Worth reporting but *not* what the above is about. Miri rejects
`cache::tests::test_blockcache_lru_remove` — an existing test, on the
unmodified crate — under both Stacked Borrows and Tree Borrows. `insert` takes
a raw pointer out of a `Box` and then moves the `Box` into `head.next`, which
invalidates the pointer:

```rust
let newp = new.as_mut() as *mut LRUNode<T>;
...
self.head.next = Some(new);   // invalidates newp
```

Every later use of an `LRUHandle` is therefore UB by the aliasing rules, even
in runs that behave correctly. Because Miri fails on the crate's own tests it
cannot be used as evidence for the tail-pointer bug; the failures listed above
are all deterministic without it. Fixing this properly probably means holding
the nodes in something like `NonNull`/raw allocations rather than `Box`, or
using an index-based list.
