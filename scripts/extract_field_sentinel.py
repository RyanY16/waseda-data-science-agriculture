from __future__ import annotations

import json
import math
import warnings
from dataclasses import dataclass
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import planetary_computer
import rasterio
from pyproj import Geod, Transformer
from pystac_client import Client
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds
from shapely import wkt
from shapely.geometry import mapping, shape
from shapely.ops import transform as shapely_transform


FUDÉ_PARQUET_URL = (
    "https://data.source.coop/pacificspatial/field-polygon-jp/parquet/"
    "jp_field_polygons_2024.parquet"
)
PLANETARY_COMPUTER_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"

# Tight box in a paddy-heavy area near Niigata. Keep this small so remote
# parquet and Sentinel reads stay fast.
DEFAULT_BBOX = [138.86, 37.76, 138.92, 37.82]  # west, south, east, north
DEFAULT_DATE_RANGE = "2024-06-01/2024-09-30"

OUTPUT_DIR = Path("outputs")

warnings.filterwarnings(
    "ignore",
    message="Setting the shape on a NumPy array has been deprecated.*",
    category=DeprecationWarning,
)


@dataclass
class SelectedField:
    polygon_uuid: str
    point_lng: float
    point_lat: float
    local_government_cd: str
    land_type_en: str
    area_m2: float
    geometry: dict


def geodesic_area_m2(geom) -> float:
    geod = Geod(ellps="GRS80")
    area, _ = geod.geometry_area_perimeter(geom)
    return abs(area)


def query_candidate_fields(bbox: list[float], limit: int = 5000) -> pd.DataFrame:
    west, south, east, north = bbox
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("INSTALL spatial; LOAD spatial;")

    query = f"""
        SELECT
            polygon_uuid,
            land_type_en,
            point_lng,
            point_lat,
            local_government_cd,
            ST_AsText(GEOM) AS wkt
        FROM read_parquet('{FUDÉ_PARQUET_URL}')
        WHERE point_lng BETWEEN {west} AND {east}
          AND point_lat BETWEEN {south} AND {north}
          AND land_type_en = 'rice_field'
        LIMIT {limit}
    """
    return con.execute(query).fetchdf()


def select_field(bbox: list[float]) -> SelectedField:
    candidates = query_candidate_fields(bbox)
    if candidates.empty:
        raise RuntimeError(f"No MAFF Fude rice-field polygons found in bbox {bbox}.")

    candidates["geometry_obj"] = candidates["wkt"].map(wkt.loads)
    candidates["area_m2"] = candidates["geometry_obj"].map(geodesic_area_m2)

    # Avoid picking a tiny sliver or a giant multi-plot block. This gives a
    # field-sized parcel that still has enough Sentinel pixels to inspect.
    candidates = candidates.sort_values("area_m2", ascending=False)
    field_row = candidates[
        (candidates["area_m2"] >= 1500) & (candidates["area_m2"] <= 20000)
    ].head(1)
    if field_row.empty:
        field_row = candidates.head(1)

    row = field_row.iloc[0]
    return SelectedField(
        polygon_uuid=row["polygon_uuid"],
        point_lng=float(row["point_lng"]),
        point_lat=float(row["point_lat"]),
        local_government_cd=str(row["local_government_cd"]),
        land_type_en=row["land_type_en"],
        area_m2=float(row["area_m2"]),
        geometry=mapping(row["geometry_obj"]),
    )


def search_sentinel_scene(bbox: list[float], date_range: str):
    catalog = Client.open(PLANETARY_COMPUTER_STAC)
    search = catalog.search(
        collections=["sentinel-2-l2a"],
        bbox=bbox,
        datetime=date_range,
        query={"eo:cloud_cover": {"lt": 30}},
        max_items=25,
    )
    items = list(search.items())
    if not items:
        raise RuntimeError("No Sentinel-2 L2A scenes found for the selected area/date range.")
    items.sort(key=lambda item: item.properties.get("eo:cloud_cover", 1000))
    return planetary_computer.sign(items[0])


def field_geometry_in_crs(field_geom: dict, dst_crs) -> dict:
    geom = shape(field_geom)
    transformer = Transformer.from_crs("EPSG:4326", dst_crs, always_xy=True)
    return mapping(shapely_transform(transformer.transform, geom))


def read_asset_crop(asset_href: str, field_geom: dict, pad_meters: float = 40.0):
    with rasterio.open(asset_href) as src:
        geom_proj = field_geometry_in_crs(field_geom, src.crs)
        bounds = shape(geom_proj).bounds
        window = from_bounds(
            bounds[0] - pad_meters,
            bounds[1] - pad_meters,
            bounds[2] + pad_meters,
            bounds[3] + pad_meters,
            src.transform,
        ).round_offsets().round_lengths()
        data = np.array(src.read(1, window=window), dtype="float32", copy=True)
        transform = src.window_transform(window)
        nodata = src.nodata
        if nodata is not None:
            data[data == nodata] = np.nan

        mask = geometry_mask(
            [geom_proj],
            out_shape=data.shape,
            transform=transform,
            invert=True,
            all_touched=True,
        )
        return data, mask, transform, src.crs


def scale_reflectance(arr: np.ndarray) -> np.ndarray:
    # Planetary Computer Sentinel-2 L2A COGs are scaled reflectance.
    return arr / 10000.0


def masked_stats(arr: np.ndarray, mask: np.ndarray) -> dict[str, float]:
    values = arr[mask & np.isfinite(arr)]
    if values.size == 0:
        return {"mean": math.nan, "median": math.nan, "min": math.nan, "max": math.nan}
    return {
        "mean": float(np.nanmean(values)),
        "median": float(np.nanmedian(values)),
        "min": float(np.nanmin(values)),
        "max": float(np.nanmax(values)),
    }


def normalized_difference(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    denom = a + b
    out = np.full(a.shape, np.nan, dtype="float32")
    valid = np.isfinite(a) & np.isfinite(b) & (np.abs(denom) > 1e-6)
    out[valid] = (a[valid] - b[valid]) / denom[valid]
    return out


def extract_features(item, field: SelectedField) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    asset_names = ["B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B11", "B12"]
    features: dict[str, float | str] = {
        "field_id": field.polygon_uuid,
        "field_area_m2": field.area_m2,
        "field_center_lng": field.point_lng,
        "field_center_lat": field.point_lat,
        "local_government_cd": field.local_government_cd,
        "sentinel_item_id": item.id,
        "datetime": item.datetime.isoformat(),
        "scene_cloud_cover_pct": float(item.properties.get("eo:cloud_cover", np.nan)),
    }

    arrays: dict[str, np.ndarray] = {}
    masks: dict[str, np.ndarray] = {}
    transforms = {}

    for asset_name in asset_names:
        href = item.assets[asset_name].href
        raw, mask, transform, _ = read_asset_crop(href, field.geometry)
        arr = scale_reflectance(raw)
        arrays[asset_name] = arr
        masks[asset_name] = mask
        transforms[asset_name] = transform
        stats = masked_stats(arr, mask)
        for stat_name, value in stats.items():
            features[f"{asset_name}_{stat_name}"] = value

    red = arrays["B04"]
    green = arrays["B03"]
    nir = arrays["B08"]
    swir1 = arrays["B11"]

    ndvi = normalized_difference(nir, red)
    gndvi = normalized_difference(nir, green)

    # B11 is 20 m while B08 is 10 m. For a scalar MVP feature, calculate from
    # field-level means to avoid resampling until we build the full pipeline.
    b08_mean = features["B08_mean"]
    b11_mean = features["B11_mean"]
    features["NDWI_LSWI_mean"] = (
        (b08_mean - b11_mean) / (b08_mean + b11_mean)
        if np.isfinite(b08_mean) and np.isfinite(b11_mean) and abs(b08_mean + b11_mean) > 1e-6
        else math.nan
    )

    red_edge_means = {}
    for band in ["B05", "B06", "B07"]:
        band_mean = features[f"{band}_mean"]
        red_edge_means[f"RE_NDVI_{band}_mean"] = (
            (b08_mean - band_mean) / (b08_mean + band_mean)
            if np.isfinite(b08_mean) and np.isfinite(band_mean) and abs(b08_mean + band_mean) > 1e-6
            else math.nan
        )
    features.update(red_edge_means)

    for name, arr in [("NDVI", ndvi), ("GNDVI", gndvi)]:
        stats = masked_stats(arr, masks["B08"])
        for stat_name, value in stats.items():
            features[f"{name}_{stat_name}"] = value

    return pd.DataFrame([features]), {
        "B02": arrays["B02"],
        "B03": arrays["B03"],
        "B04": arrays["B04"],
        "B08": arrays["B08"],
        "NDVI": ndvi,
        "mask_10m": masks["B08"],
    }


def save_selected_field_geojson(field: SelectedField) -> None:
    feature_collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "polygon_uuid": field.polygon_uuid,
                    "land_type_en": field.land_type_en,
                    "point_lng": field.point_lng,
                    "point_lat": field.point_lat,
                    "local_government_cd": field.local_government_cd,
                    "area_m2": field.area_m2,
                },
                "geometry": field.geometry,
            }
        ],
    }
    (OUTPUT_DIR / "selected_field.geojson").write_text(
        json.dumps(feature_collection, indent=2), encoding="utf-8"
    )


def save_preview(arrays: dict[str, np.ndarray]) -> None:
    red = arrays["B04"]
    green = arrays["B03"]
    blue = arrays["B02"]
    rgb = np.dstack([red, green, blue])
    high = np.nanpercentile(rgb, 98)
    rgb = np.clip(rgb / high, 0, 1) if high > 0 else np.nan_to_num(rgb)

    ndvi = arrays["NDVI"].copy()
    ndvi[~arrays["mask_10m"]] = np.nan

    fig, axes = plt.subplots(1, 2, figsize=(10, 5), constrained_layout=True)
    axes[0].imshow(rgb)
    axes[0].set_title("Sentinel-2 RGB crop")
    axes[0].axis("off")

    im = axes[1].imshow(ndvi, cmap="RdYlGn", vmin=-0.2, vmax=0.9)
    axes[1].set_title("NDVI inside selected field")
    axes[1].axis("off")
    fig.colorbar(im, ax=axes[1], fraction=0.046, pad=0.04)
    fig.savefig(OUTPUT_DIR / "field_rgb_ndvi.png", dpi=180)
    plt.close(fig)


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    field = select_field(DEFAULT_BBOX)
    item = search_sentinel_scene(DEFAULT_BBOX, DEFAULT_DATE_RANGE)
    features, preview_arrays = extract_features(item, field)

    save_selected_field_geojson(field)
    save_preview(preview_arrays)

    features.to_csv(OUTPUT_DIR / "field_band_features.csv", index=False)

    metadata = {
        "field": {
            "polygon_uuid": field.polygon_uuid,
            "land_type_en": field.land_type_en,
            "area_m2": field.area_m2,
            "point_lng": field.point_lng,
            "point_lat": field.point_lat,
            "local_government_cd": field.local_government_cd,
        },
        "sentinel_scene": {
            "item_id": item.id,
            "datetime": item.datetime.isoformat(),
            "cloud_cover_pct": item.properties.get("eo:cloud_cover"),
            "collection": item.collection_id,
        },
        "sources": {
            "field_polygons": FUDÉ_PARQUET_URL,
            "sentinel_stac": PLANETARY_COMPUTER_STAC,
        },
    }
    (OUTPUT_DIR / "field_metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )

    print("Selected field")
    print(json.dumps(metadata["field"], indent=2))
    print("\nSentinel scene")
    print(json.dumps(metadata["sentinel_scene"], indent=2))
    print("\nFeature row")
    print(features.T.to_string(header=False))
    print(f"\nWrote outputs to {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
