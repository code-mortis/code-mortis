import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
PROBE_ARGS = ["--login", "missing", "--pr-id", "pr", "--path", "main.py"]


def test_direct_snapshot_isolates_unreadable_files(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    from code_graph_eval.evaluate import direct

    (tmp_path / "legacy.py").write_bytes(b'# coding: latin-1\nlog("caf\xe9")\n')
    (tmp_path / "python2.py").write_text("print 1\n")
    (tmp_path / "valid.py").write_text("from .target import f\n")
    (tmp_path / "target.py").write_text("def f(): pass\n")
    result = direct(tmp_path)
    assert result["statuses"] == {
        "legacy.py": "partial", "python2.py": "partial", "valid.py": "parsed", "target.py": "parsed",
    }
    assert [site["target"] for site in result["imports"]] == ["target.py"]


@pytest.mark.parametrize(("script", "args", "error"), [
    ("check_pr_code_performance.py", [*PROBE_ARGS, "--samples", "0"], "--samples must be between 1 and 100"),
    ("check_pr_code_performance.py", [*PROBE_ARGS, "--samples", "101"], "--samples must be between 1 and 100"),
    ("check_pr_code_performance.py", PROBE_ARGS, "User not found"),
    ("code_graph_eval/quality_run.py", ["--authorized", "--requests", "requests.json", "--output", "out.json",
                                        "--connection-id", "unused"], "restricted to local development"),
    ("code_graph_eval/overlap_workload.py", ["database"], "must never use an application database"),
])
def test_script_guards_survive_optimized_python(tmp_path, script, args, error):
    env = os.environ.copy()
    if script.endswith("overlap_workload.py"):
        env["CM_DATABASE_URL"] = "sqlite:///:memory:"
    result = subprocess.run([sys.executable, "-O", str(SCRIPTS / script), *args],
                            env=env, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert error in result.stderr
    assert not (tmp_path / "out.json").exists()
