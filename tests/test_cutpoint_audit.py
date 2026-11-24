from fractions import Fraction
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

import cutpoint_audit as audit


SCRIPT = Path(audit.__file__).resolve()


def probe(pts, time_base="1/1000", start_pts=500):
    return {
        "streams": [{"index": 1, "codec_type": "video", "start_pts": start_pts, "time_base": time_base}],
        "packets": [{"stream_index": 1, "pts": timestamp, "flags": flags} for timestamp, flags in pts],
    }


class CutpointTests(unittest.TestCase):
    def setUp(self):
        self.basic = probe([(2500, "K__"), (500, "K__"), (1500, "___"), (4500, "K__"),
                            (2500, "K__"), (3300, "KD_"), (3400, "K_C")])

    def evaluate(self, time, policy="nearest", tolerance=0):
        return audit.evaluate(self.basic, [{"id": "example", "seconds": time}], Fraction(tolerance), policy)

    def test_exact_hit_uses_stream_start_and_deduplicates(self):
        report = self.evaluate("2")
        self.assertEqual(report["key_packet_count"], 3)
        self.assertEqual(report["origin_pts"], 500)
        cut = report["cuts"][0]
        self.assertEqual(cut["chosen"], {"pts": 2500, "seconds": "2", "offset_ms": "0"})
        self.assertEqual(cut["before"], cut["after"])
        self.assertEqual(report["misses"], 0)

    def test_nearest_tie_prefers_earlier_packet(self):
        result = self.evaluate("1", tolerance=1000)["cuts"][0]
        self.assertEqual(result["chosen"], result["before"])
        self.assertEqual(result["status"], "within_tolerance")

    def test_direction_changes_selected_packet_and_gate(self):
        before = self.evaluate("1.98", "before", 50)
        after = self.evaluate("1.98", "after", 50)
        self.assertEqual(before["cuts"][0]["chosen"]["offset_ms"], "-1980")
        self.assertEqual(before["misses"], 1)
        self.assertEqual(after["cuts"][0]["chosen"]["offset_ms"], "20")
        self.assertEqual(after["misses"], 0)

    def test_unavailable_direction_is_explicit(self):
        result = self.evaluate("4.01", "after", 50)["cuts"][0]
        self.assertIsNone(result["after"])
        self.assertIsNone(result["chosen"])
        self.assertEqual(result["status"], "no_candidate")

    def test_gate_uses_exact_rational_time(self):
        self.basic = probe([(1, "K__")], time_base="1/3", start_pts=0)
        cut = self.evaluate("0.333333333", tolerance=Fraction("0.000000333"))["cuts"][0]
        self.assertEqual(cut["chosen"]["offset_ms"], "0.000000333")
        self.assertEqual(cut["status"], "outside_tolerance")
        self.assertEqual(self.evaluate("0.333333333", tolerance=Fraction("0.000000334"))["misses"], 0)

    def test_missing_origin_needs_explicit_override(self):
        data = probe([(2000, "K__")], start_pts="N/A")
        cuts = [{"id": "title", "seconds": "2.5"}]
        with self.assertRaisesRegex(audit.InputError, "supply --origin-pts"):
            audit.evaluate(data, cuts, Fraction(0), "nearest")
        self.assertEqual(audit.evaluate(data, cuts, Fraction(0), "nearest", -500)["misses"], 0)

    def test_missing_key_pts_and_mixed_streams_fail_closed(self):
        cuts = [{"id": "clip", "seconds": 0}]
        malformed = probe([(None, "K__")])
        with self.assertRaisesRegex(audit.InputError, "key packet 1 PTS"):
            audit.evaluate(malformed, cuts, Fraction(0), "nearest")
        malformed["packets"][0]["stream_index"] = 2
        with self.assertRaisesRegex(audit.InputError, "does not belong"):
            audit.evaluate(malformed, cuts, Fraction(0), "nearest")

    def test_duplicate_cut_ids_and_bad_numbers(self):
        cuts = [{"id": "repeat", "seconds": 0}] * 2
        with self.assertRaisesRegex(audit.InputError, "repeats an id"):
            audit.evaluate(self.basic, cuts, Fraction(0), "nearest")
        for value in [True, "-1", "1e-3", "NaN"]:
            with self.subTest(value=value), self.assertRaises(audit.InputError):
                self.evaluate(value)

    def test_cli_gate_and_invalid_json(self):
        folder = Path(self.enterContext(TemporaryDirectory()))
        (folder / "probe.json").write_text(json.dumps(self.basic), encoding="utf-8")
        (folder / "cuts.json").write_text('[{"id":"intro","seconds":1.98}]', encoding="utf-8")
        command = [sys.executable, "-I", str(SCRIPT), "check", "probe.json", "cuts.json", "--json"]
        close = subprocess.run(command, cwd=folder, capture_output=True, check=False)
        miss = subprocess.run([*command, "--tolerance-ms", "19"], cwd=folder, capture_output=True, check=False)
        self.assertEqual(close.returncode, 0, close.stderr)
        self.assertEqual(miss.returncode, 1, miss.stderr)
        self.assertEqual(json.loads(close.stdout)["misses"], 0)
        self.assertEqual(json.loads(miss.stdout)["misses"], 1)
        (folder / "cuts.json").write_text("[", encoding="utf-8")
        invalid = subprocess.run(command, cwd=folder, capture_output=True, check=False)
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stdout, b"")

    def test_demo_is_labeled_and_exit_zero(self):
        run = subprocess.run([sys.executable, "-I", str(SCRIPT), "demo", "--json"],
                             capture_output=True, check=False)
        self.assertEqual(run.returncode, 0, run.stderr)
        demo = json.loads(run.stdout)
        self.assertTrue(demo["synthetic"])
        self.assertEqual(demo["misses"], 1)
        self.assertEqual(demo["cuts"][1]["chosen"]["offset_ms"], "550")


if __name__ == "__main__":
    unittest.main()
