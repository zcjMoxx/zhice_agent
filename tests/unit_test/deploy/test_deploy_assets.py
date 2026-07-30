from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy"


def test_public_deploy_assets_are_complete() -> None:
    expected = {
        "Dockerfile",
        "docker-compose.yml",
        "README.md",
        ".gitignore",
        "scripts/build-image.ps1",
        "scripts/push-image.ps1",
        "scripts/run-local.ps1",
        "scripts/deploy.sh",
        "scripts/stop.sh",
        "scripts/status.sh",
        "scripts/logs.sh",
    }
    assert all((DEPLOY / path).is_file() for path in expected)
    assert (ROOT / "config" / ".env.example").is_file()
    assert not (DEPLOY / ".env.example").exists()


def test_private_deploy_files_are_ignored() -> None:
    patterns = (DEPLOY / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert {".env", "config.yml", "models.json"} <= set(patterns)


def test_deploy_readme_uses_current_workspace_config_and_marks_legacy_env() -> None:
    readme = (DEPLOY / "README.md").read_text(encoding="utf-8")
    assert "${workspace}/config/.env" in readme
    assert "${workspace}/config/config.yml" in readme
    assert "${workspace}/config/models.json" in readme
    assert r"C:\Users\<user>\.zhice\config\.env" in readme
    assert "legacy migration" in readme
    assert "deploy/.env` 不包含 `ZHICE_AGENT_WORKSPACE" in readme
    assert "普通 `zcagent init` 已默认" in readme
    assert "`--write-env` 仅作为兼容参数" in readme


def test_dockerfile_builds_repository_assets_and_only_private_config() -> None:
    dockerfile = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY . ." not in dockerfile
    assert "COPY agent/ ./agent/" in dockerfile
    assert "COPY prompts/ ./prompts/" in dockerfile
    assert "COPY skill_repo/ ./skill_repo/" in dockerfile
    assert "npm run build" in dockerfile
    assert "ZHICE_AGENT_SKILL_REPO=/app/skill_repo" in dockerfile
    assert "HOME=/home/zhice" in dockerfile
    assert "ZHICE_AGENT_WORKSPACE=" not in dockerfile
    assert "USER zhice" in dockerfile
    assert "deploy/.env /home/zhice/.zhice/config/.env" in dockerfile
    assert "deploy/config.yml /home/zhice/.zhice/config/config.yml" in dockerfile
    assert "deploy/models.json /home/zhice/.zhice/config/models.json" in dockerfile
    assert (
        'ENTRYPOINT ["zcagent", "--env-file", "/home/zhice/.zhice/config/.env"]'
        in dockerfile
    )
    assert 'CMD ["gateway", "--host", "0.0.0.0", "--port", "10086"]' in dockerfile


def test_compose_persists_only_runtime_directories() -> None:
    compose = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    mounts = {
        "/home/zhice/.zhice/contexts",
        "/home/zhice/.zhice/state",
        "/home/zhice/.zhice/logs",
        "/home/zhice/.zhice/extends",
    }
    assert all(path in compose for path in mounts)
    assert "/home/zhice/.zhice/config" not in compose
    assert "/home/zhice/.zhice/prompts" not in compose


def test_cloud_deploy_requires_digest_and_single_container() -> None:
    script = (DEPLOY / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    assert "@sha256:" in script
    assert "--restart unless-stopped" in script
    assert "docker run -d" in script
    assert "--scale" not in script
