"""Explicit offline jobs on the existing host API; no second model runtime."""

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


def request(base, path, body=None):
    headers = {"Accept": "application/json"}
    token = os.getenv("RIDER_ACCESS_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    encoded = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        encoded = json.dumps(body, allow_nan=False).encode()
    operation = urllib.request.Request(
        base.rstrip("/") + "/api/v1/rider/analysis" + path,
        data=encoded,
        headers=headers,
    )
    with urllib.request.urlopen(operation, timeout=30) as response:
        return json.load(response)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8800")
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("profiles")
    listing = commands.add_parser("runs")
    listing.add_argument("--session")
    create = commands.add_parser("submit")
    create.add_argument("session")
    create.add_argument("--request-id", default=None)
    create.add_argument(
        "--spec", type=Path, help="AnalysisSpec JSON; only approved profile IDs"
    )
    for name in ("show", "cancel", "report", "recover"):
        command = commands.add_parser(name)
        command.add_argument("id")
    comparison = commands.add_parser("compare")
    comparison.add_argument("first")
    comparison.add_argument("second")
    args = parser.parse_args(argv)
    try:
        if args.action == "profiles":
            value = request(args.url, "/profiles")
        elif args.action == "runs":
            value = request(
                args.url,
                "/runs"
                + (
                    "?" + urllib.parse.urlencode({"session_id": args.session})
                    if args.session
                    else ""
                ),
            )
        elif args.action == "submit":
            identity = args.request_id or uuid.uuid4().hex
            # Print intent first: an interrupted HTTP reply can be recovered by GET.
            print(
                json.dumps({"request_id": identity, "recovery": "recover " + identity})
            )
            value = request(
                args.url,
                "/runs",
                {
                    "session_id": args.session,
                    "request_id": identity,
                    "spec": json.loads(args.spec.read_text()) if args.spec else {},
                },
            )
        elif args.action == "compare":
            value = request(
                args.url,
                "/compare?"
                + urllib.parse.urlencode({"first": args.first, "second": args.second}),
            )
        else:
            identity = urllib.parse.quote(args.id, safe="")
            if args.action == "recover":
                value = request(args.url, "/requests/" + identity)
            elif args.action == "report":
                value = request(
                    args.url, "/runs/" + identity + "/artifacts/report.json"
                )
            elif args.action == "cancel":
                value = request(args.url, "/runs/" + identity + "/cancel", {})
            else:
                value = request(args.url, "/runs/" + identity + "?context=true")
        print(json.dumps(value, indent=2, allow_nan=False))
        return 0
    except urllib.error.HTTPError as exc:
        print(json.dumps({"error": json.loads(exc.read()), "status": exc.code}))
        return 1
    except (OSError, ValueError):
        print(json.dumps({"error": "analysis_cli_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
