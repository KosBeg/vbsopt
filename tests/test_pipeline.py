import json
import pathlib
import textwrap
import unittest

from vbsopt.baseline import run_string_baseline
from vbsopt.evaluation import count_hits
from vbsopt.pipeline import run_pipeline

ROOT = pathlib.Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
EXPANDED = ROOT / "samples_expanded"
REAL = ROOT / "samples_real"
GENERAL = ROOT / "samples_general"
ALL = ROOT / "samples_all"


def _dedent(text: str) -> str:
    return textwrap.dedent(text).strip() + "\n"


class PipelineTests(unittest.TestCase):
    def test_original_samples_full_recall(self):
        metadata = json.loads((SAMPLES / "metadata.json").read_text(encoding="utf-8"))
        total_hits = 0
        total_expected = 0
        for name, info in metadata.items():
            text = (SAMPLES / name).read_text(encoding="utf-8")
            result = run_pipeline(text)
            hits, expected = count_hits(info["expected"], result.normalized_script)
            total_hits += hits
            total_expected += expected
            self.assertEqual(hits, expected, msg=f"{name}: expected full recall {expected}, got {hits}")
        self.assertEqual(total_hits, total_expected)

    def test_real_derived_samples_full_recall(self):
        metadata = json.loads((REAL / "metadata.json").read_text(encoding="utf-8"))
        total_hits = 0
        total_expected = 0
        for name, info in metadata.items():
            text = (REAL / name).read_text(encoding="utf-8")
            result = run_pipeline(text)
            hits, expected = count_hits(info["expected"], result.normalized_script)
            total_hits += hits
            total_expected += expected
            self.assertEqual(hits, expected, msg=f"{name}: expected full recall {expected}, got {hits}")
        self.assertEqual(total_hits, total_expected)

    def test_general_samples_full_recall(self):
        metadata = json.loads((GENERAL / "metadata.json").read_text(encoding="utf-8"))
        total_hits = 0
        total_expected = 0
        for name, info in metadata.items():
            text = (GENERAL / name).read_text(encoding="utf-8")
            result = run_pipeline(text)
            hits, expected = count_hits(info["expected"], result.normalized_script)
            total_hits += hits
            total_expected += expected
            self.assertEqual(hits, expected, msg=f"{name}: expected full recall {expected}, got {hits}")
        self.assertEqual(total_hits, total_expected)

    def test_merged_samples_full_recall(self):
        if not ALL.exists():
            self.skipTest("merged corpus not generated")
        metadata = json.loads((ALL / "metadata.json").read_text(encoding="utf-8"))
        total_hits = 0
        total_expected = 0
        for name, info in metadata.items():
            text = (ALL / name).read_text(encoding="utf-8")
            result = run_pipeline(text)
            hits, expected = count_hits(info["expected"], result.normalized_script)
            total_hits += hits
            total_expected += expected
            self.assertEqual(hits, expected, msg=f"{name}: expected full recall {expected}, got {hits}")
        self.assertEqual(total_hits, total_expected)

    def test_no_duplicate_blob_annotations(self):
        text = (SAMPLES / "s6_b64_tokens.vbs").read_text(encoding="utf-8")
        result = run_pipeline(text)
        self.assertEqual(result.stats["base64_decodes"], 1)
        self.assertEqual(result.normalized_script.count("' blob "), 1)

    def test_string_baseline_is_lower_or_equal_to_full_pipeline(self):
        metadata = json.loads((SAMPLES / "metadata.json").read_text(encoding="utf-8"))
        baseline_hits = 0
        full_hits = 0
        total_expected = 0
        for name, info in metadata.items():
            text = (SAMPLES / name).read_text(encoding="utf-8")
            baseline = run_string_baseline(text)
            full = run_pipeline(text).normalized_script
            b_hits, expected = count_hits(info["expected"], baseline)
            f_hits, _ = count_hits(info["expected"], full)
            baseline_hits += b_hits
            full_hits += f_hits
            total_expected += expected
        self.assertLessEqual(baseline_hits, full_hits)
        self.assertEqual(full_hits, total_expected)

    def test_ast_renderer_exposes_structure(self):
        text = _dedent(
            """
            Dim x
            If flag Then
              x = "A"
            Else
              x = "B"
            End If
            WScript.Echo x
            """
        )
        result = run_pipeline(text)
        self.assertIn("Program", result.ast_text)
        self.assertIn("IfStmt", result.ast_text)
        self.assertIn("CallStmt(WScript.Echo)", result.ast_text)

    def test_ssa_phi_inserted_on_branch_join(self):
        text = _dedent(
            """
            Dim x
            If flag Then
              x = "A"
            Else
              x = "B"
            End If
            WScript.Echo x
            """
        )
        result = run_pipeline(text)
        self.assertGreaterEqual(result.stats["ssa_phi_nodes"], 1)
        self.assertIn("phi", result.ssa_text)
        self.assertIn("x.3 = phi", result.ssa_text)

    def test_join_array_is_folded(self):
        text = _dedent(
            """
            payload = Join(Array("ab", "cd", "ef"), "")
            WScript.Echo payload
            """
        )
        result = run_pipeline(text)
        self.assertIn('"abcdef"', result.normalized_script)

    def test_expanded_dataset_smoke(self):
        if not EXPANDED.exists():
            self.skipTest("expanded dataset not generated")
        metadata = json.loads((EXPANDED / "metadata.json").read_text(encoding="utf-8"))
        checked = 0
        for name, info in list(metadata.items())[:5]:
            text = (EXPANDED / name).read_text(encoding="utf-8")
            result = run_pipeline(text)
            hits, expected = count_hits(info["expected"], result.normalized_script)
            self.assertEqual(hits, expected, msg=f"{name}: expected {expected}, got {hits}")
            checked += 1
        self.assertGreaterEqual(checked, 5)


    def test_execute_literal_is_expanded(self):
        text = _dedent(
            """
            payload = "url = ""hxxps://mirror.example.invalid/p"" : WScript.Echo url"
            ExecuteGlobal payload
            """
        )
        result = run_pipeline(text)
        self.assertEqual(result.stats["execute_expansions"], 1)
        self.assertIn("expanded ExecuteGlobal", result.normalized_script)
        self.assertIn('url = "hxxps://mirror.example.invalid/p"', result.normalized_script)

    def test_execute_base64_is_expanded_once(self):
        text = (GENERAL / "g5_execute_base64.vbs").read_text(encoding="utf-8")
        result = run_pipeline(text)
        self.assertEqual(result.stats["execute_expansions"], 1)
        self.assertEqual(result.stats["base64_decodes"], 1)
        self.assertEqual(result.normalized_script.count("' blob "), 1)
        self.assertIn('repo = "hxxps://raw.example.invalid/seed.vbs"', result.normalized_script)

    def test_helper_function_is_inlined(self):
        text = (GENERAL / "g3_replace_wrapper.vbs").read_text(encoding="utf-8")
        result = run_pipeline(text)
        self.assertGreaterEqual(result.stats["helper_inlines"], 1)
        self.assertIn('"WScript.Shell"', result.normalized_script)
        self.assertIn('"hxxps://cdn1.example.invalid/stage"', result.normalized_script)

    def test_hex_literal_in_chr_is_folded(self):
        text = _dedent(
            """
            s = Chr(&H57) & Chr(&H53) & Chr(&H63)
            WScript.Echo s
            """
        )
        result = run_pipeline(text)
        self.assertIn('"WSc"', result.normalized_script)

    def test_ir_cse_stat_is_nonzero_on_duplicate_expression(self):
        text = _dedent(
            """
            x = a & b
            y = a & b
            WScript.Echo x
            WScript.Echo y
            """
        )
        result = run_pipeline(text)
        self.assertGreaterEqual(result.stats["ir_cse_eliminated"], 1)


if __name__ == "__main__":
    unittest.main()
