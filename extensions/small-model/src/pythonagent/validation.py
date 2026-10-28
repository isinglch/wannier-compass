"""Objective contract checks; no orbital prior, projection floor or tail gate."""

import math

from .channels import basis_dimension
from .contracts import PLAN_SCHEMA, schema_errors
from .state import file_sha


def validate_plan(plan, evidence):
    errors = schema_errors(plan, PLAN_SCHEMA)
    if errors:
        return {"valid": False, "errors": errors, "facts": None}

    def fail(path, code, message, **facts):
        errors.append({"path": path, "code": code, "message": message, **facts})

    try:
        evidence.ensure_current()
    except ValueError as error:
        fail("/input_revision", "source_changed", str(error))
    if plan["input_revision"] != evidence.revision:
        fail(
            "/input_revision",
            "source_revision_mismatch",
            "Copy the current input revision",
            expected=evidence.revision,
        )
    if set(plan["basis"]) - set(evidence.groups):
        fail(
            "/basis",
            "unknown_channel",
            "A channel is absent from this source layout",
            available=list(evidence.groups),
        )
    else:
        expected = basis_dimension(
            plan["basis"],
            evidence.meta["species"],
            evidence.meta["counts"],
            evidence.meta["spin_mode"],
        )
        if expected != plan["num_wann"]:
            fail(
                "/num_wann",
                "dimension_mismatch",
                "Whole-shell dimension disagrees with the selected basis",
                expected=expected,
            )
    nb = evidence.energy.shape[1]
    target = set()
    for index, (first, last) in enumerate(plan["target_band_ranges_1based"]):
        if not 1 <= first <= last <= nb:
            fail(
                f"/target_band_ranges_1based/{index}",
                "source_range",
                "Band range exceeds the source or is reversed",
                computed_bands=nb,
            )
        elif target & set(range(first, last + 1)):
            fail(
                f"/target_band_ranges_1based/{index}",
                "overlap",
                "Target ranges overlap",
            )
        target.update(range(first, min(last, nb) + 1))
    excluded = set(plan["excluded_bands_1based"])
    if any(b > nb for b in excluded) or len(excluded) >= nb:
        fail(
            "/excluded_bands_1based",
            "source_range",
            "Exclude only existing bands and retain at least one",
        )
    if plan["num_wann"] > nb - len(excluded):
        fail(
            "/num_wann",
            "retained_dimension",
            "num_wann exceeds retained source-band capacity",
            available=nb - len(excluded),
        )
    if excluded & target:
        fail(
            "/excluded_bands_1based",
            "contradictory_membership",
            "A source band is simultaneously excluded and declared a target",
            bands=sorted(excluded & target),
        )
    low, high = plan["windows_relative_eV"]["dis_eV"]
    fl, fh = plan["windows_relative_eV"]["frozen_eV"]
    if not low <= fl < fh <= high:
        fail(
            "/windows_relative_eV",
            "window_order",
            "Require dis_min <= frozen_min < frozen_max <= dis_max",
        )
    for index, reference in enumerate(plan["evidence_refs"]):
        path = f"/evidence_refs/{index}"
        try:
            source = evidence.resolve(reference["path"])
            if file_sha(source) != reference["sha256"]:
                fail(
                    path + "/sha256",
                    "evidence_revision",
                    "Evidence hash does not match the current file",
                )
            numerical = any(
                k in reference
                for k in ("energy_relative_eV", "raw_projection", "channel")
            )
            if numerical and (
                source != evidence.cache_path or "k_band_1based" not in reference
            ):
                fail(
                    path,
                    "unbound_numeric_claim",
                    "Numerical state claims require cache source and [k,band]",
                )
                continue
            if "k_band_1based" in reference:
                k, b = reference["k_band_1based"]
                if (
                    source != evidence.cache_path
                    or not 1 <= k <= evidence.energy.shape[0]
                    or not 1 <= b <= nb
                ):
                    fail(
                        path + "/k_band_1based",
                        "source_coordinate",
                        "Coordinate is outside this source cache",
                    )
                    continue
                checks = {}
                if "energy_relative_eV" in reference:
                    checks["energy_relative_eV"] = float(evidence.energy[k - 1, b - 1])
                if "raw_projection" in reference:
                    channel = reference.get("channel")
                    if channel not in evidence.groups:
                        fail(
                            path + "/channel",
                            "unknown_channel",
                            "Projection claim needs a known channel",
                        )
                        continue
                    checks["raw_projection"] = float(
                        evidence.groups[channel][k - 1, b - 1]
                    )
                for key, actual in checks.items():
                    # Floating-point representation tolerance, never a scientific cutoff.
                    if not math.isclose(
                        reference[key], actual, rel_tol=1e-9, abs_tol=1e-7
                    ):
                        fail(
                            path + "/" + key,
                            "source_value_mismatch",
                            "Claim differs from source value",
                            actual=actual,
                        )
        except (OSError, ValueError, KeyError) as error:
            fail(path, "unreadable_evidence", str(error))
    facts = None
    if not errors:
        facts = evidence.window_diagnostics(plan)
        # Path counts are evidence; only the actual input grid controls execution.
        if evidence.meta.get("sampling") in ("wannier_mesh", "mesh"):
            errors.extend(execution_count_errors(facts))
    return {
        "valid": not errors,
        "errors": errors,
        "facts": facts,
        "scope": "Format, source claims, arithmetic and explicitly identified execution inputs. Scientific approval is a reviewer decision.",
    }


def execution_count_errors(facts):
    errors = []
    for field, code, message in [
        (
            "nouter_below_num_wann_kpoints",
            "outer_dimension",
            "Actual input has fewer outer states than num_wann",
        ),
        (
            "nfrozen_above_num_wann_kpoints",
            "frozen_dimension",
            "Actual input has more frozen states than num_wann",
        ),
    ]:
        if facts[field]:
            errors.append(
                {
                    "path": "/windows_relative_eV",
                    "code": code,
                    "message": message,
                    "kpoints": facts[field],
                }
            )
    return errors
