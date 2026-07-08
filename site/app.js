const assetVersion = "20260708-yield-polygons";
const withAssetVersion = (url) => `${url}${url.includes("?") ? "&" : "?"}v=${assetVersion}`;
const metadataUrl = withAssetVersion("/outputs/shibata_city_152064/yield_metadata.json");
const geojsonUrl = withAssetVersion("/outputs/shibata_city_152064/field_yield_estimates.geojson");
const viewConfigUrl = withAssetVersion("/outputs/shibata_city_152064/views/views.json");

const dom = {
  sceneId: document.getElementById("scene-id"),
  sceneDate: document.getElementById("scene-date"),
  sceneCloud: document.getElementById("scene-cloud"),
  fieldCount: document.getElementById("field-count"),
  medianYield: document.getElementById("median-yield"),
  highYield: document.getElementById("high-yield"),
  fieldInfoToggle: document.getElementById("field-info-toggle"),
  empty: document.getElementById("selection-empty"),
  details: document.getElementById("selection-details"),
  detailYield: document.getElementById("detail-yield"),
  detailB02: document.getElementById("detail-b02"),
  detailB03: document.getElementById("detail-b03"),
  detailB04: document.getElementById("detail-b04"),
  detailB08: document.getElementById("detail-b08"),
  detailNdvi: document.getElementById("detail-ndvi"),
  detailSavi: document.getElementById("detail-savi"),
  detailLai: document.getElementById("detail-lai"),
  detailPixels: document.getElementById("detail-pixels"),
  detailId: document.getElementById("detail-id"),
  detailArea: document.getElementById("detail-area"),
  detailCentroid: document.getElementById("detail-centroid"),
  detailMuni: document.getElementById("detail-muni"),
  bandPreview: document.getElementById("band-preview"),
  mapViewSwitcher: document.getElementById("map-view-switcher"),
  sidebar: document.querySelector(".sidebar"),
  methodologyPanel: document.querySelector(".methodology-panel"),
};

const defaultStyle = {
  color: "rgba(14, 38, 29, 0.18)",
  weight: 0.35,
  fillColor: "#d9e3df",
  fillOpacity: 0.42,
};

const activeStyle = {
  color: "#0f355a",
  weight: 1.4,
  fillColor: "#4d7fbe",
  fillOpacity: 0.28,
};

const yieldPalette = ["#fff4b8", "#cbe986", "#78c679", "#31a354", "#006837"];

const map = L.map("map", {
  preferCanvas: true,
  zoomControl: true,
  maxBoundsViscosity: 1.0,
});

L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
  subdomains: "abcd",
  maxZoom: 20,
  attribution: "&copy; OpenStreetMap &copy; CARTO",
}).addTo(map);

let selectedLayer = null;
let yieldBreaks = [];
let legendControl = null;
let currentViewId = null;
let currentImageOverlay = null;
let currentViewConfig = null;
let fieldLayer = null;
let fieldGeojson = null;
let rasterRequestId = 0;
let selectedFieldId = null;
let fieldInfoEnabled = false;
const preloadedImages = new Set();
const overlayLayerCache = new Map();

map.createPane("rasterPane");
map.getPane("rasterPane").style.zIndex = 260;
map.createPane("fieldPane");
map.getPane("fieldPane").style.zIndex = 420;
const fieldRenderer = L.canvas({ pane: "fieldPane" });

function formatArea(areaM2) {
  if (!Number.isFinite(areaM2)) return "Unknown";
  if (areaM2 >= 10000) return `${(areaM2 / 10000).toFixed(2)} ha`;
  return `${Math.round(areaM2).toLocaleString()} m²`;
}

function formatDate(isoDate) {
  const parsed = new Date(isoDate);
  if (Number.isNaN(parsed.getTime())) return isoDate || "Unknown";
  return parsed.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatYield(value) {
  return Number.isFinite(value) ? `${value.toFixed(2)} t/ha` : "Unavailable";
}

function formatMetric(value) {
  return Number.isFinite(value) ? value.toFixed(3) : "Unavailable";
}

function formatLegendValue(value, units) {
  if (!Number.isFinite(value)) return "n/a";
  const formatted = units === "t/ha" ? value.toFixed(2) : value.toFixed(3);
  return units && !["index", "reflectance"].includes(units) ? `${formatted} ${units}` : formatted;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function formatPixelCount(value) {
  return Number.isFinite(value) ? value.toLocaleString() : "Unavailable";
}

function buildFieldSummaryHtml(properties) {
  const rows = [
    ["Expected yield", formatYield(properties.prototype_yield_t_ha)],
    ["NDVI", formatMetric(properties.NDVI)],
    ["LAI", formatMetric(properties.LAI)],
    ["NIR / B08", formatMetric(properties.B08_mean)],
    ["Red / B04", formatMetric(properties.B04_mean)],
    ["Green / B03", formatMetric(properties.B03_mean)],
    ["Blue / B02", formatMetric(properties.B02_mean)],
    ["SAVI", formatMetric(properties.SAVI)],
    ["Valid pixels", formatPixelCount(properties.pixel_count)],
    ["Area", formatArea(properties.area_m2)],
  ];

  return `
    <div class="field-popup">
      <div class="field-popup-title">Field ${escapeHtml(properties.field_id)}</div>
      <div class="field-popup-grid">
        ${rows
          .map(
            ([label, value]) =>
              `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`
          )
          .join("")}
      </div>
    </div>
  `;
}

function buildFieldTooltipHtml(properties) {
  return `
    <strong>${formatYield(properties.prototype_yield_t_ha)}</strong>
    <br>NDVI ${formatMetric(properties.NDVI)}
    <br>NIR ${formatMetric(properties.B08_mean)}
    <br>${formatArea(properties.area_m2)}
  `;
}

function populateScene(meta, geojson) {
  const yieldValues = geojson.features
    .map((feature) => feature.properties.prototype_yield_t_ha)
    .filter((value) => Number.isFinite(value));

  dom.sceneId.textContent = meta.sentinel_scene_id || "Unavailable";
  dom.sceneDate.textContent = formatDate(meta.sentinel_datetime);
  dom.sceneCloud.textContent = Number.isFinite(meta.sentinel_cloud_cover_pct)
    ? `${meta.sentinel_cloud_cover_pct.toFixed(2)}%`
    : "Unknown";
  dom.fieldCount.textContent = geojson.features.length.toLocaleString();
  dom.medianYield.textContent = formatYield(median(yieldValues));
  dom.highYield.textContent = formatYield(percentile(yieldValues, 0.75));
}

function clampMapToBbox(bbox) {
  if (!Array.isArray(bbox) || bbox.length !== 4) {
    return;
  }
  const southWest = L.latLng(bbox[1], bbox[0]);
  const northEast = L.latLng(bbox[3], bbox[2]);
  const bounds = L.latLngBounds(southWest, northEast);
  map.setMaxBounds(bounds);
  const containZoom = map.getBoundsZoom(bounds, false);
  const coverZoom = map.getBoundsZoom(bounds, true);
  const lockedZoom = Math.max(containZoom, coverZoom);
  map.setView(bounds.getCenter(), lockedZoom);
  map.setMinZoom(lockedZoom);
  map.setMaxZoom(18);
}

function setSelection(properties) {
  dom.empty.classList.add("hidden");
  dom.details.classList.remove("hidden");
  selectedFieldId = properties.field_id;
  dom.detailYield.textContent = formatYield(properties.prototype_yield_t_ha);
  dom.detailB02.textContent = formatMetric(properties.B02_mean);
  dom.detailB03.textContent = formatMetric(properties.B03_mean);
  dom.detailB04.textContent = formatMetric(properties.B04_mean);
  dom.detailB08.textContent = formatMetric(properties.B08_mean);
  dom.detailNdvi.textContent = formatMetric(properties.NDVI);
  dom.detailSavi.textContent = formatMetric(properties.SAVI);
  dom.detailLai.textContent = formatMetric(properties.LAI);
  dom.detailPixels.textContent = formatPixelCount(properties.pixel_count);
  dom.detailId.textContent = properties.field_id;
  dom.detailArea.textContent = formatArea(properties.area_m2);
  dom.detailCentroid.textContent = `${properties.point_lat.toFixed(5)}, ${properties.point_lng.toFixed(5)}`;
  dom.detailMuni.textContent = properties.local_government_cd;
}

function percentile(values, ratio) {
  if (!values.length) return NaN;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.max(0, Math.floor(ratio * (sorted.length - 1))));
  return sorted[index];
}

function median(values) {
  if (!values.length) return NaN;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[middle - 1] + sorted[middle]) / 2
    : sorted[middle];
}

function buildYieldBreaks(geojson) {
  const yieldValues = geojson.features
    .map((feature) => feature.properties.prototype_yield_t_ha)
    .filter((value) => Number.isFinite(value));
  return [
    percentile(yieldValues, 0.2),
    percentile(yieldValues, 0.4),
    percentile(yieldValues, 0.6),
    percentile(yieldValues, 0.8),
  ];
}

function yieldColor(value) {
  if (!Number.isFinite(value)) return "#d9e3df";
  if (!yieldBreaks.length) return yieldPalette[2];
  if (value <= yieldBreaks[0]) return yieldPalette[0];
  if (value <= yieldBreaks[1]) return yieldPalette[1];
  if (value <= yieldBreaks[2]) return yieldPalette[2];
  if (value <= yieldBreaks[3]) return yieldPalette[3];
  return yieldPalette[4];
}

function isYieldView() {
  return currentViewId === "yield";
}

function addValueLegend(view) {
  if (!view?.legend) {
    removeLegend();
    return;
  }
  if (legendControl) {
    legendControl.remove();
  }
  legendControl = L.control({ position: "bottomright" });
  legendControl.onAdd = () => {
    const div = L.DomUtil.create("div", "yield-legend");
    const { title, units, min, mid, max, gradient } = view.legend;
    div.innerHTML = `
      <strong>${escapeHtml(title)}</strong>
      <div class="value-ramp" style="background:${escapeHtml(gradient)}"></div>
      <div class="value-ramp-labels">
        <span>${escapeHtml(formatLegendValue(min, units))}</span>
        <span>${escapeHtml(formatLegendValue(mid, units))}</span>
        <span>${escapeHtml(formatLegendValue(max, units))}</span>
      </div>
    `;
    return div;
  };
  legendControl.addTo(map);
}

function removeLegend() {
  if (legendControl) {
    legendControl.remove();
    legendControl = null;
  }
}

function styleForFeature(feature) {
  if (isYieldView()) {
    const yieldValue = feature.properties?.prototype_yield_t_ha;
    return {
      color: "rgba(15, 53, 90, 0.62)",
      weight: 0.45,
      fillColor: yieldColor(yieldValue),
      fillOpacity: Number.isFinite(yieldValue) ? 0.76 : 0.22,
    };
  }

  return {
    color: "rgba(15, 53, 90, 0.72)",
    weight: 0.7,
    fillColor: "#ffffff",
    fillOpacity: 0.06,
  };
}

function restoreSelectedLayer() {
  if (!fieldLayer || !selectedFieldId) {
    return;
  }
  fieldLayer.eachLayer((layer) => {
    if (layer.feature?.properties?.field_id === selectedFieldId) {
      selectedLayer = layer;
      selectedLayer.setStyle(activeStyle);
    }
  });
}

function renderFieldLayer(geojson) {
  if (fieldLayer) {
    map.removeLayer(fieldLayer);
    fieldLayer = null;
  }

  fieldLayer = L.geoJSON(geojson, {
    renderer: fieldRenderer,
    pane: "fieldPane",
    style: (feature) => styleForFeature(feature),
    onEachFeature: (feature, featureLayer) => {
      featureLayer.on("click", () => {
        if (selectedLayer) {
          selectedLayer.setStyle(styleForFeature(selectedLayer.feature));
        }
        selectedLayer = featureLayer;
        selectedLayer.setStyle(activeStyle);
        setSelection(feature.properties);
      });

      featureLayer.bindPopup(buildFieldSummaryHtml(feature.properties), {
        className: "field-popup-shell",
        maxWidth: 360,
      });
      featureLayer.bindTooltip(buildFieldTooltipHtml(feature.properties), {
        sticky: true,
        direction: "top",
        opacity: 0.94,
      });
    },
  }).addTo(map);

  restoreSelectedLayer();
}

function updateFieldLayerVisibility() {
  const shouldShowFields = fieldInfoEnabled || isYieldView();
  map.getPane("fieldPane").style.display = shouldShowFields ? "" : "none";

  if (!shouldShowFields) {
    if (fieldLayer) {
      map.removeLayer(fieldLayer);
      fieldLayer = null;
      selectedLayer = null;
    }
    return;
  }

  if (fieldGeojson && !fieldLayer) {
    renderFieldLayer(fieldGeojson);
    return;
  }

  if (fieldLayer) {
    fieldLayer.setStyle((feature) => styleForFeature(feature));
  }
}

function renderViewButtons(config) {
  currentViewConfig = config;
  dom.mapViewSwitcher.innerHTML = "";
  config.views.forEach((view) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `view-button${view.id === config.default_view ? " is-active" : ""}`;
    button.textContent = view.label;
    button.addEventListener("click", () => setActiveView(config, view.id));
    dom.mapViewSwitcher.appendChild(button);
  });
  preloadViewImages(config);
  setActiveView(config, config.default_view);
}

function placeMethodologyPanel() {
  if (dom.sidebar && dom.methodologyPanel && dom.methodologyPanel.parentElement !== dom.sidebar) {
    dom.sidebar.appendChild(dom.methodologyPanel);
  }
}

function preloadViewImages(config) {
  config.views.forEach((view) => {
    if (view.id === "yield") return;
    const imageUrl = view.web_image || view.overlay_image || view.image;
    if (!imageUrl || preloadedImages.has(imageUrl)) return;
    const image = new Image();
    image.src = withAssetVersion(imageUrl);
    preloadedImages.add(imageUrl);
  });
}

function overlayBoundsFromConfig(config) {
  return L.latLngBounds(
    L.latLng(config.bounds[1], config.bounds[0]),
    L.latLng(config.bounds[3], config.bounds[2])
  );
}

function getOverlayLayer(config, view) {
  if (view.id === "yield") return null;
  const overlayImage = view.web_image || view.overlay_image;
  if (!overlayImage || !config?.bounds) return null;
  if (!overlayLayerCache.has(view.id)) {
    overlayLayerCache.set(
      view.id,
      L.imageOverlay(withAssetVersion(overlayImage), overlayBoundsFromConfig(config), {
        opacity: 0.92,
        pane: "rasterPane",
      })
    );
  }
  return overlayLayerCache.get(view.id);
}

async function setActiveView(config, viewId) {
  currentViewId = viewId;
  const selected = config.views.find((view) => view.id === viewId);
  if (!selected) return;
  dom.bandPreview.src = withAssetVersion(selected.image);
  dom.bandPreview.alt = `${selected.label} municipality view`;

  if (currentImageOverlay) {
    map.removeLayer(currentImageOverlay);
    currentImageOverlay = null;
  }

  rasterRequestId += 1;
  currentImageOverlay = getOverlayLayer(config, selected);
  if (currentImageOverlay) {
    currentImageOverlay.addTo(map);
  }

  addValueLegend(selected);
  updateFieldLayerVisibility();

  [...dom.mapViewSwitcher.querySelectorAll(".view-button")].forEach((button, index) => {
    const view = config.views[index];
    button.classList.toggle("is-active", view.id === viewId);
  });
}

function attachGeojson(geojson) {
  fieldGeojson = geojson;
  yieldBreaks = buildYieldBreaks(geojson);
  updateFieldLayerVisibility();
}

dom.fieldInfoToggle.addEventListener("change", () => {
  fieldInfoEnabled = dom.fieldInfoToggle.checked;
  updateFieldLayerVisibility();
});

async function init() {
  try {
    placeMethodologyPanel();
    const [meta, geojson] = await Promise.all([
      fetch(metadataUrl).then((response) => response.json()),
      fetch(geojsonUrl).then((response) => response.json()),
      fetch(viewConfigUrl).then((response) => response.json()),
    ]).then(([metaResult, geojsonResult, viewConfig]) => {
      viewConfig.bounds = viewConfig.overlay_bounds || metaResult.bbox;
      renderViewButtons(viewConfig);
      return [metaResult, geojsonResult, viewConfig];
    });

    populateScene(meta, geojson);
    clampMapToBbox(currentViewConfig?.bounds || meta.bbox);
    attachGeojson(geojson);
  } catch (error) {
    console.error(error);
    dom.sceneId.textContent = "Load failed";
    dom.sceneDate.textContent = "Load failed";
    dom.sceneCloud.textContent = "Load failed";
    dom.empty.textContent = "The website could not load the municipality data files.";
  }
}

init();
