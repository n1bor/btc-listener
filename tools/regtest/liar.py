import socket, struct, hashlib, threading, time, sys
MAGIC = bytes([0xfa,0xbf,0xb5,0xda])           # regtest
def msg(cmd, payload, corrupt=False):
    c = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    if corrupt: c = bytes([(c[0]+1) % 256]) + c[1:]
    return MAGIC + cmd.encode().ljust(12, b'\0') + struct.pack('<I', len(payload)) + c + payload
AGENT = b'/liar:1/'
def version_payload():
    return (struct.pack('<iQq', 70016, 0, int(time.time())) + b'\0'*26 + b'\0'*26
            + struct.pack('<Q', 12345) + bytes([len(AGENT)]) + AGENT + struct.pack('<i', 0) + b'\0')
REGTEST_GENESIS = bytes.fromhex('0f9188f13cb7b2c71f2a335e3a4fc328bf5beb436012afca590b1a11466e2206')[::-1]
def low_bits_header():
    # A Header off regtest genesis claiming bits 0x01010000: a target of one,
    # worth 2^255 of work, which no hash of this Header can ever meet (#281).
    return (struct.pack('<i', 1) + REGTEST_GENESIS + b'\0'*32
            + struct.pack('<III', int(time.time()), 0x01010000, 0))
def headers_payload(headers):
    return bytes([len(headers)]) + b''.join(h + b'\0' for h in headers)
def command_of(frame):
    return frame[4:16].rstrip(b'\0').decode(errors='replace') if len(frame) >= 16 else ''
def frames(conn):
    # One frame at a time, however many a read returns: a node sends verack,
    # getaddr and getheaders back to back, and a reader that takes each recv
    # as one frame sees only the first.
    buf = b''
    while True:
        while len(buf) >= 24:
            length = struct.unpack('<I', buf[16:20])[0]
            if len(buf) < 24 + length: break
            yield buf[:24 + length]; buf = buf[24 + length:]
        chunk = conn.recv(65536)
        if not chunk: return
        buf += chunk
def answer_getheaders(conn, payload, then=b'', on='getheaders'):
    # Announce the Headers unasked, then answer every getheaders with them, so
    # the follow loop's catch-up asks this Peer and gets the same lie back.
    # `then` is sent once, on the first Message named by `on`: what the liar
    # really came to say, delivered when the node is where it should hear it.
    conn.sendall(msg('headers', payload))
    conn.settimeout(120)
    try:
        for frame in frames(conn):
            print('liar: got', command_of(frame), file=sys.stderr, flush=True)
            if command_of(frame) == 'getheaders': conn.sendall(msg('headers', payload))
            if command_of(frame) == on and then:
                conn.sendall(then); then = b''; print('liar: sent the lie', file=sys.stderr, flush=True)
    except socket.timeout:
        return
GENESIS_COINBASE = bytes.fromhex(
    '01000000010000000000000000000000000000000000000000000000000000000000000000ffffffff4d04ffff001d0104455468652054696d65732030332f4a616e2f32303039204368616e63656c6c6f72206f6e206272696e6b206f66207365636f6e64206261696c6f757420666f722062616e6b73ffffffff0100f2052a01000000434104678afdb0fe5548271967f1a67130b7105cd6a828e03909a67962e0ea1f61deb649f6bc3f4cef38c4f35504e51ec112de5c384df7ba0b8d578a4c702b6bf11d5fac00000000')
def header_of(cli, block_hash):
    # The honest Header for a Block Id, from Core, so the body can be wrong
    # under a Header that is right (#283). `cli` is the bitcoin-cli command.
    import subprocess
    return bytes.fromhex(subprocess.check_output(cli.split() + ['getblockheader', block_hash, 'false']).decode().strip())
def rpc(cli, *args):
    import subprocess
    return subprocess.check_output(cli.split() + list(args)).decode().strip()
def honest_headers(cli, getheaders_payload):
    # Core's own Headers after the first Locator entry we hold, so a node
    # that asks this Peer for Headers gets the truth and then asks it for
    # bodies -- which is the whole point.
    import json
    count = getheaders_payload[4]; first = getheaders_payload[5:37][::-1].hex()
    height = json.loads(rpc(cli, 'getblockheader', first, 'true'))['height']
    tip = int(rpc(cli, 'getblockcount'))
    return headers_payload([bytes.fromhex(rpc(cli, 'getblockheader', rpc(cli, 'getblockhash', str(h)), 'false')) for h in range(height + 1, min(tip, height + 2000) + 1)])
def wrong_bodies(conn, cli):
    # Answer every getdata for a Block with its real Header and a body that
    # is one coinbase -- mainnet's genesis coinbase -- which hashes to the
    # Block Id asked for and to nothing the Header commits to.
    conn.settimeout(120)
    try:
        for frame in frames(conn):
            cmd = command_of(frame)
            print('liar: got', cmd, file=sys.stderr, flush=True)
            if cmd == 'getheaders':
                conn.sendall(msg('headers', honest_headers(cli, frame[24:])))
            if cmd == 'getdata':
                payload = frame[24:]
                count = payload[0]; at = 1
                for _ in range(count):
                    kind = struct.unpack('<I', payload[at:at+4])[0]; h = payload[at+4:at+36]; at += 36
                    if kind & 2:
                        block_hash = h[::-1].hex()
                        conn.sendall(msg('block', header_of(cli, block_hash) + b'\x01' + GENESIS_COINBASE))
                        print('liar: sent a wrong body for', block_hash, file=sys.stderr, flush=True)
    except (socket.timeout, OSError):
        return                                        # dropped, as it should be
REGTEST_GENESIS_HEADER = bytes.fromhex(
    '0100000000000000000000000000000000000000000000000000000000000000000000003b'
    'a3edfd7a7b12b27ac72c3e67768f617fc81bc3888a51323a9fb8aa4b1e5e4adae5494dffff'
    '7f2002000000')
def header_flood(conn):
    # A Peer that says the chain moved, for ever, having spent nothing (#300).
    # Two claims in turn, both free to make: a headers with no Headers in it
    # at all, and a headers carrying regtest genesis -- a Header every node
    # holds, so it names nothing the tree has not placed. Each one used to be
    # answered with a whole catch-up: the Header round trip, the realignment,
    # the body walk and the Set build. Every getheaders is answered with
    # nothing, so a catch-up that does start against this Peer moves nothing
    # and quiets it.
    conn.settimeout(1)
    sent = 0
    started = time.time()
    while time.time() - started < 90:
        payload = headers_payload([] if sent % 2 else [REGTEST_GENESIS_HEADER])
        try:
            conn.sendall(msg('headers', payload))
        except OSError:
            print('liar: dropped after', sent, 'claims', file=sys.stderr, flush=True)
            return
        sent += 1
        try:
            for frame in frames(conn):
                if command_of(frame) == 'getheaders':
                    conn.sendall(msg('headers', headers_payload([])))
                break
        except (socket.timeout, OSError):
            pass
        time.sleep(0.5)
    print('liar: sent', sent, 'claims', file=sys.stderr, flush=True)
def addr_payload(n, seed):
    # n routable IPv4 addresses, distinct per seed, as one addr Message.
    out = bytes([0xfd]) + struct.pack('<H', n)
    for i in range(n):
        v = seed * 100000 + i
        out += struct.pack('<I', 0) + struct.pack('<Q', 1) + b'\0'*10 + b'\xff\xff' + bytes([8, (v >> 16) & 255, (v >> 8) & 255, v & 255]) + struct.pack('>H', 8333)
    return out
def serve(port, mode):
    global AGENT
    if mode == 'escape':
        # A user agent that clears the terminal and retitles the window
        # (#293): ESC [ 2 J, then OSC 0 ; pwned BEL, then a name.
        AGENT = b'\x1b[2J\x1b]0;pwned\x07/liar:1/'
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('127.0.0.1', port)); s.listen(1)
    conn, addr = s.accept()
    conn.recv(4096)                                   # their version
    conn.sendall(msg('version', version_payload()))
    conn.sendall(msg('verack', b''))
    conn.recv(4096)                                   # their verack
    time.sleep(1)
    if mode == 'checksum':
        conn.sendall(msg('ping', struct.pack('<Q', 7), corrupt=True))
    elif mode == 'network':
        bad = bytes([0xf9,0xbe,0xb4,0xd9]) + b'ping'.ljust(12, b'\0') + struct.pack('<I', 0) + hashlib.sha256(hashlib.sha256(b'').digest()).digest()[:4]
        conn.sendall(bad)
    elif mode == 'hugetx':
        # Thirteen bytes: version 1, then an Input count of 2^64-1 and nothing
        # behind it. A decoder that trusts the count spins for ever (#282).
        # Sent once the node is in its listen loop -- a Message kept during
        # a catch-up is acted on only if it is an addr -- which the getaddr it sends on joining
        # announces; then the liar stays connected and answers getheaders
        # with nothing.
        answer_getheaders(conn, headers_payload([]), then=msg('tx', struct.pack('<i', 1) + b'\xff' + b'\xff'*8), on='getaddr')
        return
    elif mode == 'escape':
        answer_getheaders(conn, headers_payload([]))
        return
    elif mode == 'addrflood':
        # Ten thousand Candidates in ten Messages (#291). A Book without a
        # cap took every one; one with a per-source cap keeps 256 of them.
        # Sent once the node is listening (on its getaddr), so each Message
        # is answered with a `named` line rather than folded in silently.
        flood = b''.join(msg('addr', addr_payload(1000, i)) for i in range(10))
        answer_getheaders(conn, headers_payload([]), then=flood, on='getaddr')
        return
    elif mode == 'wrongbody':
        wrong_bodies(conn, sys.argv[3])
        return
    elif mode == 'lowbits':
        answer_getheaders(conn, headers_payload([low_bits_header()]))
        return
    elif mode == 'headerflood':
        header_flood(conn)
        return
    time.sleep(120)
serve(int(sys.argv[1]), sys.argv[2])
