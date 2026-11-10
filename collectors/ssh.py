"""Read-only SSH collector.

Only the commands in ALLOWED_COMMANDS are ever executed; there is no path for
arbitrary input. Credentials come from the server environment or a key file and
are never logged, echoed, or included in repr().
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import paramiko

ALLOWED_COMMANDS: tuple[str, ...] = (
    "show running-config",
    "show ip ospf neighbor",
    "show ip interface brief",
)
CONFIG_COMMAND = "show running-config"
MAX_OUTPUT_BYTES = 4 * 1024 * 1024


class CollectionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Target:
    host: str
    port: int
    username: str
    timeout: float = 15
    password: str | None = field(default=None, repr=False)
    key_path: str | None = None
    strict_host_key: bool = False


def _clean(output: str) -> str:
    text = output.replace("\r\n", "\n").replace("\r", "\n")
    while "--More--" in text:
        head, _, tail = text.partition("--More--")
        text = head + tail.lstrip("\b \n")
    return "\n".join(line.rstrip() for line in text.splitlines()).strip() + "\n"


def _read_command(channel, deadline: float, command: str) -> tuple[str, str, int]:
    stdout = bytearray()
    stderr = bytearray()
    channel.settimeout(0.2)
    while True:
        now = time.monotonic()
        if now >= deadline:
            raise CollectionError("SSH_TIMEOUT", f"命令超时：{command}")
        if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
            break
        if channel.recv_ready():
            stdout.extend(channel.recv(65536))
        if channel.recv_stderr_ready():
            stderr.extend(channel.recv_stderr(65536))
        if len(stdout) + len(stderr) > MAX_OUTPUT_BYTES:
            raise CollectionError("SSH_OUTPUT_TOO_LARGE", f"输出超过上限：{command}")
        if not channel.recv_ready() and not channel.recv_stderr_ready() and not channel.exit_status_ready():
            time.sleep(0.01)
    try:
        status = channel.recv_exit_status()
    except Exception:  # noqa: BLE001 - some devices never send an exit status
        status = 0
    return _clean(stdout.decode("utf-8", errors="replace")), stderr.decode("utf-8", errors="replace").strip(), status


def collect(target: Target, commands: tuple[str, ...] = ALLOWED_COMMANDS) -> dict:
    unknown = [command for command in commands if command not in ALLOWED_COMMANDS]
    if unknown:
        raise CollectionError("COMMAND_NOT_ALLOWED", f"命令不在只读白名单中：{unknown[0]}")
    started = time.perf_counter()
    deadline = time.monotonic() + target.timeout
    client = paramiko.SSHClient()
    if target.strict_host_key:
        known_hosts = os.environ.get("NETCFG_SSH_KNOWN_HOSTS")
        client.load_system_host_keys(known_hosts) if known_hosts else client.load_system_host_keys()
    else:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    observations: list[dict] = []
    try:
        connect_kwargs: dict = {
            "hostname": target.host,
            "port": target.port,
            "username": target.username,
            "timeout": max(0.1, min(target.timeout, deadline - time.monotonic())),
            "banner_timeout": target.timeout,
            "auth_timeout": target.timeout,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if target.password:
            connect_kwargs["password"] = target.password
        if target.key_path:
            connect_kwargs["key_filename"] = target.key_path

        try:
            client.connect(**connect_kwargs)
        except paramiko.AuthenticationException as exc:
            raise CollectionError("SSH_AUTH_FAILED", "SSH 认证失败") from exc
        except CollectionError:
            raise
        except Exception as exc:  # noqa: BLE001 - network and host-key errors
            raise CollectionError("SSH_CONNECT_FAILED", f"SSH 连接失败：{type(exc).__name__}") from exc

        for command in commands:
            if time.monotonic() >= deadline:
                raise CollectionError("SSH_TIMEOUT", f"总时长超限，未执行：{command}")
            try:
                channel = client.get_transport().open_session(timeout=max(0.1, deadline - time.monotonic()))
                channel.settimeout(max(0.1, deadline - time.monotonic()))
                channel.exec_command(command)
                output, error, status = _read_command(channel, deadline, command)
                channel.close()
            except CollectionError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise CollectionError("SSH_COMMAND_FAILED", f"命令执行失败：{command}") from exc
            observations.append({"command": command, "exit_code": status,
                                 "stdout": output, "stderr": error})
            if status != 0:
                raise CollectionError("SSH_COMMAND_FAILED", f"设备返回非零退出码：{command}")
    finally:
        # Covers auth failures, connect errors and command errors alike.
        client.close()

    config = next((item["stdout"] for item in observations if item["command"] == CONFIG_COMMAND), "")
    if not config.strip():
        raise CollectionError("SSH_EMPTY_CONFIG", "没有采集到配置文本")
    return {"config_text": config, "commands": observations,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1)}


def target_from_env(host: str, port: int, username: str, credential_ref: str | None,
                    key_path: str | None) -> Target:
    password = os.environ.get(credential_ref, "") if credential_ref else ""
    return Target(
        host=host,
        port=port,
        username=username,
        timeout=float(os.environ.get("NETCFG_SSH_TIMEOUT", "15")),
        password=password or None,
        key_path=key_path or None,
        strict_host_key=os.environ.get("NETCFG_SSH_STRICT_HOST_KEY", "true").lower() != "false",
    )
