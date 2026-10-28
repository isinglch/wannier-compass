"""Representation and executable arithmetic only. Scientific choices belong to roles."""

from __future__ import annotations

import json
import math

from jsonschema import Draft202012Validator


def obj(properties, required=None):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


TEXT = {"type": "string", "minLength": 1, "pattern": r"\S"}
PAIR = {
    "type": "array",
    "items": {"type": "integer", "minimum": 1},
    "minItems": 2,
    "maxItems": 2,
}
WINDOW = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
REF = obj(
    {
        "path": TEXT,
        "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "k_band_1based": PAIR,
        "energy_relative_eV": {"type": "number"},
        "channel": TEXT,
        "raw_projection": {"type": "number"},
    },
    ["path", "sha256"],
)
SOLVER = obj(
    {
        "num_iter": {"type": "integer", "const": 0},
        "dis_num_iter": {"type": "integer", "minimum": 1},
        "dis_conv_tol": {"type": "number", "exclusiveMinimum": 0},
        "dis_conv_window": {"type": "integer", "minimum": 1},
    }
)
PLAN_SCHEMA = obj(
    {
        "schema_version": {"const": "model-plan-v1"},
        "objective": TEXT,
        "input_revision": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "basis": {"type": "array", "items": TEXT, "minItems": 1, "uniqueItems": True},
        "num_wann": {"type": "integer", "minimum": 1},
        "target_band_ranges_1based": {"type": "array", "items": PAIR, "minItems": 1},
        "excluded_bands_1based": {
            "type": "array",
            "items": {"type": "integer", "minimum": 1},
            "uniqueItems": True,
        },
        "windows_relative_eV": obj({"dis_eV": WINDOW, "frozen_eV": WINDOW}),
        "solver": SOLVER,
        "reason": TEXT,
        "evidence_refs": {"type": "array", "items": REF, "minItems": 1},
    }
)


def schema_errors(value, schema):
    errors = [
        {
            "path": "/" + "/".join(map(str, error.absolute_path)),
            "code": "schema_" + error.validator,
            "message": error.message,
        }
        for error in Draft202012Validator(schema).iter_errors(value)
    ]

    def finite(item, path=""):
        if isinstance(item, float) and not math.isfinite(item):
            errors.append(
                {"path": path, "code": "nonfinite", "message": "Numbers must be finite"}
            )
        elif isinstance(item, dict):
            for key, child in item.items():
                finite(child, path + "/" + str(key))
        elif isinstance(item, list):
            for index, child in enumerate(item):
                finite(child, path + "/" + str(index))

    finite(value)
    return errors


def require_disentanglement_only(plan):
    """Enforce the operator's format contract even at direct executor entry points."""
    solver = plan.get("solver")
    value = solver.get("num_iter") if isinstance(solver, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != 0:
        raise ValueError("solver.num_iter must be explicitly 0 (disentanglement-only)")


def parse_answer(text):
    """Only remove explicitly delimited reasoning/fences; reject ambiguous objects."""
    text = text.strip()
    while "<think>" in text:
        start = text.index("<think>")
        end = text.find("</think>", start)
        if end < 0:
            raise ValueError("Unclosed reasoning block")
        text = text[:start] + text[end + 8 :]
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    def invalid_constant(value):
        raise ValueError("Invalid JSON numeric constant: " + value)

    value = json.loads(text, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("Return one JSON object")
    return value


ACTION_SCHEMAS = {
    "planner": {
        "oneOf": [
            obj({"action": {"const": "submit"}, "plan_id": TEXT}),
            obj({"action": {"const": "request_data"}, "reason": TEXT, "request": TEXT}),
        ]
    },
    "reviewer": {
        "oneOf": [
            obj({"action": {"const": "approve"}, "plan_id": TEXT, "reason": TEXT}),
            obj(
                {
                    "action": {"const": "request_revision"},
                    "plan_id": TEXT,
                    "issues": {"type": "array", "items": TEXT, "minItems": 1},
                    "reason": TEXT,
                }
            ),
        ]
    },
    "evaluator": {
        "oneOf": [
            obj(
                {
                    "action": {"const": "complete"},
                    "reason": TEXT,
                    "observations": {"type": "array", "items": TEXT},
                    "termination_basis": {
                        "enum": [
                            "objective_satisfied",
                            "no_justified_trial",
                            "data_blocker",
                        ]
                    },
                    "remaining_work": {"type": "array", "items": TEXT},
                }
            ),
            obj(
                {
                    "action": {"const": "iterate"},
                    "reason": TEXT,
                    "observations": {"type": "array", "items": TEXT},
                    "questions": {"type": "array", "items": TEXT, "minItems": 1},
                }
            ),
        ]
    },
    "strategist": {
        "oneOf": [
            obj(
                {
                    "action": {"const": "request_revision"},
                    "reason": TEXT,
                    "strategy": TEXT,
                    "questions": {"type": "array", "items": TEXT},
                }
            ),
            obj(
                {
                    "action": {"const": "stop"},
                    "reason": TEXT,
                    "termination_basis": {
                        "enum": ["no_justified_trial", "data_blocker"]
                    },
                    "remaining_work": {"type": "array", "items": TEXT},
                }
            ),
        ]
    },
}


def action_schema_for(role, *, scheduler=False):
    """One authoritative role contract; the operational extension is explicit."""
    if not scheduler:
        return ACTION_SCHEMAS[role]
    return {
        "oneOf": [
            *ACTION_SCHEMAS[role]["oneOf"],
            obj(
                {
                    "action": {"const": "request_operations"},
                    "reason": TEXT,
                    "request": TEXT,
                }
            ),
        ]
    }
