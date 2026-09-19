#!/usr/bin/env python3
"""Fake TCP resolver acceptance for the bounded DNS exchange in Infra.Resolver.

Seed discovery is the part of the Work/Wait migration nothing else tests. A
regtest network has no DNS seeds, the real seeds are on the internet, and CI
has neither, so the rewritten dial, write and reassembly loop shipped without
automated coverage. Infra.Resolver.lookupAt takes the server as an argument
for this reason; tools/dns_probe.av calls it and prints one line, and this
harness is the resolver on the other end of the socket.

Pass the command that runs the probe. The harness appends 127.0.0.1, the port
it is listening on and the name to ask about, so anything that can run the
program works:

    python3 tools/regtest/dns-probe.py /tmp/btc-dns-probe/target/iteration/dns_probe
    python3 tools/regtest/dns-probe.py --skip stopped-while-silent \
        aver run tools/dns_probe.av --module-root . --

The command is taken verbatim, so this harness's own options come before it.
The second form needs no build, and six of the seven scenarios pass under it.
SIGINT does not reach Process.stopRequested on the VM, which is why the stop
scenario has to be skipped there and why CI uses the compiled binary.

Every scenario checks the question that arrived as well as the verdict that
came back: a probe that never wrote a well-formed question would otherwise
pass the read cases by accident. The answers are built here rather than
captured, because what is under test is the exchange and not the record
format, which domain/dns.av already checks against a real captured answer.
"""
import argparse
import json
import signal
import socket
import struct
import subprocess
import time

# Longer than the hundred milliseconds the read loop bounds each of its own
# waits by, so a piece sent after this gap is a piece the probe comes back to
# the socket for, and short enough that the scenarios stay inside a couple of
# seconds each.
GAP = 0.12


def decoded_name(labels):
    """The same, read back, so the question can be compared with what was asked."""
    parts, at = [], 0
    while labels[at] != 0:
        length = labels[at]
        parts.append(labels[at + 1:at + 1 + length].decode())
        at += 1 + length
    return ".".join(parts)


def exact(connection, count):
    """Read precisely this many bytes, or say that the probe went away."""
    data = b""
    while len(data) < count:
        part = connection.recv(count - len(data))
        assert part, "the probe closed before it had written a complete question"
        data += part
    return data


def question_of(connection):
    """Read the length-prefixed question and check it is the one DNS expects.

    The two-byte prefix has to agree with the body, the flags have to be a
    plain recursion-desired query, and the question has to be one A record and
    nothing after it. This is the only check the write half of the exchange
    gets: the probe writes through the same writeNow loop the seed lookup uses.
    """
    size = struct.unpack("!H", exact(connection, 2))[0]
    body = exact(connection, size)
    identifier, flags, questions, answers, authorities, additional = struct.unpack("!HHHHHH", body[:12])
    assert flags == 0x0100, "expected a recursion-desired query, got flags {:#06x}".format(flags)
    assert (questions, answers, authorities, additional) == (1, 0, 0, 0), body[:12].hex()
    end = body.index(b"\0", 12) + 1
    kind, klass = struct.unpack("!HH", body[end:end + 4])
    assert (kind, klass) == (1, 1), "expected an A/IN question, got {}/{}".format(kind, klass)
    assert end + 4 == size, "the question was followed by {} bytes".format(size - end - 4)
    return identifier, body[12:end + 4], decoded_name(body[12:end])


def built_answer(identifier, question, addresses):
    """A length-prefixed answer carrying one A record per address."""
    records = b"".join(
        b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + bytes(int(part) for part in address.split("."))
        for address in addresses
    )
    body = struct.pack("!HHHHHH", identifier, 0x8180, 1, len(addresses), 0, 0) + question + records
    assert len(body) <= 65535, "a DNS message over TCP cannot exceed 65535 bytes"
    return struct.pack("!H", len(body)) + body


def whole(connection, reply):
    """Everything in one send, which is what a resolver on loopback usually does."""
    connection.sendall(reply)


def fragmented(connection, reply):
    """The length prefix split down the middle and the body in three pieces.

    This is the case the rewritten loop exists for. The old code read the two
    prefix bytes and then exactly as many body bytes as they announced, and an
    exact-length read cannot be interrupted; the new one keeps the bytes it has
    and comes back for the rest between bounded waits.
    """
    for piece in (reply[:1], reply[1:2], reply[2:14], reply[14:30], reply[30:]):
        connection.sendall(piece)
        time.sleep(GAP)


def closing(connection, reply):
    """Everything in one send and then the write side closed, which is what a
    real resolver does: one question, one answer, and the socket is finished
    with. The read loop has to take the answer it is holding rather than the
    end of the stream that arrives in the same breath.
    """
    connection.sendall(reply)
    connection.shutdown(socket.SHUT_WR)


def truncated(connection, reply):
    """Half of the announced body, then the write side closed for good.

    A resolver that dies mid-answer must produce an error. The two ways to get
    this wrong are both worse than an error: returning the addresses read so
    far is a partial answer presented as a complete one, and waiting for bytes
    that will never arrive holds the caller until the deadline.
    """
    connection.sendall(reply[:2 + (len(reply) - 2) // 2])
    time.sleep(GAP)
    connection.shutdown(socket.SHUT_WR)


def chunked(connection, reply):
    """A long answer in four-kilobyte pieces with the same gap between them.

    Size alone would not settle this. A probe that starts in five milliseconds
    can find the whole answer already buffered and take it in one read, however
    long the answer is. The gap is longer than the hundred-millisecond wait the
    read loop bounds itself by, so the probe comes back to the socket between
    the pieces and assembles the answer out of many arrivals, and the length is
    what gives the buffer it carries between them some weight.
    """
    for start in range(0, len(reply), 4096):
        connection.sendall(reply[start:start + 4096])
        time.sleep(GAP)


def silent(connection, reply):
    """Accept the question and say nothing, leaving the socket open."""


def many(count):
    """Distinct addresses, enough of them that the answer outgrows one read."""
    return ["10.{}.{}.{}".format(index // 65536 % 256, index // 256 % 256, index % 256) for index in range(1, count + 1)]


def stopped(process):
    """End the probe and return whatever it managed to print on the way.

    A probe that never connected, or that hung, is the interesting failure
    here, and the only thing that can say why is what it wrote before it was
    stopped. Losing that leaves a timeout with no cause attached to it.
    """
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    return (process.communicate()[0] or "").strip() or "nothing at all"


def verdict_of(command, respond, name, addresses, timeout, stop_after=None):
    """Run the probe against one scenario and return its line and its duration.

    The listening socket, the connection and the process are all closed on the
    way out, including after a failed assertion, so a red scenario does not
    leave a port bound or a probe running for the next one.
    """
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(timeout)
        port = listener.getsockname()[1]
        started = time.monotonic()
        process = subprocess.Popen(
            [*command, "127.0.0.1", str(port), name],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            connection = listener.accept()[0]
            with connection:
                connection.settimeout(timeout)
                identifier, question, asked = question_of(connection)
                assert asked == name, "the probe asked about {} rather than {}".format(asked, name)
                respond(connection, built_answer(identifier, question, addresses))
                if stop_after is not None:
                    time.sleep(stop_after)
                    process.send_signal(signal.SIGINT)
                output = process.communicate(timeout=timeout)[0]
        except BaseException as failure:
            raise AssertionError("{}: {}. The probe said: {}".format(
                type(failure).__name__, failure, stopped(process))) from failure
        assert process.returncode == 0, "the probe exited {}: {}".format(process.returncode, output)
        lines = [line for line in output.splitlines() if line.startswith(("answer ", "error "))]
        assert len(lines) == 1, "expected one verdict line, got: {}".format(output)
        return lines[0], time.monotonic() - started


def answered(addresses):
    """The line the probe prints when every address came back."""
    return "answer {} {}".format(len(addresses), " ".join(addresses))


def scenarios(records):
    """What is run, in the order it is run, with what each one has to produce.

    `within` is a ceiling rather than a measurement. It is what separates an
    error from a hang: the exchange deadline is fifteen seconds, so a case that
    must refuse promptly is only passing if it refuses well inside that.
    """
    three = ["1.2.3.4", "5.6.7.8", "9.10.11.12"]
    long_answer = many(records)
    return [
        ("whole-answer", whole, three, answered(three), 10, None),
        ("answer-then-close", closing, three, answered(three), 10, None),
        ("fragmented-prefix-and-body", fragmented, three, answered(three), 10, None),
        ("closed-before-complete", truncated, three, "error DNS connection closed before its complete answer", 10, None),
        ("larger-than-one-read", chunked, long_answer, answered(long_answer), 30, None),
        ("stopped-while-silent", silent, three, "error DNS discovery stopped", 10, 0.4),
        ("silent-until-deadline", silent, three, "error DNS exchange deadline expired", 30, None),
    ]


def trial(command, name, records, skip):
    """Every scenario in turn, returning what each one measured."""
    measured = {}
    for label, respond, addresses, expected, within, stop_after in scenarios(records):
        if label in skip:
            continue
        verdict, seconds = verdict_of(command, respond, name, addresses, within * 2, stop_after)
        assert verdict == expected, "{}: expected {!r}, got {!r}".format(
            label, expected[:120], verdict[:120])
        assert seconds < within, "{}: took {:.1f}s, which is not inside {}s".format(label, seconds, within)
        measured[label] = {"addresses": len(addresses), "seconds": round(seconds, 2)}
    return measured


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", default="seed.bitcoin.sipa.be", help="the name the probe asks about")
    parser.add_argument("--records", type=int, default=4000, help="A records in the long answer")
    parser.add_argument("--skip", action="append", default=[], help="a scenario to leave out, repeatable")
    # Taken verbatim, so a command with options of its own is passed through
    # rather than parsed here. The options above therefore come first.
    parser.add_argument("command", nargs=argparse.REMAINDER, help="how to run the probe; the host, port and name are appended")
    args = parser.parse_args()
    if not args.command:
        parser.error("a command that runs the probe is required")
    print(json.dumps(trial(args.command, args.name, args.records, set(args.skip))), flush=True)
