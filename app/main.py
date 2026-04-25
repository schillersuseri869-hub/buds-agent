import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.webhooks import router as webhooks_router
from app.api.ws_print import router as ws_router
from app.bot.owner_bot import create_owner_bot
from app.bot.florist_bot import create_florist_bot


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

    yield

    owner_task.cancel()
    await owner_bot.session.close()
    if florist_task:
        florist_task.cancel()
    if florist_bot:
        await florist_bot.session.close()


app = FastAPI(title="BUDS Agent", version="1.0.0", lifespan=lifespan)

app.include_router(webhooks_router, prefix="/webhooks")
app.include_router(ws_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
