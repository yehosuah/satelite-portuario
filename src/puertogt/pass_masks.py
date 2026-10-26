"""Verified, derived unions of observable masks within the same satellite pass.

The expensive pixel read runs explicitly, never on an API request. The catalogue
and original observations remain untouched. Mask unions say nothing about vessel
counts, classes, matching between slices or model accuracy.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from contextlib import ExitStack, closing
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.features import geometry_mask
from rasterio.merge import merge
from shapely.geometry import Polygon, shape
from shapely.ops import transform

from puertogt.contracts import utcnow
from puertogt.store import Store, confined_path, sha256_file

_SOURCE_FIELDS = ("observation_id", "asset_id", "path", "sha256", "size_bytes")


def source_record(row) -> dict:
    return {key: row[key] for key in _SOURCE_FIELDS}


def source_records_key(records: list[dict]) -> str:
    return hashlib.sha256(
        json.dumps(
            sorted(records, key=lambda r: r["observation_id"]),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _union(paths: list[Path], zone: dict, *, max_pixels: int) -> dict:
    with ExitStack() as stack:
        sources = [stack.enter_context(rasterio.open(path)) for path in paths]
        first = sources[0]
        if first.crs is None or first.count != 1 or first.transform.b or first.transform.d:
            raise ValueError("observable masks require a single-band north-up grid with CRS")
        for source in sources:
            if (
                source.crs != first.crs
                or source.count != 1
                or source.res != first.res
                or source.transform.b
                or source.transform.d
            ):
                raise ValueError("mask grids differ; no reprojection is allowed in pass union")
            for offset in (
                (source.transform.c - first.transform.c) / first.res[0],
                (source.transform.f - first.transform.f) / first.res[1],
            ):
                if abs(offset - round(offset)) > 1e-7:
                    raise ValueError("mask grids are not pixel aligned")
        left = min(source.bounds.left for source in sources)
        right = max(source.bounds.right for source in sources)
        bottom = min(source.bounds.bottom for source in sources)
        top = max(source.bounds.top for source in sources)
        pixels = math.ceil((right - left) / first.res[0]) * math.ceil((top - bottom) / first.res[1])
        if pixels > max_pixels:
            raise ValueError("pass union pixel budget exceeded")
        raster, grid = merge(sources, method="max", indexes=[1], nodata=0)
        observable = raster[0] > 0
        project = Transformer.from_crs("EPSG:4326", first.crs, always_xy=True).transform
        geometry = transform(project, shape(zone))
        in_zone = geometry_mask([geometry], out_shape=observable.shape, transform=grid, invert=True)
        count = int(in_zone.sum())
        if count == 0:
            raise ValueError("zone has no pixel centres in pass union grid")
        height, width = observable.shape
        footprint = Polygon([grid * p for p in ((0, 0), (width, 0), (width, height), (0, height))])
        crop_fraction = min(1.0, geometry.intersection(footprint).area / geometry.area)
        observed = int(np.logical_and(observable, in_zone).sum())
        return {
            "observable_fraction": min(1.0, observed / count * crop_fraction),
            "observable_pixels": observed,
            "zone_pixels": count,
            "crop_fraction": crop_fraction,
            "crs": str(first.crs),
            "pixel_size": list(first.res),
            "grid_shape": [height, width],
        }


def build_pass_union_cache(
    store: Store, ports_path: Path, output_path: Path, *, max_pixels: int = 25_000_000
) -> dict:
    """Verify original bytes and build same-pass/same-zone/same-recipe mask unions."""
    from puertogt.pass_coverage import pass_key

    ports_bytes = Path(ports_path).read_bytes()
    zones = {
        feature["properties"]["zone_id"]: feature["geometry"]
        for feature in json.loads(ports_bytes)["features"]
    }
    with closing(store.connect()) as tx:
        rows = tx.execute(
            "SELECT o.observation_id,o.acquisition_key,o.sensor,o.zone_id,o.recipe_version,"
            "o.observable_fraction,a.asset_id,a.path,a.sha256,a.size_bytes "
            "FROM observations o JOIN assets a ON a.observation_id=o.observation_id "
            "WHERE o.status IN ('usable','partial') AND o.review_status!='rejected' "
            "AND a.path LIKE '%/observable.tif' ORDER BY o.observation_id"
        ).fetchall()
    groups = defaultdict(list)
    for row in rows:
        groups[
            (
                row["sensor"],
                pass_key(row["acquisition_key"], row["sensor"]),
                row["zone_id"],
                row["recipe_version"],
            )
        ].append(dict(row))
    entries, errors = [], []
    verified_bytes = 0
    for (sensor, key, zone, recipe), group in sorted(groups.items()):
        if len({row["acquisition_key"] for row in group}) < 2:
            continue
        identity = {"sensor": sensor, "pass_key": key, "zone_id": zone, "recipe_version": recipe}
        try:
            paths = []
            for row in group:
                path = confined_path(store.data_dir, row["path"])
                if path.stat().st_size != row["size_bytes"] or sha256_file(path) != row["sha256"]:
                    raise ValueError(f"observable mask checksum mismatch: {row['asset_id']}")
                verified_bytes += row["size_bytes"]
                paths.append(path)
            result = _union(paths, zones[zone], max_pixels=max_pixels)
            pins = [source_record(row) for row in group]
            entries.append(
                identity
                | result
                | {
                    "baseline_max_fraction": max(row["observable_fraction"] or 0 for row in group),
                    "source_records": pins,
                    "source_records_sha256": source_records_key(pins),
                    "source_acquisition_keys": sorted({row["acquisition_key"] for row in group}),
                }
            )
        except (OSError, ValueError, KeyError, rasterio.errors.RasterioError) as error:
            errors.append(identity | {"error_code": type(error).__name__, "message": str(error)})
    report = {
        "schema": "pass-mask-unions-v1",
        "generated_at": utcnow().isoformat(),
        "ports_sha256": hashlib.sha256(ports_bytes).hexdigest(),
        "verified_source_bytes": verified_bytes,
        "entries": entries,
        "errors": errors,
        "method": "binary union of byte-verified observable masks on the same native pixel grid; identical satellite pass, zone and recipe",
        "limits": [
            "Coverage only, not vessel counts or accuracy.",
            "No resampling or synthetic pixels; incompatible grids are rejected.",
            "Area fraction uses pixel centres and the geometric crop-coverage correction used by the worker.",
        ],
    }
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(output_path)
    return report
