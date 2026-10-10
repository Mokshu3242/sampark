import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import MapView, { type CameraTarget } from "./components/MapView";
import RunForm from "./components/RunForm";
import LayerPanel from "./components/LayerPanel";
import StatsPanel from "./components/StatsPanel";
import CutoffList from "./components/CutoffList";
import ChatBox from "./components/ChatBox";
import FlowPanel from "./components/FlowPanel";
import VillageSearch from "./components/VillageSearch";
import VillageCard from "./components/VillageCard";
import ComparePanel from "./components/ComparePanel";
import StoryPanel from "./components/StoryPanel";
import StoryOverlay from "./components/StoryOverlay";
import { buildStory, type StoryAction, type StoryStep } from "./story/story";
import { primeVoices, speak, startRiver, stopRiver, stopSpeaking } from "./story/speak";
import {
  exportUrl, getRun, getStats, imageUrl, layerUrl, reportUrl, traceFlow, villageNamesFrom, type RunInfo,
} from "./api/client";

const STAGE_LABELS = ["Created", "Satellite data", "Flood map", "Damage", "Cut-off routing", "Done"];
const same = (a: any, b: any) => String(a ?? "").toLowerCase() === String(b ?? "").toLowerCase();
const low = (s: any) => String(s ?? "").toLowerCase();
const ease = (x: number) => (x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2);

type Tween = { from: number; to: number; t0: number; dur: number };
type StoryState = {
  start: number; steps: StoryStep[]; total: number; fired: Set<number>;
  tweens: Tween[]; cmp: number; flow: any; raf: number; lastUi: number;
  snap: { visible: Record<string, boolean>; compareEnabled: boolean; compareValue: number; repairUpTo: number };
};

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

  // Story mode
  const [storyRunning, setStoryRunning] = useState(false);
  const [storyLoading, setStoryLoading] = useState(false);
  const [storyProgress, setStoryProgress] = useState(0);
  const [storyVoice, setStoryVoice] = useState(false);
  const [storyRiver, setStoryRiver] = useState(false);
  const [caption, setCaption] = useState("");
  const [cameraTarget, setCameraTarget] = useState<CameraTarget | null>(null);
  const [relief, setRelief] = useState(false);
  const storyRef = useRef<StoryState | null>(null);
  const camKey = useRef(0);
  const voiceRef = useRef(storyVoice);
  voiceRef.current = storyVoice;
  const liveRef = useRef({ visible, compareEnabled, compareValue, repairUpTo });
  liveRef.current = { visible, compareEnabled, compareValue, repairUpTo };

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

  /** Stop the story loop and sound. Does not restore the map (callers decide). */
  const haltStory = useCallback(() => {
    if (storyRef.current) cancelAnimationFrame(storyRef.current.raf);
    stopSpeaking();
    stopRiver();
    setStoryRunning(false);
    setStoryLoading(false);
    setCaption("");
    setRelief(false);
  }, []);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    haltStory();
    storyRef.current = null;
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
  }, [runId, reload, haltStory]);

  useEffect(() => () => { haltStory(); }, [haltStory]);

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

  // ---------- Story mode ----------
  function runAction(s: StoryState, a: StoryAction, t: number) {
    switch (a.kind) {
      case "camera":
        setCameraTarget({ ...a, key: ++camKey.current });
        break;
      case "fit":
        setCameraTarget({ fit: true, pitch: a.pitch, duration: a.duration, key: ++camKey.current });
        break;
      case "compare":
        setCompareEnabled(a.on);
        if (a.value != null) { s.cmp = a.value; setCompareValue(a.value); }
        break;
      case "compareFade":
        s.tweens.push({ from: s.cmp, to: a.to, t0: t, dur: a.dur });
        break;
      case "layer":
        setVisible((v) => ({ ...v, [a.name]: a.on }));
        break;
      case "relief":
        setRelief(a.on);
        break;
      case "flow":
        if (s.flow) setFlowResult(s.flow);   // FlowPanel plays the wave
        break;
      case "repairUpTo":
        setRepairUpTo(a.value);
        break;
    }
  }

  const endStory = useCallback(() => {
    const s = storyRef.current;
    haltStory();
    if (s) {
      setVisible(s.snap.visible);
      setCompareEnabled(s.snap.compareEnabled);
      setCompareValue(s.snap.compareValue);
      setRepairUpTo(s.snap.repairUpTo);
    }
    storyRef.current = null;
    setFlowResult(null);
    setFlowKm(null);
    setCameraTarget({ fit: true, pitch: 0, duration: 1200, key: ++camKey.current });
  }, [haltStory]);

  const startStory = useCallback(async () => {
    if (!stats || !run || !runId || storyLoading) return;
    // Sound must start inside the click
    if (storyRiver) startRiver();
    primeVoices();
    setStoryLoading(true);
    setSelected(null);
    setFlowMode(false);

    // Warm the image cache so "before" is ready when its step plays
    for (const n of ["s2_before", "s2_after"]) { const img = new Image(); img.src = imageUrl(runId, n); }

    const bbox = run.request?.bbox as number[];
    const located = villages.filter((v) => Number.isFinite(v._lon) && Number.isFinite(v._lat));
    const cut = located.filter((v) => v.status === "cut_off");
    const pool = cut.length ? cut : located;
    const top = pool.length ? pool.reduce((a, b) => (b._lat > a._lat ? b : a)) : null;
    const mid = cut.length
      ? { lon: cut.reduce((s, v) => s + v._lon, 0) / cut.length, lat: cut.reduce((s, v) => s + v._lat, 0) / cut.length }
      : { lon: (bbox[0] + bbox[2]) / 2, lat: (bbox[1] + bbox[3]) / 2 };
    const p1 = stats?.cutoff?.repair_plan?.[0];
    const repV = p1 ? located.find((v) => same(v.name, p1.near)) : null;

    let flow: any = null;
    if (top) {
      try { flow = await traceFlow(runId, top._lon, top._lat, 500, 25); } catch { flow = null; }
    }
    const end = flow?.path?.geometry?.coordinates?.slice(-1)?.[0];

    const { steps, total } = buildStory({
      date: run.request?.flood_date,
      stats,
      mid,
      top: top ? { lon: top._lon, lat: top._lat, name: top.name } : null,
      flowEnd: end ? { lon: end[0], lat: end[1] } : null,
      hasFlow: !!flow?.path,
      repairPt: repV ? { lon: repV._lon, lat: repV._lat } : null,
    });

    const s: StoryState = {
      start: performance.now(), steps, total, fired: new Set(), tweens: [], cmp: 0, flow, raf: 0, lastUi: 0,
      snap: { ...liveRef.current },
    };
    storyRef.current = s;
    setStoryLoading(false);
    setStoryProgress(0);
    setStoryRunning(true);

    const loop = (now: number) => {
      if (storyRef.current !== s) return;            // stopped or replaced
      const t = (now - s.start) / 1000;
      s.steps.forEach((st, i) => {
        if (s.fired.has(i) || t < st.at) return;
        s.fired.add(i);
        setCaption(st.caption);
        if (voiceRef.current) speak(st.caption);
        for (const a of st.actions ?? []) runAction(s, a, t);
      });
      s.tweens = s.tweens.filter((tw) => {
        const f = Math.min(1, (t - tw.t0) / tw.dur);
        s.cmp = tw.from + (tw.to - tw.from) * ease(f);
        setCompareValue(s.cmp);
        return f < 1;
      });
      if (now - s.lastUi > 100) { s.lastUi = now; setStoryProgress(t / s.total); }
      if (t >= s.total) { endStory(); return; }
      s.raf = requestAnimationFrame(loop);
    };
    s.raf = requestAnimationFrame(loop);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stats, run, runId, villages, storyRiver, storyLoading, endStory]);

  const stageIdx = ready ? 5 : Math.max(0, run?.stage_index ?? 0);

  return (
    <>
      <div className={`app ${storyRunning ? "cinema" : ""}`}>
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

          {ready && (
            <StoryPanel
              running={storyRunning}
              loading={storyLoading}
              voice={storyVoice}
              river={storyRiver}
              onToggleVoice={() => setStoryVoice((v) => !v)}
              onToggleRiver={() => setStoryRiver((v) => !v)}
              onStart={startStory}
              onStop={endStory}
            />
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
            flowMode={flowMode} onFlowResult={onFlowResult} flowKm={flowKm} flowData={flowResult}
            onVillageSelect={onSelect} highlight={highlight}
            whatIf={mapWhatIf}
            compareEnabled={compareEnabled} compareValue={compareValue}
            relief={relief} cameraTarget={cameraTarget}
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

      <StoryOverlay active={storyRunning} progress={storyProgress} caption={caption} onStop={endStory} />
    </>
  );
}