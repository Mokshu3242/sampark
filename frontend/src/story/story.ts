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

/** 1078 -> "1,078" (voices read commas as thousands). */
const n = (v: any) => Math.round(Number(v ?? 0)).toLocaleString("en-US");
/** 0.934 -> "93 percent" (spelled out so the voice reads it right). */
const pct = (v: any) => (typeof v === "number" ? `${Math.round(v * 100)} percent` : null);
/** count + noun with the right plural: "1 village", "3 villages". */
const count = (v: any, one: string, many = `${one}s`) => {
  const k = Math.round(Number(v ?? 0));
  return `${n(k)} ${k === 1 ? one : many}`;
};
const has = (v: any) => Math.round(Number(v ?? 0)) > 0;

/** "a, b and c" from a list of phrases (skips empty ones). */
function joinAnd(parts: (string | null | false | undefined)[]): string {
  const p = parts.filter(Boolean) as string[];
  if (p.length <= 1) return p[0] ?? "";
  return `${p.slice(0, -1).join(", ")} and ${p[p.length - 1]}`;
}

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

  // 1. Where and when
  steps.push({
    at: 0,
    caption: `This is the flood of ${niceDate(o.date)} in central Nepal, seen from space.`,
    actions: [
      ...allLayers.map((name) => ({ kind: "layer" as const, name, on: false })),
      { kind: "compare", on: true, value: 0 },
      { kind: "relief", on: true },
      { kind: "fit", pitch: 0, duration: 1500 },
    ],
  });

  // 2. Before
  steps.push({
    at: 5,
    caption: "First, the valley before the flood. In this satellite view, green is forest and fields, and dark areas are water.",
    actions: [{ kind: "camera", ...o.mid, zoom: 11, pitch: 50, bearing: -15, duration: 4000 }],
  });

  // 3. After
  steps.push({
    at: 12,
    caption: "Now the same valley after the flood. The new brown scars along the river are mud and rock left behind by the water.",
    actions: [{ kind: "compareFade", to: 100, dur: 6 }],
  });

  // 4. What the radar measured
  const debris = has(f.debris_area_ha) ? `${n(f.debris_area_ha)} hectares of mud and rock` : null;
  const water = has(f.flood_area_ha) ? `${n(f.flood_area_ha)} hectares of standing water` : null;
  steps.push({
    at: 20,
    caption: debris || water
      ? `Radar images, which see through cloud, map ${joinAnd([debris, water])}.`
      : "Radar images, which see through cloud, show where the river changed its course.",
    actions: [{ kind: "layer", name: "debris", on: true }, { kind: "layer", name: "flood", on: true }],
  });

  // 5. What it means on the ground
  const losses = joinAnd([
    has(d.buildings_affected) && `${count(d.buildings_affected, "building")} in the damage zone`,
    has(d.roads_cut) && `${count(d.roads_cut, "road section")} cut`,
    has(d.bridges_lost) && `${count(d.bridges_lost, "bridge")} lost`,
  ]);
  steps.push({
    at: 27,
    caption: losses
      ? `On the ground, that means ${losses}.`
      : "On the ground, roads and buildings close to the river took the hardest hit.",
    actions: [
      { kind: "layer", name: "roads", on: true },
      { kind: "camera", ...o.mid, zoom: 11.3, pitch: 55, bearing: 10, duration: 3000 },
    ],
  });

  // 6. Follow the water
  if (o.hasFlow && o.top) {
    steps.push({
      at: 34,
      caption: `Let's follow the water downstream from ${o.top.name}.`,
      actions: [
        { kind: "layer", name: "villages", on: true },
        { kind: "camera", lon: o.top.lon, lat: o.top.lat, zoom: 12, pitch: 60, bearing: 0, duration: 2000 },
        { kind: "flow" },
      ],
    });
    if (o.flowEnd)
      steps.push({
        at: 38,
        caption: "It rushed down the valley, past the villages and roads in its path.",
        actions: [{ kind: "camera", ...o.flowEnd, zoom: 11.5, pitch: 55, bearing: 0, duration: 22000, linear: true }],
      });
  } else {
    steps.push({
      at: 34,
      caption: "Here are the villages along the river, and the roads that link them.",
      actions: [{ kind: "layer", name: "villages", on: true }],
    });
  }

  // 7. Who is cut off
  const longer = has(c.longer_route)
    ? ` ${count(c.longer_route, "more village", "more villages")} can only be reached the long way round.`
    : "";
  steps.push({
    at: 46,
    caption: has(c.cut_off)
      ? `With these roads gone, ${count(c.cut_off, "village")} can no longer be reached by road.${longer}`
      : `Luckily, no village is fully cut off by road.${longer}`,
  });

  // 8. Where to start repairs
  if (p1) {
    const near = p1.near ? ` near ${p1.near}` : "";
    steps.push({
      at: 60,
      caption: `So where should repair crews start? The first priority is ${n(p1.length_m)} metres of road${near}. Fixing just that reconnects ${count(p1.villages_count, "village")} and ${count(p1.buildings_reconnected, "building")}.`,
      actions: [
        { kind: "layer", name: "repair", on: true },
        { kind: "repairUpTo", value: 1 },
        ...(o.repairPt ? [{ kind: "camera" as const, ...o.repairPt, zoom: 13, pitch: 45, bearing: 0, duration: 3000 }] : []),
      ],
    });

    if (plan.length > 1) {
      const totalM = plan.reduce((s, p) => s + (p.length_m || 0), 0);
      const vSum = plan.reduce((s, p) => s + (p.villages_count || 0), 0);
      const br = plan.filter((p) => p.has_bridge).length;
      const work = br ? `${n(totalM)} metres of road and ${count(br, "bridge")}` : `${n(totalM)} metres of road`;
      steps.push({
        at: 68,
        caption: `Doing all ${plan.length} repairs, ${work}, would reconnect ${count(vSum, "village")}.`,
        actions: [{ kind: "repairUpTo", value: plan.length }, { kind: "fit", pitch: 30, duration: 3000 }],
      });
    }
  }

  // 9. How good is the map
  const found = pct(ev?.buildings?.by_class?.Destroyed?.recall_with_tolerance);
  const prec = pct(ev?.ours_flood_plus_debris?.precision_tolerant);
  if (found)
    steps.push({
      at: 75,
      caption: `We checked this map against the official Copernicus emergency map. It found ${found} of the buildings marked as destroyed${prec ? `, and ${prec} of the damage areas we mapped match theirs` : ""}.`,
    });

  // 10. Honest ending
  steps.push({
    at: 81,
    caption: "Sampark is an educational prototype. These are satellite estimates, not yet checked on the ground.",
    actions: [{ kind: "fit", pitch: 0, duration: 2500 }],
  });

  return { steps, total: 87 };
}