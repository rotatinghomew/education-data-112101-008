import csv
import shutil
import tempfile
import unittest
from pathlib import Path

from analysis.model import load_dataset
from analysis.pipeline import DISPOSITION_LABELS, analyze, usable_value
from analysis.provenance import (build_manifest, diff_results, load_run,
                                 save_run)
from analysis.report import render_report

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _result():
    return analyze(load_dataset(DATA))


class UsableValueTests(unittest.TestCase):
    def test_missing_answer_is_not_zero(self):
        class Obs:
            response_state = "answered"
            raw_value = ""
        self.assertIsNone(usable_value(Obs()))
        Obs.response_state = "missing"
        Obs.raw_value = "4"
        self.assertIsNone(usable_value(Obs()))
        Obs.response_state = "answered"
        Obs.raw_value = "4"
        self.assertEqual(usable_value(Obs()), 4.0)


class DenominatorTests(unittest.TestCase):
    def test_strata_denominators_are_conserved(self):
        result = _result()
        for s in result["strata"]:
            with self.subTest(stratum=s["key"]):
                self.assertEqual(
                    sum(s["dispositions"].values()), s["n_enrolled"],
                    "各去向之和必须等于报名数：失访者不得从分母消失")
        total = sum(s["n_enrolled"] for s in result["strata"]
                    if s["construct"] == "family_confidence")
        self.assertEqual(total, result["n_participants"])

    def test_every_participant_appears_in_exactly_one_stratum_per_construct(self):
        result = _result()
        for construct in result["constructs"]:
            seen = [m["participant_ref"] for s in result["strata"]
                    if s["construct"] == construct for m in s["members"]]
            self.assertEqual(len(seen), len(set(seen)))


class VersionAndWindowTests(unittest.TestCase):
    def test_cross_version_pairs_are_detected(self):
        result = _result()
        cross = sum(s["paired"]["version_mix"]["cross_version"]
                    for s in result["strata"])
        self.assertGreater(cross, 0, "当前数据存在 M-1/M-2 跨版本配对，应被识别")

    def test_out_of_window_observations_are_flagged(self):
        result = _result()
        self.assertGreater(result["flag_counts"].get("obs_out_of_window", 0), 0,
                           "3 月仍使用 M-1（有效期至 2026-02-01）的观测应被标记窗外")

    def test_value_state_mismatches_are_flagged_not_silently_used(self):
        result = _result()
        self.assertGreater(result["flag_counts"].get("obs_answered_without_value", 0), 0)
        self.assertGreater(result["flag_counts"].get("obs_value_without_answered_state", 0), 0)


class CorrectionAndRerunTests(unittest.TestCase):
    """量表定义补正后重跑，差异应被解释。"""

    def _copy_data(self, tmp: Path) -> Path:
        target = tmp / "data_v2"
        shutil.copytree(DATA, target)
        return target

    def _rewrite_measure_window(self, data_dir: Path, valid_to: str):
        path = data_dir / "measures.csv"
        with path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
            fieldnames = list(rows[0])
        for row in rows:
            if row["measure_ref"] == "M-1":
                row["valid_to"] = valid_to
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def test_measure_definition_correction_changes_window_flags(self):
        before = _result()
        with tempfile.TemporaryDirectory() as tmp:
            corrected = self._copy_data(Path(tmp))
            self._rewrite_measure_window(corrected, "2026-04-01")
            after = analyze(load_dataset(corrected))
        self.assertGreater(before["flag_counts"].get("obs_out_of_window", 0), 0)
        self.assertEqual(after["flag_counts"].get("obs_out_of_window", 0), 0,
                         "M-1 有效期补正至 4 月后，窗外观测标记应消失")
        changes = diff_results(before, after)
        self.assertTrue(any(f["type"] == "obs_out_of_window" for f in changes["flags"]),
                        "diff 应指出窗外观测标记数量的变化")

    def test_superseded_observation_leaves_current_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            corrected = self._copy_data(Path(tmp))
            path = corrected / "observations.csv"
            with path.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
                fieldnames = list(rows[0])
            # 补正：新观测 O-2B 取代 O-2（P-1 的随访值由 4 改为 5）
            rows.append({"observation_ref": "O-2B", "participant_ref": "P-1",
                         "measure_ref": "M-2", "observed_at": "2026-03-01",
                         "value": "5", "response_state": "answered",
                         "supersedes": "O-2"})
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            after = analyze(load_dataset(corrected))
        self.assertIn("O-2", after["superseded_refs"])
        p1 = [m for s in after["strata"] for m in s["members"]
              if m["participant_ref"] == "P-1" and s["construct"] == "family_confidence"][0]
        self.assertEqual(p1["followup"]["observation_ref"], "O-2B")
        self.assertEqual(p1["change"], 2.0)


class RunStoreTests(unittest.TestCase):
    def test_save_and_load_roundtrip(self):
        dataset = load_dataset(DATA)
        result = analyze(dataset)
        manifest = build_manifest(dataset, label="测试运行")
        report = render_report(result, manifest)
        with tempfile.TemporaryDirectory() as tmp:
            save_run(tmp, manifest, result, report)
            loaded_manifest, loaded_result, loaded_report = load_run(tmp, manifest["run_id"])
        self.assertEqual(loaded_manifest["run_id"], manifest["run_id"])
        self.assertEqual(loaded_result["strata"], result["strata"])
        self.assertIn("家庭教育服务结果比较报告", loaded_report)


class ReportContentTests(unittest.TestCase):
    def test_report_states_non_causal_and_denominator(self):
        dataset = load_dataset(DATA)
        result = analyze(dataset)
        report = render_report(result, build_manifest(dataset))
        self.assertIn("观察性关联", report)
        self.assertIn("不能据此断定服务因果效果", report)
        self.assertIn("分母", report)
        self.assertIn("敏感性边界", report)
        self.assertIn("跨版本", report)


if __name__ == "__main__":
    unittest.main()
