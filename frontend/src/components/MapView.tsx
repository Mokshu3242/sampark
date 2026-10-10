import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { imageBounds, imageUrl, layerUrl, traceFlow } from "../api/client";

type Highlight = { name: string; lon: number; lat: number } | null;
type WhatIf = { ranks: number[]; villagesGeo: any } | null;

type Props = {
  runId: string | null;
  ready: boolean;
  bbox: number[] | null;
  visible: Record<string, boolean>;
  flowMode: boolean;
  onFlowResult: (r: any) => void;
  flowKm: number | null;
  onVillageSelect: (name: string) => void;
  highlight: Highlight;
  whatIf: WhatIf;
  compareEnabled: boolean;
  compareValue: number; // 0..100
  card: ReactNode; // pinned village card, shown in a popup on the map
};

const LAYER_IDS: Record<string, string[]> = {
  flood: ["flood-fill"],
  debris: ["debris-fill"],
  roads: ["roads-ok", "roads-affected", "roads-cut"],
  villages: ["villages-longer", "villages-cut"],
  repair: ["repair-line", "repair-label"],
  uncertainty: ["raster-uncertainty"],
};
const VECTOR_LAYERS = ["debris", "flood", "roads", "villages", "repair"]; // drawing order
const RASTERS = ["uncertainty", "dem", "s2_before", "s2_after"];
const COMPARE = ["s2_before", "s2_after"];
const RASTER_WIDTH: Record<string, number> = { uncertainty: 2400 }; // class maps: keep 20 m detail
const RASTER_SHARP = new Set(["uncertainty"]);                    // class maps: no blending
const FLOW_LAYERS = [
  "flowpath-ahead", "flow-done-casing", "flow-done", "flow-events-glow",
  "flow-events", "flow-front-halo", "flow-front",
];
const FLOW_SOURCES = ["flowpath", "flow-done", "flow-front", "flow-events"];
const SELECT_LAYERS = ["selected-halo", "selected-ring"];
const SELECT_SOURCE = "selected-village";
const WHATIF_LAYERS = ["whatif-casing", "whatif-line", "reconnect-halo", "reconnect-dot"];
const WHATIF_SOURCE = "whatif-villages";
const ATTRIBUTION =
  "Contains modified Copernicus Sentinel data 2026 · Copernicus WorldDEM-30 © DLR e.V., © Airbus DS · © OpenStreetMap contributors";

const EVENT_COLOR: any = [
  "match", ["get", "kind"],
  "bridge_lost", "#ff1744",
  "road_cut", "#e53935",
  "road_affected", "#fbc02d",
  "debris", "#ef6c00",
  "flood", "#1565c0",
  "village", "#d500f9",
  "#888",
];
const STATUS_LABEL: Record<string, string> = { cut_off: "Cut off", longer_route: "Longer route" };

const loadedMaps = new WeakSet<maplibregl.Map>();
const EMPTY: any = { type: "FeatureCollection", features: [] };

function esc(s: any): string {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));
}

function whenReady(map: maplibregl.Map, fn: () => void) {
  if (loadedMaps.has(map)) fn();
  else map.once("load", () => { loadedMaps.add(map); fn(); });
}

function clearFlow(map: maplibregl.Map) {
  for (const id of FLOW_LAYERS) if (map.getLayer(id)) map.removeLayer(id);
  for (const s of FLOW_SOURCES) if (map.getSource(s)) map.removeSource(s);
}

function clearSelection(map: maplibregl.Map) {
  for (const id of SELECT_LAYERS) if (map.getLayer(id)) map.removeLayer(id);
  if (map.getSource(SELECT_SOURCE)) map.removeSource(SELECT_SOURCE);
}

function clearWhatIf(map: maplibregl.Map) {
  for (const id of WHATIF_LAYERS) if (map.getLayer(id)) map.removeLayer(id);
  if (map.getSource(WHATIF_SOURCE)) map.removeSource(WHATIF_SOURCE);
}

function removeRaster(map: maplibregl.Map, name: string) {
  if (map.getLayer(`raster-${name}`)) map.removeLayer(`raster-${name}`);
  if (map.getSource(`raster-${name}`)) map.removeSource(`raster-${name}`);
}

function clearRunLayers(map: maplibregl.Map) {
  clearFlow(map);
  clearWhatIf(map);
  clearSelection(map);
  for (const n of RASTERS) removeRaster(map, n);
  for (const ids of Object.values(LAYER_IDS))
    for (const id of ids) if (map.getLayer(id)) map.removeLayer(id);
  for (const name of VECTOR_LAYERS)
    if (map.getSource(`src-${name}`)) map.removeSource(`src-${name}`);
}

/** First basemap label layer. Imagery goes under it, so town names stay readable. */
function firstBasemapLabel(map: maplibregl.Map): string | undefined {
  const ours = /^(repair|flow|selected|reconnect|whatif|villages|roads|flood|debris|raster|bbox)/;
  return map.getStyle().layers?.find((l) => l.type === "symbol" && !ours.test(l.id))?.id;
}

/** Fade: before = 1 - v, after = v. Hidden when compare is off. */
function applyCompare(map: maplibregl.Map, on: boolean, value: number) {
  const v = Math.max(0, Math.min(100, value)) / 100;
  const ops: [string, number][] = [["s2_before", 1 - v], ["s2_after", v]];
  for (const [n, op] of ops) {
    const id = `raster-${n}`;
    if (!map.getLayer(id)) continue;
    map.setLayoutProperty(id, "visibility", on && op > 0.001 ? "visible" : "none");
    map.setPaintProperty(id, "raster-opacity", op);
  }
}

/** Highlight the ticked repair packages and the villages they bring back. */
function applyWhatIf(map: maplibregl.Map, w: WhatIf) {
  clearWhatIf(map);
  if (map.getLayer("repair-line")) map.setPaintProperty("repair-line", "line-opacity", w ? 0.25 : 0.85);
  if (!w || !map.getSource("src-repair")) return;
  const before = map.getLayer("selected-halo") ? "selected-halo" : undefined;
  const byRank: any = ["in", ["get", "rank"], ["literal", w.ranks]];

  map.addLayer({ id: "whatif-casing", type: "line", source: "src-repair", filter: byRank,
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#004d40", "line-width": 15, "line-opacity": 0.8 } }, before);
  map.addLayer({ id: "whatif-line", type: "line", source: "src-repair", filter: byRank,
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#00e676", "line-width": 9 } }, before);

  map.addSource(WHATIF_SOURCE, { type: "geojson", data: w.villagesGeo });
  map.addLayer({ id: "reconnect-halo", type: "circle", source: WHATIF_SOURCE,
    paint: { "circle-radius": 14, "circle-color": "#00e676", "circle-opacity": 0.35, "circle-blur": 0.6 } }, before);
  map.addLayer({ id: "reconnect-dot", type: "circle", source: WHATIF_SOURCE,
    paint: { "circle-radius": 7, "circle-color": "#00e676",
             "circle-stroke-color": "#004d40", "circle-stroke-width": 2 } }, before);
}

function addLayers(map: maplibregl.Map, name: string, src: string) {
  switch (name) {
    case "flood":
      map.addLayer({ id: "flood-fill", type: "fill", source: src,
        paint: { "fill-color": "#1565c0", "fill-opacity": 0.55 } });
      break;
    case "debris":
      map.addLayer({ id: "debris-fill", type: "fill", source: src,
        paint: { "fill-color": "#ef6c00", "fill-opacity": 0.6 } });
      break;
    case "roads":
      map.addLayer({ id: "roads-ok", type: "line", source: src,
        filter: ["==", ["get", "status"], "ok"],
        paint: { "line-color": "#555", "line-width": 1, "line-opacity": 0.6 } });
      map.addLayer({ id: "roads-affected", type: "line", source: src,
        filter: ["==", ["get", "status"], "affected"],
        paint: { "line-color": "#fbc02d", "line-width": 2 } });
      map.addLayer({ id: "roads-cut", type: "line", source: src,
        filter: ["in", ["get", "status"], ["literal", ["likely_cut", "bridge_lost"]]],
        paint: { "line-color": "#e53935", "line-width": 3.5 } });
      break;
    case "villages":
      map.addLayer({ id: "villages-longer", type: "circle", source: src,
        filter: ["==", ["get", "status"], "longer_route"],
        paint: { "circle-radius": 6, "circle-color": "#ffa726",
                 "circle-stroke-color": "#000", "circle-stroke-width": 0.8 } });
      map.addLayer({ id: "villages-cut", type: "circle", source: src,
        filter: ["==", ["get", "status"], "cut_off"],
        paint: { "circle-radius": 7, "circle-color": "#d500f9",
                 "circle-stroke-color": "#000", "circle-stroke-width": 0.8 } });
      break;
    case "repair":
      map.addLayer({ id: "repair-line", type: "line", source: src,
        paint: { "line-color": "#00e676", "line-width": 8, "line-opacity": 0.85 } });
      map.addLayer({ id: "repair-label", type: "symbol", source: src,
        layout: {
          "symbol-placement": "line-center",
          "text-field": ["concat", "#", ["to-string", ["get", "rank"]]],
          "text-font": ["Noto Sans Bold"], "text-size": 14, "text-allow-overlap": true,
        },
        paint: { "text-color": "#000", "text-halo-color": "#00e676", "text-halo-width": 2 } });
      break;
  }
}

/** Add a run raster (PNG from the image endpoint) as an image layer. */
async function addRasterLayer(
  map: maplibregl.Map, runId: string, name: string, opacity = 0.7, beforeId?: string,
): Promise<boolean> {
  const id = `raster-${name}`;
  if (map.getSource(id)) return true;
  const width = RASTER_WIDTH[name] ?? 1400;
  try {
    const coords = await imageBounds(runId, name, width);
    if (map.getSource(id)) return true; // added while we waited
    map.addSource(id, { type: "image", url: imageUrl(runId, name, width), coordinates: coords as any });
    map.addLayer(
      { id, type: "raster", source: id,
        paint: {
          "raster-opacity": opacity,
          "raster-fade-duration": 200,
          "raster-resampling": RASTER_SHARP.has(name) ? "nearest" : "linear",
        } },
      beforeId && map.getLayer(beforeId) ? beforeId : undefined,
    );
    return true;
  } catch {
    return false;
  }
}

/** Part of the line from the start to `km`, plus the front point. */
function sliceLine(coords: number[][], cum: number[], km: number) {
  const out: number[][] = [coords[0]];
  for (let i = 1; i < coords.length; i++) {
    if (cum[i] <= km) { out.push(coords[i]); continue; }
    const seg = cum[i] - cum[i - 1];
    const f = seg > 0 ? (km - cum[i - 1]) / seg : 0;
    out.push([
      coords[i - 1][0] + f * (coords[i][0] - coords[i - 1][0]),
      coords[i - 1][1] + f * (coords[i][1] - coords[i - 1][1]),
    ]);
    break;
  }
  if (out.length === 1) out.push(coords[0]);
  return out;
}

function drawFlow(map: maplibregl.Map, r: any) {
  clearFlow(map);
  const events = (r.corridor?.events || []).map((e: any) => ({
    type: "Feature", properties: e, geometry: { type: "Point", coordinates: [e.lon, e.lat] },
  }));

  map.addSource("flowpath", { type: "geojson", data: r.path });
  map.addSource("flow-done", { type: "geojson", data: EMPTY });
  map.addSource("flow-front", { type: "geojson", data: EMPTY });
  map.addSource("flow-events", { type: "geojson",
    data: { type: "FeatureCollection", features: events } as any });

  const before = map.getLayer("selected-halo") ? "selected-halo" : undefined;

  map.addLayer({ id: "flowpath-ahead", type: "line", source: "flowpath",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#00e5ff", "line-width": 2.5, "line-opacity": 0.35, "line-dasharray": [2, 2] } }, before);
  map.addLayer({ id: "flow-done-casing", type: "line", source: "flow-done",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#003a4d", "line-width": 8, "line-opacity": 0.5 } }, before);
  map.addLayer({ id: "flow-done", type: "line", source: "flow-done",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#00e5ff", "line-width": 4.5 } }, before);
  map.addLayer({ id: "flow-events-glow", type: "circle", source: "flow-events",
    filter: ["<", ["get", "km"], -1],
    paint: { "circle-radius": 16, "circle-color": EVENT_COLOR, "circle-opacity": 0.35, "circle-blur": 0.6 } }, before);
  map.addLayer({ id: "flow-events", type: "circle", source: "flow-events",
    filter: ["<", ["get", "km"], -1],
    paint: {
      "circle-radius": ["match", ["get", "kind"], "village", 8, "bridge_lost", 7, "debris", 6, 5],
      "circle-color": EVENT_COLOR, "circle-stroke-color": "#fff", "circle-stroke-width": 1.2,
    } }, before);
  map.addLayer({ id: "flow-front-halo", type: "circle", source: "flow-front",
    paint: { "circle-radius": 18, "circle-color": "#00e5ff", "circle-opacity": 0.25, "circle-blur": 0.5 } }, before);
  map.addLayer({ id: "flow-front", type: "circle", source: "flow-front",
    paint: { "circle-radius": 7, "circle-color": "#ffffff",
             "circle-stroke-color": "#00e5ff", "circle-stroke-width": 3 } }, before);
}

function updateFlow(map: maplibregl.Map, r: any, km: number) {
  const coords: number[][] = r?.path?.geometry?.coordinates;
  const cum: number[] = r?.corridor?.cum_km;
  if (!coords || !cum || !map.getSource("flow-done")) return;
  const part = sliceLine(coords, cum, km);
  (map.getSource("flow-done") as maplibregl.GeoJSONSource).setData({
    type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: part },
  } as any);
  (map.getSource("flow-front") as maplibregl.GeoJSONSource).setData({
    type: "Feature", properties: {}, geometry: { type: "Point", coordinates: part[part.length - 1] },
  } as any);
  map.setFilter("flow-events", ["<=", ["get", "km"], km]);
  map.setFilter("flow-events-glow", ["all", [">", ["get", "km"], km - 1.5], ["<=", ["get", "km"], km]]);
  map.setPaintProperty("flow-front-halo", "circle-radius", 16 + 5 * Math.sin(performance.now() / 180));
}

export default function MapView({
  runId, ready, bbox, visible, flowMode, onFlowResult, flowKm, onVillageSelect,
  highlight, whatIf, compareEnabled, compareValue, card,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const bboxRef = useRef<number[] | null>(null);
  const userMoved = useRef(false);
  const lastFlow = useRef<any>(null);
  const cardPopup = useRef<maplibregl.Popup | null>(null);
  const [cardEl, setCardEl] = useState<HTMLDivElement | null>(null);

  const visibleRef = useRef(visible);
  visibleRef.current = visible;
  const flowRef = useRef({ flowMode, runId, ready, onFlowResult });
  flowRef.current = { flowMode, runId, ready, onFlowResult };
  const selectRef = useRef(onVillageSelect);
  selectRef.current = onVillageSelect;
  const highlightRef = useRef<Highlight>(highlight);
  highlightRef.current = highlight;
  const whatIfRef = useRef<WhatIf>(whatIf);
  whatIfRef.current = whatIf;
  const compareRef = useRef({ on: compareEnabled, value: compareValue });
  compareRef.current = { on: compareEnabled, value: compareValue };

  function fitToBox(animate: boolean) {
    const map = mapRef.current, b = bboxRef.current;
    if (!map || !b) return;
    map.resize();
    map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 40, duration: animate ? 600 : 0 });
  }

  /** Draw (or clear) the yellow ring for the selected village. */
  function drawSelection(map: maplibregl.Map, h: Highlight) {
    const data: any = h
      ? { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [h.lon, h.lat] } }
      : EMPTY;
    const src = map.getSource(SELECT_SOURCE) as maplibregl.GeoJSONSource | undefined;
    if (src) { src.setData(data); return; }
    map.addSource(SELECT_SOURCE, { type: "geojson", data });
    map.addLayer({ id: "selected-halo", type: "circle", source: SELECT_SOURCE,
      paint: { "circle-radius": 18, "circle-color": "#ffeb3b", "circle-opacity": 0.4, "circle-blur": 0.6 } });
    map.addLayer({ id: "selected-ring", type: "circle", source: SELECT_SOURCE,
      paint: { "circle-radius": 11, "circle-color": "rgba(0,0,0,0)",
               "circle-stroke-color": "#fdd835", "circle-stroke-width": 3 } });
  }

  // Init once
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: "https://tiles.openfreemap.org/styles/liberty",
      center: [85.3, 28.1],
      zoom: 9,
      attributionControl: false,
    });
    mapRef.current = map;

    map.addControl(new maplibregl.NavigationControl(), "top-right");
    map.addControl(new maplibregl.ScaleControl(), "bottom-left");
    map.addControl(new maplibregl.AttributionControl({ customAttribution: ATTRIBUTION, compact: true }));

    const markMoved = (e: any) => { if (e.originalEvent) userMoved.current = true; };
    map.on("dragstart", markMoved);
    map.on("zoomstart", markMoved);
    map.on("rotatestart", markMoved);

    let t: ReturnType<typeof setTimeout> | undefined;
    const ro = new ResizeObserver(() => {
      map.resize();
      clearTimeout(t);
      t = setTimeout(() => { if (!userMoved.current) fitToBox(false); }, 150);
    });
    ro.observe(containerRef.current);

    // Village dots: hover = quick tooltip, click = pinned card
    const hover = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12, className: "vhover" });
    const kmTxt = (v: any) =>
      v === null || v === undefined || v === "null" || !Number.isFinite(Number(v)) ? "no route" : `${Number(v).toFixed(1)} km`;

    for (const id of ["villages-cut", "villages-longer"]) {
      map.on("mousemove", id, (e) => {
        if (flowRef.current.flowMode) return;
        const f: any = e.features?.[0];
        const p = f?.properties || {};
        if (!p.name || highlightRef.current?.name === p.name) { hover.remove(); return; }
        map.getCanvas().style.cursor = "pointer";
        hover
          .setLngLat(f.geometry.coordinates)
          .setHTML(
            `<b>${esc(p.name)}</b><br>` +
            `<span class="h-${esc(p.status)}">${STATUS_LABEL[p.status] ?? esc(p.status)}</span>` +
            ` · ${Number(p.buildings_total || 0).toLocaleString()} buildings<br>` +
            `To help: ${kmTxt(p.dist_before_km)} → ${kmTxt(p.dist_after_km)}<br>` +
            `<i>Click for details</i>`,
          )
          .addTo(map);
      });
      map.on("mouseleave", id, () => {
        hover.remove();
        map.getCanvas().style.cursor = flowRef.current.flowMode ? "crosshair" : "";
      });
      map.on("click", id, (e) => {
        if (flowRef.current.flowMode) return;
        hover.remove();
        const name = (e.features?.[0]?.properties as any)?.name;
        if (name) selectRef.current(name);
      });
    }

    // Flow tracing click
    let busy = false;
    map.on("click", async (e) => {
      const { flowMode, runId, ready, onFlowResult } = flowRef.current;
      if (!flowMode || !runId || !ready || busy) return;
      busy = true;
      onFlowResult({ busy: true });
      map.getCanvas().style.cursor = "progress";
      try {
        const r = await traceFlow(runId, e.lngLat.lng, e.lngLat.lat);
        if (flowRef.current.runId !== runId) return;
        lastFlow.current = r;
        drawFlow(map, r);
        updateFlow(map, r, 0);
        onFlowResult(r);
      } catch (err: any) {
        onFlowResult({ error: String(err.message || err) });
      } finally {
        busy = false;
        map.getCanvas().style.cursor = flowRef.current.flowMode ? "crosshair" : "";
      }
    });

    map.once("load", () => { loadedMaps.add(map); map.resize(); });

    return () => {
      clearTimeout(t);
      ro.disconnect();
      hover.remove();
      cardPopup.current?.remove();
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Wave front progress
  useEffect(() => {
    const map = mapRef.current;
    if (!map || flowKm === null || !lastFlow.current) return;
    updateFlow(map, lastFlow.current, flowKm);
  }, [flowKm]);

  // Cursor + clear when flow mode is turned off
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    map.getCanvas().style.cursor = flowMode ? "crosshair" : "";
    if (!flowMode) { lastFlow.current = null; whenReady(map, () => clearFlow(map)); }
  }, [flowMode]);

  // Repair what-if overlay
  const whatIfKey = whatIf ? `${whatIf.ranks.join(",")}|${whatIf.villagesGeo?.features?.length ?? 0}` : "";
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    whenReady(map, () => applyWhatIf(map, whatIfRef.current));
  }, [whatIfKey]);

  // Before/after: load the two images the first time compare is turned on
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !runId) return;
    let cancelled = false;
    whenReady(map, async () => {
      if (compareEnabled) {
        const below = firstBasemapLabel(map);
        for (const n of COMPARE) {
          await addRasterLayer(map, runId, n, 0, below);
          if (cancelled) { removeRaster(map, n); return; }   // run changed while loading
        }
      }
      applyCompare(map, compareRef.current.on, compareRef.current.value);
    });
    return () => { cancelled = true; };
  }, [compareEnabled, ready, runId]);

  // Before/after: slider moves
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    whenReady(map, () => applyCompare(map, compareEnabled, compareValue));
  }, [compareValue, compareEnabled]);

  // Selected village: ring + pinned card popup next to the dot
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    whenReady(map, () => {
      drawSelection(map, highlight);
      cardPopup.current?.remove();
      cardPopup.current = null;
      setCardEl(null);
      if (!highlight) return;

      const el = document.createElement("div");
      cardPopup.current = new maplibregl.Popup({
        closeButton: false, closeOnClick: false, maxWidth: "340px", offset: 16, className: "vpopup",
      })
        .setLngLat([highlight.lon, highlight.lat])
        .setDOMContent(el)
        .addTo(map);
      setCardEl(el);

      userMoved.current = true;
      map.flyTo({
        center: [highlight.lon, highlight.lat],
        zoom: Math.max(map.getZoom(), 12.5),
        offset: [0, 180],
        duration: 900,
      });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlight?.name, highlight?.lon, highlight?.lat]);

  // Bbox outline + zoom
  const bboxKey = bbox ? bbox.join(",") : "";
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !bbox) return;
    bboxRef.current = bbox;
    userMoved.current = false;
    whenReady(map, () => {
      const data: any = {
        type: "Feature", properties: {},
        geometry: { type: "Polygon", coordinates: [[
          [bbox[0], bbox[1]], [bbox[2], bbox[1]], [bbox[2], bbox[3]], [bbox[0], bbox[3]], [bbox[0], bbox[1]],
        ]] },
      };
      const src = map.getSource("bbox") as maplibregl.GeoJSONSource | undefined;
      if (src) src.setData(data);
      else {
        map.addSource("bbox", { type: "geojson", data });
        map.addLayer({ id: "bbox", type: "line", source: "bbox",
          paint: { "line-color": "#00bcd4", "line-width": 2, "line-dasharray": [3, 3] } });
      }
      fitToBox(true);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bboxKey]);

  // Run layers: vectors first, then rasters (so their stacking targets exist)
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    let cancelled = false;
    lastFlow.current = null;
    whenReady(map, async () => {
      clearRunLayers(map);
      if (!runId || !ready) return;

      for (const name of VECTOR_LAYERS) {
        try {
          const r = await fetch(layerUrl(runId, name));
          if (!r.ok || cancelled) continue;
          const data = await r.json();
          if (cancelled || map.getSource(`src-${name}`)) continue;
          map.addSource(`src-${name}`, { type: "geojson", data });
          addLayers(map, name, `src-${name}`);
          const before = map.getLayer("flowpath-ahead") ? "flowpath-ahead" : undefined;
          if (before) for (const id of LAYER_IDS[name]) map.moveLayer(id, before);
          for (const id of LAYER_IDS[name])
            map.setLayoutProperty(id, "visibility", visibleRef.current[name] === false ? "none" : "visible");
        } catch {
          /* layer missing for this run */
        }
      }

      // Uncertainty: above flood/debris, below roads/villages/repair
      if (cancelled) return;
      const ok = await addRasterLayer(map, runId, "uncertainty", 0.9, "roads-ok");
      if (ok && !cancelled && map.getLayer("raster-uncertainty"))
        map.setLayoutProperty("raster-uncertainty", "visibility",
                              visibleRef.current.uncertainty ? "visible" : "none");

      // What-if overlay, then the selection ring on top of everything
      if (!cancelled) {
        applyWhatIf(map, whatIfRef.current);
        drawSelection(map, highlightRef.current);
      }
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, ready]);

  // Toggles
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    for (const [name, ids] of Object.entries(LAYER_IDS))
      for (const id of ids)
        if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", visible[name] ? "visible" : "none");
  }, [visible]);

  return (
    <>
      <div ref={containerRef} className="map" />
      {cardEl && card ? createPortal(card, cardEl) : null}
    </>
  );
}