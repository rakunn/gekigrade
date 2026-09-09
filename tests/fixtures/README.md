# Test fixture provenance

`sample.jpg` is generated from `sample-source.svg`, an original CC0-style programmatic test chart created for GekiGrade. It contains gradients, neutral and saturated patches, clipped endpoints, and sharp edges for automated signals. It is not a photograph and does not demonstrate photographic quality.

Regenerate it on macOS with:

```sh
/opt/homebrew/bin/magick sample-source.svg -resize 640x480\! \
  -strip -profile "/System/Library/ColorSync/Profiles/sRGB Profile.icc" \
  -sampling-factor 4:4:4 -quality 95 sample.jpg
```
# Legacy tone pixel baseline

`legacy-v1.json` contains recipes and hashes only, captured from revision `d760964` before the version-2 renderer changes. `tests/unit/test_legacy_pixels.py` recreates its 31×47 synthetic float32 ramp and saturated/neutral patches in code, including negative and above-white samples. It checks three version-1 looks and nonzero geometry, correction, vignette, resize, and sharpening settings at linear, quantized, and decoded-JPEG boundaries. Exact hashes are scoped to the locked Apple Silicon environment and system profiles; no photograph or derived private image is part of this fixture.
