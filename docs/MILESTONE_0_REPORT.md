# Milestone 0 feasibility report

Verified on 2026-08-30 on Apple Silicon macOS (Darwin 25.6.0):

- Python 3.12.10, uv 0.12.7;
- ExifTool 13.55;
- ImageMagick 7.1.1-47 Q16-HDRI with LittleCMS;
- OpenImageIO 3.1.16.0 Python wheel;
- OpenColorIO 2.5.2 Python wheel and built-in ACES 2.0 CG config;
- RawTherapee 5.13 CLI present, without an ARW quality claim;
- ACEScg profile SHA-256 `7a06987f2d7e458e98fa744c4acad9f3610f3c895c04a98179970d380d8a46e8`;
- sRGB profile SHA-256 `2b3aa1645779a9e634744faf9b01e9102b0c9b88fd6deced7934df86b949af7e`.

`geki doctor` creates a temporary synthetic patch image, converts it twice from embedded sRGB JPEG to profile-tagged 16-bit ACEScg TIFF, and verifies identical TIFF file hashes. It also verifies the TIFF profile and an ACEScg → ACEScct → ACEScg OCIO round trip. The recorded RMSE for the final run was `3.4589840356951634e-06`. This proves the pinned components interoperate on this Mac; it is not a visual-quality evaluation.

At the Milestone 0 checkpoint, the JPEG dependency path was ready and RAW remained `installed-unverified-with-arw`. Milestone 2 subsequently validated one private Sony ARW compatibility path; see [`RAW_MANUAL_TEST.md`](RAW_MANUAL_TEST.md). General camera color quality, confirmed automatic lens application, and cross-machine pixel identity remain unproven.
