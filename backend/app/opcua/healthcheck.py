import argparse
import asyncio

from asyncua.client.client import Client


async def check(endpoint: str, timeout: float) -> None:
    async with Client(url=endpoint, timeout=timeout) as client:
        await client.nodes.server_state.read_value()


def main() -> None:
    parser = argparse.ArgumentParser(description="Check an OPC UA endpoint")
    parser.add_argument("endpoint")
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args()
    asyncio.run(check(args.endpoint, args.timeout))


if __name__ == "__main__":
    main()
