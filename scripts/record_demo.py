"""Record a ~60 second demo video of the workbench with Playwright.

    python scripts/record_demo.py --username 何深 --password '...'
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8020")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--out", default="docs/demo.mp4")
    args = parser.parse_args()

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder, sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800},
                                      record_video_dir=folder,
                                      record_video_size={"width": 1280, "height": 800})
        page = context.new_page()
        page.set_default_timeout(15000)

        page.goto(args.base)
        page.fill("[data-testid=login-username]", args.username)
        page.fill("[data-testid=login-password]", args.password)
        page.click("[data-testid=login-submit]")
        page.wait_for_selector("[data-testid=drift-summary]")
        page.wait_for_timeout(2500)

        # Theme toggle (light -> back to dark)
        page.click("[data-testid=theme-toggle]")
        page.wait_for_function("() => document.documentElement.dataset.theme === 'light'")
        page.wait_for_timeout(2200)
        page.click("[data-testid=theme-toggle]")
        page.wait_for_function("() => document.documentElement.dataset.theme === 'dark'")
        page.wait_for_timeout(1200)

        # Drift -> side-by-side diff
        page.click("[data-testid=drift-item] [data-testid=diff-button]")
        page.wait_for_selector("[data-testid=diff-body]")
        page.wait_for_timeout(4000)
        page.keyboard.press("Escape")
        page.wait_for_timeout(800)

        # Rules (including cross-device link rule)
        page.click("[data-testid=run-rules]")
        page.wait_for_selector("[data-testid=finding]")
        page.wait_for_timeout(2200)
        page.evaluate("document.querySelector('[data-testid=rules-result]')?.scrollIntoView({block:'center'})")
        page.wait_for_timeout(2500)

        # Create a change request from a finding, submit and approve
        page.click("[data-testid=generate-change]")
        page.wait_for_selector("[data-testid=cr-title]")
        page.wait_for_timeout(1200)
        page.fill("[data-testid=cr-title]", "演示：补齐 eth2 的 OSPF cost")
        page.wait_for_timeout(800)
        page.click("[data-testid=cr-submit]")
        page.wait_for_selector("[data-testid=transition-submit]")
        page.click("[data-testid=transition-submit]")
        page.fill("[data-testid=transition-note]", "演示：提交审批")
        page.click("[data-testid=transition-confirm]")
        page.wait_for_selector("[data-testid=transition-approve]")
        page.wait_for_timeout(1200)
        page.click("[data-testid=transition-approve]")
        page.fill("[data-testid=transition-note]", "演示：人工核对后同意")
        page.click("[data-testid=transition-confirm]")
        page.wait_for_selector("[data-testid=audit-list]")
        page.evaluate("document.querySelector('[data-testid=audit-list]')?.scrollIntoView({block:'center'})")
        page.wait_for_timeout(2800)

        # Topology
        page.evaluate("document.querySelector('[data-testid=topo-svg]')?.scrollIntoView({block:'center'})")
        page.wait_for_timeout(3200)

        context.close()
        browser.close()

        videos = sorted(Path(folder).glob("*.webm"))
        if not videos:
            print("no video recorded")
            return 1
        if out.suffix == ".mp4":
            import subprocess
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(videos[0]),
                            "-vf", "scale=1280:-2", "-c:v", "libx264", "-crf", "30",
                            "-preset", "veryfast", "-an", "-movflags", "+faststart", str(out)], check=True)
        else:
            shutil.copyfile(videos[0], out)
        print(f"video: {out} ({out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
