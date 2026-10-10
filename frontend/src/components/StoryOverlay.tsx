import { useEffect } from "react";
import { createPortal } from "react-dom";

type Props = { active: boolean; progress: number; caption: string; onStop: () => void };

export default function StoryOverlay({ active, progress, caption, onStop }: Props) {
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onStop(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, onStop]);

  if (!active) return null;
  return createPortal(
    <div className="story-overlay">
      {caption && <div key={caption} className="story-caption">{caption}</div>}
      <div className="story-bottom">
        <div className="story-bar"><div className="story-fill" style={{ width: `${Math.min(100, progress * 100)}%` }} /></div>
        <button className="story-stop" onClick={onStop}>Stop (Esc)</button>
      </div>
    </div>,
    document.body,
  );
}