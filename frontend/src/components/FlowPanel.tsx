import { useEffect, useMemo, useRef, useState } from "react";

type Props = {
  flowMode: boolean;
  onToggle: () => void;
  result: any;
  onKm: (km: number | null) => void;
};

const STATUS: Record<string, string> = {
  cut_off: "cut off", longer_route: "longer", ok: "ok", pre_isolated: "no road",
};
const PLAYBACK_S = 25;

function clock(hours: number) {
  const m = Math.round(hours * 60);
  return `+${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}
function vname(n: any) {
  return String(n ?? "").startsWith("unnamed") ? "Unnamed hamlet" : n;
}

export default function FlowPanel({ flowMode, onToggle, result, onKm }: Props) {
  const p = result?.path?.properties;
  const corr = result?.corridor;
  const total: number = corr?.cum_km?.[corr.cum_km.length - 1] ?? p?.length_km ?? 0;

  const [km, setKm] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(20);   // km/h, illustrative
  const kmRef = useRef(0);

  // New trace: start from the top and play
  useEffect(() => {
    kmRef.current = 0;
    setKm(0);
    if (p) { setPlaying(true); onKm(0); } else { setPlaying(false); onKm(null); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result]);

  // Animation loop
  useEffect(() => {
    if (!playing || !total) return;
    let raf = 0, last = performance.now();
    const step = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      const next = Math.min(total, kmRef.current + (dt * total) / PLAYBACK_S);
      kmRef.current = next;
      setKm(next);
      onKm(next);
      if (next >= total) { setPlaying(false); return; }
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, total]);

  const live = useMemo(() => {
    const ev = (corr?.events || []).filter((e: any) => e.km <= km);
    const c = (k: string) => ev.filter((e: any) => e.kind === k).length;
    return {
      villages: c("village"),
      cut: ev.filter((e: any) => e.kind === "village" && e.status === "cut_off").length,
      buildings: ev.reduce((s: number, e: any) => s + (e.kind === "village" ? e.buildings_affected || 0 : 0), 0),
      roads: c("road_cut"),
      bridges: c("bridge_lost"),
      debris: ev.reduce((s: number, e: any) => s + (e.kind === "debris" ? e.area_ha || 0 : 0), 0),
    };
  }, [corr, km]);

  function replay() {
    kmRef.current = 0;
    setKm(0);
    onKm(0);
    setPlaying(true);
  }

  return (
    <div className="panel">
      <h2>Flood path</h2>
      <label className="layer-row">
        <input type="checkbox" checked={flowMode} onChange={onToggle} />
        Trace downstream from a click
      </label>
      {flowMode && !result && <p className="muted">Click on or near a river.</p>}
      {result?.busy && <p className="muted">Tracing… (the first click on an old run takes about 30 s)</p>}
      {result?.error && <div className="error">{result.error}</div>}

      {p && corr && (
        <>
          <div className="flow-clock">
            <b>{km.toFixed(1)} / {total.toFixed(1)} km</b>
            <span>{clock(km / speed)}</span>
          </div>
          <div className="flow-bar"><div style={{ width: `${total ? (100 * km) / total : 0}%` }} /></div>

          <div className="flow-controls">
            <button onClick={() => (km >= total ? replay() : setPlaying(!playing))}>
              {playing ? "Pause" : km >= total ? "Replay" : "Play"}
            </button>
            <label>
              Wave speed <b>{speed} km/h</b>
              <input type="range" min={10} max={40} step={5} value={speed}
                     onChange={(e) => setSpeed(Number(e.target.value))} />
            </label>
          </div>

          <div className="grid2 flow-counters">
            <div><span>Villages reached</span><b>{live.villages}</b></div>
            <div><span>Of them cut off</span><b>{live.cut}</b></div>
            <div><span>Buildings hit</span><b>{live.buildings.toLocaleString()}</b></div>
            <div><span>Road segments cut</span><b>{live.roads}</b></div>
            <div><span>Bridges lost</span><b>{live.bridges}</b></div>
            <div><span>Debris / scour</span><b>{live.debris.toFixed(1)} ha</b></div>
          </div>

          {result.villages.length > 0 && (
            <table className="villages">
              <thead><tr><th>Village</th><th className="num">Km</th><th className="num">Arrives</th><th>Status</th></tr></thead>
              <tbody>
                {result.villages.slice(0, 15).map((v: any, i: number) => (
                  <tr key={`${v.name}-${i}`} className={v.km_along_path <= km ? "reached" : ""}>
                    <td>{vname(v.name)}</td>
                    <td className="num">{v.km_along_path}</td>
                    <td className="num">{clock(v.km_along_path / speed)}</td>
                    <td>{STATUS[v.status] ?? v.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <p className="muted">
            Replay of satellite-detected damage within {corr.buffer_m} m of the flow path. Timing is
            illustrative ({speed} km/h assumed); this is not a flood forecast. The path follows the
            steepest way down the DEM (D8) and ignores dams and debris jams.
          </p>
        </>
      )}
    </div>
  );
}