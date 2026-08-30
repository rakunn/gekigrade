from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field

from gekigrade.domain.models import Identifier, SemanticVersion, StrictModel


class LookError(ValueError):
    """Raised for an unknown, mismatched, or invalid look."""


class LookOperations(StrictModel):
    temperature_mired_shift: float = Field(ge=-10.0, le=10.0)
    contrast: float = Field(ge=-0.1, le=0.1)
    black_lift: float = Field(ge=0.0, le=0.02)
    highlight_rolloff: float = Field(ge=0.0, le=0.1)
    saturation: float = Field(ge=-0.15, le=0.1)
    shadow_hue_shift: float = Field(ge=-0.05, le=0.05)


class LookDefinition(StrictModel):
    id: Identifier
    version: SemanticVersion
    description: str
    expected_input_color_space: Annotated[str, Field(pattern=r"^ACEScct$")]
    continuation_color_space: Annotated[str, Field(pattern=r"^ACEScct$")]
    default_strength: float = Field(ge=0.0, le=1.0)
    strength_range: tuple[float, float]
    operations: LookOperations
    applicable_scene_guidance: str
    known_limitations: str


def _look_root() -> Path:
    repository_looks = Path(__file__).resolve().parents[3] / "looks"
    if repository_looks.is_dir():
        return repository_looks
    return Path(__file__).resolve().parents[1] / "looks"


def list_looks() -> list[LookDefinition]:
    definitions = []
    for path in sorted(_look_root().glob("*/look.json")):
        definitions.append(LookDefinition.model_validate_json(path.read_text(encoding="utf-8")))
    if len(definitions) != 3:
        raise LookError(f"expected exactly three curated looks, found {len(definitions)}")
    return sorted(definitions, key=lambda look: look.id)


def get_look(identifier: str, version: str) -> LookDefinition:
    matches = [look for look in list_looks() if look.id == identifier]
    if not matches:
        raise LookError(f"unknown look: {identifier}")
    look = matches[0]
    if look.version != version:
        raise LookError(
            f"look version mismatch for {identifier}: requested {version}, available {look.version}"
        )
    return look


def looks_as_json() -> list[dict[str, object]]:
    return [look.model_dump(mode="json") for look in list_looks()]
