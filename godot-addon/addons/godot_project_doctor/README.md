# Godot Project Doctor (editor addon)

One-click static auditor for **Godot 4** projects, right inside the editor. A
native GDScript port of the [`godot-project-doctor`](https://github.com/chamcat97/godot-project-doctor)
CLI — **no Python, no dependencies, no network**. Pure static analysis.

![bottom panel](https://img.shields.io/badge/panel-bottom-blue) ![Godot](https://img.shields.io/badge/Godot-4.2%2B-478cbf)

## Install

Copy the `addons/godot_project_doctor/` folder into your project's `addons/`
directory, then enable it in **Project ▸ Project Settings ▸ Plugins**.

```
your_project/
└── addons/
    └── godot_project_doctor/   ← copy this folder
```

## Use

1. Open the **🩺 Project Doctor** panel at the bottom of the editor.
2. Click **Scan Project**.
3. Findings appear grouped by severity. **Double-click a row** to open the
   offending scene/script.

- **Errors / Warnings / Info** checkboxes filter the list without rescanning.
- `res://addons/` and `res://script_templates/` are always excluded — the doctor
  audits your code, not third-party plugins.

## What it detects

| Severity | Checks |
|---|---|
| **ERROR** | `MISSING_MAIN_SCENE`, `MISSING_AUTOLOAD`, `MISSING_EXT_RESOURCE`, `DANGLING_EXT_RESOURCE`, `DUPLICATE_CLASS_NAME`, `CIRCULAR_DEPENDENCY` |
| **WARNING** | `BROKEN_SIGNAL_CONNECTION`, `UNDEFINED_INPUT_ACTION`, `UNUSED_SCRIPT`, `UNUSED_AUTOLOAD`, `UNUSED_ASSET_CANDIDATE`, `LARGE_TEXTURE`, `LARGE_AUDIO`, `DUPLICATE_UID` |
| **INFO** | `NO_MAIN_SCENE`, `NO_EXPORT_PRESETS` |

Because it runs *inside* the editor, `uid://` references are resolved through
the engine's `ResourceUID`, and texture sizes are read directly via `Image` —
both more accurate than the offline CLI and with zero extra dependencies.

## CLI vs addon

| | CLI (`gdoctor`) | This addon |
|---|---|---|
| Runs in | terminal / CI | the Godot editor |
| Needs | Python 3.11+ | nothing |
| Output | text / JSON / SARIF / Markdown | bottom-panel list |
| Best for | CI gates, AI agents, pre-commit | quick in-editor checks |

The two share the same check definitions; keep them in sync when adding checks.

## Notes / limitations (v1)

- Static analysis only — dynamically loaded assets (`load(variable)`),
  inherited signal-handler methods, and autoloads accessed only via
  `get_node('/root/...')` or C#/GDExtension can produce false positives.
- Config files (`.gdoctor.toml`), severity overrides, baselines, and report
  export are not yet wired into the addon (planned). Use the CLI for those.
