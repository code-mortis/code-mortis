import os
import subprocess
import sys
from pathlib import Path

MANAGE = Path(__file__).parents[1] / "code_mortis/manage.py"


def run(*arguments):
    return subprocess.run([sys.executable, str(MANAGE), *arguments], capture_output=True, text=True, check=False)


def values(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#"))


def test_init_creates_private_config_without_retired_service_tokens(tmp_path):
    path = tmp_path / ".env"
    result = run("init-env", "--owner", "owner", "--output", str(path))
    assert result.returncode == 0, result.stderr
    config = values(path)
    assert config["COMPOSE_PROFILES"] == ""
    assert "CM_CODE_GRAPH_TOKEN" not in config
    assert "CM_RUNNER_TOKEN" not in config
    assert config["CM_ENCRYPTION_KEY"] not in result.stdout
    assert os.stat(path).st_mode & 0o777 == 0o600
    before = path.read_bytes()
    assert run("init-env", "--owner", "owner", "--output", str(path)).returncode == 2
    assert path.read_bytes() == before


def test_upgrade_preserves_existing_credentials_and_profiles(tmp_path):
    path = tmp_path / ".env"
    original = "CM_ENCRYPTION_KEY=existing-key\nCOMPOSE_PROFILES=webhooks,code-graph\nCM_CODE_GRAPH_TOKEN=old"
    path.write_text(original)
    result = run("upgrade-env", "--file", str(path))
    assert result.returncode == 0, result.stderr
    assert path.read_text() == original
    assert "existing-key" not in result.stdout
