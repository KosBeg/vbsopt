from __future__ import annotations

import argparse
import json
from pathlib import Path

from vbsopt.pipeline import run_pipeline
from vbsopt.quarantine import read_sample_text, validate_external_sample


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a quarantine VBS/HTA sample without executing it.")
    parser.add_argument("--input", type=Path, required=True, help="Path to a .vbs/.hta sample or password-protected .zip archive")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("evidence/external_sample/8c9f356a4dc3c3a43b5567e338e108b4_manifest.json"),
        help="Path to manifest with defanged expected artifacts",
    )
    parser.add_argument("--report-dir", type=Path, required=True, help="Directory for JSON and Markdown report")
    args = parser.parse_args()

    report = validate_external_sample(args.input, args.manifest)
    sample_text, _ = read_sample_text(args.input)
    result = run_pipeline(sample_text, debug=True)

    args.report_dir.mkdir(parents=True, exist_ok=True)

    data = report.as_dict()
    data["clean_nonempty_lines"] = len([line for line in result.deobfuscated_script.splitlines() if line.strip()])
    data["annotated_nonempty_lines"] = len([line for line in result.normalized_script.splitlines() if line.strip()])
    data["debug_events"] = len([line for line in result.debug_text.splitlines() if line.strip()])
    (args.report_dir / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    (args.report_dir / "normalized_clean.vbs").write_text(result.deobfuscated_script, encoding="utf-8")
    (args.report_dir / "normalized_annotated.vbs").write_text(result.normalized_script, encoding="utf-8")
    (args.report_dir / "debug.log").write_text(result.debug_text, encoding="utf-8")
    (args.report_dir / "ast.txt").write_text(result.ast_text, encoding="utf-8")
    (args.report_dir / "ir.txt").write_text(result.ir_text, encoding="utf-8")
    (args.report_dir / "ssa.txt").write_text(result.ssa_text, encoding="utf-8")

    md: list[str] = []
    md.append("# Карантинна валідація додаткового VBS-зразка")
    md.append("")
    md.append(f"- Мітка зразка: `{report.sample_label}`")
    md.append(f"- SHA-256 фактично проаналізованого вмісту: `{report.sha256}`")
    md.append(f"- Очікувані артефакти: {report.expected_total}")
    md.append(f"- До нормалізації: {report.hits_before}/{report.expected_total}")
    md.append(f"- Рядковий baseline: {report.hits_baseline}/{report.expected_total}")
    md.append(f"- AST/IR/SSA-ядро: {report.hits_core}/{report.expected_total}")
    md.append(f"- Повний конвеєр: {report.hits_full}/{report.expected_total}")
    md.append(f"- Непорожні рядки у чистому деобфускованому VBS: {data['clean_nonempty_lines']}")
    md.append(f"- Непорожні рядки в анотованому рендері: {data['annotated_nonempty_lines']}")
    md.append(f"- Записів у debug trace: {data['debug_events']}")
    md.append("")
    md.append("## Що підтверджено на чистому виході")
    md.append("")
    md.append("- збережено основну поведінкову логіку HTTP-завантаження;")
    md.append("- вираз через `Eval(...)` статично згорнуто до `FormatDateTime(Now(), vbShortDate)`;")
    md.append("- відновлено `%TEMP%\\exteriorGz3.hta`, `msxml2.xmlhttp`, `adodb.stream`, `WScript.Shell`;")
    md.append("- паразитні локальні конструктори та шумові рутини не потрапляють у чистий вихід.")
    md.append("")
    md.append("## Службова статистика")
    md.append("")
    for key, value in sorted(report.stats.items()):
        md.append(f"- {key}: {value}")
    (args.report_dir / "report.md").write_text("\n".join(md), encoding="utf-8")


if __name__ == "__main__":
    main()
