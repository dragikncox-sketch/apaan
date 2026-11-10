import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from config import (
    API_KEY,
    AUTH_PREFIX,
    ACCEPT,
    NUMBERS_URL,
    STATISTICS_URL,
    MESSAGES_URL,
    PER_PAGE,
    PAGE_PARAM,
    PER_PAGE_PARAM,
    START_DATE,
    END_DATE,
    GROUP_BY,
)

logger = logging.getLogger(__name__)

CACHE_FILE = "numbers_cache.json"
CACHE_DURATION = 600
REFRESH_INTERVAL = 1800
MAX_PAGES = 1000

MIN_REQUEST_INTERVAL = 12

_client = None
_numbers_cache = []
_cache_timestamp = 0
_refresh_task = None
_numbers_refresh_task = None
_numbers_refresh_lock = asyncio.Lock()

_request_lock = asyncio.Lock()
_last_request_at = 0.0
_rate_limit_until = 0.0


class AugestelRateLimitError(Exception):
    pass


def get_headers():
    if not API_KEY:
        raise RuntimeError("API_KEY tidak ditemukan di .env")

    return {
        "Authorization": f"{AUTH_PREFIX} {API_KEY}".strip(),
        "Accept": ACCEPT or "application/json",
    }


async def get_client():
    global _client

    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10,
                read=30,
                write=10,
                pool=10,
            ),
            limits=httpx.Limits(
                max_connections=2,
                max_keepalive_connections=2,
            ),
        )

    return _client


async def close_client():
    global _client

    if _client is not None:
        await _client.aclose()
        _client = None


def parse_retry_after(response):
    value = response.headers.get("Retry-After", "")

    if value:
        try:
            return max(1.0, float(value))
        except ValueError:
            try:
                retry_date = parsedate_to_datetime(value)
                now = datetime.now(timezone.utc)
                if retry_date.tzinfo is None:
                    retry_date = retry_date.replace(tzinfo=timezone.utc)
                return max(1.0, (retry_date - now).total_seconds())
            except Exception:
                pass

    try:
        data = response.json()
        value = data.get("retry_after")
        if value is None and isinstance(data.get("error"), dict):
            value = data["error"].get("retry_after")
        if value is not None:
            return max(1.0, float(value))
    except Exception:
        pass

    return 60.0


async def api_get(url, params=None):
    global _last_request_at, _rate_limit_until

    if not url:
        raise RuntimeError("Endpoint API belum dikonfigurasi.")

    client = await get_client()

    async with _request_lock:
        now = time.monotonic()

        wait_time = max(
            _rate_limit_until - now,
            MIN_REQUEST_INTERVAL - (now - _last_request_at),
            0,
        )

        if wait_time > 0:
            logger.info(
                "Antrean API Augestel: menunggu %.1f detik.",
                wait_time,
            )
            await asyncio.sleep(wait_time)

        _last_request_at = time.monotonic()

        try:
            response = await client.get(
                url,
                headers=get_headers(),
                params=params,
            )
        except httpx.TimeoutException as error:
            raise RuntimeError("Koneksi Augestel timeout.") from error
        except httpx.RequestError as error:
            raise RuntimeError(
                f"Koneksi Augestel gagal: {error}"
            ) from error

        if response.status_code == 429:
            delay = parse_retry_after(response)
            _rate_limit_until = time.monotonic() + delay

            logger.warning(
                "Augestel HTTP 429. Semua request ditahan %.1f detik.",
                delay,
            )

            raise AugestelRateLimitError(
                f"Augestel membatasi permintaan. Tunggu {delay:.0f} detik."
            )

        if response.status_code == 401:
            raise RuntimeError("HTTP 401: API Key tidak valid.")

        if response.status_code == 403:
            raise RuntimeError("HTTP 403: Akses API ditolak.")

        if response.status_code >= 400:
            raise RuntimeError(
                f"HTTP {response.status_code}: {response.text[:500]}"
            )

        try:
            result = response.json()
        except ValueError as error:
            raise RuntimeError("Respons API bukan JSON.") from error

        return result


def load_cache():
    global _numbers_cache, _cache_timestamp

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        numbers = data.get("numbers", [])
        if not isinstance(numbers, list):
            return False

        _numbers_cache = numbers
        _cache_timestamp = float(data.get("timestamp", 0))

        logger.info(
            "Cache berhasil dimuat: %s nomor",
            len(_numbers_cache),
        )
        return bool(_numbers_cache)

    except (OSError, ValueError, TypeError):
        logger.warning("Cache belum tersedia atau rusak.")
        return False


def save_cache(numbers):
    global _numbers_cache, _cache_timestamp

    timestamp = time.time()
    temp_file = CACHE_FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(
            {
                "timestamp": timestamp,
                "numbers": numbers,
            },
            file,
            ensure_ascii=False,
        )

    os.replace(temp_file, CACHE_FILE)

    _numbers_cache = numbers
    _cache_timestamp = timestamp

    logger.info("Cache disimpan: %s nomor", len(numbers))


async def download_numbers():
    numbers = []
    page = 1
    last_page = 1

    while page <= last_page:
        logger.info("Mengambil halaman nomor %s", page)

        result = await api_get(
            NUMBERS_URL,
            {
                PAGE_PARAM: page,
                PER_PAGE_PARAM: PER_PAGE,
            },
        )

        if not isinstance(result, dict):
            raise RuntimeError("Format respons API tidak valid.")

        if result.get("success") is not True:
            raise RuntimeError(f"API gagal: {str(result)[:500]}")

        data = result.get("data", [])
        if not isinstance(data, list):
            raise RuntimeError("Format data nomor tidak valid.")

        numbers.extend(
            item for item in data
            if isinstance(item, dict)
        )

        pagination = result.get("pagination") or {}

        try:
            last_page = int(pagination.get("last_page", page))
        except (TypeError, ValueError):
            last_page = page

        if last_page < 1 or last_page > MAX_PAGES:
            raise RuntimeError("Jumlah halaman API tidak valid.")

        page += 1

    return numbers


async def refresh_numbers():
    global _numbers_cache

    # Hindari dua proses refresh daftar nomor berjalan bersamaan.
    async with _numbers_refresh_lock:
        try:
            numbers = await download_numbers()

            if not numbers:
                raise RuntimeError("Daftar nomor dari API kosong.")

            save_cache(numbers)
            logger.info("Refresh berhasil: %s nomor", len(numbers))
            return True

        except Exception:
            logger.exception("Gagal memperbarui nomor Augestel.")
            return False


async def _refresh_numbers_in_background():
    global _numbers_refresh_task

    try:
        await refresh_numbers()
    finally:
        _numbers_refresh_task = None


def _schedule_numbers_refresh():
    global _numbers_refresh_task

    if _numbers_refresh_task is None or _numbers_refresh_task.done():
        _numbers_refresh_task = asyncio.create_task(
            _refresh_numbers_in_background(),
            name="augestel-numbers-refresh",
        )


async def get_all_numbers():
    global _numbers_cache, _cache_timestamp

    if not _numbers_cache:
        load_cache()

    if _numbers_cache:
        age = time.time() - _cache_timestamp

        if age < CACHE_DURATION:
            return list(_numbers_cache)

        # STALE-WHILE-REVALIDATE:
        # Kirim cache lama seketika, perbarui di belakang layar.
        # Pengguna tidak perlu menunggu antrean API 12 detik x jumlah halaman.
        logger.info(
            "Cache nomor kedaluwarsa (usia %.0f detik); "
            "menggunakan cache sementara refresh berjalan.",
            age,
        )
        _schedule_numbers_refresh()
        return list(_numbers_cache)

    # Belum ada cache sama sekali: pengambilan pertama tetap diperlukan.
    success = await refresh_numbers()

    if success:
        return list(_numbers_cache)

    raise RuntimeError(
        "Tidak dapat mengambil nomor dari Augestel. "
        "Periksa API Key, endpoint, dan koneksi."
    )


async def get_statistics():
    result = await api_get(
        STATISTICS_URL,
        {
            "start_date": START_DATE,
            "end_date": END_DATE,
            "group_by": GROUP_BY,
        },
    )

    if not isinstance(result, dict) or result.get("success") is not True:
        raise RuntimeError("Gagal mengambil statistik Augestel.")

    return result


async def get_messages():
    result = await api_get(
        MESSAGES_URL,
        {
            "per_page": 50,
            "page": 1,
        },
    )

    if not isinstance(result, dict) or result.get("success") is not True:
        raise RuntimeError("Gagal mengambil messages Augestel.")

    return result


async def background_refresh():
    while True:
        await asyncio.sleep(REFRESH_INTERVAL)
        await refresh_numbers()


async def start_cache(application):
    global _refresh_task

    load_cache()

    if _refresh_task is None or _refresh_task.done():
        _refresh_task = asyncio.create_task(
            background_refresh(),
            name="augestel-cache-refresh",
        )

    if not _numbers_cache:
        await refresh_numbers()


async def stop_cache(application):
    global _refresh_task, _numbers_refresh_task

    tasks = [_refresh_task, _numbers_refresh_task]

    for task in tasks:
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Error saat menghentikan task cache.")

    _refresh_task = None
    _numbers_refresh_task = None

    await close_client()
