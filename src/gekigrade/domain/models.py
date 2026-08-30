from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
SemanticVersion = Annotated[str, StringConstraints(pattern=r"^\d+\.\d+\.\d+$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LookReference(StrictModel):
    id: Identifier
    version: SemanticVersion
    strength: float = Field(ge=0.0, le=1.0)


class CandidateRecipe(StrictModel):
    id: Identifier
    description: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    rotation_degrees: float = Field(ge=-5.0, le=5.0)
    exposure_ev: float = Field(ge=-2.0, le=2.0)
    temperature_mired_shift: float = Field(ge=-30.0, le=30.0)
    contrast: float = Field(ge=-0.25, le=0.25)
    black_lift: float = Field(ge=0.0, le=0.03)
    highlight_rolloff: float = Field(ge=0.0, le=0.5)
    saturation: float = Field(ge=-0.25, le=0.25)
    vignette: float = Field(ge=0.0, le=0.25)
    sharpen: float = Field(ge=0.0, le=1.0)
    look: LookReference
    crop_id: Identifier


class EditPlan(StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://gekigrade.local/schemas/edit-plan-1.0.0.json",
        },
    )

    schema_version: Literal["1.0.0"]
    source_sha256: Sha256
    candidates: tuple[CandidateRecipe, CandidateRecipe, CandidateRecipe]

    @model_validator(mode="after")
    def candidate_ids_are_distinct(self) -> EditPlan:
        ids = [candidate.id for candidate in self.candidates]
        if len(set(ids)) != len(ids):
            raise ValueError("candidate IDs must be distinct")
        return self
