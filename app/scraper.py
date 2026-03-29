import logging
from datetime import datetime, timezone, timedelta
from playwright.async_api import async_playwright
import asyncio
import json
import os

STATION_IDS = {
    "ANKARA GAR": 98,
    "ESKİŞEHİR": 93,
    "İSTANBUL(SÖĞÜTLÜÇEŞME)": 1325,
    "İSTANBUL(PENDİK)": 48,
    "SİVAS": 566,
    "KONYA": 796,
    "ERYAMAN YHT": 1306,
}
try:
    with open("app/station_dict.json", "r", encoding="utf-8") as f:
        STATION_IDS.update(json.load(f))
except Exception as e:
    logging.warning(f"station_dict.json yüklenemedi: {e}")

async def check_train_tickets(kalkis, varis, tarih, baslangic_saati, bitis_saati, yolcu_sayisi, vagon_tipi):
    try:
        dt = datetime.strptime(tarih, "%d.%m.%Y")
        formatted_date = dt.strftime("%d-%m-%Y 00:00:00")
    except:
        formatted_date = tarih

    payload = {
        "searchRoutes": [{
            "departureStationId": STATION_IDS.get(kalkis.upper(), 98),
            "departureStationName": kalkis.upper(),
            "arrivalStationId": STATION_IDS.get(varis.upper(), 93),
            "arrivalStationName": varis.upper(),
            "departureDate": formatted_date
        }],
        "passengerTypeCounts": [{"id": 0, "count": yolcu_sayisi}],
        "searchReservation": False,
        "blTrainTypes": ["TURISTIK_TREN"],
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--ignore-certificate-errors'
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={'width': 1920, 'height': 1080}
        )
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        page = await context.new_page()
        token = None
        cloned_headers = {}

        async def intercept(request):
            nonlocal token, cloned_headers
            if "tcddtasimacilik.gov.tr" in request.url:
                auth = request.headers.get("authorization")
                if auth and "eyJ" in auth and not token:
                    token = auth
                    cloned_headers = request.headers

        page.on("request", intercept)

        try:
            await page.goto("https://ebilet.tcddtasimacilik.gov.tr/", wait_until="networkidle", timeout=30000)

            for _ in range(10):
                if token:
                    break
                await asyncio.sleep(1)

            if not token:
                logging.error("❌ Token yakalanamadı!")
                return []

            url = "https://web-api-prod-ytp.tcddtasimacilik.gov.tr/tms/train/train-availability?environment=dev&userId=1"

            response_data = await page.evaluate('''async ([api_url, req_payload, headers_dict]) => {
                let finalHeaders = { ...headers_dict };
                finalHeaders['Content-Type'] = 'application/json';
                finalHeaders['Accept'] = 'application/json, text/plain, */*';
                delete finalHeaders['content-length'];
                delete finalHeaders['host'];
                delete finalHeaders['origin'];

                const response = await fetch(api_url, {
                    method: 'POST',
                    headers: finalHeaders,
                    body: JSON.stringify(req_payload)
                });
                return {
                    status: response.status,
                    data: await response.json().catch(() => null)
                };
            }''', [url, payload, cloned_headers])

            if response_data['status'] != 200:
                logging.error(f"❌ API Hatası (HTTP {response_data['status']})")
                return []

            data = response_data['data']
            found_trains = []
            tz_tr = timezone(timedelta(hours=3))

            if data and "trainLegs" in data:
                for leg in data.get("trainLegs", []):
                    for availability in leg.get("trainAvailabilities", []):
                        for train in availability.get("trains", []):

                            eko_koltuk = 0
                            bus_koltuk = 0
                            diger_koltuk = 0
                            fiyat_eko = 0
                            fiyat_bus = 0
                            sinif_koltuk_sayilari = {}

                            for car in train.get("cars", []):
                                for avail in car.get("availabilities", []):
                                    bos_koltuk_sayisi = avail.get("availability", 0)
                                    if bos_koltuk_sayisi <= 0:
                                        continue

                                    c_info = avail.get("cabinClass")
                                    cabin_name = ""
                                    if c_info:
                                        cabin_name = str(c_info.get("name", "")).upper()

                                    plist = avail.get("pricingList", [])
                                    if not cabin_name or cabin_name == "NONE":
                                        if plist:
                                            bcl = plist[0].get("bookingClass", {})
                                            cabin_name = str(bcl.get("name", "")).upper()

                                    price_val = 0
                                    if plist:
                                        price_val = (plist[0].get("crudePrice", {}) or {}).get("priceAmount") or \
                                                    (plist[0].get("basePrice", {}) or {}).get("priceAmount", 0)

                                    display_name = cabin_name.title()
                                    if "YATAKL" in cabin_name:                display_name = "Yataklı"
                                    elif "PULMAN" in cabin_name:              display_name = "Pulman"
                                    elif "LOCA" in cabin_name:               display_name = "Loca"
                                    elif "BUS" in cabin_name:                display_name = "Business"
                                    elif "TEKERLEKLİ" in cabin_name or "ENGELLİ" in cabin_name:
                                                                              display_name = "Tek. Sandalye"
                                    elif "EKONOM" in cabin_name or "EKO" in cabin_name or "STANDART" in cabin_name:
                                                                              display_name = "Ekonomi"

                                    sinif_koltuk_sayilari[display_name] = sinif_koltuk_sayilari.get(display_name, 0) + bos_koltuk_sayisi

                                    if "BUS" in cabin_name:
                                        bus_koltuk += bos_koltuk_sayisi
                                        if not fiyat_bus and price_val: fiyat_bus = price_val
                                    elif "EKONOM" in cabin_name or "PULMAN" in cabin_name or "STANDART" in cabin_name or "EKO" in cabin_name:
                                        eko_koltuk += bos_koltuk_sayisi
                                        if not fiyat_eko and price_val: fiyat_eko = price_val
                                    else:
                                        diger_koltuk += bos_koltuk_sayisi

                            toplam_kapasite = eko_koltuk + bus_koltuk + diger_koltuk

                            if toplam_kapasite >= yolcu_sayisi:
                                if vagon_tipi != "Fark Etmez":
                                    aranan_vagon = "Tek. Sandalye" if "Tekerlekl" in vagon_tipi else vagon_tipi
                                    mevcut_koltuk = sinif_koltuk_sayilari.get(aranan_vagon, 0)
                                    if mevcut_koltuk < yolcu_sayisi:
                                        continue

                                tren_adi = train.get("commercialName") or train.get("name", "Bilinmeyen Tren")

                                final_fiyat = fiyat_eko if vagon_tipi == "Ekonomi" and fiyat_eko else fiyat_bus
                                if not final_fiyat:
                                    final_fiyat = fiyat_eko or fiyat_bus or "Bilgi Yok"

                                saat = "00:00"
                                varis_saat = "00:00"
                                segments = train.get("segments", [])
                                if segments:
                                    dep_time_ms = segments[0].get("departureTime")
                                    if dep_time_ms:
                                        dt_obj = datetime.fromtimestamp(dep_time_ms / 1000.0, tz=tz_tr)
                                        saat = dt_obj.strftime("%H:%M")
                                    arv_time_ms = segments[-1].get("arrivalTime")
                                    if arv_time_ms:
                                        arv_obj = datetime.fromtimestamp(arv_time_ms / 1000.0, tz=tz_tr)
                                        varis_saat = arv_obj.strftime("%H:%M")

                                if not (baslangic_saati <= saat <= bitis_saati):
                                    continue

                                detay_str = ", ".join([f"{isim}: {sayi}" for isim, sayi in sinif_koltuk_sayilari.items()])
                                koltuk_detay = f"{toplam_kapasite} Boş Koltuk" if not detay_str else f"{toplam_kapasite} Boş Koltuk ({detay_str})"

                                found_trains.append({
                                    "tren_tipi": tren_adi,
                                    "saat": saat,
                                    "varis_saat": varis_saat,
                                    "fiyat": final_fiyat,
                                    "bos_koltuk": koltuk_detay,
                                    "vagon_tipi": vagon_tipi
                                })

            return found_trains

        except Exception as e:
            logging.error(f"📡 Sistem hatası: {e}")
            return []
        finally:
            await browser.close()