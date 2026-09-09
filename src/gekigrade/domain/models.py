from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

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


class CandidateRecipeV2(StrictModel):
    """Version 2 controls; version 1 fields deliberately remain a separate contract."""

    id: Identifier
    description: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    rotation_degrees: float = Field(ge=-5.0, le=5.0)
    exposure_ev: float = Field(ge=-2.0, le=2.0)
    temperature_mired_shift: float = Field(ge=-30.0, le=30.0)
    contrast: float = Field(ge=-0.25, le=0.25)
    black_lift: float = Field(ge=0.0, le=0.03)
    shadow_recovery_ev: float = Field(
        ge=0.0,
        le=2.0,
        description="Maximum gain in stops; linear ACEScg shadows, fades to zero at Y=0.18.",
    )
    highlight_compression: float = Field(
        ge=0.0,
        le=1.0,
        description="Dimensionless rational luminance shoulder above linear ACEScg Y=0.5; 0 off.",
    )
    saturation: float = Field(ge=-0.25, le=0.25)
    vignette: float = Field(ge=0.0, le=0.25)
    sharpen: float = Field(ge=0.0, le=1.0)
    look: LookReference
    crop_id: Identifier


class EditPlanV2(StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://gekigrade.local/schemas/edit-plan-2.0.0.json",
        },
    )

    schema_version: Literal["2.0.0"]
    source_sha256: Sha256
    candidates: tuple[CandidateRecipeV2, CandidateRecipeV2, CandidateRecipeV2]

    @model_validator(mode="after")
    def candidate_ids_are_distinct(self) -> EditPlanV2:
        ids = [candidate.id for candidate in self.candidates]
        if len(set(ids)) != len(ids):
            raise ValueError("candidate IDs must be distinct")
        return self


AnyRecipe = CandidateRecipe | CandidateRecipeV2
AnyEditPlan = Annotated[EditPlan | EditPlanV2, Field(discriminator="schema_version")]
EDIT_PLAN_ADAPTER: TypeAdapter[AnyEditPlan] = TypeAdapter(AnyEditPlan)


def edit_plan_schema() -> dict[str, object]:
    schema = EDIT_PLAN_ADAPTER.json_schema()
    # Embedded standalone IDs would create new resource roots, making their
    # #/$defs/... references point at missing definitions instead of this union.
    for definition in schema.get("$defs", {}).values():
        definition.pop("$id", None)
        definition.pop("$schema", None)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://gekigrade.local/schemas/edit-plan.json",
        **schema,
    }
