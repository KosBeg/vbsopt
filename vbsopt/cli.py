from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .pipeline import run_pipeline


_OUTPUT_FIELDS = {
    "normalized": "deobfuscated_script",
    "annotated": "normalized_script",
    "ast": "ast_text",
    "ir": "ir_text",
    "ssa": "ssa_text",
}


def _write_if_requested(path: str | None, content: str) -> None:
    if path:
        Path(path).write_text(content, encoding="utf-8")



def main() -> None:
    ap = argparse.ArgumentParser(
        description="Безпечний невиконуваний конвеєр деобфускації VBS/HTA з AST → CFG IR → SSA"
    )
    ap.add_argument("input", help="Шлях до VBS або HTA зразка")
    ap.add_argument("--normalized", help="Записати чистий деобфускований VBS у файл")
    ap.add_argument("--annotated-normalized", help="Записати анотований VBS із recovered-коментарями у файл")
    ap.add_argument("--ast", help="Записати rendered AST у файл")
    ap.add_argument("--ir", help="Записати lowered CFG IR у файл")
    ap.add_argument("--ssa", help="Записати SSA-форму у файл")
    ap.add_argument(
        "--stdout",
        choices=["normalized", "annotated", "ast", "ir", "ssa", "all", "none"],
        default=None,
        help="Що друкувати в stdout. Якщо вихідні файли не задано, за замовчуванням друкується normalized.",
    )
    ap.add_argument(
        "--quiet-stats",
        action="store_true",
        help="Не друкувати службову статистику та індикатори в stderr.",
    )
    ap.add_argument(
        "--debug",
        action="store_true",
        help="Увімкнути детальний трасувальний лог деобфускації.",
    )
    ap.add_argument(
        "--debug-file",
        help="Записати детальний трасувальний лог у файл.",
    )
    args = ap.parse_args()

    text = Path(args.input).read_text(encoding="utf-8")
    res = run_pipeline(text, debug=args.debug or bool(args.debug_file))

    outputs = {
        "normalized": res.deobfuscated_script,
        "annotated": res.normalized_script,
        "ast": res.ast_text,
        "ir": res.ir_text,
        "ssa": res.ssa_text,
    }

    _write_if_requested(args.normalized, res.deobfuscated_script)
    _write_if_requested(args.annotated_normalized, res.normalized_script)
    _write_if_requested(args.ast, res.ast_text)
    _write_if_requested(args.ir, res.ir_text)
    _write_if_requested(args.ssa, res.ssa_text)

    stdout_mode = args.stdout
    if stdout_mode is None:
        if any((args.normalized, args.annotated_normalized, args.ast, args.ir, args.ssa)):
            stdout_mode = "none"
        else:
            stdout_mode = "normalized"

    if stdout_mode == "all":
        blocks = []
        for key in ("normalized", "annotated", "ast", "ir", "ssa"):
            blocks.append(f"--- {key.upper()} ---\n{outputs[key].rstrip()}" if outputs[key].strip() else f"--- {key.upper()} ---")
        print("\n\n".join(blocks))
    elif stdout_mode in _OUTPUT_FIELDS:
        content = outputs[stdout_mode]
        sys.stdout.write(content)
        if content and not content.endswith("\n"):
            sys.stdout.write("\n")

    if args.debug_file:
        Path(args.debug_file).write_text(res.debug_text, encoding="utf-8")

    if not args.quiet_stats:
        print("Indicators before:", sorted(res.indicators_before), file=sys.stderr)
        print("Indicators after:", sorted(res.indicators_after), file=sys.stderr)
        print("Stats:", res.stats, file=sys.stderr)
        if args.debug and not args.debug_file:
            print("Debug trace:\n" + res.debug_text, file=sys.stderr)


if __name__ == "__main__":
    main()
