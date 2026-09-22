import json

from s9.product.lifecycle import ProductBackup
from s9.product.registry import ProductRegistry


def test_product_backup_round_trip_preserves_database_and_artifacts(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    database = live / "product.sqlite"
    artifacts = live / "product-artifacts"
    registry = ProductRegistry(database)
    registry.put("evidence", "e1", {"value": "kept"}, project_id="p", environment_id="dev", incident_id="i")
    (artifacts / "incident").mkdir(parents=True)
    (artifacts / "incident" / "evidence.json").write_text('{"status":"ready"}\n', encoding="utf-8")
    backup = ProductBackup(database, artifacts)
    created = backup.create(tmp_path / "backup")
    assert created["manifest"]["schema_version"] == "product-backup-v1"
    restored = ProductBackup.restore_into(tmp_path / "backup", tmp_path / "restored")
    assert restored["target_dir"].endswith("/restored")
    restored_registry = ProductRegistry(tmp_path / "restored" / "product.sqlite")
    assert restored_registry.get("evidence", "e1", project_id="p", environment_id="dev", incident_id="i")["value"] == "kept"
    assert json.loads((tmp_path / "restored" / "product-artifacts" / "incident" / "evidence.json").read_text())["status"] == "ready"


def test_product_backup_includes_separate_external_project_database(tmp_path):
    import sqlite3

    live = tmp_path / "live"
    live.mkdir()
    registry_path = live / "product.sqlite"
    external = tmp_path / "candidate.sqlite"
    artifacts = live / "product-artifacts"
    registry = ProductRegistry(registry_path)
    registry.put("incident", "i1", {"title": "fixture"}, project_id="p", environment_id="dev")
    with sqlite3.connect(external) as db:
        db.execute("create table records (value text)")
        db.execute("insert into records values ('candidate')")

    backup = ProductBackup(registry_path, artifacts, {"support-agent": external})
    created = backup.create(tmp_path / "backup-with-candidate")
    assert {item["name"] for item in created["manifest"]["databases"]} == {"product-registry", "support-agent"}
    ProductBackup.restore_into(tmp_path / "backup-with-candidate", tmp_path / "restored-with-candidate")
    with sqlite3.connect(tmp_path / "restored-with-candidate" / "databases" / "support-agent.sqlite") as db:
        assert db.execute("select value from records").fetchone()[0] == "candidate"


def test_product_backup_rejects_artifact_symlink_to_outside_root(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    database = live / "product.sqlite"
    ProductRegistry(database)
    artifacts = live / "product-artifacts"
    artifacts.mkdir(exist_ok=True)
    outside = tmp_path / "outside.json"
    outside.write_text('{"outside":true}', encoding="utf-8")
    (artifacts / "linked.json").symlink_to(outside)

    try:
        ProductBackup(database, artifacts).create(tmp_path / "backup")
    except ValueError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("artifact symlink was followed")


def test_product_restore_rejects_symlinked_backup_file(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    database = live / "product.sqlite"
    ProductRegistry(database)
    backup_dir = tmp_path / "backup"
    ProductBackup(database, live / "product-artifacts").create(backup_dir)
    external = tmp_path / "outside.json"
    external.write_text("outside", encoding="utf-8")
    (backup_dir / "product.sqlite").unlink()
    (backup_dir / "product.sqlite").symlink_to(external)

    try:
        ProductBackup.restore_into(backup_dir, tmp_path / "restored")
    except ValueError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("restore followed a backup symlink")
