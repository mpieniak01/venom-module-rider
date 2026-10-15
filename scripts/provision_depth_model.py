"""Explicit pinned provisioning; runtime never downloads a model."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

MODEL = "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf"


def provision(output, revision):
    if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("revision must be a full commit SHA")
    if output.exists():
        raise FileExistsError("model output already exists")
    from huggingface_hub import hf_hub_download

    files = {}
    for name in ("config.json", "preprocessor_config.json", "model.safetensors"):
        source = Path(hf_hub_download(MODEL, name, revision=revision))
        output.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, output / name)
        files[name] = hashlib.sha256((output / name).read_bytes()).hexdigest()
    (output / "manifest.json").write_text(
        json.dumps({"model": MODEL, "revision": revision, "files": files}, indent=2)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    provision(args.output, args.revision)


if __name__ == "__main__":
    main()
