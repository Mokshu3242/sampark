import { useCallback, useEffect, useState } from "react";
import MapView from "./components/MapView";
import RunForm from "./components/RunForm";
import LayerPanel from "./components/LayerPanel";
import StatsPanel from "./components/StatsPanel";
import CutoffList from "./components/CutoffList";
import ChatBox from "./components/ChatBox";
import FlowPanel from "./components/FlowPanel";
import { exportUrl, getRun, getStats, reportUrl, type RunInfo } from "./api/client";

const STAGE_LABELS = ["Created", "Satellite data", "Flood map", "Damage", "Cut-off routing", "Done"];

export default function App() {
  const [runId, setRunId] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [run, setRun] = useState<RunInfo | null>(null);
  const [stats, setStats] = useState<any>(null);
  const [visible, setVisible] = useState<Record<string, boolean>>({
    repair: true, flood: true, debris: true, roads: true, villages: true,
  });
  const [flowMode, setFlowMode] = useState(false);
  const [flowResult, setFlowResult] = useState<any>(null);
  const [flowKm, setFlowKm] = useState<number | null>(null);
  const onFlowResult = useCallback((r: any) => setFlowResult(r), []);
  const onFlowKm = useCallback((km: number | null) => setFlowKm(km), []);

  const failed = run?.status === "failed";
  const ready = !!run?.ready && !failed;
  const running = !!runId && !ready && !failed;

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    setRun(null);
    setStats(null);
    setFlowMode(false);
    setFlowResult(null);
    setFlowKm(null);
    const tick = async () => {
      try {
        const r = await getRun(runId);
        if (cancelled) return;
        setRun(r);
        if (r.ready || r.status === "failed") {
          const s = await getStats(runId);
          if (!cancelled) setStats(s);
          return;
        }
      } catch { /* retry */ }
      if (!cancelled) setTimeout(tick, 2000);
    };
    tick();
    return () => { cancelled = true; };
  }, [runId, reload]);

  const stageIdx = ready ? 5 : Math.max(0, run?.stage_index ?? 0);

  return (
    <div className="app">
      <aside className="left">
        <header>
          <h1>Flood Watch</h1>
          <p className="muted">Satellite flood damage and road access · educational prototype</p>
        </header>

        <div className="disclaimer">
          Educational prototype, not an operational tool. Results are satellite estimates and are not field-verified.
        </div>

        <RunForm onSelect={(id) => { setRunId(id); setReload((n) => n + 1); }} disabled={running} />

        {runId && (
          <div className="panel">
            <h2>Run {runId}</h2>
            <div className="progress">
              {STAGE_LABELS.slice(1).map((l, i) => (
                <div key={l} className={`step ${ready || i + 1 < stageIdx ? "done" : i + 1 === stageIdx ? "active" : ""}`}>{l}</div>
              ))}
            </div>
            <div className="status">{failed ? `Failed: ${run?.detail}` : run?.detail || "starting…"}</div>
            {run?.quality && <div className="muted">Data quality: <b>{run.quality.badge}</b>
              {" "}· radar {run.quality.s1?.before_date} → {run.quality.s1?.after_date}</div>}
            {ready && (
              <div className="actions">
                <a href={reportUrl(runId, "en")} target="_blank" rel="noreferrer">Report (EN)</a>
                <a href={reportUrl(runId, "ne")} target="_blank" rel="noreferrer">Report (नेपाली)</a>
                <a href={exportUrl(runId)}>Download data</a>
              </div>
            )}
          </div>
        )}

        <LayerPanel visible={visible} onToggle={(n) => setVisible((v) => ({ ...v, [n]: !v[n] }))} />

        {ready && (
          <FlowPanel
            flowMode={flowMode}
            onToggle={() => { setFlowMode((m) => !m); setFlowResult(null); setFlowKm(null); }}
            result={flowResult}
            onKm={onFlowKm}
          />
        )}
      </aside>

      <main className="map-wrap">
        <MapView runId={runId} ready={ready} bbox={run?.request?.bbox ?? null} visible={visible}
                 flowMode={flowMode} onFlowResult={onFlowResult} flowKm={flowKm} />
      </main>

      <aside className="right">
        <StatsPanel stats={stats} />
        <CutoffList stats={stats} />
        <ChatBox key={runId ?? "none"} runId={ready ? runId : null} />
      </aside>
    </div>
  );
}