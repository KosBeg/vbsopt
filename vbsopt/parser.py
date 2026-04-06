from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import List, Optional

from .ast_nodes import (
    Assignment,
    BinaryOp,
    BooleanLiteral,
    CallExpr,
    CallStmt,
    Concat,
    ConstStmt,
    DimStmt,
    FunctionDecl,
    Identifier,
    IfStmt,
    NumberLiteral,
    Program,
    RawExpr,
    RawStmt,
    Statement,
    StringLiteral,
    SubDecl,
    UnaryOp,
)
from .lexer import LexError, Token, tokenize_expr

SCRIPT_RE = re.compile(
    r"<script[^>]*type\s*=\s*[\"']text/vbscript[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)

_FUNC_SIG_RE = re.compile(
    r"(?is)^(?:public\s+|private\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:\((.*?)\))?\s*$"
)
_SUB_SIG_RE = re.compile(
    r"(?is)^(?:public\s+|private\s+)?sub\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:\((.*?)\))?\s*$"
)


class ParseError(ValueError):
    pass


@dataclass(slots=True)
class _Line:
    text: str
    raw_text: str


def extract_vbscript(text: str) -> str:
    matches = SCRIPT_RE.findall(text)
    if not matches:
        return text
    return "\n\n".join(html.unescape(match) for match in matches)


def _strip_comment(line: str) -> str:
    in_str = False
    out: list[str] = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            out.append(ch)
            if in_str and i + 1 < len(line) and line[i + 1] == '"':
                out.append('"')
                i += 2
                continue
            in_str = not in_str
            i += 1
            continue
        if not in_str and ch == "'":
            break
        out.append(ch)
        i += 1
    stripped = "".join(out).rstrip()
    if stripped.lower().startswith("rem "):
        return ""
    return stripped


def _split_outside_strings(text: str, delimiter: str) -> List[str]:
    parts: List[str] = []
    in_str = False
    depth = 0
    cur: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '"':
            cur.append(ch)
            if in_str and i + 1 < len(text) and text[i + 1] == '"':
                cur.append('"')
                i += 2
                continue
            in_str = not in_str
            i += 1
            continue
        if not in_str:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth = max(0, depth - 1)
            elif ch == delimiter and depth == 0:
                parts.append("".join(cur).strip())
                cur = []
                i += 1
                continue
        cur.append(ch)
        i += 1
    parts.append("".join(cur).strip())
    return [part for part in parts if part]


def _logical_lines(script: str) -> List[_Line]:
    joined: List[str] = []
    buf = ""
    for raw_line in script.splitlines():
        line = raw_line.rstrip()
        if not line and not buf:
            continue
        if line.rstrip().endswith(" _") or line.rstrip().endswith("_"):
            buf += line.rstrip()[:-1].rstrip() + " "
            continue
        full = (buf + line).strip()
        buf = ""
        if full:
            joined.append(full)
    if buf.strip():
        joined.append(buf.strip())

    logical: List[_Line] = []
    for line in joined:
        stripped = _strip_comment(line)
        if not stripped:
            continue
        for part in _split_outside_strings(stripped, ':'):
            logical.append(_Line(text=part.strip(), raw_text=part.strip()))
    return logical


class ExprParser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token:
        return self.tokens[self.pos]

    def consume(self, kind: str | None = None) -> Token:
        tok = self.peek()
        if kind is not None and tok.kind != kind:
            raise ParseError(f"Очікувався токен {kind}, отримано {tok.kind} у позиції {tok.pos}")
        self.pos += 1
        return tok

    def parse(self):
        expr = self.parse_or()
        if self.peek().kind != "EOF":
            raise ParseError(f"Надлишковий фрагмент виразу біля позиції {self.peek().pos}")
        return expr

    def parse_or(self):
        expr = self.parse_and()
        while self.peek().kind in {"OR", "XOR"}:
            op = self.consume().kind.lower()
            expr = BinaryOp(op, expr, self.parse_and())
        return expr

    def parse_and(self):
        expr = self.parse_compare()
        while self.peek().kind == "AND":
            self.consume("AND")
            expr = BinaryOp("and", expr, self.parse_compare())
        return expr

    def parse_compare(self):
        expr = self.parse_concat()
        while self.peek().kind in {"=", "<>", "<", ">", "<=", ">="}:
            op = self.consume().kind
            expr = BinaryOp(op.lower(), expr, self.parse_concat())
        return expr

    def parse_concat(self):
        expr = self.parse_additive()
        while self.peek().kind == "&":
            self.consume("&")
            expr = Concat(expr, self.parse_additive())
        return expr

    def parse_additive(self):
        expr = self.parse_term()
        while self.peek().kind in {"+", "-"}:
            op = self.consume().kind
            expr = BinaryOp(op, expr, self.parse_term())
        return expr

    def parse_term(self):
        expr = self.parse_unary()
        while self.peek().kind in {"*", "/"}:
            op = self.consume().kind
            expr = BinaryOp(op, expr, self.parse_unary())
        return expr

    def parse_unary(self):
        if self.peek().kind == "NOT":
            self.consume("NOT")
            return UnaryOp("not", self.parse_unary())
        if self.peek().kind == "-":
            self.consume("-")
            return UnaryOp("-", self.parse_unary())
        return self.parse_primary()

    def parse_primary(self):
        tok = self.peek()
        if tok.kind == "STRING":
            self.consume("STRING")
            return StringLiteral(tok.value)
        if tok.kind == "NUMBER":
            self.consume("NUMBER")
            return NumberLiteral(int(tok.value))
        if tok.kind == "TRUE":
            self.consume("TRUE")
            return BooleanLiteral(True)
        if tok.kind == "FALSE":
            self.consume("FALSE")
            return BooleanLiteral(False)
        if tok.kind == "(":
            self.consume("(")
            expr = self.parse_or()
            self.consume(")")
            return expr
        if tok.kind == "IDENT":
            return self.parse_name_or_call()
        raise ParseError(f"Неочікуваний токен {tok.kind} у позиції {tok.pos}")

    def parse_name_or_call(self):
        parts = [self.consume("IDENT").value]
        while self.peek().kind == ".":
            self.consume(".")
            parts.append(self.consume("IDENT").value)
        name = ".".join(parts)
        if self.peek().kind == "(":
            self.consume("(")
            args = []
            if self.peek().kind != ")":
                while True:
                    args.append(self.parse_or())
                    if self.peek().kind == ",":
                        self.consume(",")
                        continue
                    break
            self.consume(")")
            return CallExpr(name, args)
        return Identifier(name)


def parse_expr(text: str):
    text = text.strip()
    if not text:
        return StringLiteral("")
    try:
        return ExprParser(tokenize_expr(text)).parse()
    except (LexError, ParseError):
        return RawExpr(text)


def _find_top_level_eq(line: str) -> int:
    in_str = False
    depth = 0
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            if in_str and i + 1 < len(line) and line[i + 1] == '"':
                i += 2
                continue
            in_str = not in_str
            i += 1
            continue
        if not in_str:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth = max(0, depth - 1)
            elif ch == '=' and depth == 0:
                prev = line[i - 1] if i > 0 else ''
                nxt = line[i + 1] if i + 1 < len(line) else ''
                if prev in {'<', '>'} or nxt == '=':
                    i += 1
                    continue
                return i
        i += 1
    return -1


def _split_call_name_and_args(line: str) -> tuple[str, str] | None:
    match = re.match(r"([A-Za-z_][A-Za-z0-9_\.]*)(.*)$", line)
    if not match:
        return None
    return match.group(1), match.group(2).strip()


def _parse_param_names(text: str) -> List[str]:
    if not text.strip():
        return []
    names: List[str] = []
    for part in _split_outside_strings(text, ','):
        token = part.strip()
        if not token:
            continue
        token = re.sub(r"(?i)\bbyval\b|\bbyref\b", "", token).strip()
        if not token:
            continue
        token = token.split('=')[0].strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token):
            names.append(token)
    return names


class ProgramParser:
    def __init__(self, lines: List[_Line]):
        self.lines = lines
        self.pos = 0

    def at_end(self) -> bool:
        return self.pos >= len(self.lines)

    def peek(self) -> Optional[_Line]:
        if self.at_end():
            return None
        return self.lines[self.pos]

    def consume(self) -> _Line:
        line = self.lines[self.pos]
        self.pos += 1
        return line

    def parse(self) -> List[Statement]:
        return self.parse_block(until=frozenset())

    def parse_block(self, until: frozenset[str]) -> List[Statement]:
        out: List[Statement] = []
        while not self.at_end():
            line = self.peek()
            assert line is not None
            lowered = line.text.lower()
            if lowered in until:
                break
            stmt = self.parse_statement()
            if stmt is not None:
                out.append(stmt)
        return out

    def parse_statement(self) -> Optional[Statement]:
        line = self.consume()
        text = line.text.strip()
        lowered = text.lower()
        if not text:
            return None
        if lowered.startswith("dim "):
            names = [part.strip() for part in _split_outside_strings(text[4:], ',') if part.strip()]
            clean = [name for name in names if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)]
            return DimStmt(clean or names, raw_text=line.raw_text)
        const_stmt = self.parse_const(text, line.raw_text)
        if const_stmt is not None:
            return const_stmt
        if lowered.startswith("if "):
            return self.parse_if(line)
        if lowered in {"else", "end if", "end function", "end sub"}:
            return RawStmt(text=text)
        func_stmt = self.parse_function_or_sub(line)
        if func_stmt is not None:
            return func_stmt
        assignment = self.parse_assignment(text, line.raw_text)
        if assignment is not None:
            return assignment
        call_stmt = self.parse_call_stmt(text, line.raw_text)
        if call_stmt is not None:
            return call_stmt
        return RawStmt(text=text)

    def parse_const(self, text: str, raw_text: str) -> Optional[ConstStmt]:
        match = re.match(r"(?is)^const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$", text)
        if not match:
            return None
        name, expr_text = match.groups()
        return ConstStmt(name=name, expr=parse_expr(expr_text), raw_text=raw_text)

    def parse_function_or_sub(self, line: _Line) -> Optional[Statement]:
        text = line.text.strip()
        func_match = _FUNC_SIG_RE.match(text)
        if func_match:
            name, params_text = func_match.groups()
            body = self.parse_block(until=frozenset({"end function"}))
            if not self.at_end() and self.peek() and self.peek().text.lower() == "end function":
                self.consume()
            return FunctionDecl(name=name, params=_parse_param_names(params_text or ""), body=body, raw_text=line.raw_text)
        sub_match = _SUB_SIG_RE.match(text)
        if sub_match:
            name, params_text = sub_match.groups()
            body = self.parse_block(until=frozenset({"end sub"}))
            if not self.at_end() and self.peek() and self.peek().text.lower() == "end sub":
                self.consume()
            return SubDecl(name=name, params=_parse_param_names(params_text or ""), body=body, raw_text=line.raw_text)
        return None

    def parse_if(self, line: _Line) -> Statement:
        text = line.text.strip()
        match = re.match(r"(?is)^if\s+(.*?)\s+then\s*(.*)$", text)
        if not match:
            return RawStmt(text=text)
        cond_text, tail = match.groups()
        condition = parse_expr(cond_text)
        if tail:
            then_part = tail
            else_part = ""
            if re.search(r"(?i)\belse\b", tail):
                pieces = re.split(r"(?i)\belse\b", tail, maxsplit=1)
                then_part = pieces[0].strip()
                else_part = pieces[1].strip() if len(pieces) > 1 else ""
            then_stmt = self.parse_inline_statement(then_part)
            else_stmt = self.parse_inline_statement(else_part) if else_part else None
            return IfStmt(
                condition=condition,
                then_body=[then_stmt] if then_stmt is not None else [],
                else_body=[else_stmt] if else_stmt is not None else [],
                raw_text=line.raw_text,
            )

        then_body = self.parse_block(until=frozenset({"else", "end if"}))
        else_body: List[Statement] = []
        if not self.at_end() and self.peek() and self.peek().text.lower() == "else":
            self.consume()
            else_body = self.parse_block(until=frozenset({"end if"}))
        if not self.at_end() and self.peek() and self.peek().text.lower() == "end if":
            self.consume()
        return IfStmt(condition=condition, then_body=then_body, else_body=else_body, raw_text=line.raw_text)

    def parse_inline_statement(self, text: str) -> Optional[Statement]:
        temp = ProgramParser([_Line(text=text, raw_text=text)])
        block = temp.parse()
        return block[0] if block else None

    def parse_assignment(self, text: str, raw_text: str) -> Optional[Assignment]:
        eq_index = _find_top_level_eq(text)
        if eq_index == -1:
            return None
        lhs = text[:eq_index].strip()
        rhs = text[eq_index + 1 :].strip()
        set_kw = False
        if lhs.lower().startswith("set "):
            lhs = lhs[4:].strip()
            set_kw = True
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\.]*", lhs):
            return None
        return Assignment(target=lhs, expr=parse_expr(rhs), set_kw=set_kw, raw_text=raw_text)

    def parse_call_stmt(self, text: str, raw_text: str) -> Optional[CallStmt]:
        call_text = text
        if call_text.lower().startswith("call "):
            call_text = call_text[5:].strip()
        parsed = _split_call_name_and_args(call_text)
        if not parsed:
            return None
        name, rest = parsed
        receiver = None
        call_name = name
        if "." in name:
            receiver, call_name = name.rsplit(".", 1)
        if call_name.lower() in {
            "on",
            "close",
            "if",
            "then",
            "else",
            "end",
            "next",
            "for",
            "function",
            "sub",
            "class",
            "select",
            "case",
            "loop",
            "do",
            "const",
        }:
            return None
        if not rest:
            return CallStmt(receiver=receiver, name=call_name, args=[], raw_text=raw_text)
        if rest.startswith("(") and rest.endswith(")"):
            inner = rest[1:-1].strip()
            args = [] if not inner else [parse_expr(part) for part in _split_outside_strings(inner, ',')]
            return CallStmt(receiver=receiver, name=call_name, args=args, raw_text=raw_text)
        args = [parse_expr(part) for part in _split_outside_strings(rest, ',')]
        return CallStmt(receiver=receiver, name=call_name, args=args, raw_text=raw_text)


def parse_program(text: str) -> Program:
    script = extract_vbscript(text)
    lines = _logical_lines(script)
    parser = ProgramParser(lines)
    stmts = parser.parse()
    return Program(statements=stmts, original_text=text, extracted_script_text=script)
