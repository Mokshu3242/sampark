import { useEffect, useState } from "react";
import { createRun, deleteRun, listRuns, PRESETS, type RunInfo } from "../api/client";

type Props = {
  onSelect: (runId: string) => void;
  onDeleted?: (runId: string) => void;
  disabled?: boolean;
};

const floodDateOf = (r: RunInfo): string | undefined =>
  (r as any).flood_date ?? (r as any).request?.flood_date;

/** "Flood 26 Aug 2026 · made 10 Oct 22:42" (made time in local time, read from the run ID). */
function runLabel(r: RunInfo) {
  const m = r.run_id.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})/);
  const made = m
    ? new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5])).toLocaleString("en-GB", {
        day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
      })
    : r.run_id;
  const fd = floodDateOf(r);
  const flood = fd
    ? new Date(fd + "T00:00:00Z").toLocaleDateString("en-GB", {
        day: "numeric", month: "short", year: "numeric", timeZone: "UTC",
      })
    : "?";
  const st = (r as any).status;
  const tag = r.ready ? "" : st === "failed" ? " · failed" : st === "cancelled" ? " · stopped" : " · running";
  return `Flood ${flood} · made ${made}${tag}`;
}

export default function RunForm({ onSelect, onDeleted, disabled }: Props) {
  const [bbox, setBbox] = useState<number[]>(PRESETS[0].bbox);
  const [date, setDate] = useState(PRESETS[0].date);
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [pick, setPick] = useState("");

  useEffect(() => {
    listRuns().then((r) => {
      setRuns(r);
      setPick((cur) => (r.some((x) => x.run_id === cur) ? cur : r[0]?.run_id || ""));
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

  async function remove() {
    if (!pick || !confirm("Delete this run everywhere? This cannot be undone.")) return;
    setDeleting(true);
    setError(null);
    try {
      await deleteRun(pick);
      const left = runs.filter((r) => r.run_id !== pick);
      setRuns(left);
      onDeleted?.(pick);
      setPick(left[0]?.run_id || "");
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setDeleting(false);
    }
  }

  return (
    <>
      {runs.length > 0 && (
        <div className="panel">
          <h2>Open existing run</h2>
          <select value={pick} onChange={(e) => setPick(e.target.value)} disabled={disabled}>
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id} title={r.run_id}>{runLabel(r)}</option>
            ))}
          </select>
          <div className="btn-row">
            <button onClick={() => pick && onSelect(pick)} disabled={disabled || !pick}>Open</button>
            <button className="danger" onClick={remove} disabled={disabled || deleting || !pick}>
              {deleting ? "Deleting…" : "Delete"}
            </button>
          </div>
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