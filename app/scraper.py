"""
scraper.py — Güvenli ve Optimize TCDD Bilet Takip Scraper
==========================================================
Mimari:
  1. GlobalTokenManager  → Playwright'ı sadece token süresi dolduğunda başlatır.
                           Token + header'lar bellekte önbelleğe alınır.
  2. check_train_tickets → aiohttp ile doğrudan API isteği atar.
                           HTTP 401 gelirse token yeniler ve tekrar dener.
  3. check_alarms_grouped → Aynı (kalkış, varış, tarih) grubunu tek API isteğiyle
                             sorgular; dönen sonucu ilgili alarmların kriterleriyle
                             eşleştirir (Sorgu Gruplama Optimizasyonu).
"""

import asyncio
import json
import logging
import os
import ssl
import time
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Optional

import aiohttp
from aiohttp import TCPConnector
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

# ──────────────────────────────
# İstasyon ID Sözlüğü
# ──────────────────────────────
STATION_IDS: dict[str, int] = {
    "ANKARA GAR": 98,
    "ESKİŞEHİR": 93,
    "İSTANBUL(SÖĞÜTLÜÇEŞME)": 1325,
    "İSTANBUL(PENDİK)": 48,
    "SİVAS": 566,
    "KONYA": 796,
    "ERYAMAN YHT": 1306,
}
try:
    _json_path = os.path.join(os.path.dirname(__file__), "station_dict.json")
    with open(_json_path, "r", encoding="utf-8") as _f:
        STATION_IDS.update(json.load(_f))
except Exception as _e:
    logger.warning(f"station_dict.json yüklenemedi: {_e}")

# ──────────────────────────────
# TCDD API Sabitleri
# ──────────────────────────────
TCDD_API_URL = (
    "https://web-api-prod-ytp.tcddtasimacilik.gov.tr"
    "/tms/train/train-availability?environment=dev&userId=1"
)
TCDD_HOME_URL = "https://ebilet.tcddtasimacilik.gov.tr/"

# Token geçerlilik süresi (saniye). 55 dakika — JWT'ler genellikle 60 dk geçerlidir.
TOKEN_TTL = 3300


# ═══════════════════════════════════════════════════════
# 1. Global Token Yöneticisi
# ═══════════════════════════════════════════════════════
class GlobalTokenManager:
    """
    Playwright'ı sadece gerektiğinde (ilk çalıştırma veya token süresi dolduğunda)
    başlatır. Token ve HTTP header'ları bellekte saklar.
    Thread-safe: asyncio.Lock ile korunur.
    """

    def __init__(self):
        self._token: Optional[str] = None
        self._headers: dict = {}
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def is_valid(self) -> bool:
        return bool(self._token) and time.monotonic() < self._expires_at

    async def get_headers(self, force_refresh: bool = False) -> dict:
        """
        Önbellekteki header'ları döndürür.
        Token yoksa veya süresi dolmuşsa Playwright ile yeniler.
        """
        async with self._lock:
            if not force_refresh and self.is_valid:
                logger.debug("✅ Token önbellekte geçerli, Playwright başlatılmıyor.")
                return dict(self._headers)

            logger.info("🌐 Token yenileniyor — Playwright başlatılıyor...")
            await self._refresh_via_playwright()
            return dict(self._headers)

    async def _refresh_via_playwright(self):
        """TCDD ana sayfasına gider, API isteğini yakalar ve token'ı çalar."""
        captured_token: Optional[str] = None
        captured_headers: dict = {}

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--ignore-certificate-errors",
                ],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
            )
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            page = await context.new_page()

            def _intercept(request):
                nonlocal captured_token, captured_headers
                if "tcddtasimacilik.gov.tr" in request.url:
                    auth = request.headers.get("authorization", "")
                    if "eyJ" in auth and not captured_token:
                        captured_token = auth
                        captured_headers = dict(request.headers)

            page.on("request", _intercept)

            try:
                await page.goto(TCDD_HOME_URL, wait_until="networkidle", timeout=30000)
                for _ in range(15):
                    if captured_token:
                        break
                    await asyncio.sleep(1)
            except Exception as exc:
                logger.error(f"Playwright sayfa yükleme hatası: {exc}")
            finally:
                await browser.close()

        if not captured_token:
            logger.error("❌ Playwright ile token yakalanamadı!")
            raise RuntimeError("Token alınamadı")

        # Gereksiz / düşük seviye header'ları temizle
        for bad_key in ("content-length", "host", "origin", "content-type"):
            captured_headers.pop(bad_key, None)

        # aiohttp ile kullanılacak header seti
        captured_headers["Content-Type"] = "application/json"
        captured_headers["Accept"] = "application/json, text/plain, */*"

        self._token = captured_token
        self._headers = captured_headers
        self._expires_at = time.monotonic() + TOKEN_TTL
        logger.info(
            f"✅ Yeni token alındı — {TOKEN_TTL // 60} dakika geçerli. "
            f"Sona erecek: {datetime.now() + timedelta(seconds=TOKEN_TTL):%H:%M:%S}"
        )


# Uygulama genelinde tek örnek
token_manager = GlobalTokenManager()


# ═══════════════════════════════════════════════════════
# 2. Hafif HTTP İsteği — aiohttp ile Bilet Sorgulama
# ═══════════════════════════════════════════════════════
def _build_payload(
    kalkis: str,
    varis: str,
    tarih: str,
    yolcu_sayisi: int,
) -> dict:
    """TCDD API için POST payload'ı oluşturur."""
    try:
        dt = datetime.strptime(tarih, "%d.%m.%Y")
        formatted_date = dt.strftime("%d-%m-%Y 00:00:00")
    except ValueError:
        formatted_date = tarih

    return {
        "searchRoutes": [
            {
                "departureStationId": STATION_IDS.get(kalkis.upper(), 98),
                "departureStationName": kalkis.upper(),
                "arrivalStationId": STATION_IDS.get(varis.upper(), 93),
                "arrivalStationName": varis.upper(),
                "departureDate": formatted_date,
            }
        ],
        "passengerTypeCounts": [{"id": 0, "count": yolcu_sayisi}],
        "searchReservation": False,
        "blTrainTypes": ["TURISTIK_TREN"],
    }


async def _fetch_availability(
    kalkis: str,
    varis: str,
    tarih: str,
    yolcu_sayisi: int,
    session: aiohttp.ClientSession,
    retry: bool = True,
) -> Optional[dict]:
    """
    aiohttp ile TCDD API'sine POST atar.
    HTTP 401 → token yenile + tekrar dene.
    HTTP 403 → tarayıcı header'larını tam taklit et + tekrar dene.
    """
    headers = await token_manager.get_headers()

    # TCDD sunucusunun 403 vermemesi için eksik browser header'larını ekle
    headers.setdefault("Referer",          "https://ebilet.tcddtasimacilik.gov.tr/")
    headers.setdefault("Origin",           "https://ebilet.tcddtasimacilik.gov.tr")
    headers.setdefault("Accept-Language",  "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7")
    headers.setdefault("Accept-Encoding",  "gzip, deflate, br")
    headers.setdefault("sec-ch-ua",        '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"')
    headers.setdefault("sec-ch-ua-mobile", "?0")
    headers.setdefault("sec-ch-ua-platform", '"Windows"')
    headers.setdefault("Sec-Fetch-Dest",   "empty")
    headers.setdefault("Sec-Fetch-Mode",   "cors")
    headers.setdefault("Sec-Fetch-Site",   "same-site")
    headers.setdefault("Connection",       "keep-alive")

    payload = _build_payload(kalkis, varis, tarih, yolcu_sayisi)

    try:
        async with session.post(
            TCDD_API_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=25),
            ssl=False,          # TCDD'nin SSL sertifikası sorunlarını atla
        ) as resp:
            if resp.status == 401 and retry:
                logger.warning("⚠️ HTTP 401 — Token süresi dolmuş, yenileniyor...")
                await token_manager.get_headers(force_refresh=True)
                return await _fetch_availability(kalkis, varis, tarih, yolcu_sayisi, session, retry=False)

            if resp.status == 403 and retry:
                logger.warning("⚠️ HTTP 403 — Token zorla yenileniyor...")
                await token_manager.get_headers(force_refresh=True)
                return await _fetch_availability(kalkis, varis, tarih, yolcu_sayisi, session, retry=False)

            if resp.status == 403:
                logger.error(f"❌ API Hatası (403 Yasaklı): Token yetkisiz veya bloklandı | {kalkis}→{varis} {tarih}")
                return None
            elif resp.status not in (200, 400):
                resp_text = await resp.text()
                logger.error(f"❌ API Hatası: HTTP {resp.status} | {kalkis}→{varis} {tarih}")
                logger.error(f"❌ API Yanıtı: {resp_text}")
                return None

            try:
                return await resp.json(content_type=None)
            except Exception as exc:
                logger.error(f"JSON ayrıştırma hatası: {exc}")
                return None
    except aiohttp.ClientError as exc:
        logger.error(f"Bağlantı hatası ({kalkis}→{varis}): {exc}")
        return None


# ═══════════════════════════════════════════════════════
# 3. Yanıt Ayrıştırma — Tren Listesi Çıkarma
# ═══════════════════════════════════════════════════════
def _parse_trains(data: dict, yolcu_sayisi: int, vagon_tipi: str, baslangic_saati: str, bitis_saati: str) -> list[dict]:
    """
    Ham TCDD API yanıtından uygun trenleri filtreler.
    Saat aralığı, yolcu sayısı ve vagon tipi kriterlerine göre döndürür.
    """
    found_trains = []
    tz_tr = timezone(timedelta(hours=3))

    if not data or "trainLegs" not in data:
        return found_trains

    for leg in data.get("trainLegs", []):
        for availability in leg.get("trainAvailabilities", []):
            for train in availability.get("trains", []):
                eko_koltuk = bus_koltuk = diger_koltuk = 0
                fiyat_eko = fiyat_bus = 0
                sinif_koltuk_sayilari: dict[str, int] = {}

                for car in train.get("cars", []):
                    for avail in car.get("availabilities", []):
                        bos = avail.get("availability", 0)
                        if bos <= 0:
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
                            price_val = (
                                (plist[0].get("crudePrice") or {}).get("priceAmount")
                                or (plist[0].get("basePrice") or {}).get("priceAmount", 0)
                            )

                        # Görüntülenecek vagon adı
                        if "YATAKL" in cabin_name:
                            display_name = "Yataklı"
                        elif "PULMAN" in cabin_name:
                            display_name = "Pulman"
                        elif "LOCA" in cabin_name:
                            display_name = "Loca"
                        elif "BUS" in cabin_name:
                            display_name = "Business"
                        elif "TEKERLEKLİ" in cabin_name or "ENGELLİ" in cabin_name:
                            display_name = "Tek. Sandalye"
                        elif "EKONOM" in cabin_name or "EKO" in cabin_name or "STANDART" in cabin_name:
                            display_name = "Ekonomi"
                        else:
                            display_name = cabin_name.title() or "Diğer"

                        sinif_koltuk_sayilari[display_name] = sinif_koltuk_sayilari.get(display_name, 0) + bos

                        if "BUS" in cabin_name:
                            bus_koltuk += bos
                            if not fiyat_bus and price_val:
                                fiyat_bus = price_val
                        elif any(k in cabin_name for k in ("EKONOM", "PULMAN", "STANDART", "EKO")):
                            eko_koltuk += bos
                            if not fiyat_eko and price_val:
                                fiyat_eko = price_val
                        else:
                            diger_koltuk += bos

                toplam = eko_koltuk + bus_koltuk + diger_koltuk
                if toplam < yolcu_sayisi:
                    continue

                # Vagon tipi filtresi
                if vagon_tipi != "Fark Etmez":
                    aranan = "Tek. Sandalye" if "Tekerlekl" in vagon_tipi else vagon_tipi
                    if sinif_koltuk_sayilari.get(aranan, 0) < yolcu_sayisi:
                        continue

                # Kalkış / varış saatlerini hesapla
                saat = varis_saat = "00:00"
                segments = train.get("segments", [])
                if segments:
                    dep_ms = segments[0].get("departureTime")
                    if dep_ms:
                        saat = datetime.fromtimestamp(dep_ms / 1000.0, tz=tz_tr).strftime("%H:%M")
                    arv_ms = segments[-1].get("arrivalTime")
                    if arv_ms:
                        varis_saat = datetime.fromtimestamp(arv_ms / 1000.0, tz=tz_tr).strftime("%H:%M")

                # Saat aralığı filtresi
                if not (baslangic_saati <= saat <= bitis_saati):
                    continue

                tren_adi = train.get("commercialName") or train.get("name", "Bilinmeyen Tren")
                final_fiyat = fiyat_eko if vagon_tipi == "Ekonomi" and fiyat_eko else fiyat_bus
                if not final_fiyat:
                    final_fiyat = fiyat_eko or fiyat_bus or "Bilgi Yok"

                detay_str = ", ".join(f"{k}: {v}" for k, v in sinif_koltuk_sayilari.items())
                koltuk_detay = f"{toplam} Boş Koltuk" if not detay_str else f"{toplam} Boş Koltuk ({detay_str})"

                found_trains.append(
                    {
                        "tren_tipi": tren_adi,
                        "saat": saat,
                        "varis_saat": varis_saat,
                        "fiyat": final_fiyat,
                        "bos_koltuk": koltuk_detay,
                        "vagon_tipi": vagon_tipi,
                    }
                )

    return found_trains


# ═══════════════════════════════════════════════════════
# 4. Ana Sorgu Fonksiyonu — Tekil Alarm
# ═══════════════════════════════════════════════════════
async def check_train_tickets(
    kalkis: str,
    varis: str,
    tarih: str,
    baslangic_saati: str,
    bitis_saati: str,
    yolcu_sayisi: int,
    vagon_tipi: str,
) -> list[dict]:
    """
    Tek bir alarm için bilet sorgular (aiohttp kullanır).
    Toplu sorgu için check_alarms_grouped tercih edin.
    """
    try:
        connector = TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            data = await _fetch_availability(kalkis, varis, tarih, yolcu_sayisi, session)
        if data is None:
            return []
        return _parse_trains(data, yolcu_sayisi, vagon_tipi, baslangic_saati, bitis_saati)
    except Exception as exc:
        logger.error(f"check_train_tickets hatası: {exc}")
        return []


# ═══════════════════════════════════════════════════════
# 5. Gruplu Sorgu — Birden Fazla Alarm (Ana Optimizasyon)
# ═══════════════════════════════════════════════════════
async def check_alarms_grouped(tasks: list) -> dict[int, list[dict]]:
    """
    Gelen görev listesini (kalkış, varış, tarih) üçlüsüne göre gruplar.
    Her benzersiz rota için sadece 1 API isteği atar.
    """
    GroupKey = tuple
    groups: dict[GroupKey, list] = defaultdict(list)

    for task in tasks:
        key: GroupKey = (task.kalkis_gar.upper(), task.varis_gar.upper(), task.tarih)
        groups[key].append(task)

    results: dict[int, list[dict]] = {}

    connector = TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        for (kalkis, varis, tarih), group_tasks in groups.items():
            max_yolcu = max(t.yolcu_sayisi for t in group_tasks)

            logger.info(
                f"📡 Sorgu: {kalkis} → {varis} | {tarih} "
                f"| {len(group_tasks)} alarm, max {max_yolcu} yolcu"
            )

            data = await _fetch_availability(kalkis, varis, tarih, max_yolcu, session)

            for task in group_tasks:
                if data is None:
                    results[task.id] = []
                else:
                    results[task.id] = _parse_trains(
                        data,
                        task.yolcu_sayisi,
                        task.vagon_tipi,
                        task.baslangic_saati,
                        task.bitis_saati,
                    )

    return results