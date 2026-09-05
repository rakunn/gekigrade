# Decision log

## ADR-001 — Python 3.12 and uv

Use the installed Python 3.12 runtime and a committed `uv.lock`. Python 3.13 remains outside the supported range for the first slice so native-wheel behavior is frozen.

## ADR-002 — RawTherapee as first RAW adapter

Choose RawTherapee 5.13 because explicit, stackable PP3 profiles and 16-bit TIFF CLI output form a compact reproducibility boundary. Defer darktable because its XMP history plus application configuration, presets, and database state add more inputs to freeze. One private user-owned Sony ARW now confirms this adapter path; broader quality remains an evaluation milestone.

## ADR-003 — 16-bit TIFF intermediate

Use oriented, profile-tagged 16-bit TIFF for Milestone 1. It provides inspectable ICC metadata and matches the chosen RAW adapter path. Reconsider float OpenEXR after real RAW dynamic-range or local-compositing evidence.

## ADR-004 — linear ACEScg working space

Use macOS `ACESCG Linear.icc` at the LittleCMS boundary and the pinned OCIO ACES 2.0 CG configuration internally. Record the system profile hash and treat a changed hash as a different rendering fingerprint. ImageMagick commands operate on private, hash-checked profile snapshots and reject output if either a snapshot or its source profile changes. The validated ACEScg and sRGB hashes are preserved through manifest construction, replace redundant doctor-report hashes, and are bracket-checked around manifest publication. JPEG normalization must retain the EXIF-oriented dimensions, while the analysis preview must exactly preserve their aspect ratio within a 2048-pixel maximum edge. This is a local macOS decision, not a cross-platform guarantee.

Normalization and preview encoding resolve and invoke one explicit ImageMagick executable. Each operation brackets execution with its path, version, and binary SHA-256 fingerprint. It receives a minimal C-locale environment with fixed system `PATH`, `MAGICK_THREAD_LIMIT=1`, and pinned single-thread OpenMP settings; caller configuration, coder/filter paths, and unrelated variables are excluded. The adapter hashes each regular, non-symlink target through a stable descriptor before returning. `source.json` and the prepared manifest retain the actual producer identity, complete passed environment, and output hash separately from the environment snapshot reported by `geki doctor`. Working-TIFF and preview validation must reproduce that output hash, and the accepted artifact hashes are then held constant through manifest publication. Preview pixels are accepted only from a three-channel JPEG carrying the expected sRGB profile. Doctor tests the same fixed ExifTool and ImageMagick paths used by default preparation rather than substituting a different executable found on `PATH`.

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

Security hardening at this boundary is fail-closed. Lensfun camera make/model must select exactly one database entry before GekiGrade trusts its mounts; duplicate normalized records produce no correction-support claim. Doctor rejects a symlinked RawTherapee CLI before consulting bundle metadata. Publication-boundary source validation opens without following symlinks and without blocking on special files, then requires a stable regular-file identity and the recorded digest; every failed post-publication check removes the provisional manifest.

Sony ARW uses RawTherapee 5.13 with a committed partial PP3 layered only on that version's neutral defaults. Source signature detection rejects symlinks and non-regular files before opening them. RAW preparation always selects the committed profile rather than accepting a caller-supplied PP3 while making fixed capability claims. The shipped PP3 must match its code-pinned digest before a job is created, the copied execution profile must retain that identity through manifest publication, and doctor uses the same rule for RAW readiness; changing profile semantics therefore requires an intentional profile, digest, documentation, and test update. Each run receives isolated settings, cache, and temporary directories plus a minimal fully recorded subprocess environment with a C locale and pinned single-thread OpenMP settings; caller environment variables are not inherited. Arguments remain fixed and non-shell. The adapter rejects any RawTherapee version other than 5.13, revalidates 5.13 at the immediate launch fingerprint boundary, requires output TIFFs to contain exactly three RGB channels with 16-bit samples, hashes the source around ExifTool metadata inspection, records the exact resolved ExifTool path/version/hash, rejects an executable change during extraction, and verifies the inspected source hash after development and before publishing a prepared manifest. A change while the manifest is written removes that manifest and fails the job. The RawTherapee executable version and hash are captured before launch and must match after exit; malformed application metadata produces structured not-ready doctor output. Because `(cameraICC)` may resolve through external application resources, the adapter mirrors RawTherapee's DCP, ICC, then camera-matrix selection order; it records the selected profile plus alias and camera-constants fingerprints and rejects a resource change during development. Camera constants are accepted using RawTherapee's JSON-with-comments syntax only when parsing yields a `camera_constants` object list. Lensfun inspection and the `RTv4_Large` output profile are bound to the selected executable's application bundle with no preparation-time path override. The Lensfun search fingerprints every bundled XML file rather than a manufacturer-specific subset, resolves the camera mount, and requires model, mount, and available lens-maker metadata to agree. Missing-maker cross-manufacturer ambiguity or multiple remaining fully matching entries produces no lens match. The output profile must be a regular non-symlink file, its hash is checked across development, and it must match the developed TIFF's embedded ICC. The developed TIFF hash must match the RawTherapee result before profile inspection and throughout normalization. Only RAW EXIF orientation 1 is accepted; orientations 2–8 are rejected until the complete output transform, including 90-degree direction and mirroring, can be independently verified. The normalized working TIFF's axis ordering must agree with the orientation-1 inspection, may not exceed either inspected dimension, and must retain at least 95% of both axes before exact border-cropped dimensions are adopted. The working TIFF must then decode as exactly three RGB channels with 16-bit samples and the expected ACEScg profile. Both developed and normalized TIFF validation calculate the accepted SHA-256 through the same open file descriptor used for decoding and reject any file-identity change through close, preventing a path replacement from separating validated structure from recorded bytes. The profile selects camera white balance, AMaZE, Coloropp highlight recovery, automatic RAW chromatic-aberration correction, requested Lensfun distortion/vignetting correction, no denoising, no sharpening, and `RTv4_Large` 16-bit TIFF output. LittleCMS converts the embedded output profile to the existing ACEScg working TIFF using a stable private snapshot. Requested corrections, calibration types found in the matched lens entry, and actual-application confirmation are recorded independently.
