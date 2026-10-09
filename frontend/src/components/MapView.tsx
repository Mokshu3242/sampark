import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { layerUrl, traceFlow } from "../api/client";

type Props = {
  runId: string | null;
  ready: boolean;
  bbox: number[] | null;
  visible: Record<string, boolean>;
  flowMode: boolean;
  onFlowResult: (r: any) => void;
  flowKm: number | null;
};

const LAYER_IDS: Record<string, string[]> = {
  flood: ["flood-fill"],
  debris: ["debris-fill"],
  roads: ["roads-ok", "roads-affected", "roads-cut"],
  villages: ["villages-longer", "villages-cut"],
  repair: ["repair-line", "repair-label"],
};
const FLOW_LAYERS = ["flowpath-ahead", "flow-done-casing", "flow-done", "flow-events-glow",
                     "flow-events", "flow-front-halo", "flow-front"];
const FLOW_SOURCES = ["flowpath", "flow-done", "flow-front", "flow-events"];
const ATTRIBUTION =
  "Contains modified Copernicus Sentinel data 2026 · Copernicus WorldDEM-30 © DLR e.V., © Airbus DS · © OpenStreetMap contributors";

const EVENT_COLOR: any = ["match", ["get", "kind"],
  "bridge_lost", "#ff1744", "road_cut", "#e53935", "road_affected", "#fbc02d",
  "debris", "#ef6c00", "flood", "#1565c0", "village", "#d500f9", "#888"];

const loadedMaps = new WeakSet<maplibregl.Map>();
const EMPTY: any = { type: "FeatureCollection", features: [] };

function whenReady(map: maplibregl.Map, fn: () => void) {
  if (loadedMaps.has(map)) fn();
  else map.once("load", () => { loadedMaps.add(map); fn(); });
}

function clearFlow(map: maplibregl.Map) {
  for (const id of FLOW_LAYERS) if (map.getLayer(id)) map.removeLayer(id);
  for (const s of FLOW_SOURCES) if (map.getSource(s)) map.removeSource(s);
}

function clearRunLayers(map: maplibregl.Map) {
  clearFlow(map);
  for (const ids of Object.values(LAYER_IDS)) for (const id of ids) if (map.getLayer(id)) map.removeLayer(id);
  for (const name of Object.keys(LAYER_IDS)) if (map.getSource(`src-${name}`)) map.removeSource(`src-${name}`);
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
      map.addLayer({ id: "roads-ok", type: "line", source: src, filter: ["==", ["get", "status"], "ok"],
        paint: { "line-color": "#555", "line-width": 1, "line-opacity": 0.6 } });
      map.addLayer({ id: "roads-affected", type: "line", source: src, filter: ["==", ["get", "status"], "affected"],
        paint: { "line-color": "#fbc02d", "line-width": 2 } });
      map.addLayer({ id: "roads-cut", type: "line", source: src,
        filter: ["in", ["get", "status"], ["literal", ["likely_cut", "bridge_lost"]]],
        paint: { "line-color": "#e53935", "line-width": 3.5 } });
      break;
    case "villages":
      map.addLayer({ id: "villages-longer", type: "circle", source: src, filter: ["==", ["get", "status"], "longer_route"],
        paint: { "circle-radius": 6, "circle-color": "#ffa726", "circle-stroke-color": "#000", "circle-stroke-width": 0.8 } });
      map.addLayer({ id: "villages-cut", type: "circle", source: src, filter: ["==", ["get", "status"], "cut_off"],
        paint: { "circle-radius": 7, "circle-color": "#d500f9", "circle-stroke-color": "#000", "circle-stroke-width": 0.8 } });
      break;
    case "repair":
      map.addLayer({ id: "repair-line", type: "line", source: src,
        paint: { "line-color": "#00e676", "line-width": 8, "line-opacity": 0.85 } });
      map.addLayer({ id: "repair-label", type: "symbol", source: src,
        layout: { "symbol-placement": "line-center", "text-field": ["concat", "#", ["to-string", ["get", "rank"]]],
                  "text-font": ["Noto Sans Bold"], "text-size": 14, "text-allow-overlap": true },
        paint: { "text-color": "#000", "text-halo-color": "#00e676", "text-halo-width": 2 } });
      break;
  }
}

/** Part of the line from the start to `km`, plus the front point. */
function sliceLine(coords: number[][], cum: number[], km: number) {
  const out: number[][] = [coords[0]];
  for (let i = 1; i < coords.length; i++) {
    if (cum[i] <= km) { out.push(coords[i]); continue; }
    const seg = cum[i] - cum[i - 1];
    const f = seg > 0 ? (km - cum[i - 1]) / seg : 0;
    out.push([coords[i - 1][0] + f * (coords[i][0] - coords[i - 1][0]),
              coords[i - 1][1] + f * (coords[i][1] - coords[i - 1][1])]);
    break;
  }
  if (out.length === 1) out.push(coords[0]);
  return out;
}

function drawFlow(map: maplibregl.Map, r: any) {
  clearFlow(map);
  const path = r.path;
  const events = (r.corridor?.events || []).map((e: any) => ({
    type: "Feature", properties: e, geometry: { type: "Point", coordinates: [e.lon, e.lat] },
  }));

  map.addSource("flowpath", { type: "geojson", data: path });
  map.addSource("flow-done", { type: "geojson", data: EMPTY });
  map.addSource("flow-front", { type: "geojson", data: EMPTY });
  map.addSource("flow-events", { type: "geojson", data: { type: "FeatureCollection", features: events } as any });

  map.addLayer({ id: "flowpath-ahead", type: "line", source: "flowpath",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#00e5ff", "line-width": 2.5, "line-opacity": 0.35, "line-dasharray": [2, 2] } });
  map.addLayer({ id: "flow-done-casing", type: "line", source: "flow-done",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#003a4d", "line-width": 8, "line-opacity": 0.5 } });
  map.addLayer({ id: "flow-done", type: "line", source: "flow-done",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#00e5ff", "line-width": 4.5 } });
  map.addLayer({ id: "flow-events-glow", type: "circle", source: "flow-events", filter: ["<", ["get", "km"], -1],
    paint: { "circle-radius": 16, "circle-color": EVENT_COLOR, "circle-opacity": 0.35, "circle-blur": 0.6 } });
  map.addLayer({ id: "flow-events", type: "circle", source: "flow-events", filter: ["<", ["get", "km"], -1],
    paint: {
      "circle-radius": ["match", ["get", "kind"], "village", 8, "bridge_lost", 7, "debris", 6, 5],
      "circle-color": EVENT_COLOR, "circle-stroke-color": "#fff", "circle-stroke-width": 1.2,
    } });
  map.addLayer({ id: "flow-front-halo", type: "circle", source: "flow-front",
    paint: { "circle-radius": 18, "circle-color": "#00e5ff", "circle-opacity": 0.25, "circle-blur": 0.5 } });
  map.addLayer({ id: "flow-front", type: "circle", source: "flow-front",
    paint: { "circle-radius": 7, "circle-color": "#ffffff", "circle-stroke-color": "#00e5ff", "circle-stroke-width": 3 } });
}

function updateFlow(map: maplibregl.Map, r: any, km: number) {
  const coords: number[][] = r?.path?.geometry?.coordinates;
  const cum: number[] = r?.corridor?.cum_km;
  if (!coords || !cum || !map.getSource("flow-done")) return;
  const part = sliceLine(coords, cum, km);
  (map.getSource("flow-done") as maplibregl.GeoJSONSource).setData(
    { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: part } } as any);
  (map.getSource("flow-front") as maplibregl.GeoJSONSource).setData(
    { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: part[part.length - 1] } } as any);
  map.setFilter("flow-events", ["<=", ["get", "km"], km]);
  map.setFilter("flow-events-glow", ["all", [">", ["get", "km"], km - 1.5], ["<=", ["get", "km"], km]]);
  const pulse = 16 + 5 * Math.sin(performance.now() / 180);
  map.setPaintProperty("flow-front-halo", "circle-radius", pulse);
}

export default function MapView({ runId, ready, bbox, visible, flowMode, onFlowResult, flowKm }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const bboxRef = useRef<number[] | null>(null);
  const userMoved = useRef(false);
  const lastFlow = useRef<any>(null);
  const flowRef = useRef({ flowMode, runId, ready, onFlowResult });
  flowRef.current = { flowMode, runId, ready, onFlowResult };

  function fitToBox(animate: boolean) {
    const map = mapRef.current, b = bboxRef.current;
    if (!map || !b) return;
    map.resize();
    map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 40, duration: animate ? 600 : 0 });
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

    for (const id of ["villages-cut", "villages-longer"]) {
      map.on("click", id, (e) => {
        if (flowRef.current.flowMode) return;
        const p: any = e.features?.[0]?.properties || {};
        const km = (v: any) => (v === null || v === undefined || v === "null" ? "no route" : `${v} km`);
        new maplibregl.Popup()
          .setLngLat(e.lngLat)
          .setHTML(`<b>${p.name}</b><br>Status: ${p.status}<br>Buildings: ${p.buildings_total}<br>
                    To help: ${km(p.dist_before_km)} → ${km(p.dist_after_km)}<br>
                    Nearest road: ${p.road_snap_m} m`)
          .addTo(map);
      });
      map.on("mouseenter", id, () => { if (!flowRef.current.flowMode) map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", id, () => { map.getCanvas().style.cursor = flowRef.current.flowMode ? "crosshair" : ""; });
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

  // Bbox outline + zoom
  const bboxKey = bbox ? bbox.join(",") : "";
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !bbox) return;
    bboxRef.current = bbox;
    userMoved.current = false;
    whenReady(map, () => {
      const data: any = { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [[
        [bbox[0], bbox[1]], [bbox[2], bbox[1]], [bbox[2], bbox[3]], [bbox[0], bbox[3]], [bbox[0], bbox[1]]]] } };
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

  // Run layers
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    let cancelled = false;
    lastFlow.current = null;
    whenReady(map, async () => {
      clearRunLayers(map);
      if (!runId || !ready) return;
      for (const name of ["debris", "flood", "roads", "villages", "repair"]) {
        try {
          const r = await fetch(layerUrl(runId, name));
          if (!r.ok || cancelled) continue;
          const data = await r.json();
          if (cancelled || map.getSource(`src-${name}`)) continue;
          // keep flow layers on top
          const before = map.getLayer("flowpath-ahead") ? "flowpath-ahead" : undefined;
          map.addSource(`src-${name}`, { type: "geojson", data });
          addLayers(map, name, `src-${name}`);
          if (before) for (const id of LAYER_IDS[name]) map.moveLayer(id, before);
          for (const id of LAYER_IDS[name])
            map.setLayoutProperty(id, "visibility", visible[name] === false ? "none" : "visible");
        } catch { /* layer missing for this run */ }
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

  return <div ref={containerRef} className="map" />;
}