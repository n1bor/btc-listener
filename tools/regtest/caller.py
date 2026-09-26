import socket, struct, hashlib, time, sys
# A caller that dials the node's served port and misbehaves during the
# Handshake (#284). liar.py is a Peer the node dials; this is the other way
# round, which is the side anybody on the internet can be.
#
#     python3 tools/regtest/caller.py 18456 silent    # connect, say nothing, hold the socket
#     python3 tools/regtest/caller.py 18456 pinger    # version, then a ping every 3 s, never verack
#     python3 tools/regtest/caller.py 18456 chatty    # version, then twelve addr frames, never verack
#     python3 tools/regtest/caller.py 18456 early     # a ping before any version
#     python3 tools/regtest/caller.py 18456 polite    # a proper Handshake, then stay connected
#     python3 tools/regtest/caller.py 18456 lurker    # a proper Handshake, then silence past the sweep (#330)
#     python3 tools/regtest/caller.py 18456 locator   # a proper Handshake, then a getheaders of 102 Ids (#280)
#     python3 tools/regtest/caller.py 18456 deaf 127.0.0.3 "$C"   # twenty getdata for a thousand Blocks, reads nothing for 45 s (#359)
#     python3 tools/regtest/caller.py 18456 echo       # sends the node its own nonce back: this node talking to itself (#280 item 16)
#     python3 tools/regtest/caller.py 18456 ancient    # protocol 209, below the 70016 floor (#280 item 16)
#     python3 tools/regtest/caller.py 18456 unwitnessed  # a light client without NODE_WITNESS: seated, served, never asked (#280 item 16)
#
# A third argument binds the source address, which is how one machine seats
# more than one inbound Peer (#333): hostLimit is one slot per host, and every
# 127.x.x.x is a different host that loopback already carries.
#
#     python3 tools/regtest/caller.py 18456 lurker 127.0.0.5
MAGIC = bytes([0xfa,0xbf,0xb5,0xda])           # regtest
def msg(cmd, payload):
    c = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return MAGIC + cmd.encode().ljust(12, b'\0') + struct.pack('<I', len(payload)) + c + payload
def version_payload(version=70016, services=0, nonce=4242):
    return (struct.pack('<iQq', version, services, int(time.time())) + b'\0'*26 + b'\0'*26
            + struct.pack('<Q', nonce) + b'\x0a/caller:1/' + struct.pack('<i', 0) + b'\0')
def their_version(s):
    # The node's version frame, read whole: header, then the payload it announces.
    s.settimeout(10)
    head = b''
    while len(head) < 24:
        got = s.recv(24 - len(head))
        if not got: return b''                        # the node hung up first
        head += got
    length = struct.unpack('<I', head[16:20])[0]
    body = b''
    while len(body) < length:
        got = s.recv(length - len(body))
        if not got: return b''
        body += got
    return body
def held(s, started, seconds):
    # Read until the node hangs up, which is the only way a caller that is
    # not sending can learn it has been dropped.
    s.settimeout(seconds)
    try:
        while s.recv(65536): pass
    except socket.timeout:
        print('caller: still connected after %.1f s' % (time.time() - started), file=sys.stderr, flush=True); return
    print('caller: dropped after %.1f s' % (time.time() - started), file=sys.stderr, flush=True)
def dial(port, mode, source=None):
    src = (source, 0) if source else None
    s = socket.create_connection(('127.0.0.1', port), source_address=src)
    started = time.time()
    try:
        if mode == 'silent':
            return held(s, started, 300)
        elif mode == 'early':
            s.sendall(msg('ping', struct.pack('<Q', 1)))
            return held(s, started, 300)
        elif mode == 'polite':
            s.sendall(msg('version', version_payload()))
            s.settimeout(10); s.recv(65536)               # their version
            s.sendall(msg('verack', b''))
            return held(s, started, 60)
        elif mode == 'lurker':
            # What a crawler is: a correct Handshake and then nothing at all,
            # for ever. Held past the twenty-minute per-Peer deadline (#330),
            # which is the only thing that takes the slot back -- the
            # pool-wide silence rule never notices one quiet Peer.
            s.sendall(msg('version', version_payload()))
            s.settimeout(10); s.recv(65536)               # their version
            s.sendall(msg('verack', b''))
            return held(s, started, 1500)
        elif mode == 'ancient':
            # A version from 2012 (#280 item 16): protocol 209 cannot serve
            # witness Blocks, and this node has nothing to ask it.
            s.sendall(msg('version', version_payload(version=209)))
            return held(s, started, 30)
        elif mode == 'unwitnessed':
            # A proper Handshake without NODE_WITNESS (#280 item 16): a light
            # client. Kept and served, and never asked for a Block -- so this
            # caller reports every command the node sends it for 40 s.
            s.sendall(msg('version', version_payload(services=1, nonce=4243)))
            their_version(s)
            s.sendall(msg('verack', b''))
            s.settimeout(40); buf = b''; seen = []
            try:
                while True:
                    chunk = s.recv(65536)
                    if not chunk: break
                    buf += chunk
                    while len(buf) >= 24:
                        length = struct.unpack('<I', buf[16:20])[0]
                        if len(buf) < 24 + length: break
                        seen.append(buf[4:16].rstrip(b'\0').decode(errors='replace')); buf = buf[24 + length:]
            except socket.timeout:
                pass
            print('caller: still connected after %.1f s; the node sent: %s' % (time.time() - started, ' '.join(seen) or 'nothing'), file=sys.stderr, flush=True)
            return
        elif mode == 'locator':
            # A proper Handshake, then one getheaders whose Locator carries
            # 102 Ids -- one over Core's MAX_LOCATOR_SZ (#280, item 15). Every
            # Id in a Locator is a Store read on the served path, and a
            # Message over the protocol's own limit is dropped by name.
            s.sendall(msg('version', version_payload()))
            s.settimeout(10); s.recv(65536)               # their version
            s.sendall(msg('verack', b''))
            locator = struct.pack('<I', 70016) + bytes([102]) + b'\x00' * (32 * 102) + b'\x00' * 32
            s.sendall(msg('getheaders', locator))
            return held(s, started, 60)
        elif mode == 'deaf':
            # A proper Handshake, then twenty getdata for the same thousand
            # Blocks and not one byte read back, for 45 s, then hang up. The
            # node serves each getdata with a blocking write; once the
            # socket buffers fill, the write holds the whole loop until the
            # far end reads or goes away (#359). The Ids come from Core, so
            # pass bitcoin-cli as the fourth argument, as liar.py takes it.
            s.sendall(msg('version', version_payload()))
            s.settimeout(10); s.recv(65536)               # their version
            s.sendall(msg('verack', b''))
            import subprocess
            cli = sys.argv[4].split()
            tip = int(subprocess.check_output(cli + ['getblockcount']).decode().strip())
            ids = [bytes.fromhex(subprocess.check_output(cli + ['getblockhash', str(h)]).decode().strip())[::-1] for h in range(max(1, tip - 999), tip + 1)]
            getdata = (bytes([len(ids)]) if len(ids) < 0xfd else b'\xfd' + struct.pack('<H', len(ids)))
            for h in ids: getdata += struct.pack('<I', 2) + h
            print('caller: asking for %d Block(s), twenty times, and reading nothing' % len(ids), file=sys.stderr, flush=True)
            for _ in range(20): s.sendall(msg('getdata', getdata))
            time.sleep(45)
            print('caller: hanging up after 45 s deaf', file=sys.stderr, flush=True)
            return
        elif mode == 'pinger':
            s.sendall(msg('version', version_payload()))
            for i in range(100):
                time.sleep(3); s.sendall(msg('ping', struct.pack('<Q', i)))
        elif mode == 'chatty':
            s.sendall(msg('version', version_payload()))
            for i in range(12):
                s.sendall(msg('addr', b'\x00'))
            return held(s, started, 300)
    except OSError as e:
        print('caller: dropped after %.1f s (%s)' % (time.time() - started, e), file=sys.stderr, flush=True)
        return
    print('caller: still connected after %.1f s' % (time.time() - started), file=sys.stderr, flush=True)
dial(int(sys.argv[1]), sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
