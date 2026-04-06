from __future__ import annotations

from dataclasses import dataclass
import re
from typing import List


@dataclass(slots=True)
class Token:
    kind: str
    value: str
    pos: int


_TWO_CHAR = {"<=", ">=", "<>"}
_ONE_CHAR = set("(),.&+-=*/:<>")
_KEYWORDS = {
    "and",
    "or",
    "xor",
    "not",
    "true",
    "false",
    "then",
    "else",
    "call",
    "set",
}


class LexError(ValueError):
    pass


_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_HEX_NUMBER_RE = re.compile(r"&[Hh][0-9A-Fa-f]+")


def tokenize_expr(text: str) -> List[Token]:
    tokens: List[Token] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
            continue

        if ch == '"':
            start = i
            i += 1
            buf: list[str] = []
            while i < len(text):
                cur = text[i]
                if cur == '"':
                    if i + 1 < len(text) and text[i + 1] == '"':
                        buf.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(cur)
                i += 1
            else:
                raise LexError(f"Незавершений рядковий літерал у позиції {start}")
            tokens.append(Token("STRING", "".join(buf), start))
            continue

        hex_match = _HEX_NUMBER_RE.match(text, i)
        if hex_match:
            raw = hex_match.group(0)
            tokens.append(Token("NUMBER", str(int(raw[2:], 16)), i))
            i = hex_match.end()
            continue

        if i + 1 < len(text) and text[i : i + 2] in _TWO_CHAR:
            tokens.append(Token(text[i : i + 2], text[i : i + 2], i))
            i += 2
            continue

        if ch in _ONE_CHAR:
            tokens.append(Token(ch, ch, i))
            i += 1
            continue

        if ch.isdigit() or (ch == '-' and i + 1 < len(text) and text[i + 1].isdigit()):
            start = i
            if ch == '-':
                i += 1
            while i < len(text) and text[i].isdigit():
                i += 1
            tokens.append(Token("NUMBER", text[start:i], start))
            continue

        ident_match = _IDENTIFIER_RE.match(text, i)
        if ident_match:
            value = ident_match.group(0)
            lower = value.lower()
            kind = lower.upper() if lower in _KEYWORDS else "IDENT"
            tokens.append(Token(kind, value, i))
            i = ident_match.end()
            continue

        raise LexError(f"Невідомий символ {ch!r} у позиції {i}")

    tokens.append(Token("EOF", "", len(text)))
    return tokens
