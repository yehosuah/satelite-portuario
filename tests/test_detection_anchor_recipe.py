import sys
from types import SimpleNamespace

import pytest

from puertogt.contracts import ModelManifest
from puertogt.ml.detection import detector_anchor_sizes, load_detector
from puertogt.store import sha256_file


def manifest(**updates):
    return ModelManifest(model_id="anchor-fixture", version="1", sensor="sentinel-2",
                         task="detection", recipe_version="ai2-s2-v1", **updates)


def test_legacy_recipe_unchanged_and_custom_geometry_explicit():
    original = manifest()
    assert detector_anchor_sizes(original) == [[32], [64], [128], [256]]
    assert original.metadata == {}
    configured = manifest(metadata={"anchor_sizes": [[8], [16], [32], [64]]})
    assert detector_anchor_sizes(configured) == [[8], [16], [32], [64]]
    result = detector_anchor_sizes(configured)
    result[0][0] = 99
    assert configured.metadata["anchor_sizes"][0] == [8]


@pytest.mark.parametrize("sizes", [None, [8, 16, 32, 64], [[8, 16], [16], [32], [64]],
                                  [[8], [16], [32]], [[8.0], [16], [32], [64]],
                                  [[True], [16], [32], [64]], [[0], [16], [32], [64]],
                                  [[16], [8], [32], [64]]])
def test_incompatible_or_ambiguous_anchor_recipes_fail_closed(sizes):
    with pytest.raises(ValueError, match="Anchor recipe"):
        detector_anchor_sizes(manifest(metadata={"anchor_sizes": sizes}))


def test_exported_train_recipe_reaches_inference_constructor_unchanged(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    from puertogt.cli import read_model
    from puertogt.ml.training import export_training_artifact

    configured = manifest(code_commit="fixture", metadata={"anchor_sizes": [[8], [16], [32], [64]]})
    trained = export_training_artifact(torch.nn.Linear(1, 1), configured, tmp_path / "trained",
                                       training={"scope": "train fixture"}, dataset_version="train-only")
    trained = read_model(tmp_path / "trained/manifest.json", tmp_path)
    assert trained.metadata["anchor_sizes"] == configured.metadata["anchor_sizes"]
    assert sha256_file(tmp_path / "trained/weights.pt") == trained.weights_sha256
    calls = {}

    class Network:
        def __init__(self, **kwargs):
            pass

        def load_state_dict(self, state, strict):
            calls["strict"] = strict

        def to(self, device):
            return self

        def eval(self):
            return self

    def head(**kwargs):
        calls.update(kwargs)
        return object()

    for module, symbol, value in [
        ("faster_rcnn", "FasterRCNN", head),
        ("fpn", "Fpn", lambda **kwargs: object()),
        ("multitask", "MultiTaskModel", Network),
        ("swin", "Swin", lambda **kwargs: object()),
    ]:
        monkeypatch.setitem(sys.modules, f"rslearn.models.{module}", SimpleNamespace(**{symbol: value}))
    loaded = load_detector(trained)
    assert calls["anchor_sizes"] == [[8], [16], [32], [64]]
    assert calls["downsample_factors"] == [4, 8, 16, 32]
    assert calls["strict"] is True
    assert loaded.manifest.metadata["anchor_sizes"] == calls["anchor_sizes"]
