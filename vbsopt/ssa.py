from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .ir import CBranch, Const, IRProgram, Instr, Jump, Operand, Stop, Var, is_temp_name, render_ir


@dataclass(slots=True)
class SSAResult:
    ir: IRProgram
    phi_nodes: int
    input_nodes: int
    constants: Dict[str, object] = field(default_factory=dict)


class Unknown:
    def __repr__(self) -> str:
        return "Unknown"


class Overdefined:
    def __repr__(self) -> str:
        return "Overdefined"


UNKNOWN = Unknown()
OVERDEFINED = Overdefined()


def _clone_ir(ir: IRProgram) -> IRProgram:
    new_blocks = {}
    for name, block in ir.blocks.items():
        new_blocks[name] = type(block)(
            name=block.name,
            instrs=[
                Instr(
                    op=instr.op,
                    dest=instr.dest,
                    args=list(instr.args),
                    callee=instr.callee,
                    incomings=list(instr.incomings),
                    text=instr.text,
                )
                for instr in block.instrs
            ],
            terminator=block.terminator,
            preds=list(block.preds),
            succs=list(block.succs),
        )
    return IRProgram(entry=ir.entry, blocks=new_blocks)


def _all_blocks(ir: IRProgram) -> List[str]:
    return list(ir.blocks.keys())


def _compute_dominators(ir: IRProgram) -> Dict[str, Set[str]]:
    blocks = _all_blocks(ir)
    dom: Dict[str, Set[str]] = {}
    all_blocks = set(blocks)
    for name in blocks:
        dom[name] = {name} if name == ir.entry else set(all_blocks)
    changed = True
    while changed:
        changed = False
        for name in blocks:
            if name == ir.entry:
                continue
            preds = ir.blocks[name].preds
            if not preds:
                new = {name}
            else:
                new = {name} | set.intersection(*(dom[pred] for pred in preds))
            if new != dom[name]:
                dom[name] = new
                changed = True
    return dom


def _compute_idom(dom: Dict[str, Set[str]], entry: str) -> Dict[str, Optional[str]]:
    idom: Dict[str, Optional[str]] = {entry: None}
    for block, doms in dom.items():
        if block == entry:
            continue
        candidates = doms - {block}
        immediate = None
        for candidate in candidates:
            if all(candidate == other or candidate not in dom[other] for other in candidates):
                immediate = candidate
                break
        idom[block] = immediate
    return idom


def _compute_dom_tree(idom: Dict[str, Optional[str]]) -> Dict[str, List[str]]:
    tree: Dict[str, List[str]] = {name: [] for name in idom}
    for block, parent in idom.items():
        if parent is not None:
            tree[parent].append(block)
    return tree


def _compute_dom_frontier(ir: IRProgram, idom: Dict[str, Optional[str]]) -> Dict[str, Set[str]]:
    frontier: Dict[str, Set[str]] = {name: set() for name in ir.blocks}
    for block_name, block in ir.blocks.items():
        if len(block.preds) < 2:
            continue
        for pred in block.preds:
            runner = pred
            while runner is not None and runner != idom[block_name]:
                frontier[runner].add(block_name)
                runner = idom[runner]
    return frontier


def _def_blocks(ir: IRProgram) -> Dict[str, Set[str]]:
    defs: Dict[str, Set[str]] = {}
    for block_name, block in ir.blocks.items():
        for instr in block.instrs:
            if instr.dest and not is_temp_name(instr.dest):
                defs.setdefault(instr.dest, set()).add(block_name)
    return defs


def _insert_phi_nodes(ir: IRProgram, frontier: Dict[str, Set[str]], defs: Dict[str, Set[str]]) -> int:
    inserted = 0
    for var_name, origin_blocks in defs.items():
        work = list(origin_blocks)
        has_phi: Set[str] = set()
        while work:
            block_name = work.pop()
            for frontier_block in frontier[block_name]:
                if frontier_block in has_phi:
                    continue
                phi = Instr(op="phi", dest=var_name, incomings=[])
                ir.blocks[frontier_block].instrs.insert(0, phi)
                has_phi.add(frontier_block)
                inserted += 1
                if frontier_block not in defs[var_name]:
                    defs[var_name].add(frontier_block)
                    work.append(frontier_block)
    return inserted


def _operand_name(operand: Operand) -> Optional[str]:
    return operand.name if isinstance(operand, Var) else None


def _rename_operand(operand: Operand, stacks: Dict[str, List[str]], inputs: Set[str]) -> Operand:
    if not isinstance(operand, Var):
        return operand
    name = operand.name
    if is_temp_name(name) or "." in name:
        return operand
    if not stacks.get(name):
        synthetic = f"{name}.0"
        stacks.setdefault(name, []).append(synthetic)
        inputs.add(name)
    return Var(stacks[name][-1])


def _rename_ir(ir: IRProgram, dom_tree: Dict[str, List[str]]) -> tuple[IRProgram, int]:
    counters: Dict[str, int] = {}
    stacks: Dict[str, List[str]] = {}
    inputs: Set[str] = set()

    def fresh(base: str) -> str:
        counters[base] = counters.get(base, 0) + 1
        return f"{base}.{counters[base]}"

    def rename_block(block_name: str) -> None:
        block = ir.blocks[block_name]
        pushed: List[str] = []

        for instr in block.instrs:
            if instr.op == "phi" and instr.dest:
                base = instr.dest
                new_name = fresh(base)
                stacks.setdefault(base, []).append(new_name)
                instr.dest = new_name
                pushed.append(base)

        for instr in block.instrs:
            if instr.op == "phi":
                continue
            instr.args = [_rename_operand(arg, stacks, inputs) for arg in instr.args]
            if instr.op == "effectcall":
                pass
            if instr.dest:
                base = instr.dest
                if is_temp_name(base):
                    continue
                new_name = fresh(base)
                stacks.setdefault(base, []).append(new_name)
                instr.dest = new_name
                pushed.append(base)

        term = block.terminator
        if isinstance(term, CBranch):
            term.cond = _rename_operand(term.cond, stacks, inputs)

        for succ_name in block.succs:
            succ = ir.blocks[succ_name]
            for instr in succ.instrs:
                if instr.op != "phi" or instr.dest is None:
                    continue
                base = instr.dest.split(".", 1)[0]
                incoming = _rename_operand(Var(base), stacks, inputs)
                instr.incomings.append((block_name, incoming))

        for child in dom_tree.get(block_name, []):
            rename_block(child)

        for base in reversed(pushed):
            stacks[base].pop()

    rename_block(ir.entry)

    if inputs:
        entry_block = ir.blocks[ir.entry]
        prefix: List[Instr] = []
        for name in sorted(inputs):
            prefix.append(Instr(op="input", dest=f"{name}.0"))
        entry_block.instrs = prefix + entry_block.instrs
    return ir, len(inputs)


def to_ssa(ir: IRProgram) -> SSAResult:
    ssa_ir = _clone_ir(ir)
    dom = _compute_dominators(ssa_ir)
    idom = _compute_idom(dom, ssa_ir.entry)
    dom_tree = _compute_dom_tree(idom)
    frontier = _compute_dom_frontier(ssa_ir, idom)
    defs = _def_blocks(ssa_ir)
    phi_nodes = _insert_phi_nodes(ssa_ir, frontier, defs)
    ssa_ir, input_nodes = _rename_ir(ssa_ir, dom_tree)
    constants = evaluate_ssa_constants(ssa_ir)
    return SSAResult(ir=ssa_ir, phi_nodes=phi_nodes, input_nodes=input_nodes, constants=constants)


def _merge_values(values: Sequence[object]) -> object:
    known = [value for value in values if value is not UNKNOWN]
    if not values:
        return UNKNOWN
    if any(value is OVERDEFINED for value in values):
        return OVERDEFINED
    if not known:
        return UNKNOWN
    first = known[0]
    if all(value == first for value in known) and len(known) == len(values):
        return first
    if all(value == first for value in known):
        return UNKNOWN
    return OVERDEFINED


def _value_of(operand: Operand, constants: Dict[str, object]) -> object:
    if isinstance(operand, Const):
        return operand.value
    return constants.get(operand.name, UNKNOWN)


def _parse_int_like(value: object) -> int | None:
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


def _eval_known_call(callee: str, args: Sequence[object]) -> object:
    lower = callee.lower()
    try:
        if lower in {"chr", "chrw"} and len(args) == 1:
            value = _parse_int_like(args[0])
            if value is not None:
                return chr(value)
        if lower.endswith("replace") and len(args) == 3 and all(isinstance(arg, str) for arg in args):
            return args[0].replace(args[1], args[2])
        if lower == "left" and len(args) == 2 and isinstance(args[0], str):
            value = _parse_int_like(args[1])
            if value is not None:
                return args[0][: value]
        if lower == "right" and len(args) == 2 and isinstance(args[0], str):
            value = _parse_int_like(args[1])
            if value is not None:
                return args[0][-value :] if value else ""
        if lower == "mid" and len(args) >= 2 and isinstance(args[0], str):
            start = _parse_int_like(args[1])
            if start is not None:
                start = max(start - 1, 0)
                if len(args) >= 3:
                    count = _parse_int_like(args[2])
                    if count is not None:
                        return args[0][start : start + count]
                return args[0][start:]
        if lower == "array":
            return list(args)
        if lower == "split" and len(args) >= 1 and isinstance(args[0], str):
            sep = " "
            if len(args) > 1 and isinstance(args[1], str):
                sep = args[1]
            return args[0].split(sep)
        if lower == "join" and len(args) >= 1 and isinstance(args[0], list):
            sep = " "
            if len(args) > 1 and isinstance(args[1], str):
                sep = args[1]
            if all(isinstance(item, str) for item in args[0]):
                return sep.join(args[0])
        if lower == "lcase" and len(args) == 1 and isinstance(args[0], str):
            return args[0].lower()
        if lower == "ucase" and len(args) == 1 and isinstance(args[0], str):
            return args[0].upper()
        if lower == "trim" and len(args) == 1 and isinstance(args[0], str):
            return args[0].strip()
        if lower == "ltrim" and len(args) == 1 and isinstance(args[0], str):
            return args[0].lstrip()
        if lower == "rtrim" and len(args) == 1 and isinstance(args[0], str):
            return args[0].rstrip()
        if lower == "strreverse" and len(args) == 1 and isinstance(args[0], str):
            return args[0][::-1]
        if lower == "cstr" and len(args) == 1:
            return str(args[0])
        if lower in {"clng", "cint"} and len(args) == 1:
            value = _parse_int_like(args[0])
            if value is not None:
                return value
        if lower == "hex" and len(args) == 1:
            value = _parse_int_like(args[0])
            if value is not None:
                return format(value, "X")
        if lower in {"asc", "ascw"} and len(args) == 1 and isinstance(args[0], str) and args[0]:
            return ord(args[0][0])
        if lower == "len" and len(args) == 1 and isinstance(args[0], (str, list)):
            return len(args[0])
        if lower == "xorhex" and len(args) == 2 and isinstance(args[0], str):
            key = _parse_int_like(args[1])
            if key is not None:
                raw = bytes.fromhex(args[0])
                decoded = bytes(b ^ (key & 0xFF) for b in raw)
                try:
                    return decoded.decode("utf-8")
                except UnicodeDecodeError:
                    return decoded.decode("latin-1")
        if lower in {"striptoken", "striptokens", "cleanbase64"} and args and isinstance(args[0], str):
            value = args[0]
            for token in ("??", "***"):
                value = value.replace(token, "")
            return value
    except Exception:
        return OVERDEFINED
    return OVERDEFINED


def evaluate_ssa_constants(ir: IRProgram) -> Dict[str, object]:
    constants: Dict[str, object] = {}
    changed = True
    while changed:
        changed = False
        for block in ir.blocks.values():
            for instr in block.instrs:
                if not instr.dest:
                    continue
                current = constants.get(instr.dest, UNKNOWN)
                new_value = UNKNOWN
                if instr.op == "input":
                    new_value = UNKNOWN
                elif instr.op == "phi":
                    values = [_value_of(value, constants) for _, value in instr.incomings]
                    new_value = _merge_values(values)
                elif instr.op == "copy":
                    new_value = _value_of(instr.args[0], constants)
                elif instr.op == "concat":
                    left = _value_of(instr.args[0], constants)
                    right = _value_of(instr.args[1], constants)
                    if left is UNKNOWN or right is UNKNOWN:
                        new_value = UNKNOWN
                    elif left is OVERDEFINED or right is OVERDEFINED:
                        new_value = OVERDEFINED
                    else:
                        new_value = str(left) + str(right)
                elif instr.op == "add":
                    left = _value_of(instr.args[0], constants)
                    right = _value_of(instr.args[1], constants)
                    if left is UNKNOWN or right is UNKNOWN:
                        new_value = UNKNOWN
                    elif left is OVERDEFINED or right is OVERDEFINED:
                        new_value = OVERDEFINED
                    elif isinstance(left, int) and isinstance(right, int):
                        new_value = left + right
                    else:
                        new_value = str(left) + str(right)
                elif instr.op.startswith("cmp_"):
                    left = _value_of(instr.args[0], constants)
                    right = _value_of(instr.args[1], constants)
                    if left is UNKNOWN or right is UNKNOWN:
                        new_value = UNKNOWN
                    elif left is OVERDEFINED or right is OVERDEFINED:
                        new_value = OVERDEFINED
                    else:
                        if instr.op == "cmp_eq":
                            new_value = left == right
                        elif instr.op == "cmp_ne":
                            new_value = left != right
                        elif instr.op == "cmp_lt":
                            new_value = left < right
                        elif instr.op == "cmp_gt":
                            new_value = left > right
                        elif instr.op == "cmp_le":
                            new_value = left <= right
                        elif instr.op == "cmp_ge":
                            new_value = left >= right
                elif instr.op == "bool_and":
                    left = _value_of(instr.args[0], constants)
                    right = _value_of(instr.args[1], constants)
                    if left is UNKNOWN or right is UNKNOWN:
                        new_value = UNKNOWN
                    elif left is OVERDEFINED or right is OVERDEFINED:
                        new_value = OVERDEFINED
                    elif isinstance(left, bool) and isinstance(right, bool):
                        new_value = left and right
                elif instr.op == "bool_or":
                    left = _value_of(instr.args[0], constants)
                    right = _value_of(instr.args[1], constants)
                    if left is UNKNOWN or right is UNKNOWN:
                        new_value = UNKNOWN
                    elif left is OVERDEFINED or right is OVERDEFINED:
                        new_value = OVERDEFINED
                    elif isinstance(left, bool) and isinstance(right, bool):
                        new_value = left or right
                elif instr.op == "bool_xor":
                    left = _value_of(instr.args[0], constants)
                    right = _value_of(instr.args[1], constants)
                    if left is UNKNOWN or right is UNKNOWN:
                        new_value = UNKNOWN
                    elif left is OVERDEFINED or right is OVERDEFINED:
                        new_value = OVERDEFINED
                    elif isinstance(left, bool) and isinstance(right, bool):
                        new_value = bool(left) ^ bool(right)
                    elif isinstance(left, int) and isinstance(right, int):
                        new_value = left ^ right
                elif instr.op == "unary_not":
                    value = _value_of(instr.args[0], constants)
                    if value is UNKNOWN:
                        new_value = UNKNOWN
                    elif value is OVERDEFINED:
                        new_value = OVERDEFINED
                    elif isinstance(value, bool):
                        new_value = not value
                elif instr.op == "unary_-":
                    value = _value_of(instr.args[0], constants)
                    if value is UNKNOWN:
                        new_value = UNKNOWN
                    elif value is OVERDEFINED:
                        new_value = OVERDEFINED
                    elif isinstance(value, int):
                        new_value = -value
                elif instr.op == "call":
                    values = [_value_of(arg, constants) for arg in instr.args]
                    if any(value is OVERDEFINED for value in values):
                        new_value = OVERDEFINED
                    elif any(value is UNKNOWN for value in values):
                        new_value = UNKNOWN
                    else:
                        new_value = _eval_known_call(instr.callee or "", values)
                elif instr.op == "raw":
                    new_value = OVERDEFINED
                else:
                    new_value = OVERDEFINED

                if new_value != current:
                    constants[instr.dest] = new_value
                    changed = True
    return constants


def render_ssa(ssa: SSAResult) -> str:
    return render_ir(ssa.ir)
