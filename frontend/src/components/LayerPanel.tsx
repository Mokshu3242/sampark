type Props = {
  visible: Record<string, boolean>;
  onToggle: (name: string) => void;
};

const LAYERS = [
  { id: "repair", label: "Repair priority", color: "#00e676" },
  { id: "flood", label: "Flood water", color: "#1565c0" },
  { id: "debris", label: "Debris / scour", color: "#ef6c00" },
  { id: "roads", label: "Roads (red = cut)", color: "#e53935" },
  { id: "villages", label: "Cut-off villages", color: "#d500f9" },
];

export default function LayerPanel({ visible, onToggle }: Props) {
  return (
    <div className="panel">
      <h2>Layers</h2>
      {LAYERS.map((l) => (
        <label key={l.id} className="layer-row">
          <input type="checkbox" checked={!!visible[l.id]} onChange={() => onToggle(l.id)} />
          <span className="swatch" style={{ background: l.color }} />
          {l.label}
        </label>
      ))}
    </div>
  );
}