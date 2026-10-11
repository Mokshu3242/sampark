import { useEffect, useRef, useState } from "react";

/** Typical seconds per stage, measured on the GitHub runner. */
const STAGES = [
  { label: "Starting", s: 90 }, // queue + install
  { label: "Satellite data", s: 540 },
  { label: "Flood map", s: 60 },
  { label: "Damage", s: 30 },
  { label: "Cut-off routing", s: 80 }, // includes flow and map caches
];
const TOTAL = STAGES.reduce((a, b) => a + b.s, 0);

/** "20261011T072247-2acd36" -> ms since epoch (the ID is UTC). */
function startMs(runId: string): number | null {
  const m = runId.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})/);
  return m ? Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]) : null;
}

type Props = {
  runId: string;
  stageIndex: number; // 0..4 while running, 5 when done, -1 on failure
  status?: string;
  detail?: string;
  ready: boolean;
  stopped: boolean;
};

export default function RunProgress({
  runId,
  stageIndex,
  status,
  detail,
  ready,
  stopped,
}: Props) {
  const [now, setNow] = useState(Date.now());
  const sawRunning = useRef(false);
  if (!ready && !stopped) sawRunning.current = true;

  useEffect(() => {
    if (ready || stopped) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [ready, stopped]);

  // An old run opened from the list: no bar, just a line
  if (ready && !sawRunning.current)
    return <div className="status">Finished.</div>;

  const t0 = startMs(runId) ?? now;
  const elapsed = Math.max(0, (now - t0) / 1000);
  const i = Math.min(Math.max(stageIndex, 0), STAGES.length - 1);
  const cumStart = STAGES.slice(0, i).reduce((a, b) => a + b.s, 0);
  const cumEnd = cumStart + STAGES[i].s;

  // Follow the clock, but never past the real stage; jump ahead if a stage ends early
  const pos = ready ? TOTAL : Math.min(Math.max(elapsed, cumStart), cumEnd - 3);
  const frac = Math.min(1, pos / TOTAL);
  const late = !ready && !stopped && elapsed > cumEnd + 15;
  const left = Math.max(0, TOTAL - Math.max(elapsed, cumStart));

  let note: string;
  if (ready) note = "Done.";
  else if (stopped) note = status === "cancelled" ? "Stopped." : "Failed.";
  else if (status === "saving") note = "Saving results…";
  else if (late) note = "Taking a little longer than usual…";
  else if (left > 90) note = `About ${Math.round(left / 60)} min left`;
  else if (left > 30) note = "About a minute left";
  else note = "Almost done…";

  return (
    <div
      className={`run-progress ${ready ? "is-done" : ""} ${stopped ? "is-stopped" : ""}`}
    >
      <div
        className="rp-bar"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(frac * 100)}
      >
        <div className="rp-fill" style={{ width: `${frac * 100}%` }} />
      </div>
      <div className="rp-meta">
        <span>{note}</span>
        {!ready && !stopped && (
          <span className="rp-pct">{Math.round(frac * 100)}%</span>
        )}
      </div>
      <div className="progress">
        {STAGES.map((s, k) => (
          <div
            key={s.label}
            className={`step ${ready || k < i ? "done" : k === i && !stopped ? "active" : ""}`}
          >
            {s.label}
          </div>
        ))}
      </div>
      {detail && !ready && detail !== status && (
        <div className="status">{detail}</div>
      )}
    </div>
  );
}
