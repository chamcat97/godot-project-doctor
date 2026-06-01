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


def _to_res_path(path: str) -> str:
    """Normalise a project-relative or ``res://``-prefixed path to ``res://`` form.

    Graph source nodes use project-relative paths (e.g. ``scenes/Main.tscn``);
    target nodes use ``res://`` paths (e.g. ``res://scenes/Main.tscn``).
    Normalising both to ``res://`` form lets cycle-detection compare them.
    """
    p = path.replace("\\", "/")
    if p.startswith("res://"):
        return p
    return f"res://{p}"


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
# Cycle detection
# ---------------------------------------------------------------------------


def find_cycles(graph: Graph) -> list[list[str]]:
    """Return all simple cycles in *graph* as lists of ``res://`` paths.

    Uses iterative DFS with a recursion-stack set to avoid Python stack
    overflows on deep graphs.  Each cycle is represented as the ordered
    sequence of nodes from the first repeated node back to itself
    (the repeated node appears only once, at position 0).

    Cycles that consist of the same nodes in the same rotation are
    deduplicated: only the canonical rotation (starting with the
    lexicographically smallest node) is kept.

    Parameters
    ----------
    graph:
        The dependency graph returned by :func:`build_graph`.  Source keys
        are project-relative paths; target values are ``res://`` paths.
        Both are normalised to ``res://`` form internally.

    Returns
    -------
    list[list[str]]
        Sorted list of unique cycles.  Each inner list is a sequence of
        ``res://`` paths forming a cycle.  Empty when the graph is acyclic.
    """
    # Build adjacency using uniform res:// keys
    adj: dict[str, list[str]] = {}
    for src, targets in graph.items():
        src_key = _to_res_path(src)
        if src_key not in adj:
            adj[src_key] = []
        for tgt in targets:
            tgt_key = _to_res_path(tgt)
            adj[src_key].append(tgt_key)
            # Ensure target node exists in adj even without outgoing edges
            if tgt_key not in adj:
                adj[tgt_key] = []

    found_cycles: list[list[str]] = []
    seen_canonical: set[tuple[str, ...]] = set()
    visited: set[str] = set()

    for start in sorted(adj):
        if start in visited:
            continue

        # Iterative DFS: stack items are (node, iterator-over-neighbours, path-so-far)
        path: list[str] = []
        path_set: set[str] = set()
        stack: list[tuple[str, int]] = [(start, 0)]

        while stack:
            node, idx = stack[-1]

            if idx == 0:
                # First visit to this node in this DFS branch
                if node in visited and node not in path_set:
                    stack.pop()
                    continue
                path.append(node)
                path_set.add(node)

            neighbours = adj.get(node, [])
            advanced = False
            while idx < len(neighbours):
                nb = neighbours[idx]
                idx += 1
                stack[-1] = (node, idx)

                if nb in path_set:
                    # Found a cycle: extract it from the current path
                    cycle_start = path.index(nb)
                    cycle = path[cycle_start:]
                    # Canonical form: rotate to start at lex-smallest node
                    min_pos = cycle.index(min(cycle))
                    canonical = tuple(cycle[min_pos:] + cycle[:min_pos])
                    if canonical not in seen_canonical:
                        seen_canonical.add(canonical)
                        found_cycles.append(list(canonical))
                elif nb not in visited:
                    stack.append((nb, 0))
                    advanced = True
                    break

            if not advanced:
                stack.pop()
                if path and path[-1] == node:
                    path.pop()
                    path_set.discard(node)
                visited.add(node)

    return sorted(found_cycles)


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
