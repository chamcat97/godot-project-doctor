"""Extract static resource references from GDScript source files.

Only *string-literal* ``res://`` paths are collected.  Dynamic paths
(variables, expressions, format strings) are deliberately ignored — the
analyser cannot resolve them without executing the script.

Supported call forms
--------------------
* ``preload("res://path/to/resource")``
* ``load("res://path/to/resource")``
* ``ResourceLoader.load("res://path/to/resource")``

Lines that begin with ``#`` (whole-line GDScript comments) are skipped.
Inline comments after code are not stripped; a ``load()`` call that appears
in an inline comment will still be matched.  This is an accepted limitation
of a line-based static analyser that has no GDScript tokeniser.
"""

from __future__ import annotations

import re
from pathlib import Path

from godot_project_doctor.models import ResourceRef

# Matches the three supported call forms, capturing the res:// literal.
# Only fires when the first argument is a plain string literal starting with
# res:// — expressions, variables, and non-res paths are not matched.
_STATIC_LOAD_RE = re.compile(
    r"(?P<call>ResourceLoader\.load|preload|load)"
    r'\s*\(\s*"(?P<path>res://[^"]*)"\s*'
)


def extract_gdscript_refs(file_path: Path, project_root: Path) -> list[ResourceRef]:
    """Return all static ``res://`` references found in *file_path*.

    Parameters
    ----------
    file_path:
        Absolute path to the ``.gd`` file.
    project_root:
        Absolute path to the Godot project root (used to build
        ``source_file`` as a project-relative path).

    Returns
    -------
    list[ResourceRef]
        One entry per matched string literal.  ``kind`` is always
        ``"gdscript"``; ``ref_type`` is the call name
        (``"preload"``, ``"load"``, or ``"ResourceLoader.load"``);
        ``ref_id`` is empty; ``uid`` is ``None``.
    """
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    rel = str(file_path.relative_to(project_root))
    refs: list[ResourceRef] = []

    for line in text.splitlines():
        # Skip whole-line comments
        if line.lstrip().startswith("#"):
            continue

        for m in _STATIC_LOAD_RE.finditer(line):
            refs.append(
                ResourceRef(
                    source_file=rel,
                    ref_type=m.group("call"),
                    path=m.group("path"),
                    ref_id="",
                    uid=None,
                    kind="gdscript",
                )
            )

    return refs
