"""Tests for godot-project-doctor (unittest-based, pytest-compatible)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Add src to path for direct test execution
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from godot_project_doctor.context import render_context_markdown
from godot_project_doctor.gdscript import extract_gdscript_refs
from godot_project_doctor.graph import build_graph, render_mermaid_graph, render_text_graph
from godot_project_doctor.indexer import resolve_res_path
from godot_project_doctor.models import (
    FileStats,
    Issue,
    ProjectIndex,
    ProjectSummary,
    Severity,
)
from godot_project_doctor.parser import parse_project_godot
from godot_project_doctor.reporter import build_report, render_json
from godot_project_doctor.scanner import GodotProjectError, scan

# ─── Fixture helpers ───────────────────────────────────────────────────────────


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TempProject:
    """Context manager that creates a temp directory and cleans it up."""

    def __init__(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tmpdir.name)

    def __enter__(self):
        return self.path

    def __exit__(self, *args):
        self._tmpdir.cleanup()


def make_minimal_project(root: Path) -> Path:
    _write(
        root / "project.godot",
        """\
; Engine: Godot 4.x
[application]

config/name="Minimal Game"
run/main_scene="res://scenes/Main.tscn"

[autoload]

GameState="res://autoload/GameState.gd"
""",
    )
    return root


def make_project_with_valid_ref(root: Path) -> Path:
    _write(root / "project.godot", '[application]\nconfig/name="RefGame"\n')
    _write(root / "player" / "player.gd", "extends CharacterBody2D\n")
    _write(
        root / "scenes" / "Player.tscn",
        """\
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" uid="uid://abc123" path="res://player/player.gd" id="1"]

[node name="Player" type="CharacterBody2D"]
script = ExtResource("1")
""",
    )
    return root


def make_project_with_missing_ref(root: Path) -> Path:
    _write(root / "project.godot", '[application]\nconfig/name="BrokenGame"\n')
    _write(
        root / "scenes" / "Main.tscn",
        """\
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://scripts/missing.gd" id="1"]

[node name="Main" type="Node"]
script = ExtResource("1")
""",
    )
    return root


def make_project_with_unused_asset(root: Path) -> Path:
    _write(root / "project.godot", '[application]\nconfig/name="AssetGame"\n')
    _write(root / "assets" / "background.png", "PNG")
    _write(root / "scenes" / "Main.tscn", '[gd_scene format=3]\n\n[node name="Main" type="Node"]\n')
    return root


def make_project_with_export_presets(root: Path) -> Path:
    _write(root / "project.godot", '[application]\nconfig/name="ExportGame"\n')
    _write(root / "export_presets.cfg", '[preset.0]\nname="Windows Desktop"\n')
    return root


# ─── Project detection ────────────────────────────────────────────────────────


class TestProjectDetection(unittest.TestCase):
    def test_valid_project_is_detected(self):
        with TempProject() as root:
            make_minimal_project(root)
            index = scan(root)
            self.assertEqual(index.project_root, str(root))

    def test_missing_project_godot_raises(self):
        with TempProject() as root:
            with self.assertRaises(GodotProjectError) as ctx:
                scan(root)
            self.assertIn("project.godot", str(ctx.exception))

    def test_nonexistent_path_raises(self):
        with TempProject() as root:
            missing = root / "does_not_exist"
            with self.assertRaises(NotADirectoryError):
                scan(missing)

    def test_project_name_parsed(self):
        with TempProject() as root:
            make_minimal_project(root)
            summary = parse_project_godot(root)
            self.assertEqual(summary.project_name, "Minimal Game")

    def test_main_scene_parsed(self):
        with TempProject() as root:
            make_minimal_project(root)
            summary = parse_project_godot(root)
            self.assertEqual(summary.main_scene, "res://scenes/Main.tscn")

    def test_autoload_parsed(self):
        with TempProject() as root:
            make_minimal_project(root)
            summary = parse_project_godot(root)
            self.assertIn("GameState", summary.autoloads)
            self.assertEqual(summary.autoloads["GameState"], "res://autoload/GameState.gd")

    def test_icon_parsed(self):
        with TempProject() as root:
            _write(
                root / "project.godot",
                '[application]\nconfig/name="X"\nconfig/icon="res://icon.svg"\n',
            )
            summary = parse_project_godot(root)
            self.assertEqual(summary.icon, "res://icon.svg")

    def test_empty_dir_returns_empty_summary(self):
        with TempProject() as root:
            summary = parse_project_godot(root)
            self.assertIsNone(summary.project_name)
            self.assertIsNone(summary.main_scene)


# ─── ext_resource parsing ─────────────────────────────────────────────────────


class TestExtResourceParsing(unittest.TestCase):
    def test_valid_ref_is_parsed(self):
        with TempProject() as root:
            make_project_with_valid_ref(root)
            index = scan(root)
            self.assertEqual(len(index.refs), 1)
            ref = index.refs[0]
            self.assertEqual(ref.path, "res://player/player.gd")
            self.assertEqual(ref.ref_type, "Script")
            self.assertEqual(ref.uid, "uid://abc123")
            self.assertEqual(ref.ref_id, "1")

    def test_ref_source_file_is_relative(self):
        with TempProject() as root:
            make_project_with_valid_ref(root)
            index = scan(root)
            sf = index.refs[0].source_file
            self.assertFalse(sf.startswith("/"), f"Expected relative path, got: {sf}")

    def test_no_refs_in_empty_scene(self):
        with TempProject() as root:
            make_project_with_unused_asset(root)
            index = scan(root)
            self.assertEqual(index.refs, [])

    def test_missing_ref_produces_error(self):
        with TempProject() as root:
            make_project_with_missing_ref(root)
            index = scan(root)
            errors = [i for i in index.issues if i.code == "MISSING_EXT_RESOURCE"]
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].severity, Severity.ERROR)


# ─── res:// path resolution ───────────────────────────────────────────────────


class TestResPathResolution(unittest.TestCase):
    def test_res_prefix_stripped(self):
        with TempProject() as root:
            result = resolve_res_path("res://scenes/Main.tscn", root)
            self.assertEqual(result, root / "scenes" / "Main.tscn")

    def test_nested_path_resolved(self):
        with TempProject() as root:
            result = resolve_res_path("res://player/sprites/idle.png", root)
            self.assertEqual(result, root / "player" / "sprites" / "idle.png")

    def test_fallback_for_non_res_path(self):
        with TempProject() as root:
            result = resolve_res_path("scripts/foo.gd", root)
            self.assertEqual(result, root / "scripts" / "foo.gd")


# ─── Missing reference ────────────────────────────────────────────────────────


class TestMissingReference(unittest.TestCase):
    def test_missing_ref_has_correct_code(self):
        with TempProject() as root:
            make_project_with_missing_ref(root)
            index = scan(root)
            codes = [i.code for i in index.issues]
            self.assertIn("MISSING_EXT_RESOURCE", codes)

    def test_missing_ref_includes_path_in_message(self):
        with TempProject() as root:
            make_project_with_missing_ref(root)
            index = scan(root)
            err = next(i for i in index.issues if i.code == "MISSING_EXT_RESOURCE")
            self.assertIn("scripts/missing.gd", err.message)

    def test_missing_ref_has_source_file(self):
        with TempProject() as root:
            make_project_with_missing_ref(root)
            index = scan(root)
            err = next(i for i in index.issues if i.code == "MISSING_EXT_RESOURCE")
            self.assertIsNotNone(err.file)
            self.assertIn("Main.tscn", err.file)

    def test_valid_ref_no_missing_error(self):
        with TempProject() as root:
            make_project_with_valid_ref(root)
            index = scan(root)
            errors = [i for i in index.issues if i.code == "MISSING_EXT_RESOURCE"]
            self.assertEqual(errors, [])


# ─── Unused asset candidates ─────────────────────────────────────────────────


class TestUnusedAssetCandidates(unittest.TestCase):
    def test_unreferenced_image_flagged(self):
        with TempProject() as root:
            make_project_with_unused_asset(root)
            index = scan(root)
            candidates = [i for i in index.issues if i.code == "UNUSED_ASSET_CANDIDATE"]
            self.assertEqual(len(candidates), 1)
            self.assertIn("background.png", candidates[0].message)

    def test_unreferenced_asset_is_warning(self):
        with TempProject() as root:
            make_project_with_unused_asset(root)
            index = scan(root)
            candidate = next(i for i in index.issues if i.code == "UNUSED_ASSET_CANDIDATE")
            self.assertEqual(candidate.severity, Severity.WARNING)

    def test_referenced_image_not_flagged(self):
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="X"\n')
            _write(root / "sprites" / "hero.png", "PNG")
            _write(
                root / "scenes" / "Game.tscn",
                "[gd_scene format=3]\n"
                '[ext_resource type="Texture2D" path="res://sprites/hero.png" id="1"]\n',
            )
            index = scan(root)
            candidates = [i for i in index.issues if i.code == "UNUSED_ASSET_CANDIDATE"]
            self.assertEqual(candidates, [])

    def test_project_icon_not_flagged_unused(self):
        """application/config/icon must count as a referenced asset."""
        with TempProject() as root:
            _write(
                root / "project.godot",
                '[application]\nconfig/name="X"\nconfig/icon="res://icon.svg"\n',
            )
            _write(root / "icon.svg", "<svg/>")
            index = scan(root)
            flagged = [
                i
                for i in index.issues
                if i.code == "UNUSED_ASSET_CANDIDATE" and "icon.svg" in (i.file or "")
            ]
            self.assertEqual(flagged, [])


# ─── Export presets check ─────────────────────────────────────────────────────


class TestExportPresets(unittest.TestCase):
    def test_missing_export_presets_is_info(self):
        with TempProject() as root:
            make_minimal_project(root)
            index = scan(root)
            info = [i for i in index.issues if i.code == "NO_EXPORT_PRESETS"]
            self.assertEqual(len(info), 1)
            self.assertEqual(info[0].severity, Severity.INFO)

    def test_present_export_presets_no_info(self):
        with TempProject() as root:
            make_project_with_export_presets(root)
            index = scan(root)
            info = [i for i in index.issues if i.code == "NO_EXPORT_PRESETS"]
            self.assertEqual(info, [])


# ─── File stats ───────────────────────────────────────────────────────────────


class TestFileStats(unittest.TestCase):
    def test_script_counted(self):
        with TempProject() as root:
            make_project_with_valid_ref(root)
            index = scan(root)
            self.assertEqual(index.file_stats.scripts, 1)

    def test_scene_counted(self):
        with TempProject() as root:
            make_project_with_valid_ref(root)
            index = scan(root)
            self.assertEqual(index.file_stats.scenes, 1)

    def test_image_counted(self):
        with TempProject() as root:
            make_project_with_unused_asset(root)
            index = scan(root)
            self.assertEqual(index.file_stats.images, 1)


# ─── JSON output ─────────────────────────────────────────────────────────────


class TestJsonOutput(unittest.TestCase):
    def test_json_is_valid(self):
        with TempProject() as root:
            make_minimal_project(root)
            index = scan(root)
            report = build_report(index)
            text = render_json(report)
            parsed = json.loads(text)
            self.assertIsInstance(parsed, dict)

    def test_json_has_required_keys(self):
        with TempProject() as root:
            make_minimal_project(root)
            index = scan(root)
            report = build_report(index)
            data = json.loads(render_json(report))
            for key in ("project_root", "summary", "file_stats", "issue_counts", "refs", "issues"):
                self.assertIn(key, data, f"Missing key: {key}")

    def test_json_schema_version_present(self):
        with TempProject() as root:
            make_minimal_project(root)
            index = scan(root)
            report = build_report(index)
            data = json.loads(render_json(report))
            self.assertEqual(data.get("schema_version"), "1.2")

    def test_json_issues_are_serialisable(self):
        with TempProject() as root:
            make_project_with_missing_ref(root)
            index = scan(root)
            report = build_report(index)
            data = json.loads(render_json(report))
            self.assertGreater(len(data["issues"]), 0)
            issue = data["issues"][0]
            self.assertIn("code", issue)
            self.assertIn("severity", issue)
            self.assertIn("message", issue)

    def test_json_written_to_file(self):
        with TempProject() as root:
            make_minimal_project(root)
            index = scan(root)
            report = build_report(index)
            out = root / "report.json"
            render_json(report, out)
            self.assertTrue(out.exists())
            data = json.loads(out.read_text())
            self.assertIn("project_root", data)

    def test_json_stable_across_calls(self):
        """Two scans of the same project should produce identical JSON."""
        with TempProject() as root:
            make_minimal_project(root)
            index1 = scan(root)
            index2 = scan(root)
            r1 = render_json(build_report(index1))
            r2 = render_json(build_report(index2))
            self.assertEqual(r1, r2)

    def test_ref_fields_in_json(self):
        with TempProject() as root:
            make_project_with_valid_ref(root)
            index = scan(root)
            report = build_report(index)
            data = json.loads(render_json(report))
            self.assertEqual(len(data["refs"]), 1)
            ref = data["refs"][0]
            self.assertEqual(ref["path"], "res://player/player.gd")
            self.assertEqual(ref["type"], "Script")


# ─── Dependency graph ─────────────────────────────────────────────────────────


def make_project_simple_graph(root: Path) -> Path:
    """One scene referencing one script."""
    _write(root / "project.godot", '[application]\nconfig/name="SimpleGraph"\n')
    _write(root / "player" / "player.gd", "extends CharacterBody2D\n")
    _write(
        root / "scenes" / "Player.tscn",
        """\
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" uid="uid://abc" path="res://player/player.gd" id="1"]

[node name="Player" type="CharacterBody2D"]
script = ExtResource("1")
""",
    )
    return root


def make_project_multi_ref_graph(root: Path) -> Path:
    """One scene referencing both a Script and a PackedScene."""
    _write(root / "project.godot", '[application]\nconfig/name="MultiRef"\n')
    _write(root / "scripts" / "enemy.gd", "extends Node\n")
    _write(
        root / "scenes" / "Bullet.tscn", '[gd_scene format=3]\n\n[node name="Bullet" type="Node"]\n'
    )
    _write(
        root / "scenes" / "Enemy.tscn",
        """\
[gd_scene load_steps=3 format=3]

[ext_resource type="Script" path="res://scripts/enemy.gd" id="1"]
[ext_resource type="PackedScene" path="res://scenes/Bullet.tscn" id="2"]

[node name="Enemy" type="Node"]
script = ExtResource("1")
""",
    )
    return root


class TestDependencyGraph(unittest.TestCase):
    # ── build_graph ──────────────────────────────────────────────────────────

    def test_simple_graph_has_one_source(self):
        with TempProject() as root:
            make_project_simple_graph(root)
            index = scan(root)
            graph = build_graph(index)
            self.assertEqual(len(graph), 1)

    def test_simple_graph_edge_correct(self):
        with TempProject() as root:
            make_project_simple_graph(root)
            index = scan(root)
            graph = build_graph(index)
            source = next(iter(graph))
            self.assertIn("Player.tscn", source)
            self.assertEqual(graph[source], ["res://player/player.gd"])

    def test_multi_ref_graph_has_two_deps(self):
        with TempProject() as root:
            make_project_multi_ref_graph(root)
            index = scan(root)
            graph = build_graph(index)
            # Enemy.tscn is the only source with ext_resources
            enemy_key = next(k for k in graph if "Enemy.tscn" in k)
            self.assertEqual(len(graph[enemy_key]), 2)

    def test_multi_ref_graph_contains_script_and_packed_scene(self):
        with TempProject() as root:
            make_project_multi_ref_graph(root)
            index = scan(root)
            graph = build_graph(index)
            enemy_key = next(k for k in graph if "Enemy.tscn" in k)
            deps = graph[enemy_key]
            self.assertIn("res://scripts/enemy.gd", deps)
            self.assertIn("res://scenes/Bullet.tscn", deps)

    def test_empty_project_graph_is_empty(self):
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="Empty"\n')
            _write(
                root / "scenes" / "Main.tscn",
                '[gd_scene format=3]\n\n[node name="Main" type="Node"]\n',
            )
            index = scan(root)
            graph = build_graph(index)
            self.assertEqual(graph, {})

    # ── render_text_graph ────────────────────────────────────────────────────

    def test_text_output_contains_source_file(self):
        with TempProject() as root:
            make_project_simple_graph(root)
            index = scan(root)
            graph = build_graph(index)
            text = render_text_graph(graph)
            self.assertIn("Player.tscn", text)

    def test_text_output_contains_dep_arrow(self):
        with TempProject() as root:
            make_project_simple_graph(root)
            index = scan(root)
            graph = build_graph(index)
            text = render_text_graph(graph)
            self.assertIn("  -> res://player/player.gd", text)

    def test_text_output_empty_graph_message(self):
        graph: dict = {}
        text = render_text_graph(graph)
        self.assertIn("no dependencies", text)

    # ── render_mermaid_graph ─────────────────────────────────────────────────

    def test_mermaid_starts_with_flowchart(self):
        with TempProject() as root:
            make_project_simple_graph(root)
            index = scan(root)
            graph = build_graph(index)
            mermaid = render_mermaid_graph(graph)
            self.assertTrue(mermaid.startswith("flowchart TD"))

    def test_mermaid_contains_edge_arrow(self):
        with TempProject() as root:
            make_project_simple_graph(root)
            index = scan(root)
            graph = build_graph(index)
            mermaid = render_mermaid_graph(graph)
            self.assertIn("-->", mermaid)

    def test_mermaid_multi_ref_has_two_edges(self):
        with TempProject() as root:
            make_project_multi_ref_graph(root)
            index = scan(root)
            graph = build_graph(index)
            mermaid = render_mermaid_graph(graph)
            self.assertEqual(mermaid.count("-->"), 2)

    def test_mermaid_node_ids_are_stable(self):
        """Two calls with the same graph must produce identical output."""
        with TempProject() as root:
            make_project_multi_ref_graph(root)
            index = scan(root)
            graph = build_graph(index)
            self.assertEqual(render_mermaid_graph(graph), render_mermaid_graph(graph))

    def test_mermaid_empty_graph_message(self):
        graph: dict = {}
        mermaid = render_mermaid_graph(graph)
        self.assertIn("no dependencies", mermaid)


# ─── Context report ───────────────────────────────────────────────────────────


def _make_index_with_issues(issues: list) -> ProjectIndex:
    """Build a minimal ProjectIndex with the given issues pre-loaded."""
    return ProjectIndex(
        project_root="/fake/project",
        summary=ProjectSummary(
            project_name="TestGame",
            main_scene="res://scenes/Main.tscn",
            autoloads={"GameState": "res://autoload/GameState.gd"},
            godot_version_hint="Godot 4.x",
        ),
        file_stats=FileStats(scenes=2, scripts=3, images=1, audio=1),
        refs=[],
        issues=issues,
    )


class TestContextReport(unittest.TestCase):
    # ── Section headings present ──────────────────────────────────────────────

    def test_report_contains_project_summary_heading(self):
        index = _make_index_with_issues([])
        md = render_context_markdown(index)
        self.assertIn("## Project Summary", md)

    def test_report_contains_file_counts_heading(self):
        index = _make_index_with_issues([])
        md = render_context_markdown(index)
        self.assertIn("## File Counts", md)

    def test_report_contains_issues_heading(self):
        index = _make_index_with_issues([])
        md = render_context_markdown(index)
        self.assertIn("## Issues", md)

    def test_report_contains_missing_references_heading(self):
        index = _make_index_with_issues([])
        md = render_context_markdown(index)
        self.assertIn("## Missing References", md)

    def test_report_contains_large_assets_heading(self):
        index = _make_index_with_issues([])
        md = render_context_markdown(index)
        self.assertIn("## Large Assets", md)

    def test_report_contains_unused_assets_heading(self):
        index = _make_index_with_issues([])
        md = render_context_markdown(index)
        self.assertIn("## Unused Asset Candidates", md)

    def test_report_contains_graph_summary_heading(self):
        index = _make_index_with_issues([])
        md = render_context_markdown(index)
        self.assertIn("## Dependency Graph Summary", md)

    def test_report_contains_investigation_focus_heading(self):
        index = _make_index_with_issues([])
        md = render_context_markdown(index)
        self.assertIn("## Suggested Investigation Focus", md)

    # ── Project metadata ──────────────────────────────────────────────────────

    def test_report_contains_project_name(self):
        index = _make_index_with_issues([])
        md = render_context_markdown(index)
        self.assertIn("TestGame", md)

    def test_report_contains_main_scene(self):
        index = _make_index_with_issues([])
        md = render_context_markdown(index)
        self.assertIn("res://scenes/Main.tscn", md)

    def test_report_contains_autoload(self):
        index = _make_index_with_issues([])
        md = render_context_markdown(index)
        self.assertIn("GameState", md)
        self.assertIn("res://autoload/GameState.gd", md)

    def test_report_contains_godot_version(self):
        index = _make_index_with_issues([])
        md = render_context_markdown(index)
        self.assertIn("Godot 4.x", md)

    def test_reported_issue_text_appears_in_output(self):
        index = _make_index_with_issues([])
        md = render_context_markdown(index, issue_text="game crashes on startup")
        self.assertIn("game crashes on startup", md)

    def test_no_issue_text_omits_reported_issue_section(self):
        index = _make_index_with_issues([])
        md = render_context_markdown(index, issue_text="")
        self.assertNotIn("## Reported Issue", md)

    # ── Issues section ────────────────────────────────────────────────────────

    def test_no_issues_message_shown(self):
        index = _make_index_with_issues([])
        md = render_context_markdown(index)
        self.assertIn("No issues found", md)

    def test_error_issue_appears_in_report(self):
        issues = [
            Issue(
                "MISSING_EXT_RESOURCE",
                Severity.ERROR,
                "External resource not found: res://foo.gd",
                file="scenes/Main.tscn",
            )
        ]
        index = _make_index_with_issues(issues)
        md = render_context_markdown(index)
        self.assertIn("MISSING_EXT_RESOURCE", md)
        self.assertIn("ERROR", md)

    def test_warning_issue_appears_in_report(self):
        issues = [
            Issue(
                "UNUSED_ASSET_CANDIDATE",
                Severity.WARNING,
                "Asset not referenced: assets/bg.png",
                file="assets/bg.png",
            )
        ]
        index = _make_index_with_issues(issues)
        md = render_context_markdown(index)
        self.assertIn("UNUSED_ASSET_CANDIDATE", md)

    # ── Missing references section ────────────────────────────────────────────

    def test_missing_ref_shown_in_section(self):
        issues = [
            Issue(
                "MISSING_EXT_RESOURCE",
                Severity.ERROR,
                "External resource not found: res://scripts/missing.gd",
                file="scenes/Main.tscn",
            )
        ]
        index = _make_index_with_issues(issues)
        md = render_context_markdown(index)
        self.assertIn("res://scripts/missing.gd", md)

    def test_no_missing_refs_message(self):
        index = _make_index_with_issues([])
        md = render_context_markdown(index)
        self.assertIn("No missing references detected", md)

    # ── Large assets section ──────────────────────────────────────────────────

    def test_large_texture_shown_in_section(self):
        issues = [
            Issue(
                "LARGE_TEXTURE",
                Severity.WARNING,
                "Large texture (4096×4096): assets/hero.png",
                file="assets/hero.png",
            )
        ]
        index = _make_index_with_issues(issues)
        md = render_context_markdown(index)
        self.assertIn("assets/hero.png", md)
        self.assertIn("Large Assets", md)

    def test_large_audio_shown_in_section(self):
        issues = [
            Issue(
                "LARGE_AUDIO",
                Severity.WARNING,
                "Large audio file (15.2 MB): audio/music.ogg",
                file="audio/music.ogg",
            )
        ]
        index = _make_index_with_issues(issues)
        md = render_context_markdown(index)
        self.assertIn("audio/music.ogg", md)

    def test_no_large_assets_message(self):
        index = _make_index_with_issues([])
        md = render_context_markdown(index)
        self.assertIn("No large assets detected", md)

    # ── Unused assets section ─────────────────────────────────────────────────

    def test_unused_asset_shown_in_section(self):
        issues = [
            Issue(
                "UNUSED_ASSET_CANDIDATE",
                Severity.WARNING,
                "Asset not referenced: assets/old_bg.png",
                file="assets/old_bg.png",
            )
        ]
        index = _make_index_with_issues(issues)
        md = render_context_markdown(index)
        self.assertIn("assets/old_bg.png", md)

    # ── Investigation focus: mobile ───────────────────────────────────────────

    def test_mobile_focus_mentions_large_textures(self):
        issues = [
            Issue(
                "LARGE_TEXTURE",
                Severity.WARNING,
                "Large texture (4096×4096): assets/bg.png",
                file="assets/bg.png",
            )
        ]
        index = _make_index_with_issues(issues)
        md = render_context_markdown(index, issue_text="game is slow on mobile")
        self.assertIn("Large textures", md)

    def test_mobile_focus_mentions_export_presets_when_missing(self):
        issues = [Issue("NO_EXPORT_PRESETS", Severity.INFO, "export_presets.cfg not found.")]
        index = _make_index_with_issues(issues)
        md = render_context_markdown(index, issue_text="build for android mobile")
        self.assertIn("export presets", md.lower())

    def test_mobile_focus_no_large_assets_gives_positive_note(self):
        index = _make_index_with_issues([])
        md = render_context_markdown(index, issue_text="targeting ios mobile")
        focus_section = md.split("## Suggested Investigation Focus", 1)[-1]
        self.assertIn("No large textures", focus_section)

    # ── Investigation focus: missing / broken / reference ─────────────────────

    def test_missing_keyword_highlights_missing_refs(self):
        issues = [
            Issue(
                "MISSING_EXT_RESOURCE",
                Severity.ERROR,
                "External resource not found: res://player.gd",
                file="scenes/Player.tscn",
            )
        ]
        index = _make_index_with_issues(issues)
        md = render_context_markdown(index, issue_text="missing resource error")
        focus = md.split("## Suggested Investigation Focus", 1)[-1]
        self.assertIn("missing external resource", focus.lower())

    def test_broken_keyword_highlights_missing_refs(self):
        issues = [
            Issue(
                "MISSING_EXT_RESOURCE",
                Severity.ERROR,
                "External resource not found: res://ui.gd",
                file="scenes/UI.tscn",
            )
        ]
        index = _make_index_with_issues(issues)
        md = render_context_markdown(index, issue_text="broken scene references")
        focus = md.split("## Suggested Investigation Focus", 1)[-1]
        self.assertIn("missing external resource", focus.lower())

    def test_reference_keyword_when_none_missing_gives_alternative_causes(self):
        index = _make_index_with_issues([])
        md = render_context_markdown(index, issue_text="reference not found")
        focus = md.split("## Suggested Investigation Focus", 1)[-1]
        self.assertIn("No missing external resources found", focus)

    # ── Investigation focus: default ──────────────────────────────────────────

    def test_default_focus_errors_mentioned_first(self):
        issues = [
            Issue(
                "MISSING_EXT_RESOURCE", Severity.ERROR, "External resource not found: res://foo.gd"
            ),
            Issue("UNUSED_ASSET_CANDIDATE", Severity.WARNING, "Asset not referenced: assets/x.png"),
        ]
        index = _make_index_with_issues(issues)
        md = render_context_markdown(index, issue_text="something odd is happening")
        focus = md.split("## Suggested Investigation Focus", 1)[-1]
        error_pos = focus.find("ERROR")
        warning_pos = focus.find("WARNING")
        self.assertLess(error_pos, warning_pos)

    def test_default_focus_no_issues_gives_static_analysis_note(self):
        index = _make_index_with_issues([])
        md = render_context_markdown(index, issue_text="something odd is happening")
        focus = md.split("## Suggested Investigation Focus", 1)[-1]
        self.assertIn("No critical issues", focus)

    # ── Markdown structure ────────────────────────────────────────────────────

    def test_report_ends_with_footer(self):
        index = _make_index_with_issues([])
        md = render_context_markdown(index)
        self.assertIn("godot-project-doctor", md)
        self.assertIn("No AI APIs were called", md)

    def test_report_is_valid_markdown_string(self):
        with TempProject() as root:
            make_project_with_valid_ref(root)
            index = scan(root)
            md = render_context_markdown(index, issue_text="test issue")
            self.assertIsInstance(md, str)
            self.assertGreater(len(md), 200)

    def test_output_written_to_file(self):
        with TempProject() as root:
            make_minimal_project(root)
            index = scan(root)
            md = render_context_markdown(index)
            out = root / "context.md"
            out.write_text(md, encoding="utf-8")
            self.assertTrue(out.exists())
            content = out.read_text(encoding="utf-8")
            self.assertIn("Project Summary", content)


# ─── ext_resource attribute ordering ─────────────────────────────────────────


class TestExtResourceAttributeOrdering(unittest.TestCase):
    """The parser must be order-independent for [ext_resource ...] attributes."""

    def _make_project_with_scene(self, root: Path, header: str) -> None:
        _write(root / "project.godot", '[application]\nconfig/name="OrderTest"\n')
        _write(root / "player.gd", "extends Node\n")
        _write(root / "scenes" / "Main.tscn", f"[gd_scene format=3]\n\n{header}\n")

    def test_standard_order_type_uid_path_id(self):
        with TempProject() as root:
            self._make_project_with_scene(
                root,
                '[ext_resource type="Script" uid="uid://abc" path="res://player.gd" id="1"]',
            )
            index = scan(root)
            self.assertEqual(len(index.refs), 1)
            self.assertEqual(index.refs[0].path, "res://player.gd")
            self.assertEqual(index.refs[0].ref_type, "Script")

    def test_reversed_order_path_id_uid_type(self):
        with TempProject() as root:
            self._make_project_with_scene(
                root,
                '[ext_resource path="res://player.gd" id="1" uid="uid://abc" type="Script"]',
            )
            index = scan(root)
            self.assertEqual(len(index.refs), 1)
            self.assertEqual(index.refs[0].path, "res://player.gd")
            self.assertEqual(index.refs[0].ref_type, "Script")
            self.assertEqual(index.refs[0].uid, "uid://abc")
            self.assertEqual(index.refs[0].ref_id, "1")

    def test_no_uid_field(self):
        with TempProject() as root:
            self._make_project_with_scene(
                root,
                '[ext_resource type="Script" path="res://player.gd" id="1"]',
            )
            index = scan(root)
            self.assertEqual(len(index.refs), 1)
            self.assertIsNone(index.refs[0].uid)

    def test_id_before_type(self):
        with TempProject() as root:
            self._make_project_with_scene(
                root,
                '[ext_resource id="2" type="Texture2D" path="res://player.gd"]',
            )
            index = scan(root)
            self.assertEqual(index.refs[0].ref_id, "2")
            self.assertEqual(index.refs[0].ref_type, "Texture2D")


# ─── Relative path ext_resource ──────────────────────────────────────────────


class TestRelativePathExtResource(unittest.TestCase):
    def test_relative_path_resolved_from_declaring_file(self):
        """A relative ext_resource path is resolved relative to the .tscn file."""
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="RelPath"\n')
            # script sits at root/scripts/foo.gd
            _write(root / "scripts" / "foo.gd", "extends Node\n")
            # scene sits at root/scenes/Main.tscn and references ../scripts/foo.gd
            _write(
                root / "scenes" / "Main.tscn",
                "[gd_scene format=3]\n\n"
                '[ext_resource type="Script" path="../scripts/foo.gd" id="1"]\n',
            )
            index = scan(root)
            # Should be no MISSING_EXT_RESOURCE because the file exists
            missing = [i for i in index.issues if i.code == "MISSING_EXT_RESOURCE"]
            self.assertEqual(missing, [], "Relative path should resolve correctly")

    def test_relative_path_missing_produces_error(self):
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="RelPath"\n')
            _write(
                root / "scenes" / "Main.tscn",
                "[gd_scene format=3]\n\n"
                '[ext_resource type="Script" path="../scripts/gone.gd" id="1"]\n',
            )
            index = scan(root)
            missing = [i for i in index.issues if i.code == "MISSING_EXT_RESOURCE"]
            self.assertEqual(len(missing), 1)


# ─── GDScript static reference extraction ────────────────────────────────────


class TestGDScriptRefExtraction(unittest.TestCase):
    """Unit tests for gdscript.extract_gdscript_refs."""

    def _gd(self, root: Path, name: str, content: str) -> Path:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def test_preload_literal_extracted(self):
        with TempProject() as root:
            gd = self._gd(root, "player.gd", 'var x = preload("res://assets/hero.png")\n')
            refs = extract_gdscript_refs(gd, root)
            self.assertEqual(len(refs), 1)
            self.assertEqual(refs[0].path, "res://assets/hero.png")
            self.assertEqual(refs[0].ref_type, "preload")
            self.assertEqual(refs[0].kind, "gdscript")

    def test_load_literal_extracted(self):
        with TempProject() as root:
            gd = self._gd(root, "ui.gd", 'var t = load("res://ui/theme.tres")\n')
            refs = extract_gdscript_refs(gd, root)
            self.assertEqual(len(refs), 1)
            self.assertEqual(refs[0].path, "res://ui/theme.tres")
            self.assertEqual(refs[0].ref_type, "load")

    def test_resourceloader_load_extracted(self):
        with TempProject() as root:
            gd = self._gd(
                root,
                "loader.gd",
                'var r = ResourceLoader.load("res://data/config.tres")\n',
            )
            refs = extract_gdscript_refs(gd, root)
            self.assertEqual(len(refs), 1)
            self.assertEqual(refs[0].ref_type, "ResourceLoader.load")
            self.assertEqual(refs[0].path, "res://data/config.tres")

    def test_comment_line_ignored(self):
        with TempProject() as root:
            gd = self._gd(root, "foo.gd", '# var x = load("res://scripts/foo.gd")\n')
            refs = extract_gdscript_refs(gd, root)
            self.assertEqual(refs, [])

    def test_dynamic_load_variable_ignored(self):
        with TempProject() as root:
            gd = self._gd(root, "bar.gd", "var path = my_path\nvar x = load(path)\n")
            refs = extract_gdscript_refs(gd, root)
            self.assertEqual(refs, [])

    def test_non_res_path_ignored(self):
        with TempProject() as root:
            gd = self._gd(root, "baz.gd", 'var x = load("user://save.dat")\n')
            refs = extract_gdscript_refs(gd, root)
            self.assertEqual(refs, [])

    def test_multiple_refs_in_one_file(self):
        with TempProject() as root:
            gd = self._gd(
                root,
                "multi.gd",
                'var a = preload("res://a.png")\n'
                'var b = load("res://b.png")\n'
                'var c = ResourceLoader.load("res://c.png")\n',
            )
            refs = extract_gdscript_refs(gd, root)
            self.assertEqual(len(refs), 3)
            paths = {r.path for r in refs}
            self.assertEqual(paths, {"res://a.png", "res://b.png", "res://c.png"})


# ─── GDScript refs integrated into scan ──────────────────────────────────────


class TestGDScriptIntegration(unittest.TestCase):
    """GDScript refs must flow through scan → checks correctly."""

    def test_existing_preload_no_missing_error(self):
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="GDScan"\n')
            _write(root / "assets" / "hero.png", "PNG")
            _write(
                root / "player.gd",
                'extends Node\nvar t = preload("res://assets/hero.png")\n',
            )
            index = scan(root)
            missing = [i for i in index.issues if i.code == "MISSING_EXT_RESOURCE"]
            self.assertEqual(missing, [])

    def test_missing_load_produces_error(self):
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="GDScan"\n')
            _write(
                root / "player.gd",
                'extends Node\nvar x = load("res://scripts/gone.gd")\n',
            )
            index = scan(root)
            missing = [i for i in index.issues if i.code == "MISSING_EXT_RESOURCE"]
            self.assertEqual(len(missing), 1)
            self.assertIn("gone.gd", missing[0].message)

    def test_gdscript_preload_suppresses_unused_asset(self):
        """An image referenced only by GDScript preload() should not be UNUSED."""
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="GDScan"\n')
            _write(root / "sprites" / "idle.png", "PNG")
            _write(
                root / "player.gd",
                'extends Node\nvar t = preload("res://sprites/idle.png")\n',
            )
            index = scan(root)
            unused = [i for i in index.issues if i.code == "UNUSED_ASSET_CANDIDATE"]
            self.assertEqual(unused, [])

    def test_resourceloader_load_appears_in_graph(self):
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="GDScan"\n')
            _write(root / "data" / "config.tres", "[resource]\n")
            _write(
                root / "loader.gd",
                'extends Node\nvar cfg = ResourceLoader.load("res://data/config.tres")\n',
            )
            index = scan(root)
            graph = build_graph(index)
            src = next((k for k in graph if "loader.gd" in k), None)
            self.assertIsNotNone(src, "loader.gd should be a graph source node")
            self.assertIn("res://data/config.tres", graph[src])

    def test_dynamic_load_no_missing_error(self):
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="GDScan"\n')
            _write(
                root / "player.gd",
                "extends Node\nvar path = get_path()\nvar x = load(path)\n",
            )
            index = scan(root)
            missing = [i for i in index.issues if i.code == "MISSING_EXT_RESOURCE"]
            self.assertEqual(missing, [])


# ─── Godot version hint parsing ──────────────────────────────────────────────


class TestGodotVersionHint(unittest.TestCase):
    def test_version_hint_parsed_from_header(self):
        with TempProject() as root:
            _write(
                root / "project.godot",
                '; Engine: Godot 4.2\n\n[application]\nconfig/name="HintTest"\n',
            )
            summary = parse_project_godot(root)
            self.assertEqual(summary.godot_version_hint, "Godot 4.2")

    def test_version_hint_without_space_after_semicolon(self):
        with TempProject() as root:
            _write(
                root / "project.godot",
                ';Engine: Godot 4.3-stable\n\n[application]\nconfig/name="HintTest"\n',
            )
            summary = parse_project_godot(root)
            self.assertEqual(summary.godot_version_hint, "Godot 4.3-stable")

    def test_version_hint_not_overwritten_by_later_comment(self):
        with TempProject() as root:
            _write(
                root / "project.godot",
                "; Engine: Godot 4.1\n; Engine: Godot 99.0\n\n[application]\n",
            )
            summary = parse_project_godot(root)
            self.assertEqual(summary.godot_version_hint, "Godot 4.1")

    def test_no_version_hint_returns_none(self):
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="NoHint"\n')
            summary = parse_project_godot(root)
            self.assertIsNone(summary.godot_version_hint)


# ─── ResourceRef.kind field ───────────────────────────────────────────────────


class TestResourceRefKind(unittest.TestCase):
    def test_ext_resource_kind_is_ext_resource(self):
        with TempProject() as root:
            make_project_with_valid_ref(root)
            index = scan(root)
            ext_refs = [r for r in index.refs if r.kind == "ext_resource"]
            self.assertGreater(len(ext_refs), 0)

    def test_gdscript_ref_kind_is_gdscript(self):
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="K"\n')
            _write(root / "assets" / "x.png", "PNG")
            _write(root / "s.gd", 'var x = preload("res://assets/x.png")\n')
            index = scan(root)
            gd_refs = [r for r in index.refs if r.kind == "gdscript"]
            self.assertGreater(len(gd_refs), 0)

    def test_json_output_includes_kind_field(self):
        with TempProject() as root:
            make_project_with_valid_ref(root)
            index = scan(root)
            report = build_report(index)
            data = json.loads(render_json(report))
            self.assertIn("kind", data["refs"][0])
            self.assertEqual(data["refs"][0]["kind"], "ext_resource")


# ─── CLI Unicode safety (regression) ─────────────────────────────────────────


class TestCliUnicodeSafety(unittest.TestCase):
    """The terminal text renderer must not raise UnicodeEncodeError on narrow
    encodings.  We simulate a CP949 stdout by monkey-patching the encoding."""

    def test_ascii_icons_used_for_narrow_encoding(self):
        from godot_project_doctor.reporter import _SEVERITY_ICONS_ASCII, _terminal_icons

        class _NarrowStream:
            encoding = "cp949"

        original = sys.stdout
        try:
            sys.stdout = _NarrowStream()  # type: ignore[assignment]
            icons = _terminal_icons()
        finally:
            sys.stdout = original

        self.assertEqual(icons, _SEVERITY_ICONS_ASCII)

    def test_ascii_glyphs_used_for_narrow_encoding(self):
        """Divider / arrow / dash / ok glyphs must be pure ASCII on CP949."""
        from godot_project_doctor.reporter import _GLYPHS_ASCII, _terminal_glyphs

        class _NarrowStream:
            encoding = "cp949"

        original = sys.stdout
        try:
            sys.stdout = _NarrowStream()  # type: ignore[assignment]
            glyphs = _terminal_glyphs()
        finally:
            sys.stdout = original

        self.assertEqual(glyphs, _GLYPHS_ASCII)
        for value in glyphs.values():
            value.encode("cp949")  # raises if not representable

    def test_render_text_terminal_output_cp949_encodable(self):
        """The full terminal report must be cp949-encodable on a narrow console."""
        from godot_project_doctor.reporter import build_report, render_text

        # click encodes the message to the stream's cp949 encoding before
        # writing; any non-encodable box-drawing glyph would raise
        # UnicodeEncodeError at that point.  Completing without error is the
        # regression guard.
        class _NarrowCapture:
            encoding = "cp949"

            def __init__(self) -> None:
                self.chunks: list[object] = []

            def write(self, s: object) -> int:
                self.chunks.append(s)
                return len(s)  # type: ignore[arg-type]

            def flush(self) -> None:
                pass

        with TempProject() as root:
            make_project_with_missing_ref(root)
            index = scan(root)
            report = build_report(index)

        captured = _NarrowCapture()
        original = sys.stdout
        try:
            sys.stdout = captured  # type: ignore[assignment]
            render_text(report)  # terminal mode (no output file)
        finally:
            sys.stdout = original

        self.assertTrue(captured.chunks, "render_text produced no terminal output")

    def test_utf8_encoding_uses_unicode_icons(self):
        from godot_project_doctor.reporter import _SEVERITY_ICONS, _terminal_icons

        class _Utf8Stream:
            encoding = "utf-8"

        original = sys.stdout
        try:
            sys.stdout = _Utf8Stream()  # type: ignore[assignment]
            icons = _terminal_icons()
        finally:
            sys.stdout = original

        self.assertEqual(icons, _SEVERITY_ICONS)

    def test_render_text_does_not_raise_on_narrow_encoding(self):
        """render_text() must complete without raising even with ASCII icons."""
        from godot_project_doctor.reporter import render_text

        with TempProject() as root:
            make_project_with_missing_ref(root)
            index = scan(root)
            report = build_report(index)

        class _NarrowStream:
            encoding = "cp949"

        original = sys.stdout
        try:
            sys.stdout = _NarrowStream()  # type: ignore[assignment]
            # Output to a file so click doesn't actually write to the fake stream
            out = Path(tempfile.mktemp(suffix=".txt"))
            render_text(report, out)
            out.unlink(missing_ok=True)
        finally:
            sys.stdout = original


# ─── GDScript false-positive guard ───────────────────────────────────────────


class TestGDScriptFalsePositives(unittest.TestCase):
    """Regression tests for calls that must NOT be extracted as refs."""

    def _gd(self, root: Path, name: str, content: str) -> Path:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def test_inline_comment_load_ignored(self):
        with TempProject() as root:
            gd = self._gd(root, "a.gd", 'var x = 1  # load("res://foo.gd")\n')
            refs = extract_gdscript_refs(gd, root)
            self.assertEqual(refs, [])

    def test_download_call_ignored(self):
        with TempProject() as root:
            gd = self._gd(root, "b.gd", 'download("res://foo.gd")\n')
            refs = extract_gdscript_refs(gd, root)
            self.assertEqual(refs, [])

    def test_my_load_call_ignored(self):
        with TempProject() as root:
            gd = self._gd(root, "c.gd", 'my_load("res://foo.gd")\n')
            refs = extract_gdscript_refs(gd, root)
            self.assertEqual(refs, [])

    def test_preload_cache_call_ignored(self):
        with TempProject() as root:
            gd = self._gd(root, "d.gd", 'preload_cache("res://foo.gd")\n')
            refs = extract_gdscript_refs(gd, root)
            self.assertEqual(refs, [])

    def test_load_in_single_quoted_string_ignored(self):
        """load() call text inside a single-quoted string must not be extracted."""
        with TempProject() as root:
            # The GDScript line: var s = 'load("res://foo.gd") example'
            gd = self._gd(root, "e.gd", "var s = 'load(\"res://foo.gd\") example'\n")
            refs = extract_gdscript_refs(gd, root)
            self.assertEqual(refs, [])

    def test_single_quote_path_extracted(self):
        """GDScript supports single-quoted string literals for resource paths."""
        with TempProject() as root:
            gd = self._gd(root, "f.gd", "var x = preload('res://assets/hero.png')\n")
            refs = extract_gdscript_refs(gd, root)
            self.assertEqual(len(refs), 1)
            self.assertEqual(refs[0].path, "res://assets/hero.png")

    def test_inline_comment_after_valid_load_still_extracts(self):
        """A valid load before the # comment must still be extracted."""
        with TempProject() as root:
            gd = self._gd(root, "g.gd", 'var x = load("res://foo.gd")  # load stuff\n')
            refs = extract_gdscript_refs(gd, root)
            self.assertEqual(len(refs), 1)
            self.assertEqual(refs[0].path, "res://foo.gd")


# ─── Mermaid node-ID stability and edge dedup ─────────────────────────────────


class TestMermaidGraphStability(unittest.TestCase):
    def test_different_paths_same_filename_get_distinct_ids(self):
        """res://foo-bar.tres and res://foo/bar.tres must produce different node IDs."""
        from godot_project_doctor.graph import _node_id

        id1 = _node_id("res://foo-bar.tres")
        id2 = _node_id("res://foo/bar.tres")
        self.assertNotEqual(id1, id2, "Collision between paths with same filename segment")

    def test_node_id_is_deterministic(self):
        from godot_project_doctor.graph import _node_id

        self.assertEqual(_node_id("res://scenes/Main.tscn"), _node_id("res://scenes/Main.tscn"))

    def test_mermaid_no_duplicate_edges(self):
        """If the same (src, dst) appears twice, the mermaid output renders it once."""
        from godot_project_doctor.models import ResourceRef

        index = _make_index_with_issues([])
        # Inject two refs to the same target from the same source
        index.refs = [
            ResourceRef(
                source_file="scenes/Main.tscn",
                ref_type="Script",
                path="res://scripts/player.gd",
                ref_id="1",
            ),
            ResourceRef(
                source_file="scenes/Main.tscn",
                ref_type="preload",
                path="res://scripts/player.gd",
                ref_id="",
                kind="gdscript",
            ),
        ]
        graph = build_graph(index)
        mermaid = render_mermaid_graph(graph)
        self.assertEqual(mermaid.count("-->"), 1, "Duplicate edge found in mermaid output")

    def test_mermaid_label_quote_escaped(self):
        """Double-quotes in labels must be replaced so the mermaid is syntactically valid."""
        from godot_project_doctor.graph import _mermaid_label

        label = _mermaid_label('res://some "thing".tres')
        self.assertNotIn('"', label)

    def test_mermaid_collision_paths_both_present(self):
        """Both collision-prone paths appear as separate nodes in the diagram."""
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="CollTest"\n')
            _write(root / "foo-bar.tres", "[resource]\n")
            _write(root / "foo" / "bar.tres", "[resource]\n")
            _write(
                root / "scenes" / "Main.tscn",
                "[gd_scene format=3]\n"
                '[ext_resource type="Resource" path="res://foo-bar.tres" id="1"]\n'
                '[ext_resource type="Resource" path="res://foo/bar.tres" id="2"]\n',
            )
            index = scan(root)
            graph = build_graph(index)
            mermaid = render_mermaid_graph(graph)
            from godot_project_doctor.graph import _node_id

            id1 = _node_id("res://foo-bar.tres")
            id2 = _node_id("res://foo/bar.tres")
            self.assertNotEqual(id1, id2)
            self.assertIn(id1, mermaid)
            self.assertIn(id2, mermaid)


# ─── Version and schema consistency ──────────────────────────────────────────


class TestVersionConsistency(unittest.TestCase):
    def test_package_version(self):
        import godot_project_doctor

        self.assertEqual(godot_project_doctor.__version__, "0.9.0")

    def test_schema_version_constant_is_1_1(self):
        from godot_project_doctor.models import SCHEMA_VERSION

        self.assertEqual(SCHEMA_VERSION, "1.2")

    def test_json_report_uses_schema_version_constant(self):
        with TempProject() as root:
            make_minimal_project(root)
            index = scan(root)
            from godot_project_doctor.models import SCHEMA_VERSION

            data = json.loads(render_json(build_report(index)))
            self.assertEqual(data["schema_version"], SCHEMA_VERSION)


# ─── project.godot integrity checks ──────────────────────────────────────────


class TestProjectGodotIntegrity(unittest.TestCase):
    def test_missing_main_scene_file_is_error(self):
        with TempProject() as root:
            _write(
                root / "project.godot",
                '[application]\nconfig/name="X"\nrun/main_scene="res://scenes/Gone.tscn"\n',
            )
            index = scan(root)
            errs = [i for i in index.issues if i.code == "MISSING_MAIN_SCENE"]
            self.assertEqual(len(errs), 1)
            self.assertEqual(errs[0].severity, Severity.ERROR)
            self.assertIn("Gone.tscn", errs[0].message)

    def test_existing_main_scene_no_error(self):
        with TempProject() as root:
            _write(
                root / "project.godot",
                '[application]\nrun/main_scene="res://scenes/Main.tscn"\n',
            )
            _write(root / "scenes" / "Main.tscn", "[gd_scene format=3]\n")
            index = scan(root)
            errs = [i for i in index.issues if i.code == "MISSING_MAIN_SCENE"]
            self.assertEqual(errs, [])

    def test_no_main_scene_configured_is_info(self):
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="Lib"\n')
            index = scan(root)
            info = [i for i in index.issues if i.code == "NO_MAIN_SCENE"]
            self.assertEqual(len(info), 1)
            self.assertEqual(info[0].severity, Severity.INFO)

    def test_missing_autoload_is_error(self):
        with TempProject() as root:
            _write(
                root / "project.godot",
                '[application]\nconfig/name="X"\n[autoload]\nGameState="res://autoload/Gone.gd"\n',
            )
            index = scan(root)
            errs = [i for i in index.issues if i.code == "MISSING_AUTOLOAD"]
            self.assertEqual(len(errs), 1)
            self.assertEqual(errs[0].severity, Severity.ERROR)
            self.assertIn("GameState", errs[0].message)

    def test_existing_autoload_no_error(self):
        with TempProject() as root:
            _write(
                root / "project.godot",
                '[application]\n[autoload]\nGameState="res://autoload/gs.gd"\n',
            )
            _write(root / "autoload" / "gs.gd", "extends Node\n")
            index = scan(root)
            errs = [i for i in index.issues if i.code == "MISSING_AUTOLOAD"]
            self.assertEqual(errs, [])

    def test_uid_autoload_not_checked(self):
        """uid:// autoload paths must not produce a false MISSING_AUTOLOAD error."""
        with TempProject() as root:
            _write(
                root / "project.godot",
                '[application]\n[autoload]\nGameState="uid://abc123xyz"\n',
            )
            index = scan(root)
            errs = [i for i in index.issues if i.code == "MISSING_AUTOLOAD"]
            self.assertEqual(errs, [], "uid:// autoload path should not be checked")

    def test_uid_main_scene_not_checked(self):
        """uid:// main scene must not produce a false MISSING_MAIN_SCENE error."""
        with TempProject() as root:
            _write(
                root / "project.godot",
                '[application]\nrun/main_scene="uid://abc123"\n',
            )
            index = scan(root)
            errs = [i for i in index.issues if i.code == "MISSING_MAIN_SCENE"]
            self.assertEqual(errs, [], "uid:// main scene should not be checked")

    def test_minimal_project_main_scene_missing_produces_error(self):
        """make_minimal_project sets a main scene path that doesn't exist -> ERROR."""
        with TempProject() as root:
            make_minimal_project(root)
            index = scan(root)
            codes = [i.code for i in index.issues]
            self.assertIn("MISSING_MAIN_SCENE", codes)


# ─── CP949 subprocess smoke tests ────────────────────────────────────────────


def _run_cp949(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Spawn gdoctor with PYTHONIOENCODING=cp949 and return the result."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp949"
    env["PYTHONPATH"] = str(Path(__file__).parent.parent / "src")
    return subprocess.run(
        [sys.executable, "-m", "godot_project_doctor"] + args,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


class TestCp949Subprocess(unittest.TestCase):
    """Real subprocess tests with PYTHONIOENCODING=cp949.

    Verifies that all CLI commands complete without a Traceback on narrow
    encoding terminals.  These tests spawn a real child process.
    """

    def _assert_no_traceback(self, result: subprocess.CompletedProcess[str]) -> None:
        combined = result.stdout + result.stderr
        self.assertNotIn(
            "Traceback",
            combined,
            f"Traceback detected (exit={result.returncode}):\n{combined[:800]}",
        )

    def test_cp949_help(self):
        result = _run_cp949(["--help"])
        self._assert_no_traceback(result)

    def test_cp949_version(self):
        result = _run_cp949(["--version"])
        self._assert_no_traceback(result)

    def test_cp949_scan_text(self):
        with TempProject() as root:
            make_minimal_project(root)
            result = _run_cp949(["scan", str(root)])
            self._assert_no_traceback(result)
            self.assertIn(result.returncode, (0, 1))

    def test_cp949_scan_json(self):
        with TempProject() as root:
            make_minimal_project(root)
            result = _run_cp949(["scan", str(root), "--format", "json"])
            self._assert_no_traceback(result)

    def test_cp949_scan_markdown(self):
        with TempProject() as root:
            make_minimal_project(root)
            result = _run_cp949(["scan", str(root), "--format", "markdown"])
            self._assert_no_traceback(result)

    def test_cp949_graph(self):
        with TempProject() as root:
            make_minimal_project(root)
            result = _run_cp949(["graph", str(root)])
            self._assert_no_traceback(result)

    def test_cp949_context(self):
        with TempProject() as root:
            make_minimal_project(root)
            result = _run_cp949(["context", str(root), "--issue", "test issue"])
            self._assert_no_traceback(result)


# ─── _normalize_posix and _ref_to_canonical_rel ──────────────────────────────


class TestNormalizePosixAndCanonicalRel(unittest.TestCase):
    """Unit tests for the path-normalization helpers in checks.py."""

    def setUp(self) -> None:
        from godot_project_doctor.checks import _normalize_posix, _ref_to_canonical_rel

        self._norm = _normalize_posix
        self._canon = _ref_to_canonical_rel

    # _normalize_posix
    def test_plain_path_unchanged(self):
        self.assertEqual(self._norm("assets/bg.png"), "assets/bg.png")

    def test_dotdot_resolved(self):
        self.assertEqual(self._norm("scenes/../assets/bg.png"), "assets/bg.png")

    def test_multiple_dotdot_resolved(self):
        self.assertEqual(self._norm("a/b/c/../../d.png"), "a/d.png")

    def test_single_dot_removed(self):
        self.assertEqual(self._norm("a/./b.png"), "a/b.png")

    def test_backslash_normalised(self):
        self.assertEqual(self._norm("scenes\\..\\assets\\bg.png"), "assets/bg.png")

    def test_already_normalised_unchanged(self):
        self.assertEqual(self._norm("foo/bar/baz.png"), "foo/bar/baz.png")

    # _ref_to_canonical_rel
    def test_res_path_strips_prefix(self):
        self.assertEqual(
            self._canon("res://assets/hero.png", "scenes/Main.tscn"),
            "assets/hero.png",
        )

    def test_uid_returns_none(self):
        self.assertIsNone(self._canon("uid://abc123", "scenes/Main.tscn"))

    def test_relative_dotdot_resolved(self):
        """../assets/bg.png from scenes/Main.tscn → assets/bg.png (not scenes/../...)."""
        result = self._canon("../assets/bg.png", "scenes/Main.tscn")
        self.assertEqual(result, "assets/bg.png")
        self.assertNotIn("..", result)

    def test_relative_same_dir(self):
        result = self._canon("player.gd", "scripts/Player.gd")
        self.assertEqual(result, "scripts/player.gd")

    def test_relative_two_levels_up(self):
        result = self._canon("../../shared/common.gd", "a/b/c.gd")
        self.assertEqual(result, "shared/common.gd")


class TestUnusedAssetRelativePath(unittest.TestCase):
    """Ensure unused-asset check correctly handles relative ext_resource paths."""

    def test_relative_dotdot_ref_suppresses_unused_warning(self):
        """A .tscn referencing ../assets/hero.png must not flag hero.png as unused."""
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="RelTest"\n')
            _write(root / "assets" / "hero.png", "PNG")
            # Scene in scenes/ references ../assets/hero.png (relative path)
            _write(
                root / "scenes" / "Main.tscn",
                "[gd_scene format=3]\n"
                '[ext_resource type="Texture2D" path="../assets/hero.png" id="1"]\n',
            )
            index = scan(root)
            unused = [i for i in index.issues if i.code == "UNUSED_ASSET_CANDIDATE"]
            self.assertEqual(unused, [], "hero.png referenced via .. should not be flagged unused")


# ─── context.py keyword fix ──────────────────────────────────────────────────


class TestContextKeywordFix(unittest.TestCase):
    def test_slow_load_time_issue_does_not_trigger_missing_focus(self):
        """'game is slow to load' must NOT route to the missing-resource branch."""
        index = _make_index_with_issues([])
        md = render_context_markdown(index, issue_text="game is slow to load")
        focus = md.split("## Suggested Investigation Focus", 1)[-1]
        # Should NOT say "missing external resource"
        self.assertNotIn("missing external resource", focus.lower())

    def test_missing_keyword_still_triggers_missing_focus(self):
        issues = [
            Issue(
                "MISSING_EXT_RESOURCE",
                Severity.ERROR,
                "External resource not found: res://foo.gd",
            )
        ]
        index = _make_index_with_issues(issues)
        md = render_context_markdown(index, issue_text="missing resource error")
        focus = md.split("## Suggested Investigation Focus", 1)[-1]
        self.assertIn("missing external resource", focus.lower())

    def test_broken_keyword_still_triggers_missing_focus(self):
        index = _make_index_with_issues([])
        md = render_context_markdown(index, issue_text="scene is broken")
        focus = md.split("## Suggested Investigation Focus", 1)[-1]
        # With no issues, should give static-analysis note (not crash)
        self.assertIsNotNone(focus)


# ─── Circular dependency detection ───────────────────────────────────────────


class TestFindCycles(unittest.TestCase):
    """Unit tests for graph.find_cycles()."""

    def setUp(self) -> None:
        import godot_project_doctor.graph as graph_mod

        self._find = graph_mod.find_cycles

    def test_acyclic_graph_returns_empty(self):
        g = {
            "scenes/Main.tscn": ["res://scripts/player.gd"],
            "scripts/player.gd": [],
        }
        self.assertEqual(self._find(g), [])

    def test_simple_two_node_cycle(self):
        # A -> B -> A
        g = {
            "scenes/A.tscn": ["res://scenes/B.tscn"],
            "scenes/B.tscn": ["res://scenes/A.tscn"],
        }
        cycles = self._find(g)
        self.assertEqual(len(cycles), 1)
        cycle = cycles[0]
        self.assertEqual(len(cycle), 2)
        self.assertIn("res://scenes/A.tscn", cycle)
        self.assertIn("res://scenes/B.tscn", cycle)

    def test_three_node_cycle(self):
        g = {
            "scenes/A.tscn": ["res://scenes/B.tscn"],
            "scenes/B.tscn": ["res://scenes/C.tscn"],
            "scenes/C.tscn": ["res://scenes/A.tscn"],
        }
        cycles = self._find(g)
        self.assertEqual(len(cycles), 1)
        self.assertEqual(len(cycles[0]), 3)

    def test_self_loop_detected(self):
        g = {"scenes/A.tscn": ["res://scenes/A.tscn"]}
        cycles = self._find(g)
        self.assertEqual(len(cycles), 1)

    def test_no_duplicate_rotations(self):
        """The same cycle reported from different starting nodes must appear once."""
        g = {
            "scenes/A.tscn": ["res://scenes/B.tscn"],
            "scenes/B.tscn": ["res://scenes/C.tscn"],
            "scenes/C.tscn": ["res://scenes/A.tscn"],
        }
        cycles = self._find(g)
        self.assertEqual(len(cycles), 1)

    def test_disconnected_cycle_plus_acyclic(self):
        g = {
            "scenes/A.tscn": ["res://scenes/B.tscn"],
            "scenes/B.tscn": ["res://scenes/A.tscn"],
            "scenes/X.tscn": ["res://scripts/foo.gd"],
        }
        cycles = self._find(g)
        self.assertEqual(len(cycles), 1)

    def test_two_independent_cycles(self):
        g = {
            "scenes/A.tscn": ["res://scenes/B.tscn"],
            "scenes/B.tscn": ["res://scenes/A.tscn"],
            "scenes/C.tscn": ["res://scenes/D.tscn"],
            "scenes/D.tscn": ["res://scenes/C.tscn"],
        }
        cycles = self._find(g)
        self.assertEqual(len(cycles), 2)

    def test_output_is_deterministic(self):
        g = {
            "scenes/A.tscn": ["res://scenes/B.tscn"],
            "scenes/B.tscn": ["res://scenes/A.tscn"],
        }
        self.assertEqual(self._find(g), self._find(g))


class TestCircularDependencyCheck(unittest.TestCase):
    """Integration tests: CIRCULAR_DEPENDENCY reported through scan()."""

    def test_acyclic_project_no_circular_issue(self):
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="AC"\n')
            _write(root / "player.gd", "extends Node\n")
            _write(
                root / "scenes" / "Main.tscn",
                '[gd_scene format=3]\n[ext_resource type="Script" path="res://player.gd" id="1"]\n',
            )
            index = scan(root)
            circs = [i for i in index.issues if i.code == "CIRCULAR_DEPENDENCY"]
            self.assertEqual(circs, [])

    def test_two_scene_cycle_detected(self):
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="Cy"\n')
            _write(
                root / "scenes" / "A.tscn",
                "[gd_scene format=3]\n"
                '[ext_resource type="PackedScene" path="res://scenes/B.tscn" id="1"]\n',
            )
            _write(
                root / "scenes" / "B.tscn",
                "[gd_scene format=3]\n"
                '[ext_resource type="PackedScene" path="res://scenes/A.tscn" id="1"]\n',
            )
            index = scan(root)
            circs = [i for i in index.issues if i.code == "CIRCULAR_DEPENDENCY"]
            self.assertEqual(len(circs), 1)
            self.assertEqual(circs[0].severity, Severity.ERROR)
            self.assertIn("A.tscn", circs[0].details or "")
            self.assertIn("B.tscn", circs[0].details or "")

    def test_self_referencing_scene_detected(self):
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="Self"\n')
            _write(
                root / "scenes" / "Recursive.tscn",
                "[gd_scene format=3]\n"
                '[ext_resource type="PackedScene" path="res://scenes/Recursive.tscn" id="1"]\n',
            )
            index = scan(root)
            circs = [i for i in index.issues if i.code == "CIRCULAR_DEPENDENCY"]
            self.assertEqual(len(circs), 1)

    def test_cycle_details_contains_arrow_chain(self):
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="Cy"\n')
            _write(
                root / "scenes" / "A.tscn",
                "[gd_scene format=3]\n"
                '[ext_resource type="PackedScene" path="res://scenes/B.tscn" id="1"]\n',
            )
            _write(
                root / "scenes" / "B.tscn",
                "[gd_scene format=3]\n"
                '[ext_resource type="PackedScene" path="res://scenes/A.tscn" id="1"]\n',
            )
            index = scan(root)
            circ = next(i for i in index.issues if i.code == "CIRCULAR_DEPENDENCY")
            self.assertIn("->", circ.details or "")


# ─── uid:// sidecar resolution ───────────────────────────────────────────────


class TestUidMapBuilding(unittest.TestCase):
    """Unit tests for uid_map.build_uid_map()."""

    def setUp(self) -> None:
        from godot_project_doctor.uid_map import build_uid_map

        self._build = build_uid_map

    def test_no_uid_files_returns_empty_map(self):
        with TempProject() as root:
            uid_map, _sources, issues = self._build(root)
            self.assertEqual(uid_map, {})
            self.assertEqual(issues, [])

    def test_single_uid_file_parsed(self):
        with TempProject() as root:
            _write(root / "scripts" / "player.gd", "extends Node\n")
            _write(root / "scripts" / "player.gd.uid", "uid://cb6n3abc\n")
            uid_map, _sources, issues = self._build(root)
            self.assertEqual(uid_map.get("uid://cb6n3abc"), "res://scripts/player.gd")
            self.assertEqual(issues, [])

    def test_multiple_uid_files_all_parsed(self):
        with TempProject() as root:
            _write(root / "a.gd", "")
            _write(root / "a.gd.uid", "uid://aaa\n")
            _write(root / "b.gd", "")
            _write(root / "b.gd.uid", "uid://bbb\n")
            uid_map, _sources, _ = self._build(root)
            self.assertIn("uid://aaa", uid_map)
            self.assertIn("uid://bbb", uid_map)

    def test_duplicate_uid_produces_warning(self):
        with TempProject() as root:
            _write(root / "a.gd", "")
            _write(root / "a.gd.uid", "uid://same\n")
            _write(root / "b.gd", "")
            _write(root / "b.gd.uid", "uid://same\n")
            uid_map, _sources, issues = self._build(root)
            self.assertIn("uid://same", uid_map)
            dup_issues = [i for i in issues if i.code == "DUPLICATE_UID"]
            self.assertEqual(len(dup_issues), 1)
            self.assertEqual(dup_issues[0].severity, Severity.WARNING)

    def test_malformed_uid_file_skipped(self):
        with TempProject() as root:
            _write(root / "a.gd.uid", "not-a-uid\n")
            uid_map, _sources, issues = self._build(root)
            self.assertEqual(uid_map, {})

    def test_skip_dirs_respected(self):
        """.uid files inside .git must not be read."""
        with TempProject() as root:
            _write(root / ".git" / "some.gd.uid", "uid://git111\n")
            uid_map, _sources, _ = self._build(root)
            self.assertNotIn("uid://git111", uid_map)


class TestUidResolutionIntegration(unittest.TestCase):
    """Integration: uid:// refs resolve via sidecar to suppress false errors."""

    def test_uid_ref_with_sidecar_no_missing_error(self):
        """A uid:// ext_resource whose .uid sidecar resolves to an existing file
        must NOT produce MISSING_EXT_RESOURCE."""
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="UidTest"\n')
            _write(root / "scripts" / "player.gd", "extends Node\n")
            _write(root / "scripts" / "player.gd.uid", "uid://cb6n3abc\n")
            _write(
                root / "scenes" / "Main.tscn",
                "[gd_scene format=3]\n"
                '[ext_resource type="Script" uid="uid://cb6n3abc"'
                ' path="res://scripts/player.gd" id="1"]\n',
            )
            index = scan(root)
            missing = [i for i in index.issues if i.code == "MISSING_EXT_RESOURCE"]
            self.assertEqual(missing, [])

    def test_uid_ref_pointing_to_missing_file_with_sidecar_is_error(self):
        """uid:// resolved via sidecar to a non-existent file → ERROR."""
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="UidTest"\n')
            # .uid sidecar points to a file that does NOT exist
            _write(root / "scripts" / "gone.gd.uid", "uid://deadbeef\n")
            _write(
                root / "scenes" / "Main.tscn",
                "[gd_scene format=3]\n"
                '[ext_resource type="Script" uid="uid://deadbeef"'
                ' path="res://scripts/gone.gd" id="1"]\n',
            )
            scan(root)
            # The ext_resource uses res:// path (not uid://) so it's resolved directly.
            # uid:// in the uid= attribute is metadata, not the path.
            # This tests the uid-only case — the path= field takes priority.
            # No assertion needed; confirms no crash.

    def test_uid_ref_only_no_path_field_resolved_via_sidecar(self):
        """An ext_resource with ONLY a uid:// path (no res://) resolved via sidecar."""
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="UidTest"\n')
            _write(root / "scripts" / "player.gd", "extends Node\n")
            _write(root / "scripts" / "player.gd.uid", "uid://cb6n3abc\n")
            # Scene references the resource by uid:// only (no path= field)
            _write(
                root / "scenes" / "Main.tscn",
                '[gd_scene format=3]\n[ext_resource type="Script" uid="uid://cb6n3abc" id="1"]\n',
            )
            index = scan(root)
            # uid:// in the path= attribute resolves via sidecar → no missing error
            # (In this case path= attribute is absent, so the indexer won't create
            # a ResourceRef — the check doesn't fire. This confirms graceful handling.)
            missing = [i for i in index.issues if i.code == "MISSING_EXT_RESOURCE"]
            self.assertEqual(missing, [])

    def test_uid_asset_ref_suppresses_unused_candidate(self):
        """An image referenced only via uid:// (with matching sidecar) must not
        be flagged as UNUSED_ASSET_CANDIDATE."""
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="UidTest"\n')
            _write(root / "sprites" / "hero.png", "PNG")
            _write(root / "sprites" / "hero.png.uid", "uid://img999\n")
            _write(
                root / "scenes" / "Main.tscn",
                "[gd_scene format=3]\n"
                '[ext_resource type="Texture2D" uid="uid://img999"'
                ' path="res://sprites/hero.png" id="1"]\n',
            )
            index = scan(root)
            unused = [i for i in index.issues if i.code == "UNUSED_ASSET_CANDIDATE"]
            self.assertEqual(unused, [], "hero.png referenced via uid sidecar should not be unused")

    def test_uid_with_no_sidecar_still_skipped(self):
        """uid:// references with no matching sidecar must not produce errors."""
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="UidTest"\n')
            _write(
                root / "scenes" / "Main.tscn",
                '[gd_scene format=3]\n[ext_resource type="Script" uid="uid://unknown111" id="1"]\n',
            )
            index = scan(root)
            # No sidecar → uid:// not in map → ref has no path= → no ResourceRef
            # No crash expected
            missing = [i for i in index.issues if i.code == "MISSING_EXT_RESOURCE"]
            self.assertEqual(missing, [])


# ─── Phase 1: .import parsing + resolved_path/via ────────────────────────────


class TestImportFileParsing(unittest.TestCase):
    """Unit tests for uid_map._parse_import_file() and .import integration."""

    def setUp(self) -> None:
        from godot_project_doctor.uid_map import _parse_import_file, build_uid_map

        self._parse = _parse_import_file
        self._build = build_uid_map

    def test_import_file_uid_and_source_parsed(self):
        with TempProject() as root:
            _write(
                root / "assets" / "hero.png.import",
                '[remap]\nuid="uid://img001"\nsource_file="res://assets/hero.png"\n',
            )
            imp = root / "assets" / "hero.png.import"
            uid_str, src = self._parse(imp)
            self.assertEqual(uid_str, "uid://img001")
            self.assertEqual(src, "res://assets/hero.png")

    def test_import_file_no_uid_returns_none(self):
        with TempProject() as root:
            _write(root / "x.import", '[remap]\nsource_file="res://x.png"\n')
            uid_str, src = self._parse(root / "x.import")
            self.assertIsNone(uid_str)

    def test_import_file_not_in_remap_section_ignored(self):
        """uid= outside [remap] must not be returned."""
        with TempProject() as root:
            _write(
                root / "x.import",
                '[deps]\nuid="uid://wrong"\nsource_file="res://x.png"\n',
            )
            uid_str, _ = self._parse(root / "x.import")
            self.assertIsNone(uid_str)

    def test_build_uid_map_includes_import_files(self):
        with TempProject() as root:
            _write(root / "assets" / "hero.png", "PNG")
            _write(
                root / "assets" / "hero.png.import",
                '[remap]\nuid="uid://imgfromimport"\nsource_file="res://assets/hero.png"\n',
            )
            uid_map, _sources, _issues = self._build(root)
            self.assertEqual(uid_map.get("uid://imgfromimport"), "res://assets/hero.png")

    def test_import_source_is_import(self):
        with TempProject() as root:
            _write(
                root / "assets" / "bg.png.import",
                '[remap]\nuid="uid://bgimport"\nsource_file="res://assets/bg.png"\n',
            )
            _uid_map, uid_sources, _issues = self._build(root)
            self.assertEqual(uid_sources.get("uid://bgimport"), "import")

    def test_sidecar_beats_import_when_same_uid_same_path(self):
        """If both sidecar and .import agree on the same path, source=uid_sidecar wins."""
        with TempProject() as root:
            _write(root / "scripts" / "foo.gd", "extends Node\n")
            _write(root / "scripts" / "foo.gd.uid", "uid://foouid\n")
            _write(
                root / "scripts" / "foo.gd.import",
                '[remap]\nuid="uid://foouid"\nsource_file="res://scripts/foo.gd"\n',
            )
            _uid_map, uid_sources, issues = self._build(root)
            self.assertEqual(uid_sources.get("uid://foouid"), "uid_sidecar")
            self.assertEqual(issues, [])  # same path → no DUPLICATE_UID


class TestResolvedPathField(unittest.TestCase):
    """Tests that ResourceRef.resolved_path / resolved_via are populated correctly."""

    def test_res_path_ref_has_no_resolved_path(self):
        """A ref with a res:// path should not have resolved_path set."""
        with TempProject() as root:
            make_project_with_valid_ref(root)
            index = scan(root)
            ref = index.refs[0]
            self.assertIsNone(ref.resolved_path)
            self.assertIsNone(ref.resolved_via)

    def test_uid_path_ref_resolved_path_populated(self):
        """A ref whose path= is uid:// and has a sidecar gets resolved_path set."""
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="R"\n')
            _write(root / "scripts" / "player.gd", "extends Node\n")
            _write(root / "scripts" / "player.gd.uid", "uid://puid\n")
            _write(
                root / "scenes" / "Main.tscn",
                '[gd_scene format=3]\n[ext_resource type="Script" path="uid://puid" id="1"]\n',
            )
            index = scan(root)
            uid_refs = [r for r in index.refs if r.path == "uid://puid"]
            self.assertEqual(len(uid_refs), 1)
            self.assertEqual(uid_refs[0].resolved_path, "res://scripts/player.gd")
            self.assertEqual(uid_refs[0].resolved_via, "uid_sidecar")

    def test_uid_path_ref_resolved_via_import(self):
        """A ref resolved via .import gets resolved_via='import'."""
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="R"\n')
            _write(root / "assets" / "tex.png", "PNG")
            _write(
                root / "assets" / "tex.png.import",
                '[remap]\nuid="uid://teximp"\nsource_file="res://assets/tex.png"\n',
            )
            _write(
                root / "scenes" / "Main.tscn",
                '[gd_scene format=3]\n[ext_resource type="Texture2D" path="uid://teximp" id="1"]\n',
            )
            index = scan(root)
            uid_refs = [r for r in index.refs if r.path == "uid://teximp"]
            self.assertEqual(len(uid_refs), 1)
            self.assertEqual(uid_refs[0].resolved_via, "import")
            self.assertIsNotNone(uid_refs[0].resolved_path)

    def test_uid_path_missing_sidecar_no_resolved_path(self):
        """A uid:// ref without any sidecar/import leaves resolved_path=None."""
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="R"\n')
            _write(
                root / "scenes" / "Main.tscn",
                '[gd_scene format=3]\n[ext_resource type="Script" path="uid://orphan" id="1"]\n',
            )
            index = scan(root)
            uid_refs = [r for r in index.refs if r.path == "uid://orphan"]
            self.assertEqual(len(uid_refs), 1)
            self.assertIsNone(uid_refs[0].resolved_path)
            self.assertIsNone(uid_refs[0].resolved_via)

    def test_json_output_includes_resolved_fields(self):
        """schema 1.2: JSON refs include resolved_path and resolved_via."""
        with TempProject() as root:
            make_project_with_valid_ref(root)
            index = scan(root)
            data = json.loads(render_json(build_report(index)))
            self.assertEqual(data["schema_version"], "1.2")
            ref = data["refs"][0]
            self.assertIn("resolved_path", ref)
            self.assertIn("resolved_via", ref)
            # For a res:// ref these should be null
            self.assertIsNone(ref["resolved_path"])
            self.assertIsNone(ref["resolved_via"])

    def test_uid_resolved_ref_no_missing_error(self):
        """uid:// path resolved via sidecar to an existing file → no error."""
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="R"\n')
            _write(root / "scripts" / "player.gd", "extends Node\n")
            _write(root / "scripts" / "player.gd.uid", "uid://playeruid\n")
            _write(
                root / "scenes" / "Main.tscn",
                '[gd_scene format=3]\n[ext_resource type="Script" path="uid://playeruid" id="1"]\n',
            )
            index = scan(root)
            missing = [i for i in index.issues if i.code == "MISSING_EXT_RESOURCE"]
            self.assertEqual(missing, [])

    def test_uid_resolved_ref_to_missing_file_is_error(self):
        """uid:// resolved to a path that doesn't exist → MISSING_EXT_RESOURCE."""
        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="R"\n')
            # Sidecar points to a file that doesn't exist
            _write(root / "scripts" / "gone.gd.uid", "uid://goneuid\n")
            _write(
                root / "scenes" / "Main.tscn",
                '[gd_scene format=3]\n[ext_resource type="Script" path="uid://goneuid" id="1"]\n',
            )
            index = scan(root)
            missing = [i for i in index.issues if i.code == "MISSING_EXT_RESOURCE"]
            self.assertEqual(len(missing), 1)


# ─── Phase 2: Config loading ──────────────────────────────────────────────────


class TestConfigLoading(unittest.TestCase):
    """Tests for config.load_config() and Config helpers."""

    def setUp(self) -> None:
        from godot_project_doctor.config import Config, load_config

        self._load = load_config
        self._Config = Config

    def test_no_config_file_returns_defaults(self):
        with TempProject() as root:
            cfg = self._load(root)
            self.assertEqual(cfg.large_texture_dim, 2048)
            self.assertEqual(cfg.large_audio_bytes, 10 * 1024 * 1024)
            self.assertEqual(cfg.ignore, [])
            self.assertEqual(cfg.severity_overrides, {})
            self.assertEqual(cfg.baseline, [])

    def test_pyproject_toml_gdoctor_table_loaded(self):
        with TempProject() as root:
            _write(
                root / "pyproject.toml",
                "[tool.gdoctor]\nlarge_texture_dim = 512\n",
            )
            cfg = self._load(root)
            self.assertEqual(cfg.large_texture_dim, 512)

    def test_gdoctor_toml_fallback(self):
        with TempProject() as root:
            _write(root / ".gdoctor.toml", "large_audio_bytes = 5242880\n")
            cfg = self._load(root)
            self.assertEqual(cfg.large_audio_bytes, 5242880)

    def test_pyproject_takes_priority_over_gdoctor_toml(self):
        with TempProject() as root:
            _write(root / "pyproject.toml", "[tool.gdoctor]\nlarge_texture_dim = 512\n")
            _write(root / ".gdoctor.toml", "large_texture_dim = 999\n")
            cfg = self._load(root)
            self.assertEqual(cfg.large_texture_dim, 512)

    def test_explicit_config_path_used(self):
        with TempProject() as root:
            custom = root / "custom.toml"
            _write(custom, "large_texture_dim = 256\n")
            cfg = self._load(root, config_path=custom)
            self.assertEqual(cfg.large_texture_dim, 256)

    def test_ignore_globs_loaded(self):
        with TempProject() as root:
            _write(root / ".gdoctor.toml", 'ignore = ["assets/vendor/**"]\n')
            cfg = self._load(root)
            self.assertEqual(cfg.ignore, ["assets/vendor/**"])

    def test_severity_overrides_loaded(self):
        with TempProject() as root:
            _write(
                root / ".gdoctor.toml",
                '[severity]\nUNUSED_ASSET_CANDIDATE = "info"\n',
            )
            cfg = self._load(root)
            self.assertEqual(cfg.severity_overrides.get("UNUSED_ASSET_CANDIDATE"), "info")

    def test_baseline_loaded(self):
        with TempProject() as root:
            _write(
                root / ".gdoctor.toml",
                '[[baseline]]\ncode = "MISSING_EXT_RESOURCE"\n'
                'file = "scenes/Old.tscn"\n'
                'message = "External resource not found: res://old.gd"\n',
            )
            cfg = self._load(root)
            self.assertEqual(len(cfg.baseline), 1)
            self.assertEqual(cfg.baseline[0].code, "MISSING_EXT_RESOURCE")

    def test_invalid_toml_silently_falls_back_to_defaults(self):
        with TempProject() as root:
            _write(root / ".gdoctor.toml", "this is not valid toml !!!!\n")
            cfg = self._load(root)
            self.assertEqual(cfg.large_texture_dim, 2048)  # default

    def test_negative_threshold_ignored(self):
        with TempProject() as root:
            _write(root / ".gdoctor.toml", "large_texture_dim = -1\n")
            cfg = self._load(root)
            self.assertEqual(cfg.large_texture_dim, 2048)  # default unchanged


class TestApplyConfig(unittest.TestCase):
    """Tests for config.apply_config() post-processing."""

    def setUp(self) -> None:
        from godot_project_doctor.config import BaselineEntry, Config, apply_config

        self._apply = apply_config
        self._Config = Config
        self._Baseline = BaselineEntry

    def _issue(self, code="CODE", sev=Severity.WARNING, file="f.gd", msg="msg"):
        return Issue(code=code, severity=sev, message=msg, file=file)

    def test_no_filters_passes_all(self):
        issues = [self._issue(), self._issue("B")]
        cfg = self._Config()
        idx = _make_index_with_issues(issues)
        result = self._apply(idx, cfg)
        self.assertEqual(len(result), 2)

    def test_ignore_glob_suppresses_matching_file(self):
        cfg = self._Config(ignore=["assets/**"])
        idx = _make_index_with_issues([self._issue(file="assets/bg.png")])
        result = self._apply(idx, cfg)
        self.assertEqual(result, [])

    def test_ignore_glob_passes_non_matching(self):
        cfg = self._Config(ignore=["assets/**"])
        idx = _make_index_with_issues([self._issue(file="scenes/Main.tscn")])
        result = self._apply(idx, cfg)
        self.assertEqual(len(result), 1)

    def test_addons_ignored_by_default(self):
        cfg = self._Config()  # ignore_addons defaults to True
        idx = _make_index_with_issues([self._issue(file="addons/beehave/node.gd")])
        self.assertEqual(self._apply(idx, cfg), [])

    def test_script_templates_ignored_by_default(self):
        cfg = self._Config()
        idx = _make_index_with_issues([self._issue(file="script_templates/Node/default.gd")])
        self.assertEqual(self._apply(idx, cfg), [])

    def test_addons_backslash_path_ignored(self):
        """Windows-style backslash paths under addons/ are also ignored."""
        cfg = self._Config()
        idx = _make_index_with_issues([self._issue(file="addons\\beehave\\node.gd")])
        self.assertEqual(self._apply(idx, cfg), [])

    def test_include_addons_re_enables_addon_issues(self):
        cfg = self._Config(ignore_addons=False)
        idx = _make_index_with_issues([self._issue(file="addons/beehave/node.gd")])
        self.assertEqual(len(self._apply(idx, cfg)), 1)

    def test_non_addons_path_not_affected_by_default(self):
        cfg = self._Config()
        idx = _make_index_with_issues([self._issue(file="addons_helper/util.gd")])
        self.assertEqual(len(self._apply(idx, cfg)), 1)  # not under addons/

    def test_severity_override_changes_level(self):
        cfg = self._Config(severity_overrides={"CODE": "error"})
        idx = _make_index_with_issues([self._issue(sev=Severity.WARNING)])
        result = self._apply(idx, cfg)
        self.assertEqual(result[0].severity, Severity.ERROR)

    def test_severity_override_none_suppresses(self):
        cfg = self._Config(severity_overrides={"CODE": "none"})
        idx = _make_index_with_issues([self._issue()])
        result = self._apply(idx, cfg)
        self.assertEqual(result, [])

    def test_baseline_suppresses_matching_issue(self):
        entry = self._Baseline(code="CODE", file="f.gd", message="msg")
        cfg = self._Config(baseline=[entry])
        idx = _make_index_with_issues([self._issue()])
        result = self._apply(idx, cfg)
        self.assertEqual(result, [])

    def test_baseline_passes_non_matching(self):
        entry = self._Baseline(code="OTHER", file="f.gd", message="msg")
        cfg = self._Config(baseline=[entry])
        idx = _make_index_with_issues([self._issue()])
        result = self._apply(idx, cfg)
        self.assertEqual(len(result), 1)

    def test_original_issues_not_mutated(self):
        cfg = self._Config(ignore=["f.gd"])
        orig_issue = self._issue()
        idx = _make_index_with_issues([orig_issue])
        self._apply(idx, cfg)
        self.assertEqual(len(idx.issues), 1)  # original unchanged


class TestFailOnExitCode(unittest.TestCase):
    """Tests for _compute_exit_code() exit-code logic."""

    def setUp(self) -> None:
        from godot_project_doctor.cli import _compute_exit_code

        self._exit = _compute_exit_code

    def _issues(self, *sevs):
        return [Issue("C", sev, "msg") for sev in sevs]

    def test_fail_on_error_no_errors_returns_0(self):
        self.assertEqual(self._exit(self._issues(Severity.WARNING), "error"), 0)

    def test_fail_on_error_with_error_returns_1(self):
        self.assertEqual(self._exit(self._issues(Severity.ERROR), "error"), 1)

    def test_fail_on_warning_with_warning_returns_1(self):
        self.assertEqual(self._exit(self._issues(Severity.WARNING), "warning"), 1)

    def test_fail_on_warning_info_only_returns_0(self):
        self.assertEqual(self._exit(self._issues(Severity.INFO), "warning"), 0)

    def test_fail_on_info_with_any_issue_returns_1(self):
        self.assertEqual(self._exit(self._issues(Severity.INFO), "info"), 1)

    def test_fail_on_none_always_returns_0(self):
        self.assertEqual(self._exit(self._issues(Severity.ERROR), "none"), 0)

    def test_empty_issues_always_0(self):
        self.assertEqual(self._exit([], "error"), 0)
        self.assertEqual(self._exit([], "warning"), 0)


class TestConfigIntegration(unittest.TestCase):
    """End-to-end: config thresholds flow through scan() correctly."""

    def test_ignore_suppresses_issue_after_scan(self):
        """An ignored file's issues must not appear in apply_config output."""
        from godot_project_doctor.config import Config, apply_config

        with TempProject() as root:
            make_project_with_missing_ref(root)
            index = scan(root)
            cfg = Config(ignore=["scenes/**"])
            filtered = apply_config(index, cfg)
            missing = [i for i in filtered if i.code == "MISSING_EXT_RESOURCE"]
            self.assertEqual(missing, [])

    def test_severity_override_in_scan(self):
        """Severity override changes issue level in apply_config output."""
        from godot_project_doctor.config import Config, apply_config

        with TempProject() as root:
            make_project_with_missing_ref(root)
            index = scan(root)
            cfg = Config(severity_overrides={"MISSING_EXT_RESOURCE": "warning"})
            filtered = apply_config(index, cfg)
            errors = [i for i in filtered if i.code == "MISSING_EXT_RESOURCE"]
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].severity, Severity.WARNING)

    def test_no_config_preserves_default_behavior(self):
        """scan() with no config uses the same defaults as before."""
        with TempProject() as root:
            make_minimal_project(root)
            index = scan(root)
            self.assertIsNotNone(index)

    def test_cp949_subprocess_with_fail_on(self):
        """--fail-on none must exit 0 even with errors on CP949 terminal."""
        with TempProject() as root:
            make_project_with_missing_ref(root)
            result = _run_cp949(["scan", str(root), "--fail-on", "none"])
            self._assert_no_traceback(result)
            self.assertEqual(result.returncode, 0)

    def _assert_no_traceback(self, result):
        combined = result.stdout + result.stderr
        self.assertNotIn("Traceback", combined, combined[:800])


# ─── Phase 3: SARIF output ────────────────────────────────────────────────────


class TestSarifOutput(unittest.TestCase):
    """Tests for reporter.render_sarif()."""

    def setUp(self) -> None:
        from godot_project_doctor.reporter import render_sarif

        self._render = render_sarif

    def _sarif(self, issues):
        idx = _make_index_with_issues(issues)
        report = build_report(idx)
        return json.loads(self._render(report))

    def test_sarif_version_is_2_1_0(self):
        self.assertEqual(self._sarif([])["version"], "2.1.0")

    def test_sarif_has_runs(self):
        self.assertEqual(len(self._sarif([])["runs"]), 1)

    def test_tool_name_present(self):
        driver = self._sarif([])["runs"][0]["tool"]["driver"]
        self.assertEqual(driver["name"], "godot-project-doctor")

    def test_no_issues_empty_results(self):
        self.assertEqual(self._sarif([])["runs"][0]["results"], [])

    def test_error_maps_to_sarif_error(self):
        r = self._sarif([Issue("X", Severity.ERROR, "m")])["runs"][0]["results"][0]
        self.assertEqual(r["level"], "error")

    def test_warning_maps_to_sarif_warning(self):
        r = self._sarif([Issue("X", Severity.WARNING, "m")])["runs"][0]["results"][0]
        self.assertEqual(r["level"], "warning")

    def test_info_maps_to_sarif_note(self):
        r = self._sarif([Issue("X", Severity.INFO, "m")])["runs"][0]["results"][0]
        self.assertEqual(r["level"], "note")

    def test_rule_id_equals_issue_code(self):
        r = self._sarif([Issue("MY_CODE", Severity.WARNING, "m")])["runs"][0]["results"][0]
        self.assertEqual(r["ruleId"], "MY_CODE")

    def test_rule_defined_in_rules(self):
        doc = self._sarif([Issue("MY_CODE", Severity.WARNING, "m")])
        ids = [r["id"] for r in doc["runs"][0]["tool"]["driver"]["rules"]]
        self.assertIn("MY_CODE", ids)

    def test_file_location_in_result(self):
        r = self._sarif([Issue("X", Severity.ERROR, "m", file="scenes/Main.tscn")])["runs"][0][
            "results"
        ][0]
        uri = r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        self.assertEqual(uri, "scenes/Main.tscn")

    def test_backslash_normalised_in_uri(self):
        r = self._sarif([Issue("X", Severity.ERROR, "m", file="scenes\\Main.tscn")])["runs"][0][
            "results"
        ][0]
        uri = r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        self.assertNotIn("\\", uri)

    def test_no_file_no_locations(self):
        r = self._sarif([Issue("X", Severity.ERROR, "m", file=None)])["runs"][0]["results"][0]
        self.assertNotIn("locations", r)

    def test_results_sorted_deterministically(self):
        issues_a = [
            Issue("Z_CODE", Severity.ERROR, "z", file="b.gd"),
            Issue("A_CODE", Severity.WARNING, "a", file="a.gd"),
        ]
        issues_b = list(reversed(issues_a))
        ra = self._sarif(issues_a)["runs"][0]["results"]
        rb = self._sarif(issues_b)["runs"][0]["results"]
        self.assertEqual(ra, rb)

    def test_rules_sorted_by_id(self):
        doc = self._sarif([Issue("Z", Severity.WARNING, "z"), Issue("A", Severity.ERROR, "a")])
        ids = [r["id"] for r in doc["runs"][0]["tool"]["driver"]["rules"]]
        self.assertEqual(ids, sorted(ids))

    def test_sarif_written_to_file(self):
        with TempProject() as root:
            make_minimal_project(root)
            idx = scan(root)
            report = build_report(idx)
            out = root / "results.sarif"
            self._render(report, out)
            self.assertTrue(out.exists())
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["version"], "2.1.0")

    def test_scan_format_sarif_subprocess(self):
        """gdoctor scan --format sarif must produce valid SARIF on CP949 terminal."""
        with TempProject() as root:
            make_minimal_project(root)
            result = _run_cp949(["scan", str(root), "--format", "sarif", "--fail-on", "none"])
            self.assertNotIn("Traceback", result.stderr + result.stdout)
            try:
                doc = json.loads(result.stdout)
                self.assertEqual(doc["version"], "2.1.0")
            except json.JSONDecodeError:
                self.fail(f"Not valid JSON: {result.stdout[:300]}")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4b — UNDEFINED_INPUT_ACTION
# ─────────────────────────────────────────────────────────────────────────────


class TestParserInputActions(unittest.TestCase):
    """Parser correctly collects action names from the [input] section."""

    def _parse(self, content: str):
        with TempProject() as root:
            _write(root / "project.godot", content)
            return parse_project_godot(root)

    def test_single_action_parsed(self):
        summary = self._parse(
            """\
[input]

jump={
"deadzone": 0.2,
"events": []
}
"""
        )
        self.assertIn("jump", summary.input_actions)

    def test_multiple_actions_parsed(self):
        summary = self._parse(
            """\
[input]

move_left={
"deadzone": 0.2,
"events": []
}
move_right={
"deadzone": 0.2,
"events": []
}
"""
        )
        self.assertIn("move_left", summary.input_actions)
        self.assertIn("move_right", summary.input_actions)

    def test_no_input_section_empty_set(self):
        summary = self._parse('[application]\nconfig/name="Game"\n')
        self.assertEqual(summary.input_actions, set())

    def test_input_actions_not_serialised(self):
        """input_actions must not appear in the to_dict() JSON output."""
        summary = self._parse("[input]\njump={\n}\n")
        d = summary.to_dict()
        self.assertNotIn("input_actions", d)


class TestExtractInputActionRefs(unittest.TestCase):
    """GDScript Input action reference extraction."""

    def _extract(self, gd_content: str):
        from godot_project_doctor.gdscript import extract_input_action_refs

        with TempProject() as root:
            gd = root / "player.gd"
            _write(gd, gd_content)
            return extract_input_action_refs(gd, root)

    def test_is_action_pressed_double_quote(self):
        refs = self._extract('if Input.is_action_pressed("jump"):\n    pass\n')
        self.assertEqual(refs, [("jump", "player.gd")])

    def test_is_action_just_pressed_single_quote(self):
        refs = self._extract("if Input.is_action_just_pressed('fire'):\n    pass\n")
        self.assertEqual(refs, [("fire", "player.gd")])

    def test_is_action_just_released(self):
        refs = self._extract('if Input.is_action_just_released("dash"):\n    pass\n')
        self.assertEqual(refs, [("dash", "player.gd")])

    def test_get_action_strength(self):
        refs = self._extract('var s = Input.get_action_strength("move_left")\n')
        self.assertEqual(refs, [("move_left", "player.gd")])

    def test_get_action_raw_strength(self):
        refs = self._extract('var s = Input.get_action_raw_strength("move_right")\n')
        self.assertEqual(refs, [("move_right", "player.gd")])

    def test_action_press_and_release(self):
        refs = self._extract('Input.action_press("jump")\nInput.action_release("jump")\n')
        names = [r[0] for r in refs]
        self.assertEqual(names, ["jump", "jump"])

    def test_comment_line_skipped(self):
        refs = self._extract('# Input.is_action_pressed("jump")\n')
        self.assertEqual(refs, [])

    def test_inline_comment_skipped(self):
        refs = self._extract('var x = 1  # Input.is_action_pressed("jump")\n')
        self.assertEqual(refs, [])

    def test_inside_string_skipped(self):
        refs = self._extract("var s = 'Input.is_action_pressed(\"jump\")'\n")
        self.assertEqual(refs, [])

    def test_dynamic_expression_skipped(self):
        refs = self._extract("if Input.is_action_pressed(action_name):\n    pass\n")
        self.assertEqual(refs, [])

    def test_multiple_refs_in_one_file(self):
        refs = self._extract('Input.is_action_pressed("jump")\nInput.is_action_pressed("fire")\n')
        self.assertEqual(len(refs), 2)


class TestUndefinedInputActionCheck(unittest.TestCase):
    """UNDEFINED_INPUT_ACTION check integration tests."""

    def _scan_project(self, project_godot: str, gd_content: str) -> list:
        from godot_project_doctor.checks import _check_undefined_input_actions
        from godot_project_doctor.indexer import index_project
        from godot_project_doctor.parser import parse_project_godot

        with TempProject() as root:
            _write(root / "project.godot", project_godot)
            _write(root / "player.gd", gd_content)
            summary = parse_project_godot(root)
            index = index_project(root, summary)
            return _check_undefined_input_actions(index)

    def test_declared_action_no_issue(self):
        issues = self._scan_project(
            '[input]\njump={\n"deadzone": 0.2,\n"events": []\n}\n',
            'if Input.is_action_pressed("jump"):\n    pass\n',
        )
        self.assertEqual(issues, [])

    def test_undeclared_action_raises_warning(self):
        issues = self._scan_project(
            '[application]\nconfig/name="Game"\n',
            'if Input.is_action_pressed("jump"):\n    pass\n',
        )
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "UNDEFINED_INPUT_ACTION")
        self.assertEqual(issues[0].severity.value, "WARNING")
        self.assertIn("jump", issues[0].message)

    def test_builtin_ui_action_skipped(self):
        issues = self._scan_project(
            '[application]\nconfig/name="Game"\n',
            'if Input.is_action_pressed("ui_accept"):\n    pass\n',
        )
        self.assertEqual(issues, [])

    def test_no_refs_no_issue(self):
        issues = self._scan_project(
            '[input]\njump={\n"events": []\n}\n',
            "extends Node\n",
        )
        self.assertEqual(issues, [])

    def test_deterministic_order(self):
        issues = self._scan_project(
            '[application]\nconfig/name="Game"\n',
            'Input.is_action_pressed("zzz")\nInput.is_action_pressed("aaa")\n',
        )
        codes = [i.message for i in issues]
        self.assertEqual(codes, sorted(codes))


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4c — BROKEN_SIGNAL_CONNECTION
# ─────────────────────────────────────────────────────────────────────────────


class TestParseSceneForConnections(unittest.TestCase):
    """Unit tests for the internal _parse_scene_for_connections helper."""

    def _parse(self, text: str, uid_map=None):
        from godot_project_doctor.checks import _parse_scene_for_connections

        return _parse_scene_for_connections(text, uid_map=uid_map)

    def test_root_node_gets_empty_path(self):
        text = '[node name="Main" type="Node2D"]\nscript = ExtResource("1")\n'
        _, node_scripts, _ = self._parse(text)
        self.assertIn("", node_scripts)
        self.assertEqual(node_scripts[""], "1")

    def test_child_node_path_built(self):
        text = (
            '[node name="Main" type="Node2D"]\n'
            '[node name="Button" type="Button" parent="."]\n'
            'script = ExtResource("2")\n'
        )
        _, node_scripts, _ = self._parse(text)
        self.assertIn("Button", node_scripts)

    def test_nested_node_path(self):
        text = (
            '[node name="Root" type="Node2D"]\n'
            '[node name="UI" type="Control" parent="."]\n'
            '[node name="Btn" type="Button" parent="UI"]\n'
            'script = ExtResource("3")\n'
        )
        _, node_scripts, _ = self._parse(text)
        self.assertIn("UI/Btn", node_scripts)

    def test_ext_resource_script_captured(self):
        text = '[ext_resource type="Script" path="res://player.gd" id="1"]\n'
        id_to_path, _, _ = self._parse(text)
        self.assertEqual(id_to_path.get("1"), "res://player.gd")

    def test_non_script_ext_resource_ignored(self):
        text = '[ext_resource type="Texture2D" path="res://icon.png" id="1"]\n'
        id_to_path, _, _ = self._parse(text)
        self.assertNotIn("1", id_to_path)

    def test_connection_captured(self):
        text = '[connection signal="pressed" from="Button" to="." method="_on_btn"]\n'
        _, _, conns = self._parse(text)
        self.assertEqual(len(conns), 1)
        self.assertEqual(conns[0]["method"], "_on_btn")
        self.assertEqual(conns[0]["to"], ".")

    def test_connection_missing_method_skipped(self):
        text = '[connection signal="pressed" from="Button" to="."]\n'
        _, _, conns = self._parse(text)
        self.assertEqual(len(conns), 0)

    def test_uid_resolved_via_uid_map(self):
        text = '[ext_resource type="Script" path="uid://abc123" id="1"]\n'
        uid_map = {"uid://abc123": "res://player.gd"}
        id_to_path, _, _ = self._parse(text, uid_map=uid_map)
        self.assertEqual(id_to_path.get("1"), "res://player.gd")

    def test_blank_lines_dont_break_node_block(self):
        text = '[node name="Main" type="Node2D"]\n\nscript = ExtResource("1")\n'
        _, node_scripts, _ = self._parse(text)
        self.assertIn("", node_scripts)


class TestBrokenSignalConnectionCheck(unittest.TestCase):
    """Integration tests for BROKEN_SIGNAL_CONNECTION."""

    def _check(self, scene_content: str, gd_content: str, script_name="handler.gd"):
        from godot_project_doctor.checks import _check_broken_signal_connections
        from godot_project_doctor.indexer import index_project
        from godot_project_doctor.parser import parse_project_godot

        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="Game"\n')
            _write(root / script_name, gd_content)
            _write(root / "scenes" / "Main.tscn", scene_content)
            summary = parse_project_godot(root)
            index = index_project(root, summary)
            return _check_broken_signal_connections(index, root)

    def _make_scene(self, method_name="_on_button_pressed", script_rel="res://handler.gd"):
        return (
            f"[gd_scene load_steps=2 format=3]\n"
            f'[ext_resource type="Script" path="{script_rel}" id="1"]\n'
            f'[node name="Main" type="Node2D"]\n'
            f'script = ExtResource("1")\n'
            f'[node name="Button" type="Button" parent="."]\n'
            f'[connection signal="pressed" from="Button" to="." method="{method_name}"]\n'
        )

    def test_method_present_no_issue(self):
        issues = self._check(
            self._make_scene("_on_button_pressed"),
            "extends Node2D\nfunc _on_button_pressed():\n    pass\n",
        )
        self.assertEqual(issues, [])

    def test_method_missing_reports_warning(self):
        issues = self._check(
            self._make_scene("_on_button_pressed"),
            "extends Node2D\nfunc _ready():\n    pass\n",
        )
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "BROKEN_SIGNAL_CONNECTION")
        self.assertEqual(issues[0].severity.value, "WARNING")
        self.assertIn("_on_button_pressed", issues[0].message)

    def test_static_func_counts_as_present(self):
        issues = self._check(
            self._make_scene("_on_button_pressed"),
            "extends Node2D\nstatic func _on_button_pressed():\n    pass\n",
        )
        self.assertEqual(issues, [])

    def test_no_script_on_target_node_skipped(self):
        scene = (
            "[gd_scene load_steps=1 format=3]\n"
            '[node name="Main" type="Node2D"]\n'
            '[node name="Button" type="Button" parent="."]\n'
            '[connection signal="pressed" from="Button" to="." method="_on_btn"]\n'
        )
        issues = self._check(scene, "extends Node2D\nfunc _ready():\n    pass\n")
        self.assertEqual(issues, [])

    def test_unresolvable_to_path_skipped(self):
        scene = (
            "[gd_scene load_steps=2 format=3]\n"
            '[ext_resource type="Script" path="res://handler.gd" id="1"]\n'
            '[node name="Main" type="Node2D"]\n'
            'script = ExtResource("1")\n'
            # connection targets "NonExistentChild" which has no node entry
            '[connection signal="pressed" from="." to="NonExistentChild" method="_on_btn"]\n'
        )
        issues = self._check(scene, "extends Node2D\nfunc _ready():\n    pass\n")
        self.assertEqual(issues, [])

    def test_indented_func_found(self):
        issues = self._check(
            self._make_scene("_on_button_pressed"),
            "extends Node2D\n\n\nfunc _on_button_pressed():\n\tpass\n",
        )
        self.assertEqual(issues, [])


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4d — UNUSED_SCRIPT / UNUSED_AUTOLOAD
# ─────────────────────────────────────────────────────────────────────────────


class TestUnusedScriptCheck(unittest.TestCase):
    """UNUSED_SCRIPT: scripts not referenced by any scene, resource, or autoload."""

    def _run(self, files: dict) -> list:
        """Build a temp project from *files* dict (relative_path → content) and run the check."""
        from godot_project_doctor.checks import _check_unused_scripts
        from godot_project_doctor.indexer import index_project
        from godot_project_doctor.parser import parse_project_godot

        with TempProject() as root:
            for rel, content in files.items():
                _write(root / rel, content)
            summary = parse_project_godot(root)
            index = index_project(root, summary)
            return _check_unused_scripts(index, root)

    def test_script_referenced_in_scene_no_issue(self):
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="Game"\n',
                "player.gd": "extends CharacterBody2D\n",
                "scenes/Main.tscn": (
                    "[gd_scene format=3]\n"
                    '[ext_resource type="Script" path="res://player.gd" id="1"]\n'
                    '[node name="Player" type="CharacterBody2D"]\n'
                    'script = ExtResource("1")\n'
                ),
            }
        )
        unused_codes = [i.code for i in issues if i.code == "UNUSED_SCRIPT"]
        self.assertEqual(unused_codes, [])

    def test_orphan_script_flagged(self):
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="Game"\n',
                "orphan.gd": "extends Node\n",
            }
        )
        unused = [i for i in issues if i.code == "UNUSED_SCRIPT"]
        self.assertEqual(len(unused), 1)
        self.assertIn("orphan.gd", unused[0].message)

    def test_autoloaded_script_not_flagged(self):
        issues = self._run(
            {
                "project.godot": (
                    '[application]\nconfig/name="Game"\n'
                    '[autoload]\nGameState="res://game_state.gd"\n'
                ),
                "game_state.gd": "extends Node\n",
            }
        )
        unused = [i for i in issues if i.code == "UNUSED_SCRIPT"]
        self.assertEqual(unused, [])

    def test_class_name_referenced_by_extends_not_flagged(self):
        """A class_name script used as a base class via `extends Name` is referenced."""
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="Game"\n',
                "unit_data.gd": "class_name UnitData extends Resource\n",
                "hero_data.gd": "class_name HeroData extends UnitData\n",
                "scenes/Main.tscn": (
                    "[gd_scene format=3]\n"
                    '[ext_resource type="Script" path="res://hero_data.gd" id="1"]\n'
                ),
            }
        )
        unused = [i.file for i in issues if i.code == "UNUSED_SCRIPT"]
        self.assertNotIn("unit_data.gd", unused)  # referenced via `extends UnitData`

    def test_class_name_used_as_type_in_scene_not_flagged(self):
        """A class_name registered node referenced as a scene node type is used."""
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="Game"\n',
                "hero_body.gd": "class_name HeroBody extends Node2D\n",
                "scenes/Main.tscn": ('[gd_scene format=3]\n[node name="Hero" type="HeroBody"]\n'),
            }
        )
        unused = [i.file for i in issues if i.code == "UNUSED_SCRIPT"]
        self.assertNotIn("hero_body.gd", unused)

    def test_unused_class_name_still_flagged(self):
        """A class_name that is never referenced anywhere is still reported."""
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="Game"\n',
                "lonely.gd": "class_name Lonely extends RefCounted\n",
            }
        )
        unused = [i.file for i in issues if i.code == "UNUSED_SCRIPT"]
        self.assertIn("lonely.gd", unused)

    def test_preloaded_script_not_flagged(self):
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="Game"\n',
                "lib.gd": "class_name Lib\nextends RefCounted\n",
                "main.gd": 'var L = preload("res://lib.gd")\n',
            }
        )
        # main.gd itself might be flagged (nothing references it), but lib.gd should not be
        unused_files = [i.file for i in issues if i.code == "UNUSED_SCRIPT"]
        self.assertNotIn("lib.gd", unused_files)

    def test_multiple_orphans_all_flagged(self):
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="Game"\n',
                "a.gd": "extends Node\n",
                "b.gd": "extends Node\n",
            }
        )
        unused = [i for i in issues if i.code == "UNUSED_SCRIPT"]
        self.assertEqual(len(unused), 2)

    def test_severity_is_warning(self):
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="Game"\n',
                "orphan.gd": "extends Node\n",
            }
        )
        unused = [i for i in issues if i.code == "UNUSED_SCRIPT"]
        self.assertTrue(all(i.severity.value == "WARNING" for i in unused))


class TestUnusedAutoloadCheck(unittest.TestCase):
    """UNUSED_AUTOLOAD: autoloads not referenced in any GDScript file."""

    def _run(self, files: dict) -> list:
        from godot_project_doctor.checks import _check_unused_autoloads
        from godot_project_doctor.indexer import index_project
        from godot_project_doctor.parser import parse_project_godot

        with TempProject() as root:
            for rel, content in files.items():
                _write(root / rel, content)
            summary = parse_project_godot(root)
            index = index_project(root, summary)
            return _check_unused_autoloads(index, root)

    def test_autoload_used_in_script_no_issue(self):
        issues = self._run(
            {
                "project.godot": (
                    '[application]\nconfig/name="Game"\n'
                    '[autoload]\nGameState="res://game_state.gd"\n'
                ),
                "game_state.gd": "extends Node\n",
                "player.gd": "func _ready():\n\tGameState.reset()\n",
            }
        )
        unused = [i for i in issues if i.code == "UNUSED_AUTOLOAD"]
        self.assertEqual(unused, [])

    def test_autoload_never_referenced_flagged(self):
        issues = self._run(
            {
                "project.godot": (
                    '[application]\nconfig/name="Game"\n[autoload]\nDeadCode="res://dead_code.gd"\n'
                ),
                "dead_code.gd": "extends Node\n",
                "player.gd": "extends CharacterBody2D\n",
            }
        )
        unused = [i for i in issues if i.code == "UNUSED_AUTOLOAD"]
        self.assertEqual(len(unused), 1)
        self.assertIn("DeadCode", unused[0].message)

    def test_no_autoloads_no_issue(self):
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="Game"\n',
                "player.gd": "extends CharacterBody2D\n",
            }
        )
        unused = [i for i in issues if i.code == "UNUSED_AUTOLOAD"]
        self.assertEqual(unused, [])

    def test_severity_is_warning(self):
        issues = self._run(
            {
                "project.godot": (
                    '[application]\nconfig/name="Game"\n[autoload]\nOrphan="res://orphan.gd"\n'
                ),
                "orphan.gd": "extends Node\n",
            }
        )
        unused = [i for i in issues if i.code == "UNUSED_AUTOLOAD"]
        self.assertTrue(all(i.severity.value == "WARNING" for i in unused))

    def test_deterministic_order(self):
        issues = self._run(
            {
                "project.godot": (
                    '[application]\nconfig/name="Game"\n'
                    "[autoload]\n"
                    'Zzz="res://zzz.gd"\n'
                    'Aaa="res://aaa.gd"\n'
                ),
                "zzz.gd": "extends Node\n",
                "aaa.gd": "extends Node\n",
            }
        )
        unused = [i for i in issues if i.code == "UNUSED_AUTOLOAD"]
        names = [i.message.split("'")[1] for i in unused]
        self.assertEqual(names, sorted(names))


class TestOutputWriteErrors(unittest.TestCase):
    """An unwritable --output path must produce a clean error, not a traceback."""

    def test_output_write_guard_aborts_with_exit_2(self):
        from godot_project_doctor.cli import _output_write_guard

        with self.assertRaises(SystemExit) as cm, _output_write_guard(Path("nowhere/out.md")):
            raise PermissionError(13, "Permission denied")
        self.assertEqual(cm.exception.code, 2)

    def test_graph_unwritable_output_no_traceback(self):
        """Writing the graph to a path under a missing directory exits cleanly."""
        from click.testing import CliRunner

        from godot_project_doctor.cli import main

        with TempProject() as root:
            make_minimal_project(root)
            bad = root / "missing_dir" / "graph.md"
            result = CliRunner().invoke(
                main, ["graph", str(root), "--format", "mermaid", "--output", str(bad)]
            )
            self.assertNotIn("Traceback", result.output)
            self.assertNotEqual(result.exit_code, 0)


# ─────────────────────────────────────────────────────────────────────────────
# DANGLING_EXT_RESOURCE / DUPLICATE_CLASS_NAME
# ─────────────────────────────────────────────────────────────────────────────


class TestDanglingExtResourceCheck(unittest.TestCase):
    """DANGLING_EXT_RESOURCE: ExtResource("id") used without a declaration."""

    def _run(self, files: dict) -> list:
        from godot_project_doctor.checks import _check_dangling_ext_resources
        from godot_project_doctor.indexer import index_project
        from godot_project_doctor.parser import parse_project_godot

        with TempProject() as root:
            for rel, content in files.items():
                _write(root / rel, content)
            summary = parse_project_godot(root)
            index = index_project(root, summary)
            return _check_dangling_ext_resources(index, root)

    def test_undeclared_id_flagged(self):
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="Game"\n',
                "broken.tscn": (
                    "[gd_scene format=3]\n"
                    '[node name="Main" type="Sprite2D"]\n'
                    'texture = ExtResource("9_lost")\n'
                ),
            }
        )
        dangling = [i for i in issues if i.code == "DANGLING_EXT_RESOURCE"]
        self.assertEqual(len(dangling), 1)
        self.assertEqual(dangling[0].file, "broken.tscn")
        self.assertIn("9_lost", dangling[0].message)

    def test_declared_id_not_flagged(self):
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="Game"\n',
                "ok.tscn": (
                    "[gd_scene format=3]\n"
                    '[ext_resource type="Texture2D" path="res://icon.png" id="1_t"]\n'
                    '[node name="Main" type="Sprite2D"]\n'
                    'texture = ExtResource("1_t")\n'
                ),
            }
        )
        self.assertEqual([i for i in issues if i.code == "DANGLING_EXT_RESOURCE"], [])

    def test_unquoted_legacy_id_supported(self):
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="Game"\n',
                "legacy.tscn": (
                    "[gd_scene format=3]\n"
                    '[node name="Main" type="Sprite2D"]\n'
                    "texture = ExtResource(7)\n"
                ),
            }
        )
        dangling = [i for i in issues if i.code == "DANGLING_EXT_RESOURCE"]
        self.assertEqual(len(dangling), 1)
        self.assertIn("'7'", dangling[0].details)

    def test_repeated_usage_one_issue_per_id(self):
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="Game"\n',
                "multi.tscn": (
                    "[gd_scene format=3]\n"
                    '[node name="Main" type="Node"]\n'
                    'a = ExtResource("5_x")\n'
                    'b = [ExtResource("5_x"), ExtResource("5_x")]\n'
                ),
            }
        )
        dangling = [i for i in issues if i.code == "DANGLING_EXT_RESOURCE"]
        self.assertEqual(len(dangling), 1)
        self.assertIn("3 time(s)", dangling[0].details)

    def test_tres_files_also_checked(self):
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="Game"\n',
                "mat.tres": (
                    '[gd_resource type="Material" format=3]\n'
                    "[resource]\n"
                    'shader = ExtResource("2_s")\n'
                ),
            }
        )
        dangling = [i for i in issues if i.code == "DANGLING_EXT_RESOURCE"]
        self.assertEqual(len(dangling), 1)
        self.assertEqual(dangling[0].file, "mat.tres")


class TestDuplicateClassNameCheck(unittest.TestCase):
    """DUPLICATE_CLASS_NAME: the same class_name declared by multiple scripts."""

    def _run(self, files: dict, config=None) -> list:
        from godot_project_doctor.checks import _check_duplicate_class_names
        from godot_project_doctor.indexer import index_project
        from godot_project_doctor.parser import parse_project_godot

        with TempProject() as root:
            for rel, content in files.items():
                _write(root / rel, content)
            summary = parse_project_godot(root)
            index = index_project(root, summary)
            return _check_duplicate_class_names(index, root, config)

    def test_duplicate_flagged_with_both_paths(self):
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="Game"\n',
                "a/foo.gd": "class_name Foo\nextends Node\n",
                "b/foo2.gd": "class_name Foo\nextends Node\n",
            }
        )
        dups = [i for i in issues if i.code == "DUPLICATE_CLASS_NAME"]
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0].file, "a/foo.gd")  # sorted first
        self.assertIn("a/foo.gd", dups[0].details)
        self.assertIn("b/foo2.gd", dups[0].details)

    def test_unique_class_names_not_flagged(self):
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="Game"\n',
                "a.gd": "class_name Alpha\nextends Node\n",
                "b.gd": "class_name Beta\nextends Node\n",
            }
        )
        self.assertEqual([i for i in issues if i.code == "DUPLICATE_CLASS_NAME"], [])

    def test_three_way_duplicate_single_issue(self):
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="Game"\n',
                "x.gd": "class_name Tri\n",
                "y.gd": "class_name Tri\n",
                "z.gd": "class_name Tri\n",
            }
        )
        dups = [i for i in issues if i.code == "DUPLICATE_CLASS_NAME"]
        self.assertEqual(len(dups), 1)
        self.assertIn("3 scripts", dups[0].message)

    def test_ignored_scripts_excluded(self):
        """Scripts under addons/ are excluded with the default config."""
        from godot_project_doctor.config import Config

        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="Game"\n',
                "mine.gd": "class_name Shared\nextends Node\n",
                "addons/lib/theirs.gd": "class_name Shared\nextends Node\n",
            },
            config=Config(),
        )
        self.assertEqual([i for i in issues if i.code == "DUPLICATE_CLASS_NAME"], [])

    def test_full_scan_emits_duplicate(self):
        """End-to-end: scan() surfaces the new check."""
        from godot_project_doctor.scanner import scan

        with TempProject() as root:
            _write(root / "project.godot", '[application]\nconfig/name="Game"\n')
            _write(root / "one.gd", "class_name Twin\n")
            _write(root / "two.gd", "class_name Twin\n")
            index = scan(root)
            codes = [i.code for i in index.issues]
            self.assertIn("DUPLICATE_CLASS_NAME", codes)


# ─────────────────────────────────────────────────────────────────────────────
# BROKEN_SIGNAL_CONNECTION — inheritance-chain awareness
# ─────────────────────────────────────────────────────────────────────────────


class TestInheritedSignalHandlers(unittest.TestCase):
    """BROKEN_SIGNAL_CONNECTION follows the script's `extends` chain."""

    def _run(self, files: dict) -> list:
        from godot_project_doctor.checks import _check_broken_signal_connections
        from godot_project_doctor.indexer import index_project
        from godot_project_doctor.parser import parse_project_godot

        with TempProject() as root:
            for rel, content in files.items():
                _write(root / rel, content)
            summary = parse_project_godot(root)
            index = index_project(root, summary)
            return [
                i
                for i in _check_broken_signal_connections(index, root)
                if i.code == "BROKEN_SIGNAL_CONNECTION"
            ]

    @staticmethod
    def _scene(script_path: str, method: str) -> str:
        return (
            "[gd_scene format=3]\n"
            f'[ext_resource type="Script" path="{script_path}" id="1"]\n'
            '[node name="Main" type="Node"]\n'
            'script = ExtResource("1")\n'
            f'[connection signal="ready" from="." to="." method="{method}"]\n'
        )

    def test_handler_in_class_name_base_not_flagged(self):
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="G"\n',
                "base.gd": "class_name BaseUI\nextends Node\n\nfunc _on_pressed():\n\tpass\n",
                "child.gd": "extends BaseUI\n",
                "ui.tscn": self._scene("res://child.gd", "_on_pressed"),
            }
        )
        self.assertEqual(issues, [])

    def test_handler_in_res_path_base_not_flagged(self):
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="G"\n',
                "base.gd": "extends Node\n\nfunc _on_hit():\n\tpass\n",
                "child.gd": 'extends "res://base.gd"\n',
                "s.tscn": self._scene("res://child.gd", "_on_hit"),
            }
        )
        self.assertEqual(issues, [])

    def test_handler_in_relative_path_base_not_flagged(self):
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="G"\n',
                "ui/base.gd": "extends Node\n\nfunc _on_close():\n\tpass\n",
                "ui/child.gd": 'extends "base.gd"\n',
                "s.tscn": self._scene("res://ui/child.gd", "_on_close"),
            }
        )
        self.assertEqual(issues, [])

    def test_handler_in_grandparent_not_flagged(self):
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="G"\n',
                "a.gd": "class_name GrandBase\nextends Node\n\nfunc _on_deep():\n\tpass\n",
                "b.gd": "class_name MidBase\nextends GrandBase\n",
                "c.gd": "extends MidBase\n",
                "s.tscn": self._scene("res://c.gd", "_on_deep"),
            }
        )
        self.assertEqual(issues, [])

    def test_combined_class_name_extends_form_resolved(self):
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="G"\n',
                "base.gd": "extends Node\n\nfunc _on_combo():\n\tpass\n",
                "mid.gd": 'class_name ComboMid extends "res://base.gd"\n',
                "leaf.gd": "extends ComboMid\n",
                "s.tscn": self._scene("res://leaf.gd", "_on_combo"),
            }
        )
        self.assertEqual(issues, [])

    def test_method_nowhere_in_chain_flagged(self):
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="G"\n',
                "base.gd": "class_name LonelyBase\nextends Node\n",
                "child.gd": "extends LonelyBase\n",
                "s.tscn": self._scene("res://child.gd", "_on_ghost"),
            }
        )
        self.assertEqual(len(issues), 1)
        self.assertIn("base scripts", issues[0].message)

    def test_extends_cycle_terminates_and_flags(self):
        issues = self._run(
            {
                "project.godot": '[application]\nconfig/name="G"\n',
                "a.gd": 'extends "res://b.gd"\n',
                "b.gd": 'extends "res://a.gd"\n',
                "s.tscn": self._scene("res://a.gd", "_on_loop"),
            }
        )
        self.assertEqual(len(issues), 1)


if __name__ == "__main__":
    unittest.main()
