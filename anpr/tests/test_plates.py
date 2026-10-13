from __future__ import annotations

import pytest

from anpr_service.plates import (
    PlateRules,
    approx_match,
    best_correction,
    canonical,
    correct,
    format_display,
    is_valid,
    levenshtein,
    normalize,
    plate_format,
    plates_equal,
    split_rows,
)


@pytest.mark.parametrize(
    "plate",
    ["MH43AB1234", "MH4A1234", "DL3CAF5031", "KA051234", "MH12DE4521", "UP16ABC0001", "TN9Z1234"],
)
def test_valid_standard(plate: str) -> None:
    assert plate_format(plate) == "STANDARD"


@pytest.mark.parametrize("plate", ["22BH1234AA", "21BH0001A"])
def test_valid_bh(plate: str) -> None:
    assert plate_format(plate) == "BH"


@pytest.mark.parametrize(
    "plate",
    ["", "MH", "M143AB1234", "MH43AB123", "MH43ABCD1234", "XX43AB1234", "22BH1234", "22BH1234ABC", "MH123AB1234"],
)
def test_invalid(plate: str) -> None:
    assert not is_valid(plate)


def test_state_codes_configurable() -> None:
    rules = PlateRules.from_config(["MH", "GJ"])
    assert is_valid("MH43AB1234", rules)
    assert not is_valid("KA05MN2468", rules)
    assert is_valid("22BH1234AA", rules)
    assert not is_valid("22BH1234AA", PlateRules.from_config(["MH"], allow_bh=False))
    assert is_valid("XX43AB1234", PlateRules.from_config(["MH"], require_state_code=False))


def test_normalize() -> None:
    assert normalize(" mh-43 ab.1234 ") == "MH43AB1234"
    assert normalize(None) == ""


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("MH43AB1234", "MH43AB1234"),  # already valid: no change
        ("MH43A81234", "MH43AB1234"),  # 8 in a letter slot -> B
        ("MH43AB12S4", "MH43AB1254"),  # S in a digit slot -> 5
        ("MHI2DE4521", "MH12DE4521"),  # I in the district digits -> 1
        ("0L3CAF5031", "DL3CAF5031"),  # 0 in the state code -> D (valid state DL)
        ("MH14GX07B6", "MH14GX0786"),
        ("22BH1234AA", "22BH1234AA"),
        ("228H1234AA", "22BH1234AA"),  # 8 -> B inside the literal BH
        ("MH12ABI234", "MH12AB1234"),  # I in the number -> 1
        ("MH1ZAB1234", "MH1ZAB1234"),  # valid as-is (1-digit district, 3-letter series)
    ],
)
def test_position_aware_correction(raw: str, expected: str) -> None:
    corr = best_correction(raw)
    assert corr is not None
    assert corr.plate == expected
    assert corr.substitutions == sum(a != b for a, b in zip(raw, expected))


def test_correction_does_not_introduce_series_i_or_o() -> None:
    # '0' in the series slot becomes D/Q, never O.
    corrections = correct("MH120A1234")
    assert corrections
    assert all(c.plate[4] != "O" for c in corrections)


def test_correction_rejects_unfixable() -> None:
    assert best_correction("MHXYAB1234") is None  # X/Y have no digit look-alike
    assert best_correction("HELLO") is None


def test_confusion_aware_equality() -> None:
    assert plates_equal("MH12AB1234", "MHI2A81234")
    assert plates_equal("DL3CAF5031", "0L3CAF5O31")
    assert plates_equal("MH14GX0786", "MH14GX07B6")
    assert not plates_equal("MH12AB1234", "MH12AB1235")
    assert not plates_equal("MH12AB1234", None)
    assert canonical("O0DQ") == "0000"


def test_approx_match_levenshtein() -> None:
    assert levenshtein("kitten", "sitting") == 3
    assert approx_match("MH12AB1234", "MH12AB124")  # one deletion
    assert approx_match("MH12AB1234", "MH12A81235")  # confusion + one substitution
    assert not approx_match("MH12AB1234", "MH12AB5678")


def test_split_rows_and_display() -> None:
    assert split_rows("MH43AB1234") == ("MH43", "AB1234")
    assert split_rows("DL3CAF5031") == ("DL3", "CAF5031")
    assert split_rows("22BH1234AA") == ("22BH", "1234AA")
    assert format_display("MH43AB1234") == "MH 43 AB 1234"
    assert format_display("22BH1234AA") == "22 BH 1234 AA"
