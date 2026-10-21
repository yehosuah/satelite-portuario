from contextlib import closing

import pytest
from shapely.geometry import box, mapping, shape

from puertogt.contracts import ModelManifest
from puertogt.models import processing_recipe, register_portable_model, validated_scope
from puertogt.store import Store, sha256_file


def test_read_model_resolves_head_and_embedded_encoder_in_custom_volume(tmp_path, monkeypatch):
    from puertogt.cli import read_model

    volume = tmp_path / "mounted-volume"
    weights = volume / "models"
    weights.mkdir(parents=True)
    (weights / "head.pt").write_bytes(b"head")
    (weights / "encoder.pt").write_bytes(b"encoder")
    manifests = tmp_path / "recipes"
    manifests.mkdir()
    working = tmp_path / "unrelated-working-directory"
    working.mkdir()
    monkeypatch.chdir(working)
    encoder = ModelManifest(
        model_id="encoder",
        version="1",
        sensor="sentinel-2",
        task="detection",
        recipe_version="r1",
        weights_path="data/models/encoder.pt",
    )
    head = encoder.model_copy(
        update={
            "model_id": "head",
            "task": "attributes",
            "weights_path": "data/models/head.pt",
            "metadata": {"encoder_manifest": encoder.model_dump(mode="json")},
        }
    )
    path = manifests / "head.json"
    path.write_text(head.model_dump_json())

    loaded = read_model(path, volume)

    assert loaded.weights_path == str(weights / "head.pt")
    assert loaded.metadata["encoder_manifest"]["weights_path"] == str(weights / "encoder.pt")


def test_read_model_keeps_manifest_relative_precedence_and_explicit_absolute_paths(
    tmp_path, monkeypatch
):
    from puertogt.cli import read_model

    recipe_dir, working, volume = (tmp_path / name for name in ("recipes", "working", "volume"))
    for directory in (recipe_dir, working, volume):
        directory.mkdir()
        (directory / "weights.pt").write_bytes(directory.name.encode())
    monkeypatch.chdir(working)
    path = recipe_dir / "model.json"
    model = ModelManifest(
        model_id="test",
        version="1",
        sensor="sentinel-2",
        task="detection",
        recipe_version="r1",
        weights_path="weights.pt",
    )
    path.write_text(model.model_dump_json())
    assert read_model(path, volume).weights_path == str(recipe_dir / "weights.pt")

    model.weights_path = str(working / "weights.pt")
    path.write_text(model.model_dump_json())
    assert read_model(path, volume).weights_path == str(working / "weights.pt")


def test_weights_are_atomic_idempotent_and_keep_encoder_in_portable_volume(tmp_path):
    store = Store(tmp_path / "volume" / "catalog.sqlite")
    source = tmp_path / "encoder.pt"
    source.write_bytes(b"reference weights")
    encoder = ModelManifest(
        model_id="encoder",
        version="1",
        sensor="sentinel-2",
        task="detection",
        recipe_version="r1",
        weights_path=str(source),
        weights_sha256=sha256_file(source),
    )
    head = encoder.model_copy(
        update={
            "model_id": "head",
            "task": "attributes",
            "metadata": {"encoder_manifest": encoder.model_dump(mode="json")},
        }
    )
    registered = register_portable_model(store, head)
    assert register_portable_model(store, head) == registered
    source.unlink()
    assert (
        store.data_dir / registered.metadata["encoder_manifest"]["weights_path"]
    ).read_bytes() == b"reference weights"
    with closing(store.connect()) as tx:
        assert tx.execute("SELECT count(*) FROM models").fetchone()[0] == 2
    with pytest.raises(ValueError, match="immutable"):
        store.register_model(registered.model_copy(update={"version": "changed"}))


def test_new_model_or_attribute_policy_gets_distinct_processing_identity():
    detector = ModelManifest(
        model_id="d", version="1", sensor="sentinel-2", task="detection", recipe_version="r1"
    )
    head = detector.model_copy(update={"model_id": "a", "task": "attributes"})
    assert processing_recipe(detector) != processing_recipe(
        detector.model_copy(update={"version": "2"})
    )
    assert processing_recipe(detector, head) != processing_recipe(detector)
    changed = head.model_copy(
        update={"publication_policy": {"fields": {"family": {"validated": True}}}}
    )
    assert processing_recipe(detector, head) != processing_recipe(detector, changed)


def test_validation_does_not_spread_to_unmeasured_port_or_zone():
    model = ModelManifest(
        model_id="d",
        version="1",
        sensor="sentinel-2",
        task="detection",
        recipe_version="r1",
        publication_policy={
            "status": "validated",
            "automatic_publication": True,
            "strata": {"prq|sentinel-2|prq_muelles": {"eligible": True}},
        },
    )
    assert validated_scope(model, "prq", "prq_muelles")
    assert not validated_scope(model, "stc", "stc_muelles")
    assert not validated_scope(model, "prq", "prq_fondeo")


def test_rediscovery_keeps_geometry_representations_and_prepared_metadata(tmp_path):
    store = Store(tmp_path / "catalog.sqlite")
    base = dict(
        scene_id="s",
        acquisition_key="k",
        sensor="sentinel-2",
        product_level="L1C",
        captured_at="2025-01-01T00:00:00Z",
        footprint=mapping(box(0, 0, 1, 1)),
        port_ids=["stc"],
        metadata={"representations": [{"id": "a"}]},
    )
    store.register_scene(base)
    store.register_scene(
        {
            **base,
            "footprint": mapping(box(1, 0, 2, 1)),
            "port_ids": ["pbr"],
            "metadata": {
                "representations": [{"id": "b"}],
                "prepared_bundle": {"sensor": "sentinel-2"},
            },
        }
    )
    import json

    with closing(store.connect()) as tx:
        row = tx.execute("SELECT * FROM acquisitions").fetchone()
    assert shape(json.loads(row["footprint_json"])).area == 2
    metadata = json.loads(row["metadata_json"])
    assert len(metadata["representations"]) == 2
    assert metadata["prepared_bundle"]["sensor"] == "sentinel-2"
