"""Offline, read-only journal tool statistics. Never opens prompts or calls an API."""

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path


def audit(paths):
    totals = defaultdict(Counter)
    runs = []
    for supplied in paths:
        path = Path(supplied)
        path = path / "journal.jsonl" if path.is_dir() else path
        seen, counts, partial = set(), Counter(), 0
        snapshot = path.read_bytes()
        for line in snapshot.decode("utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                partial += 1
                continue
            if event.get("kind") != "tool":
                continue
            tool, role = event["tool"], event["role"]
            key = (
                role,
                tool,
                json.dumps(
                    event.get("arguments", {}), sort_keys=True, ensure_ascii=False
                ),
            )
            row = totals[tool]
            row["calls"] += 1
            row["same_role_repeat_calls"] += int(key in seen)
            row["observation_too_large"] += int(
                bool(event.get("result", {}).get("observation_too_large"))
            )
            row["tool_errors"] += int("tool_error" in event.get("result", {}))
            row["seconds"] += event.get("seconds", 0)
            row["result_utf8_bytes"] += len(
                json.dumps(event.get("result"), ensure_ascii=False).encode()
            )
            seen.add(key)
            counts[role] += 1
        runs.append(
            {
                "journal": str(path.resolve()),
                "sha256": sha256(snapshot).hexdigest(),
                "snapshot_bytes": len(snapshot),
                "tool_calls_by_role": dict(counts),
                "invalid_lines": partial,
            }
        )
    for row in totals.values():
        row["overflow_rate"] = row["observation_too_large"] / row["calls"]
        row["repeat_rate"] = row["same_role_repeat_calls"] / row["calls"]
    return {
        "runs": runs,
        "tools": dict(totals),
        "method": "Only kind=tool; duplicates mean exact tool+canonical arguments within one role/run, not overlapping data or repeated transport audit. Invalid lines are counted, never treated as successful calls. No model prompts or reasoning are read.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "journals", nargs="*", help="Run directories or journal.jsonl paths"
    )
    parser.add_argument("--runs-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    paths = list(map(Path, args.journals))
    if args.runs_root:
        paths.extend(sorted(args.runs_root.glob("*/journal.jsonl")))
    if not paths:
        parser.error("Supply journals or --runs-root")
    paths = list(
        dict.fromkeys(
            path.resolve() / "journal.jsonl" if path.is_dir() else path.resolve()
            for path in paths
        )
    )
    text = json.dumps(audit(paths), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
