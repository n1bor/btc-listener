//! A RocksDB behind the `Infra.Kv` capability contract.
//!
//! The database is not written here either. It comes from the `rocksdb`
//! crate, which binds the C++ engine. It replaced `rusty-leveldb`, a pure-Rust
//! port chosen originally because it needed no C++ toolchain; two defects in
//! the port's own code ended that — its table cache panicked on eviction
//! (n1bor/btc-listener#33) and it never called `fsync`, so a power loss took
//! the Index with it (n1bor/btc-listener#92). The swap changed this file and
//! nothing in Aver: the contract is the same six operations (#96).
//!
//! How much of the Index stays in memory is a parameter, not a constant:
//! `BTC_LISTENER_KV_CACHE_MB` names the block cache in megabytes. The size
//! that matters is the size of the Index being walked, which a mainnet node
//! grows past nine gigabytes, and a deployment cannot be asked to rebuild to
//! change it. n1bor/btc-listener#308.
//!
//! Durability is the point, so every batch is written with `sync = true`:
//! `putAll` and `deleteAll` return only once the write-ahead log is on the
//! disk. One batch is one fsync, which is why the callers batch.
//!
//! An open database is a capability resource. `open` hands back a
//! `ProviderResource` holding it, and every other operation takes one back.
//! Aver sees an opaque `Handle` it cannot construct, name, or serialise.

use std::sync::Arc;

use aver_rt::provider::{
    CapabilityProvider, ProviderBinding, ProviderContext, ProviderFault, ProviderResource,
    ProviderValue,
};
use rocksdb::{
    BlockBasedOptions, Cache, DBCompactionStyle, DBCompressionType, Direction, IteratorMode,
    Options, WriteBatch, WriteOptions, DB,
};

/// Pinned to the contract in `infra/kv.av`. A mismatch fails at startup rather
/// than at the first call — which is how the move to `Bytes` announced itself:
/// the contract changed shape, so its hash changed, and every verify block
/// that reaches a Store declined to run until this line agreed again.
/// n1bor/btc-listener#43.
pub const CONTRACT_HASH: &str =
    "sha256:82f1f0a28f3a09ebb5c6406c3ba87a131051934e0a953b7434e14f10d797e5f9";

/// The open database. RocksDB takes `&self` for every operation and is
/// `Sync`, so the resource needs no lock of its own.
struct Open(DB);

/// How much block cache the Index is given when nothing says otherwise, in
/// mebibytes.
///
/// A parameter and not a constant because the right size is the size of the
/// Index being walked, and that spans four orders of magnitude: a regtest
/// Index is a few megabytes and a mainnet one is nine gigabytes and still
/// growing. n1bor/btc-listener#308.
///
/// The default is what it has always been. #308 raised it to a gigabyte on the
/// reasoning that a nine-gigabyte Index deserved more, and the measurement did
/// not support it: on the mainnet node the cost of a UTXO operation swings
/// from 57 to 167 microseconds between neighbouring thousand-Height buckets on
/// one unchanged setting, which is wider than anything 6144 MB moved, and
/// reverting did not give back the trough it was compared against. What a
/// larger cache does cost is measurable and immediate -- 7.24 GB of RSS
/// against 1.1, and three gigabytes off the page cache on a machine with
/// fifteen. So the number here claims only what has been shown, and a
/// deployment that wants more says so. n1bor/btc-listener#313.
///
/// RocksDB's LRU cache is a ceiling rather than an allocation, so a regtest
/// run that touches ten megabytes holds ten whatever this says.
const CACHE_MB_DEFAULT: usize = 256;

/// Where a deployment says how big the block cache should be.
///
/// An environment variable rather than a CLI flag or `aver.toml`: this is
/// deployment policy, like the disk the node runs on, and no Aver code should
/// have to name a number that belongs to the machine. It is read at `open`,
/// so it costs a restart and not a rebuild.
const CACHE_MB_VAR: &str = "BTC_LISTENER_KV_CACHE_MB";

/// The block cache size to open with, in bytes.
///
/// Unset is the default. Set is honoured or refused -- never quietly replaced
/// by the default, because a deployment that names a size and is given another
/// has been told nothing and gets the slow Index it was trying to avoid.
fn cache_bytes() -> Result<usize, String> {
    match std::env::var(CACHE_MB_VAR) {
        Err(std::env::VarError::NotPresent) => Ok(CACHE_MB_DEFAULT << 20),
        Err(std::env::VarError::NotUnicode(_)) => Err(format!("{CACHE_MB_VAR} is not text")),
        Ok(raw) => parsed_cache_mb(&raw),
    }
}

/// What one setting of the variable means, in bytes.
fn parsed_cache_mb(raw: &str) -> Result<usize, String> {
    let said = raw.trim();
    match said.parse::<usize>() {
        Ok(mb) if mb > 0 => mb
            .checked_mul(1 << 20)
            .ok_or_else(|| format!("{CACHE_MB_VAR} is '{said}', which is more megabytes than this machine can address")),
        _ => Err(format!("{CACHE_MB_VAR} must be a whole number of megabytes above zero, not '{said}'")),
    }
}

/// Which compaction style to open with. Deployment policy like the cache
/// above, and for the same reason: which one wins depends on the disk under
/// the Index.
///
/// Leveled keeps each level a single non-overlapping sorted run, and holds
/// that invariant by rewriting the whole overlapping part of the level below
/// -- which on this project's mainnet node meant writing 101 GB into a 12 GB
/// bottom level to add 1.9 GB. Universal drops the invariant and merges runs
/// of similar size instead, trading space and read amplification for far
/// fewer rewrites. Most of this Index is append-only (`b:`, `t:`, `n:`, `k:`
/// and `o:` never change), so the rewriting leveled does to reclaim obsolete
/// versions buys nothing for those keyspaces, which is the argument for
/// trying universal at all.
///
/// It was tried, and on that node it lost badly: an hour of universal ran at
/// 14.5 Blocks a minute against 36.4 for the leveled hour before it. The
/// writes did fall exactly as the argument said -- write amplification 7.3 to
/// 4.9, compaction traffic 3.0 MB/s to 1.0 -- and the reads paid for it,
/// going from 1.44 ms to 10.26 ms a piece with the disks busier than before.
/// A UTXO lookup probes every sorted run, and there were more of them than a
/// 256 MB cache could keep. That is a fact about two 7,200 rpm spindles and a
/// small cache rather than about universal compaction, which is why this is a
/// setting and not a decision: on an SSD, or with a cache that holds the
/// Index, the trade could go the other way. Leveled is the default because it
/// is what every measurement in this repo was taken against.
const COMPACTION_VAR: &str = "BTC_LISTENER_KV_COMPACTION";

/// Leveled unless the deployment says otherwise: it is what every measurement
/// in this repo was taken against, and a default that changes under a running
/// node is a default that cannot be reasoned about.
fn compaction_style() -> Result<DBCompactionStyle, String> {
    match std::env::var(COMPACTION_VAR) {
        Err(std::env::VarError::NotPresent) => Ok(DBCompactionStyle::Level),
        Err(std::env::VarError::NotUnicode(_)) => Err(format!("{COMPACTION_VAR} is not text")),
        Ok(raw) => parsed_compaction(&raw),
    }
}

/// What one setting of the variable means. Refused rather than guessed: a
/// deployment that names a style and is given another has been told nothing.
fn parsed_compaction(raw: &str) -> Result<DBCompactionStyle, String> {
    match raw.trim() {
        "leveled" | "level" => Ok(DBCompactionStyle::Level),
        "universal" => Ok(DBCompactionStyle::Universal),
        said => Err(format!(
            "{COMPACTION_VAR} must be 'leveled' or 'universal', not '{said}'"
        )),
    }
}

/// How the database is tuned for this program's shape: a few hundred million
/// small keys written in large batches, read one at a time by exact key or
/// by prefix, and compacted in the background while the writer keeps going.
fn tuned(cache_bytes: usize, style: DBCompactionStyle) -> Options {
    let mut options = Options::default();
    options.create_if_missing(true);
    // Block Ids and scripts are hex text today and bytes after #45/#46;
    // either compresses. LZ4 is the fast one, and the Index is write-bound.
    options.set_compression_type(DBCompressionType::Lz4);
    // Compaction on its own threads is what rusty-leveldb did not have, and
    // why outputs took seven hours: the writer waited on its own compactions.
    options.increase_parallelism(parallelism());
    options.set_max_background_jobs(parallelism());
    // A larger memtable means fewer, larger flushes for the same batches.
    options.set_write_buffer_size(128 << 20);
    options.set_max_write_buffer_number(4);
    let mut table = BlockBasedOptions::default();
    // Every spend resolves by one exact-key read of the o: keyspace; a bloom
    // filter answers the misses without touching a table.
    table.set_bloom_filter(10.0, false);
    // The Index is read one exact key at a time and is far larger than any
    // cache it will be given, so what this holds is the upper levels and the
    // hot blocks of the lower ones; the rest is a seek. #308.
    table.set_block_cache(&Cache::new_lru_cache(cache_bytes));
    options.set_block_based_table_factory(&table);
    // Last, so it is the line a reader looking for the style finds. The
    // level_* settings above apply to leveled only and are inert under
    // universal; they are left in place so switching back needs no edit.
    options.set_compaction_style(style);
    options
}

fn parallelism() -> i32 {
    std::thread::available_parallelism()
        .map(|n| n.get().min(8) as i32)
        .unwrap_or(2)
}

/// Every batch reaches the disk before the call returns.
fn durable() -> WriteOptions {
    let mut write = WriteOptions::default();
    write.set_sync(true);
    write
}

fn open_in<'a>(value: &'a ProviderValue, what: &str) -> Result<&'a Open, ProviderFault> {
    let ProviderValue::Resource(resource) = value else {
        return Err(ProviderFault::new("bad_shape", format!("{what} is not a Handle")));
    };
    resource
        .downcast_ref::<Open>()
        .ok_or_else(|| ProviderFault::new("bad_shape", format!("{what} is not a Kv Handle")))
}

struct Kv;

fn string_in(value: &ProviderValue, what: &str) -> Result<String, ProviderFault> {
    match value {
        ProviderValue::String(s) => Ok(s.clone()),
        _ => Err(ProviderFault::new("bad_shape", format!("{what} is not a String"))),
    }
}

/// The bytes of a `Bytes`. Keys and values cross as bytes because that is what
/// the database stores; the contract used to say `String` and this file had to
/// prove it, which is a claim about the world made where no caller could see it
/// fail. n1bor/btc-listener#43.
fn bytes_in(value: &ProviderValue, what: &str) -> Result<Vec<u8>, ProviderFault> {
    match value {
        ProviderValue::Bytes(bytes) => Ok(bytes.clone()),
        _ => Err(ProviderFault::new("bad_shape", format!("{what} is not Bytes"))),
    }
}

/// A key as a diagnostic, never as data. A key that is not UTF-8 is perfectly
/// legal now, and a message about it still has to be readable.
fn shown(key: &[u8]) -> String {
    String::from_utf8_lossy(key).into_owned()
}

/// The pairs of a `List<Tuple<Bytes, Bytes>>`.
fn pairs_in(value: &ProviderValue, what: &str) -> Result<Vec<(Vec<u8>, Vec<u8>)>, ProviderFault> {
    let ProviderValue::List(items) = value else {
        return Err(ProviderFault::new("bad_shape", format!("{what} is not a List")));
    };
    let mut out = Vec::with_capacity(items.len());
    for item in items {
        let ProviderValue::Tuple(parts) = item else {
            return Err(ProviderFault::new("bad_shape", format!("{what} holds a non-Tuple")));
        };
        let [key, value] = parts.as_slice() else {
            return Err(ProviderFault::new("bad_shape", format!("{what} holds a Tuple that is not a pair")));
        };
        out.push((bytes_in(key, "key")?, bytes_in(value, "value")?));
    }
    Ok(out)
}

fn keys_in(value: &ProviderValue, what: &str) -> Result<Vec<Vec<u8>>, ProviderFault> {
    let ProviderValue::List(items) = value else {
        return Err(ProviderFault::new("bad_shape", format!("{what} is not a List")));
    };
    items.iter().map(|item| bytes_in(item, "key")).collect()
}

/// A database error the caller is meant to handle, as distinct from a
/// `ProviderFault`, which says the provider itself was called wrongly.
fn failed(what: &str, why: impl std::fmt::Display) -> ProviderValue {
    ProviderValue::ResultErr(Box::new(ProviderValue::String(format!("{what}: {why}"))))
}

fn ok(value: ProviderValue) -> ProviderValue {
    ProviderValue::ResultOk(Box::new(value))
}

impl CapabilityProvider for Kv {
    fn identity(&self) -> &str {
        "btc-listener.kv/rocksdb@2"
    }

    fn fingerprint(&self) -> &str {
        concat!("rocksdb 0.25, built ", env!("CARGO_PKG_VERSION"))
    }

    fn invoke(
        &self,
        context: &ProviderContext,
        args: &[ProviderValue],
    ) -> Result<ProviderValue, ProviderFault> {
        match context.operation.as_str() {
            "Infra.Kv.open" => {
                let [dir] = args else {
                    return Err(ProviderFault::new("bad_arity", "open takes one String"));
                };
                let dir = string_in(dir, "dir")?;
                let cache = match cache_bytes() {
                    Ok(bytes) => bytes,
                    Err(why) => return Ok(failed("cannot open the database", why)),
                };
                let style = match compaction_style() {
                    Ok(style) => style,
                    Err(why) => return Ok(failed("cannot open the database", why)),
                };
                Ok(match DB::open(&tuned(cache, style), &dir) {
                    Ok(db) => ok(ProviderValue::Resource(ProviderResource::new(Open(db)))),
                    Err(why) => failed(&format!("cannot open the database at '{dir}'"), why),
                })
            }
            "Infra.Kv.get" => {
                let [handle, key] = args else {
                    return Err(ProviderFault::new("bad_arity", "get takes a Handle and Bytes"));
                };
                let key = bytes_in(key, "key")?;
                let open = open_in(handle, "handle")?;
                Ok(match open.0.get(&key) {
                    Err(why) => failed(&format!("cannot read '{}'", shown(&key)), why),
                    Ok(None) => ok(ProviderValue::OptionNone),
                    Ok(Some(bytes)) => ok(ProviderValue::OptionSome(Box::new(ProviderValue::Bytes(bytes)))),
                })
            }
            "Infra.Kv.getAll" => {
                let [handle, keys] = args else {
                    return Err(ProviderFault::new("bad_arity", "getAll takes a Handle and a List"));
                };
                let keys = keys_in(keys, "keys")?;
                let open = open_in(handle, "handle")?;
                let mut found = Vec::with_capacity(keys.len());
                for (key, answer) in keys.iter().zip(open.0.multi_get(&keys)) {
                    found.push(match answer {
                        Err(why) => return Ok(failed(&format!("cannot read '{}'", shown(key)), why)),
                        Ok(None) => ProviderValue::OptionNone,
                        Ok(Some(bytes)) => ProviderValue::OptionSome(Box::new(ProviderValue::Bytes(bytes))),
                    });
                }
                Ok(ok(ProviderValue::List(found)))
            }
            "Infra.Kv.putAll" => {
                let [handle, entries] = args else {
                    return Err(ProviderFault::new("bad_arity", "putAll takes a Handle and a List"));
                };
                let entries = pairs_in(entries, "entries")?;
                let open = open_in(handle, "handle")?;
                let mut batch = WriteBatch::default();
                for (key, value) in &entries {
                    batch.put(key, value);
                }
                Ok(match open.0.write_opt(batch, &durable()) {
                    Ok(()) => ok(ProviderValue::Unit),
                    Err(why) => failed("cannot write the batch", why),
                })
            }
            "Infra.Kv.applyAll" => {
                let [handle, puts, deletes] = args else {
                    return Err(ProviderFault::new("bad_arity", "applyAll takes a Handle and two Lists"));
                };
                let puts = pairs_in(puts, "puts")?;
                let deletes = keys_in(deletes, "deletes")?;
                let open = open_in(handle, "handle")?;
                let mut batch = WriteBatch::default();
                for (key, value) in &puts {
                    batch.put(key, value);
                }
                for key in &deletes {
                    batch.delete(key);
                }
                Ok(match open.0.write_opt(batch, &durable()) {
                    Ok(()) => ok(ProviderValue::Unit),
                    Err(why) => failed("cannot write the batch", why),
                })
            }
            "Infra.Kv.deleteAll" => {
                let [handle, keys] = args else {
                    return Err(ProviderFault::new("bad_arity", "deleteAll takes a Handle and a List"));
                };
                let keys = keys_in(keys, "keys")?;
                let open = open_in(handle, "handle")?;
                let mut batch = WriteBatch::default();
                for key in &keys {
                    batch.delete(key);
                }
                Ok(match open.0.write_opt(batch, &durable()) {
                    Ok(()) => ok(ProviderValue::Unit),
                    Err(why) => failed("cannot delete the batch", why),
                })
            }
            "Infra.Kv.count" => {
                let [handle] = args else {
                    return Err(ProviderFault::new("bad_arity", "count takes a Handle"));
                };
                let open = open_in(handle, "handle")?;
                let mut held: i64 = 0;
                for item in open.0.iterator(IteratorMode::Start) {
                    if let Err(why) = item {
                        return Ok(failed("cannot walk the keyspace", why));
                    }
                    held += 1;
                }
                Ok(ok(ProviderValue::Int(held.into())))
            }
            "Infra.Kv.prefixed" => {
                let [handle, prefix] = args else {
                    return Err(ProviderFault::new("bad_arity", "prefixed takes a Handle and Bytes"));
                };
                let prefix = bytes_in(prefix, "prefix")?;
                let open = open_in(handle, "handle")?;
                let mut found = Vec::new();
                for item in open
                    .0
                    .iterator(IteratorMode::From(&prefix, Direction::Forward))
                {
                    let (key, value) = match item {
                        Ok(pair) => pair,
                        Err(why) => return Ok(failed("cannot walk the keyspace", why)),
                    };
                    if !key.starts_with(&prefix) {
                        break;
                    }
                    found.push(ProviderValue::Tuple(vec![
                        ProviderValue::Bytes(key.to_vec()),
                        ProviderValue::Bytes(value.to_vec()),
                    ]));
                }
                Ok(ok(ProviderValue::List(found)))
            }
            other => Err(ProviderFault::new("bad_operation", other)),
        }
    }
}

/// The zero-argument factory `aver.toml` names.
pub fn kv_binding() -> ProviderBinding {
    ProviderBinding::new(
        "Infra.Kv",
        CONTRACT_HASH,
        [
            "Infra.Kv.count",
            "Infra.Kv.deleteAll",
            "Infra.Kv.get",
            "Infra.Kv.applyAll",
            "Infra.Kv.getAll",
            "Infra.Kv.open",
            "Infra.Kv.prefixed",
            "Infra.Kv.putAll",
        ],
        Arc::new(Kv),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write as _;
    use std::path::Path;

    fn call(operation: &str, args: &[ProviderValue]) -> ProviderValue {
        Kv.invoke(
            &ProviderContext {
                capability: "Infra.Kv".to_string(),
                operation: format!("Infra.Kv.{operation}"),
                contract_hash: CONTRACT_HASH.to_string(),
                model_hash: String::new(),
            },
            args,
        )
        .expect("the provider faulted")
    }

    /// The Ok side of a `Result`, or a panic naming the Err.
    fn okayed(value: ProviderValue) -> ProviderValue {
        match value {
            ProviderValue::ResultOk(inner) => *inner,
            ProviderValue::ResultErr(why) => panic!("expected Ok, got Err({why:?})"),
            other => panic!("expected a Result, got {other:?}"),
        }
    }

    fn erred(value: ProviderValue) -> String {
        match value {
            ProviderValue::ResultErr(why) => match *why {
                ProviderValue::String(text) => text,
                other => panic!("expected a String in the Err, got {other:?}"),
            },
            other => panic!("expected an Err, got {other:?}"),
        }
    }

    /// Assert a `Result<Unit, String>` came back Ok.
    fn did(value: ProviderValue) {
        match okayed(value) {
            ProviderValue::Unit => {}
            other => panic!("expected Unit, got {other:?}"),
        }
    }

    /// A directory, which is still a `String` — only keys and values became
    /// `Bytes`.
    fn text(value: &str) -> ProviderValue {
        ProviderValue::String(value.to_string())
    }

    /// A key or a value, as the contract now carries them.
    fn raw(value: &str) -> ProviderValue {
        ProviderValue::Bytes(value.as_bytes().to_vec())
    }

    /// A key or a value that is deliberately not UTF-8, which the database is
    /// now entitled to hold and this file no longer has an opinion about.
    fn nonUtf8() -> ProviderValue {
        ProviderValue::Bytes(vec![0xff, 0xfe, 0x00, 0x80])
    }

    fn opened(dir: &Path) -> ProviderValue {
        okayed(call("open", &[text(&dir.to_string_lossy())]))
    }

    fn put(handle: &ProviderValue, pairs: &[(&str, &str)]) {
        let entries = ProviderValue::List(
            pairs
                .iter()
                .map(|(key, value)| ProviderValue::Tuple(vec![raw(key), raw(value)]))
                .collect(),
        );
        did(call("putAll", &[handle.clone(), entries]));
    }

    fn got(handle: &ProviderValue, key: &str) -> Option<String> {
        match okayed(call("get", &[handle.clone(), raw(key)])) {
            ProviderValue::OptionNone => None,
            ProviderValue::OptionSome(inner) => match *inner {
                ProviderValue::Bytes(value) => {
                    Some(String::from_utf8(value).expect("this test stores text"))
                }
                other => panic!("expected Bytes, got {other:?}"),
            },
            other => panic!("expected an Option, got {other:?}"),
        }
    }

    /// Every key at once, in the order asked, None where absent.
    fn gotAll(handle: &ProviderValue, keys: &[&str]) -> Vec<Option<String>> {
        let asked = ProviderValue::List(keys.iter().map(|k| raw(k)).collect());
        match okayed(call("getAll", &[handle.clone(), asked])) {
            ProviderValue::List(items) => items
                .into_iter()
                .map(|item| match item {
                    ProviderValue::OptionNone => None,
                    ProviderValue::OptionSome(inner) => match *inner {
                        ProviderValue::Bytes(value) => Some(String::from_utf8(value).expect("this test stores text")),
                        other => panic!("expected Bytes, got {other:?}"),
                    },
                    other => panic!("expected an Option, got {other:?}"),
                })
                .collect(),
            other => panic!("expected a List, got {other:?}"),
        }
    }

    fn counted(handle: &ProviderValue) -> i64 {
        match okayed(call("count", &[handle.clone()])) {
            ProviderValue::Int(n) => n.to_i64().expect("count does not fit in an i64"),
            other => panic!("expected an Int, got {other:?}"),
        }
    }

    fn under(handle: &ProviderValue, prefix: &str) -> Vec<(String, String)> {
        match okayed(call("prefixed", &[handle.clone(), raw(prefix)])) {
            ProviderValue::List(items) => items
                .into_iter()
                .map(|item| match item {
                    ProviderValue::Tuple(parts) => match parts.as_slice() {
                        [ProviderValue::Bytes(key), ProviderValue::Bytes(value)] => (
                            String::from_utf8(key.clone()).expect("this test stores text"),
                            String::from_utf8(value.clone()).expect("this test stores text"),
                        ),
                        other => panic!("expected a pair of Bytes, got {other:?}"),
                    },
                    other => panic!("expected a Tuple, got {other:?}"),
                })
                .collect(),
            other => panic!("expected a List, got {other:?}"),
        }
    }

    #[test]
    fn a_value_written_comes_back() {
        let dir = tempfile::tempdir().unwrap();
        let handle = opened(dir.path());
        put(&handle, &[("b:aa", "7:1234"), ("h:1", "aa")]);
        assert_eq!(got(&handle, "b:aa"), Some("7:1234".to_string()));
        assert_eq!(got(&handle, "h:1"), Some("aa".to_string()));
        assert_eq!(got(&handle, "h:2"), None);
    }

    #[test]
    fn a_later_write_wins() {
        let dir = tempfile::tempdir().unwrap();
        let handle = opened(dir.path());
        put(&handle, &[("b:aa", "first")]);
        put(&handle, &[("b:aa", "second")]);
        assert_eq!(got(&handle, "b:aa"), Some("second".to_string()));
        // Twice in one batch too: the later pair is the one that survives.
        put(&handle, &[("b:bb", "first"), ("b:bb", "second")]);
        assert_eq!(got(&handle, "b:bb"), Some("second".to_string()));
        assert_eq!(counted(&handle), 2);
    }

    #[test]
    fn a_delete_removes_and_deleting_what_is_absent_is_not_an_error() {
        let dir = tempfile::tempdir().unwrap();
        let handle = opened(dir.path());
        put(&handle, &[("b:aa", "1"), ("b:bb", "2")]);
        let keys = ProviderValue::List(vec![raw("b:aa"), raw("b:zz")]);
        did(call("deleteAll", &[handle.clone(), keys]));
        assert_eq!(got(&handle, "b:aa"), None);
        assert_eq!(got(&handle, "b:bb"), Some("2".to_string()));
        assert_eq!(counted(&handle), 1);
    }

    #[test]
    fn a_reopen_sees_what_was_written_before() {
        let dir = tempfile::tempdir().unwrap();
        {
            let handle = opened(dir.path());
            put(&handle, &[("b:aa", "1"), ("b:bb", "2"), ("h:0", "aa")]);
        }
        let handle = opened(dir.path());
        assert_eq!(got(&handle, "b:aa"), Some("1".to_string()));
        assert_eq!(got(&handle, "h:0"), Some("aa".to_string()));
        assert_eq!(counted(&handle), 3);
    }

    /// A batch reaches the disk whole or not at all.
    ///
    /// This is the property the append-only log could only approximate. It is
    /// shown the way it actually matters: write a batch, close, cut the tail
    /// of the write-ahead log so the record is torn, and reopen. RocksDB
    /// checksums each record, so a torn one is discarded — and because the
    /// whole batch is one record, what is discarded is the whole batch. A
    /// store that wrote entry-per-record would come back holding a prefix.
    ///
    /// Measured rather than assumed: with the tail cut, none of the four keys
    /// come back and the batch written before them still does.
    #[test]
    fn a_torn_batch_is_discarded_whole() {
        let dir = tempfile::tempdir().unwrap();
        {
            let handle = opened(dir.path());
            put(&handle, &[("first", "1")]);
            }
        {
            let handle = opened(dir.path());
            put(&handle, &[("a", "1"), ("b", "2"), ("c", "3"), ("d", "4")]);
            }
        let log = std::fs::read_dir(dir.path())
            .unwrap()
            .filter_map(|entry| entry.ok().map(|entry| entry.path()))
            .filter(|path| path.extension().is_some_and(|kind| kind == "log"))
            .max_by_key(|path| std::fs::metadata(path).unwrap().len())
            .expect("no write-ahead log to tear");
        let whole = std::fs::metadata(&log).unwrap().len();
        assert!(whole > 8, "the log is too short to tear meaningfully");
        std::fs::OpenOptions::new()
            .write(true)
            .open(&log)
            .unwrap()
            .set_len(whole - 4)
            .unwrap();

        let handle = opened(dir.path());
        let survivors: Vec<&str> = ["a", "b", "c", "d"]
            .into_iter()
            .filter(|key| got(&handle, key).is_some())
            .collect();
        assert_eq!(
            survivors,
            Vec::<&str>::new(),
            "the torn batch came back in part rather than not at all"
        );
        assert_eq!(
            got(&handle, "first"),
            Some("1".to_string()),
            "the intact batch before it should have survived"
        );
    }

    #[test]
    fn a_prefix_read_comes_back_in_key_order_and_stops_at_the_prefix() {
        let dir = tempfile::tempdir().unwrap();
        let handle = opened(dir.path());
        put(
            &handle,
            &[
                ("o:cc:1", "third"),
                ("o:aa:0", "first"),
                ("t:zz", "elsewhere"),
                ("o:bb:0", "second"),
                ("b:aa", "before"),
            ],
        );
        assert_eq!(
            under(&handle, "o:"),
            vec![
                ("o:aa:0".to_string(), "first".to_string()),
                ("o:bb:0".to_string(), "second".to_string()),
                ("o:cc:1".to_string(), "third".to_string()),
            ]
        );
        assert_eq!(
            under(&handle, "o:bb:"),
            vec![("o:bb:0".to_string(), "second".to_string())]
        );
        assert_eq!(under(&handle, "z"), vec![]);
    }

    /// Fifty overwrites and a delete leave exactly what survived them.
    #[test]
    fn overwrites_and_deletes_leave_only_what_survived() {
        let dir = tempfile::tempdir().unwrap();
        let handle = opened(dir.path());
        for round in 0..50 {
            put(&handle, &[("b:aa", &format!("round {round}"))]);
        }
        let keys = ProviderValue::List(vec![raw("b:aa")]);
        did(call("deleteAll", &[handle.clone(), keys]));
        put(&handle, &[("b:bb", "kept")]);
        assert_eq!(got(&handle, "b:aa"), None);
        assert_eq!(got(&handle, "b:bb"), Some("kept".to_string()));
        assert_eq!(counted(&handle), 1);
    }

    /// Keys and values cross as `Bytes`, so bytes that are not UTF-8 are
    /// ordinary data and come back unchanged. This file used to refuse them,
    /// because the contract said `String` and something had to make that true;
    /// deciding what text means is now Infra.Store's, where a caller can see
    /// the answer. n1bor/btc-listener#43.
    #[test]
    fn bytes_that_are_not_utf8_come_back_as_they_went_in() {
        let dir = tempfile::tempdir().unwrap();
        let handle = opened(dir.path());
        let odd = vec![0xffu8, 0xfe, 0x00, 0x80];
        let entries = ProviderValue::List(vec![ProviderValue::Tuple(vec![
            nonUtf8(),
            ProviderValue::Bytes(odd.clone()),
        ])]);
        did(call("putAll", &[handle.clone(), entries]));

        match okayed(call("get", &[handle.clone(), nonUtf8()])) {
            ProviderValue::OptionSome(inner) => match *inner {
                ProviderValue::Bytes(value) => assert_eq!(value, odd),
                other => panic!("expected Bytes, got {other:?}"),
            },
            other => panic!("expected Some, got {other:?}"),
        }

        match okayed(call("prefixed", &[handle, ProviderValue::Bytes(vec![0xff])])) {
            ProviderValue::List(items) => match items.as_slice() {
                [ProviderValue::Tuple(parts)] => match parts.as_slice() {
                    [ProviderValue::Bytes(key), ProviderValue::Bytes(value)] => {
                        assert_eq!(key, &vec![0xffu8, 0xfe, 0x00, 0x80]);
                        assert_eq!(value, &odd);
                    }
                    other => panic!("expected a pair of Bytes, got {other:?}"),
                },
                other => panic!("expected one pair, got {other:?}"),
            },
            other => panic!("expected a List, got {other:?}"),
        }
    }

    /// A prefixed scan must return one Transaction's Outputs in Output order.
    ///
    /// This is the property n1bor/btc-listener#45 exists to protect and the
    /// one nothing else can check: the ordering lives in RocksDB's bytewise
    /// key comparison, not in Aver, and `prefixed` has no caller in the
    /// program yet. The index is written big-endian precisely so that
    /// bytewise order is numeric order. Little-endian would return the right
    /// Outputs in the wrong order, silently.
    ///
    /// Fifteen Outputs, because the mistake only shows above ten: with a
    /// single digit of index every ordering agrees.
    #[test]
    fn a_prefixed_scan_returns_outputs_in_index_order() {
        let dir = tempfile::tempdir().unwrap();
        let handle = opened(dir.path());
        let txid = [0xab_u8; 32];

        // Deliberately inserted out of order, so a scan that simply returned
        // insertion order would not pass either.
        let mut entries = Vec::new();
        for index in [7_u32, 0, 14, 3, 11, 1, 9, 2, 13, 4, 10, 5, 12, 6, 8] {
            let mut key = vec![b'o'];
            key.extend_from_slice(&txid);
            key.extend_from_slice(&index.to_be_bytes());
            entries.push(ProviderValue::Tuple(vec![
                ProviderValue::Bytes(key),
                ProviderValue::Bytes(index.to_string().into_bytes()),
            ]));
        }
        did(call("putAll", &[handle.clone(), ProviderValue::List(entries)]));

        let mut prefix = vec![b'o'];
        prefix.extend_from_slice(&txid);
        let found = match okayed(call("prefixed", &[handle, ProviderValue::Bytes(prefix)])) {
            ProviderValue::List(items) => items,
            other => panic!("expected a List, got {other:?}"),
        };

        let order: Vec<u32> = found
            .iter()
            .map(|item| match item {
                ProviderValue::Tuple(parts) => match parts.as_slice() {
                    [ProviderValue::Bytes(key), _] => {
                        let tail = &key[key.len() - 4..];
                        u32::from_be_bytes([tail[0], tail[1], tail[2], tail[3]])
                    }
                    other => panic!("expected a pair, got {other:?}"),
                },
                other => panic!("expected a Tuple, got {other:?}"),
            })
            .collect();

        assert_eq!(order, (0..15).collect::<Vec<u32>>());
    }

    /// Opening what cannot be a database is an Err, not a fault: the
    /// directory is the caller's argument and a bad one is its problem.
    #[test]
    fn opening_a_file_rather_than_a_directory_is_an_error() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("not-a-directory");
        let mut file = std::fs::File::create(&path).unwrap();
        file.write_all(b"this is a file").unwrap();
        drop(file);
        let why = erred(call("open", &[text(&path.to_string_lossy())]));
        assert!(
            why.starts_with("cannot open the database at "),
            "unexpected wording: {why}"
        );
    }

    /// Wrong arity and wrong shapes are faults, because they mean the
    /// contract was not honoured rather than that the world said no.
    #[test]
    fn a_call_that_does_not_match_the_contract_faults() {
        let context = ProviderContext {
            capability: "Infra.Kv".to_string(),
            operation: "Infra.Kv.get".to_string(),
            contract_hash: CONTRACT_HASH.to_string(),
            model_hash: String::new(),
        };
        let fault = Kv.invoke(&context, &[text("only one argument")]).unwrap_err();
        assert_eq!(fault.code, "bad_arity");
        let fault = Kv
            .invoke(&context, &[text("not a handle"), raw("b:aa")])
            .unwrap_err();
        assert_eq!(fault.code, "bad_shape");
        let unknown = ProviderContext {
            operation: "Infra.Kv.truncate".to_string(),
            ..context
        };
        assert_eq!(Kv.invoke(&unknown, &[]).unwrap_err().code, "bad_operation");
    }

    /// RocksDB holds an exclusive lock on its directory, so a second open
    /// while the first Handle is alive is an Err the caller can read rather
    /// than a fault. Nothing in this program does it — one command opens one
    /// database once — but the failure should be legible if it ever does.
    #[test]
    fn a_second_open_while_the_first_is_alive_is_refused() {
        let dir = tempfile::tempdir().unwrap();
        let first = opened(dir.path());
        put(&first, &[("b:aa", "1")]);
        let why = erred(call("open", &[text(&dir.path().to_string_lossy())]));
        assert!(why.starts_with("cannot open the database at "), "unexpected: {why}");
        drop(first);
        let again = opened(dir.path());
        assert_eq!(got(&again, "b:aa"), Some("1".to_string()));
    }

    #[test]
    fn the_binding_names_the_contract_and_every_operation() {
        let binding = kv_binding();
        assert_eq!(binding.capability(), "Infra.Kv");
        assert_eq!(binding.contract_hash(), CONTRACT_HASH);
        assert_eq!(binding.operations().len(), 8);
    }

    #[test]
    fn get_all_of_five_thousand_keys_is_quick() {
        let dir = tempfile::tempdir().expect("a temporary directory");
        let handle = opened(dir.path());
        let keys: Vec<String> = (0..5000).map(|i| format!("u:{i:08}")).collect();
        let pairs: Vec<(&str, &str)> = keys.iter().map(|k| (k.as_str(), "v")).collect();
        put(&handle, &pairs);
        let asked: Vec<&str> = keys.iter().map(|k| k.as_str()).collect();
        let started = std::time::Instant::now();
        let answers = gotAll(&handle, &asked);
        let took = started.elapsed();
        assert_eq!(answers.len(), 5000);
        eprintln!("getAll of 5000 keys took {took:?}");
        assert!(took.as_millis() < 500, "getAll of 5000 keys took {took:?}");
    }

    #[test]
    fn apply_all_puts_and_deletes_in_one_batch() {
        let dir = tempfile::tempdir().expect("a temporary directory");
        let handle = opened(dir.path());
        put(&handle, &[("u:aa", "1"), ("u:bb", "2")]);
        let puts = ProviderValue::List(vec![ProviderValue::Tuple(vec![raw("u:cc"), raw("3")])]);
        let deletes = ProviderValue::List(vec![raw("u:aa"), raw("u:zz")]);
        did(call("applyAll", &[handle.clone(), puts, deletes]));
        assert_eq!(gotAll(&handle, &["u:aa", "u:bb", "u:cc"]), vec![None, Some("2".to_string()), Some("3".to_string())]);
    }

    #[test]
    fn a_cache_size_is_a_whole_number_of_megabytes_above_zero() {
        assert_eq!(parsed_cache_mb("1"), Ok(1 << 20));
        assert_eq!(parsed_cache_mb("6144"), Ok(6144 << 20));
        assert_eq!(parsed_cache_mb("  512  "), Ok(512 << 20));
    }

    /// A deployment that names a size and is given the default instead has been
    /// told nothing, so every one of these is refused rather than replaced. #308.
    #[test]
    fn a_cache_size_that_is_not_one_is_refused_rather_than_defaulted() {
        for said in ["0", "-1", "512MB", "1.5", "", "  ", "lots"] {
            assert!(parsed_cache_mb(said).is_err(), "'{said}' should be refused");
        }
        assert!(parsed_cache_mb("0").expect_err("zero is refused").contains(CACHE_MB_VAR));
    }

    /// usize::MAX megabytes is more bytes than the machine can address, and the
    /// shift that would have wrapped is caught rather than opening a database
    /// with a cache of a few bytes.
    #[test]
    fn a_cache_size_too_large_to_address_is_refused() {
        assert!(parsed_cache_mb(&usize::MAX.to_string()).is_err());
    }

    /// Not asserted through cache_bytes(): the variable is process-wide and
    /// another test setting it would decide this one. The default is the
    /// contract, and the binary proves the variable end to end.
    #[test]
    fn the_default_is_the_one_with_the_operating_history() {
        assert_eq!(CACHE_MB_DEFAULT << 20, 256 * 1024 * 1024);
    }

    #[test]
    fn get_all_answers_every_key_in_the_order_asked() {
        let dir = tempfile::tempdir().expect("a temporary directory");
        let handle = opened(dir.path());
        put(&handle, &[("u:aa", "1"), ("u:cc", "3")]);
        assert_eq!(
            gotAll(&handle, &["u:cc", "u:bb", "u:aa"]),
            vec![Some("3".to_string()), None, Some("1".to_string())]
        );
        assert_eq!(gotAll(&handle, &[]), Vec::<Option<String>>::new());
    }
}

#[cfg(test)]
mod compaction_tests {
    use super::*;

    /// Absent means leveled, which is what every measurement in this repo was
    /// taken against.
    #[test]
    fn absent_is_leveled() {
        assert!(matches!(parsed_compaction("leveled"), Ok(DBCompactionStyle::Level)));
        assert!(matches!(parsed_compaction("level"), Ok(DBCompactionStyle::Level)));
    }

    #[test]
    fn universal_is_understood() {
        assert!(matches!(parsed_compaction("universal"), Ok(DBCompactionStyle::Universal)));
        assert!(matches!(parsed_compaction("  universal  "), Ok(DBCompactionStyle::Universal)));
    }

    /// Refused, not guessed: a deployment that names a style and is given
    /// another has been told nothing and gets the Index it was avoiding.
    #[test]
    fn anything_else_is_refused_by_name() {
        match parsed_compaction("tiered") {
            Err(why) => assert!(why.contains("tiered") && why.contains("universal"), "{why}"),
            Ok(_) => panic!("an unknown style was accepted"),
        }
    }
}
