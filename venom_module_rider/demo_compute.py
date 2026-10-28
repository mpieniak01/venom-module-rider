"""Pi-side configuration fixture for the isolated compute acceptance demo."""

import time

from .compute_contract import TASKS

_COMPUTE_CONTRACT = "rider.compute"


GOALS = {
    "s12_venom_vision": (
        "Opis sceny w Venomie",
        "Venom scene description",
        ("camera", "frame_feed"),
    ),
    "s13_venom_voice": (
        "Rozmowa przez Venom",
        "Venom conversation",
        ("mic", "speaker"),
    ),
}


def goal_rows():
    return [
        {
            "name": name,
            "title": pl,
            "title_en": en,
            "resources": resources,
            "can_move": False,
            "option_schema": {},
            "members": [],
        }
        for name, (pl, en, resources) in GOALS.items()
    ]


def compute_response(state, path, body, envelope):
    """Route the simulated compute contract and preserve session identity."""
    if path == "/api/compute/config":
        return _compute_config(state, body, envelope)
    if path == "/api/compute/status":
        now = time.time()
        return envelope(
            {
                "protocol_version": 1,
                "revision": state.compute_revision,
                "selected_executor": state.compute_executor,
                "domains": {
                    domain: {
                        "state": "waiting_for_input" if state.goals[goal] else "off",
                        "stale": False,
                        "observed_at": now,
                        "quality": {"simulated": True},
                        "data": {},
                    }
                    for domain, goal in (
                        ("vision", "s12_venom_vision"),
                        ("voice", "s13_venom_voice"),
                    )
                },
            },
            _COMPUTE_CONTRACT,
        )
    return None


def _compute_config(state, body, envelope):
    """Validate and apply simulated executor selection before publishing it."""
    if body:
        if body.get("revision") != state.compute_revision:
            return envelope(
                {"error": "compute_configuration_conflict"},
                _COMPUTE_CONTRACT,
                status=409,
            )
        if body.get("executor_id") not in {None, "venom"}:
            return envelope(
                {"error": "compute_executor_unknown"}, _COMPUTE_CONTRACT, status=400
            )
        if any(state.goals[name] for name in GOALS):
            return envelope(
                {"error": "compute_stop_goals_first"}, _COMPUTE_CONTRACT, status=409
            )
        if body.get("executor_id") != state.compute_executor:
            state.compute_revision += 1
            state.compute_executor = body.get("executor_id")
    return envelope(
        {
            "protocol_version": 1,
            "revision": state.compute_revision,
            "device_id": "demo",
            "selected_executor": state.compute_executor,
            "persisted": True,
            "executors": [
                {"id": "venom", "tasks": list(TASKS), "credential_configured": True}
            ],
        },
        _COMPUTE_CONTRACT,
    )
