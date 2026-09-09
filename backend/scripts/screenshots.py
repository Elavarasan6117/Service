#!/usr/bin/env python3
"""Capture screenshots of the running application.

Used to produce documentation images and as a crude visual regression check.
Requires the backend on :8000 and the frontend dev server on :5173.

    python scripts/screenshots.py [output_dir]
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:5173"
USERNAME = "admin"
PASSWORD = "demo-password-1234"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "screenshots")

# Customer codes are unique per run, so the script can be run repeatedly
# against the same database without colliding on customer_code.
RUN = str(int(time.time()))[-5:]


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = await browser.new_page(viewport={"width": 1600, "height": 1100})
        page.on("console", lambda m: print(f"  [console:{m.type}] {m.text}")
                if m.type == "error" else None)

        # --- 1. Sign-in -------------------------------------------------
        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_timeout(800)
        await page.screenshot(path=OUT / "01-login.png")
        print("captured 01-login.png")

        # --- 2. Dashboard -----------------------------------------------
        await page.fill("#username", USERNAME)
        await page.fill("#password", PASSWORD)
        await page.click("button[type=submit]")
        await page.wait_for_selector(".topbar", timeout=15000)
        # Give Leaflet time to lay out and the map/metrics calls to settle.
        await page.wait_for_timeout(4000)
        await page.screenshot(path=OUT / "02-dashboard.png")
        print("captured 02-dashboard.png")

        # --- 3. A serviceable result ------------------------------------
        await page.fill("#customerCode", f"CUST-A{RUN}")
        await page.fill("#customerName", "Anna Nagar Bakery")
        await page.fill("#phone", "+91 98410 55555")
        await page.fill("#address", "18 Third Avenue, Anna Nagar West")
        await page.fill("#area", "Anna Nagar")
        await page.fill("#pincode", "600040")
        await page.select_option("#serviceType", "DAILY")
        await page.fill("#latitude", "13.086500")
        await page.fill("#longitude", "80.211200")
        await page.wait_for_timeout(700)
        await page.press("#address", "Escape")
        await page.wait_for_timeout(300)
        await page.screenshot(path=OUT / "03-form-filled.png")
        print("captured 03-form-filled.png")

        await page.click("button[type=submit]")
        await page.wait_for_selector(".result-banner--available", timeout=20000)
        await page.wait_for_timeout(2500)
        await page.screenshot(path=OUT / "04-available.png")
        print("captured 04-available.png")

        # Close-up of the decision panel itself.
        panel = await page.query_selector(".result-banner")
        if panel:
            await panel.screenshot(path=OUT / "05-available-closeup.png")
            print("captured 05-available-closeup.png")

        # --- 4. A non-serviceable result --------------------------------
        # Placed ~2.5 km from Chennai Sweets in Adyar: far enough to be
        # rejected, close enough that a nearest location is still reported --
        # which is the more informative screen for operations.
        await page.fill("#customerCode", f"CUST-N{RUN}")
        await page.fill("#customerName", "Besant Nagar Wholesale")
        await page.fill("#address", "Elliot's Beach Road, Besant Nagar")
        await page.fill("#area", "Besant Nagar")
        await page.fill("#pincode", "600090")
        await page.fill("#latitude", "12.998000")
        await page.fill("#longitude", "80.271000")
        await page.wait_for_timeout(700)
        await page.press("#address", "Escape")
        await page.wait_for_timeout(300)
        await page.click("button[type=submit]")
        await page.wait_for_selector(
            ".result-banner--unavailable, .result-banner--warning", timeout=20000
        )
        await page.wait_for_timeout(2500)
        await page.screenshot(path=OUT / "06-not-available.png")
        print("captured 06-not-available.png")

        panel = await page.query_selector(".result-banner")
        if panel:
            await panel.screenshot(path=OUT / "07-not-available-closeup.png")
            print("captured 07-not-available-closeup.png")

        # --- 5. Recent checks table -------------------------------------
        tables = await page.query_selector_all(".card")
        if tables:
            await tables[-1].scroll_into_view_if_needed()
            await page.wait_for_timeout(600)
            await tables[-1].screenshot(path=OUT / "08-recent-checks.png")
            print("captured 08-recent-checks.png")

        # --- 6. Administration ------------------------------------------
        await page.click("a[href='/admin']")
        await page.wait_for_selector("table.data", timeout=15000)
        await page.wait_for_timeout(1500)
        await page.screenshot(path=OUT / "09-admin.png", full_page=True)
        print("captured 09-admin.png")

        # --- 7. Dark theme ----------------------------------------------
        await page.emulate_media(color_scheme="dark")
        await page.click("a[href='/']")
        await page.wait_for_timeout(3500)
        await page.screenshot(path=OUT / "10-dashboard-dark.png")
        print("captured 10-dashboard-dark.png")

        await browser.close()

    print(f"\nScreenshots written to {OUT.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
