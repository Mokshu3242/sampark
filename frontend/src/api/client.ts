const API = import.meta.env.VITE_API_URL || "http://localhost:8000";

export type RunInfo = {
  run_id: string;
  status: string;
  stage?: string;
  stage_index?: number;
  stage_total?: number;
  detail?: string;
  ready?: boolean;
  request?: { name: string; bbox: number[]; flood_date: string };
  quality?: any;
  flood_date?: string;
};

async function j(r: Response) {
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
}

export const createRun = (bbox: number[], flood_date: string, name = "dashboard") =>
  fetch(`${API}/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, bbox, flood_date, window_days: 30 }),
  }).then(j) as Promise<{ run_id: string }>;

export const listRuns = () => fetch(`${API}/runs`).then(j).then((d) => d.runs as RunInfo[]);
export const getRun = (id: string) => fetch(`${API}/runs/${id}`).then(j) as Promise<RunInfo>;
export const getStats = (id: string) => fetch(`${API}/runs/${id}/stats`).then(j);

export const askCopilot = (id: string, question: string, language = "auto", history: any[] = []) =>
  fetch(`${API}/runs/${id}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, language, history }),
  }).then(j);

export const layerUrl = (id: string, name: string) => `${API}/runs/${id}/layers/${name}`;
export const reportUrl = (id: string, lang: "en" | "ne") => `${API}/runs/${id}/report?lang=${lang}`;
export const exportUrl = (id: string) => `${API}/runs/${id}/export`;

export const PRESETS = [
  { label: "Bhote Koshi–Trishuli", bbox: [85.15, 27.9, 85.45, 28.3], date: "2026-08-26" },
  { label: "Lower Trishuli", bbox: [85.1, 27.85, 85.5, 28.1], date: "2026-08-26" },
  { label: "Rasuwagadhi", bbox: [85.28, 28.1, 85.42, 28.3], date: "2026-08-26" },
];

export async function getEval(id: string): Promise<any | null> {
  const r = await fetch(`${API}/runs/${id}/eval`);
  return r.ok ? r.json() : null;
}

export async function traceFlow(runId: string, lon: number, lat: number, bufferM = 500, snapPx = 5): Promise<any> {
  const r = await fetch(`${API}/runs/${runId}/flowpath`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lon, lat, buffer_m: bufferM, snap_px: snapPx }),
  });
  if (!r.ok) {
    let msg = await r.text();
    try { msg = JSON.parse(msg).detail || msg; } catch { /* plain text */ }
    throw new Error(msg);
  }
  return r.json();
}

export async function imageBounds(runId: string, name: string, width = 1400): Promise<number[][]> {
  const r = await fetch(`${API}/runs/${runId}/image/${name}/bounds?width=${width}`);
  if (!r.ok) throw new Error(await r.text());
  return (await r.json()).coordinates;
}

export function imageUrl(runId: string, name: string, width = 1400): string {
  return `${API}/runs/${runId}/image/${name}.png?width=${width}`;
}

export function chipUrl(runId: string, name: "s2_before" | "s2_after", lon: number, lat: number, sizeM = 2000): string {
  return `${API}/runs/${runId}/chip/${name}.png?lon=${lon.toFixed(5)}&lat=${lat.toFixed(5)}&size_m=${sizeM}&px=240`;
}

export function villageNamesFrom(field: any): string[] {
  if (!field) return [];
  if (Array.isArray(field)) return field.map((s) => String(s).trim()).filter(Boolean);
  return String(field).split(",").map((s) => s.trim()).filter(Boolean);
}

export async function cancelRun(runId: string) {
  const r = await fetch(`${API}/runs/${runId}/cancel`, { method: "POST" });
  if (!r.ok) throw new Error((await r.json()).detail || "Stop failed");
  return r.json();
}

export async function deleteRun(runId: string) {
  const r = await fetch(`${API}/runs/${runId}`, { method: "DELETE" });
  if (!r.ok) throw new Error((await r.json()).detail || "Delete failed");
  return r.json();
}