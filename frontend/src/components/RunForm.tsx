import { useEffect, useState } from "react";
import { createRun, listRuns, PRESETS, type RunInfo } from "../api/client";

type Props = { onSelect: (runId: string) => void; disabled?: boolean };

export default function RunForm({ onSelect, disabled }: Props) {
  const [bbox, setBbox] = useState<number[]>(PRESETS[0].bbox);
  const [date, setDate] = useState(PRESETS[0].date);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [pick, setPick] = useState("");

  useEffect(() => {
    listRuns().then((r) => {
      const ready = r.filter((x) => x.ready);
      setRuns(ready);
      if (ready.length) setPick(ready[0].run_id);
    }).catch(() => {});
  }, [disabled]);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const run = await createRun(bbox, date);
      onSelect(run.run_id);
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {runs.length > 0 && (
        <div className="panel">
          <h2>Open existing run</h2>
          <select value={pick} onChange={(e) => setPick(e.target.value)} disabled={disabled}>
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>{r.run_id} · {r.flood_date}</option>
            ))}
          </select>
          <button onClick={() => pick && onSelect(pick)} disabled={disabled || !pick}>Open</button>
        </div>
      )}

      <div className="panel">
        <h2>New run</h2>
        <label>Preset</label>
        <select onChange={(e) => { const p = PRESETS[+e.target.value]; setBbox(p.bbox); setDate(p.date); }}
                disabled={disabled || busy}>
          {PRESETS.map((p, i) => <option key={i} value={i}>{p.label}</option>)}
        </select>

        <label>Area [West, South, East, North] (max 0.6° per side)</label>
        <div className="bbox-row">
          {bbox.map((v, i) => (
            <input key={i} type="number" step="0.01" value={v} disabled={disabled || busy}
                   onChange={(e) => { const b = [...bbox]; b[i] = Number(e.target.value); setBbox(b); }} />
          ))}
        </div>

        <label>Flood date</label>
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} disabled={disabled || busy} />

        <button onClick={submit} disabled={disabled || busy}>{busy ? "Starting…" : "Run pipeline"}</button>
        <p className="muted">A full run takes about 15 minutes (satellite processing).</p>
        {error && <div className="error">{error}</div>}
      </div>
    </>
  );
}