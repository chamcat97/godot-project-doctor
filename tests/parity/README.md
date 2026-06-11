# Parity harness — CLI ⇔ editor addon

The same 14 checks exist twice: in Python (`src/godot_project_doctor/checks.py`,
shipped as the `gdoctor` CLI) and in GDScript
(`godot-addon/addons/godot_project_doctor/checks.gd`, the editor addon). This
harness keeps the two implementations from drifting apart.

## How it works

```
fixture_project/   a committed Godot project with deterministic, intentional issues
expected.json      the golden list of findings — single source of truth
```

Both implementations scan the **same fixture** and must match the **same golden
file** (so CLI ⇔ addon equality follows transitively):

| Side | Where it runs | Entry point |
|---|---|---|
| CLI | every `pytest` run, all platforms | `tests/test_parity_fixture.py` |
| addon | CI `parity` job (headless Godot on Linux) | `run_addon_scan.gd` → `compare.py` |

## Covered checks (13 findings, 12 distinct codes)

`MISSING_MAIN_SCENE`, `CIRCULAR_DEPENDENCY`, `MISSING_EXT_RESOURCE` (×2: scene
+ GDScript `load()`), `DANGLING_EXT_RESOURCE`, `DUPLICATE_CLASS_NAME`,
`BROKEN_SIGNAL_CONNECTION`, `UNDEFINED_INPUT_ACTION`, `UNUSED_SCRIPT`,
`UNUSED_AUTOLOAD`, `UNUSED_ASSET_CANDIDATE`, `DUPLICATE_UID`,
`NO_EXPORT_PRESETS` — plus negative cases (used class_name, used autoload,
declared input action, referenced asset, declared ExtResource id, valid
connection).

Not covered: `LARGE_TEXTURE` / `LARGE_AUDIO` (would need Pillow + big binary
files), `MISSING_AUTOLOAD` / `NO_MAIN_SCENE` (mutually exclusive with the
covered variants; same code paths).

## Normalisation

`compare.py` and the pytest test compare `(severity, code, file)` with:
`None` → `""`, `\` → `/`, `res://` stripped, and **`DUPLICATE_UID`'s file
forced to `""`** (the CLI reports no location; the addon points at the first
claimant so the editor can open it — both correct for their medium).

## Running locally (Windows)

```powershell
# CLI side
pytest tests/test_parity_fixture.py

# addon side (needs a Godot 4.x binary)
$fix = "tests/parity/fixture_project"
gdoctor scan $fix --format json --output "$env:TEMP/cli_report.json" --fail-on none
New-Item -ItemType Directory -Force "$fix/addons" | Out-Null
Copy-Item -Recurse -Force godot-addon/addons/godot_project_doctor "$fix/addons/"
Copy-Item tests/parity/run_addon_scan.gd "$fix/addons/run_addon_scan.gd"
& <godot.exe> --headless --path $fix --script res://addons/run_addon_scan.gd > "$env:TEMP/addon_out.txt" 2>&1
python tests/parity/compare.py --cli "$env:TEMP/cli_report.json" --addon "$env:TEMP/addon_out.txt" --expected tests/parity/expected.json
```

## Maintenance rules

- **Adding/changing a check?** Update BOTH `checks.py` and `checks.gd`, extend
  `fixture_project/` to trigger it, and update `expected.json`.
- **Never open `fixture_project/` in the Godot editor** — it would regenerate
  the duplicated `.uid` sidecars and break the `DUPLICATE_UID` case.
  (Headless `--script` runs are safe: they write nothing.)
- `fixture_project/addons/` is an ephemeral copy made by CI/local runs and is
  git-ignored.
