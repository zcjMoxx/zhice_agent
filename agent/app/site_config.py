"""Public site metadata loaded from the private runtime configuration."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import yaml


class SiteConfigurationError(RuntimeError):
    """Raised when the public site configuration is structurally invalid."""


_RECORD_CODE_RE = re.compile(r"^[0-9]{14}$")
_DNS_HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


@dataclass(frozen=True)
class PublicSecurityRecordConfig:
    """One optional public-security record and its exact display hosts."""

    enabled: bool = False
    code: str = ""
    label: str = ""
    allowed_hosts: tuple[str, ...] = field(default_factory=tuple)

    def for_host(self, host: str | None) -> dict[str, str] | None:
        """Return the anonymous-safe projection only for an allowed host."""

        normalized = _normalize_request_host(host)
        if not self.enabled or not normalized or normalized not in self.allowed_hosts:
            return None
        return {
            "code": self.code,
            "label": self.label,
            "url": (
                "https://beian.mps.gov.cn/#/query/webSearch?code="
                f"{quote(self.code, safe='')}"
            ),
        }


@dataclass(frozen=True)
class SiteConfig:
    """Public site metadata that is safe to project selectively."""

    public_security_record: PublicSecurityRecordConfig = field(
        default_factory=PublicSecurityRecordConfig
    )


def load_site_config(config_dir: Path | str) -> SiteConfig:
    """Load and validate the optional ``site`` section from ``config.yml``."""

    path = Path(config_dir).expanduser().resolve() / "config.yml"
    if not path.is_file():
        return SiteConfig()
    try:
        root = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise SiteConfigurationError("Site runtime configuration cannot be read.") from exc
    if root is None:
        return SiteConfig()
    if not isinstance(root, dict):
        raise SiteConfigurationError("Runtime config root must be an object.")
    site = root.get("site")
    if site is None:
        return SiteConfig()
    if not isinstance(site, dict):
        raise SiteConfigurationError("Site configuration must be an object.")
    record = site.get("public_security_record")
    if record is None:
        return SiteConfig()
    if not isinstance(record, dict):
        raise SiteConfigurationError("Public security record must be an object.")
    enabled = record.get("enabled", False)
    if not isinstance(enabled, bool):
        raise SiteConfigurationError("Public security record enabled must be boolean.")
    if not enabled:
        return SiteConfig()

    code = _required_text(record.get("code"), "code")
    label = _required_text(record.get("label"), "label")
    if not _RECORD_CODE_RE.fullmatch(code):
        raise SiteConfigurationError("Public security record code must contain 14 digits.")
    if len(label) > 160 or code not in label:
        raise SiteConfigurationError(
            "Public security record label must contain its code and be at most 160 characters."
        )
    raw_hosts = record.get("allowed_hosts")
    if not isinstance(raw_hosts, list) or not raw_hosts:
        raise SiteConfigurationError(
            "Public security record allowed_hosts must be a non-empty list."
        )
    hosts: list[str] = []
    for value in raw_hosts:
        host = _normalize_configured_host(value)
        if host not in hosts:
            hosts.append(host)
    return SiteConfig(
        public_security_record=PublicSecurityRecordConfig(
            enabled=True,
            code=code,
            label=label,
            allowed_hosts=tuple(hosts),
        )
    )


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SiteConfigurationError(
            f"Public security record {field_name} must be non-empty text."
        )
    return value.strip()


def _normalize_request_host(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().lower().rstrip(".")


def _normalize_configured_host(value: object) -> str:
    if not isinstance(value, str):
        raise SiteConfigurationError("Public security record host must be text.")
    host = _normalize_request_host(value)
    if not host or any(character in host for character in "/:#?@"):
        raise SiteConfigurationError(
            "Public security record hosts must not include schemes, ports, paths, or wildcards."
        )
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not _DNS_HOST_RE.fullmatch(host):
            raise SiteConfigurationError("Public security record host is invalid.")
    return host
