from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

from .ir import BasicBlock, Const, CBranch, IRProgram, Instr, Jump, Operand, Stop, Var, is_temp_name

PURE_CALLS = {
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
PURE_OPS = {
    "copy",
    "concat",
    "add",
    "cmp_eq",
    "cmp_ne",
    "cmp_lt",
    "cmp_gt",
    "cmp_le",
    "cmp_ge",
    "bool_and",
    "bool_or",
    "bool_xor",
    "unary_not",
    "unary_-",
    "raw",
}


@dataclass(slots=True)
class IROptStats:
    cse_eliminated: int = 0
    dead_temps_removed: int = 0



def _clone_operand(operand: Operand) -> Operand:
    if isinstance(operand, Var):
        return Var(operand.name)
    return Const(operand.value)



def _clone_instr(instr: Instr) -> Instr:
    return Instr(
        op=instr.op,
        dest=instr.dest,
        args=[_clone_operand(arg) for arg in instr.args],
        callee=instr.callee,
        incomings=[(pred, _clone_operand(val)) for pred, val in instr.incomings],
        text=instr.text,
    )



def _clone_ir(ir: IRProgram) -> IRProgram:
    blocks: Dict[str, BasicBlock] = {}
    for name, block in ir.blocks.items():
        blocks[name] = BasicBlock(
            name=block.name,
            instrs=[_clone_instr(instr) for instr in block.instrs],
            terminator=Jump(block.terminator.target) if isinstance(block.terminator, Jump)
            else CBranch(_clone_operand(block.terminator.cond), block.terminator.true_target, block.terminator.false_target) if isinstance(block.terminator, CBranch)
            else Stop(),
            preds=list(block.preds),
            succs=list(block.succs),
        )
    return IRProgram(entry=ir.entry, blocks=blocks)



def _resolve_alias(operand: Operand, aliases: Dict[str, Operand]) -> Operand:
    if not isinstance(operand, Var):
        return operand
    current: Operand = operand
    seen: set[str] = set()
    while isinstance(current, Var) and current.name in aliases and current.name not in seen:
        seen.add(current.name)
        current = aliases[current.name]
    return current



def _pure_instruction(instr: Instr) -> bool:
    if instr.op in PURE_OPS:
        return True
    return instr.op == "call" and (instr.callee or "").lower() in PURE_CALLS



def _operand_key(operand: Operand, versions: Dict[str, int]) -> tuple:
    if isinstance(operand, Const):
        value = operand.value
        if isinstance(value, list):
            value = tuple(value)
        return ("const", value)
    return ("var", operand.name, versions.get(operand.name, 0))



def _rewrite_uses(ir: IRProgram, old_name: str, replacement: Operand) -> None:
    for block in ir.blocks.values():
        for instr in block.instrs:
            instr.args = [replacement if isinstance(arg, Var) and arg.name == old_name else arg for arg in instr.args]
            instr.incomings = [
                (pred, replacement if isinstance(val, Var) and val.name == old_name else val)
                for pred, val in instr.incomings
            ]
        term = block.terminator
        if isinstance(term, CBranch) and isinstance(term.cond, Var) and term.cond.name == old_name:
            term.cond = replacement if isinstance(replacement, Var) else Const(replacement.value)



def _block_cse(block: BasicBlock, stats: IROptStats) -> None:
    aliases: Dict[str, Operand] = {}
    seen: Dict[tuple, str] = {}
    versions: Dict[str, int] = {}
    rewritten: List[Instr] = []

    def bump(dest: str | None) -> None:
        if not dest:
            return
        versions[dest] = versions.get(dest, 0) + 1
        aliases.pop(dest, None)

    for instr in block.instrs:
        current = _clone_instr(instr)
        current.args = [_resolve_alias(arg, aliases) for arg in current.args]
        if current.op == "phi":
            current.incomings = [(pred, _resolve_alias(val, aliases)) for pred, val in current.incomings]
            bump(current.dest)
            rewritten.append(current)
            continue

        if _pure_instruction(current) and current.dest:
            key = (
                current.op,
                current.callee or "",
                tuple(_operand_key(arg, versions) for arg in current.args),
                current.text,
            )
            previous = seen.get(key)
            if previous is not None:
                replacement = Var(previous)
                if is_temp_name(current.dest):
                    aliases[current.dest] = replacement
                    stats.cse_eliminated += 1
                    continue
                current = Instr(op="copy", dest=current.dest, args=[replacement], text="cse")
                stats.cse_eliminated += 1
            else:
                seen[key] = current.dest

        rewritten.append(current)
        bump(current.dest)

    block.instrs = rewritten



def _compute_temp_uses(ir: IRProgram) -> Dict[str, int]:
    uses: Dict[str, int] = {}

    def add_operand(operand: Operand) -> None:
        if isinstance(operand, Var) and is_temp_name(operand.name):
            uses[operand.name] = uses.get(operand.name, 0) + 1

    for block in ir.blocks.values():
        for instr in block.instrs:
            for arg in instr.args:
                add_operand(arg)
            for _, val in instr.incomings:
                add_operand(val)
        term = block.terminator
        if isinstance(term, CBranch):
            add_operand(term.cond)
    return uses



def _remove_dead_temps(ir: IRProgram, stats: IROptStats) -> None:
    changed = True
    while changed:
        changed = False
        uses = _compute_temp_uses(ir)
        for block in ir.blocks.values():
            new_instrs: List[Instr] = []
            for instr in block.instrs:
                if instr.dest and is_temp_name(instr.dest) and _pure_instruction(instr) and uses.get(instr.dest, 0) == 0:
                    stats.dead_temps_removed += 1
                    changed = True
                    continue
                new_instrs.append(instr)
            block.instrs = new_instrs



def optimize_ir(ir: IRProgram) -> tuple[IRProgram, IROptStats]:
    out = _clone_ir(ir)
    stats = IROptStats()
    for block in out.blocks.values():
        _block_cse(block, stats)
    _remove_dead_temps(out, stats)
    return out, stats
