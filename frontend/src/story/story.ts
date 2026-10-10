export type StoryAction =
  | { kind: "camera"; lon: number; lat: number; zoom: number; pitch: number; bearing?: number; duration: number; linear?: boolean }
  | { kind: "fit"; pitch: number; duration: number }
  | { kind: "compare"; on: boolean; value?: number }
  | { kind: "compareFade"; to: number; dur: number }   // dur in seconds
  | { kind: "layer"; name: string; on: boolean }
  | { kind: "relief"; on: boolean }
  | { kind: "flow" }                                   // show the pre-traced wave
  | { kind: "repairUpTo"; value: number };

export type StoryStep = { at: number; caption: string; actions?: StoryAction[] };

type Pt = { lon: number; lat: number };

const n = (v: any) => Number(v ?? 0).toLocaleString();
const pct = (v: any) => (typeof v === "number" ? `${Math.round(v * 100)}%` : null);

function niceDate(iso?: string): string {
  if (!iso) return "this flood";
  const d = new Date(`${iso}T00:00:00Z`);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
}

/** Build the timeline from this run's real numbers. Returns steps and total length (s). */
export function buildStory(o: {
  date?: string;
  stats: any;
  mid: Pt;
  top: (Pt & { name: string }) | null;
  flowEnd: Pt | null;
  hasFlow: boolean;
  repairPt: Pt | null;
}): { steps: StoryStep[]; total: number } {
  const f = o.stats?.flood ?? {};
  const d = o.stats?.damage ?? {};
  const c = o.stats?.cutoff ?? {};
  const ev = o.stats?.eval ?? null;
  const plan: any[] = c.repair_plan ?? [];
  const p1 = plan[0];

  const allLayers = ["debris", "flood", "roads", "villages", "repair", "uncertainty"];
  const steps: StoryStep[] = [];

  steps.push({
    at: 0,
    caption: `Satellite view of the flood of ${niceDate(o.date)}, central Nepal.`,
    actions: [
      ...allLayers.map((name) => ({ kind: "layer" as const, name, on: false })),
      { kind: "compare", on: true, value: 0 },
      { kind: "relief", on: true },
      { kind: "fit", pitch: 0, duration: 1500 },
    ],
  });
  steps.push({
    at: 5,
    caption: "Before the flood. Sentinel-2 false colour: green is vegetation, dark is water.",
    actions: [{ kind: "camera", ...o.mid, zoom: 11, pitch: 50, bearing: -15, duration: 4000 }],
  });
  steps.push({
    at: 12,
    caption: "After the flood. New brown areas along the river are fresh debris and scour.",
    actions: [{ kind: "compareFade", to: 100, dur: 6 }],
  });
  steps.push({
    at: 20,
    caption: `Radar change detection maps about ${n(Math.round(f.debris_area_ha))} ha of debris and scour, and ${n(Math.round(f.flood_area_ha))} ha of open water.`,
    actions: [{ kind: "layer", name: "debris", on: true }, { kind: "layer", name: "flood", on: true }],
  });
  steps.push({
    at: 27,
    caption: `${n(d.buildings_affected)} buildings affected. ${n(d.roads_cut)} road segments cut. ${n(d.bridges_lost)} bridges lost.`,
    actions: [
      { kind: "layer", name: "roads", on: true },
      { kind: "camera", ...o.mid, zoom: 11.3, pitch: 55, bearing: 10, duration: 3000 },
    ],
  });

  if (o.hasFlow && o.top) {
    steps.push({
      at: 34,
      caption: `Following the flow downstream from ${o.top.name}.`,
      actions: [
        { kind: "layer", name: "villages", on: true },
        { kind: "camera", lon: o.top.lon, lat: o.top.lat, zoom: 12, pitch: 60, bearing: 0, duration: 2000 },
        { kind: "flow" },
      ],
    });
    if (o.flowEnd)
      steps.push({
        at: 36,
        caption: `Following the flow downstream from ${o.top.name}.`,
        actions: [{ kind: "camera", ...o.flowEnd, zoom: 11.5, pitch: 55, bearing: 0, duration: 22000, linear: true }],
      });
  } else {
    steps.push({ at: 34, caption: "Villages that lost their road link.", actions: [{ kind: "layer", name: "villages", on: true }] });
  }
  steps.push({
    at: 46,
    caption: `${n(c.cut_off)} villages are now cut off by road. ${n(c.longer_route)} more can only be reached by a longer route.`,
  });

  if (p1) {
    steps.push({
      at: 60,
      caption: `Priority #1: reopen ${n(Math.round(p1.length_m))} m near ${p1.near}. That alone reconnects ${n(p1.villages_count)} village${p1.villages_count === 1 ? "" : "s"} and ${n(p1.buildings_reconnected)} buildings.`,
      actions: [
        { kind: "layer", name: "repair", on: true },
        { kind: "repairUpTo", value: 1 },
        ...(o.repairPt ? [{ kind: "camera" as const, ...o.repairPt, zoom: 13, pitch: 45, bearing: 0, duration: 3000 }] : []),
      ],
    });
    const totalM = Math.round(plan.reduce((s, p) => s + (p.length_m || 0), 0));
    const vSum = plan.reduce((s, p) => s + (p.villages_count || 0), 0);
    const br = plan.filter((p) => p.has_bridge).length;
    steps.push({
      at: 68,
      caption: `All ${plan.length} repairs, ${n(totalM)} m of road${br ? ` and ${br} bridge${br === 1 ? "" : "s"}` : ""}, reconnect ${n(vSum)} villages.`,
      actions: [{ kind: "repairUpTo", value: plan.length }, { kind: "fit", pitch: 30, duration: 3000 }],
    });
  }

  const found = pct(ev?.buildings?.by_class?.Destroyed?.recall_with_tolerance);
  const prec = pct(ev?.ours_flood_plus_debris?.precision_tolerant);
  if (found)
    steps.push({
      at: 75,
      caption: `Checked against Copernicus EMS maps: ${found} of destroyed buildings found${prec ? `, with ${prec} precision on debris areas` : ""}.`,
    });

  steps.push({
    at: 81,
    caption: "Educational prototype. Satellite estimates, not field-verified.",
    actions: [{ kind: "fit", pitch: 0, duration: 2500 }],
  });

  return { steps, total: 87 };
}