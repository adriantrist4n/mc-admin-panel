#!/usr/bin/env python3
"""Test del cliente RCON (rcon.py) contra un servidor RCON falso local
(protocolo Source mínimo), para no depender de un servidor de Minecraft real."""
import os
import socket
import struct
import sys
import threading
import unittest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
import rcon


def _packet(req_id, pkt_type, payload=b""):
    body = struct.pack("<ii", req_id, pkt_type) + payload + b"\x00\x00"
    return struct.pack("<i", len(body)) + body


class FakeRconServer:
    """Acepta una conexión, responde a la autenticación y a un comando con
    un texto fijo, igual que un servidor de Minecraft real haría."""

    def __init__(self, response_text):
        self.response_text = response_text
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        with conn:
            # auth
            data = conn.recv(4096)
            total_len = struct.unpack("<i", data[:4])[0]
            req_id, _pkt_type = struct.unpack("<ii", data[4:12])
            conn.send(_packet(req_id, 2))  # SERVERDATA_AUTH_RESPONSE, éxito

            # comando
            data = conn.recv(4096)
            total_len = struct.unpack("<i", data[:4])[0]
            req_id, _pkt_type = struct.unpack("<ii", data[4:12])
            # _packet ya añade los 2 bytes finales del protocolo (terminador
            # del body + string vacío); no hay que añadir un \x00 extra aquí.
            conn.send(_packet(req_id, 0, self.response_text.encode("utf-8")))

    def close(self):
        self.thread.join(timeout=2)
        self.sock.close()


class TestRconCommand(unittest.TestCase):
    def test_successful_roundtrip(self):
        srv = FakeRconServer("There are 0 of a max of 8 players online: ")
        try:
            result = rcon.rcon_command("127.0.0.1", srv.port, "hunter2", "list")
        finally:
            srv.close()
        self.assertEqual(result, "There are 0 of a max of 8 players online: ")

    def test_connection_refused_raises(self):
        # Ningún servidor escuchando en este puerto: debe propagar el error
        # de socket (es el caller -dashboard.py/rcon_cmd en admin.sh- quien
        # decide capturarlo), no devolver un valor silencioso incorrecto.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            free_port = probe.getsockname()[1]
        with self.assertRaises(OSError):
            rcon.rcon_command("127.0.0.1", free_port, "hunter2", "list")


if __name__ == "__main__":
    unittest.main()
