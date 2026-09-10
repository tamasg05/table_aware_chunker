"""Generate immutable Git metadata for the REST wrapper image."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path


_GIT_COMMIT_PATTERN = re.compile(r"[0-9a-fA-F]{7,64}")


def _abbreviate_commit(value: str) -> str:
    """Validate and abbreviate a full or already abbreviated Git commit ID."""
    commit = value.strip()
    if not commit or commit.casefold() == "unknown":
        return "unknown"
    if _GIT_COMMIT_PATTERN.fullmatch(commit) is None:
        raise ValueError("Git commit must contain 7 to 64 hexadecimal characters")
    return commit[:7].lower()


def _utc_commit_time(value: str) -> str:
    """Validate an ISO-8601 commit time and normalize it to UTC."""
    commit_time = value.strip()
    if not commit_time or commit_time.casefold() == "unknown":
        return "unknown"
    try:
        parsed = datetime.fromisoformat(commit_time.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Git commit time must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("Git commit time must include a timezone")
    return (
        parsed.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def create_build_info(commit: str, commit_time: str) -> dict:
    """Create the static build-information document consumed by the service."""
    return {
        "git": {
            "commit": {
                "id": {"abbrev": _abbreviate_commit(commit)},
                "time": _utc_commit_time(commit_time),
            }
        }
    }


def main() -> None:
    """Write validated build information to the requested JSON file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default="unknown")
    parser.add_argument("--commit-time", default="unknown")
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()

    try:
        build_info = create_build_info(arguments.commit, arguments.commit_time)
    except ValueError as exc:
        parser.error(str(exc))
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(build_info, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
