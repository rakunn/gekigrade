# Color pipeline

## Confirmed local behavior

The inspected ImageMagick build is Q16-HDRI and includes LittleCMS, TIFF, OpenEXR, JPEG, PNG, and RAW delegates. Python 3.12 Apple Silicon wheels for OpenImageIO 3.1.16 and OpenColorIO 2.5.2 import successfully. A synthetic embedded-sRGB JPEG converted twice to 16-bit ACEScg TIFF produced identical file hashes in the environment spike. Returning through the pinned OCIO transform produced a small difference attributable to JPEG re-encoding. This confirms compatibility, not photographic quality.

## Input interpretation

An RGB JPEG with a valid ICC profile is transformed from that profile. An RGB JPEG without a profile is assigned sRGB and records a warning and assumption. An unprofiled CMYK JPEG is rejected. EXIF orientation is applied exactly once before the working image is stored.

## Working representation

Milestone 1 uses 16-bit integer RGB TIFF tagged with the macOS `ACESCG Linear.icc` profile. The manifest records profile path, description, and SHA-256. OpenImageIO reads pixels as normalized float32 for processing. TIFF is selected over OpenEXR because this slice can inspect its ICC payload end to end and it aligns with the first RAW adapter's documented output.

## Operation ordering and spaces

1. Orientation and working-profile transform through LittleCMS.
2. Rotation in linear ACEScg.
3. Exposure and chromatic adaptation in linear ACEScg.
4. Contrast, toe, shoulder, and saturation in ACEScct through pinned OCIO transforms.
5. Creative look in ACEScct, blended by bounded strength.
6. Vignette in linear ACEScg.
7. Crop in oriented, post-rotation normalized coordinates.
8. Scale and unsharp mask with resolution-aware parameters.
9. Colorimetric ACEScg to encoded sRGB through the pinned OCIO configuration.
10. Pre-clamp gamut/clipping measurements, final clamp, JPEG encoding, sRGB ICC attachment, and independent verification.

The pipeline does not use an ACES display look or an undocumented LUT. It does not describe JPEG post-development chromatic adaptation as RAW white balance.

## Preview and full resolution

The same recipe evaluator receives either the prepared analysis image or full-resolution working TIFF. Normalized crop geometry and kernel radii referenced to a 2048-pixel long edge preserve logical equivalence. The manifest distinguishes the render target and dimensions.

## Export metadata

The default safe policy may copy capture date, camera/lens, exposure settings, focal length, artist, and copyright. It excludes GPS, serial numbers, thumbnails, MakerNotes, user comments, face regions, and proprietary edit history. The strip policy retains only format-required metadata and ICC information.

## Clipping and gamut

Analysis uses encoded-sRGB thresholds of at most `1/255` for shadows and at least `254/255` for highlights, reporting any-channel and all-channel percentages. Export records pre-clamp out-of-range percentages and post-encode clipping. Structural QA rejects NaN/Inf and missing profiles; configurable photographic thresholds initially warn rather than mutate a recipe.

## Assumptions

Strict reproducibility requires identical ACEScg/sRGB profile hashes, OCIO configuration, OIIO/ImageMagick versions, architecture, and thread configuration. Cross-platform output is assessed by decoded-pixel tolerance. The system ICC dependency makes the first slice macOS-specific; a redistributable pinned profile requires a later licensing and portability decision.
