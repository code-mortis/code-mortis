import json
from importlib.resources import files
from pathlib import Path

from code_mortis.core.config import Settings


def test_frontend_default_still_points_to_repository_build():
    root = Path(__file__).resolve().parents[2]
    assert Settings.model_fields["frontend_dir"].default == root / "frontend" / "dist"


def test_provider_catalog_is_a_package_resource():
    providers = json.loads(files("code_mortis.integrations").joinpath("providers.json").read_text())
    assert providers and all(provider["name"] for provider in providers)


def test_retired_native_runtime_is_not_shipped():
    import importlib.util

    assert importlib.util.find_spec("code_mortis.native_runner") is None
