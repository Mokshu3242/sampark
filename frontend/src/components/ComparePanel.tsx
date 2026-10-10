import { useEffect, useRef, useState } from "react";

type Props = {
  enabled: boolean;
  onToggle: () => void;
  value: number; // 0 = before, 100 = after
  onChange: (v: number) => void;
};

const SWEEP_S = 4;

export default function ComparePanel({ enabled, onToggle, value, onChange }: Props) {
  const [playing, setPlaying] = useState(false);
  const valueRef = useRef(value);
  valueRef.current = value;

  // Play: sweep from the current value to 100% (or from 0 if already at the end)
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    let v = valueRef.current >= 100 ? 0 : valueRef.current;
    const step = (now: number) => {
      v = Math.min(100, v + ((now - last) / 1000) * (100 / SWEEP_S));
      last = now;
      onChange(v);
      if (v >= 100) { setPlaying(false); return; }
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing]);

  useEffect(() => { if (!enabled) setPlaying(false); }, [enabled]);

  return (
    <div className="panel">
      <h2>Compare before / after</h2>
      <label className="layer-row">
        <input type="checkbox" checked={enabled} onChange={onToggle} />
        Show Sentinel-2 imagery
      </label>

      {enabled && (
        <div className="swipe-wrap">
          <div className="swipe-labels">
            <span className={value < 50 ? "on" : ""}>Before</span>
            <span className="swipe-value">{Math.round(value)}%</span>
            <span className={value >= 50 ? "on" : ""}>After</span>
          </div>
          <input
            type="range" min={0} max={100} step={1}
            value={Math.round(value)}
            onChange={(e) => { setPlaying(false); onChange(Number(e.target.value)); }}
            className="swipe-slider"
          />
          <button className="swipe-play" onClick={() => setPlaying((p) => !p)}>
            {playing ? "Pause" : value >= 100 ? "Replay" : "Play"}
          </button>
          <p className="muted">
            Sentinel-2 false colour: green = vegetation, brown = bare ground or debris, dark = water.
            Clouds are masked and show as gaps. The first load takes a few seconds.
          </p>
        </div>
      )}
    </div>
  );
}