"""CLI entry point for godot-project-doctor."""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import click

from godot_project_doctor import __version__
from godot_project_doctor.config import Config, apply_config, load_config
from godot_project_doctor.context import render_context_markdown
from godot_project_doctor.graph import build_graph, render_mermaid_graph, render_text_graph
from godot_project_doctor.models import Severity
from godot_project_doctor.reporter import (
    build_report,
    render_json,
    render_markdown,
    render_sarif,
    render_text,
)
from godot_project_doctor.scanner import GodotProjectError, scan

# Severity rank used by --fail-on exit-code logic (higher = more severe).
_SEV_RANK: dict[str, int] = {
    Severity.ERROR: 3,
    Severity.WARNING: 2,
    Severity.INFO: 1,
}
# --fail-on value → minimum severity rank that triggers exit 1.
_FAIL_ON_RANK: dict[str, int] = {
    "error": 3,
    "warning": 2,
    "info": 1,
    "none": 0,
}


def _ensure_utf8_errors_replace() -> None:
    """Reconfigure stdout/stderr to use errors='replace' on narrow-encoding terminals.

    On Windows terminals using CP949, GBK, or other encodings that cannot
    represent all Unicode code points, writing non-encodable characters would
    raise ``UnicodeEncodeError``.  Switching to ``errors='replace'`` substitutes
    those characters with ``?`` instead of crashing.

    File output (via ``--output``) is unaffected: those paths always write
    bytes explicitly as UTF-8.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            with contextlib.suppress(Exception):
                stream.reconfigure(errors="replace")


def _compute_exit_code(issues: list, fail_on: str) -> int:
    """Return 1 if any issue meets the *fail_on* threshold, else 0."""
    threshold = _FAIL_ON_RANK.get(fail_on, 3)
    if threshold == 0:
        return 0
    for issue in issues:
        if _SEV_RANK.get(issue.severity, 0) >= threshold:
            return 1
    return 0


@click.group()
@click.version_option(version=__version__, prog_name="gdoctor")
def main() -> None:
    """Godot Project Doctor - a static auditor for Godot 4 projects."""
    _ensure_utf8_errors_replace()


@main.command("scan")
@click.argument("project_path", type=click.Path(path_type=Path))
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(["text", "json", "markdown", "sarif"], case_sensitive=False),
    default="text",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path, writable=True),
    default=None,
    help="Write output to this file instead of stdout.",
)
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    default=None,
    help="Explicit path to a TOML config file.",
)
@click.option(
    "--no-config",
    is_flag=True,
    default=False,
    help="Ignore all config files and use built-in defaults.",
)
@click.option(
    "--fail-on",
    "fail_on",
    type=click.Choice(["error", "warning", "info", "none"], case_sensitive=False),
    default="error",
    show_default=True,
    help="Minimum severity that causes a non-zero exit code.",
)
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Shorthand for --fail-on warning.",
)
@click.option(
    "--include-addons",
    is_flag=True,
    default=False,
    help="Also audit code under res://addons/ (ignored by default).",
)
def scan_cmd(
    project_path: Path,
    fmt: str,
    output: Path | None,
    config_path: Path | None,
    no_config: bool,
    fail_on: str,
    strict: bool,
    include_addons: bool,
) -> None:
    """Scan a Godot project and report issues."""
    if strict:
        fail_on = "warning"

    # Load config (unless suppressed)
    if no_config:
        cfg: Config | None = Config()  # pure defaults
    else:
        cfg = load_config(project_path.resolve(), config_path)

    if include_addons and cfg is not None:
        cfg.ignore_addons = False

    try:
        index = scan(project_path, cfg)
    except NotADirectoryError as exc:
        click.echo(click.style(f"Error: {exc}", fg="red", bold=True), err=True)
        sys.exit(2)
    except GodotProjectError as exc:
        click.echo(click.style(f"Error: {exc}", fg="red", bold=True), err=True)
        sys.exit(1)

    # Apply post-processing: ignore / severity override / baseline
    if cfg is not None:
        index.issues = apply_config(index, cfg)

    report = build_report(index)

    if fmt == "json":
        text = render_json(report, output)
        if not output:
            click.echo(text)
    elif fmt == "markdown":
        text = render_markdown(report, output)
        if not output:
            click.echo(text)
    elif fmt == "sarif":
        text = render_sarif(report, output)
        if not output:
            click.echo(text)
    else:
        if output:
            render_text(report, output)
        else:
            render_text(report)

    if output:
        click.echo(click.style(f"Report written to {output}", fg="green"), err=True)

    code = _compute_exit_code(index.issues, fail_on)
    if code:
        sys.exit(code)


@main.command("graph")
@click.argument("project_path", type=click.Path(path_type=Path))
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(["text", "mermaid"], case_sensitive=False),
    default="text",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path, writable=True),
    default=None,
    help="Write output to this file instead of stdout.",
)
def graph_cmd(project_path: Path, fmt: str, output: Path | None) -> None:
    """Generate a dependency graph from parsed Godot text resources."""
    try:
        index = scan(project_path)
    except NotADirectoryError as exc:
        click.echo(click.style(f"Error: {exc}", fg="red", bold=True), err=True)
        sys.exit(2)
    except GodotProjectError as exc:
        click.echo(click.style(f"Error: {exc}", fg="red", bold=True), err=True)
        sys.exit(1)

    graph = build_graph(index)
    text = render_mermaid_graph(graph) if fmt == "mermaid" else render_text_graph(graph)

    if output:
        output.write_text(text, encoding="utf-8")
        click.echo(click.style(f"Graph written to {output}", fg="green"), err=True)
    else:
        click.echo(text, nl=False)


@main.command("context")
@click.argument("project_path", type=click.Path(path_type=Path))
@click.option(
    "--issue",
    "-i",
    "issue_text",
    default="",
    show_default=False,
    help='Free-text description of the problem (e.g. "game crashes on mobile").',
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path, writable=True),
    default=None,
    help="Write the report to this file instead of stdout.",
)
def context_cmd(project_path: Path, issue_text: str, output: Path | None) -> None:
    """Generate an AI-friendly Markdown context report."""
    try:
        index = scan(project_path)
    except NotADirectoryError as exc:
        click.echo(click.style(f"Error: {exc}", fg="red", bold=True), err=True)
        sys.exit(2)
    except GodotProjectError as exc:
        click.echo(click.style(f"Error: {exc}", fg="red", bold=True), err=True)
        sys.exit(1)

    text = render_context_markdown(index, issue_text)

    if output:
        output.write_text(text, encoding="utf-8")
        click.echo(click.style(f"Context report written to {output}", fg="green"), err=True)
    else:
        click.echo(text, nl=False)
