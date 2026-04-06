from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parent
_STRING_LITERAL_RE = re.compile(r'"(?:[^"]|"")*"')


def _split_literal(token: str) -> str:
    inner = token[1:-1]
    if len(inner) < 10:
        return token
    midpoint = len(inner) // 2
    return f'"{inner[:midpoint]}" & "{inner[midpoint:]}"'


def _inject_noise(script: str) -> str:
    lines = script.splitlines()
    out: list[str] = []
    inserted = False
    for line in lines:
        out.append(line)
        stripped = line.strip()
        safe_insert_after = (
            bool(stripped)
            and not stripped.lower().startswith('<script')
            and not stripped.endswith('_')
            and not stripped.endswith('&')
            and not stripped.endswith(':')
        )
        if not inserted and safe_insert_after:
            out.append("' generated-noise")
            out.append('unused_generated = "AAAA"')
            out.append("")
            inserted = True
    if not inserted:
        out.extend(["' generated-noise", 'unused_generated = "AAAA"'])
    return "\n".join(out)


def _split_long_literals(script: str) -> str:
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        token = match.group(0)
        if count >= 4:
            return token
        if len(token[1:-1]) < 10:
            return token
        count += 1
        return _split_literal(token)

    return _STRING_LITERAL_RE.sub(repl, script)


def _html_extra_wrapper(text: str) -> str:
    if "<head>" in text.lower() and "text/vbscript" in text.lower():
        return re.sub(
            r"(?i)(<head>)",
            r'\1\n<script type="text/javascript">var harmlessNoise = "noop";</script>',
            text,
            count=1,
        )
    return text


def _variant_noise(text: str) -> str:
    if "text/vbscript" in text.lower():
        return _html_extra_wrapper(_inject_noise(text))
    return _inject_noise(text)


def _variant_split(text: str) -> str:
    return _split_long_literals(text)


def _variant_combo(text: str) -> str:
    return _variant_noise(_variant_split(text))


VARIANTS = {
    "": lambda text: text,
    "__noise": _variant_noise,
    "__split": _variant_split,
    "__combo": _variant_combo,
}


def _variant_name(name: str, suffix: str) -> str:
    if not suffix:
        return name
    stem, ext = name.rsplit('.', 1)
    return f"{stem}{suffix}.{ext}"


def generate_corpus(base_dir: Path, out_dir: Path, *, note: str) -> None:
    out_dir.mkdir(exist_ok=True)
    metadata = json.loads((base_dir / 'metadata.json').read_text(encoding='utf-8'))
    expanded: Dict[str, Dict[str, object]] = {}

    for name, info in metadata.items():
        source_text = (base_dir / name).read_text(encoding='utf-8')
        for suffix, transform in VARIANTS.items():
            variant_name = _variant_name(name, suffix)
            variant_text = transform(source_text)
            (out_dir / variant_name).write_text(variant_text, encoding='utf-8')
            expanded[variant_name] = {
                **info,
                'parent_sample': name,
                'generated_variant': suffix or 'base',
            }

    (out_dir / 'metadata.json').write_text(json.dumps(expanded, indent=2, ensure_ascii=False), encoding='utf-8')
    provenance = f"""
# Expanded sanitized dataset

Цей каталог містить автоматично згенеровані варіанти корпусу `{base_dir.name}`.
Генерація не моделює нові сімейства шкідливого коду, а лише перевіряє стійкість конвеєра до лексичних мутацій:
- додавання шумових рядків і коментарів;
- розщеплення довгих рядкових літералів на конкатенації;
- для HTA — вставка стороннього JavaScript-блоку, який не впливає на вилучення VBScript.

{note}
""".strip()
    (out_dir / 'provenance.md').write_text(provenance + '\n', encoding='utf-8')


def merge_corpora(sources: list[Path], out_dir: Path, *, title: str, note: str) -> None:
    out_dir.mkdir(exist_ok=True)
    merged: Dict[str, Dict[str, object]] = {}
    for source in sources:
        metadata = json.loads((source / 'metadata.json').read_text(encoding='utf-8'))
        for name, info in metadata.items():
            shutil.copy2(source / name, out_dir / name)
            merged[name] = {
                **info,
                'merged_from': source.name,
            }
    (out_dir / 'metadata.json').write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding='utf-8')
    (out_dir / 'provenance.md').write_text((f"# {title}\n\n{note}\n"), encoding='utf-8')


def main() -> None:
    generate_corpus(
        ROOT / 'samples',
        ROOT / 'samples_expanded',
        note='Набір придатний для регресійного тестування стійкості на базових модельних зразках.',
    )
    generate_corpus(
        ROOT / 'samples_general',
        ROOT / 'samples_general_expanded',
        note='Набір придатний для регресійного тестування на широкому synthetic VBS-obfuscation corpus без прив’язки до одного кластеру.',
    )
    generate_corpus(
        ROOT / 'samples_real',
        ROOT / 'samples_real_expanded',
        note='Набір придатний для регресійного тестування на real-derived, але санітизованих патернах. Його не слід трактувати як роботу з живими зразками або як зовнішню валідацію на оперативному корпусі.',
    )
    merge_corpora(
        [ROOT / 'samples', ROOT / 'samples_general', ROOT / 'samples_real'],
        ROOT / 'samples_all',
        title='Merged sanitized corpus',
        note='Комбінований корпус: базові модельні, загальні synthetic VBS-obfuscation та real-derived safe surrogates.',
    )
    generate_corpus(
        ROOT / 'samples_all',
        ROOT / 'samples_all_expanded',
        note='Комбінований expanded-набір придатний лише для регресійного тестування стійкості; він не замінює зовнішню валідацію.',
    )


if __name__ == '__main__':
    main()
