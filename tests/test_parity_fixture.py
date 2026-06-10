"""CLI side of the parity harness.

Scans the committed fixture project (tests/parity/fixture_project) through the
library API and asserts the findings match tests/parity/expected.json — the
same golden file the CI parity job checks the GDScript editor addon against.
If this test fails after a deliberate check change, update expected.json AND
port the change to godot-addon/addons/godot_project_doctor/checks.gd.
"""

from __future__ import annotations

import json
from pathlib import Path

from godot_project_doctor.config import Config, apply_config
from godot_project_doctor.scanner import scan

PARITY_DIR = Path(__file__).parent / "parity"
FIXTURE = PARITY_DIR / "fixture_project"
EXPECTED = PARITY_DIR / "expected.json"


def _normalize(severity: str, code: str, file: str | None) -> tuple[str, str, str]:
    # Keep in sync with tests/parity/compare.py::normalize.
    f = (file or "").replace("\\", "/")
    if f.startswith("res://"):
        f = f[len("res://") :]
    if code == "DUPLICATE_UID":
        f = ""
    return (severity, code, f)


def test_cli_matches_parity_golden() -> None:
    cfg = Config()  # built-in defaults, same as a config-less `gdoctor scan`
    index = scan(FIXTURE, cfg)
    index.issues = apply_config(index, cfg)

    got = sorted(_normalize(i.severity.value, i.code, i.file) for i in index.issues)
    want = sorted(
        _normalize(e["severity"], e["code"], e["file"])
        for e in json.loads(EXPECTED.read_text(encoding="utf-8"))
    )
    assert got == want
