type Props = { stats: any };

export default function CutoffList({ stats }: Props) {
  if (!stats || !stats.cutoff) return null;
  const plan = stats.cutoff.repair_plan || [];
  const ranked = (stats.cutoff.ranked || []).slice(0, 12);

  return (
    <div className="panel">
      <h2>Repair priority</h2>
      {plan.length === 0 && <p className="muted">No repair plan for this run.</p>}
      <ol className="repair">
        {plan.map((p: any) => (
          <li key={p.rank}>
            <b>{p.near}</b>{p.road ? ` · ${p.road}` : ""} · {Math.round(p.length_m)} m
            {p.has_bridge ? " (bridge)" : ""}
            <br />
            <span className="muted">
              → {p.villages_count} villages, {p.buildings_reconnected} buildings
              (total {p.cumulative_buildings})
            </span>
          </li>
        ))}
      </ol>

      <h2>Most affected villages</h2>
      <table className="villages">
        <thead>
          <tr><th>Village</th><th>Status</th><th className="num">Bldg</th><th className="num">Km before</th></tr>
        </thead>
        <tbody>
          {ranked.map((v: any) => (
            <tr key={v.name}>
              <td>{v.name}</td>
              <td>{v.status === "cut_off" ? "cut off" : "longer"}</td>
              <td className="num">{v.buildings_total ?? 0}</td>
              <td className="num">{v.dist_before_km ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}