"""Bind already frozen raw AI decisions to audited catalog identifiers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from shapely.geometry import shape

from puertogt.store import sha256_file, utc_text

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
FROZEN_SHA = "653f5055d74121c04c05164a835ef9a827e1b75ba912b02151070d66efd22665"


def pin(path):
    return {"path": str(path.relative_to(ROOT)), "sha256": sha256_file(path)}


def write(path, value):
    with path.open("x") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def main():
    frozen_path = HERE / "raw-adjudicated-scopes-v1.json"
    if sha256_file(frozen_path) != FROZEN_SHA:
        raise ValueError("Raw decisions changed before catalog correspondence")
    frozen = json.loads(frozen_path.read_text())
    evidence_path = HERE / "train-s2-mar-apr-raw/evidence.json"
    evidence = json.loads(evidence_path.read_text())
    scopes = {item["observation_id"]: item for item in frozen["complete_scopes"]}
    reports, correspondences = [], []
    tx = sqlite3.connect(f"file:{ROOT / 'data/catalog.sqlite'}?mode=ro", uri=True)
    tx.row_factory = sqlite3.Row
    try:
        for entry in evidence["entries"]:
            for item in entry["observations"]:
                oid = item["observation_id"]
                assets = [{"path": "data/" + asset["path"], "sha256": asset["sha256"]}
                          for asset in entry["assets"] if asset["asset_id"] in item["source_asset_ids"]]
                assets.extend(pin(path) for path in [frozen_path, evidence_path,
                    HERE / "raw-focus/manifest.json", HERE / "raw-focus/water-nw-coast-three-dates.png",
                    HERE / "train-s2-mar-apr-raw" / entry["raw_full_view"],
                    HERE / "train-s2-mar-apr-raw" / item["raw_view"],
                    HERE / "train-s2-mar-apr-raw" / item["zone_view"]])
                report = {field: item[field] for field in ("observation_id", "acquisition_key", "port_id", "zone_id", "sensor")}
                report.update(author="codex-ai-science-next", evidence_assets=assets,
                              existing_detections=[], confirmed_additional_vessels=[],
                              summary="Abstención: no se certifica una escena completa ni etiquetas negativas; nubes, sombras, reflejos o incertidumbre de objetos y límites.")
                if oid in scopes:
                    scope = scopes[oid]
                    area = shape(scope["reviewed_area_wgs84"])
                    report["proposed_training_reviewed_area_wgs84"] = scope["reviewed_area_wgs84"]
                    candidates = []
                    for detection in tx.execute("SELECT detection_id,review_status,geometry_json FROM detections WHERE observation_id=?", (oid,)):
                        geometry = shape(json.loads(detection["geometry_json"]))
                        if area.intersection(geometry).area > 0:
                            candidates.append((detection, geometry))
                    if scope["zone_id"] == "pbr_fondeo":
                        if candidates or scope["positives"]:
                            raise ValueError("Unexpected proposal in independently reviewed empty scope; inspect it rather than discard it")
                        report["summary"] = "Área completa de fondeo PBR revisada en la imagen cruda; no hay embarcaciones visualmente discernibles. Referencia provisional de IA, no humana."
                    else:
                        if len(candidates) != 1 or len(scope["positives"]) != 1:
                            raise ValueError("Unexpected correspondence count; preserve frozen raw decisions and adjudicate")
                        detection, geometry = candidates[0]
                        positive = scope["positives"][0]
                        raw_geometry = shape(positive["geometry_wgs84"])
                        if (detection["detection_id"] != "18ef5b50c34975b5975718de"
                                or detection["review_status"] != "provisional"
                                or not geometry.covers(raw_geometry.centroid)):
                            raise ValueError("Catalog correspondence changed")
                        report["existing_detections"].append({
                            "detection_id": detection["detection_id"], "proposed_decision": "accept",
                            "suggested_visual_geometry": positive["geometry_wgs84"],
                            "unique_physical_object_key": positive["unique_physical_object_key"],
                            "reason": positive["reason"] + " Raw box was fixed before proposal lookup; the original broad proposal is corrected to that visual box, excluding the wake. Dimensions and family remain unknown."})
                        correspondences.append({"observation_id": oid, "detection_id": detection["detection_id"],
                            "raw_box_px": positive["visual_box_px"], "proposal_geometry_wgs84": json.loads(detection["geometry_json"]),
                            "raw_scope_or_positive_changed_after_lookup": False, "scores_read": False})
                        report["summary"] = "Solo el parche marino previamente congelado contiene revisión completa: una embarcación pequeña, con discrepancia de bordes de aproximadamente un píxel entre revisores IA. No mide dimensiones físicas ni familia."
                reports.append(report)
    finally:
        tx.close()
    report_path = HERE / "train-review-proposals-v1.json"
    write(report_path, {"created_at": utc_text(), "raw_decision_source": pin(frozen_path),
                        "catalog_correspondence_after_raw_freeze": correspondences,
                        "observations": reports, "human_review": False})
    adjudication = {"author": "codex-ai-root", "kind": "ai_visual_review", "is_human_review": False,
                    "batch": "phase3-s2-caribe-raw-march-april-v1", "created_at": utc_text(),
                    "sources": [pin(report_path)], "accepted_observation_ids": frozen["reviewed_observation_ids"],
                    "blind_to_model": False,
                    "exposure": "Both first raw passes and final two scopes/one positive were frozen before reading catalog proposals, scores or counts. The final import is not wholly blinded: the sole overlapping proposal was then matched to the already fixed visual boat and its geometry corrected. No scores were read, no negatives were inferred from proposals, and no scope changed after proposal lookup. Thirty observations retain abstention. AI consensus is not independent human reference or validation."}
    output = HERE / "train-review-adjudication-v1.json"
    write(output, adjudication)
    print(json.dumps({"adjudication": str(output.relative_to(ROOT)), "sha256": sha256_file(output),
                      "reviewed_observations": len(reports), "complete_scopes": len(scopes),
                      "positive_correspondences": len(correspondences)}))


if __name__ == "__main__":
    main()
