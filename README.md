# GekiGrade

GekiGrade is a local-first, LLM-directed photo correction and color-grading pipeline. An LLM may inspect prepared artifacts and choose a bounded edit recipe; conventional image-processing tools perform all pixel operations deterministically.

GekiGrade is not a generative editor. It does not fill, outpaint, replace skies, remove objects, reconstruct faces, invent detail, or interpret prose inside the renderer. The source photograph is immutable.

## Status

The first supported vertical slice is one JPEG per job on Apple Silicon macOS. It prepares technical artifacts, validates a versioned edit plan, renders three candidates, records an explicit selection, and exports color-managed sRGB JPEGs. Sony ARW is the next adapter milestone and remains unverified until a user-owned fixture is available.

## Architecture

The `geki` CLI calls a Python domain core. Pydantic models define the job, recipe, look, selection, artifact, and QA contracts. OpenImageIO and NumPy perform high-bit-depth deterministic processing, OpenColorIO supplies pinned ACES transforms, ImageMagick/LittleCMS handles ICC boundaries, and ExifTool reads metadata. External tools are invoked only through fixed adapters.

## Installation

macOS system requirements:

```bash
brew install uv exiftool imagemagick
```

RawTherapee is used by the later RAW milestone and by the environment feasibility check:

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
- The initial looks are restrained engineering defaults. Their photographic quality has not been established on real user photographs.
- Pixel identity is guaranteed only for an identical tool, profile, configuration, architecture, and thread fingerprint. Cross-platform conformance uses a documented tolerance.
- No RAW quality, automatic lens correction, perspective correction, semantic masking, publishing, API orchestration, or desktop UI is claimed.

See [`docs/DEPENDENCIES.md`](docs/DEPENDENCIES.md) for selection and licensing details and [`docs/RAW_MANUAL_TEST.md`](docs/RAW_MANUAL_TEST.md) for the pending ARW procedure.
