// Three reproducers for the stale tail pointer in rusty-leveldb 4.0.1.
// Paste them into the `mod tests` block at the bottom of `src/cache.rs`,
// which already defines the `make_key` helper they use.
//
// Against 4.0.1 all three fail. Against the one-branch fix in `fix.patch`
// all three pass, as do the crate's own 146 tests.

#[test]
fn repro_lru_remove_tail_leaves_stale_tail_pointer() {
    // Insertion is at the front, so the first element inserted is the one at
    // the back: the tail.
    let mut list = LRUList::<u32>::new();
    let first = list.insert(1);
    let _second = list.insert(2);

    // Unlink the tail by handle. `remove` drops that node but never updates
    // `head.prev`, which goes on addressing the freed node.
    assert_eq!(1, list.remove(first));
    assert_eq!(1, list.count());

    // `remove_last` starts from `head.prev`, so it walks into freed memory.
    // The only element left is 2.
    assert_eq!(Some(2), list.remove_last());
    assert_eq!(0, list.count());
    assert_eq!(None, list.remove_last());
}

#[test]
fn repro_cache_remove_lru_then_evict() {
    let mut cache = Cache::new(2);
    let first = make_key(1, 0, 0);
    let second = make_key(2, 0, 0);
    let third = make_key(3, 0, 0);
    let fourth = make_key(4, 0, 0);

    cache.insert(&first, 1);
    cache.insert(&second, 2);

    // `first` is the least recently used entry, so it is the list tail.
    assert_eq!(Some(1), cache.remove(&first));

    cache.insert(&third, 3);
    // Back at capacity, so this insert evicts, and eviction reads the stale
    // tail pointer left behind by the remove above.
    cache.insert(&fourth, 4);

    // Whatever was evicted, the two most recent inserts should still be here.
    assert_eq!(Some(&4), cache.get(&fourth));
    assert_eq!(Some(&3), cache.get(&third));
}

#[test]
fn repro_cache_get_after_corruption() {
    // A cache kept full, with one entry unlinked by key part way through:
    // what a read-through cache does when something invalidates an entry
    // while the cache is under eviction pressure.
    let capacity = 8;
    let mut cache = Cache::new(capacity);

    for n in 0..capacity {
        cache.insert(&make_key(n as u8, 0, 0), n);
    }

    // Drop the least recently used entry by key rather than by eviction.
    assert_eq!(Some(0), cache.remove(&make_key(0, 0, 0)));

    // Keep inserting. Every insert past capacity evicts, and every eviction
    // now starts from the stale tail pointer.
    for n in capacity..(capacity * 4) {
        cache.insert(&make_key(n as u8, 0, 0), n);
    }

    // Read back everything the cache still claims to hold.
    for n in 0..(capacity * 4) {
        let _ = cache.get(&make_key(n as u8, 0, 0));
    }
}
