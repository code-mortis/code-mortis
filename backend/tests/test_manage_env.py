import os
import subprocess
import sys
from pathlib import Path

import pytest

MANAGE = Path(__file__).parents[1] / "code_mortis/manage.py"


def run(*arguments):
    return subprocess.run([sys.executable, str(MANAGE), *arguments], capture_output=True, text=True, check=False)


def values(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#"))


def test_init_creates_parser_token_without_retired_runner_or_enabling_profiles(tmp_path):
    path = tmp_path / ".env"
    result = run("init-env", "--owner", "owner", "--output", str(path))
    assert result.returncode == 0, result.stderr
    config = values(path)
    assert config["COMPOSE_PROFILES"] == ""
    assert len(config["CM_CODE_GRAPH_TOKEN"]) >= 32
    assert "CM_RUNNER_TOKEN" not in config
    assert all(config[key] not in result.stdout for key in ("CM_CODE_GRAPH_TOKEN", "CM_ENCRYPTION_KEY"))
    assert os.stat(path).st_mode & 0o777 == 0o600
    before = path.read_bytes()
    assert run("init-env", "--owner", "owner", "--output", str(path)).returncode == 2
    assert path.read_bytes() == before


@pytest.mark.parametrize("present", [(), ("CM_RUNNER_TOKEN",), ("CM_CODE_GRAPH_TOKEN",), ("CM_RUNNER_TOKEN", "CM_CODE_GRAPH_TOKEN")])
def test_upgrade_preserves_existing_credentials_and_profiles(tmp_path, present):
    path = tmp_path / ".env"
    original = "# keep comment\nCM_ENCRYPTION_KEY=existing-key\nCM_PUBLIC_URL=https://example.test\nCOMPOSE_PROFILES=webhooks\n"
    original += "".join(f"{key}=existing-{key}\n" for key in present)
    path.write_text(original.rstrip("\n"))
    before = path.read_bytes()
    result = run("upgrade-env", "--file", str(path))
    assert result.returncode == 0, result.stderr
    assert path.read_bytes().startswith(before)
    config = values(path)
    assert config["CM_ENCRYPTION_KEY"] == "existing-key"
    assert config["COMPOSE_PROFILES"] == "webhooks"
    assert config.get("CM_RUNNER_TOKEN") == ("existing-CM_RUNNER_TOKEN" if "CM_RUNNER_TOKEN" in present else None)
    assert config["CM_CODE_GRAPH_TOKEN"] == "existing-CM_CODE_GRAPH_TOKEN" if "CM_CODE_GRAPH_TOKEN" in present else len(config["CM_CODE_GRAPH_TOKEN"]) >= 32
    assert all(config[key] not in result.stdout for key in present)
    assert config["CM_CODE_GRAPH_TOKEN"] not in result.stdout
    upgraded = path.read_bytes()
    assert run("upgrade-env", "--file", str(path)).returncode == 0
    assert path.read_bytes() == upgraded


@pytest.mark.parametrize("key", ["CM_CODE_GRAPH_TOKEN"])
def test_upgrade_rejects_empty_service_token_without_partial_changes(tmp_path, key):
    path = tmp_path / ".env"
    path.write_text(f"CM_ENCRYPTION_KEY=existing\n{key}=\n")
    before = path.read_bytes()
    result = run("upgrade-env", "--file", str(path))
    assert result.returncode == 2
    assert key + " is empty" in result.stderr
    assert path.read_bytes() == before
