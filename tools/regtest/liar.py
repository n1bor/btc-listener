import socket, struct, hashlib, threading, time, sys
MAGIC = bytes([0xfa,0xbf,0xb5,0xda])           # regtest
def msg(cmd, payload, corrupt=False):
    c = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    if corrupt: c = bytes([(c[0]+1) % 256]) + c[1:]
    return MAGIC + cmd.encode().ljust(12, b'\0') + struct.pack('<I', len(payload)) + c + payload
def version_payload():
    return (struct.pack('<iQq', 70016, 0, int(time.time())) + b'\0'*26 + b'\0'*26
            + struct.pack('<Q', 12345) + b'\x08/liar:1/' + struct.pack('<i', 0) + b'\0')
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
def answer_getheaders(conn, payload):
    # Announce the Header unasked, then answer every getheaders with it, so
    # the follow loop's catch-up asks this Peer and gets the same lie back.
    conn.sendall(msg('headers', payload))
    conn.settimeout(120)
    try:
        while True:
            frame = conn.recv(65536)
            if not frame: return
            if command_of(frame) == 'getheaders': conn.sendall(msg('headers', payload))
    except socket.timeout:
        return
def serve(port, mode):
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
    elif mode == 'lowbits':
        answer_getheaders(conn, headers_payload([low_bits_header()]))
        return
    time.sleep(120)
serve(int(sys.argv[1]), sys.argv[2])
