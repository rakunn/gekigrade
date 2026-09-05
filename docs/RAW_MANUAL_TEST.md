# Manual Sony ARW test procedure and result

Do not download or commit an arbitrary RAW. Use a user-owned Sony ARW and keep it outside the job directory and repository.

1. Record the ARW SHA-256, camera/lens identifiers, exposure metadata, file size, and permission/provenance note.
2. Run `geki doctor` and save its report, including RawTherapee, profile, and platform fingerprints.
3. Create a reviewed PP3 that explicitly sets demosaicing, input/camera profile behavior, white balance, highlight reconstruction, noise reduction, sharpening, lens distortion, vignetting, chromatic aberration, working profile, output profile, and output intent. Do not rely on GUI defaults.
4. Copy the PP3 into the job and hash it. Run the RawTherapee CLI adapter from a private snapshot of the selected application bundle, with a private temporary configuration directory, the explicit PP3, no overwrite flag, a new destination path, and 16-bit TIFF output. Capture arguments, stdout, stderr, exit code, duration, runtime snapshot strategy, resource fingerprints, and output hash.
5. Repeat from a fresh temporary configuration and compare decoded pixels. A mismatch blocks the adapter until the hidden input is identified.
6. Inspect the TIFF bit depth, dimensions, orientation, ICC profile, clipping, and finite values before admitting it to the same ACEScg working-image contract used by JPEG.
7. Record separately whether distortion, vignetting, and chromatic-aberration correction were requested, supported for that camera/lens, and actually applied. Missing profiles must warn; they must not silently become “corrected.”
8. Compare the result with the permitted in-camera JPEG and a trusted manual development. Score color, detail, noise, highlight recovery, lens behavior, artifacts, and whether the reference is preferable.
9. Keep the ARW and rendered comparisons private unless explicit commit permission is granted.

## 2026-08-31 execution

The procedure was executed with one private user-owned Sony ILCE-7RM5 ARW captured with an FE 24–70mm F2.8 GM II. The file and its private path are not committed.

- Source SHA-256 was identical before and after inspection, two clean developments, rendering, QA, and export.
- RawTherapee 5.13 produced a 9556×6366, 16-bit TIFF tagged with the installed `RTv4_Large` profile.
- Camera and lens entries were found in RawTherapee's bundled data. The matched lens entry contains distortion calibration but no vignetting calibration, so not every requested correction is supported. RawTherapee CLI also did not confirm actual application.
- Two developments with fresh settings/cache directories had zero differing decoded pixels.
- The two normalized ACEScg working TIFFs were byte-identical and explicitly tagged ACES CG Linear.
- Three candidates rendered, technical QA passed, and a 1080×1350 JPEG was exported with an embedded sRGB profile and without GPS or serial metadata.
- A visual inspection found the neutral development technically coherent but the default conservative recipe left the shaded foreground dark. This is a plan-tuning observation, not a RAW-development failure.

Step 8 remains incomplete because no paired in-camera JPEG or trusted manual development was supplied. The adapter may be described as functional for this compatibility path, not as generally validated for Sony RAW quality.
