"""Shared pytest fixtures for godot-project-doctor tests."""

from __future__ import annotations

from pathlib import Path

import pytest

# ─── Helper ───────────────────────────────────────────────────────────────────


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def minimal_project(tmp_path: Path) -> Path:
    """A valid Godot project with only a project.godot file."""
    _write(
        tmp_path / "project.godot",
        """\
; Engine configuration file.
; It's best edited using the editor UI and not directly,
; since the parameters that go here are not all documented.
;
; Format:
;   [section] ; section goes between []
;   param=value ; assign values to parameters

config_version=5

[application]

config/name="Minimal Game"
run/main_scene="res://scenes/Main.tscn"

[autoload]

GameState="res://autoload/GameState.gd"
""",
    )
    return tmp_path


@pytest.fixture()
def project_with_valid_ref(tmp_path: Path) -> Path:
    """
    A project where a .tscn file references a script that exists on disk.
    """
    _write(
        tmp_path / "project.godot",
        '[application]\nconfig/name="RefGame"\n',
    )
    # The referenced script must actually exist
    _write(tmp_path / "player" / "player.gd", "extends CharacterBody2D\n")
    _write(
        tmp_path / "scenes" / "Player.tscn",
        """\
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" uid="uid://abc123" path="res://player/player.gd" id="1"]

[node name="Player" type="CharacterBody2D"]
script = ExtResource("1")
""",
    )
    return tmp_path


@pytest.fixture()
def project_with_missing_ref(tmp_path: Path) -> Path:
    """
    A project where a .tscn file references a script that does NOT exist.
    """
    _write(
        tmp_path / "project.godot",
        '[application]\nconfig/name="BrokenGame"\n',
    )
    _write(
        tmp_path / "scenes" / "Main.tscn",
        """\
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://scripts/missing.gd" id="1"]

[node name="Main" type="Node"]
script = ExtResource("1")
""",
    )
    return tmp_path


@pytest.fixture()
def project_with_unused_asset(tmp_path: Path) -> Path:
    """
    A project containing an image that is not referenced by any scene/resource.
    """
    _write(
        tmp_path / "project.godot",
        '[application]\nconfig/name="AssetGame"\n',
    )
    _write(tmp_path / "assets" / "background.png", b"\x89PNG\r\n".decode("latin-1"))
    # A scene with NO ext_resource entries
    _write(
        tmp_path / "scenes" / "Main.tscn",
        '[gd_scene format=3]\n\n[node name="Main" type="Node"]\n',
    )
    return tmp_path


@pytest.fixture()
def project_with_export_presets(tmp_path: Path) -> Path:
    """A minimal project that also has export_presets.cfg."""
    _write(tmp_path / "project.godot", '[application]\nconfig/name="ExportGame"\n')
    _write(tmp_path / "export_presets.cfg", '[preset.0]\nname="Windows Desktop"\n')
    return tmp_path
