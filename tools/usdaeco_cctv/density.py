"""Datasheet optics (mm, degrees) and pixel density (px/m) at a distance (m).

hfov = 2 atan(w / 2f), with the effective width w(f) interpolated between
2 f_min tan(HFOV_wide / 2) and 2 f_max tan(HFOV_tele / 2) so that datasheet FOV
ranges are honoured (design §3.4), or taken from a physical sensor size.

plane model: rho(d) = H / (2 d tan(hfov/2))   d(rho) = H / (2 rho tan(hfov/2))
arc model:   rho(d) = H / (hfov_rad d)         d(rho) = H / (hfov_rad rho)
"""
import math

from . import registry

MODELS = ("plane", "arc")
PROJECTIONS = ("rectilinear", "fisheye", "cylindrical")


def ladder(name="dori2015"):
    """A named density ladder {level: px/m} from registries/density_levels.json."""
    ladders = registry("density_levels")
    if name not in ladders:
        raise ValueError("unknown density ladder: " + str(name))
    return ladders[name]


def _positive(value, name):
    if not math.isfinite(value) or value <= 0:
        raise ValueError(name + " must be finite and positive")
    return value


def clamp_focal(focal_length, focal_range):
    """Zoom state clamped to the focal range; 0 = the widest setting."""
    lo, hi = map(float, focal_range)
    _positive(lo, "minimum focal length")
    _positive(hi, "maximum focal length")
    if hi < lo or not math.isfinite(focal_length):
        raise ValueError("invalid focal range or focal length")
    return min(max(focal_length or lo, lo), hi)


def effective_width(focal_length, focal_range, hfov_range=(0, 0), sensor_size=0):
    """Interpolate widths from the wide and telephoto FOV endpoints; clamp zoom.

    FOV pairs follow focal_range order (wide angle at the minimum focal
    length, telephoto at the maximum). An absent (0, 0) FOV pair uses the
    physical sensor_size in mm instead.
    """
    lo, hi = map(float, focal_range)
    f = clamp_focal(focal_length, focal_range)
    if tuple(hfov_range) == (0, 0):
        return _positive(float(sensor_size), "physical sensor size")
    wide, tele = map(float, hfov_range)
    if not (0 < tele <= wide < 180):
        raise ValueError("rectilinear FOV endpoints must satisfy 0 < tele <= wide < 180")
    w0 = 2 * lo * math.tan(math.radians(wide) / 2)
    w1 = 2 * hi * math.tan(math.radians(tele) / 2)
    return w0 if hi == lo else w0 + (w1 - w0) * (f - lo) / (hi - lo)


def field_of_view(size, focal_length):
    """Rectilinear angle in degrees for an image size and focal length in mm."""
    return math.degrees(2 * math.atan(_positive(size, "size") / (2 * _positive(focal_length, "focal length"))))


def optics(focal_length, focal_range, hfov_range, vfov_range, pixels, sensor_size=(0, 0)):
    """Rectilinear head: focal length, effective width/height (mm), hfov/vfov (deg)."""
    f = clamp_focal(focal_length, focal_range)
    w = effective_width(f, focal_range, hfov_range, sensor_size[0])
    if tuple(vfov_range) != (0, 0) or sensor_size[1] > 0:
        h = effective_width(f, focal_range, vfov_range, sensor_size[1])
    else:
        _positive(pixels[0], "horizontal pixels")
        _positive(pixels[1], "vertical pixels")
        h = w * pixels[1] / pixels[0]
    return dict(focalLength=f, effectiveWidth=w, effectiveHeight=h,
                hfov=field_of_view(w, f), vfov=field_of_view(h, f))


def _interpolate(pair, f, focal_range):
    lo, hi = map(float, focal_range)
    a, b = map(float, pair)
    return a if hi == lo else a + (b - a) * (f - lo) / (hi - lo)


def head_optics(focal_length, focal_range, hfov_range, vfov_range, pixels,
                sensor_size=(0, 0), projection="rectilinear"):
    """Optics for any projection; adds the density model the projection implies.

    Fisheye and cylindrical heads take their angles straight from the
    datasheet ranges (interpolated over the focal range) and use the
    equidistant image (w = f * hfov_rad), which is the arc density model.
    """
    if projection not in PROJECTIONS:
        raise ValueError("unknown projection: " + str(projection))
    if projection == "rectilinear":
        return dict(optics(focal_length, focal_range, hfov_range, vfov_range, pixels, sensor_size),
                    model=None)
    f = clamp_focal(focal_length, focal_range)
    hfov = _interpolate(hfov_range, f, focal_range)
    if tuple(vfov_range) != (0, 0):
        vfov = _interpolate(vfov_range, f, focal_range)
    else:
        _positive(pixels[0], "horizontal pixels")
        _positive(pixels[1], "vertical pixels")
        vfov = hfov * pixels[1] / pixels[0]
    limit = 360 if projection == "fisheye" else 360
    if not (0 < hfov <= limit and 0 < vfov <= 180):
        raise ValueError("invalid angles for a " + projection + " head")
    return dict(focalLength=f, effectiveWidth=f * math.radians(hfov), effectiveHeight=f * math.radians(vfov),
                hfov=hfov, vfov=vfov, model="arc")


def _spread(hfov, model):
    if model not in MODELS:
        raise ValueError("density model must be plane or arc")
    if not math.isfinite(hfov) or not 0 < hfov < (180 if model == "plane" else 361):
        raise ValueError("invalid field of view for density model")
    return 2 * math.tan(math.radians(hfov) / 2) if model == "plane" else math.radians(hfov)


def density_at_range(pixels, hfov, distance, model="plane"):
    """Pixels per metre at a distance for a horizontal pixel count and angle."""
    _positive(distance, "distance")
    if not math.isfinite(pixels) or pixels < 0:
        raise ValueError("pixels must be finite and nonnegative")
    return pixels / (_spread(hfov, model) * distance)


def range_at_density(pixels, hfov, density, model="plane"):
    """Distance at which a density is met. Zero-pixel (radar) heads have no density range."""
    _positive(density, "density")
    if not math.isfinite(pixels) or pixels < 0:
        raise ValueError("pixels must be finite and nonnegative")
    return pixels / (_spread(hfov, model) * density)


def plane_density(pixels, hfov, distance):
    return density_at_range(pixels, hfov, distance, "plane")


def arc_density(pixels, hfov, distance):
    return density_at_range(pixels, hfov, distance, "arc")


def plane_range(pixels, hfov, density):
    return range_at_density(pixels, hfov, density, "plane")


def arc_range(pixels, hfov, density):
    return range_at_density(pixels, hfov, density, "arc")


def level_name(density, ladder):
    """Name of the highest threshold attained; registry ordering is irrelevant."""
    return next((name for name, threshold in sorted(ladder.items(), key=lambda p: p[1], reverse=True)
                 if density >= threshold), "none")
