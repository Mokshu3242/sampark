type Ha = { low?: number; medium?: number; high?: number } | null | undefined;

type Props = {
  visible: Record<string, boolean>;
  onToggle: (name: string) => void;
  uncertaintyHa?: Ha;
};

const LAYERS: { key: string; label: string; color: string }[] = [
  { key: "repair", label: "Repair priority", color: "#00e676" },
  { key: "flood", label: "Flood water", color: "#1565c0" },
  { key: "debris", label: "Debris / scour", color: "#ef6c00" },
  { key: "roads", label: "Roads (red = cut)", color: "#e53935" },
  { key: "villages", label: "Cut-off villages", color: "#d500f9" },
  { key: "uncertainty", label: "Uncertainty", color: "linear-gradient(90deg,#27ae60,#f39c12,#e74c3c)" },
];

const UNC = [
  { k: "low", label: "Low (confident)", color: "#27ae60" },
  { k: "medium", label: "Medium", color: "#f39c12" },
  { k: "high", label: "High (check first)", color: "#e74c3c" },
] as const;

export default function LayerPanel({ visible, onToggle, uncertaintyHa }: Props) {
  const ha = uncertaintyHa ?? null;
  const total = ha ? (ha.low ?? 0) + (ha.medium ?? 0) + (ha.high ?? 0) : 0;

  return (
    <div className="panel">
      <h2>Layers</h2>
      {LAYERS.map((l) => (
        <label key={l.key} className="layer-row">
          <input type="checkbox" checked={!!visible[l.key]} onChange={() => onToggle(l.key)} />
          <span className="swatch" style={{ background: l.color }} />
          {l.label}
        </label>
      ))}

      {visible.uncertainty && (
        <div className="unc-legend">
          {total > 0 && ha ? (
            <>
              {UNC.map((u) => {
                const v = ha[u.k] ?? 0;
                return (
                  <div key={u.k} className="unc-row">
                    <span className="swatch" style={{ background: u.color }} />
                    <span>{u.label}</span>
                    <b>{v.toFixed(1)} ha <small>({Math.round((100 * v) / total)}%)</small></b>
                  </div>
                );
              })}
              <p className="muted">
                How sure the map is about each detected flood or debris patch. Red patches are the
                ones to check on the ground or in other imagery first.
              </p>
            </>
          ) : (
            <p className="muted">Not available for this run.</p>
          )}
        </div>
      )}
    </div>
  );
}