"""Pi-side configuration fixture for the isolated compute acceptance demo."""

import time

from .compute_contract import TASKS

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
    if path == "/api/compute/config":
        if body:
            if body.get("revision") != state.compute_revision:
                return envelope(
                    {"error": "compute_configuration_conflict"},
                    "rider.compute",
                    status=409,
                )
            if body.get("executor_id") not in {None, "venom"}:
                return envelope(
                    {"error": "compute_executor_unknown"}, "rider.compute", status=400
                )
            if any(state.goals[name] for name in GOALS):
                return envelope(
                    {"error": "compute_stop_goals_first"}, "rider.compute", status=409
                )
            if body["executor_id"] != state.compute_executor:
                state.compute_revision += 1
                state.compute_executor = body["executor_id"]
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
            "rider.compute",
        )
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
            "rider.compute",
        )
    return None
