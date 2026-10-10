import { useCallback, useEffect, useMemo, useState } from "react";
import MapView from "./components/MapView";
import RunForm from "./components/RunForm";
import LayerPanel from "./components/LayerPanel";
import StatsPanel from "./components/StatsPanel";
import CutoffList from "./components/CutoffList";
import ChatBox from "./components/ChatBox";
import FlowPanel from "./components/FlowPanel";
import VillageSearch from "./components/VillageSearch";
import VillageCard from "./components/VillageCard";
import ComparePanel from "./components/ComparePanel";
import { exportUrl, getRun, getStats, layerUrl, reportUrl, villageNamesFrom, type RunInfo } from "./api/client";

const STAGE_LABELS = ["Created", "Satellite data", "Flood map", "Damage", "Cut-off routing", "Done"];
const same = (a: any, b: any) => String(a ?? "").toLowerCase() === String(b ?? "").toLowerCase();
const low = (s: any) => String(s ?? "").toLowerCase();

export default function App() {
  const [runId, setRunId] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [run, setRun] = useState<RunInfo | null>(null);
  const [stats, setStats] = useState<any>(null);
  const [villageRows, setVillageRows] = useState<any[]>([]);
  const [visible, setVisible] = useState<Record<string, boolean>>({
    repair: true, flood: true, debris: true, roads: true, villages: true, uncertainty: false,
  });
  const [flowMode, setFlowMode] = useState(false);
  const [flowResult, setFlowResult] = useState<any>(null);
  const [flowKm, setFlowKm] = useState<number | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [repairUpTo, setRepairUpTo] = useState(0); // 0 = nothing ticked
  const [compareEnabled, setCompareEnabled] = useState(false);
  const [compareValue, setCompareValue] = useState(0);

  const onFlowResult = useCallback((r: any) => setFlowResult(r), []);
  const onFlowKm = useCallback((km: number | null) => setFlowKm(km), []);
  const onSelect = useCallback((name: string) => setSelected(name), []);
  const onConsumed = useCallback(() => setPendingQuestion(null), []);
  // In plan order: tick #k → #1..#k. Untick #k → keep #1..#(k-1).
  const onToggleRank = useCallback(
    (rank: number) => setRepairUpTo((cur) => (cur >= rank ? rank - 1 : rank)), []);

  const failed = run?.status === "failed";
  const ready = !!run?.ready && !failed;
  const running = !!runId && !ready && !failed;

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    setRun(null); setStats(null); setVillageRows([]);
    setFlowMode(false); setFlowResult(null); setFlowKm(null);
    setSelected(null); setPendingQuestion(null); setRepairUpTo(0);
    setCompareEnabled(false); setCompareValue(0);
    const tick = async () => {
      try {
        const r = await getRun(runId);
        if (cancelled) return;
        setRun(r);
        if (r.ready || r.status === "failed") {
          const s = await getStats(runId);
          if (!cancelled) setStats(s);
          try {
            const vr = await fetch(layerUrl(runId, "villages"));
            if (vr.ok && !cancelled) {
              const gj = await vr.json();
              setVillageRows((gj.features || []).map((f: any) => {
                const c = f.geometry?.type === "Point" ? f.geometry.coordinates : null;
                return { ...f.properties, _lon: c?.[0], _lat: c?.[1] };
              }));
            }
          } catch { /* no villages layer */ }
          return;
        }
      } catch { /* retry */ }
      if (!cancelled) setTimeout(tick, 2000);
    };
    tick();
    return () => { cancelled = true; };
  }, [runId, reload]);

  // Villages + their damage numbers from damage_stats
  const villages = useMemo(() => {
    const per: any[] = stats?.damage?.per_settlement || [];
    return villageRows.map((v) => {
      const d = per.find((p) => same(p.name, v.name));
      return d ? { ...v, buildings_affected: d.buildings_affected, roads_cut: d.roads_cut, bridges_lost: d.bridges_lost } : v;
    });
  }, [villageRows, stats]);

  const selectedRow = useMemo(
    () => (selected ? villages.find((v) => same(v.name, selected)) ?? null : null),
    [selected, villages],
  );
  const highlight = useMemo(
    () => (selectedRow && Number.isFinite(selectedRow._lon)
      ? { name: selectedRow.name, lon: selectedRow._lon, lat: selectedRow._lat } : null),
    [selectedRow],
  );

  // Repair what-if: packages #1..#repairUpTo, using the plan's own numbers
  const whatIf = useMemo(() => {
    const plan: any[] = stats?.cutoff?.repair_plan || [];
    if (!repairUpTo || plan.length === 0) return null;
    const chosen = plan.filter((p) => p.rank <= repairUpTo);
    const names = new Set(chosen.flatMap((p) => villageNamesFrom(p.villages_reconnected)).map(low));
    const cutRows = villages.filter((v) => v.status === "cut_off");
    const backRows = villages.filter((v) => names.has(low(v.name)));
    const cutBefore = stats?.cutoff?.cut_off ?? cutRows.length;
    const stillCut = cutRows.length
      ? cutRows.filter((v) => !names.has(low(v.name))).length
      : Math.max(0, cutBefore - names.size);
    return {
      upTo: repairUpTo,
      ranks: chosen.map((p) => p.rank),
      names,
      cutBefore,
      stillCut,
      villages: names.size,
      buildings: chosen[chosen.length - 1]?.cumulative_buildings ?? 0,
      repairM: Math.round(chosen.reduce((s, p) => s + (p.length_m || 0), 0)),
      bridges: chosen.filter((p) => p.has_bridge).length,
      villagesGeo: {
        type: "FeatureCollection",
        features: backRows
          .filter((v) => Number.isFinite(v._lon) && Number.isFinite(v._lat))
          .map((v) => ({ type: "Feature", properties: { name: v.name },
                         geometry: { type: "Point", coordinates: [v._lon, v._lat] } })),
      },
    };
  }, [repairUpTo, stats, villages]);

  const mapWhatIf = useMemo(
    () => (whatIf ? { ranks: whatIf.ranks, villagesGeo: whatIf.villagesGeo } : null),
    [whatIf],
  );

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

        {ready && villages.length > 0 && <VillageSearch villages={villages} onSelect={onSelect} />}

        <LayerPanel
          visible={visible}
          onToggle={(n) => setVisible((v) => ({ ...v, [n]: !v[n] }))}
          uncertaintyHa={stats?.flood?.uncertainty_ha ?? null}
        />

        {ready && (
          <ComparePanel
            enabled={compareEnabled}
            onToggle={() => setCompareEnabled((v) => !v)}
            value={compareValue}
            onChange={setCompareValue}
          />
        )}

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
        <MapView
          runId={runId} ready={ready} bbox={run?.request?.bbox ?? null} visible={visible}
          flowMode={flowMode} onFlowResult={onFlowResult} flowKm={flowKm}
          onVillageSelect={onSelect} highlight={highlight}
          whatIf={mapWhatIf}
          compareEnabled={compareEnabled} compareValue={compareValue}
          card={selectedRow && runId ? (
            <VillageCard runId={runId} village={selectedRow}
                         repairPlan={stats?.cutoff?.repair_plan ?? []}
                         onClose={() => setSelected(null)} />
          ) : null}
        />
      </main>

      <aside className="right">
        <StatsPanel stats={stats} />
        <CutoffList stats={stats} whatIf={whatIf} onToggleRank={onToggleRank} onSelectVillage={onSelect} />
        <ChatBox key={runId ?? "none"} runId={ready ? runId : null}
                 pendingQuestion={pendingQuestion} onPendingConsumed={onConsumed} />
      </aside>
    </div>
  );
}