import copy
import json

import numpy as np
import pytest
from test_change_workflows import change_manifest, make_pair_dataset

from puertogt.ml import change_workflows as workflows
from puertogt.store import json_text


def public_label(label):
    return {key: value for key, value in label.items() if not key.startswith("reviewed_")} | {
        "review_kind": "public_dataset",
        "provenance": {
            "dataset": "OSCD",
            "source": "https://doi.org/10.21227/asqe-7s69",
            "annotation_method": "manual_pixel_annotation",
            "attribution": "Daudt et al., 2018",
            "license": "CC-BY-NC-SA; archive README; DOI conflict retained",
            "source_sha256": "a" * 64,
        },
    }


def test_public_labels_require_manual_source_provenance_without_fake_review_date(tmp_path):
    dataset = tmp_path / "pairs"
    manifest = make_pair_dataset(dataset)
    sample = manifest["samples"][0]
    sample["labels"] = public_label(sample["labels"])
    _, _, _, valid = workflows._reviewed_pair(dataset, sample, manifest["quality_protocol"])
    assert valid.any()
    sample["labels"]["provenance"].pop("license")
    with pytest.raises(ValueError, match="provenance"):
        workflows._reviewed_pair(dataset, sample, manifest["quality_protocol"])


def test_acquisition_windows_are_distinct_but_cannot_leak_to_test(tmp_path):
    dataset = tmp_path / "pairs"
    manifest = make_pair_dataset(dataset)
    first = manifest["samples"][0]
    first["spatial_window"] = [0, 0, 8, 8]
    second = copy.deepcopy(first)
    second["pair_id"] = "next-window"
    second["spatial_window"] = [8, 0, 8, 8]
    manifest["samples"] = [first, second]
    (dataset / "manifest.json").write_text(json_text(manifest))
    assert len(workflows._dataset(dataset, ())["samples"]) == 2
    reserved = tmp_path / "test.json"
    reserved.write_text(json.dumps({**manifest, "split": "test"}))
    with pytest.raises(ValueError, match="leakage"):
        workflows._dataset(dataset, (reserved,))
    second["spatial_window"] = first["spatial_window"]
    (dataset / "manifest.json").write_text(json_text(manifest))
    with pytest.raises(ValueError, match="unique"):
        workflows._dataset(dataset, ())


def test_alignment_detects_translation_and_radiometric_gain_without_labels():
    from puertogt.ml.oscd import measure_alignment

    rng = np.random.default_rng(84)
    before = rng.random((256, 256)).astype("float32")
    aligned = measure_alignment(before, before * 1.7 + 500)
    assert aligned["rmse_pixels"] == 0
    assert aligned["stable_patches"] >= 4
    shifted = measure_alignment(before, np.roll(before, (0, 4), axis=(0, 1)))
    assert shifted["rmse_pixels"] == 4


def test_source_label_encoding_is_verified_against_documented_png_semantics():
    from puertogt.ml.oscd import decode_manual_mask

    values = np.asarray([[1, 1], [2, 1]], dtype="uint8")
    png = np.asarray([[0, 0], [255, 0]], dtype="uint8")
    assert np.array_equal(decode_manual_mask(values, png), values - 1)
    with pytest.raises(ValueError, match="semantics"):
        decode_manual_mask(values, np.zeros((2, 2), dtype="uint8"))


def test_positive_weight_changes_only_reviewed_positive_gradient():
    torch = pytest.importorskip("torch")
    from puertogt.ml.change import masked_change_loss

    logits = torch.zeros((1, 1, 1, 3), requires_grad=True)
    targets = torch.tensor([[[[1.0, 0.0, 1.0]]]])
    mask = torch.tensor([[[[True, True, False]]]])
    masked_change_loss(logits, targets, mask, positive_weight=4.0).backward()
    assert logits.grad.tolist() == [[[[-1.0, 0.25, 0.0]]]]


def test_change_training_export_keeps_fixed_operational_thresholds(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    from puertogt.ml.change import build_siamese_change_model

    dataset = tmp_path / "pairs"
    make_pair_dataset(dataset)
    recipe = change_manifest(tmp_path)

    class Encoder(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.layer = torch.nn.Conv2d(9, 2, 1)

        def forward(self, value):
            return [self.layer(value)]

    monkeypatch.setattr(
        workflows,
        "create_satlas_change_model",
        lambda **kwargs: build_siamese_change_model(Encoder(), [2], decoder_channels=4),
    )
    trained = workflows.train_change_dataset(dataset, recipe, tmp_path / "model")
    for field in (
        "change_threshold",
        "min_area_m2",
        "max_alignment_rmse_pixels",
        "minimum_observable_fraction",
    ):
        assert trained.publication_policy[field] == recipe.publication_policy[field]
    assert trained.publication_policy["automatic_publication"] is False
    assert trained.publication_policy["status"] == "experimental"
