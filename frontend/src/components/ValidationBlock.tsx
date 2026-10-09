import { useEffect, useState } from "react";
import { getEval } from "../api/client";

const pct = (x?: number | null) => (x == null ? "–" : `${Math.round(x * 100)}%`);

export default function ValidationBlock({ runId }: { runId: string }) {
  const [ev, setEv] = useState<any | null>(null);
  useEffect(() => { getEval(runId).then(setEv); }, [runId]);
  if (!ev) return null;
  const o = ev.ours_flood_plus_debris, b = ev.buildings, f = ev.flagged_share_of_run;
  return (
    <div className="stats-block">
      <h4>Check against EMSR927</h4>
      {o && <p>Debris areas: precision {pct(o.precision_tolerant)}, recall {pct(o.recall_tolerant)}</p>}
      {b?.by_class?.Destroyed && <p>Destroyed buildings found: {pct(b.by_class.Destroyed.recall_with_tolerance)}</p>}
      {f && <p>Land flagged: ours {pct(f.ours)} vs plain threshold {pct(f.baseline)}</p>}
      <p className="muted">EMS used for checking only. © European Union, Copernicus EMS.</p>
    </div>
  );
}