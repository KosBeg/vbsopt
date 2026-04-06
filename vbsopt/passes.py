from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

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

_URL_RE = re.compile(r"h(?:tt|xx)p[s]?://[^\s\"']+", re.IGNORECASE)
_REG_PATH_RE = re.compile(r"HKEY_[A-Z_\\0-9]+|HK(?:CU|LM|CR|U|CC)\\[^\s\"']+", re.IGNORECASE)
_FILE_PATH_RE = re.compile(r"(?:[A-Za-z]:\\[^\n\r\"']+|%[A-Za-z_]+%\\[^\n\r\"']+)")
_COM_RE = re.compile(r"(?:[A-Za-z0-9_]+\.)+[A-Za-z0-9_]+")
_B64_RE = re.compile(r"^[A-Za-z0-9+/=\r\n]+$")

MAX_HELPER_CALL_DEPTH = 3
MAX_EXECUTE_DEPTH = 2
MAX_EXECUTE_CHARS = 12000
PURE_CALL_NAMES = {
    "chr",
    "chrw",
    "replace",
    "left",
    "right",
    "mid",
    "join",
    "split",
    "array",
    "lcase",
    "ucase",
    "trim",
    "ltrim",
    "rtrim",
    "strreverse",
    "cstr",
    "clng",
    "cint",
    "asc",
    "ascw",
    "len",
    "hex",
    "striptoken",
    "striptokens",
    "cleanbase64",
    "xorhex",
}


@dataclass(slots=True)
class Blob:
    source_var: str
    decoded_text: str
    kind: str = "base64"


@dataclass(slots=True)
class TraceEvent:
    stage: str
    action: str
    detail: str
    before: str = ""
    after: str = ""


@dataclass(slots=True)
class Stats:
    folded_concat: int = 0
    folded_chr: int = 0
    folded_replace: int = 0
    folded_substr: int = 0
    folded_case: int = 0
    folded_trim: int = 0
    folded_boolean: int = 0
    folded_reverse: int = 0
    folded_split: int = 0
    folded_cast: int = 0
    folded_asc: int = 0
    propagated_constants: int = 0
    helper_inlines: int = 0
    execute_expansions: int = 0
    recovered_nested_statements: int = 0
    base64_decodes: int = 0
    dead_assign_removed: int = 0
    blank_or_noise_removed: int = 0
    lowered_ir_instructions: int = 0
    ssa_blocks: int = 0
    ssa_phi_nodes: int = 0
    indicators_exposed: int = 0
    recursive_blob_analyses: int = 0
    ir_cse_eliminated: int = 0
    ir_dead_temps_removed: int = 0


@dataclass(slots=True)
class Context:
    constants: Dict[str, object] = field(default_factory=dict)
    symbolics: Dict[str, object] = field(default_factory=dict)
    object_types: Dict[str, str] = field(default_factory=dict)
    blobs: List[Blob] = field(default_factory=list)
    stats: Stats = field(default_factory=Stats)
    seen_blob_keys: Set[tuple[str, str]] = field(default_factory=set)
    functions: Dict[str, FunctionDecl] = field(default_factory=dict)
    subs: Dict[str, SubDecl] = field(default_factory=dict)
    recovered_strings: Set[str] = field(default_factory=set)
    debug: bool = False
    trace: List[TraceEvent] = field(default_factory=list)
    call_depth: int = 0
    execute_depth: int = 0


@dataclass(slots=True)
class EvalResult:
    ok: bool
    value: object | None = None


def _trace(ctx: Context, stage: str, action: str, detail: str, *, before: str = "", after: str = "") -> None:
    if not ctx.debug:
        return
    ctx.trace.append(TraceEvent(stage=stage, action=action, detail=detail, before=before, after=after))


def render_trace(events: Sequence[TraceEvent]) -> str:
    lines: List[str] = []
    for index, event in enumerate(events, 1):
        lines.append(f"[{index:05d}] {event.stage}:{event.action} :: {event.detail}")
        if event.before:
            lines.append(f"  before: {event.before}")
        if event.after:
            lines.append(f"  after:  {event.after}")
    return "\n".join(lines) + ("\n" if lines else "")


_SAFE_STRING_FUNCTIONS = PURE_CALL_NAMES


def is_literal(expr) -> bool:
    return isinstance(expr, (StringLiteral, NumberLiteral, BooleanLiteral)) or (
        isinstance(expr, CallExpr) and expr.name.lower() == "array" and all(is_literal(arg) for arg in expr.args)
    )


def expr_to_python(expr):
    if isinstance(expr, StringLiteral):
        return expr.value
    if isinstance(expr, NumberLiteral):
        return expr.value
    if isinstance(expr, BooleanLiteral):
        return expr.value
    if isinstance(expr, CallExpr) and expr.name.lower() == "array" and all(is_literal(arg) for arg in expr.args):
        return [expr_to_python(arg) for arg in expr.args]
    raise TypeError(type(expr))



def python_to_expr(value):
    if isinstance(value, bool):
        return BooleanLiteral(value)
    if isinstance(value, int):
        return NumberLiteral(value)
    if isinstance(value, list):
        return CallExpr("Array", [python_to_expr(item) for item in value])
    return StringLiteral(str(value))


def clone_expr(expr):
    if isinstance(expr, StringLiteral):
        return StringLiteral(expr.value)
    if isinstance(expr, NumberLiteral):
        return NumberLiteral(expr.value)
    if isinstance(expr, BooleanLiteral):
        return BooleanLiteral(expr.value)
    if isinstance(expr, Identifier):
        return Identifier(expr.name)
    if isinstance(expr, UnaryOp):
        return UnaryOp(expr.op, clone_expr(expr.operand))
    if isinstance(expr, BinaryOp):
        return BinaryOp(expr.op, clone_expr(expr.left), clone_expr(expr.right))
    if isinstance(expr, Concat):
        return Concat(clone_expr(expr.left), clone_expr(expr.right))
    if isinstance(expr, CallExpr):
        return CallExpr(expr.name, [clone_expr(arg) for arg in expr.args])
    if isinstance(expr, RawExpr):
        return RawExpr(expr.text)
    return expr


def substitute_identifier(expr, name: str, replacement):
    if isinstance(expr, Identifier):
        if expr.name == name:
            return clone_expr(replacement)
        return Identifier(expr.name)
    if isinstance(expr, UnaryOp):
        return UnaryOp(expr.op, substitute_identifier(expr.operand, name, replacement))
    if isinstance(expr, BinaryOp):
        return BinaryOp(
            expr.op,
            substitute_identifier(expr.left, name, replacement),
            substitute_identifier(expr.right, name, replacement),
        )
    if isinstance(expr, Concat):
        return Concat(
            substitute_identifier(expr.left, name, replacement),
            substitute_identifier(expr.right, name, replacement),
        )
    if isinstance(expr, CallExpr):
        return CallExpr(expr.name, [substitute_identifier(arg, name, replacement) for arg in expr.args])
    return clone_expr(expr)


def clone_statement(stmt: Statement) -> Statement:
    if isinstance(stmt, DimStmt):
        return DimStmt(list(stmt.names), raw_text=stmt.raw_text)
    if isinstance(stmt, ConstStmt):
        return ConstStmt(stmt.name, clone_expr(stmt.expr), raw_text=stmt.raw_text)
    if isinstance(stmt, Assignment):
        return Assignment(stmt.target, clone_expr(stmt.expr), set_kw=stmt.set_kw, raw_text=stmt.raw_text)
    if isinstance(stmt, CallStmt):
        return CallStmt(stmt.receiver, stmt.name, [clone_expr(arg) for arg in stmt.args], raw_text=stmt.raw_text)
    if isinstance(stmt, IfStmt):
        return IfStmt(
            condition=clone_expr(stmt.condition),
            then_body=[clone_statement(inner) for inner in stmt.then_body],
            else_body=[clone_statement(inner) for inner in stmt.else_body],
            raw_text=stmt.raw_text,
        )
    if isinstance(stmt, FunctionDecl):
        return FunctionDecl(stmt.name, list(stmt.params), [clone_statement(inner) for inner in stmt.body], raw_text=stmt.raw_text)
    if isinstance(stmt, SubDecl):
        return SubDecl(stmt.name, list(stmt.params), [clone_statement(inner) for inner in stmt.body], raw_text=stmt.raw_text)
    if isinstance(stmt, RawStmt):
        return RawStmt(stmt.text)
    return stmt


def _merge_symbolic_env(left: Dict[str, object], right: Dict[str, object]) -> Dict[str, object]:
    merged: Dict[str, object] = {}
    for key in set(left) & set(right):
        if left[key] == right[key]:
            merged[key] = clone_expr(left[key]) if hasattr(left[key], '__class__') else left[key]
    return merged


def _merge_string_env(left: Dict[str, str], right: Dict[str, str]) -> Dict[str, str]:
    merged: Dict[str, str] = {}
    for key in set(left) & set(right):
        if left[key] == right[key]:
            merged[key] = left[key]
    return merged


def _record_recovered_string(ctx: Context, value: str) -> None:
    if not value:
        return
    interesting = False
    if extract_indicators(value):
        interesting = True
    if re.search(r"(?i)\.(?:hta|vbs|vbe|exe|dll|txt|log|json|xml|bin|dat|ps1|cmd|bat|js|tmp|m4v)\b", value):
        interesting = True
    if "utf-8" in value.lower() or "%temp%" in value.lower() or value.startswith("\\"):
        interesting = True
    if interesting:
        ctx.recovered_strings.add(value)


def decode_base64_maybe(value: str) -> Optional[str]:
    cleaned = "".join(ch for ch in value if ch not in " \t\r\n")
    if len(cleaned) < 16 or len(cleaned) % 4 != 0 or not _B64_RE.match(cleaned):
        return None
    try:
        raw = base64.b64decode(cleaned, validate=True)
    except Exception:
        return None
    if not raw:
        return None
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            decoded = raw.decode("latin-1")
        except UnicodeDecodeError:
            return None
    printable = sum(1 for ch in decoded if ch in "\n\r\t" or 32 <= ord(ch) < 127)
    if printable / max(1, len(decoded)) < 0.9:
        return None
    return decoded



def extract_indicators(text: str) -> Set[str]:
    out: Set[str] = set()
    out.update(_URL_RE.findall(text))
    out.update(_REG_PATH_RE.findall(text))
    out.update(_FILE_PATH_RE.findall(text))
    for value in _COM_RE.findall(text):
        if value.lower() not in {"hxxp", "https", "http", "text.vbscript"}:
            out.add(value)
    return out



def _merge_constant_env(left: Dict[str, object], right: Dict[str, object]) -> Dict[str, object]:
    merged: Dict[str, object] = {}
    for key in set(left) & set(right):
        if left[key] == right[key]:
            merged[key] = left[key]
    return merged



def _parse_int_like(value: object) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        raw = value.strip()
        try:
            if raw.lower().startswith("&h"):
                return int(raw[2:], 16)
            return int(raw)
        except Exception:
            return None
    return None



def _split_receiver_call(name: str) -> tuple[str | None, str]:
    if "." not in name:
        return None, name
    receiver, method = name.rsplit(".", 1)
    return receiver, method



def _eval_object_method(name: str, args: Sequence[object], ctx: Context) -> tuple[bool, object | None]:
    receiver, method = _split_receiver_call(name)
    if not receiver:
        return False, None
    progid = ctx.object_types.get(receiver, "").lower()
    lower_method = method.lower()
    if progid == "wscript.shell" and lower_method == "expandenvironmentstrings" and len(args) == 1 and isinstance(args[0], str):
        return True, args[0]
    return False, None



def _eval_known_call(name: str, args: Sequence[object], ctx: Context) -> tuple[bool, object | None]:
    lower = name.lower()
    try:
        ok, value = _eval_object_method(name, args, ctx)
        if ok:
            return True, value
        if lower in {"chr", "chrw"} and len(args) == 1:
            code = _parse_int_like(args[0])
            if code is not None:
                ctx.stats.folded_chr += 1
                return True, chr(code)
        if lower.endswith("replace") and len(args) == 3 and all(isinstance(arg, str) for arg in args):
            ctx.stats.folded_replace += 1
            old, new = args[1], args[2]
            return True, args[0].replace(old, new)
        if lower == "left" and len(args) == 2 and isinstance(args[0], str):
            count = _parse_int_like(args[1])
            if count is not None:
                ctx.stats.folded_substr += 1
                return True, args[0][:count]
        if lower == "right" and len(args) == 2 and isinstance(args[0], str):
            count = _parse_int_like(args[1])
            if count is not None:
                ctx.stats.folded_substr += 1
                return True, args[0][-count:] if count else ""
        if lower == "mid" and len(args) >= 2 and isinstance(args[0], str):
            start = _parse_int_like(args[1])
            if start is not None:
                start_idx = max(start - 1, 0)
                if len(args) >= 3:
                    count = _parse_int_like(args[2])
                    if count is not None:
                        ctx.stats.folded_substr += 1
                        return True, args[0][start_idx : start_idx + count]
                ctx.stats.folded_substr += 1
                return True, args[0][start_idx:]
        if lower == "lcase" and len(args) == 1 and isinstance(args[0], str):
            ctx.stats.folded_case += 1
            return True, args[0].lower()
        if lower == "ucase" and len(args) == 1 and isinstance(args[0], str):
            ctx.stats.folded_case += 1
            return True, args[0].upper()
        if lower == "trim" and len(args) == 1 and isinstance(args[0], str):
            ctx.stats.folded_trim += 1
            return True, args[0].strip()
        if lower == "ltrim" and len(args) == 1 and isinstance(args[0], str):
            ctx.stats.folded_trim += 1
            return True, args[0].lstrip()
        if lower == "rtrim" and len(args) == 1 and isinstance(args[0], str):
            ctx.stats.folded_trim += 1
            return True, args[0].rstrip()
        if lower == "strreverse" and len(args) == 1 and isinstance(args[0], str):
            ctx.stats.folded_reverse += 1
            return True, args[0][::-1]
        if lower == "array":
            return True, list(args)
        if lower == "split" and len(args) >= 1 and isinstance(args[0], str):
            sep = " "
            if len(args) > 1 and isinstance(args[1], str):
                sep = args[1]
            ctx.stats.folded_split += 1
            return True, args[0].split(sep)
        if lower == "join" and len(args) >= 1 and isinstance(args[0], list):
            sep = " "
            if len(args) > 1 and isinstance(args[1], str):
                sep = args[1]
            if all(isinstance(item, str) for item in args[0]):
                ctx.stats.folded_concat += max(len(args[0]) - 1, 0)
                return True, sep.join(args[0])
        if lower == "cstr" and len(args) == 1:
            ctx.stats.folded_cast += 1
            return True, str(args[0])
        if lower in {"clng", "cint", "int"} and len(args) == 1:
            if lower == "int" and isinstance(args[0], (int, float)):
                ctx.stats.folded_cast += 1
                return True, int(args[0])
            number = _parse_int_like(args[0])
            if number is not None:
                ctx.stats.folded_cast += 1
                return True, number
        if lower == "hex" and len(args) == 1:
            number = _parse_int_like(args[0])
            if number is not None:
                ctx.stats.folded_cast += 1
                return True, format(number, "X")
        if lower in {"asc", "ascw"} and len(args) == 1 and isinstance(args[0], str) and args[0]:
            ctx.stats.folded_asc += 1
            return True, ord(args[0][0])
        if lower == "len" and len(args) == 1:
            if isinstance(args[0], (str, list)):
                ctx.stats.folded_cast += 1
                return True, len(args[0])
        if lower == "xorhex" and len(args) == 2 and isinstance(args[0], str):
            key = _parse_int_like(args[1])
            if key is not None:
                raw = bytes.fromhex(args[0])
                decoded = bytes(b ^ (key & 0xFF) for b in raw)
                try:
                    return True, decoded.decode("utf-8")
                except UnicodeDecodeError:
                    return True, decoded.decode("latin-1")
        if lower in {"striptoken", "striptokens", "cleanbase64"} and args and isinstance(args[0], str):
            value = args[0]
            for token in ("??", "***"):
                value = value.replace(token, "")
            return True, value
    except Exception:
        return False, None
    return False, None



def maybe_decode_literal(value: str, ctx: Context, source_var: str) -> Optional[str]:
    cleaned = value
    for token in ("??", "***"):
        cleaned = cleaned.replace(token, "")
    decoded = decode_base64_maybe(cleaned)
    if decoded is None:
        return None
    key = (decoded, "base64")
    if key in ctx.seen_blob_keys:
        return decoded
    ctx.seen_blob_keys.add(key)
    ctx.stats.base64_decodes += 1
    ctx.blobs.append(Blob(source_var=source_var, decoded_text=decoded, kind="base64"))
    return decoded



def _is_property_target(name: str) -> bool:
    return '.' in name



def _receiver_idents(name: str) -> Set[str]:
    out: Set[str] = set()
    for part in name.split('.'):
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part):
            out.add(part)
    return out


def _child_context(ctx: Context, *, call_depth: Optional[int] = None, execute_depth: Optional[int] = None) -> Context:
    return Context(
        constants={},
        symbolics={},
        object_types=dict(ctx.object_types),
        blobs=ctx.blobs,
        stats=ctx.stats,
        seen_blob_keys=ctx.seen_blob_keys,
        functions=dict(ctx.functions),
        subs=dict(ctx.subs),
        recovered_strings=ctx.recovered_strings,
        debug=ctx.debug,
        trace=ctx.trace,
        call_depth=ctx.call_depth if call_depth is None else call_depth,
        execute_depth=ctx.execute_depth if execute_depth is None else execute_depth,
    )



def _expr_is_pure(expr, functions: Dict[str, FunctionDecl], seen_funcs: Optional[Set[str]] = None) -> bool:
    if isinstance(expr, (StringLiteral, NumberLiteral, BooleanLiteral, Identifier)):
        return True
    if isinstance(expr, UnaryOp):
        return _expr_is_pure(expr.operand, functions, seen_funcs)
    if isinstance(expr, (BinaryOp, Concat)):
        return _expr_is_pure(expr.left, functions, seen_funcs) and _expr_is_pure(expr.right, functions, seen_funcs)
    if isinstance(expr, CallExpr):
        lower = expr.name.lower()
        if not all(_expr_is_pure(arg, functions, seen_funcs) for arg in expr.args):
            return False
        if lower in PURE_CALL_NAMES:
            return True
        if lower in functions:
            return _function_is_pure(functions[lower], functions, seen_funcs)
        return False
    return False



def _stmt_block_is_pure(statements: Sequence[Statement], functions: Dict[str, FunctionDecl], seen_funcs: Optional[Set[str]] = None) -> bool:
    for stmt in statements:
        if isinstance(stmt, DimStmt):
            continue
        if isinstance(stmt, ConstStmt):
            if not _expr_is_pure(stmt.expr, functions, seen_funcs):
                return False
            continue
        if isinstance(stmt, Assignment):
            if not _expr_is_pure(stmt.expr, functions, seen_funcs):
                return False
            continue
        if isinstance(stmt, IfStmt):
            if not _expr_is_pure(stmt.condition, functions, seen_funcs):
                return False
            if not _stmt_block_is_pure(stmt.then_body, functions, seen_funcs):
                return False
            if not _stmt_block_is_pure(stmt.else_body, functions, seen_funcs):
                return False
            continue
        return False
    return True



def _function_is_pure(func: FunctionDecl, functions: Dict[str, FunctionDecl], seen_funcs: Optional[Set[str]] = None) -> bool:
    seen = set(seen_funcs or set())
    lower_name = func.name.lower()
    if lower_name in seen:
        return False
    seen.add(lower_name)
    return _stmt_block_is_pure(func.body, functions, seen)



def _evaluate_user_function(func: FunctionDecl, args: Sequence[object], ctx: Context) -> EvalResult:
    if ctx.call_depth >= MAX_HELPER_CALL_DEPTH:
        return EvalResult(False, None)
    if len(args) != len(func.params):
        return EvalResult(False, None)
    if not _function_is_pure(func, ctx.functions):
        return EvalResult(False, None)

    child = _child_context(ctx, call_depth=ctx.call_depth + 1, execute_depth=ctx.execute_depth)
    for param, arg in zip(func.params, args):
        child.constants[param] = arg
        child.symbolics[param] = python_to_expr(arg)
    body = optimize_statements([clone_statement(stmt) for stmt in func.body], child)
    body, _ = eliminate_dead_assignments(body, child, live_after={func.name})
    if func.name in child.constants:
        ctx.stats.helper_inlines += 1
        return EvalResult(True, child.constants[func.name])
    return EvalResult(False, None)



def _expr_contains_identifier(expr, name: str) -> bool:
    if isinstance(expr, Identifier):
        return expr.name == name
    if isinstance(expr, UnaryOp):
        return _expr_contains_identifier(expr.operand, name)
    if isinstance(expr, (BinaryOp, Concat)):
        return _expr_contains_identifier(expr.left, name) or _expr_contains_identifier(expr.right, name)
    if isinstance(expr, CallExpr):
        return any(_expr_contains_identifier(arg, name) for arg in expr.args)
    return False



def _expr_has_string_hint(expr, ctx: Context, *, skip_ident: str | None = None) -> bool:
    if isinstance(expr, StringLiteral):
        return True
    if isinstance(expr, Identifier):
        if skip_ident is not None and expr.name == skip_ident:
            return False
        value = ctx.constants.get(expr.name)
        if isinstance(value, str):
            return True
        symbolic = ctx.symbolics.get(expr.name)
        if symbolic is not None:
            return _expr_has_string_hint(symbolic, ctx, skip_ident=skip_ident)
        return False
    if isinstance(expr, UnaryOp):
        return _expr_has_string_hint(expr.operand, ctx, skip_ident=skip_ident)
    if isinstance(expr, Concat):
        return True
    if isinstance(expr, BinaryOp):
        return _expr_has_string_hint(expr.left, ctx, skip_ident=skip_ident) or _expr_has_string_hint(expr.right, ctx, skip_ident=skip_ident)
    if isinstance(expr, CallExpr):
        lower = expr.name.lower()
        if lower in {
            'chr', 'chrw', 'replace', 'left', 'right', 'mid', 'join', 'lcase', 'ucase', 'trim',
            'ltrim', 'rtrim', 'strreverse', 'cstr', 'hex', 'striptoken', 'striptokens', 'cleanbase64', 'xorhex'
        }:
            return True
        if lower in ctx.functions and _function_is_pure(ctx.functions[lower], ctx.functions):
            return True
        return any(_expr_has_string_hint(arg, ctx, skip_ident=skip_ident) for arg in expr.args)
    return False



def _try_fold_self_build_assignment(target: str, expr, ctx: Context):
    if not _expr_contains_identifier(expr, target):
        return None
    if isinstance(expr, Identifier):
        return None
    if not isinstance(expr, (BinaryOp, Concat, CallExpr, UnaryOp)):
        return None
    if not _expr_has_string_hint(expr, ctx, skip_ident=target):
        return None
    if target in ctx.constants:
        replacement = python_to_expr(ctx.constants[target])
        seed_kind = "constant"
    elif target in ctx.symbolics:
        replacement = clone_expr(ctx.symbolics[target])
        seed_kind = "symbolic"
    else:
        replacement = StringLiteral("")
        seed_kind = "empty"
    child = _child_context(ctx, call_depth=ctx.call_depth, execute_depth=ctx.execute_depth)
    child.constants = dict(ctx.constants)
    child.symbolics = {name: clone_expr(value) for name, value in ctx.symbolics.items()}
    child.constants.pop(target, None)
    child.symbolics.pop(target, None)
    rewritten = substitute_identifier(clone_expr(expr), target, replacement)
    folded = eval_expr(rewritten, child)
    _trace(
        ctx,
        "ast",
        "self-build",
        f"{target} seeded from {seed_kind}",
        before=expr_to_text(expr),
        after=expr_to_text(folded),
    )
    return folded



def eval_expr(expr, ctx: Context, seen: Optional[Set[str]] = None):
    seen_names = set(seen or set())
    if isinstance(expr, (StringLiteral, NumberLiteral, BooleanLiteral)):
        return expr
    if isinstance(expr, Identifier):
        if expr.name in ctx.constants:
            ctx.stats.propagated_constants += 1
            folded = python_to_expr(ctx.constants[expr.name])
            _trace(ctx, "ast", "const-prop", expr.name, before=expr.name, after=expr_to_text(folded))
            return folded
        if expr.name in ctx.symbolics and expr.name not in seen_names:
            seen_names.add(expr.name)
            symbolic = eval_expr(clone_expr(ctx.symbolics[expr.name]), ctx, seen_names)
            _trace(ctx, "ast", "symbolic-inline", expr.name, before=expr.name, after=expr_to_text(symbolic))
            return symbolic
        return expr
    if isinstance(expr, UnaryOp):
        inner = eval_expr(expr.operand, ctx, seen_names)
        if isinstance(inner, RawExpr):
            return UnaryOp(expr.op, inner)
        if expr.op == "not" and isinstance(inner, BooleanLiteral):
            ctx.stats.folded_boolean += 1
            out = BooleanLiteral(not inner.value)
            _trace(ctx, "ast", "fold-unary", expr.op, before=expr_to_text(expr), after=expr_to_text(out))
            return out
        if expr.op == "-" and isinstance(inner, NumberLiteral):
            out = NumberLiteral(-inner.value)
            _trace(ctx, "ast", "fold-unary", expr.op, before=expr_to_text(expr), after=expr_to_text(out))
            return out
        return UnaryOp(expr.op, inner)
    if isinstance(expr, Concat):
        left = eval_expr(expr.left, ctx, seen_names)
        right = eval_expr(expr.right, ctx, seen_names)
        if is_literal(left) and is_literal(right):
            ctx.stats.folded_concat += 1
            out = StringLiteral(str(expr_to_python(left)) + str(expr_to_python(right)))
            _trace(ctx, "ast", "fold-concat", "literal concat", before=expr_to_text(expr), after=expr_to_text(out))
            return out
        return Concat(left, right)
    if isinstance(expr, BinaryOp):
        left = eval_expr(expr.left, ctx, seen_names)
        right = eval_expr(expr.right, ctx, seen_names)
        if expr.op == "+":
            if isinstance(left, NumberLiteral) and isinstance(right, NumberLiteral):
                out = NumberLiteral(left.value + right.value)
                _trace(ctx, "ast", "fold-add", "+", before=expr_to_text(expr), after=expr_to_text(out))
                return out
            if is_literal(left) and is_literal(right):
                ctx.stats.folded_concat += 1
                out = StringLiteral(str(expr_to_python(left)) + str(expr_to_python(right)))
                _trace(ctx, "ast", "fold-add", "stringy +", before=expr_to_text(expr), after=expr_to_text(out))
                return out
            return BinaryOp(expr.op, left, right)
        if expr.op == "-" and isinstance(left, NumberLiteral) and isinstance(right, NumberLiteral):
            out = NumberLiteral(left.value - right.value)
            _trace(ctx, "ast", "fold-sub", "-", before=expr_to_text(expr), after=expr_to_text(out))
            return out
        if expr.op == "*" and isinstance(left, NumberLiteral) and isinstance(right, NumberLiteral):
            out = NumberLiteral(left.value * right.value)
            _trace(ctx, "ast", "fold-mul", "*", before=expr_to_text(expr), after=expr_to_text(out))
            return out
        if expr.op == "/" and isinstance(left, NumberLiteral) and isinstance(right, NumberLiteral) and right.value != 0:
            out = NumberLiteral(int(left.value / right.value)) if left.value % right.value == 0 else RawExpr(expr_to_text(BinaryOp(expr.op, left, right)))
            _trace(ctx, "ast", "fold-div", "/", before=expr_to_text(expr), after=expr_to_text(out))
            return out
        if expr.op == "xor" and isinstance(left, NumberLiteral) and isinstance(right, NumberLiteral):
            ctx.stats.folded_boolean += 1
            out = NumberLiteral(left.value ^ right.value)
            _trace(ctx, "ast", "fold-xor", "xor", before=expr_to_text(expr), after=expr_to_text(out))
            return out
        if is_literal(left) and is_literal(right):
            lval = expr_to_python(left)
            rval = expr_to_python(right)
            if expr.op == "=":
                ctx.stats.folded_boolean += 1
                return BooleanLiteral(lval == rval)
            if expr.op == "<>":
                ctx.stats.folded_boolean += 1
                return BooleanLiteral(lval != rval)
            if expr.op == "<":
                ctx.stats.folded_boolean += 1
                return BooleanLiteral(lval < rval)
            if expr.op == ">":
                ctx.stats.folded_boolean += 1
                return BooleanLiteral(lval > rval)
            if expr.op == "<=":
                ctx.stats.folded_boolean += 1
                return BooleanLiteral(lval <= rval)
            if expr.op == ">=":
                ctx.stats.folded_boolean += 1
                return BooleanLiteral(lval >= rval)
            if expr.op == "and" and isinstance(lval, bool) and isinstance(rval, bool):
                ctx.stats.folded_boolean += 1
                return BooleanLiteral(lval and rval)
            if expr.op == "or" and isinstance(lval, bool) and isinstance(rval, bool):
                ctx.stats.folded_boolean += 1
                return BooleanLiteral(lval or rval)
        return BinaryOp(expr.op, left, right)
    if isinstance(expr, CallExpr):
        args = [eval_expr(arg, ctx, seen_names) for arg in expr.args]
        lower_name = expr.name.lower()
        if lower_name == "eval" and len(args) == 1 and isinstance(args[0], StringLiteral):
            try:
                from .parser import parse_expr as _parse_expr
                inner = _parse_expr(args[0].value)
                if not isinstance(inner, RawExpr):
                    folded_inner = eval_expr(inner, ctx, seen_names)
                    _trace(ctx, "ast", "expand-eval", expr.name, before=expr_to_text(expr), after=expr_to_text(folded_inner))
                    return folded_inner
            except Exception:
                pass
        if all(is_literal(arg) for arg in args):
            py_args = [expr_to_python(arg) for arg in args]
            ok, value = _eval_known_call(expr.name, py_args, ctx)
            if ok:
                out = python_to_expr(value) if not isinstance(value, list) else CallExpr("Array", [python_to_expr(item) for item in value])
                _trace(ctx, "ast", "fold-call", expr.name, before=expr_to_text(expr), after=expr_to_text(out))
                return out
            func = ctx.functions.get(lower_name)
            if func is not None:
                result = _evaluate_user_function(func, py_args, ctx)
                if result.ok:
                    out = python_to_expr(result.value) if not isinstance(result.value, list) else CallExpr("Array", [python_to_expr(item) for item in result.value])
                    _trace(ctx, "ast", "inline-helper", expr.name, before=expr_to_text(expr), after=expr_to_text(out))
                    return out
        return CallExpr(expr.name, args)
    if isinstance(expr, RawExpr):
        return expr
    return expr


def _flatten_statement_count(statements: Sequence[Statement]) -> int:
    count = 0
    for stmt in statements:
        count += 1
        if isinstance(stmt, IfStmt):
            count += _flatten_statement_count(stmt.then_body)
            count += _flatten_statement_count(stmt.else_body)
        elif isinstance(stmt, (FunctionDecl, SubDecl)):
            count += _flatten_statement_count(stmt.body)
    return count



def collect_declarations(statements: Sequence[Statement], ctx: Context) -> None:
    for stmt in statements:
        if isinstance(stmt, FunctionDecl):
            ctx.functions[stmt.name.lower()] = stmt
            collect_declarations(stmt.body, ctx)
        elif isinstance(stmt, SubDecl):
            ctx.subs[stmt.name.lower()] = stmt
            collect_declarations(stmt.body, ctx)
        elif isinstance(stmt, IfStmt):
            collect_declarations(stmt.then_body, ctx)
            collect_declarations(stmt.else_body, ctx)



def _expand_executed_script(script_text: str, ctx: Context, source_name: str) -> Optional[List[Statement]]:
    if ctx.execute_depth >= MAX_EXECUTE_DEPTH or len(script_text) > MAX_EXECUTE_CHARS:
        return None
    decoded = maybe_decode_literal(script_text, ctx, source_name)
    if decoded and decoded.strip():
        script_text = decoded
    if not script_text.strip():
        return []
    from .parser import parse_program  # local import to avoid cycle

    inner_prog = parse_program(script_text)
    inner_ctx = _child_context(ctx, call_depth=ctx.call_depth, execute_depth=ctx.execute_depth + 1)
    collect_declarations(inner_prog.statements, inner_ctx)
    inner_statements = optimize_statements(inner_prog.statements, inner_ctx)
    inner_statements, _ = eliminate_dead_assignments(inner_statements, inner_ctx)
    inner_statements = prune_unused_declarations(inner_statements)
    ctx.functions.update(inner_ctx.functions)
    ctx.subs.update(inner_ctx.subs)
    return inner_statements



def _expand_user_routine(name: str, args: Sequence[object], ctx: Context) -> Optional[tuple[List[Statement], Context]]:
    lower = name.lower()
    decl: FunctionDecl | SubDecl | None = ctx.subs.get(lower) or ctx.functions.get(lower)
    if decl is None:
        return None
    if ctx.call_depth >= MAX_HELPER_CALL_DEPTH:
        return None
    if len(args) != len(decl.params):
        return None
    child = _child_context(ctx, call_depth=ctx.call_depth + 1, execute_depth=ctx.execute_depth)
    child.constants = dict(ctx.constants)
    child.symbolics = {k: clone_expr(v) for k, v in ctx.symbolics.items()}
    child.object_types = dict(ctx.object_types)
    for param, arg in zip(decl.params, args):
        if is_literal(arg):
            child.constants[param] = expr_to_python(arg)
        child.symbolics[param] = clone_expr(arg)
    body = optimize_statements([clone_statement(stmt) for stmt in decl.body], child)
    body, _, _ = eliminate_shadowed_assignments(body, child)
    live_after = {decl.name} if isinstance(decl, FunctionDecl) else None
    body, _ = eliminate_dead_assignments(body, child, live_after=live_after)
    body = prune_unused_declarations(body)
    return body, child


def _statement_has_supported_effect(stmt: Statement, ctx: Context) -> bool:
    if isinstance(stmt, RawStmt):
        return False
    if isinstance(stmt, IfStmt):
        return any(_statement_has_supported_effect(s, ctx) for s in stmt.then_body) or any(
            _statement_has_supported_effect(s, ctx) for s in stmt.else_body
        )
    if isinstance(stmt, Assignment):
        if _is_property_target(stmt.target):
            return True
        if stmt.set_kw and isinstance(stmt.expr, CallExpr) and stmt.expr.name.lower() == 'createobject':
            return len(stmt.expr.args) == 1 and isinstance(stmt.expr.args[0], StringLiteral)
        return False
    if isinstance(stmt, ConstStmt):
        return False
    if isinstance(stmt, CallStmt):
        if stmt.receiver is None:
            return stmt.name.lower() in {'execute', 'executeglobal'}
        receiver = stmt.receiver.split('.')[0]
        return receiver in ctx.object_types
    return False


def _expanded_body_is_useful(body: Sequence[Statement], ctx: Context) -> bool:
    return any(_statement_has_supported_effect(stmt, ctx) for stmt in body)


def optimize_statements(statements: Sequence[Statement], ctx: Context) -> List[Statement]:
    out: List[Statement] = []
    for stmt in statements:
        if isinstance(stmt, DimStmt):
            out.append(stmt)
            continue
        if isinstance(stmt, ConstStmt):
            expr = eval_expr(stmt.expr, ctx)
            new_stmt = ConstStmt(name=stmt.name, expr=expr, raw_text=stmt.raw_text)
            ctx.symbolics[stmt.name] = clone_expr(expr)
            ctx.object_types.pop(stmt.name, None)
            if is_literal(expr):
                value = expr_to_python(expr)
                ctx.constants[stmt.name] = value
                if isinstance(value, str):
                    maybe_decode_literal(value, ctx, stmt.name)
                    _record_recovered_string(ctx, value)
            else:
                ctx.constants.pop(stmt.name, None)
            _trace(ctx, "ast", "bind-const", stmt.name, before=stmt.raw_text, after=expr_to_text(expr))
            out.append(new_stmt)
            continue
        if isinstance(stmt, Assignment):
            expr = None
            if not _is_property_target(stmt.target) and stmt.target not in ctx.constants and stmt.target not in ctx.symbolics:
                expr = _try_fold_self_build_assignment(stmt.target, stmt.expr, ctx)
            if expr is None:
                expr = eval_expr(stmt.expr, ctx)
            if not _is_property_target(stmt.target) and isinstance(expr, Identifier) and expr.name.lower() == stmt.target.lower() and not stmt.set_kw:
                _trace(ctx, "dce", "drop-noop-assign", stmt.target, before=stmt.raw_text)
                continue
            new_stmt = Assignment(target=stmt.target, expr=expr, set_kw=stmt.set_kw, raw_text=stmt.raw_text)
            if _is_property_target(stmt.target):
                if is_literal(expr) and isinstance(expr_to_python(expr), str):
                    _record_recovered_string(ctx, str(expr_to_python(expr)))
                out.append(new_stmt)
                continue
            ctx.symbolics[stmt.target] = clone_expr(expr)
            ctx.object_types.pop(stmt.target, None)
            if stmt.set_kw and isinstance(expr, CallExpr) and expr.name.lower() == "createobject" and len(expr.args) == 1 and isinstance(expr.args[0], StringLiteral):
                ctx.object_types[stmt.target] = expr.args[0].value
                _trace(ctx, "ast", "bind-object", stmt.target, before=stmt.raw_text, after=expr.args[0].value)
            if is_literal(expr):
                value = expr_to_python(expr)
                ctx.constants[stmt.target] = value
                if isinstance(value, str):
                    maybe_decode_literal(value, ctx, stmt.target)
                    _record_recovered_string(ctx, value)
            else:
                ctx.constants.pop(stmt.target, None)
            _trace(ctx, "ast", "bind-assign", stmt.target, before=stmt.raw_text, after=expr_to_text(expr))
            out.append(new_stmt)
            continue
        if isinstance(stmt, CallStmt):
            args = [eval_expr(arg, ctx) for arg in stmt.args]
            lower_name = stmt.name.lower()
            if stmt.receiver is None and lower_name in {"execute", "executeglobal"} and len(args) == 1 and isinstance(args[0], StringLiteral):
                expanded = _expand_executed_script(args[0].value, ctx, source_name=f"__{lower_name}_arg")
                if expanded is not None:
                    ctx.stats.execute_expansions += 1
                    ctx.stats.recovered_nested_statements += _flatten_statement_count(expanded)
                    _trace(ctx, "ast", "expand-execute", stmt.name, before=stmt.raw_text, after=f"{len(expanded)} stmts")
                    out.append(RawStmt(text=f"' --- expanded {stmt.name} ---"))
                    out.extend(expanded)
                    out.append(RawStmt(text=f"' --- end expanded {stmt.name} ---"))
                    continue
            if stmt.receiver is None:
                expanded_routine = _expand_user_routine(stmt.name, args, ctx)
                if expanded_routine is not None:
                    expanded_body, expanded_ctx = expanded_routine
                    if _expanded_body_is_useful(expanded_body, expanded_ctx):
                        _trace(ctx, "ast", "expand-routine", stmt.name, before=stmt.raw_text, after=f"{len(expanded_body)} stmts")
                        out.append(RawStmt(text=f"' --- expanded {stmt.name} ---"))
                        out.extend(expanded_body)
                        out.append(RawStmt(text=f"' --- end expanded {stmt.name} ---"))
                    else:
                        _trace(ctx, "ast", "drop-routine-call", stmt.name, before=stmt.raw_text, after="no supported effect after optimization")
                    continue
            out.append(CallStmt(receiver=stmt.receiver, name=stmt.name, args=args, raw_text=stmt.raw_text))
            continue
        if isinstance(stmt, IfStmt):
            condition = eval_expr(stmt.condition, ctx)
            if isinstance(condition, BooleanLiteral):
                chosen = stmt.then_body if condition.value else stmt.else_body
                _trace(ctx, "ast", "fold-if", "constant condition", before=expr_to_text(stmt.condition), after=str(condition.value))
                out.extend(optimize_statements(chosen, ctx))
                continue
            left_ctx = _child_context(ctx, call_depth=ctx.call_depth, execute_depth=ctx.execute_depth)
            left_ctx.constants = dict(ctx.constants)
            left_ctx.symbolics = {k: clone_expr(v) for k, v in ctx.symbolics.items()}
            left_ctx.object_types = dict(ctx.object_types)
            right_ctx = _child_context(ctx, call_depth=ctx.call_depth, execute_depth=ctx.execute_depth)
            right_ctx.constants = dict(ctx.constants)
            right_ctx.symbolics = {k: clone_expr(v) for k, v in ctx.symbolics.items()}
            right_ctx.object_types = dict(ctx.object_types)
            then_body = optimize_statements(stmt.then_body, left_ctx)
            else_body = optimize_statements(stmt.else_body, right_ctx)
            ctx.constants = _merge_constant_env(left_ctx.constants, right_ctx.constants)
            ctx.symbolics = _merge_symbolic_env(left_ctx.symbolics, right_ctx.symbolics)
            ctx.object_types = _merge_string_env(left_ctx.object_types, right_ctx.object_types)
            ctx.seen_blob_keys |= left_ctx.seen_blob_keys | right_ctx.seen_blob_keys
            out.append(IfStmt(condition=condition, then_body=then_body, else_body=else_body, raw_text=stmt.raw_text))
            continue
        if isinstance(stmt, FunctionDecl):
            inner_ctx = _child_context(ctx, call_depth=ctx.call_depth, execute_depth=ctx.execute_depth)
            inner_ctx.constants = dict(ctx.constants)
            inner_ctx.symbolics = {k: clone_expr(v) for k, v in ctx.symbolics.items()}
            inner_ctx.object_types = dict(ctx.object_types)
            inner_ctx.functions.update(ctx.functions)
            inner_ctx.subs.update(ctx.subs)
            body = optimize_statements([clone_statement(s) for s in stmt.body], inner_ctx)
            body, _, _ = eliminate_shadowed_assignments(body, inner_ctx)
            body, _ = eliminate_dead_assignments(body, inner_ctx, live_after={stmt.name})
            out.append(FunctionDecl(name=stmt.name, params=list(stmt.params), body=body, raw_text=stmt.raw_text))
            continue
        if isinstance(stmt, SubDecl):
            inner_ctx = _child_context(ctx, call_depth=ctx.call_depth, execute_depth=ctx.execute_depth)
            inner_ctx.constants = dict(ctx.constants)
            inner_ctx.symbolics = {k: clone_expr(v) for k, v in ctx.symbolics.items()}
            inner_ctx.object_types = dict(ctx.object_types)
            inner_ctx.functions.update(ctx.functions)
            inner_ctx.subs.update(ctx.subs)
            body = optimize_statements([clone_statement(s) for s in stmt.body], inner_ctx)
            body, _, _ = eliminate_shadowed_assignments(body, inner_ctx)
            body, _ = eliminate_dead_assignments(body, inner_ctx)
            out.append(SubDecl(name=stmt.name, params=list(stmt.params), body=body, raw_text=stmt.raw_text))
            continue
        if isinstance(stmt, RawStmt):
            text = stmt.text.strip()
            lowered = text.lower()
            if not text:
                ctx.stats.blank_or_noise_removed += 1
                _trace(ctx, "ast", "drop-raw", "blank raw stmt")
                continue
            if lowered.startswith("rem "):
                ctx.stats.blank_or_noise_removed += 1
                _trace(ctx, "ast", "drop-raw", "rem comment", before=stmt.text)
                continue
            out.append(stmt)
            continue
        out.append(stmt)
    return out


def used_identifiers_in_expr(expr) -> Set[str]:
    out: Set[str] = set()
    if isinstance(expr, Identifier):
        out.add(expr.name)
    elif isinstance(expr, UnaryOp):
        out |= used_identifiers_in_expr(expr.operand)
    elif isinstance(expr, (BinaryOp, Concat)):
        out |= used_identifiers_in_expr(expr.left)
        out |= used_identifiers_in_expr(expr.right)
    elif isinstance(expr, CallExpr):
        for arg in expr.args:
            out |= used_identifiers_in_expr(arg)
    return out



def _collect_called_names_expr(expr, out: Set[str]) -> None:
    if isinstance(expr, CallExpr):
        out.add(expr.name.lower())
        for arg in expr.args:
            _collect_called_names_expr(arg, out)
    elif isinstance(expr, UnaryOp):
        _collect_called_names_expr(expr.operand, out)
    elif isinstance(expr, (BinaryOp, Concat)):
        _collect_called_names_expr(expr.left, out)
        _collect_called_names_expr(expr.right, out)



def collect_called_names(statements: Sequence[Statement], out: Optional[Set[str]] = None) -> Set[str]:
    names = out if out is not None else set()
    for stmt in statements:
        if isinstance(stmt, ConstStmt):
            _collect_called_names_expr(stmt.expr, names)
        elif isinstance(stmt, Assignment):
            _collect_called_names_expr(stmt.expr, names)
        elif isinstance(stmt, CallStmt):
            names.add(stmt.name.lower())
            for arg in stmt.args:
                _collect_called_names_expr(arg, names)
        elif isinstance(stmt, IfStmt):
            _collect_called_names_expr(stmt.condition, names)
            collect_called_names(stmt.then_body, names)
            collect_called_names(stmt.else_body, names)
        elif isinstance(stmt, (FunctionDecl, SubDecl)):
            collect_called_names(stmt.body, names)
    return names



def prune_unused_declarations(statements: Sequence[Statement]) -> List[Statement]:
    called = collect_called_names(statements)
    out: List[Statement] = []
    for stmt in statements:
        if isinstance(stmt, FunctionDecl):
            if stmt.name.lower() in called:
                out.append(FunctionDecl(name=stmt.name, params=list(stmt.params), body=prune_unused_declarations(stmt.body), raw_text=stmt.raw_text))
            continue
        if isinstance(stmt, SubDecl):
            if stmt.name.lower() in called:
                out.append(SubDecl(name=stmt.name, params=list(stmt.params), body=prune_unused_declarations(stmt.body), raw_text=stmt.raw_text))
            continue
        if isinstance(stmt, IfStmt):
            out.append(IfStmt(condition=stmt.condition, then_body=prune_unused_declarations(stmt.then_body), else_body=prune_unused_declarations(stmt.else_body), raw_text=stmt.raw_text))
            continue
        out.append(stmt)
    return out



def _is_obviously_interesting_assignment(stmt: Assignment | ConstStmt) -> bool:
    return False



def eliminate_shadowed_assignments(
    statements: Sequence[Statement],
    ctx: Context,
) -> tuple[List[Statement], Set[str], Set[str]]:
    live: Set[str] = set()
    assigned_after: Set[str] = set()
    kept_rev: List[Statement] = []
    for stmt in reversed(list(statements)):
        if isinstance(stmt, IfStmt):
            then_body, then_live, then_assigned = eliminate_shadowed_assignments(stmt.then_body, ctx)
            else_body, else_live, else_assigned = eliminate_shadowed_assignments(stmt.else_body, ctx)
            live = live | then_live | else_live | used_identifiers_in_expr(stmt.condition)
            assigned_after |= then_assigned | else_assigned
            kept_rev.append(IfStmt(condition=stmt.condition, then_body=then_body, else_body=else_body, raw_text=stmt.raw_text))
            continue
        if isinstance(stmt, FunctionDecl):
            body, _, _ = eliminate_shadowed_assignments(stmt.body, ctx)
            kept_rev.append(FunctionDecl(name=stmt.name, params=list(stmt.params), body=body, raw_text=stmt.raw_text))
            continue
        if isinstance(stmt, SubDecl):
            body, _, _ = eliminate_shadowed_assignments(stmt.body, ctx)
            kept_rev.append(SubDecl(name=stmt.name, params=list(stmt.params), body=body, raw_text=stmt.raw_text))
            continue
        if isinstance(stmt, CallStmt):
            for arg in stmt.args:
                live |= used_identifiers_in_expr(arg)
            if stmt.receiver:
                for part in stmt.receiver.split('.'):
                    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part):
                        live.add(part)
            kept_rev.append(stmt)
            continue
        if isinstance(stmt, ConstStmt):
            deps = used_identifiers_in_expr(stmt.expr)
            shadowed = stmt.name in assigned_after and stmt.name not in live
            assigned_after.add(stmt.name)
            if shadowed and is_literal(stmt.expr):
                ctx.stats.dead_assign_removed += 1
                _trace(ctx, "dce", "drop-shadowed", stmt.name if isinstance(stmt, ConstStmt) else getattr(stmt, "target", "?"), before=getattr(stmt, "raw_text", ""), after="")
                continue
            live.discard(stmt.name)
            live |= deps
            kept_rev.append(stmt)
            continue
        if isinstance(stmt, Assignment):
            deps = used_identifiers_in_expr(stmt.expr)
            if _is_property_target(stmt.target):
                live |= deps | _receiver_idents(stmt.target)
                kept_rev.append(stmt)
                continue
            shadowed = stmt.target in assigned_after and stmt.target not in live
            assigned_after.add(stmt.target)
            if shadowed and is_literal(stmt.expr):
                ctx.stats.dead_assign_removed += 1
                _trace(ctx, "dce", "drop-shadowed", stmt.target, before=stmt.raw_text)
                continue
            live.discard(stmt.target)
            live |= deps
            kept_rev.append(stmt)
            continue
        kept_rev.append(stmt)
    kept_rev.reverse()
    return kept_rev, live, assigned_after


def eliminate_dead_assignments(
    statements: Sequence[Statement],
    ctx: Context,
    live_after: Optional[Set[str]] = None,
) -> tuple[List[Statement], Set[str]]:
    live = set(live_after or set())
    kept_rev: List[Statement] = []
    for stmt in reversed(list(statements)):
        if isinstance(stmt, IfStmt):
            then_body, live_then = eliminate_dead_assignments(stmt.then_body, ctx, set(live))
            else_body, live_else = eliminate_dead_assignments(stmt.else_body, ctx, set(live))
            cond_live = used_identifiers_in_expr(stmt.condition)
            live = live_then | live_else | cond_live
            kept_rev.append(IfStmt(condition=stmt.condition, then_body=then_body, else_body=else_body, raw_text=stmt.raw_text))
            continue
        if isinstance(stmt, FunctionDecl):
            body, _ = eliminate_dead_assignments(stmt.body, ctx, live_after={stmt.name})
            kept_rev.append(FunctionDecl(name=stmt.name, params=list(stmt.params), body=body, raw_text=stmt.raw_text))
            continue
        if isinstance(stmt, SubDecl):
            body, _ = eliminate_dead_assignments(stmt.body, ctx)
            kept_rev.append(SubDecl(name=stmt.name, params=list(stmt.params), body=body, raw_text=stmt.raw_text))
            continue
        if isinstance(stmt, CallStmt):
            for arg in stmt.args:
                live |= used_identifiers_in_expr(arg)
            if stmt.receiver:
                for part in stmt.receiver.split('.'):
                    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part):
                        live.add(part)
            kept_rev.append(stmt)
            continue
        if isinstance(stmt, ConstStmt):
            deps = used_identifiers_in_expr(stmt.expr)
            if stmt.name in live or _is_obviously_interesting_assignment(stmt):
                live.discard(stmt.name)
                live |= deps
                kept_rev.append(stmt)
            else:
                ctx.stats.dead_assign_removed += 1
                _trace(ctx, "dce", "drop-dead", stmt.name, before=stmt.raw_text)
            continue
        if isinstance(stmt, Assignment):
            deps = used_identifiers_in_expr(stmt.expr)
            if _is_property_target(stmt.target):
                live |= deps | _receiver_idents(stmt.target)
                kept_rev.append(stmt)
                continue
            if stmt.target in live or _is_obviously_interesting_assignment(stmt):
                live.discard(stmt.target)
                live |= deps
                kept_rev.append(stmt)
            else:
                ctx.stats.dead_assign_removed += 1
                _trace(ctx, "dce", "drop-dead", stmt.target, before=stmt.raw_text)
            continue
        kept_rev.append(stmt)
    kept_rev.reverse()
    return kept_rev, live



def optimize_program(prog: Program, *, debug: bool = False) -> Tuple[Program, Context]:
    ctx = Context(debug=debug)
    collect_declarations(prog.statements, ctx)
    _trace(ctx, "pipeline", "stage-start", "optimize_program", before=f"{len(prog.statements)} stmts")
    rewritten = optimize_statements(prog.statements, ctx)
    rewritten, _, _ = eliminate_shadowed_assignments(rewritten, ctx)
    rewritten, _ = eliminate_dead_assignments(rewritten, ctx)
    rewritten = prune_unused_declarations(rewritten)
    _trace(ctx, "pipeline", "stage-end", "optimize_program", after=f"{len(rewritten)} stmts")
    return Program(statements=rewritten, original_text=prog.original_text, extracted_script_text=prog.extracted_script_text), ctx


def expr_to_text(expr) -> str:
    if isinstance(expr, StringLiteral):
        return '"' + expr.value.replace('"', '""') + '"'
    if isinstance(expr, NumberLiteral):
        return str(expr.value)
    if isinstance(expr, BooleanLiteral):
        return "True" if expr.value else "False"
    if isinstance(expr, Identifier):
        return expr.name
    if isinstance(expr, UnaryOp):
        return f"{expr.op} {expr_to_text(expr.operand)}"
    if isinstance(expr, BinaryOp):
        return f"{expr_to_text(expr.left)} {expr.op} {expr_to_text(expr.right)}"
    if isinstance(expr, Concat):
        return f"{expr_to_text(expr.left)} & {expr_to_text(expr.right)}"
    if isinstance(expr, CallExpr):
        return f"{expr.name}(" + ", ".join(expr_to_text(arg) for arg in expr.args) + ")"
    if isinstance(expr, RawExpr):
        return expr.text
    return str(expr)



def _render_statements(statements: Sequence[Statement], lines: List[str], indent: int = 0) -> None:
    pad = "    " * indent
    for stmt in statements:
        if isinstance(stmt, DimStmt):
            lines.append(pad + "Dim " + ", ".join(stmt.names))
        elif isinstance(stmt, ConstStmt):
            lines.append(pad + f"Const {stmt.name} = {expr_to_text(stmt.expr)}")
        elif isinstance(stmt, Assignment):
            prefix = "Set " if stmt.set_kw else ""
            lines.append(pad + f"{prefix}{stmt.target} = {expr_to_text(stmt.expr)}")
        elif isinstance(stmt, CallStmt):
            head = f"{stmt.receiver}.{stmt.name}" if stmt.receiver else stmt.name
            if stmt.args:
                lines.append(pad + head + " " + ", ".join(expr_to_text(arg) for arg in stmt.args))
            else:
                lines.append(pad + head)
        elif isinstance(stmt, IfStmt):
            lines.append(pad + f"If {expr_to_text(stmt.condition)} Then")
            _render_statements(stmt.then_body, lines, indent + 1)
            if stmt.else_body:
                lines.append(pad + "Else")
                _render_statements(stmt.else_body, lines, indent + 1)
            lines.append(pad + "End If")
        elif isinstance(stmt, FunctionDecl):
            sig = f"Function {stmt.name}(" + ", ".join(stmt.params) + ")"
            lines.append(pad + sig)
            _render_statements(stmt.body, lines, indent + 1)
            lines.append(pad + "End Function")
        elif isinstance(stmt, SubDecl):
            sig = f"Sub {stmt.name}(" + ", ".join(stmt.params) + ")"
            lines.append(pad + sig)
            _render_statements(stmt.body, lines, indent + 1)
            lines.append(pad + "End Sub")
        elif isinstance(stmt, RawStmt):
            lines.append(pad + stmt.text)



def render_program(
    prog: Program,
    ctx: Optional[Context] = None,
    *,
    include_recovered_strings: bool = True,
    include_recovered_blobs: bool = True,
) -> str:
    lines: List[str] = []
    _render_statements(prog.statements, lines)
    if ctx and include_recovered_strings and ctx.recovered_strings:
        lines.append("")
        lines.append("' --- recovered strings ---")
        for value in sorted(ctx.recovered_strings):
            lines.append("' " + value)
    if ctx and include_recovered_blobs and ctx.blobs:
        lines.append("")
        lines.append("' --- recovered blobs ---")
        for index, blob in enumerate(ctx.blobs, 1):
            lines.append(f"' blob {index} from {blob.source_var} [{blob.kind}]")
            for blob_line in blob.decoded_text.splitlines():
                lines.append("' " + blob_line)
    return "\n".join(lines).rstrip() + ("\n" if lines else "")
