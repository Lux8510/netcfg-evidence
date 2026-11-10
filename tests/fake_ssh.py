"""Tiny in-process SSH server that answers allow-listed network CLI commands."""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
BASELINE = (ROOT / "samples/frr/r1.conf").read_text(encoding="utf-8")


class FakeDeviceState:
    def __init__(self, username: str = "netops", password: str = "secret"):
        self.username = username
        self.password = password
        self.outputs = {
            "show running-config": BASELINE,
            "show ip ospf neighbor": "Neighbor ID     Pri State           Up Time         Address         Interface\n10.255.1.2        1 Full/Backup     00:12:34        10.12.0.2       eth2:10.12.0.1\n",
            "show ip interface brief": "Interface    Status  Protocol\neth2         up      up\n",
        }
        self.commands: list[str] = []
        self.delay = 0.0
        self.stop = False
        self.key = paramiko.RSAKey.generate(2048)


class _Server(paramiko.ServerInterface):
    def __init__(self, state: FakeDeviceState):
        self.state = state

    def check_auth_password(self, username, password):
        if username == self.state.username and password == self.state.password:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command):
        text = command.decode("utf-8", errors="replace")
        self.state.commands.append(text)
        output = self.state.outputs.get(text)
        def respond():
            time.sleep(self.state.delay or 0.05)
            try:
                if output is None:
                    channel.send_stderr(b"% Invalid input detected at '^' marker.\n")
                    channel.send_exit_status(1)
                else:
                    channel.sendall(output.encode())
                    channel.send_exit_status(0)
            finally:
                channel.close()

        threading.Thread(target=respond, daemon=True).start()
        return True


def _handle(state: FakeDeviceState, client: socket.socket) -> None:
    transport = paramiko.Transport(client)
    transport.add_server_key(state.key)
    try:
        transport.start_server(server=_Server(state))
        while not state.stop:
            channel = transport.accept(1)
            if channel is None:
                continue
            while channel.active and not state.stop:
                time.sleep(0.05)
    except Exception:
        pass
    finally:
        transport.close()


def _serve(state: FakeDeviceState, listener: socket.socket) -> None:
    while not state.stop:
        try:
            client, _ = listener.accept()
        except OSError:
            return
        threading.Thread(target=_handle, args=(state, client), daemon=True).start()


def start(state: FakeDeviceState) -> int:
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)
    state.listener = listener  # type: ignore[attr-defined]
    threading.Thread(target=_serve, args=(state, listener), daemon=True).start()
    return listener.getsockname()[1]


def stop(state: FakeDeviceState) -> None:
    state.stop = True
    listener = getattr(state, "listener", None)
    if listener is not None:
        listener.close()
