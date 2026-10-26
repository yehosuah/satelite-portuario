"""Measure actual overpasses and known observable coverage without inventing revisits.

Product representations, optical granules and radar slices are not independent
passes. Coverage is measured per configured zone; it is not vessel accuracy.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform, unary_union

from puertogt.contracts import as_utc, utcnow
from puertogt.store import Store

_SENSORS = ("sentinel-1", "sentinel-2")
_PROCESSED = {"usable", "partial", "unusable"}


def _date(value: datetime | str) -> datetime:
    return as_utc(
        datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    )


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def pass_key(acquisition_key: str, sensor: str) -> str:
    """Group S2 tile deliveries and S1 along-track slices, retaining satellite identity."""
    parts = acquisition_key.split(":")
    if sensor == "sentinel-2" and len(parts) == 5 and parts[0].startswith("S2"):
        return ":".join(parts[:4])
    if sensor == "sentinel-1" and len(parts) == 8 and parts[0].startswith("S1"):
        return ":".join((parts[0], "absolute-orbit", parts[6]))
    # Imported/non-Sentinel keys remain distinct; never guess a matching pass.
    return acquisition_key


def _intervals(times: list[datetime]) -> dict:
    times = sorted(set(times))
    gaps = sorted((b - a).total_seconds() / 86400 for a, b in zip(times, times[1:]))
    return {
        "count": len(gaps),
        "median": round(median(gaps), 6) if gaps else None,
        "p90": round(gaps[max(0, math.ceil(len(gaps) * 0.9) - 1)], 6) if gaps else None,
        "max": round(max(gaps), 6) if gaps else None,
    }


def _days(times: list[datetime]) -> list[datetime]:
    return [
        datetime.combine(day, datetime.min.time(), timezone.utc)
        for day in sorted({value.date() for value in times})
    ]


def _boundaries(times: list[datetime], start: datetime, end: datetime) -> dict:
    return {
        "leading": round((min(times) - start).total_seconds() / 86400, 6) if times else None,
        "trailing": round((end - max(times)).total_seconds() / 86400, 6) if times else None,
    }


def _summarize(
    events: list[dict], zones: list[str], start: datetime, end: datetime, threshold: float
) -> dict:
    captures = [event["time"] for event in events]
    useful = [
        event["time"]
        for event in events
        if all(event["fractions"].get(zone, 0.0) >= threshold for zone in zones)
    ]
    conservative = [
        event
        for event in events
        if all(event["conservative_fractions"].get(zone, 0.0) >= threshold for zone in zones)
    ]
    acquisitions = {a["acquisition_key"]: a for event in events for a in event["acquisitions"]}
    return {
        "catalogued_acquisitions": len(acquisitions),
        "catalogued_passes": len(events),
        "processed_passes": sum(bool(event["processed"].intersection(zones)) for event in events),
        "sufficiently_observed_passes": len(useful),
        "conservative_sufficiently_observed_passes": len(conservative),
        "catalogued_capture_days": len(_days(captures)),
        "sufficiently_observed_days": len(_days(useful)),
        "catalogue_intervals_days": _intervals(captures),
        "sufficiently_observed_intervals_days": _intervals(useful),
        "sufficiently_observed_day_intervals_days": _intervals(_days(useful)),
        "sufficiently_observed_boundary_gaps_days": _boundaries(useful, start, end),
        "catalogue_boundary_gaps_days": _boundaries(captures, start, end),
        "window_days_without_sufficient_observation": round(
            (end - start).total_seconds() / 86400, 6
        )
        if not useful
        else None,
        "first_capture": _iso(min(captures)) if captures else None,
        "last_capture": _iso(max(captures)) if captures else None,
        "platforms": sorted({a["acquisition_key"].split(":")[0] for a in acquisitions.values()}),
        "relative_orbits": sorted(
            {a["relative_orbit"] for a in acquisitions.values() if a["relative_orbit"] is not None},
            key=str,
        ),
        "orbit_directions": sorted(
            {a["orbit_direction"] for a in acquisitions.values() if a["orbit_direction"]}
        ),
        "recipe_versions": sorted({recipe for event in events for recipe in event["recipes"]}),
    }


def pass_coverage_report(
    store: Store,
    ports_path: Path,
    *,
    port_id: str | None = None,
    sensor: str | None = None,
    start: datetime | str | None = None,
    end: datetime | str | None = None,
    recipe_version: str | None = None,
    min_observable_fraction: float = 0.9,
    mask_union_cache: Path | None = None,
) -> dict:
    """Return a read-only snapshot of pass cadence and conservative observed coverage.

    A sufficient port pass requires *every* configured zone to meet the threshold
    in that same pass. Multiple observations of a zone use their maximum fraction,
    a conservative lower bound on the union, unless a matching byte-verified mask
    union cache exists; fractions are never added. Missing
    observations contribute zero *known coverage*, never zero vessels.
    """
    if sensor is not None and sensor not in _SENSORS:
        raise ValueError("unsupported sensor")
    if not math.isfinite(min_observable_fraction) or not 0 < min_observable_fraction <= 1:
        raise ValueError("min_observable_fraction must be in (0, 1]")
    generated_at = utcnow()
    start = _date(start) if start is not None else datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = _date(end) if end is not None else generated_at
    if start >= end:
        raise ValueError("start must precede end")
    ports_bytes = Path(ports_path).read_bytes()
    features = json.loads(ports_bytes)["features"]
    available_ports = {f["properties"]["port_id"] for f in features}
    if port_id is not None and port_id not in available_ports:
        raise ValueError("unknown port")
    ports = defaultdict(list)
    for feature in features:
        if port_id is None or feature["properties"]["port_id"] == port_id:
            ports[feature["properties"]["port_id"]].append(feature)

    with closing(store.connect()) as tx:
        tx.execute("BEGIN")  # Both tables belong to one SQLite read snapshot.
        acquisition_rows = tx.execute(
            "SELECT acquisition_key,sensor,captured_at,footprint_json,metadata_json,data_json "
            "FROM acquisitions ORDER BY captured_at,acquisition_key"
        ).fetchall()
        observation_rows = tx.execute(
            "SELECT observation_id,acquisition_key,zone_id,recipe_version,status,review_status,"
            "observable_fraction FROM observations ORDER BY observation_id"
        ).fetchall()
        asset_rows = (
            tx.execute(
                "SELECT observation_id,asset_id,path,sha256,size_bytes FROM assets "
                "WHERE path LIKE '%/observable.tif' ORDER BY observation_id,asset_id"
            ).fetchall()
            if mask_union_cache is not None
            else []
        )
        tx.rollback()

    acquisitions = []
    for row in acquisition_rows:
        captured_at = _date(row["captured_at"])
        metadata = json.loads(row["metadata_json"])
        if (
            not start <= captured_at < end
            or sensor
            and row["sensor"] != sensor
            or metadata.get("context_only")
            or metadata.get("purpose") == "auxiliary_context"
        ):
            continue
        data = json.loads(row["data_json"])
        acquisitions.append(
            {
                "acquisition_key": row["acquisition_key"],
                "sensor": row["sensor"],
                "captured_at": _iso(captured_at),
                "footprint": json.loads(row["footprint_json"]),
                "relative_orbit": data.get("relative_orbit"),
                "orbit_direction": data.get("orbit_direction"),
            }
        )
    selected_keys = {a["acquisition_key"] for a in acquisitions}
    observations = [
        dict(row)
        for row in observation_rows
        if row["acquisition_key"] in selected_keys
        and row["review_status"] != "rejected"
        and (recipe_version is None or row["recipe_version"] == recipe_version)
    ]
    indexed = defaultdict(list)
    for row in observations:
        indexed[(row["acquisition_key"], row["zone_id"])].append(row)
    grouped = defaultdict(list)
    for acquisition in acquisitions:
        grouped[
            (acquisition["sensor"], pass_key(acquisition["acquisition_key"], acquisition["sensor"]))
        ].append(acquisition)

    cache_status, cache_digest, cache_entries = "disabled", None, {}
    if mask_union_cache is not None:
        from puertogt.pass_masks import source_record, source_records_key

        try:
            cache_bytes = Path(mask_union_cache).read_bytes()
            cache = json.loads(cache_bytes)
            cache_digest = hashlib.sha256(cache_bytes).hexdigest()
            if cache.get("schema") != "pass-mask-unions-v1":
                cache_status = "schema_mismatch"
            elif cache.get("ports_sha256") != hashlib.sha256(ports_bytes).hexdigest():
                cache_status = "geometry_changed"
            else:
                cache_status = "loaded"
                assets_by_observation = defaultdict(list)
                for row in asset_rows:
                    assets_by_observation[row["observation_id"]].append(source_record(row))
                current_records = defaultdict(list)
                sensor_by_acquisition = {a["acquisition_key"]: a["sensor"] for a in acquisitions}
                for row in observations:
                    if row["status"] not in {"usable", "partial"}:
                        continue
                    row_sensor = sensor_by_acquisition[row["acquisition_key"]]
                    key = (
                        row_sensor,
                        pass_key(row["acquisition_key"], row_sensor),
                        row["zone_id"],
                        row["recipe_version"],
                    )
                    current_records[key].extend(assets_by_observation[row["observation_id"]])
                for entry in cache.get("entries", []):
                    key = (
                        entry["sensor"],
                        entry["pass_key"],
                        entry["zone_id"],
                        entry["recipe_version"],
                    )
                    fraction = entry["observable_fraction"]
                    if (
                        isinstance(fraction, (float, int))
                        and math.isfinite(fraction)
                        and 0 <= fraction <= 1
                        and len(current_records.get(key, [])) >= 2
                        and source_records_key(current_records[key])
                        == entry["source_records_sha256"]
                    ):
                        cache_entries[key] = fraction
        except FileNotFoundError:
            cache_status = "missing"
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            cache_status, cache_entries = "unreadable", {}

    items, zone_items = [], []
    applied_entries = set()
    sensors = [sensor] if sensor else [*_SENSORS, "combined"]
    for port, port_features in sorted(ports.items()):
        zone_geometries = {f["properties"]["zone_id"]: shape(f["geometry"]) for f in port_features}
        geometry = unary_union(list(zone_geometries.values()))
        if geometry.is_empty or not geometry.is_valid:
            raise ValueError(f"invalid geometry for {port}")
        longitude, latitude = geometry.centroid.x, geometry.centroid.y
        utm = (32600 if latitude >= 0 else 32700) + int((longitude + 180) // 6) + 1
        project = Transformer.from_crs("EPSG:4326", f"EPSG:{utm}", always_xy=True).transform
        area = transform(project, geometry).area
        events = []
        for (event_sensor, key), group in grouped.items():
            matches = [a for a in group if shape(a["footprint"]).intersects(geometry)]
            if not matches:
                continue
            footprint = unary_union([shape(a["footprint"]) for a in matches])
            event = {
                "key": key,
                "sensor": event_sensor,
                "time": min(_date(a["captured_at"]) for a in matches),
                "acquisitions": matches,
                "fractions": {},
                "processed": set(),
                "recipes": set(),
                "zones_intersected": set(),
                "conservative_fractions": {},
                "catalogue_fraction": min(
                    1.0, transform(project, footprint.intersection(geometry)).area / area
                ),
            }
            for zone, zone_geometry in zone_geometries.items():
                if footprint.intersects(zone_geometry):
                    event["zones_intersected"].add(zone)
                for acquisition in matches:
                    for observation in indexed.get((acquisition["acquisition_key"], zone), []):
                        event["recipes"].add(observation["recipe_version"])
                        if observation["status"] in _PROCESSED:
                            event["processed"].add(zone)
                        fraction = observation["observable_fraction"]
                        if observation["status"] in {"usable", "partial"} and fraction is not None:
                            event["fractions"][zone] = max(
                                event["fractions"].get(zone, 0), fraction
                            )
                event["conservative_fractions"][zone] = event["fractions"].get(zone, 0.0)
                for recipe in event["recipes"]:
                    cache_key = (event_sensor, key, zone, recipe)
                    if cache_key in cache_entries:
                        event["fractions"][zone] = max(
                            event["fractions"].get(zone, 0), cache_entries[cache_key]
                        )
                        applied_entries.add(cache_key)
            events.append(event)
        for event_sensor in sensors:
            selected = [
                e for e in events if event_sensor == "combined" or e["sensor"] == event_sensor
            ]
            item = {
                "port_id": port,
                "port_name": port_features[0]["properties"].get("port_name", port),
                "sensor": event_sensor,
                "configured_zones": sorted(zone_geometries),
                **_summarize(selected, list(zone_geometries), start, end, min_observable_fraction),
            }
            item["mean_catalogued_area_fraction"] = (
                round(mean(e["catalogue_fraction"] for e in selected), 6) if selected else None
            )
            item["sufficient_footprint_passes"] = sum(
                e["catalogue_fraction"] >= min_observable_fraction for e in selected
            )
            items.append(item)
            for zone in zone_geometries:
                zone_events = [
                    e
                    | {
                        "acquisitions": [
                            a
                            for a in e["acquisitions"]
                            if shape(a["footprint"]).intersects(zone_geometries[zone])
                        ]
                    }
                    for e in selected
                    if zone in e["zones_intersected"]
                ]
                zone_item = {
                    "port_id": port,
                    "zone_id": zone,
                    "sensor": event_sensor,
                    **_summarize(zone_events, [zone], start, end, min_observable_fraction),
                }
                zone_item["mean_known_observable_fraction"] = (
                    round(mean(e["fractions"].get(zone, 0.0) for e in zone_events), 6)
                    if zone_events
                    else None
                )
                zone_items.append(zone_item)

    digest = hashlib.sha256(
        json.dumps(
            {"acquisitions": acquisitions, "observations": observations},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "generated_at": _iso(generated_at),
        "period": {"start": _iso(start), "end": _iso(end), "end_exclusive": True},
        "filters": {
            "port_id": port_id,
            "sensor": sensor,
            "recipe_version": recipe_version,
            "min_observable_fraction": min_observable_fraction,
        },
        "source": {
            "snapshot_sha256": digest,
            "ports_sha256": hashlib.sha256(ports_bytes).hexdigest(),
            "acquisition_rows": len(acquisitions),
            "observation_rows": len(observations),
            "mask_union_cache_status": cache_status,
            "mask_union_cache_sha256": cache_digest,
            "mask_union_entries_applied": len(applied_entries),
        },
        "items": items,
        "zones": zone_items,
        "notes": [
            "A sufficient port pass requires every configured zone to meet the observable-fraction threshold.",
            "S2 granules share a satellite/sensing-time/relative-orbit pass; S1 slices share a satellite/absolute-orbit pass.",
            "The maximum observed fraction per zone is a conservative union lower bound; fractions and vessel counts are never added across passes.",
            "A matching mask-union cache can recover complementary pixels within the same pass and recipe; the conservative count remains available for comparison.",
            "Unknown or unprocessed areas contribute zero known coverage, not zero vessels. Context-only acquisitions are excluded.",
            "Combined cadence pools capture opportunities from both sensors; it does not fuse measurements or validate detector accuracy.",
            "Interval statistics exclude window boundaries; leading/trailing gaps are reported separately. Day intervals use distinct UTC dates.",
        ],
    }
