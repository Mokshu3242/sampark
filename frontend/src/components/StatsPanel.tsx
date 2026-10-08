type Props = { stats: any };

function fmt(v: any, suffix = ""): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return v.toLocaleString() + suffix;
  return String(v);
}

export default function StatsPanel({ stats }: Props) {
  if (!stats) {
    return (
      <div className="panel">
        <h2>Summary</h2>
        <p className="muted">Open a run to see results.</p>
      </div>
    );
  }
  const f = stats.flood || {};
  const d = stats.damage || {};
  const c = stats.cutoff || {};
  return (
    <div className="panel">
      <h2>Summary</h2>
      <div className="grid2">
        <div><span>Villages cut off</span><b>{fmt(c.cut_off)}</b></div>
        <div><span>Longer route</span><b>{fmt(c.longer_route)}</b></div>
        <div><span>Buildings affected</span><b>{fmt(d.buildings_affected)}</b></div>
        <div><span>Road segments cut</span><b>{fmt(d.roads_cut)}</b></div>
        <div><span>Bridges lost</span><b>{fmt(d.bridges_lost)}</b></div>
        <div><span>Facilities hit</span><b>{fmt(d.facilities_affected)}</b></div>
        <div><span>Debris / scour</span><b>{fmt(f.debris_area_ha, " ha")}</b></div>
        <div><span>Flood water</span><b>{fmt(f.flood_area_ha, " ha")}</b></div>
      </div>
    </div>
  );
}