//! An fsync behind the `Infra.Durable` capability contract.
//!
//! Thirty lines of `std::fs`, and the reason they are here rather than in an
//! Aver `Disk` call is that Aver has no fsync. `Disk.appendBytes` opens the
//! file, writes and returns; the bytes are in the operating system's cache at
//! that point. For almost everything a program appends that is right. For a
//! Segment whose `b:` Location is about to be recorded in a RocksDB that does
//! call fsync, it is n1bor/btc-listener#301: a power cut between the two
//! leaves a durable Location naming bytes that were never written.
//!
//! `File::sync_all` is `fsync(2)`, which flushes the file's data *and* its
//! metadata. `sync_data` (`fdatasync`) would be enough for bytes appended to a
//! file that already exists, and is not enough for the append that creates a
//! Segment — the file's own directory entry has to be durable too, or the
//! bytes are in a file nothing names. Segments are created rarely and synced
//! often, so the cheap call would be right nearly always and wrong exactly
//! when a Segment rolls over. `sync_all` on every path is the honest one.
//!
//! Opening for read is enough to fsync on every platform this runs on: the
//! descriptor names the file, and the kernel flushes that file's dirty pages
//! whoever opened it. Nothing here writes, so a bug here cannot corrupt a
//! Segment — the worst it can do is fail to make one durable, and say so.

use std::sync::Arc;

use aver_rt::provider::{
    CapabilityProvider, ProviderBinding, ProviderContext, ProviderFault, ProviderValue,
};

/// Pinned to the contract in `infra/durable.av`. A mismatch fails at startup
/// rather than at the first call.
pub const CONTRACT_HASH: &str = "sha256:1b95bd10e074e8ba87cda51847d603885ddbcd0f51a3c0960270aab672644185";

struct Durable;

fn string_in(value: &ProviderValue, what: &str) -> Result<String, ProviderFault> {
    match value {
        ProviderValue::String(s) => Ok(s.clone()),
        _ => Err(ProviderFault::new("bad_shape", format!("{what} is not a String"))),
    }
}

fn paths_in(value: &ProviderValue, what: &str) -> Result<Vec<String>, ProviderFault> {
    let ProviderValue::List(items) = value else {
        return Err(ProviderFault::new("bad_shape", format!("{what} is not a List")));
    };
    items.iter().map(|item| string_in(item, "path")).collect()
}

/// A failure the caller is meant to handle, as distinct from a
/// `ProviderFault`, which says the provider itself was called wrongly.
fn failed(what: &str, why: impl std::fmt::Display) -> ProviderValue {
    ProviderValue::ResultErr(Box::new(ProviderValue::String(format!("{what}: {why}"))))
}

fn ok(value: ProviderValue) -> ProviderValue {
    ProviderValue::ResultOk(Box::new(value))
}

/// One file to the disk. The path is named in both failures, because the
/// caller passes several and a message that does not say which is no use.
fn synced_one(path: &str) -> Result<(), ProviderValue> {
    let file = std::fs::File::open(path)
        .map_err(|why| failed(&format!("cannot open '{path}' to make it durable"), why))?;
    file.sync_all()
        .map_err(|why| failed(&format!("cannot make '{path}' durable"), why))
}

impl CapabilityProvider for Durable {
    fn identity(&self) -> &str {
        "btc-listener.durable/fsync@1"
    }

    fn fingerprint(&self) -> &str {
        concat!("std::fs::File::sync_all, built ", env!("CARGO_PKG_VERSION"))
    }

    fn invoke(
        &self,
        context: &ProviderContext,
        args: &[ProviderValue],
    ) -> Result<ProviderValue, ProviderFault> {
        match context.operation.as_str() {
            "Infra.Durable.synced" => {
                let [paths] = args else {
                    return Err(ProviderFault::new(
                        "bad_arity",
                        "synced takes one List<String>",
                    ));
                };
                for path in paths_in(paths, "paths")? {
                    if let Err(why) = synced_one(&path) {
                        return Ok(why);
                    }
                }
                Ok(ok(ProviderValue::Unit))
            }
            other => Err(ProviderFault::new(
                "unknown_operation",
                format!("{other} is not an operation of Infra.Durable"),
            )),
        }
    }
}

pub fn durable_binding() -> ProviderBinding {
    ProviderBinding::new(
        "Infra.Durable",
        CONTRACT_HASH,
        ["Infra.Durable.synced"],
        Arc::new(Durable),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write as _;

    fn call(args: &[ProviderValue]) -> ProviderValue {
        Durable
            .invoke(
                &ProviderContext {
                    capability: "Infra.Durable".to_string(),
                    operation: "Infra.Durable.synced".to_string(),
                    contract_hash: CONTRACT_HASH.to_string(),
                    model_hash: String::new(),
                },
                args,
            )
            .expect("the provider faulted")
    }

    fn listed(paths: &[&std::path::Path]) -> ProviderValue {
        ProviderValue::List(
            paths
                .iter()
                .map(|p| ProviderValue::String(p.to_string_lossy().into_owned()))
                .collect(),
        )
    }

    fn wrote(dir: &tempfile::TempDir, name: &str) -> std::path::PathBuf {
        let path = dir.path().join(name);
        let mut file = std::fs::File::create(&path).expect("create");
        file.write_all(b"a Block, near enough").expect("write");
        path
    }

    #[test]
    fn syncs_a_file_that_is_there() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = wrote(&dir, "blk000000.dat");
        assert!(matches!(call(&[listed(&[&path])]), ProviderValue::ResultOk(_)));
    }

    /// The batch form is the only form, so several at once is the ordinary
    /// call and not a special case: a batch of Locations spans a Segment
    /// boundary whenever one fills.
    #[test]
    fn syncs_several() {
        let dir = tempfile::tempdir().expect("tempdir");
        let first = wrote(&dir, "blk000000.dat");
        let second = wrote(&dir, "blk000001.dat");
        assert!(matches!(
            call(&[listed(&[&first, &second])]),
            ProviderValue::ResultOk(_)
        ));
    }

    /// Nothing to do is not a failure. A batch of Locations written before
    /// any Block was appended -- a run that fetched nothing -- names no
    /// Segment, and that is a legal batch.
    #[test]
    fn an_empty_list_is_fine() {
        assert!(matches!(
            call(&[ProviderValue::List(vec![])]),
            ProviderValue::ResultOk(_)
        ));
    }

    /// An absent Segment is an Err the caller has to handle, not a silence.
    /// The caller names files it has just appended to, so an absent one means
    /// the append did not go where it thought.
    #[test]
    fn an_absent_file_is_an_error_naming_it() {
        let dir = tempfile::tempdir().expect("tempdir");
        let missing = dir.path().join("blk000009.dat");
        match call(&[listed(&[&missing])]) {
            ProviderValue::ResultErr(why) => match *why {
                ProviderValue::String(text) => {
                    assert!(text.contains("blk000009.dat"), "{text}");
                    assert!(text.contains("durable"), "{text}");
                }
                other => panic!("the Err is not a String: {other:?}"),
            },
            other => panic!("an absent file was not an Err: {other:?}"),
        }
    }

    /// The first failure is the answer, and the ones after it are not
    /// attempted: the caller is about to decide whether a batch of Locations
    /// may be written, and one Segment that would not go is enough to say no.
    #[test]
    fn the_first_failure_is_the_answer() {
        let dir = tempfile::tempdir().expect("tempdir");
        let missing = dir.path().join("blk000009.dat");
        let there = wrote(&dir, "blk000000.dat");
        match call(&[listed(&[&missing, &there])]) {
            ProviderValue::ResultErr(why) => match *why {
                ProviderValue::String(text) => assert!(text.contains("blk000009.dat"), "{text}"),
                other => panic!("the Err is not a String: {other:?}"),
            },
            other => panic!("an absent file was not an Err: {other:?}"),
        }
    }

    #[test]
    fn a_non_list_is_a_fault_not_an_err() {
        let fault = Durable.invoke(
            &ProviderContext {
                capability: "Infra.Durable".to_string(),
                operation: "Infra.Durable.synced".to_string(),
                contract_hash: CONTRACT_HASH.to_string(),
                model_hash: String::new(),
            },
            &[ProviderValue::String("not a list".to_string())],
        );
        assert!(fault.is_err());
    }
}
