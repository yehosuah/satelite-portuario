"""Resumable, bounded public historical materialization, isolated from pilot data.

The plan pins catalogue inputs, zone geometry and protected observations. Download
reservations survive interruption. One OS lock covers materialization; the normal
worker uses the same heavy-work lock when publishing experimental observations.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio import Affine
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from shapely.geometry import box, shape
from shapely.ops import transform as project_geometry

from .contracts import ModelManifest, RasterBundle, Scene
from .jobs import JobQueue, ResourceLimit, RuntimeLimits
from .models import processing_recipe
from .preprocess import S1_BANDS, S2_BANDS, add_cloud_shadow_masks, prepare
from .store import Store, json_text, sha256_file, utc_now, utc_text

HISTORY_VERSION = "history-full-v1"


@dataclass(frozen=True)
class HistoryLimits:
    reserve_bytes: int = 5 * 1024**3
    total_download_bytes: int = 200 * 1024**3
    acquisition_download_bytes: int = 1024**3
    volume_limit_bytes: int = 100 * 1024**3
    max_attempts: int = 3
    retry_seconds: int = 60
    io_workers: int = 2

    def __post_init__(self):
        if self.reserve_bytes < 0 or min(self.total_download_bytes,
                                       self.acquisition_download_bytes,
                                       self.volume_limit_bytes,
                                       self.max_attempts, self.retry_seconds) <= 0:
            raise ValueError("History limits must be positive; reserve may be zero")
        if not 1 <= self.io_workers <= 4:
            raise ValueError("Public raster I/O concurrency must be between one and four")


def _atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("w") as output:
        output.write(json.dumps(value, indent=2) + "\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def build_tiles(features: list[dict], *, pixel_size=10, tile_size=512, margin=32) -> list[dict]:
    """Deduplicate one fixed UTM grid, retaining a complete core and context border."""
    if pixel_size <= 0 or not 32 <= tile_size <= 512 or not 0 <= margin < tile_size / 2:
        raise ValueError("Tiles need positive resolution, <=512 pixels and a smaller margin")
    core = tile_size - 2 * margin
    span = core * pixel_size
    tiles = {}
    for feature in features:
        zone = shape(feature["geometry"])
        center = zone.centroid
        crs = f"EPSG:{(32600 if center.y >= 0 else 32700) + int((center.x + 180) // 6) + 1}"
        projection = Transformer.from_crs(4326, crs, always_xy=True)
        projected = project_geometry(projection.transform, zone)
        west, south, east, north = projected.bounds
        for column in range(math.floor(west / span), math.ceil(east / span)):
            for row in range(math.floor(south / span), math.ceil(north / span)):
                core_bounds = [column * span, row * span, (column + 1) * span, (row + 1) * span]
                if projected.intersection(box(*core_bounds)).area <= 0:
                    continue
                tile_id = f"{crs.split(':')[1]}-{column}-{row}-{tile_size}-{margin}"
                tile = tiles.setdefault(tile_id, {
                    "tile_id": tile_id, "crs": crs, "size": tile_size,
                    "margin": margin, "pixel_size": pixel_size,
                    "core_bounds": core_bounds,
                    "transform": [pixel_size, 0, core_bounds[0] - margin * pixel_size,
                                  0, -pixel_size, core_bounds[3] + margin * pixel_size],
                    "zone_ids": [],
                })
                tile["zone_ids"].append(feature["properties"]["zone_id"])
    for tile in tiles.values():
        tile["zone_ids"] = sorted(set(tile["zone_ids"]))
    return sorted(tiles.values(), key=lambda tile: tile["tile_id"])


def choose_context(target: Scene, scenes: list[Scene]) -> Scene:
    candidates = []
    for candidate in scenes:
        days = (target.captured_at - candidate.captured_at).total_seconds() / 86400
        if (candidate.sensor != "sentinel-1" or not 30 <= days <= 90
                or candidate.orbit_direction != target.orbit_direction
                or candidate.relative_orbit != target.relative_orbit
                or not set(target.port_ids).issubset(candidate.port_ids)):
            continue
        overlap = shape(candidate.footprint).intersection(shape(target.footprint)).area
        if overlap <= 0:
            continue
        candidates.append((abs(days - 60), -overlap, candidate.acquisition_key, candidate))
    if not candidates:
        raise ValueError("No same-track public context 30–90 days before acquisition")
    return min(candidates, key=lambda item: item[:3])[-1]


def create_plan(catalog: Store, features: list[dict], directory: Path) -> dict:
    """Snapshot the target inventory once, excluding every already observed capture."""
    directory = Path(directory)
    destination = directory / "plan.json"
    if destination.exists():
        existing = json.loads(destination.read_text())
        if existing["zones_sha256"] != hashlib.sha256(json_text(features).encode()).hexdigest():
            raise ValueError("Zone geometry changed; create a separate historical plan directory")
        return existing
    with closing(catalog.connect()) as tx:
        tx.execute("BEGIN")
        scenes = [Scene.model_validate_json(row[0]) for row in tx.execute(
            "SELECT data_json FROM acquisitions ORDER BY captured_at,acquisition_key"
        )]
        protected = [dict(row) for row in tx.execute(
            "SELECT observation_id,acquisition_key,zone_id,recipe_version FROM observations"
        )]
        tx.rollback()
    excluded = {row["acquisition_key"] for row in protected}
    acquisitions = []
    for scene in scenes:
        if (scene.metadata.get("purpose") != "public_history"
                or not 2025 <= scene.captured_at.year <= 2026
                or scene.acquisition_key in excluded):
            continue
        selected = [feature for feature in features
                    if feature["properties"]["port_id"] in scene.port_ids
                    and shape(feature["geometry"]).intersection(shape(scene.footprint)).area > 0]
        item = {"scene": scene.model_dump(mode="json"), "zones": selected,
                "tiles": build_tiles(selected)}
        if scene.sensor == "sentinel-1":
            try:
                item["context"] = choose_context(scene, scenes).model_dump(mode="json")
            except ValueError as exc:
                item["planning_error"] = str(exc)
        acquisitions.append(item)
    identity = {"version": HISTORY_VERSION, "acquisitions": acquisitions,
                "protected_observation_ids": sorted(row["observation_id"] for row in protected),
                "zones_sha256": hashlib.sha256(json_text(features).encode()).hexdigest()}
    plan = {**identity, "plan_id": HISTORY_VERSION + "-" + hashlib.sha256(
        json_text(identity).encode()).hexdigest()[:16], "created_at": utc_text(),
        "protected_observations": protected, "source_database": str(catalog.db_path),
        "publication": "experimental; no local validation; no automatic acceptance"}
    _atomic_json(destination, plan)
    return plan


class HistoryLedger:
    def __init__(self, directory: Path, limits: HistoryLimits):
        self.directory = Path(directory).resolve()
        self.store = Store(self.directory / "progress.sqlite", self.directory)
        self.limits = limits
        with self.store.transaction() as tx:
            tx.execute("""CREATE TABLE IF NOT EXISTS history_acquisitions (
                acquisition_key TEXT PRIMARY KEY, sensor TEXT NOT NULL, plan_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                not_before TEXT NOT NULL, bytes_reserved INTEGER NOT NULL DEFAULT 0,
                started_at TEXT, finished_at TEXT, error_code TEXT, error_message TEXT,
                metrics_json TEXT NOT NULL DEFAULT '{}')""")
        if not (self.directory / "limits.json").exists():
            _atomic_json(self.directory / "limits.json", asdict(limits))

    def initialize_plan(self, plan):
        with self.store.transaction() as tx:
            for item in plan["acquisitions"]:
                scene = item["scene"]
                tx.execute("""INSERT OR IGNORE INTO history_acquisitions
                    (acquisition_key,sensor,plan_json,not_before) VALUES (?,?,?,?)""",
                    (scene["acquisition_key"], scene["sensor"], json_text(item), utc_text()))

    def recover_interrupted(self):
        # The caller must hold campaign.lock; an active process is never preempted.
        with self.store.transaction() as tx:
            tx.execute("""UPDATE history_acquisitions SET status='retry', not_before=?,
                error_code='interrupted',error_message='Previous campaign process exited'
                WHERE status='running'""", (utc_text(),))

    def claim(self, sensor=None, acquisition_key=None, priority_keys=()):
        priority = "CASE acquisition_key " + " ".join(
            f"WHEN ? THEN {index}" for index, _ in enumerate(priority_keys)) + f" ELSE {len(priority_keys)} END," if priority_keys else ""
        with self.store.transaction() as tx:
            row = tx.execute("""SELECT * FROM history_acquisitions
                WHERE status IN ('pending','retry') AND not_before<=?
                AND (? IS NULL OR sensor=?) AND (? IS NULL OR acquisition_key=?)
                ORDER BY """ + priority + """ attempts, json_extract(plan_json,'$.scene.captured_at')
                LIMIT 1""", (utc_text(), sensor, sensor, acquisition_key, acquisition_key, *priority_keys)).fetchone()
            if row is None:
                return None
            tx.execute("""UPDATE history_acquisitions SET status='running', attempts=attempts+1,
                started_at=?, error_code=NULL,error_message=NULL WHERE acquisition_key=?""",
                (utc_text(), row["acquisition_key"]))
            return json.loads(row["plan_json"])

    def waiting_seconds(self, sensor=None, acquisition_key=None):
        with closing(self.store.connect()) as tx:
            stamp = tx.execute("""SELECT min(not_before) FROM history_acquisitions
                WHERE status IN ('pending','retry') AND (? IS NULL OR sensor=?)
                AND (? IS NULL OR acquisition_key=?)""", (sensor, sensor, acquisition_key, acquisition_key)).fetchone()[0]
        return None if stamp is None else max(1, (datetime.fromisoformat(stamp.replace("Z", "+00:00")) - utc_now()).total_seconds())

    def reserve_download(self, key, size):
        if size < 0:
            raise ValueError("Download reservation cannot be negative")
        if shutil.disk_usage(self.directory).free < self.limits.reserve_bytes + size:
            raise ResourceLimit("insufficient_disk: preserve 5 GiB configured reserve")
        with self.store.transaction() as tx:
            used = tx.execute("SELECT sum(bytes_reserved) FROM history_acquisitions").fetchone()[0] or 0
            row = tx.execute("SELECT bytes_reserved FROM history_acquisitions WHERE acquisition_key=?", (key,)).fetchone()
            if row is None:
                raise ValueError("Unknown acquisition in plan")
            if row[0] + size > self.limits.acquisition_download_bytes:
                raise ResourceLimit("acquisition_download_budget")
            if used + size > self.limits.total_download_bytes:
                raise ResourceLimit("campaign_download_budget")
            tx.execute("UPDATE history_acquisitions SET bytes_reserved=bytes_reserved+? WHERE acquisition_key=?", (size, key))

    def finish(self, key, metrics):
        with self.store.transaction() as tx:
            tx.execute("""UPDATE history_acquisitions SET status='complete',finished_at=?,
                metrics_json=?,error_code=NULL,error_message=NULL WHERE acquisition_key=?""",
                (utc_text(), json_text(metrics), key))

    def fail(self, key, exc, *, permanent=False):
        with self.store.transaction() as tx:
            attempts = tx.execute("SELECT attempts FROM history_acquisitions WHERE acquisition_key=?", (key,)).fetchone()[0]
            status = ("paused" if isinstance(exc, ResourceLimit) else
                      "unavailable" if permanent else "failed" if attempts >= self.limits.max_attempts else "retry")
            tx.execute("""UPDATE history_acquisitions SET status=?,not_before=?,error_code=?,
                error_message=? WHERE acquisition_key=?""",
                (status, utc_text(utc_now() + timedelta(seconds=self.limits.retry_seconds * 2 ** (attempts - 1))),
                 type(exc).__name__, str(exc)[:2000], key))
            if isinstance(exc, ResourceLimit):
                tx.execute("UPDATE history_acquisitions SET attempts=max(0,attempts-1) WHERE acquisition_key=?", (key,))
            return status

    def summary(self):
        with closing(self.store.connect()) as tx:
            states = dict(tx.execute("SELECT status,count(*) FROM history_acquisitions GROUP BY status").fetchall())
            used = tx.execute("SELECT sum(bytes_reserved) FROM history_acquisitions").fetchone()[0] or 0
        return {"states": states, "download_bytes_reserved": used,
                "free_bytes": shutil.disk_usage(self.directory).free, "limits": asdict(self.limits)}


@contextmanager
def file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ResourceLimit(f"Another worker holds {path.name}") from exc
        yield
    finally:
        os.close(descriptor)


def _public_json(url: str) -> dict:
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=45) as response:
                body = response.read(4 * 1024**2 + 1)
                if len(body) > 4 * 1024**2:
                    raise ValueError("Public metadata exceeded 4 MiB budget")
                return json.loads(body)
        except (TimeoutError, urllib.error.URLError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code not in {408, 429, 500, 502, 503, 504}:
                raise
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def discover_sources(item: dict) -> dict:
    """Resolve only anonymous public objects, retaining immutable version evidence."""
    scene = Scene.model_validate(item["scene"])
    if scene.sensor == "sentinel-2":
        names = [entry["name"] for entry in scene.metadata["representations"]]
        preferred = sorted(names, reverse=True)[0].removesuffix(".SAFE")
        tile = preferred.split("_")[5].removeprefix("T")
        # Include alternate processing dates of this exact spacecraft/capture/tile.
        prefix = f"tiles/{tile[:2]}/{tile[2]}/{tile[3:]}/" + preferred.split("_N")[0]
        query = {"prefix": prefix, "maxResults": 1000}
        objects = []
        for _ in range(20):
            listing = _public_json("https://storage.googleapis.com/storage/v1/b/gcp-public-data-sentinel-2/o?"
                                   + urllib.parse.urlencode(query))
            objects.extend(listing.get("items", []))
            if not listing.get("nextPageToken"):
                break
            query["pageToken"] = listing["nextPageToken"]
        else:
            raise ValueError("Public scene metadata pagination exceeded explicit page budget")
        folders = sorted({entry["name"].split("/")[4] for entry in objects}, reverse=True)
        folder = next((candidate for candidate in folders if all(
            any(entry["name"].startswith(f"tiles/{tile[:2]}/{tile[2]}/{tile[3:]}/{candidate}/")
                and entry["name"].endswith(f"_{band}.jp2") for entry in objects)
            for band in S2_BANDS)), None)
        if folder is None:
            raise FileNotFoundError(f"GCS has no complete L1C nine-band representation for {scene.acquisition_key}")
        sources = {}
        for band in S2_BANDS:
            entry = next(entry for entry in objects if f"/{folder}/" in entry["name"]
                         and entry["name"].endswith(f"_{band}.jp2"))
            sources[band] = {"url": "https://storage.googleapis.com/gcp-public-data-sentinel-2/"
                            + entry["name"] + "?generation=" + entry["generation"],
                            "size": int(entry["size"]), "generation": entry["generation"],
                            "md5Hash": entry.get("md5Hash"), "format": "jp2"}
        baseline = folder.split("_")[3].removeprefix("N")
        return {"sources": sources, "representation": folder,
                "processing_baseline": f"{int(baseline[:2])}.{baseline[2:]}",
                "provider": "GCS public Sentinel-2 L1C", "metadata_pages": len(objects)}
    if "context" not in item:
        raise FileNotFoundError(item.get("planning_error", "Missing historical radar context"))
    sources = {}
    names = []
    for index, value in enumerate([item["scene"], item["context"]]):
        current = Scene.model_validate(value)
        names_for_scene = sorted(entry["name"].removesuffix(".SAFE")
                                 for entry in current.metadata["representations"]
                                 if "_COG" not in entry["name"])
        if not names_for_scene:
            raise FileNotFoundError(f"No original GRDH representation for {current.acquisition_key}")
        name = names_for_scene[-1]
        names.append(name)
        date = current.captured_at
        for band in ("vv", "vh"):
            url = (f"https://sentinel-s1-l1c.s3.amazonaws.com/GRD/{date.year}/{date.month}/{date.day}"
                   f"/IW/DV/{name}/measurement/iw-{band}.tiff")
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=45) as response:
                key = f"historical_{band}" if index else band
                sources[key] = {"url": url, "size": int(response.headers["Content-Length"]),
                                "etag": response.headers["ETag"], "format": "tiff"}
    return {"sources": sources, "representations": names,
            "provider": "AWS public unsigned Sentinel-1 GRDH"}


class _RangeSource:
    def __init__(self, spec, ledger, key, cache):
        self.spec, self.ledger, self.key = spec, ledger, key
        self.cache = cache / hashlib.sha256(json_text(spec).encode()).hexdigest()[:24]
        self.cache.mkdir(parents=True, exist_ok=True)
        self.bytes = self.requests = self.cached_bytes = 0
        self.lock = threading.Lock()

    def fetch(self, start, end):
        if start < 0 or end <= start or end > self.spec["size"]:
            raise ValueError("Invalid bounded range")
        path = self.cache / f"{start}-{end}.bin"
        checksum = path.with_suffix(".sha256")
        with self.lock:
            if path.exists() and checksum.exists() and path.stat().st_size == end - start:
                if sha256_file(path) == checksum.read_text().strip():
                    self.cached_bytes += end - start
                    return path.read_bytes()
            for attempt in range(3):
                self.ledger.reserve_download(self.key, end - start)
                headers = {"Range": f"bytes={start}-{end - 1}", "Accept-Encoding": "identity"}
                if self.spec.get("etag"):
                    headers["If-Match"] = self.spec["etag"]
                try:
                    request = urllib.request.Request(self.spec["url"], headers=headers)
                    with urllib.request.urlopen(request, timeout=120) as response:
                        expected = f"bytes {start}-{end - 1}/{self.spec['size']}"
                        if response.status != 206 or response.headers.get("Content-Range") != expected:
                            raise ValueError("Provider did not honor the bounded immutable byte range")
                        data = response.read(end - start + 1)
                        if len(data) != end - start:
                            raise OSError("Truncated public asset byte range")
                    self.bytes += len(data)
                    self.requests += 1
                    temporary = path.with_suffix(".part")
                    temporary.write_bytes(data)
                    os.replace(temporary, path)
                    checksum.write_text(hashlib.sha256(data).hexdigest() + "\n")
                    return data
                except (TimeoutError, urllib.error.URLError, OSError) as exc:
                    if isinstance(exc, urllib.error.HTTPError) and exc.code not in {408, 429, 500, 502, 503, 504}:
                        raise
                    if attempt == 2:
                        raise
                    time.sleep(2 ** attempt)


@contextmanager
def _range_server(sources):
    failures = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_HEAD(self):
            source = sources.get(Path(self.path).stem)
            if source is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(source.spec["size"]))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()

        def do_GET(self):
            source = sources.get(Path(self.path).stem)
            request = self.headers.get("Range", "")
            if source is None or not request.startswith("bytes=") or "," in request:
                self.send_error(416)
                return
            try:
                start, end = request.removeprefix("bytes=").split("-")
                start, end = int(start), min(int(end) + 1 if end else source.spec["size"], source.spec["size"])
                data = source.fetch(start, end)
                self.send_response(206)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Range", f"bytes {start}-{end - 1}/{source.spec['size']}")
                self.end_headers()
                self.wfile.write(data)
            except Exception as exc:
                failures.append(exc)
                self.send_error(502, type(exc).__name__)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, failures
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _verified_bundle(path: Path) -> RasterBundle | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    bundle = RasterBundle.model_validate(value)
    hashes = bundle.metadata.get("history_file_hashes", {})
    paths = {**bundle.band_paths, **bundle.mask_paths}
    if not hashes or any(not path.is_file() or sha256_file(path) != hashes.get(name)
                         for name, path in paths.items()):
        return None
    return bundle


def materialize(item: dict, ledger: HistoryLedger, *, quality_models: Path,
                quality=True, device="cpu") -> tuple[list[tuple[dict, RasterBundle]], dict]:
    """Read bounded remote windows; publish verified local tiles for replay."""
    started = time.monotonic()
    scene = Scene.model_validate(item["scene"])
    key = scene.acquisition_key
    directory = ledger.directory / "work" / hashlib.sha256(key.encode()).hexdigest()[:24]
    directory.mkdir(parents=True, exist_ok=True)
    # The worst-case prepared + raw tile footprint is reserved before allocating.
    bands = S2_BANDS if scene.sensor == "sentinel-2" else S1_BANDS
    allocation = len(item["tiles"]) * 512**2 * (len(bands) * 8 + 8)
    if shutil.disk_usage(directory).free < ledger.limits.reserve_bytes + allocation:
        raise ResourceLimit("insufficient_disk")
    size_on_disk = sum(path.stat().st_size for path in ledger.directory.rglob("*")
                       if path.is_file() and not path.is_symlink())
    if size_on_disk + allocation > ledger.limits.volume_limit_bytes:
        raise ResourceLimit("history_volume_budget")
    discovery_path = directory / "sources.json"
    discovery = (json.loads(discovery_path.read_text()) if discovery_path.exists()
                 else discover_sources(item))
    _atomic_json(discovery_path, discovery)
    sources = {name: _RangeSource(spec, ledger, key, directory / "ranges")
               for name, spec in discovery["sources"].items()}
    bundles = {tile["tile_id"]: _verified_bundle(directory / tile["tile_id"] / "prepared" / "bundle.json")
               for tile in item["tiles"]}
    if quality and scene.sensor == "sentinel-2":
        bundles = {key: (bundle if bundle and bundle.metadata.get("quality_model") else None)
                   for key, bundle in bundles.items()}
    pending = [tile for tile in item["tiles"] if bundles[tile["tile_id"]] is None]
    measurements = []
    with _range_server(sources) as (server, failures):
        def materialize_band(pair):
            band, remote = pair
            with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_TIMEOUT=120,
                              GDAL_HTTP_MULTIRANGE="NO", GDAL_NUM_THREADS="1", GDAL_CACHEMAX=64 * 1024**2):
                needed = []
                for tile in pending:
                    target = directory / tile["tile_id"] / "raw" / f"{band}.tif"
                    checksum = target.with_suffix(".sha256")
                    if (not target.exists() or not checksum.exists()
                            or sha256_file(target) != checksum.read_text().strip()):
                        needed.append(tile)
                if not needed:
                    return None
                try:
                    with rasterio.open(f"/vsicurl/http://127.0.0.1:{server.server_port}/{band}.{remote.spec['format']}") as source:
                        gcps, gcp_crs = source.gcps
                        if scene.sensor == "sentinel-1":
                            if source.crs is not None or not gcps or str(gcp_crs) != "EPSG:4326":
                                raise ValueError("S1 raw GRDH must use WGS84 GCP georeferencing")
                            first = gcps[0]
                            native = f"EPSG:{(32600 if first.y >= 0 else 32700) + int((first.x + 180) // 6) + 1}"
                            intermediate = WarpedVRT(source, crs=native, resampling=Resampling.cubic,
                                                     MAX_GCP_ORDER="-1")
                        else:
                            if source.crs is None:
                                raise ValueError("S2 numeric source CRS missing")
                            intermediate = source
                        try:
                            for tile in needed:
                                transform = Affine(*tile["transform"])
                                with WarpedVRT(intermediate, crs=tile["crs"], transform=transform,
                                               width=tile["size"], height=tile["size"], nodata=0,
                                               resampling=Resampling.cubic if scene.sensor == "sentinel-1" else Resampling.nearest) as vrt:
                                    data = vrt.read(1)
                                if failures:
                                    raise failures[0]
                                target = directory / tile["tile_id"] / "raw" / f"{band}.tif"
                                target.parent.mkdir(parents=True, exist_ok=True)
                                temporary = target.with_suffix(".part.tif")
                                with rasterio.open(temporary, "w", driver="GTiff", count=1,
                                                   width=tile["size"], height=tile["size"],
                                                   dtype=str(data.dtype), crs=tile["crs"], transform=transform,
                                                   compress="deflate", nodata=0) as output:
                                    output.write(data, 1)
                                os.replace(temporary, target)
                                target.with_suffix(".sha256").write_text(sha256_file(target) + "\n")
                        finally:
                            if intermediate is not source:
                                intermediate.close()
                except Exception:
                    if failures:
                        raise failures[0]
                    raise
                measurement = {"band": band, "downloaded_bytes": remote.bytes,
                               "range_requests": remote.requests, "cached_bytes": remote.cached_bytes}
                print(json.dumps({"event": "materialized_band", "acquisition": key, **measurement}), flush=True)
                return measurement
        with ThreadPoolExecutor(max_workers=ledger.limits.io_workers) as pool:
            measurements = [result for result in pool.map(materialize_band, sources.items()) if result]
    for tile in pending:
        root = directory / tile["tile_id"]
        metadata = {**scene.metadata, "history_version": HISTORY_VERSION,
                    "history_tile": tile, "source_versions": discovery,
                    "units": {band: "DN_raw" if scene.sensor == "sentinel-2" else "amplitude_dn" for band in bands}}
        if scene.sensor == "sentinel-2":
            metadata["processing_baseline"] = discovery["processing_baseline"]
        else:
            context = Scene.model_validate(item["context"])
            metadata.update(historical_scene_id=context.scene_id,
                            historical_captured_at=context.captured_at.isoformat(),
                            historical_orbit_direction=context.orbit_direction,
                            historical_relative_orbit=context.relative_orbit)
        numeric_scene = scene.model_copy(update={"source_assets": {
            band: str((root / "raw" / f"{band}.tif").resolve()) for band in bands}, "metadata": metadata})
        recipe = "ai2-s2-v1" if scene.sensor == "sentinel-2" else "ai2-s1-v1"
        bundle = prepare(numeric_scene, tile["tile_id"], recipe, output_dir=root / "prepared")
        if quality and scene.sensor == "sentinel-2":
            bundle = add_cloud_shadow_masks(bundle, quality_models, device=device)
        bundle.metadata["history_file_hashes"] = {
            name: sha256_file(path) for name, path in {**bundle.band_paths, **bundle.mask_paths}.items()}
        _atomic_json(root / "prepared" / "bundle.json", bundle.model_dump(mode="json"))
        bundles[tile["tile_id"]] = bundle
    with closing(ledger.store.connect()) as tx:
        reserved = tx.execute("SELECT bytes_reserved FROM history_acquisitions WHERE acquisition_key=?", (key,)).fetchone()[0]
    report = {"acquisition_key": key, "tiles": len(item["tiles"]), "resumed_tiles": len(bundles) - len(pending),
              "downloaded_bytes": sum(source.bytes for source in sources.values()),
              "download_bytes_reserved_cumulative": reserved,
              "range_requests": sum(source.requests for source in sources.values()),
              "seconds": time.monotonic() - started, "bands": measurements,
              "api_processing_units": 0, "quality_enabled": quality, "io_workers": ledger.limits.io_workers,
              "stored_bytes": sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())}
    _atomic_json(directory / "materialization.json", report)
    _atomic_json(directory / f"attempt-{time.time_ns()}.json", report)
    return [(tile, bundles[tile["tile_id"]]) for tile in item["tiles"]], report


def mask_statistics(mask_paths: dict[str, Path]) -> dict:
    """Whole-grid fractions belong to the mosaic, not its first source tile."""
    masks = {}
    for name in ("valid", "observable", "potentially_observable", "cloud", "shadow"):
        if name in mask_paths:
            with rasterio.open(mask_paths[name]) as source:
                masks[name] = source.read(1) > 0
    output = {name + "_fraction": float(masks[name].mean()) if name in masks else None
              for name in ("valid", "observable", "potentially_observable")}
    for name in ("cloud", "shadow"):
        if name in masks:
            values = masks[name] & masks["valid"] if "valid" in masks else masks[name]
            output[name + "_fraction"] = float(values.mean())
    output["quality_fraction_scope"] = "entire_mosaic_grid; observation fraction is computed inside its zone"
    return output


def mosaic_zone(tiles: list[tuple[dict, RasterBundle]], zone_id: str, directory: Path) -> RasterBundle:
    """Join nonoverlapping cores, so margins neither double-count nor overwrite quality."""
    selected = [(tile, bundle) for tile, bundle in tiles if zone_id in tile["zone_ids"]]
    if not selected:
        raise ValueError(f"No prepared tile covers zone {zone_id}")
    reference = selected[0][1]
    if any(bundle.crs != reference.crs for _, bundle in selected):
        raise ValueError("A zone mosaic must use one projected CRS")
    west = min(tile["core_bounds"][0] for tile, _ in selected)
    south = min(tile["core_bounds"][1] for tile, _ in selected)
    east = max(tile["core_bounds"][2] for tile, _ in selected)
    north = max(tile["core_bounds"][3] for tile, _ in selected)
    resolution = selected[0][0]["pixel_size"]
    width, height = round((east - west) / resolution), round((north - south) / resolution)
    if max(width, height) > 4096:
        raise ValueError("Zone mosaic exceeds the current worker's 4096-pixel bound")
    transform = Affine(resolution, 0, west, 0, -resolution, north)
    directory.mkdir(parents=True, exist_ok=True)
    band_paths, mask_paths = {}, {}
    # Missing quality on any input invalidates that mask for the entire mosaic.
    masks = set.intersection(*(set(bundle.mask_paths) for _, bundle in selected))
    for name in [*reference.band_paths, *sorted(masks)]:
        is_band = name in reference.band_paths
        dtype = "float32" if is_band else "uint8"
        target = directory / f"{name}.tif"
        temporary = target.with_suffix(".part.tif")
        data = np.zeros((height, width), dtype=dtype)
        for tile, bundle in selected:
            path = bundle.band_paths[name] if is_band else bundle.mask_paths[name]
            with rasterio.open(path) as source:
                core = tile["size"] - tile["margin"] * 2
                margin = tile["margin"]
                values = source.read(1, window=rasterio.windows.Window(margin, margin, core, core))
            row = round((north - tile["core_bounds"][3]) / resolution)
            column = round((tile["core_bounds"][0] - west) / resolution)
            data[row:row + core, column:column + core] = values
        with rasterio.open(temporary, "w", driver="GTiff", width=width, height=height,
                           count=1, dtype=dtype, transform=transform, crs=reference.crs,
                           nodata=0 if is_band else None, compress="deflate") as output:
            output.write(data, 1)
        os.replace(temporary, target)
        (band_paths if is_band else mask_paths)[name] = target.resolve()
    metadata = {**reference.metadata, "zone_id": zone_id,
                "history_tiles": [tile for tile, _ in selected],
                "quality_flags": sorted(set(flag for _, bundle in selected
                                            for flag in bundle.metadata.get("quality_flags", []))),
                "mosaic_method": "nonoverlapping 448-pixel cores from 512-pixel tiles with32-pixel context"}
    metadata.pop("history_file_hashes", None)
    metadata.pop("history_tile", None)
    for name in ("valid", "observable", "potentially_observable", "cloud", "shadow"):
        metadata.pop(name + "_fraction", None)
    metadata.update(mask_statistics(mask_paths))
    metadata["sha256"] = {name: sha256_file(path) for name, path in band_paths.items()}
    result = reference.model_copy(update={"band_paths": band_paths, "mask_paths": mask_paths,
                                          "transform": tuple(transform)[:6], "metadata": metadata})
    _atomic_json(directory / "bundle.json", result.model_dump(mode="json"))
    return result


def history_model(base: ModelManifest) -> ModelManifest:
    """A distinct processing identity never overwrites the pilot model or observations."""
    return base.model_copy(update={
        "model_id": base.model_id + "-" + HISTORY_VERSION,
        "version": base.version + "+" + HISTORY_VERSION,
        "publication_policy": {**base.publication_policy, "status": "experimental",
                               "automatic_publication": False, "local_validation": "absent"},
        "metadata": {**base.metadata, "history_version": HISTORY_VERSION,
                     "materialization": "complete configured zones;512px tiles;32px margins;10m UTM"},
    })


def verify_published_history_cores(catalog: Store, item: dict, tiles, directory: Path) -> dict:
    """Compare a pinned-source replay with every immutable published core mosaic.

    This verifies the published area, not the former unretained halo bytes.
    A mismatch prevents registration and never replaces the published evidence.
    """
    verified = []
    for feature in item["zones"]:
        zone = feature["properties"]["zone_id"]
        with closing(catalog.connect()) as tx:
            rows = tx.execute("""SELECT a.* FROM assets a JOIN observations o USING(observation_id)
                WHERE o.acquisition_key=? AND o.zone_id=? AND o.model_id LIKE ?
                AND json_extract(a.metadata_json,'$.role') IN ('numeric','mask')""",
                (item["scene"]["acquisition_key"], zone, "%" + HISTORY_VERSION)).fetchall()
        mosaic = mosaic_zone(tiles, zone, directory / zone)
        expected = {**mosaic.band_paths, **mosaic.mask_paths}
        if {Path(row["path"]).stem for row in rows} != set(expected) or len(rows) != len(expected):
            raise ValueError(f"Incomplete or ambiguous published numeric evidence for {zone}")
        for row in rows:
            path = catalog.asset_path(row["asset_id"])
            if sha256_file(path) != row["sha256"]:
                raise ValueError("Published mosaic checksum changed; refusing reconstruction")
            with rasterio.open(path) as original, rasterio.open(expected[path.stem]) as replay:
                if (original.shape != replay.shape or original.crs != replay.crs
                        or original.transform != replay.transform
                        or not np.array_equal(original.read(), replay.read(), equal_nan=True)):
                    raise ValueError(f"Reconstructed core pixels differ from published evidence: {zone}/{path.name}")
            verified.append({"asset_id": row["asset_id"], "sha256": row["sha256"]})
    if not verified:
        raise ValueError("No published cores exist to verify reconstruction")
    return {"core_pixel_match_verified": True, "verified_mosaic_files": len(verified),
            "verified_mosaic_assets": verified,
            "verification_scope": "every published band/mask mosaic pixel; original external halos were not retained"}


def repair_history_evidence(catalog: Store, plan: dict, ledger: HistoryLedger, *,
                            priority_keys: list[str], quality_models: Path, device="cpu") -> dict:
    """Retain existing original tiles or reconstruct completed priority acquisitions.

    Requires the campaign lock and fixed sources already on disk. Never mutates
    observations, detections, reviews, or their published raster evidence.
    """
    from .ml.history_evidence import _load_retained, retain_history_evidence

    output = {"version": "history-evidence-repair-v1", "acquisitions": [], "errors": []}
    with file_lock(ledger.directory / "campaign.lock"), file_lock(catalog.data_dir / ".heavy-worker.lock"):
        with closing(ledger.store.connect()) as tx:
            completed = {row[0] for row in tx.execute("SELECT acquisition_key FROM history_acquisitions WHERE status='complete'")}
        def original_files_present(item):
            root = ledger.directory / "work" / hashlib.sha256(item["scene"]["acquisition_key"].encode()).hexdigest()[:24]
            return all((root / tile["tile_id"] / "prepared" / "bundle.json").exists() for tile in item["tiles"])

        for item in sorted(plan["acquisitions"], key=lambda value: not original_files_present(value)):
            key = item["scene"]["acquisition_key"]
            if key not in completed:
                continue
            directory = ledger.directory / "work" / hashlib.sha256(key.encode()).hexdigest()[:24]
            originals = [(tile, _verified_bundle(directory / tile["tile_id"] / "prepared" / "bundle.json"))
                         for tile in item["tiles"]]
            original_available = all(bundle is not None for _, bundle in originals)
            if key not in priority_keys and not original_available:
                continue
            with closing(catalog.connect()) as tx:
                retained = tx.execute("SELECT 1 FROM assets WHERE json_extract(metadata_json,'$.role')=? "
                                      "AND json_extract(metadata_json,'$.acquisition_key')=?",
                                      ("history_inference_manifest", key)).fetchone()
            try:
                if retained:
                    _load_retained(catalog, key)
                    output["acquisitions"].append({"acquisition_key": key, "already_retained": True})
                    continue
                if not (directory / "sources.json").exists():
                    raise FileNotFoundError("Pinned original sources missing; discovery is not allowed during reconstruction")
                sources = json.loads((directory / "sources.json").read_text())
                sources_hash = sha256_file(directory / "sources.json")
                print(json.dumps({"event": "history_evidence_repair_started", "acquisition_key": key,
                                  "original_prepared_available": original_available}), flush=True)
                if original_available:
                    tiles, measurements = originals, {"downloaded_bytes": 0}
                    provenance = {"kind": "retained_original_prepared_bytes", "original_halo_hash_available": True}
                else:
                    tiles, measurements = materialize(item, ledger, quality_models=quality_models, device=device)
                    if sha256_file(directory / "sources.json") != sources_hash:
                        raise ValueError("Pinned source description changed during reconstruction")
                    provenance = {"kind": "reconstructed_from_pinned_sources", "original_halo_hash_available": False,
                                  **verify_published_history_cores(catalog, item, tiles, directory / "core-comparison")}
                pointer = retain_history_evidence(catalog, item, tiles, sources=sources, provenance=provenance,
                                                  reserve_bytes=ledger.limits.reserve_bytes,
                                                  volume_limit_bytes=ledger.limits.volume_limit_bytes)
                report = {"acquisition_key": key, "history_inference": pointer, "provenance": provenance,
                          "downloaded_bytes": measurements["downloaded_bytes"]}
                output["acquisitions"].append(report)
                print(json.dumps({"event": "history_evidence_repaired", **report}), flush=True)
                for path in directory.iterdir():
                    if path.is_dir():
                        shutil.rmtree(path)
            except Exception as exc:
                error = {"acquisition_key": key, "type": type(exc).__name__, "reason": str(exc)}
                output["errors"].append(error)
                print(json.dumps({"event": "history_evidence_repair_failed", **error}), flush=True)
                if isinstance(exc, ResourceLimit):
                    break
            finally:
                _atomic_json(ledger.directory / "evidence-repair.json", output)
    return output


def infer_tiles(tiles, model, detector, *, device="cpu"):
    from .ml.detection import detect

    candidates = []
    for tile, bundle in tiles:
        to_utm = Transformer.from_crs(4326, tile["crs"], always_xy=True)
        west, south, east, north = tile["core_bounds"]
        prediction_path = next(iter(bundle.band_paths.values())).parent / "predictions.json"
        identity = processing_recipe(model)
        hashes = bundle.metadata["history_file_hashes"]
        cached = json.loads(prediction_path.read_text()) if prediction_path.exists() else {}
        if cached.get("recipe") == identity and cached.get("input_hashes") == hashes:
            from .contracts import Prediction
            predictions = [Prediction.model_validate(value) for value in cached["predictions"]]
        else:
            predictions = detect(bundle, model, detector=detector, device=device)
            _atomic_json(prediction_path, {"recipe": identity, "input_hashes": hashes,
                                         "predictions": [p.model_dump(mode="json") for p in predictions]})
        for prediction in predictions:
            center = shape(prediction.geometry).centroid
            x, y = to_utm.transform(center.x, center.y)
            if west <= x < east and south <= y < north:
                prediction.metadata["history_tile_id"] = tile["tile_id"]
                candidates.append((prediction, tile["crs"], x, y))
    kept = []
    # Same upstream 100m center merge, now across tile boundaries as well.
    for candidate in sorted(candidates, key=lambda value: value[0].detection_score, reverse=True):
        if all(candidate[1] != prior[1] or (candidate[2] - prior[2])**2 + (candidate[3] - prior[3])**2 >= 100**2
               for prior in kept):
            kept.append(candidate)
    return [value[0] for value in kept]


def publish_acquisition(catalog, item, tiles, predictions, model, *, protected_ids,
                        runtime=None):
    """Publish through the existing fenced worker while retaining immutable inputs."""
    from .worker import process_once

    scene = Scene.model_validate(item["scene"])
    with closing(catalog.connect()) as tx:
        existing_ids = {row[0] for row in tx.execute(
            "SELECT observation_id FROM observations WHERE acquisition_key=?",
            (scene.acquisition_key,))}
    if existing_ids.intersection(protected_ids):
        raise ValueError("Refusing to reprocess a protected pilot acquisition")
    recipe = processing_recipe(model)
    runtime = runtime or RuntimeLimits(reserve_bytes=5 * 1024**3)
    queue = JobQueue(catalog, runtime=runtime)
    results = []
    work = next(iter(tiles[0][1].band_paths.values())).parent.parent.parent / "zones"
    for feature in item["zones"]:
        zone = feature["properties"]["zone_id"]
        with closing(catalog.connect()) as tx:
            existing = tx.execute("""SELECT o.observation_id,o.review_status,j.status FROM observations o
                JOIN jobs j USING(observation_id) WHERE acquisition_key=? AND zone_id=? AND recipe_version=?""",
                (scene.acquisition_key, zone, recipe)).fetchone()
        if existing and existing["observation_id"] in protected_ids:
            raise ValueError("Refusing to touch a protected pilot observation")
        if existing and existing["status"] == "succeeded":
            results.append({"observation_id": existing["observation_id"], "resumed": True})
            continue
        bundle = mosaic_zone(tiles, zone, work / zone)
        if item.get("history_inference"):
            bundle.metadata["history_inference"] = item["history_inference"]
        # Register only the original catalogue scene. Prepared metadata belongs to
        # this job input, so another zone can never rewrite acquisition metadata.
        job_id = queue.enqueue(scene, zone, recipe, port_id=feature["properties"]["port_id"],
                               estimated_bytes=sum(path.stat().st_size for path in {
                                   **bundle.band_paths, **bundle.mask_paths}.values()))
        with catalog.transaction() as tx:
            row = tx.execute("SELECT status,data_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row["status"] in {"pending", "retry", "paused"}:
                value = json.loads(row["data_json"])
                value["input_scene"]["metadata"]["prepared_bundle"] = bundle.model_dump(mode="json")
                value["input_scene"]["metadata"]["history_version"] = HISTORY_VERSION
                tx.execute("UPDATE jobs SET data_json=? WHERE job_id=?", (json_text(value), job_id))

        def predictor(input_bundle, manifest):
            if input_bundle.metadata.get("acquisition_key") != scene.acquisition_key:
                raise ValueError("Refusing predictions from another acquisition")
            output = []
            for prediction in predictions:
                copy = prediction.model_copy(deep=True)
                copy.prediction_id = hashlib.sha256(
                    f"{input_bundle.observation_id}:{prediction.prediction_id}".encode()).hexdigest()[:24]
                output.append(copy)
            return output

        while True:
            result = process_once(catalog, model, predictor=predictor, runtime=runtime,
                                  acquisition_key=scene.acquisition_key)
            scheduled = queue.get_job(job_id)
            if scheduled["status"] == "succeeded":
                break
            if result is None:
                with closing(catalog.connect()) as tx:
                    active = tx.execute("SELECT min(lease_expires_at) FROM jobs WHERE status='running'").fetchone()[0]
                if active:
                    # A dead process may leave an unexpired fenced lease. Wait
                    # for expiry instead of exhausting acquisition retries or
                    # stealing a live worker's job.
                    remaining = (datetime.fromisoformat(active.replace("Z", "+00:00")) - utc_now()).total_seconds()
                    time.sleep(max(1, min(30, remaining + 1)))
                    continue
            if scheduled["status"] == "paused":
                raise ResourceLimit(scheduled.get("error_code") or "worker_resource_budget")
            raise OSError(f"Zone worker incomplete for {zone}: {result}")
        results.append(result)
    return results


def run_history(catalog: Store, plan: dict, ledger: HistoryLedger, models: dict[str, ModelManifest],
                *, max_acquisitions=1, sensor=None, quality_models: Path, device="cpu",
                materialize_only=False, quality=True, keep_work=False, acquisition_key=None,
                priority_file: Path | None = None):
    """Execute one campaign serially, resuming completed numeric/published work."""
    detectors = {}
    processed = 0
    with file_lock(ledger.directory / "campaign.lock"):
        _atomic_json(ledger.directory / "limits.json", asdict(ledger.limits))
        ledger.recover_interrupted()
        while processed < max_acquisitions:
            if (ledger.directory / "PAUSE").exists():
                print(json.dumps({"event": "campaign_paused", "reason": "PAUSE file at acquisition boundary"}), flush=True)
                break
            priority_keys = []
            if priority_file and priority_file.exists():
                supplied = json.loads(priority_file.read_text())
                priority_keys = supplied if isinstance(supplied, list) else supplied["acquisition_keys"]
                if not isinstance(priority_keys, list) or not all(isinstance(key, str) for key in priority_keys):
                    raise ValueError("Priority file must contain an acquisition_keys string list")
            item = ledger.claim(sensor, acquisition_key, priority_keys)
            if item is None:
                waiting = ledger.waiting_seconds(sensor, acquisition_key)
                if waiting is None:
                    break
                time.sleep(min(30, waiting))
                continue
            key = item["scene"]["acquisition_key"]
            selected_sensor = item["scene"]["sensor"]
            print(json.dumps({"event": "acquisition_started", "acquisition": key,
                              "sensor": selected_sensor, "zones": len(item["zones"])}), flush=True)
            started = time.monotonic()
            terminal = True
            try:
                if item.get("planning_error"):
                    raise FileNotFoundError(item["planning_error"])
                with file_lock(catalog.data_dir / ".heavy-worker.lock"):
                    tiles, report = materialize(item, ledger, quality_models=quality_models,
                                                quality=quality, device=device)
                    if materialize_only:
                        with ledger.store.transaction() as tx:
                            tx.execute("""UPDATE history_acquisitions SET status='pending',metrics_json=?
                                WHERE acquisition_key=?""", (json_text(report), key))
                        print(json.dumps({"event": "materialized_only", **report}), flush=True)
                        return ledger.summary()
                    from .ml.history_evidence import retain_history_evidence

                    directory = ledger.directory / "work" / hashlib.sha256(key.encode()).hexdigest()[:24]
                    item["history_inference"] = retain_history_evidence(
                        catalog, item, tiles, sources=json.loads((directory / "sources.json").read_text()),
                        reserve_bytes=ledger.limits.reserve_bytes,
                        volume_limit_bytes=ledger.limits.volume_limit_bytes)
                    report["history_inference"] = item["history_inference"]
                    model = history_model(models[selected_sensor])
                    if selected_sensor not in detectors:
                        from .ml.detection import load_detector
                        # Release the other sensor's weights; one heavy model at a time.
                        detectors.clear()
                        detectors[selected_sensor] = load_detector(model, device=device)
                    predictions = infer_tiles(tiles, model, detectors[selected_sensor], device=device)
                results = publish_acquisition(catalog, item, tiles, predictions, model,
                                              protected_ids=set(plan["protected_observation_ids"]),
                                              runtime=RuntimeLimits(reserve_bytes=ledger.limits.reserve_bytes,
                                                                    volume_limit_bytes=ledger.limits.volume_limit_bytes))
                report.update(observations=results, experimental_proposals=len(predictions),
                              total_seconds=time.monotonic() - started)
                ledger.finish(key, report)
                print(json.dumps({"event": "acquisition_complete", **report}), flush=True)
                if not keep_work:
                    directory = ledger.directory / "work" / hashlib.sha256(key.encode()).hexdigest()[:24]
                    # Only generated caches; published evidence is managed by Store.
                    for path in directory.iterdir():
                        if path.is_dir():
                            shutil.rmtree(path)
            except Exception as exc:
                permanent = (isinstance(exc, (FileNotFoundError, ValueError))
                             or isinstance(exc, urllib.error.HTTPError) and exc.code in {401, 403, 404, 410})
                state = ledger.fail(key, exc, permanent=permanent)
                terminal = state != "retry"
                print(json.dumps({"event": "acquisition_failed", "acquisition": key,
                                  "type": type(exc).__name__, "reason": str(exc)}), flush=True)
                if isinstance(exc, ResourceLimit) and str(exc) != "acquisition_download_budget":
                    break
            processed += int(terminal)
            _atomic_json(ledger.directory / "status.json", ledger.summary())
    summary = ledger.summary()
    _atomic_json(ledger.directory / "status.json", summary)
    return summary
