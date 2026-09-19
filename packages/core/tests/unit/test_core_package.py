import importlib

import pytest

import doculens

pytestmark = pytest.mark.unit

LAYERS = ("domain", "application", "infrastructure")


def test_package_reports_installed_version() -> None:
    assert doculens.__version__ == "0.1.0"


@pytest.mark.parametrize("layer", LAYERS)
def test_layer_package_is_importable(layer: str) -> None:
    module = importlib.import_module(f"doculens.{layer}")

    assert module.__doc__, f"doculens.{layer} must document its layer rules"
