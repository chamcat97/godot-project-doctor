"""Extract static resource references from GDScript source files.

Only *string-literal* ``res://`` paths are collected.  Dynamic paths
(variables, expressions, format strings) are deliberately ignored — the
analyser cannot resolve them without executing the script.

Supported call forms
--------------------
* ``preload("res://path/to/resource")``   (double-quoted)
* ``preload('res://path/to/resource')``   (single-quoted — valid GDScript)
* ``load("res://path/to/resource")``
* ``load('res://path/to/resource')``
* ``ResourceLoader.load("res://path/to/resource")``
* ``ResourceLoader.load('res://path/to/resource')``

False-positive guards
---------------------
* Calls that are part of a longer identifier are rejected:

  - ``download("res://...")``   → ``load`` has a word char to its left
  - ``preload_cache("res://...")`` → ``preload`` has a word char to its right
  - ``my_load("res://...")``    → ``load`` has a word char to its left

* Whole-line GDScript comments (``# …``) are skipped entirely.
* Inline comments (``var x = 1  # load("res://foo")``) are stripped before
  scanning, using a small lexical helper that tracks string context.
* A call whose identifier position is inside a string literal is skipped,
  handling cases like ``var s = 'load("res://foo")'``.
"""

from __future__ import annotations

import re
from pathlib import Path

from godot_project_doctor.models import ResourceRef

# ── Regex ─────────────────────────────────────────────────────────────────────
#
# (?<![A-Za-z0-9_])  — negative lookbehind: rejects download(), my_load() etc.
# (?![A-Za-z0-9_])   — negative lookahead:  rejects preload_cache() etc.
# Supports both " and ' quoted res:// paths.

_STATIC_LOAD_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?P<call>ResourceLoader\.load|preload|load)"
    r"(?![A-Za-z0-9_])"
    r'\s*\(\s*(?:"(?P<path_dq>res://[^"]*)"|\'(?P<path_sq>res://[^\']*)\')'
    r"\s*",
)


# ── Lexical helpers ────────────────────────────────────────────────────────────


def _strip_inline_comment(line: str) -> str:
    """Return *line* with everything from the first ``#`` outside a string removed.

    Handles both ``"…"`` and ``'…'`` single-line string literals, including
    simple ``\\x`` escape sequences.
    """
    in_str = False
    q = ""
    i = 0
    while i < len(line):
        c = line[i]
        if in_str:
            if c == "\\":
                i += 2  # skip one-char escape sequence
                continue
            if c == q:
                in_str = False
        else:
            if c in ('"', "'"):
                in_str = True
                q = c
            elif c == "#":
                return line[:i]
        i += 1
    return line


def _pos_in_string(line: str, pos: int) -> bool:
    """Return ``True`` if the character at *pos* is inside a string literal.

    Used to reject ``load()`` calls that appear as text content inside another
    string, e.g. ``var s = 'load("res://foo")'``.
    """
    in_str = False
    q = ""
    i = 0
    while i < pos:
        c = line[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == q:
                in_str = False
        else:
            if c in ('"', "'"):
                in_str = True
                q = c
        i += 1
    return in_str


# ── Public API ────────────────────────────────────────────────────────────────


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

    for raw_line in text.splitlines():
        # Skip whole-line comments
        if raw_line.lstrip().startswith("#"):
            continue

        # Strip inline comment (from # outside a string)
        line = _strip_inline_comment(raw_line)

        for m in _STATIC_LOAD_RE.finditer(line):
            # Skip matches where the call identifier is inside a string literal
            if _pos_in_string(line, m.start()):
                continue

            path = m.group("path_dq") or m.group("path_sq") or ""
            if not path:
                continue

            refs.append(
                ResourceRef(
                    source_file=rel,
                    ref_type=m.group("call"),
                    path=path,
                    ref_id="",
                    uid=None,
                    kind="gdscript",
                )
            )

    return refs
