"""Restore real pilot assets and own smoke weights; infer without original paths."""

import json
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from puertogt.api import create_app
from puertogt.cli import read_model
from puertogt.export import export_bundle, restore_bundle
from puertogt.review import bundle_from_evidence
from puertogt.store import Store, json_text


def main():
    import torch

    from puertogt.ml.detection import detect

    torch.set_num_threads(4)
    store = Store(Path("data/catalog.sqlite"), Path("data"))
    destination = Path("dist/pilot-bundle")
    manifest = export_bundle(
        store,
        destination,
        config={
            "ports.geojson": json.loads(Path("config/ports.geojson").read_text()),
            "scope": "experimental pilot; incomplete history; unvalidated local accuracy",
        },
    )
    restored = restore_bundle(destination, Path("tmp/restored-pilot"))
    client = TestClient(create_app(restored))
    observations = client.get("/v1/observations?limit=500")
    observations.raise_for_status()
    results = []
    with closing(restored.connect()) as tx:
        for sensor in ("sentinel-1", "sentinel-2"):
            row = tx.execute(
                "SELECT * FROM observations WHERE sensor=? AND port_id='prq' ORDER BY zone_id LIMIT 1",
                (sensor,),
            ).fetchone()
            sample = {
                "observation_id": row["observation_id"],
                "captured_at": row["captured_at"],
                "evidence_assets": [],
            }
            for asset in tx.execute(
                "SELECT * FROM assets WHERE observation_id=?", (row["observation_id"],)
            ):
                sample["evidence_assets"].append(
                    {
                        "path": asset["path"],
                        "sha256": asset["sha256"],
                        "metadata": json.loads(asset["metadata_json"]),
                    }
                )
                response = client.get(f"/v1/assets/{asset['asset_id']}")
                response.raise_for_status()
            bundle = bundle_from_evidence(sample, restored.data_dir)
            model_row = tx.execute(
                "SELECT data_json FROM models WHERE sensor=? AND model_id LIKE 'puertogt-detection-%' ORDER BY model_id LIMIT 1",
                (sensor,),
            ).fetchone()
            model_path = restored.data_dir / f"{sensor}-model.json"
            model_path.write_text(model_row[0])
            model = read_model(model_path, restored.data_dir)
            if not Path(model.weights_path).is_relative_to(restored.data_dir):
                raise ValueError("Restored model escaped the restored data volume")
            predictions = detect(bundle, model)
            results.append(
                {
                    "sensor": sensor,
                    "model_id": model.model_id,
                    "weights_sha256": model.weights_sha256,
                    "restored_inference_proposals": len(predictions),
                    "only_restored_paths": True,
                }
            )
    report = {
        "scope": "portable operational validation; not scientific accuracy",
        "files_verified": len(manifest.files),
        "bytes": sum(manifest.metadata["file_sizes"].values()),
        "observations_restored": len(observations.json()["items"]),
        "inference": results,
        "http_status": observations.status_code,
    }
    Path("reports/pilot/restore-validation.json").write_text(json_text(report) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
