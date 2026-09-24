import pytest

from buildflow.cpm import CpmError, run_cpm


def net(*rows):
    return {"activities": [{"activity_id": i, "duration_days": d, "predecessors": p, **kw}
                           for i, d, p, kw in rows]}


def by(result):
    return {a["activity_id"]: a for a in result["activities"]}


def test_forward_backward_float_and_critical_path():
    r = run_cpm(net(("A", 3, [], {}), ("B", 5, ["A"], {}), ("C", 2, ["A"], {}), ("D", 1, ["B", "C"], {})))
    a = by(r)
    assert r["project_duration_days"] == 9
    assert r["critical_path"] == ["A", "B", "D"]
    assert (a["C"]["ES"], a["C"]["EF"], a["C"]["LS"], a["C"]["LF"]) == (3, 5, 6, 8)
    assert a["C"]["total_float"] == 3 and a["C"]["free_float"] == 3 and not a["C"]["critical"]
    assert a["B"]["critical"] and a["B"]["total_float"] == 0


def test_lag_delays_successor():
    r = run_cpm(net(("A", 2, [], {}), ("B", 1, ["A"], {"lags": {"A": 7}})))
    assert by(r)["B"]["ES"] == 9 and r["project_duration_days"] == 10


def test_free_float_smaller_than_total_float():
    r = run_cpm(net(("A", 1, [], {}), ("B", 1, ["A"], {}), ("C", 4, ["A"], {}),
                    ("D", 1, ["B"], {}), ("E", 1, ["C", "D"], {})))
    d = by(r)["D"]
    assert d["total_float"] == 2 and d["free_float"] == 2
    assert by(r)["B"]["free_float"] == 0 and by(r)["B"]["total_float"] == 2


def test_errors():
    with pytest.raises(CpmError, match="cycle"):
        run_cpm(net(("A", 1, ["B"], {}), ("B", 1, ["A"], {})))
    with pytest.raises(CpmError, match="unknown predecessor"):
        run_cpm(net(("A", 1, ["Z"], {})))
    with pytest.raises(CpmError, match="no duration"):
        run_cpm({"activities": [{"activity_id": "A", "duration_days": None, "predecessors": []}]})
    with pytest.raises(CpmError, match="duplicate"):
        run_cpm(net(("A", 1, [], {}), ("A", 1, [], {})))
