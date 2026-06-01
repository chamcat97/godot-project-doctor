"""Tests for godot-project-doctor (unittest-based, pytest-compatible)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Optional

# Add src to path for direct test execution
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from godot_project_doctor.checks import run_all_checks
from godot_project_doctor.indexer import index_project, resolve_res_path
from godot_project_doctor.models import (
    Issue,
    ProjectIndex,
    ProjectSummary,
    ResourceRef,
    Severity,
)
from godot_project_doctor.parser import parse_project_godot
from godot_project_doctor.reporter import build_report, render_json
from godot_project_doctor.scanner import GodotProjectError, scan


# ─── Fixture helpers ──────────────────────────────────────────────────────────

import tempfile
import os


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
    _write(root / "project.godot", """\
; Engine: Godot 4.x
[application]

config/name="Minimal Game"
run/main_scene="res://scenes/Main.tscn"

[autoload]

GameState="res://autoload/GameState.gd"
""")
    return root


def make_project_with_valid_ref(root: Path) -> Path:
    _write(root / "project.godot", '[application]\nconfig/name="RefGame"\n')
    _write(root / "player" / "player.gd", "extends CharacterBody2D\n")
    _write(root / "scenes" / "Player.tscn", """\
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" uid="uid://abc123" path="res://player/player.gd" id="1"]

[node name="Player" type="CharacterBody2D"]
script = ExtResource("1")
""")
    return root


def make_project_with_missing_ref(root: Path) -> Path:
    _write(root / "project.godot", '[application]\nconfig/name="BrokenGame"\n')
    _write(root / "scenes" / "Main.tscn", """\
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://scripts/missing.gd" id="1"]

[node name="Main" type="Node"]
script = ExtResource("1")
""")
    return root


def make_project_with_unused_asset(root: Path) -> Path:
    _write(root / "project.godot", '[application]\nconfig/name="AssetGame"\n')
    _write(root / "assets" / "background.png", "PNG")
    _write(root / "scenes" / "Main.tscn",
           '[gd_scene format=3]\n\n[node name="Main" type="Node"]\n')
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
            _write(root / "scenes" / "Game.tscn",
                   '[gd_scene format=3]\n'
                   '[ext_resource type="Texture2D" path="res://sprites/hero.png" id="1"]\n')
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


if __name__ == "__main__":
    unittest.main()
