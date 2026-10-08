import { useState } from "react";
import { askCopilot } from "../api/client";

type Msg = { role: "user" | "bot"; text: string; sources?: any[]; provider?: string };

const SUGGESTIONS = [
  "How many villages are cut off?",
  "Which road should we reopen first?",
  "कति गाउँ सम्पर्कविहीन छन्?",
];

export default function ChatBox({ runId }: { runId: string | null }) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [lang, setLang] = useState<"auto" | "en" | "ne">("auto");

  async function send(q = input.trim()) {
    if (!q || !runId) return;
    setInput("");
    setMsgs((m) => [...m, { role: "user", text: q }]);
    setBusy(true);
    try {
      const history = msgs.map((m) => ({ role: m.role === "user" ? "user" : "assistant", content: m.text }));
      const r = await askCopilot(runId, q, lang, history);
      setMsgs((m) => [...m, { role: "bot", text: r.answer, sources: r.sources, provider: r.provider }]);
    } catch (e: any) {
      setMsgs((m) => [...m, { role: "bot", text: `Error: ${e.message || e}` }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel chat">
      <h2>Ask the copilot</h2>
      <div className="msgs">
        {msgs.length === 0 && (
          <div>
            <p className="muted">{runId ? "Every number is checked against the maps. Try:" : "Open a finished run first."}</p>
            {runId && SUGGESTIONS.map((s) => (
              <button key={s} className="suggest" onClick={() => send(s)} disabled={busy}>{s}</button>
            ))}
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div>{m.text}</div>
            {m.sources && m.sources.length > 0 && (
              <details>
                <summary>{m.sources.length} numbers traced to data{m.provider ? ` · ${m.provider}` : ""}</summary>
                <ul>{m.sources.map((s, j) => <li key={j}>{s.number} ← {s.source}</li>)}</ul>
              </details>
            )}
          </div>
        ))}
        {busy && <div className="msg bot muted">Thinking…</div>}
      </div>
      <div className="chat-controls">
        <select value={lang} onChange={(e) => setLang(e.target.value as any)}>
          <option value="auto">Auto</option>
          <option value="en">English</option>
          <option value="ne">नेपाली</option>
        </select>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="Ask a question…"
          disabled={!runId || busy}
        />
        <button onClick={() => send()} disabled={!runId || busy || !input.trim()}>Send</button>
      </div>
    </div>
  );
}