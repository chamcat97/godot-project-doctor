# godot-project-doctor

[![PyPI version](https://img.shields.io/pypi/v/godot-project-doctor.svg)](https://pypi.org/project/godot-project-doctor/)
[![PyPI downloads](https://img.shields.io/pypi/dm/godot-project-doctor.svg)](https://pypi.org/project/godot-project-doctor/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/chamcat97/godot-project-doctor/actions/workflows/ci.yml/badge.svg)](https://github.com/chamcat97/godot-project-doctor/actions)

A deterministic CLI auditor for [Godot 4](https://godotengine.org/) projects.

`godot-project-doctor` scans a Godot project folder, parses project metadata and resource references, detects common issues, and generates human-readable and machine-readable reports for CI pipelines and AI agents.

> **Not an AI tool.** Pure static analysis — no LLM calls, no network requests. Perfect for GitHub Actions, linters, and AI coding workflows.

---

## What it detects

| Category | Issues |
|---|---|
| **Structural** | Circular dependency cycles, missing main scene, missing autoloads |
| **References** | Missing external resources (scenes, scripts, textures), broken uid:// paths |
| **Scripts** | Undefined input actions, broken signal connections, unused scripts |
| **Assets** | Unused scripts/autoloads, unused asset candidates, oversized textures/audio |

**v0.8.0 additions**: Full `uid://` resolution (sidecar + import + cache), SARIF 2.1.0 output, GitHub Action, config-driven severity overrides, unused-script detection.

---

## Installation

```bash
pip install godot-project-doctor
```

Optional: For texture-size checks, add Pillow:
```bash
pip install "godot-project-doctor[image]"
```

---

## Quick start

```bash
gdoctor scan ./my-game
```

**Output** (text mode, ANSI-safe on Windows CP949):
```
ERROR    MISSING_MAIN_SCENE          project.godot: run/main_scene = res://scenes/Main.tscn (file not found)
ERROR    CIRCULAR_DEPENDENCY         scenes/Player.tscn → scenes/Level.tscn → scenes/Player.tscn
WARNING  BROKEN_SIGNAL_CONNECTION    scenes/UI.tscn:[connection] signal=pressed method=_on_clicked (not found in target script)
WARNING  UNUSED_SCRIPT               player/old_controller.gd (not referenced by any scene/autoload)
INFO     NO_EXPORT_PRESETS           export_presets.cfg not found

Scanned 12 scenes, 18 scripts. 2 errors, 2 warnings, 1 info.
```

---

## Usage

### `scan` — audit for issues

```
gdoctor scan <project_path> [--format text|json|markdown] [--output <path>]
              [--fail-on error|warning|info|none] [--strict]
              [--config <path>] [--no-config]
```

```bash
# Human-readable terminal report
gdoctor scan ./my-godot-game

# JSON report for CI or AI agents
gdoctor scan ./my-godot-game --format json --output report.json

# Markdown report
gdoctor scan ./my-godot-game --format markdown --output report.md

# Fail CI on any WARNING or higher (default: ERROR only)
gdoctor scan ./my-godot-game --strict

# Suppress exit code entirely (always exits 0)
gdoctor scan ./my-godot-game --fail-on none

# Use an explicit config file
gdoctor scan ./my-godot-game --config ci-strict.toml
```

#### Configuration file

`gdoctor` reads `[tool.gdoctor]` from `pyproject.toml`, or `.gdoctor.toml`
in the project root.  Use `--no-config` to ignore all config files.

```toml
# pyproject.toml
[tool.gdoctor]
large_texture_dim  = 1024          # warn on textures > 1024 px (default 2048)
large_audio_bytes  = 5_242_880     # warn on audio > 5 MB (default 10 MB)
ignore             = ["assets/vendor/**", "*.tmp.gd"]

[tool.gdoctor.severity]
UNUSED_ASSET_CANDIDATE = "info"    # downgrade to INFO
NO_EXPORT_PRESETS      = "none"    # suppress entirely

[[tool.gdoctor.baseline]]
code    = "MISSING_EXT_RESOURCE"
file    = "scenes/legacy/Old.tscn"
message = "External resource not found: res://legacy/old.gd"
```

### `graph` — dependency graph

```
gdoctor graph <project_path> [--format text|mermaid] [--output <path>]
```

Builds a directed graph of `source_file → referenced_resource` edges from all
parsed `.tscn`, `.tres`, and `.gd` files.  No Godot engine is invoked.

```bash
# Indented text graph
gdoctor graph ./my-godot-game

# Mermaid flowchart (paste into GitHub Markdown or Mermaid Live)
gdoctor graph ./my-godot-game --format mermaid --output graph.md
```

### `context` — AI-friendly report

```
gdoctor context <project_path> [--issue "<free text>"] [--output <path>]
```

Generates a self-contained Markdown document you can paste into ChatGPT,
Codex, or another coding agent.  The "Suggested Investigation Focus" section
uses deterministic keyword rules — no external API calls are made.

```bash
gdoctor context ./my-godot-game --issue "game crashes on Android"
gdoctor context ./my-godot-game --issue "missing resource on startup" --output ctx.md
```

---

## Output formats (`scan`)

### `text` (default)

ANSI colour-coded terminal output, grouped by severity.  Falls back to ASCII
icons (`[E]`, `[W]`, `[i]`) on narrow-encoding terminals (Windows CP949 / GBK).

### `json`

Stable, machine-readable JSON for CI pipelines and AI code agents:

```json
{
  "schema_version": "1.2",
  "project_root": "/path/to/game",
  "summary": { "project_name": "My Game", "main_scene": "res://scenes/Main.tscn" },
  "file_stats": { "scenes": 3, "scripts": 12 },
  "issue_counts": { "ERROR": 1, "WARNING": 2, "INFO": 1 },
  "refs": [
    {
      "source_file": "scenes/Main.tscn",
      "kind": "ext_resource",
      "type": "Script",
      "uid": "uid://abc123",
      "path": "res://player/player.gd",
      "id": "1",
      "resolved_path": null,
      "resolved_via": null
    }
  ],
  "issues": [ { "code": "MISSING_EXT_RESOURCE", "severity": "ERROR" } ]
}
```

**Schema history**

| Version | Added fields |
|---|---|
| `1.0` | initial |
| `1.1` | `refs[].kind` |
| `1.2` | `refs[].resolved_path`, `refs[].resolved_via` |

`resolved_path` is set when `path` is a `uid://` reference that was resolved
via a `.uid` sidecar, `.import` file, or `uid_cache.bin`.
`resolved_via` is `"uid_sidecar"`, `"import"`, or `"uid_cache"` accordingly.

### `markdown`

Simple Markdown report suitable for GitHub issues or documentation.

### `sarif`

[SARIF 2.1.0](https://sarifweb.azurewebsites.net/) for GitHub Code Scanning:

```bash
gdoctor scan ./my-godot-game --format sarif --output gdoctor.sarif
```

Use the bundled **GitHub Action** to scan and upload in one step:

```yaml
# .github/workflows/godot-doctor.yml
- uses: chamcat97/godot-project-doctor@v0.8.1
  with:
    project-path: .
    fail-on: warning
    upload-sarif: "true"
  permissions:
    security-events: write
```

---

## Checks

| Code | Severity | Description |
|---|---|---|
| `CIRCULAR_DEPENDENCY` | ERROR | A cycle exists in the resource dependency graph (e.g. scene A → scene B → scene A) |
| `MISSING_MAIN_SCENE` | ERROR | `run/main_scene` in `project.godot` points to a file that does not exist |
| `MISSING_AUTOLOAD` | ERROR | An autoload path in `project.godot` does not exist |
| `MISSING_EXT_RESOURCE` | ERROR | A `.tscn`/`.tres`/`.gd` file references a path that does not exist |
| `LARGE_TEXTURE` | WARNING | Raster image exceeds 2048×2048 px (requires Pillow) |
| `LARGE_AUDIO` | WARNING | Audio file is larger than 10 MB |
| `DUPLICATE_UID` | WARNING | Two or more sources claim the same `uid://` for different files |
| `BROKEN_SIGNAL_CONNECTION` | WARNING | A `.tscn` signal connection's `method` is not found in the target node's attached GDScript (inherited methods not checked — false-negatives possible) |
| `UNDEFINED_INPUT_ACTION` | WARNING | GDScript calls `Input.is_action_*()`/`get_action_strength()` with a name not declared in `project.godot` `[input]` (built-in `ui_*` actions excluded) |
| `UNUSED_AUTOLOAD` | WARNING | Autoload singleton name not referenced in any `.gd` file (access via `get_node('/root/...')` or non-GDScript code are false positives) |
| `UNUSED_ASSET_CANDIDATE` | WARNING | Asset not referenced by any parsed scene, resource, or script |
| `UNUSED_SCRIPT` | WARNING | `.gd` file not referenced by any scene, resource, or autoload (scripts used via string `extends` or dynamically loaded are false positives) |
| `NO_MAIN_SCENE` | INFO | `run/main_scene` is not configured (may be intentional for library projects) |
| `NO_EXPORT_PRESETS` | INFO | `export_presets.cfg` is absent |

> **`uid://` paths**: `gdoctor` resolves `uid://` references from three sources —
> `*.uid` sidecar files (highest priority), `*.import` files, and
> `.godot/uid_cache.bin` (best-effort binary parse, fails silently).
> Resolved UIDs are checked for existence and excluded from unused-asset
> candidates.  Unresolvable UIDs are silently skipped to prevent false positives.

> **Note on `UNUSED_ASSET_CANDIDATE`:** Dynamic `load()` calls with variable
> paths cannot be detected by static analysis.  Assets loaded that way will
> still appear as unused candidates.

---

## Development

```bash
# Install dev dependencies (includes mypy, ruff, pytest)
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src tests

# Format
ruff format src tests

# Type-check
mypy src/godot_project_doctor
```

---

## Roadmap

**Completed (v0.2.1 – v0.8.0)**
- ✅ Circular dependency detection
- ✅ Missing resource detection with `uid://` resolution (sidecar, `.import`, `uid_cache.bin`)
- ✅ Config-driven severity overrides and baseline suppression
- ✅ SARIF 2.1.0 output for GitHub Code Scanning
- ✅ GitHub Action for CI/CD integration
- ✅ Input action validation (GDScript `Input.*` vs `project.godot` `[input]`)
- ✅ Signal connection validation (`.tscn` signal → target GDScript method)
- ✅ Unused script and autoload detection

**Future (5.0+)**
- Binary `.res`/`.scn` file support (requires Godot binary format parser)
- GDScript dynamic path heuristics (partial coverage via string concatenation patterns)
- Scene node tree analysis (orphaned nodes, mismatched node types)

---

## License

MIT — see [LICENSE](LICENSE).
