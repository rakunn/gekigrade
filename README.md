# GekiGrade

GekiGrade is a local-first, LLM-directed photo correction and color-grading pipeline. An LLM may inspect prepared artifacts and choose a bounded edit recipe; conventional image-processing tools perform all pixel operations deterministically.

GekiGrade is not a generative editor. It does not fill, outpaint, replace skies, remove objects, reconstruct faces, invent detail, or interpret prose inside the renderer. The source photograph is immutable.

## Status

The supported vertical slice is one JPEG or Sony ARW per job on Apple Silicon macOS. It prepares technical artifacts and deterministic edge/center crop alternatives, validates a versioned edit plan, renders three candidates, records an explicit selection, and exports color-managed sRGB JPEGs. Private compatibility evaluation now covers 15 normal-orientation Sony ILCE-7RM5 files from one camera/lens combination; independent development repeatability was checked on the original pilot. This is not a general RAW-quality claim.

## Architecture

The `geki` CLI calls a Python domain core. Pydantic models define the job, recipe, look, selection, artifact, and QA contracts. OpenImageIO and NumPy perform high-bit-depth deterministic processing, OpenColorIO supplies pinned ACES transforms, ImageMagick/LittleCMS handles ICC boundaries, and ExifTool reads metadata. External tools are invoked only through fixed adapters.

## Installation

macOS system requirements:

```bash
brew install uv exiftool imagemagick
```

RawTherapee is required for Sony ARW development:

```bash
brew install --cask rawtherapee
```

Install the locked project environment:

```bash
uv sync
```

## Example workflow

```bash
uv run geki doctor
uv run geki prepare tests/fixtures/sample.jpg --output work/sample
uv run geki validate-plan work/sample/plans/example-plan.json
uv run geki render work/sample --plan work/sample/plans/example-plan.json
uv run geki qa work/sample
uv run geki select work/sample 02-warm-editorial
uv run geki export work/sample --preset instagram-feed
```

The same workflow accepts an ARW while keeping the source outside the job directory:

```bash
uv run geki prepare /path/to/photo.ARW --output work/photo-raw
```

## Safety guarantees

- Source bytes are hashed before and after work.
- Plans reject unknown fields, operations, looks, crop references, and out-of-range values.
- Recipes contain no executable fragments or arbitrary paths.
- Derived writes stay inside a validated job directory; JSON state files use atomic promotion.
- The manifest records source, profile, tool, plan, and artifact checksums. QA records output dimensions and profile presence.
- Final JPEGs are encoded as sRGB and contain an embedded sRGB ICC profile.

## Limitations

- Milestone 1 is macOS-first because it uses the operating system's ACEScg and sRGB ICC profiles and records their hashes rather than redistributing Apple assets.
- JPEGs without an embedded profile are explicitly assumed to be sRGB; unprofiled CMYK JPEGs are rejected.
- The initial looks are restrained engineering defaults. A private real-photo comparison now has provisional model assessments, but human preference and broader photographic-quality acceptance remain pending.
- Pixel identity is guaranteed only for an identical tool, profile, configuration, architecture, and thread fingerprint. Cross-platform conformance uses a documented tolerance.
- Fifteen normal-orientation Sony ILCE-7RM5/FE 24–70mm F2.8 GM II files passed the private compatibility run. This camera/lens combination's bundled Lensfun entry contains distortion but not vignetting calibration, and RawTherapee does not report actual application. GekiGrade records requested, supported, and confirmed states separately.
- RAW EXIF orientation 1 is accepted. Orientations 2–8 are rejected until the pipeline can verify the complete pixel transform, including rotation direction and mirroring, rather than infer it from dimensions.
- Crop alternatives are geometric left/center/right or top/center/bottom anchors. They do not detect subjects, faces, horizons, or perspective, and their composition quality still requires human review.
- General RAW quality, paired camera-JPEG fidelity, perspective correction, semantic masking, publishing, API orchestration, and desktop UI remain unproven or deferred.

See [`docs/DEPENDENCIES.md`](docs/DEPENDENCIES.md) for selection and licensing details, [`docs/RAW_MANUAL_TEST.md`](docs/RAW_MANUAL_TEST.md) for the ARW compatibility procedure, and [`docs/REAL_PHOTO_EVALUATION.md`](docs/REAL_PHOTO_EVALUATION.md) for measured outcomes, provisional visual findings, and remaining acceptance gaps.

## Global tone controls and stage QA

Edit-plan `1.0.0` keeps its existing pixel semantics. New opt-in `2.0.0` plans replace the global `highlight_rolloff` field with required `shadow_recovery_ev` (0–2 stops, fades out at 18% linear luminance) and `highlight_compression` (0–1, luminance shoulder above 50%). They also apply fixed output-gamut compression. Versioned look definitions are unchanged; do not rename a legacy plan's version without explicitly replacing its controls.

Preparation writes `plans/example-plan-v2.json` alongside the legacy example, plus both versioned schemas and their union. The new example starts with **zero shadow recovery** and 0.5 highlight compression; it is a starting point for visual comparison, not a scene recommendation:

```bash
uv run geki validate-plan work/sample/plans/example-plan-v2.json
uv run geki render work/sample --plan work/sample/plans/example-plan-v2.json
uv run geki qa work/sample
```

Inspect `qa/report.json`: correction, creative-look, pre-gamut, and pre-clamp measurements show where changes occur. Full-frame and cropped-output percentages have different denominators. Post-sharpening and decoded-JPEG clipping are measured separately. Gamut compression may trade saturation for fewer channel excursions and cannot retain out-of-range luminance; shadow recovery can amplify noise. Dark silhouettes can remain intentional. Evaluation includes a controlled backlit pilot and fixed-look comparisons on 14 additional private RAWs; general quality acceptance remains open. See [the math and QA contract](docs/COLOR_PIPELINE.md), [operator comparison](docs/TONE_EXPERIMENT.md), and [real-photo evaluation](docs/REAL_PHOTO_EVALUATION.md).
