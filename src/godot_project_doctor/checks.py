"""Audit checks for a Godot project index."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from godot_project_doctor.indexer import resolve_ref_path
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


def _check_missing_external_resources(index: ProjectIndex, project_root: Path) -> list[Issue]:
    issues: list[Issue] = []
    for ref in index.refs:
        real_path = resolve_ref_path(ref.path, project_root, ref.source_file)
        if real_path is None:
            continue  # uid:// — can't resolve statically
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
        from PIL import Image, UnidentifiedImageError
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
                    message=f"Large texture ({w}x{h}): {rel}",
                    file=rel,
                    details=(
                        f"Image dimensions {w}x{h} exceed the {LARGE_TEXTURE_DIM}px threshold. "
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


def _ref_to_canonical_rel(ref_path: str, source_file: str) -> str | None:
    """Return the project-root-relative canonical path for a resource reference.

    Returns ``None`` for ``uid://`` paths that cannot be resolved statically.
    Uses the same rules as :func:`~godot_project_doctor.indexer.resolve_ref_path`:

    * ``res://foo/bar.png`` → ``"foo/bar.png"``
    * ``../assets/bg.png`` declared in ``scenes/Main.tscn``
      → ``"assets/bg.png"``
    """
    if ref_path.startswith("uid://"):
        return None
    if ref_path.startswith("res://"):
        return ref_path[len("res://") :]
    # Relative path: normalise via PurePosixPath arithmetic
    source_dir = PurePosixPath(source_file.replace("\\", "/")).parent
    return str(source_dir / ref_path)


def _check_unused_asset_candidates(index: ProjectIndex, project_root: Path) -> list[Issue]:
    """Flag asset files that are not referenced by any parsed ref.

    These are *candidates* — GDScript can load assets dynamically, so
    this check produces false positives for runtime-loaded assets.
    The same canonical-path rules used in the missing-reference check
    are applied here so that both checks agree on what is "referenced".
    """
    referenced_rel: set[str] = set()
    for ref in index.refs:
        canonical = _ref_to_canonical_rel(ref.path, ref.source_file)
        if canonical is not None:
            # Normalise path separators for cross-platform comparison
            referenced_rel.add(canonical.replace("\\", "/"))

    asset_files = list(index.images) + list(index.audio)
    issues: list[Issue] = []

    for rel in asset_files:
        rel_normalised = rel.replace("\\", "/")
        if rel_normalised not in referenced_rel:
            issues.append(
                Issue(
                    code="UNUSED_ASSET_CANDIDATE",
                    severity=Severity.WARNING,
                    message=f"Asset not referenced by any parsed scene or resource: {rel}",
                    file=rel,
                    details=(
                        "This file was not found in any ext_resource declaration or "
                        "static load()/preload() call. "
                        "It may be loaded dynamically via GDScript (load(), preload()), "
                        "or it may be genuinely unused."
                    ),
                )
            )
    return issues
