import json
from contextlib import closing

import pytest

from puertogt.contracts import ModelManifest
from puertogt.models import register_portable_model
from puertogt.store import Store, sha256_file


def test_exported_training_registers_beside_public_base_without_collision(tmp_path):
    torch = pytest.importorskip("torch")
    from puertogt.ml.training import export_training_artifact

    network = torch.nn.Linear(2, 1)
    public_path = tmp_path / "public.pt"
    torch.save({"state_dict": network.state_dict()}, public_path)
    base = ModelManifest(model_id="public-detector", version="v1", sensor="sentinel-2",
                         task="detection", recipe_version="r1", weights_path=str(public_path),
                         weights_sha256=sha256_file(public_path), metrics={"fixture": 1},
                         publication_policy={"status": "validated", "automatic_publication": True})
    store = Store(tmp_path / "volume/catalog.sqlite")
    original = register_portable_model(store, base)
    with torch.no_grad():
        network.weight.add_(1)
    destination = tmp_path / "trained"
    trained = export_training_artifact(network, base, destination,
                                       training={"epochs": 1}, dataset_version="train-v1")
    registered = register_portable_model(store, trained.model_copy(update={
        "weights_path": str(destination / trained.weights_path)}))
    assert registered.model_id != original.model_id
    assert registered.version != original.version
    assert registered.metrics == {}
    assert registered.publication_policy["status"] == "experimental"
    assert registered.publication_policy["automatic_publication"] is False
    assert registered.metadata["parent_model"] == {
        "model_id": base.model_id, "version": base.version, "weights_sha256": base.weights_sha256}
    with closing(store.connect()) as tx:
        rows = tx.execute("SELECT model_id,sha256 FROM models ORDER BY model_id").fetchall()
    assert {row["model_id"]: row["sha256"] for row in rows} == {
        base.model_id: base.weights_sha256, registered.model_id: trained.weights_sha256}
    assert register_portable_model(store, base) == original


def test_distinct_training_exports_get_distinct_ids_even_with_identical_parameters(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    from puertogt.ml.training import export_training_artifact

    monkeypatch.setattr("puertogt.ml.training.utc_text", lambda: "2026-01-01T00:00:00Z")
    model = torch.nn.Linear(1, 1)
    base = ModelManifest(model_id="public", version="v1", sensor="sentinel-2",
                         task="attributes", recipe_version="r1")
    first = export_training_artifact(model, base, tmp_path / "one", training={}, dataset_version="v1")
    second = export_training_artifact(model, base, tmp_path / "two", training={}, dataset_version="v1")
    assert first.model_id != second.model_id
    assert first.version != second.version
    assert base.model_id == "public"


@pytest.mark.parametrize("model_context,cache_context", [
    (None, {"pixels": 64}), ({"pixels": 64}, None), ({"pixels": 64}, {"pixels": 128}),
])
def test_attribute_training_rejects_different_context_before_reading_features(
        tmp_path, model_context, cache_context):
    from puertogt.ml.training import train_attribute_dataset

    labels = tmp_path / "labels.json"
    labels.write_text('{"features": []}')
    manifest = {"version": "v1", "split": "train", "samples": [{
        "observation_id": "one", "labels": labels.name, "sha256": sha256_file(labels),
        "feature_cache": {"path": "must-not-be-opened.npz", "sha256": "b" * 64,
                          "encoder_sha256": "a" * 64, "pooling": "roi_mean", "feature_level": 0,
                          "context_policy": cache_context}}]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    base = ModelManifest(model_id="attributes", version="v1", sensor="sentinel-2", task="attributes",
                         recipe_version="r1", metadata={"feature_dim": 4, "feature_context": model_context,
                         "encoder_manifest": {"weights_sha256": "a" * 64}})
    with pytest.raises(ValueError, match="context"):
        train_attribute_dataset(tmp_path, base, tmp_path / "trained")
    assert not (tmp_path / "trained").exists()
