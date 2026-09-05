# Evaluation protocol

## Technical checks

- Source SHA-256 is unchanged after success and every reproduced failure.
- A structurally and semantically valid recipe is required before rendering.
- Unknown operations, fields, looks, versions, crops, and out-of-range values are rejected.
- Orientation and normalized crop transforms produce expected dimensions at preview and full resolution.
- Working and output profiles are present, described, hashed, and independently verified.
- NaN/Inf, malformed images, unprofiled CMYK, unsafe paths, missing tools, timeouts, and non-zero subprocess exits are surfaced.
- Repeated renders under the same fingerprint have identical decoded-pixel hashes.
- Repeated RAW developments use fresh isolated settings/cache directories and require zero decoded-pixel differences under the same fingerprint; container hashes may differ when the external engine writes timestamps.
- RAW output must be exactly three-channel RGB with 16-bit samples, profile-tagged, correctly oriented, and admitted to ACEScg only after the intermediate profile is verified.
- RAW metadata inspection hashes the source before ExifTool and rejects any change detected immediately afterward.
- RawTherapee camera-input provenance records the selected DCP or ICC profile, or the camera-matrix fallback, together with camera-alias and camera-constants hashes; the resource fingerprint must remain unchanged during development.
- RawTherapee executable version and file hash are captured before launch and must remain unchanged after exit.
- Lensfun inspection is derived from the selected RawTherapee bundle, searches and fingerprints every bundled XML file including third-party lens files, requires overlapping camera and lens mounts, and keeps the aggregate fingerprint unchanged during development.
- Lensfun camera/lens database matches and actual-application confirmation are separate fields; absent CLI confirmation must remain false.
- RAW doctor readiness requires present camera-profile directories, a parseable alias map, camera constants, and a complete parseable Lensfun XML set.
- Cross-platform decoded 8-bit output allows maximum channel difference 1 and RMSE 0.25; file-byte equality is not promised across encoders.
- Manifest records source, schema, plan, recipe, tool, profile, intermediate, candidate, selection, master, and derivative provenance.

## Visual review rubric

Score 1–5 for exposure, white balance or post-development color adaptation, highlight handling, shadow handling, skin appearance when relevant, crop quality, style suitability, naturalness, artifacts, and publish readiness. Also record whether the unchanged original is preferable. A synthetic fixture receives no photographic-quality score.

## Private evaluation set

The eventual set should contain approximately 20–30 user-owned photos spanning portraits, travel/lifestyle, landscapes, backlit scenes, high dynamic range, low light, imperfect JPEGs, and permitted RAW/JPEG pairs. Store hashes and private locations in an untracked manifest. Never commit an evaluation image without explicit permission and provenance.

## Regression policy

Pinned-environment changes require exact decoded-pixel comparisons for programmatic fixtures. Intentional operation or tool upgrades require a decision entry, new baselines, technical diff metrics, and visual review on the private set. A test update alone cannot justify a changed image.
