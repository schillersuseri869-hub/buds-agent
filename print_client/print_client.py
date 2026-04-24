#!/usr/bin/env python3
"""
Runs on the florist's local PC. Connects to BUDS VPS WebSocket,
receives print jobs, prints via ESC/POS thermal printer, sends ACK.

Setup:
    pip install -r print_client/requirements.txt

Run:
    BUDS_WS_URL=ws://YOUR_VPS_IP:8000/ws/print \
    PRINTER_USB_VENDOR=0x04b8 \
    PRINTER_USB_PRODUCT=0x0202 \
    python print_client/print_client.py
"""
import asyncio
import json
import os
import urllib.request
import tempfile

import websockets

BUDS_WS_URL = os.environ.get("BUDS_WS_URL", "ws://localhost:8000/ws/print")
PRINTER_USB_VENDOR = int(os.environ.get("PRINTER_USB_VENDOR", "0x04b8"), 16)
PRINTER_USB_PRODUCT = int(os.environ.get("PRINTER_USB_PRODUCT", "0x0202"), 16)


def print_label(label_url: str, job_id: str) -> bool:
    try:
        from escpos.printer import Usb
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            urllib.request.urlretrieve(label_url, f.name)
        printer = Usb(PRINTER_USB_VENDOR, PRINTER_USB_PRODUCT)
        printer.text(f"Заказ {job_id}\n")
        printer.text(f"{label_url[:60]}\n")
        printer.cut()
        return True
    except Exception as exc:
        print(f"[print_client] Print error for job {job_id}: {exc}")
        return False


async def run():
    print(f"[print_client] Connecting to {BUDS_WS_URL}")
    async for websocket in websockets.connect(BUDS_WS_URL, ping_interval=20):
        try:
            print("[print_client] Connected")
            async for raw in websocket:
                job = json.loads(raw)
                job_id = job.get("job_id", "unknown")
                label_url = job.get("label_url", "")
                print(f"[print_client] Printing job {job_id}")
                success = print_label(label_url, job_id)
                ack = {"job_id": job_id, "status": "done" if success else "failed"}
                await websocket.send(json.dumps(ack))
        except websockets.ConnectionClosed:
            print("[print_client] Disconnected, retrying in 5s...")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(run())
