"""Version-bound authority snapshots; never cache a frame or a motion verdict."""

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass

from .errors import RiderError

POLICY_PATH = "/api/navigation/config"
PIPELINE_PATH = "/api/resource/camera/pipeline"


@dataclass(frozen=True)
class SourcePolicySnapshot:
    key: tuple
    domain: object
    client: object
    models: object
    connection: object
    policy: dict
    pipeline: dict


class NativeAdmissionCache:
    def __init__(self):
        self.snapshots = OrderedDict()

    @staticmethod
    def _key(domain, models):
        runtime = getattr(models, "runtime", None)
        generation = getattr(runtime, "generation", None)
        if (
            type(generation) is not int
            or getattr(models, "switching", False)
            or getattr(domain, "native_admission_mutating", 0) != 0
        ):
            return None
        return (
            domain.session,
            domain.epoch,
            getattr(domain, "native_admission_epoch", 0),
            generation,
        )

    @staticmethod
    def _live(domain, hub, pipeline, connection):
        return (
            not hub.closed
            and connection is not None
            and hub.connection is connection
            and not connection.closed
            and connection.error is None
            and connection.reader is not None
            and not connection.reader.done()
            and bool(connection.frames)
            and connection.domain is domain
            and connection.client is domain.transport()
            and connection.binding == hub.binding(domain, pipeline)
        )

    @staticmethod
    def _cached_data(domain, path):
        entry = domain.cache.get(path)
        return entry[1].get("data") if entry else None

    @staticmethod
    def _same_policy(current, stored):
        # Service.data adds server_time to the cached dictionary in place.
        # Every actual policy field remains part of the immutable version proof.
        return isinstance(current, dict) and {
            key: value for key, value in current.items() if key != "server_time"
        } == {key: value for key, value in stored.items() if key != "server_time"}

    @staticmethod
    def _same_pipeline(current, stored):
        return (
            isinstance(current, dict)
            and current.get("state") in {"active", "waiting_frame"}
            and all(
                current.get(key) == stored.get(key)
                for key in (
                    "contract_id",
                    "contract_version",
                    "revision",
                    "transport_epoch",
                    "camera_session",
                    "active_profile",
                    "profile",
                )
            )
        )

    def put(self, device, domain, models, hub, pipeline, policy):
        key = self._key(domain, models)
        # Legacy models have no immutable selected artifact identity. Preserve
        # their original admission path instead of inventing such a proof.
        if (
            key is None
            or not isinstance(policy, dict)
            or type(policy.get("revision")) is not int
            or not (policy.get("perception") or {}).get("model_id")
            or (pipeline.get("active_profile") or {}).get("analysis_location")
            != "venom"
            or (pipeline.get("active_profile") or {}).get("remote_transport") != "mjpeg"
        ):
            return
        connection = hub.connection
        if not self._live(domain, hub, pipeline, connection):
            return
        self.snapshots[device] = SourcePolicySnapshot(
            key=key,
            domain=domain,
            client=domain.transport(),
            models=models,
            connection=connection,
            policy=deepcopy(policy),
            pipeline=deepcopy(pipeline),
        )
        self.snapshots.move_to_end(device)
        while len(self.snapshots) > 4:
            self.snapshots.popitem(last=False)

    def get(self, device, domain, models, hub, descriptor):
        stored = self.snapshots.get(device)
        if stored is None:
            return None
        source = descriptor.source
        try:
            matches = (
                stored.domain is domain
                and stored.client is domain.transport()
                and stored.models is models
                and domain.transport().device.id == device
                and stored.key == self._key(domain, models)
                and stored.policy.get("revision") == descriptor.frame.config_revision
                and self._same_policy(
                    self._cached_data(domain, POLICY_PATH), stored.policy
                )
                and self._same_pipeline(
                    self._cached_data(domain, PIPELINE_PATH), stored.pipeline
                )
                and source.transport_epoch == stored.pipeline["transport_epoch"]
                and source.camera_session == stored.pipeline["camera_session"]
                and source.pipeline_revision == stored.pipeline["revision"]
                and self._live(domain, hub, stored.pipeline, stored.connection)
            )
        except RiderError:
            matches = False
        if not matches:
            self.snapshots.pop(device, None)
            return None
        return deepcopy(stored.pipeline), deepcopy(stored.policy)


def admission_cache(request):
    state = request.app.state
    cache = getattr(state, "rider_native_admission", None)
    if cache is None:
        cache = NativeAdmissionCache()
        state.rider_native_admission = cache
    return cache
