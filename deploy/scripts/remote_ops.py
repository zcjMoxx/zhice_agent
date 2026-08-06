from __future__ import annotations

import argparse
import importlib
import json
import posixpath
import re
import shlex
import sys
import time
import warnings
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.utils import CryptographyDeprecationWarning

with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        category=CryptographyDeprecationWarning,
        module=r"^paramiko(?:\.|$)",
    )
    paramiko = importlib.import_module("paramiko")

SCRIPT_NAMES = ("deploy.sh", "status.sh", "logs.sh", "stop.sh", "restart.sh")
PLACEHOLDER_RE = re.compile(r"[^\x00-\x7f]")


class RemoteOpsError(RuntimeError):
    pass


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RemoteOpsError(f"Invalid cloud deployment JSON: {path}") from exc

    remote_ops_dir = config.get("RemoteOpsDir")
    required = {
        "Registry": config.get("Registry"),
        "SshHost": config.get("SshHost"),
        "SshUser": config.get("SshUser"),
        "SshPassword": config.get("SshPassword"),
        "RemoteOpsDir": remote_ops_dir,
        "PublicUrl": config.get("PublicUrl"),
        "Port": config.get("Port"),
    }
    for key, value in required.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            raise RemoteOpsError(f"Missing cloud deployment config value: {key}")
        if key == "SshPassword" and str(value) == "云服务器SSH登录密码":
            raise RemoteOpsError(f"Replace the Chinese placeholder in cloud deployment config: {key}")
        if key not in {"Port", "SshPassword"} and PLACEHOLDER_RE.search(str(value)):
            raise RemoteOpsError(f"Replace the Chinese placeholder in cloud deployment config: {key}")

    password = str(required["SshPassword"])
    if any(character in password for character in ("\x00", "\r", "\n")):
        raise RemoteOpsError("SSH password must not contain NUL or line breaks")

    remote_ops_dir = str(remote_ops_dir).strip().rstrip("/")
    if not re.fullmatch(r"/[A-Za-z0-9._/-]+", remote_ops_dir) or ".." in remote_ops_dir.split("/"):
        raise RemoteOpsError(f"Invalid remote operations directory: {remote_ops_dir}")
    required["RemoteOpsDir"] = remote_ops_dir
    return required


def public_config(config: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in config.items() if key != "SshPassword"}


def connect(config: dict[str, Any]) -> paramiko.SSHClient:
    known_hosts = Path.home() / ".ssh" / "known_hosts"
    if not known_hosts.is_file():
        raise RemoteOpsError(
            f"SSH known_hosts file is missing: {known_hosts}. Verify the host key before deployment."
        )
    client = paramiko.SSHClient()
    client.load_host_keys(str(known_hosts))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(
            hostname=str(config["SshHost"]),
            username=str(config["SshUser"]),
            password=str(config["SshPassword"]),
            look_for_keys=False,
            allow_agent=False,
            timeout=15,
            auth_timeout=15,
            banner_timeout=15,
        )
    except Exception as exc:
        raise RemoteOpsError(f"SSH connection failed: {exc}") from exc
    return client


def run(client: paramiko.SSHClient, command: str) -> tuple[str, str]:
    _stdin, stdout, stderr = client.exec_command(command)
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    if exit_code != 0:
        raise RemoteOpsError(err.strip() or out.strip() or f"Remote command failed ({exit_code})")
    return out, err


def upload_release(
    client: paramiko.SSHClient, scripts_dir: Path, remote_ops_dir: str, release_id: str
) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", release_id):
        raise RemoteOpsError(f"Invalid remote operations release id: {release_id}")
    release_dir = posixpath.join(remote_ops_dir, "releases", release_id)
    run(client, f"mkdir -p -- {shlex.quote(release_dir)}")
    with client.open_sftp() as sftp:
        for name in SCRIPT_NAMES:
            local_path = scripts_dir / name
            if not local_path.is_file():
                raise RemoteOpsError(f"Missing remote operations script: {local_path}")
            temp_path = posixpath.join(release_dir, f".{name}.upload")
            final_path = posixpath.join(release_dir, name)
            sftp.put(str(local_path), temp_path)
            sftp.chmod(temp_path, 0o700)
            sftp.rename(temp_path, final_path)

    checks = " && ".join(
        f"sh -n {shlex.quote(posixpath.join(release_dir, name))}" for name in SCRIPT_NAMES
    )
    run(client, checks)
    current = posixpath.join(remote_ops_dir, "current")
    pending = posixpath.join(remote_ops_dir, f".current-{release_id}")
    target = posixpath.join("releases", release_id)
    run(
        client,
        f"ln -sfn {shlex.quote(target)} {shlex.quote(pending)} && "
        f"mv -Tf {shlex.quote(pending)} {shlex.quote(current)}",
    )
    return current


def sudo_deploy(
    client: paramiko.SSHClient,
    password: str,
    current_dir: str,
    digest: str,
    port: int,
    timeout_seconds: float = 300,
) -> tuple[str, str]:
    inner = (
        f"sh {shlex.quote(posixpath.join(current_dir, 'deploy.sh'))} "
        f"{shlex.quote(digest)} {port} && "
        f"sh {shlex.quote(posixpath.join(current_dir, 'status.sh'))}"
    )
    command = f"sudo -S -p '' sh -c {shlex.quote(inner)}"
    transport = client.get_transport()
    if transport is None:
        raise RemoteOpsError("SSH transport is unavailable")
    channel = transport.open_session()
    channel.get_pty()
    channel.exec_command(command)
    channel.sendall((password + "\n").encode("utf-8"))
    channel.shutdown_write()
    chunks: list[bytes] = []
    error_chunks: list[bytes] = []
    deadline = time.monotonic() + timeout_seconds
    while not channel.exit_status_ready():
        if channel.recv_ready():
            chunks.append(channel.recv(32768))
        if channel.recv_stderr_ready():
            error_chunks.append(channel.recv_stderr(32768))
        if time.monotonic() >= deadline:
            channel.close()
            raise RemoteOpsError(
                f"Remote sudo deployment timed out after {timeout_seconds:g} seconds"
            )
        time.sleep(0.05)
    while channel.recv_ready():
        chunks.append(channel.recv(32768))
    while channel.recv_stderr_ready():
        error_chunks.append(channel.recv_stderr(32768))
    exit_code = channel.recv_exit_status()
    out = b"".join(chunks).decode("utf-8", errors="replace").replace(password, "[REDACTED]")
    err = (
        b"".join(error_chunks)
        .decode("utf-8", errors="replace")
        .replace(password, "[REDACTED]")
    )
    if exit_code != 0:
        raise RemoteOpsError(err.strip() or out.strip() or "Remote deployment failed")
    return out, err


def verify_public_health(client: paramiko.SSHClient, public_url: str) -> str:
    public_url = public_url.rstrip("/")
    parsed = urlsplit(public_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise RemoteOpsError("PublicUrl must be an HTTPS origin without credentials or path")
    health_url = f"{public_url}/health"
    command = (
        "curl --fail --silent --show-error --max-time 20 -- "
        f"{shlex.quote(health_url)}"
    )
    out, _err = run(client, command)
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        raise RemoteOpsError("Remote public health check returned invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise RemoteOpsError("Remote public health check returned unexpected status")
    return health_url


def main() -> int:
    parser = argparse.ArgumentParser(description="Secure ZhiCe-Agent remote operations helper")
    parser.add_argument("--config", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("inspect-config")
    subparsers.add_parser("check")
    deploy_parser = subparsers.add_parser("deploy")
    deploy_parser.add_argument("--scripts-dir", type=Path, required=True)
    deploy_parser.add_argument("--release-id", required=True)
    deploy_parser.add_argument("--digest", required=True)
    deploy_parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    password = ""
    try:
        config = load_config(args.config.resolve())
        password = str(config["SshPassword"])
        if args.action == "inspect-config":
            print(json.dumps(public_config(config), ensure_ascii=True))
            return 0
        if args.action == "check":
            client = connect(config)
            client.close()
            print("SSH host key and password authentication passed")
            return 0
        if not re.fullmatch(r"[^\s]+/zhice-agent@sha256:[0-9a-f]{64}", args.digest):
            raise RemoteOpsError("Invalid immutable image digest")
        if not 1 <= args.port <= 65535:
            raise RemoteOpsError("Invalid remote host port")
        client = connect(config)
        try:
            current = upload_release(
                client, args.scripts_dir.resolve(), str(config["RemoteOpsDir"]), args.release_id
            )
            out, err = sudo_deploy(client, password, current, args.digest, args.port)
            verified_health_url = verify_public_health(client, str(config["PublicUrl"]))
        finally:
            client.close()
        if out:
            sys.stdout.write(out)
        if err:
            sys.stderr.write(err)
        print(f"Remote public HTTPS health passed: {verified_health_url}")
        return 0
    except Exception as exc:
        message = str(exc)
        if password:
            message = message.replace(password, "[REDACTED]")
        print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
