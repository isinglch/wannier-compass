"""Deliver the MCP project's original scientific knowledge, without resummarizing it.

All files are local immutable-release snapshots. Slice selection routes original
paragraphs/chapters to roles; it never derives a scientific decision or numerical
policy. Every delivered excerpt records its exact original line range and hash.
"""

from functools import lru_cache
import hashlib
import json
import re
from pathlib import Path

BASE = Path(__file__).parent / "guides"


@lru_cache(maxsize=1)
def source_catalog():
    value = json.loads((BASE / "mcp-sources.json").read_text())
    for document in value["documents"]:
        local = BASE / document["path"].removeprefix("guides/")
        if not local.resolve().is_relative_to(BASE.resolve()):
            raise ValueError("Scientific source path escapes the bundled snapshot")
        raw = local.read_bytes()
        if hashlib.sha256(raw).hexdigest() != document["sha256"]:
            raise ValueError(
                "Bundled scientific source differs from its recorded original: "
                + document["path"]
            )
    return value


def original(name, *, before=None, paragraphs=None):
    """Read a whole original or explicitly named original paragraphs, byte-for-byte."""
    suffix = "mcp/" + name
    record = next(
        d for d in source_catalog()["documents"] if d["path"] == "guides/" + suffix
    )
    raw = (BASE / suffix).read_bytes()
    if hashlib.sha256(raw).hexdigest() != record["sha256"]:
        raise ValueError(
            "Scientific guidance changed during this run: " + record["path"]
        )
    lines = raw.decode().splitlines(keepends=True)
    ranges = []
    if paragraphs is not None:
        for prefix in paragraphs:
            start = next(
                (i for i, line in enumerate(lines) if line.startswith(prefix)), None
            )
            if start is None:
                raise ValueError(
                    "Required scientific paragraph is missing: " + name + ": " + prefix
                )
            stop = start + 1
            while (
                stop < len(lines)
                and lines[stop].strip()
                and not re.match(r"^\d+\. ", lines[stop])
            ):
                stop += 1
            ranges.append((start, stop))
    else:
        stop = (
            next((i for i, line in enumerate(lines) if line.startswith(before)), None)
            if before
            else len(lines)
        )
        if stop is None:
            raise ValueError(
                "Scientific excerpt boundary is missing: " + name + ": " + before
            )
        ranges = [(0, stop)]
    fragments = []
    for start, stop in sorted(set(ranges)):
        content = "".join(lines[start:stop])
        fragments.append(
            {
                "source": {
                    key: record[key]
                    for key in ("path", "source_repository_path", "sha256")
                },
                "start_line_1based": start + 1,
                "end_line_1based": stop,
                "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                "content": content,
            }
        )
    return fragments


def retry_guidance():
    # Planner receives this only once a real execution attempt exists. Evaluator
    # and strategist have it in their fixed system; state updates retain it intact.
    return [
        {
            **item,
            "operational_scope": "Quoted MCP reference. SOURCE-ADAPTATION governs current role, fields, tools, metrics and stopping policy; do not execute legacy instructions.",
        }
        for item in original("docs/WINDOW-RETRIES.md")
    ]


def role_guidance(role):
    documents = original("docs/CONSTRUCTION-OVERVIEW.md")
    documents += original(
        "ROOT-SKILL.md",
        paragraphs=("Declare the task and concentrated deep-block roles",),
    )
    if role in ("planner", "reviewer"):
        documents += original("SKILL.md", paragraphs=("1. Declare the physical task",))
        documents += original("docs/FIRST-TRIAL-CARD.md", before="## Tool loop")
        documents += original("docs/WORKFLOW-DECISIONS.md")
        documents += original("docs/PHYSICAL-REASONING.md")
    elif role in ("evaluator", "strategist"):
        documents += original(
            "SKILL.md", paragraphs=("After an actual trial, use the short",)
        )
        documents += retry_guidance()
        documents += original(
            "docs/WANNIER-COMPARISON.md",
            paragraphs=(
                "Read official execution evidence/products",
                "Diagnostics use per-k one-to-one",
                "Default displays extend",
            ),
        )
        documents += original(
            "docs/WANNIER-EXECUTION.md",
            paragraphs=(
                "DIS convergence uses",
                "| Status |",
                "All retain",
            ),
        )
    else:
        raise ValueError("Unknown scientific role: " + role)
    return documents


def render(documents):
    return "\n\n".join(
        "BEGIN QUOTED MCP REFERENCE (apply through Current workflow protocol; legacy commands/fields are not current instructions)\nMCP scientific source: "
        + item["source"]["source_repository_path"]
        + "\nLocal path: "
        + item["source"]["path"]
        + "\nSource SHA256: "
        + item["source"]["sha256"]
        + "\nOriginal lines "
        + str(item["start_line_1based"])
        + "–"
        + str(item["end_line_1based"])
        + "\n\n"
        + item["content"]
        + "\nEND QUOTED MCP REFERENCE"
        for item in documents
    )


def reference_catalog():
    # Titles and paths only. Full chapter/line indexes can be read on demand,
    # rather than injecting the entire software command manual into every role.
    return {
        "manifest": "guides/mcp-sources.json",
        "instructions": "Use read_record on manifest /documents with limit=5 for chapter headings and line offsets; read_text retrieves original paragraphs. Follow next_offset until the required chapter is complete. Source-document links are relative to their original MCP layout; resolve them with source_repository_path in the manifest, never through the old project filesystem.",
        "documents": [
            {k: d[k] for k in ("title", "path")} for d in source_catalog()["documents"]
        ],
    }
