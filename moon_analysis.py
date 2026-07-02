"""
moon_analysis.py — Lunar sign-change handling for the natal report system.

Two entry points:
  * detect_moon_ingress(...) -> use when the birth time is UNKNOWN
  * check_moon_cusp(...)     -> use when the birth time is KNOWN

Both return a plain dict that can be merged into the report's response meta.

NOTE: the pasted source had the private helper defined as `moon_sign_at`
but called as `_moon_sign_at` — a plain NameError at first call. The
underscore-prefixed name matches the intent (private helper) and both
public entry points, so the definition is fixed to `_moon_sign_at` here.
"""
from kerykeion import AstrologicalSubject
from datetime import datetime, timedelta

SIGN_PT = {
    "Ari": "Áries", "Tau": "Touro", "Gem": "Gêmeos", "Can": "Câncer",
    "Leo": "Leão", "Vir": "Virgem", "Lib": "Libra", "Sco": "Escorpião",
    "Sag": "Sagitário", "Cap": "Capricórnio", "Aqu": "Aquário", "Pis": "Peixes",
}


def _moon_sign_at(year, month, day, hour, minute, lat, lng, tz_str):
    """Return the Moon's sign abbreviation at a given local clock time."""
    subject = AstrologicalSubject(
        "", year, month, day, hour, minute,
        lat=lat, lng=lng, tz_str=tz_str,
        city="_", online=False,
    )
    return subject.moon.sign


def detect_moon_ingress(year, month, day, lat, lng, tz_str):
    """
    For an UNKNOWN-birth-time chart: did the Moon change signs on the birth date?
    If so, the sign cannot be stated with certainty.

    Returns either:
      {"moon_sign_uncertain": False, "moon_sign": "<PT>", "moon_sign_abbr": "<abbr>"}
    or:
      {"moon_sign_uncertain": True, "moon_sign_before": "<PT>",
       "moon_sign_after": "<PT>", "moon_ingress_local_time": "HH:MM"}
    """
    start = _moon_sign_at(year, month, day, 0, 0, lat, lng, tz_str)
    end = _moon_sign_at(year, month, day, 23, 59, lat, lng, tz_str)

    if start == end:
        return {
            "moon_sign_uncertain": False,
            "moon_sign": SIGN_PT[start],
            "moon_sign_abbr": start,
        }

    # Ingress happened: binary-search the local minute of the crossing.
    base = datetime(year, month, day, 0, 0)
    lo, hi = 0, 1439
    while hi - lo > 1:
        mid = (lo + hi) // 2
        t = base + timedelta(minutes=mid)
        if _moon_sign_at(year, month, day, t.hour, t.minute, lat, lng, tz_str) == start:
            lo = mid
        else:
            hi = mid
    cross = base + timedelta(minutes=hi)

    return {
        "moon_sign_uncertain": True,
        "moon_sign_before": SIGN_PT[start],
        "moon_sign_after": SIGN_PT[end],
        "moon_ingress_local_time": cross.strftime("%H:%M"),
    }


def check_moon_cusp(year, month, day, hour, minute, lat, lng, tz_str,
                    margin_minutes=15):
    """
    For a KNOWN-birth-time chart: is the given time close enough to a lunar
    sign change that a small error in the recorded time could flip the
    Moon's sign?

    Returns either:
      {"moon_near_cusp": False, "moon_sign": "<PT>"}
    or:
      {"moon_near_cusp": True, "moon_sign": "<PT>",
       "moon_adjacent_sign": "<PT>", "minutes_from_cusp": <int>}
    """
    here = _moon_sign_at(year, month, day, hour, minute, lat, lng, tz_str)
    base = datetime(year, month, day, hour, minute)

    for direction in (-1, 1):
        for delta in range(1, margin_minutes + 1):
            t = base + direction * timedelta(minutes=delta)
            if t.day != day:  # don't cross out of the birth date
                break
            other = _moon_sign_at(year, month, day, t.hour, t.minute,
                                  lat, lng, tz_str)
            if other != here:
                return {
                    "moon_near_cusp": True,
                    "moon_sign": SIGN_PT[here],
                    "moon_adjacent_sign": SIGN_PT[other],
                    "minutes_from_cusp": delta,
                }
    return {"moon_near_cusp": False, "moon_sign": SIGN_PT[here]}
