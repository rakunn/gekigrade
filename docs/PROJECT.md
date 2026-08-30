# GekiGrade project definition

## User problem

Careful photo correction requires both visual judgment and exact, repeatable processing. General-purpose LLMs can discuss an image but should not directly manipulate pixels, invent commands, or silently make color-management decisions. GekiGrade separates the judgment boundary from deterministic rendering.

## Target user and promise

The initial user is a technically comfortable photographer running one local photo at a time on macOS. GekiGrade promises inspectable artifacts, bounded LLM choices, immutable source files, reproducible recipes, and color-managed social exports without generative manipulation or external photo uploads.

## Initial workflow

1. Prepare a JPEG into a safe job directory.
2. Inspect metadata, a normalized preview, histograms, clipping, sharpness, crop candidates, looks, and the plan schema.
3. Have Codex or the user produce a three-candidate plan.
4. Validate the plan before rendering.
5. Render and compare previews, run technical QA, and explicitly select a candidate.
6. Rerender the selected recipe from the full-resolution intermediate and export sRGB variants.

## Functional requirements

- One JPEG per job; Sony ARW follows in a separate adapter milestone.
- Source checksum, dimensions, orientation, camera/lens/exposure metadata, and embedded profile detection.
- Explicit ICC conversion to a named high-bit-depth working space.
- Deterministic histograms, clipping, luminance, saturation, sharpness, aspect, and crop measurements.
- Normalized original, 4:5, 9:16, and 1:1 crop candidates with a numbered contact sheet.
- Strict, versioned plans using bounded global operations and versioned looks.
- Three candidate previews, comparison sheet, per-candidate provenance, and QA.
- Explicit selection followed by full-resolution and social JPEG export.

## Non-functional requirements

- Source immutability, safe paths, atomic derived writes, subprocess timeouts, bounded resources, and actionable failures.
- No arbitrary commands or paths from a recipe.
- Explicit profiles and operation spaces; 16-bit or float processing before final JPEG encoding.
- Canonical recipe hashes, tool/profile fingerprints, and decoded-pixel hashes.
- Test-first implementation with synthetic signals separated from visual-quality evaluation.

## MVP exclusions

Semantic or brush masks, depth, object/face/skin analysis, generative editing, batch/catalog features, cloud processing, publishing, user accounts, subscriptions, model training, API-based orchestration, and desktop/web UI are excluded.

## Success criteria

The documented CLI workflow completes on the licensed project fixture; the source hash is unchanged; invalid plans fail before rendering; repeated results match under an identical fingerprint; output dimensions and sRGB profile are verified; and all format, lint, type, unit, and integration checks pass.

## Assumptions and unresolved validation

- macOS supplies usable ACEScg and sRGB profiles at known ColorSync paths.
- A future user-owned ARW is required to evaluate camera development and lens behavior.
- Real-photo evaluation is required before making quality claims or tuning look defaults aggressively.
- Public distribution and repository licensing require a separate decision and dependency-notice review.
