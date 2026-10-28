import os
import sys
from importlib.abc import MetaPathFinder
from pathlib import Path

import pytest

host = Path(__file__).resolve().parents[3]
if os.getenv("RIDER_TEST_STANDALONE") == "1":

    class _NoVenomCore(MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "venom_core" or fullname.startswith("venom_core."):
                raise ModuleNotFoundError(
                    "venom_core is unavailable in standalone tests"
                )
            return None

    if any(
        name == "venom_core" or name.startswith("venom_core.") for name in sys.modules
    ):
        raise RuntimeError("standalone tests must not preload venom_core")
    sys.meta_path.insert(0, _NoVenomCore())
elif (host / "venom_core").is_dir():
    sys.path.insert(0, str(host))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def isolated_samples_root(monkeypatch, tmp_path):
    """No host/router test can touch the operator's persistent data."""
    monkeypatch.setenv("RIDER_SAMPLE_ROOT", str(tmp_path / "module-samples"))
