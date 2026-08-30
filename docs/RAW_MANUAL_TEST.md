# Manual Sony ARW test procedure (Milestone 2 gate)

Do not download or commit an arbitrary RAW. Use a user-owned Sony ARW and keep it outside the job directory and repository.

1. Record the ARW SHA-256, camera/lens identifiers, exposure metadata, file size, and permission/provenance note.
2. Run `geki doctor` and save its report, including RawTherapee, profile, and platform fingerprints.
3. Create a reviewed PP3 that explicitly sets demosaicing, input/camera profile behavior, white balance, highlight reconstruction, noise reduction, sharpening, lens distortion, vignetting, chromatic aberration, working profile, output profile, and output intent. Do not rely on GUI defaults.
4. Copy the PP3 into the job and hash it. Run the RawTherapee CLI adapter with a private temporary configuration directory, the explicit PP3, overwrite permission limited to a new temporary output path, and 16-bit TIFF output. Capture arguments, stdout, stderr, exit code, duration, and output hash.
5. Repeat from a fresh temporary configuration and compare decoded pixels. A mismatch blocks the adapter until the hidden input is identified.
6. Inspect the TIFF bit depth, dimensions, orientation, ICC profile, clipping, and finite values before admitting it to the same ACEScg working-image contract used by JPEG.
7. Record separately whether distortion, vignetting, and chromatic-aberration correction were requested, supported for that camera/lens, and actually applied. Missing profiles must warn; they must not silently become “corrected.”
8. Compare the result with the permitted in-camera JPEG and a trusted manual development. Score color, detail, noise, highlight recovery, lens behavior, artifacts, and whether the reference is preferable.
9. Keep the ARW and rendered comparisons private unless explicit commit permission is granted.

Only after this procedure succeeds may the RAW adapter be described as functional. One ARW validates compatibility, not general Sony RAW quality.
