from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

from gekigrade.domain.models import CandidateRecipe
from gekigrade.geometry.crops import generate_crop_candidates
from gekigrade.grading.engine import apply_recipe
from gekigrade.grading.looks import get_look
from gekigrade.pipeline.render import evaluate_candidate, save_srgb_jpeg


def test_version_one_matches_pixels_captured_before_renderer_changes(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "fixtures/legacy-v1.json"
    records = json.loads(fixture.read_text())["records"]
    pixels = np.linspace(-0.02, 1.6, 31 * 47 * 3, dtype=np.float32).reshape(31, 47, 3)
    pixels[0, :6] = np.array(
        [[0, 0, 0], [1, 1, 1], [1, 0, 0], [0, 1, 0], [0, 0, 1], [0.18, 0.18, 0.18]],
        dtype=np.float32,
    )
    for record in records:
        recipe = CandidateRecipe.model_validate(record["recipe"])
        look = get_look(recipe.look.id, recipe.look.version)
        linear = apply_recipe(pixels, recipe, look)
        assert hashlib.sha256(linear.tobytes()).hexdigest() == record["linear_sha256"]
        crop = next(c for c in generate_crop_candidates(47, 31) if c["id"] == recipe.crop_id)
        integer, qa = evaluate_candidate(pixels, recipe, crop, target_dimensions=(24, 30))
        assert hashlib.sha256(integer.tobytes()).hexdigest() == record["integer_sha256"]
        assert qa["preclamp_low_percent"] == record["preclamp_low_percent"]
        assert qa["preclamp_high_percent"] == record["preclamp_high_percent"]
        path = tmp_path / f"{recipe.id}.jpg"
        save_srgb_jpeg(integer, path, quality=92)
        with Image.open(path) as im:
            decoded = np.asarray(im.convert("RGB"))
        assert hashlib.sha256(decoded.tobytes()).hexdigest() == record["jpeg_decoded_sha256"]
