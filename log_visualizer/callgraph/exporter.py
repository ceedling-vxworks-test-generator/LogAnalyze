"""CallGraph の出力（JSON / Graphviz DOT）。"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

from ..domain.models import UNRESOLVED_FUNCTION_POINTER, CallEdge, CallGraph, CallKind

EDGE_STYLE = {
    CallKind.SYNC: "solid",
    CallKind.ASYNC: "dotted",
    CallKind.CALLBACK: "dashed",
}
EDGE_COLOR = {
    CallKind.SYNC: "#334155",
    CallKind.ASYNC: "#0f766e",
    CallKind.CALLBACK: "#b45309",
}


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class CallGraphExporter:
    """callgraph.json / callgraph_detail.json / callgraph.dot を出力する。"""

    def write(self, graph: CallGraph, out_dir: Path) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        simple = out_dir / "callgraph.json"
        detail = out_dir / "callgraph_detail.json"
        dot = out_dir / "callgraph.dot"
        simple.write_text(
            json.dumps(graph.to_adjacency(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        detail.write_text(
            json.dumps(self.detail(graph), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        dot.write_text(self.to_dot(graph), encoding="utf-8")
        return [simple, detail, dot]

    @staticmethod
    def detail(graph: CallGraph) -> dict[str, Any]:
        return {
            "functions": {
                fid: {
                    "name": f.name,
                    "file": f.file_path,
                    "start_line": f.start_line,
                    "end_line": f.end_line,
                    "static": f.is_static,
                    "module": f.module,
                }
                for fid, f in sorted(graph.functions.items())
            },
            "edges": [
                {
                    "caller": e.caller,
                    "callee": e.callee,
                    "kind": e.kind.value,
                    "line": e.line,
                    **({"via": e.via} if e.via else {}),
                }
                for e in sorted(graph.edges(), key=lambda e: (e.caller, e.callee))
            ],
        }

    @staticmethod
    def to_dot(
        graph: CallGraph,
        edges: Optional[Iterable[CallEdge]] = None,
        highlight: Optional[str] = None,
    ) -> str:
        selected = list(graph.edges() if edges is None else edges)
        nodes: set[str] = set()
        for e in selected:
            nodes.add(e.caller)
            nodes.add(e.callee)
        if highlight:
            nodes.add(highlight)
        clusters: dict[str, list[str]] = defaultdict(list)
        loose: list[str] = []
        for node in sorted(nodes):
            info = graph.functions.get(node)
            if info is None:
                loose.append(node)
            else:
                clusters[info.module].append(node)

        lines = [
            "digraph callgraph {",
            "  rankdir=LR;",
            '  graph [fontname="Helvetica", fontsize=10, bgcolor="transparent"];',
            '  node [shape=box, style="rounded,filled", fillcolor="#f8fafc", '
            'fontname="Helvetica", fontsize=10];',
            '  edge [fontname="Helvetica", fontsize=8];',
        ]
        for idx, (module, members) in enumerate(sorted(clusters.items())):
            lines.append(f"  subgraph cluster_{idx} {{")
            lines.append(f"    label={_quote(module)}; style=dashed; color=\"#94a3b8\";")
            for node in members:
                info = graph.functions[node]
                attrs = f"label={_quote(info.name)}, tooltip={_quote(node)}"
                if node == highlight:
                    attrs += ', fillcolor="#fde68a"'
                lines.append(f"    {_quote(node)} [{attrs}];")
            lines.append("  }")
        for node in loose:
            if node == UNRESOLVED_FUNCTION_POINTER:
                lines.append(
                    f'  {_quote(node)} [label="UNRESOLVED\\nFUNCTION_POINTER", '
                    'shape=octagon, fillcolor="#fee2e2"];'
                )
            else:
                lines.append(f"  {_quote(node)};")
        for e in selected:
            attrs = f'style={EDGE_STYLE[e.kind]}, color="{EDGE_COLOR[e.kind]}"'
            if e.via:
                attrs += f", tooltip={_quote(e.via)}"
            lines.append(f"  {_quote(e.caller)} -> {_quote(e.callee)} [{attrs}];")
        lines.append("}")
        return "\n".join(lines) + "\n"
