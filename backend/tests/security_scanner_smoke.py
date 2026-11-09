"""Synthetic Trivy integration check in the application image, without AI or GitHub."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

os.environ.setdefault("CM_ENCRYPTION_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
os.environ.setdefault("CM_TRIVY_CACHE_DIR", "/tmp/security-scanner-cache")
os.environ.setdefault("CM_BOOTSTRAP_TOKEN", "isolated-scanner-smoke-only")
os.environ.setdefault("CM_OWNER_GITHUB_LOGIN", "synthetic-scanner-test")

from code_mortis.core.process import child_environment
from code_mortis.security_scans import engine as security_engine

with tempfile.TemporaryDirectory(prefix="scanner-fixture-") as directory:
    root = Path(directory)
    item = security_engine.Snapshot(root, "a" * 40, child_environment(), lambda: False)
    item.git.mkdir()
    fixtures = {
        "app.py": "token = 'ghp_" + "aB9cD8eF7gH6iJ5kL4mN3oP2qR1sT0uV9wX8" + "'\n",
        "Dockerfile": "FROM alpine:3.10\n#trivy:ignore:DS002\nUSER root\nRUN echo hello\n",
        "requirements.txt": "Flask==0.12\n",
        "trivy.yaml": "scan:\n  scanners: []\n",
        "trivy-secret.yaml": "disable-rules: [github-pat]\n",
        ".trivyignore": "DS002\nAVD-DS-0002\n",
        "infra/main.tf": '''resource "aws_security_group" "unsafe" {
  description = "Synthetic offline audit fixture"
  ingress {
    description = "Synthetic unrestricted rule"
    from_port = 22
    to_port = 22
    protocol = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
''',
    }
    for path, content in fixtures.items():
        (item.files / path).parent.mkdir(parents=True, exist_ok=True)
        (item.files / path).write_text(content)
        item.inventory[path] = {"hash": security_engine.digest(content), "lines": len(content.splitlines()), "code": False}
    item.read = lambda path, sha=None: fixtures[path]
    result = security_engine.trivy(item, 300, lambda: False)
    kinds = {finding["kind"] for finding in result["findings"]}
    assert {"secret", "infrastructure"} <= kinds, (kinds, result["problems"])
    assert any(finding.get("rule_id") in {"DS002", "DS-0002", "AVD-DS-0002"} for finding in result["findings"]), [(f["rule_id"], f["title"]) for f in result["findings"]]
    assert fixtures["app.py"].split("'")[1] not in json.dumps(result)
    assert "Flask".lower() in {package["name"].lower() for row in result["targets"].values() for package in row["packages"]} or not result["metadata"]["database_fresh"]
    if result["metadata"]["database_fresh"]:
        assert "dependency" in kinds, kinds
    assert "[redacted]" in item.source("app.py")
    assert any(finding.get("resource") == "aws_security_group.unsafe" and finding["path"] == "infra/main.tf"
               for finding in result["findings"])
    assert result["metadata"]["directory_summaries"]
    print(json.dumps({"kinds": sorted(kinds), "metadata": result["metadata"], "problems": result["problems"]}, indent=2))

process = subprocess.run(["python", "-c", "from code_mortis.security_scans.scanner import deny_network; import socket; deny_network(); socket.socket()"], capture_output=True)
assert process.returncode and b"Operation not permitted" in process.stderr
print("Pinned Trivy detected synthetic exposed material and infrastructure, reported dependency coverage, ignored repository scanner policies, and denied scanner networking.")
