//! What the `o:` keyspace actually costs, as text and as bytes.
//!
//! n1bor/btc-listener#42 estimated that moving the Index off text would halve
//! 136 GB. That arithmetic was done against LevelDB with compression off,
//! which is what the issue says it was measuring. The RocksDB move
//! (n1bor/btc-listener#96) turned LZ4 on, and hex text is two characters per
//! byte drawn from a sixteen-symbol alphabet — precisely what a compressor
//! eats. So the estimate is stale in the direction that matters, and #42 asks
//! for a measurement rather than a trusted sum.
//!
//! This writes the same Outputs both ways into two databases tuned exactly as
//! `providers/kv` tunes the real one, compacts them, and reports what each
//! costs on disk.
//!
//! Run with `cargo run --release -- [count]`.

use rocksdb::{BlockBasedOptions, Cache, DBCompressionType, Options, WriteBatch, WriteOptions, DB};

/// The same tuning `providers/kv/src/lib.rs` uses, so the answer is about the
/// encoding and not about the settings.
fn tuned() -> Options {
    let mut options = Options::default();
    options.create_if_missing(true);
    options.set_compression_type(DBCompressionType::Lz4);
    options.set_write_buffer_size(128 << 20);
    options.set_max_write_buffer_number(4);
    let mut table = BlockBasedOptions::default();
    table.set_bloom_filter(10.0, false);
    table.set_block_cache(&Cache::new_lru_cache(256 << 20));
    options.set_block_based_table_factory(&table);
    options
}

/// One representative P2PKH Output: a 32-byte Transaction Id, a position, an
/// amount, and a 25-byte script. The Transaction Ids are sequential rather
/// than random, which flatters both encodings equally — real ones are digests
/// and share no prefix, so this is the optimistic end for text.
fn sample(n: u64) -> ([u8; 32], u32, u64, [u8; 25]) {
    let mut txid = [0u8; 32];
    txid[..8].copy_from_slice(&n.to_be_bytes());
    txid[8..16].copy_from_slice(&(n.wrapping_mul(0x9E3779B97F4A7C15)).to_be_bytes());
    txid[16..24].copy_from_slice(&(n.wrapping_mul(0xC2B2AE3D27D4EB4F)).to_be_bytes());
    txid[24..].copy_from_slice(&(n.wrapping_mul(0x165667B19E3779F9)).to_be_bytes());
    let mut script = [0u8; 25];
    script[0] = 0x76;
    script[1] = 0xa9;
    script[2] = 0x14;
    script[23] = 0x88;
    script[24] = 0xac;
    script[3..23].copy_from_slice(&txid[..20]);
    (txid, (n % 8) as u32, 1_000_000 + n, script)
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn sized(dir: &std::path::Path) -> u64 {
    let mut total = 0;
    for entry in std::fs::read_dir(dir).expect("read the database directory") {
        let entry = entry.expect("a directory entry");
        if entry.file_type().expect("a file type").is_file() {
            total += entry.metadata().expect("file metadata").len();
        }
    }
    total
}

fn built(dir: &std::path::Path, count: u64, as_text: bool) -> u64 {
    let db = DB::open(&tuned(), dir).expect("open");
    let mut durable = WriteOptions::default();
    durable.set_sync(false);
    let mut batch = WriteBatch::default();
    for n in 0..count {
        let (txid, index, value, script) = sample(n);
        if as_text {
            let key = format!("o:{}:{:05}", hex(&txid), index);
            let entry = format!("{}:{}", value, hex(&script));
            batch.put(key.as_bytes(), entry.as_bytes());
        } else {
            let mut key = Vec::with_capacity(37);
            key.push(b'o');
            key.extend_from_slice(&txid);
            key.extend_from_slice(&index.to_be_bytes());
            let mut entry = Vec::with_capacity(33);
            entry.extend_from_slice(&value.to_be_bytes());
            entry.extend_from_slice(&script);
            batch.put(&key, &entry);
        }
        if n % 100_000 == 99_999 {
            db.write_opt(std::mem::take(&mut batch), &durable).expect("write");
        }
    }
    db.write_opt(batch, &durable).expect("write");
    db.flush().expect("flush");
    db.compact_range(None::<&[u8]>, None::<&[u8]>);
    drop(db);
    sized(dir)
}

fn main() {
    let count: u64 = std::env::args()
        .nth(1)
        .and_then(|a| a.parse().ok())
        .unwrap_or(1_000_000);

    let text_dir = tempfile::tempdir().expect("a temporary directory");
    let byte_dir = tempfile::tempdir().expect("a temporary directory");

    let text = built(text_dir.path(), count, true);
    let bytes = built(byte_dir.path(), count, false);

    println!("{count} Outputs, LZ4 on, tuned as providers/kv tunes it");
    println!("  text  {text:>12} bytes  ({:.1} per Output)", text as f64 / count as f64);
    println!("  bytes {bytes:>12} bytes  ({:.1} per Output)", bytes as f64 / count as f64);
    let saved = text as i64 - bytes as i64;
    println!(
        "  saved {saved:>12} bytes  ({:.1}%)",
        100.0 * saved as f64 / text as f64
    );
}
