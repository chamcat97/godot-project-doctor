# Changelog

All notable changes to **godot-project-doctor** are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added
- **`CIRCULAR_DEPENDENCY`** (ERROR): detects cycles in the resource dependency
  graph (e.g. `SceneA.tscn → Enemy.tscn → SceneA.tscn`).  Uses iterative DFS
  to avoid Python stack overflows; duplicate cycle rotations are suppressed.
- `graph.find_cycles()`: public API for cycle detection, normalises
  project-relative and `res://` paths before comparison.
- `graph._to_res_path()`: internal helper that normalises both path forms.

### Fixed
- `_ref_to_canonical_rel`: relative paths containing `..` (e.g. `../assets/bg.png`)
  were not normalised, causing false `UNUSED_ASSET_CANDIDATE` warnings for assets
  referenced via parent-directory traversal.
- `context.py` investigation focus: removed `"load"` from `_MISSING_KEYWORDS`; it
  matched unrelated phrases like *"slow to load"* and incorrectly directed focus to
  missing-resource issues.
- `checks._check_large_textures`: collapsed `except (OSError, UnidentifiedImageError,
  Exception)` to plain `except Exception` with a clarifying comment (the former two
  are redundant subclasses of `Exception`).
- `parser.py`: removed unused module-level regexes `_KV_RE` and `_AUTOLOAD_RE`.

### Changed
- Coverage gate: `--cov-fail-under=80` added to CI and `[tool.coverage.report]`
  in `pyproject.toml`.

---

## [0.2.0] — 2025-06-01

### Added
- **`MISSING_MAIN_SCENE`** (ERROR): `run/main_scene` in `project.godot` set but file
  absent.
- **`NO_MAIN_SCENE`** (INFO): `run/main_scene` not configured (library projects).
- **`MISSING_AUTOLOAD`** (ERROR): autoload path declared in `project.godot` does not
  exist on disk.  `uid://` paths are silently skipped (require Godot import cache).
- `gdoctor context` command: AI-friendly Markdown report with deterministic
  investigation focus rules (no LLM calls).
- `gdoctor graph` command: dependency graph in `text` or `mermaid` format.
- GDScript static reference extraction: `preload()`, `load()`,
  `ResourceLoader.load()` — word-boundary guards, inline-comment stripping,
  string-context tracking, single-quoted paths.
- Mermaid node IDs use `slug_md5[:6]` to prevent collisions between paths
  sharing the same filename.
- `ResourceRef.kind` field (`"ext_resource"` | `"gdscript"`); JSON schema `"1.1"`.
- CP949 / narrow-encoding terminal safety: `stream.reconfigure(errors="replace")`
  at CLI startup; ASCII icon fallbacks in terminal renderer.
- `SCHEMA_VERSION = "1.1"` constant in `models.py`.
- `__version__ = "0.2.0"` used by `--version` flag.

### Fixed
- `[ext_resource]` attribute order is now fully irrelevant (two-pass parser).
- Relative `ext_resource` paths resolved from the declaring file's directory.
- Godot version hint in `project.godot` header comments now parsed correctly.
- `build-backend` in `pyproject.toml` corrected to `setuptools.build_meta`.

---

## [0.1.0] — 2025-06-01 (initial release)

### Added
- `gdoctor scan` command with `text`, `json`, `markdown` output formats.
- Detection of missing `ext_resource` references (`MISSING_EXT_RESOURCE`).
- Optional large-texture detection via Pillow (`LARGE_TEXTURE`).
- Large audio file detection (`LARGE_AUDIO`).
- Unused asset candidate detection (`UNUSED_ASSET_CANDIDATE`).
- Missing export presets detection (`NO_EXPORT_PRESETS`).
- `project.godot` metadata parsing: project name, main scene, autoloads,
  Godot version hint.
- Dependency graph builder and Mermaid/text renderers.

[Unreleased]: https://github.com/chamcat97/godot-project-doctor/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/chamcat97/godot-project-doctor/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/chamcat97/godot-project-doctor/releases/tag/v0.1.0
