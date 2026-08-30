from __future__ import annotations

from gekigrade.doctor import build_doctor_report


def test_doctor_confirms_milestone_one_runtime() -> None:
    report = build_doctor_report()

    assert report["ready_for_jpeg"] is True
    assert report["python"]["version"].startswith("3.12.")
    assert report["python"]["openimageio"] == "3.1.16.0"
    assert report["python"]["opencolorio"] == "2.5.2"
    assert report["tools"]["exiftool"]["available"] is True
    assert report["tools"]["imagemagick"]["available"] is True
    assert report["profiles"]["acescg"]["sha256"]
    assert report["profiles"]["srgb"]["sha256"]
    assert report["color_probe"]["repeat_tiff_file_hash_equal"] is True
    assert report["color_probe"]["working_profile_embedded"] is True
    assert report["color_probe"]["ocio_roundtrip_rmse"] < 0.00001


def test_doctor_reports_raw_adapter_separately_from_jpeg_readiness() -> None:
    report = build_doctor_report()

    assert report["tools"]["rawtherapee"]["available"] is True
    assert report["tools"]["rawtherapee"]["version"] == "5.13"
    assert report["raw_status"] == "installed-unverified-with-arw"
