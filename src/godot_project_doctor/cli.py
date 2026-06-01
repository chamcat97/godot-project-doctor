"""CLI entry point for godot-project-doctor."""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import click

from godot_project_doctor import __version__
from godot_project_doctor.context import render_context_markdown
from godot_project_doctor.graph import build_graph, render_mermaid_graph, render_text_graph
from godot_project_doctor.reporter import build_report, render_json, render_markdown, render_text
from godot_project_doctor.scanner import GodotProjectError, scan


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
    type=click.Choice(["text", "json", "markdown"], case_sensitive=False),
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
def scan_cmd(project_path: Path, fmt: str, output: Path | None) -> None:
    """Scan a Godot project and report issues."""
    try:
        index = scan(project_path)
    except NotADirectoryError as exc:
        click.echo(click.style(f"Error: {exc}", fg="red", bold=True), err=True)
        sys.exit(2)
    except GodotProjectError as exc:
        click.echo(click.style(f"Error: {exc}", fg="red", bold=True), err=True)
        sys.exit(1)

    report = build_report(index)

    if fmt == "json":
        text = render_json(report, output)
        if not output:
            click.echo(text)
    elif fmt == "markdown":
        text = render_markdown(report, output)
        if not output:
            click.echo(text)
    else:
        if output:
            render_text(report, output)
        else:
            render_text(report)

    if output:
        click.echo(click.style(f"Report written to {output}", fg="green"), err=True)

    # Exit code 1 if there are any ERRORs
    if index.issue_counts.get("ERROR", 0) > 0:
        sys.exit(1)


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
