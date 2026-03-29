import asyncio
from playwright.async_api import async_playwright
import json

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()

        async def handle_response(response):
            if "station" in response.url.lower() and response.status == 200:
                try:
                    data = await response.json()
                    if isinstance(data, list) and len(data) > 10:
                        pairs = {s['name']: s['id'] for s in data if 'name' in s and 'id' in s}
                        with open("app/station_dict.json", "w", encoding="utf-8") as f:
                            json.dump(pairs, f, ensure_ascii=False)
                        print(f"✅ {len(pairs)} istasyon kaydedildi.")
                except Exception as e:
                    print("Hata:", e)

        page.on("response", handle_response)
        await page.goto("https://ebilet.tcddtasimacilik.gov.tr/", wait_until="networkidle")
        await asyncio.sleep(5)
        await browser.close()

asyncio.run(run())
