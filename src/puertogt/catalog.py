"""Discovery and acquisition identity for the public Copernicus catalogue.

Only catalogue metadata is fetched here. Catalogue coverage is not an assertion
about observable water, usable pixels, detections or access to product downloads.
"""

import hashlib
import json
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from shapely.geometry import Point, mapping, shape
from shapely.ops import unary_union

from puertogt.contracts import Scene, as_utc, utcnow

CATALOGUE_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
_BUNDLED_CONFIG = Path(__file__).parent / "config"
DEFAULT_PORTS_PATH = (
    _BUNDLED_CONFIG if _BUNDLED_CONFIG.exists() else Path(__file__).resolve().parents[2] / "config"
) / "ports.geojson"

_S1_NAME = re.compile(
    r"^(S1[A-D])_(IW)_(GRDH)_(1SDV)_(\d{8}T\d{6})_(\d{8}T\d{6})_"
    r"(\d{6})_([A-F0-9]{6})_[A-F0-9]{4}(?:_COG)?(?:\.SAFE)?$"
)
_S2_NAME = re.compile(
    r"^(S2[A-D])_(MSIL1C)_(\d{8}T\d{6})_N\d{4}_(R\d{3})_"
    r"(T\d{2}[A-Z]{3})_\d{8}T\d{6}(?:\.SAFE)?$"
)


def acquisition_key(product_name: str) -> str:
    """Identify a sensing event independently of its delivery representation."""
    match = _S1_NAME.fullmatch(product_name) or _S2_NAME.fullmatch(product_name)
    if match is None:
        raise ValueError(f"unsupported Sentinel product name: {product_name}")
    return ":".join(match.groups())


@dataclass
class CatalogSearch:
    """A complete search, retaining page evidence and product representations."""

    products: list[dict[str, Any]]
    pages: list[dict[str, Any]]
    reported_count: int | None

    @property
    def complete(self) -> bool:
        return self.reported_count is None or self.reported_count == len(self.products)


class CatalogClient:
    def __init__(
        self,
        *,
        fetch_json: Callable | None = None,
        page_size: int = 100,
        max_pages: int = 1000,
        timeout: int = 45,
    ):
        if not 1 <= page_size <= 1000 or max_pages < 1:
            raise ValueError("page_size must be 1..1000 and max_pages positive")
        self.page_size = page_size
        self.max_pages = max_pages
        self.timeout = timeout
        self.fetch_json = fetch_json or self._fetch_json

    def _fetch_json(self, url: str) -> dict[str, Any]:
        for attempt in range(4):
            try:
                request = Request(url, headers={"User-Agent": "satelite-portuario/0.1 catalogue"})
                with urlopen(request, timeout=self.timeout) as response:
                    return json.load(response)
            except HTTPError as exc:
                if exc.code not in {408, 429, 500, 502, 503, 504} or attempt == 3:
                    raise
                retry_after = exc.headers.get("Retry-After", "")
                delay = min(float(retry_after), 30) if retry_after.isdigit() else 2**attempt
                time.sleep(delay)
            except (URLError, TimeoutError):
                if attempt == 3:
                    raise
                time.sleep(2**attempt)
        raise RuntimeError("unreachable retry state")

    def search(
        self,
        sensor: str,
        geometry: dict,
        start: datetime,
        end: datetime,
        *,
        as_of: datetime | None = None,
    ) -> CatalogSearch:
        """Fetch every page for a UTC half-open interval; fail closed on truncation."""
        start, end = as_utc(start), as_utc(end)
        if start >= end:
            raise ValueError("start must precede end")
        if sensor not in {"sentinel-1", "sentinel-2"}:
            raise ValueError(f"unsupported sensor: {sensor}")
        area = shape(geometry)
        if not area.is_valid or area.is_empty or area.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError("search geometry must be a valid nonempty polygon")
        if not (
            -180 <= area.bounds[0] <= area.bounds[2] <= 180
            and -90 <= area.bounds[1] <= area.bounds[3] <= 90
        ):
            raise ValueError("search geometry must use WGS84 longitude/latitude")
        token = "_IW_GRDH_1SDV_" if sensor == "sentinel-1" else "_MSIL1C_"
        query_filter = (
            f"Collection/Name eq '{sensor.upper()}' and contains(Name,'{token}') "
            f"and OData.CSC.Intersects(area=geography'SRID=4326;{area.wkt}') "
            f"and ContentDate/Start ge {_iso(start)} and ContentDate/Start lt {_iso(end)}"
        )
        if as_of is not None:
            query_filter += f" and PublicationDate le {_iso(as_utc(as_of))}"
        url = (
            CATALOGUE_URL
            + "?"
            + urlencode(
                {
                    "$filter": query_filter,
                    "$top": self.page_size,
                    "$count": "true",
                    "$expand": "Attributes",
                    "$orderby": "ContentDate/Start asc",
                }
            )
        )
        products: dict[str, dict] = {}
        pages = []
        visited = set()
        expected = None
        while url:
            if url in visited:
                raise RuntimeError("catalogue repeated a pagination URL")
            if len(pages) >= self.max_pages:
                raise RuntimeError("catalogue pagination limit reached; inventory is incomplete")
            visited.add(url)
            payload = self.fetch_json(url)
            if not isinstance(payload.get("value"), list):
                raise ValueError("catalogue response has no product list")
            count = payload.get("@odata.count", payload.get("@OData.count"))
            if count is not None:
                if expected is not None and expected != count:
                    raise RuntimeError("catalogue count changed while paginating; repeat search")
                expected = count
            next_link = payload.get("@odata.nextLink", payload.get("@OData.nextLink"))
            pages.append(
                {
                    "url": url,
                    "fetched_at_utc": _iso(utcnow()),
                    "returned_products": len(payload["value"]),
                    "reported_count": count,
                    "response_sha256": hashlib.sha256(
                        json.dumps(payload, sort_keys=True).encode()
                    ).hexdigest(),
                    "next_link": next_link,
                }
            )
            for product in payload["value"]:
                products[product["Id"]] = product
            if next_link:
                next_link = urljoin(url, next_link)
                parsed = urlparse(next_link)
                if parsed.scheme != "https" or parsed.netloc != urlparse(CATALOGUE_URL).netloc:
                    raise ValueError("unexpected catalogue pagination host")
            url = next_link
        result = CatalogSearch(list(products.values()), pages, expected)
        if not result.complete:
            raise RuntimeError(
                f"catalogue count mismatch: retrieved {len(products)}, reported {expected}"
            )
        return result


def _iso(value: datetime) -> str:
    return as_utc(value).isoformat().replace("+00:00", "Z")


def _date(value: str) -> datetime:
    return as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def normalize_products(
    products: list[dict],
    *,
    port_ids: list[str] | None = None,
    discovered_at: datetime | None = None,
) -> list[Scene]:
    """Retain every representation while collapsing identical sensing events.

    A tile/time may have multiple partial S2 products, so the footprint is their
    union and each representation retains its own footprint for asset selection.
    """
    groups: dict[str, dict[str, dict]] = defaultdict(dict)
    for product in products:
        groups[acquisition_key(product["Name"])][product["Id"]] = product
    scenes = []
    for key, grouped in groups.items():
        values = sorted(grouped.values(), key=lambda p: p["Id"])
        first = values[0]
        sensor = "sentinel-1" if first["Name"].startswith("S1") else "sentinel-2"
        attrs = {item["Name"]: item["Value"] for item in first.get("Attributes", [])}
        source_assets = {}
        representations = []
        for product in values:
            product_id = product["Id"]
            source_assets[f"{product_id}:product"] = (
                f"https://download.dataspace.copernicus.eu/odata/v1/Products({product_id})/$value"
            )
            if product.get("S3Path"):
                source_assets[f"{product_id}:s3"] = product["S3Path"]
            product_attrs = {item["Name"]: item["Value"] for item in product.get("Attributes", [])}
            representations.append(
                {
                    "product_id": product_id,
                    "name": product["Name"],
                    "format": "COG_SAFE" if "_COG" in product["Name"] else "SAFE",
                    "online": product.get("Online"),
                    "content_length": product.get("ContentLength"),
                    "published_at": product.get("PublicationDate"),
                    "footprint": product["GeoFootprint"],
                    "attributes": product_attrs,
                }
            )
        footprints = [shape(product["GeoFootprint"]) for product in values]
        if any(not geom.is_valid or geom.is_empty for geom in footprints):
            raise ValueError(f"invalid footprint for acquisition {key}")
        publications = [_date(v["PublicationDate"]) for v in values if v.get("PublicationDate")]
        scenes.append(
            Scene(
                scene_id="scene_" + hashlib.sha256(key.encode()).hexdigest()[:24],
                acquisition_key=key,
                sensor=sensor,
                product_level="IW_GRDH_1SDV" if sensor == "sentinel-1" else "L1C",
                captured_at=min(_date(v["ContentDate"]["Start"]) for v in values),
                footprint=dict(mapping(unary_union(footprints))),
                source_assets=source_assets,
                published_at=min(publications) if publications else None,
                discovered_at=discovered_at or utcnow(),
                port_ids=sorted(set(port_ids or [])),
                relative_orbit=attrs.get("relativeOrbitNumber"),
                orbit_direction=attrs.get("orbitDirection"),
                metadata={
                    "representations": representations,
                    "product_count": len(values),
                    "catalogue_only": True,
                    "usability_evaluated": False,
                },
            )
        )
    return sorted(scenes, key=lambda scene: (scene.captured_at, scene.acquisition_key))


def load_ports(path: Path | str | None = None) -> dict[str, dict]:
    """Read validated zone geometry; preserve the explicit human review status."""
    if path is None and os.getenv("PUERTOGT_CONFIG_DIR"):
        path = Path(os.environ["PUERTOGT_CONFIG_DIR"]) / "ports.geojson"
    path = Path(path) if path else DEFAULT_PORTS_PATH
    collection = json.loads(path.read_text(encoding="utf-8"))
    if collection.get("type") != "FeatureCollection":
        raise ValueError("ports configuration must be a GeoJSON FeatureCollection")
    ports = {}
    zone_ids = set()
    for feature in collection["features"]:
        properties = feature["properties"]
        port_id, zone_id = properties["port_id"], properties["zone_id"]
        if zone_id in zone_ids:
            raise ValueError(f"duplicate zone_id: {zone_id}")
        zone_ids.add(zone_id)
        geom = shape(feature["geometry"])
        if geom.is_empty or not geom.is_valid or geom.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError(f"invalid polygon for zone {zone_id}")
        port = ports.setdefault(
            port_id,
            {
                "port_id": port_id,
                "name": properties["port_name"],
                "geometry_version": properties["version"],
                "zones": [],
            },
        )
        port["zones"].append(feature)
    for port in ports.values():
        port["geometry"] = dict(mapping(unary_union([shape(z["geometry"]) for z in port["zones"]])))
        port["properties"] = {k: v for k, v in port.items() if k not in {"geometry", "zones"}}
    values = list(ports.values())
    for i, port in enumerate(values):
        for other in values[i + 1 :]:
            if shape(port["geometry"]).intersection(shape(other["geometry"])).area > 1e-12:
                raise ValueError(
                    f"port attribution overlaps: {port['port_id']} / {other['port_id']}"
                )
    return ports


def attribute_point(longitude: float, latitude: float, *, ports: dict | None = None) -> str | None:
    """Assign an observed point to at most one configured port, including boundaries."""
    point = Point(longitude, latitude)
    matches = [
        port_id
        for port_id, port in (ports if ports is not None else load_ports()).items()
        if shape(port["geometry"]).covers(point)
    ]
    return sorted(matches)[0] if matches else None


def discover(
    port_id: str,
    start: datetime,
    end: datetime,
    *,
    client: CatalogClient | None = None,
    ports_path: Path | str | None = None,
) -> list[Scene]:
    ports = load_ports(ports_path)
    if port_id not in ports:
        raise ValueError(f"unknown port: {port_id}")
    client = client or CatalogClient()
    stamp = utcnow()
    products = []
    for sensor in ("sentinel-1", "sentinel-2"):
        result = client.search(sensor, ports[port_id]["geometry"], start, end, as_of=stamp)
        products.extend(result.products)
    return normalize_products(products, port_ids=[port_id], discovered_at=stamp)
