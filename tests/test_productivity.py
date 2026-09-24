import json

import pytest

from buildflow.productivity import attach_productivity, load_library, main


def _lib(tmp_path, extra=()):
    entry = {"activity_class": "RCC_FOOTING", "unit": "cum", "source": "DAR",
             "match_quality": "exact", "coefficients_days_per_unit": {"beldar": 2.0},
             "default_gang": {"beldar": 12}, "output_per_gang_day": 6.0, "note": ""}
    path = tmp_path / "lib.json"
    path.write_text(json.dumps({"items": [entry, *extra]}))
    return path


def test_matches_class_and_attaches_block(tmp_path):
    lib = load_library(_lib(tmp_path))
    data = {"project": "x", "activities": [
        {"activity_id": "A03", "activity_class": "rcc_footing", "unit": "Cum",
         "quantity": 150, "predecessors": ["A02"]}]}
    out, report = attach_productivity(data, lib)
    act = out["activities"][0]
    assert act["productivity"]["output_per_gang_day"] == 6.0
    assert act["predecessors"] == ["A02"] and out["project"] == "x"
    assert act["derived"] == {"resource_days": {"beldar": 300.0},
                              "limiting_resource": "beldar"}
    assert report["matched"] == 1 and not report["missing"]
    assert "productivity" not in data["activities"][0]  # input untouched


def test_unknown_class_and_unit_mismatch_are_reported_not_guessed(tmp_path):
    lib = load_library(_lib(tmp_path))
    out, report = attach_productivity([
        {"activity_id": "A01", "activity_class": "WELDING", "unit": "m"},
        {"activity_id": "A02", "activity_class": "RCC_FOOTING", "unit": "sqm"}], lib)
    assert out[0]["productivity"] is None
    assert report["missing"] == [{"activity_id": "A01", "activity_class": "WELDING"}]
    assert report["unit_mismatch"][0]["activity_id"] == "A02"


def test_duplicate_library_class_rejected(tmp_path):
    dup = {"activity_class": "RCC_FOOTING", "unit": "cum"}
    with pytest.raises(ValueError):
        load_library(_lib(tmp_path, extra=[dup]))


def test_cli_strict_exit_code(tmp_path):
    src, dst = tmp_path / "in.json", tmp_path / "out.json"
    src.write_text(json.dumps([{"activity_id": "A1", "activity_class": "NOPE", "unit": "m"}]))
    assert main([str(src), str(dst), "--library", str(_lib(tmp_path))]) == 0
    assert main([str(src), str(dst), "--library", str(_lib(tmp_path)), "--strict"]) == 1


def test_fixed_days_activity_is_not_reported_missing(tmp_path):
    lib = load_library(_lib(tmp_path))
    out, report = attach_productivity([{"activity_id": "CUR", "activity_class": "CURING", "fixed_days": 7}], lib)
    assert out[0]["productivity"] is None and report["missing"] == []
