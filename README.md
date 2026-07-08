# Riceify data pipeline MVP

This repo starts with the data, not the dashboard.

The script in `scripts/extract_field_sentinel.py`:

1. Queries MAFF Fude field polygons from the Source Cooperative mirror.
2. Selects one rice-field polygon in a tight Niigata bounding box.
3. Searches Sentinel-2 Level-2A scenes on Microsoft Planetary Computer.
4. Extracts band means inside the selected field.
5. Computes NDVI, GNDVI, NDWI/LSWI, and simple red-edge indices.
6. Writes CSV/JSON/image outputs under `outputs/`.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/extract_field_sentinel.py
```

Default area: a rice-growing area in Niigata, Japan.

## Outputs

- `outputs/selected_field.geojson` - selected MAFF Fude polygon.
- `outputs/field_band_features.csv` - one row of model-style features.
- `outputs/field_metadata.json` - selected field and Sentinel scene metadata.
- `outputs/field_rgb_ndvi.png` - RGB and NDVI preview for the selected field.

## Notes

The field boundaries come from MAFF Fude polygon data mirrored by Source Cooperative.
Sentinel-2 L2A imagery comes from Microsoft Planetary Computer's STAC API.

This is still not a yield model. It is the first data extraction building block needed before training.

## Region-scale extraction

The script in `scripts/extract_region_fields.py` pulls all MAFF rice-field parcels in an official Niigata municipality and exports:

- `outputs/shibata_city_152064/region_fields.geojson`
- `outputs/shibata_city_152064/region_field_summary.csv`
- `outputs/shibata_city_152064/region_metadata.json`
- `outputs/shibata_city_152064/region_fields_map.html`

Run it with:

```bash
.venv/bin/python scripts/extract_region_fields.py
```

The current region is `Shibata City` with official local government code `152064`.

## Prototype yield layer

The script in `scripts/estimate_prototype_yield.py` computes a per-field prototype yield estimate for Shibata City using:

- `NDVI = (B08 - B04) / (B08 + B04)`
- `SAVI = ((B08 - B04) / (B08 + B04 + 0.5)) * 1.5`
- `LAI = -ln((0.69 - SAVI) / 0.59) / 0.91`
- `Yield = -3.110 + 1.684 * LAI + 12.458 * NDVI`

It searches for a peak-greenness proxy scene inside the configured date window, then writes:

- `outputs/shibata_city_152064/field_yield_estimates.csv`
- `outputs/shibata_city_152064/field_yield_estimates.geojson`
- `outputs/shibata_city_152064/yield_metadata.json`

Run it with:

```bash
.venv/bin/python scripts/estimate_prototype_yield.py
```

Treat this as a prototype yield potential layer, not calibrated Japan field truth.

## Region view images

The script in `scripts/generate_region_view_images.py` exports municipality-wide view assets for the selected Sentinel scene:

- `satellite_rgb.png`
- `blue.png`
- `green.png`
- `red.png`
- `nir.png`
- `ndvi.png`

It also writes `outputs/shibata_city_152064/views/views.json` for the website view switcher.
