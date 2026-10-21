#!/usr/bin/env python3
"""Reproduce bounded public-data downloads, numeric inference and a frozen training step.

Examples (from the repository root, after ``uv sync --extra ml --extra quality``):
  python scripts/pilot_inference.py weights --model config/models/s2.yaml
  python scripts/pilot_inference.py public-sample
  python scripts/pilot_inference.py crop --scene S2B_MSIL1C_20260910T161859_N0512_R040_T15PYR_20260910T212543 --port prq --lon -90.787 --lat 13.927
  python scripts/pilot_inference.py infer --bundle data/pilot/prq-prepared/bundle.json --model config/models/s2.yaml --quality

The public sample is a known upstream training example, not a local test set.
No command starts a historical bulk download or turns proposals into reviewed labels.
"""

import argparse
import collections
import concurrent.futures
import hashlib
import io
import json
import math
import os
import pickle
import platform
import resource
import struct
import tarfile
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile
import zlib
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import rasterio
import yaml
from pyproj import Transformer
from rasterio.windows import Window, bounds, from_bounds

from puertogt.assets import download_asset, file_hash
from puertogt.catalog import CATALOGUE_URL, CatalogClient, acquisition_key, normalize_products
from puertogt.contracts import RasterBundle, Scene
from puertogt.ml.detection import detect, load_detector, load_manifest
from puertogt.preprocess import (
    OMNICLOUDMASK_WEIGHTS,
    S1_BANDS,
    S2_BANDS,
    add_cloud_shadow_masks,
    prepare,
    read_model_input,
)


class RangeSource(io.RawIOBase):
    """Read a generation-pinned HTTP object through byte ranges with a hard budget."""

    def __init__(self, url, size, budget, *, etag=None):
        self.url, self.size, self.budget, self.pos = url, size, budget, 0
        self.etag = etag
        self.bytes = self.requests = 0
        self.lock = threading.Lock()

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        return self.pos

    def fetch(self, start, end):
        if start < 0 or end > self.size or end < start:
            raise ValueError("invalid byte range")
        if end == start:
            return b""
        with self.lock:
            if self.bytes + end - start > self.budget:
                raise ValueError("range download exceeds the explicit byte budget")
            self.bytes += end - start
            self.requests += 1
        headers = {"Range": f"bytes={start}-{end - 1}"}
        if self.etag:
            headers["If-Match"] = self.etag
        request = urllib.request.Request(self.url, headers=headers)
        with urllib.request.urlopen(request, timeout=120) as response:
            if response.status != 206 or not response.headers.get("Content-Range", "").startswith(
                f"bytes {start}-{end - 1}/"
            ):
                raise ValueError("provider did not honor the requested byte range")
            data = response.read(end - start + 1)
        if len(data) != end - start:
            raise ValueError("truncated HTTP byte range")
        return data

    def read(self, size=-1):
        end = self.size if size < 0 else min(self.size, self.pos + size)
        data = self.fetch(self.pos, end)
        self.pos = end
        return data


class StorageType:
    def __init__(self, name):
        self.name = name


class TensorReference:
    def __init__(self, storage, offset, size, stride, *unused):
        self.storage, self.offset, self.size, self.stride = storage, offset, size, stride


class MetadataUnpickler(pickle.Unpickler):
    """Interpret checkpoint tensor metadata without importing or executing pickle globals."""

    def find_class(self, module, name):
        if module == "collections" and name == "OrderedDict":
            return collections.OrderedDict
        if module == "torch" and name.endswith("Storage"):
            return StorageType(name)
        if module == "torch._utils" and name in {"_rebuild_tensor", "_rebuild_tensor_v2"}:
            return TensorReference
        raise ValueError(f"unsupported checkpoint metadata global: {module}.{name}")

    def persistent_load(self, value):
        if value[0] != "storage":
            raise ValueError("unsupported checkpoint persistent object")
        return {"key": value[2], "dtype": value[1].name, "numel": value[4]}


def fetch_weights(args):
    """Keep every state_dict tensor, omit optimizer states, verify every ZIP CRC32."""
    import torch

    config_path = Path(args.model)
    config = yaml.safe_load(config_path.read_text())
    metadata = config["metadata"]
    output = Path(config["weights_path"])
    if output.exists() and config.get("weights_sha256"):
        if file_hash(output) != config["weights_sha256"]:
            raise ValueError("existing checkpoint SHA256 mismatch")
        print(json.dumps({"cached": True, "path": str(output), "sha256": file_hash(output)}))
        return
    url = metadata["checkpoint_url"]
    if "generation=" not in url:
        raise ValueError("checkpoint URL must pin an immutable GCS generation")
    with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30) as response:
        size = int(response.headers["Content-Length"])
        md5 = response.headers["ETag"].strip('"')
    if size != metadata["checkpoint_bytes"] or md5 != metadata["checkpoint_md5"]:
        raise ValueError("pinned provider metadata changed")
    source = RangeSource(url, size, 550 * 1024 * 1024)
    started = time.monotonic()
    archive = zipfile.ZipFile(source)
    infos = archive.infolist()
    pickled = archive.read(next(i.filename for i in infos if i.filename.endswith("data.pkl")))
    state_refs = MetadataUnpickler(io.BytesIO(pickled)).load()["state_dict"]
    keys = {ref.storage["key"] for ref in state_refs.values()}
    members = sorted([i for i in infos if "/data/" in i.filename and i.filename.rsplit("/", 1)[1] in keys],
                     key=lambda item: item.header_offset)
    if len(members) != len(keys) or any(i.compress_type != zipfile.ZIP_STORED for i in members):
        raise ValueError("unsupported checkpoint storage layout")
    groups = []
    for item in members:
        start, end = item.header_offset, item.header_offset + 30 + len(item.filename) + 128 + item.file_size
        if groups and start - groups[-1][1] <= 65536 and end - groups[-1][0] < 32 * 1024 * 1024:
            groups[-1][1] = max(groups[-1][1], end)
        else:
            groups.append([start, end])
    cache = Path("tmp/pilot") / (config["model_id"] + "-ranges")
    cache.mkdir(parents=True, exist_ok=True)

    def fetch(pair):
        start, end = pair
        path = cache / f"{start}-{end}.bin"
        if not path.exists() or path.stat().st_size != end - start:
            path.write_bytes(source.fetch(start, end))
        return path

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(fetch, groups))
    storages = {}
    dtypes = {"FloatStorage": torch.float32, "LongStorage": torch.int64,
              "IntStorage": torch.int32, "DoubleStorage": torch.float64,
              "HalfStorage": torch.float16, "ByteStorage": torch.uint8,
              "BoolStorage": torch.bool, "BFloat16Storage": torch.bfloat16}
    for item in members:
        member_end = item.header_offset + 30 + len(item.filename) + 128 + item.file_size
        start, end = next((a, b) for a, b in groups if a <= item.header_offset and b >= member_end)
        with (cache / f"{start}-{end}.bin").open("rb") as src:
            src.seek(item.header_offset - start)
            header = struct.unpack("<IHHHHHIIIHH", src.read(30))
            if header[0] != 0x04034B50:
                raise ValueError("invalid ZIP local header")
            src.seek(header[-2] + header[-1], 1)
            data = src.read(item.file_size)
        if len(data) != item.file_size or zlib.crc32(data) != item.CRC:
            raise ValueError(f"checkpoint member CRC32 mismatch: {item.filename}")
        storages[item.filename.rsplit("/", 1)[1]] = bytearray(data)
    state = {}
    for key, ref in state_refs.items():
        storage = torch.frombuffer(storages[ref.storage["key"]], dtype=dtypes[ref.storage["dtype"]])
        state[key] = torch.as_strided(storage, ref.size, ref.stride, ref.offset).clone()
    output.parent.mkdir(parents=True, exist_ok=True)
    # PyTorch embeds the file's basename in its ZIP members. Pin it so a fresh
    # extraction reproduces the already-reviewed SHA256 even when output moves.
    archive_prefix = metadata.get("serialization_archive_prefix", output.stem)
    if Path(archive_prefix).name != archive_prefix or archive_prefix in {"", ".", ".."}:
        raise ValueError("invalid serialization archive prefix")
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary_dir:
        temporary = Path(temporary_dir) / f"{archive_prefix}.ckpt"
        torch.save({"state_dict": state}, temporary)
        actual_hash = file_hash(temporary)
        if config.get("weights_sha256") and actual_hash != config["weights_sha256"]:
            raise ValueError("rebuilt checkpoint SHA256 differs from pinned artifact")
        os.replace(temporary, output)
    config["weights_sha256"] = file_hash(output)
    config["metadata"]["checkpoint_derivation"] = (
        f"All {len(state)} state_dict tensors from pinned ZIP; per-member CRC32 verified; "
        "optimizer and loop states omitted; tensor values unchanged."
    )
    config["metadata"]["inference_checkpoint_bytes"] = output.stat().st_size
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    report = {"model_id": config["model_id"], "source_url": url, "provider_md5": md5,
              "whole_source_md5_recomputed": False, "tensor_count": len(state),
              "member_crc32_verified": True, "sha256": config["weights_sha256"],
              "stored_bytes": output.stat().st_size, "downloaded_bytes_this_attempt": source.bytes,
              "checkpoint_range_bytes": sum(end - start for start, end in groups),
              "download_note": "Cached ranges may come from an earlier attempt; this counter is not cumulative network traffic.",
              "range_requests": source.requests, "seconds": time.monotonic() - started}
    Path("reports/pilot").mkdir(parents=True, exist_ok=True)
    Path(f"reports/pilot/{config['model_id']}-download.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


def public_sample(args):
    url = "https://storage.googleapis.com/ai2-rslearn-projects-data/sentinel2_vessels/dataset_20250213.tar?generation=1772126779837328"
    source = RangeSource(url, 239963688960, 8388608)
    payload = source.fetch(0, 8388608)
    if hashlib.sha256(payload).hexdigest() != "36f041e5dfc81e01b1183b58d960e30699c4663f3d161d67454044609b1dc60c":
        raise ValueError("public S2 reference prefix SHA256 mismatch")
    prefix = "20250213/windows/split3/3535872_1626112_130313/"
    destination = Path("data/pilot/public-reference")
    destination.mkdir(parents=True, exist_ok=True)
    archive = tarfile.open(fileobj=io.BytesIO(payload), mode="r:")
    captured = False
    for item in archive:
        if item.name.startswith(prefix):
            captured = True
            suffix = item.name[len(prefix):]
            if not item.isfile() or not suffix.endswith((".json", ".geojson", ".tif")):
                continue
            path = destination / suffix
            if ".." in Path(suffix).parts or Path(suffix).is_absolute():
                raise ValueError("unsafe archive path")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.extractfile(item).read())
        elif captured:
            break
    meta = json.loads((destination / "metadata.json").read_text())
    item = json.loads((destination / "items.json").read_text())[0]["serialized_item_groups"][0][0]
    high = destination / "layers/sentinel2/B02_B03_B04_B08/geotiff.tif"
    low = destination / "layers/sentinel2/B05_B06_B07_B8A_B11_B12/geotiff.tif"
    indices = {"B02": 1, "B03": 2, "B04": 3, "B08": 4, "B05": 1, "B06": 2, "B07": 3, "B11": 5, "B12": 6}
    scene = Scene(
        scene_id=item["name"], acquisition_key=acquisition_key(item["name"]), sensor="sentinel-2",
        product_level="L1C", captured_at=datetime.fromisoformat(meta["time_range"][0]),
        footprint={"type": "Polygon", "coordinates": []},
        source_assets={band: str(high if band in {"B02", "B03", "B04", "B08"} else low) for band in S2_BANDS},
        metadata={"units": {band: "DN_harmonized" for band in S2_BANDS}, "band_indices": indices,
                  "pixel_window": [270, 256, 512, 512], "reference_license": "CC-BY-4.0",
                  "reference_source": url, "reference_split": "split3 (upstream training)",
                  "local_validation": False},
    )
    (destination / "scene.json").write_text(scene.model_dump_json(indent=2))
    prepared = Path("data/pilot/upstream-prepared")
    bundle = prepare(scene, "upstream_reference", "ai2-s2-v1", output_dir=prepared)
    boxes = []
    for feature in json.loads((destination / "layers/label/data.geojson").read_text())["features"]:
        x, y = feature["geometry"]["coordinates"]
        x, y = x - meta["bounds"][0] - 270, y - meta["bounds"][1] - 256
        if 0 <= x < 512 and 0 <= y < 512:
            boxes.append([x - 15, y - 15, x + 15, y + 15])
    (prepared / "reference_boxes.json").write_text(json.dumps(boxes, indent=2))
    print(bundle.model_dump_json(indent=2))


def public_sample_s1(args):
    """First public raw-GRD training window; no display imagery is loaded as input."""
    import numpy as np
    from PIL import Image

    dataset_url = "https://storage.googleapis.com/ai2-olmoearth-projects-public-data/evals/partner_tasks/sentinel1_vessels.tar?generation=1762098182799064"
    cached = Path("tmp/pilot/s1-dataset-head.tar")
    if cached.exists() and cached.stat().st_size == 8388608:
        payload = cached.read_bytes()
    else:
        payload = RangeSource(dataset_url, 9589923840, 8388608).fetch(0, 8388608)
    if hashlib.sha256(payload).hexdigest() != "62483e5d1f534f454daead986ef359a5b2e2dd7dff2b9ede363b957f28c877ea":
        raise ValueError("public S1 reference prefix SHA256 mismatch")
    prefix = "windows/val_ascending/1189838_1832353_1301/"
    destination = Path("data/pilot/s1-public-reference")
    destination.mkdir(parents=True, exist_ok=True)
    captured = False
    for item in tarfile.open(fileobj=io.BytesIO(payload), mode="r:"):
        if item.name.startswith(prefix):
            captured = True
            suffix = item.name[len(prefix):]
            if not item.isfile() or not (suffix.endswith((".json", ".geojson", ".tif"))
                                        or suffix == "layers/mask/mask/image.png"):
                continue
            if ".." in Path(suffix).parts or Path(suffix).is_absolute():
                raise ValueError("unsafe archive path")
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
                path = destination / suffix
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.extractfile(item).read())
        elif captured:
            break
    metadata = json.loads((destination / "metadata.json").read_text())
    items = {entry["layer_name"]: entry["serialized_item_groups"]
             for entry in json.loads((destination / "items.json").read_text())}
    current, historical = items["sentinel1"][0][0], items["sentinel1_historical"][0][0]
    catalog = []
    for entry in (current, historical):
        url = f"{CATALOGUE_URL}({entry['product_uuid']})?$expand=Attributes"
        record = CatalogClient().fetch_json(url)
        (destination / f"catalog-{entry['product_uuid']}.json").write_text(json.dumps(record, indent=2))
        catalog.append({item["Name"]: item["Value"] for item in record["Attributes"]})
    if catalog[0]["orbitDirection"] != catalog[1]["orbitDirection"]:
        raise ValueError("public reference has incompatible target/historical orbits")
    current_path = destination / "layers/sentinel1/vv_vh/geotiff.tif"
    historical_path = destination / "layers/sentinel1_historical/vv_vh/geotiff.tif"
    # This PNG is an annotation coverage mask, never a satellite model input.
    annotation_mask = np.asarray(Image.open(destination / "layers/mask/mask/image.png"))
    if not (annotation_mask[150:662, 150:662] > 0).all():
        raise ValueError("the negative training crop includes unannotated pixels")
    scene = Scene(
        scene_id=current["name"], acquisition_key=acquisition_key(current["name"]),
        sensor="sentinel-1", product_level="IW_GRDH_1SDV",
        captured_at=datetime.fromisoformat(current["geometry"]["time_range"][0]),
        orbit_direction=catalog[0]["orbitDirection"],
        footprint={"type": "Polygon", "coordinates": []},
        source_assets={key: str(historical_path if key.startswith("historical") else current_path)
                       for key in S1_BANDS},
        metadata={"units": {key: "amplitude_dn" for key in S1_BANDS},
                  "band_indices": {"vv": 1, "vh": 2, "historical_vv": 1, "historical_vh": 2},
                  "pixel_window": [150, 150, 512, 512], "reference_license": "CC-BY-4.0",
                  "reference_source": dataset_url, "reference_split": metadata["options"]["olmoearth_evals_split"],
                  "local_validation": False, "historical_scene_id": historical["name"],
                  "training_annotation_mask_coverage": 1.0,
                  "historical_captured_at": historical["geometry"]["time_range"][0],
                  "historical_orbit_direction": catalog[1]["orbitDirection"],
                  "relative_orbit": catalog[0]["relativeOrbitNumber"],
                  "historical_relative_orbit": catalog[1]["relativeOrbitNumber"]},
    )
    (destination / "scene.json").write_text(scene.model_dump_json(indent=2))
    directory = Path("data/pilot/s1-upstream-prepared")
    bundle = prepare(scene, "upstream_reference", "ai2-s1-v1", output_dir=directory)
    labels = json.loads((destination / "layers/label/data.geojson").read_text())["features"]
    if labels:
        raise ValueError("this bounded sample expected the documented empty reference window")
    (directory / "reference_boxes.json").write_text("[]\n")
    print(bundle.model_dump_json(indent=2))


def start_range_server(sources, byte_budget):
    """Expose measured, bounded remote reads to GDAL on loopback only."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_HEAD(self):
            band = Path(self.path).stem
            if band not in sources:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(sources[band].size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()

        def do_GET(self):
            band = Path(self.path).stem
            if band not in sources:
                self.send_error(404)
                return
            source = sources[band]
            requested = self.headers.get("Range", "")
            if not requested.startswith("bytes=") or "," in requested:
                self.send_error(416, "a single explicit byte range is required")
                return
            start, end = requested.removeprefix("bytes=").split("-")
            start, end = int(start), min(int(end) + 1 if end else source.size, source.size)
            if sum(item.bytes for item in sources.values()) + end - start > byte_budget:
                self.send_error(413, "scene exceeds the byte budget")
                return
            try:
                payload = source.fetch(start, end)
            except Exception as exc:
                self.send_error(502, str(exc))
                return
            self.send_response(206)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Content-Range", f"bytes {start}-{end - 1}/{source.size}")
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def crop(args):
    if not 32 <= args.size <= 4096:
        raise ValueError("crop size must be 32–4096 pixels")
    scene_id = args.scene.removesuffix(".SAFE")
    tile = scene_id.split("_")[5].removeprefix("T")
    prefix = f"tiles/{tile[:2]}/{tile[2]}/{tile[3:]}/{scene_id}.SAFE/"
    query = urllib.parse.urlencode({"prefix": prefix, "maxResults": 1000})
    with urllib.request.urlopen("https://storage.googleapis.com/storage/v1/b/gcp-public-data-sentinel-2/o?" + query, timeout=30) as response:
        listing = json.load(response)
    if "nextPageToken" in listing:
        raise ValueError("unexpectedly large scene listing: explicit pagination required")
    destination = Path("data/pilot") / f"{args.port}-source"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "gcs-listing.json").write_text(json.dumps(listing, indent=2))
    sources = {}
    for band in S2_BANDS:
        item = next(item for item in listing.get("items", []) if item["name"].endswith(f"_{band}.jp2"))
        url = "https://storage.googleapis.com/gcp-public-data-sentinel-2/" + item["name"] + "?generation=" + item["generation"]
        sources[band] = RangeSource(url, int(item["size"]), args.max_download_mb * 1024 * 1024)

    server = start_range_server(sources, args.max_download_mb * 1024 * 1024)
    source_assets = {}
    records = []
    reference_bounds = None
    started = time.monotonic()
    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_TIMEOUT=120):
            for band in S2_BANDS:
                with rasterio.open(f"/vsicurl/http://127.0.0.1:{server.server_port}/{band}.jp2") as src:
                    if reference_bounds is None:
                        x, y = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True).transform(args.lon, args.lat)
                        row, col = src.index(x, y)
                        window = Window(col - args.size // 2, row - args.size // 2, args.size, args.size)
                        reference_bounds = bounds(window, src.transform)
                    else:
                        floating = from_bounds(*reference_bounds, transform=src.transform)
                        col, row = math.floor(floating.col_off), math.floor(floating.row_off)
                        window = Window(col, row, math.ceil(floating.col_off + floating.width) - col,
                                        math.ceil(floating.row_off + floating.height) - row)
                    data = src.read(1, window=window)
                    path = destination / f"{band}.tif"
                    with rasterio.open(path, "w", driver="GTiff", count=1, dtype=str(data.dtype),
                                       width=data.shape[1], height=data.shape[0], crs=src.crs,
                                       transform=src.window_transform(window), compress="deflate", nodata=0) as dst:
                        dst.write(data, 1)
                source_assets[band] = str(path)
                records.append({"band": band, "source_url": sources[band].url,
                                "source_bytes": sources[band].size, "downloaded_bytes": sources[band].bytes,
                                "range_requests": sources[band].requests, "local_bytes": path.stat().st_size,
                                "sha256": file_hash(path)})
                print(json.dumps(records[-1]), flush=True)
    finally:
        server.shutdown()
        server.server_close()
    baseline = scene_id.split("_")[3].removeprefix("N")
    scene = Scene(
        scene_id=scene_id, acquisition_key=acquisition_key(scene_id), sensor="sentinel-2", product_level="L1C",
        captured_at=datetime.strptime(scene_id.split("_")[2], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc),
        footprint={"type": "Polygon", "coordinates": [[
            list(Transformer.from_crs(src.crs, "EPSG:4326", always_xy=True).transform(x, y))
            for x, y in [(reference_bounds[0], reference_bounds[1]),
                         (reference_bounds[2], reference_bounds[1]),
                         (reference_bounds[2], reference_bounds[3]),
                         (reference_bounds[0], reference_bounds[3]),
                         (reference_bounds[0], reference_bounds[1])]
        ]]},
        provider="copernicus_catalog/gcp_public_assets", port_ids=[args.port], source_assets=source_assets,
        metadata={"units": {band: "DN_raw" for band in S2_BANDS},
                  "processing_baseline": f"{int(baseline[:2])}.{baseline[2:]}",
                  "catalog_product_uuid": args.product_uuid, "sources": records,
                  "capture_time_source": "Sentinel product name UTC", "footprint_role": "numeric_crop"},
    )
    (destination / "scene.json").write_text(scene.model_dump_json(indent=2))
    report = {"scene_id": scene_id, "downloaded_bytes": sum(source.bytes for source in sources.values()),
              "range_requests": sum(source.requests for source in sources.values()),
              "stored_bytes": sum(record["local_bytes"] for record in records),
              "seconds": time.monotonic() - started, "api_processing_units": 0,
              "provider": "GCS public bucket (HTTP ranges)", "bands": records}
    (destination / "download.json").write_text(json.dumps(report, indent=2))
    bundle = prepare(scene, f"{args.port}_pilot", "ai2-s2-v1", output_dir=Path(f"data/pilot/{args.port}-prepared"))
    print(json.dumps({"bundle": f"data/pilot/{args.port}-prepared/bundle.json", "download": report,
                      "valid_fraction": bundle.metadata["valid_fraction"]}, indent=2))


def infer(args):
    import torch

    torch.set_num_threads(4)
    torch.manual_seed(0)
    bundle_path = Path(args.bundle)
    bundle = RasterBundle.model_validate_json(bundle_path.read_text())
    model = load_manifest(Path(args.model))
    report = {"device": args.device, "torch": torch.__version__, "sample": bundle.metadata.get("scene_id")}
    if args.quality:
        started = time.monotonic()
        bundle = add_cloud_shadow_masks(bundle, Path(args.quality_models), device=args.device)
        report["quality_seconds"] = time.monotonic() - started
    started = time.monotonic()
    detector = load_detector(model, device=args.device)
    report["load_seconds"] = time.monotonic() - started
    started = time.monotonic()
    predictions = detect(bundle, model, device=args.device, detector=detector)
    report.update(inference_seconds=time.monotonic() - started, detections=len(predictions),
                  scores=[prediction.detection_score for prediction in predictions],
                  observable_fraction=bundle.metadata.get("observable_fraction"))
    (bundle_path.parent / "predictions.json").write_text(json.dumps([p.model_dump(mode="json") for p in predictions], indent=2))
    publish_pilot_assets(bundle, predictions, bundle_path.parent)
    if args.train_boxes:
        report["training_step"] = detector.frozen_train_step(
            read_model_input(bundle, model), json.loads(Path(args.train_boxes).read_text())
        )
    report["peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if platform.system() == "Darwin" else 1024)
    (bundle_path.parent / "inference.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


def crop_s1(args):
    """Read four raw GRDH bands anonymously, retaining their GCP georeferencing.

    GCP thin-plate spline warp with cubic interpolation matches the pinned
    Sentinel-1 config. The second warp materializes the shared 10 m crop, also
    cubic as in the upstream Sentinel-1 layer. No calibration/RTC is substituted.
    """
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    from rasterio.vrt import WarpedVRT

    if not 32 <= args.size <= 4096:
        raise ValueError("crop size must be 32–4096 pixels")
    inventory = json.loads(Path(args.inventory).read_text())
    records = []
    for name in (args.scene, args.historical):
        matches = [p for p in inventory["products"] if p["Name"] == name.removesuffix(".SAFE") + ".SAFE"]
        if len(matches) != 1:
            raise ValueError("target and historical SAFE products must exist once in the pinned inventory")
        records.append(normalize_products(matches)[0])
    current, historical = records
    days = (current.captured_at - historical.captured_at).total_seconds() / 86400
    if current.orbit_direction != historical.orbit_direction or not 30 <= days <= 90:
        raise ValueError("historical context must have the same orbit direction, 30–90 days earlier")
    sources = {}
    for index, name in enumerate((args.scene, args.historical)):
        name = name.removesuffix(".SAFE")
        stamp = datetime.strptime(name.split("_")[4], "%Y%m%dT%H%M%S")
        for band in ("vv", "vh"):
            key = f"historical_{band}" if index else band
            url = f"https://sentinel-s1-l1c.s3.amazonaws.com/GRD/{stamp.year}/{stamp.month}/{stamp.day}/IW/DV/{name}/measurement/iw-{band}.tiff"
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=45) as response:
                sources[key] = RangeSource(url, int(response.headers["Content-Length"]),
                                           args.max_download_mb * 1024 * 1024, etag=response.headers["ETag"])
    directory = Path(f"data/pilot/{args.port}-s1-source")
    directory.mkdir(parents=True, exist_ok=True)
    utm = f"EPSG:{(32600 if args.lat >= 0 else 32700) + int((args.lon + 180) // 6) + 1}"
    x, y = Transformer.from_crs("EPSG:4326", utm, always_xy=True).transform(args.lon, args.lat)
    transform = from_origin(math.floor(x / 10) * 10 - args.size * 5,
                            math.ceil(y / 10) * 10 + args.size * 5, 10, 10)
    server = start_range_server(sources, args.max_download_mb * 1024 * 1024)
    started = time.monotonic()
    asset_paths, measurements = {}, []
    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_TIMEOUT=120):
            for band, source in sources.items():
                with rasterio.open(f"/vsicurl/http://127.0.0.1:{server.server_port}/{band}.tiff") as src:
                    gcps, gcp_crs = src.gcps
                    if src.crs is not None or not gcps or str(gcp_crs) != "EPSG:4326":
                        raise ValueError("expected raw GRDH georeferenced by WGS84 GCPs")
                    first = gcps[0]
                    native_utm = f"EPSG:{(32600 if first.y >= 0 else 32700) + int((first.x + 180) // 6) + 1}"
                    with WarpedVRT(src, crs=native_utm, resampling=Resampling.cubic,
                                   MAX_GCP_ORDER="-1") as intermediate:
                        with WarpedVRT(intermediate, crs=utm, transform=transform, width=args.size,
                                       height=args.size, resampling=Resampling.cubic) as vrt:
                            data = vrt.read(1)
                    path = directory / f"{band}.tif"
                    with rasterio.open(path, "w", driver="GTiff", count=1, dtype=str(data.dtype),
                                       width=args.size, height=args.size, crs=utm, transform=transform,
                                       compress="deflate", nodata=0) as dst:
                        dst.write(data, 1)
                asset_paths[band] = str(path)
                measurements.append({"band": band, "source_url": source.url, "etag": source.etag,
                                     "source_bytes": source.size, "downloaded_bytes": source.bytes,
                                     "range_requests": source.requests, "gcp_count": len(gcps),
                                     "native_utm": native_utm, "sha256": file_hash(path),
                                     "stored_bytes": path.stat().st_size})
                print(json.dumps(measurements[-1]), flush=True)
    finally:
        server.shutdown()
        server.server_close()
    metadata = {**current.metadata, "units": {band: "amplitude_dn" for band in S1_BANDS},
                "historical_scene_id": args.historical, "historical_captured_at": historical.captured_at.isoformat(),
                "historical_orbit_direction": historical.orbit_direction,
                "historical_relative_orbit": historical.relative_orbit,
                "relative_orbit": current.relative_orbit, "sources": measurements,
                "spatial_recipe": "GCP thin-plate spline to native UTM cubic; shared 10 m crop cubic; raw amplitude units"}
    scene = current.model_copy(update={"scene_id": args.scene, "source_assets": asset_paths, "metadata": metadata})
    (directory / "scene.json").write_text(scene.model_dump_json(indent=2))
    report = {"scene_id": args.scene, "historical_scene_id": args.historical,
              "downloaded_bytes": sum(s.bytes for s in sources.values()),
              "range_requests": sum(s.requests for s in sources.values()),
              "stored_bytes": sum(m["stored_bytes"] for m in measurements),
              "seconds": time.monotonic() - started, "api_processing_units": 0,
              "provider": "public unsigned AWS S3 byte ranges", "bands": measurements}
    (directory / "download.json").write_text(json.dumps(report, indent=2))
    bundle = prepare(scene, f"{args.port}_pilot", "ai2-s1-v1", output_dir=Path(f"data/pilot/{args.port}-s1-prepared"))
    print(json.dumps({"bundle": f"data/pilot/{args.port}-s1-prepared/bundle.json", "download": report,
                      "valid_fraction": bundle.metadata["valid_fraction"]}, indent=2))


def publish_pilot_assets(bundle, predictions, directory):
    """Write display-only preview and an explicit local Scene ready for ingestion."""
    import numpy as np
    from PIL import Image, ImageDraw

    data = read_model_input(bundle)
    if bundle.sensor == "sentinel-2":
        rgb = np.moveaxis(data[:3], 0, 2)
    else:
        rgb = np.repeat((data[0] / 4)[..., None], 3, axis=2)
    picture = Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8))
    draw = ImageDraw.Draw(picture)
    for prediction in predictions:
        draw.rectangle(prediction.metadata["pixel_box"], outline="yellow", width=2)
    picture.save(directory / "preview.png")
    if all(name in bundle.mask_paths for name in ("cloud", "shadow")):
        overlay = np.array(picture).astype(float)
        for name, color in (("cloud", [255, 80, 0]), ("shadow", [150, 0, 255])):
            with rasterio.open(bundle.mask_paths[name]) as src:
                mask = src.read(1) > 0
            overlay[mask] = overlay[mask] * 0.5 + np.array(color) * 0.5
        Image.fromarray(overlay.astype(np.uint8)).save(directory / "quality-preview.png")
    with rasterio.open(next(iter(bundle.band_paths.values()))) as src:
        left, bottom, right, top = rasterio.warp.transform_bounds(src.crs, "EPSG:4326", *src.bounds)
    bounds_wgs84 = [left, bottom, right, top]
    metadata = {**bundle.metadata, "units": bundle.units, "wgs84_bounds": bounds_wgs84,
                "footprint_role": "numeric_crop", "preview_role": "display_only_never_model_input"}
    bundle.metadata.update(metadata)
    (directory / "bundle.json").write_text(bundle.model_dump_json(indent=2) + "\n")
    source_assets = {key: str(value) for key, value in bundle.band_paths.items()}
    source_assets.update({f"{name}_mask": str(bundle.mask_paths[name])
                          for name in ("cloud", "shadow") if name in bundle.mask_paths})
    scene = Scene(
        scene_id=metadata["scene_id"], acquisition_key=metadata["acquisition_key"],
        sensor=bundle.sensor, product_level=bundle.product_level,
        captured_at=datetime.fromisoformat(metadata["captured_at"]),
        footprint={"type": "Polygon", "coordinates": [[[left, bottom], [right, bottom],
                    [right, top], [left, top], [left, bottom]]]},
        source_assets=source_assets, orbit_direction=metadata.get("orbit_direction"),
        provider="public_pilot_prepared", metadata=metadata,
    )
    (directory / "scene.json").write_text(scene.model_dump_json(indent=2) + "\n")


def quality_weights(args):
    revision = "c9a4fb88188709127aa25cfe51ae7fd41b0132f8"
    reports = []
    for name, digest in OMNICLOUDMASK_WEIGHTS.items():
        reports.append(download_asset(
            f"https://huggingface.co/NickWright/OmniCloudMask/resolve/{revision}/{name}",
            Path(args.output) / name, expected_sha256=digest, max_bytes=32 * 1024 * 1024,
        ))
    print(json.dumps(reports, indent=2))


def compare_infrastructure(args):
    """Measure upstream's distance deletion on proposals without claiming improvement."""
    import numpy as np
    from shapely.geometry import shape

    path = Path(args.infrastructure)
    expected_hash = "350d0e3c27613b4874c5c35cf683cd9a40cf09fd6a2bca871c6dadaa06ff7855"
    download_asset(
        "https://storage.googleapis.com/satlas-explorer-public/outputs/marine/latest.geojson?generation=1791818639390818",
        path, expected_sha256=expected_hash, max_bytes=8 * 1024 * 1024,
    )
    points = np.array([f["geometry"]["coordinates"] for f in json.loads(path.read_text())["features"]])
    lons, lats = np.radians(points[:, 0]), np.radians(points[:, 1])
    bundle_path = Path(args.bundle)
    bundle = RasterBundle.model_validate_json(bundle_path.read_text())
    predictions = json.loads((bundle_path.parent / "predictions.json").read_text())
    threshold = 50 if bundle.sensor == "sentinel-2" else 200
    distances = []
    for prediction in predictions:
        center = shape(prediction["geometry"]).centroid
        lon, lat = np.radians(center.x), np.radians(center.y)
        a = np.sin((lats - lat) / 2) ** 2 + np.cos(lat) * np.cos(lats) * np.sin((lons - lon) / 2) ** 2
        meters = float((6371000 * 2 * np.arcsin(np.sqrt(a))).min())
        distances.append({"prediction_id": prediction["prediction_id"],
                          "nearest_infrastructure_m": meters, "deleted_by_upstream_filter": meters < threshold})
    report = {"sensor": bundle.sensor, "proposals": len(predictions), "threshold_m": threshold,
              "deleted": sum(d["deleted_by_upstream_filter"] for d in distances),
              "infrastructure_sha256": expected_hash, "distances": distances,
              "interpretation": "Effect on proposals only; no claim about precision, recall or near-dock improvement."}
    (bundle_path.parent / "infrastructure-filter.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("weights")
    p.add_argument("--model", required=True)
    p.set_defaults(run=fetch_weights)
    p = commands.add_parser("quality-weights")
    p.add_argument("--output", default="data/models/omnicloudmask")
    p.set_defaults(run=quality_weights)
    p = commands.add_parser("compare-infrastructure")
    p.add_argument("--bundle", required=True)
    p.add_argument("--infrastructure", default="data/pilot/marine-infra-20261012.geojson")
    p.set_defaults(run=compare_infrastructure)
    p = commands.add_parser("public-sample")
    p.set_defaults(run=public_sample)
    p = commands.add_parser("public-sample-s1")
    p.set_defaults(run=public_sample_s1)
    p = commands.add_parser("crop")
    p.add_argument("--scene", required=True)
    p.add_argument("--port", choices=["prq", "stc", "pbr"], required=True)
    p.add_argument("--product-uuid")
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--size", type=int, default=512)
    p.add_argument("--max-download-mb", type=int, default=80)
    p.set_defaults(run=crop)
    p = commands.add_parser("crop-s1")
    p.add_argument("--scene", required=True)
    p.add_argument("--historical", required=True)
    p.add_argument("--inventory", default="reports/pilot/inventory.json")
    p.add_argument("--port", choices=["prq", "stc", "pbr"], required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--size", type=int, default=512)
    p.add_argument("--max-download-mb", type=int, default=192)
    p.set_defaults(run=crop_s1)
    p = commands.add_parser("infer")
    p.add_argument("--bundle", required=True)
    p.add_argument("--model", default="config/models/s2.yaml")
    p.add_argument("--device", choices=["cpu", "mps"], default="cpu")
    p.add_argument("--quality", action="store_true")
    p.add_argument("--quality-models", default="data/models/omnicloudmask")
    p.add_argument("--train-boxes")
    p.set_defaults(run=infer)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
