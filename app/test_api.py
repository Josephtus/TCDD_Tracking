import asyncio
import logging
from scraper import check_train_tickets, _fetch_availability, token_manager
import aiohttp

logging.basicConfig(level=logging.INFO)

async def main():
    print("Testing check_train_tickets ERYAMAN YHT -> ANKARA GAR...")
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as sess:
        res = await _fetch_availability('ERYAMAN YHT', 'ANKARA GAR', '30.03.2026', 1, sess)
    print("Raw _fetch_availability Result:", res)

if __name__ == "__main__":
    asyncio.run(main())
