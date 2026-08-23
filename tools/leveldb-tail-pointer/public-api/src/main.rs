//! Crashes rusty-leveldb 4.0.1 through its public API, using only `put` and
//! `get` on an in-memory database.
//!
//! `LRUList::remove` unlinks a node without updating the list's tail pointer,
//! so removing the least recently used entry leaves that pointer addressing
//! freed memory. `TableCache::evict` reaches `Cache::remove`, and
//! `delete_obsolete_files` calls `evict` after every compaction, so ordinary
//! write traffic gets there on its own once the table cache is full.
//!
//! The settings below are small so that happens in seconds rather than hours.
//! Nothing here depends on the sizes: they only raise the table count and the
//! compaction rate.

use rusty_leveldb::{in_memory, Options, DB};
use std::io::{stdout, Write};

fn key_for(n: usize) -> Vec<u8> {
    format!("{:08}", n).into_bytes()
}

/// A cheap deterministic shuffle, so keys arrive out of order and the tables
/// written have overlapping ranges. Overlapping ranges make each compaction
/// take many input files, and so delete many obsolete ones.
fn scrambled(n: usize, total: usize) -> usize {
    n.wrapping_mul(2_654_435_761) % total
}

fn main() {
    let mut options: Options = in_memory();
    options.write_buffer_size = 2 * 1024;
    options.max_file_size = 2 * 1024;
    // The table cache holds max_open_files - 10 entries.
    options.max_open_files = 20;

    let mut db = DB::open("db", options).unwrap();

    let total = 50_000usize;
    let value = vec![b'v'; 64];

    for n in 0..total {
        db.put(&key_for(scrambled(n, total)), &value).unwrap();

        if n % 8 == 0 {
            let _ = db.get(&key_for(scrambled(n / 2, total)));
        }

        if n % 5_000 == 0 {
            println!("wrote {n}");
            stdout().flush().unwrap();
        }
    }

    db.flush().unwrap();
    println!("finished {total} writes without crashing");
}
