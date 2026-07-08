from __future__ import annotations

import json
import math
from collections import defaultdict
from contextlib import ExitStack
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import planetary_computer
import rasterio
from rasterio.merge import merge
from pyproj import Geod, Transformer
from pystac_client import Client
from rasterio.features import rasterize
from shapely import wkt
from shapely.geometry import mapping
from shapely.ops import transform as shapely_transform


FIDE_PARQUET_URL = (
    "https://data.source.coop/pacificspatial/field-polygon-jp/parquet/"
    "jp_field_polygons_2024.parquet"
)
PLANETARY_COMPUTER_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
REGION_ID = "shibata_city_152064"
REGION_LABEL = "Shibata City"
LOCAL_GOVERNMENT_CD = "152064"
DATE_RANGE = "2024-06-01/2024-10-15"
OUTPUT_DIR = Path("outputs") / REGION_ID


def geodesic_area_m2(geom) -> float:
    geod = Geod(ellps="GRS80")
    area, _ = geod.geometry_area_perimeter(geom)
    return abs(area)


def geometry_bbox_from_wkts(wkts: pd.Series) -> list[float]:
    minx = float("inf")
    miny = float("inf")
    maxx = float("-inf")
    maxy = float("-inf")
    for geom_wkt in wkts:
        geom = wkt.loads(geom_wkt)
        gx0, gy0, gx1, gy1 = geom.bounds
        minx = min(minx, gx0)
        miny = min(miny, gy0)
        maxx = max(maxx, gx1)
        maxy = max(maxy, gy1)
    return [minx, miny, maxx, maxy]


def fetch_region_fields(local_government_cd: str) -> pd.DataFrame:
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("INSTALL spatial; LOAD spatial;")
    query = f"""
        SELECT
            polygon_uuid,
            point_lng,
            point_lat,
            local_government_cd,
            land_type_en,
            ST_AsText(GEOM) AS wkt
        FROM read_parquet('{FIDE_PARQUET_URL}')
        WHERE local_government_cd = '{local_government_cd}'
          AND land_type_en = 'rice_field'
    """
    return con.execute(query).fetchdf()


def normalized_difference(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    denom = a + b
    out = np.full(a.shape, np.nan, dtype="float32")
    valid = np.isfinite(a) & np.isfinite(b) & (np.abs(denom) > 1e-6)
    out[valid] = (a[valid] - b[valid]) / denom[valid]
    return out


def group_items_by_date(items: list[object]) -> dict[str, list[object]]:
    grouped: dict[str, list[object]] = defaultdict(list)
    for item in items:
        grouped[item.datetime.date().isoformat()].append(item)
    return dict(grouped)


def read_merged_bbox_band(items: list[object], asset_name: str, bbox: list[float]) -> tuple[np.ndarray, rasterio.Affine, object]:
    with ExitStack() as stack:
        datasets = [stack.enter_context(rasterio.open(item.assets[asset_name].href)) for item in items]
        crs = datasets[0].crs
        transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        west, south = transformer.transform(bbox[0], bbox[1])
        east, north = transformer.transform(bbox[2], bbox[3])
        left, right = sorted([west, east])
        bottom, top = sorted([south, north])
        nodata = datasets[0].nodata if datasets[0].nodata is not None else 0
        merged, transform = merge(
            datasets,
            bounds=(left, bottom, right, top),
            nodata=nodata,
            indexes=1,
        )
        arr = np.array(merged[0], dtype="float32", copy=True)
        if nodata is not None:
            arr[arr == nodata] = np.nan
        return arr / 10000.0, transform, crs


def search_peak_scene(bbox: list[float], date_range: str) -> tuple[list[object], float]:
    catalog = Client.open(PLANETARY_COMPUTER_STAC)
    search = catalog.search(
        collections=["sentinel-2-l2a"],
        bbox=bbox,
        datetime=date_range,
        query={"eo:cloud_cover": {"lt": 20}},
        max_items=20,
    )
    items = list(search.items())
    if not items:
        raise RuntimeError("No Sentinel-2 L2A scenes found for prototype yield estimation.")

    grouped = group_items_by_date(items)
    best_items = None
    best_mean_ndvi = -np.inf
    for _, group in grouped.items():
        signed_group = [planetary_computer.sign(item) for item in group]
        b04, _, _ = read_merged_bbox_band(signed_group, "B04", bbox)
        b08, _, _ = read_merged_bbox_band(signed_group, "B08", bbox)
        ndvi = normalized_difference(b08, b04)
        mean_ndvi = float(np.nanmean(ndvi))
        if mean_ndvi > best_mean_ndvi:
            best_mean_ndvi = mean_ndvi
            best_items = signed_group
    if best_items is None:
        raise RuntimeError("Unable to identify a peak-greenness Sentinel scene.")
    return best_items, best_mean_ndvi


def compute_yield_table(fields_df: pd.DataFrame, bbox: list[float]) -> tuple[pd.DataFrame, dict]:
    b02 = b03 = None
    peak_items, municipality_mean_ndvi = search_peak_scene(bbox, DATE_RANGE)
    b02, _, _ = read_merged_bbox_band(peak_items, "B02", bbox)
    b03, _, _ = read_merged_bbox_band(peak_items, "B03", bbox)
    b04, transform, crs = read_merged_bbox_band(peak_items, "B04", bbox)
    b08, _, _ = read_merged_bbox_band(peak_items, "B08", bbox)

    geom_transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)

    geometries = []
    records = []
    for idx, row in enumerate(fields_df.itertuples(index=False), start=1):
        geom = wkt.loads(row.wkt)
        area_m2 = geodesic_area_m2(geom)
        geom_projected = shapely_transform(geom_transformer.transform, geom)
        geometries.append((geom_projected, idx))
        records.append(
            {
                "label_id": idx,
                "field_id": row.polygon_uuid,
                "local_government_cd": str(row.local_government_cd),
                "land_type_en": row.land_type_en,
                "point_lng": row.point_lng,
                "point_lat": row.point_lat,
                "area_m2": area_m2,
                "geometry": geom,
            }
        )

    label_raster = rasterize(
        geometries,
        out_shape=b04.shape,
        transform=transform,
        fill=0,
        dtype="int32",
        all_touched=True,
    )

    labels = label_raster.ravel()
    valid = (
        (labels > 0)
        & np.isfinite(b02.ravel())
        & np.isfinite(b03.ravel())
        & np.isfinite(b04.ravel())
        & np.isfinite(b08.ravel())
    )
    label_values = labels[valid]
    b02_values = b02.ravel()[valid]
    b03_values = b03.ravel()[valid]
    b04_values = b04.ravel()[valid]
    b08_values = b08.ravel()[valid]

    counts = np.bincount(label_values, minlength=len(records) + 1)
    b02_sums = np.bincount(label_values, weights=b02_values, minlength=len(records) + 1)
    b03_sums = np.bincount(label_values, weights=b03_values, minlength=len(records) + 1)
    b04_sums = np.bincount(label_values, weights=b04_values, minlength=len(records) + 1)
    b08_sums = np.bincount(label_values, weights=b08_values, minlength=len(records) + 1)

    output_rows = []
    for record in records:
        label_id = record["label_id"]
        pixel_count = int(counts[label_id])
        if pixel_count == 0:
            b02_mean = math.nan
            b03_mean = math.nan
            b04_mean = math.nan
            b08_mean = math.nan
            ndvi = math.nan
            savi = math.nan
            lai = math.nan
            yield_estimate = math.nan
        else:
            b02_mean = float(b02_sums[label_id] / pixel_count)
            b03_mean = float(b03_sums[label_id] / pixel_count)
            b04_mean = float(b04_sums[label_id] / pixel_count)
            b08_mean = float(b08_sums[label_id] / pixel_count)
            denominator = b08_mean + b04_mean
            ndvi = (
                float((b08_mean - b04_mean) / denominator)
                if abs(denominator) > 1e-6
                else math.nan
            )
            savi_denom = b08_mean + b04_mean + 0.5
            savi = (
                float(((b08_mean - b04_mean) / savi_denom) * 1.5)
                if abs(savi_denom) > 1e-6
                else math.nan
            )
            savi_for_lai = min(savi, 0.689) if math.isfinite(savi) else math.nan
            lai = (
                float(-math.log((0.69 - savi_for_lai) / 0.59) / 0.91)
                if math.isfinite(savi_for_lai) and savi_for_lai < 0.69
                else math.nan
            )
            yield_estimate = (
                float(-3.110 + 1.684 * lai + 12.458 * ndvi)
                if math.isfinite(lai) and math.isfinite(ndvi)
                else math.nan
            )

        output_rows.append(
            {
                **{k: v for k, v in record.items() if k not in {"label_id", "geometry"}},
                "pixel_count": pixel_count,
                "B02_mean": b02_mean,
                "B03_mean": b03_mean,
                "B04_mean": b04_mean,
                "B08_mean": b08_mean,
                "NDVI": ndvi,
                "SAVI": savi,
                "LAI": lai,
                "prototype_yield_t_ha": yield_estimate,
                "geometry": record["geometry"],
            }
        )

    output_df = pd.DataFrame(output_rows)
    metadata = {
        "region_id": REGION_ID,
        "region_label": REGION_LABEL,
        "local_government_cd": LOCAL_GOVERNMENT_CD,
        "bbox": bbox,
        "date_range": DATE_RANGE,
        "sentinel_scene_ids": [item.id for item in peak_items],
        "sentinel_scene_id": peak_items[0].id,
        "sentinel_datetime": peak_items[0].datetime.isoformat(),
        "sentinel_cloud_cover_pct": float(np.nanmean([item.properties.get("eo:cloud_cover", math.nan) for item in peak_items])),
        "municipality_mean_ndvi_peak_proxy": municipality_mean_ndvi,
        "formula": {
            "NDVI": "(B08 - B04) / (B08 + B04)",
            "SAVI": "((B08 - B04) / (B08 + B04 + 0.5)) * 1.5",
            "LAI": "-ln((0.69 - SAVI) / 0.59) / 0.91",
            "Yield": "-3.110 + 1.684 * LAI + 12.458 * NDVI",
        },
        "label": "Prototype yield estimate",
        "units": "t/ha",
        "notes": [
            "Prototype estimate from Sentinel-2 B04/B08 using a fitted formula chain.",
            "Peak scene chosen as the highest municipality-mean NDVI scene within the configured date range.",
            "This is not Japan-calibrated field truth and should be treated as yield potential, not actual yield.",
            "SAVI was capped at 0.689 for LAI domain stability when needed.",
        ],
    }
    return output_df, metadata


def build_yield_geojson(yield_df: pd.DataFrame) -> dict:
    features = []
    for row in yield_df.itertuples(index=False):
        simplified = row.geometry.simplify(0.00001, preserve_topology=True)
        properties = {
            "field_id": row.field_id,
            "local_government_cd": row.local_government_cd,
            "land_type_en": row.land_type_en,
            "area_m2": round(row.area_m2, 2),
            "point_lng": row.point_lng,
            "point_lat": row.point_lat,
            "pixel_count": row.pixel_count,
            "B02_mean": None if not math.isfinite(row.B02_mean) else round(row.B02_mean, 6),
            "B03_mean": None if not math.isfinite(row.B03_mean) else round(row.B03_mean, 6),
            "B04_mean": None if not math.isfinite(row.B04_mean) else round(row.B04_mean, 6),
            "B08_mean": None if not math.isfinite(row.B08_mean) else round(row.B08_mean, 6),
            "NDVI": None if not math.isfinite(row.NDVI) else round(row.NDVI, 6),
            "SAVI": None if not math.isfinite(row.SAVI) else round(row.SAVI, 6),
            "LAI": None if not math.isfinite(row.LAI) else round(row.LAI, 6),
            "prototype_yield_t_ha": (
                None
                if not math.isfinite(row.prototype_yield_t_ha)
                else round(row.prototype_yield_t_ha, 6)
            ),
        }
        features.append(
            {"type": "Feature", "properties": properties, "geometry": mapping(simplified)}
        )
    return {"type": "FeatureCollection", "features": features}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fields_df = fetch_region_fields(LOCAL_GOVERNMENT_CD)
    bbox = geometry_bbox_from_wkts(fields_df["wkt"])
    yield_df, metadata = compute_yield_table(fields_df, bbox)
    yield_df.drop(columns=["geometry"]).to_csv(OUTPUT_DIR / "field_yield_estimates.csv", index=False)
    geojson = build_yield_geojson(yield_df)
    (OUTPUT_DIR / "field_yield_estimates.geojson").write_text(
        json.dumps(geojson), encoding="utf-8"
    )
    (OUTPUT_DIR / "yield_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    summary = {
        **metadata,
        "field_count": int(len(yield_df)),
        "yield_mean_t_ha": float(yield_df["prototype_yield_t_ha"].mean()),
        "yield_median_t_ha": float(yield_df["prototype_yield_t_ha"].median()),
        "yield_min_t_ha": float(yield_df["prototype_yield_t_ha"].min()),
        "yield_max_t_ha": float(yield_df["prototype_yield_t_ha"].max()),
    }
    print(json.dumps(summary, indent=2))
    print(f"Wrote outputs to {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
