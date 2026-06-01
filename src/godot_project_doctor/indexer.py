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

# Regex to match [ext_resource ...] lines in .tscn / .tres
# Handles both single-line and attribute order variations.
_EXT_RESOURCE_RE = re.compile(
    r'\[ext_resource\b'
    r'(?:[^\]]*\btype="(?P<type>[^"]*)")?'
    r'(?:[^\]]*\buid="(?P<uid>[^"]*)")?'
    r'(?:[^\]]*\bpath="(?P<path>[^"]*)")?'
    r'(?:[^\]]*\bid="(?P<id>[^"]*)")?'
    r'[^\]]*\]'
)


def _parse_ext_resources(file_path: Path, project_root: Path) -> list[ResourceRef]:
    """Extract all ext_resource entries from a text scene/resource file."""
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    rel = str(file_path.relative_to(project_root))
    refs: list[ResourceRef] = []

    for m in _EXT_RESOURCE_RE.finditer(text):
        path = m.group("path") or ""
        ref_type = m.group("type") or ""
        uid = m.group("uid")
        ref_id = m.group("id") or ""

        if not path:
            continue  # skip malformed entries without a path

        refs.append(
            ResourceRef(
                source_file=rel,
                ref_type=ref_type,
                uid=uid,
                path=path,
                ref_id=ref_id,
            )
        )

    return refs


def index_project(project_root: Path, summary: ProjectSummary) -> ProjectIndex:
    """
    Walk the project directory and build a ProjectIndex.

    Parses ext_resource entries from all .tscn and .tres files.
    """
    scenes: list[str] = []
    resources: list[str] = []
    scripts: list[str] = []
    shaders: list[str] = []
    images: list[str] = []
    audio: list[str] = []
    other_files: list[str] = []
    has_export_presets = False
    all_refs: list[ResourceRef] = []

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

    return ProjectIndex(
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
    )


def _walk(root: Path):
    """Yield all files under root, skipping ignored directories."""
    for child in sorted(root.iterdir()):
        if child.is_dir():
            if child.name not in _SKIP_DIRS:
                yield from _walk(child)
        elif child.is_file():
            # Skip project.godot itself from the file lists
            if child.name != "project.godot":
                yield child


def resolve_res_path(res_path: str, project_root: Path) -> Path:
    """Convert a res:// Godot path to an absolute filesystem path."""
    if res_path.startswith("res://"):
        relative = res_path[len("res://"):]
        return project_root / relative
    # Fallback: treat as relative
    return project_root / res_path
