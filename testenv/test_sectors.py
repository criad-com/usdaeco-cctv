import numpy as np
import pytest
from usdaeco_cctv.density import ladder
from usdaeco_cctv.sectors import is_closed, level_shells, ptz_envelope, sector_mesh


@pytest.mark.parametrize("projection,hfov,vfov", [("rectilinear", 104, 76), ("fisheye", 180, 180), ("cylindrical", 270, 90)])
def test_sector_is_closed_at_the_radius(projection, hfov, vfov):
    points, counts, indices = sector_mesh(hfov, vfov, 14.0, nu=24, nv=14, projection=projection)
    assert len(points) == 1 + 25 * 15 and sum(counts) == len(indices)
    assert is_closed(counts, indices)
    assert np.allclose(np.linalg.norm(points[1:], axis=1), 14.0) or projection == "cylindrical"
    assert np.all(points[1:, 2] <= 1e-9) if projection == "rectilinear" else True


def test_rectilinear_sector_spans_its_angles():
    points = sector_mesh(104, 76, 10.0)[0]
    x, y, z = points[1:].T
    assert np.degrees(2 * np.arctan(np.max(np.abs(x) / -z))) == pytest.approx(104)
    assert np.degrees(2 * np.arctan(np.max(np.abs(y) / -z))) == pytest.approx(76)
    with pytest.raises(ValueError):
        sector_mesh(180, 76, 10.0)


def test_level_shells_lie_strictly_inside():
    shells = level_shells(104, 76, 2592, 14.0, ladder("dori2015"))
    assert set(shells) == {"identify", "recognise"}
    assert all(np.max(np.linalg.norm(m[0][1:], axis=1)) < 14.0 for m in shells.values())
    assert level_shells(95, 40, 0, 60.0, ladder("dori2015")) == {}


def test_envelope_reach_in_device_frame():
    points, counts, indices = ptz_envelope((-180, 180), (0, 90), 20.0)
    assert is_closed(counts, indices) and np.allclose(np.linalg.norm(points[1:], axis=1), 20.0)
    assert np.all(points[1:, 2] <= 1e-9)  # positive tilt reaches downwards only
    with pytest.raises(ValueError):
        ptz_envelope((180, -180), (0, 90), 20.0)
