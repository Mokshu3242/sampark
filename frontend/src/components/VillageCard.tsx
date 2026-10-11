import { useEffect, useState } from "react";
import { askCopilot, chipUrl } from "../api/client";

type Props = {
  runId: string;
  village: any;
  repairPlan: any[];
  onClose: () => void;
};

const STATUS: Record<string, { text: string; tone: string }> = {
  cut_off: { text: "Cut off", tone: "danger" },
  longer_route: { text: "Longer route", tone: "warn" },
  ok: { text: "Connected", tone: "ok" },
  pre_isolated: { text: "No vehicle road before the flood", tone: "muted" },
};

/** "unnamed hamlet (node/3510164368)" -> "Unnamed hamlet". Real names stay as they are. */
export function cleanName(s: any): string {
  const t = String(s ?? "").replace(/\s*\((node|way|relation)\/\d+\)/i, "").trim();
  if (!t) return "Unnamed place";
  return /^unnamed/i.test(t) ? t.charAt(0).toUpperCase() + t.slice(1) : t;
}

function km(v: any): string {
  const n = Number(v);
  return v === null || v === undefined || v === "null" || !Number.isFinite(n) ? "no route" : `${n.toFixed(1)} km`;
}

function findPackage(plan: any[], name: string): any | null {
  const low = (name || "").toLowerCase();
  return (plan || []).find((p) => (p.villages_reconnected || []).some((n: string) => (n || "").toLowerCase() === low)) ?? null;
}

type Answer = { busy: boolean; text?: string; sources?: any[]; provider?: string; error?: string };

export default function VillageCard({ runId, village, repairPlan, onClose }: Props) {
  const [ans, setAns] = useState<Answer | null>(null);
  const [imgOk, setImgOk] = useState({ before: true, after: true });

  // New village: clear the old answer and image state
  useEffect(() => { setAns(null); setImgOk({ before: true, after: true }); }, [village?.name]);

  const st = STATUS[village.status] ?? { text: village.status, tone: "muted" };
  const pkg = findPackage(repairPlan, village.name);
  const name = cleanName(village.name);
  const hasPos = Number.isFinite(village._lon) && Number.isFinite(village._lat);

  async function ask() {
    setAns({ busy: true });
    try {
      const r = await askCopilot(runId, `Tell me about ${village.name}`, "auto", []);
      setAns({ busy: false, text: r.answer, sources: r.sources, provider: r.provider });
    } catch (e: any) {
      setAns({ busy: false, error: String(e.message || e) });
    }
  }

  return (
    <div className="panel vcard">
      <div className="vcard-head">
        <div className="vtitle">{name}</div>
        <button className="vclose" onClick={onClose} aria-label="Close">×</button>
      </div>
      <div className={`vstatus ${st.tone}`}>{st.text}</div>

      {hasPos && (
        <div className="vchips">
          {(["before", "after"] as const).map((k) => (
            <figure key={k}>
              {imgOk[k] ? (
                <img
                  src={chipUrl(runId, k === "before" ? "s2_before" : "s2_after", village._lon, village._lat)}
                  alt={`Sentinel-2 ${k} the flood around ${name}`}
                  crossOrigin="anonymous"
                  onError={() => setImgOk((s) => ({ ...s, [k]: false }))}
                />
              ) : (
                <div className="vchip-empty">No image</div>
              )}
              <span className="vchip-dot" />
              <figcaption>{k === "before" ? "Before" : "After"}</figcaption>
            </figure>
          ))}
        </div>
      )}
      {hasPos && (
        <p className="vchip-note">
          Sentinel-2, 2 × 2 km, false colour: green = vegetation, brown = bare ground or debris,
          dark = water, checkered = cloud.
        </p>
      )}

      <div className="grid2">
        <div><span>Buildings</span><b>{(village.buildings_total ?? 0).toLocaleString()}</b></div>
        <div><span>Damaged</span><b>{(village.buildings_affected ?? 0).toLocaleString()}</b></div>
        <div><span>To help, before</span><b>{km(village.dist_before_km)}</b></div>
        <div><span>To help, after</span><b>{km(village.dist_after_km)}</b></div>
      </div>

      {pkg ? (
        <div className="repair-inline">
          <b>Reopen #{pkg.rank}</b> near {cleanName(pkg.near)}{pkg.road ? `, ${pkg.road}` : ""}:{" "}
          {Math.round(pkg.length_m)} m{pkg.has_bridge ? " including a bridge" : ""}. It brings back{" "}
          {pkg.villages_count} village{pkg.villages_count === 1 ? "" : "s"}.
        </div>
      ) : village.status === "cut_off" ? (
        <p className="muted">No package in the current repair plan reconnects this village.</p>
      ) : null}

      {!ans && (
        <button className="ask-btn" onClick={ask}>Ask the copilot about {name}</button>
      )}
      {ans?.busy && <div className="vanswer muted">Thinking…</div>}
      {ans?.error && <div className="error">{ans.error}</div>}
      {ans?.text && (
        <div className="vanswer">
          <div>{ans.text}</div>
          {ans.sources && ans.sources.length > 0 && (
            <details>
              <summary>{ans.sources.length} numbers traced to data{ans.provider ? ` · ${ans.provider}` : ""}</summary>
              <ul>{ans.sources.map((s, j) => <li key={j}>{s.number} ← {s.source}</li>)}</ul>
            </details>
          )}
          <button className="vagain" onClick={ask}>Ask again</button>
        </div>
      )}
    </div>
  );
}