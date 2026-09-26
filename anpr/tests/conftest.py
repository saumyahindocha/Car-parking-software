"""Shared fixtures.  Makes ``anpr_service`` importable without installation."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from anpr_service import synth  # noqa: E402


@pytest.fixture(scope="session")
def synthetic_gate(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any]]:
    """A short rendered gate scene (L + R cameras, 1280x720 @ 15 fps).

    Contents: a side-by-side pair (single-line + two-line plates), a bike in
    the middle seen by both cameras, a staggered, partly occluded pair, and a
    bike with an unreadable plate, then one bike riding the wrong way
    (towards the cameras) through the IN gate.
    """
    out = tmp_path_factory.mktemp("synth") / "G1"
    sc = synth.default_scenario("G1", width=1280, height=720, fps=15, compact=True, overview=False, ext=".avi",
                                wrong_way=True)
    gt = synth.render_scenario(sc, out)
    return out, gt
