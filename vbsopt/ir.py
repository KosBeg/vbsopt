from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Union

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


@dataclass(frozen=True, slots=True)
class Var:
    name: str


@dataclass(frozen=True, slots=True)
class Const:
    value: object


Operand = Union[Var, Const]


@dataclass(slots=True)
class Instr:
    op: str
    dest: Optional[str] = None
    args: List[Operand] = field(default_factory=list)
    callee: Optional[str] = None
    incomings: List[tuple[str, Operand]] = field(default_factory=list)
    text: str = ""


@dataclass(slots=True)
class Jump:
    target: str


@dataclass(slots=True)
class CBranch:
    cond: Operand
    true_target: str
    false_target: str


@dataclass(slots=True)
class Stop:
    pass


Terminator = Union[Jump, CBranch, Stop]


@dataclass(slots=True)
class BasicBlock:
    name: str
    instrs: List[Instr] = field(default_factory=list)
    terminator: Optional[Terminator] = None
    preds: List[str] = field(default_factory=list)
    succs: List[str] = field(default_factory=list)


@dataclass(slots=True)
class IRProgram:
    entry: str
    blocks: Dict[str, BasicBlock]


class IRBuilder:
    def __init__(self):
        self.blocks: Dict[str, BasicBlock] = {}
        self.temp_counter = 0
        self.block_counter = 0
        self.entry = self.new_block("entry")

    def new_block(self, prefix: str = "block") -> str:
        name = f"{prefix}_{self.block_counter}"
        self.block_counter += 1
        self.blocks[name] = BasicBlock(name=name)
        return name

    def emit(self, block_name: str, instr: Instr) -> None:
        self.blocks[block_name].instrs.append(instr)

    def set_terminator(self, block_name: str, term: Terminator) -> None:
        self.blocks[block_name].terminator = term

    def new_temp(self) -> str:
        name = f"t{self.temp_counter}"
        self.temp_counter += 1
        return name



def is_temp_name(name: str) -> bool:
    return name.startswith("t") and name[1:].isdigit()



def lower_expr(expr, builder: IRBuilder, block_name: str) -> Operand:
    if isinstance(expr, StringLiteral):
        return Const(expr.value)
    if isinstance(expr, NumberLiteral):
        return Const(expr.value)
    if isinstance(expr, BooleanLiteral):
        return Const(expr.value)
    if isinstance(expr, Identifier):
        return Var(expr.name)
    if isinstance(expr, UnaryOp):
        operand = lower_expr(expr.operand, builder, block_name)
        dest = builder.new_temp()
        builder.emit(block_name, Instr(op=f"unary_{expr.op}", dest=dest, args=[operand]))
        return Var(dest)
    if isinstance(expr, Concat):
        left = lower_expr(expr.left, builder, block_name)
        right = lower_expr(expr.right, builder, block_name)
        dest = builder.new_temp()
        builder.emit(block_name, Instr(op="concat", dest=dest, args=[left, right]))
        return Var(dest)
    if isinstance(expr, BinaryOp):
        left = lower_expr(expr.left, builder, block_name)
        right = lower_expr(expr.right, builder, block_name)
        dest = builder.new_temp()
        op = {
            "+": "add",
            "=": "cmp_eq",
            "<>": "cmp_ne",
            "<": "cmp_lt",
            ">": "cmp_gt",
            "<=": "cmp_le",
            ">=": "cmp_ge",
            "and": "bool_and",
            "or": "bool_or",
            "xor": "bool_xor",
        }.get(expr.op, "raw")
        builder.emit(block_name, Instr(op=op, dest=dest, args=[left, right], text=expr.op))
        return Var(dest)
    if isinstance(expr, CallExpr):
        args = [lower_expr(arg, builder, block_name) for arg in expr.args]
        dest = builder.new_temp()
        builder.emit(block_name, Instr(op="call", dest=dest, args=args, callee=expr.name))
        return Var(dest)
    if isinstance(expr, RawExpr):
        dest = builder.new_temp()
        builder.emit(block_name, Instr(op="raw", dest=dest, args=[Const(expr.text)], text=expr.text))
        return Var(dest)
    dest = builder.new_temp()
    builder.emit(block_name, Instr(op="raw", dest=dest, args=[Const(str(expr))], text=str(expr)))
    return Var(dest)



def _render_decl_as_text(stmt: FunctionDecl | SubDecl) -> str:
    keyword = "Function" if isinstance(stmt, FunctionDecl) else "Sub"
    return f"{keyword} {stmt.name}(...)"



def lower_statement(stmt: Statement, builder: IRBuilder, block_name: str) -> str:
    if isinstance(stmt, DimStmt):
        builder.emit(block_name, Instr(op="decl", args=[Const(name) for name in stmt.names]))
        return block_name
    if isinstance(stmt, ConstStmt):
        value = lower_expr(stmt.expr, builder, block_name)
        builder.emit(block_name, Instr(op="copy", dest=stmt.name, args=[value], text="const"))
        return block_name
    if isinstance(stmt, Assignment):
        value = lower_expr(stmt.expr, builder, block_name)
        builder.emit(block_name, Instr(op="copy", dest=stmt.target, args=[value], text="set" if stmt.set_kw else ""))
        return block_name
    if isinstance(stmt, CallStmt):
        args = [lower_expr(arg, builder, block_name) for arg in stmt.args]
        callee = f"{stmt.receiver}.{stmt.name}" if stmt.receiver else stmt.name
        builder.emit(block_name, Instr(op="effectcall", args=args, callee=callee))
        return block_name
    if isinstance(stmt, RawStmt):
        builder.emit(block_name, Instr(op="rawstmt", args=[Const(stmt.text)], text=stmt.text))
        return block_name
    if isinstance(stmt, (FunctionDecl, SubDecl)):
        builder.emit(block_name, Instr(op="rawstmt", args=[Const(_render_decl_as_text(stmt))], text=_render_decl_as_text(stmt)))
        return block_name
    if isinstance(stmt, IfStmt):
        cond = lower_expr(stmt.condition, builder, block_name)
        then_block = builder.new_block("then")
        else_block = builder.new_block("else")
        join_block = builder.new_block("join")
        builder.set_terminator(block_name, CBranch(cond=cond, true_target=then_block, false_target=else_block))
        end_then = lower_statements(stmt.then_body, builder, then_block)
        if builder.blocks[end_then].terminator is None:
            builder.set_terminator(end_then, Jump(join_block))
        end_else = lower_statements(stmt.else_body, builder, else_block)
        if builder.blocks[end_else].terminator is None:
            builder.set_terminator(end_else, Jump(join_block))
        return join_block
    builder.emit(block_name, Instr(op="rawstmt", args=[Const(str(stmt))], text=str(stmt)))
    return block_name



def lower_statements(statements: Sequence[Statement], builder: IRBuilder, block_name: str) -> str:
    current = block_name
    for stmt in statements:
        current = lower_statement(stmt, builder, current)
    return current



def _populate_edges(ir: IRProgram) -> None:
    for block in ir.blocks.values():
        block.preds.clear()
        block.succs.clear()
    for name, block in ir.blocks.items():
        term = block.terminator
        succs: List[str] = []
        if isinstance(term, Jump):
            succs.append(term.target)
        elif isinstance(term, CBranch):
            succs.extend([term.true_target, term.false_target])
        block.succs = succs
        for succ in succs:
            ir.blocks[succ].preds.append(name)



def lower_program(prog: Program) -> IRProgram:
    builder = IRBuilder()
    end_block = lower_statements(prog.statements, builder, builder.entry)
    if builder.blocks[end_block].terminator is None:
        builder.set_terminator(end_block, Stop())
    ir = IRProgram(entry=builder.entry, blocks=builder.blocks)
    _populate_edges(ir)
    return ir



def _format_operand(operand: Operand) -> str:
    if isinstance(operand, Var):
        return operand.name
    if isinstance(operand.value, str):
        return repr(operand.value)
    return str(operand.value)



def render_ir(ir: IRProgram) -> str:
    lines: List[str] = []
    for block_name in ir.blocks:
        block = ir.blocks[block_name]
        lines.append(f"{block.name}:")
        if block.preds:
            lines.append(f"  ; preds: {', '.join(block.preds)}")
        for instr in block.instrs:
            if instr.op == "phi":
                incoming = ", ".join(f"[{pred}: {_format_operand(value)}]" for pred, value in instr.incomings)
                lines.append(f"  {instr.dest} = phi {incoming}")
                continue
            head = f"{instr.dest} = " if instr.dest is not None else ""
            if instr.op in {"call", "effectcall"}:
                callee = instr.callee or "<unknown>"
                args = ", ".join(_format_operand(arg) for arg in instr.args)
                lines.append(f"  {head}{instr.op} {callee}({args})")
                continue
            if instr.op == "decl":
                args = ", ".join(_format_operand(arg) for arg in instr.args)
                lines.append(f"  decl {args}")
                continue
            args = ", ".join(_format_operand(arg) for arg in instr.args)
            lines.append(f"  {head}{instr.op} {args}".rstrip())
        term = block.terminator
        if isinstance(term, Jump):
            lines.append(f"  jump {term.target}")
        elif isinstance(term, CBranch):
            lines.append(f"  cbranch {_format_operand(term.cond)} ? {term.true_target} : {term.false_target}")
        else:
            lines.append("  stop")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
