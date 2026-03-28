import requests
import logging
from datetime import datetime
from playwright.async_api import async_playwright
import asyncio

# EKRAN GÖRÜNTÜSÜNDEN GELEN YENİ NESİL ID'LER
STATION_IDS = {
    "ANKARA GAR": 98,
    "ESKİŞEHİR": 93,
    "İSTANBUL(SÖĞÜTLÜÇEŞME)": 234,
    "İSTANBUL(PENDİK)": 250
}

async def get_fresh_token():
    """Yeni nesil Bearer Token'ı Playwright ile ağdan yakalar."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        page = await browser.new_page()
        token = None

        async def intercept(request):
            nonlocal token
            auth = request.headers.get("authorization")
            if auth and "bearer" in auth.lower():
                token = auth

        page.on("request", intercept)
        try:
            # Siteye git ve Token üretilmesi için sayfayı biraz beklet
            await page.goto("https://ebilet.tcddtasimacilik.gov.tr/", wait_until="networkidle")
            await asyncio.sleep(5) 
        except: pass
        await browser.close()
        return token

async def check_train_tickets(kalkis, varis, tarih):
    token = await get_fresh_token()
    if not token:
        logging.error("❌ Token yakalanamadı!")
        return []

    # EKRAN GÖRÜNTÜSÜNDEKİ YENİ API ADRESİ
    url = "https://web-api-prod-ytp.tcddtasimacilik.gov.tr/tms/train/train-availability?environment=dev&userId=1"
    
    # Tarih formatını ekran görüntüsündeki gibi ayarla: 27-03-2026 21:00:00
    try:
        dt = datetime.strptime(tarih, "%d.%m.%Y")
        formatted_date = dt.strftime("%d-%m-%Y 00:00:00")
    except:
        formatted_date = tarih

    # EKRAN GÖRÜNTÜSÜNDEKİ TAM PAYLOAD YAPISI
    payload = {
        "blTrainTypes": ["TURISTIK_TREN", "TURISTIK_TREN"], # Görüntüdeki gibi
        "passengerTypeCounts": [{"id": 0, "count": 1}],
        "searchReservation": False,
        "searchRoutes": [{
            "departureStationId": STATION_IDS.get(kalkis.upper(), 98),
            "departureStationName": kalkis.upper(),
            "arrivalStationId": STATION_IDS.get(varis.upper(), 93),
            "arrivalStationName": varis.upper(),
            "departureDate": formatted_date
        }],
        "searchType": "DOMESTIC"
    }

    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0"
    }

    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, lambda: requests.post(url, json=payload, headers=headers, timeout=15))
        data = response.json()

        # Yeni API'nin JSON yapısına göre biletleri ayıkla
        found_trains = []
        # Not: Yeni API yapısında 'trainAvailabilityList' gibi farklı anahtarlar olabilir
        # Bu kısım gelen JSON'a göre gerekirse güncellenir
        if "trainAvailabilityList" in data:
            for train in data["trainAvailabilityList"]:
                if train.get("totalAvailability", 0) > 0:
                    found_trains.append({
                        "tren_tipi": train.get("trainName"),
                        "saat": train.get("departureDate"),
                        "bos_koltuk": train.get("totalAvailability")
                    })
        return found_trains
    except Exception as e:
        logging.error(f"📡 Yeni API Hatası: {e}")
        return []