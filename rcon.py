#!/usr/bin/env python3
import socket
import struct
import sys
import os

def rcon_command(host, port, password, command):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect((host, port))

    req_id = 1
    payload = password.encode("utf-8") + b"\x00"
    packet = struct.pack("<ii", req_id, 3) + payload + b"\x00\x00"
    sock.send(struct.pack("<i", len(packet)) + packet)
    data = sock.recv(4096)
    if not data or len(data) < 12:
        sock.close()
        return None

    req_id = 2
    payload = command.encode("utf-8") + b"\x00"
    packet = struct.pack("<ii", req_id, 2) + payload + b"\x00\x00"
    sock.send(struct.pack("<i", len(packet)) + packet)
    data = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) >= 4:
            total_len = struct.unpack("<i", data[:4])[0]
            if len(data) >= total_len + 4:
                break
    sock.close()

    if len(data) < 12:
        return None
    resp_payload = data[12:-2]
    return resp_payload.decode("utf-8", errors="replace")

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: rcon.py <host> <port> <password> <command>")
        sys.exit(1)
    host = sys.argv[1]
    port = int(sys.argv[2])
    password = sys.argv[3]
    command = " ".join(sys.argv[4:])
    result = rcon_command(host, port, password, command)
    if result is None:
        print("ERROR")
        sys.exit(1)
    print(result)
