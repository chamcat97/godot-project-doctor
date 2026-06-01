"""Output formatters: text (ANSI via click) and JSON."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from godot_project_doctor.models import SCHEMA_VERSION, ProjectIndex, ScanReport, Severity

_SEVERITY_COLORS: dict[Severity, str] = {
    Severity.ERROR: "red",
    Severity.WARNING: "yellow",
    Severity.INFO: "cyan",
}

# UTF-8 icons used in Markdown / JSON file output (always safe).
_SEVERITY_ICONS: dict[Severity, str] = {
    Severity.ERROR: "✖",
    Severity.WARNING: "⚠",
    Severity.INFO: "ℹ",
}

# ASCII fallbacks used for terminal output on narrow-encoding consoles
# (e.g. Windows CP949 / GBK terminals where the Unicode icons cannot be
# encoded and would raise UnicodeEncodeError).
_SEVERITY_ICONS_ASCII: dict[Severity, str] = {
    Severity.ERROR: "[E]",
    Severity.WARNING: "[W]",
    Severity.INFO: "[i]",
}

# Decorative glyphs used in the terminal report.  Like the severity icons,
# these must fall back to ASCII on narrow-encoding consoles (CP949 / GBK),
# otherwise the divider lines / arrows / dashes render as replacement
# characters ("?") under errors="replace".
_GLYPHS_UTF8: dict[str, str] = {"divider": "─", "arrow": "→ ", "dash": "—", "ok": "✔"}
_GLYPHS_ASCII: dict[str, str] = {"divider": "-", "arrow": "-> ", "dash": "-", "ok": "OK"}


def _stdout_is_utf8() -> bool:
    """Return True when the current stdout encoding can represent box-drawing glyphs."""
    enc = (
        (getattr(sys.stdout, "encoding", None) or "ascii").lower().replace("-", "").replace("_", "")
    )
    return enc in ("utf8", "utf8bom")


def _terminal_icons() -> dict[Severity, str]:
    """Return icon map safe for the current stdout encoding."""
    return _SEVERITY_ICONS if _stdout_is_utf8() else _SEVERITY_ICONS_ASCII


def _terminal_glyphs() -> dict[str, str]:
    """Return decorative-glyph map safe for the current stdout encoding."""
    return _GLYPHS_UTF8 if _stdout_is_utf8() else _GLYPHS_ASCII


def build_report(index: ProjectIndex) -> ScanReport:
    """Convert a ProjectIndex into a ScanReport."""
    return ScanReport(
        schema_version=SCHEMA_VERSION,
        project_root=index.project_root,
        summary=index.summary,
        file_stats=index.file_stats,
        issue_counts=index.issue_counts,
        refs=index.refs,
        issues=index.issues,
    )


# JSON


def render_json(report: ScanReport, output: Path | None = None) -> str:
    """Serialise the report to a JSON string, optionally writing to a file."""
    text = json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
    if output:
        output.write_text(text, encoding="utf-8")
    return text


# Text (ANSI via click)


def render_text(report: ScanReport, output: Path | None = None) -> None:
    """Render a human-readable report to the terminal (or a file)."""
    # File output is always written as UTF-8, so it can use the pretty glyphs.
    # Terminal output must respect the console encoding (CP949 / GBK fall back).
    use_utf8 = True if output is not None else _stdout_is_utf8()
    icons = _SEVERITY_ICONS if use_utf8 else _SEVERITY_ICONS_ASCII
    glyphs = _GLYPHS_UTF8 if use_utf8 else _GLYPHS_ASCII
    lines: list[str] = []

    def _echo(msg: str = "", styled: bool = False) -> None:
        if output:
            # Strip ANSI for file output
            lines.append(click.unstyle(msg))
        else:
            click.echo(msg)

    # Summary
    _echo(click.style("=" * 60, fg="blue"))
    _echo(click.style("  Godot Project Doctor", fg="blue", bold=True))
    _echo(click.style("=" * 60, fg="blue"))
    name = report.summary.project_name or "(unnamed)"
    main_scene = report.summary.main_scene or glyphs["dash"]
    autoloads = ", ".join(report.summary.autoloads.keys()) or glyphs["dash"]

    _echo(f"  Project:    {click.style(name, bold=True)}")
    _echo(f"  Root:       {report.project_root}")
    _echo(f"  Main scene: {main_scene}")
    _echo(f"  Autoloads:  {autoloads}")
    if report.summary.godot_version_hint:
        _echo(f"  Godot:      {report.summary.godot_version_hint}")
    _echo()

    # File stats
    _echo(click.style("File Counts", bold=True))
    _echo(click.style(glyphs["divider"] * 30, fg="bright_black"))
    stats = report.file_stats
    for label, val in [
        ("Scenes (.tscn)", stats.scenes),
        ("Resources (.tres)", stats.resources),
        ("Scripts (.gd)", stats.scripts),
        ("Shaders", stats.shaders),
        ("Images", stats.images),
        ("Audio", stats.audio),
        ("Other", stats.other),
    ]:
        _echo(f"  {label:<20} {val}")
    _echo()

    # Issues
    counts = report.issue_counts
    total = sum(counts.values())
    _echo(click.style("Issues", bold=True))
    _echo(click.style(glyphs["divider"] * 30, fg="bright_black"))

    if total == 0:
        _echo(click.style(f"  {glyphs['ok']} No issues found!", fg="green", bold=True))
    else:
        err_str = click.style(f"{counts.get('ERROR', 0)} errors", fg="red", bold=True)
        warn_str = click.style(f"{counts.get('WARNING', 0)} warnings", fg="yellow")
        info_str = click.style(f"{counts.get('INFO', 0)} info", fg="cyan")
        _echo(f"  {err_str}  {warn_str}  {info_str}")

    _echo()

    for severity in (Severity.ERROR, Severity.WARNING, Severity.INFO):
        issues_of_level = [i for i in report.issues if i.severity == severity]
        if not issues_of_level:
            continue

        color = _SEVERITY_COLORS[severity]
        icon = icons[severity]
        _echo(click.style(f"{icon} {severity.value}", fg=color, bold=True))

        for issue in issues_of_level:
            loc = f"{click.style(issue.file, dim=True)}  " if issue.file else ""
            code_str = click.style(issue.code, fg=color)
            _echo(f"  {code_str}  {loc}{issue.message}")
            if issue.details:
                _echo(f"    {click.style(glyphs['arrow'] + issue.details, dim=True)}")
        _echo()

    if output:
        output.write_text("\n".join(lines), encoding="utf-8")


# Markdown


def render_markdown(report: ScanReport, output: Path | None = None) -> str:
    """Render a Markdown report."""
    lines: list[str] = [
        "# Godot Project Doctor Report",
        "",
        f"**Project:** {report.summary.project_name or '(unnamed)'}",
        f"**Root:** `{report.project_root}`",
        f"**Main scene:** `{report.summary.main_scene or '—'}`",
        "",
        "## File Counts",
        "",
        f"- Scenes: {report.file_stats.scenes}",
        f"- Resources: {report.file_stats.resources}",
        f"- Scripts: {report.file_stats.scripts}",
        f"- Shaders: {report.file_stats.shaders}",
        f"- Images: {report.file_stats.images}",
        f"- Audio: {report.file_stats.audio}",
        "",
        "## Issues",
        "",
    ]

    if not report.issues:
        lines.append("_No issues found._")
    else:
        for issue in report.issues:
            icon = _SEVERITY_ICONS[issue.severity]
            loc = f"`{issue.file}` — " if issue.file else ""
            lines.append(f"- {icon} **{issue.severity.value}** `{issue.code}` {loc}{issue.message}")
            if issue.details:
                lines.append(f"  > {issue.details}")

    text = "\n".join(lines)
    if output:
        output.write_text(text, encoding="utf-8")
    return text


# SARIF 2.1.0

# Map godot-project-doctor severity → SARIF level
_SARIF_LEVEL: dict[Severity, str] = {
    Severity.ERROR: "error",
    Severity.WARNING: "warning",
    Severity.INFO: "note",
}

# Tool metadata embedded in every SARIF run
_TOOL_NAME = "godot-project-doctor"
_TOOL_URI = "https://github.com/chamcat97/godot-project-doctor"
_SARIF_VERSION = "2.1.0"
_SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json"
)


def render_sarif(report: ScanReport, output: Path | None = None) -> str:
    """Render the report as a SARIF 2.1.0 document.

    The output is suitable for upload to GitHub Code Scanning via
    ``github/codeql-action/upload-sarif``.

    SARIF mapping
    -------------
    * ``ruleId``  ← ``issue.code``
    * ``level``   ← ``error`` / ``warning`` / ``note``
    * ``text``    ← ``issue.message``
    * ``uri``     ← ``issue.file`` (relative, URI-encoded)
    * ``region``  ← line 1 column 1 (line info not available)

    Determinism
    -----------
    Rules are sorted by ``id``; results are sorted by
    ``(ruleId, uri, message)`` so the output is stable across runs.
    """
    from godot_project_doctor import __version__

    # Collect unique rules (deduplicated by code)
    rule_ids: list[str] = sorted({i.code for i in report.issues})
    rules = [
        {
            "id": code,
            "name": code,
            "shortDescription": {"text": code.replace("_", " ").title()},
            "helpUri": _TOOL_URI,
        }
        for code in rule_ids
    ]

    # Build results, sorted for determinism
    results = []
    for issue in sorted(report.issues, key=lambda i: (i.code, i.file or "", i.message)):
        result: dict = {
            "ruleId": issue.code,
            "level": _SARIF_LEVEL.get(issue.severity, "warning"),
            "message": {"text": issue.message},
        }
        if issue.file:
            # Normalise separators and percent-encode spaces
            uri = issue.file.replace("\\", "/").replace(" ", "%20")
            result["locations"] = [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": uri, "uriBaseId": "%SRCROOT%"},
                        "region": {"startLine": 1, "startColumn": 1},
                    }
                }
            ]
        results.append(result)

    sarif_doc = {
        "$schema": _SARIF_SCHEMA,
        "version": _SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": _TOOL_NAME,
                        "version": __version__,
                        "informationUri": _TOOL_URI,
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }

    text = json.dumps(sarif_doc, indent=2, ensure_ascii=False)
    if output:
        output.write_text(text, encoding="utf-8")
    return text
