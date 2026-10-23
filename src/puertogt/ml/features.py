"""Explicit context geometry shared by attribute training and inference."""

from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np
from pyproj import Transformer
from rasterio import Affine
from rasterio.enums import Resampling
from rasterio.warp import reproject
from shapely.geometry import Polygon, shape
from shapely.ops import transform as project_geometry

from puertogt.contracts import RasterBundle

FIXED_CONTEXT_V1 = {
    "kind": "fixed_center_context_v1",
    "size_px": 128,
    "resolution_m": 10.0,
    "projection": "local_utm",
    "center": "geometry_centroid",
    "resampling": "nearest",
    "pooling": "full_context_mean",
    "minimum_coverage": 1.0,
}


class ContextUnavailable(ValueError):
    """The supplied raster does not contain the required fixed context."""


def context_grid(geometry: dict, policy: Mapping[str, Any]) -> tuple[Affine, str]:
    """Return a fixed metric context around the reference geometry's center.

    Geometry provides location only. Its size and all attribute target values
    are excluded from this policy. Version one deliberately fixes every option.
    """
    if dict(policy) != FIXED_CONTEXT_V1:
        raise ValueError("Unsupported or incomplete attribute context policy")
    source = shape(geometry)
    if source.is_empty or not source.is_valid:
        raise ValueError("Attribute context requires a valid reference geometry")
    lon, lat = source.centroid.x, source.centroid.y
    if not math.isfinite(lon + lat) or not -180 <= lon <= 180 or not -80 <= lat <= 84:
        raise ValueError("Attribute context requires a WGS84 location within UTM coverage")
    zone = min(60, int((lon + 180) // 6) + 1)
    crs = f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"
    project = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    center = project_geometry(project.transform, source).centroid
    resolution = policy["resolution_m"]
    half = policy["size_px"] * resolution / 2
    return Affine(resolution, 0, center.x-half, 0, -resolution, center.y+half), crs


def fixed_context_image(image: np.ndarray, bundle: RasterBundle, geometry: dict,
                        policy: Mapping[str, Any]) -> np.ndarray:
    """Reproject an existing normalized image without hidden downloads or padding.

    Full geometric coverage is required; nodata and quality limitations already
    present in the source remain present and are not replaced with observations.
    """
    target, crs = context_grid(geometry, policy)
    if image.ndim != 3 or not np.isfinite(image).all():
        raise ValueError("Attribute contexts require finite CHW numeric input")
    height, width = image.shape[-2:]
    source_transform = Affine(*bundle.transform)
    footprint = Polygon([source_transform * point for point in
                         [(0, 0), (width, 0), (width, height), (0, height)]])
    project = Transformer.from_crs(bundle.crs, crs, always_xy=True)
    footprint = project_geometry(project.transform, footprint)
    size = policy["size_px"]
    desired = Polygon([target * point for point in [(0, 0), (size, 0), (size, size), (0, size)]])
    coverage = footprint.intersection(desired).area / desired.area
    if coverage < policy["minimum_coverage"] - 1e-8:
        raise ContextUnavailable(f"Fixed attribute context coverage is {coverage:.6f}; full coverage required")
    result = np.zeros((image.shape[0], size, size), dtype=np.float32)
    for index, band in enumerate(image):
        reproject(band, result[index], src_transform=source_transform, src_crs=bundle.crs,
                  dst_transform=target, dst_crs=crs, resampling=Resampling.nearest)
    return result
