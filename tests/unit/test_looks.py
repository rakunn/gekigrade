from __future__ import annotations

import pytest

from gekigrade.grading.looks import LookError, get_look, list_looks


def test_three_versioned_restrained_looks_are_available() -> None:
    looks = list_looks()

    assert [look.id for look in looks] == [
        "muted-cinematic",
        "natural-clean",
        "warm-editorial",
    ]
    for look in looks:
        assert look.version == "1.0.0"
        assert look.expected_input_color_space == "ACEScct"
        assert look.continuation_color_space == "ACEScct"
        assert look.strength_range[0] == 0.0
        assert look.default_strength <= look.strength_range[1]


def test_unknown_or_wrong_version_look_is_rejected() -> None:
    with pytest.raises(LookError, match="unknown look"):
        get_look("invented-look", "1.0.0")
    with pytest.raises(LookError, match="version"):
        get_look("natural-clean", "2.0.0")
