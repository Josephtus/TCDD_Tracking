import logging
from datetime import datetime, timezone, timedelta
from playwright.async_api import async_playwright
import asyncio

STATION_IDS = {
    "ANKARA GAR": 98,
    "ESKİŞEHİR": 93,
    "İSTANBUL(SÖĞÜTLÜÇEŞME)": 234,
    "İSTANBUL(PENDİK)": 250,
    "SİVAS": 298,
    "KONYA": 169
}

async def check_train_tickets(kalkis, varis, tarih, baslangic_saati, bitis_saati, yolcu_sayisi, vagon_tipi):
    try:
        dt = datetime.strptime(tarih, "%d.%m.%Y")
        formatted_date = dt.strftime("%d-%m-%Y 00:00:00")
    except:
        formatted_date = tarih

    # KRİTİK DÜZELTME: Yolcu ID'si tekrar 0 (Tam Bilet / Tüm Koltuklar) yapıldı!
    payload = {
        "blTrainTypes": ["YHT", "ANAHAT", "BOLGESEL", "TURISTIK_TREN"], 
        "passengerTypeCounts": [{"id": 0, "count": yolcu_sayisi}],
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
            logging.info("🌐 TCDD sitesine bağlanılıyor...")
            await page.goto("https://ebilet.tcddtasimacilik.gov.tr/", wait_until="networkidle", timeout=30000)
            
            for _ in range(10):
                if token:
                    break
                await asyncio.sleep(1)

            if not token:
                logging.error("❌ Token yakalanamadı!")
                return []
            
            logging.info("✅ Token alındı, API sorgusu yapılıyor...")
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

                            # 1. TCDD'nin boş koltukları sakladığı asıl yeri (availableFareInfo) buluyoruz
                            fare_info_list = train.get("availableFareInfo", [])
                            cabin_list = []
                            
                            if fare_info_list:
                                # Standart tarife altındaki vagon tiplerini alıyoruz
                                cabin_list = fare_info_list[0].get("cabinClasses", [])
                            elif train.get("cabinClassAvailabilities"):
                                # Bazen API güncellemelerinde burada da dönebiliyor, alternatif olarak ekliyoruz
                                cabin_list = train.get("cabinClassAvailabilities", [])

                            # 2. Vagonları dönüp boş koltukları topluyoruz
                            for cabin in cabin_list:
                                cabin_info = cabin.get("cabinClass", {})
                                # İsimleri büyük harfe çevirip olası Türkçe karakter farklılıklarını önlüyoruz
                                cabin_name = str(cabin_info.get("name", "")).strip().upper()
                                
                                bos_koltuk_sayisi = cabin.get("availabilityCount", 0)
                                
                                if "EKONOM" in cabin_name:
                                    eko_koltuk += bos_koltuk_sayisi
                                elif "BUS" in cabin_name:
                                    bus_koltuk += bos_koltuk_sayisi
                                else:
                                    diger_koltuk += bos_koltuk_sayisi

                            toplam_kapasite = eko_koltuk + bus_koltuk + diger_koltuk
                            
                            if toplam_kapasite >= yolcu_sayisi:
                                # Kullanıcının butonlardan seçtiği vagon tipine göre eleme yapıyoruz
                                if vagon_tipi == "Ekonomi" and eko_koltuk < yolcu_sayisi:
                                    continue
                                if vagon_tipi == "Business" and bus_koltuk < yolcu_sayisi:
                                    continue

                                tren_adi = train.get("commercialName") or train.get("name", "Bilinmeyen Tren")
                                
                                saat = "00:00"
                                segments = train.get("segments", [])
                                if segments:
                                    dep_time_ms = segments[0].get("departureTime")
                                    if dep_time_ms:
                                        dt_obj = datetime.fromtimestamp(dep_time_ms / 1000.0, tz=tz_tr)
                                        saat = dt_obj.strftime("%H:%M")
                                
                                if not (baslangic_saati <= saat <= bitis_saati):
                                    continue 
                                
                                # Telegram mesajına yansıyacak detaylı döküm: "35 (Eko: 35, Bus: 0)"
                                koltuk_detay = f"{toplam_kapasite} (Eko: {eko_koltuk}, Bus: {bus_koltuk})"
                                
                                found_trains.append({
                                    "tren_tipi": tren_adi,
                                    "saat": saat,
                                    "bos_koltuk": koltuk_detay,
                                    "vagon_tipi": vagon_tipi
                                })
            
            return found_trains

        except Exception as e:
            logging.error(f"📡 Sistem hatası: {e}")
            return []
        finally:
            await browser.close()