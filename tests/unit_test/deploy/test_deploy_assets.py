from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy"


def test_public_deploy_assets_are_complete() -> None:
    expected = {
        "Dockerfile",
        "docker-compose.yml",
        "README.md",
        ".gitignore",
        "private/cloud-target.example.json",
        "deploy-cloud.cmd",
        "deploy-cloud-image.cmd",
        "deploy-local.cmd",
        "pipelines/deploy-cloud.ps1",
        "pipelines/deploy-cloud-image.ps1",
        "pipelines/deploy-local.ps1",
        "pipelines/invoke-cloud-release.ps1",
        "scripts/build-image.ps1",
        "scripts/push-image.ps1",
        "scripts/run-local.ps1",
        "scripts/deploy.sh",
        "scripts/stop.sh",
        "scripts/status.sh",
        "scripts/logs.sh",
        "scripts/restart.sh",
        "scripts/remote_ops.py",
    }
    assert all((DEPLOY / path).is_file() for path in expected)
    assert (ROOT / "config" / ".env.example").is_file()
    assert not (DEPLOY / ".env.example").exists()


def test_remote_shell_scripts_are_checked_out_with_lf() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "deploy/scripts/*.sh text eol=lf" in attributes


def test_private_deploy_files_are_ignored() -> None:
    patterns = (DEPLOY / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "private/*" in patterns
    assert "!private/cloud-target.example.json" in patterns


def test_cloud_config_example_uses_chinese_placeholders_without_secrets() -> None:
    example_path = DEPLOY / "private" / "cloud-target.example.json"
    example_text = example_path.read_text(encoding="utf-8")
    example = json.loads(example_text)

    assert example["Registry"] == "阿里云镜像仓库路径"
    assert example["SshHost"] == "云服务器地址"
    assert example["SshUser"] == "云服务器登录用户名"
    assert example["SshPassword"] == "云服务器SSH登录密码"
    assert example["RemoteOpsDir"] == "云服务器运维脚本目录"
    assert example["PublicUrl"] == "公网访问地址"
    assert example["Port"] == 10086
    assert not {"Token", "Secret", "PrivateKey"} & set(example)
    assert "suqianbei" not in example_text
    assert "81.70.158.81" not in example_text


def test_deploy_readme_uses_current_workspace_config_and_marks_legacy_env() -> None:
    readme = (DEPLOY / "README.md").read_text(encoding="utf-8")
    assert "${workspace}/config/.env" in readme
    assert "${workspace}/config/config.yml" in readme
    assert "${workspace}/config/models.json" in readme
    assert r"C:\Users\<user>\.zhice\config\.env" in readme
    assert "legacy migration" in readme
    assert "deploy/private/.env` 不包含 `ZHICE_AGENT_WORKSPACE" in readme
    assert "普通 `zcagent init` 已默认" in readme
    assert "`--write-env` 仅作为兼容参数" in readme


def test_deploy_readme_explains_cloud_fields_and_credential_storage() -> None:
    readme = (DEPLOY / "README.md").read_text(encoding="utf-8")

    assert "所有需要替换的字符串直接使用中文占位" in readme
    assert "`Port`" in readme and "默认 `10086`" in readme
    assert "credsStore=desktop" in readme
    assert r"%USERPROFILE%\.docker\config.json" in readme
    assert "/root/.docker/config.json" in readme
    assert "Base64 编码，并非加密" in readme
    assert "%USERPROFILE%\\.ssh\\" in readme
    assert "MobaXterm" not in readme
    assert "sudo" in readme
    assert "RemoteOpsDir" in readme
    assert "不会上传整个 `deploy/`" in readme
    assert "python -m pip install \".[deploy]\"" in readme
    assert "known_hosts" in readme
    assert "明文 Secret" in readme
    assert "每个启用的 QQ `accounts` 项" in readme
    assert "https://agent.zouzhou.xyz" in readme
    assert "不能依赖未配置时的本地默认值" in readme


def test_dockerfile_builds_repository_assets_and_only_private_config() -> None:
    dockerfile = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY . ." not in dockerfile
    assert "COPY agent/ ./agent/" in dockerfile
    assert "COPY prompts/ ./prompts/" in dockerfile
    assert "COPY skill_repo/ ./skill_repo/" in dockerfile
    assert "npm run build" in dockerfile
    assert "ZHICE_AGENT_SKILL_REPO=" not in dockerfile
    assert "HOME=/home/zhice" in dockerfile
    assert "ZHICE_AGENT_WORKSPACE=" not in dockerfile
    assert "USER zhice" in dockerfile
    assert "deploy/private/.env /home/zhice/.zhice/config/.env" in dockerfile
    assert (
        "deploy/private/config.yml /home/zhice/.zhice/config/config.yml"
        in dockerfile
    )
    assert (
        "deploy/private/models.json /home/zhice/.zhice/config/models.json"
        in dockerfile
    )
    assert (
        'ENTRYPOINT ["zcagent", "--env-file", "/home/zhice/.zhice/config/.env"]'
        in dockerfile
    )
    assert 'CMD ["gateway", "--host", "0.0.0.0", "--port", "10086"]' in dockerfile


def test_docker_installs_explicit_gateway_websocket_runtime() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    gateway = metadata["project"]["optional-dependencies"]["gateway"]
    deploy_extra = metadata["project"]["optional-dependencies"]["deploy"]
    dockerfile = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
    sidecar_main = (
        ROOT / "integrations" / "weixin_sidecar" / "src" / "main.js"
    ).read_text(encoding="utf-8")

    assert any(requirement.startswith("websockets") for requirement in gateway)
    assert any(requirement.startswith("paramiko") for requirement in deploy_extra)
    assert 'pip install --no-cache-dir ".[gateway,qq]"' in dockerfile
    assert ".[deploy]" not in dockerfile
    assert 'from "node:url"' in sidecar_main
    assert "pathToFileURL(process.argv[1]).href" in sidecar_main


def test_apt_mirror_is_optional_validated_and_shared_by_build_entrypoints() -> None:
    dockerfile = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
    build_script = (DEPLOY / "scripts" / "build-image.ps1").read_text(
        encoding="utf-8"
    )
    compose = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    readme = (DEPLOY / "README.md").read_text(encoding="utf-8")

    assert 'ARG APT_MIRROR=""' in dockerfile
    assert "Invalid APT_MIRROR host" in dockerfile
    assert 's|deb.debian.org|${APT_MIRROR}|g' in dockerfile
    assert '[string]$AptMirror = ""' in build_script
    assert "Invalid APT mirror host" in build_script
    assert '"APT_MIRROR=$AptMirror"' in build_script
    assert "APT_MIRROR: ${ZHICE_APT_MIRROR:-}" in compose
    assert "-AptMirror mirrors.aliyun.com" in readme
    assert "只影响 Docker 构建阶段" in readme


def test_compose_persists_runtime_and_weixin_credentials_only() -> None:
    compose = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    mounts = {
        "/home/zhice/.zhice/contexts",
        "/home/zhice/.zhice/state",
        "/home/zhice/.zhice/logs",
        "/home/zhice/.zhice/extends",
        "/home/zhice/.zhice/config/channels/weixin/accounts",
    }
    assert all(path in compose for path in mounts)
    assert "zhice-weixin-credentials:" in compose
    assert "zhice-weixin-credentials:/home/zhice/.zhice/config/channels/weixin/accounts" in compose
    assert "zhice-config:/home/zhice/.zhice/config" not in compose
    assert "/home/zhice/.zhice/prompts" not in compose


def test_docker_and_cloud_deploy_persist_weixin_credentials() -> None:
    dockerfile = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
    script = (DEPLOY / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    credential_path = "/home/zhice/.zhice/config/channels/weixin/accounts"

    assert credential_path in dockerfile
    assert credential_path in script
    assert "zhice-weixin-credentials" in script
    assert "docker run --rm --user root --entrypoint sh" in script
    assert "chown -R zhice:zhice" in script
    assert "chmod 700" in script
    assert "docker volume rm" not in script


def test_cloud_deploy_requires_digest_and_single_container() -> None:
    script = (DEPLOY / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    assert "@sha256:" in script
    assert "--restart unless-stopped" in script
    assert "docker run -d" in script
    assert '-p "127.0.0.1:${HOST_PORT}:10086"' in script
    assert '-p "${HOST_PORT}:10086"' not in script
    assert "--scale" not in script


def test_push_image_selects_digest_for_exact_target_repository() -> None:
    script = (DEPLOY / "scripts" / "push-image.ps1").read_text(encoding="utf-8")

    assert 'docker image inspect --format \'{{json .RepoDigests}}\'' in script
    assert "$parsedRepoDigests = ConvertFrom-Json -InputObject $repoDigestsJson" in script
    assert "$parsedRepoDigests | Where-Object" in script
    assert "@($repoDigestsJson | ConvertFrom-Json)" not in script
    assert '$digestPrefix = "${repository}@sha256:"' in script
    assert ".StartsWith($digestPrefix)" in script
    assert "$matchingDigests.Count -ne 1" in script
    assert "{{index .RepoDigests 0}}" not in script


def test_windows_powershell_expands_repo_digest_json_before_unique_match() -> None:
    powershell = r'''
$ErrorActionPreference = "Stop"
$repository = "registry.example.test/team/zhice-agent"
$repoDigestsJson = '["other.example.test/team/zhice-agent@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","registry.example.test/team/zhice-agent@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]'
$parsedRepoDigests = ConvertFrom-Json -InputObject $repoDigestsJson
$digestPrefix = "${repository}@sha256:"
$matchingDigests = @($parsedRepoDigests | Where-Object {
    $_ -is [string] -and $_.StartsWith($digestPrefix)
})
if ($parsedRepoDigests.Count -ne 2) { throw "JSON array was not expanded" }
if ($matchingDigests.Count -ne 1) { throw "Expected one repository digest" }
Write-Output $matchingDigests[0]
'''
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", powershell],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().endswith("@sha256:" + "b" * 64)


def test_local_smoke_does_not_overwrite_powershell_home() -> None:
    script = (DEPLOY / "scripts" / "run-local.ps1").read_text(encoding="utf-8")

    assert "$home =" not in script.lower()
    assert "$homeResponse = Invoke-WebRequest" in script
    assert "$homeResponse.StatusCode -eq 200" in script


def test_local_smoke_removes_its_anonymous_volumes() -> None:
    script = (DEPLOY / "scripts" / "run-local.ps1").read_text(encoding="utf-8")

    assert '$name = "zhice-agent-smoke"' in script
    assert "function Test-SmokeContainerExists" in script
    assert '$existing = @(docker ps -a --filter "name=^/$name$"' in script
    assert 'docker ps -a --filter "name=^/$name$"' in script
    assert "return $existing -contains $name" in script
    assert "function Remove-SmokeContainer" in script
    assert "if (-not (Test-SmokeContainerExists)) { return }" in script
    assert '$running = @(docker ps --filter "name=^/$name$"' in script
    assert "if ($running -contains $name)" in script
    assert ".Trim()" not in script
    assert "docker rm --volumes $name" in script
    assert script.count("Remove-SmokeContainer") == 3
    assert "docker volume rm" not in script
    assert "No such container" not in script


def test_local_deploy_pipeline_has_one_no_argument_entrypoint() -> None:
    script = (DEPLOY / "pipelines" / "deploy-local.ps1").read_text(
        encoding="utf-8"
    )
    readme = (DEPLOY / "README.md").read_text(encoding="utf-8")

    assert not (DEPLOY / "deploy-local.ps1").exists()
    assert not (DEPLOY / "scripts" / "deploy-local.ps1").exists()
    assert "param()" in script
    assert '$deployRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path' in script
    assert '$scriptsRoot = Join-Path $deployRoot "scripts"' in script
    assert '$aptMirror = "mirrors.aliyun.com"' in script
    assert '$imageRef = "${imageName}:${imageTag}"' in script
    assert '$publicPort = 10086' in script
    assert '$smokePort = 10087' in script
    assert "& $buildScript -Image $imageName -Tag $imageTag -AptMirror $aptMirror" in script
    assert "& $smokeScript -Image $imageRef -Port $smokePort" in script
    assert "up -d --force-recreate --no-build" in script
    assert 'if ($health -ne "healthy")' in script
    assert "docker compose" in script
    assert "docker volume rm" not in script
    assert "down -v" not in script
    assert r".\deploy\pipelines\deploy-local.ps1" in readme
    assert r".\deploy\scripts\deploy-local.ps1" not in readme
    assert "终端中则运行无参数 PowerShell 入口" in readme


def test_local_deploy_cmd_is_a_thin_double_click_launcher() -> None:
    launcher = (DEPLOY / "deploy-local.cmd").read_text(encoding="utf-8")
    readme = (DEPLOY / "README.md").read_text(encoding="utf-8")

    assert launcher.startswith("@echo off")
    assert (
        'powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File '
        r'"%~dp0pipelines\deploy-local.ps1"'
    ) in launcher
    assert 'set "EXIT_CODE=%ERRORLEVEL%"' in launcher
    assert "pause" in launcher
    assert "exit /b %EXIT_CODE%" in launcher
    assert "docker " not in launcher.lower()
    assert r"deploy\deploy-local.cmd" in readme
    assert "资源管理器中可直接双击" in readme


def test_cloud_entrypoints_separate_existing_image_and_full_release() -> None:
    existing = (DEPLOY / "pipelines" / "deploy-cloud-image.ps1").read_text(
        encoding="utf-8"
    )
    full = (DEPLOY / "pipelines" / "deploy-cloud.ps1").read_text(
        encoding="utf-8"
    )
    readme = (DEPLOY / "README.md").read_text(encoding="utf-8")

    assert '[string]$Image = "zhice-agent:local"' in existing
    assert "[switch]$Smoke" in existing
    assert "if ($Smoke)" in existing
    assert "Reusing operator-approved local image without smoke" in existing
    assert "build-image.ps1" not in existing
    assert "invoke-cloud-release.ps1" in existing

    assert '$imageName = "zhice-agent"' in full
    assert '$imageTag = "local"' in full
    assert "& $buildScript" in full
    assert "& $smokeScript" in full
    assert "& $releaseScript" in full
    assert "docker compose" not in full

    assert r".\deploy\pipelines\deploy-cloud-image.ps1" in readme
    assert r".\deploy\pipelines\deploy-cloud.ps1" in readme
    assert "默认不再次 smoke" in readme


def test_shared_cloud_release_uses_paramiko_digest_and_https() -> None:
    script = (DEPLOY / "pipelines" / "invoke-cloud-release.ps1").read_text(
        encoding="utf-8"
    )

    assert 'SourceImage -notmatch \'^zhice-agent:' in script
    assert 'ToString("yyyyMMdd-HHmmss")' in script
    assert "git -C $repoRoot rev-parse --short=8 HEAD" in script
    assert "& $pushScript" in script
    assert 'StartsWith("Pushed image digest: ")' in script
    assert 'Join-Path $scriptsRoot $_' in script
    assert 'private/cloud-target.json' in script
    assert "ConvertFrom-Json" in script
    assert "inspect-config" in script
    assert "Import-PowerShellDataFile" not in script
    assert "Replace the Chinese placeholder" in script
    assert '@("deploy.sh", "status.sh", "logs.sh", "stop.sh", "restart.sh")' in script
    assert script.index("Checking known_hosts") < script.index("Pushing release image")
    assert "remote_ops.py" in script
    assert 'python -c "import paramiko"' not in script
    assert '$ErrorActionPreference = "Continue"' in script
    assert "--help 2>&1" in script
    assert "Paramiko is unavailable" in script
    assert "--config $ConfigPath deploy" in script
    assert "ssh @" not in script
    assert "scp @" not in script
    assert 'Invoke-RestMethod -UseBasicParsing -Uri "${publicUrl}/health"' in script
    assert "Invoke-Expression" not in script
    assert "password=" not in script.lower()
    assert "Local public HTTPS health passed" in script
    assert "remote public HTTPS health already passed" in script
    assert 'throw "Public health check failed' not in script


def test_remote_ops_helper_keeps_password_out_of_process_arguments() -> None:
    helper = (DEPLOY / "scripts" / "remote_ops.py").read_text(encoding="utf-8")

    assert 'config.get("RemoteOpsDir")' in helper
    assert 'config.get("RemoteDir")' not in helper
    assert 'Path.home() / ".ssh" / "known_hosts"' in helper
    assert "load_host_keys" in helper
    assert "paramiko.RejectPolicy()" in helper
    assert "CryptographyDeprecationWarning" in helper
    assert 'module=r"^paramiko(?:\\.|$)"' in helper
    assert "AutoAddPolicy" not in helper
    assert 'password=str(config["SshPassword"])' in helper
    assert 'parser.add_argument("--password"' not in helper
    assert "os.environ" not in helper
    assert "get_pty()" in helper
    assert "sudo -S -p ''" in helper
    assert '.replace(password, "[REDACTED]")' in helper
    assert 'SCRIPT_NAMES = ("deploy.sh", "status.sh", "logs.sh", "stop.sh", "restart.sh")' in helper
    assert "sh -n" in helper
    assert "mv -Tf" in helper
    assert "def verify_public_health" in helper
    assert "curl --fail --silent --show-error --max-time 20 --" in helper
    assert helper.index("sudo_deploy(") < helper.index("verify_public_health(")


def test_remote_operations_scripts_have_safe_maintenance_semantics() -> None:
    status = (DEPLOY / "scripts" / "status.sh").read_text(encoding="utf-8")
    logs = (DEPLOY / "scripts" / "logs.sh").read_text(encoding="utf-8")
    stop = (DEPLOY / "scripts" / "stop.sh").read_text(encoding="utf-8")
    restart = (DEPLOY / "scripts" / "restart.sh").read_text(encoding="utf-8")
    deploy = (DEPLOY / "scripts" / "deploy.sh").read_text(encoding="utf-8")

    for field in ("image=", "status=", "health=", "created=", "restarts="):
        assert field in status
    assert "exists=false" in status
    assert "positive integer" in logs
    assert "already absent" in stop
    assert "already stopped" in stop
    assert "docker restart --time 30" in restart
    assert "rollback()" in deploy
    assert "restored previous container" in deploy


def test_cloud_cmd_files_are_thin_double_click_launchers() -> None:
    for stem in ("deploy-cloud", "deploy-cloud-image"):
        launcher = (DEPLOY / f"{stem}.cmd").read_text(encoding="utf-8")

        assert launcher.startswith("@echo off")
        assert (
            "powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "
            fr'"%~dp0pipelines\{stem}.ps1"'
        ) in launcher
        assert 'set "EXIT_CODE=%ERRORLEVEL%"' in launcher
        assert "pause" in launcher
        assert "exit /b %EXIT_CODE%" in launcher
        assert "docker " not in launcher.lower()
