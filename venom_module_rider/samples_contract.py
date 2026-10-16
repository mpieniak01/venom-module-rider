"""Shared capture and annotation shapes for operator API and local tooling."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt

from .api.schemas import StrictModel

RequestID = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
RecordID = Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{32}$")]


class CollectionPolicy(StrictModel):
    kind: Literal["manual", "sequence", "import"] = "manual"
    duration_s: Annotated[StrictFloat, Field(ge=1, le=30)] = 10.0
    interval_ms: Annotated[StrictInt, Field(ge=150, le=2000)] = 200
    max_frames: Annotated[StrictInt, Field(ge=1, le=150)] = 50
    scene_group: Annotated[str, Field(strict=True, max_length=128)] = ""
    dataset_split: Literal["unassigned", "tuning", "acceptance"] = "unassigned"
    import_digest: Annotated[str, Field(strict=True, pattern=r"^$|^[a-f0-9]{64}$")] = ""
    order_source: Literal["receipt", "manifest", "filename-unverified"] = "receipt"


class CollectionCreate(StrictModel):
    request_id: RequestID
    source_device_id: RequestID
    policy: CollectionPolicy = CollectionPolicy()


class CollectionEnd(StrictModel):
    interrupted: StrictBool = False


class CaptureRequest(StrictModel):
    session_id: RecordID
    capture_request_id: RequestID
    frame_key: RecordID
    allow_historical: StrictBool = False


class LabelValues(StrictModel):
    class_name: Annotated[str, Field(strict=True, max_length=128)] = "unknown"
    scene: Annotated[str, Field(strict=True, max_length=128)] = ""
    expected_state: Literal["clear", "obstacle", "unknown"] = "unknown"
    obstacle_side: Literal["left", "right", "centre", "both", "none", "unknown"] = (
        "unknown"
    )
    review: Literal["pending", "approved", "rejected"] = "pending"
    note: Annotated[str, Field(strict=True, max_length=1024)] = ""


class LabelRequest(StrictModel):
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    values: LabelValues
