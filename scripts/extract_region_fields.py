from __future__ import annotations

import json
from pathlib import Path

import duckdb
import folium
import pandas as pd
from shapely import wkt
from shapely.geometry import mapping
from pyproj import Geod


FIDE_PARQUET_URL = (
    "https://data.source.coop/pacificspatial/field-polygon-jp/parquet/"
    "jp_field_polygons_2024.parquet"
)

# Official municipality unit: Shibata City, Niigata.
# The 6-digit local government code comes from Japan's municipal code system
# as carried in the MAFF Fude polygons.
REGION_ID = "shibata_city_152064"
REGION_LABEL = "Shibata City"
LOCAL_GOVERNMENT_CD = "152064"

OUTPUT_DIR = Path("outputs") / REGION_ID


def geodesic_area_m2(geom) -> float:
    geod = Geod(ellps="GRS80")
    area, _ = geod.geometry_area_perimeter(geom)
    return abs(area)


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


def build_geojson(df: pd.DataFrame) -> dict:
    features = []
    for row in df.itertuples(index=False):
        geom = wkt.loads(row.wkt)
        area_m2 = geodesic_area_m2(geom)
        simplified = geom.simplify(0.00001, preserve_topology=True)
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "field_id": row.polygon_uuid,
                    "local_government_cd": str(row.local_government_cd),
                    "land_type_en": row.land_type_en,
                    "area_m2": round(area_m2, 2),
                    "point_lng": row.point_lng,
                    "point_lat": row.point_lat,
                },
                "geometry": mapping(simplified),
            }
        )
    return {"type": "FeatureCollection", "features": features}


def save_tabular_outputs(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["geometry_obj"] = df["wkt"].map(wkt.loads)
    df["area_m2"] = df["geometry_obj"].map(geodesic_area_m2)

    summary_df = df[
        [
            "polygon_uuid",
            "local_government_cd",
            "land_type_en",
            "point_lng",
            "point_lat",
            "area_m2",
        ]
    ].rename(columns={"polygon_uuid": "field_id"})
    summary_df.to_csv(OUTPUT_DIR / "region_field_summary.csv", index=False)
    return summary_df


def create_map(geojson: dict, bbox: list[float], summary_df: pd.DataFrame) -> folium.Map:
    west, south, east, north = bbox
    center = [(south + north) / 2, (west + east) / 2]
    area_ha = summary_df["area_m2"].sum() / 10000.0

    fmap = folium.Map(location=center, zoom_start=11, tiles="CartoDB positron")

    folium.GeoJson(
        geojson,
        name="Rice field parcels",
        style_function=lambda feature: {
            "color": "#2a7f62",
            "weight": 0.45,
            "fillColor": "#9ed8a6",
            "fillOpacity": 0.14,
        },
        highlight_function=lambda feature: {
            "color": "#14532d",
            "weight": 1.2,
            "fillOpacity": 0.24,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=["field_id", "area_m2", "local_government_cd"],
            aliases=["Field ID", "Area (m2)", "Municipality code"],
            sticky=False,
            labels=True,
        ),
    ).add_to(fmap)

    folium.Rectangle(
        bounds=[[south, west], [north, east]],
        color="#1d4ed8",
        weight=1,
        fill=False,
        dash_array="6,4",
        tooltip=f"{REGION_LABEL} rice-field extent",
    ).add_to(fmap)

    title_html = f"""
    <div style="
        position: fixed;
        top: 14px;
        left: 50px;
        z-index: 9999;
        background: rgba(255,255,255,0.92);
        border: 1px solid rgba(15,23,42,0.12);
        border-radius: 8px;
        padding: 12px 14px;
        font-family: Arial, sans-serif;
        box-shadow: 0 8px 24px rgba(15,23,42,0.12);
    ">
        <div style="font-size: 16px; font-weight: 700;">{REGION_LABEL} rice fields</div>
        <div style="font-size: 12px; margin-top: 4px;">Region ID: {REGION_ID}</div>
        <div style="font-size: 12px;">Municipality code: {LOCAL_GOVERNMENT_CD}</div>
        <div style="font-size: 12px;">Fields: {len(summary_df):,}</div>
        <div style="font-size: 12px;">Total mapped rice area: {area_ha:,.1f} ha</div>
    </div>
    """
    fmap.get_root().html.add_child(folium.Element(title_html))
    fmap.fit_bounds([[south, west], [north, east]])
    folium.LayerControl(collapsed=False).add_to(fmap)
    return fmap


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    fields_df = fetch_region_fields(LOCAL_GOVERNMENT_CD)
    if fields_df.empty:
        raise RuntimeError("No rice fields found for the configured study region.")

    bbox = [
        float(fields_df["point_lng"].min()),
        float(fields_df["point_lat"].min()),
        float(fields_df["point_lng"].max()),
        float(fields_df["point_lat"].max()),
    ]

    geojson = build_geojson(fields_df)
    (OUTPUT_DIR / "region_fields.geojson").write_text(
        json.dumps(geojson), encoding="utf-8"
    )

    summary_df = save_tabular_outputs(fields_df)

    metadata = {
        "region_id": REGION_ID,
        "region_label": REGION_LABEL,
        "local_government_cd": LOCAL_GOVERNMENT_CD,
        "bbox": bbox,
        "field_count": int(len(summary_df)),
        "total_area_m2": float(summary_df["area_m2"].sum()),
        "field_polygon_source": FIDE_PARQUET_URL,
    }
    (OUTPUT_DIR / "region_metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )

    fmap = create_map(geojson, bbox, summary_df)
    fmap.save(str(OUTPUT_DIR / "region_fields_map.html"))

    print(json.dumps(metadata, indent=2))
    print(f"Wrote outputs to {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
