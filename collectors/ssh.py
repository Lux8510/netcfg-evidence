"""Read-only SSH collector.

Only the commands in ALLOWED_COMMANDS are ever executed; there is no path for
arbitrary input. Credentials come from the server environment or a key file and
are never logged or returned.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import paramiko

ALLOWED_COMMANDS: tuple[str, ...] = (
    "show running-config",
    "show ip ospf neighbor",
    "show ip interface brief",
)
CONFIG_COMMAND = "show running-config"


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
    password: str | None = None
    key_path: str | None = None
    strict_host_key: bool = False


def _clean(output: str) -> str:
    text = output.replace("\r\n", "\n").replace("\r", "\n")
    # Some devices paginate with --More--; strip the marker and its backspaces.
    while "--More--" in text:
        head, _, tail = text.partition("--More--")
        text = head + tail.lstrip("\b \n")
    return "\n".join(line.rstrip() for line in text.splitlines()).strip() + "\n"


def collect(target: Target, commands: tuple[str, ...] = ALLOWED_COMMANDS) -> dict:
    unknown = [command for command in commands if command not in ALLOWED_COMMANDS]
    if unknown:
        raise CollectionError("COMMAND_NOT_ALLOWED", f"命令不在只读白名单中：{unknown[0]}")
    started = time.perf_counter()
    client = paramiko.SSHClient()
    if target.strict_host_key:
        client.load_system_host_keys()
    else:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs: dict = {
        "hostname": target.host,
        "port": target.port,
        "username": target.username,
        "timeout": target.timeout,
        "banner_timeout": target.timeout,
        "auth_timeout": target.timeout,
        "allow_agent": False,
        "look_for_keys": target.key_path is None,
    }
    if target.password:
        connect_kwargs["password"] = target.password
    if target.key_path:
        connect_kwargs["key_filename"] = target.key_path

    try:
        client.connect(**connect_kwargs)
    except paramiko.AuthenticationException as exc:
        raise CollectionError("SSH_AUTH_FAILED", "SSH 认证失败") from exc
    except Exception as exc:  # noqa: BLE001 - network and host-key errors
        raise CollectionError("SSH_CONNECT_FAILED", f"SSH 连接失败：{type(exc).__name__}") from exc

    observations = []
    try:
        for command in commands:
            try:
                _, stdout, stderr = client.exec_command(command, timeout=target.timeout)
                output = stdout.read().decode("utf-8", errors="replace")
                error = stderr.read().decode("utf-8", errors="replace")
                status = stdout.channel.recv_exit_status()
            except Exception as exc:  # noqa: BLE001
                raise CollectionError("SSH_COMMAND_FAILED", f"命令执行失败：{command}") from exc
            observations.append({
                "command": command,
                "exit_code": status,
                "stdout": _clean(output),
                "stderr": error.strip(),
            })
            if status != 0:
                raise CollectionError("SSH_COMMAND_FAILED", f"设备返回非零退出码：{command}")
    finally:
        client.close()

    config = next((item["stdout"] for item in observations if item["command"] == CONFIG_COMMAND), "")
    if not config.strip():
        raise CollectionError("SSH_EMPTY_CONFIG", "没有采集到配置文本")
    return {
        "config_text": config,
        "commands": observations,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def target_from_env(host: str, port: int, username: str, credential_ref: str | None,
                    key_path: str | None) -> Target:
    password = None
    if credential_ref:
        password = os.environ.get(credential_ref, "")
    return Target(
        host=host,
        port=port,
        username=username,
        timeout=float(os.environ.get("NETCFG_SSH_TIMEOUT", "15")),
        password=password or None,
        key_path=key_path or None,
        strict_host_key=os.environ.get("NETCFG_SSH_STRICT_HOST_KEY", "false").lower() == "true",
    )
