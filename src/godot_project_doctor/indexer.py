"""Index Godot project files and parse external resource references."""

from __future__ import annotations

import re
from pathlib import Path

from godot_project_doctor.models import (
    FileStats,
    ProjectIndex,
    ProjectSummary,
    ResourceRef,
)

# Directories to skip entirely during traversal
_SKIP_DIRS = frozenset(
    [
        ".git",
        ".godot",
        ".import",
        "__pycache__",
        "build",
        ".gradle",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        ".cache",
    ]
)

# File extension → category
_SCENE_EXTS = frozenset([".tscn"])
_RESOURCE_EXTS = frozenset([".tres"])
_SCRIPT_EXTS = frozenset([".gd"])
_SHADER_EXTS = frozenset([".shader", ".gdshader"])
_IMAGE_EXTS = frozenset([".png", ".jpg", ".jpeg", ".webp", ".svg"])
_AUDIO_EXTS = frozenset([".wav", ".ogg", ".mp3"])

# ── ext_resource parsing (two-pass, order-independent) ───────────────────────

# Pass 1: capture everything between [ext_resource and the closing ]
_EXT_RESOURCE_BLOCK_RE = re.compile(r"\[ext_resource\b([^\]]*)\]")

# Pass 2: extract key="value" pairs from that block
_ATTR_KV_RE = re.compile(r'\b(\w+)="([^"]*)"')


def _parse_ext_resources(file_path: Path, project_root: Path) -> list[ResourceRef]:
    """Extract all ext_resource entries from a text scene/resource file.

    Attribute order within the ``[ext_resource ...]`` header is irrelevant.
    Paths that are not ``res://`` absolute are resolved relative to the
    declaring file's directory (rare in practice but spec-compliant).
    """
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    rel = str(file_path.relative_to(project_root))
    refs: list[ResourceRef] = []

    for block_match in _EXT_RESOURCE_BLOCK_RE.finditer(text):
        attrs: dict[str, str] = dict(_ATTR_KV_RE.findall(block_match.group(1)))

        path = attrs.get("path", "")
        if not path:
            continue  # skip malformed entries without a path

        # Normalise relative paths to a project-root-relative form.
        # Godot always writes res:// paths, but we handle the edge case.
        if not path.startswith("res://") and not path.startswith("uid://"):
            source_dir = file_path.parent
            try:
                resolved_abs = (source_dir / path).resolve()
                path = "res://" + resolved_abs.relative_to(project_root.resolve()).as_posix()
            except ValueError:
                # Path escapes project root — keep raw and let checks report it
                pass

        refs.append(
            ResourceRef(
                source_file=rel,
                ref_type=attrs.get("type", ""),
                uid=attrs.get("uid"),
                path=path,
                ref_id=attrs.get("id", ""),
                kind="ext_resource",
            )
        )

    return refs


def index_project(project_root: Path, summary: ProjectSummary) -> ProjectIndex:
    """Walk the project directory and build a ProjectIndex.

    Parses ext_resource entries from all ``.tscn`` and ``.tres`` files,
    extracts static ``res://`` references from all ``.gd`` files, and builds
    a ``uid:// → res://`` mapping from ``.uid`` sidecar files, ``.import``
    files, and ``.godot/uid_cache.bin`` (best-effort).

    For each :class:`~godot_project_doctor.models.ResourceRef` whose ``path``
    field is a ``uid://`` reference that appears in the uid map, the
    ``resolved_path`` and ``resolved_via`` fields are populated.
    """
    # Import here to avoid circular dependencies at module load time
    from godot_project_doctor.gdscript import extract_gdscript_refs, extract_input_action_refs
    from godot_project_doctor.uid_map import build_uid_map

    scenes: list[str] = []
    resources: list[str] = []
    scripts: list[str] = []
    shaders: list[str] = []
    images: list[str] = []
    audio: list[str] = []
    other_files: list[str] = []
    has_export_presets = False
    all_refs: list[ResourceRef] = []
    all_input_refs: list[tuple[str, str]] = []

    for path in _walk(project_root):
        rel = str(path.relative_to(project_root))
        ext = path.suffix.lower()

        if path.name == "export_presets.cfg":
            has_export_presets = True
            continue  # don't add to "other"

        if ext in _SCENE_EXTS:
            scenes.append(rel)
            all_refs.extend(_parse_ext_resources(path, project_root))
        elif ext in _RESOURCE_EXTS:
            resources.append(rel)
            all_refs.extend(_parse_ext_resources(path, project_root))
        elif ext in _SCRIPT_EXTS:
            scripts.append(rel)
            all_refs.extend(extract_gdscript_refs(path, project_root))
            all_input_refs.extend(extract_input_action_refs(path, project_root))
        elif ext in _SHADER_EXTS:
            shaders.append(rel)
        elif ext in _IMAGE_EXTS:
            images.append(rel)
        elif ext in _AUDIO_EXTS:
            audio.append(rel)
        else:
            other_files.append(rel)

    file_stats = FileStats(
        scenes=len(scenes),
        resources=len(resources),
        scripts=len(scripts),
        shaders=len(shaders),
        images=len(images),
        audio=len(audio),
        other=len(other_files),
    )

    uid_map, uid_sources, uid_issues = build_uid_map(project_root)

    # Populate resolved_path / resolved_via on uid:// refs
    for ref in all_refs:
        if ref.path.startswith("uid://"):
            res = uid_map.get(ref.path)
            if res:
                ref.resolved_path = res
                ref.resolved_via = uid_sources.get(ref.path)

    index = ProjectIndex(
        project_root=str(project_root),
        summary=summary,
        file_stats=file_stats,
        scenes=sorted(scenes),
        resources=sorted(resources),
        scripts=sorted(scripts),
        shaders=sorted(shaders),
        images=sorted(images),
        audio=sorted(audio),
        other_files=sorted(other_files),
        has_export_presets=has_export_presets,
        refs=all_refs,
        uid_map=uid_map,
        uid_sources=uid_sources,
        input_action_refs=all_input_refs,
    )
    index.issues.extend(uid_issues)
    return index


def _walk(root: Path):
    """Yield all files under root, skipping ignored directories."""
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.name not in _SKIP_DIRS:
            yield from _walk(child)
        elif child.is_file() and child.name != "project.godot":
            yield child


def resolve_res_path(res_path: str, project_root: Path) -> Path:
    """Convert a ``res://`` Godot path to an absolute filesystem path.

    For paths that are already project-root-relative (no ``res://`` prefix),
    the path is treated as relative to the project root.  Use
    :func:`resolve_ref_path` when the declaring file's location is known.
    """
    if res_path.startswith("res://"):
        return project_root / res_path[len("res://") :]
    return project_root / res_path


def resolve_ref_path(
    ref_path: str,
    project_root: Path,
    source_file: str = "",
    uid_map: dict[str, str] | None = None,
) -> Path | None:
    """Resolve a resource reference to an absolute filesystem path.

    Parameters
    ----------
    ref_path:
        Raw path from the ``ResourceRef.path`` field.
    project_root:
        Absolute path to the Godot project root.
    source_file:
        Project-root-relative path of the file that declares the reference.
        Used to resolve relative (non-``res://``) paths.
    uid_map:
        Optional mapping of ``uid://`` → ``res://`` paths built from ``.uid``
        sidecar files.  When provided, ``uid://`` references that appear in
        the map are resolved to their ``res://`` counterpart; unrecognised
        UIDs are still skipped (returns ``None``) to avoid false positives.

    Returns
    -------
    Path | None
        ``None`` when the path uses an unresolvable ``uid://`` scheme.
    """
    if ref_path.startswith("uid://"):
        if uid_map:
            resolved = uid_map.get(ref_path)
            if resolved and resolved.startswith("res://"):
                return project_root / resolved[len("res://") :]
        return None  # UID not in map — skip to avoid false positives

    if ref_path.startswith("res://"):
        return project_root / ref_path[len("res://") :]

    # Relative path — resolve from the declaring file's directory
    if source_file:
        return (project_root / source_file).parent / ref_path

    return project_root / ref_path
