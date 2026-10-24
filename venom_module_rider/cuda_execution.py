"""CUDA-only model execution; missing acceleration must not become CPU inference."""


def require_cuda():
    try:
        import torch
    except ImportError:
        raise ValueError("vision_cuda_unavailable") from None

    if not torch.cuda.is_available():
        raise ValueError("vision_cuda_unavailable")
    return "cuda"


def cuda_session(path, options):
    require_cuda()
    try:
        import onnxruntime as ort
    except ImportError:
        raise ValueError("vision_cuda_provider_unavailable") from None

    if "CUDAExecutionProvider" not in ort.get_available_providers():
        raise ValueError("vision_cuda_provider_unavailable")
    ort.preload_dlls()
    options.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
    session = ort.InferenceSession(
        str(path), options, providers=["CUDAExecutionProvider"]
    )
    if session.get_providers()[0] != "CUDAExecutionProvider":
        raise ValueError("vision_cuda_provider_unavailable")
    session.disable_fallback()
    return session


def cuda_logits(session, pixels):
    """Keep ORT outputs on CUDA; DLPack shares storage with torch for reduction."""
    import numpy as np
    import torch

    require_cuda()
    if session.get_providers()[0] != "CUDAExecutionProvider":
        raise ValueError("vision_cuda_provider_unavailable")
    binding = session.io_binding()
    binding.bind_cpu_input(session.get_inputs()[0].name, np.ascontiguousarray(pixels))
    binding.bind_output(session.get_outputs()[0].name, "cuda", 0)
    session.run_with_iobinding(binding)
    binding.synchronize_outputs()
    outputs = binding.get_outputs()
    if len(outputs) != 1 or outputs[0].device_name() != "cuda":
        raise ValueError("vision_cuda_provider_unavailable")
    # ORT synchronizes its producer before torch consumes the shared allocation.
    tensor = torch.from_dlpack(outputs[0])
    if tensor.device.type != "cuda":
        raise ValueError("vision_cuda_provider_unavailable")
    return tensor
