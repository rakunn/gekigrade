# Global tone operator experiment — 2026-09-09

## Scope and evidence

This slice is based on PR #2 head `d7609649cc1696ba24866e54f801d026b361b0ec`. Before changing the renderer, capture version-1 linear, quantized, and decoded-JPEG fixture hashes in `tests/fixtures/legacy-v1.json`. Compare conservative alternatives with synthetic signals and the authorized private backlit RAW. Do not commit the source, any derived image, private paths, or a scene-identifying manifest. Local scripts, full reports, source hashes, and visual comparisons remain under ignored `work/` storage.

The first photographic comparison resized the same accepted linear working image to a 1600-pixel long edge **before** experimental grading for turnaround. Those numbers are comparative experiments, not production-output baselines: resize and nonlinear grading do not commute. The final A/B below instead starts with a fresh 9556×6366 development, processes all 60,833,496 pixels, and resizes only after grading/crop through the production evaluator.

The comparison keeps the centered 4:5 crop, +0.15 EV exposure, +3 mired post-development adaptation, contrast 0.03, black lift 0.005, saturation 0.04, vignette 0.02, sharpening 0.25, and `warm-editorial` look 1.0.0 at strength 0.55. Legacy global rolloff is 0.22. The tested new controls are 0.75 EV shadow recovery and 0.8 highlight compression; these are not equivalent parameter units or universal defaults. Preparation's new example uses zero shadow recovery and 0.5 highlight compression. Look files are unchanged.

## Alternatives compared before selection

### Shadows

Compare existing ACEScct black lift 0.015, a black-preserving cubic gain `Y*[1+(2^EV-1)*max(1-Y/0.18,0)^2]`, and the selected squared EV fade `Y*2^(EV*max(1-Y/0.18,0)^2)`. The latter two use common RGB scaling.

| Curve | Black output | Y=0.01 output at 0.75 EV | Y=0.05 output at 0.75 EV | Minimum sampled slope at 2 EV |
|---|---:|---:|---:|---:|
| Existing ACEScct lift 0.015 (different units) | 0.00142312 | 0.01189373 | 0.05399433 | Not an EV control |
| Cubic gain | 0 | 0.01608142 | 0.06778132 | approximately 0 |
| Selected EV fade | 0 | 0.01589950 | 0.06557436 | 0.414058 |

Both new curves meet identity at Y=0.18. The cubic gain reaches a flat derivative at the maximum setting, risking local tonal flattening. The selected fade has a positive analytic derivative throughout 0–2 EV and leaves exact black alone. The old black lift remains available as a distinct creative/technical control; it is not relabeled as recovery.

### Highlights and gamut

Compare the selected rational shoulder `Y'=0.5+d/(1+2*c*d)` with an identity/exponential blend `Y'=0.5+(1-c)*d+c*0.5*(1-exp(-d/0.5))`, where `d=max(Y-0.5,0)` and the lower range is identity. Both preserve positive-luminance RGB ratios. At c=1 the exponential alternative approaches its ceiling faster and the float32 gradient develops repeated values earlier; at c<1 its identity component remains unbounded. The rational curve has a positive derivative and controlled asymptote for every c>0.

For output compare channel clamp, hard radial reduction to the gamut boundary, and the selected soft radial knee at 95% of that boundary. Hard radial output lands directly on endpoints and has a derivative discontinuity there. Soft radial compression preserves the interior and brings feasible chroma gradually inside the boundary. An additional independent per-channel soft curve was not selected for testing because it abandons the common-scale chromaticity constraint already demonstrated by the channel-clamp baseline.

Experimental 1600-edge working input, same output crop; strict out-of-range percentages are measured **before** final clamp:

| Tone / gamut | Low-gamut % | High-gamut % | Threshold all-shadow % | Threshold all-highlight % |
|---|---:|---:|---:|---:|
| Legacy / clamp | 9.79206 | 6.32345 | 2.90565 | 1.21555 |
| Rational 0.8 / clamp | 7.30454 | 0.20785 | 2.34961 | 0 |
| Exponential blend 0.8 / clamp | 7.30454 | 4.80305 | 2.34961 | 0.02749 |
| Rational 0.8 / hard radial | 0.68272 | 0.20785 | 2.63995 | 0 |
| Rational 0.8 / soft radial | 0 | 0 | 2.63995 | 0 |
| Rational 1.0 / soft radial | 0 | 0 | 2.63995 | 0 |

Strict counts at the hard boundary include numerical roundoff. Nominal linear white encodes to approximately 1.0000067 in the pinned OCIO processor; channel extrema are necessary context. The stronger rational setting was unnecessary for the selected comparison and is not adopted as a default.

On four synthetic saturated out-of-gamut linear-sRGB patches, channel clamp changed luminance by up to 0.07152 and RGB opponent direction by 10.6237 degrees. Soft radial compression changed luminance by at most 8.95e-9 and direction by 2.26e-6 degrees in the prototype. This is a linear-RGB mathematical check, not a perceptual hue-quality score. The final implementation has explicit tests for feasible neutrals, interior identity, continuous chroma response, infeasible luminance, finite values, and repeatability.

## Full-resolution A/B

Run a fresh preparation through the existing pinned RAW adapter. Its working TIFF SHA-256 equals the previous accepted development. Use that same array for both versions and the repeated v2 evaluation; source SHA-256 is verified before and after. Render the selected crop at 1080×1350 using JPEG quality 92, 4:4:4 sampling, existing sharpening, and the same sRGB ICC profile.

| Stage (strict any-channel gamut) | v1 low % | v1 high % | v2 low % | v2 high % |
|---|---:|---:|---:|---:|
| After global correction, full rotated frame | 7.14617 | 3.34039 | 7.14617 | 0.03169 |
| After creative look, full rotated frame | 19.38723 | 3.68868 | 14.74615 | 0.13042 |
| Before gamut, output crop | 9.26331 | 6.38484 | 6.09410 | 0.25898 |
| Before clamp, output crop | 9.26331 | 6.38484 | 0 | 0.00137 |

The global correction already has negative-color excursions. The unchanged look introduces additional ones. Crop/resize changes the denominator and can introduce interpolation excursions, so the two full-frame rows must not be subtracted from cropped rows as a causal percentage delta. The final v2 maximum is 1.0000066757 in each channel: its residual strict high-gamut count is numerical white overshoot, not a material above-white range.

| Final-output endpoint thresholds | v1 shadow-all % | v1 highlight-all % | v2 shadow-all % | v2 highlight-all % |
|---|---:|---:|---:|---:|
| Before output gamut | 2.47627 | 1.25597 | 1.39431 | 0.00034 |
| Before clamp | 2.47627 | 1.25597 | 1.65103 | 0.00130 |
| After quantization and sharpening | 3.09609 | 1.42558 | 1.89691 | 0.05837 |
| Actual decoded JPEG | 1.98018 | 0.97531 | 1.22353 | 0.01646 |

Gamut compression increases v2 all-channel shadow thresholds from 1.39431% to 1.65103% because colors with infeasible luminance become black. Quantization/sharpening introduces additional endpoint pixels; JPEG loss changes them again. None of these rows is interchangeable with the initial motivating candidate-preview figures (approximately 2.03% / 0.97%), which used a different size. This is why QA must retain stage and dimension context.

The new legacy render matches the prior accepted export's decoded pixels exactly. Two v2 runs have identical quantized pixels, JPEG bytes, decoded pixels, and all stage measurements under this fingerprint. Source bytes are unchanged. Private hashes and exact artifacts stay in the ignored local report.

## Visual findings and limits

Inspect the experimental contact sheet and both final sRGB exports. The selected treatment reveals somewhat more shaded foreground texture and holds more separation in the bright sky while the backlit figure remains dark. Overall color and composition remain coherent. The soft gamut variant has subtle differences in dark saturated colors; no obvious new hue discontinuity, halo, or broad contrast collapse was visible at the final export size. Existing edge ringing and fine dark-region noise remain possible; this slice neither changes resampling/sharpening nor adds noise reduction.

For this scene, the selected version is a plausible improvement in highlight handling and restrained shadow visibility. It is not evidence that silhouettes should be brightened or every clipping percentage should be zero. No user preference score, paired camera JPEG, trusted manual development, calibrated-display assessment, or broader scene set was available. Do not claim general photographic-quality acceptance, source-detail reconstruction, or universal perceptual hue preservation. Wider real-photo evaluation remains required before changing look defaults or enabling local adjustments.

## Fingerprint and reproduction

The run uses the locked Python 3.12.10 environment with NumPy 2.5.2, OpenImageIO 3.1.16.0, OpenColorIO 2.5.2, Pillow 12.3.0, RawTherapee 5.13, and `cg-config-v4.0.0_aces-v2.0_ocio-v2.5` on Apple Silicon macOS. Preparation captures complete external-tool/resource/configuration fingerprints in its private manifest and run reports. ACEScg ICC hash is `7a06987f2d7e458e98fa744c4acad9f3610f3c895c04a98179970d380d8a46e8`; sRGB ICC hash is `2b3aa1645779a9e634744faf9b01e9102b0c9b88fd6deced7934df86b949af7e`.

To reproduce with an authorized private image: prepare a new ignored job, validate separate three-candidate plans with explicit versions and the settings above, render/QA/select the same crop/look candidate, and export the same target. Keep the other two candidates constant within each plan. Compare both stage reports and actual decoded JPEG arrays; repeat v2 and recheck source/intermediate hashes. The committed automated fixtures reproduce the mathematical and legacy pixel assertions without private imagery. These constants and tests pin semantics; tool/configuration upgrades require a new fingerprint and renewed evaluation.
