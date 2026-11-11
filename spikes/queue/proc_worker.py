import asyncio

import tasks


async def main() -> None:
    async with tasks.app.open_async():
        await tasks.app.run_worker_async(concurrency=1)


if __name__ == "__main__":
    asyncio.run(main())
