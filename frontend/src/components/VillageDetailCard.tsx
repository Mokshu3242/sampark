type Props = {
  village: any | null;
  repairPlan: any[];
  onAskCopilot: (question: string) => void;
  onClose: () => void;
};

const STATUS: Record<string, { text: string; tone: string }> = {
  cut_off: { text: "Cut off", tone: "danger" },
  longer_route: { text: "Longer route", tone: "warn" },
  ok: { text: "Connected", tone: "ok" },
  pre_isolated: { text: "No vehicle road before the flood", tone: "muted" },
};

function km(v: any): string {
  const n = Number(v);
  return v === null || v === undefined || v === "null" || !Number.isFinite(n) ? "no route" : `${n.toFixed(1)} km`;
}

function findPackage(plan: any[], name: string): any | null {
  const low = (name || "").toLowerCase();
  return (plan || []).find((p) => (p.villages_reconnected || []).some((n: string) => (n || "").toLowerCase() === low)) ?? null;
}

export default function VillageDetailCard({ village, repairPlan, onAskCopilot, onClose }: Props) {
  if (!village) return null;
  const st = STATUS[village.status] ?? { text: village.status, tone: "muted" };
  const pkg = findPackage(repairPlan, village.name);
  const before = Number(village.dist_before_km), after = Number(village.dist_after_km);
  const detour = Number.isFinite(before) && Number.isFinite(after) && village.dist_after_km != null
    ? after - before : null;
  const name = String(village.name).startsWith("unnamed") ? "Unnamed hamlet" : village.name;

  return (
    <div className="panel vcard">
      <div className="vcard-head">
        <div className="vtitle">{name}</div>
        <button className="vclose" onClick={onClose} aria-label="Close">×</button>
      </div>
      <div className={`vstatus ${st.tone}`}>{st.text}</div>

      <div className="grid2">
        <div><span>Buildings</span><b>{(village.buildings_total ?? 0).toLocaleString()}</b></div>
        <div><span>Damaged</span><b>{(village.buildings_affected ?? 0).toLocaleString()}</b></div>
        <div><span>To help, before</span><b>{km(village.dist_before_km)}</b></div>
        <div><span>To help, after</span><b>{km(village.dist_after_km)}</b></div>
        {village.roads_cut != null && <div><span>Roads cut nearby</span><b>{village.roads_cut}</b></div>}
        {village.bridges_lost != null && <div><span>Bridges lost nearby</span><b>{village.bridges_lost}</b></div>}
      </div>

      {detour != null && detour > 0 && (
        <p className="muted">The detour adds {detour.toFixed(1)} km compared to before the flood.</p>
      )}
      {Number(village.road_snap_m) > 100 && (
        <p className="muted">The nearest vehicle road is {Math.round(Number(village.road_snap_m))} m away.</p>
      )}

      {pkg ? (
        <div className="repair-inline">
          <b>Reopen #{pkg.rank}</b> near {pkg.near}{pkg.road ? `, ${pkg.road}` : ""}: {Math.round(pkg.length_m)} m
          {pkg.has_bridge ? " including a bridge" : ""}. It brings back {pkg.villages_count} village
          {pkg.villages_count === 1 ? "" : "s"}.
        </div>
      ) : village.status === "cut_off" ? (
        <p className="muted">No package in the current repair plan reconnects this village.</p>
      ) : null}

      <button className="ask-btn" onClick={() => onAskCopilot(`Tell me about ${village.name}`)}>
        Ask the copilot about {name}
      </button>
    </div>
  );
}