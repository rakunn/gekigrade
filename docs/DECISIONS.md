# Decision log

## ADR-001 — Python 3.12 and uv

Use the installed Python 3.12 runtime and a committed `uv.lock`. Python 3.13 remains outside the supported range for the first slice so native-wheel behavior is frozen.

## ADR-002 — RawTherapee as first RAW adapter

Choose RawTherapee 5.13 because explicit, stackable PP3 profiles and 16-bit TIFF CLI output form a compact reproducibility boundary. Defer darktable because its XMP history plus application configuration, presets, and database state add more inputs to freeze. RAW output is not claimed until a user-owned ARW is tested.

## ADR-003 — 16-bit TIFF intermediate

Use oriented, profile-tagged 16-bit TIFF for Milestone 1. It provides inspectable ICC metadata and matches the chosen RAW adapter path. Reconsider float OpenEXR after real RAW dynamic-range or local-compositing evidence.

## ADR-004 — linear ACEScg working space

Use macOS `ACESCG Linear.icc` at the LittleCMS boundary and the pinned OCIO ACES 2.0 CG configuration internally. Record the system profile hash and treat a changed hash as a different rendering fingerprint. This is a local macOS decision, not a cross-platform guarantee.

## ADR-005 — OpenImageIO and OpenColorIO Python wheels

Use project-local native wheels rather than Homebrew formulae. The exact Apple Silicon Python 3.12 versions imported successfully during planning and avoid Homebrew's larger dependency graph.

## ADR-006 — analytic looks instead of LUTs

Compose initial looks from documented operations in known spaces. Defer LUT assets until their input/output spaces, licensing, interpolation, range, and gamut behavior are independently verified.

## ADR-007 — deterministic comparison scope

Require exact decoded-pixel hashes only under an identical tool/profile/configuration/architecture/thread fingerprint. Use maximum channel difference 1 and RMSE 0.25 for cross-platform 8-bit conformance.

## ADR-008 — safe metadata policy

Default to an explicit allowlist and strip location, serial, MakerNote, thumbnail, comment, face-region, and proprietary edit metadata. Provide a stricter strip policy. ICC data is never removed by either policy.

## ADR-009 — local-adjustment boundary

Schema v1 exposes an empty local-adjustment stage and accepts no local operations. A future version must define masks, coordinate space, compositing math, feathering, and halo QA before enabling the stage.

## ADR-010 — output metadata and explicit selection

Candidate rendering never implies selection. `geki select` writes a plan-hash-bound `selection.json`; export reconstructs the exact rendered plan from candidate metadata. The `safe` export policy copies only Make, Model, capture date, exposure time, f-number, ISO, and focal length when encodable, resets orientation, and always excludes GPS, serial, MakerNotes, thumbnails, comments, face regions, and proprietary edit history. `strip` retains only format data and ICC.

## ADR-011 — deterministic operation semantics

Rotation and resizing use OpenImageIO Lanczos3. Exposure and a bounded Bradford chromatic adaptation operate in linear ACEScg. Contrast, black lift, shoulder, saturation, and analytic look blending operate in ACEScct. Vignette returns to linear ACEScg; unsharp masking is resolution-aware after the encoded-sRGB resize. JPEG chromatic adaptation is explicitly post-development color correction, never described as RAW white balance.
