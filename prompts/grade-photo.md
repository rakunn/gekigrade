# Grade one prepared GekiGrade job

Inspect `source.json`, `analysis.json`, `preview.jpg`, `crops/contact-sheet.jpg`, `crops/candidates.json`, `looks/available.json`, and `schema/edit-plan.schema.json` inside the job.

Create exactly three restrained candidates: conservative correction, recommended look, and alternative look. Use only fields and values permitted by the schema. Reference existing crop and look IDs exactly. For a JPEG, treat `temperature_mired_shift` as post-development chromatic adaptation, not RAW white balance. Do not emit prose, commands, paths, masks, or unknown operations in the plan. Save only schema-valid JSON to the job's `plans` directory, then run `geki validate-plan` before rendering.
