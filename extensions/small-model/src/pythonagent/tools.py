"""Role tools and bounded observations. Only write_plan mutates a scientific proposal."""

import json
import time
import uuid

from .contracts import schema_errors, obj
from .specs import tool_specs_for
from .state import file_sha, immutable_json, digest
from .observations import encoded_size
from .trial_analysis import trial_compare, residual_ranking
from .statistics_only import require_access
from .execution_facts import execution_facts


class ToolBox:
    def __init__(self, evidence, plans, journal):
        self.evidence, self.plans, self.journal = evidence, plans, journal
        self.delivered = set()

    def call(self, role, name, arguments):
        started = time.monotonic()
        spec = tool_specs_for(role).get(name)
        try:
            if spec is None:
                raise ValueError("Tool is unavailable to this role")
            errors = schema_errors(
                arguments,
                obj({"plan": {}}) if name == "write_plan" else spec["arguments"],
            )
            if errors:
                raise ValueError(json.dumps(errors, ensure_ascii=False))
            if name == "execution_facts":
                result = execution_facts(self.evidence, **arguments)
            elif name == "write_plan":
                result = self.plans.write(role, arguments["plan"])
            elif name == "anchor_table":
                result = self.evidence.overview(**arguments)
            elif name in (
                "ef_crossings",
                "orbital_character",
                "read_record",
                "list_records",
            ):
                result = getattr(self.evidence, name)(**arguments)
            elif name == "window_diagnostics":
                plan, meta, _ = self.plans.read(arguments.get("plan_id"))
                result = {
                    "plan_id": meta["plan_id"],
                    **self.evidence.window_diagnostics(plan),
                }
            elif name == "projection_audit":
                plan, meta, _ = self.plans.read(arguments["plan_id"])
                audit = self.evidence.projection_audit(
                    plan,
                    region=arguments.get("region", "frozen"),
                    weak_selected_max=arguments.get("weak_selected_max"),
                    include_states=False,
                )
                audit["plan_id"] = meta["plan_id"]
                path = self.evidence.run / "observations" / (uuid.uuid4().hex + ".json")
                immutable_json(path, audit)
                result = {k: v for k, v in audit.items() if k != "states"}
                result.update(
                    full_record="run/" + str(path.relative_to(self.evidence.run)),
                    full_record_sha256=file_sha(path),
                    archive_scope="Summary statistics and bounded witnesses only; no /states table.",
                )
            elif name == "read_text":
                path = require_access(self.evidence, arguments["path"], "text")
                offset = arguments.get("offset", 0)
                limit = arguments.get("limit", 80)
                with path.open() as stream:
                    total = 0
                    selected = []
                    for total, line in enumerate(stream, 1):
                        if offset < total <= offset + limit:
                            selected.append(line)
                    text = "".join(selected)
                result = {
                    "source": {"path": arguments["path"], "sha256": file_sha(path)},
                    "offset": offset,
                    "text": text,
                    "total_lines": total,
                    "next_offset": offset + limit if offset + limit < total else None,
                }
            elif name == "trial_compare":
                result = trial_compare(self.evidence, **arguments)
            else:
                result = self.residual_ranking(**arguments)
            requested_limit = result.get("requested_limit")
            while (
                encoded_size(result) > 14000
                and result.get("returned_rows", 0) > 1
                and name
                in (
                    "anchor_table",
                    "orbital_character",
                    "ef_crossings",
                    "read_record",
                    "list_records",
                )
            ):
                smaller = {**arguments, "limit": max(1, result["returned_rows"] // 2)}
                method = (
                    self.evidence.overview
                    if name == "anchor_table"
                    else getattr(self.evidence, name)
                )
                result = method(**smaller)
                result.update(requested_limit=requested_limit, byte_limited=True)
            if result.get("next_offset") is not None:
                result["next_query"] = {
                    "tool": name,
                    "arguments": {**arguments, "offset": result["next_offset"]},
                }
            if encoded_size(result) > 14000:
                identifier = uuid.uuid4().hex
                path = self.evidence.run / "observations" / f"{identifier}.json"
                immutable_json(path, result)
                # Never silently truncate evidence or a validation error list.
                result = {
                    "observation_too_large": True,
                    "complete": False,
                    "full_record": f"run/observations/{identifier}.json",
                    "record_keys": list(result) if isinstance(result, dict) else [],
                    "archive_scope": "This result only, not the complete source query.",
                    "source_page": {
                        k: result[k]
                        for k in (
                            "offset",
                            "returned_rows",
                            "total_rows",
                            "next_offset",
                            "next_query",
                        )
                        if k in result
                    },
                    "suggested_reads": self.record_hints(result),
                    "instruction": "Read specific fields from full_record. After this archived result, source_page.next_query continues the ORIGINAL query; archive pages have a separate cursor. No data was silently dropped.",
                }
            identity = digest(
                {"role": role, "tool": name, "arguments": arguments, "result": result}
            )
            if identity in self.delivered:
                result["repeated_identical_observation"] = True
            self.delivered.add(identity)
        except (ValueError, TypeError, KeyError, IndexError, OSError) as error:
            result = {"tool_error": str(error)}
        self.journal.record(
            "tool",
            {
                "role": role,
                "tool": name,
                "arguments": arguments,
                "seconds": time.monotonic() - started,
                "result": result,
            },
        )
        return result

    @staticmethod
    def record_hints(value, pointer="", depth=0):
        if isinstance(value, list):
            return [{"pointer": pointer, "limit": 10, "total_rows": len(value)}]
        if not isinstance(value, dict) or depth >= 3:
            return []
        hints = []
        for key, child in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            hints.extend(
                ToolBox.record_hints(child, pointer + "/" + escaped, depth + 1)
            )
        return hints[:12]

    def residual_ranking(self, **arguments):
        return residual_ranking(self.evidence, **arguments)
