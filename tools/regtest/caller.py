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
def version_payload():
    return (struct.pack('<iQq', 70016, 0, int(time.time())) + b'\0'*26 + b'\0'*26
            + struct.pack('<Q', 4242) + b'\x0a/caller:1/' + struct.pack('<i', 0) + b'\0')
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
