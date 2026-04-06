from __future__ import annotations

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
    StringLiteral,
    SubDecl,
    UnaryOp,
)


def _indent(level: int) -> str:
    return "  " * level


def _render_expr(expr, level: int = 0) -> list[str]:
    pad = _indent(level)
    if isinstance(expr, StringLiteral):
        return [f"{pad}String({expr.value!r})"]
    if isinstance(expr, NumberLiteral):
        return [f"{pad}Number({expr.value})"]
    if isinstance(expr, BooleanLiteral):
        return [f"{pad}Boolean({expr.value})"]
    if isinstance(expr, Identifier):
        return [f"{pad}Identifier({expr.name})"]
    if isinstance(expr, UnaryOp):
        lines = [f"{pad}UnaryOp({expr.op})"]
        lines.extend(_render_expr(expr.operand, level + 1))
        return lines
    if isinstance(expr, BinaryOp):
        lines = [f"{pad}BinaryOp({expr.op})"]
        lines.extend(_render_expr(expr.left, level + 1))
        lines.extend(_render_expr(expr.right, level + 1))
        return lines
    if isinstance(expr, Concat):
        lines = [f"{pad}Concat"]
        lines.extend(_render_expr(expr.left, level + 1))
        lines.extend(_render_expr(expr.right, level + 1))
        return lines
    if isinstance(expr, CallExpr):
        lines = [f"{pad}CallExpr({expr.name})"]
        for arg in expr.args:
            lines.extend(_render_expr(arg, level + 1))
        return lines
    if isinstance(expr, RawExpr):
        return [f"{pad}RawExpr({expr.text})"]
    return [f"{pad}{type(expr).__name__}({expr})"]


def _render_stmt(stmt, level: int = 0) -> list[str]:
    pad = _indent(level)
    if isinstance(stmt, DimStmt):
        return [f"{pad}DimStmt({', '.join(stmt.names)})"]
    if isinstance(stmt, ConstStmt):
        lines = [f"{pad}ConstStmt({stmt.name})"]
        lines.extend(_render_expr(stmt.expr, level + 1))
        return lines
    if isinstance(stmt, Assignment):
        lines = [f"{pad}Assignment(target={stmt.target}, set_kw={stmt.set_kw})"]
        lines.extend(_render_expr(stmt.expr, level + 1))
        return lines
    if isinstance(stmt, CallStmt):
        target = f"{stmt.receiver}.{stmt.name}" if stmt.receiver else stmt.name
        lines = [f"{pad}CallStmt({target})"]
        for arg in stmt.args:
            lines.extend(_render_expr(arg, level + 1))
        return lines
    if isinstance(stmt, IfStmt):
        lines = [f"{pad}IfStmt", f"{pad}  Condition:"]
        lines.extend(_render_expr(stmt.condition, level + 2))
        lines.append(f"{pad}  Then:")
        for inner in stmt.then_body:
            lines.extend(_render_stmt(inner, level + 2))
        if stmt.else_body:
            lines.append(f"{pad}  Else:")
            for inner in stmt.else_body:
                lines.extend(_render_stmt(inner, level + 2))
        return lines
    if isinstance(stmt, FunctionDecl):
        lines = [f"{pad}FunctionDecl({stmt.name}({', '.join(stmt.params)}))"]
        for inner in stmt.body:
            lines.extend(_render_stmt(inner, level + 1))
        return lines
    if isinstance(stmt, SubDecl):
        lines = [f"{pad}SubDecl({stmt.name}({', '.join(stmt.params)}))"]
        for inner in stmt.body:
            lines.extend(_render_stmt(inner, level + 1))
        return lines
    if isinstance(stmt, RawStmt):
        return [f"{pad}RawStmt({stmt.text})"]
    return [f"{pad}{type(stmt).__name__}({stmt})"]


def render_ast(program: Program) -> str:
    lines: list[str] = ["Program"]
    for stmt in program.statements:
        lines.extend(_render_stmt(stmt, 1))
    return "\n".join(lines) + "\n"
