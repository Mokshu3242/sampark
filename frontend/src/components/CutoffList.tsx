import { cleanName } from "./VillageCard";

type WhatIf = {
  upTo: number;
  names: Set<string>;   // lower-case names that come back online
  cutBefore: number;
  stillCut: number;
  villages: number;
  buildings: number;
  repairM: number;
  bridges: number;
} | null;

type Props = {
  stats: any;
  whatIf: WhatIf;
  onToggleRank: (rank: number) => void;
  onSelectVillage?: (name: string) => void;
};

const plural = (n: number, one: string, many = one + "s") => `${(n ?? 0).toLocaleString()} ${n === 1 ? one : many}`;
const STATUS: Record<string, string> = { cut_off: "cut off", longer_route: "longer", ok: "ok", pre_isolated: "no road" };
const shown = (n: any) => (String(n ?? "").startsWith("unnamed") ? "Unnamed hamlet" : n);

export default function CutoffList({ stats, whatIf, onToggleRank, onSelectVillage }: Props) {
  const c = stats?.cutoff;
  if (!c) return null;
  const plan = c.repair_plan || [];
  const ranked = (c.ranked || []).slice(0, 12);
  const upTo = whatIf?.upTo ?? 0;

  return (
    <div className="panel">
      <h2>Repair priority</h2>

      {plan.length === 0 ? (
        <p className="muted">No repairs needed: every village still has a route.</p>
      ) : (
        <>
          <p className="muted whatif-hint">Tick a package to see what reopening it brings back.</p>
          <ol className="repair">
            {plan.map((p: any) => {
              const on = p.rank <= upTo;
              return (
                <li key={p.rank} className={on ? "repair-on" : ""} onClick={() => onToggleRank(p.rank)}>
                  <input type="checkbox" className="repair-check" checked={on} readOnly tabIndex={-1} />
                  <b>{cleanName(p.near)}</b>{p.road ? ` · ${p.road}` : ""} · {Math.round(p.length_m)} m{p.has_bridge ? " (bridge)" : ""}
                  <br />
                  <span className="muted">
                    → {plural(p.villages_count, "village")}, {plural(p.buildings_reconnected, "building")} (total{" "}
                    {(p.cumulative_buildings ?? 0).toLocaleString()})
                  </span>
                </li>
              );
            })}
          </ol>
        </>
      )}

      {whatIf && (
        <div className="whatif">
          <div className="whatif-row"><span>Villages still cut off</span><b>{whatIf.cutBefore} → {whatIf.stillCut}</b></div>
          <div className="whatif-row"><span>Villages back online</span><b>+{whatIf.villages}</b></div>
          <div className="whatif-row"><span>Buildings reconnected</span><b>+{whatIf.buildings.toLocaleString()}</b></div>
          <div className="whatif-row">
            <span>Road to reopen</span>
            <b>{whatIf.repairM.toLocaleString()} m{whatIf.bridges ? `, ${whatIf.bridges} bridge${whatIf.bridges === 1 ? "" : "s"}` : ""}</b>
          </div>
          <p className="muted">
            Packages build on each other in plan order, so ticking #{upTo} includes #1{upTo > 1 ? `–#${upTo}` : ""}.
          </p>
        </div>
      )}

      <h2>Most affected villages</h2>
      <table className="villages">
        <thead>
          <tr><th>Village</th><th>Status</th><th className="num">Bldg</th><th className="num">Km before</th></tr>
        </thead>
        <tbody>
          {ranked.map((v: any, i: number) => {
            const back = !!whatIf && whatIf.names.has(String(v.name).toLowerCase());
            return (
              <tr key={`${v.name}-${i}`}
                  className={`${onSelectVillage ? "clickable" : ""} ${back ? "v-reconnect" : ""}`}
                  onClick={() => onSelectVillage?.(v.name)}>
                <td>{shown(v.name)}</td>
                <td>{back ? "back online" : STATUS[v.status] ?? v.status}</td>
                <td className="num">{(v.buildings_total ?? 0).toLocaleString()}</td>
                <td className="num">{v.dist_before_km ?? "-"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}