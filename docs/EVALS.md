# Evaluation protocol

## Technical checks

- Source SHA-256 is unchanged after success and every reproduced failure.
- A structurally and semantically valid recipe is required before rendering.
- Unknown operations, fields, looks, versions, crops, and out-of-range values are rejected.
- Orientation and normalized crop transforms produce expected dimensions at preview and full resolution. JPEG working dimensions must equal the EXIF-oriented source, and preview dimensions must exactly match the aspect-preserving 2048-pixel maximum-edge transform.
- Working and output profiles are present, described, hashed, and independently verified.
- NaN/Inf, malformed images, unprofiled CMYK, unsafe paths, missing tools, timeouts, and non-zero subprocess exits are surfaced.
- Repeated renders under the same fingerprint have identical decoded-pixel hashes.
- Repeated RAW developments use fresh isolated settings/cache/temp directories, a minimal recorded locale/path environment, and pinned single-thread OpenMP settings. They require zero decoded-pixel differences under the same fingerprint; container hashes may differ when the external engine writes timestamps.
- RawTherapee executes from a private symlink-free application-bundle snapshot whose executable and accepted camera, alias, constants, Lensfun, and output-profile fingerprints match the selected-bundle evidence before launch and remain unchanged after execution. Temporarily replacing a resource in the selected bundle during execution must not alter developed pixels.
- The RawTherapee invocation never passes an overwrite flag. A destination-path symlink raced into place must produce failure without changing the symlink target.
- RAW output must be exactly three-channel RGB with 16-bit samples, profile-tagged, correctly oriented, and admitted to ACEScg only after the intermediate profile is verified.
- RawTherapee output structure and its recorded SHA-256 must come from one open file descriptor whose identity remains stable through validation; replacing the path during validation fails the job and removes the output.
- The developed TIFF hash must match the recorded RawTherapee output before profile inspection and immediately before and after normalization. Normalization reads a private, read-only, identity-bound snapshot, so replacing the retained developed path during conversion cannot alter working pixels.
- RAW metadata inspection copies the source into a transient read-only, identity-stable snapshot with the validated digest and invokes ExifTool only on that snapshot. Both snapshot and original are revalidated afterward, including temporary original-path replacement tests. It records the exact resolved ExifTool path, version, and executable hash and rejects a binary change during extraction.
- JPEG and RAW normalization plus preview encoding record the exact resolved ImageMagick path, version, and executable hash for each operation; a binary change during an operation is rejected.
- ImageMagick receives private ICC snapshots whose hashes remain stable across the command. A changed snapshot or source system profile rejects and removes the produced artifact.
- ImageMagick receives only the recorded allowlisted environment with fixed locale, system path, and single-thread ImageMagick/OpenMP settings; caller configuration and module paths do not cross the subprocess boundary.
- Each ImageMagick adapter result contains the SHA-256 of a regular, non-symlink, identity-stable output; working-TIFF and preview validation must reproduce that exact invocation-bound hash.
- RawTherapee camera-input provenance records the selected DCP or ICC profile, or the camera-matrix fallback, together with camera-alias and camera-constants hashes; the resource fingerprint must remain unchanged during development.
- RawTherapee executable version and file hash are captured before launch and must remain unchanged after exit.
- The accepted RawTherapee output ICC path/hash remains unchanged through manifest publication; its complete availability, validity, color-space, device-class, error, path, and hash status comes from the selected bundle and is recorded in the manifest tool snapshot.
- The manifest's RawTherapee tool status, camera resources, Lensfun database, RAW readiness, and RAW status all come from the executable selected for the prepared job; the developed TIFF must retain its recorded producer hash through publication.
- Manifest readiness and tool status must use the exact selected ExifTool and ImageMagick executables as well as the selected RawTherapee bundle; unavailable system defaults cannot make a successful custom-tool job appear not ready.
- Accepted selected-camera profile/alias/constants and complete Lensfun snapshots must remain unchanged through manifest publication; the accepted camera-resource and Lensfun statuses, not recomputed replacements, populate the manifest report.
- A selected RawTherapee executable with any symlinked path component is rejected before job creation. The adapter returns the final run-report hash, source provenance records it, and preparation rejects run-report modification or removal before or during manifest publication.
- ExifTool doctor/version and metadata reads pass `-config ""` and the same recorded C-locale, fixed-PATH environment. Caller `HOME`, `EXIFTOOL_HOME`, and unrelated variables must not cross the subprocess boundary.
- RAW preparation rejects a caller-supplied PP3, requires the shipped profile to match its pinned digest before creating a job, creates the execution copy exclusively without following a raced destination symlink, and requires that copy to retain its identity through manifest publication. Doctor reports RAW ready only for the same known profile. The version captured at the launch boundary must equal the supported 5.13 release.
- Source format detection reads through a non-blocking, no-follow, identity-stable regular-file descriptor. RAW hashes immediately before and after ExifTool use the same contract, and reject a descriptor over 1 GiB before streaming its contents, so special-file, oversized-file, or symlink replacement fails inspection; the copied PP3 hash must remain unchanged across RAW execution.
- Camera alias lookup rejects more than one canonical key for the same case-insensitive captured make/model instead of selecting by JSON order.
- Lensfun inspection is derived from the selected RawTherapee bundle, searches and fingerprints every bundled XML file including third-party lens files, requires overlapping camera and lens mounts, and keeps the aggregate fingerprint unchanged during development.
- Lensfun matching includes ExifTool's lens maker when present. Camera make/model must resolve to exactly one database entry before its mounts can be used. Duplicate normalized camera entries, missing-maker cross-manufacturer lens ambiguity, or multiple fully matching lens entries are reported as ambiguous rather than combined or selected by order.
- Lensfun camera/lens database matches and actual-application confirmation are separate fields; absent CLI confirmation must remain false.
- RAW doctor readiness requires present camera-profile directories, a regular non-symlink output profile that LittleCMS parses as RGB display-class ICC, a parseable alias map, structurally valid RawTherapee JSON-with-comments camera constants, and a complete parseable Lensfun XML set.
- Doctor evaluates the fixed ExifTool and ImageMagick paths used by default preparation; unrelated PATH-only installations cannot make readiness pass.
- Doctor's ImageMagick color probe uses and records the exact allowlisted environment used by preparation; caller configuration/coder paths cannot affect readiness.
- RAW doctor returns structured not-ready output rather than raising when RawTherapee application metadata is malformed or unreadable, and rejects a symlink in the RawTherapee executable or any path ancestor so version and resource evidence cannot come from different bundles.
- The expected `RTv4_Large` profile is derived from the selected RawTherapee bundle, fingerprinted across development, and required to match the developed TIFF's embedded ICC bytes.
- RAW EXIF orientation 1 is accepted; orientations 2–8 fail before job creation until their complete pixel transforms, including rotation direction and mirroring, can be verified. The working dimensions must retain the source axis ordering, must not exceed the inspected dimensions, and must retain at least 95% of both inspected axes before exact developed dimensions replace the metadata expectation; normal RAW border cropping within that bound remains allowed.
- Every normalized working image must decode as a three-channel, 16-bit RGB TIFF with the expected embedded ACEScg profile before preview or analysis begins. Its accepted SHA-256 is calculated from the same stable open file descriptor used for structural validation.
- The validated working TIFF hash remains unchanged across preview generation and through manifest publication. The preview must decode as three-channel JPEG, embed the expected sRGB profile, and remain hash-stable through pixel loading and manifest publication.
- A final RAW source check precedes the prepared manifest. Both publication-boundary checks open the path without following symlinks and without blocking on special files, require one identity-stable regular file, and compare its digest. Any failed post-publication check, including a resource-inspection exception, removes the manifest and fails the job.
- RAW development copies the validated source through the same non-blocking, no-symlink, stable-regular-file contract into a transient read-only execution snapshot, invokes RawTherapee only on that snapshot, verifies both snapshot and original hashes after exit, and removes the snapshot. A missing, replaced, or special-file source produces a structured run report, removes developed output, and fails the job; temporary replacement of the original path cannot alter developed pixels.
- JSON publication creates its temporary file exclusively without following symlinks. A pre-created `run.json.tmp` link cannot overwrite the source and causes RAW development to fail safely without admitting output.
- The ACEScg and sRGB hashes validated from produced artifacts remain identical in all manifest profile entries; either system profile changing before or during publication blocks or removes the manifest.
- Cross-platform decoded 8-bit output allows maximum channel difference 1 and RMSE 0.25; file-byte equality is not promised across encoders.
- Manifest records source, schema, plan, recipe, tool, profile, intermediate, candidate, selection, master, and derivative provenance.

## Visual review rubric

Score 1–5 for exposure, white balance or post-development color adaptation, highlight handling, shadow handling, skin appearance when relevant, crop quality, style suitability, naturalness, artifacts, and publish readiness. Also record whether the unchanged original is preferable. A synthetic fixture receives no photographic-quality score.

## Private evaluation set

The eventual set should contain approximately 20–30 user-owned photos spanning portraits, travel/lifestyle, landscapes, backlit scenes, high dynamic range, low light, imperfect JPEGs, and permitted RAW/JPEG pairs. Store hashes and private locations in an untracked manifest. Never commit an evaluation image without explicit permission and provenance.

## Regression policy

Pinned-environment changes require exact decoded-pixel comparisons for programmatic fixtures. Intentional operation or tool upgrades require a decision entry, new baselines, technical diff metrics, and visual review on the private set. A test update alone cannot justify a changed image.
