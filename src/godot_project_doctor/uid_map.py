"""Build a ``uid:// → res://`` mapping from multiple Godot 4 sources.

Godot 4 records ``uid://`` identifiers for every tracked resource.  This
module aggregates mappings from three sources in priority order:

1. **`*.uid` sidecar files** (highest priority)
   Godot writes ``scripts/player.gd.uid`` alongside ``scripts/player.gd``.
   Format: a single line ``uid://cb6n3abcde7o5``.

2. **`*.import` files**
   Godot creates ``assets/hero.png.import`` alongside imported resources.
   The ``[remap]`` section contains ``uid=`` and ``source_file=`` keys.

3. **`.godot/uid_cache.bin`** (best-effort, lowest priority)
   A binary cache maintained by the Godot editor.  Parsed on a
   best-effort basis; any parse failure is silently ignored so that
   projects without an editor-opened `.godot/` directory are unaffected.

All three sources are merged; when different sources agree on the same
UID→path pair the mapping is accepted.  When different sources assign the
**same UID to different paths**, a ``DUPLICATE_UID`` WARNING is emitted.

Return value
------------
``build_uid_map`` returns ``(uid_map, uid_sources, issues)`` where

* ``uid_map``     — ``dict[str, str]``   uid://... → res://...
* ``uid_sources`` — ``dict[str, str]``   uid://... → "uid_sidecar" | "import" | "uid_cache"
* ``issues``      — ``list[Issue]``      DUPLICATE_UID warnings

Limitations
-----------
* ``*.import`` files that lack both ``uid=`` **and** ``source_file=`` in
  their ``[remap]`` section are silently skipped.
* ``.godot/uid_cache.bin`` parsing is a best-effort binary heuristic.
  If the file format changes in a future Godot version, parsing will fail
  silently and the map will be built from the other two sources only.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

from godot_project_doctor.models import Issue, Severity

# Source-priority order (lower index = higher priority)
_SOURCE_PRIORITY: dict[str, int] = {
    "uid_sidecar": 0,
    "import": 1,
    "uid_cache": 2,
}

# Directories skipped during the project walk (kept in sync with indexer)
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


# ── Public API ────────────────────────────────────────────────────────────────


def build_uid_map(
    project_root: Path,
) -> tuple[dict[str, str], dict[str, str], list[Issue]]:
    """Build a ``uid:// → res://`` mapping for *project_root*.

    Returns
    -------
    uid_map : dict[str, str]
        Maps each resolved ``uid://`` string to its ``res://`` path.
    uid_sources : dict[str, str]
        Maps each UID to the source that provided the mapping
        (``"uid_sidecar"``, ``"import"``, or ``"uid_cache"``).
    issues : list[Issue]
        One ``DUPLICATE_UID`` WARNING per UID claimed by more than one
        *distinct* ``res://`` path.
    """
    # uid_str → list of (res_path, source_name) from all sources
    raw: dict[str, list[tuple[str, str]]] = {}

    # ── source 1: .uid sidecar files ─────────────────────────────────────────
    for uid_file in _walk_by_suffix(project_root, ".uid"):
        uid_str = _parse_uid_file(uid_file)
        if not uid_str:
            continue
        rel = uid_file.relative_to(project_root)
        resource_rel = str(rel.with_suffix("")).replace("\\", "/")
        res_path = f"res://{resource_rel}"
        raw.setdefault(uid_str, []).append((res_path, "uid_sidecar"))

    # ── source 2: .import files ───────────────────────────────────────────────
    for import_file in _walk_by_suffix(project_root, ".import"):
        uid_str, source_file = _parse_import_file(import_file)
        if uid_str and source_file:
            raw.setdefault(uid_str, []).append((source_file, "import"))

    # ── source 3: .godot/uid_cache.bin (best-effort) ─────────────────────────
    for uid_str, res_path in _try_parse_uid_cache(
        project_root / ".godot" / "uid_cache.bin"
    ).items():
        raw.setdefault(uid_str, []).append((res_path, "uid_cache"))

    # ── merge & deduplicate ───────────────────────────────────────────────────
    uid_map: dict[str, str] = {}
    uid_sources: dict[str, str] = {}
    issues: list[Issue] = []

    for uid_str, entries in sorted(raw.items()):
        # Group by unique res_path
        path_to_best: dict[str, tuple[str, str]] = {}
        for res_path, source in entries:
            if res_path not in path_to_best or (
                _SOURCE_PRIORITY.get(source, 99)
                < _SOURCE_PRIORITY.get(path_to_best[res_path][1], 99)
            ):
                path_to_best[res_path] = (res_path, source)

        unique_paths = sorted(path_to_best.keys())

        if len(unique_paths) == 1:
            res_path, source = path_to_best[unique_paths[0]]
            uid_map[uid_str] = res_path
            uid_sources[uid_str] = source
        else:
            # Multiple *distinct* paths claim the same UID → DUPLICATE_UID
            best_path = unique_paths[0]
            best_source = path_to_best[best_path][1]
            uid_map[uid_str] = best_path
            uid_sources[uid_str] = best_source
            issues.append(
                Issue(
                    code="DUPLICATE_UID",
                    severity=Severity.WARNING,
                    message=f"UID {uid_str!r} is claimed by {len(unique_paths)} files",
                    file=None,
                    details=(
                        f"Files: {', '.join(unique_paths)}. "
                        "Godot will bind only one; the others may fail to load."
                    ),
                )
            )

    return uid_map, uid_sources, issues


# ── Per-source parsers ────────────────────────────────────────────────────────


def _parse_uid_file(path: Path) -> str | None:
    """Return the ``uid://...`` string from a ``.uid`` sidecar, or ``None``."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    line = text.splitlines()[0].strip() if text else ""
    return line if line.startswith("uid://") else None


def _parse_import_file(path: Path) -> tuple[str | None, str | None]:
    """Extract ``(uid_str, source_file)`` from a ``.import`` file.

    Only keys inside the ``[remap]`` section are considered.  Returns
    ``(None, None)`` when either field is absent or the file is unreadable.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None

    in_remap = False
    uid_str: str | None = None
    source_file: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("["):
            in_remap = line == "[remap]"
            continue
        if not in_remap:
            continue
        # key="value" lines
        if line.startswith('uid="') and line.endswith('"'):
            val = line[5:-1]
            if val.startswith("uid://"):
                uid_str = val
        elif line.startswith('source_file="') and line.endswith('"'):
            val = line[13:-1]
            if val.startswith("res://"):
                source_file = val

    return uid_str, source_file


def _try_parse_uid_cache(cache_path: Path) -> dict[str, str]:
    """Best-effort extract of uid→res mappings from ``.godot/uid_cache.bin``.

    Godot 4 stores each entry as a pair of length-prefixed UTF-8 strings
    (4-byte LE uint32 length followed by the UTF-8 content).  The file
    begins with a 12-byte header (4-byte magic + 4-byte format version +
    4-byte entry count).  Any deviation from this layout — or any other
    exception — is caught silently and an empty dict is returned.
    """
    result: dict[str, str] = {}
    try:
        data = cache_path.read_bytes()
    except OSError:
        return result

    try:
        # Header: 4-byte magic, 4-byte version, 4-byte count
        if len(data) < 12:
            return result
        magic = data[:4]
        if magic not in (b"GDUC", b"GDRC"):
            # Unrecognised magic — fall back to regex heuristic
            return _try_regex_uid_cache(data)

        count = struct.unpack_from("<I", data, 8)[0]
        pos = 12
        for _ in range(count):
            if pos + 4 > len(data):
                break
            uid_len = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            if pos + uid_len > len(data):
                break
            uid_str = data[pos : pos + uid_len].decode("utf-8", errors="replace")
            pos += uid_len

            if pos + 4 > len(data):
                break
            path_len = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            if pos + path_len > len(data):
                break
            res_path = data[pos : pos + path_len].decode("utf-8", errors="replace")
            pos += path_len

            if uid_str.startswith("uid://") and res_path.startswith("res://"):
                result[uid_str] = res_path
    except Exception:  # noqa: BLE001 — any parse failure is silently ignored
        pass

    return result


# ── Internal helpers ──────────────────────────────────────────────────────────

_UID_RE = re.compile(r"uid://[a-z0-9]+")
_RES_RE = re.compile(r"res://[^\x00\n\r\"']+")


def _try_regex_uid_cache(data: bytes) -> dict[str, str]:
    """Fallback heuristic: scan binary content for uid:// + res:// string pairs."""
    result: dict[str, str] = {}
    try:
        text = data.decode("utf-8", errors="replace")
        uids = list(_UID_RE.finditer(text))
        paths = list(_RES_RE.finditer(text))

        # Pair each uid with the nearest following res:// path within 512 chars
        path_idx = 0
        for uid_m in uids:
            uid_end = uid_m.end()
            while path_idx < len(paths) and paths[path_idx].start() < uid_end:
                path_idx += 1
            if path_idx < len(paths):
                gap = paths[path_idx].start() - uid_end
                if gap <= 512:
                    result[uid_m.group()] = paths[path_idx].group()
    except Exception:  # noqa: BLE001
        pass
    return result


def _walk_by_suffix(root: Path, suffix: str):
    """Yield files with *suffix* under *root*, skipping ignored directories."""
    for child in sorted(root.iterdir()):
        if child.is_dir():
            if child.name not in _SKIP_DIRS:
                yield from _walk_by_suffix(child, suffix)
        elif child.is_file() and child.suffix == suffix:
            yield child
