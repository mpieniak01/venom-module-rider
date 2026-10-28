"""Causal video replay must preserve newest-frame identity and backend semantics."""

import numpy as np
import pytest
from PIL import Image

from scripts.compare_depth_video import assess_window, decode_video, window_indices
from scripts.depth_candidates import Candidate


@pytest.mark.parametrize(
    "name,width",
    [("da3-small", 1), ("da3-small", 3), ("da3-base", 5), ("da3metric-large", 1)],
)
def test_native_window_returns_newest_depth_without_mono_recursion(name, width):
    torch = pytest.importorskip("torch")
    candidate = Candidate.__new__(Candidate)
    candidate.name, candidate.device, candidate.resolution = name, "cpu", 280
    candidate.metadata = {}
    calls = []

    def processor(images, **kwargs):
        return torch.zeros((len(images), 3, 64, 64)), None, None

    def model(inputs, **kwargs):
        calls.append((tuple(inputs.shape), kwargs))
        values = torch.arange(1, width + 1).reshape(1, width, 1, 1).float()
        return {"depth": values.expand(1, width, 64, 64)}

    candidate.processor, candidate.model = processor, model
    images = [Image.new("RGB", (64, 64)) for _ in range(width)]
    result = (
        candidate.infer(images[0]) if width == 1 else candidate.infer_window(images)
    )
    assert result.shape == (64, 64)
    assert np.all(result == width)
    assert len(calls) == 1
    assert calls[0][1]["ref_view_strategy"] == "first"


@pytest.mark.parametrize("name", ["da2", "da3metric-large"])
def test_single_view_backends_cannot_claim_joint_attention(name):
    candidate = Candidate.__new__(Candidate)
    candidate.name = name
    with pytest.raises(ValueError, match="backend_has_no_joint"):
        candidate.infer_window([Image.new("RGB", (64, 64)) for _ in range(3)])


@pytest.mark.parametrize("width", [0, 6])
def test_window_bounds(width):
    candidate = Candidate.__new__(Candidate)
    with pytest.raises(ValueError, match="window_bounds"):
        candidate.infer_window([Image.new("RGB", (64, 64)) for _ in range(width)])


def test_causal_slice_has_no_future_or_missing_history():
    received = []

    class Spy:
        def infer_window(self, images):
            received.extend(int(np.asarray(image)[0, 0, 0]) for image in images)
            return np.ones((64, 64), dtype=np.float32)

    images = [Image.new("RGB", (64, 64), (i, i, i)) for i in range(8)]
    result = assess_window(
        Spy(), images, [i * 0.1 for i in range(8)], 4, 3, "model_probe"
    )
    assert received == [2, 3, 4]
    assert result["history_indices"] == [2, 3, 4]
    assert result["history_span_ms"] == pytest.approx(200)
    assert result["drive_supported"] is False
    with pytest.raises(ValueError, match="window_incomplete"):
        assess_window(Spy(), images, [i * 0.1 for i in range(8)], 0, 3, "model_probe")


def test_production_dark_video_does_not_reach_model():
    class Spy:
        def infer_window(self, images):
            raise AssertionError("dark image reached model")

    result = assess_window(
        Spy(), [Image.new("RGB", (64, 64))] * 5, list(range(5)), 4, 3, "production"
    )
    assert result["state"] == "unknown"
    assert result["reason"] == "vision_image_quality"
    assert result["drive_supported"] is False


def test_endpoint_selection_is_chronological_and_shared_across_windows():
    indices = window_indices(269, 32)
    assert len(indices) == 32
    assert indices[0] >= 4
    assert indices[-1] == 268
    assert np.all(np.diff(indices) > 0)
    with pytest.raises(ValueError, match="video_too_short"):
        window_indices(4, 32)


def test_decoder_rejects_extra_frames_and_closes(monkeypatch):
    cv2 = pytest.importorskip("cv2")

    class Fake:
        closed = False
        index = 0

        def isOpened(self):
            return True

        def read(self):
            self.index += 1
            return True, np.zeros((64, 64, 3), dtype=np.uint8)

        def release(self):
            self.closed = True

    fake = Fake()
    monkeypatch.setattr(cv2, "VideoCapture", lambda *args: fake)
    with pytest.raises(ValueError, match="video_frame_count_changed"):
        decode_video("unused", 2)
    assert fake.closed


@pytest.mark.parametrize("dark_index", [0, 1, 2])
def test_production_rejects_dark_frame_anywhere_in_window(dark_index):
    class Spy:
        def infer_window(self, images):
            raise AssertionError("rejected history reached model")

    image = Image.fromarray(np.tile(np.arange(64, dtype=np.uint8) * 4, (64, 1)))
    images = [image.convert("RGB") for _ in range(3)]
    images[dark_index] = Image.new("RGB", (64, 64))
    result = assess_window(Spy(), images, [0, 0.1, 0.2], 2, 3, "production")
    assert result["reason"] == "vision_image_quality"
    assert result["image_quality_passed"] is False
    assert result["drive_supported"] is False


def test_video_report_does_not_follow_output_inserted_during_replay(
    tmp_path, monkeypatch
):
    from scripts import compare_depth_video as compare

    target = tmp_path / "private"
    target.write_text("original")
    output = tmp_path / "report.json"

    def replay(_args):
        output.symlink_to(target)
        return {"runs": []}

    monkeypatch.setattr(compare, "replay", replay)
    monkeypatch.setattr(
        "sys.argv",
        [
            "compare_depth_video",
            "--backend",
            "da2",
            "--model",
            str(tmp_path / "model"),
            "--manifest",
            str(tmp_path / "manifest"),
            "--transport",
            str(tmp_path / "transport"),
            "--video",
            str(tmp_path / "video"),
            "--output",
            str(output),
        ],
    )
    with pytest.raises(FileExistsError):
        compare.main()
    assert target.read_text() == "original"
