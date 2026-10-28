import copy
import json

import numpy as np

from pythonagent.evidence import Evidence
from pythonagent.journal import Journal
from pythonagent.plans import PlanStore
from pythonagent.plotting import save_comparison
from pythonagent.state import file_sha
from pythonagent.tools import ToolBox
from pythonagent.tool_audit import audit
from support import CaseTest


class AnalysisToolTests(CaseTest):
    def tools(self):
        journal = Journal(self.run)
        return ToolBox(
            self.evidence, PlanStore(self.run, self.evidence, journal), journal
        )

    def path_cache(self, energy=None, segments=None):
        energy = np.array(
            energy if energy is not None else [[-2, -1, 1, 5], [-2, -0.5, 2, 6]],
            dtype=float,
        )
        nk, nb = energy.shape
        segments = segments or [[1, nk]]
        coords = np.zeros((nk, 3))
        for i, (first, last) in enumerate(segments):
            coords[first - 1 : last, 0] = np.linspace(
                i * 2, i * 2 + 1, last - first + 1
            )
        kpoints = self.case / "02_band/KPOINTS"
        kpoints.write_text("authenticated synthetic path\n")
        source = file_sha(kpoints)
        meta = {**self.evidence.meta, "source_files": {"kpoints": {"sha256": source}}}
        raw = np.zeros((1, nk, nb, 1, 9))
        raw[..., 0] = 0.7
        raw[..., 4] = 0.9
        np.savez(
            self.evidence.cache_path,
            metadata=json.dumps(meta),
            energies=energy[None] + meta["ef"],
            raw=raw,
            occupations=np.zeros((1, nk, nb)),
            kcoords=coords[None],
            path_geometry=json.dumps(
                {
                    "kcoords_type": "fractional",
                    "segments_1based": segments,
                    "ticks": [{"index_1based": 1, "label": "A"}],
                    "reciprocal_lattice_2pi_per_angstrom": (
                        2 * np.pi * np.eye(3)
                    ).tolist(),
                    "source_kpoints_sha256": source,
                }
            ),
        )
        self.evidence = Evidence(self.case, self.run)
        return coords

    def test_internal_columns_are_lossless_and_cover_every_source_state(self):
        self.path_cache(np.tile([-2, -1, 1, 5], (60, 1)))
        request = {"band_lo": 1, "band_hi": 4, "limit": 100}
        got = []
        while True:
            page = self.evidence.band_slice(**request)
            self.assertEqual(page["total_rows"], 240)
            c = page["columns"]
            for i, (k, b) in enumerate(c["k_band_1based"]):
                got.append((k, b))
                self.assertEqual(
                    c["energy_relative_eV"][i], self.evidence.energy[k - 1, b - 1]
                )
                self.assertEqual(
                    c["raw_projection"]["Cu:s"][i],
                    self.evidence.groups["Cu:s"][k - 1, b - 1],
                )
                self.assertEqual(c["occupation"][i], 0)
            if page["next_offset"] is None:
                self.assertTrue(page["complete"])
                break
            request = {**request, "offset": page["next_offset"]}
        self.assertEqual(got, [(k, b) for k in range(1, 61) for b in range(1, 5)])

    def test_internal_raw_pagination_preserves_next_source_coordinate(self):
        self.path_cache(np.tile([-2, -1, 1, 5], (40, 1)))
        request = {"band_lo": 1, "band_hi": 4, "limit": 100, "layout": "rows"}
        page = self.evidence.band_slice(**request)
        self.assertTrue(page["byte_limited"])
        self.assertLess(page["returned_rows"], 100)
        self.assertEqual(page["next_offset"], page["returned_rows"])
        second = self.evidence.band_slice(**{**request, "offset": page["next_offset"]})
        self.assertEqual(
            second["rows"][0]["k_band_1based"],
            [page["returned_rows"] // 4 + 1, page["returned_rows"] % 4 + 1],
        )

    def test_large_json_structure_pointers_address_original_record(self):
        (self.run / "observations").mkdir(exist_ok=True)
        record = {"a/b~c": [{"payload": "x" * 1000, "i": i} for i in range(50)]}
        (self.run / "observations/large.json").write_text(json.dumps(record))
        tools = self.tools()
        result = tools.call(
            "planner", "read_record", {"path": "run/observations/large.json"}
        )
        self.assertEqual(result["view"], "structure")
        pointer = result["rows"][0]["pointer"]
        self.assertEqual(pointer, "/a~1b~0c")
        got = []
        request = {
            "path": "run/observations/large.json",
            "pointer": pointer,
            "limit": 100,
        }
        while True:
            page = tools.call("planner", "read_record", request)
            self.assertNotIn("observation_too_large", page)
            got.extend(page["value"])
            if page["next_offset"] is None:
                break
            request = page["next_query"]["arguments"]
        self.assertEqual(got, record["a/b~c"])

    def test_repeat_notice_is_factual_not_a_quota_and_changed_data_is_read_fresh(self):
        (self.run / "observations").mkdir(exist_ok=True)
        path = self.run / "observations/data.json"
        path.write_text("[1,2,3]")
        tools = self.tools()
        args = {"path": "run/observations/data.json", "limit": 1}
        first = tools.call("planner", "read_record", args)
        second = tools.call("planner", "read_record", args)
        self.assertNotIn("repeated_identical_observation", first)
        self.assertTrue(second["repeated_identical_observation"])
        self.assertEqual(second["next_query"], first["next_query"])
        self.assertEqual(second["value"], [1])
        self.assertNotIn(
            "repeated_identical_observation",
            tools.call("reviewer", "read_record", args),
        )
        path.write_text("[4,5,6]")
        fresh = tools.call("planner", "read_record", args)
        self.assertEqual(fresh["value"], [4])
        self.assertNotIn("repeated_identical_observation", fresh)

    def test_overview_reuses_authenticated_geometry_and_can_request_original_rows(self):
        self.path_cache()
        table = self.tools().call("planner", "anchor_table", {})
        self.assertEqual(table["columns"]["band"], [1, 2, 3, 4])
        self.assertEqual(table["k_path"]["segments"][0]["start_label"], "A")
        rows = self.evidence.overview(layout="rows")["rows"]
        self.assertEqual(
            table["columns"]["raw_mean"]["Cu:s"], [r["raw_mean"]["Cu:s"] for r in rows]
        )
        (self.case / "02_band/KPOINTS").write_text("modified")
        self.assertEqual(self.evidence.overview()["k_path"]["status"], "unavailable")
        self.assertIn("tool_error", self.tools().call("planner", "ef_crossings", {}))

    def test_ef_brackets_respect_gaps_exact_zeros_and_endpoint_projection_only(self):
        self.path_cache(
            [
                [-1, -1, 2, 0],
                [0, 1, 2, 0],
                [-1, 2, 2, 0],
                [1, 2, 2, 0],
                [1, 1, 2, 0],
                [1, -1, 2, 0],
            ],
            [[1, 3], [4, 6]],
        )
        tools = self.tools()
        request = {"limit": 1}
        events = []
        while True:
            result = tools.call("planner", "ef_crossings", request)
            self.assertEqual(result["sign_change_bracket_count"], 2)
            self.assertEqual(result["sample_at_ef_count"], 7)
            self.assertEqual(result["bands_with_sign_changes_1based"], [2])
            self.assertEqual(result["bands_with_exact_ef_samples_1based"], [1, 4])
            events.extend(result["rows"])
            if result["next_offset"] is None:
                break
            request = result["next_query"]["arguments"]
        bracket = next(r for r in events if r["kind"] == "sign_change_bracket")
        self.assertEqual(bracket["linear_estimate"]["fraction_between_samples"], 0.5)
        self.assertNotIn("raw_projection", bracket["linear_estimate"])
        self.assertFalse(
            any(r["kind"] == "sign_change_bracket" and r["band"] == 1 for r in events)
        )

    def test_character_runs_preserve_ties_zeros_and_segment_boundaries(self):
        self.path_cache(np.tile([-2, -1, 1, 5], (6, 1)), [[1, 3], [4, 6]])
        for w in self.evidence.groups.values():
            w[:, 0] = 0
        self.evidence.groups["Cu:s"][:5, 0] = 1
        self.evidence.groups["Cu:p"][2, 0] = 1
        tools = self.tools()
        request = {"band": 1, "limit": 1}
        runs = []
        while True:
            result = tools.call("planner", "orbital_character", request)
            runs.extend(result["rows"])
            if result["next_offset"] is None:
                break
            request = result["next_query"]["arguments"]
        self.assertEqual(
            [r["k_range_1based"] for r in runs], [[1, 2], [3, 3], [4, 5], [6, 6]]
        )
        self.assertEqual(runs[1]["leading_channels"], ["Cu:s", "Cu:p"])
        self.assertEqual(runs[-1]["leading_channels"], [])
        self.assertEqual(sum(r["samples"] for r in runs), 6)

    def test_missing_geometry_never_invents_a_continuous_path(self):
        tools = self.tools()
        self.assertEqual(self.evidence.overview()["k_path"]["status"], "unavailable")
        self.assertEqual(
            tools.call("planner", "orbital_character", {"band": 1})["segment_status"],
            "unavailable",
        )
        self.assertIn("tool_error", tools.call("planner", "ef_crossings", {}))

    def native_trial(self, name, plan, offset, coords, baseline=None):
        directory = self.run / "trials" / name
        directory.mkdir(parents=True)
        (directory / "approved-plan.json").write_text(json.dumps(plan))
        path = directory / "comparison.npz"
        save_comparison(
            path,
            self.evidence,
            plan,
            coords,
            self.evidence.energy[:, :3] + offset,
            baseline=baseline,
        )
        (directory / "result_binding.json").write_text(
            json.dumps({"comparison_sha256": file_sha(path)})
        )
        return directory

    def test_trial_compare_fixed_population_and_no_delta_for_changed_target(self):
        coords = self.path_cache()
        plan = self.plan()
        other = copy.deepcopy(plan)
        other["target_band_ranges_1based"] = [[1, 2]]
        other["solver"]["dis_num_iter"] = 200
        self.native_trial("a", plan, 0.01, coords, baseline=plan)
        self.native_trial("b", other, 0.02, coords, baseline=plan)
        tools = self.tools()
        result = tools.call("evaluator", "trial_compare", {"left": "a", "right": "b"})
        self.assertAlmostEqual(result["metric_delta_right_minus_left"]["mae_eV"], 0.01)
        self.assertIn(
            "/solver/dis_num_iter", [c["field"] for c in result["changed_plan_fields"]]
        )
        result = tools.call(
            "evaluator",
            "trial_compare",
            {"left": "a", "right": "b", "population": "target"},
        )
        self.assertIsNone(result["metric_delta_right_minus_left"])
        self.assertFalse(result["comparison_facts"]["same_target_population"])
        self.assertNotIn("best_plan", result)

    def test_trial_compare_rejects_changed_bound_spectra_and_path_traversal(self):
        coords = self.path_cache()
        plan = self.plan()
        directory = self.native_trial("a", plan, 0.01, coords)
        (directory / "comparison.npz").write_bytes(b"changed")
        result = self.tools().call(
            "evaluator", "trial_compare", {"left": "a", "right": "a"}
        )
        self.assertIn("binding", result["tool_error"])
        self.assertIn(
            "tool_error",
            self.tools().call(
                "evaluator", "trial_compare", {"left": "../a", "right": "a"}
            ),
        )

    def test_residual_filters_apply_before_limit_and_use_historical_trial_windows(self):
        trial = self.run / "trials/a"
        trial.mkdir(parents=True)
        plan = self.plan()
        plan["windows_relative_eV"]["frozen_eV"] = [-3, -0.6]
        (trial / "approved-plan.json").write_text(json.dumps(plan))
        region = {
            "counts": {"missing_states": 1},
            "per_k": [
                {
                    "k_index_1based": 1,
                    "pairs": [
                        {"dft_band_1based": 1, "dft_eV": -2, "error_eV": 0.1},
                        {"dft_band_1based": 3, "dft_eV": 1, "error_eV": 9},
                    ],
                    "unmatched_dft_bands": [2],
                }
            ],
        }
        (trial / "diagnostics.json").write_text(json.dumps({"baseline_target": region}))
        r = self.tools().call(
            "evaluator",
            "residual_ranking",
            {"trial": "a", "top_n": 1, "region": "frozen"},
        )
        self.assertEqual(r["rows"][0]["dft_band_1based"], 1)
        self.assertEqual(
            r["filtered_counts"], {"matched_states": 1, "missing_states": 1}
        )
        self.assertEqual(r["missing_witnesses"][0]["dft_band_1based"], 2)
        self.assertEqual(r["filters"]["region"], "frozen")

    def test_tool_audit_counts_calls_not_transport_events_and_separates_roles(self):
        path = self.run / "journal.jsonl"
        events = [
            {"kind": "tool", "tool": "x", "role": role, "arguments": {}, "result": {}}
            for role in ["planner", "planner", "reviewer"]
        ]
        events.append({"kind": "transport_attempt_completed"})
        path.write_text("\n".join(json.dumps(e) for e in events))
        result = audit([path])
        self.assertEqual(result["tools"]["x"]["calls"], 3)
        self.assertEqual(result["tools"]["x"]["same_role_repeat_calls"], 1)
