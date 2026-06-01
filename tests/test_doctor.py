"""Tests for godot-project-doctor (unittest-based, pytest-compatible)."""

from __future__ import annotations

import json
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
            self.assertEqual(data.get("schema_version"), "1.0")

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


if __name__ == "__main__":
    unittest.main()
