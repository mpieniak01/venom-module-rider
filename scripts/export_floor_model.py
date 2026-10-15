"""Explicit model provisioning. Never called by a panel, request or startup."""

import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--revision", required=True, help="Pinned Hugging Face commit SHA"
    )
    args = parser.parse_args()
    if len(args.revision) != 40 or any(
        c not in "0123456789abcdef" for c in args.revision
    ):
        parser.error("revision must be a full commit SHA")
    import torch
    from transformers import SegformerForSemanticSegmentation

    model = SegformerForSemanticSegmentation.from_pretrained(
        "nvidia/segformer-b0-finetuned-ade-512-512", revision=args.revision
    ).eval()
    if model.config.id2label.get(3) != "floor":
        raise ValueError("floor_label_mismatch")

    class Logits(torch.nn.Module):
        def __init__(self, inner):
            super().__init__()
            self.inner = inner

        def forward(self, pixels):
            return self.inner(pixel_values=pixels).logits

    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / "model.onnx"
    if target.exists():
        raise FileExistsError("model output already exists")
    torch.onnx.export(
        Logits(model),
        (torch.zeros((1, 3, 512, 512)),),
        str(target),
        input_names=["pixel_values"],
        output_names=["logits"],
        opset_version=17,
        dynamo=False,
    )
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    metadata = {
        "model": "nvidia/segformer-b0-finetuned-ade-512-512",
        "revision": args.revision,
        "sha256": digest,
        "floor_label": 3,
        "input_size": 512,
        "distance_measurement": False,
    }
    (args.output / "manifest.json").write_text(json.dumps(metadata, indent=2))
    print("RIDER_VISION_MODEL=" + str(target.resolve()))
    print("RIDER_VISION_SHA256=" + digest)


if __name__ == "__main__":
    main()
