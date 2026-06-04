#!/usr/bin/env python3
"""
Tiny CLI test client for blackjack_ws_server.py

Install:
    pip install websockets

Run:
    python blackjack_ws_test_client.py ws://127.0.0.1:8765
"""

import asyncio
import json
import sys

import websockets


async def reader(ws):
    async for raw in ws:
        try:
            msg = json.loads(raw)
            print("\nSERVER:", json.dumps(msg, indent=2))
            print("> ", end="", flush=True)
        except Exception:
            print("\nSERVER RAW:", raw)


async def main():
    uri = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8765"

    async with websockets.connect(uri) as ws:
        asyncio.create_task(reader(ws))

        print("Connected.")
        print("Examples:")
        print('  {"type":"CREATE_ROOM","name":"Ali"}')
        print('  {"type":"JOIN_ROOM","room_code":"JASLI-1234","name":"Friend"}')
        print('  {"type":"CLAIM_SEAT","seat":0}')
        print('  {"type":"START_BETTING"}')
        print('  {"type":"BET","amount":1000}')
        print('  {"type":"HIT"}')
        print('  {"type":"STAND"}')
        print()

        while True:
            line = await asyncio.to_thread(input, "> ")
            if not line:
                continue
            if line.lower() in ["quit", "exit"]:
                break
            await ws.send(line)


if __name__ == "__main__":
    asyncio.run(main())
