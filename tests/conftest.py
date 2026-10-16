import sys
from pathlib import Path

import pytest

host = Path(__file__).resolve().parents[3]
if (host / "venom_core").is_dir():
    sys.path.insert(0, str(host))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def isolated_samples_root(monkeypatch, tmp_path):
    """No host/router test can touch the operator's persistent data."""
    monkeypatch.setenv("RIDER_SAMPLE_ROOT", str(tmp_path / "module-samples"))
