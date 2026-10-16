"""Research CLI compatibility exports from the packaged depth implementation."""

from venom_module_rider.depth_candidates import CHECKPOINT_HASHES as CHECKPOINT_HASHES
from venom_module_rider.depth_candidates import MODELS as MODELS
from venom_module_rider.depth_candidates import SDK_REVISION as SDK_REVISION
from venom_module_rider.depth_candidates import Candidate as Candidate
from venom_module_rider.depth_candidates import file_hash as file_hash
from venom_module_rider.depth_candidates import resize_depth as resize_depth
from venom_module_rider.depth_candidates import validate_depth as validate_depth
from venom_module_rider.depth_candidates import verify_sdk as _verify_sdk


def verify_sdk(sdk):
    """Keep the research revision override while sharing production validation."""
    return _verify_sdk(sdk, expected_revision=SDK_REVISION)
