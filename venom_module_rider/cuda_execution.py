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
