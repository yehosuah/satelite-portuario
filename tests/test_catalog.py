from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from shapely.geometry import box, mapping, shape

from puertogt.catalog import (
    CATALOGUE_URL,
    CatalogClient,
    acquisition_key,
    attribute_point,
    load_ports,
    normalize_products,
)

SAFE = "S1D_IW_GRDH_1SDV_20260901T001341_20260901T001406_004377_008164_850A.SAFE"
COG = "S1D_IW_GRDH_1SDV_20260901T001341_20260901T001406_004377_008164_DFE0_COG.SAFE"
START = datetime(2025, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 1, 1, tzinfo=timezone.utc)


def product(name=SAFE, product_id="safe", footprint=None):
    return {
        "Id": product_id,
        "Name": name,
        "ContentDate": {
            "Start": "2026-09-01T00:13:41.538713Z",
            "End": "2026-09-01T00:14:06.536817Z",
        },
        "PublicationDate": "2026-09-01T04:43:20.022555Z",
        "GeoFootprint": footprint or dict(mapping(box(-91, 13, -90, 14))),
        "S3Path": "/eodata/test/" + name,
        "Online": True,
        "Attributes": [
            {"Name": "relativeOrbitNumber", "Value": 136},
            {"Name": "orbitDirection", "Value": "ASCENDING"},
        ],
    }


def test_safe_and_cog_are_one_acquisition_but_a_later_capture_is_another():
    names = [
        "S1D_IW_GRDH_1SDV_20260901T001341_20260901T001406_004377_008164_850A.SAFE",
        "S1D_IW_GRDH_1SDV_20260901T001341_20260901T001406_004377_008164_DFE0_COG.SAFE",
        "S1D_IW_GRDH_1SDV_20260913T001342_20260913T001407_004552_008588_A001.SAFE",
    ]
    assert len({acquisition_key(name) for name in names}) == 2


def test_normalization_retains_both_delivery_assets_and_stable_scene_id():
    safe, cog = product(), product(COG, "cog")
    scenes = normalize_products([safe, cog, deepcopy(safe)], port_ids=["prq"])
    assert len(scenes) == 1
    scene = scenes[0]
    assert scene.scene_id == normalize_products([cog])[0].scene_id
    assert scene.metadata["product_count"] == 2
    assert set(scene.source_assets) == {"safe:product", "safe:s3", "cog:product", "cog:s3"}
    assert scene.port_ids == ["prq"]
    assert scene.relative_orbit == 136
    assert scene.captured_at == datetime(2026, 9, 1, 0, 13, 41, 538713, tzinfo=timezone.utc)


def test_s2_reprocessed_tile_unites_footprints_but_keeps_other_passes_separate():
    first = "S2B_MSIL1C_20260910T161859_N0512_R040_T15PYR_20260910T212543.SAFE"
    partial = "S2B_MSIL1C_20260910T161859_N0513_R040_T15PYR_20260910T195745.SAFE"
    later = "S2B_MSIL1C_20260920T161859_N0513_R040_T15PYR_20260920T212543.SAFE"
    assert acquisition_key(first) == acquisition_key(partial)
    assert acquisition_key(first) != acquisition_key(later)
    # The independently known union of adjacent unit squares has area 2.
    scenes = normalize_products(
        [
            product(first, "tile-a", mapping(box(-91, 13, -90, 14))),
            product(partial, "tile-b", mapping(box(-90, 13, -89, 14))),
        ]
    )
    assert len(scenes) == 1
    assert shape(scenes[0].footprint).area == 2
    assert scenes[0].product_level == "L1C"
    assert len(scenes[0].metadata["representations"]) == 2


def test_platform_orbit_and_slice_end_are_not_discarded_from_identity():
    keys = {
        acquisition_key(name)
        for name in [
            SAFE,
            SAFE.replace("S1D_", "S1A_"),
            SAFE.replace("004377", "004378"),
            SAFE.replace("001406", "001407"),
        ]
    }
    assert len(keys) == 4


@pytest.mark.parametrize(
    "name",
    [
        "invalid",
        SAFE.replace("1SDV", "1SDH"),
        "S2B_MSIL2A_20260910T161859_N0512_R040_T15PYR_20260910T212543.SAFE",
    ],
)
def test_incompatible_product_names_are_rejected(name):
    with pytest.raises(ValueError, match="unsupported Sentinel"):
        acquisition_key(name)


def test_search_follows_next_link_and_preserves_page_evidence():
    urls = []
    second = CATALOGUE_URL + "?$skip=1"

    def fetch(url):
        urls.append(url)
        return (
            {"value": [product()], "@odata.count": 2, "@odata.nextLink": second}
            if len(urls) == 1
            else {"value": [product(COG, "cog")], "@odata.count": 2}
        )

    result = CatalogClient(fetch_json=fetch, page_size=1).search(
        "sentinel-1",
        mapping(box(-91, 13, -90, 14)),
        START,
        END,
        as_of=END,
    )
    assert [p["Id"] for p in result.products] == ["safe", "cog"]
    assert result.complete
    assert len(result.pages) == 2
    assert urls[1] == second
    assert result.pages[0]["next_link"] == second
    query = parse_qs(urlparse(urls[0]).query)
    assert "ContentDate/Start ge 2025-01-01T00:00:00Z" in query["$filter"][0]
    assert "ContentDate/Start lt 2026-01-01T00:00:00Z" in query["$filter"][0]
    assert "PublicationDate le 2026-01-01T00:00:00Z" in query["$filter"][0]
    assert query["$expand"] == ["Attributes"]


def test_partial_catalogue_is_an_error_not_silent_coverage():
    def fetch(url):
        return {"value": [product()], "@odata.count": 2}

    with pytest.raises(RuntimeError, match="count mismatch"):
        CatalogClient(fetch_json=fetch).search(
            "sentinel-1", mapping(box(-91, 13, -90, 14)), START, END
        )


def test_repeated_pagination_is_an_error():
    second = CATALOGUE_URL + "?$skip=1"

    def fetch(url):
        return {"value": [product()], "@odata.count": 2, "@odata.nextLink": second}

    with pytest.raises(RuntimeError, match="repeated a pagination URL"):
        CatalogClient(fetch_json=fetch).search(
            "sentinel-1", mapping(box(-91, 13, -90, 14)), START, END
        )


def test_naive_time_cannot_shift_the_inventory_cutoff():
    with pytest.raises(ValueError, match="timezone"):
        CatalogClient().search(
            "sentinel-1", mapping(box(-91, 13, -90, 14)), datetime(2025, 1, 1), END
        )


def test_port_zones_are_provisional_and_do_not_double_attribute_atlantic_water():
    ports = load_ports()
    assert set(ports) == {"prq", "stc", "pbr"}
    assert shape(ports["stc"]["geometry"]).intersection(shape(ports["pbr"]["geometry"])).area == 0
    assert attribute_point(-88.618, 15.695, ports=ports) == "stc"
    assert attribute_point(-88.608, 15.7348, ports=ports) == "pbr"
    assert attribute_point(-88.70, 15.80, ports=ports) is None
    for port in ports.values():
        assert {z["properties"]["zone_type"] for z in port["zones"]} == {
            "muelles",
            "fondeo",
            "canal",
            "patios",
        }
        assert all(z["properties"]["geometry_status"] == "provisional" for z in port["zones"])


def test_shared_boundary_is_assigned_exactly_once():
    ports = {
        "stc": {"geometry": mapping(box(0, 0, 1, 1))},
        "pbr": {"geometry": mapping(box(1, 0, 2, 1))},
    }
    assert attribute_point(1, 0.5, ports=ports) == "pbr"
