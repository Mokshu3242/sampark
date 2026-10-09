type Props = { stats: any; onSelectVillage?: (name: string) => void };

const plural = (n: number, one: string, many = one + "s") => `${(n ?? 0).toLocaleString()} ${n === 1 ? one : many}`;
const STATUS: Record<string, string> = { cut_off: "cut off", longer_route: "longer", ok: "ok", pre_isolated: "no road" };

export default function CutoffList({ stats, onSelectVillage }: Props) {
  const c = stats?.cutoff;
  if (!c) return null;
  const plan = c.repair_plan || [];
  const ranked = (c.ranked || []).slice(0, 12);

  return (
    <div className="panel">
      <h2>Repair priority</h2>
      {plan.length === 0 ? (
        <p className="muted">No repairs needed: every village still has a route.</p>
      ) : (
        <ol className="repair">
          {plan.map((p: any) => (
            <li key={p.rank}>
              <b>{p.near}</b>{p.road ? ` · ${p.road}` : ""} · {Math.round(p.length_m)} m{p.has_bridge ? " (bridge)" : ""}
              <br />
              <span className="muted">
                → {plural(p.villages_count, "village")}, {plural(p.buildings_reconnected, "building")} (total{" "}
                {(p.cumulative_buildings ?? 0).toLocaleString()})
              </span>
            </li>
          ))}
        </ol>
      )}

      <h2>Most affected villages</h2>
      <table className="villages">
        <thead>
          <tr><th>Village</th><th>Status</th><th className="num">Bldg</th><th className="num">Km before</th></tr>
        </thead>
        <tbody>
          {ranked.map((v: any, i: number) => (
            <tr key={`${v.name}-${i}`} className={onSelectVillage ? "clickable" : ""} onClick={() => onSelectVillage?.(v.name)}>
              <td>{String(v.name).startsWith("unnamed") ? "Unnamed hamlet" : v.name}</td>
              <td>{STATUS[v.status] ?? v.status}</td>
              <td className="num">{(v.buildings_total ?? 0).toLocaleString()}</td>
              <td className="num">{v.dist_before_km ?? "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}