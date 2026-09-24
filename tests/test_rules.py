import copy
import json
from pathlib import Path

import pytest

from buildflow.pipeline import run_pipeline
from buildflow.rules import RulesError, apply_rules, load_rules, validate_rules

ROOT = Path(__file__).resolve().parent.parent
RULES = load_rules("RAIN_WATER_HARVESTING")


def items(*classes):
    return [{"activity_id": f"X{i}", "activity_class": c, "quantity": 10, "unit": "cum", "rate": 100,
             "description": c.lower()} for i, c in enumerate(classes)]


def acts(result):
    return result if isinstance(result, list) else result["activities"]   # bare list in, bare list out


def by_id(result):
    return {a["activity_id"]: a for a in acts(result)}


def test_two_tank_network_from_the_real_rwh_items():
    data = json.loads((ROOT / "data" / "rwh_normalized_items.json").read_text())
    result, report = apply_rules(data, RULES, structures=2)
    a = by_id(result)
    assert report["activities"] == 40 and report["synthetic_added"] == ["MOB", "T1_CUR", "T2_CUR", "TEST"]
    assert a["T1_A01"]["predecessors"] == ["MOB"]
    assert set(a["T2_A01"]["predecessors"]) == {"MOB", "T1_A02"}            # documented: T2 excavation follows T1 PCC
    assert set(a["T2_A05"]["predecessors"]) == {"T2_A03", "T2_A04", "T1_A05"}  # shared RCC crew
    assert a["T1_A07"]["predecessors"] == ["T1_CUR"] and a["T1_CUR"]["predecessors"] == ["T1_A06"]
    assert a["T1_A11"]["predecessors"] == ["MOB"]                              # bore well: mobilisation only
    assert a["T2_A11"]["predecessors"] == ["MOB", "T1_A11"]                    # one rig
    assert a["T1_A16"]["predecessors"] == ["T1_A12"]
    assert set(a["TEST"]["predecessors"]) == {"T1_A10", "T2_A10", "T1_A14", "T2_A14", "T1_A16", "T2_A16",
                                              "T1_A15", "T2_A15", "T1_A17", "T2_A17", "T1_A18", "T2_A18"}
    assert all("predecessors" not in i for i in data["activities"])           # input untouched


def test_single_structure_keeps_original_ids():
    data = json.loads((ROOT / "data" / "rwh_normalized_items.json").read_text())
    result, report = apply_rules(data, RULES)
    ids = {x["activity_id"] for x in result["activities"]}
    assert {"A01", "A11", "MOB", "CUR", "TEST"} <= ids and not any(i.startswith("T1_") for i in ids)


def test_absent_classes_are_bypassed():
    result, report = apply_rules(items("EXCAVATION_MECHANICAL", "PCC_LEAN", "RCC_FOOTING", "BACKFILLING"), RULES)
    a = {x["activity_class"]: x for x in acts(result)}
    assert a["RCC_FOOTING"]["predecessors"] == [a["PCC_LEAN"]["activity_id"]]      # steel and formwork absent
    assert a["BACKFILLING"]["predecessors"] == [a["CURING_DESHUTTERING"]["activity_id"]]  # curing follows the footing
    assert a["CURING_DESHUTTERING"]["predecessors"] == [a["RCC_FOOTING"]["activity_id"]]    # superstructure absent -> bypassed
    assert "BOREWELL_DRILLING" in report["classes_bypassed"]


def test_curing_only_added_when_rcc_present():
    result, _ = apply_rules(items("EXCAVATION_MECHANICAL", "PCC_LEAN", "BOREWELL_DRILLING"), RULES)
    assert "CURING_DESHUTTERING" not in {a["activity_class"] for a in acts(result)}


def test_unruled_class_is_scheduled_and_reported():
    result, report = apply_rules(items("EXCAVATION_MECHANICAL", "SOLAR_PANEL_INSTALL"), RULES)
    unruled = [a for a in acts(result) if a.get("rule_status") == "unruled"]
    assert report["unruled"][0]["activity_class"] == "SOLAR_PANEL_INSTALL"
    assert unruled[0]["predecessors"] == ["MOB"]
    test = [a for a in acts(result) if a["activity_class"] == "TESTING_HANDOVER"][0]
    assert unruled[0]["activity_id"] in test["predecessors"]


def test_quantity_basis_project_total_divides_across_structures():
    it = items("EXCAVATION_MECHANICAL")
    per, _ = apply_rules(it, RULES, structures=2)
    tot, _ = apply_rules(it, RULES, structures=2, quantity_basis="project_total")
    assert all(a["quantity"] == 10 for a in acts(per) if a["activity_class"] == "EXCAVATION_MECHANICAL")
    assert all(a["quantity"] == 5 for a in acts(tot) if a["activity_class"] == "EXCAVATION_MECHANICAL")


def test_class_names_are_normalised_and_duplicate_ids_rejected():
    result, _ = apply_rules([{"activity_id": "1", "activity_class": "pcc lean", "quantity": 1}], RULES)
    assert any(a["activity_class"] == "PCC_LEAN" for a in acts(result))
    with pytest.raises(RulesError, match="unique"):
        apply_rules([{"activity_id": "1", "activity_class": "PCC_LEAN"}, {"activity_id": "1", "activity_class": "PCC_LEAN"}], RULES)


def test_rule_file_validation():
    bad = copy.deepcopy(RULES)
    bad["classes"]["PCC_LEAN"]["after"] = ["NOT_A_CLASS"]
    with pytest.raises(RulesError, match="unknown class"):
        validate_rules(bad)
    cyc = copy.deepcopy(RULES)
    cyc["classes"]["EXCAVATION_MECHANICAL"]["after"] = ["PCC_LEAN"]
    with pytest.raises(RulesError, match="cycle"):
        validate_rules(cyc)
    with pytest.raises(RulesError, match="no rule file"):
        load_rules("SPACE_ELEVATOR")


def test_rules_output_runs_through_the_whole_pipeline():
    data = json.loads((ROOT / "data" / "rwh_normalized_items.json").read_text())
    applied, _ = apply_rules(data, RULES, structures=2)
    result, _ = run_pipeline(applied, "2026-04-25",
                             overrides=json.loads((ROOT / "data" / "rwh_gang_overrides.json").read_text()))
    assert result["project_start_date"] == "2026-04-25" and len(result["activities"]) == 40
    assert result["project_duration_days"] > 0 and result["critical_path"][0] == "MOB"


def test_pipeline_can_start_from_normalized_items():
    data = json.loads((ROOT / "data" / "rwh_normalized_items.json").read_text())
    result, rep = run_pipeline(data, "2026-04-25", use_rules=True, structures=2,
                               overrides=json.loads((ROOT / "data" / "rwh_gang_overrides.json").read_text()))
    assert rep["rules"]["activities"] == 40 and result["project_duration_days"] == 75
