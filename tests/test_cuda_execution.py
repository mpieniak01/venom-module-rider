"""No real models: prove CUDA absence and fallback cannot execute CPU inference."""

import sys
from types import SimpleNamespace

import pytest

from venom_module_rider.cuda_execution import cuda_session, require_cuda


def torch_stub(monkeypatch, available):
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: available)),
    )


def test_missing_cuda_is_an_error(monkeypatch):
    torch_stub(monkeypatch, False)
    with pytest.raises(ValueError, match="vision_cuda_unavailable"):
        require_cuda()


def test_cpu_only_ort_cannot_create_a_session(monkeypatch):
    torch_stub(monkeypatch, True)

    def forbidden(*a, **kw):
        pytest.fail("CPU session must never be constructed")

    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(
            get_available_providers=lambda: ["CPUExecutionProvider"],
            InferenceSession=forbidden,
        ),
    )
    with pytest.raises(ValueError, match="vision_cuda_provider_unavailable"):
        cuda_session(
            "model.onnx",
            SimpleNamespace(add_session_config_entry=lambda key, value: None),
        )


@pytest.mark.parametrize("actual", ["CPUExecutionProvider", "CUDAExecutionProvider"])
def test_effective_provider_is_verified_and_runtime_fallback_disabled(
    monkeypatch, actual
):
    torch_stub(monkeypatch, True)
    calls = []

    class Session:
        def __init__(self, path, options, providers):
            assert providers == ["CUDAExecutionProvider"]

        def get_providers(self):
            return [actual]

        def disable_fallback(self):
            calls.append("disabled")

    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(
            get_available_providers=lambda: [
                "CUDAExecutionProvider",
                "CPUExecutionProvider",
            ],
            preload_dlls=lambda: None,
            InferenceSession=Session,
        ),
    )
    if actual == "CPUExecutionProvider":
        with pytest.raises(ValueError, match="vision_cuda_provider_unavailable"):
            cuda_session(
                "model.onnx",
                SimpleNamespace(add_session_config_entry=lambda key, value: None),
            )
        assert calls == []
    else:
        cuda_session(
            "model.onnx",
            SimpleNamespace(add_session_config_entry=lambda key, value: None),
        )
        assert calls == ["disabled"]


def test_archive_candidate_rejects_cpu_before_loading_weights():
    from venom_module_rider.depth_candidates import Candidate

    with pytest.raises(ValueError, match="vision_cuda_required"):
        Candidate("da2", "/not-opened", "cpu", 0)


def test_cuda_session_disables_cpu_graph_assignment(monkeypatch):
    torch_stub(monkeypatch, True)
    entries = {}
    options = SimpleNamespace(
        add_session_config_entry=lambda key, value: entries.update({key: value})
    )

    class Session:
        def __init__(self, path, session_options, providers):
            assert session_options is options
            assert entries == {"session.disable_cpu_ep_fallback": "1"}

        def get_providers(self):
            return ["CUDAExecutionProvider"]

        def disable_fallback(self):
            pass

    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(
            get_available_providers=lambda: ["CUDAExecutionProvider"],
            preload_dlls=lambda: None,
            InferenceSession=Session,
        ),
    )
    cuda_session("model.onnx", options)


def test_missing_torch_is_a_clear_cuda_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)
    with pytest.raises(ValueError, match="vision_cuda_unavailable"):
        require_cuda()


def test_unloaded_detectors_do_not_claim_an_execution_provider():
    from venom_module_rider.depth_detector import DepthDetector
    from venom_module_rider.floor_detector import FloorDetector

    assert FloorDetector().status()["device"] is None
    assert DepthDetector().status()["device"] is None


def test_archive_worker_requires_cuda_even_before_reading_rejected_images(monkeypatch):
    from venom_module_rider.analysis_worker import run

    torch_stub(monkeypatch, False)
    with pytest.raises(ValueError, match="vision_cuda_unavailable"):
        run({}, None)


def test_io_binding_rejects_non_cuda_session(monkeypatch):
    from venom_module_rider.cuda_execution import cuda_logits

    monkeypatch.setattr(
        "venom_module_rider.cuda_execution.require_cuda", lambda: "cuda"
    )
    with pytest.raises(ValueError, match="vision_cuda_provider_unavailable"):
        cuda_logits(
            SimpleNamespace(get_providers=lambda: ["CPUExecutionProvider"]), None
        )


def test_real_cuda_iobinding_shares_output_storage_with_torch(tmp_path):
    import numpy as np
    import torch

    if not torch.cuda.is_available():
        pytest.skip("requires CUDA hardware")
    ort = pytest.importorskip("onnxruntime")
    onnx = pytest.importorskip("onnx")
    from venom_module_rider.cuda_execution import cuda_logits

    helper, proto = onnx.helper, onnx.TensorProto
    graph = helper.make_graph(
        [helper.make_node("Mul", ["pixels", "factor"], ["logits"])],
        "cuda-binding",
        [helper.make_tensor_value_info("pixels", proto.FLOAT, [1, 150, 8, 8])],
        [helper.make_tensor_value_info("logits", proto.FLOAT, [1, 150, 8, 8])],
        [helper.make_tensor("factor", proto.FLOAT, [1], [2.0])],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 9
    target = tmp_path / "cuda-binding.onnx"
    onnx.save(model, target)
    session = cuda_session(target, ort.SessionOptions())
    pixels = np.arange(150 * 8 * 8, dtype=np.float32).reshape(1, 150, 8, 8)
    output = cuda_logits(session, pixels)
    assert output.device.type == "cuda"
    torch.testing.assert_close(output, torch.from_numpy(pixels).to("cuda") * 2)
