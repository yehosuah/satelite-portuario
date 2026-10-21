import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from puertogt.contracts import ModelManifest, RasterBundle, Scene
from puertogt.ml.change import TemporalEvidence, build_siamese_change_model, track_change
from puertogt.store import Store, json_text, sha256_file


def write_raster(path, values, *, dtype="uint8"):
    values = np.asarray(values, dtype=dtype)
    if values.ndim == 2:
        values = values[None]
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=values.shape[2],
        height=values.shape[1],
        count=values.shape[0],
        dtype=dtype,
        crs="EPSG:32615",
        transform=from_origin(300000, 1540000, 10, 10),
    ) as dst:
        dst.write(values)
    return path


def make_bundle(root, name, day, *, observable=True):
    from puertogt.preprocess import S2_BANDS

    image = write_raster(root / f"{name}.tif", np.ones((9, 8, 8)) * 1000, dtype="uint16")
    mask = write_raster(root / f"{name}-observable.tif", np.full((8, 8), observable))
    static = write_raster(root / f"{name}-static.tif", np.ones((8, 8)))
    return RasterBundle(
        observation_id=name,
        band_paths={band: image for band in S2_BANDS},
        mask_paths={"observable": mask, "static_area": static},
        crs="EPSG:32615",
        transform=tuple(from_origin(300000, 1540000, 10, 10))[:6],
        units={band: "DN_harmonized" for band in S2_BANDS},
        sensor="sentinel-2",
        product_level="L1C",
        preprocessing_version="ai2-s2-v1",
        metadata={
            "captured_at": (
                datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)
            ).isoformat(),
            "alignment_rmse_pixels": 0.1,
            "radiometric_comparable": True,
            "band_indices": {band: i + 1 for i, band in enumerate(S2_BANDS)},
        },
    )


def change_manifest(root):
    checkpoint = root / "encoder.pt"
    checkpoint.write_bytes(b"pinned encoder fixture")
    return ModelManifest(
        model_id="changes-v1",
        version="v1",
        task="change",
        sensor="sentinel-2",
        recipe_version="ai2-s2-v1",
        weights_path=str(checkpoint),
        weights_sha256=sha256_file(checkpoint),
        publication_policy={
            "status": "experimental",
            "change_threshold": 0.5,
            "min_area_m2": 1,
            "max_alignment_rmse_pixels": 0.5,
            "minimum_observable_fraction": 0.9,
        },
        metadata={
            "architecture": "satlas_swint_siamese_unet_v1",
            "encoder_checkpoint": str(checkpoint),
            "encoder_sha256": sha256_file(checkpoint),
            "encoder_code_commit": "pinned",
            "decoder_channels": 4,
        },
    )


def make_pair_dataset(root):
    root.mkdir()
    samples = []
    for index, changed in enumerate((True, False)):
        sample = {"pair_id": f"pair-{index}", "split_groups": [f"series-{index}"]}
        hashes = {}
        for role, day in (("reference", index * 20), ("target", index * 20 + 10)):
            bundle = make_bundle(root, f"{role}-{index}", day)
            value = bundle.model_dump(mode="json")
            for field in ("band_paths", "mask_paths"):
                for name, source in value[field].items():
                    path = Path(source)
                    value[field][name] = path.name
                    hashes[path.name] = sha256_file(path)
            path = root / f"{role}-{index}.json"
            path.write_text(json_text(value))
            sample[role] = {
                "bundle": path.name,
                "sha256": sha256_file(path),
                "observation_id": bundle.observation_id,
                "acquisition_key": f"take-{role}-{index}",
                "captured_at": bundle.metadata["captured_at"],
            }
        labels = np.zeros((8, 8), dtype="uint8")
        if changed:
            labels[2:6, 2:6] = 1
        path = write_raster(root / f"labels-{index}.tif", labels)
        sample["labels"] = {
            "path": path.name,
            "sha256": sha256_file(path),
            "reviewed_by": "fixture reviewer",
            "reviewed_at": "2026-01-01T00:00:00Z",
            "complete": True,
            "negative": not changed,
        }
        sample["asset_hashes"] = hashes
        samples.append(sample)
    manifest = {
        "format": "puertogt-reviewed-change-pairs-v1",
        "version": "fixture-v1",
        "split": "train",
        "quality_protocol": {"max_alignment_rmse_pixels": 0.5, "minimum_observable_fraction": 0.9},
        "samples": samples,
    }
    (root / "manifest.json").write_text(json_text(manifest))
    return manifest


def test_training_updates_decoder_from_reviewed_positive_and_negative_pairs(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    from puertogt.ml import change_workflows as workflows

    make_pair_dataset(tmp_path / "dataset")
    recipe = change_manifest(tmp_path)
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)

        class Encoder(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.layer = torch.nn.Conv2d(9, 2, 1)

            def forward(self, value):
                return [self.layer(value)]

        return build_siamese_change_model(Encoder(), [2], decoder_channels=4)

    monkeypatch.setattr(workflows, "create_satlas_change_model", factory)
    result = workflows.train_change_dataset(
        tmp_path / "dataset", recipe, tmp_path / "trained", epochs=1
    )
    assert calls[0]["encoder_sha256"] == recipe.metadata["encoder_sha256"]
    assert result.metadata["training"]["updated_parameters"] is True
    assert result.metadata["training"]["positive_pairs"] == 1
    assert result.metadata["training"]["negative_pairs"] == 1
    assert result.metadata["training"]["split_groups"] == ["series-0", "series-1"]
    assert "take-reference-0" in result.metadata["training"]["acquisition_ids"]
    assert "target-1" in result.metadata["training"]["observation_ids"]
    assert result.publication_policy["automatic_publication"] is False
    assert (tmp_path / "trained" / result.weights_path).is_file()


def test_training_rejects_related_reserved_pairs_and_tampered_evidence(tmp_path, monkeypatch):
    from puertogt.ml import change_workflows as workflows

    manifest = make_pair_dataset(tmp_path / "dataset")
    recipe = change_manifest(tmp_path)
    monkeypatch.setattr(
        workflows,
        "create_satlas_change_model",
        lambda **kwargs: pytest.fail("must validate before loading ML"),
    )
    reserved = tmp_path / "reserved.json"
    reserved.write_text(json_text({**manifest, "split": "test"}))
    with pytest.raises(ValueError, match="related|leakage"):
        workflows.train_change_dataset(
            tmp_path / "dataset", recipe, tmp_path / "out", reserved_manifests=[reserved]
        )
    with (tmp_path / "dataset" / "target-0.tif").open("ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        workflows.train_change_dataset(tmp_path / "dataset", recipe, tmp_path / "out")


def test_training_rejects_missing_negative_examples_before_loading_encoder(tmp_path, monkeypatch):
    from puertogt.ml import change_workflows as workflows

    manifest = make_pair_dataset(tmp_path / "dataset")
    manifest["samples"] = manifest["samples"][:1]
    (tmp_path / "dataset" / "manifest.json").write_text(json_text(manifest))
    monkeypatch.setattr(
        workflows,
        "create_satlas_change_model",
        lambda **kwargs: pytest.fail("no encoder without negatives"),
    )
    with pytest.raises(ValueError, match="negative pairs"):
        workflows.train_change_dataset(
            tmp_path / "dataset", change_manifest(tmp_path), tmp_path / "out"
        )


def registered_series(tmp_path):
    store = Store(tmp_path / "volume" / "catalog.sqlite")
    bundles = [
        make_bundle(tmp_path, name, day, observable=visible)
        for name, day, visible in (
            ("t0", 0, True),
            ("t1", 10, True),
            ("cloudy", 20, False),
            ("t2", 30, True),
            ("t3", 40, True),
        )
    ]
    for bundle in bundles:
        name = bundle.observation_id
        scene = Scene(
            scene_id=name,
            acquisition_key=f"take-{name}",
            sensor="sentinel-2",
            product_level="L1C",
            captured_at=bundle.metadata["captured_at"],
            footprint={"type": "Point", "coordinates": [-94.85, 13.9]},
        )
        store.register_scene(scene)
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO observations(observation_id,acquisition_key,zone_id,recipe_version,port_id,sensor,captured_at,status,observable_fraction,detection_count) VALUES (?,?,?,'recipe','prq','sentinel-2',?,'usable',1,0)",
                (name, scene.acquisition_key, "prq_muelles", bundle.metadata["captured_at"]),
            )
    return store, bundles


def candidate_from_bundles(reference, target, model):
    from pyproj import Transformer
    from shapely.geometry import box, mapping
    from shapely.ops import transform

    geometry = mapping(
        transform(
            Transformer.from_crs(reference.crs, "EPSG:4326", always_xy=True).transform,
            box(300020, 1539940, 300060, 1539980),
        )
    )
    return [
        track_change(
            TemporalEvidence(
                reference.observation_id,
                datetime.fromisoformat(reference.metadata["captured_at"]),
                False,
            ),
            [
                TemporalEvidence(
                    target.observation_id,
                    datetime.fromisoformat(target.metadata["captured_at"]),
                    True,
                )
            ],
            geometry=geometry,
            model_id=model.model_id,
        )
    ]


def test_compare_registers_both_evidences_idempotently_and_checks_capture_identity(
    tmp_path, monkeypatch
):
    from puertogt.export import export_bundle, restore_bundle
    from puertogt.ml import change_workflows as workflows

    store, bundles = registered_series(tmp_path)
    model = change_manifest(tmp_path)
    monkeypatch.setattr(workflows, "compare", candidate_from_bundles)
    event = workflows.compare_and_register(store, bundles[0], bundles[1], model)[0]
    assert len(event.evidence_asset_ids) >= 2
    again = workflows.compare_and_register(store, bundles[0], bundles[1], model)[0]
    assert event.event_id == again.event_id
    with store.connect() as tx:
        assert tx.execute("SELECT count(*) FROM changes").fetchone()[0] == 1
        parents = {row[0] for row in tx.execute("SELECT observation_id FROM assets")}
        masks = {
            name
            for row in tx.execute("SELECT metadata_json FROM assets")
            for name in json.loads(row[0])["bundle"]["masks"]
        }
    assert parents == {"t0", "t1"}
    assert masks == {"observable", "static_area"}
    export_bundle(store, tmp_path / "portable")
    restored = restore_bundle(tmp_path / "portable", tmp_path / "restored")
    assert all(restored.asset_path(identifier).is_file() for identifier in event.evidence_asset_ids)
    forged = bundles[1].model_copy(deep=True)
    forged.metadata["captured_at"] = "2025-02-01T00:00:00Z"
    with pytest.raises(ValueError, match="timestamp|capture"):
        workflows.compare_and_register(store, bundles[0], forged, model)


def test_temporal_updates_ignore_clouds_preserve_history_and_never_revive_rejection(
    tmp_path, monkeypatch
):
    from puertogt.ml import change_workflows as workflows
    from puertogt.review import record_review

    store, bundles = registered_series(tmp_path)
    model = change_manifest(tmp_path)
    monkeypatch.setattr(workflows, "compare", candidate_from_bundles)
    event = workflows.compare_and_register(store, bundles[0], bundles[1], model)[0]
    cloudy = workflows.record_temporal_evidence(
        store, event.event_id, bundles[2], changed=True, author="reviewer"
    )
    assert cloudy.status == "candidate"
    assert cloudy.history[-1]["valid"] is False
    persistent = workflows.record_temporal_evidence(
        store, event.event_id, bundles[3], changed=True, author="reviewer"
    )
    assert persistent.status == "persistent"
    assert persistent.confirming_observation_id == "t2"
    with pytest.raises(ValueError, match="distinct later"):
        workflows.record_temporal_evidence(
            store, event.event_id, bundles[3], changed=True, author="reviewer"
        )
    reverted = workflows.record_temporal_evidence(
        store, event.event_id, bundles[4], changed=False, author="reviewer"
    )
    assert reverted.status == "reverted"
    assert reverted.confirming_observation_id == "t2"
    assert len(reverted.history) >= 4
    with store.connect() as tx:
        latest = tx.execute(
            "SELECT revision_id FROM revisions ORDER BY rowid DESC LIMIT 1"
        ).fetchone()[0]
    record_review(
        store,
        event.event_id,
        {"review_status": "rejected", "status": "rejected"},
        "human",
        entity_type="change",
        previous_revision_id=latest,
    )
    with pytest.raises(ValueError, match="rejected"):
        workflows.record_temporal_evidence(
            store, event.event_id, bundles[4], changed=True, author="reviewer"
        )
    repeated = workflows.compare_and_register(store, bundles[0], bundles[1], model)[0]
    assert repeated.status == "rejected"
