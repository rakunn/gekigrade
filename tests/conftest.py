from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    integration_directory = Path(__file__).parent / "integration"
    for item in items:
        if Path(str(item.path)).is_relative_to(integration_directory):
            item.add_marker(pytest.mark.integration)


@pytest.fixture
def tagged_oriented_jpeg(tmp_path: Path) -> Path:
    width, height = 320, 240
    x = np.linspace(16, 240, width, dtype=np.uint8)
    y = np.linspace(8, 220, height, dtype=np.uint8)[:, None]
    pixels = np.empty((height, width, 3), dtype=np.uint8)
    pixels[:, :, 0] = x[None, :]
    pixels[:, :, 1] = y
    pixels[:, :, 2] = ((x[None, :].astype(np.uint16) + y) // 2).astype(np.uint8)
    image = Image.fromarray(pixels, mode="RGB")
    exif = Image.Exif()
    exif[274] = 6
    exif[271] = "GekiGrade Fixture"
    exif[272] = "Synthetic Gradient"
    exif[33434] = IFDRational(1, 125)
    exif[33437] = IFDRational(28, 10)
    exif[34855] = 100
    exif[37386] = IFDRational(35, 1)
    profile = Path("/System/Library/ColorSync/Profiles/sRGB Profile.icc").read_bytes()
    source = tmp_path / "oriented.jpg"
    image.save(source, quality=95, subsampling=0, exif=exif, icc_profile=profile)
    return source
