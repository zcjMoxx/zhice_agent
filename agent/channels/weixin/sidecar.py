"""Bounded stdio NDJSON client for the shared Weixin Transport sidecar."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import uuid
from pathlib import Path
from queue import Queue
from typing import Callable

from agent.logging_utils import log_event

PROTOCOL_VERSION = "1"
MAX_FRAME_BYTES = 256 * 1024
sidecar_logger = logging.getLogger("zcagent.agent.channel.weixin")


class WeixinSidecarError(RuntimeError):
    pass


class WeixinSidecarClient:
    def __init__(
        self,
        *,
        node_path: str,
        entry: Path,
        workspace: Path,
        timeout_seconds: float = 10,
        process_factory=subprocess.Popen,
    ):
        self.node_path = node_path
        self.entry = entry
        self.workspace = workspace
        self.timeout_seconds = timeout_seconds
        self.process_factory = process_factory
        self._process = None
        self._pending: dict[str, Queue] = {}
        self._pending_lock = threading.Lock()
        self._handler: Callable[[dict[str, object]], None] | None = None
        self._reader: threading.Thread | None = None
        self._failure = ""
        self._lease = _WorkspaceLease(workspace / "state" / "channels" / "weixin" / "sidecar.lease")

    def set_event_handler(self, handler: Callable[[dict[str, object]], None]) -> None:
        self._handler = handler

    @property
    def failure(self) -> str:
        return self._failure

    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self._failure = ""
        self._lease.acquire()
        try:
            environment = os.environ.copy()
            environment["ZHICE_WEIXIN_STATE_DIR"] = str(
                self.workspace / "state" / "channels" / "weixin"
            )
            self._process = self.process_factory(
                [self.node_path, str(self.entry)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
                cwd=str(self.workspace),
                env=environment,
            )
            self._reader = threading.Thread(
                target=self._read_loop, name="weixin-sidecar", daemon=True
            )
            self._reader.start()
            response = self.request("hello", client="zcagent")
            if response.get("type") != "hello.ok":
                raise WeixinSidecarError("sidecar protocol handshake failed")
        except Exception:
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
            self._process = None
            self._lease.release()
            raise

    def stop(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            if process.poll() is None:
                self.request("shutdown", timeout_seconds=3)
                process.wait(timeout=3)
        except (WeixinSidecarError, subprocess.TimeoutExpired):
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        finally:
            self._process = None
            self._lease.release()

    def request(
        self, frame_type: str, *, timeout_seconds: float | None = None, **payload: object
    ) -> dict[str, object]:
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            raise WeixinSidecarError("sidecar is unavailable")
        request_id = "wx-" + uuid.uuid4().hex
        frame = {
            "protocol_version": PROTOCOL_VERSION,
            "type": frame_type,
            "request_id": request_id,
            **payload,
        }
        encoded = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_FRAME_BYTES:
            raise WeixinSidecarError("sidecar frame is too large")
        pending: Queue = Queue(maxsize=1)
        with self._pending_lock:
            if len(self._pending) >= 128:
                raise WeixinSidecarError("too many pending sidecar requests")
            self._pending[request_id] = pending
        try:
            process.stdin.write(encoded + "\n")
            process.stdin.flush()
            result = pending.get(timeout=timeout_seconds or self.timeout_seconds)
        except Exception as exc:
            raise WeixinSidecarError("sidecar request timed out") from exc
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)
        if isinstance(result, Exception):
            raise result
        if result.get("type") == "protocol.error":
            raise WeixinSidecarError(str(result.get("code") or "sidecar protocol error"))
        return result

    def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for raw in process.stdout:
                if len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
                    raise WeixinSidecarError("sidecar frame is too large")
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise WeixinSidecarError("sidecar stdout protocol corruption") from exc
                if not isinstance(frame, dict) or frame.get("protocol_version") != PROTOCOL_VERSION:
                    raise WeixinSidecarError("sidecar protocol version mismatch")
                request_id = str(frame.get("request_id") or "")
                with self._pending_lock:
                    pending = self._pending.get(request_id)
                if pending is not None:
                    pending.put(frame)
                elif self._handler is not None:
                    self._handler(frame)
        except Exception as exc:  # noqa: BLE001 - isolate an optional sidecar failure.
            self._failure = type(exc).__name__
            log_event(
                sidecar_logger,
                logging.WARNING,
                "channel.weixin.sidecar_failed",
                error_type=self._failure,
            )
            with self._pending_lock:
                queues = tuple(self._pending.values())
            for pending in queues:
                pending.put(WeixinSidecarError("sidecar reader failed"))


class _WorkspaceLease:
    """One-byte cross-process lease preventing duplicate workspace consumers."""

    def __init__(self, path: Path):
        self.path = path
        self._file = None

    def acquire(self) -> None:
        if self._file is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise WeixinSidecarError("weixin sidecar workspace lease is already held") from exc
        self._file = handle

    def release(self) -> None:
        handle = self._file
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._file = None
