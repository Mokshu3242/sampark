import { useMemo, useRef, useState } from "react";

type Props = {
  villages: any[];
  onSelect: (name: string) => void;
};

const STATUS_LABEL: Record<string, string> = {
  cut_off: "cut off", longer_route: "longer route", ok: "ok", pre_isolated: "no road",
};
const RANK: Record<string, number> = { cut_off: 0, longer_route: 1, ok: 2, pre_isolated: 3 };

const display = (n: any) => (String(n ?? "").startsWith("unnamed") ? "Unnamed hamlet" : String(n ?? ""));

export default function VillageSearch({ villages, onSelect }: Props) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const blurTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const matches = useMemo(() => {
    const t = q.trim().toLowerCase();
    if (!t) return [];
    return villages
      .filter((v) => v.name && !String(v.name).startsWith("unnamed") && String(v.name).toLowerCase().includes(t))
      .sort((a, b) => {
        const starts = (v: any) => (String(v.name).toLowerCase().startsWith(t) ? 0 : 1);
        return starts(a) - starts(b)
          || (RANK[a.status] ?? 9) - (RANK[b.status] ?? 9)
          || (b.buildings_total || 0) - (a.buildings_total || 0);
      })
      .slice(0, 12);
  }, [q, villages]);

  function pick(name: string) {
    onSelect(name);
    setQ("");
    setOpen(false);
  }

  return (
    <div className="panel">
      <h2>Find a village</h2>
      <div className="vsearch">
        <input
          type="text"
          value={q}
          placeholder="Type a name…"
          onChange={(e) => { setQ(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onBlur={() => { blurTimer.current = setTimeout(() => setOpen(false), 150); }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && matches.length > 0) pick(matches[0].name);
            if (e.key === "Escape") { setQ(""); setOpen(false); }
          }}
        />
        {open && q.trim() && (
          <ul className="vsearch-list" onMouseDown={() => blurTimer.current && clearTimeout(blurTimer.current)}>
            {matches.length === 0 && <li className="empty">No village with that name in this run</li>}
            {matches.map((v, i) => (
              <li key={`${v.name}-${i}`} onClick={() => pick(v.name)}>
                <span className="vn">{display(v.name)}</span>
                <span className={`vm s-${v.status}`}>
                  {STATUS_LABEL[v.status] ?? v.status} · {(v.buildings_total ?? 0).toLocaleString()} bldg
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}