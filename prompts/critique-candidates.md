# Critique GekiGrade candidates

Inspect the candidate contact sheet, individual candidate JPEGs, candidate metadata, and QA report. Compare exposure, color balance, highlight and shadow handling, crop, naturalness, artifacts, look suitability, and whether the unchanged source preview is preferable.

Do not infer technical measurements that are already present in QA. Do not select a candidate that has fatal QA. If all candidates are worse than the source, say so and revise the plan within the existing schema. Otherwise run `geki select JOB --candidate ID` with an exact candidate ID.

Use QA report version 2 to locate changes: `after_global_correction` and `after_creative_look` cover the full rotated frame; `before_output_gamut` and `before_output_clamp` cover the cropped/resized output. Gamut compression may remove excursions without restoring detail, and infeasible luminance still clips. Compare `post_quantization_and_sharpen` with `post_encode` for final-output effects. Inspect channel extrema alongside strict gamut counts because tiny OCIO rounding excursions also count. Keep intentional silhouettes dark when visually appropriate; zero clipping is not the objective. A candidate can be structurally valid and photographically worse. Do not describe a single private comparison as general quality acceptance.
