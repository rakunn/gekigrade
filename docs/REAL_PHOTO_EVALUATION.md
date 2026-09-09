# Real-photo evaluation — 2026-09-09

## Scope and method

This run evaluates a convenience sample of 25 private ARWs from one outing, including the previously used backlit pilot. The 24 additional files are evenly spaced through the folder's sorted filenames, excluding macOS sidecars. They are distinct source files, not necessarily independent scenes: nearby frames can depict the same subject. The compatible subset contains one Sony camera/lens combination, backlit daylight and illuminated sunset scenes, and ISO values from 100 to 2500. No paired camera JPEGs, trusted manual edits, or owner preference scores were supplied.

The renderer code is unchanged from `aebec71fe67c78b2ce48d87498f8f48ddaa6e15d`. Exact source paths, hashes, validated plans, tool/profile fingerprints, image outputs, and detailed review records remain in ignored private storage. The public report contains no photographs or private source paths. All visual judgments below are provisional Codex assessments, not human acceptance scores.

## Compatibility screening

All 25 source hashes remained unchanged across inspection and at the final post-render audit. Fifteen files passed the current RAW admission checks. Ten were rejected before preparation: one has EXIF orientation 6 and nine have orientation 8. These are documented unsupported cases, not successful renders or photographic-quality failures. The run does not bypass the orientation policy.

Fourteen additional supported images completed a controlled full-frame look comparison: the generated version-2 example's neutral global settings, zero shadow recovery, highlight compression 0.5, sharpening 0.25, original crop, and the three unchanged default looks. Production rendering graded the full working image before resizing to a 1200-pixel maximum edge and JPEG quality 92. Each prepared preview, complete crop sheet, and all three individual final JPEGs were visually reviewed. This standard comparison evaluates look behavior, not scene-specific plan optimization. The retained pilot used the separate controls below.

## Completed technical results

| Comparison | Source photos | Final comparison JPEGs | Output dimensions |
|---|---:|---:|---|
| Controlled pilot | 1 | 10 | 1080×1350 |
| Additional fixed-look survey | 14 | 42 | 1200×799 |

All 52 comparison JPEGs passed independent checks of RGB mode, expected dimensions, exact embedded sRGB ICC bytes, file hashes, and actual decoded-pixel hashes. All survey QA reports passed without fatal failures. Every accepted working TIFF retained its hash, and the pilot's pre-existing preparation artifacts remained unchanged. The final audit also verified that every survey recipe used the intended common controls and unchanged look versions and strengths. Contact sheets and prepared previews are additional review artifacts, not included in the 52 comparison-output count.

Across the 42 survey JPEGs, decoded all-channel shadow-threshold percentages ranged from 0.00010% to 1.04693%, and all-channel highlight-threshold percentages from 0% to 0.02795%. These measurements describe endpoint thresholds in the resized JPEGs, not lost or recovered detail. An any-channel threshold count can reflect a saturated color with one channel near an endpoint and must not be described as the fraction of fully black or white pixels. The pilot uses a different crop and controls, so its percentages are reported separately below.

Repository verification also passed: frozen dependency sync, Ruff formatting and lint, mypy, all 209 tests, and the explicit integration selection of 100 tests (a subset of the 209). The candidate-selection command was checked against CLI help. Renderer code, schemas, dependencies, and look definitions were unchanged by this evaluation.

## Controlled backlit pilot

The pilot reuses the previously accepted 9556×6366 working TIFF and compares outputs at 1080×1350, JPEG quality 92. Three validated plans isolate look choice, shadow strength, and version-2 baseline operators; a zero-control version-1 neutral render supplies a derived reference. A neutral render is not a paired camera JPEG, manual edit, or unchanged source photograph.

The look comparison uses the center 4:5 crop, exposure +0.15 EV, shadow recovery 0.75 EV, highlight compression 0.8, sharpening 0.25, and zero adaptation, contrast, black lift, saturation, and vignette adjustments. Look strengths remain their defaults: natural 0.5, warm 0.6, and muted 0.55. The shadow comparison holds those settings and the natural look constant while changing shadow recovery alone.

All ten pilot renders completed with the expected RGB dimensions and exact sRGB ICC bytes. The natural-look and moderate-shadow cases independently render the same mathematical recipe: their integer hashes, JPEG file hashes, decoded-pixel hashes, and complete QA measurements are identical. The source RAW, working TIFF, prepared manifest, source metadata, and crop artifact retain their before/after hashes. These are observed technical passes; they do not establish photographic preference.

| Shadow recovery | Decoded all-channel shadow threshold % | Decoded all-channel highlight threshold % |
|---|---:|---:|
| 0 EV | 0.33395 | 0.00885 |
| 0.75 EV | 0.08896 | 0.00960 |
| 1.5 EV | 0.02106 | 0.00727 |

These are endpoint-threshold measurements after JPEG decoding, not amounts of reconstructed detail or a preference score. At final output size, 0.75 EV opens shaded detail while retaining the dark foreground figure. The 1.5 EV treatment opens more foliage and flattens the dark frame; it remains visually plausible but is not automatically preferable because its clipping count is lower. The provisional balanced choice for this scene is 0.75 EV; no global default is changed.

All three curated look treatments are restrained and close in quality. The warm variant adds a small warm shift; the muted variant is slightly cooler and more subdued. No decisive winner is established without owner preference. No obvious new broad halo or color discontinuity was visible at the inspected output size; sensor-level noise and calibrated color accuracy are not certified. Skin appearance is unscored because the visible person is small and backlit.

The center crop anchors retain the foreground person and main background subject together in this pilot. The outer 9:16 anchors exclude them, and the left 4:5 crop cuts the main background subject at its edge. This supports reviewing the contact sheet for every image; it does not establish that center anchors are generally best.

## Visual findings beyond the pilot

A sunset skyline and a wider daylight skyline provide counterexamples to a center default: their center 9:16 anchors cut through the principal tower, while the left anchors retain it. Left feed and square anchors also retain more of the intended composition. The left story crops still crowd the tower toward an edge, so the three geometric choices do not always contain an ideal crop. This is a coverage limitation of the candidate set, not a coordinate-equivalence failure. The planner must inspect the actual choices and may prefer the original aspect when no social crop is satisfactory.

Centered anchors work better for the environmental portraits that place a person on a rock beneath the tower. The outer story crops omit the person. Some frames already contain a partial foreground figure at the bottom edge; the current full-height, width-trimming social candidates cannot remove that intrusion. Another source frame already cuts off the tower's top. A crop can choose among captured content; it cannot restore content outside the source frame.

The fixed zero-exposure, zero-shadow-recovery comparison leaves several backlit subjects and foregrounds dark. All three look defaults remain close; a look change does not substitute for choosing global tone controls for the scene. A dark rear-facing figure can also be an intentional silhouette, so brightness is not a universal objective. Backlighting, small faces, or rear-facing subjects prevent reliable skin assessment in these views.

A fern-framed daylight image retains a large, featureless bright sky area even when the decoded all-channel highlight count is low. Moving a flat highlight away from the endpoint does not demonstrate recovered detail. Flare and fine-detail softness visible in prepared previews must also be distinguished from newly introduced rendering artifacts.

## Acceptance limits

The diverse 20–30-photo acceptance set remains open: this is one camera/lens, one outing, with correlated frames and ten unsupported orientations. Paired camera-JPEG fidelity, trusted manual-development quality, human preference, reliable skin assessment, and broader camera/lens behavior remain unverified. More images from the same burst cannot fill those gaps. Global defaults and deferred local-adjustment work should not advance solely on this run.

## Remaining evaluation work

- Obtain owner preference scores and permitted camera-JPEG/manual-development references before tuning the curated looks or claiming RAW fidelity.
- Add independent scenes, clearly assessable portraits, low-light subjects, and imperfect JPEGs to the acceptance set.
- Evaluate scene-specific plans and candidate rankings. These fixed example recipes do not establish planner quality, and crop selection should permit keeping the original aspect when the available social crops fail the intended composition.
- Treat orientation support as a separate compatibility change requiring verified rotation and mirroring semantics. Ten rejected inputs show its practical impact; this run does not implement or validate those transforms.
- Keep local masks, semantic processing, APIs, and UI deferred under the existing global-processing acceptance gates.
