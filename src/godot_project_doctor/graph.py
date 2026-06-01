"""Dependency graph builder and renderers for Godot projects."""

from __future__ import annotations

import re
from collections import defaultdict

from godot_project_doctor.models import ProjectIndex

# source_file (relative path) -> list of referenced res:// paths
Graph = dict[str, list[str]]

# Binary extensions that must never appear in refs (belt-and-suspenders guard)
_BINARY_EXTS = frozenset([".res", ".scn"])


def build_graph(index: ProjectIndex) -> Graph:
    """Build a directed dependency graph from a ProjectIndex.

    Edges: source_file -> referenced_resource_path

    Binary .res / .scn files are excluded even if somehow present in the index.
    Only text-format resources (.tscn / .tres) produce source nodes, which is
    already guaranteed by the indexer — this guard is a safety net.
    """
    graph: Graph = defaultdict(list)

    for ref in index.refs:
        # Skip any ref whose path points at a binary resource
        suffix = "." + ref.path.rstrip("/").rsplit(".", 1)[-1].lower() if "." in ref.path else ""
        if suffix in _BINARY_EXTS:
            continue
        graph[ref.source_file].append(ref.path)

    return dict(graph)


# ---------------------------------------------------------------------------
# Stable Mermaid node-ID helper
# ---------------------------------------------------------------------------

def _node_id(path: str) -> str:
    """Return a stable, Mermaid-safe identifier derived from *path*.

    Replaces every character that is not ASCII alphanumeric with ``_``,
    then ensures the result starts with a letter so it is always valid.
    """
    sanitised = re.sub(r"[^A-Za-z0-9]", "_", path)
    # Mermaid node IDs must start with a letter
    if sanitised and sanitised[0].isdigit():
        sanitised = "n" + sanitised
    return sanitised or "node"


# ---------------------------------------------------------------------------
# Text renderer
# ---------------------------------------------------------------------------

def render_text_graph(graph: Graph) -> str:
    """Render *graph* as a human-readable indented list.

    Each source file is printed on its own line, followed by its
    referenced resources indented with ``  -> ``.
    Returns ``"(no dependencies found)\\n"`` when the graph is empty.
    """
    if not graph:
        return "(no dependencies found)\n"

    lines: list[str] = []
    for source in sorted(graph):
        lines.append(source)
        for dep in sorted(graph[source]):
            lines.append(f"  -> {dep}")
        lines.append("")

    # Strip trailing blank line then add a single newline
    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# Mermaid renderer
# ---------------------------------------------------------------------------

def render_mermaid_graph(graph: Graph) -> str:
    """Render *graph* as a Mermaid ``flowchart TD`` diagram.

    Node labels show the last path segment (filename) for readability;
    node identifiers are the full sanitised path so they are unique and stable.
    """
    lines: list[str] = ["flowchart TD"]

    if not graph:
        lines.append('    _empty["(no dependencies found)"]')
        return "\n".join(lines) + "\n"

    # Collect every unique path (sources + targets)
    all_paths: set[str] = set()
    for source, deps in graph.items():
        all_paths.add(source)
        all_paths.update(deps)

    # Emit node definitions  id["label"]
    node_ids: dict[str, str] = {}
    for path in sorted(all_paths):
        nid = _node_id(path)
        node_ids[path] = nid
        label = path.replace("\\", "/").rsplit("/", 1)[-1]
        lines.append(f'    {nid}["{label}"]')

    lines.append("")

    # Emit edges
    for source in sorted(graph):
        src_id = node_ids[source]
        for dep in sorted(graph[source]):
            lines.append(f"    {src_id} --> {node_ids[dep]}")

    return "\n".join(lines) + "\n"
