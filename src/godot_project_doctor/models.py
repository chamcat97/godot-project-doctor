"""Data models for godot-project-doctor (stdlib dataclasses version)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


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
        Raw resource path as it appears in the source (``res://`` absolute or
        a relative path for non-GDScript refs).
    ref_id:
        Intra-file identifier used by ``[ext_resource]`` entries; empty string
        for GDScript refs.
    uid:
        Godot UID string (``uid://...``) when present; ``None`` otherwise.
    kind:
        Origin of the reference.  One of:

        * ``"ext_resource"`` — declared in a ``[ext_resource ...]`` header
          inside a ``.tscn`` or ``.tres`` file (default).
        * ``"gdscript"`` — extracted from a static string literal passed to
          ``preload()``, ``load()``, or ``ResourceLoader.load()`` in a
          ``.gd`` file.

        Added in schema 1.1.  Consumers that only need the path can ignore
        this field.
    """

    source_file: str  # relative path within project
    ref_type: str  # Godot class or call type
    path: str  # raw resource path (res:// or relative)
    ref_id: str  # id within declaring file; "" for GDScript refs
    uid: str | None = None
    kind: str = "ext_resource"  # "ext_resource" | "gdscript"


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
                }
                for r in self.refs
            ],
            "issues": [i.to_dict() for i in self.issues],
        }
