# Changelog

All notable changes to **godot-project-doctor** are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

---

## [0.8.3] — 2026-06-01

### Fixed
- **Unwritable `--output` paths no longer crash with a traceback.** `scan`,
  `graph`, and `context` now catch `OSError` (permission denied, missing parent
  directory, read-only target) when writing the output file and exit cleanly
  with code 2 and a `cannot write to '<path>': <reason>` message. Previously a
  `PermissionError` (e.g. running from `C:\Windows\System32`) surfaced as a raw
  Python traceback.

### Changed
- `__version__` bumped to `"0.8.3"`.

---

## [0.8.2] — 2026-06-01

Signal-to-noise release — validated against a real 86-script project where the
default scan dropped from 77 warnings (≈99% false positives) to 2 genuine ones.

### Added
- **`addons/` ignored by default.** Third-party plugin code under
  `res://addons/` and `res://script_templates/` is now excluded by default
  (`Config.ignore_addons`, on by default). Pass `--include-addons` on the CLI
  or set `ignore_addons = false` in config to audit it. Editor/plugin code is
  loaded by the engine or referenced by `class_name`, so auditing it produced
  overwhelming false positives.
- **`class_name` reference tracking in `UNUSED_SCRIPT`.** A script registered
  with `class_name X` is now considered referenced when `X` appears via
  `extends X`, a typed variable, `X.new()`, or a scene node `type="X"` — not
  only by its `res://` path. Removes the systematic false positives for data
  classes and custom-node scripts.
- **`application/config/icon` parsing.** The project icon (e.g. `icon.svg`) is
  now recognised as a referenced asset and no longer flagged
  `UNUSED_ASSET_CANDIDATE`. Stored on `ProjectSummary.icon` (not serialised).

### Changed
- `__version__` bumped to `"0.8.2"`.

### Notes
- `.gdoctor.toml` / `--config` files use **root-level** keys (no
  `[tool.gdoctor]` prefix); only `pyproject.toml` uses the namespaced table.
  This was always the case but is now documented explicitly in the README.

---

## [0.8.1] — 2026-06-01

### Fixed
- **`BROKEN_SIGNAL_CONNECTION`** details message rendered the literal text
  `{method}` instead of the actual method name (a missing f-string prefix).
- Terminal `scan` text output used the box-drawing glyphs `─`, `→`, `—`, and
  `✔`, which garbled to replacement characters on CP949 / GBK consoles under
  `errors="replace"`.  These now fall back to ASCII (`-`, `-> `, `-`, `OK`) on
  narrow-encoding terminals, matching the existing severity-icon fallback.
  File output (`--output`) is always UTF-8 and keeps the Unicode glyphs.

### Changed
- `__version__` bumped to `"0.8.1"`.

---

## [0.8.0] — 2026-06-01

### Added
- **`UNUSED_SCRIPT`** (WARNING): flags ``.gd`` files that are not referenced by
  any ``ext_resource`` declaration, static ``load()``/``preload()`` call, or
  autoload entry.  Known false-positive sources noted in ``details``: scripts
  used as base classes via string ``extends``, editor tool scripts, and
  dynamically loaded scripts.
- **`UNUSED_AUTOLOAD`** (WARNING): warns when an autoload name declared in
  ``project.godot`` does not appear as a whole word in any ``.gd`` file.
  Compiles per-name regex patterns once; exits early when all autoloads are
  found (O(scripts × unresolved_autoloads)).  Known false-negatives: name found
  only in comments/strings still counts as "used".

### Changed
- `__version__` bumped to `"0.8.0"`.

---

## [0.7.0] — 2026-06-01

### Added
- **`BROKEN_SIGNAL_CONNECTION`** (WARNING): detects signal connections in `.tscn`
  files where the target `method` is not defined in the receiving node's directly-
  attached GDScript file.
  - Parses `[connection signal=... from=... to=... method=...]` headers.
  - Builds a `node_path → script` map from `[node ...]` and `[ext_resource ...]`
    headers within each scene.
  - `uid://` paths in ext_resources are resolved via the existing `uid_map`.
  - False-positive guards: skip if target node has no script, script is
    unreadable, or the `to` node path is not found in the scene.
  - Known limitation: inherited methods (from `extends`) are not followed;
    suppress with `severity.BROKEN_SIGNAL_CONNECTION = "none"` in config.
- `_parse_scene_for_connections()` internal helper in `checks.py`.

### Changed
- `__version__` bumped to `"0.7.0"`.

---

## [0.6.0] — 2026-06-01

### Added
- **`UNDEFINED_INPUT_ACTION`** (WARNING): warns when a GDScript file calls
  `Input.is_action_pressed()`, `Input.is_action_just_pressed()`,
  `Input.is_action_just_released()`, `Input.get_action_strength()`,
  `Input.get_action_raw_strength()`, `Input.action_press()`, or
  `Input.action_release()` with a static string literal that is not declared in
  the `[input]` section of `project.godot`.
  - Built-in Godot actions (`ui_*` prefix) are excluded from the check.
  - Dynamic expressions / variables are silently skipped (no false positives).
  - `ProjectSummary.input_actions` (internal `set[str]`, not serialised):
    action names collected from `[input]` section during parsing.
  - `ProjectIndex.input_action_refs` (internal `list[tuple[str, str]]`, not
    serialised): `(action_name, source_file)` pairs from GDScript scanning.
- `extract_input_action_refs()` in `gdscript.py`: new public function that
  extracts static action-name string literals from the supported Input API.

### Changed
- `__version__` bumped to `"0.6.0"`.

---

## [0.5.0] — 2026-06-01

### Added
- **`scan --format sarif`**: SARIF 2.1.0 output suitable for GitHub Code
  Scanning (`github/codeql-action/upload-sarif`).
  - `ruleId` ← `issue.code`; `level` ← `error`/`warning`/`note`
  - `physicalLocation.uri` with `%SRCROOT%` base; backslashes normalised
  - Rules deduplicated and sorted by `id`; results sorted by `(ruleId, uri,
    message)` for determinism
- **`action.yml`**: composite GitHub Action that installs
  `godot-project-doctor`, runs `scan --format sarif`, and optionally uploads
  to GitHub Code Scanning.  Inputs: `project-path`, `fail-on`, `sarif-output`,
  `upload-sarif`, `extra-args`.

### Changed
- `__version__` bumped to `"0.5.0"`.

---

## [0.4.0] — 2026-06-01

### Added
- **Config file support** (`config.py`, Phase 2):
  - Reads `[tool.gdoctor]` from `pyproject.toml` or `.gdoctor.toml` in the
    project root.  Keys: `large_texture_dim`, `large_audio_bytes`, `ignore`
    (glob list), `severity` (per-code override table), `baseline` (list of
    `{code, file, message}` fingerprints to suppress).
  - `load_config(project_root, config_path=None)` — TOML loader with
    graceful fallback to defaults on parse errors.
  - `apply_config(index, config)` — post-scan filter: ignore globs, baseline
    suppression, severity overrides (including `"none"` to fully suppress).
- **CLI exit-code policy** (`scan` subcommand):
  - `--fail-on {error,warning,info,none}` (default `error`): sets the minimum
    severity that causes exit code 1.
  - `--strict`: shorthand for `--fail-on warning`.
  - `--config <path>`: explicit TOML config file.
  - `--no-config`: ignore all config files and use built-in defaults.
- `scan()` accepts an optional `Config` parameter; threshold values
  (`large_texture_dim`, `large_audio_bytes`) are taken from it when supplied.
- `run_all_checks()` accepts an optional `Config` parameter for thresholds.

### Changed
- `__version__` bumped to `"0.4.0"`.

---

## [0.3.0] — 2026-06-01  *(schema 1.2)*

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

## [0.2.1] — 2026-06-01  *(schema 1.1 unchanged)*

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

## [0.2.0] — 2026-06-01

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

## [0.1.0] — 2026-06-01  *(initial release)*

### Added
- `gdoctor scan` command with `text`, `json`, `markdown` output formats.
- `MISSING_EXT_RESOURCE`, `LARGE_TEXTURE`, `LARGE_AUDIO`, `UNUSED_ASSET_CANDIDATE`,
  `NO_EXPORT_PRESETS` checks.
- `project.godot` metadata parsing.
- Dependency graph builder and Mermaid/text renderers.

[Unreleased]: https://github.com/chamcat97/godot-project-doctor/compare/v0.8.3...HEAD
[0.8.3]: https://github.com/chamcat97/godot-project-doctor/compare/v0.8.2...v0.8.3
[0.8.2]: https://github.com/chamcat97/godot-project-doctor/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/chamcat97/godot-project-doctor/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/chamcat97/godot-project-doctor/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/chamcat97/godot-project-doctor/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/chamcat97/godot-project-doctor/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/chamcat97/godot-project-doctor/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/chamcat97/godot-project-doctor/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/chamcat97/godot-project-doctor/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/chamcat97/godot-project-doctor/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/chamcat97/godot-project-doctor/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/chamcat97/godot-project-doctor/releases/tag/v0.1.0
