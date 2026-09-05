# Color pipeline

## Confirmed local behavior

The inspected ImageMagick build is Q16-HDRI and includes LittleCMS, TIFF, OpenEXR, JPEG, PNG, and RAW delegates. Python 3.12 Apple Silicon wheels for OpenImageIO 3.1.16 and OpenColorIO 2.5.2 import successfully. A synthetic embedded-sRGB JPEG converted twice to 16-bit ACEScg TIFF produced identical file hashes in the environment spike. A private Sony ILCE-7RM5 ARW developed twice through RawTherapee 5.13 produced zero differing decoded pixels; the normalized ACEScg TIFFs were also byte-identical. This confirms compatibility and same-machine determinism, not general photographic quality.

## Input interpretation

Input format detection first requires a regular, non-symlink source before reading signature bytes, so special files such as FIFOs cannot block inspection. An RGB JPEG with a valid ICC profile is transformed from that profile. An RGB JPEG without a profile is assigned sRGB and records a warning and assumption. An unprofiled CMYK JPEG is rejected. EXIF orientation is applied exactly once before the working image is stored.

A Sony ARW is accepted only when its TIFF-based signature and ExifTool type/MIME metadata agree. The source is hashed immediately before ExifTool runs and verified immediately afterward, so captured metadata and later development cannot silently refer to different source bytes. The exact resolved ExifTool executable path, version, and SHA-256 are recorded in `source.json`; the executable fingerprint must remain unchanged across metadata extraction. Preparation always uses the committed PP3; it does not accept a profile override while reporting these fixed semantics. The copied PP3 hash is captured before launch and must remain identical after RawTherapee exits. RawTherapee uses camera white balance, AMaZE demosaicing, Coloropp highlight recovery, automatic RAW chromatic-aberration correction, and requests Lensfun distortion/vignetting correction. Denoising and sharpening are disabled at development so the later recipe retains control. The adapter resolves RawTherapee's automatic camera-input selection in DCP, ICC, then camera-matrix order. It records the selected profile when present plus the camera-alias and camera-constants resource hashes, and verifies that fingerprint again after development. RawTherapee's comment-bearing `camconst.json` is parsed after string-aware comment removal and must contain a `camera_constants` object list. The Lensfun directory is derived from the selected executable's application bundle rather than accepted from a caller. That complete XML set is searched and recorded with per-file and aggregate hashes so third-party E-mount lenses are not excluded; camera matching happens before lens matching, and a lens is accepted only when its mount overlaps the matched camera mount. The Lensfun fingerprint is checked before and after development. The RawTherapee executable version and hash are captured at the launch boundary; the captured version must be 5.13 and both values must remain identical after exit. The output profile, arguments, logs, and source/output hashes are also recorded. Requested corrections, calibration types present in the matched lens entry, and actual-application confirmation are separate fields.

## Working representation

The working representation is 16-bit integer RGB TIFF tagged with the macOS `ACESCG Linear.icc` profile. JPEG reaches it through LittleCMS directly. RAW first produces a TIFF that must contain exactly three RGB channels with 16-bit samples and whose embedded ICC hash must equal the installed RawTherapee `RTv4_Large` profile; any mismatch blocks the job. The developed TIFF hash is checked before profile inspection and immediately around normalization, so the normalized working image cannot silently come from different bytes than the recorded development output. LittleCMS then transforms that profile into ACEScg and strips non-deterministic development metadata before reattaching the ACEScg profile. Normalization and preview generation each resolve and invoke an explicit ImageMagick executable, bracket-check its version and binary hash, and retain that producer identity in `source.json` and the manifest. The prepared manifest is published only after a final RAW source check and is removed if the source changes while the manifest is being written. The manifest records profile paths and SHA-256 values. OpenImageIO reads pixels as normalized float32 for recipe processing.

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
