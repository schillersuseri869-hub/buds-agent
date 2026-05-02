import asyncio
import base64
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from redis.asyncio import Redis

from app.api.webhooks import router as webhooks_router
from app.api.ws_print import router as ws_router, set_callbacks, send_print_job
from app.bot.owner_bot import (
    create_owner_bot,
    register_order_callbacks as register_owner_callbacks,
    register_stock_commands,
    register_pricing_callbacks,
    register_eucalyptus_callbacks as register_owner_eucalyptus_callbacks,
)
from app.bot.florist_bot import (
    create_florist_bot,
    register_order_callbacks as register_florist_callbacks,
    register_eucalyptus_callbacks as register_florist_eucalyptus_callbacks,
)
from app.core.event_bus import EventBus
from app.database import AsyncSessionLocal
from app.config import settings
from app.agents.print_agent.agent import PrintAgent
from app.agents.order_agent.agent import OrderAgent
from app.agents.flower_stock.agent import FlowerStockAgent
from app.agents.pricing_agent.agent import PricingAgent
from apscheduler.schedulers.asyncio import AsyncIOScheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    owner_bot, owner_dp = create_owner_bot()
    owner_task = asyncio.create_task(owner_dp.start_polling(owner_bot))

    florist_result = create_florist_bot()
    florist_task = None
    florist_bot = None
    if florist_result:
        florist_bot, florist_dp = florist_result
        florist_task = asyncio.create_task(florist_dp.start_polling(florist_bot))

    redis = Redis.from_url(settings.redis_url)
    event_bus = EventBus(redis)
    app.state.event_bus = event_bus

    print_agent = PrintAgent(redis, AsyncSessionLocal, owner_bot, settings)
    await event_bus.subscribe("order.created", print_agent.handle_order_created)
    set_callbacks(
        on_connect=print_agent.flush_pending_jobs,
        on_ack=print_agent.handle_ack,
    )

    order_agent = OrderAgent(redis, AsyncSessionLocal, owner_bot, florist_bot, event_bus, settings)
    await event_bus.subscribe("order.created", order_agent.handle_order_created)
    await event_bus.subscribe("order.ready", order_agent.handle_order_status)
    await event_bus.subscribe("order.cancelled", order_agent.handle_order_status)
    await event_bus.subscribe("order.shipped", order_agent.handle_order_status)
    await event_bus.subscribe("order.delivered", order_agent.handle_order_status)
    await order_agent.recover_timers()

    flower_stock_agent = FlowerStockAgent(AsyncSessionLocal, owner_bot, settings, florist_bot=florist_bot)
    await event_bus.subscribe("order.created", flower_stock_agent.handle_order_created)
    await event_bus.subscribe("order.ready", flower_stock_agent.handle_order_ready)
    await event_bus.subscribe("order.cancelled", flower_stock_agent.handle_order_released)
    await event_bus.subscribe("order.timeout", flower_stock_agent.handle_order_released)

    register_owner_callbacks(order_agent)
    if florist_bot:
        register_florist_callbacks(order_agent)
    register_stock_commands(flower_stock_agent)
    register_owner_eucalyptus_callbacks(flower_stock_agent)
    if florist_bot:
        register_florist_eucalyptus_callbacks(flower_stock_agent)

    scheduler = AsyncIOScheduler()
    pricing_agent = PricingAgent(AsyncSessionLocal, owner_bot, settings, scheduler)
    pricing_agent.schedule()
    scheduler.start()
    register_pricing_callbacks(pricing_agent)

    yield

    scheduler.shutdown(wait=False)
    owner_task.cancel()
    await owner_bot.session.close()
    if florist_task:
        florist_task.cancel()
    if florist_bot:
        await florist_bot.session.close()
    await event_bus.close()
    await redis.aclose()


app = FastAPI(title="BUDS Agent", version="1.0.0", lifespan=lifespan)

app.include_router(webhooks_router, prefix="/webhooks")
app.include_router(ws_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/admin/test-print")
async def test_print():
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=164, height=113)  # 58mm x 40mm
    page.insert_text((10, 40), "BUDS TEST PRINT", fontsize=14)
    page.insert_text((10, 70), "Принтер OK", fontsize=12)
    pdf_bytes = doc.tobytes()
    doc.close()
    pdf_b64 = base64.b64encode(pdf_bytes).decode()
    sent = await send_print_job("test-001", pdf_b64)
    if not sent:
        raise HTTPException(status_code=503, detail="Print client not connected")
    return {"status": "sent"}


@app.get("/admin/label-info/{market_order_id}")
async def label_info(market_order_id: str):
    import fitz
    from app.agents.print_agent.agent import download_label
    try:
        pdf_bytes = await download_label(
            market_order_id,
            settings.market_campaign_id,
            settings.market_api_token,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Label download failed: {exc}")
    doc = fitz.open("pdf", pdf_bytes)
    pages = []
    for i, page in enumerate(doc):
        r = page.rect
        pages.append({
            "page": i,
            "width_pt": round(r.width, 2),
            "height_pt": round(r.height, 2),
            "width_mm": round(r.width * 25.4 / 72, 2),
            "height_mm": round(r.height * 25.4 / 72, 2),
        })
    doc.close()
    return {"size_bytes": len(pdf_bytes), "pages": pages}


@app.post("/admin/print-order/{market_order_id}")
async def print_order(market_order_id: str, format: str | None = None):
    from app.agents.print_agent.agent import download_label
    try:
        pdf_bytes = await download_label(
            market_order_id,
            settings.market_campaign_id,
            settings.market_api_token,
            format=format,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Label download failed: {exc}")
    pdf_b64 = base64.b64encode(pdf_bytes).decode()
    sent = await send_print_job(f"admin-{market_order_id}-{format or 'default'}", pdf_b64)
    if not sent:
        raise HTTPException(status_code=503, detail="Print client not connected")
    return {"status": "sent", "market_order_id": market_order_id, "format": format}
