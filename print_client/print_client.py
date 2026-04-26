"""
Runs on the florist's local PC. Connects to BUDS VPS WebSocket,
receives print jobs, prints via ESC/POS thermal printer, sends ACK.

Setup:
    pip install -r print_client/requirements.txt

Run:
    set BUDS_WS_URL=ws://82.22.3.55:8000/ws/print
    set PRINTER_USB_VENDOR=0x1FC9
    set PRINTER_USB_PRODUCT=0x0082
    pythonw print_client/print_client.py
"""
import asyncio
import base64
import json
import logging
import os
import sys
import tempfile

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"), override=False)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("buds_print")

BUDS_WS_URL = os.environ.get("BUDS_WS_URL", "ws://localhost:8000/ws/print")
PRINTER_NAME = os.environ.get("PRINTER_NAME", "Xprinter Xp-365B")
LOCK_FILE = os.path.join(tempfile.gettempdir(), "buds_print.lock")


def acquire_lock(lock_path: str = LOCK_FILE) -> bool:
    if os.path.exists(lock_path):
        try:
            with open(lock_path) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            return False  # process still running
        except (OSError, ValueError):
            pass  # stale lock
    with open(lock_path, "w") as f:
        f.write(str(os.getpid()))
    return True


def release_lock(lock_path: str = LOCK_FILE) -> None:
    try:
        os.remove(lock_path)
    except FileNotFoundError:
        pass


def render_pdf_to_image(
    pdf_bytes: bytes,
    target_width_mm: float = 58.0,
    dpi: int = 203,
):
    import fitz
    from PIL import Image
    import io

    doc = fitz.open("pdf", pdf_bytes)
    try:
        page = doc[0]
        target_px = int(target_width_mm * dpi / 25.4)
        zoom = target_px / page.rect.width
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
        img = Image.open(io.BytesIO(pix.tobytes("ppm")))
        return img.convert("1")
    finally:
        doc.close()


def print_label(pdf_bytes: bytes, job_id: str) -> bool:
    try:
        import win32print
        import win32ui
        from PIL import ImageWin
        img = render_pdf_to_image(pdf_bytes).rotate(90, expand=True).convert("RGB")
        hdc = win32ui.CreateDC()
        hdc.CreatePrinterDC(PRINTER_NAME)
        dpi_x = hdc.GetDeviceCaps(88)   # LOGPIXELSX
        dpi_y = hdc.GetDeviceCaps(90)   # LOGPIXELSY
        horzres = hdc.GetDeviceCaps(8)  # HORZRES
        vertres = hdc.GetDeviceCaps(10) # VERTRES
        logger.info("Printer: dpi_x=%s dpi_y=%s horzres=%s vertres=%s img=%sx%s",
                    dpi_x, dpi_y, horzres, vertres, img.width, img.height)
        draw_w = int(img.width * dpi_x / 203)
        draw_h = int(img.height * dpi_y / 203)
        hdc.StartDoc(job_id)
        hdc.StartPage()
        dib = ImageWin.Dib(img)
        dib.draw(hdc.GetHandleOutput(), (0, 0, draw_w, draw_h))
        hdc.EndPage()
        hdc.EndDoc()
        hdc.DeleteDC()
        return True
    except Exception as exc:
        logger.error("Print error for job %s: %s", job_id, exc)
        return False


async def run():
    import websockets  # lazy import — not needed for tests

    if not acquire_lock():
        logger.info("Another instance is running. Exiting.")
        sys.exit(0)

    try:
        logger.info("Connecting to %s", BUDS_WS_URL)
        async for websocket in websockets.connect(BUDS_WS_URL, ping_interval=20):
            try:
                logger.info("Connected")
                async for raw in websocket:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.warning("Non-JSON frame received, ignoring: %r", raw[:100])
                        continue
                    if "error" in msg:
                        logger.error("Server error: %s", msg["error"])
                        continue
                    job_id = msg.get("job_id", "unknown")
                    pdf_b64 = msg.get("pdf_data", "")
                    pdf_bytes = base64.b64decode(pdf_b64)
                    logger.info("Printing job %s", job_id)
                    success = print_label(pdf_bytes, job_id)
                    ack = {"job_id": job_id, "status": "done" if success else "failed"}
                    if not success:
                        ack["error"] = "ESC/POS print failed"
                    await websocket.send(json.dumps(ack))
            except websockets.ConnectionClosed:
                logger.info("Disconnected, retrying in 5s...")
                await asyncio.sleep(5)
    finally:
        release_lock()


if __name__ == "__main__":
    asyncio.run(run())
