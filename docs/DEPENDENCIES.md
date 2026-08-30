# Dependency record

Versions below are the verified local versions on 2026-08-30. Homebrew packages are system prerequisites; Python packages are pinned by `uv.lock`.

## Selected for Milestone 1

| Dependency | Pipeline role and interface | Installation | License / redistribution | Color and format behavior | Principal risk |
|---|---|---|---|---|---|
| ExifTool 13.55 | Read an allowlisted metadata subset through a fixed-argument subprocess. | `brew install exiftool` | Perl Artistic License or GPL; bundle notices and terms if redistributed. | Reads EXIF and ICC-related tags but does not transform pixels. | Metadata is untrusted; output is parsed as JSON and never becomes a command. |
| ImageMagick 7.1.1-47 Q16-HDRI with LittleCMS | Apply orientation, perform source-ICC to ACEScg conversion, write 16-bit TIFF, create preview encoding. Fixed-argument subprocess. | `brew install imagemagick` | ImageMagick License; bundled redistribution needs attribution and license text. | `-profile` performs the ICC transform; TIFF and JPEG retain explicit profiles. The local build reports LCMS, TIFF and JPEG support. | Behavior depends on build delegates and system profile bytes, so both are fingerprinted. |
| OpenImageIO 3.1.16.0 | Read the working TIFF as float32 and perform Lanczos3 rotation/resizing through Python bindings. | `uv sync --frozen` | Apache-2.0. Include notices if bundled. | Pixels are treated according to the prepared-image contract; OIIO does not infer an arbitrary working space in recipe evaluation. | Native wheel and algorithm versions affect pixels; exact hashes are fingerprint-scoped. |
| OpenColorIO 2.5.2 | Transform ACEScg, ACEScct and encoded sRGB using the named built-in ACES 2.0 CG config. Python binding. | `uv sync --frozen` | BSD-3-Clause. Include notice if bundled. | Uses `cg-config-v4.0.0_aces-v2.0_ocio-v2.5`; no external LUT is required. | A config or library upgrade is a rendering change and needs new baselines and visual review. |
| NumPy 2.x | Histogram/statistical analysis and explicit float32 global operations. | `uv sync --frozen` | BSD-3-Clause. | Operations use named ACEScg or ACEScct stages. | Architecture and threading changes can affect floating-point least-significant bits. |
| Pillow 12.x | Safe JPEG decode checks, contact sheets, final deterministic JPEG encoding, ICC attachment, and safe EXIF allowlist. | `uv sync --frozen` | HPND. | Final files embed the macOS sRGB profile; clipping is measured before and after encoding. | JPEG encoder upgrades may change file bytes; decoded conformance is evaluated separately. |
| Pydantic 2.x / Typer 0.x | Strict recipe boundary and CLI only; no pixel operations. | `uv sync --frozen` | MIT. | Unknown fields and invalid bounds fail before image processing. | Schema changes require a version change and committed schema update. |

Primary references: [ExifTool source and license](https://github.com/exiftool/exiftool), [ImageMagick color management](https://imagemagick.org/color-management/), [ImageMagick license](https://imagemagick.org/license/), [OpenImageIO documentation](https://openimageio.readthedocs.io/), [OpenColorIO ACES CG config](https://opencolorio.readthedocs.io/en/v2.5.2/configurations/aces_cg.html).

## Selected for Sony ARW development

RawTherapee 5.13 is installed from `brew install --cask rawtherapee`. The adapter calls `rawtherapee-cli` with a committed PP3 and requests a ZIP-compressed 16-bit TIFF, captures stdout/stderr, isolates settings/cache state, and records executable, application, PP3, profile, Lensfun database, source, and output fingerprints. RawTherapee is GPL-3.0; using a separately installed executable is suitable for this personal tool, while bundling or redistribution requires a dedicated GPL compliance review. Same-machine repeatability and one Sony ILCE-7RM5 compatibility path are confirmed. Camera color quality, paired JPEG fidelity, and broad lens/camera behavior remain unverified.

RawTherapee was preferred over darktable for the first adapter because a PP3 plus explicit CLI arguments is a smaller state surface. The official darktable CLI supports explicit XMP history but can also consult the library, configuration, styles, auto-presets, and last export configuration; isolating all of that is possible but broader. This is a reversible adapter choice, not a claim that RawTherapee has superior image quality. Primary references: [RawTherapee source/license](https://github.com/RawTherapee/RawTherapee), [darktable CLI](https://docs.darktable.org/usermanual/development/en/special-topics/program-invocation/darktable-cli/), and [darktable isolated test environment](https://docs.darktable.org/usermanual/development/en/special-topics/test-environment/).

## Considered and deferred

- **OpenEXR intermediate:** stronger for unclipped float interchange, but the first JPEG slice benefits from a directly inspectable 16-bit ICC-tagged TIFF and matches RawTherapee output. Reconsider if real RAW evaluation shows TIFF range loss.
- **libvips/pyvips:** excellent streaming performance and LittleCMS support, but duplicates OIIO/Pillow roles for one-image-at-a-time Milestone 1. Its LGPL-2.1-or-later redistribution obligations also add packaging work. Reconsider for large-image memory pressure or batch export. [Official libvips overview](https://www.libvips.org/).
- **OpenCV:** not selected because its default image APIs do not provide the explicit ICC-managed boundary needed here, while its geometry/statistics would duplicate existing dependencies.
- **G'MIC:** not selected because the initial allowlist is small and analytic; its broad command/filter surface would increase validation and redistribution complexity.
- **darktable:** deferred as the alternative RAW adapter for the state-isolation reasons above.

## System profiles

The first slice uses `/System/Library/ColorSync/Profiles/ACESCG Linear.icc` and `/System/Library/ColorSync/Profiles/sRGB Profile.icc`. Their SHA-256 values are recorded per job. Apple profile files are not copied into this repository or redistributed. A future portable release needs independently redistributable, pinned profiles and a separate license decision.
