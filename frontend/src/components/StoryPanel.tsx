type Props = {
  running: boolean;
  loading: boolean;
  voice: boolean;
  river: boolean;
  onToggleVoice: () => void;
  onToggleRiver: () => void;
  onStart: () => void;
  onStop: () => void;
};

export default function StoryPanel({ running, loading, voice, river, onToggleVoice, onToggleRiver, onStart, onStop }: Props) {
  return (
    <div className="panel">
      <h2>Story mode</h2>
      <p className="muted">
        A guided flyover of about 90 seconds: before and after, the damage, the flow downstream, and
        the repair plan. Captions work with the sound off.
      </p>
      <label className="layer-row">
        <input type="checkbox" checked={voice} onChange={onToggleVoice} />
        Voice narration
      </label>
      <label className="layer-row">
        <input type="checkbox" checked={river} onChange={onToggleRiver} />
        River sound
      </label>
      <button onClick={running ? onStop : onStart} disabled={loading}>
        {loading ? "Preparing…" : running ? "Stop" : "Start story"}
      </button>
    </div>
  );
}