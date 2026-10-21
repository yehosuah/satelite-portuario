"""Immutable complete historical inference tiles, shared by acquisition.

Publication retains every 512-pixel tile from the frozen plan before disposable
work is removed. Dataset export copies the entire acquisition, including other
zones, because global center deduplication depends on those tiles. It never
substitutes core mosaics for missing halos or downloads replacement inputs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from contextlib import closing
from pathlib import Path

import rasterio
from rasterio import Affine

from puertogt.contracts import RasterBundle
from puertogt.jobs import ResourceLimit
from puertogt.preprocess import S1_BANDS, S2_BANDS
from puertogt.store import Store, confined_path, json_text, sha256_file

VERSION = "history-tiles-v1"
ROLE = "history_inference_manifest"


def _pass_id(key: str) -> str:
    fields = key.split(":")
    if len(fields) == 5 and fields[0].startswith("S2") and fields[1] == "MSIL1C":
        return ":".join([fields[0], fields[2], fields[3]])
    if len(fields) == 8 and fields[0].startswith("S1"):
        return ":".join([fields[0], fields[6], fields[7]])
    raise ValueError("Unsupported historical acquisition key for pass identity")


def _copy_verified(source: Path, target: Path, digest: str) -> None:
    """Create once; a concurrent/retried publication may only reuse equal bytes."""
    if target.exists():
        if target.is_symlink() or sha256_file(target) != digest:
            raise ValueError("Existing historical evidence differs from pinned bytes")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".part")
    try:
        with source.open("rb") as src, temporary.open("xb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        if sha256_file(temporary) != digest:
            raise ValueError("Historical evidence checksum changed during copy")
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.is_symlink() or sha256_file(target) != digest:
                raise ValueError("Concurrent historical evidence has different bytes") from None
    finally:
        temporary.unlink(missing_ok=True)


def _validate_grid(tile: dict, bundle: RasterBundle, paths: dict[str, Path]) -> None:
    if (tile.get("size"), tile.get("margin"), tile.get("pixel_size")) != (512, 32, 10):
        raise ValueError("Historical evidence requires complete 512/core448/margin32 tiles")
    west, south, east, north = tile["core_bounds"]
    expected = Affine(10, 0, west - 320, 0, -10, north + 320)
    if ((east - west, north - south) != (4480, 4480)
            or not expected.almost_equals(Affine(*tile["transform"]))
            or not expected.almost_equals(Affine(*bundle.transform))
            or bundle.crs != tile["crs"]):
        raise ValueError("Historical evidence has an inconsistent core/halo grid")
    bands = S2_BANDS if bundle.sensor == "sentinel-2" else S1_BANDS
    if set(bundle.band_paths) != set(bands) or "observable" not in bundle.mask_paths:
        raise ValueError("Historical evidence is missing required numeric bands or observable mask")
    for path in paths.values():
        with rasterio.open(path) as src:
            if (src.driver != "GTiff" or src.shape != (512, 512) or src.count != 1
                    or str(src.crs) != tile["crs"] or not expected.almost_equals(src.transform)):
                raise ValueError("Historical numeric evidence must retain its entire 512-pixel halo grid")


def _manifest_record(store: Store, key: str):
    with closing(store.connect()) as tx:
        rows = tx.execute("""SELECT * FROM assets WHERE
            json_extract(metadata_json,'$.role')=? AND
            json_extract(metadata_json,'$.acquisition_key')=?""", (ROLE, key)).fetchall()
    if len(rows) != 1:
        raise ValueError(f"Complete historical evidence missing or ambiguous for {key}")
    return dict(rows[0])


def _load_retained(store: Store, key: str) -> tuple[dict, dict]:
    record = _manifest_record(store, key)
    path = confined_path(store.data_dir, record["path"])
    if sha256_file(path) != record["sha256"]:
        raise ValueError("Historical inference manifest checksum mismatch")
    manifest = json.loads(path.read_text())
    entries = manifest.get("tiles", [])
    ids = [entry["tile"]["tile_id"] for entry in entries]
    descriptors = [entry["tile"] for entry in entries]
    if (manifest.get("version") != VERSION or manifest.get("acquisition_key") != key
            or manifest.get("pass_id") != _pass_id(key) or not ids
            or ids != manifest.get("expected_tile_ids") or len(ids) != len(set(ids))
            or hashlib.sha256(json_text(descriptors).encode()).hexdigest() != manifest.get("plan_tiles_sha256")):
        raise ValueError("Historical evidence does not contain the complete frozen plan tile set")
    with closing(store.connect()) as tx:
        for entry in entries:
            bundle = RasterBundle.model_validate(entry["bundle"])
            if bundle.metadata.get("acquisition_key") != key:
                raise ValueError("Historical tile acquisition mismatch")
            relative_paths = {str(path) for path in [*bundle.band_paths.values(), *bundle.mask_paths.values()]}
            if relative_paths != set(entry.get("file_sha256", {})):
                raise ValueError("Every complete historical band and mask requires a checksum")
            paths = {}
            for relative, digest in entry["file_sha256"].items():
                candidate = confined_path(store.data_dir, relative)
                registered = tx.execute("SELECT sha256 FROM assets WHERE path=?", (relative,)).fetchone()
                if not registered or registered[0] != digest or sha256_file(candidate) != digest:
                    raise ValueError("Historical tile checksum mismatch, including halo")
                paths[relative] = candidate
            _validate_grid(entry["tile"], bundle, paths)
    return manifest, record


def retain_history_evidence(store: Store, item: dict, tiles: list[tuple[dict, RasterBundle]], *,
                            sources: dict, provenance: dict | None = None,
                            reserve_bytes: int = 0, volume_limit_bytes: int | None = None) -> dict:
    """Persist one shared complete acquisition before its working cache is removed.

    Reconstructed tiles must supply explicit provenance; no default ever labels
    reconstructed halos as original. The frozen plan descriptors are retained
    independently of the reviewed zone subset.
    """
    key = item["scene"]["acquisition_key"]
    expected = item["tiles"]
    if not expected or [tile for tile, _ in tiles] != expected:
        raise ValueError("Complete frozen plan tiles are required before retention")
    with closing(store.connect()) as tx:
        existing = tx.execute("SELECT 1 FROM assets WHERE json_extract(metadata_json,'$.role')=? AND "
                              "json_extract(metadata_json,'$.acquisition_key')=?", (ROLE, key)).fetchone()
    if existing:
        manifest, record = _load_retained(store, key)
        if ([entry["tile"] for entry in manifest["tiles"]] != expected
                or manifest["sources_sha256"] != hashlib.sha256(json_text(sources).encode()).hexdigest()):
            raise ValueError("Retained acquisition differs from frozen plan or pinned sources")
        for (_, bundle), entry in zip(tiles, manifest["tiles"], strict=True):
            retained_bundle = RasterBundle.model_validate(entry["bundle"])
            for field in ("band_paths", "mask_paths"):
                supplied, retained_paths = getattr(bundle, field), getattr(retained_bundle, field)
                if set(supplied) != set(retained_paths):
                    raise ValueError("Historical retry uses different input bytes")
                for name, source in supplied.items():
                    source = confined_path(store.data_dir, Path(source).absolute().relative_to(store.data_dir))
                    if sha256_file(source) != entry["file_sha256"][str(retained_paths[name])]:
                        raise ValueError("Historical retry uses different input bytes")
        return {"manifest_asset_id": record["asset_id"], "manifest_sha256": record["sha256"],
                "acquisition_key": key, "pass_id": manifest["pass_id"]}
    identifier = hashlib.sha256(key.encode()).hexdigest()[:24]
    base = Path("assets/history") / identifier
    manifest = {"version": VERSION, "acquisition_key": key, "pass_id": _pass_id(key),
                "captured_at": item["scene"]["captured_at"],
                "context_acquisition_key": item.get("context", {}).get("acquisition_key"),
                "expected_tile_ids": [tile["tile_id"] for tile in expected],
                "plan_tiles_sha256": hashlib.sha256(json_text(expected).encode()).hexdigest(),
                "sources": sources, "sources_sha256": hashlib.sha256(json_text(sources).encode()).hexdigest(),
                "provenance": provenance or {"kind": "retained_original_prepared_bytes",
                                               "original_halo_hash_available": True}, "tiles": []}
    copies = []
    for tile, bundle in tiles:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", tile["tile_id"]):
            raise ValueError("Unsafe tile identity")
        if bundle.metadata.get("acquisition_key") != key:
            raise ValueError("Historical tile acquisition mismatch")
        file_paths = {**bundle.band_paths, **bundle.mask_paths}
        if len(file_paths) != len(bundle.band_paths) + len(bundle.mask_paths):
            raise ValueError("Band and mask names cannot collide")
        hashes = bundle.metadata.get("history_file_hashes", {})
        paths = {}
        for name, path in file_paths.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                raise ValueError("Unsafe historical raster name")
            source = confined_path(store.data_dir, Path(path).absolute().relative_to(store.data_dir))
            if sha256_file(source) != hashes.get(name):
                raise ValueError("Prepared tile differs from pinned materialization checksum")
            paths[name] = source
        _validate_grid(tile, bundle, paths)
        copied = bundle.model_dump(mode="json")
        file_hashes = {}
        for name, source in paths.items():
            relative = (base / tile["tile_id"] / (name + ".tif")).as_posix()
            copied["band_paths" if name in bundle.band_paths else "mask_paths"][name] = relative
            file_hashes[relative] = hashes[name]
            copies.append((source, relative, hashes[name], tile, name))
        manifest["tiles"].append({"tile": tile, "bundle": copied, "file_sha256": file_hashes})
    required = sum(source.stat().st_size for source, relative, *_ in copies
                   if not (store.data_dir / relative).exists())
    if shutil.disk_usage(store.data_dir).free < reserve_bytes + required:
        raise ResourceLimit("insufficient_disk_for_complete_history_evidence")
    if volume_limit_bytes is not None:
        size = sum(path.stat().st_size for path in store.data_dir.rglob("*")
                   if path.is_file() and not path.is_symlink())
        if size + required > volume_limit_bytes:
            raise ResourceLimit("history_evidence_volume_budget")
    for source, relative, digest, _, _ in copies:
        _copy_verified(source, confined_path(store.data_dir, relative, must_exist=False), digest)
    manifest_relative = (base / "inference.json").as_posix()
    manifest_path = confined_path(store.data_dir, manifest_relative, must_exist=False)
    encoded = (json_text(manifest) + "\n").encode()
    temporary = manifest_path.with_name("inference." + uuid.uuid4().hex + ".part")
    try:
        with temporary.open("xb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, manifest_path)
        except FileExistsError:
            if manifest_path.is_symlink() or manifest_path.read_bytes() != encoded:
                raise ValueError("Existing unregistered historical manifest differs") from None
    finally:
        temporary.unlink(missing_ok=True)
    with store.transaction() as tx:
        for _, relative, digest, tile, name in copies:
            row = tx.execute("SELECT sha256 FROM assets WHERE path=?", (relative,)).fetchone()
            if row and row[0] != digest:
                raise ValueError("Registered historical asset checksum differs")
            if not row:
                store.register_asset(relative, connection=tx, metadata={
                    "role": "history_tile", "acquisition_key": key, "tile_id": tile["tile_id"],
                    "name": name, "crs": tile["crs"], "mime_type": "image/tiff"})
        asset_id = store.register_asset(manifest_relative, connection=tx, metadata={
            "role": ROLE, "acquisition_key": key, "mime_type": "application/json"})
    return {"manifest_asset_id": asset_id, "manifest_sha256": sha256_file(manifest_path),
            "acquisition_key": key, "pass_id": manifest["pass_id"]}


def freeze_history_evidence(store: Store, destination: Path, samples: list[dict]) -> dict:
    """Copy exact complete tiles for every sample; fail closed if any is missing.

    Empty samples return an empty acquisitions list. Nonhistorical samples are
    rejected rather than omitted. Paths in the returned bundle and checksum map
    are relative to destination, suitable for an independent immutable dataset.
    """
    keys = sorted({sample["acquisition_key"] for sample in samples})
    verified = [_load_retained(store, key)[0] for key in keys]
    for sample in samples:
        acquisition = next(item for item in verified if item["acquisition_key"] == sample["acquisition_key"])
        if "zone_id" in sample and not any(sample["zone_id"] in entry["tile"]["zone_ids"]
                                           for entry in acquisition["tiles"]):
            raise ValueError("Sample zone is absent from the complete acquisition plan")
    result = {"version": VERSION, "acquisitions": []}
    if not verified:
        return result
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / "history").exists():
        raise FileExistsError("Frozen historical evidence cannot be replaced")
    stage = destination / (".history-" + uuid.uuid4().hex)
    stage.mkdir()
    try:
        for original in verified:
            acquisition = json.loads(json.dumps(original))
            for entry in acquisition["tiles"]:
                replacements = {}
                checksums = {}
                for relative, digest in entry["file_sha256"].items():
                    source = confined_path(store.data_dir, relative)
                    suffix = Path(relative).relative_to("assets/history")
                    target_relative = (Path("history") / suffix).as_posix()
                    _copy_verified(source, stage / suffix, digest)
                    replacements[relative] = target_relative
                    checksums[target_relative] = digest
                for field in ("band_paths", "mask_paths"):
                    entry["bundle"][field] = {name: replacements[path]
                                               for name, path in entry["bundle"][field].items()}
                entry["file_sha256"] = checksums
            result["acquisitions"].append(acquisition)
        os.rename(stage, destination / "history")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return result
