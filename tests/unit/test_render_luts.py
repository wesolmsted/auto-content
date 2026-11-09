from pathlib import Path

import pytest

from engine.recipes.models import Look
from engine.render.filters import lut_path_for

LUTS_DIR = Path(__file__).resolve().parents[2] / "config" / "luts"

Rgb = tuple[float, float, float]


def read_cube(path: Path) -> tuple[int, list[Rgb]]:
    size = 0
    rows: list[Rgb] = []
    for line in path.read_text().splitlines():
        if line.startswith("LUT_3D_SIZE"):
            size = int(line.split()[1])
        elif line and (line[0].isdigit() or line[0] == "-"):
            red, green, blue = (float(value) for value in line.split())
            rows.append((red, green, blue))
    return size, rows


def lookup(size: int, rows: list[Rgb], red: int, green: int, blue: int) -> Rgb:
    return rows[red + green * size + blue * size * size]


@pytest.mark.parametrize("look", [look for look in Look if look is not Look.NATURAL])
def test_every_non_natural_look_has_a_complete_cube_file(look: Look) -> None:
    path = lut_path_for(look, LUTS_DIR)
    assert path is not None
    size, rows = read_cube(path)
    assert size >= 2
    assert len(rows) == size**3
    assert all(0.0 <= value <= 1.0 for row in rows for value in row)


def test_bw_maps_every_color_to_gray() -> None:
    size, rows = read_cube(LUTS_DIR / "bw.cube")
    assert all(red == green == blue for red, green, blue in rows)
    assert lookup(size, rows, size - 1, size - 1, size - 1) == pytest.approx((1.0, 1.0, 1.0))


def test_warm_pushes_mid_gray_toward_red_and_away_from_blue() -> None:
    size, rows = read_cube(LUTS_DIR / "warm.cube")
    middle = size // 2
    red, _, blue = lookup(size, rows, middle, middle, middle)
    assert red > middle / (size - 1) > blue


def test_high_contrast_darkens_shadows_and_brightens_highlights() -> None:
    size, rows = read_cube(LUTS_DIR / "high_contrast.cube")
    shadow = size // 4
    highlight = size - 1 - size // 4
    assert lookup(size, rows, shadow, shadow, shadow)[0] < shadow / (size - 1)
    assert lookup(size, rows, highlight, highlight, highlight)[0] > highlight / (size - 1)
