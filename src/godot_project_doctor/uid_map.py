"""Build a ``uid:// → res://`` mapping from Godot 4 ``.uid`` sidecar files.

Godot 4 writes a small sidecar file next to every resource it tracks.
For example, ``res://scripts/player.gd`` may have a sibling
``res://scripts/player.gd.uid`` whose entire content is a single line::

    uid://cb6n3abcde7o5

This module scans the project tree for ``.uid`` files, parses them, and
returns two artefacts:

1. A ``uid_map`` (``dict[str, str]``) mapping each ``uid://`` string to the
   corresponding ``res://`` path.
2. A list of ``DUPLICATE_UID`` issues where two files claim the same UID.

Limitations
-----------
* Only ``.uid`` sidecar files are read.  ``.import`` files and the binary
  ``.godot/uid_cache.bin`` are **not** parsed in this version.
* The ``.godot/`` directory is already excluded from the project walk, so
  import-cache UIDs are unavailable without additional logic.
* If Godot has not yet generated ``.uid`` files (e.g. a freshly cloned repo
  that has never been opened), the map will be empty and all ``uid://``
  references will continue to be skipped silently.
"""

from __future__ import annotations

from pathlib import Path

from godot_project_doctor.models import Issue, Severity

# Directories skipped during the main project walk (kept in sync with indexer)
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


def build_uid_map(project_root: Path) -> tuple[dict[str, str], list[Issue]]:
    """Scan *project_root* for ``.uid`` sidecar files and build a UID mapping.

    Parameters
    ----------
    project_root:
        Absolute path to the Godot project root.

    Returns
    -------
    uid_map : dict[str, str]
        Maps each ``uid://...`` string to its ``res://`` path.
        The ``res://`` path is derived from the sidecar file's location:
        ``scripts/player.gd.uid`` → ``res://scripts/player.gd``.
    issues : list[Issue]
        One ``DUPLICATE_UID`` WARNING per UID that is claimed by more than
        one file.
    """
    # uid_string → list of res:// paths that claim it
    raw: dict[str, list[str]] = {}

    for uid_file in _walk_uid_files(project_root):
        uid_str = _parse_uid_file(uid_file)
        if not uid_str:
            continue

        # The resource path is the sidecar path without the trailing ".uid"
        rel = uid_file.relative_to(project_root)
        resource_rel = str(rel.with_suffix("")).replace("\\", "/")
        # Strip the remaining extension suffix (e.g. ".gd" is the actual ext)
        # The sidecar is "foo.gd.uid" → resource is "foo.gd"
        res_path = f"res://{resource_rel}"

        raw.setdefault(uid_str, []).append(res_path)

    uid_map: dict[str, str] = {}
    issues: list[Issue] = []

    for uid_str, paths in sorted(raw.items()):
        if len(paths) == 1:
            uid_map[uid_str] = paths[0]
        else:
            # Multiple files claim the same UID → duplicate
            sorted_paths = sorted(paths)
            uid_map[uid_str] = sorted_paths[0]  # use first deterministically
            issues.append(
                Issue(
                    code="DUPLICATE_UID",
                    severity=Severity.WARNING,
                    message=f"UID {uid_str!r} is claimed by {len(paths)} files",
                    file=None,
                    details=(
                        f"Files claiming this UID: {', '.join(sorted_paths)}. "
                        "Godot will only bind one of them; the others may fail to load."
                    ),
                )
            )

    return uid_map, issues


def _parse_uid_file(path: Path) -> str | None:
    """Return the ``uid://...`` string from a ``.uid`` sidecar, or ``None``."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None

    # A valid sidecar contains exactly one line: "uid://..."
    # Tolerate a trailing newline.
    line = text.splitlines()[0].strip() if text else ""
    if line.startswith("uid://"):
        return line
    return None


def _walk_uid_files(root: Path):
    """Yield all ``*.uid`` files under *root*, skipping ignored directories."""
    for child in sorted(root.iterdir()):
        if child.is_dir():
            if child.name not in _SKIP_DIRS:
                yield from _walk_uid_files(child)
        elif child.is_file() and child.suffix == ".uid":
            yield child
