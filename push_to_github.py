
#!/usr/bin/env python3
"""
push_to_github.py  — CANONICAL VERSION
---------------------------------------
Generates data.json + index.html and pushes all PDFs to the GitHub Pages repo.

KEY DESIGN: index.html is a dynamic shell — it calls the GitHub Contents API
at load time to discover all PDFs in the repo.  This means any PDF added to
the repo (manually or via this script) appears automatically; no re-generation
of index.html is required just to show a new report.

data.json is the only file that must be regenerated each run; it holds the
historical chart data and fire stats used to annotate the PDF archive table.

Usage: python3 push_to_github.py [YYYY-MM-DD]

╔══════════════════════════════════════════════════════════════════════════════╗
║ ⚠  DUAL-LOCATION SYNC REQUIRED — READ BEFORE EDITING                          ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ This file exists in TWO places on the Mac:                                   ║
║                                                                              ║
║   1. CANONICAL (this file):                                                  ║
║      ~/Documents/Claude/outputs/push_to_github.py                            ║
║                                                                              ║
║   2. RUNTIME COPY (what push_daily.sh actually executes at 08:15 AM):        ║
║      Check the `python3 "$HOME/Documents/Claude/outputs/push_to_github.py"`  ║
║      line in ~/Library/Scripts/push_daily.sh — it currently points to THIS   ║
║      canonical path, so edits here take effect on the next run.              ║
║                                                                              ║
║ IMPORTANT: push_to_github.py regenerates index.html from scratch every run   ║
║ (see the HTML template further down this file). Editing                      ║
║ ~/Documents/Claude/oregon-wildfire-reports-repo/index.html directly will be  ║
║ OVERWRITTEN on the next push. Change the template in THIS file instead.      ║
║                                                                              ║
║ Same dual-location sync pattern exists for fetch_structure_data.py (07:00    ║
║ AM fetch) and push_daily.sh (08:15 AM push wrapper). See those files.        ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import json, os, re, sys, subprocess, shutil, glob
from datetime import datetime, date

TODAY = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()

# ── Paths ───────────────────────────────────────────────────────────────────
OUTPUTS_DIR = os.path.expanduser("~/Documents/Claude/outputs")
JSON_PATH   = os.path.join(OUTPUTS_DIR, "wildfire_data", f"oregon_fires_{TODAY[:4]}.json")
LOCAL_REPO  = os.path.expanduser("~/Documents/Claude/oregon-wildfire-reports-repo")
REMOTE      = "https://github.com/mramage1207-ux/oregon-wildfire-reports.git"

# ── GitHub token — read from file so it never touches git config or Keychain ─
# LaunchAgent sessions lack GUI context and can't use osxkeychain; embedding
# the token in the push URL is the reliable workaround.
_token_file = os.path.join(OUTPUTS_DIR, "wildfire_data", ".gh_token")
try:
    with open(_token_file) as _tf:
        _GH_TOKEN = _tf.read().strip()
    REMOTE_AUTH = f"https://{_GH_TOKEN}@github.com/mramage1207-ux/oregon-wildfire-reports.git"
except FileNotFoundError:
    print(f"WARNING: Token file not found at {_token_file} — falling back to unauthenticated push (may fail).")
    REMOTE_AUTH = REMOTE
REPO_SLUG   = "mramage1207-ux/oregon-wildfire-reports"   # used in index.html JS

# ── New-year guard ───────────────────────────────────────────────────────────
if not os.path.exists(JSON_PATH):
    _prev_year = str(int(TODAY[:4]) - 1)
    _prev_json = os.path.join(OUTPUTS_DIR, "wildfire_data", f"oregon_fires_{_prev_year}.json")
    _setup_script = os.path.join(OUTPUTS_DIR, "new_year_setup.py")
    print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  ⚠  NEW YEAR DATA FILE MISSING — ACTION REQUIRED                            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Expected:  {JSON_PATH}
║  Not found. A new season data file must be created before reports can push. ║
║                                                                              ║
║  TO FIX — run the setup script:                                              ║
║    python3 {_setup_script}
║                                                                              ║
║  That script copies {_prev_json}
║  into a fresh {TODAY[:4]} file with fires, historical_data, and             ║
║  last_updated reset for the new season.                                      ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
    sys.exit(1)

# ── Load fire JSON ──────────────────────────────────────────────────────────
with open(JSON_PATH) as f:
    jdata = json.load(f)

hist               = jdata.get("historical_data", [])
fires_list         = jdata.get("fires", [])
structures_lost    = sum(f.get("structures_lost", 0) for f in fires_list)
# Active Large Fires: active fires >= 100 acres (ODF/NIFC large-fire threshold for timber/brush).
# The scheduled task overwrites this from the OEM dashboard when available.
active_large_fires = sum(1 for f in fires_list
                         if f.get("status") == "Active" and f.get("acres", 0) >= 100)
# Total Acres YTD: NWCC large fires only (WF, >=100 ac OR triggered evacuation).
# Matches the "Total Acres" stat shown in the daily PDF report header exactly.
_wf_fires_for_acres = [f for f in fires_list if f.get("incident_type", "WF") != "RX"]
total_large_fire_acres = sum(
    f.get("acres", 0) for f in _wf_fires_for_acres
    if f.get("acres", 0) >= 100 or f.get("triggered_evacuation")
)

# Evac-Triggering Fires: active fires with evacuations that are NOT yet lifted.
evac_triggering_fires = sum(
    1 for f in fires_list
    if f.get("status") == "Active"
    and f.get("triggered_evacuation")
    and f.get("evacuation_status", "").lower() not in ("lifted", "none", "")
)

# Evac zones by level — active large fires only, evacuations not lifted.
# Each fire is listed under ALL levels that have active zones (not just peak).
evac_zones = {"level3": [], "level2": [], "level1": []}
for f in fires_list:
    if f.get("status") != "Active":
        continue
    if f.get("evacuation_status", "").lower() in ("lifted", "none", ""):
        continue
    entry = f.get("name", "Unknown Fire")
    loc   = f.get("location", "")
    if loc:
        entry += f" \u2014 {loc}"
    evz  = f.get("evac_zones", {})
    peak = f.get("peak_evacuation_level")
    if evz.get("level_3") or peak == 3:
        evac_zones["level3"].append(entry)
    if evz.get("level_2") or peak == 2:
        evac_zones["level2"].append(entry)
    if evz.get("level_1") or peak == 1:
        evac_zones["level1"].append(entry)

# OEM evac address counts — authoritative address-level counts per evacuation level.
# These come from the OEM ArcGIS dashboard and match what the PDF report shows.
_oem_evac = jdata.get("oem_evac", {})
evac_addresses_l1 = _oem_evac.get("level_1_addresses", 0) or 0
evac_addresses_l2 = _oem_evac.get("level_2_addresses", 0) or 0
evac_addresses_l3 = _oem_evac.get("level_3_addresses", 0) or 0

# ── Load historical seasons summary (if available) ──────────────────────────
_hist_seasons_path = os.path.join(OUTPUTS_DIR, "wildfire_data", "historical_seasons.json")
hist_seasons = []
if os.path.exists(_hist_seasons_path):
    with open(_hist_seasons_path) as f:
        hist_seasons = json.load(f)

# ── Load latest structure loss data (if available) ──────────────────────────
struct_loss_files = sorted(
    glob.glob(os.path.join(OUTPUTS_DIR, "wildfire_data", "structure_loss_*.json")),
    reverse=True
)
struct_loss_data = None
if struct_loss_files:
    with open(struct_loss_files[0]) as f:
        struct_loss_data = json.load(f)

# ── Auto-generate current year summary from live fire data ───────────────────
_cur_year = int(TODAY[:4])
_cur_structures = 0
if struct_loss_data and struct_loss_data.get("oregon_totals"):
    _st = struct_loss_data["oregon_totals"]
    _cur_structures = (_st.get("res_destroyed", 0) +
                       _st.get("comm_destroyed", 0) +
                       _st.get("minor_destroyed", 0))
# Large fires for season summary: WF type (non-RX), >= 100 acres OR triggered
# evacuation (covers significant fires that mobilized resources regardless of size).
# Matches the same threshold used for total_large_fire_acres.
_large_fires_season = [
    f for f in fires_list
    if f.get("incident_type", "WF") != "RX"
    and (f.get("acres", 0) >= 100 or f.get("triggered_evacuation"))
]
_top5_fires = sorted(_large_fires_season, key=lambda f: f.get("acres", 0), reverse=True)[:5]
_cur_season = {
    "year": _cur_year,
    "total_fires": len(_large_fires_season),
    "total_acres": total_large_fire_acres,
    "total_structures_destroyed": _cur_structures,
    "fatalities": sum(f.get("fatalities", 0) for f in fires_list),
    "injuries":   sum(f.get("injuries", 0)   for f in fires_list),
    "largest_fire": {
        "name": _top5_fires[0].get("name", ""),
        "county": _top5_fires[0].get("county", ""),
        "acres": _top5_fires[0].get("acres", 0)
    } if _top5_fires else None,
    "notable_fires": [
        {"name": f.get("name", ""), "county": f.get("county", ""), "acres": f.get("acres", 0)}
        for f in _top5_fires
    ]
}
# Prepend current year, replacing any existing entry for this year
hist_seasons_with_cur = [s for s in hist_seasons if s.get("year") != _cur_year]
hist_seasons_with_cur.insert(0, _cur_season)

# ── Build data.json ─────────────────────────────────────────────────────────
# This is the ONLY file the page needs from the repo to show stats.
# PDFs are discovered dynamically via GitHub Contents API.
data_json_obj = {
    "last_updated":           TODAY,
    "report_time":            jdata.get("report_time", ""),   # PDF generation time (e.g. "8:04 AM")
    "active_large_fires":     active_large_fires,
    "total_large_fire_acres": total_large_fire_acres,
    "structures_lost":        structures_lost,
    "evac_triggering_fires":  evac_triggering_fires,
    "evac_zones":             evac_zones,
    "evac_addresses_l1":      evac_addresses_l1,
    "evac_addresses_l2":      evac_addresses_l2,
    "evac_addresses_l3":      evac_addresses_l3,
    "struct_loss":            struct_loss_data,
    "historical_data":        hist,
    "historical_seasons":     hist_seasons_with_cur
}
data_json_str = json.dumps(data_json_obj, indent=2)

# ── Find all local PDFs to push ─────────────────────────────────────────────
pdf_pattern = os.path.join(OUTPUTS_DIR, "oregon_wildfire_report_*.pdf")
pdf_files   = sorted(glob.glob(pdf_pattern), reverse=True)

if not pdf_files:
    print("No PDFs found in outputs. Nothing to push.")
    sys.exit(0)

# ── Generate dynamic index.html ─────────────────────────────────────────────
# The page fetches its own PDF list at runtime via GitHub Contents API.
# Adding or removing a PDF from the repo is instantly reflected — no
# script re-run needed.

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Oregon Wildfire Daily Reports &mdash; Archive</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #f4f5f7; color: #1a1a2e; min-height: 100vh;
    }}
    a {{ color: inherit; text-decoration: none; }}

    /* ── header ── */
    header {{
      background: linear-gradient(135deg, #1a2744 0%, #0f3460 100%);
      color: #fff; padding: 0 2rem;
    }}
    .header-inner {{
      max-width: 1100px; margin: 0 auto;
      display: flex; align-items: center; gap: 1.25rem; padding: 1.4rem 0;
    }}
    .header-flame {{ font-size: 2.4rem; line-height: 1; filter: drop-shadow(0 0 8px rgba(255,120,20,.7)); }}
    .header-text h1 {{ font-size: 1.45rem; font-weight: 700; letter-spacing: -.3px; }}
    .header-text p {{ font-size: .82rem; opacity: .72; margin-top: 2px; }}
    .header-badge {{
      margin-left: auto;
      background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.22);
      border-radius: 20px; padding: .3rem .85rem;
      font-size: .78rem; font-weight: 600; letter-spacing: .5px; white-space: nowrap;
    }}

    /* ── hero ── */
    .hero-wrap {{
      background: linear-gradient(135deg, #0f3460 0%, #1a2744 100%);
      padding: 0 2rem 2.5rem;
    }}
    .hero {{ max-width: 1100px; margin: 0 auto; padding-top: 2rem; }}
    .hero-label {{
      font-size: .72rem; font-weight: 700; letter-spacing: 1.2px;
      text-transform: uppercase; color: rgba(255,255,255,.55); margin-bottom: .9rem;
    }}
    .hero-card {{
      background: rgba(255,255,255,.07); border: 1px solid rgba(255,255,255,.14);
      border-radius: 12px; padding: 1.4rem 1.6rem;
      display: flex; align-items: center; gap: 2rem; flex-wrap: wrap; color: #fff;
    }}
    .report-date {{ font-size: 1.35rem; font-weight: 700; }}
    .report-sub {{ font-size: .8rem; opacity: .6; margin-top: 3px; }}
    .report-time-badge {{
      display: inline-block; background: #fff; color: #2d3748;
      border: 1px solid rgba(255,255,255,.35); border-radius: 5px;
      padding: .15rem .55rem; font-size: .82rem; font-weight: 600;
      margin-top: 6px; white-space: nowrap;
    }}
    .report-time-badge .time-val {{ color: #e53e3e; font-weight: 700; }}
    .hero-stats {{ display: flex; gap: 1.2rem; }}
    .stat-pill {{
      background: rgba(255,255,255,.1); border-radius: 10px;
      padding: .6rem 1rem; text-align: center; min-width: 80px;
    }}
    .stat-pill .val {{ font-size: 1.5rem; font-weight: 700; line-height: 1; }}
    .stat-pill .lbl {{ font-size: .68rem; opacity: .65; margin-top: 3px; text-transform: uppercase; letter-spacing: .4px; }}
    .hero-actions {{ margin-left: auto; display: flex; gap: .7rem; flex-direction: column; }}
    .btn-primary {{
      background: #e67e22; color: #fff; border-radius: 8px;
      padding: .55rem 1.1rem; font-size: .85rem; font-weight: 600; white-space: nowrap;
    }}
    .btn-secondary {{
      background: rgba(255,255,255,.12); color: #fff; border-radius: 8px;
      padding: .55rem 1.1rem; font-size: .85rem; font-weight: 600;
      border: 1px solid rgba(255,255,255,.22); white-space: nowrap;
    }}
    .loading-hero {{
      color: rgba(255,255,255,.5); font-size: .9rem; padding: 1.5rem 0;
    }}

    /* ── evacuation zones ── */
    .evac-section {{
      max-width: 1100px; margin: 1rem auto 0; padding: 0;
    }}
    .evac-label {{
      font-size: .7rem; font-weight: 700; letter-spacing: .8px;
      text-transform: uppercase; color: rgba(255,255,255,.70);
      margin-bottom: .55rem;
    }}
    .evac-grid {{
      display: grid; grid-template-columns: 1fr 1fr 1fr; gap: .75rem;
      margin-bottom: .5rem;
    }}
    .evac-box {{
      border-radius: 8px; padding: .75rem 1rem; border: 1px solid;
      display: flex; align-items: center; justify-content: space-between; gap: 1rem;
      min-height: 52px;
    }}
    .evac-box .evac-level {{
      font-size: .7rem; font-weight: 800; letter-spacing: .6px;
      text-transform: uppercase; flex: 1;
    }}
    .evac-box .evac-count {{
      font-size: 1.6rem; font-weight: 700; text-align: right; flex-shrink: 0; line-height: 1;
    }}
    .evac-box.level3 {{ background: rgba(197,48,48,.18); border-color: rgba(197,48,48,.5); }}
    .evac-box.level3 .evac-level {{ color: #fc8181; }}
    .evac-box.level3 .evac-count {{ color: #fc8181; }}
    .evac-box.level2 {{ background: rgba(221,107,32,.18); border-color: rgba(221,107,32,.5); }}
    .evac-box.level2 .evac-level {{ color: #fbd38d; }}
    .evac-box.level2 .evac-count {{ color: #fbd38d; }}
    .evac-box.level1 {{ background: rgba(34,139,34,.2); border-color: rgba(34,139,34,.5); }}
    .evac-box.level1 .evac-level {{ color: #68d391; }}
    .evac-box.level1 .evac-count {{ color: #68d391; }}
    @media (max-width: 700px) {{ .evac-grid {{ grid-template-columns: 1fr; }} }}

    /* ── structure loss table ── */
    .struct-loss-section {{
      max-width: 1100px; margin: 1rem auto 0; padding: 0 0 .75rem;
    }}
    .struct-loss-label {{
      font-size: .7rem; font-weight: 700; letter-spacing: .8px;
      text-transform: uppercase; color: rgba(255,255,255,.70);
      margin-bottom: .55rem;
    }}
    .struct-loss-table {{
      width: 100%; border-collapse: collapse;
      background: rgba(255,255,255,.07); border: 1px solid rgba(255,255,255,.14);
      border-radius: 8px; overflow: hidden; color: #fff; font-size: .82rem;
    }}
    .struct-loss-table th {{
      background: rgba(255,255,255,.1); color: rgba(255,255,255,.82);
      font-size: .7rem; font-weight: 700; text-transform: uppercase;
      letter-spacing: .5px; padding: .55rem .9rem; text-align: center;
      border-bottom: 1px solid rgba(255,255,255,.14);
    }}
    .struct-loss-table th:first-child {{ text-align: left; }}
    .struct-loss-table th .th-sub {{
      display: block; font-size: .58rem; font-weight: 600;
      letter-spacing: .4px; opacity: .62; margin-top: 2px;
    }}
    .struct-loss-table td {{
      padding: .55rem .9rem; border-bottom: 1px solid rgba(255,255,255,.08);
      text-align: center;
    }}
    .struct-loss-table td:first-child {{ text-align: left; font-weight: 600; }}
    .struct-loss-table tr:last-child td {{
      border-bottom: none; font-weight: 700;
      background: rgba(255,255,255,.05);
    }}
    .sl-zero  {{ color: rgba(255,255,255,.4); }}
    .sl-warn  {{ color: #fbd38d; font-weight: 700; }}
    .sl-alert {{ color: #fc8181; font-weight: 700; }}

    @media (max-width: 700px) {{
      .evac-grid {{ grid-template-columns: 1fr; }}
    }}

    /* ── main ── */
    main {{ max-width: 1100px; margin: 0 auto; padding: 2rem; }}
    .chart-card {{
      background: #fff; border-radius: 12px; padding: 1.4rem 1.6rem;
      margin-bottom: 1.5rem; box-shadow: 0 1px 4px rgba(0,0,0,.07);
    }}
    .chart-card h2 {{ font-size: 1rem; font-weight: 700; color: #1a2744; }}
    .chart-sub {{ font-size: .78rem; color: #5a6473; margin-top: 3px; margin-bottom: 1rem; }}
    .chart-legend {{ display: flex; gap: 1.2rem; margin-bottom: .6rem; }}
    .legend-item {{ display: flex; align-items: center; gap: .4rem; font-size: .78rem; color: #4a5568; }}
    .legend-dot {{ width: 10px; height: 10px; border-radius: 50%; }}
    svg.trend {{ width: 100%; display: block; }}

    /* ── archive table ── */
    .section-header {{ display: flex; align-items: center; gap: .8rem; margin-bottom: .8rem; }}
    .section-header h2 {{ font-size: 1rem; font-weight: 700; color: #1a2744; }}
    .pill-count {{
      background: #b85c00; color: #fff; border-radius: 20px;
      padding: .15rem .6rem; font-size: .72rem; font-weight: 700;
    }}
    .table-card {{
      background: #fff; border-radius: 12px;
      box-shadow: 0 1px 4px rgba(0,0,0,.07);
      overflow: hidden; margin-bottom: 1.5rem;
    }}
    table {{ width: 100%; border-collapse: collapse; }}
    thead th {{
      background: #f8fafc; color: #5a6473;
      font-size: .72rem; font-weight: 700; text-transform: uppercase; letter-spacing: .5px;
      padding: .75rem 1.2rem; text-align: left; border-bottom: 1px solid #e2e8f0;
    }}
    tbody tr {{ border-bottom: 1px solid #f0f4f8; }}
    tbody tr:last-child {{ border-bottom: none; }}
    tbody tr.latest {{ background: #fffbf5; }}
    tbody td {{ padding: .85rem 1.2rem; font-size: .88rem; }}
    .date-cell {{ font-weight: 600; }}
    .iso {{ font-size: .75rem; color: #6b7280; font-weight: 400; }}
    .badge-new {{
      background: #b85c00; color: #fff; border-radius: 4px;
      padding: .1rem .4rem; font-size: .65rem; font-weight: 700;
      vertical-align: middle; margin-left: 4px;
    }}
    .fires-val {{ font-weight: 700; color: #c0392b; }}
    .acres-val {{ font-weight: 700; color: #b85c00; }}
    .action-link {{
      display: inline-flex; align-items: center; gap: .3rem;
      background: #f0f4f8; border-radius: 6px;
      padding: .3rem .65rem; font-size: .78rem; font-weight: 600;
      color: #1a2744; margin-right: 4px;
    }}
    .action-link.dl {{ background: #fff3e6; color: #7a3800; }}
    .loading-row td {{
      text-align: center; padding: 2rem; color: #718096; font-size: .88rem;
    }}
    .error-box {{
      background: #fff5f5; border-left: 3px solid #e53e3e; border-radius: 0 8px 8px 0;
      padding: 1rem 1.2rem; font-size: .82rem; color: #742a2a; margin-bottom: 1.5rem;
    }}
    .info-box {{
      background: #f0f6ff; border-left: 3px solid #3b82f6;
      border-radius: 0 8px 8px 0; padding: 1rem 1.2rem;
      font-size: .82rem; color: #4a5568; line-height: 1.6; margin-bottom: 1.5rem;
    }}

    /* ── historical seasons ── */
    .seasons-section {{ max-width: 1100px; margin: 0 auto; padding: 2rem 2rem 0; }}
    .seasons-section h2 {{ font-size: 1rem; font-weight: 700; color: #1a2744; margin-bottom: .3rem; }}
    .seasons-sub {{ font-size: .78rem; color: #5a6473; margin-bottom: 1.1rem; }}
    .seasons-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 1rem; margin-bottom: 1.5rem;
    }}
    .season-card {{
      background: #fff; border-radius: 12px;
      box-shadow: 0 1px 4px rgba(0,0,0,.07);
      padding: 1.1rem 1.3rem; border-top: 4px solid #1a2744;
    }}
    .season-year {{
      font-size: 1.4rem; font-weight: 800; color: #1a2744; line-height: 1;
    }}
    .season-stats {{
      display: flex; gap: .6rem; flex-wrap: wrap; margin: .65rem 0 .75rem;
    }}
    .season-stat {{
      background: #f4f5f7; border-radius: 8px;
      padding: .35rem .65rem; font-size: .75rem; text-align: center;
    }}
    .season-stat .sv {{ font-weight: 700; font-size: .95rem; color: #1a2744; display: block; }}
    .season-stat .sl {{ color: #5a6473; font-size: .68rem; text-transform: uppercase; letter-spacing: .3px; }}
    .season-fires-label {{
      font-size: .68rem; font-weight: 700; letter-spacing: .6px;
      text-transform: uppercase; color: #5a6473; margin-bottom: .35rem;
    }}
    .season-fire-row {{
      display: flex; justify-content: space-between;
      font-size: .78rem; padding: .2rem 0;
      border-bottom: 1px solid #f0f4f8; color: #2d3748;
    }}
    .season-fire-row:last-child {{ border-bottom: none; }}
    .season-fire-name {{ font-weight: 600; flex: 1; padding-right: .5rem; }}
    .season-fire-acres {{ color: #b85c00; font-weight: 700; white-space: nowrap; }}
    .season-fire-county {{ color: #6b7280; font-size: .72rem; }}
    .season-alert {{ color: #c0392b; font-weight: 700; font-size: .78rem; margin-top: .4rem; }}

    @media (max-width: 700px) {{
      .seasons-grid {{ grid-template-columns: 1fr; }}
    }}

    /* ── month accordion ── */
    .month-section {{ margin-bottom: .6rem; }}
    .month-toggle {{
      width: 100%; display: flex; align-items: center; gap: .75rem;
      background: #fff; border: none; border-radius: 12px;
      padding: .85rem 1.2rem; cursor: pointer; text-align: left;
      box-shadow: 0 1px 4px rgba(0,0,0,.07);
      font-family: inherit; transition: background .15s, box-shadow .15s;
    }}
    .month-toggle:hover {{ background: #f8fafc; box-shadow: 0 2px 6px rgba(0,0,0,.1); }}
    .month-section.month-current .month-toggle {{
      border-radius: 12px 12px 0 0;
      border-bottom: 2px solid #e67e22;
    }}
    .month-chevron {{
      font-size: .78rem; color: #a0aec0; width: 1rem;
      flex-shrink: 0; transition: transform .2s;
    }}
    .month-toggle[aria-expanded="true"] .month-chevron {{ transform: rotate(90deg); }}
    .month-name {{ font-size: .95rem; font-weight: 700; color: #1a2744; flex: 1; }}
    .month-badge {{
      background: #f0f4f8; color: #5a6473; border-radius: 20px;
      padding: .15rem .65rem; font-size: .72rem; font-weight: 600; white-space: nowrap;
    }}
    .month-section.month-current .month-badge {{ background: #fff3e6; color: #7a3800; }}
    .month-body .table-card {{
      border-radius: 0 0 12px 12px;
      margin-bottom: 0;
      box-shadow: 0 2px 4px rgba(0,0,0,.07);
    }}
    .month-body[hidden] {{ display: none; }}

    /* ── footer ── */
    footer {{
      background: #1a2744; color: rgba(255,255,255,.5);
      text-align: center; padding: 1.6rem 2rem; font-size: .78rem; line-height: 1.8;
    }}
    footer a {{ color: rgba(255,255,255,.7); text-decoration: underline; }}
    footer strong {{ color: rgba(255,255,255,.85); }}

    @media (max-width: 700px) {{
      .hero-card {{ flex-direction: column; gap: 1.2rem; }}
      .hero-actions {{ margin-left: 0; flex-direction: row; flex-wrap: wrap; }}
      thead th:nth-child(3), tbody td:nth-child(3) {{ display: none; }}
    }}
  </style>
</head>
<body>

<header>
  <div class="header-inner">
    <div class="header-flame">&#128293;</div>
    <div class="header-text">
      <h1>Oregon Wildfire Daily Reports</h1>
      <p>Daily Archive &mdash; Published automatically from NIFC WFIGS, ODF &amp; OEM data feeds</p>
    </div>
    <div class="header-badge">{TODAY[:4]} Season</div>
  </div>
</header>

<div class="hero-wrap">
  <div class="hero">
    <div class="hero-label">Latest Report</div>
    <div id="hero-card" class="hero-card">
      <div class="loading-hero">&#9203; Loading latest report&hellip;</div>
    </div>
    <div class="evac-section">
      <div class="evac-label">Evacuation Addresses by Level &mdash; Active Fires Only</div>
      <div class="evac-grid">
        <div class="evac-box level1">
          <div class="evac-level">Level 1 &mdash; Be Ready</div>
          <div class="evac-count" id="evac-l1">0</div>
        </div>
        <div class="evac-box level2">
          <div class="evac-level">Level 2 &mdash; Be Set</div>
          <div class="evac-count" id="evac-l2">0</div>
        </div>
        <div class="evac-box level3">
          <div class="evac-level">Level 3 &mdash; Go Now</div>
          <div class="evac-count" id="evac-l3">0</div>
        </div>
      </div>
    </div>
    <div class="struct-loss-section" id="struct-loss-section" style="display:none">
      <div class="struct-loss-label">Structure Loss &mdash; All Reported Incidents (WFIGS)</div>
      <table class="struct-loss-table">
        <thead>
          <tr>
            <th>Structure Type</th>
            <th>Threatened<span class="th-sub">Active</span></th>
            <th>Damaged<span class="th-sub">YTD</span></th>
            <th>Destroyed<span class="th-sub">YTD</span></th>
          </tr>
        </thead>
        <tbody id="struct-loss-tbody"></tbody>
      </table>
    </div>
  </div>
</div>

<main>
  <div class="chart-card" id="chart-card">
    <h2>{TODAY[:4]} Season Trend</h2>
    <div class="chart-sub">Active fires &amp; acres burned &mdash; all reported days</div>
    <div class="chart-legend">
      <div class="legend-item">
        <div class="legend-dot" style="background:#e67e22;"></div> Acres Burned (left axis)
      </div>
      <div class="legend-item">
        <div class="legend-dot" style="background:#1a2744;"></div> Active Fires (right axis)
      </div>
    </div>
    <svg class="trend" viewBox="0 0 1000 180" preserveAspectRatio="none" id="trendChart"></svg>
  </div>

  <div class="section-header">
    <h2>Report Archive</h2>
    <span class="pill-count" id="pdf-count">&#8230;</span>
  </div>
  <div id="error-area"></div>
  <div id="archive-container">
    <div class="table-card">
      <table><tbody>
        <tr class="loading-row"><td colspan="4">&#9203; Loading reports&hellip;</td></tr>
      </tbody></table>
    </div>
  </div>

  <div class="info-box">
    <strong style="color:#1a2744;">About this archive:</strong>
    Daily PDF reports are published automatically each morning using three live data feeds:
    <strong>NIFC WFIGS</strong> (fire incident locations &amp; acreage),
    <strong>Oregon OEM ArcGIS</strong> (evacuation zones), and
    <strong>Oregon Dept. of Forestry</strong> records. The PDF list is fetched live from
    the repository &mdash; any report added (automatically or manually) appears here immediately.
    PDFs are preserved indefinitely.
  </div>
</main>

<div class="seasons-section" id="seasons-section" style="display:none">
  <h2>Historical Season Summaries</h2>
  <div class="seasons-sub">Prior Oregon wildfire seasons &mdash; large fires</div>
  <div class="seasons-grid" id="seasons-grid"></div>
</div>

<footer>
  <strong>Oregon Wildfire Daily Reports</strong><br>
  Data sourced from
  <a href="https://www.nifc.gov/nicc/logistics/situation.htm" target="_blank">NIFC WFIGS</a>
  &nbsp;&bull;&nbsp;
  <a href="https://geo.maps.arcgis.com/apps/instant/portfolio/index.html?appid=22d04c007866419c91ccf00d097526c8" target="_blank">Oregon OEM ArcGIS</a>
  &nbsp;&bull;&nbsp;
  <a href="https://www.oregon.gov/odf/fire/pages/firestats.aspx" target="_blank">Oregon Dept. of Forestry</a>
  &nbsp;&bull;&nbsp;
  <a href="https://wildfire.oregon.gov" target="_blank">Oregon Wildfire Response</a><br>
  Reports generated automatically. Not an official State of Oregon publication.<br>\n  Report assembly assisted by <strong style=\"color:rgba(255,255,255,.7);\">Claude AI</strong> (Anthropic) &mdash; data sourced from public government feeds.
</footer>

<script>
// ── CONFIG ──────────────────────────────────────────────────────────────────
const REPO      = '{REPO_SLUG}';
const API_URL   = 'https://api.github.com/repos/' + REPO + '/contents/';
const DATA_URL  = 'data.json';   // relative: served from same GitHub Pages origin
const PDF_RE    = /^oregon_wildfire_report_(\\d{{4}}-\\d{{2}}-\\d{{2}})\\.pdf$/;

// ── ACCORDION ────────────────────────────────────────────────────────────────
function toggleMonth(btn) {{
  const body = btn.nextElementSibling;
  const isOpen = btn.getAttribute('aria-expanded') === 'true';
  btn.setAttribute('aria-expanded', String(!isOpen));
  if (isOpen) {{
    body.setAttribute('hidden', '');
    btn.closest('.month-section').classList.remove('month-current');
  }} else {{
    body.removeAttribute('hidden');
    btn.closest('.month-section').classList.add('month-current');
  }}
}}

// ── UTILITIES ────────────────────────────────────────────────────────────────
function fmtDate(iso) {{
  try {{
    const [y,m,d] = iso.split('-').map(Number);
    return new Date(y, m-1, d).toLocaleDateString('en-US',
      {{weekday:'long', year:'numeric', month:'long', day:'numeric'}});
  }} catch(e) {{ return iso; }}
}}

function fmtAcres(v) {{
  if (v == null || v === '—' || v === '') return '—';
  const n = parseFloat(v);
  return isNaN(n) ? v : n.toLocaleString('en-US', {{maximumFractionDigits: 1}});
}}

// ── CHART ────────────────────────────────────────────────────────────────────
function drawChart(hist) {{
  const svg = document.getElementById('trendChart');
  if (!svg || !hist || hist.length < 2) return;
  const W=1000, H=180, P={{t:10,r:44,b:28,l:46}};
  const cW=W-P.l-P.r, cH=H-P.t-P.b;
  const maxA = Math.max(...hist.map(d=>d.acres_burned||0)) || 1;
  const maxF = Math.max(...hist.map(d=>d.active_fires||0)) || 1;
  const n = hist.length;
  const xS = i => P.l + (i/(n-1))*cW;
  const yA = v => P.t + cH - ((v||0)/maxA)*cH;
  const yF = v => P.t + cH - ((v||0)/maxF)*cH;

  const ns = 'http://www.w3.org/2000/svg';
  const el = (tag, attrs) => {{
    const e = document.createElementNS(ns, tag);
    Object.entries(attrs).forEach(([k,v]) => e.setAttribute(k,v));
    return e;
  }};

  // grid lines + left axis labels (acres burned)
  [0,.25,.5,.75,1].forEach(t => {{
    const y = P.t + cH*(1-t);
    svg.appendChild(el('line', {{x1:P.l, y1:y, x2:P.l+cW, y2:y,
      stroke:'#e2e8f0', 'stroke-width':'0.6'}}));
    const lb = el('text', {{x:P.l-5, y:y+4, 'text-anchor':'end',
      'font-size':'8', fill:'#a0aec0'}});
    lb.textContent = Math.round(maxA*t);
    svg.appendChild(lb);
  }});

  // right axis labels (active fires)
  [0,.25,.5,.75,1].forEach(t => {{
    const y = P.t + cH*(1-t);
    const lb = el('text', {{x:P.l+cW+6, y:y+4, 'text-anchor':'start',
      'font-size':'8', fill:'#1a2744', 'font-weight':'600'}});
    lb.textContent = Math.round(maxF*t);
    svg.appendChild(lb);
  }});

  // right axis border line
  svg.appendChild(el('line', {{x1:P.l+cW, y1:P.t, x2:P.l+cW, y2:P.t+cH,
    stroke:'#e2e8f0', 'stroke-width':'0.8'}}));

  // acres area
  const ap = hist.map((d,i)=>`${{i===0?'M':'L'}}${{xS(i)}},${{yA(d.acres_burned)}}`).join(' ')
    + ` L${{xS(n-1)}},${{P.t+cH}} L${{xS(0)}},${{P.t+cH}} Z`;
  svg.appendChild(el('path', {{d:ap, fill:'rgba(230,126,34,.18)'}}));

  // acres line
  svg.appendChild(el('polyline', {{
    points: hist.map((d,i)=>`${{xS(i)}},${{yA(d.acres_burned)}}`).join(' '),
    fill:'none', stroke:'#e67e22', 'stroke-width':'2', 'stroke-linejoin':'round'
  }}));

  // fires line
  svg.appendChild(el('polyline', {{
    points: hist.map((d,i)=>`${{xS(i)}},${{yF(d.active_fires)}}`).join(' '),
    fill:'none', stroke:'#1a2744', 'stroke-width':'1.8',
    'stroke-dasharray':'4 2', 'stroke-linejoin':'round'
  }}));

  // month labels
  const months = [...new Set(hist.map(d=>d.date.slice(0,7)))].sort();
  months.forEach(mo => {{
    const idx = hist.findIndex(d => d.date >= mo+'-01');
    if (idx < 0) return;
    const x = xS(idx);
    const lb = el('text', {{x, y:H-4, 'text-anchor':'middle',
      'font-size':'9', fill:'#718096', 'font-weight':'600'}});
    lb.textContent = new Date(mo+'-15').toLocaleDateString('en-US',{{month:'short'}});
    svg.appendChild(lb);
    svg.appendChild(el('line', {{x1:x, y1:P.t+cH, x2:x, y2:P.t+cH+4,
      stroke:'#cbd5e0', 'stroke-width':'1'}}));
  }});

  // latest dot
  svg.appendChild(el('circle', {{
    cx:xS(n-1), cy:yA(hist[n-1].acres_burned),
    r:'4', fill:'#e67e22', stroke:'#fff', 'stroke-width':'2'
  }}));
}}

// ── RENDER ───────────────────────────────────────────────────────────────────
function render(pdfs, data) {{
  const histByDate = {{}};
  if (data && data.historical_data) {{
    data.historical_data.forEach(r => {{ histByDate[r.date] = r; }});
  }}

  // Enrich PDFs with stats
  const enriched = pdfs.map(p => {{
    const row = histByDate[p.date_iso] || {{}};
    return {{
      ...p,
      active_fires: row.active_fires != null ? row.active_fires : '—',
      acres_burned: row.acres_burned != null ? row.acres_burned : '—'
    }};
  }});

  const latest = enriched[0];
  const nLargeFires   = data && data.active_large_fires      != null ? data.active_large_fires      : '—';
  const nLargeAcres   = data && data.total_large_fire_acres  != null ? data.total_large_fire_acres  : '—';
  const nStructLost   = data && data.structures_lost         != null ? data.structures_lost         : '—';
  const nEvacFires    = data && data.evac_triggering_fires   != null ? data.evac_triggering_fires   : '—';
  const evacZones     = (data && data.evac_zones) || {{}};

  // ── Hero card ──
  const reportTimeBadge = data && data.report_time
    ? `<div class="report-time-badge">Updated at <span class="time-val">${{data.report_time}}</span></div>`
    : '';
  document.getElementById('hero-card').innerHTML = `
    <div class="hero-date-block">
      <div class="report-date">${{fmtDate(latest.date_iso)}}</div>
      ${{reportTimeBadge}}
    </div>
    <div class="hero-stats">
      <div class="stat-pill">
        <div class="val">${{nLargeFires}}</div>
        <div class="lbl">Active Large Fires</div>
      </div>
      <div class="stat-pill">
        <div class="val">${{latest.active_fires}}</div>
        <div class="lbl">Active Fires</div>
      </div>
      <div class="stat-pill">
        <div class="val">${{fmtAcres(nLargeAcres)}}</div>
        <div class="lbl">Total Acres YTD<br><span style="font-size:0.75em;opacity:0.8">(Large Fires)</span></div>
      </div>
      <div class="stat-pill">
        <div class="val">${{nStructLost}}</div>
        <div class="lbl">Structures Lost</div>
      </div>
      <div class="stat-pill">
        <div class="val">${{nEvacFires}}</div>
        <div class="lbl">Evac-Triggering Fires</div>
      </div>
    </div>
    <div class="hero-actions">
      <a class="btn-primary" href="${{latest.filename}}" target="_blank">&#128196; View Report</a>
      <a class="btn-secondary" href="${{latest.filename}}" download>&#8595; Download PDF</a>
    </div>`;

  // ── Evacuation address counts ──
  // Use OEM address counts from data.json (authoritative, matches PDF report).
  // Falls back to counting fire entries if address counts unavailable.
  function setEvacCount(id, addrCount, zones) {{
    const el = document.getElementById(id);
    if (!el) return;
    if (addrCount != null && addrCount !== '') {{
      el.textContent = Number(addrCount).toLocaleString();
    }} else {{
      const count = zones && zones.length
        ? zones.reduce((acc, z) => acc + z.split(';').filter(a => a.trim()).length, 0)
        : 0;
      el.textContent = count;
    }}
  }}
  const evacAddrL1 = data && data.evac_addresses_l1 != null ? data.evac_addresses_l1 : null;
  const evacAddrL2 = data && data.evac_addresses_l2 != null ? data.evac_addresses_l2 : null;
  const evacAddrL3 = data && data.evac_addresses_l3 != null ? data.evac_addresses_l3 : null;
  setEvacCount('evac-l1', evacAddrL1, evacZones.level1);
  setEvacCount('evac-l2', evacAddrL2, evacZones.level2);
  setEvacCount('evac-l3', evacAddrL3, evacZones.level3);

  // ── Structure loss table ──
  const sl = data && data.struct_loss && data.struct_loss.oregon_totals;
  if (sl) {{
    const rows = [
      ['Residences',   sl.res_threatened,   sl.res_damaged,   sl.res_destroyed],
      ['Commercial',   sl.comm_threatened,  sl.comm_damaged,  sl.comm_destroyed],
      ['Minor / Other',sl.minor_threatened, sl.minor_damaged, sl.minor_destroyed],
      ['<strong>Total</strong>', sl.total_threatened, sl.total_damaged, sl.total_destroyed],
    ];
    function slClass(v) {{
      if (v == null || v === 0) return 'sl-zero';
      if (v >= 10) return 'sl-alert';
      return 'sl-warn';
    }}
    document.getElementById('struct-loss-tbody').innerHTML = rows.map(r => `
      <tr>
        <td>${{r[0]}}</td>
        <td class="${{slClass(r[1])}}">${{r[1] != null ? r[1].toLocaleString() : '&mdash;'}}</td>
        <td class="${{slClass(r[2])}}">${{r[2] != null ? r[2].toLocaleString() : '&mdash;'}}</td>
        <td class="${{slClass(r[3])}}">${{r[3] != null ? r[3].toLocaleString() : '&mdash;'}}</td>
      </tr>`).join('');
    const slEl = document.getElementById('struct-loss-section');
    if (slEl) slEl.style.display = '';
  }}

  // ── Count pill ──
  document.getElementById('pdf-count').textContent = enriched.length + ' PDF' + (enriched.length !== 1 ? 's' : '') + ' available';

  // ── Archive table — grouped by month with accordion for past months ──
  const todayISO = new Date().toISOString().slice(0, 7); // "YYYY-MM"

  const byMonth = {{}};
  enriched.forEach(r => {{
    const mo = r.date_iso.slice(0, 7);
    if (!byMonth[mo]) byMonth[mo] = [];
    byMonth[mo].push(r);
  }});

  const sortedMonths = Object.keys(byMonth).sort((a, b) => b.localeCompare(a));
  let isVeryFirst = true;

  document.getElementById('archive-container').innerHTML = sortedMonths.map(mo => {{
    const isCurrent = mo === todayISO;
    const [y, m] = mo.split('-').map(Number);
    const monthLabel = new Date(y, m - 1, 1)
      .toLocaleDateString('en-US', {{ month: 'long', year: 'numeric' }});
    const reports = byMonth[mo];

    const rows = reports.map(r => {{
      const markLatest = isVeryFirst;
      if (isVeryFirst) isVeryFirst = false;
      return `
        <tr${{markLatest ? ' class="latest"' : ''}}>
          <td class="date-cell">
            ${{fmtDate(r.date_iso)}}${{markLatest ? ' <span class="badge-new">Latest</span>' : ''}}<br>
            <span class="iso">${{r.date_iso}}</span>
          </td>
          <td class="fires-val">${{r.active_fires}}</td>
          <td class="acres-val">${{fmtAcres(r.acres_burned)}}</td>
          <td>
            <a class="action-link" href="${{r.filename}}" target="_blank">&#128196; View</a>
            <a class="action-link dl" href="${{r.filename}}" download>&#8595; Download</a>
          </td>
        </tr>`;
    }}).join('');

    return `
      <div class="month-section${{isCurrent ? ' month-current' : ''}}">
        <button class="month-toggle" onclick="toggleMonth(this)" aria-expanded="${{isCurrent}}">
          <span class="month-chevron">&#9658;</span>
          <span class="month-name">${{monthLabel}}</span>
          <span class="month-badge">${{reports.length}}&nbsp;report${{reports.length !== 1 ? 's' : ''}}</span>
        </button>
        <div class="month-body"${{isCurrent ? '' : ' hidden'}}>
          <div class="table-card">
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Active Fires</th>
                  <th>Acres Burned</th>
                  <th>Report</th>
                </tr>
              </thead>
              <tbody>${{rows}}</tbody>
            </table>
          </div>
        </div>
      </div>`;
  }}).join('');

  // ── Chart ──
  if (data && data.historical_data) drawChart(data.historical_data);

  // ── Historical seasons ──
  const seasons = (data && data.historical_seasons) || [];
  if (seasons.length) {{
    document.getElementById('seasons-grid').innerHTML = seasons.map(s => {{
      const acresStr = s.total_acres != null ? s.total_acres.toLocaleString() : '—';
      const structs  = s.total_structures_destroyed || 0;
      const fatStr   = s.fatalities ? `<div class="season-alert">&#9888; ${{s.fatalities}} fatali${{s.fatalities === 1 ? 'ty' : 'ties'}}</div>` : '';
      const fireRows = (s.notable_fires || []).map(f => `
        <div class="season-fire-row">
          <span class="season-fire-name">${{f.name}}</span>
          <span>
            <span class="season-fire-county">${{f.county}}&nbsp;</span>
            <span class="season-fire-acres">${{f.acres.toLocaleString()}} ac</span>
          </span>
        </div>`).join('');
      return `
        <div class="season-card">
          <div class="season-year">${{s.year}} Season</div>
          <div class="season-stats">
            <div class="season-stat">
              <span class="sv">${{s.total_fires}}</span>
              <span class="sl">Fires Tracked</span>
            </div>
            <div class="season-stat">
              <span class="sv">${{acresStr}}</span>
              <span class="sl">Total Acres</span>
            </div>
            <div class="season-stat">
              <span class="sv">${{structs.toLocaleString()}}</span>
              <span class="sl">Structures Lost</span>
            </div>
          </div>
          <div class="season-fires-label">Top Fires by Size</div>
          ${{fireRows}}
          ${{fatStr}}
        </div>`;
    }}).join('');
    const secEl = document.getElementById('seasons-section');
    if (secEl) secEl.style.display = '';
  }}
}}

// ── MAIN LOAD ────────────────────────────────────────────────────────────────
(async function() {{
  try {{
    // Fetch PDF list from GitHub Contents API + data.json simultaneously
    const [apiResp, dataResp] = await Promise.allSettled([
      fetch(API_URL + '?_=' + Date.now()).then(r => {{
        if (!r.ok) throw new Error('GitHub API ' + r.status);
        return r.json();
      }}),
      fetch(DATA_URL + '?_=' + Date.now()).then(r => r.ok ? r.json() : null)
    ]);

    const files = apiResp.status === 'fulfilled' ? apiResp.value : null;
    const data  = dataResp.status === 'fulfilled' ? dataResp.value : null;

    if (!files || !Array.isArray(files)) {{
      // GitHub API failed (rate limit etc.) — show what we can
      document.getElementById('error-area').innerHTML =
        '<div class="error-box">&#9888; Could not reach GitHub API to list reports. ' +
        'If you know the filename, you can access reports directly at: ' +
        '<code>https://mramage1207-ux.github.io/oregon-wildfire-reports/oregon_wildfire_report_YYYY-MM-DD.pdf</code></div>';
      // Still try to render chart if we have data.json
      if (data && data.historical_data) drawChart(data.historical_data);
      document.getElementById('hero-card').innerHTML =
        '<div style="color:rgba(255,255,255,.6);font-size:.9rem;">Unable to load report list &mdash; see error above.</div>';
      document.getElementById('archive-container').innerHTML =
        '<div class="table-card"><table><tbody><tr class="loading-row"><td colspan="4">Unable to load PDF list from GitHub API.</td></tr></tbody></table></div>';
      return;
    }}

    // Filter and sort PDFs
    const pdfs = files
      .filter(f => PDF_RE.test(f.name))
      .sort((a, b) => b.name.localeCompare(a.name))
      .map(f => ({{ filename: f.name, date_iso: f.name.match(PDF_RE)[1] }}));

    if (pdfs.length === 0) {{
      document.getElementById('archive-container').innerHTML =
        '<div class="table-card"><table><tbody><tr class="loading-row"><td colspan="4">No reports found in repository yet.</td></tr></tbody></table></div>';
      return;
    }}

    render(pdfs, data);

  }} catch(err) {{
    document.getElementById('error-area').innerHTML =
      '<div class="error-box">&#9888; Error loading page: ' + err.message + '</div>';
    console.error(err);
  }}
}})();
</script>
</body>
</html>"""

# ── Write files locally first ───────────────────────────────────────────────
os.makedirs(LOCAL_REPO, exist_ok=True)

# Clone or pull repo
# If LOCAL_REPO has a lock that can't be removed (e.g. sandbox permission issue),
# fall back to a fresh temp-dir clone so the push always succeeds.
import glob as _glob
import tempfile as _tempfile

def _try_use_existing_repo():
    """Returns True if the existing LOCAL_REPO is usable, False to trigger fresh clone."""
    if not os.path.isdir(os.path.join(LOCAL_REPO, ".git")):
        return False  # doesn't exist yet
    # 1. Remove stale lock files
    for _lock in _glob.glob(os.path.join(LOCAL_REPO, ".git", "**", "*.lock"), recursive=True):
        try:
            os.remove(_lock)
            print(f"  Removed stale lock: {_lock}")
        except OSError as _e:
            print(f"  WARNING: Cannot remove lock {_lock}: {_e}")
            return False  # can't fix lock — fall back to fresh clone
    # 2. Abort any in-progress rebase or merge
    subprocess.run(["git", "-C", LOCAL_REPO, "rebase", "--abort"], capture_output=True)
    subprocess.run(["git", "-C", LOCAL_REPO, "merge", "--abort"], capture_output=True)
    # 3. Fetch remote then hard-reset to it
    r1 = subprocess.run(["git", "-C", LOCAL_REPO, "fetch", "origin"], capture_output=True)
    r2 = subprocess.run(["git", "-C", LOCAL_REPO, "reset", "--hard", "origin/main"], capture_output=True)
    if r1.returncode != 0 or r2.returncode != 0:
        return False
    print("  Repo synced to origin/main")
    return True

_use_existing = _try_use_existing_repo()
if not _use_existing:
    # Fresh clone — use temp dir if LOCAL_REPO is unusable
    if os.path.isdir(os.path.join(LOCAL_REPO, ".git")):
        print(f"  Existing repo unusable (lock issue); cloning fresh to temp dir ...")
        LOCAL_REPO = _tempfile.mkdtemp(prefix="gh_push_")
    else:
        print(f"Cloning repo to {LOCAL_REPO} ...")
    subprocess.run(["git", "clone", REMOTE_AUTH, LOCAL_REPO], check=True)

# Write index.html
with open(os.path.join(LOCAL_REPO, "index.html"), "w") as fh:
    fh.write(html)
print("  index.html written")

# Write data.json
with open(os.path.join(LOCAL_REPO, "data.json"), "w") as fh:
    fh.write(data_json_str)
print("  data.json written")

# Copy all local PDFs into repo
for pdf_path in pdf_files:
    fname = os.path.basename(pdf_path)
    shutil.copy2(pdf_path, os.path.join(LOCAL_REPO, fname))
    print(f"  copied {fname}")

# Git commit & push
subprocess.run(["git", "-C", LOCAL_REPO, "add", "-A"], check=True)

status = subprocess.run(
    ["git", "-C", LOCAL_REPO, "diff", "--cached", "--quiet"],
    capture_output=True
)
if status.returncode == 0:
    print("No new changes to commit.")
else:
    commit_msg = f"Daily update: {TODAY}"
    subprocess.run(["git", "-C", LOCAL_REPO, "commit", "-m", commit_msg], check=True)
    print(f"Committed: {commit_msg}")

# Always push — this also flushes any commits that were made but not yet
# pushed (e.g. from a previous automated run that committed but couldn't push).
# Use REMOTE_AUTH (token in URL) because LaunchAgent sessions lack Keychain access.
print("Pushing to GitHub ...")
subprocess.run(["git", "-C", LOCAL_REPO, "push", REMOTE_AUTH, "main"], check=True)
print("Done.")

