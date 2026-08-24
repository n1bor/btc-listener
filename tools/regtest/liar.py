import socket, struct, hashlib, threading, time, sys
MAGIC = bytes([0xfa,0xbf,0xb5,0xda])           # regtest
def msg(cmd, payload, corrupt=False):
    c = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    if corrupt: c = bytes([(c[0]+1) % 256]) + c[1:]
    return MAGIC + cmd.encode().ljust(12, b'\0') + struct.pack('<I', len(payload)) + c + payload
def version_payload():
    return (struct.pack('<iQq', 70016, 0, int(time.time())) + b'\0'*26 + b'\0'*26
            + struct.pack('<Q', 12345) + b'\x08/liar:1/' + struct.pack('<i', 0) + b'\0')
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
    time.sleep(120)
serve(int(sys.argv[1]), sys.argv[2])
