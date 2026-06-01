# godot-project-doctor

A deterministic CLI auditor for [Godot 4](https://godotengine.org/) projects.

`godot-project-doctor` scans a Godot project folder, parses project metadata and text-based resource references, detects common issues, and generates human-readable and AI-friendly reports.

> **Not an AI tool.** This is a pure static analyser — no LLM API calls, no network requests. It produces structured context that is useful for both humans and AI coding agents.

---

## Features

- Detects missing external resources referenced in `.tscn` / `.tres` files
- Flags large textures (>2048px) using [Pillow](https://pillow.readthedocs.io/) (optional)
- Flags large audio files (>10 MB)
- Identifies unused asset candidates (images/audio not referenced by any scene)
- Parses `project.godot` for project name, main scene, and autoloads
- Outputs `text` (Rich), `json`, and `markdown` reports
- Exit code `1` on errors, `0` on clean scans

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

```
gdoctor scan <project_path> [--format text|json|markdown] [--output <path>]
```

### Examples

```bash
# Print a human-readable report
gdoctor scan ./my-godot-game

# Export a JSON report for CI or AI agents
gdoctor scan ./my-godot-game --format json --output report.json

# Markdown report
gdoctor scan ./my-godot-game --format markdown --output report.md
```

---

## Output formats

### `text` (default)

Rich-formatted terminal output with colour-coded issues grouped by severity.

### `json`

Stable, machine-readable JSON suitable for CI pipelines and AI code agents:

```json
{
  "schema_version": "1.0",
  "project_root": "/path/to/game",
  "summary": { "project_name": "My Game", "main_scene": "res://scenes/Main.tscn" },
  "file_stats": { "scenes": 3, "scripts": 12, ... },
  "issue_counts": { "ERROR": 1, "WARNING": 2, "INFO": 1 },
  "refs": [ ... ],
  "issues": [ { "code": "MISSING_EXT_RESOURCE", "severity": "ERROR", ... } ]
}
```

### `markdown`

A simple Markdown report suitable for GitHub issues or documentation.

---

## Checks

| Code | Severity | Description |
|---|---|---|
| `MISSING_EXT_RESOURCE` | ERROR | A `.tscn`/`.tres` file references a path that does not exist |
| `LARGE_TEXTURE` | WARNING | Raster image exceeds 2048×2048 px (requires Pillow) |
| `LARGE_AUDIO` | WARNING | Audio file is larger than 10 MB |
| `UNUSED_ASSET_CANDIDATE` | WARNING | Asset not referenced by any parsed scene or resource |
| `NO_EXPORT_PRESETS` | INFO | `export_presets.cfg` is absent |

> **Note on `UNUSED_ASSET_CANDIDATE`:** GDScript can load assets dynamically via `load()` or `preload()`, so these are candidates, not guaranteed unused.

---

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src tests

# Format
ruff format src tests
```

---

## Roadmap

- `gdoctor graph` — dependency graph of scenes and resources
- `gdoctor context` — generate AI-friendly context summaries
- Binary `.res`/`.scn` file support
- GDScript `load()`/`preload()` path extraction

---

## License

MIT — see [LICENSE](LICENSE).
