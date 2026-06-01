"""Orchestrate parsing, indexing, and checks for a Godot project."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from godot_project_doctor.checks import run_all_checks
from godot_project_doctor.indexer import index_project
from godot_project_doctor.models import ProjectIndex
from godot_project_doctor.parser import parse_project_godot

if TYPE_CHECKING:
    from godot_project_doctor.config import Config


class GodotProjectError(Exception):
    """Raised when the target directory is not a valid Godot project."""


def scan(project_path: Path, config: Config | None = None) -> ProjectIndex:
    """Scan a Godot project at *project_path* and return a populated ProjectIndex.

    Parameters
    ----------
    project_path:
        Path to the Godot project root directory (must contain
        ``project.godot``).
    config:
        Optional :class:`~godot_project_doctor.config.Config` instance.
        When provided, configurable thresholds (e.g. ``large_texture_dim``)
        are read from it.  Pass ``None`` to use built-in defaults.

    Raises
    ------
    GodotProjectError
        If ``project.godot`` is not found in the given directory.
    NotADirectoryError
        If the path does not exist or is not a directory.
    """
    project_root = project_path.resolve()

    if not project_root.is_dir():
        raise NotADirectoryError(f"Not a directory: {project_root}")

    godot_cfg = project_root / "project.godot"
    if not godot_cfg.is_file():
        raise GodotProjectError(
            f"No project.godot found in: {project_root}\n"
            "Make sure you are pointing to the root of a Godot project."
        )

    summary = parse_project_godot(project_root)
    index = index_project(project_root, summary)

    issues = run_all_checks(index, config)
    index.issues.extend(issues)

    return index
