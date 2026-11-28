import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize(
    ("database_fresh", "wal_fresh", "healthy"),
    [(False, True, True), (True, None, True), (False, False, False), (False, None, False)],
)
def test_scheduler_healthcheck(tmp_path, database_fresh, wal_fresh, healthy):
    overlay = Path(__file__).resolve().parents[2] / "compose.coolify.yaml"
    services = yaml.load(overlay.read_text(), Loader=yaml.BaseLoader)["services"]
    mode, executable, flag, probe, schedule = services["scheduler"]["healthcheck"]["test"]
    assert (mode, executable, flag) == ("CMD", "python", "-c")
    database = tmp_path / Path(schedule).name
    for path, fresh in [(database, database_fresh), (tmp_path / (database.name + "-wal"), wal_fresh)]:
        if fresh is not None:
            path.touch()
            if not fresh:
                os.utime(path, (1, 1))

    result = subprocess.run([sys.executable, flag, probe, database], capture_output=True, text=True)
    assert result.returncode == (0 if healthy else 1), result.stderr
    if not healthy:
        assert "Celery Beat schedule is stale" in result.stderr
