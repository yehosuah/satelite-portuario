import json
from datetime import datetime

import pytest
from shapely.geometry import box, mapping

from puertogt.contracts import ModelManifest, Scene
from puertogt.history import (
    create_plan,
    create_selected_plan,
    history_model,
    publish_acquisition,
)
from puertogt.jobs import JobQueue
from puertogt.models import processing_recipe
from puertogt.store import Store


def setup_catalog(tmp_path):
    catalog = Store(tmp_path / "data/catalog.sqlite", tmp_path / "data")
    features = [{"type": "Feature", "properties": {"zone_id": "zone", "port_id": "p"},
                 "geometry": mapping(box(-88.6245, 15.748, -88.623, 15.7495))}]
    old = Scene(scene_id="old", acquisition_key="old", sensor="sentinel-2", product_level="L1C",
                captured_at=datetime.fromisoformat("2025-01-05T00:00:00+00:00"),
                footprint=mapping(box(-91, 13, -87, 17)), port_ids=["p"],
                metadata={"purpose": "public_history"})
    queue = JobQueue(catalog)
    job_id = queue.enqueue(old, "zone", "pilot-recipe", port_id="p")
    parent = tmp_path / "parent"
    create_plan(catalog, features, parent)
    new = old.model_copy(update={"scene_id": "new", "acquisition_key": "new"})
    catalog.register_scene(new)
    return catalog, features, old, new, queue, job_id, parent / "plan.json"


def make_selected(tmp_path, *, selected=("old",), allow=("old",)):
    catalog, features, old, new, queue, job_id, parent = setup_catalog(tmp_path)
    plan = create_selected_plan(catalog, features, tmp_path / "selected", acquisition_keys=list(selected),
                                allow_protected_keys=list(allow), protected_source_plan=parent if allow else None)
    base = ModelManifest(model_id="test", version="v1", sensor="sentinel-2", task="detection", recipe_version="test")
    model = history_model(base, version=plan["plan_id"])
    return catalog, plan, model, queue, job_id, features, new


def test_selected_plan_protects_original_and_uses_exact_selection(tmp_path):
    catalog, plan, model, queue, job_id, features, new = make_selected(tmp_path)
    protected = queue.get_job(job_id)["observation_id"]
    assert plan["selected_acquisition_keys"] == ["old"]
    assert plan["protected_observation_ids"] == [protected]
    assert plan["supplemental_authorization"]["acquisition_keys"] == ["old"]
    assert model.model_id.endswith(plan["plan_id"])
    with pytest.raises(ValueError, match="selection"):
        create_selected_plan(catalog, features, tmp_path / "selected", acquisition_keys=["new"])
    with pytest.raises(ValueError, match="protected"):
        create_selected_plan(catalog, features, tmp_path / "unapproved", acquisition_keys=["old"])
    assert queue.count_jobs() == 1


def test_new_selection_does_not_schedule_existing_unavailable_or_pilot(tmp_path):
    catalog, plan, model, queue, job_id, features, new = make_selected(tmp_path, selected=("new",), allow=())
    assert [item["scene"]["acquisition_key"] for item in plan["acquisitions"]] == ["new"]
    assert plan["supplemental_authorization"] is None
    assert create_selected_plan(catalog, features, tmp_path / "selected", acquisition_keys=["new"]) == plan


def test_source_plan_cannot_authorize_unrelated_protected_capture(tmp_path):
    catalog, features, old, new, queue, job_id, parent = setup_catalog(tmp_path)
    queue.enqueue(new, "zone", "another-pilot", port_id="p")
    with pytest.raises(ValueError, match="source plan"):
        create_selected_plan(catalog, features, tmp_path / "selected", acquisition_keys=["new"],
                             allow_protected_keys=["new"], protected_source_plan=parent)


def test_selected_publication_rejects_modified_protected_rows(tmp_path):
    catalog, plan, model, queue, job_id, features, new = make_selected(tmp_path)
    with catalog.transaction() as tx:
        tx.execute("UPDATE observations SET review_status='reviewed'")
    with pytest.raises(ValueError, match="Protected snapshot"):
        publish_acquisition(catalog, plan["acquisitions"][0], [], [], model,
                            protected_ids=set(plan["protected_observation_ids"]), selected_plan=plan)
    assert queue.count_jobs() == 1


def test_selected_publication_rejects_outside_key_and_recipe_collision(tmp_path):
    catalog, plan, model, queue, job_id, features, new = make_selected(tmp_path)
    item = {**plan["acquisitions"][0], "scene": new.model_dump(mode="json")}
    with pytest.raises(ValueError, match="frozen selection"):
        publish_acquisition(catalog, item, [], [], model,
                            protected_ids=set(plan["protected_observation_ids"]), selected_plan=plan)
    queue.enqueue(Scene.model_validate(plan["acquisitions"][0]["scene"]), "zone", processing_recipe(model))
    with pytest.raises(ValueError, match="recipe collision"):
        publish_acquisition(catalog, plan["acquisitions"][0], [], [], model,
                            protected_ids=set(plan["protected_observation_ids"]), selected_plan=plan)


def test_selected_publication_rejects_wrong_model_and_tampered_plan(tmp_path):
    catalog, plan, model, queue, job_id, features, new = make_selected(tmp_path)
    with pytest.raises(ValueError, match="model identity"):
        publish_acquisition(catalog, plan["acquisitions"][0], [], [], model.model_copy(update={"model_id": "wrong"}),
                            protected_ids=set(plan["protected_observation_ids"]), selected_plan=plan)
    path = tmp_path / "selected/plan.json"
    value = json.loads(path.read_text())
    value["acquisitions"][0]["tiles"] = []
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="identity"):
        create_selected_plan(catalog, features, tmp_path / "selected", acquisition_keys=["old"],
                             allow_protected_keys=["old"], protected_source_plan=tmp_path / "parent/plan.json")


def test_real_publication_adds_new_observation_and_preserves_pilot(tmp_path, monkeypatch):
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    from puertogt.contracts import RasterBundle

    catalog, plan, model, queue, job_id, features, new = make_selected(tmp_path)
    config = tmp_path / "config"
    config.mkdir()
    (config / "ports.geojson").write_text(json.dumps({"features": features}))
    monkeypatch.setenv("PUERTOGT_CONFIG_DIR", str(config))
    numeric = tmp_path / "work/tiles/prepared/numeric.tif"
    numeric.parent.mkdir(parents=True)
    transform = from_origin(-88.625, 15.75, .0001, .0001)
    with rasterio.open(numeric, "w", driver="GTiff", width=32, height=32, count=1,
                       crs="EPSG:4326", transform=transform, dtype="uint16") as target:
        target.write(np.ones((32, 32), dtype="uint16"), 1)
    bundle = RasterBundle(band_paths={name: numeric for name in ["B04", "B03", "B02"]},
                          mask_paths={"observable": numeric}, crs="EPSG:4326", transform=tuple(transform)[:6],
                          units={}, preprocessing_version="test", sensor="sentinel-2", product_level="L1C",
                          metadata={"acquisition_key": "old"})
    monkeypatch.setattr("puertogt.history.mosaic_zone", lambda *args: bundle.model_copy(deep=True))
    old_id = queue.get_job(job_id)["observation_id"]
    with catalog.connect() as tx:
        before = dict(tx.execute("SELECT * FROM observations WHERE observation_id=?", (old_id,)).fetchone())
    output = publish_acquisition(catalog, plan["acquisitions"][0], [(None, bundle)], [], model,
                                 protected_ids=set(plan["protected_observation_ids"]), selected_plan=plan)
    assert output[0]["observation_id"] != old_id
    assert queue.count_jobs() == 2
    assert queue.get_job(job_id)["status"] == "pending"
    with catalog.connect() as tx:
        assert dict(tx.execute("SELECT * FROM observations WHERE observation_id=?", (old_id,)).fetchone()) == before
    resumed = publish_acquisition(catalog, plan["acquisitions"][0], [(None, bundle)], [], model,
                                  protected_ids=set(plan["protected_observation_ids"]), selected_plan=plan)
    assert resumed[0]["resumed"] is True
    assert queue.count_jobs() == 2
