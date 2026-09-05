# Decision log

## ADR-001 — Python 3.12 and uv

Use the installed Python 3.12 runtime and a committed `uv.lock`. Python 3.13 remains outside the supported range for the first slice so native-wheel behavior is frozen.

## ADR-002 — RawTherapee as first RAW adapter

Choose RawTherapee 5.13 because explicit, stackable PP3 profiles and 16-bit TIFF CLI output form a compact reproducibility boundary. Defer darktable because its XMP history plus application configuration, presets, and database state add more inputs to freeze. One private user-owned Sony ARW now confirms this adapter path; broader quality remains an evaluation milestone.

## ADR-003 — 16-bit TIFF intermediate

Use oriented, profile-tagged 16-bit TIFF for Milestone 1. It provides inspectable ICC metadata and matches the chosen RAW adapter path. Reconsider float OpenEXR after real RAW dynamic-range or local-compositing evidence.

## ADR-004 — linear ACEScg working space

Use macOS `ACESCG Linear.icc` at the LittleCMS boundary and the pinned OCIO ACES 2.0 CG configuration internally. Record the system profile hash and treat a changed hash as a different rendering fingerprint. This is a local macOS decision, not a cross-platform guarantee.

Normalization and preview encoding resolve and invoke one explicit ImageMagick executable. Each operation brackets execution with its path, version, and binary SHA-256 fingerprint; `source.json` and the prepared manifest retain those actual producer identities separately from the environment snapshot reported by `geki doctor`. Doctor tests the same fixed ExifTool and ImageMagick paths used by default preparation rather than substituting a different executable found on `PATH`.

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

## ADR-012 — RawTherapee development boundary

Sony ARW uses RawTherapee 5.13 with a committed partial PP3 layered only on that version's neutral defaults. Source signature detection rejects symlinks and non-regular files before opening them. RAW preparation always selects the committed profile rather than accepting a caller-supplied PP3 while making fixed capability claims, and the copied profile hash must remain unchanged across execution. Each run receives isolated `RT_SETTINGS` and `RT_CACHE` directories and fixed, non-shell CLI arguments. The adapter rejects any RawTherapee version other than 5.13, revalidates 5.13 at the immediate launch fingerprint boundary, requires output TIFFs to contain exactly three RGB channels with 16-bit samples, hashes the source around ExifTool metadata inspection, records the exact resolved ExifTool path/version/hash, rejects an executable change during extraction, and verifies the inspected source hash after development and before publishing a prepared manifest. A change while the manifest is written removes that manifest and fails the job. The RawTherapee executable version and hash are captured before launch and must match after exit; malformed application metadata produces structured not-ready doctor output. Because `(cameraICC)` may resolve through external application resources, the adapter mirrors RawTherapee's DCP, ICC, then camera-matrix selection order; it records the selected profile plus alias and camera-constants fingerprints and rejects a resource change during development. Camera constants are accepted using RawTherapee's JSON-with-comments syntax only when parsing yields a `camera_constants` object list. Lensfun inspection and the `RTv4_Large` output profile are bound to the selected executable's application bundle with no preparation-time path override. The Lensfun search fingerprints every bundled XML file rather than a manufacturer-specific subset, resolves the camera mount, and requires model, mount, and available lens-maker metadata to agree. Missing-maker cross-manufacturer ambiguity or multiple remaining fully matching entries produces no lens match. The output profile must be a regular non-symlink file, its hash is checked across development, and it must match the developed TIFF's embedded ICC. The developed TIFF hash must match the RawTherapee result before profile inspection and throughout normalization. The normalized working TIFF must then decode as exactly three RGB channels with 16-bit samples and the expected ACEScg profile. The profile selects camera white balance, AMaZE, Coloropp highlight recovery, automatic RAW chromatic-aberration correction, requested Lensfun distortion/vignetting correction, no denoising, no sharpening, and `RTv4_Large` 16-bit TIFF output. LittleCMS converts the embedded output profile to the existing ACEScg working TIFF. Requested corrections, calibration types found in the matched lens entry, and actual-application confirmation are recorded independently.
