import { useEffect, useState } from "react";
import { getAccessKey, setAccessKey, verifyAccessKey } from "../api/client";

export default function UnlockPanel() {
  const [unlocked, setUnlocked] = useState(!!getAccessKey());
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const on = () => setUnlocked(!!getAccessKey());
    window.addEventListener("access-changed", on);
    return () => window.removeEventListener("access-changed", on);
  }, []);

  async function unlock() {
    if (!value.trim()) return;
    setBusy(true);
    setError(null);
    try {
      if (await verifyAccessKey(value.trim())) {
        setAccessKey(value.trim());
        setValue("");
      } else {
        setError("Wrong key.");
      }
    } catch {
      setError("Server not reachable. It may be waking up, try again in a minute.");
    } finally {
      setBusy(false);
    }
  }

  if (unlocked) {
    return (
      <div className="panel unlock ok">
        <span>🔓 Unlocked: new runs and copilot are on.</span>
        <button className="link" onClick={() => setAccessKey(null)}>Lock</button>
      </div>
    );
  }

  return (
    <div className="panel unlock">
      <h2>Unlock</h2>
      <p className="muted">Browsing is open. New runs, the copilot, and delete need the access key from the README.</p>
      <div className="btn-row">
        <input type="password" placeholder="Access key" value={value}
               onChange={(e) => setValue(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && unlock()} disabled={busy} />
        <button onClick={unlock} disabled={busy || !value.trim()}>{busy ? "Checking…" : "Unlock"}</button>
      </div>
      {error && <div className="error">{error}</div>}
    </div>
  );
}