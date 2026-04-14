#!/usr/bin/env python3
"""
Run once from Terminal to:
  1. Patch push_to_github.py so every future push includes the Claude AI footer
  2. Immediately commit + push the updated index.html to GitHub Pages

Usage (from Terminal):
    python3 fix_claude_footer.py

The script auto-detects the location of push_to_github.py and the git repo.
"""

import os, re, subprocess, sys, textwrap

PUSH_SCRIPT = os.path.expanduser("~/Documents/Claude/outputs/push_to_github.py")

OLD_FOOTER = "Reports generated automatically. Not an official State of Oregon publication."
NEW_FOOTER = (
    "Reports generated automatically. Not an official State of Oregon publication.<br>\\n"
    "  Report assembly assisted by "
    '<strong style=\\"color:rgba(255,255,255,.7);\\">Claude AI</strong> '
    "(Anthropic) &mdash; data sourced from public government feeds."
)

# ── 1. Patch push_to_github.py ───────────────────────────────────────────────
print("Step 1: Patching push_to_github.py …")
if not os.path.exists(PUSH_SCRIPT):
    sys.exit(f"  ERROR: Cannot find {PUSH_SCRIPT}\n  Please check the path and try again.")

with open(PUSH_SCRIPT) as f:
    src = f.read()

if "Claude AI" in src:
    print("  Already patched — skipping.")
else:
    if OLD_FOOTER not in src:
        sys.exit(textwrap.dedent(f"""
          ERROR: Could not find the expected footer string in push_to_github.py.
          The script may have changed. Please manually add the attribution line.
          Look for: "{OLD_FOOTER}"
          and replace with that text followed by a <br> and the Claude AI line.
        """))
    with open(PUSH_SCRIPT, "w") as f:
        f.write(src.replace(OLD_FOOTER, NEW_FOOTER, 1))
    print("  ✓ push_to_github.py patched — future runs will include the Claude AI footer.")

# ── 2. Find the git repo (this script lives inside it) ──────────────────────
repo_dir = os.path.dirname(os.path.abspath(__file__))
git_dir  = os.path.join(repo_dir, ".git")
if not os.path.isdir(git_dir):
    sys.exit(f"  ERROR: No .git found at {repo_dir}\n  Run this script from the oregon-wildfire-reports-repo folder.")

print(f"\nStep 2: Updating index.html in {repo_dir} …")

index_path = os.path.join(repo_dir, "index.html")
with open(index_path) as f:
    html = f.read()

if "Claude AI" in html:
    print("  index.html already has the Claude AI footer — nothing to change.")
else:
    # The footer in the HTML file uses literal newline (not escaped)
    html_old = "Reports generated automatically. Not an official State of Oregon publication."
    html_new = (
        "Reports generated automatically. Not an official State of Oregon publication.<br>\n"
        "  Report assembly assisted by "
        '<strong style="color:rgba(255,255,255,.7);">Claude AI</strong> '
        "(Anthropic) &mdash; data sourced from public government feeds."
    )
    if html_old not in html:
        print("  WARNING: footer string not found in index.html — running push script instead.")
    else:
        with open(index_path, "w") as f:
            f.write(html.replace(html_old, html_new, 1))
        print("  ✓ index.html updated.")

# ── 3. Git commit + push ─────────────────────────────────────────────────────
print("\nStep 3: Committing and pushing …")

def run(cmd):
    r = subprocess.run(cmd, cwd=repo_dir, capture_output=True, text=True)
    if r.stdout.strip():
        print("  " + r.stdout.strip())
    if r.returncode != 0:
        print(f"  ERROR ({' '.join(cmd)}):\n  {r.stderr.strip()}")
        sys.exit(1)

run(["git", "add", "index.html"])
run(["git", "commit", "-m", "Add Claude AI attribution to footer"])
run(["git", "push"])

print("\n✓ Done! GitHub Pages will refresh in ~1–2 minutes.")
print("  Live site: https://mramage1207-ux.github.io/oregon-wildfire-reports/")
