# Work/Wait migration

This branch moves block decoding and pure UTXO connection onto bounded typed Work
jobs. A single lookahead decodes Block N+1 while the owner resolves inputs
and a second job connects Block N. The CLI owner resolves database inputs, serves peers while the
job runs, and alone applies and persists its result. The exact existing
`Domain.Block.transactionsOf` and `Domain.Connect.connected` algorithms run
behind capability-owned Task/Reply adapters. A worker owns no database or socket.
This is the explicit CLI owner using Work/Wait; it is not yet a generated
`[run]` / yielding-process rewrite.

All eight `Tcp.poll` calls on the application path now use `Wait.poll`.
Peer and dashboard readiness reads use `readNow`. A Work wait watches the job,
connected sockets, listener and pending dial, with a 100 ms stop-check deadline.
A read tick processes one batch even when it contains only an incomplete frame.
A ping is answered while Work is pending; other frames remain deferred. Address
gossip is folded into the Book, and the remaining deferred messages now pass
through the ordinary dispatcher, oldest first, rather than being discarded.
The existing queue still has its 64-message overflow policy.

No block context can be replaced while this owner is awaiting these jobs. Both jobs settle before the owner
commits Block N; every error or stop cancels the retained lookahead. The next
height is never read past the requested target.
Cancellation discards the answer and returns through the normal path that
flushes earlier completed work. Generated Rust detaches a cancelled computation;
its thread is not preempted and retains its job slot until it finishes. This
branch does not claim that cancellation terminates native computation.

## Compiler requirement and build

Build with the Aver revision in `.aver-version`, including its matching
`aver-rt`. The compiler changes and validation results are tracked in
[PR #361](https://github.com/n1bor/btc-listener/pull/361).

The upstream provider Cargo manifests already contain the author's absolute
local aver-rt path. For a local build, set both `providers/primitives/Cargo.toml`
and `providers/kv/Cargo.toml` to the aver-rt directory of the **same checkout**
used by the compiler. Those machine-specific overrides are not part of this
migration commit. Reuse a Cargo target directory when disk space is limited.

```sh
aver check main.av
aver compile main.av --target rust --with-replay -o /tmp/btc-work-rust
cargo build --manifest-path /tmp/btc-work-rust/Cargo.toml --profile iteration
```

The compiler picks up `[work] max-jobs = 4` and the `Infra.BlockJobs` binding
from aver.toml. The limit is not what bounds the overlap: the walk asks for a
lookahead decode and a connect, and settles both before every commit, so two
jobs are in flight however high the limit goes. The limit is four rather than
two because `begin` refuses at the limit and a cancelled native job keeps its
slot until its body finishes. At exactly two, a retry that follows an
owner-side error could be refused with `work: job limit 2 reached`, and that
refusal is charged to whichever peer was answering at the time. The normal CLI
and data format are unchanged.

## Production owner responsiveness probe

This entry uses the actual `Infra.Working`, `Infra.Peers`, `Infra.Tending` and
`Domain.BlockWorkJob` implementation, with a local Bitcoin wire peer. It supplies
a synthetic payload containing repeated genesis transactions to keep the decoder
busy. It is not a consensus-valid block, a throughput benchmark, or an end-to-end
chain acceptance test. No database is opened by this probe.

```sh
aver compile tools/working_probe.av --module-root . --target rust --with-replay -o /tmp/btc-working-probe
cargo build --manifest-path /tmp/btc-working-probe/Cargo.toml --profile iteration
python3 tools/regtest/work-wait.py /tmp/btc-working-probe/target/iteration/working_probe
python3 tools/regtest/work-wait.py /tmp/btc-working-probe/target/iteration/working_probe --mode cancel
python3 tools/regtest/work-wait.py /tmp/btc-working-probe/target/iteration/working_probe --count 10000 --mode record
```

The peer fragments a checksummed ping with a 20 ms gap and sends an inv. The
harness checks the nonce/checksum, requires pong before the worker result,
checks the complete transaction count and retained announcement, then closes
all processes and sockets. Cancel mode requires no delivered result and a
prompt owner return. Record mode replays after the real peer has gone away.
Use the corresponding target path if CARGO_TARGET_DIR is set.

## DNS exchange probe

Seed discovery is the one rewritten network path nothing else reaches. A
regtest network has no DNS seeds and the real ones are on the internet, so the
new dial, write and reassembly loop shipped with no automated coverage at all.
`Infra.Resolver.lookupAt` takes the server as an argument for exactly this
reason: `tools/dns_probe.av` calls it and prints one line, and
`tools/regtest/dns-probe.py` is the resolver on the other end of the socket.

```sh
aver compile tools/dns_probe.av --module-root . -o /tmp/btc-dns-probe
cargo build --manifest-path /tmp/btc-dns-probe/Cargo.toml --profile iteration
python3 tools/regtest/dns-probe.py /tmp/btc-dns-probe/target/iteration/dns_probe
```

Seven scenarios take about nineteen seconds. One sends the whole answer. One
sends the whole answer and then closes, which is what a real resolver does. One
splits the two-byte length prefix down the middle and sends the body in three
further pieces. One closes halfway through the body it announced, which has to
produce an error rather than a partial answer presented as a complete one and
rather than a wait for bytes that are not coming. One sends a 64 KB answer of
4,000 A records in 4 KiB pieces, none of which carries the message. One says
nothing and is interrupted with SIGINT, which has to return through the
cooperative stop. One says nothing and is left alone, which has to return when
the fifteen-second deadline expires. Every scenario also reads the question
that arrived and checks its length prefix, flags and A/IN section, so a probe
that never wrote a well-formed question could not pass the read cases by
accident. Each scenario has a ceiling and the harness fails rather than waits,
so nothing here can pass by hanging.

The deadline scenario costs the fifteen seconds it is measuring and there is no
honest way to shorten it. An injectable deadline would be a test hook in
production code, and the stop that is already there is what the SIGINT scenario
uses. `--skip silent-until-deadline` leaves it out of a local run.

CI runs this in the `compile` job and not under the Node host in `wasm-node`,
where the Work probe runs. `wasm/host.mjs` serves `Wait.poll` through the Work
host's codec, which reads the `aver:work/v1` descriptor and the `__cap_abi_`
helpers the compiler emits for a program that binds a Work capability. The
resolver binds none, so the module carries no descriptor and `createWorkHost`
refuses it with `work: expected one aver:work/v1 descriptor`. Giving the probe
a Work capability it has no use for, so that a host would accept it, would be a
worse test than this one. The `compile` job already holds the toolchain and the
Cargo target directory this needs, so the probe's own crate is the only new
work there, and it runs before the binary is uploaded because a resolver that
hangs is a reason not to publish one.

The same scenarios run on the VM with no build at all, and six of the seven
pass:

```sh
python3 tools/regtest/dns-probe.py --skip stopped-while-silent \
  aver run tools/dns_probe.av --module-root . --
```

SIGINT does not reach `Process.stopRequested` under the VM -- the probe runs on
to the deadline and the process is killed -- so `stopped-while-silent` has to
be skipped there. That difference is why CI pays for the compiled binary rather
than taking the cheaper run.

## Remaining network waits removed

Four groups of network stalls remained after the first Work slice: inline
inbound/outbound greetings, peer writes, dashboard readers/writes, and DNS.
The six blocking TCP call sites have been removed. The application contains no
calls to Tcp.connect, Tcp.readBytes, Tcp.readSome, or Tcp.writeBytes.

- Active inbound and outbound greetings are pool-owned state. Admission reserves
  the key immediately; peerKeys and normal frame delivery expose a peer only
  after verack. One ten-second deadline is retained across partial frames and
  pings. At most four buffered greeting frames per peer are processed per
  maintenance pass. Pre-verack traffic has the existing eight-frame cap and is
  released in order only after a successful greeting. Failures discard its
  pending state and close only that peer. An outbound greeting remains part of
  the one-at-a-time dial budget; completion does not clear another dial.
- Framed peer messages enter a FIFO bounded to 8 MiB and 256 messages per peer.
  Each flush writes at most 64 KiB, accounts actual bytes, retains the exact
  offset, and watches write readiness only while bytes remain. Overflow or
  delayed write failure drops the offending peer. Closing a peer removes its
  outbox, greeting, pending block queue and deferred input. These bounds
  intentionally refuse a client that accumulates too much output.
- A getdata for blocks is answered from a bounded per-peer queue of the
  requested identifiers rather than by framing every block where the message
  arrived. The follow loop reads one block off disk for a peer only while that
  peer has less than 4 MiB and fewer than 128 messages waiting. Both bounds,
  because the outbox refuses on whichever it reaches first and which one that
  is depends on block size: 4 MiB over 256 messages puts the crossover at
  16 KiB a block, and a peer asking for early mainnet blocks of a couple of
  hundred bytes each would reach the message limit thousands of blocks before
  the byte one. With both, serving is never itself the enqueue that fails. What
  it leaves the rest of the queue is 194,281 bytes and 128 message slots, and
  that byte reserve holds one 2,000-header reply and not two.
  Blocks leave in the order they were asked for, and blocks this node does not
  hold are still passed over in silence. `servedBlockCap` now caps the queue
  rather than one message, so a second getdata cannot lift it. One turn reads
  `servedPerTurn` = 16 blocks over all the peers together and leaves the rest
  for the next turn: a turn that stopped only at every peer's watermark would
  be up to 125 × `servedBlockCap` store lookups for hashes this node does not
  hold, none of which move a watermark, and the serving chain carries no
  `Process.stopRequested` and cannot notice a stop partway through. A peer
  with anything queued shortens the poll to 100 ms. Transaction serving is
  unchanged: the mempool already holds those bytes.
  `Infra.Working` has no `Infra.Kv` or `Disk.readBytesAt` effects and
  does not get them, so the serving turns that run while a Work job is pending
  drain wires and answer pings but read no blocks; a request that arrives
  during one waits for the loop's next turn.
- The board retains up to sixteen clients and accepts at most four per turn.
  Each client has a bounded 4 KiB request, a response offset and a single
  five-second lifetime. Partial headers and responses survive across turns.
  The latest Board travels back from catch-up Eyes; graceful shutdown releases
  retained readers as well as the listener. Pending board readers get short
  follow turns and enter the Work owner's combined wait on disjoint keys.
- DNS uses beginConnect/dialled/readNow/writeNow with a single fifteen-second
  deadline covering connect, write, prefix and response. Stop is checked between
  waits of at most 100 ms. This is the path the [DNS exchange
  probe](#dns-exchange-probe) covers, against a fake resolver on loopback.
  Seed discovery remains a sequential facade used at
  startup or when no peers/candidates remain; it does not run a dashboard turn
  during the lookup. Startup joined/handshake APIs likewise remain synchronous
  facades, with bounded waits and cooperative stop checks.
- The follow loop checks for its first ready peer again after a read turn,
  because an asynchronous greeting can complete there and require catch-up.

## Node wasm acceptance

`wasm/host.mjs` now supplies `Wait.poll`, `Tcp.readNow` and `Tcp.writeNow`.
The Work adapter is copied unchanged from the pinned Aver source under
`wasm/aver-work/`; CI checks its bytes against that source. Socket wait
subscriptions are removed when a job wins the wait. Drain events resume
backpressured writers, and SIGINT/SIGTERM stop the Work owner.

The production Work probe decodes 2,000 synthetic transactions in a Node worker
while the main instance handles a fragmented ping. Delivery and cancellation
both preserve the deferred inv; pong must precede the result, and cancellation
must not deliver a decoded result. The full CLI separately rejects a corrupt
frame and exits promptly after its final peer disappears. A real Core regtest
run synced through height 175 and stopped cooperatively.

The host uses temporary Disk and in-memory KV. These tests establish this
application's Work/Wait path in Node, not durable deployment parity with native
RocksDB. Native cancellation still discards an answer without preempting the
running computation.

## Repeatable native acceptance

`tools/regtest/suite.py` automates fresh isolated Core nodes, the four script
types, reorganisations and undo, relay, compact reconstruction, inbound sync,
catch-up announcements, hostile peers, the terminal Screen, storage guards and
one getdata whose blocks come to more than a peer's outbox holds
(`tools/regtest/suite_serving.py`).
It keeps a JSON report and logs after cleaning up its processes. CI consumes
the existing native build artifact and runs this suite against pinned Core 31.1.
See [the standing regtest guide](regtest-testing.md) for invocation and scope.

Synchronous owner-side database and filesystem operations remain. Startup DNS
is sequential, native cancellation does not preempt a running computation, and
the deferred-input queue retains its existing 64-message limit.

The pinned compiler transfers native Work tasks and results without rebuilding
provider value trees during ordinary execution. Recording and explicit host
providers retain the public value format. Measured throughput, responsiveness
and the current CI status are recorded in
[PR #361](https://github.com/n1bor/btc-listener/pull/361).
