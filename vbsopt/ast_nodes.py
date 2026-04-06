from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence


class Node:
    pass


class Expr(Node):
    pass


@dataclass(slots=True)
class StringLiteral(Expr):
    value: str


@dataclass(slots=True)
class NumberLiteral(Expr):
    value: int


@dataclass(slots=True)
class BooleanLiteral(Expr):
    value: bool


@dataclass(slots=True)
class Identifier(Expr):
    name: str


@dataclass(slots=True)
class UnaryOp(Expr):
    op: str
    operand: Expr


@dataclass(slots=True)
class BinaryOp(Expr):
    op: str
    left: Expr
    right: Expr


@dataclass(slots=True)
class Concat(Expr):
    left: Expr
    right: Expr


@dataclass(slots=True)
class CallExpr(Expr):
    name: str
    args: List[Expr]


@dataclass(slots=True)
class RawExpr(Expr):
    text: str


class Statement(Node):
    pass


@dataclass(slots=True)
class DimStmt(Statement):
    names: List[str]
    raw_text: str = ""


@dataclass(slots=True)
class ConstStmt(Statement):
    name: str
    expr: Expr
    raw_text: str = ""


@dataclass(slots=True)
class Assignment(Statement):
    target: str
    expr: Expr
    set_kw: bool = False
    raw_text: str = ""


@dataclass(slots=True)
class CallStmt(Statement):
    receiver: Optional[str]
    name: str
    args: List[Expr]
    raw_text: str = ""


@dataclass(slots=True)
class IfStmt(Statement):
    condition: Expr
    then_body: List[Statement] = field(default_factory=list)
    else_body: List[Statement] = field(default_factory=list)
    raw_text: str = ""


@dataclass(slots=True)
class FunctionDecl(Statement):
    name: str
    params: List[str] = field(default_factory=list)
    body: List[Statement] = field(default_factory=list)
    raw_text: str = ""


@dataclass(slots=True)
class SubDecl(Statement):
    name: str
    params: List[str] = field(default_factory=list)
    body: List[Statement] = field(default_factory=list)
    raw_text: str = ""


@dataclass(slots=True)
class RawStmt(Statement):
    text: str


@dataclass(slots=True)
class Program(Node):
    statements: List[Statement] = field(default_factory=list)
    original_text: str = ""
    extracted_script_text: str = ""


StatementList = Sequence[Statement]
