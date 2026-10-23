import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio import Affine
from shapely.geometry import box, mapping
from shapely.ops import transform

from puertogt.contracts import ModelManifest, Prediction, RasterBundle
from puertogt.ml.attributes import extract_features, predict_attributes
from puertogt.ml.features import (
    FIXED_CONTEXT_V1,
    ContextUnavailable,
    context_grid,
    fixed_context_image,
)
from puertogt.preprocess import S2_BANDS


def geometry(half_width=20):
    to_wgs84 = Transformer.from_crs(32616, 4326, always_xy=True).transform
    return mapping(transform(to_wgs84, box(400000-half_width, 1700000-half_width,
                                          400000+half_width, 1700000+half_width)))


def bundle(transform_value):
    return RasterBundle(band_paths={}, crs="EPSG:32616", transform=tuple(transform_value)[:6],
                        sensor="sentinel-2", product_level="L1C", units={},
                        preprocessing_version="ai2-s2-v1")


def test_fixed_context_does_not_use_reference_dimensions_or_box_extents():
    grid, crs = context_grid(geometry(), FIXED_CONTEXT_V1)
    source = bundle(grid)
    data = np.arange(9*128*128, dtype=np.float32).reshape(9, 128, 128)
    small = fixed_context_image(data, source, geometry(10), FIXED_CONTEXT_V1)
    large = fixed_context_image(data, source, geometry(200), FIXED_CONTEXT_V1)
    assert crs == "EPSG:32616"
    np.testing.assert_array_equal(small, large)
    np.testing.assert_array_equal(small, data)


def test_fixed_context_rejects_missing_surrounding_pixels_instead_of_inventing_them():
    grid, _ = context_grid(geometry(), FIXED_CONTEXT_V1)
    source = bundle(grid * Affine.translation(1, 0))
    with pytest.raises(ContextUnavailable, match="coverage"):
        fixed_context_image(np.ones((9, 128, 128), dtype=np.float32), source,
                            geometry(), FIXED_CONTEXT_V1)


def test_feature_extraction_uses_same_context_policy_as_inference(tmp_path):
    grid, _ = context_grid(geometry(), FIXED_CONTEXT_V1)
    source = bundle(grid)
    # A real raster prevents lazy imports from retaining a patched reader alias.
    path = tmp_path / "context.tif"
    with rasterio.open(path, "w", driver="GTiff", count=9, height=128, width=128,
                       dtype="uint16", crs=source.crs, transform=grid) as destination:
        destination.write(np.full((9, 128, 128), 9000, dtype=np.uint16))
    source = source.model_copy(update={
        "band_paths": {band: path for band in S2_BANDS},
        "units": {band: "DN_harmonized" for band in S2_BANDS},
        "metadata": {"band_indices": {band: index + 1 for index, band in enumerate(S2_BANDS)}},
    })

    class Tensor:
        def __init__(self, values): self.values = values
        def __getitem__(self, item): return Tensor(self.values[item])
        def detach(self): return self
        def cpu(self): return self
        def numpy(self): return self.values

    class Detector:
        def __init__(self): self.seen = []
        def features(self, values):
            self.seen.append(values.copy())
            return [Tensor(np.stack([values[0], values[1]])[None])]

    detector = Detector()
    model = ModelManifest(model_id="encoder", version="v1", task="detection",
                          sensor="sentinel-2", recipe_version="ai2-s2-v1",
                          bands=S2_BANDS, units=source.units)
    predictions = [Prediction(prediction_id="a", geometry=geometry(10), detection_score=1),
                   Prediction(prediction_id="b", geometry=geometry(200), detection_score=1)]
    features = extract_features(source, predictions, model, detector=detector,
                                context_policy=FIXED_CONTEXT_V1)
    assert features.shape == (2, 2)
    np.testing.assert_array_equal(detector.seen[0], detector.seen[1])
    np.testing.assert_array_equal(features, np.ones((2, 2)))


def test_context_policy_requires_declared_resampling_and_full_coverage():
    with pytest.raises(ValueError, match="context policy"):
        context_grid(geometry(), {**FIXED_CONTEXT_V1, "resampling": "bilinear"})


def test_missing_context_abstains_only_for_affected_detection(monkeypatch):
    model = ModelManifest(model_id="attribute", version="v1", task="attributes",
                          sensor="sentinel-2", recipe_version="ai2-s2-v1", weights_path="unused.pt",
                          metadata={"feature_context": FIXED_CONTEXT_V1}, publication_policy={"fields": {
                              "family": {"validated": True, "min_support": 1, "reference_support": 10,
                                         "allowed_families": ["carga"], "min_probability": 0.8}}})
    predictions = [Prediction(prediction_id=name, geometry=geometry(), detection_score=0.9)
                   for name in ["inside", "edge"]]

    def estimate(_bundle, items, _model):
        if any(item.prediction_id == "edge" for item in items):
            raise ContextUnavailable("coverage")
        return [{"family": "carga", "family_probability": 0.99}]

    monkeypatch.setattr("puertogt.ml.attributes.estimate_attributes", estimate)
    result = predict_attributes(bundle(context_grid(geometry(), FIXED_CONTEXT_V1)[0]), predictions, model)
    assert result[0].family == "carga"
    assert result[1].family is None
    assert result[1].field_quality["family"]["reason"] == "no_estimate"
