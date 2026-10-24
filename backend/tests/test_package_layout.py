import json
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

from code_mortis.core.config import Settings


def test_frontend_default_still_points_to_repository_build():
    root = Path(__file__).resolve().parents[2]
    assert Settings.model_fields["frontend_dir"].default == root / "frontend" / "dist"


def test_provider_catalog_is_a_package_resource():
    providers = json.loads(files("code_mortis.integrations").joinpath("providers.json").read_text())
    assert providers and all(provider["name"] for provider in providers)


def test_native_runner_import_does_not_initialize_application_services():
    subprocess.run(
        [sys.executable, "-c", """
import sys
from code_mortis.native_runner import app
assert app.routes
assert not any(name in sys.modules for name in (
    "code_mortis.main", "code_mortis.api.dependencies", "code_mortis.jobs",
    "code_mortis.models", "code_mortis.core.db", "celery",
))
"""],
        check=True,
        capture_output=True,
        text=True,
    )
