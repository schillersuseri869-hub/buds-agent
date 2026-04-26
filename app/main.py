import asyncio
import base64
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from redis.asyncio import Redis

from app.api.webhooks import router as webhooks_router
from app.api.ws_print import router as ws_router, set_callbacks, send_print_job
from app.bot.owner_bot import create_owner_bot
from app.bot.florist_bot import create_florist_bot
from app.core.event_bus import EventBus
from app.database import AsyncSessionLocal
from app.config import settings
from app.agents.print_agent.agent import PrintAgent


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

    yield

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
    page = doc.new_page(width=165, height=250)
    page.insert_text((10, 60), "BUDS TEST PRINT", fontsize=18)
    page.insert_text((10, 100), "Принтер работает!", fontsize=14)
    pdf_bytes = doc.tobytes()
    doc.close()
    pdf_b64 = base64.b64encode(pdf_bytes).decode()
    sent = await send_print_job("test-001", pdf_b64)
    if not sent:
        raise HTTPException(status_code=503, detail="Print client not connected")
    return {"status": "sent"}
