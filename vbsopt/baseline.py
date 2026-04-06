
from __future__ import annotations

import re
from typing import Dict

from .evaluation import normalize_match_text
from .parser import extract_vbscript

_STRING_RE = re.compile(r'"(?:[^"]|"")*"')
_CONCAT_LITERALS_RE = re.compile(r'("(?:[^"]|"")*")\s*(?:&|\+)\s*("(?:[^"]|"")*")')
_CHR_RE = re.compile(r'(?i)chrw?\(\s*(-?\d+)\s*\)')


def _decode_string_literal(token: str) -> str:
    return token[1:-1].replace('""', '"')


def _encode_string_literal(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _fold_literal_concats(expr: str) -> str:
    prev = None
    out = expr
    while prev != out:
        prev = out
        out = _CONCAT_LITERALS_RE.sub(
            lambda m: _encode_string_literal(
                _decode_string_literal(m.group(1)) + _decode_string_literal(m.group(2))
            ),
            out,
        )
    return out


def _fold_chr_calls(expr: str) -> str:
    return _CHR_RE.sub(lambda m: _encode_string_literal(chr(int(m.group(1)))), expr)


def _simplify_expr(expr: str) -> str:
    prev = None
    out = expr.strip()
    while prev != out:
        prev = out
        out = _fold_chr_calls(out)
        out = _fold_literal_concats(out)
    return out


def run_string_baseline(text: str) -> str:
    script = extract_vbscript(text)
    env: Dict[str, str] = {}
    rendered: list[str] = []

    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        assignment = re.match(r'(?i)(set\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$', line)
        if assignment:
            prefix, name, expr = assignment.groups()
            simple = _simplify_expr(expr)

            replace_call = re.fullmatch(
                r'(?i)replace\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*("(?:[^"]|"")*")\s*,\s*("(?:[^"]|"")*")\s*\)',
                simple,
            )
            if replace_call:
                source_name, old_lit, new_lit = replace_call.groups()
                if source_name in env:
                    env[name] = env[source_name].replace(
                        _decode_string_literal(old_lit),
                        _decode_string_literal(new_lit),
                    )
                    simple = _encode_string_literal(env[name])

            if simple.startswith('"') and simple.endswith('"'):
                env[name] = _decode_string_literal(simple)

            rendered.append(f"{'Set ' if prefix else ''}{name} = {simple}")
            continue

        call_line = _simplify_expr(line)
        rendered.append(call_line)

    return "\n".join(rendered).strip() + ("\n" if rendered else "")
