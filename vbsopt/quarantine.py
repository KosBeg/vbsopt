from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Sequence

from .baseline import run_string_baseline
from .evaluation import count_hits
from .parser import parse_program
from .passes import optimize_program, render_program
from .pipeline import run_pipeline

DEFAULT_PASSWORD = b"infected"


@dataclass(slots=True)
class ExternalSampleReport:
    sample_label: str
    sha256: str
    expected_total: int
    hits_before: int
    hits_baseline: int
    hits_core: int
    hits_full: int
    stats: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "sample_label": self.sample_label,
            "sha256": self.sha256,
            "expected_total": self.expected_total,
            "hits_before": self.hits_before,
            "hits_baseline": self.hits_baseline,
            "hits_core": self.hits_core,
            "hits_full": self.hits_full,
            "stats": self.stats,
        }



def defang_text(text: str) -> str:
    text = re.sub(r"https://", "hxxps://", text, flags=re.IGNORECASE)
    text = re.sub(r"http://", "hxxp://", text, flags=re.IGNORECASE)
    return text



def read_sample_text(path: Path, password: bytes = DEFAULT_PASSWORD) -> tuple[str, str]:
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            names = [name for name in zf.namelist() if name.lower().endswith((".vbs", ".hta"))]
            if not names:
                raise FileNotFoundError("Архів не містить .vbs або .hta зразка")
            name = names[0]
            raw = zf.read(name, pwd=password)
            return raw.decode("utf-8", errors="replace"), name
    raw = path.read_bytes()
    return raw.decode("utf-8", errors="replace"), path.name



def sha256_text_bytes(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()



def load_manifest(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))



def validate_external_sample(sample_path: Path, manifest_path: Path) -> ExternalSampleReport:
    manifest = load_manifest(manifest_path)
    text, label = read_sample_text(sample_path)
    sha256 = sha256_text_bytes(text)
    expected: Sequence[str] = manifest["expected_defanged"]

    raw_script = parse_program(text).extracted_script_text
    baseline_text = defang_text(run_string_baseline(text))
    core_prog, core_ctx = optimize_program(parse_program(text))
    core_text = defang_text(render_program(core_prog, core_ctx))
    full = run_pipeline(text)
    full_text = defang_text(full.normalized_script)

    hits_before, total = count_hits(expected, defang_text(raw_script))
    hits_baseline, _ = count_hits(expected, baseline_text)
    hits_core, _ = count_hits(expected, core_text)
    hits_full, _ = count_hits(expected, full_text)

    return ExternalSampleReport(
        sample_label=label,
        sha256=sha256,
        expected_total=total,
        hits_before=hits_before,
        hits_baseline=hits_baseline,
        hits_core=hits_core,
        hits_full=hits_full,
        stats=full.stats,
    )



def default_external_sample_path() -> str | None:
    return os.getenv("VBSOPT_EXTERNAL_SAMPLE")
