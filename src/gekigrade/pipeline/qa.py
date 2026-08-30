from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from gekigrade.analysis.metrics import analyze_srgb
from gekigrade.domain.jsonio import read_json, write_json
from gekigrade.domain.paths import job_child
from gekigrade.pipeline.manifests import assert_source_unchanged, refresh_manifest


def run_qa(job_path: Path) -> Path:
    job = job_path.resolve(strict=True)
    manifest = assert_source_unchanged(job)
    metadata: dict[str, Any] = read_json(job_child(job, "candidates/metadata.json"))
    report_path = job_child(job, "qa/report.json")
    report: dict[str, Any] = read_json(report_path)
    failures: list[str] = []
    warnings: list[str] = list(report.get("warnings", []))
    verification: dict[str, Any] = {}
    for candidate_id, candidate in metadata["candidates"].items():
        path = job_child(job, candidate["output"])
        if not path.is_file():
            failures.append(f"missing candidate: {candidate_id}")
            continue
        with Image.open(path) as opened:
            profile_present = bool(opened.info.get("icc_profile"))
            pixels = np.asarray(opened.convert("RGB"), dtype=np.float32) / np.float32(255.0)
            dimensions = {"width": opened.width, "height": opened.height}
        if not profile_present:
            failures.append(f"missing ICC profile: {candidate_id}")
        metrics = analyze_srgb(pixels)
        clipping = metrics["clipping"]
        if clipping["shadow_all_percent"] > 1.0:
            warnings.append(f"{candidate_id}: all-channel shadow clipping exceeds 1.0%")
        if clipping["highlight_all_percent"] > 1.0:
            warnings.append(f"{candidate_id}: all-channel highlight clipping exceeds 1.0%")
        verification[candidate_id] = {
            "icc_profile_embedded": profile_present,
            "dimensions": dimensions,
            "clipping": clipping,
            "finite": True,
        }
    report["verification"] = verification
    report["failures"] = failures
    report["warnings"] = sorted(set(warnings))
    report["passed"] = not failures
    write_json(report_path, report)
    refresh_manifest(
        job,
        state="assessed" if not failures else "qa-failed",
        plan_sha256=manifest.get("plan_sha256"),
    )
    if failures:
        raise RuntimeError("QA failed: " + "; ".join(failures))
    return report_path
