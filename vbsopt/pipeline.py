from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .ast_render import render_ast
from .ir import lower_program, render_ir
from .ir_opt import optimize_ir
from .parser import parse_program
from .passes import Blob, extract_indicators, optimize_program, render_program, render_trace
from .ssa import render_ssa, to_ssa


@dataclass(slots=True)
class BlobAnalysis:
    source_var: str
    kind: str
    decoded_text: str
    normalized_text: str = ""
    ir_text: str = ""
    ssa_text: str = ""
    indicators: List[str] = field(default_factory=list)


@dataclass(slots=True)
class PipelineResult:
    normalized_script: str
    deobfuscated_script: str
    ast_text: str
    ir_text: str
    ssa_text: str
    indicators_before: set[str]
    indicators_after: set[str]
    stats: Dict[str, Any]
    debug_text: str = ""
    blob_analyses: List[BlobAnalysis] = field(default_factory=list)


MAX_BLOB_DEPTH = 1
MAX_BLOBS_PER_SCRIPT = 4
MAX_BLOB_CHARS = 12000



def _analyze_blob(blob: Blob, depth: int, *, debug: bool = False) -> Optional[BlobAnalysis]:
    if depth > MAX_BLOB_DEPTH or len(blob.decoded_text) > MAX_BLOB_CHARS:
        return None
    inner = run_pipeline(blob.decoded_text, analyze_blobs=False, depth=depth, debug=debug)
    return BlobAnalysis(
        source_var=blob.source_var,
        kind=blob.kind,
        decoded_text=blob.decoded_text,
        normalized_text=inner.normalized_script,
        ir_text=inner.ir_text,
        ssa_text=inner.ssa_text,
        indicators=sorted(inner.indicators_after),
    )



def run_pipeline(text: str, *, analyze_blobs: bool = True, depth: int = 0, debug: bool = False) -> PipelineResult:
    prog = parse_program(text)
    before_script = prog.extracted_script_text
    indicators_before = extract_indicators(before_script)

    opt_prog, ctx = optimize_program(prog, debug=debug)
    ast_text = render_ast(opt_prog)
    raw_ir = lower_program(opt_prog)
    ir, ir_opt_stats = optimize_ir(raw_ir)
    ssa = to_ssa(ir)

    normalized = render_program(opt_prog, ctx)
    deobfuscated = render_program(opt_prog, ctx, include_recovered_strings=False, include_recovered_blobs=False)
    ir_text = render_ir(ir)
    ssa_text = render_ssa(ssa)
    indicators_after = extract_indicators(normalized)

    ctx.stats.lowered_ir_instructions = sum(len(block.instrs) for block in ir.blocks.values())
    ctx.stats.ssa_blocks = len(ssa.ir.blocks)
    ctx.stats.ssa_phi_nodes = ssa.phi_nodes
    ctx.stats.ir_cse_eliminated = ir_opt_stats.cse_eliminated
    ctx.stats.ir_dead_temps_removed = ir_opt_stats.dead_temps_removed
    ctx.stats.indicators_exposed = max(0, len(indicators_after) - len(indicators_before))

    blob_analyses: List[BlobAnalysis] = []
    if analyze_blobs and depth < MAX_BLOB_DEPTH:
        for blob in ctx.blobs[:MAX_BLOBS_PER_SCRIPT]:
            analysis = _analyze_blob(blob, depth + 1, debug=debug)
            if analysis is not None:
                blob_analyses.append(analysis)
        ctx.stats.recursive_blob_analyses = len(blob_analyses)

    return PipelineResult(
        normalized_script=normalized,
        deobfuscated_script=deobfuscated,
        ast_text=ast_text,
        ir_text=ir_text,
        ssa_text=ssa_text,
        indicators_before=indicators_before,
        indicators_after=indicators_after,
        stats=asdict(ctx.stats),
        debug_text=render_trace(ctx.trace),
        blob_analyses=blob_analyses,
    )
