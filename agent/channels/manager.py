"""Lifecycle and aggregate status for optional channel adapters."""

from __future__ import annotations

from agent.protocols.capability import CapabilityStatus


class ChannelManager:
    def __init__(self, adapters=()):
        self.adapters = {adapter.key: adapter for adapter in adapters}
        self._started = False
        self._failures: dict[str, str] = {}

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        for key, adapter in self.adapters.items():
            try:
                adapter.start()
            except Exception as exc:  # noqa: BLE001 - optional channel must not stop Gateway.
                self._failures[key] = type(exc).__name__

    def stop(self) -> None:
        for adapter in reversed(tuple(self.adapters.values())):
            adapter.stop()
        self._started = False

    def statuses(self) -> dict[str, CapabilityStatus]:
        statuses = {key: adapter.status() for key, adapter in self.adapters.items()}
        for key, error_code in tuple(self._failures.items()):
            current = statuses.get(key)
            if current is not None and current.available:
                del self._failures[key]
                continue
            statuses[key] = CapabilityStatus(
                name=key,
                state="unavailable",
                code="CHANNEL_START_FAILED",
                message="The channel account failed to start.",
                details={"error_type": error_code},
            )
        return statuses
