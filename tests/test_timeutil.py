import random

import pytest

from videocli.core import timeutil as tu
from videocli.core.errors import UsageError


@pytest.mark.parametrize(
    "text,expected",
    [("12", 12.0), ("12.345", 12.345), (".5", 0.5), ("1:30", 90.0), ("01:30.5", 90.5), ("1:02:03.25", 3723.25), ("0:00:00", 0.0)],
)
def test_parse_time(text, expected):
    assert tu.parse_time(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "abc", "1:75", "-3", "1:2:3:4", "1:60:00"])
def test_parse_time_rejects(text):
    with pytest.raises(UsageError):
        tu.parse_time(text)


def test_rate_frames():
    r = tu.Rate.parse("30000/1001")
    assert str(r) == "30000/1001"
    assert r.to_frame(1.0) == 30
    assert r.to_seconds(30) == pytest.approx(1.001)
    assert r.snap(0.52) == pytest.approx(0.534, abs=1e-3)  # frame 16
    assert tu.Rate.parse(25).frame_duration == pytest.approx(0.04)
    with pytest.raises(UsageError):
        tu.Rate.parse("0/1")


def _rand_ranges(rng, n=6, span=100):
    out = []
    for _ in range(n):
        a = rng.uniform(0, span)
        out.append((a, a + rng.uniform(0, 20)))
    return out


def _covered(ranges, t):
    return any(a <= t < b for a, b in ranges)


def test_range_properties():
    rng = random.Random(1234)
    for _ in range(300):
        a = _rand_ranges(rng)
        b = _rand_ranges(rng)
        m = tu.normalize(a)
        # merged ranges are sorted and disjoint
        assert all(m[i][1] < m[i + 1][0] for i in range(len(m) - 1))
        sub = tu.subtract(a, b)
        inter = tu.intersect(a, b)
        comp = tu.complement(a, 0, 130)
        for _ in range(40):
            t = rng.uniform(-5, 135)
            in_a, in_b = _covered(a, t), _covered(b, t)
            assert _covered(m, t) == in_a
            assert _covered(sub, t) == (in_a and not in_b)
            assert _covered(inter, t) == (in_a and in_b)
            if 0 <= t < 130:
                assert _covered(comp, t) == (not in_a)
        assert tu.total(sub) + tu.total(inter) == pytest.approx(tu.total(a))


def test_time_map_round_trip():
    tm = tu.TimeMap([("c1", "s", 10.0, 20.0, 1.0), ("c2", "s", 30.0, 34.0, 2.0), ("c3", "t", 0.0, 5.0, 1.0)])
    assert tm.duration == pytest.approx(10 + 2 + 5)
    assert tm.to_output("s", 15.0) == [5.0]
    assert tm.to_output("s", 32.0) == [11.0]
    span, t = tm.to_source(11.0)
    assert span.id == "c2" and t == pytest.approx(32.0)
    assert tm.to_output("s", 25.0) == []
    assert tm.map_range("s", 18.0, 31.0) == [(8.0, 10.0), (10.0, 10.5)]
    tm2 = tu.TimeMap([("a", "s", 0, 4, 1), ("b", "s", 10, 14, 1)], overlaps={"a": 0.5})
    assert tm2.duration == pytest.approx(7.5)


def test_parse_range_and_duration_spec():
    assert tu.parse_range("1:00-1:30") == (60.0, 90.0)
    with pytest.raises(UsageError):
        tu.parse_range("30-10")
    assert tu.parse_duration_spec("30d") == 30 * 86400
    assert tu.parse_duration_spec("12h") == 12 * 3600
