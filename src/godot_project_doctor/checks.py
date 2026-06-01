"""Audit checks for a Godot project index."""

from __future__ import annotations

from pathlib import Path

from godot_project_doctor.indexer import resolve_res_path
from godot_project_doctor.models import Issue, ProjectIndex, Severity

# ─── Constants ────────────────────────────────────────────────────────────────

LARGE_TEXTURE_DIM = 2048  # pixels
LARGE_AUDIO_BYTES = 10 * 1024 * 1024  # 10 MB

# ─── Check runners ────────────────────────────────────────────────────────────


def run_all_checks(index: ProjectIndex) -> list[Issue]:
    """Run every check and return a combined issue list."""
    issues: list[Issue] = []
    project_root = Path(index.project_root)

    issues.extend(_check_missing_export_presets(index))
    issues.extend(_check_missing_external_resources(index, project_root))
    issues.extend(_check_large_textures(index, project_root))
    issues.extend(_check_large_audio(index, project_root))
    issues.extend(_check_unused_asset_candidates(index, project_root))

    return issues


# ─── Individual checks ────────────────────────────────────────────────────────


def _check_missing_export_presets(index: ProjectIndex) -> list[Issue]:
    if not index.has_export_presets:
        return [
            Issue(
                code="NO_EXPORT_PRESETS",
                severity=Severity.INFO,
                message="export_presets.cfg not found. No export configuration is present.",
                file=None,
                details="Create export presets in the Godot editor if you intend to export the project.",
            )
        ]
    return []


def _check_missing_external_resources(
    index: ProjectIndex, project_root: Path
) -> list[Issue]:
    issues: list[Issue] = []
    for ref in index.refs:
        real_path = resolve_res_path(ref.path, project_root)
        if not real_path.exists():
            issues.append(
                Issue(
                    code="MISSING_EXT_RESOURCE",
                    severity=Severity.ERROR,
                    message=f"External resource not found: {ref.path}",
                    file=ref.source_file,
                    details=(
                        f"Referenced in [{ref.source_file}] as type={ref.ref_type!r} "
                        f"id={ref.ref_id!r}. "
                        f"Expected at: {real_path}"
                    ),
                )
            )
    return issues


def _check_large_textures(index: ProjectIndex, project_root: Path) -> list[Issue]:
    """Warn on raster images wider or taller than LARGE_TEXTURE_DIM pixels."""
    try:
        from PIL import Image, UnidentifiedImageError  # type: ignore[import]
    except ImportError:
        return []

    # Only raster formats; skip SVG
    raster_exts = {".png", ".jpg", ".jpeg", ".webp"}
    issues: list[Issue] = []

    for rel in index.images:
        if Path(rel).suffix.lower() not in raster_exts:
            continue
        abs_path = project_root / rel
        try:
            with Image.open(abs_path) as img:
                w, h = img.size
        except (OSError, UnidentifiedImageError, Exception):
            continue

        if w > LARGE_TEXTURE_DIM or h > LARGE_TEXTURE_DIM:
            issues.append(
                Issue(
                    code="LARGE_TEXTURE",
                    severity=Severity.WARNING,
                    message=f"Large texture ({w}×{h}): {rel}",
                    file=rel,
                    details=(
                        f"Image dimensions {w}×{h} exceed the {LARGE_TEXTURE_DIM}px threshold. "
                        "Consider downscaling or using mipmaps to reduce GPU memory usage."
                    ),
                )
            )
    return issues


def _check_large_audio(index: ProjectIndex, project_root: Path) -> list[Issue]:
    """Warn on audio files larger than LARGE_AUDIO_BYTES."""
    issues: list[Issue] = []
    for rel in index.audio:
        abs_path = project_root / rel
        try:
            size = abs_path.stat().st_size
        except OSError:
            continue
        if size > LARGE_AUDIO_BYTES:
            size_mb = size / (1024 * 1024)
            issues.append(
                Issue(
                    code="LARGE_AUDIO",
                    severity=Severity.WARNING,
                    message=f"Large audio file ({size_mb:.1f} MB): {rel}",
                    file=rel,
                    details=(
                        f"File size {size_mb:.1f} MB exceeds the 10 MB threshold. "
                        "Consider compressing or streaming this asset."
                    ),
                )
            )
    return issues


def _check_unused_asset_candidates(
    index: ProjectIndex, project_root: Path
) -> list[Issue]:
    """
    Flag asset files that are not referenced by any parsed ext_resource.

    These are *candidates* — GDScript can load assets dynamically, so
    this check produces false positives for runtime-loaded assets.
    """
    referenced_res_paths: set[str] = {ref.path for ref in index.refs}

    # Convert all referenced res:// paths to normalised relative strings
    referenced_rel: set[str] = set()
    for rp in referenced_res_paths:
        if rp.startswith("res://"):
            referenced_rel.add(rp[len("res://"):])

    asset_files = list(index.images) + list(index.audio)
    issues: list[Issue] = []

    for rel in asset_files:
        # Normalize path separators for comparison
        rel_normalized = rel.replace("\\", "/")
        if rel_normalized not in referenced_rel:
            issues.append(
                Issue(
                    code="UNUSED_ASSET_CANDIDATE",
                    severity=Severity.WARNING,
                    message=f"Asset not referenced by any parsed scene or resource: {rel}",
                    file=rel,
                    details=(
                        "This file was not found in any ext_resource declaration. "
                        "It may be loaded dynamically via GDScript (load(), preload()), "
                        "or it may be genuinely unused."
                    ),
                )
            )
    return issues
