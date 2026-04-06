import pathlib
import unittest

from vbsopt.pipeline import run_pipeline
from vbsopt.quarantine import default_external_sample_path, read_sample_text, validate_external_sample

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "evidence" / "external_sample" / "8c9f356a4dc3c3a43b5567e338e108b4_manifest.json"


class ExternalQuarantineSampleTests(unittest.TestCase):
    def test_user_provided_sample_optional(self):
        sample_path = default_external_sample_path()
        if not sample_path:
            self.skipTest("VBSOPT_EXTERNAL_SAMPLE не задано; зовнішній карантинний зразок не входить до публічного бандла")
        report = validate_external_sample(pathlib.Path(sample_path), MANIFEST)
        self.assertEqual(report.hits_core, report.expected_total)
        self.assertEqual(report.hits_full, report.expected_total)

        text, _ = read_sample_text(pathlib.Path(sample_path))
        result = run_pipeline(text, debug=True)
        clean = result.deobfuscated_script
        self.assertIn('FormatDateTime(Now(), vbShortDate)', clean)
        self.assertIn('CreateObject("WScript.Shell")', clean)
        self.assertIn('SaveToFile "%TEMP%\\exteriorGz3.hta"', clean)
        self.assertNotIn('furthermorejp9', clean)
        self.assertNotIn('gloomyDap.Run', clean)
        self.assertLessEqual(len([line for line in clean.splitlines() if line.strip()]), 20)
        self.assertIn('drop-routine-call', result.debug_text)
        self.assertIn('drop-dead', result.debug_text)


if __name__ == "__main__":
    unittest.main()
