"""Dependency graph builder and renderers for Godot projects."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from godot_project_doctor.models import ProjectIndex

# source_file (relative path) -> list of referenced res:// paths (deduped)
Graph = dict[str, list[str]]

# Binary extensions that must never appear in refs (belt-and-suspenders guard)
_BINARY_EXTS = frozenset([".res", ".scn"])


def build_graph(index: ProjectIndex) -> Graph:
    """Build a directed dependency graph from a ProjectIndex.

    Edges: source_file -> referenced_resource_path

    Binary .res / .scn files are excluded even if somehow present in the index.
    Duplicate edges (same source → same target) are removed while preserving
    insertion order.
    """
    graph: dict[str, dict[str, None]] = defaultdict(dict)  # ordered set via dict keys

    for ref in index.refs:
        suffix = "." + ref.path.rstrip("/").rsplit(".", 1)[-1].lower() if "." in ref.path else ""
        if suffix in _BINARY_EXTS:
            continue
        graph[ref.source_file][ref.path] = None  # dedup via dict key

    return {src: list(deps.keys()) for src, deps in graph.items()}


# ---------------------------------------------------------------------------
# Mermaid node-ID helper
# ---------------------------------------------------------------------------


def _node_id(path: str) -> str:
    """Return a stable, unique, Mermaid-safe node identifier for *path*.

    Format: ``{slug}_{hash6}`` where

    * *slug* is the filename's non-alphanumeric chars replaced by ``_``
    * *hash6* is the first 6 hex digits of the MD5 of the full path,
      which prevents collisions when different paths share the same filename
      (e.g. ``res://foo-bar.tres`` vs ``res://foo/bar.tres``).

    The identifier always starts with a letter (Mermaid requirement).
    """
    norm = path.replace("\\", "/")
    # Slug from the last path segment (filename)
    name = norm.rsplit("/", 1)[-1]
    slug = re.sub(r"[^A-Za-z0-9]", "_", name)
    # Short hash over the *full* path to guarantee uniqueness
    h6 = hashlib.md5(norm.encode()).hexdigest()[:6]
    nid = f"{slug}_{h6}" if slug else f"node_{h6}"
    # Must start with a letter
    if nid[0].isdigit():
        nid = "n" + nid
    return nid


def _mermaid_label(path: str) -> str:
    """Return a Mermaid-safe label for *path*.

    Shows only the last path segment.  Replaces ``"`` with ``'`` so the label
    can be safely embedded inside Mermaid's ``["…"]`` syntax.
    """
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return name.replace('"', "'")


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

    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# Mermaid renderer
# ---------------------------------------------------------------------------


def render_mermaid_graph(graph: Graph) -> str:
    """Render *graph* as a Mermaid ``flowchart TD`` diagram.

    * Node IDs are ``{slug}_{md5[:6]}`` — unique even when different paths
      share the same filename.
    * Node labels show only the filename; ``"`` is replaced with ``'``.
    * Duplicate edges are suppressed.
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

    # Emit node definitions
    node_ids: dict[str, str] = {}
    for path in sorted(all_paths):
        nid = _node_id(path)
        node_ids[path] = nid
        label = _mermaid_label(path)
        lines.append(f'    {nid}["{label}"]')

    lines.append("")

    # Emit edges, deduplicating across (src_id, dep_id) pairs
    seen_edges: set[tuple[str, str]] = set()
    for source in sorted(graph):
        src_id = node_ids[source]
        for dep in sorted(graph[source]):
            dep_id = node_ids[dep]
            edge = (src_id, dep_id)
            if edge not in seen_edges:
                seen_edges.add(edge)
                lines.append(f"    {src_id} --> {dep_id}")

    return "\n".join(lines) + "\n"
