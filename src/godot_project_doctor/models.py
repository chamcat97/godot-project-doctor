"""Data models for godot-project-doctor (stdlib dataclasses version)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# Increment when the JSON output shape changes in a backward-incompatible way.
# 1.0 — initial release
# 1.1 — added ResourceRef.kind field
# 1.2 — added ResourceRef.resolved_path, ResourceRef.resolved_via
SCHEMA_VERSION = "1.2"


class Severity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class ResourceRef:
    """A single external resource reference collected from a project file.

    Fields
    ------
    source_file:
        Project-root-relative path of the file that declares this reference.
    ref_type:
        Godot resource class (e.g. ``"Script"``, ``"Texture2D"``) for
        ext_resource entries; the calling convention (``"preload"``,
        ``"load"``, ``"ResourceLoader.load"``) for GDScript static refs.
    path:
        Raw resource path as it appears in the source (``res://`` absolute,
        a ``uid://`` identifier, or a relative path).
    ref_id:
        Intra-file identifier used by ``[ext_resource]`` entries; empty string
        for GDScript refs.
    uid:
        Godot UID string (``uid://...``) from the ``uid=`` attribute when
        present; ``None`` otherwise.
    kind:
        Origin of the reference.  One of:

        * ``"ext_resource"`` — declared in a ``[ext_resource ...]`` header
          inside a ``.tscn`` or ``.tres`` file (default).
        * ``"gdscript"`` — extracted from a static string literal passed to
          ``preload()``, ``load()``, or ``ResourceLoader.load()`` in a
          ``.gd`` file.

        Added in schema 1.1.
    resolved_path:
        When ``path`` is a ``uid://`` reference that was successfully resolved
        via a ``.uid`` sidecar, ``.import`` file, or ``uid_cache.bin``, this
        field holds the resolved ``res://`` path.  ``None`` when the path is
        already a ``res://`` path or when the UID could not be resolved.

        Added in schema 1.2.
    resolved_via:
        The source that provided the ``resolved_path``.  One of:

        * ``"uid_sidecar"`` — resolved from a ``*.uid`` sidecar file.
        * ``"import"``      — resolved from a ``*.import`` file.
        * ``"uid_cache"``   — resolved from ``.godot/uid_cache.bin``.
        * ``None``          — not applicable (path was not a ``uid://``).

        Added in schema 1.2.
    """

    source_file: str  # relative path within project
    ref_type: str  # Godot class or call type
    path: str  # raw resource path (res://, uid://, or relative)
    ref_id: str  # id within declaring file; "" for GDScript refs
    uid: str | None = None
    kind: str = "ext_resource"  # "ext_resource" | "gdscript"
    resolved_path: str | None = None  # set when path is uid:// and resolved
    resolved_via: str | None = None  # "uid_sidecar" | "import" | "uid_cache"


@dataclass
class Issue:
    """A single audit finding."""

    code: str
    severity: Severity
    message: str
    file: str | None = None
    details: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "file": self.file,
            "details": self.details,
        }


@dataclass
class FileStats:
    """Counts of different file types found in the project."""

    scenes: int = 0
    resources: int = 0
    scripts: int = 0
    shaders: int = 0
    images: int = 0
    audio: int = 0
    other: int = 0

    @property
    def total(self) -> int:
        return (
            self.scenes
            + self.resources
            + self.scripts
            + self.shaders
            + self.images
            + self.audio
            + self.other
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "scenes": self.scenes,
            "resources": self.resources,
            "scripts": self.scripts,
            "shaders": self.shaders,
            "images": self.images,
            "audio": self.audio,
            "other": self.other,
        }


@dataclass
class ProjectSummary:
    """High-level metadata extracted from project.godot."""

    project_name: str | None = None
    main_scene: str | None = None
    autoloads: dict[str, str] = field(default_factory=dict)
    godot_version_hint: str | None = None
    # Project icon from application/config/icon.  Not serialised to JSON.
    icon: str | None = None
    # Input actions declared in the [input] section.  Not serialised to JSON.
    input_actions: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "main_scene": self.main_scene,
            "autoloads": self.autoloads,
            "godot_version_hint": self.godot_version_hint,
        }


@dataclass
class ProjectIndex:
    """Complete index of a scanned Godot project."""

    project_root: str
    summary: ProjectSummary
    file_stats: FileStats
    scenes: list[str] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    shaders: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    audio: list[str] = field(default_factory=list)
    other_files: list[str] = field(default_factory=list)
    has_export_presets: bool = False
    refs: list[ResourceRef] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    # Internal uid:// → res:// mapping.  Not serialised to JSON.
    uid_map: dict[str, str] = field(default_factory=dict)
    # Source of each UID resolution.  Not serialised to JSON.
    uid_sources: dict[str, str] = field(default_factory=dict)
    # (action_name, source_file) pairs from GDScript Input.*() calls.  Not serialised.
    input_action_refs: list[tuple[str, str]] = field(default_factory=list)

    @property
    def issue_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {s.value: 0 for s in Severity}
        for issue in self.issues:
            counts[issue.severity.value] += 1
        return counts


@dataclass
class ScanReport:
    """Top-level serialisable report."""

    schema_version: str
    project_root: str
    summary: ProjectSummary
    file_stats: FileStats
    issue_counts: dict[str, int]
    refs: list[ResourceRef]
    issues: list[Issue]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_root": self.project_root,
            "summary": self.summary.to_dict(),
            "file_stats": self.file_stats.to_dict(),
            "issue_counts": self.issue_counts,
            "refs": [
                {
                    "source_file": r.source_file,
                    "kind": r.kind,
                    "type": r.ref_type,
                    "uid": r.uid,
                    "path": r.path,
                    "id": r.ref_id,
                    "resolved_path": r.resolved_path,
                    "resolved_via": r.resolved_via,
                }
                for r in self.refs
            ],
            "issues": [i.to_dict() for i in self.issues],
        }
