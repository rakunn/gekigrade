# Architecture

## Component boundaries

```mermaid
flowchart LR
    CLI[Typer CLI] --> UseCases[Pipeline use cases]
    UseCases --> Domain[Strict domain models and validation]
    UseCases --> Imaging[Deterministic imaging functions]
    UseCases --> Adapters[External-tool adapters]
    Adapters --> Exif[ExifTool]
    Adapters --> Magick[ImageMagick and LittleCMS]
    Imaging --> OIIO[OpenImageIO and NumPy]
    Imaging --> OCIO[OpenColorIO]
    Domain --> Artifacts[Versioned JSON artifacts]
```

CLI code translates arguments and errors. Pipeline use cases own job transitions. Domain code owns schemas, invariants, hashes, and path policy without importing Typer or subprocess modules. Imaging code receives arrays and typed recipes. Adapters receive validated paths and construct fixed command arguments.

## Job lifecycle

```mermaid
stateDiagram-v2
    [*] --> Prepared: prepare
    Prepared --> Planned: external plan creation
    Planned --> Validated: validate-plan
    Validated --> Rendered: render
    Rendered --> Assessed: qa
    Assessed --> Selected: select
    Selected --> Exported: export
```

Each command validates the manifest and prerequisite artifact hashes before changing state. A command writes new derived artifacts but never edits the source. Conflicting existing artifacts fail unless the command can prove they are identical and reusable.

## Staged processing

The immutable working image is oriented and transformed to linear ACEScg before editable stages. A recipe then applies geometry, global correction, a creative look, an empty local-adjustment stage, crop, output transform, and export encoding. Operations state their units, valid ranges, and processing spaces. Preview and full resolution share this evaluator.

## Edit-plan boundary

Plans contain a schema version, source hash, and exactly three recipes. Each recipe references allowlisted operation fields, a known look ID/version, and a prepared crop ID. Pydantic performs structural validation; job-aware validation resolves crop and look references and checks the source hash. Unknown fields are forbidden. No plan value reaches a shell or becomes a path.

## Data formats

- JSON: UTF-8, canonicalized with sorted keys and compact separators before hashing.
- Working image: oriented 16-bit RGB TIFF with ACEScg ICC profile.
- Preview and contact sheets: sRGB JPEG with ICC profile.
- Histograms: fixed 256-bin integer arrays.
- Final output: sRGB JPEG with an explicit metadata policy.

## Errors and security

User or validation errors exit with code 2; missing dependencies with 3; processor failures with 4; fatal QA with 5. External tools run without a shell, with explicit timeouts, local temporary directories, safe environment variables, and captured stderr. Input is verified as a single JPEG by decoder and signature. Symlinks, path traversal, source/output overlap, and unprofiled CMYK are rejected.

## Extension points

A future RAW adapter produces the same working-image contract from an explicit PP3 profile. A future planner consumes current artifacts and emits the same plan contract. Local adjustments may be added only through a new schema version with typed masks in the same post-geometry coordinate system. No current abstraction downloads models or anticipates a UI.
