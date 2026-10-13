from __future__ import annotations

import cv2
import numpy as np
import pytest

from anpr_service.recognizers.classical import ClassicalRecognizer
from anpr_service.rows import detect_rows, split_rows
from anpr_service.synth import render_plate


def _plate(plate: str, layout: str, width: int, font: int = cv2.FONT_HERSHEY_SIMPLEX) -> np.ndarray:
    img = render_plate(plate, layout, font)
    h = int(round(img.shape[0] * width / img.shape[1]))
    return cv2.resize(img, (width, h), interpolation=cv2.INTER_AREA)


@pytest.mark.parametrize("width", [90, 140, 220])
def test_two_line_plate_has_two_rows_in_order(width: int) -> None:
    crop = _plate("MH43AB1234", "two_line", width)
    layout = detect_rows(crop)
    assert layout.n_rows == 2
    (a0, a1), (b0, b1) = layout.rows
    assert a1 <= b0 + 1, "rows must be ordered top to bottom and not overlap"
    assert a0 < crop.shape[0] / 2 < b1


@pytest.mark.parametrize("width", [120, 200, 300])
def test_single_line_plate_has_one_row(width: int) -> None:
    crop = _plate("MH43AB1234", "single", width)
    assert detect_rows(crop).n_rows == 1
    assert len(split_rows(crop)) == 1


def test_light_text_on_dark_plate_polarity() -> None:
    crop = 255 - _plate("MH43AB1234", "two_line", 160)
    layout = detect_rows(crop)
    assert layout.n_rows == 2
    assert layout.ink_polarity_dark is False


@pytest.mark.parametrize(
    "plate,layout,width,font",
    [
        ("MH43AB1234", "single", 200, cv2.FONT_HERSHEY_SIMPLEX),
        ("MH12DE4521", "two_line", 120, cv2.FONT_HERSHEY_DUPLEX),
        ("KA05MN2468", "two_line", 160, cv2.FONT_HERSHEY_DUPLEX),
        ("DL3CAF5031", "two_line", 110, cv2.FONT_HERSHEY_SIMPLEX),
        ("22BH1234AA", "two_line", 150, cv2.FONT_HERSHEY_SIMPLEX),
    ],
)
def test_classical_ocr_reads_rendered_plates(plate: str, layout: str, width: int, font: int) -> None:
    rec = ClassicalRecognizer()
    res = rec.read_plate(_plate(plate, layout, width, font))
    assert res is not None
    assert res.text == plate
    assert res.rows == (2 if layout == "two_line" else 1)
    assert res.confidence > 0.6
