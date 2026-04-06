from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from vbsopt.baseline import run_string_baseline
from vbsopt.evaluation import count_hits
from vbsopt.parser import parse_program
from vbsopt.passes import optimize_program, render_program
from vbsopt.pipeline import run_pipeline

ROOT = Path(__file__).resolve().parent
DEFAULT_SAMPLES = ROOT / "samples"
DEFAULT_RESULTS = ROOT / "results"



def _nonempty_lines(text: str) -> int:
    return len([line for line in text.splitlines() if line.strip()])



def _line_reduction(before: str, after: str) -> float:
    before_count = _nonempty_lines(before)
    after_count = _nonempty_lines(after)
    if before_count == 0:
        return 0.0
    return round((1 - after_count / before_count) * 100, 1)



def _normalized_contains(artifact: str, text: str) -> int:
    normalized_artifact = artifact.lower().replace("hxxps://", "https://").replace("hxxp://", "http://")
    normalized_text = text.lower().replace("hxxps://", "https://").replace("hxxp://", "http://")
    return int(normalized_artifact in normalized_text)



def run_experiments(samples_dir: Path, results_dir: Path) -> None:
    results_dir.mkdir(exist_ok=True)
    metadata = json.loads((samples_dir / "metadata.json").read_text(encoding="utf-8"))

    sample_rows: list[dict[str, object]] = []
    artifact_rows: list[dict[str, object]] = []

    for name, info in metadata.items():
        text = (samples_dir / name).read_text(encoding="utf-8")
        expected = info["expected"]

        prog = parse_program(text)
        raw_script = prog.extracted_script_text
        core_prog, _core_ctx = optimize_program(prog)
        core_script = render_program(core_prog)
        pipeline_result = run_pipeline(text)
        baseline_script = run_string_baseline(text)

        (results_dir / f"{name}.baseline.vbs").write_text(baseline_script, encoding="utf-8")
        (results_dir / f"{name}.normalized.vbs").write_text(pipeline_result.normalized_script, encoding="utf-8")
        (results_dir / f"{name}.ast.txt").write_text(pipeline_result.ast_text, encoding="utf-8")
        (results_dir / f"{name}.ir.txt").write_text(pipeline_result.ir_text, encoding="utf-8")
        (results_dir / f"{name}.ssa.txt").write_text(pipeline_result.ssa_text, encoding="utf-8")

        hits_before, total = count_hits(expected, raw_script)
        hits_baseline, _ = count_hits(expected, baseline_script)
        hits_core, _ = count_hits(expected, core_script)
        hits_full, _ = count_hits(expected, pipeline_result.normalized_script)

        sample_rows.append(
            {
                "sample": name,
                "pattern": info.get("source_pattern", info.get("source", "")),
                "expected_total": total,
                "hits_before": hits_before,
                "hits_baseline": hits_baseline,
                "hits_after_core": hits_core,
                "hits_after_full": hits_full,
                "recall_before": round(hits_before / total if total else 1.0, 3),
                "recall_baseline": round(hits_baseline / total if total else 1.0, 3),
                "recall_after_core": round(hits_core / total if total else 1.0, 3),
                "recall_after_full": round(hits_full / total if total else 1.0, 3),
                "lines_before": _nonempty_lines(raw_script),
                "lines_after_baseline": _nonempty_lines(baseline_script),
                "lines_after_core": _nonempty_lines(core_script),
                "line_reduction_pct": _line_reduction(raw_script, core_script),
                "blob_analyses": len(pipeline_result.blob_analyses),
                **pipeline_result.stats,
            }
        )

        for artifact in expected:
            artifact_rows.append(
                {
                    "sample": name,
                    "artifact": artifact,
                    "hit_before": _normalized_contains(artifact, raw_script),
                    "hit_baseline": _normalized_contains(artifact, baseline_script),
                    "hit_after_core": _normalized_contains(artifact, core_script),
                    "hit_after_full": _normalized_contains(artifact, pipeline_result.normalized_script),
                }
            )

    with (results_dir / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(sample_rows[0].keys()))
        writer.writeheader()
        writer.writerows(sample_rows)

    with (results_dir / "artifact_hits.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(artifact_rows[0].keys()))
        writer.writeheader()
        writer.writerows(artifact_rows)

    total_artifacts = sum(int(row["expected_total"]) for row in sample_rows)
    total_before = sum(int(row["hits_before"]) for row in sample_rows)
    total_baseline = sum(int(row["hits_baseline"]) for row in sample_rows)
    total_core = sum(int(row["hits_after_core"]) for row in sample_rows)
    total_full = sum(int(row["hits_after_full"]) for row in sample_rows)
    avg_line_reduction = sum(float(row["line_reduction_pct"]) for row in sample_rows) / len(sample_rows)

    lines: list[str] = []
    lines.append("# Експериментальний звіт")
    lines.append("")
    lines.append("## Підсумок")
    lines.append("")
    lines.append(f"- Корпус: `{samples_dir.name}`")
    lines.append(f"- Кількість зразків: {len(sample_rows)}")
    lines.append(f"- Загальна кількість очікуваних артефактів: {total_artifacts}")
    lines.append(f"- Відновлено до нормалізації: {total_before}/{total_artifacts}")
    lines.append(f"- Відновлено рядковим baseline: {total_baseline}/{total_artifacts}")
    lines.append(f"- Відновлено після AST/IR-ядра: {total_core}/{total_artifacts}")
    lines.append(f"- Відновлено після повного конвеєра: {total_full}/{total_artifacts}")
    lines.append(f"- Середнє скорочення кількості непорожніх рядків після AST/IR-ядра: {avg_line_reduction:.1f}%")
    lines.append(f"- Загалом декодовано Base64-блоків: {sum(int(row['base64_decodes']) for row in sample_rows)}")
    lines.append(f"- Загалом розгорнуто Execute/ExecuteGlobal: {sum(int(row['execute_expansions']) for row in sample_rows)}")
    lines.append(f"- Загалом інлайнено pure helper-функцій: {sum(int(row['helper_inlines']) for row in sample_rows)}")
    lines.append(f"- Загалом згенеровано SSA φ-вузлів: {sum(int(row['ssa_phi_nodes']) for row in sample_rows)}")
    lines.append(f"- Загалом виконано IR CSE-усунень: {sum(int(row['ir_cse_eliminated']) for row in sample_rows)}")
    lines.append(f"- Загалом усунуто мертвих IR-тимчасових значень: {sum(int(row['ir_dead_temps_removed']) for row in sample_rows)}")
    lines.append(f"- Загалом виконано рекурсивних аналізів відновлених blob-ів: {sum(int(row['blob_analyses']) for row in sample_rows)}")
    lines.append(f"- Загалом усунуто мертвих присвоєнь: {sum(int(row['dead_assign_removed']) for row in sample_rows)}")
    lines.append("")
    lines.append("## Результати по зразках")
    lines.append("")
    lines.append("| Зразок | До | Baseline | Ядро AST/IR | Повний конвеєр | φ | Exec | Helper | IR CSE | Blob-и | Δ рядків, % |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for row in sample_rows:
        lines.append(
            f"| {row['sample']} | {row['recall_before']:.3f} | {row['recall_baseline']:.3f} | "
            f"{row['recall_after_core']:.3f} | {row['recall_after_full']:.3f} | {row['ssa_phi_nodes']} | "
            f"{row['execute_expansions']} | {row['helper_inlines']} | {row['ir_cse_eliminated']} | "
            f"{row['blob_analyses']} | {row['line_reduction_pct']:.1f} |"
        )

    (results_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")



def main() -> None:
    parser = argparse.ArgumentParser(description="Run VBScript/HTA normalization experiments.")
    parser.add_argument("--samples-dir", type=Path, default=DEFAULT_SAMPLES, help="Directory with samples and metadata.json")
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS, help="Directory for generated outputs")
    args = parser.parse_args()
    run_experiments(args.samples_dir, args.results_dir)


if __name__ == "__main__":
    main()
