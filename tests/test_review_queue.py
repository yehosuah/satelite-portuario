import json
import sqlite3
from uuid import uuid4

import pytest

from puertogt.review_queue import (
    blind_asset,
    blind_entry,
    build_review_queue,
    freeze_queue,
    read_queue,
)
from puertogt.store import Store, sha256_file


class GuardedStore(Store):
    def connect(self):
        tx = super().connect()

        def guard(action, table, column, *rest):
            if action == sqlite3.SQLITE_READ and (
                table == "detections" or column == "detection_count"
            ):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        tx.set_authorizer(guard)
        return tx


@pytest.fixture
def fixture(tmp_path):
    store = Store(tmp_path / "catalog.sqlite", tmp_path)
    store.initialize()
    slots = []
    for index, (date, tile) in enumerate(
        [
            ("20250105", "T16PCC"),
            ("20250105", "T16PCD"),
            ("20250205", "T16PCC"),
            ("20251005", "T16PCC"),
        ]
    ):
        key = f"S2B:MSIL1C:{date}T160000:R140:{tile}"
        split = "calibration" if index == 3 else "train"
        slots.append(
            {
                "split": split,
                "port_id": "pbr",
                "sensor": "sentinel-2",
                "selected": {"acquisition_key": key, "captured_at": date, "context": None},
                "reserves": [],
            }
        )
        with store.connect() as tx:
            tx.execute(
                "INSERT INTO acquisitions(acquisition_key,scene_id,sensor,product_level,captured_at,discovered_at,data_json) VALUES (?,?,?,?,?,?,?)",
                (
                    key,
                    str(index),
                    "sentinel-2",
                    "L1C",
                    f"{date[:4]}-{date[4:6]}-{date[6:]}T16:00:00.000000Z",
                    "2026-01-01T00:00:00Z",
                    "{}",
                ),
            )
            tx.execute(
                "INSERT INTO observations(observation_id,acquisition_key,zone_id,recipe_version,port_id,sensor,captured_at,status,observable_fraction,data_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    str(index),
                    key,
                    "pbr_fondeo",
                    "v1",
                    "pbr",
                    "sentinel-2",
                    f"{date[:4]}-{date[4:6]}-{date[6:]}T16:00:00.000000Z",
                    "usable",
                    0.9,
                    json.dumps({"note": "secret labels", "reviewed_area": {"secret": True}}),
                ),
            )
            for role, name, mime in [
                ("preview", "preview.png", "image/png"),
                ("numeric", "B04.tif", "image/tiff"),
                ("mask", "observable.tif", "image/tiff"),
            ]:
                path = tmp_path / str(index) / name
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(b"raw")
                tx.execute(
                    "INSERT INTO assets(asset_id,observation_id,path,sha256,size_bytes,mime_type,crs,bounds_json,created_at,metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        f"{index}-{role}",
                        str(index),
                        str(path.relative_to(tmp_path)),
                        sha256_file(path),
                        3,
                        mime,
                        "EPSG:4326",
                        "[0,0,1,1]",
                        "now",
                        json.dumps(
                            {
                                "role": role,
                                "display_only": True,
                                "wgs84_bounds": [0, 0, 1, 1],
                                "score": 0.99,
                            }
                        ),
                    ),
                )
    sampling = {
        "windows": {
            "train": {"start": "2025-01-01", "end_exclusive": "2025-07-01", "anchor_days": [1, 16]},
            "calibration": {"start": "2025-10-01", "end_exclusive": "2026-01-01"},
            "test": {"start": "2026-04-02"},
        },
        "slots": slots,
    }
    paths = [tmp_path / name for name in ("sampling.json", "order.json", "ports.json")]
    for path, data in zip(
        paths,
        [
            sampling,
            {"activated_reserves": []},
            {
                "features": [
                    {
                        "properties": {"zone_id": "pbr_fondeo", "port_id": "pbr"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
                        },
                    }
                ]
            },
        ],
    ):
        path.write_text(json.dumps(data))
    store.__class__ = GuardedStore
    return store, paths


def test_deterministic_blind_selection_deduplicates_passes_and_retains_deficits(fixture, tmp_path):
    store, paths = fixture
    first = build_review_queue(store, *paths)
    assert first == build_review_queue(store, *paths)
    assert [r["observation_id"] for r in first["entries"]] == ["0", "2"]
    assert first["distribution"]["pbr|sentinel-2"]["deficit"] == 0
    assert first["distribution"]["stc|sentinel-1"]["deficit"] == 2
    exposed = json.dumps(blind_entry(first, "0"))
    assert all(
        term not in exposed
        for term in ["secret", "reviewed_area", "score", "model_id", "detection_count"]
    )
    path = tmp_path / "queue.json"
    sha = freeze_queue(first, path)
    assert read_queue(path, sha) == first
    with pytest.raises(FileExistsError):
        freeze_queue(first, path)
    assert blind_asset(store, first, "0", "0-preview").read_bytes() == b"raw"
    with pytest.raises(KeyError):
        blind_asset(store, first, "0", "0-numeric")
    (store.data_dir / "0/preview.png").write_bytes(b"changed")
    with pytest.raises(ValueError):
        blind_asset(store, first, "0", "0-preview")


def test_reserved_context_and_reviewed_recipe_cannot_reenter(fixture):
    store, paths = fixture
    with store.connect() as tx:
        tx.set_authorizer(None)
        tx.execute(
            "UPDATE observations SET review_status='reviewed',data_json=? WHERE observation_id='0'",
            (json.dumps({"scene_complete": True}),),
        )
        # A reserved observation protects a related TRAIN group despite its date.
        key = tx.execute(
            "SELECT acquisition_key FROM observations WHERE observation_id='2'"
        ).fetchone()[0]
        tx.execute(
            "UPDATE observations SET data_json=? WHERE observation_id='3'",
            (json.dumps({"dataset_split": "test", "split_groups": [key]}),),
        )
    queue = build_review_queue(store, *paths)
    assert queue["entries"] == []
    reasons = {r["observation_id"]: r["reason"] for r in queue["exclusions"]}
    assert reasons["1"] == "previous_observation_review_or_abstention"
    assert reasons["2"] == "reserved_split_or_context"


def test_changed_queue_and_embargo_fail_closed(fixture, tmp_path):
    store, paths = fixture
    queue = build_review_queue(store, *paths)
    path = tmp_path / "q.json"
    freeze_queue(queue, path)
    envelope = json.loads(path.read_text())
    envelope["manifest"]["split"] = "test"
    path.write_text(json.dumps(envelope))
    with pytest.raises(ValueError):
        read_queue(path)
    plan = json.loads(paths[0].read_text())
    plan["windows"]["calibration"]["start"] = "2025-08-01"
    paths[0].write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="embargo"):
        build_review_queue(store, *paths)


def test_inactive_reserve_and_missing_evidence_stay_out(fixture):
    store, paths = fixture
    plan = json.loads(paths[0].read_text())
    removed = plan["slots"].pop(2)
    plan["slots"][0]["reserves"].append(removed["selected"])
    paths[0].write_text(json.dumps(plan))
    (store.data_dir / "0/observable.tif").unlink()
    (store.data_dir / "1/observable.tif").unlink()
    queue = build_review_queue(store, *paths)
    assert queue["entries"] == []
    reasons = {r["observation_id"]: r["reason"] for r in queue["exclusions"]}
    assert reasons["2"] == "outside_activated_train_sampling"
    assert reasons["0"] == "unavailable_or_changed_evidence"


def test_test_embargo_and_radar_context_fail_closed(fixture):
    store, paths = fixture
    plan = json.loads(paths[0].read_text())
    plan["windows"]["test"]["start"] = "2026-03-01"
    paths[0].write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="embargo"):
        build_review_queue(store, *paths)
    plan["windows"]["test"]["start"] = "2026-04-02"
    plan["slots"][0]["sensor"] = "sentinel-1"
    paths[0].write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="planned context"):
        build_review_queue(store, *paths)


def test_radar_segments_in_same_orbit_are_one_physical_visit():
    from puertogt.review_queue import groups, physical_pass

    first = "S1A:IW:GRDH:1SDV:20250102T001418:20250102T001443:057258:070B29"
    second = "S1A:IW:GRDH:1SDV:20250102T001443:20250102T001508:057258:070B30"
    assert physical_pass(first) == physical_pass(second)
    assert physical_pass(first) in groups(first) & groups(second)


@pytest.mark.parametrize("crs,bounds", [
    (None, "[0,0,1,1]"), ("EPSG:4326", None), ("invalid", "[0,0,1,1]"),
    ("EPSG:4326", "[0,0,0,1]"), ("EPSG:4326", "[0,0,1,NaN]"),
    ("EPSG:4326", "[0,0,1]"), ("EPSG:4326", "{}"),
])
def test_ungeoreferenced_preview_is_excluded_before_freezing(fixture, crs, bounds):
    store, paths = fixture
    with store.connect() as tx:
        tx.execute("UPDATE assets SET crs=?,bounds_json=? WHERE asset_id='2-preview'", (crs, bounds))
    queue = build_review_queue(store, *paths)
    assert all(row["observation_id"] != "2" for row in queue["entries"])
    assert {r["observation_id"]: r["reason"] for r in queue["exclusions"]}["2"] == "missing_or_invalid_preview_georeference"


def test_first_pass_abstention_excludes_the_pass_zone_and_corruption_fails_closed(fixture):
    from puertogt.review_intake import record_first_pass

    store, paths = fixture
    original = build_review_queue(store, *paths)
    inspection_id = str(uuid4())
    record_first_pass(store, original, {
        "queue_id": original["queue_id"], "inspection_id": inspection_id,
        "observation_id": "0", "author": "AI fixture", "reviewer_kind": "ai_visual_review",
        "decision": "uncertain", "candidates": [], "reviewed_area": None,
        "note": "secret first-pass uncertainty", "elapsed_seconds": 1,
    })
    queue = build_review_queue(store, *paths)
    assert [row["observation_id"] for row in queue["entries"]] == ["2"]
    reasons = {row["observation_id"]: row["reason"] for row in queue["exclusions"]}
    assert reasons["0"] == reasons["1"] == "previous_observation_review_or_abstention"
    assert "secret first-pass uncertainty" not in json.dumps(queue)
    assert original["queue_id"] != queue["queue_id"]
    receipt = store.data_dir / "review-intake" / original["queue_id"] / f"{inspection_id}.json"
    receipt.write_text("{broken")
    with pytest.raises(ValueError, match="first-pass receipt"):
        build_review_queue(store, *paths)


def test_first_pass_identity_must_match_immutable_location(fixture):
    store, paths = fixture
    directory = store.data_dir / "review-intake" / "train-01234567890123456789"
    directory.mkdir(parents=True)
    (directory / f"{uuid4()}.json").write_text(json.dumps({
        "schema": "puertogt-first-pass-v1", "queue_id": directory.name,
        "submission": {"queue_id": directory.name, "observation_id": "0", "inspection_id": str(uuid4())},
    }))
    with pytest.raises(ValueError, match="first-pass receipt"):
        build_review_queue(store, *paths)
