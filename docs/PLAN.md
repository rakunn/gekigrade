# Delivery plan

## Milestone 0 — environment and feasibility

Status: implemented and locally verified. Deliver durable documents, locked Python environment, `geki doctor`, tool/profile fingerprints, ICC round-trip probes, explicit dependency decisions, and an honest manual ARW procedure. Acceptance requires actionable diagnostics and repeatable synthetic color conversion.

## Milestone 1 — deterministic JPEG vertical slice

Status: implemented and locally verified on the programmatic JPEG fixture. Deliver safe JPEG ingest, working TIFF, preview, analysis, normalized crops, strict three-candidate plans, restrained looks, candidate rendering, QA, selection, and full/social sRGB export. Acceptance is the end-to-end CLI workflow with unchanged source bytes and passing automated checks.

## Milestone 2 — RAW adapter

Status: adapter implemented; compatibility verified on 15 private normal-orientation Sony ILCE-7RM5 ARWs from one camera/lens combination, with independent development repeatability checked on the original pilot. Ten other sampled files were rejected for unsupported orientations 6 or 8. RawTherapee 5.13 runs behind the existing working-image boundary with isolated settings/cache directories, a committed PP3, 16-bit profiled TIFF output, captured diagnostics, and honest lens-capability reporting. Paired in-camera JPEG and trusted manual development comparisons remain pending, so general photographic quality is not yet accepted.

## Milestone 3 — geometry and crop evaluation

Status: deterministic anchor alternatives implemented, with provisional model contact-sheet review on 15 private photos. For each social aspect, preparation emits left/center/right candidates when width is trimmed or top/center/bottom candidates when height is trimmed; exact pixel bounds and normalized coordinates share the existing preview/full evaluator. The real-photo review found scene-dependent anchor preferences and cases with no satisfactory story crop. Human composition scoring and broader coverage remain pending. Horizon assistance and perspective correction remain deferred until they can be implemented and evaluated reliably. Acceptance requires preview/full coordinate equivalence and visual review on real images; the current evidence and its limits are in [the evaluation report](REAL_PHOTO_EVALUATION.md).

## Milestone 4 — curated-look evaluation

Status: all three unchanged defaults compared on a controlled pilot and 14 additional photos. Provisional model judgments found restrained differences without a decisive general winner; no defaults were changed. Owner preference, trusted-reference comparisons, and broader artifact assessment remain pending. Tune the three existing looks using that evidence and add no look whose process space or limitations are unclear. Acceptance requires preference and artifact scores, not synthetic tests alone.

### Approved intervening slice — global tone and stage QA

Status: implemented and merged with explicit edit-plan `2.0.0`, observational QA report `2.0.0`, bounded global shadow recovery, a luminance highlight shoulder, and fixed output-gamut compression. Version-1 rendering remains covered by pre-change pixel goldens. Conservative alternatives were compared before selecting operators; one private backlit Sony RAW has a fresh-development/full-resolution A/B evaluation recorded in `TONE_EXPERIMENT.md`. The crop-anchor dependency and this slice were squash-merged in PRs #2 and #3. The [subsequent photo evaluation](REAL_PHOTO_EVALUATION.md) adds controlled shadow comparisons and 14 further fixed-look cases. Curated look definitions remain unchanged. More independent scene coverage, trusted manual/paired-camera comparisons, human preference scores, and native-pixel noise/artifact evaluation remain pending. No semantic masks, RAW-specific controls, APIs, LUTs, or UI are included.

## Milestone 5 — Codex-generated plans

Status: not yet evaluated as a scene-specific planning benchmark. The fixed example plans in the photo survey isolate look behavior and do not establish planner or ranking quality. Evaluate the repository prompts, schema adherence, parameter conservatism, and candidate-ranking usefulness. Codex remains the interactive planner; no API integration is introduced.

## Milestone 6 — real-photo evaluation

Status: initial convenience survey complete: 25 private ARWs screened, 15 supported photos rendered, and 52 comparison JPEGs technically checked. All source hashes remained unchanged. Provisional model reviews are recorded separately from missing human scores. One outing, correlated frames, one camera/lens, and ten orientation rejections do not satisfy the diverse-set gate. Complete that gate with approximately 20–30 user-owned portraits, travel/lifestyle images, landscapes, backlit/HDR/low-light scenes, imperfect JPEGs, and permitted RAW/JPEG pairs. Record technical and human scores without committing private photos.

## Milestone 7 — local masks

Only after global processing succeeds, define a new recipe version and coordinate contract for deterministic local masks. Semantic models, brush masks, and depth remain excluded until separately designed and approved.
