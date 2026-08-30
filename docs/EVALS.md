# Evaluation protocol

## Technical checks

- Source SHA-256 is unchanged after success and every reproduced failure.
- A structurally and semantically valid recipe is required before rendering.
- Unknown operations, fields, looks, versions, crops, and out-of-range values are rejected.
- Orientation and normalized crop transforms produce expected dimensions at preview and full resolution.
- Working and output profiles are present, described, hashed, and independently verified.
- NaN/Inf, malformed images, unprofiled CMYK, unsafe paths, missing tools, timeouts, and non-zero subprocess exits are surfaced.
- Repeated renders under the same fingerprint have identical decoded-pixel hashes.
- Cross-platform decoded 8-bit output allows maximum channel difference 1 and RMSE 0.25; file-byte equality is not promised across encoders.
- Manifest records source, schema, plan, recipe, tool, profile, intermediate, candidate, selection, master, and derivative provenance.

## Visual review rubric

Score 1–5 for exposure, white balance or post-development color adaptation, highlight handling, shadow handling, skin appearance when relevant, crop quality, style suitability, naturalness, artifacts, and publish readiness. Also record whether the unchanged original is preferable. A synthetic fixture receives no photographic-quality score.

## Private evaluation set

The eventual set should contain approximately 20–30 user-owned photos spanning portraits, travel/lifestyle, landscapes, backlit scenes, high dynamic range, low light, imperfect JPEGs, and permitted RAW/JPEG pairs. Store hashes and private locations in an untracked manifest. Never commit an evaluation image without explicit permission and provenance.

## Regression policy

Pinned-environment changes require exact decoded-pixel comparisons for programmatic fixtures. Intentional operation or tool upgrades require a decision entry, new baselines, technical diff metrics, and visual review on the private set. A test update alone cannot justify a changed image.
