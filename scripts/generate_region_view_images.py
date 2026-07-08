from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import planetary_computer
import rasterio
from PIL import Image
from pyproj import Transformer
from pystac_client import Client
from rasterio.features import rasterize
from rasterio.merge import merge
from rasterio.transform import array_bounds
from rasterio.warp import Resampling, calculate_default_transform, reproject


PLANETARY_COMPUTER_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
REGION_ID = "shibata_city_152064"
OUTPUT_DIR = Path("outputs") / REGION_ID
VIEWS_DIR = OUTPUT_DIR / "views"


def normalized_difference(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    denom = a + b
    out = np.full(a.shape, np.nan, dtype="float32")
    valid = np.isfinite(a) & np.isfinite(b) & (np.abs(denom) > 1e-6)
    out[valid] = (a[valid] - b[valid]) / denom[valid]
    return out


def load_metadata() -> dict:
    return json.loads((OUTPUT_DIR / "yield_metadata.json").read_text())


def load_field_yield_geojson() -> dict:
    return json.loads((OUTPUT_DIR / "field_yield_estimates.geojson").read_text())


def fetch_items(scene_ids: list[str]) -> list[object]:
    catalog = Client.open(PLANETARY_COMPUTER_STAC)
    search = catalog.search(collections=["sentinel-2-l2a"], ids=scene_ids)
    items = list(search.items())
    items.sort(key=lambda item: scene_ids.index(item.id))
    return [planetary_computer.sign(item) for item in items]


def read_merged_bbox_band(items: list[object], asset_name: str, bbox: list[float]) -> tuple[np.ndarray, object, object]:
    with ExitStack() as stack:
        datasets = [stack.enter_context(rasterio.open(item.assets[asset_name].href)) for item in items]
        crs = datasets[0].crs
        transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        west, south = transformer.transform(bbox[0], bbox[1])
        east, north = transformer.transform(bbox[2], bbox[3])
        left, right = sorted([west, east])
        bottom, top = sorted([south, north])
        nodata = datasets[0].nodata if datasets[0].nodata is not None else 0
        merged, out_transform = merge(
            datasets,
            bounds=(left, bottom, right, top),
            nodata=nodata,
            indexes=1,
        )
        arr = np.array(merged[0], dtype="float32", copy=True)
        if nodata is not None:
            arr[arr == nodata] = np.nan
        return arr / 10000.0, out_transform, crs


def raster_bounds_to_lonlat(transform, shape, crs) -> list[float]:
    height, width = shape
    left = transform.c
    top = transform.f
    right = left + transform.a * width
    bottom = top + transform.e * height
    transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    min_lng, min_lat = transformer.transform(left, bottom)
    max_lng, max_lat = transformer.transform(right, top)
    west, east = sorted([min_lng, max_lng])
    south, north = sorted([min_lat, max_lat])
    return [west, south, east, north]


def save_single_band_image(arr: np.ndarray, cmap: str, title: str, output_path: Path, vmin=None, vmax=None) -> None:
    fig, ax = plt.subplots(figsize=(8, 8), constrained_layout=True)
    im = ax.imshow(arr, cmap=cmap, vmin=vmin, vmax=vmax)
    ax.set_title(title)
    ax.axis("off")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def save_rgb_image(rgb: np.ndarray, output_path: Path) -> None:
    high = np.nanpercentile(rgb, 98)
    scaled = np.clip(rgb / high, 0, 1) if high > 0 else np.nan_to_num(rgb)
    fig, ax = plt.subplots(figsize=(8, 8), constrained_layout=True)
    ax.imshow(scaled)
    ax.set_title("Satellite RGB")
    ax.axis("off")
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def save_overlay_rgb(rgb: np.ndarray, output_path: Path) -> None:
    high = np.nanpercentile(rgb, 98)
    scaled = np.clip(rgb / high, 0, 1) if high > 0 else np.nan_to_num(rgb)
    scaled = np.nan_to_num(scaled, nan=0.0)
    alpha = np.where(np.isfinite(rgb).any(axis=2), 255, 0).astype(np.uint8)
    rgba = np.dstack(
        [
            (scaled[:, :, 0] * 255).astype(np.uint8),
            (scaled[:, :, 1] * 255).astype(np.uint8),
            (scaled[:, :, 2] * 255).astype(np.uint8),
            alpha,
        ]
    )
    Image.fromarray(rgba, mode="RGBA").save(output_path)
    return rgba


def save_overlay_single_band(arr: np.ndarray, cmap: str, output_path: Path, vmin=None, vmax=None) -> None:
    cmap_obj = plt.get_cmap(cmap)
    if vmin is None:
        vmin = float(np.nanpercentile(arr, 2))
    if vmax is None:
        vmax = float(np.nanpercentile(arr, 98))
    normalized = np.clip((arr - vmin) / (vmax - vmin + 1e-9), 0, 1)
    rgba = (cmap_obj(normalized) * 255).astype(np.uint8)
    rgba[:, :, 3] = np.where(np.isfinite(arr), 255, 0).astype(np.uint8)
    Image.fromarray(rgba, mode="RGBA").save(output_path)
    return rgba


def percentile_range(arr: np.ndarray, low: float = 2, high: float = 98) -> tuple[float, float]:
    return float(np.nanpercentile(arr, low)), float(np.nanpercentile(arr, high))


def legend_metadata(title: str, units: str, vmin: float, vmax: float, gradient: str) -> dict:
    midpoint = (vmin + vmax) / 2
    return {
        "title": title,
        "units": units,
        "min": vmin,
        "mid": midpoint,
        "max": vmax,
        "gradient": gradient,
    }


def save_geotiff_rgba_direct(rgba: np.ndarray, transform, crs, output_path: Path) -> None:
    height, width, _ = rgba.shape
    with rasterio.open(
        output_path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=4,
        dtype="uint8",
        crs=crs,
        transform=transform,
    ) as dataset:
        for idx in range(4):
            dataset.write(rgba[:, :, idx], idx + 1)
    web_path = output_path.with_name(output_path.name.replace("_overlay.tif", "_web_overlay.png"))
    Image.fromarray(rgba, mode="RGBA").save(web_path, optimize=True)
    Image.fromarray(rgba, mode="RGBA").save(web_path.with_suffix(".webp"), format="WEBP", quality=86, method=6)


def save_reprojected_geotiff_rgba(
    rgba: np.ndarray,
    src_transform,
    src_crs,
    output_path: Path,
) -> list[float]:
    height, width, _ = rgba.shape
    left, bottom, right, top = array_bounds(height, width, src_transform)
    dst_crs = "EPSG:4326"
    dst_transform, dst_width, dst_height = calculate_default_transform(
        src_crs,
        dst_crs,
        width,
        height,
        left,
        bottom,
        right,
        top,
    )
    warped = np.zeros((4, dst_height, dst_width), dtype="uint8")
    for idx in range(4):
        reproject(
            source=rgba[:, :, idx],
            destination=warped[idx],
            src_transform=src_transform,
            src_crs=src_crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            resampling=Resampling.nearest if idx == 3 else Resampling.bilinear,
        )

    with rasterio.open(
        output_path,
        "w",
        driver="GTiff",
        width=dst_width,
        height=dst_height,
        count=4,
        dtype="uint8",
        crs=dst_crs,
        transform=dst_transform,
    ) as dataset:
        for idx in range(4):
            dataset.write(warped[idx], idx + 1)
    web_path = output_path.with_name(output_path.name.replace("_overlay.tif", "_web_overlay.png"))
    web_rgba = Image.fromarray(warped.transpose(1, 2, 0), mode="RGBA")
    web_rgba.save(web_path, optimize=True)
    web_rgba.save(web_path.with_suffix(".webp"), format="WEBP", quality=86, method=6)
    west, south, east, north = array_bounds(dst_height, dst_width, dst_transform)
    return [west, south, east, north]


def build_yield_raster(geojson: dict, out_shape: tuple[int, int], transform) -> np.ndarray:
    shapes = []
    for feature in geojson["features"]:
        value = feature["properties"].get("prototype_yield_t_ha")
        if value is None or not np.isfinite(value):
            continue
        shapes.append((feature["geometry"], float(value)))
    return rasterize(
        shapes,
        out_shape=out_shape,
        transform=transform,
        fill=np.nan,
        dtype="float32",
        all_touched=True,
    )


def main() -> None:
    metadata = load_metadata()
    yield_geojson = load_field_yield_geojson()
    scene_ids = metadata.get("sentinel_scene_ids") or [metadata["sentinel_scene_id"]]
    bbox = metadata["bbox"]
    VIEWS_DIR.mkdir(parents=True, exist_ok=True)

    items = fetch_items(scene_ids)
    b02, transform, crs = read_merged_bbox_band(items, "B02", bbox)
    b03, _, _ = read_merged_bbox_band(items, "B03", bbox)
    b04, _, _ = read_merged_bbox_band(items, "B04", bbox)
    b08, _, _ = read_merged_bbox_band(items, "B08", bbox)
    arrays = {
        "B02": b02,
        "B03": b03,
        "B04": b04,
        "B08": b08,
    }
    arrays["NDVI"] = normalized_difference(arrays["B08"], arrays["B04"])
    view_ranges = {
        "B02": percentile_range(arrays["B02"]),
        "B03": percentile_range(arrays["B03"]),
        "B04": percentile_range(arrays["B04"]),
        "B08": percentile_range(arrays["B08"]),
        "NDVI": (-0.2, 0.9),
    }

    rgb = np.dstack([arrays["B04"], arrays["B03"], arrays["B02"]])
    save_rgb_image(rgb, VIEWS_DIR / "satellite_rgb.png")
    satellite_rgba = save_overlay_rgb(rgb, VIEWS_DIR / "satellite_rgb_overlay.png")
    overlay_bounds = save_reprojected_geotiff_rgba(
        satellite_rgba,
        transform,
        crs,
        VIEWS_DIR / "satellite_rgb_overlay.tif",
    )
    with rasterio.open(VIEWS_DIR / "satellite_rgb_overlay.tif") as overlay_dataset:
        overlay_transform = overlay_dataset.transform
        overlay_crs = overlay_dataset.crs
        overlay_shape = (overlay_dataset.height, overlay_dataset.width)

    save_single_band_image(arrays["B02"], "Blues", "Blue (490nm)", VIEWS_DIR / "blue.png", *view_ranges["B02"])
    save_single_band_image(arrays["B03"], "Greens", "Green (560nm)", VIEWS_DIR / "green.png", *view_ranges["B03"])
    save_single_band_image(arrays["B04"], "Reds", "Red (665nm)", VIEWS_DIR / "red.png", *view_ranges["B04"])
    save_single_band_image(arrays["B08"], "Purples", "NIR (842nm)", VIEWS_DIR / "nir.png", *view_ranges["B08"])
    save_single_band_image(arrays["NDVI"], "RdYlGn", "NDVI", VIEWS_DIR / "ndvi.png", vmin=-0.2, vmax=0.9)
    blue_rgba = save_overlay_single_band(arrays["B02"], "Blues", VIEWS_DIR / "blue_overlay.png", *view_ranges["B02"])
    green_rgba = save_overlay_single_band(arrays["B03"], "Greens", VIEWS_DIR / "green_overlay.png", *view_ranges["B03"])
    red_rgba = save_overlay_single_band(arrays["B04"], "Reds", VIEWS_DIR / "red_overlay.png", *view_ranges["B04"])
    nir_rgba = save_overlay_single_band(arrays["B08"], "Purples", VIEWS_DIR / "nir_overlay.png", *view_ranges["B08"])
    ndvi_rgba = save_overlay_single_band(arrays["NDVI"], "RdYlGn", VIEWS_DIR / "ndvi_overlay.png", vmin=-0.2, vmax=0.9)
    save_reprojected_geotiff_rgba(blue_rgba, transform, crs, VIEWS_DIR / "blue_overlay.tif")
    save_reprojected_geotiff_rgba(green_rgba, transform, crs, VIEWS_DIR / "green_overlay.tif")
    save_reprojected_geotiff_rgba(red_rgba, transform, crs, VIEWS_DIR / "red_overlay.tif")
    save_reprojected_geotiff_rgba(nir_rgba, transform, crs, VIEWS_DIR / "nir_overlay.tif")
    save_reprojected_geotiff_rgba(ndvi_rgba, transform, crs, VIEWS_DIR / "ndvi_overlay.tif")

    yield_raster = build_yield_raster(yield_geojson, overlay_shape, overlay_transform)
    yield_vmin = float(np.nanpercentile(yield_raster, 2))
    yield_vmax = float(np.nanpercentile(yield_raster, 98))
    save_single_band_image(
        yield_raster,
        "RdYlGn",
        "Expected yield (t/ha)",
        VIEWS_DIR / "yield.png",
        vmin=yield_vmin,
        vmax=yield_vmax,
    )
    yield_rgba = save_overlay_single_band(
        yield_raster,
        "RdYlGn",
        VIEWS_DIR / "yield_overlay.png",
        vmin=yield_vmin,
        vmax=yield_vmax,
    )
    save_geotiff_rgba_direct(yield_rgba, overlay_transform, overlay_crs, VIEWS_DIR / "yield_overlay.tif")

    views_metadata = {
        "default_view": "satellite",
        "overlay_bounds": overlay_bounds,
        "views": [
            {
                "id": "satellite",
                "label": "Satellite",
                "image": "/outputs/shibata_city_152064/views/satellite_rgb.png",
                "overlay_image": "/outputs/shibata_city_152064/views/satellite_rgb_overlay.png",
                "web_image": "/outputs/shibata_city_152064/views/satellite_rgb_web_overlay.webp",
                "raster": "/outputs/shibata_city_152064/views/satellite_rgb_overlay.tif",
            },
            {
                "id": "blue",
                "label": "Blue",
                "image": "/outputs/shibata_city_152064/views/blue.png",
                "overlay_image": "/outputs/shibata_city_152064/views/blue_overlay.png",
                "web_image": "/outputs/shibata_city_152064/views/blue_web_overlay.webp",
                "raster": "/outputs/shibata_city_152064/views/blue_overlay.tif",
                "legend": legend_metadata(
                    "B02 blue reflectance",
                    "reflectance",
                    view_ranges["B02"][0],
                    view_ranges["B02"][1],
                    "linear-gradient(90deg, #f7fbff, #6baed6, #08306b)",
                ),
            },
            {
                "id": "green",
                "label": "Green",
                "image": "/outputs/shibata_city_152064/views/green.png",
                "overlay_image": "/outputs/shibata_city_152064/views/green_overlay.png",
                "web_image": "/outputs/shibata_city_152064/views/green_web_overlay.webp",
                "raster": "/outputs/shibata_city_152064/views/green_overlay.tif",
                "legend": legend_metadata(
                    "B03 green reflectance",
                    "reflectance",
                    view_ranges["B03"][0],
                    view_ranges["B03"][1],
                    "linear-gradient(90deg, #f7fcf5, #74c476, #00441b)",
                ),
            },
            {
                "id": "red",
                "label": "Red",
                "image": "/outputs/shibata_city_152064/views/red.png",
                "overlay_image": "/outputs/shibata_city_152064/views/red_overlay.png",
                "web_image": "/outputs/shibata_city_152064/views/red_web_overlay.webp",
                "raster": "/outputs/shibata_city_152064/views/red_overlay.tif",
                "legend": legend_metadata(
                    "B04 red reflectance",
                    "reflectance",
                    view_ranges["B04"][0],
                    view_ranges["B04"][1],
                    "linear-gradient(90deg, #fff5f0, #fb6a4a, #67000d)",
                ),
            },
            {
                "id": "nir",
                "label": "NIR",
                "image": "/outputs/shibata_city_152064/views/nir.png",
                "overlay_image": "/outputs/shibata_city_152064/views/nir_overlay.png",
                "web_image": "/outputs/shibata_city_152064/views/nir_web_overlay.webp",
                "raster": "/outputs/shibata_city_152064/views/nir_overlay.tif",
                "legend": legend_metadata(
                    "B08 near-infrared reflectance",
                    "reflectance",
                    view_ranges["B08"][0],
                    view_ranges["B08"][1],
                    "linear-gradient(90deg, #fcfbfd, #9e9ac8, #3f007d)",
                ),
            },
            {
                "id": "ndvi",
                "label": "NDVI",
                "image": "/outputs/shibata_city_152064/views/ndvi.png",
                "overlay_image": "/outputs/shibata_city_152064/views/ndvi_overlay.png",
                "web_image": "/outputs/shibata_city_152064/views/ndvi_web_overlay.webp",
                "raster": "/outputs/shibata_city_152064/views/ndvi_overlay.tif",
                "legend": legend_metadata(
                    "NDVI",
                    "index",
                    view_ranges["NDVI"][0],
                    view_ranges["NDVI"][1],
                    "linear-gradient(90deg, #d73027, #fee08b, #1a9850)",
                ),
            },
            {
                "id": "yield",
                "label": "Yield",
                "image": "/outputs/shibata_city_152064/views/yield.png",
                "overlay_image": "/outputs/shibata_city_152064/views/yield_overlay.png",
                "web_image": "/outputs/shibata_city_152064/views/yield_web_overlay.webp",
                "raster": "/outputs/shibata_city_152064/views/yield_overlay.tif",
                "legend": legend_metadata(
                    "Expected yield",
                    "t/ha",
                    yield_vmin,
                    yield_vmax,
                    "linear-gradient(90deg, #d73027, #fee08b, #1a9850)",
                ),
            },
        ],
    }
    (VIEWS_DIR / "views.json").write_text(json.dumps(views_metadata, indent=2), encoding="utf-8")
    print(json.dumps(views_metadata, indent=2))


if __name__ == "__main__":
    main()
