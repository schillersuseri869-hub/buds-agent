from fastapi import FastAPI
from app.api.webhooks import router as webhooks_router
from app.api.ws_print import router as ws_router

app = FastAPI(title="BUDS Agent", version="1.0.0")

app.include_router(webhooks_router, prefix="/webhooks")
app.include_router(ws_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
