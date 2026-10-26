"""Render four previously selected TRAIN dates without querying detections."""

import json
import math
import sqlite3
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.merge import merge
from shapely.geometry import shape
from shapely.ops import transform as project

from puertogt.store import sha256_file, utc_text

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).parent / "train-s2-mar-apr-raw"
PLAN = ROOT / "reports/local-review/phase2/sampling-plan.json"
ZONES = ROOT / "config/ports.geojson"


def render():
    OUT.mkdir(exist_ok=False)
    plan = json.loads(PLAN.read_text())
    selected = [slot for slot in plan["slots"] if slot["split"] == "train"
                and slot["sensor"] == "sentinel-2" and slot["port_id"] in {"stc", "pbr"}
                and "2025-03-01" <= slot["slot_start"] < "2025-05-01"]
    selection = {"created_at": utc_text(), "split": "train", "selected_slots": selected,
                 "selection_rule": "First four existing chronological TRAIN primaries after February, both Caribbean ports",
                 "sampling_plan_sha256": sha256_file(PLAN), "zones_sha256": sha256_file(ZONES),
                 "prediction_scores_counts_or_geometries_read": False, "test_opened": False,
                 "date_selection_before_image_inspection": True}
    (OUT / "selection.json").write_text(json.dumps(selection, indent=2) + "\n")
    zones = {f["properties"]["zone_id"]: shape(f["geometry"])
             for f in json.loads(ZONES.read_text())["features"]}
    catalog = sqlite3.connect((ROOT / "data/catalog.sqlite").resolve().as_uri() + "?mode=ro", uri=True)
    catalog.row_factory = sqlite3.Row
    entries = []
    for key in dict.fromkeys(slot["selected"]["acquisition_key"] for slot in selected):
        observations = [dict(row) for row in catalog.execute(
            "SELECT observation_id,acquisition_key,zone_id,port_id,sensor,captured_at,status,observable_fraction "
            "FROM observations WHERE acquisition_key=? ORDER BY zone_id", (key,))]
        assert len(observations) == 8
        # Each observation retains only tiles intersecting its zone. Merge all
        # zone evidence; using STC canal alone would omit the NW fondeo tile.
        used = []
        planes = {}
        for obs in observations:
            assets = [dict(row) for row in catalog.execute(
                "SELECT asset_id,path,sha256 FROM assets WHERE observation_id=? AND state='ready' ORDER BY path",
                (obs["observation_id"],))]
            obs["source_asset_ids"] = [row["asset_id"] for row in assets]
            for row in assets:
                if Path(row["path"]).stem in {"B04", "B03", "B02", "observable", "cloud", "shadow", "valid"}:
                    assert sha256_file(ROOT / "data" / row["path"]) == row["sha256"]
                    used.append(row)
        for name in ("B04", "B03", "B02", "observable", "cloud", "shadow", "valid"):
            sources = [rasterio.open(ROOT / "data" / row["path"]) for row in used
                       if Path(row["path"]).stem == name]
            try:
                # A merge of identical tile copies must be invariant to order.
                forward, grid = merge(sources, nodata=0)
                reverse, reverse_grid = merge(list(reversed(sources)), nodata=0)
                assert np.array_equal(forward, reverse) and grid == reverse_grid
                crs = sources[0].crs
                assert all(src.crs == crs and src.res == (10, 10) for src in sources)
                planes[name] = forward[0]
            finally:
                for src in sources:
                    src.close()
        height, width = planes["B04"].shape
        bands = [planes[name] for name in ("B04", "B03", "B02")]
        rgb = np.clip(np.stack(bands, axis=-1).astype(float) / 3000, 0, 1) ** (1 / 1.8)
        date = key.split(":")[2][:8]
        backward = Transformer.from_crs(4326, crs, always_xy=True)
        inverse = ~grid

        def pixel_geometry(geometry):
            native = project(backward.transform, geometry)
            return project(lambda x, y: inverse * (x, y), native)

        raw_name = f"{date}-full-raw.png"
        plt.imsave(OUT / raw_name, rgb)
        for obs in observations:
            zone = pixel_geometry(zones[obs["zone_id"]])
            x1, y1, x2, y2 = zone.bounds
            x1, x2 = max(0, math.floor(x1) - 30), min(width, math.ceil(x2) + 30)
            y1, y2 = max(0, math.floor(y1) - 30), min(height, math.ceil(y2) + 30)
            if x1 >= x2 or y1 >= y2:
                raise ValueError("Review zone is outside the chosen numeric mosaic")
            filename = f"{date}-{obs['zone_id']}-raw.png"
            fig, ax = plt.subplots(figsize=(10, max(5, min(12, 10 * (y2-y1) / (x2-x1)))))
            ax.imshow(rgb[y1:y2, x1:x2], extent=(x1, x2, y2, y1), interpolation="nearest")
            ax.set_title(f"{date} {obs['zone_id']} | raw RGB | 10 m / pixel")
            ax.set_xlabel("Global mosaic column (pixel boundary coordinates)")
            ax.set_ylabel("Global mosaic row")
            ax.grid(alpha=.2)
            fig.tight_layout()
            fig.savefig(OUT / filename, dpi=120)
            plt.close(fig)
            scope_name = filename.replace("-raw", "-zone")
            fig, ax = plt.subplots(figsize=(10, max(5, min(12, 10 * (y2-y1) / (x2-x1)))))
            ax.imshow(rgb[y1:y2, x1:x2], extent=(x1, x2, y2, y1), interpolation="nearest")
            for part in getattr(zone, "geoms", [zone]):
                xs, ys = part.exterior.xy
                ax.plot(xs, ys, color="yellow", linewidth=1)
            ax.set_title(f"{date} {obs['zone_id']} | fixed provisional AOI only, no detector")
            ax.set_xlabel("Global mosaic column")
            ax.set_ylabel("Global mosaic row")
            ax.grid(alpha=.2)
            fig.tight_layout()
            fig.savefig(OUT / scope_name, dpi=120)
            plt.close(fig)
            obs.update(raw_view=filename, zone_view=scope_name,
                       window_pixel_boundaries=[x1, y1, x2, y2], zone_pixel_geometry=zone.__geo_interface__)
        for mask in ("observable", "cloud", "shadow", "valid"):
            plt.imsave(OUT / f"{date}-{mask}.png", planes[mask], cmap="gray", vmin=0, vmax=1)
        entries.append({"acquisition_key": key, "date": date, "crs": str(crs),
                        "transform": list(grid)[:6], "shape": [height, width],
                        "rgb_rendering": "B04,B03,B02; clip(DN/3000,0,1)^(1/1.8); native grid",
                        "mosaic_source": "all eight original zone rasters; no resampling; nonzero overlap identical; forward/reverse merge equality verified",
                        "raw_full_view": raw_name, "observations": observations,
                        "assets": [{k: row[k] for k in ("asset_id", "path", "sha256")} for row in used]})
    catalog.close()
    (OUT / "evidence.json").write_text(json.dumps({"created_at": utc_text(), "entries": entries,
        "script_sha256": sha256_file(Path(__file__)), "predictions_read": False,
        "selection_sha256": sha256_file(OUT / "selection.json")}, indent=2) + "\n")
    (OUT / "SHA256SUMS").write_text("".join(f"{sha256_file(path)}  {path.name}\n" for path in sorted(OUT.iterdir())))
    print(json.dumps({"output": str(OUT), "acquisitions": len(entries), "observations": sum(len(e["observations"]) for e in entries)}))


if __name__ == "__main__":
    render()
