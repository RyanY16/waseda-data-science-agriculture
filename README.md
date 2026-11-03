# Rice Flood Observatory — Waseda data science project

An interactive dashboard for the October 2019 Typhoon Hagibis flood in Koriyama, Fukushima. This replaces the earlier Shibata/Riceify web dashboard in this repository. Earlier research scripts, notebooks and outputs are retained; their documentation is in [the legacy pipeline notes](docs/legacy-shibata-pipeline.md).

## Run locally

```sh
npm start
```

Open http://127.0.0.1:8010/. This is a static application: no npm dependencies or API credentials are needed. Python 3 serves the files; Node.js 18+ runs the checks and build. GSI background maps and Google Fonts require internet. The field polygons, radar images, model footprint, GSI event-depth mosaic and Leaflet library are bundled.

```sh
npm test
npm run build
```

The build copies `site/` into generated `public/`. The existing `vercel.json` deploys that directory at the website root. The root webpage is the current dashboard; research outputs are not copied into the deployment.

Japanese is the default. Use the language menu in the header to switch to English; the choice is saved in your browser.

## Three map views

The dropdown directly above the map selects:

1. **Before the flood:** actual Sentinel-1 VV imagery from 7 October 2019, mapped paddy parcels and assumed standing-harvest potential. It does not establish that a field was dry or had rice planted.
2. **Flood-affected area:** the dashboard opens with the radar footprint. The layer menu opens a side-by-side comparison with approximate 0/1 labels digitized from Igarashi & Wakabayashi (2023), Figure 2, in the southern holdout. Both panels use the same 11,235 test pixels; grey means excluded from scoring. Flood IoU is 91.5% and F1 is 95.6%. This is a same-event comparison, not independent flood validation.
3. **Rice production after flood:** scenario standing rice remaining, reduction per hectare or tonnes lost. Click a field to compare before/after standing production. These are scenarios, not measured harvest outcomes.

Panning and zoom-out stay inside the mapped paddy study region. Source parcel boundaries have a confirmed local mismatch against aerial imagery; no alignment correction is claimed.

## Farmer harvest outlook

A study-wide summary beside the map shows mapped paddy area, no-flood season production, detected flooded paddy area, and season production after assumed damage with the percentage reduction. Controls change normal yield, unharvested share and the depth/duration damage scenario. Map filters do not change these totals.

Default assumptions: normal yield 5 t/ha, 25% still unharvested, maturity-stage damage fraction 0.4787 at an assumed 1.095 m depth for 8 days.

| Quantity | Default result |
|---|---:|
| Mapped paddy area | 1,270.9 ha / 11,074 parcels |
| Assessed paddy area | 1,269.6 ha |
| Detected flooded paddy area | 519.3 ha |
| No-flood season production | 6,348 tonnes |
| After-damage season production | 6,037 tonnes |
| Scenario loss | 311 tonnes / 4.9% |

Both harvest totals use the same radar-assessed parcels. The 138 parcels with no samples (about 1.3 ha) are excluded from both sides, not treated as dry. Season totals include the assumed already-harvested share; field-map values represent standing rice only.

```text
Flooded parcel ha = parcel area × share of valid radar pixel centres classified as flooded
Loss t = flooded parcel ha × normal yield t/ha × unharvested share × damage fraction
No-flood season t = assessed parcel ha × normal yield t/ha
After-damage season t = no-flood season t − loss t
Change % = −100 × loss t / no-flood season t
```

Flooded area does not mean the crop was destroyed. The GSI reference layer is retained in the repository for analysis and does not feed the crop-loss calculation. Depth, duration, crop presence, growth stage, yield and harvest status remain assumptions for that calculation. No crop quality or price loss is calculated. Physical mass uses an illustrative paddy/rough-rice basis.

## Evidence and limitations

- Flood classification and crop-loss accuracy have not been independently validated for Japan. The dashboard is a historical reconstruction, not a flood forecast or operational advice.
- Parcel boundaries are 2021 approximations, not surveyed boundaries or a 2019 rice census. Alignment, mixed pixels and small parcels affect the results.
- RF footprint totals cover all land; they differ from area-weighted flooded-paddy hectares. Permanent water and false positives may remain. Classifier scores are not calibrated flood probabilities.
- GSI depth uses 13 October aerial photographs and event information through 14 October 18:00 JST. It differs in timing from Sentinel-1 at 05:42 JST on 13 October. It is not a guaranteed maximum-depth map and supplies no duration. Grey means unknown.
- Damage fractions are published lookup predictions from Thailand/Myanmar research, not a Japanese damage model trained here. Scenarios are not confidence intervals.

`npm test` checks data integrity, unknown handling and harvest conservation; it does **not** test predictive accuracy. Numerical and browser verification records are in `qa/`.

## Sources and attribution

- [MAFF-derived 2021 Koriyama parcels / AIGID](https://www.geospatial.jp/ckan/dataset/agri-poly-2021-07/resource/c007ede4-046c-4f00-b2c2-1bf96e359996). Paddy kind 田; selected by centroid within 140.32–140.44 E, 37.35–37.44 N. Source JGD2011 coordinates; areas in UTM54N, display in WGS84/Web Mercator.
- [Microsoft Planetary Computer Sentinel-1 RTC](https://planetarycomputer.microsoft.com/dataset/sentinel-1-rtc): modified Copernicus Sentinel-1 data, pre 7 October and post 13 October 2019, descending relative orbit 46. Frozen project RF with eleven pre/post VV/VH features and threshold 0.05.
- [GSI 2019 event-depth maps](https://www.gsi.go.jp/BOUSAI/R1.taihuu19gou.html) and [tile catalogue](https://maps.gsi.go.jp/development/ichiran.html). Original depth colours and legend are retained; grey unknown background is a project modification. `site/data/gsi_depth.json` records provenance and tile statuses/hashes.
- [Bill-Weilandt et al. (2026), Table S11](https://doi.org/10.5194/nhess-26-925-2026), CC BY 4.0: maturity-stage mean RF lookup fractions 0.3661, 0.4787, 0.8092. The dashboard does not execute or retrain the original damage RF.
- Leaflet 1.9.4: bundled license in `site/vendor/LEAFLET-LICENSE.txt`. Other source links and hashes are in `site/data/`.

## Data regeneration

The dashboard runs with its bundled derived data. Exact original preparation scripts are retained in `scripts/dashboard_preparation/`; they require the original workspace layout and raw inputs, so they are provenance scripts rather than a standalone end-to-end reproduction package. Inputs include the CP932 MAFF/AIGID shapefile, aligned pre/post radar TIFFs, frozen RF mask TIFF and damage lookup CSV. Original Python environment: numpy 2.2.5, rasterio 1.4.3, pyshp 2.3.1, Pillow 11.2.1, requests 2.32.3. The repository's older `requirements.txt` belongs to the separate Shibata extraction prototype.

## Reference-map agreement

Across mapped paddies, the radar footprint and GSI mapped inundation overlap by about 28% IoU. Radar flagged 93% of the GSI-mapped flooded paddy area, but only 29% of radar-flagged paddy area overlaps GSI. These describe map agreement; areas GSI did not map are not confirmed dry. The published-label comparison uses a different, restricted set of pixels, so its 91.5% IoU cannot validate the full dashboard footprint.

Regenerate the comparison with `scripts/prepare-test-comparison.py` using the original weak-label TIFF, geographic-partition TIFF and frozen dashboard prediction TIFF. Scores are calculated on the native grid before projection for display.
