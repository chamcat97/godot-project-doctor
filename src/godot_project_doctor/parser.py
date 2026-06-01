"""Parse Godot project.godot config files."""

from __future__ import annotations

import re
from pathlib import Path

from godot_project_doctor.models import ProjectSummary

_SECTION_RE = re.compile(r"^\[(\w+)\]")


def parse_project_godot(project_root: Path) -> ProjectSummary:
    """
    Parse project.godot and extract metadata.

    Uses a simple line-based parser — intentionally not a full INI parser
    since project.godot has Godot-specific syntax.
    """
    config_path = project_root / "project.godot"
    if not config_path.is_file():
        return ProjectSummary()

    summary = ProjectSummary()
    current_section = ""

    try:
        text = config_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return summary

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Version hint lives in a header comment before any section:
        #   ; Engine: Godot 4.x  or  ;Engine: Godot 4.x
        # Must be checked BEFORE the generic comment skip below.
        if line.startswith(";"):
            if summary.godot_version_hint is None and (
                line.startswith("; Engine:") or line.startswith(";Engine:")
            ):
                hint = line.lstrip(";").replace("Engine:", "").strip()
                if hint:
                    summary.godot_version_hint = hint
            continue  # skip all comment lines for everything else

        # Section header: [application], [autoload], etc.
        section_match = _SECTION_RE.match(line)
        if section_match:
            current_section = section_match.group(1).lower()
            continue

        if current_section == "application":
            # config/name="My Game"
            if line.startswith("config/name"):
                m = re.match(r'^config/name\s*=\s*"([^"]*)"', line)
                if m:
                    summary.project_name = m.group(1)
            # run/main_scene="res://scenes/Main.tscn"
            elif line.startswith("run/main_scene"):
                m = re.match(r'^run/main_scene\s*=\s*"([^"]*)"', line)
                if m:
                    summary.main_scene = m.group(1)
            # config/icon="res://icon.svg"
            elif line.startswith("config/icon"):
                m = re.match(r'^config/icon\s*=\s*"([^"]*)"', line)
                if m:
                    summary.icon = m.group(1)

        elif current_section == "autoload":
            # NodeName="*res://autoload/MySingleton.gd"
            m = re.match(r'^(\w+)\s*=\s*"?\*?(res://[^"\n]*)"?', line)
            if m:
                summary.autoloads[m.group(1)] = m.group(2)

        elif current_section == "input":
            # action_name={ ... }  — one action per key=value block
            m = re.match(r"^(\w+)\s*=\s*\{", line)
            if m:
                summary.input_actions.add(m.group(1))

    return summary
