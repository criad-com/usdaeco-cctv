import math
import pytest
from usdaeco_cctv.density import (arc_range, effective_width, head_optics, ladder, level_name,
                                  optics, plane_density, plane_range, range_at_density)

P3277 = dict(focal_range=(3, 8.5), hfov_range=(104, 34), vfov_range=(76, 26), pixels=(2592, 1944))


def test_datasheet_endpoints_are_reproduced():
    wide = optics(3, **P3277)
    tele = optics(8.5, **P3277)
    assert wide["hfov"] == pytest.approx(104, abs=1e-9) and wide["vfov"] == pytest.approx(76, abs=1e-9)
    assert tele["hfov"] == pytest.approx(34, abs=1e-9) and tele["vfov"] == pytest.approx(26, abs=1e-9)
    assert wide["effectiveWidth"] == pytest.approx(2 * 3 * math.tan(math.radians(52)))
    assert optics(0, **P3277)["focalLength"] == 3 and optics(50, **P3277)["focalLength"] == 8.5


def test_sensor_size_and_pixel_aspect_fallbacks():
    o = optics(4, (4, 4), (0, 0), (0, 0), (1920, 1080), sensor_size=(5.76, 3.24))
    assert o["hfov"] == pytest.approx(math.degrees(2 * math.atan(5.76 / 8)))
    aspect = optics(4, (4, 4), (90, 90), (0, 0), (1920, 1080))
    assert aspect["effectiveHeight"] == pytest.approx(aspect["effectiveWidth"] * 1080 / 1920)
    with pytest.raises(ValueError):
        effective_width(4, (4, 4), (0, 0), 0)


def test_plane_and_arc_ranges():
    assert plane_range(2688, 88, 250) == pytest.approx(2688 / (2 * math.tan(math.radians(44)) * 250))
    assert arc_range(2688, 88, 250) == pytest.approx(2688 / (math.radians(88) * 250))
    assert plane_density(2688, 88, plane_range(2688, 88, 250)) == pytest.approx(250)
    assert range_at_density(0, 88, 250) == 0
    with pytest.raises(ValueError):
        range_at_density(2688, 88, 250, "sphere")


def test_ladders_and_level_names():
    dori = ladder("dori2015")
    assert dori == {"detect": 25, "observe": 62.5, "recognise": 125, "identify": 250}
    assert len(ladder("oodpcvs2025")) == 7 and ladder("project")["project"] == 180
    assert level_name(300, dori) == "identify" and level_name(125, dori) == "recognise"
    assert level_name(10, dori) == "none"
    with pytest.raises(ValueError):
        ladder("dori1999")


def test_non_rectilinear_heads_use_the_arc_model():
    fish = head_optics(1.6, (1.6, 1.6), (180, 180), (180, 180), (2048, 2048), projection="fisheye")
    assert fish["hfov"] == 180 and fish["model"] == "arc"
    assert fish["effectiveWidth"] == pytest.approx(1.6 * math.pi)
    radar = head_optics(0, (2.8, 2.8), (95, 95), (40, 40), (0, 0), projection="fisheye")
    assert radar["vfov"] == 40
    with pytest.raises(ValueError):
        head_optics(0, (2.8, 2.8), (95, 95), (0, 0), (0, 0), projection="fisheye")
