type Props = {
  visible: Record<string, boolean>;
  compareEnabled: boolean;
  compareValue: number;
  story?: boolean;
};

/** Match these to the paint colours in MapView if yours differ. */
const ITEMS: { key: string; label: string; color: string; shape: "dot" | "line" | "area" }[] = [
  { key: "flood", label: "Flood water", color: "#1565c0", shape: "area" },
  { key: "debris", label: "Mud and debris", color: "#f57c00", shape: "area" },
  { key: "roads", label: "Road cut", color: "#e53935", shape: "line" },
  { key: "repair", label: "Road to reopen", color: "#22c55e", shape: "line" },
  { key: "villages", label: "Cut off by road", color: "#d500f9", shape: "dot" },
];

export default function MapLegend({ visible, compareEnabled, compareValue, story }: Props) {
  const shown = ITEMS.filter((it) => visible[it.key]);
  const unc = visible.uncertainty;
  if (!shown.length && !unc && !compareEnabled) return null;

  return (
    <div className={`map-legend ${story ? "in-story" : ""}`} aria-label="Map legend">
      {compareEnabled && (
        <div className="ml-row ml-sat">
          Satellite image: <b>{compareValue >= 50 ? "after the flood" : "before the flood"}</b>
        </div>
      )}
      {shown.map((it) => (
        <div className="ml-row" key={it.key}>
          <i className={`ml-key ml-${it.shape}`} style={{ background: it.color }} />
          {it.label}
        </div>
      ))}
      {visible.villages && (
        <div className="ml-row">
          <i className="ml-key ml-dot" style={{ background: "#f39c12" }} />
          Reachable, longer route
        </div>
      )}
      {unc && (
        <div className="ml-row ml-unc">
          <span>Confidence</span>
          <i style={{ background: "#27ae60" }} /> high
          <i style={{ background: "#f39c12" }} /> medium
          <i style={{ background: "#e74c3c" }} /> low
        </div>
      )}
    </div>
  );
}