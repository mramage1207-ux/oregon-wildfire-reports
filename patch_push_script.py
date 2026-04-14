#!/usr/bin/env python3
"""
One-time patch: adds the Claude AI attribution line to the footer
in push_to_github.py and commits the updated index.html to GitHub.

Run once from Terminal:
    python3 ~/Documents/Claude/outputs/patch_push_script.py
"""

import os
import re
import subprocess
import sys

PUSH_SCRIPT = os.path.expanduser("~/Documents/Claude/outputs/push_to_github.py")
REPO_DIR    = os.path.expanduser("~/Documents/Claude/outputs")

# ── 1. Patch push_to_github.py ────────────────────────────────────────────────
OLD_FOOTER = "Reports generated automatically. Not an official State of Oregon publication."
NEW_FOOTER = (
    "Reports generated automatically. Not an official State of Oregon publication.<br>\\n"
    "  Report assembly assisted by <strong style=\\"color:rgba(255,255,255,.7);\\">Claude AI</strong> "
    "(Anthropic) &mdash; data sourced from public government feeds."
)

if not os.path.exists(PUSH_SCRIPT):
    sys.exit(f"ERROR: Could not find {PUSH_SCRIPT}")

with open(PUSH_SCRIPT, "r") as f:
    src = f.read()

if "Claude AI" in src:
    print("push_to_github.py already contains the Claude AI attribution — skipping script patch.")
else:
    if OLD_FOOTER not in src:
        sys.exit(
            "ERROR: Could not locate the expected footer string in push_to_github.py.\n"
            "The script may have changed. Please add the attribution manually."
        )
    patched = src.replace(OLD_FOOTER, NEW_FOOTER, 1)
    with open(PUSH_SCRIPT, "w") as f:
        f.write(patched)
    print("✓ push_to_github.py patched — future runs will include the Claude AI attribution.")

# ── 2. Find the cloned repo directory ────────────────────────────────────────
# The push script commits to a local clone of the GitHub Pages repo.
# Common locations to check:
candidate_dirs = [
    os.path.expanduser("~/Documents/Claude/outputs/oregon-wildfire-reports"),
    os.path.expanduser("~/Documents/oregon-wildfire-reports"),
    os.path.expanduser("~/oregon-wildfire-reports"),
]

repo_dir = None
for d in candidate_dirs:
    if os.path.isdir(os.path.join(d, ".git")):
        repo_dir = d
        break

if repo_dir is None:
    # Fall back: let the push script locate it by running it once
    print("\nCould not auto-locate the git repo clone. Running push_to_github.py now to")
    print("regenerate and commit index.html with the new footer...")
    result = subprocess.run(
        [sys.executable, PUSH_SCRIPT],
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.returncode != 0:
        print("STDERR:", result.stderr)
    sys.exit(0)

# ── 3. Update index.html in the repo clone & commit ──────────────────────────
index_path = os.path.join(repo_dir, "index.html")
if not os.path.exists(index_path):
    print(f"No index.html found at {index_path}. Running push script instead.")
    subprocess.run([sys.executable, PUSH_SCRIPT])
    sys.exit(0)

with open(index_path, "r") as f:
    html = f.read()

if "Claude AI" in html:
    print("index.html already has the Claude AI attribution — no HTML change needed.")
else:
    OLD_HTML = "Reports generated automatically. Not an official State of Oregon publication."
    NEW_HTML = (
        "Reports generated automatically. Not an official State of Oregon publication.<br>\n"
        "  Report assembly assisted by <strong style=\"color:rgba(255,255,255,.7);\">Claude AI</strong> "
        "(Anthropic) &mdash; data sourced from public government feeds."
    )
    if OLD_HTML not in html:
        print("WARNING: Could not find footer text in index.html — running push script to regenerate.")
        subprocess.run([sys.executable, PUSH_SCRIPT])
        sys.exit(0)
    html = html.replace(OLD_HTML, NEW_HTML, 1)
    with open(index_path, "w") as f:
        f.write(html)
    print(f"✓ index.html updated at {index_path}")

    # Commit and push
    cmds = [
        ["git", "-C", repo_dir, "add", "index.html"],
        ["git", "-C", repo_dir, "commit", "-m", "Add Claude AI attribution to footer"],
        ["git", "-C", repo_dir, "push"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"ERROR running {' '.join(cmd)}:\n{result.stderr}")
            sys.exit(1)
        print(result.stdout.strip())

    print("\n✓ Done! The Claude AI attribution is now live on the website.")
    print("  GitHub Pages typically refreshes within 1–2 minutes.")
