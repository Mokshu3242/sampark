import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl"
import "maplibre-gl/dist/maplibre-gl.css";
import { layerUrl } from "../api/client";

type Props = { runId: string | null; ready: boolean; bbox: number[] | null; visible: Record<string, boolean> };

const LAYER_IDS: Record<string, string[]> = {
  flood: ["flood-fill"],
  debris: ["debris-fill"],
  roads: ["roads-ok", "roads-affected", "roads-cut"],
  villages: ["villages-longer", "villages-cut"],
  repair: ["repair-line", "repair-label"],
};
const ATTRIBUTION =
  "Contains modified Copernicus Sentinel data 2026 · Copernicus WorldDEM-30 © DLR e.V., © Airbus DS · © OpenStreetMap contributors";

const loadedMaps = new WeakSet<maplibregl.Map>();

function whenReady(map: maplibregl.Map, fn: () => void) {
  if (loadedMaps.has(map)) fn();
  else map.once("load", () => { loadedMaps.add(map); fn(); });
}

function clearRunLayers(map: maplibregl.Map) {
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

export default function MapView({ runId, ready, bbox, visible }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);

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
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    map.addControl(new maplibregl.ScaleControl(), "bottom-left");
    map.addControl(new maplibregl.AttributionControl({ customAttribution: ATTRIBUTION, compact: true }));

    // Village popups
    for (const id of ["villages-cut", "villages-longer"]) {
      map.on("click", id, (e) => {
        const p: any = e.features?.[0]?.properties || {};
        const km = (v: any) => (v === null || v === undefined || v === "null" ? "no route" : `${v} km`);
        new maplibregl.Popup()
          .setLngLat(e.lngLat)
          .setHTML(`<b>${p.name}</b><br>Status: ${p.status}<br>Buildings: ${p.buildings_total}<br>
                    To help: ${km(p.dist_before_km)} → ${km(p.dist_after_km)}<br>
                    Nearest road: ${p.road_snap_m} m`)
          .addTo(map);
      });
      map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
    }
    mapRef.current = map;
    map.once("load", () => loadedMaps.add(map));
    setTimeout(() => map.resize(), 200);
    return () => { map.remove(); mapRef.current = null; };
    
  }, []);

  // Bbox outline + zoom
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !bbox) return;
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
      map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 30, duration: 600 });
    });
  }, [bbox]);

  // Clear old layers when run changes; load new ones once the run is ready
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    let cancelled = false;
    whenReady(map, async () => {
      clearRunLayers(map);
      if (!runId || !ready) return;
      for (const name of ["debris", "flood", "roads", "villages", "repair"]) {   // drawing order
        try {
          const r = await fetch(layerUrl(runId, name));
          if (!r.ok || cancelled) continue;
          map.addSource(`src-${name}`, { type: "geojson", data: await r.json() });
          addLayers(map, name, `src-${name}`);
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