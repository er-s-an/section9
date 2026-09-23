import json
from hashlib import sha256
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_parity_catalog_preserves_source_records_and_ids():
    source_path = ROOT / "docs/parity/source/Section9_功能对标登记表.json"
    catalog_path = ROOT / "docs/parity/catalog.yaml"
    source_bytes = source_path.read_bytes()
    source = json.loads(source_bytes)
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))

    assert catalog["registry_import"]["sha256"] == sha256(source_bytes).hexdigest()
    assert catalog["registry_import"]["source_copy_is_byte_identical"] is True
    assert len(catalog["features"]) == 36
    assert len(catalog["connectors"]) == 83
    assert len(catalog["supplemental_integration_tasks"]) == 4
    assert [item["id"] for item in catalog["features"]] == [item["id"] for item in source["features"]]
    assert [item["id"] for item in catalog["connectors"]] == [item["id"] for item in source["connectors"]]
    assert all("id" not in item for item in catalog["supplemental_integration_tasks"])

    allowed_statuses = {"discovered", "specified", "implemented", "verified", "blocked", "not_supported"}
    for group in ("features", "connectors"):
        imported = [{key: value for key, value in item.items() if key != "tracking"}
                    for item in catalog[group]]
        assert imported == source[group]
        assert all(item["tracking"]["current_status"] in allowed_statuses for item in catalog[group])

    feature_status = {item["id"]: item["tracking"]["current_status"] for item in catalog["features"]}
    assert {key for key, value in feature_status.items() if value == "implemented"} == {
        "CAP-02", "CAP-03", "CAP-15", "RES-01", "RES-02", "RES-03",
    }
    assert not any(item["verification_status"] == "verified" for item in catalog["features"])
    connector_status = {item["id"]: item["tracking"]["current_status"] for item in catalog["connectors"]}
    assert {key for key, value in connector_status.items() if value == "implemented"} == {"CONN-056", "CONN-059"}
    assert all(item["tracking"].get("evidence") for item in catalog["features"]
               if item["tracking"]["current_status"] == "implemented")

    source_keys = set(source)
    catalog_source = {key: value for key, value in catalog.items()
                      if key not in {"registry_import", "implementation_progress"}}
    for group in ("features", "connectors"):
        catalog_source[group] = [{key: value for key, value in item.items() if key != "tracking"}
                                 for item in catalog[group]]
    assert set(catalog_source) == source_keys
    assert catalog_source == source
