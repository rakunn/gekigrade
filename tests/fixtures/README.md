# Test fixture provenance

`sample.jpg` is generated from `sample-source.svg`, an original CC0-style programmatic test chart created for GekiGrade. It contains gradients, neutral and saturated patches, clipped endpoints, and sharp edges for automated signals. It is not a photograph and does not demonstrate photographic quality.

Regenerate it on macOS with:

```sh
/opt/homebrew/bin/magick sample-source.svg -resize 640x480\! \
  -strip -profile "/System/Library/ColorSync/Profiles/sRGB Profile.icc" \
  -sampling-factor 4:4:4 -quality 95 sample.jpg
```
