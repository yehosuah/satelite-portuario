#!/usr/bin/env python3
"""Record a complete public catalogue inventory; never download scene pixels."""

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shapely.geometry import mapping, shape

from puertogt.catalog import (
    CatalogClient,
    CatalogSearch,
    acquisition_key,
    load_ports,
    normalize_products,
)


def parse_date(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        if len(value) == 10:
            return parsed.replace(tzinfo=timezone.utc)
        raise ValueError("timestamps require an explicit timezone")
    return parsed.astimezone(timezone.utc)


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


def write_report(report, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(output)


def run(args):
    previous = json.loads(args.output.read_text()) if args.resume and args.output.exists() else None
    cutoff = (
        parse_date(args.end)
        if args.end
        else (parse_date(previous["cutoff_utc"]) if previous else datetime.now(timezone.utc))
    )
    start, context_start = parse_date(args.start), parse_date(args.context_start)
    if not context_start < start < cutoff:
        raise ValueError("expected context_start < start < end")
    ports = load_ports(args.ports)
    report = {
        "schema_version": 1,
        "status": "in_progress",
        "observed_at_utc": iso(datetime.now(timezone.utc)),
        "cutoff_utc": iso(cutoff),
        "public_history_period": {"start_inclusive": iso(start), "end_exclusive": iso(cutoff)},
        "auxiliary_radar_context_period": {
            "start_inclusive": iso(context_start),
            "end_exclusive": iso(start),
            "publishable": False,
            "reason": "preceding context for approximately 60-day radar reference",
        },
        "provider": "Copernicus Data Space Ecosystem public OData catalogue",
        "source_urls": [
            "https://documentation.dataspace.copernicus.eu/APIs/OData.html",
            "https://dataspace.copernicus.eu/data-collections/copernicus-sentinel-missions/sentinel-1",
        ],
        "ports_config": {
            "path": str(args.ports),
            "sha256": hashlib.sha256(args.ports.read_bytes()).hexdigest(),
            "version": next(iter(ports.values()))["geometry_version"],
            "geometry_status": "provisional",
        },
        "scope": {
            "sentinel-1": "IW GRDH 1SDV (VV/VH), SAFE and COG_SAFE",
            "sentinel-2": "MSI L1C; no cloud filter at discovery",
        },
        "identity_policy": {
            "sentinel-1": "platform/mode/GRDH/polarisation/start/end/absolute-orbit/datatake; excludes product checksum and COG suffix",
            "sentinel-2": "platform/level/sensing-time/relative-orbit/MGRS-tile; excludes processing baseline and generation-time",
            "representations": "retained with separate footprints, assets, publication dates; acquisition footprint is union",
        },
        "limitations": [
            "Catalogue availability is not pixel usability, local cloud coverage, or detected vessel count.",
            "Geometries are provisional analytic zones; no operational or navigation boundary validation.",
            "No scene pixels, model weights or paid services were downloaded by this inventory.",
            "Same acquisition may cover multiple ports; global acquisition IDs are not summed from per-port totals.",
            "Counts are complete relative to this provider/filter/publication cutoff, not a guarantee of future archive stability.",
        ],
        "queries": [],
        "products": [],
        "acquisitions": [],
        "samples": [],
        "errors": [],
    }
    if previous:
        if (
            previous["ports_config"]["sha256"] != report["ports_config"]["sha256"]
            or previous["public_history_period"] != report["public_history_period"]
            or previous["auxiliary_radar_context_period"]
            != report["auxiliary_radar_context_period"]
        ):
            raise ValueError("resume requires identical geometry and periods")
        report["queries"] = previous["queries"]
        report["products"] = previous["products"]
        report["previous_attempt_errors"] = previous.get(
            "previous_attempt_errors", []
        ) + previous.get("errors", [])
    product_index = {product["Id"]: product for product in report["products"]}
    membership = defaultdict(set)
    for query in report["queries"]:
        for key in query["acquisition_keys"]:
            membership[key].add(query["port_id"])
    tasks = [
        (pid, sensor, start, cutoff, "public_history")
        for pid in ports
        for sensor in ("sentinel-1", "sentinel-2")
    ]
    tasks += [(pid, "sentinel-1", context_start, start, "auxiliary_context") for pid in ports]
    expected_query_count = len(tasks)
    completed = {
        (query["port_id"], query["sensor"], query["purpose"]) for query in report["queries"]
    }
    tasks = [task for task in tasks if (task[0], task[1], task[4]) not in completed]

    def search(task):
        pid, sensor, begin, end, purpose = task
        # Optical archive queries over long spans can time out at the provider.
        # Bounded calendar partitions retain the same half-open interval exactly.
        partitions = []
        current = begin
        while current < end:
            if sensor == "sentinel-2":
                next_month = (
                    current.replace(year=current.year + 1, month=1, day=1)
                    if current.month == 12
                    else current.replace(month=current.month + 1, day=1)
                )
                until = min(next_month, end)
            else:
                until = end
            partitions.append((current, until))
            current = until
        products, pages, count = [], [], 0
        client = CatalogClient(page_size=args.page_size)
        for part_begin, part_end in partitions:
            part = client.search(
                sensor,
                mapping(shape(ports[pid]["geometry"]).envelope),
                part_begin,
                part_end,
                as_of=cutoff,
            )
            products.extend(part.products)
            pages.extend(part.pages)
            count += part.reported_count if part.reported_count is not None else len(part.products)
        result = CatalogSearch(products, pages, count)
        return task, result

    write_report(report, args.output)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(search, task): task for task in tasks}
        for future in as_completed(futures):
            task = futures[future]
            pid, sensor, begin, end, purpose = task
            try:
                _, result = future.result()
                matching_products = [
                    p
                    for p in result.products
                    if shape(p["GeoFootprint"]).intersects(shape(ports[pid]["geometry"]))
                ]
                scenes = normalize_products(matching_products, port_ids=[pid], discovered_at=cutoff)
                for product in result.products:
                    product_index[product["Id"]] = {
                        key: product[key]
                        for key in (
                            "Id",
                            "Name",
                            "ContentDate",
                            "PublicationDate",
                            "GeoFootprint",
                            "S3Path",
                            "Online",
                            "ContentLength",
                            "Attributes",
                        )
                        if key in product
                    }
                for product in matching_products:
                    membership[acquisition_key(product["Name"])].add(pid)
                query = {
                    "port_id": pid,
                    "sensor": sensor,
                    "purpose": purpose,
                    "period": {"start_inclusive": iso(begin), "end_exclusive": iso(end)},
                    "geometry": ports[pid]["geometry"],
                    "complete": result.complete,
                    "remote_search_geometry": mapping(shape(ports[pid]["geometry"]).envelope),
                    "geometry_filter": "remote envelope followed by exact local zone intersection",
                    "reported_product_count": result.reported_count,
                    "retrieved_product_count": len(result.products),
                    "products_intersecting_zones": len(matching_products),
                    "unique_acquisition_count": len(scenes),
                    "page_count": len(result.pages),
                    "pages": result.pages,
                    "product_ids": sorted(p["Id"] for p in result.products),
                    "acquisition_keys": [s.acquisition_key for s in scenes],
                    "monthly_acquisition_counts": dict(
                        sorted(Counter(s.captured_at.strftime("%Y-%m") for s in scenes).items())
                    ),
                    "first_capture_utc": iso(scenes[0].captured_at) if scenes else None,
                    "last_capture_utc": iso(scenes[-1].captured_at) if scenes else None,
                }
                report["queries"].append(query)
                print(
                    f"{pid} {sensor} {purpose}: {len(result.products)} products / {len(scenes)} acquisitions / {len(result.pages)} pages",
                    flush=True,
                )
            except Exception as exc:
                report["errors"].append(
                    {
                        "port_id": pid,
                        "sensor": sensor,
                        "purpose": purpose,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                print(
                    f"ERROR {pid} {sensor} {purpose}: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
            report["products"] = sorted(product_index.values(), key=lambda product: product["Id"])
            write_report(report, args.output)
    scenes = normalize_products(
        [p for p in product_index.values() if acquisition_key(p["Name"]) in membership],
        discovered_at=cutoff,
    )
    for scene in scenes:
        scene.port_ids = sorted(membership[scene.acquisition_key])
        scene.metadata["purpose"] = (
            "public_history" if scene.captured_at >= start else "auxiliary_context"
        )
    report["acquisitions"] = [scene.model_dump(mode="json") for scene in scenes]
    for pid, port in ports.items():
        for sensor in ("sentinel-1", "sentinel-2"):
            eligible = [
                s
                for s in scenes
                if pid in s.port_ids and s.sensor == sensor and s.captured_at >= start
            ]
            for zone in port["zones"]:
                zone_geometry = shape(zone["geometry"])
                candidates = [s for s in eligible if shape(s.footprint).covers(zone_geometry)]
                if sensor == "sentinel-2":

                    def rank(scene):
                        covers = [
                            r
                            for r in scene.metadata["representations"]
                            if shape(r["footprint"]).covers(zone_geometry)
                        ]
                        return (
                            min(
                                (r["attributes"].get("cloudCover", 100) for r in covers),
                                default=100,
                            ),
                            -scene.captured_at.timestamp(),
                        )

                    candidates.sort(key=rank)
                else:
                    candidates.sort(key=lambda s: s.captured_at, reverse=True)
                selected = candidates[0] if candidates else None
                sample = {
                    "port_id": pid,
                    "zone_id": zone["properties"]["zone_id"],
                    "sensor": sensor,
                    "scene_id": selected.scene_id if selected else None,
                    "acquisition_key": selected.acquisition_key if selected else None,
                    "captured_at": iso(selected.captured_at) if selected else None,
                    "status": "catalogue_candidate_requires_pixel_review"
                    if selected
                    else "no_full_footprint_candidate",
                    "selection": "lowest product cloud cover among full-zone footprints; not local visibility"
                    if sensor == "sentinel-2"
                    else "latest full-zone footprint",
                }
                if selected and sensor == "sentinel-1":
                    context = [
                        s
                        for s in scenes
                        if pid in s.port_ids
                        and s.sensor == sensor
                        and s.orbit_direction == selected.orbit_direction
                        and timedelta(days=45)
                        <= selected.captured_at - s.captured_at
                        <= timedelta(days=90)
                        and shape(s.footprint).covers(zone_geometry)
                    ]
                    context.sort(
                        key=lambda s: abs(
                            (selected.captured_at - s.captured_at).total_seconds() - 60 * 86400
                        )
                    )
                    sample["context_scene_id"] = context[0].scene_id if context else None
                    sample["context_status"] = (
                        "candidate_orbit_direction_compatible_requires_recipe_check"
                        if context
                        else "missing"
                    )
                report["samples"].append(sample)
    report["queries"].sort(key=lambda q: (q["purpose"], q["port_id"], q["sensor"]))
    report["global_counts"] = {
        "unique_catalogue_products": len(product_index),
        "unique_acquisitions": len(scenes),
        "public_history_acquisitions": sum(s.captured_at >= start for s in scenes),
        "auxiliary_context_acquisitions": sum(s.captured_at < start for s in scenes),
        "catalogue_pages": sum(q["page_count"] for q in report["queries"]),
        "completed_queries": len(report["queries"]),
        "expected_queries": expected_query_count,
        "processed_scenes": 0,
        "usability_assessed": 0,
    }
    report["finished_at_utc"] = iso(datetime.now(timezone.utc))
    report["status"] = "complete" if not report["errors"] else "incomplete"
    write_report(report, args.output)
    return 0 if report["status"] == "complete" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", help="exclusive UTC capture/publication cutoff; defaults to now")
    parser.add_argument("--context-start", default="2024-10-01")
    parser.add_argument("--ports", type=Path, default=Path("config/ports.geojson"))
    parser.add_argument("--output", type=Path, default=Path("reports/pilot/inventory.json"))
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="reuse completed searches with identical periods and geometry",
    )
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        parser.error("workers must be 1..4 to bound provider load")
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
