import base64
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Template

from copilot import report_map

log = logging.getLogger("flood-watch.report")

ATTRIBUTION = [
    "Contains modified Copernicus Sentinel data 2026.",
    "Produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH "
    "2014–2018 provided under COPERNICUS by the European Union and ESA; all rights reserved.",
    "© OpenStreetMap contributors.",
]

TEMPLATE = """<!doctype html><html><head><meta charset="utf-8"><title>{{ t.title }}</title>
<style>
@page { size: A4; margin: 12mm; }
body { font-family: "Noto Sans", "Noto Sans Devanagari", "Nirmala UI", Arial, sans-serif; font-size: 9pt; line-height: 1.35; color: #111; max-width: 190mm; margin: auto; }
h1 { font-size: 14pt; margin: 0 0 1mm; } h2 { font-size: 10.5pt; margin: 3mm 0 1mm; }
.meta { color: #555; font-size: 7.5pt; margin-bottom: 2mm; }
.grid { display: flex; gap: 4mm; } .col { flex: 1; }
table { width: 100%; border-collapse: collapse; font-size: 8.5pt; }
th, td { border: 1px solid #ccc; padding: 1mm 1.5mm; text-align: left; } th { background: #f2f2f2; }
.num { text-align: right; } .key { font-size: 11pt; font-weight: bold; background: #fff3cd; padding: 2mm; border-left: 3px solid #e0a800; margin: 2mm 0; }
.note { font-size: 7.5pt; color: #444; background: #f7f7f7; padding: 1.5mm 2mm; margin-top: 2mm; }
.foot { font-size: 6.5pt; color: #666; margin-top: 2mm; }
img { width: 100%; }
</style></head><body>
<h1>{{ t.title }}</h1>
<div class="meta">{{ t.flood_date }}: {{ req.flood_date }} · {{ t.radar }}: {{ q.s1.before_date }} → {{ q.s1.after_date }} · {{ t.quality }}: {{ q.badge }} · {{ t.generated }}: {{ generated }}</div>
{% if plan %}<div class="key">{{ t.key_action }}: {{ plan[0].near }}, {{ plan[0].length_m|int }} m → {{ plan[0].villages_count }} {{ t.villages }}, {{ plan[0].buildings_reconnected }} {{ t.buildings }}</div>{% endif %}
<div class="grid">
 <div class="col">{% if map_b64 %}<img src="data:image/png;base64,{{ map_b64 }}">{% endif %}
  <div class="note">{{ t.legend }}</div></div>
 <div class="col">
  <h2>{{ t.cutoff }}</h2><table>
   <tr><td>{{ t.cut_off }}</td><td class="num">{{ c.cut_off }}</td></tr>
   <tr><td>{{ t.longer_route }}</td><td class="num">{{ c.longer_route }}</td></tr>
   <tr><td>{{ t.pre_isolated }}</td><td class="num">{{ c.pre_isolated }}</td></tr></table>
  <h2>{{ t.damage }}</h2><table>
   <tr><td>{{ t.buildings_affected }}</td><td class="num">{{ d.buildings_affected }}</td></tr>
   <tr><td>{{ t.roads_cut }}</td><td class="num">{{ d.roads_cut }}</td></tr>
   <tr><td>{{ t.bridges_lost }}</td><td class="num">{{ d.bridges_lost }} / {{ d.bridges_total }}</td></tr>
   <tr><td>{{ t.facilities }}</td><td class="num">{{ d.facilities_affected }}</td></tr>
   <tr><td>{{ t.debris }}</td><td class="num">{{ f.debris_area_ha }} ha</td></tr>
   <tr><td>{{ t.flood }}</td><td class="num">{{ f.flood_area_ha }} ha</td></tr></table>
 </div></div>
<h2>{{ t.repair }}</h2><table>
 <tr><th>#</th><th>{{ t.near }}</th><th>{{ t.road }}</th><th class="num">m</th><th class="num">{{ t.villages }}</th><th class="num">{{ t.buildings }}</th></tr>
 {% for p in plan %}<tr><td>{{ p.rank }}</td><td>{{ p.near }}</td><td>{{ p.road or "-" }}</td><td class="num">{{ p.length_m|int }}</td><td class="num">{{ p.villages_count }}</td><td class="num">{{ p.buildings_reconnected }}</td></tr>{% endfor %}
</table>
<h2>{{ t.top_villages }}</h2><table>
 <tr><th>{{ t.village }}</th><th class="num">{{ t.buildings }}</th><th class="num">{{ t.before_km }}</th></tr>
 {% for v in villages %}<tr><td>{{ v.name }}</td><td class="num">{{ v.buildings_total }}</td><td class="num">{{ v.dist_before_km if v.dist_before_km is not none else "-" }}</td></tr>{% endfor %}
</table>
<div class="note"><b>{{ t.limits }}:</b> {{ t.limits_body }}</div>
<div class="foot">{% for a in attribution %}{{ a }}<br>{% endfor %}{{ t.prototype }}</div>
</body></html>"""

LABELS = {
    "en": dict(
        title="Flood Situation Report: Bhote Koshi–Trishuli", flood_date="Flood date", radar="Radar images",
        quality="Data quality", generated="Generated", key_action="Reopen first",
        villages="villages", buildings="buildings",
        legend="Orange: debris and scour. Blue: flood water. Red: cut roads. Magenta: cut-off villages. Green numbers: repair order.",
        cutoff="Road access", cut_off="Villages cut off", longer_route="Longer route only",
        pre_isolated="No vehicle road before the flood", damage="Damage",
        buildings_affected="Buildings likely affected", roads_cut="Road segments cut",
        bridges_lost="Bridges likely lost", facilities="Health or school facilities affected",
        debris="Debris / scour area", flood="Open flood water", repair="Repair priority",
        near="Near", road="Road", top_villages="Largest cut-off villages", village="Village",
        before_km="Km to help before", limits="Limitations",
        badge={"good": "good", "fair": "fair", "poor": "poor"},
        limits_body="Satellite-based estimate, not field-verified. Vehicle roads only; footpaths not modeled. "
                    "Radar images are 10 days before and 2 days after the event. Building counts use a 20 m tolerance.",
        prototype="Educational prototype. Not for operational use."),
    "ne": dict(
        title="बाढी स्थिति प्रतिवेदन: भोटेकोशी–त्रिशूली", flood_date="बाढी मिति", radar="राडार तस्बिर",
        quality="डाटा गुणस्तर", generated="तयार गरिएको", key_action="पहिले खोल्नुपर्ने",
        villages="गाउँ", buildings="भवन",
        legend="सुन्तला: मलबा र कटान। निलो: बाढीको पानी। रातो: अवरुद्ध सडक। गुलाबी: सम्पर्कविहीन गाउँ। हरियो अंक: मर्मत क्रम।",
        cutoff="सडक पहुँच", cut_off="सम्पर्कविहीन गाउँ", longer_route="लामो बाटो मात्र",
        pre_isolated="बाढी अघि पनि सवारी सडक नभएको", damage="क्षति",
        buildings_affected="प्रभावित हुन सक्ने भवन", roads_cut="अवरुद्ध सडक खण्ड",
           badge={"good": "राम्रो", "fair": "ठीकै", "poor": "कमजोर"},
        bridges_lost="क्षतिग्रस्त हुन सक्ने पुल", facilities="प्रभावित स्वास्थ्य वा विद्यालय",
        debris="मलबा / कटान क्षेत्र", flood="बाढीको पानी", repair="मर्मत प्राथमिकता",
        near="नजिक", road="सडक", top_villages="ठूला सम्पर्कविहीन गाउँ", village="गाउँ",
        before_km="पहिले सहयोगसम्म किमी", limits="सीमितता",
        limits_body="यो भूउपग्रहमा आधारित अनुमान हो, स्थलगत रूपमा पुष्टि गरिएको छैन। सवारी सडक मात्र समावेश छ, पैदल बाटो छैन।",
        prototype="शैक्षिक प्रोटोटाइप। सञ्चालनका लागि होइन।"),
}


def generate(run_id: str, run_dir: Path, language: str = "en") -> dict:
    """Always writes HTML. Also writes PDF if WeasyPrint is installed (Docker). Numbers come from files, not the LLM."""
    load = lambda n: json.loads((run_dir / n).read_text(encoding="utf-8"))
    cutoff = load("cutoff_stats.json")
    try:
        map_b64 = base64.b64encode(report_map.render(run_dir).read_bytes()).decode()
    except Exception as e:
        log.warning(f"Map render failed: {e}")
        map_b64 = None

    html = Template(TEMPLATE).render(
        t=LABELS[language], req=load("request.json"), q=load("quality.json"),
        f=load("flood_stats.json"), d=load("damage_stats.json"), c=cutoff,
        plan=cutoff.get("repair_plan", []),
        villages=[r for r in cutoff.get("ranked", []) if r["status"] == "cut_off"][:6],
        map_b64=map_b64, attribution=ATTRIBUTION,
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )
    html_path = run_dir / f"report_{language}.html"
    html_path.write_text(html, encoding="utf-8")
    out = {"html": html_path, "pdf": None}

    try:
        from weasyprint import HTML  # only available in Docker
        pdf_path = run_dir / f"report_{language}.pdf"
        HTML(string=html).write_pdf(pdf_path)
        out["pdf"] = pdf_path
    except Exception as e:
        log.info(f"PDF skipped ({type(e).__name__}). HTML report is ready; print it to PDF from a browser.")
    return out