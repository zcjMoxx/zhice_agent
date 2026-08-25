from __future__ import annotations

from pathlib import Path

import pytest

from agent.app.site_config import SiteConfigurationError, load_site_config


def test_site_config_defaults_to_disabled_when_section_is_absent(tmp_path: Path) -> None:
    config = load_site_config(tmp_path)

    assert config.public_security_record.enabled is False
    assert config.public_security_record.for_host("example.test") is None


def test_site_config_projects_record_only_for_exact_allowed_host(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
site:
  public_security_record:
    enabled: true
    code: "00000000000000"
    label: 测试公安备案00000000000000号
    allowed_hosts:
      - Example.Test
      - 127.0.0.1
""",
    )

    record = load_site_config(tmp_path).public_security_record

    assert record.allowed_hosts == ("example.test", "127.0.0.1")
    assert record.for_host("EXAMPLE.TEST.") == {
        "code": "00000000000000",
        "label": "测试公安备案00000000000000号",
        "url": "https://beian.mps.gov.cn/#/query/webSearch?code=00000000000000",
    }
    assert record.for_host("other.example.test") is None


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            "enabled: true\ncode: invalid\nlabel: invalid\nallowed_hosts: [example.test]",
            "14 digits",
        ),
        (
            "enabled: true\ncode: '00000000000000'\nlabel: missing\n"
            "allowed_hosts: [example.test]",
            "label must contain",
        ),
        (
            "enabled: true\ncode: '00000000000000'\n"
            "label: 测试00000000000000\nallowed_hosts: ['https://example.test']",
            "must not include",
        ),
    ],
)
def test_site_config_rejects_invalid_enabled_record(
    tmp_path: Path,
    body: str,
    message: str,
) -> None:
    _write_config(
        tmp_path,
        "site:\n  public_security_record:\n"
        + "\n".join(f"    {line}" for line in body.splitlines()),
    )

    with pytest.raises(SiteConfigurationError, match=message):
        load_site_config(tmp_path)


def _write_config(config_dir: Path, suffix: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yml").write_text(
        "schema_version: 1\n" + suffix.strip() + "\n",
        encoding="utf-8",
    )
