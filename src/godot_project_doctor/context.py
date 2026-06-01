"""AI-friendly context report for Godot projects.

Produces a self-contained Markdown document that a developer can paste
directly into ChatGPT, Codex, or another coding agent.  No external API
calls are made at any point.
"""

from __future__ import annotations

from godot_project_doctor.graph import build_graph
from godot_project_doctor.models import Issue, ProjectIndex, Severity

# ── Issue-code constants shared with checks.py ────────────────────────────────
_CODE_CIRCULAR = "CIRCULAR_DEPENDENCY"
_CODE_MISSING = "MISSING_EXT_RESOURCE"
_CODE_LARGE_TEX = "LARGE_TEXTURE"
_CODE_LARGE_AUDIO = "LARGE_AUDIO"
_CODE_UNUSED = "UNUSED_ASSET_CANDIDATE"
_CODE_NO_EXPORT = "NO_EXPORT_PRESETS"
_CODE_UNDEF_INPUT = "UNDEFINED_INPUT_ACTION"

_SEVERITY_ICON = {
    Severity.ERROR: "✖",
    Severity.WARNING: "⚠",
    Severity.INFO: "ℹ",
}

# Maximum items shown in any repeated list before we add a "… and N more" line
_LIST_CAP = 10


# ── Internal helpers ──────────────────────────────────────────────────────────


def _by_severity(issues: list[Issue], severity: Severity) -> list[Issue]:
    return [i for i in issues if i.severity == severity]


def _by_code(issues: list[Issue], *codes: str) -> list[Issue]:
    return [i for i in issues if i.code in codes]


def _capped(items: list, cap: int = _LIST_CAP) -> tuple[list, int]:
    """Return the first *cap* items and the number omitted."""
    return items[:cap], max(0, len(items) - cap)


# ── Investigation focus (deterministic rules) ─────────────────────────────────

_MOBILE_KEYWORDS = frozenset(["mobile", "phone", "android", "ios", "tablet", "handheld"])
# "load" was intentionally removed: it is too common in non-error contexts
# (e.g. "slow to load", "load time") and caused incorrect focus selection.
_MISSING_KEYWORDS = frozenset(["missing", "broken", "reference", "ref", "not found"])


def _investigation_focus(index: ProjectIndex, issue_text: str) -> list[str]:
    """Return Markdown bullet strings based purely on *issue_text* and *index*.

    No LLM calls are made.  Rules, in priority order:

    1. **mobile** keywords  → focus on large textures, large audio, export presets.
    2. **missing / broken / reference** keywords → focus on MISSING_EXT_RESOURCE.
    3. **default** → highest-severity issues first, or a "no issues" note.
    """
    lower = issue_text.lower()
    bullets: list[str] = []

    # ── Rule 1: mobile ────────────────────────────────────────────────────────
    if any(kw in lower for kw in _MOBILE_KEYWORDS):
        large_tex = _by_code(index.issues, _CODE_LARGE_TEX)
        large_audio = _by_code(index.issues, _CODE_LARGE_AUDIO)
        no_export = _by_code(index.issues, _CODE_NO_EXPORT)

        if large_tex:
            bullets.append(
                f"**Large textures ({len(large_tex)} file(s)) detected** — oversized textures are a "
                "leading cause of GPU memory pressure and long load times on mobile. "
                "Downscale to ≤ 1024 px and enable mipmaps where possible."
            )
        else:
            bullets.append(
                "No large textures detected — texture sizes look appropriate for mobile."
            )

        if large_audio:
            bullets.append(
                f"**Large audio files ({len(large_audio)} file(s)) detected** — large audio assets "
                "inflate APK/IPA size and RAM usage. "
                "Consider OGG compression or streaming background music."
            )
        else:
            bullets.append(
                "No oversized audio files detected — audio sizes look acceptable for mobile."
            )

        if no_export:
            bullets.append(
                "**No export presets configured** — mobile builds require platform-specific presets "
                "(Android / iOS). Set them up in the Godot editor before attempting a build."
            )
        else:
            bullets.append(
                "Export presets are present — the project has at least one export configuration."
            )

        return bullets

    # ── Rule 2: missing / broken / reference ──────────────────────────────────
    if any(kw in lower for kw in _MISSING_KEYWORDS):
        missing = _by_code(index.issues, _CODE_MISSING)
        if missing:
            bullets.append(
                f"**{len(missing)} missing external resource(s) found** — these are the most "
                "likely direct cause of the reported issue. Each file must exist at the exact "
                "`res://` path referenced inside the scene or resource file."
            )
            shown, omitted = _capped(missing)
            for issue in shown:
                loc = f"`{issue.file}` " if issue.file else ""
                ref_path = issue.message.split(": ", 1)[-1]
                bullets.append(f"  - {loc}→ `{ref_path}`")
            if omitted:
                bullets.append(
                    f"  - … and {omitted} more (see **Missing References** section above)."
                )
        else:
            bullets.append(
                "No missing external resources found in text-format files. "
                "The problem may involve: binary `.res`/`.scn` files (not parsed), "
                "dynamic `load()` / `preload()` calls in GDScript, "
                "or path case-sensitivity differences on Linux servers."
            )
        return bullets

    # ── Rule 3: default — highest severity first ──────────────────────────────
    errors = _by_severity(index.issues, Severity.ERROR)
    warnings = _by_severity(index.issues, Severity.WARNING)

    if errors:
        bullets.append(
            f"**Start with the {len(errors)} ERROR-level issue(s)** — broken or missing "
            "references will cause runtime failures and should be resolved first."
        )
    if warnings:
        bullets.append(
            f"**Then review the {len(warnings)} WARNING-level issue(s)** — large assets and "
            "unreferenced files may affect performance or indicate stale content."
        )
    if not errors and not warnings:
        bullets.append(
            "No critical issues detected by static analysis. "
            "The reported problem may lie in GDScript logic, node configuration, "
            "runtime-only paths, or binary resources that are not parsed by this tool."
        )

    return bullets


# ── Main renderer ─────────────────────────────────────────────────────────────


def render_context_markdown(index: ProjectIndex, issue_text: str = "") -> str:
    """Build and return an AI-friendly Markdown context report.

    Parameters
    ----------
    index:
        A fully-populated :class:`~godot_project_doctor.models.ProjectIndex`
        (as returned by :func:`~godot_project_doctor.scanner.scan`).
    issue_text:
        Free-text description of the problem the developer is investigating.
        Used only by :func:`_investigation_focus` to select which sections to
        emphasise; never sent to any external service.
    """
    out: list[str] = []

    def _h(level: int, title: str) -> None:
        out.append(f"{'#' * level} {title}")
        out.append("")

    def _line(text: str = "") -> None:
        out.append(text)

    # ── Header ────────────────────────────────────────────────────────────────
    _h(1, "Godot Project Context Report")
    _line("> Generated by **godot-project-doctor** — paste this into your AI assistant.")
    _line()

    # ── Reported issue (optional) ─────────────────────────────────────────────
    if issue_text.strip():
        _h(2, "Reported Issue")
        _line(f"> {issue_text.strip()}")
        _line()

    # ── 1. Project summary ────────────────────────────────────────────────────
    _h(2, "Project Summary")
    name = index.summary.project_name or "_(unnamed)_"
    main_scene = f"`{index.summary.main_scene}`" if index.summary.main_scene else "_(not set)_"
    _line("| Field | Value |")
    _line("|---|---|")
    _line(f"| Project name | {name} |")
    _line(f"| Project root | `{index.project_root}` |")
    _line(f"| Main scene | {main_scene} |")
    if index.summary.godot_version_hint:
        _line(f"| Godot version | {index.summary.godot_version_hint} |")
    _line()

    # ── 4. Autoloads ──────────────────────────────────────────────────────────
    if index.summary.autoloads:
        _h(2, "Autoloads")
        for aname, apath in sorted(index.summary.autoloads.items()):
            _line(f"- **{aname}**: `{apath}`")
        _line()

    # ── 2. File counts ────────────────────────────────────────────────────────
    _h(2, "File Counts")
    s = index.file_stats
    _line("| Type | Count |")
    _line("|---|---|")
    _line(f"| Scenes (`.tscn`) | {s.scenes} |")
    _line(f"| Resources (`.tres`) | {s.resources} |")
    _line(f"| Scripts (`.gd`) | {s.scripts} |")
    _line(f"| Shaders | {s.shaders} |")
    _line(f"| Images | {s.images} |")
    _line(f"| Audio | {s.audio} |")
    _line(f"| Other | {s.other} |")
    _line(f"| **Total** | **{s.total}** |")
    _line()

    # ── 5. Issues grouped by severity ────────────────────────────────────────
    _h(2, "Issues")
    counts = index.issue_counts
    total_issues = sum(counts.values())

    if total_issues == 0:
        _line("_No issues found._")
        _line()
    else:
        _line(f"- ✖ **Errors:** {counts.get('ERROR', 0)}")
        _line(f"- ⚠ **Warnings:** {counts.get('WARNING', 0)}")
        _line(f"- ℹ **Info:** {counts.get('INFO', 0)}")
        _line()

        for sev in (Severity.ERROR, Severity.WARNING, Severity.INFO):
            bucket = _by_severity(index.issues, sev)
            if not bucket:
                continue
            icon = _SEVERITY_ICON[sev]
            _h(3, f"{icon} {sev.value}")
            shown, omitted = _capped(bucket)
            for issue in shown:
                loc = f"`{issue.file}` — " if issue.file else ""
                _line(f"- `{issue.code}` {loc}{issue.message}")
                if issue.details:
                    _line(f"  > {issue.details}")
            if omitted:
                _line(f"- … and {omitted} more issue(s) of this severity.")
            _line()

    # ── 6. Missing references ─────────────────────────────────────────────────
    _h(2, "Missing References")
    missing = _by_code(index.issues, _CODE_MISSING)
    if missing:
        shown, omitted = _capped(missing)
        for issue in shown:
            loc = f"`{issue.file}`" if issue.file else ""
            ref_path = issue.message.split(": ", 1)[-1]
            _line(f"- {loc} → `{ref_path}`")
            if issue.details:
                _line(f"  > {issue.details}")
        if omitted:
            _line(f"- … and {omitted} more.")
    else:
        _line("_No missing references detected._")
    _line()

    # ── 7. Large assets ───────────────────────────────────────────────────────
    _h(2, "Large Assets")
    large = _by_code(index.issues, _CODE_LARGE_TEX, _CODE_LARGE_AUDIO)
    if large:
        for issue in large:
            _line(f"- `{issue.file}`: {issue.message}")
            if issue.details:
                _line(f"  > {issue.details}")
    else:
        _line("_No large assets detected._")
    _line()

    # ── 8. Unused asset candidates ────────────────────────────────────────────
    _h(2, "Unused Asset Candidates")
    unused = _by_code(index.issues, _CODE_UNUSED)
    if unused:
        shown, omitted = _capped(unused)
        for issue in shown:
            _line(f"- `{issue.file}`")
        if omitted:
            _line(f"- … and {omitted} more.")
    else:
        _line("_No unused assets detected._")
    _line()

    # ── 9. Dependency graph summary ───────────────────────────────────────────
    _h(2, "Dependency Graph Summary")
    graph = build_graph(index)
    total_sources = len(graph)
    total_edges = sum(len(deps) for deps in graph.values())
    unique_targets: set[str] = set()
    for deps in graph.values():
        unique_targets.update(deps)

    _line(f"- **Source files with dependencies:** {total_sources}")
    _line(f"- **Total dependency edges:** {total_edges}")
    _line(f"- **Unique referenced targets:** {len(unique_targets)}")
    _line()

    if graph:
        _line("Top-level sources:")
        _line()
        sources_shown, sources_omitted = _capped(sorted(graph), cap=15)
        for src in sources_shown:
            deps = sorted(graph[src])
            shown_deps = deps[:5]
            dep_str = ", ".join(f"`{d}`" for d in shown_deps)
            if len(deps) > 5:
                dep_str += f", … (+{len(deps) - 5} more)"
            _line(f"- `{src}` → {dep_str}")
        if sources_omitted:
            _line(f"- … and {sources_omitted} more source file(s).")
        _line()

    # ── 10. Suggested investigation focus ────────────────────────────────────
    _h(2, "Suggested Investigation Focus")
    for bullet in _investigation_focus(index, issue_text):
        _line(f"- {bullet}")
    _line()

    # ── Footer ────────────────────────────────────────────────────────────────
    _line("---")
    _line()
    _line(
        "_Report generated by [godot-project-doctor](https://github.com/chamcat97/godot-project-doctor). "
        "No AI APIs were called during report generation._"
    )

    return "\n".join(out) + "\n"
