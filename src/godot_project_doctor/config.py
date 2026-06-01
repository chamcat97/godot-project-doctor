"""Configuration loader for godot-project-doctor.

Settings are read from the first file found in this priority order:

1. Path passed to ``--config`` on the CLI.
2. ``[tool.gdoctor]`` table inside ``pyproject.toml`` in the project root.
3. ``.gdoctor.toml`` in the project root.
4. Built-in defaults (no file needed).

File format (TOML)
------------------

.. code-block:: toml

    [tool.gdoctor]
    # Pixel threshold for LARGE_TEXTURE  (default: 2048)
    large_texture_dim = 1024

    # Byte threshold for LARGE_AUDIO  (default: 10 MB)
    large_audio_bytes = 5_242_880   # 5 MB

    # Glob patterns (project-root-relative) to silence entirely
    ignore = ["assets/vendor/**", "*.tmp.gd"]

    # Per-code severity overrides  ("error" | "warning" | "info" | "none")
    [tool.gdoctor.severity]
    UNUSED_ASSET_CANDIDATE = "info"
    NO_EXPORT_PRESETS = "none"      # suppress entirely

    # Known-acceptable issues: a list of {code, file, message} fingerprints
    # Any issue whose (code, file, message) matches a baseline entry is
    # suppressed from output and exit-code calculation.
    [[tool.gdoctor.baseline]]
    code    = "MISSING_EXT_RESOURCE"
    file    = "scenes/legacy/OldScene.tscn"
    message = "External resource not found: res://legacy/old.gd"

Usage inside a standalone ``.gdoctor.toml``
-------------------------------------------

The root table replaces ``[tool.gdoctor]``:

.. code-block:: toml

    large_texture_dim = 1024

    [severity]
    UNUSED_ASSET_CANDIDATE = "info"

    [[baseline]]
    code = "MISSING_EXT_RESOURCE"
    file = "scenes/legacy/OldScene.tscn"
    message = "External resource not found: res://legacy/old.gd"
"""

from __future__ import annotations

import fnmatch
import tomllib
from dataclasses import dataclass, field
from dataclasses import replace as _dc_replace
from pathlib import Path

from godot_project_doctor.models import Issue, ProjectIndex, Severity

# ── Default thresholds (kept in sync with checks.py constants) ────────────────
_DEFAULT_LARGE_TEXTURE_DIM: int = 2048
_DEFAULT_LARGE_AUDIO_BYTES: int = 10 * 1024 * 1024  # 10 MB

# ── Config dataclass ──────────────────────────────────────────────────────────


@dataclass
class BaselineEntry:
    """A single fingerprint to suppress from issue output."""

    code: str
    file: str | None
    message: str


@dataclass
class Config:
    """Runtime configuration derived from the settings file."""

    large_texture_dim: int = _DEFAULT_LARGE_TEXTURE_DIM
    large_audio_bytes: int = _DEFAULT_LARGE_AUDIO_BYTES
    ignore: list[str] = field(default_factory=list)
    severity_overrides: dict[str, str] = field(default_factory=dict)
    baseline: list[BaselineEntry] = field(default_factory=list)
    # Editor/plugin tooling under res://addons/ and res://script_templates/ is
    # excluded by default — its scripts/assets are loaded by the editor or
    # referenced by class_name, which produces overwhelming false positives.
    # Set to False (or pass --include-addons) to audit that code as well.
    ignore_addons: bool = True

    # ── Derived helpers ───────────────────────────────────────────────────────

    def effective_severity(self, code: str, original: Severity) -> Severity | None:
        """Return the effective Severity for *code*, or ``None`` to suppress.

        Returns the original severity when no override is configured.
        """
        raw = self.severity_overrides.get(code, "").lower()
        if not raw:
            return original
        mapping = {
            "error": Severity.ERROR,
            "warning": Severity.WARNING,
            "info": Severity.INFO,
            "none": None,  # suppress
        }
        return mapping.get(raw, original)

    def is_ignored(self, file_path: str | None) -> bool:
        """Return ``True`` when *file_path* matches any ignore glob.

        Files under ``addons/`` are ignored by default (``ignore_addons``).
        """
        if not file_path:
            return False
        norm = file_path.replace("\\", "/")
        if self.ignore_addons and any(
            norm == d or norm.startswith(d + "/") for d in ("addons", "script_templates")
        ):
            return True
        return any(fnmatch.fnmatch(norm, pat) for pat in self.ignore)

    def is_baseline(self, issue: Issue) -> bool:
        """Return ``True`` when *issue* matches a baseline entry."""
        for entry in self.baseline:
            if (
                entry.code == issue.code
                and entry.file == issue.file
                and entry.message == issue.message
            ):
                return True
        return False


# ── Loader ────────────────────────────────────────────────────────────────────


def load_config(
    project_root: Path,
    config_path: Path | None = None,
) -> Config:
    """Load configuration and return a :class:`Config` instance.

    Parameters
    ----------
    project_root:
        The Godot project root.  Used to find ``pyproject.toml`` and
        ``.gdoctor.toml`` when *config_path* is ``None``.
    config_path:
        Explicit path to a TOML config file (``--config`` CLI option).
        When given, the project-root discovery is skipped entirely.

    Returns
    -------
    Config
        Populated config; falls back to all defaults when no file is found.
    """
    raw: dict = {}
    if config_path is not None:
        raw = _read_toml(config_path) or {}
        # A standalone .toml file uses the root table directly
    else:
        # Try pyproject.toml first
        pyproject = project_root / "pyproject.toml"
        if pyproject.is_file():
            data = _read_toml(pyproject) or {}
            raw = data.get("tool", {}).get("gdoctor", {})

        # Fall back to .gdoctor.toml
        if not raw:
            gdoctor_toml = project_root / ".gdoctor.toml"
            if gdoctor_toml.is_file():
                raw = _read_toml(gdoctor_toml) or {}

    return _build_config(raw)


def _read_toml(path: Path) -> dict | None:
    """Read a TOML file and return its contents, or ``None`` on error."""
    try:
        return tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 — any TOML / IO error → use defaults
        return None


def _build_config(raw: dict) -> Config:
    """Build a :class:`Config` from a raw TOML dict (may be empty)."""
    cfg = Config()

    if "large_texture_dim" in raw:
        val = raw["large_texture_dim"]
        if isinstance(val, int) and val > 0:
            cfg.large_texture_dim = val

    if "large_audio_bytes" in raw:
        val = raw["large_audio_bytes"]
        if isinstance(val, int) and val > 0:
            cfg.large_audio_bytes = val

    if "ignore" in raw:
        val = raw["ignore"]
        if isinstance(val, list):
            cfg.ignore = [str(p) for p in val if isinstance(p, str)]

    if "ignore_addons" in raw:
        val = raw["ignore_addons"]
        if isinstance(val, bool):
            cfg.ignore_addons = val

    if "severity" in raw:
        val = raw["severity"]
        if isinstance(val, dict):
            cfg.severity_overrides = {str(k): str(v).lower() for k, v in val.items()}

    if "baseline" in raw:
        val = raw["baseline"]
        if isinstance(val, list):
            for entry in val:
                if not isinstance(entry, dict):
                    continue
                code = entry.get("code", "")
                msg = entry.get("message", "")
                if code and msg:
                    cfg.baseline.append(
                        BaselineEntry(
                            code=str(code),
                            file=str(entry["file"]) if "file" in entry else None,
                            message=str(msg),
                        )
                    )

    return cfg


# ── Post-processing ───────────────────────────────────────────────────────────


def apply_config(index: ProjectIndex, config: Config) -> list[Issue]:
    """Apply ignore rules, severity overrides, and baseline to *index.issues*.

    Returns a new list of :class:`~godot_project_doctor.models.Issue` objects
    with:

    * Issues whose ``file`` matches an ignore glob removed.
    * Issues matching a baseline entry removed.
    * Remaining issues' severity replaced according to ``severity_overrides``
      (issues overridden to ``"none"`` are removed).

    The original ``index.issues`` list is **not** mutated.
    """
    result: list[Issue] = []
    for issue in index.issues:
        # 1. Ignore by file glob
        if config.is_ignored(issue.file):
            continue
        # 2. Baseline suppression
        if config.is_baseline(issue):
            continue
        # 3. Severity override
        new_sev = config.effective_severity(issue.code, issue.severity)
        if new_sev is None:
            continue  # suppressed by "none"
        if new_sev != issue.severity:
            issue = _dc_replace(issue, severity=new_sev)
        result.append(issue)
    return result
