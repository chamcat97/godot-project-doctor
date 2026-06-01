# Changelog

All notable changes to **godot-project-doctor** are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

*(Phase 2 config file + exit-code policy in progress)*

---

## [0.3.0] — 2025-06-02  *(schema 1.2)*

### Added
- **`uid://` full resolution** (Phase 1): `uid_map.py` now reads three sources:
  1. `*.uid` sidecar files (highest priority)
  2. `*.import` files (`[remap]` section `uid=` + `source_file=`)
  3. `.godot/uid_cache.bin` (best-effort binary parse, silently ignored on failure)
  Sources are merged; same UID from different sources for the *same path* is
  accepted silently; different paths → `DUPLICATE_UID` WARNING.
- `ResourceRef.resolved_path` — `res://` path when a `uid://` ref was resolved;
  `None` for already-resolved `res://` paths.  Added to JSON output.
- `ResourceRef.resolved_via` — source of the resolution
  (`"uid_sidecar"` | `"import"` | `"uid_cache"`); `None` otherwise.
  Added to JSON output.
- `ProjectIndex.uid_sources` — maps each resolved UID to its source (internal,
  not serialised).

### Changed
- `build_uid_map()` now returns `(uid_map, uid_sources, issues)` instead of
  `(uid_map, issues)`.
- `SCHEMA_VERSION` bumped to `"1.2"` (`resolved_path` + `resolved_via` are
  additive; existing consumers can ignore them).
- `__version__` bumped to `"0.3.0"`.

---

## [0.2.1] — 2025-06-02  *(schema 1.1 unchanged)*

### Added
- **`CIRCULAR_DEPENDENCY`** (ERROR): detects cycles in the resource dependency
  graph (e.g. `SceneA.tscn → Enemy.tscn → SceneA.tscn`).  Uses iterative DFS
  to avoid Python stack overflows; duplicate cycle rotations are suppressed.
- `graph.find_cycles()`: public API for cycle detection, normalises
  project-relative and `res://` paths before comparison.
- **`uid://` sidecar resolution** (`uid_map.py`): reads `*.uid` sidecar files to
  build a `uid:// → res://` mapping, reducing false negatives in
  `MISSING_EXT_RESOURCE` and `UNUSED_ASSET_CANDIDATE` checks.
- **`DUPLICATE_UID`** (WARNING): emitted when two `*.uid` sidecar files claim the
  same `uid://` string.

### Fixed
- `_ref_to_canonical_rel`: relative ext_resource paths containing `..` were not
  normalised, causing false `UNUSED_ASSET_CANDIDATE` warnings.
- `context.py` investigation focus: removed `"load"` from `_MISSING_KEYWORDS`;
  it matched phrases like *"slow to load"* and misdirected focus.
- `checks._check_large_textures`: redundant `(OSError, UnidentifiedImageError,
  Exception)` collapsed to `except Exception` with an explanatory comment.
- `parser.py`: removed unused module-level regexes `_KV_RE` and `_AUTOLOAD_RE`.

### Changed
- Coverage gate: `--cov-fail-under=80` added to CI and `[tool.coverage.report]`.

---

## [0.2.0] — 2025-06-01

### Added
- **`MISSING_MAIN_SCENE`** (ERROR): `run/main_scene` in `project.godot` set but
  file absent.
- **`NO_MAIN_SCENE`** (INFO): `run/main_scene` not configured (library projects).
- **`MISSING_AUTOLOAD`** (ERROR): autoload path declared in `project.godot` does
  not exist on disk.  `uid://` paths are silently skipped (require Godot import
  cache).
- `gdoctor context` command: AI-friendly Markdown report with deterministic
  investigation focus rules (no LLM calls).
- `gdoctor graph` command: dependency graph in `text` or `mermaid` format.
- GDScript static reference extraction: `preload()`, `load()`,
  `ResourceLoader.load()` — word-boundary guards, inline-comment stripping,
  string-context tracking, single-quoted paths.
- Mermaid node IDs use `slug_md5[:6]` to prevent collisions.
- `ResourceRef.kind` field (`"ext_resource"` | `"gdscript"`); JSON schema `"1.1"`.
- CP949 / narrow-encoding terminal safety.
- `SCHEMA_VERSION = "1.1"` constant in `models.py`.

### Fixed
- `[ext_resource]` attribute order is now fully irrelevant (two-pass parser).
- Relative `ext_resource` paths resolved from the declaring file's directory.
- Godot version hint in `project.godot` header comments now parsed correctly.
- `build-backend` in `pyproject.toml` corrected to `setuptools.build_meta`.

---

## [0.1.0] — 2025-06-01  *(initial release)*

### Added
- `gdoctor scan` command with `text`, `json`, `markdown` output formats.
- `MISSING_EXT_RESOURCE`, `LARGE_TEXTURE`, `LARGE_AUDIO`, `UNUSED_ASSET_CANDIDATE`,
  `NO_EXPORT_PRESETS` checks.
- `project.godot` metadata parsing.
- Dependency graph builder and Mermaid/text renderers.

[Unreleased]: https://github.com/chamcat97/godot-project-doctor/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/chamcat97/godot-project-doctor/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/chamcat97/godot-project-doctor/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/chamcat97/godot-project-doctor/releases/tag/v0.1.0
