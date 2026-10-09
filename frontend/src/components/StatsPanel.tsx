type Props = { stats: any };

function fmt(v: any, suffix = ""): string {
  if (v === null || v === undefined) return "-";
  if (typeof v === "number") return v.toLocaleString() + suffix;
  return String(v);
}

function pct(v: any, digits = 0): string {
  if (typeof v !== "number") return "-";
  return (v * 100).toFixed(digits) + "%";
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
  const ev = stats.eval || null;
  const px = ev?.ours_flood_plus_debris;
  const bl = ev?.baseline_threshold;
  const destroyed = ev?.buildings?.by_class?.Destroyed;
  const flagged = ev?.flagged_share_of_run;

  return (
    <div className="panel">
      <h2>Summary</h2>
      <div className="grid2">
        <div>
          <span>Villages cut off</span>
          <b>{fmt(c.cut_off)}</b>
        </div>
        <div>
          <span>Longer route</span>
          <b>{fmt(c.longer_route)}</b>
        </div>
        <div>
          <span>Buildings affected</span>
          <b>{fmt(d.buildings_affected)}</b>
        </div>
        <div>
          <span>Road segments cut</span>
          <b>{fmt(d.roads_cut)}</b>
        </div>
        <div>
          <span>Bridges lost</span>
          <b>{fmt(d.bridges_lost)}</b>
        </div>
        <div>
          <span>Facilities hit</span>
          <b>{fmt(d.facilities_affected)}</b>
        </div>
        <div>
          <span>Debris / scour</span>
          <b>{fmt(f.debris_area_ha, " ha")}</b>
        </div>
        <div>
          <span>Flood water</span>
          <b>{fmt(f.flood_area_ha, " ha")}</b>
        </div>
      </div>

      {ev && (px || destroyed) && (
        <div className="validation">
          <h2>Check vs Copernicus EMS</h2>
          <div className="grid2">
            {px && (
              <>
                <div>
                  <span>Precision (±40 m)</span>
                  <b>{pct(px.precision_tolerant)}</b>
                </div>
                <div>
                  <span>Recall (±40 m)</span>
                  <b>{pct(px.recall_tolerant)}</b>
                </div>
                <div>
                  <span>F1 (±40 m)</span>
                  <b>{pct(px.f1_tolerant)}</b>
                </div>
                <div>
                  <span>Baseline F1 (±40 m)</span>
                  <b>{pct(bl?.f1_tolerant)}</b>
                </div>
              </>
            )}
            {destroyed && (
              <div>
                <span>Destroyed buildings found</span>
                <b>{pct(destroyed.recall_with_tolerance)}</b>
              </div>
            )}
            {flagged && (
              <div>
                <span>Land flagged (ours / baseline)</span>
                <b>
                  {pct(flagged.ours, 1)} / {pct(flagged.baseline, 1)}
                </b>
              </div>
            )}
          </div>
          <p className="muted">
            Scored on EMSR927 {(ev.aois_used || []).join(", ")} (debris flows).
            The baseline flags far more land, so it finds more but with many
            more false alarms. EMS used for checking only, never as input. ©
            European Union, Copernicus EMS.
          </p>
        </div>
      )}
    </div>
  );
}
