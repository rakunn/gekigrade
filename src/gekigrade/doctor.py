from __future__ import annotations

import hashlib
import io
import os
import platform
import plistlib
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

import numpy as np
import OpenImageIO as oiio
import PyOpenColorIO as ocio
from PIL import Image, ImageCms

from gekigrade.adapters.rawtherapee import (
    DEFAULT_RAW_PROFILE,
    EXPECTED_DEFAULT_RAW_PROFILE_SHA256,
    SUPPORTED_RAWTHERAPEE_VERSION,
    CameraResourceStatus,
    ResourceStatus,
    inspect_camera_resources,
    inspect_lensfun_database,
    lensfun_database_for_executable,
    path_has_symlink,
    rawtherapee_bundle_has_symlink,
    rawtherapee_output_profile_for_executable,
)
from gekigrade.adapters.tools import ExternalTool, ToolStatus, inspect_tool

ACESCG_PROFILE = Path("/System/Library/ColorSync/Profiles/ACESCG Linear.icc")
SRGB_PROFILE = Path("/System/Library/ColorSync/Profiles/sRGB Profile.icc")
EXIFTOOL_CLI = Path("/opt/homebrew/bin/exiftool")
IMAGEMAGICK_CLI = Path("/opt/homebrew/bin/magick")
RAWTHERAPEE_CLI = Path("/Applications/RawTherapee.app/Contents/MacOS/rawtherapee-cli")
RAWTHERAPEE_PLIST = Path("/Applications/RawTherapee.app/Contents/Info.plist")
EXIFTOOL_ENVIRONMENT = {
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
}
MAGICK_ENVIRONMENT = {
    "LANG": "C",
    "LC_ALL": "C",
    "MAGICK_THREAD_LIMIT": "1",
    "OMP_DYNAMIC": "FALSE",
    "OMP_NUM_THREADS": "1",
    "OMP_SCHEDULE": "static",
    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _profile_status(path: Path) -> dict[str, str | bool | None]:
    exists = not path.is_symlink() and path.is_file()
    return {
        "available": exists,
        "path": str(path),
        "sha256": sha256_file(path) if exists else None,
    }


def icc_profile_status(path: Path) -> dict[str, str | bool | None]:
    status: dict[str, str | bool | None] = {
        "available": False,
        "valid": False,
        "path": str(path),
        "sha256": None,
        "color_space": None,
        "device_class": None,
        "error": None,
    }
    flags = os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise OSError("profile is not a regular file")
            data = stream.read()
            closed = os.fstat(stream.fileno())
        path_status = path.lstat()
        if (
            not stat.S_ISREG(path_status.st_mode)
            or _file_identity(opened) != _file_identity(closed)
            or _file_identity(path_status) != _file_identity(opened)
        ):
            raise OSError("profile changed while it was read")
    except OSError as exc:
        status["error"] = str(exc)
        return status
    status["available"] = True
    status["sha256"] = hashlib.sha256(data).hexdigest()
    try:
        get_open_profile = cast(Callable[[io.BytesIO], Any], ImageCms.getOpenProfile)
        profile = get_open_profile(io.BytesIO(data))
    except (OSError, ImageCms.PyCMSError) as exc:
        status["error"] = str(exc)
        return status
    color_space = str(profile.profile.xcolor_space).strip()
    device_class = str(profile.profile.device_class).strip()
    status["color_space"] = color_space
    status["device_class"] = device_class
    if color_space != "RGB" or device_class != "mntr":
        status["error"] = (
            "RawTherapee output ICC must be an RGB display profile; "
            f"found color space {color_space or 'unknown'} and class "
            f"{device_class or 'unknown'}"
        )
        return status
    status["valid"] = True
    return status


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _rawtherapee_status(*, executable: Path, plist: Path) -> ToolStatus:
    if path_has_symlink(executable) or rawtherapee_bundle_has_symlink(executable):
        return ToolStatus(
            name="rawtherapee",
            available=False,
            path=None,
            version=None,
            install_hint="Install with: brew install --cask rawtherapee",
        )
    version: str | None = None
    if plist.is_file():
        try:
            with plist.open("rb") as stream:
                raw_version = plistlib.load(stream).get("CFBundleShortVersionString")
                version = str(raw_version) if raw_version else None
        except (OSError, plistlib.InvalidFileException):
            version = None
    available = executable.is_file() and executable.stat().st_mode & 0o111 != 0
    return ToolStatus(
        name="rawtherapee",
        available=available,
        path=str(executable) if available else None,
        version=version,
        install_hint="Install with: brew install --cask rawtherapee",
    )


def _color_probe(magick_path: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="gekigrade-color-probe-") as directory:
        root = Path(directory)
        source = root / "source.jpg"
        first = root / "first.tif"
        second = root / "second.tif"
        patches = np.zeros((16, 16, 3), dtype=np.uint8)
        patches[:8, :8] = (32, 64, 128)
        patches[:8, 8:] = (224, 190, 128)
        patches[8:, :8] = (2, 2, 2)
        patches[8:, 8:] = (253, 253, 253)
        Image.fromarray(patches, mode="RGB").save(
            source,
            quality=100,
            subsampling=0,
            icc_profile=SRGB_PROFILE.read_bytes(),
        )
        for target in (first, second):
            result = subprocess.run(
                [
                    magick_path,
                    str(source),
                    "-profile",
                    str(ACESCG_PROFILE),
                    "-depth",
                    "16",
                    "-define",
                    "tiff:bits-per-sample=16",
                    "-compress",
                    "zip",
                    f"TIFF:{target}",
                ],
                capture_output=True,
                check=False,
                text=True,
                timeout=30,
                stdin=subprocess.DEVNULL,
                env=MAGICK_ENVIRONMENT,
            )
            if result.returncode != 0:
                return {
                    "passed": False,
                    "error": result.stderr.strip(),
                    "environment": dict(MAGICK_ENVIRONMENT),
                }
        buffer = oiio.ImageBuf(str(first))
        pixels = np.asarray(buffer.get_pixels(oiio.FLOAT), dtype=np.float32)[:, :, :3]
        roundtrip = np.ascontiguousarray(pixels.copy())
        config = ocio.Config.CreateFromBuiltinConfig("cg-config-v4.0.0_aces-v2.0_ocio-v2.5")
        config.getProcessor("ACEScg", "ACEScct").getDefaultCPUProcessor().applyRGB(roundtrip)
        config.getProcessor("ACEScct", "ACEScg").getDefaultCPUProcessor().applyRGB(roundtrip)
        rmse = float(np.sqrt(np.mean((roundtrip - pixels) ** 2, dtype=np.float64)))
        with Image.open(first) as working:
            profile_embedded = bool(working.info.get("icc_profile"))
        repeated = sha256_file(first) == sha256_file(second)
        return {
            "passed": repeated and profile_embedded and rmse < 0.00001,
            "repeat_tiff_file_hash_equal": repeated,
            "working_profile_embedded": profile_embedded,
            "ocio_roundtrip_rmse": rmse,
            "environment": dict(MAGICK_ENVIRONMENT),
        }


def build_doctor_report(
    *,
    run_color_probe: bool = True,
    exiftool_executable: Path | None = None,
    imagemagick_executable: Path | None = None,
    rawtherapee_executable: Path | None = None,
    raw_output_profile_status: dict[str, str | bool | None] | None = None,
    raw_camera_resources_status: CameraResourceStatus | None = None,
    raw_lensfun_database_status: ResourceStatus | None = None,
    exiftool_tool_status: ToolStatus | None = None,
    imagemagick_tool_status: ToolStatus | None = None,
    rawtherapee_tool_status: ToolStatus | None = None,
) -> dict[str, Any]:
    selected_exiftool = exiftool_executable or EXIFTOOL_CLI
    selected_imagemagick = imagemagick_executable or IMAGEMAGICK_CLI
    selected_rawtherapee = rawtherapee_executable or RAWTHERAPEE_CLI
    rawtherapee_plist = (
        RAWTHERAPEE_PLIST
        if rawtherapee_executable is None
        else selected_rawtherapee.parent.parent / "Info.plist"
    )
    rawtherapee_output_profile = rawtherapee_output_profile_for_executable(selected_rawtherapee)
    tools = {
        "exiftool": (
            exiftool_tool_status
            if exiftool_tool_status is not None
            else inspect_tool(
                ExternalTool(
                    name="exiftool",
                    candidates=(str(selected_exiftool),),
                    version_args=("-config", "", "-ver"),
                    install_hint="Install with: brew install exiftool",
                ),
                environment=EXIFTOOL_ENVIRONMENT,
            )
        ),
        "imagemagick": (
            imagemagick_tool_status
            if imagemagick_tool_status is not None
            else inspect_tool(
                ExternalTool(
                    name="imagemagick",
                    candidates=(str(selected_imagemagick),),
                    version_args=("-version",),
                    install_hint="Install with: brew install imagemagick",
                ),
                environment=MAGICK_ENVIRONMENT,
            )
        ),
        "rawtherapee": (
            rawtherapee_tool_status
            if rawtherapee_tool_status is not None
            else _rawtherapee_status(executable=selected_rawtherapee, plist=rawtherapee_plist)
        ),
    }
    camera_resources = (
        raw_camera_resources_status
        if raw_camera_resources_status is not None
        else inspect_camera_resources(executable=selected_rawtherapee)
    )
    lensfun_database = (
        raw_lensfun_database_status
        if raw_lensfun_database_status is not None
        else inspect_lensfun_database(
            database=lensfun_database_for_executable(selected_rawtherapee)
        )
    )
    raw_profile_status = _profile_status(DEFAULT_RAW_PROFILE)
    raw_profile_status["expected_sha256"] = EXPECTED_DEFAULT_RAW_PROFILE_SHA256
    raw_profile_status["matches_expected"] = (
        raw_profile_status["sha256"] == EXPECTED_DEFAULT_RAW_PROFILE_SHA256
    )
    profiles = {
        "acescg": _profile_status(ACESCG_PROFILE),
        "srgb": _profile_status(SRGB_PROFILE),
        "raw_development_pp3": raw_profile_status,
        "rawtherapee_output": (
            raw_output_profile_status
            if raw_output_profile_status is not None
            else icc_profile_status(rawtherapee_output_profile)
        ),
        "rawtherapee_camera_resources": camera_resources,
        "lensfun_database": lensfun_database,
    }
    prerequisites_ready = (
        tools["exiftool"].available
        and tools["imagemagick"].available
        and profiles["acescg"]["available"] is True
        and profiles["srgb"]["available"] is True
    )
    color_probe: dict[str, Any] | None = None
    if run_color_probe and prerequisites_ready and tools["imagemagick"].path:
        color_probe = _color_probe(tools["imagemagick"].path)
    ready = prerequisites_ready and (color_probe is None or color_probe.get("passed") is True)
    ready_for_raw = (
        ready
        and tools["rawtherapee"].available
        and tools["rawtherapee"].version == SUPPORTED_RAWTHERAPEE_VERSION
        and profiles["raw_development_pp3"]["matches_expected"] is True
        and profiles["rawtherapee_output"]["available"] is True
        and profiles["rawtherapee_output"]["valid"] is True
        and camera_resources["ready"]
        and lensfun_database["ready"]
    )
    return {
        "schema_version": "1.0.0",
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
            "openimageio": oiio.VERSION_STRING,
            "opencolorio": ocio.GetVersion(),
        },
        "tools": {name: asdict(status) for name, status in tools.items()},
        "profiles": profiles,
        "color_probe": color_probe,
        "ready_for_jpeg": ready,
        "ready_for_raw": ready_for_raw,
        "raw_status": "adapter-ready" if ready_for_raw else "not-ready",
    }
