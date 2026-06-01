# godot-project-doctor [![version](https://img.shields.io/badge/version-0.2.0-blue)](https://github.com/chamcat97/godot-project-doctor)

A deterministic CLI auditor for [Godot 4](https://godotengine.org/) projects.

`godot-project-doctor` scans a Godot project folder, parses project metadata and text-based resource references, detects common issues, and generates human-readable and AI-friendly reports.

> **Not an AI tool.** This is a pure static analyser — no LLM API calls, no network requests. It produces structured context that is useful for both humans and AI coding agents.

---

## Features

- Detects missing external resources referenced in `.tscn` / `.tres` files
- Extracts static `preload()` / `load()` / `ResourceLoader.load()` calls from `.gd` scripts
- Flags large textures (>2048 px) using [Pillow](https://pillow.readthedocs.io/) (optional)
- Flags large audio files (>10 MB)
- Identifies unused asset candidates (images/audio not referenced by any scene or script)
- Parses `project.godot` for project name, main scene, autoloads, and Godot version hint
- Outputs `text` (ANSI colour), `json`, and `markdown` reports
- Generates dependency graphs (`text` or `mermaid` format)
- Produces AI-friendly Markdown context reports with deterministic investigation focus
- Exit code `1` on errors, `0` on clean scans
- Works on Windows CP949 / other narrow-encoding terminals

---

## Installation

```bash
pip install godot-project-doctor
```

For texture-size checks, install with the optional Pillow dependency:

```bash
pip install "godot-project-doctor[image]"
```

---

## Usage

### `scan` — audit for issues

```
gdoctor scan <project_path> [--format text|json|markdown] [--output <path>]
```

```bash
# Human-readable terminal report
gdoctor scan ./my-godot-game

# JSON report for CI or AI agents
gdoctor scan ./my-godot-game --format json --output report.json

# Markdown report
gdoctor scan ./my-godot-game --format markdown --output report.md
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
  "schema_version": "1.1",
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
      "id": "1"
    }
  ],
  "issues": [ { "code": "MISSING_EXT_RESOURCE", "severity": "ERROR" } ]
}
```

The `kind` field (added in schema 1.1) is `"ext_resource"` for references
declared in `[ext_resource ...]` headers and `"gdscript"` for references
extracted from static GDScript string literals.

### `markdown`

Simple Markdown report suitable for GitHub issues or documentation.

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
| `UNUSED_ASSET_CANDIDATE` | WARNING | Asset not referenced by any parsed scene, resource, or script |
| `NO_MAIN_SCENE` | INFO | `run/main_scene` is not configured (may be intentional for library projects) |
| `NO_EXPORT_PRESETS` | INFO | `export_presets.cfg` is absent |

> **Note on `uid://` paths:** Godot 4 uses `uid://` UIDs for some references.
> These cannot be resolved without the Godot import cache and are skipped
> rather than generating false-positive errors.

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

- Binary `.res`/`.scn` file support (requires Godot binary format parser)
- GDScript dynamic path heuristics (partial coverage via string concatenation patterns)
- Scene node tree analysis (orphaned nodes, mismatched node types)

---

## License

MIT — see [LICENSE](LICENSE).
