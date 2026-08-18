//! A LevelDB behind the `Infra.Kv` capability contract.
//!
//! The database is not written here either. It comes from `rusty-leveldb`, a
//! re-implementation of LevelDB in Rust, chosen over `rocksdb` because it
//! needs no C++ toolchain to build. That choice is provisional and the
//! contract does not depend on it: swapping the crate changes this file and
//! nothing in Aver.
//!
//! An open database should be a capability resource, and is not.
//! jasisz/aver#994: an opaque resource can be passed and returned, but a user
//! record or sum holding one fails to compile on the Rust target, and
//! `Infra.Store` is a sum whose database arm has to hold the open database.
//! So `open` hands back `Handle { id }` and the databases live here, in a
//! registry keyed by that id.
//!
//! The id is therefore forgeable from Aver. That is why every operation looks
//! it up and returns an ordinary `Err` for one this process did not issue: a
//! made-up handle is a bad argument, never undefined behaviour. When #994
//! closes, `Handle` becomes opaque again, the registry goes, and no Aver
//! changes.

use std::collections::HashMap;
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

use aver_rt::provider::{
    CapabilityProvider, ProviderBinding, ProviderContext, ProviderFault, ProviderValue,
};
use rusty_leveldb::{LdbIterator, Options, WriteBatch, DB};

/// Pinned to the contract in `infra/kv.av`. A mismatch fails at startup rather
/// than at the first call.
pub const CONTRACT_HASH: &str =
    "sha256:d71f2dc311d4ead14a8b7e9e58b533ad874b9f6b8799568311422bdb11437022";

/// The type name the contract gives the handle record.
const HANDLE: &str = "Infra.Kv.Handle";

/// The open databases, by the id handed to Aver, and which directory each
/// came from. Each is behind its own lock because the LevelDB handle wants
/// `&mut` for every call, reads included.
#[derive(Default)]
struct Registry {
    open: HashMap<i64, Arc<Mutex<DB>>>,
    by_directory: HashMap<String, i64>,
}

fn registry() -> &'static Mutex<Registry> {
    static OPEN: OnceLock<Mutex<Registry>> = OnceLock::new();
    OPEN.get_or_init(|| Mutex::new(Registry::default()))
}

/// The id for a directory, opening it only if it is not already open here.
///
/// LevelDB takes an exclusive lock on its directory, so a second live handle
/// on one directory in one process could not exist anyway. Handing back the
/// id already issued is the only answer that is not an error.
fn opened_at(dir: &str) -> Result<i64, rusty_leveldb::Status> {
    static NEXT: AtomicI64 = AtomicI64::new(1);
    let mut registry = registry().lock().expect("the Kv registry is poisoned");
    if let Some(id) = registry.by_directory.get(dir) {
        return Ok(*id);
    }
    let mut options = Options::default();
    options.create_if_missing = true;
    let db = DB::open(dir, options)?;
    let id = NEXT.fetch_add(1, Ordering::Relaxed);
    registry.open.insert(id, Arc::new(Mutex::new(db)));
    registry.by_directory.insert(dir.to_string(), id);
    Ok(id)
}

/// The database an Aver `Handle` names, or an Err if it names none.
///
/// An id this process did not issue is a bad argument rather than a fault:
/// `Handle` is a record until jasisz/aver#994 closes, so Aver can write one.
fn recall(value: &ProviderValue, what: &str) -> Result<Result<Arc<Mutex<DB>>, String>, ProviderFault> {
    #[allow(clippy::type_complexity)]
    let ProviderValue::Record { fields, .. } = value else {
        return Err(ProviderFault::new("bad_shape", format!("{what} is not a Handle")));
    };
    let Some((_, ProviderValue::Int(id))) = fields.iter().find(|(name, _)| name == "id") else {
        return Err(ProviderFault::new("bad_shape", format!("{what} has no id")));
    };
    let Some(id) = id.to_i64() else {
        return Ok(Err("this handle names no open database".to_string()));
    };
    Ok(registry()
        .lock()
        .expect("the Kv registry is poisoned")
        .open
        .get(&id)
        .cloned()
        .ok_or_else(|| "this handle names no open database".to_string()))
}

/// Turn a recalled database into either the database or an early `Result.Err`.
macro_rules! database {
    ($value:expr, $what:expr) => {
        match recall($value, $what)? {
            Ok(db) => db,
            Err(why) => return Ok(ProviderValue::ResultErr(Box::new(ProviderValue::String(why)))),
        }
    };
}

struct Kv;

fn string_in(value: &ProviderValue, what: &str) -> Result<String, ProviderFault> {
    match value {
        ProviderValue::String(s) => Ok(s.clone()),
        _ => Err(ProviderFault::new("bad_shape", format!("{what} is not a String"))),
    }
}

/// The pairs of a `List<Tuple<String, String>>`.
fn pairs_in(value: &ProviderValue, what: &str) -> Result<Vec<(String, String)>, ProviderFault> {
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
        out.push((string_in(key, "key")?, string_in(value, "value")?));
    }
    Ok(out)
}

fn keys_in(value: &ProviderValue, what: &str) -> Result<Vec<String>, ProviderFault> {
    let ProviderValue::List(items) = value else {
        return Err(ProviderFault::new("bad_shape", format!("{what} is not a List")));
    };
    items.iter().map(|item| string_in(item, "key")).collect()
}

/// A database error the caller is meant to handle, as distinct from a
/// `ProviderFault`, which says the provider itself was called wrongly.
fn failed(what: &str, why: impl std::fmt::Display) -> ProviderValue {
    ProviderValue::ResultErr(Box::new(ProviderValue::String(format!("{what}: {why}"))))
}

fn ok(value: ProviderValue) -> ProviderValue {
    ProviderValue::ResultOk(Box::new(value))
}

/// Values are Aver `String`s, so what comes back out of the database has to be
/// UTF-8. Anything else means the file was written by something other than
/// this program, which is a fact about the world and so an Err, not a fault.
fn text(bytes: Vec<u8>, what: &str) -> Result<String, String> {
    String::from_utf8(bytes).map_err(|_| format!("{what} is not UTF-8"))
}

impl CapabilityProvider for Kv {
    fn identity(&self) -> &str {
        "btc-listener.kv/rusty-leveldb@1"
    }

    fn fingerprint(&self) -> &str {
        concat!("rusty-leveldb 4.0, built ", env!("CARGO_PKG_VERSION"))
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
                Ok(match opened_at(&dir) {
                    Ok(id) => ok(ProviderValue::Record {
                        type_name: HANDLE.to_string(),
                        fields: vec![("id".to_string(), ProviderValue::Int(id.into()))],
                    }),
                    Err(why) => failed(&format!("cannot open the database at '{dir}'"), why),
                })
            }
            "Infra.Kv.get" => {
                let [handle, key] = args else {
                    return Err(ProviderFault::new("bad_arity", "get takes a Handle and a String"));
                };
                let key = string_in(key, "key")?;
                let open = database!(handle, "handle");
                let mut db = open.lock().expect("Kv handle poisoned");
                Ok(match db.get(key.as_bytes()) {
                    None => ok(ProviderValue::OptionNone),
                    Some(bytes) => match text(bytes.to_vec(), &format!("the value under '{key}'")) {
                        Ok(value) => ok(ProviderValue::OptionSome(Box::new(ProviderValue::String(value)))),
                        Err(why) => ProviderValue::ResultErr(Box::new(ProviderValue::String(why))),
                    },
                })
            }
            "Infra.Kv.putAll" => {
                let [handle, entries] = args else {
                    return Err(ProviderFault::new("bad_arity", "putAll takes a Handle and a List"));
                };
                let entries = pairs_in(entries, "entries")?;
                let open = database!(handle, "handle");
                let mut batch = WriteBatch::default();
                for (key, value) in &entries {
                    batch.put(key.as_bytes(), value.as_bytes());
                }
                let mut db = open.lock().expect("Kv handle poisoned");
                Ok(match db.write(batch, true) {
                    Ok(()) => ok(ProviderValue::Unit),
                    Err(why) => failed("cannot write the batch", why),
                })
            }
            "Infra.Kv.deleteAll" => {
                let [handle, keys] = args else {
                    return Err(ProviderFault::new("bad_arity", "deleteAll takes a Handle and a List"));
                };
                let keys = keys_in(keys, "keys")?;
                let open = database!(handle, "handle");
                let mut batch = WriteBatch::default();
                for key in &keys {
                    batch.delete(key.as_bytes());
                }
                let mut db = open.lock().expect("Kv handle poisoned");
                Ok(match db.write(batch, true) {
                    Ok(()) => ok(ProviderValue::Unit),
                    Err(why) => failed("cannot delete the batch", why),
                })
            }
            "Infra.Kv.count" => {
                let [handle] = args else {
                    return Err(ProviderFault::new("bad_arity", "count takes a Handle"));
                };
                let open = database!(handle, "handle");
                let mut db = open.lock().expect("Kv handle poisoned");
                let mut iterator = match db.new_iter() {
                    Ok(iterator) => iterator,
                    Err(why) => return Ok(failed("cannot walk the keyspace", why)),
                };
                let mut held: i64 = 0;
                while iterator.next().is_some() {
                    held += 1;
                }
                Ok(ok(ProviderValue::Int(held.into())))
            }
            "Infra.Kv.prefixed" => {
                let [handle, prefix] = args else {
                    return Err(ProviderFault::new("bad_arity", "prefixed takes a Handle and a String"));
                };
                let prefix = string_in(prefix, "prefix")?;
                let open = database!(handle, "handle");
                let mut db = open.lock().expect("Kv handle poisoned");
                let mut iterator = match db.new_iter() {
                    Ok(iterator) => iterator,
                    Err(why) => return Ok(failed("cannot walk the keyspace", why)),
                };
                let mut found = Vec::new();
                iterator.seek(prefix.as_bytes());
                while let Some((key, value)) = iterator.current() {
                    if !key.starts_with(prefix.as_bytes()) {
                        break;
                    }
                    let key = match text(key.to_vec(), "a key") {
                        Ok(key) => key,
                        Err(why) => return Ok(ProviderValue::ResultErr(Box::new(ProviderValue::String(why)))),
                    };
                    let value = match text(value.to_vec(), &format!("the value under '{key}'")) {
                        Ok(value) => value,
                        Err(why) => return Ok(ProviderValue::ResultErr(Box::new(ProviderValue::String(why)))),
                    };
                    found.push(ProviderValue::Tuple(vec![
                        ProviderValue::String(key),
                        ProviderValue::String(value),
                    ]));
                    if !iterator.advance() {
                        break;
                    }
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

    fn text(value: &str) -> ProviderValue {
        ProviderValue::String(value.to_string())
    }

    fn opened(dir: &Path) -> ProviderValue {
        okayed(call("open", &[text(&dir.to_string_lossy())]))
    }

    /// Stand in for the process ending: drop the open database so the next
    /// `open` of the directory reads it back off the disk.
    ///
    /// There is no `close` in the contract. Nothing in Aver could be relied on
    /// to call one — the language has no destructors — so durability is not
    /// allowed to depend on it: every batch is written with `sync`, which
    /// pushes it out of this process's buffers. This helper exists so the
    /// tests below can show that, rather than assert it.
    fn ended(dir: &Path) {
        let mut registry = registry().lock().expect("the Kv registry is poisoned");
        let key = dir.to_string_lossy().to_string();
        if let Some(id) = registry.by_directory.remove(&key) {
            registry.open.remove(&id);
        }
    }

    fn put(handle: &ProviderValue, pairs: &[(&str, &str)]) {
        let entries = ProviderValue::List(
            pairs
                .iter()
                .map(|(key, value)| ProviderValue::Tuple(vec![text(key), text(value)]))
                .collect(),
        );
        did(call("putAll", &[handle.clone(), entries]));
    }

    fn got(handle: &ProviderValue, key: &str) -> Option<String> {
        match okayed(call("get", &[handle.clone(), text(key)])) {
            ProviderValue::OptionNone => None,
            ProviderValue::OptionSome(inner) => match *inner {
                ProviderValue::String(value) => Some(value),
                other => panic!("expected a String, got {other:?}"),
            },
            other => panic!("expected an Option, got {other:?}"),
        }
    }

    fn counted(handle: &ProviderValue) -> i64 {
        match okayed(call("count", &[handle.clone()])) {
            ProviderValue::Int(n) => n.to_i64().expect("count does not fit in an i64"),
            other => panic!("expected an Int, got {other:?}"),
        }
    }

    fn under(handle: &ProviderValue, prefix: &str) -> Vec<(String, String)> {
        match okayed(call("prefixed", &[handle.clone(), text(prefix)])) {
            ProviderValue::List(items) => items
                .into_iter()
                .map(|item| match item {
                    ProviderValue::Tuple(parts) => match parts.as_slice() {
                        [ProviderValue::String(key), ProviderValue::String(value)] => {
                            (key.clone(), value.clone())
                        }
                        other => panic!("expected a pair of Strings, got {other:?}"),
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
        let keys = ProviderValue::List(vec![text("b:aa"), text("b:zz")]);
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
        ended(dir.path());
        let handle = opened(dir.path());
        assert_eq!(got(&handle, "b:aa"), Some("1".to_string()));
        assert_eq!(got(&handle, "h:0"), Some("aa".to_string()));
        assert_eq!(counted(&handle), 3);
    }

    /// A batch reaches the disk whole or not at all.
    ///
    /// This is the property the append-only log could only approximate. It is
    /// shown the way it actually matters: write a batch, close, cut the tail
    /// of the write-ahead log so the record is torn, and reopen. LevelDB
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
            ended(dir.path());
        }
        {
            let handle = opened(dir.path());
            put(&handle, &[("a", "1"), ("b", "2"), ("c", "3"), ("d", "4")]);
            ended(dir.path());
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
        let keys = ProviderValue::List(vec![text("b:aa")]);
        did(call("deleteAll", &[handle.clone(), keys]));
        put(&handle, &[("b:bb", "kept")]);
        assert_eq!(got(&handle, "b:aa"), None);
        assert_eq!(got(&handle, "b:bb"), Some("kept".to_string()));
        assert_eq!(counted(&handle), 1);
    }

    /// Values cross the boundary as Aver `String`s. Bytes that are not UTF-8
    /// cannot have been written by this program, so they are reported as an
    /// Err the caller can handle rather than as a provider fault.
    #[test]
    fn a_value_that_is_not_utf8_is_an_error_the_caller_can_read() {
        let dir = tempfile::tempdir().unwrap();
        {
            let mut options = Options::default();
            options.create_if_missing = true;
            let mut db = DB::open(dir.path(), options).unwrap();
            db.put(b"b:aa", &[0xff, 0xfe]).unwrap();
            db.flush().unwrap();
        }
        ended(dir.path());
        let handle = opened(dir.path());
        let why = erred(call("get", &[handle.clone(), text("b:aa")]));
        assert_eq!(why, "the value under 'b:aa' is not UTF-8");
        let why = erred(call("prefixed", &[handle, text("b:")]));
        assert_eq!(why, "the value under 'b:aa' is not UTF-8");
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
            .invoke(&context, &[text("not a handle"), text("b:aa")])
            .unwrap_err();
        assert_eq!(fault.code, "bad_shape");
        let unknown = ProviderContext {
            operation: "Infra.Kv.truncate".to_string(),
            ..context
        };
        assert_eq!(Kv.invoke(&unknown, &[]).unwrap_err().code, "bad_operation");
    }

    /// Opening one directory twice in one process gives the same database.
    /// LevelDB holds an exclusive lock on it, so no other answer is available.
    #[test]
    fn opening_the_same_directory_twice_gives_the_same_database() {
        let dir = tempfile::tempdir().unwrap();
        let first = opened(dir.path());
        put(&first, &[("b:aa", "1")]);
        let again = opened(dir.path());
        assert_eq!(got(&again, "b:aa"), Some("1".to_string()));
    }

    /// A handle this process never issued is an Err the caller can read, not a
    /// fault and not a crash. `Handle` is an ordinary record until
    /// jasisz/aver#994 closes, so Aver can write one.
    #[test]
    fn a_handle_that_was_never_issued_is_refused() {
        let invented = ProviderValue::Record {
            type_name: HANDLE.to_string(),
            fields: vec![("id".to_string(), ProviderValue::Int(999_999i64.into()))],
        };
        assert_eq!(
            erred(call("get", &[invented, text("b:aa")])),
            "this handle names no open database"
        );
    }

    #[test]
    fn the_binding_names_the_contract_and_every_operation() {
        let binding = kv_binding();
        assert_eq!(binding.capability(), "Infra.Kv");
        assert_eq!(binding.contract_hash(), CONTRACT_HASH);
        assert_eq!(binding.operations().len(), 6);
    }
}
