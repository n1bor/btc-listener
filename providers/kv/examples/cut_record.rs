//! Cut one record in a chain directory's Index short, or put it back, for the
//! regtest run in `docs/regtest-testing.md` (n1bor/btc-listener#355). The node
//! refuses a `msetTo` that is not exactly a Height and a Block Id, and
//! there is no other way to hand it one: Core cannot write our Index and the
//! node never writes a short record itself.
//!
//!     cargo run --release --manifest-path providers/kv/Cargo.toml --example cut_record -- <dir>/kv msetTo 4
//!     cargo run --release --manifest-path providers/kv/Cargo.toml --example cut_record -- <dir>/kv msetTo put:<hex>
//!
//! The first keeps the first N bytes of the value and prints the whole value
//! as hex first, so the second can put it back. Run it against a stopped node
//! only: RocksDB takes an exclusive lock, and a running node holds it.
use rocksdb::{Options, DB};

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn unhex(text: &str) -> Vec<u8> {
    (0..text.len() / 2)
        .map(|i| u8::from_str_radix(&text[2 * i..2 * i + 2], 16).expect("hex"))
        .collect()
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 4 {
        eprintln!("usage: cut_record <kv directory> <key text> <bytes to keep | put:<hex>>");
        std::process::exit(2);
    }
    let db =
        DB::open(&Options::default(), &args[1]).expect("open the Index (is the node stopped?)");
    let key = args[2].as_bytes();
    if let Some(value) = args[3].strip_prefix("put:") {
        let bytes = unhex(value);
        db.put(key, &bytes).expect("write");
        db.flush().expect("flush");
        println!("{}: {} byte(s) put back", args[2], bytes.len());
        return;
    }
    let keep: usize = args[3].parse().expect("bytes to keep is a number");
    let value = db.get(key).expect("read").unwrap_or_else(|| {
        eprintln!("no record at {}", args[2]);
        std::process::exit(1);
    });
    println!("{}: was {} byte(s): {}", args[2], value.len(), hex(&value));
    let cut = &value[..keep.min(value.len())];
    db.put(key, cut).expect("write");
    db.flush().expect("flush");
    println!("{}: cut to {} byte(s)", args[2], cut.len());
}
