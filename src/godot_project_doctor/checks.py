"""Audit checks for a Godot project index."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from godot_project_doctor.graph import build_graph, find_cycles
from godot_project_doctor.indexer import resolve_ref_path
from godot_project_doctor.models import Issue, ProjectIndex, Severity

if TYPE_CHECKING:
    from godot_project_doctor.config import Config

# Godot 4 built-in Input Map actions all use the "ui_" prefix.
# We skip them to avoid false positives from default engine actions.
_GODOT_BUILTIN_ACTION_PREFIX = "ui_"

# ── Scene-parsing regexes (BROKEN_SIGNAL_CONNECTION) ─────────────────────────
_SC_EXT_RES_RE = re.compile(r"\[ext_resource\b([^\]]*)\]")
_SC_NODE_RE = re.compile(r"\[node\b([^\]]*)\]")
_SC_CONN_RE = re.compile(r"\[connection\b([^\]]*)\]")
_SC_ATTR_RE = re.compile(r'\b(\w+)="([^"]*)"')
# Matches both quoted ("1_abc") and unquoted (1) ExtResource IDs
_SC_SCRIPT_PROP_RE = re.compile(r'^script\s*=\s*ExtResource\(\s*"?([^"\)\s]+)"?\s*\)')

# ─── Constants ────────────────────────────────────────────────────────────────

LARGE_TEXTURE_DIM = 2048  # pixels
LARGE_AUDIO_BYTES = 10 * 1024 * 1024  # 10 MB

# ─── Check runners ────────────────────────────────────────────────────────────


def run_all_checks(index: ProjectIndex, config: Config | None = None) -> list[Issue]:
    """Run every check and return a combined issue list.

    Parameters
    ----------
    index:
        Populated project index.
    config:
        Optional :class:`~godot_project_doctor.config.Config` instance.
        When provided, ``large_texture_dim`` and ``large_audio_bytes``
        thresholds are taken from the config; otherwise the module-level
        constants are used.
    """
    issues: list[Issue] = []
    project_root = Path(index.project_root)

    issues.extend(_check_missing_export_presets(index))
    issues.extend(_check_project_godot_integrity(index, project_root))
    issues.extend(_check_circular_dependencies(index))
    issues.extend(_check_missing_external_resources(index, project_root))
    issues.extend(_check_large_textures(index, project_root, config))
    issues.extend(_check_large_audio(index, project_root, config))
    issues.extend(_check_unused_asset_candidates(index, project_root))
    issues.extend(_check_undefined_input_actions(index))
    issues.extend(_check_broken_signal_connections(index, project_root))

    return issues


# ─── Individual checks ────────────────────────────────────────────────────────


def _check_project_godot_integrity(index: ProjectIndex, project_root: Path) -> list[Issue]:
    """Check that configured main scene and autoload paths actually exist.

    Rules
    -----
    * **Main scene not configured** → INFO (``NO_MAIN_SCENE``).
      A missing ``run/main_scene`` is valid for library-style projects.
    * **Main scene path does not exist** → ERROR (``MISSING_MAIN_SCENE``).
    * **Autoload path does not exist** → ERROR (``MISSING_AUTOLOAD``).

    ``uid://`` paths cannot be resolved without the Godot import cache and are
    skipped silently to avoid false positives.
    """
    issues: list[Issue] = []
    summary = index.summary

    # ── main scene ────────────────────────────────────────────────────────────
    if not summary.main_scene:
        issues.append(
            Issue(
                code="NO_MAIN_SCENE",
                severity=Severity.INFO,
                message="No main scene configured in project.godot.",
                file="project.godot",
                details=(
                    "Set run/main_scene in project.godot if this is a runnable game. "
                    "Library-only projects may intentionally omit a main scene."
                ),
            )
        )
    else:
        main_path = summary.main_scene
        if main_path.startswith("uid://"):
            pass  # uid:// resolution requires the Godot import cache — skip
        else:
            real = (
                project_root / main_path[len("res://") :]
                if main_path.startswith("res://")
                else project_root / main_path
            )
            if not real.exists():
                issues.append(
                    Issue(
                        code="MISSING_MAIN_SCENE",
                        severity=Severity.ERROR,
                        message=f"Main scene does not exist: {main_path}",
                        file="project.godot",
                        details=f"Expected at: {real}",
                    )
                )

    # ── autoloads ─────────────────────────────────────────────────────────────
    for name, path in sorted(summary.autoloads.items()):
        if path.startswith("uid://"):
            continue  # uid:// — cannot resolve statically
        real = (
            project_root / path[len("res://") :]
            if path.startswith("res://")
            else project_root / path
        )
        if not real.exists():
            issues.append(
                Issue(
                    code="MISSING_AUTOLOAD",
                    severity=Severity.ERROR,
                    message=f"Autoload '{name}' path does not exist: {path}",
                    file="project.godot",
                    details=f"Expected at: {real}",
                )
            )

    return issues


def _check_circular_dependencies(index: ProjectIndex) -> list[Issue]:
    """Detect cycles in the resource dependency graph.

    A cycle such as ``SceneA.tscn → Enemy.tscn → SceneA.tscn`` can cause
    loading deadlocks or infinite recursion at runtime.

    Each unique cycle is reported as a single ``CIRCULAR_DEPENDENCY`` ERROR.
    The ``details`` field lists the cycle path in order.  Duplicate rotations
    of the same cycle are suppressed.
    """
    graph = build_graph(index)
    cycles = find_cycles(graph)
    if not cycles:
        return []

    issues: list[Issue] = []
    for cycle in cycles:
        # Build a readable arrow chain: A → B → C → A
        chain = " -> ".join(cycle) + f" -> {cycle[0]}"
        issues.append(
            Issue(
                code="CIRCULAR_DEPENDENCY",
                severity=Severity.ERROR,
                message=f"Circular dependency detected ({len(cycle)} node(s)): {cycle[0]}",
                file=None,
                details=f"Cycle: {chain}",
            )
        )
    return issues


def _check_missing_export_presets(index: ProjectIndex) -> list[Issue]:
    if not index.has_export_presets:
        return [
            Issue(
                code="NO_EXPORT_PRESETS",
                severity=Severity.INFO,
                message="export_presets.cfg not found. No export configuration is present.",
                file=None,
                details="Create export presets in the Godot editor if you intend to export the project.",
            )
        ]
    return []


def _check_missing_external_resources(index: ProjectIndex, project_root: Path) -> list[Issue]:
    issues: list[Issue] = []
    for ref in index.refs:
        # Use pre-resolved path when available (uid:// resolved via sidecar/import/cache)
        effective_path = ref.resolved_path if ref.resolved_path else ref.path
        real_path = resolve_ref_path(effective_path, project_root, ref.source_file, index.uid_map)
        if real_path is None:
            continue  # uid:// not in map — skip to avoid false positives
        if not real_path.exists():
            issues.append(
                Issue(
                    code="MISSING_EXT_RESOURCE",
                    severity=Severity.ERROR,
                    message=f"External resource not found: {ref.path}",
                    file=ref.source_file,
                    details=(
                        f"Referenced in [{ref.source_file}] as type={ref.ref_type!r} "
                        f"id={ref.ref_id!r}. "
                        f"Expected at: {real_path}"
                    ),
                )
            )
    return issues


def _check_large_textures(
    index: ProjectIndex, project_root: Path, config: Config | None = None
) -> list[Issue]:
    """Warn on raster images wider or taller than the configured pixel threshold."""
    try:
        from PIL import Image
    except ImportError:
        return []

    dim = config.large_texture_dim if config is not None else LARGE_TEXTURE_DIM
    # Only raster formats; skip SVG
    raster_exts = {".png", ".jpg", ".jpeg", ".webp"}
    issues: list[Issue] = []

    for rel in index.images:
        if Path(rel).suffix.lower() not in raster_exts:
            continue
        abs_path = project_root / rel
        try:
            with Image.open(abs_path) as img:
                w, h = img.size
        except Exception:
            # PIL raises a wide variety of exceptions for corrupt or
            # unsupported image files; skip them all rather than crashing.
            continue

        if w > dim or h > dim:
            issues.append(
                Issue(
                    code="LARGE_TEXTURE",
                    severity=Severity.WARNING,
                    message=f"Large texture ({w}x{h}): {rel}",
                    file=rel,
                    details=(
                        f"Image dimensions {w}x{h} exceed the {dim}px threshold. "
                        "Consider downscaling or using mipmaps to reduce GPU memory usage."
                    ),
                )
            )
    return issues


def _check_large_audio(
    index: ProjectIndex, project_root: Path, config: Config | None = None
) -> list[Issue]:
    """Warn on audio files larger than the configured byte threshold."""
    limit = config.large_audio_bytes if config is not None else LARGE_AUDIO_BYTES
    issues: list[Issue] = []
    for rel in index.audio:
        abs_path = project_root / rel
        try:
            size = abs_path.stat().st_size
        except OSError:
            continue
        if size > limit:
            size_mb = size / (1024 * 1024)
            issues.append(
                Issue(
                    code="LARGE_AUDIO",
                    severity=Severity.WARNING,
                    message=f"Large audio file ({size_mb:.1f} MB): {rel}",
                    file=rel,
                    details=(
                        f"File size {size_mb:.1f} MB exceeds the "
                        f"{limit / (1024 * 1024):.0f} MB threshold. "
                        "Consider compressing or streaming this asset."
                    ),
                )
            )
    return issues


def _normalize_posix(path: str) -> str:
    """Normalize a POSIX path string by resolving ``.`` and ``..`` components.

    ``PurePosixPath`` does *not* collapse ``..``, so ``"scenes/../assets/x.png"``
    would remain un-normalised and fail set-membership tests.  This helper
    applies the same logic as ``os.path.normpath`` but stays platform-neutral
    (always uses ``/`` separators).

    Examples
    --------
    >>> _normalize_posix("scenes/../assets/bg.png")
    'assets/bg.png'
    >>> _normalize_posix("a/b/./c")
    'a/b/c'
    """
    parts: list[str] = []
    for component in path.replace("\\", "/").split("/"):
        if component == "..":
            if parts:
                parts.pop()
        elif component and component != ".":
            parts.append(component)
    return "/".join(parts)


def _ref_to_canonical_rel(
    ref_path: str,
    source_file: str,
    uid_map: dict[str, str] | None = None,
) -> str | None:
    """Return the project-root-relative canonical path for a resource reference.

    Returns ``None`` for ``uid://`` paths that cannot be resolved statically.
    Uses the same rules as :func:`~godot_project_doctor.indexer.resolve_ref_path`:

    * ``res://foo/bar.png`` → ``"foo/bar.png"``
    * ``../assets/bg.png`` declared in ``scenes/Main.tscn``
      → ``"assets/bg.png"``  (``..`` is resolved — no ``PurePosixPath`` leftover)
    * ``uid://abc123`` with a matching entry in *uid_map*
      → the canonical relative path of the mapped resource
    """
    if ref_path.startswith("uid://"):
        if uid_map:
            resolved = uid_map.get(ref_path)
            if resolved and resolved.startswith("res://"):
                return resolved[len("res://") :]
        return None
    if ref_path.startswith("res://"):
        return ref_path[len("res://") :]
    # Relative path: join with the declaring file's directory, then normalise.
    # PurePosixPath does NOT resolve ".."; we use _normalize_posix() instead.
    source_dir = PurePosixPath(source_file.replace("\\", "/")).parent
    raw = str(source_dir / ref_path)
    return _normalize_posix(raw)


def _check_undefined_input_actions(index: ProjectIndex) -> list[Issue]:
    """Warn when GDScript references an input action not declared in project.godot.

    Only static string literals passed to ``Input.is_action_*()``,
    ``Input.get_action_*()``, ``Input.action_press()``, and
    ``Input.action_release()`` are checked; expressions or variables are skipped.

    Godot's built-in actions (all prefixed with ``ui_``) are excluded from the
    check to prevent false positives from default engine bindings.
    """
    declared = index.summary.input_actions
    if not index.input_action_refs:
        return []

    # Group by action name: {name: [source_file, ...]}
    by_action: dict[str, list[str]] = defaultdict(list)
    for action_name, source_file in index.input_action_refs:
        by_action[action_name].append(source_file)

    issues: list[Issue] = []
    for action_name in sorted(by_action):
        if action_name.startswith(_GODOT_BUILTIN_ACTION_PREFIX):
            continue
        if action_name in declared:
            continue
        files = sorted(set(by_action[action_name]))
        file_list = ", ".join(f"'{f}'" for f in files[:3])
        if len(files) > 3:
            file_list += f" … (+{len(files) - 3} more)"
        issues.append(
            Issue(
                code="UNDEFINED_INPUT_ACTION",
                severity=Severity.WARNING,
                message=f"Input action '{action_name}' used in GDScript but not declared in project.godot",
                file=files[0],
                details=(
                    f"Referenced in {len(files)} file(s): {file_list}. "
                    "Add the action in the Godot editor Input Map settings, "
                    "or remove the reference if the action is obsolete."
                ),
            )
        )
    return issues


def _parse_scene_for_connections(
    text: str,
    uid_map: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, str], list[dict[str, str]]]:
    """Parse .tscn text and return scene structure needed for connection checking.

    Returns
    -------
    id_to_path:
        ``{ref_id: res_path}`` for Script-type ext_resources only.
    node_scripts:
        ``{node_path: ref_id}`` for nodes with an attached script.
        Root node path is ``""``.
    connections:
        List of ``{signal, from, to, method}`` dicts.
    """
    id_to_path: dict[str, str] = {}
    node_scripts: dict[str, str] = {}
    connections: list[dict[str, str]] = []
    current_node_path: str | None = None

    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue  # blank lines do not end a node block

        if s.startswith("["):
            # ext_resource header
            m = _SC_EXT_RES_RE.match(s)
            if m:
                attrs = dict(_SC_ATTR_RE.findall(m.group(1)))
                rid = attrs.get("id", "")
                rtype = attrs.get("type", "")
                rpath = attrs.get("path", "")
                if rid and rtype in ("Script", "GDScript", "CSharpScript") and rpath:
                    # Resolve uid:// via uid_map when available
                    if rpath.startswith("uid://") and uid_map:
                        rpath = uid_map.get(rpath, rpath)
                    id_to_path[rid] = rpath
                current_node_path = None
                continue

            # node header
            m = _SC_NODE_RE.match(s)
            if m:
                attrs = dict(_SC_ATTR_RE.findall(m.group(1)))
                name = attrs.get("name", "")
                parent = attrs.get("parent")
                if parent is None:
                    current_node_path = ""  # root node
                elif parent == ".":
                    current_node_path = name
                else:
                    current_node_path = f"{parent}/{name}"
                continue

            # connection header
            m = _SC_CONN_RE.match(s)
            if m:
                attrs = dict(_SC_ATTR_RE.findall(m.group(1)))
                if "to" in attrs and "method" in attrs:
                    connections.append(
                        {
                            "signal": attrs.get("signal", ""),
                            "from": attrs.get("from", "."),
                            "to": attrs["to"],
                            "method": attrs["method"],
                        }
                    )
                current_node_path = None
                continue

            # any other header ends the current node block
            current_node_path = None
            continue

        # Property line inside a node block
        if current_node_path is not None:
            m = _SC_SCRIPT_PROP_RE.match(s)
            if m:
                node_scripts[current_node_path] = m.group(1)

    return id_to_path, node_scripts, connections


def _check_broken_signal_connections(index: ProjectIndex, project_root: Path) -> list[Issue]:
    """Warn when a signal connection targets a method not found in the script.

    False-positive guards
    ---------------------
    * Target node has no attached script → skip.
    * Script file is unreadable / does not exist → skip (let MISSING_EXT_RESOURCE
      report that separately).
    * ``to`` node path not in the scene's node map → skip.
    * The ``method`` *is* inherited from a base class — static analysis cannot
      trace the inheritance chain, so inherited methods are a known false-negative.
      Users can suppress with ``severity.BROKEN_SIGNAL_CONNECTION = "none"`` in
      config.
    """
    issues: list[Issue] = []

    for scene_rel in index.scenes:
        abs_path = project_root / scene_rel
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        id_to_path, node_scripts, connections = _parse_scene_for_connections(
            text, uid_map=index.uid_map
        )
        if not connections:
            continue

        for conn in connections:
            to_raw = conn["to"]
            method = conn["method"]
            if not method:
                continue

            # "." in Godot NodePath means the scene root (path = "")
            to_path = "" if to_raw == "." else to_raw

            script_ref_id = node_scripts.get(to_path)
            if script_ref_id is None:
                continue  # node has no script — skip

            script_res_path = id_to_path.get(script_ref_id)
            if not script_res_path or not script_res_path.startswith("res://"):
                continue  # not a GDScript path — skip

            script_abs = project_root / script_res_path[len("res://") :]
            try:
                script_text = script_abs.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue  # unreadable — let MISSING_EXT_RESOURCE handle it

            # Search for func definition (any indentation, optional static keyword)
            func_pattern = re.compile(
                r"(?m)^\s*(?:static\s+)?func\s+" + re.escape(method) + r"\s*\("
            )
            if func_pattern.search(script_text):
                continue  # method present — OK

            issues.append(
                Issue(
                    code="BROKEN_SIGNAL_CONNECTION",
                    severity=Severity.WARNING,
                    message=(
                        f"Signal '{conn['signal']}' connection targets missing method "
                        f"'{method}' (not found in '{script_res_path}')"
                    ),
                    file=scene_rel,
                    details=(
                        f"Connection: signal='{conn['signal']}' from='{conn['from']}' "
                        f"to='{to_raw}' method='{method}'. "
                        f"Script: {script_res_path}. "
                        "If '{method}' is inherited from a base class, suppress this "
                        'warning with `severity.BROKEN_SIGNAL_CONNECTION = "none"` '
                        "in your .gdoctor.toml."
                    ),
                )
            )

    return issues


def _check_unused_asset_candidates(index: ProjectIndex, project_root: Path) -> list[Issue]:
    """Flag asset files that are not referenced by any parsed ref.

    These are *candidates* — GDScript can load assets dynamically, so
    this check produces false positives for runtime-loaded assets.
    The same canonical-path rules used in the missing-reference check
    are applied here so that both checks agree on what is "referenced".
    """
    referenced_rel: set[str] = set()
    for ref in index.refs:
        # Prefer resolved_path (uid already decoded) over raw path
        effective_path = ref.resolved_path if ref.resolved_path else ref.path
        canonical = _ref_to_canonical_rel(effective_path, ref.source_file, index.uid_map)
        if canonical is not None:
            # Normalise path separators for cross-platform comparison
            referenced_rel.add(canonical.replace("\\", "/"))

    asset_files = list(index.images) + list(index.audio)
    issues: list[Issue] = []

    for rel in asset_files:
        rel_normalised = rel.replace("\\", "/")
        if rel_normalised not in referenced_rel:
            issues.append(
                Issue(
                    code="UNUSED_ASSET_CANDIDATE",
                    severity=Severity.WARNING,
                    message=f"Asset not referenced by any parsed scene or resource: {rel}",
                    file=rel,
                    details=(
                        "This file was not found in any ext_resource declaration or "
                        "static load()/preload() call. "
                        "It may be loaded dynamically via GDScript (load(), preload()), "
                        "or it may be genuinely unused."
                    ),
                )
            )
    return issues
