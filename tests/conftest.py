import sys
from pathlib import Path

host = Path(__file__).resolve().parents[3]
if (host / "venom_core").is_dir():
    sys.path.insert(0, str(host))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
