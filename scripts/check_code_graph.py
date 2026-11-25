"""Verify the App image and graph storage configuration across Compose deployments."""

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def configurations():
    env = os.environ | {"POSTGRES_PASSWORD": "test-only", "COMPOSE_PROFILES": "",
                        "CM_PUBLIC_HOST": "example.test", "COOLIFY_RESOURCE_UUID": "test-only"}
    for files in (["compose.yaml"], ["compose.dev.yaml"], ["compose.yaml", "compose.coolify.yaml"]):
        args = ["docker", "compose", "--env-file", "/dev/null"]
        for file in files:
            args.extend(["-f", file])
        config = json.loads(subprocess.check_output(
            args + ["config", "--format", "json", "--no-env-resolution"], cwd=ROOT, env=env,
        ))
        services = config["services"]
        assert not {"code-graph-worker", "code-graph-parser"} & services.keys()
        assert "code-graph" not in config.get("networks", {})
        assert services["sync-worker"]["command"] == ["/usr/local/bin/code-mortis-sync"]
        assert services["sync-worker"]["image"] == services["api"]["image"]
        if "compose.coolify.yaml" in files:
            app_image = services["api"]["image"]
            assert app_image.startswith("codemortis/code-mortis:")
            assert all(service["image"] == app_image and "build" not in service
                       for name, service in services.items() if name not in {"postgres", "redis"})
        stores = [(name, volume) for name, service in services.items() for volume in service.get("volumes", [])
                  if volume.get("source") == "code-graph-data"]
        assert {name for name, volume in stores if not volume.get("read_only")} == {"sync-worker"}
        assert ("api", True) in {(name, volume.get("read_only")) for name, volume in stores}
        assert all(volume["target"] == "/var/lib/code-mortis/code-graph" for _, volume in stores)


if __name__ == "__main__":
    configurations()
