"""Freeze the two adjudicated TRAIN scopes before looking up model proposals."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from pyproj import Transformer
from rasterio.features import geometry_mask
from shapely.geometry import box, mapping, shape
from shapely.ops import transform

from puertogt.store import sha256_file, utc_text

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def pin(path):
    return {"path": str(path.relative_to(ROOT)), "sha256": sha256_file(path)}


def main():
    evidence_path = HERE / "train-s2-mar-apr-raw/evidence.json"
    evidence = json.loads(evidence_path.read_text())
    entry = next(item for item in evidence["entries"] if item["date"] == "20250316")
    grid = Affine(*entry["transform"])
    project = Transformer.from_crs(entry["crs"], "EPSG:4326", always_xy=True)
    zones = {f["properties"]["zone_id"]: shape(f["geometry"])
             for f in json.loads((ROOT / "config/ports.geojson").read_text())["features"]}

    def from_pixels(bounds):
        corners = box(*bounds)
        projected = transform(lambda x, y, z=None: grid * (x, y), corners)
        return transform(project.transform, projected)

    scopes = []
    for zone_id in ("pbr_fondeo", "stc_fondeo"):
        observation = next(row for row in entry["observations"] if row["zone_id"] == zone_id)
        area = (zones[zone_id] if zone_id == "pbr_fondeo" else
                from_pixels([300, 450, 350, 480]).intersection(zones[zone_id]))
        positives = []
        if zone_id == "stc_fondeo":
            geometry = from_pixels([325, 475, 329, 479])
            assert area.covers(geometry.centroid)
            positives.append({"geometry_wgs84": mapping(geometry),
                              "visual_box_px": [325, 475, 329, 479],
                              "unique_physical_object_key": "raw-nw-water-vessel-327-477",
                              "reason": "Compact body and independent wake visible in corrected raw imagery; two AI reviewers agree on vessel presence.",
                              "reviewer_box_uncertainty_px": {"science_next": [324, 475, 330, 480],
                                                              "root_accepted": [325, 475, 329, 479]},
                              "geometry_role": "visual_box_not_physical_dimensions",
                              "family": None, "length_m": None, "width_m": None})
        mask_checks = {}
        for kind in ("valid", "observable", "cloud", "shadow"):
            asset = next(a for a in entry["assets"]
                         if a["path"] == f"assets/{observation['observation_id']}/{kind}.tif")
            path = ROOT / "data" / asset["path"]
            assert sha256_file(path) == asset["sha256"]
            with rasterio.open(path) as image:
                projected_area = transform(Transformer.from_crs("EPSG:4326", image.crs, always_xy=True).transform, area)
                included = geometry_mask([mapping(projected_area)], out_shape=image.shape,
                                         transform=image.transform, invert=True)
                mask = image.read(1)
                mask_checks[kind] = {"included_pixel_centers": int(included.sum()),
                                     "nonzero_pixel_centers": int(np.count_nonzero(mask[included])),
                                     "source": {"path": "data/" + asset["path"], "sha256": asset["sha256"]}}
        count = mask_checks["valid"]["included_pixel_centers"]
        assert count > 0
        assert all(mask_checks[k]["nonzero_pixel_centers"] == count for k in ("valid", "observable"))
        assert all(mask_checks[k]["nonzero_pixel_centers"] == 0 for k in ("cloud", "shadow"))
        scopes.append({"observation_id": observation["observation_id"], "zone_id": zone_id,
                       "acquisition_key": entry["acquisition_key"], "port_id": observation["port_id"],
                       "sensor": "sentinel-2", "reviewed_area_wgs84": mapping(area),
                       "reviewed_area_rule": "Entire configured zone" if zone_id == "pbr_fondeo" else
                       "Global raw pixel rectangle [300,450,350,480] intersected with the original configured AOI",
                       "positives": positives, "complete_for_visually_discernible_vessels": True,
                       "mask_checks": mask_checks})
    all_ids = [row["observation_id"] for item in evidence["entries"] for row in item["observations"]]
    completed = {row["observation_id"] for row in scopes}
    result = {"created_at": utc_text(), "stage": "root_adjudicated_raw_scopes_before_model_correspondence",
              "author": "codex-ai-root", "second_reviewer": "codex-ai-science-next",
              "kind": "ai_visual_review", "human_review": False, "split": "train",
              "cohort_selection": "First four available March-April TRAIN primaries in existing sampling plan; all eight zones, independent of model scores/counts",
              "scope_selection": "Conservative visual confidence after two AI raw passes; not a probability sample or an independent validation set",
              "model_predictions_scores_or_counts_used_to_select_scopes": False,
              "reviewed_observation_ids": all_ids, "complete_scopes": scopes,
              "abstained_observation_ids": [oid for oid in all_ids if oid not in completed],
              "unknowns": [
                  {"dates": ["20250316", "20250403", "20250418"], "visual_box_px": [494, 688, 537, 704],
                   "reason": "PBR pier: ships versus fixed infrastructure unresolved; no positives or hard negatives"},
                  {"date": "20250403", "visual_box_px": [455, 1093, 469, 1102],
                   "reason": "STC quay: probable body but insufficient independent visual confidence"},
                  {"date": "20250301", "reason": "Cloud obscures all eight zones"},
                  {"date": "20250418", "reason": "Cloud, shadow or glint; no complete scope accepted"},
                  {"dates": ["20250316", "20250403"], "zones": ["pbr_canal", "stc_canal"],
                   "reason": "No complete canal review certified; potential ambiguous pier and outside-center overlaps remain unknown"}],
              "contextual_positive_candidates_not_training": [
                  {"date": "20250316", "visual_box_px": [466, 731, 474, 736],
                   "reason": "Moving vessel, center outside every AOI v1; no training label"},
                  {"date": "20250403", "visual_box_px": [491, 426, 499, 445],
                   "reason": "Moving vessel, center outside every AOI v1; no training label"},
                  {"date": "20250316", "visual_box_px": [486, 1072, 503, 1085],
                   "reason": "NE jetty change compatible with a real vessel, outside every AOI v1; contextual only"}],
              "source_pins": [pin(path) for path in [evidence_path,
                  HERE / "train-s2-mar-apr-raw/selection.json", HERE / "raw-independent-first-passes.json",
                  HERE / "raw-focus/manifest.json", HERE / "raw-candidate-zone-membership.json",
                  ROOT / "config/ports.geojson", Path(__file__)]],
              "local_validation_demonstrated": False}
    output = HERE / "raw-adjudicated-scopes-v1.json"
    with output.open("x") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(json.dumps({"path": str(output.relative_to(ROOT)), "sha256": sha256_file(output),
                      "complete_scopes": len(scopes), "abstentions": len(all_ids) - len(scopes),
                      "positive_labels": sum(len(row["positives"]) for row in scopes)}))


if __name__ == "__main__":
    main()
