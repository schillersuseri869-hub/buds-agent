# Print Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the full Print Agent: on new Yandex order → download PDF label → store in Redis → push via WebSocket to florist's PC → print on Xprinter XP-365B 58×40mm thermal printer. Handles offline PC by queuing jobs and flushing when print_client connects.

**Architecture:** VPS side — `PrintAgent` class subscribes to `order.created` on the Redis event bus, downloads the Yandex label PDF, stores it in Redis (TTL 24h), creates a `PrintJob` DB record, and pushes to the WebSocket. `ws_print.py` enforces single-client and flushes pending jobs on reconnect. Florist's PC runs `print_client.py` (autostarted via Windows Task Scheduler at any-user logon), which renders the PDF via PyMuPDF and prints via python-escpos.

**Tech Stack:** Python 3.11+, FastAPI WebSocket, httpx, Redis, SQLAlchemy async, aiogram (Telegram alerts), PyMuPDF (fitz), python-escpos, Windows Task Scheduler (PowerShell)

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `app/agents/__init__.py` | Create | Package marker |
| `app/agents/print_agent/__init__.py` | Create | Package marker |
| `app/agents/print_agent/agent.py` | Create | Label download, DB ops, PrintAgent class |
| `app/api/ws_print.py` | Modify | Single-client guard, callback hooks, flush on connect |
| `app/api/webhooks.py` | Modify | Create Order record + publish `order.created` |
| `app/main.py` | Modify | Init Redis, EventBus, PrintAgent; wire callbacks |
| `print_client/print_client.py` | Modify | Lock, PyMuPDF rendering, ESC/POS print |
| `print_client/requirements.txt` | Modify | Add pymupdf, Pillow |
| `print_client/install_task.ps1` | Create | Windows Task Scheduler setup |
| `print_client/.env.example` | Create | Config template for florist's PC |
| `tests/agents/__init__.py` | Create | Package marker |
| `tests/agents/print_agent/__init__.py` | Create | Package marker |
| `tests/agents/print_agent/test_agent.py` | Create | Unit + integration tests for agent.py |
| `tests/agents/print_agent/test_ws_print.py` | Create | WebSocket guard and callback tests |

---

## Task 1: Print agent module skeleton + label download

**Files:**
- Create: `app/agents/__init__.py`
- Create: `app/agents/print_agent/__init__.py`
- Create: `app/agents/print_agent/agent.py`
- Create: `tests/agents/__init__.py`
- Create: `tests/agents/print_agent/__init__.py`
- Create: `tests/agents/print_agent/test_agent.py`

- [ ] **Step 1.1: Write the failing test**

```python
# tests/agents/print_agent/test_agent.py
import pytest
from unittest.mock import patch, AsyncMock
import httpx
from app.agents.print_agent.agent import download_label


@pytest.mark.asyncio
async def test_download_label_returns_bytes():
    fake_pdf = b"%PDF-1.4 fake"
    with patch(
        "app.agents.print_agent.agent._fetch_label_bytes",
        new_callable=AsyncMock,
        return_value=fake_pdf,
    ):
        result = await download_label("YM-123", 148807227, "test_token")
    assert result == fake_pdf


@pytest.mark.asyncio
async def test_download_label_raises_on_http_error():
    with patch(
        "app.agents.print_agent.agent._fetch_label_bytes",
        new_callable=AsyncMock,
        side_effect=httpx.HTTPStatusError(
            "404 Not Found",
            request=httpx.Request("GET", "http://test"),
            response=httpx.Response(404),
        ),
    ):
        with pytest.raises(httpx.HTTPStatusError):
            await download_label("UNKNOWN", 148807227, "test_token")
```

- [ ] **Step 1.2: Run test to verify it fails**

```
pytest tests/agents/print_agent/test_agent.py -v
```
Expected: `ImportError` or `ModuleNotFoundError` — module doesn't exist yet.

- [ ] **Step 1.3: Create package markers**

```python
# app/agents/__init__.py  (empty)
# app/agents/print_agent/__init__.py  (empty)
# tests/agents/__init__.py  (empty)
# tests/agents/print_agent/__init__.py  (empty)
```

- [ ] **Step 1.4: Implement download_label**

```python
# app/agents/print_agent/agent.py
import httpx


async def _fetch_label_bytes(url: str, token: str) -> bytes:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=True,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.content


async def download_label(
    market_order_id: str, campaign_id: int, api_token: str
) -> bytes:
    url = (
        f"https://api.partner.market.yandex.ru"
        f"/campaigns/{campaign_id}/orders/{market_order_id}/delivery/labels"
    )
    return await _fetch_label_bytes(url, api_token)
```

- [ ] **Step 1.5: Run tests to verify they pass**

```
pytest tests/agents/print_agent/test_agent.py -v
```
Expected: 2 PASSED.

- [ ] **Step 1.6: Commit**

```bash
git add app/agents/ tests/agents/
git commit -m "feat(print-agent): add module skeleton and label download"
```

---

## Task 2: PrintJob DB operations

**Files:**
- Modify: `app/agents/print_agent/agent.py`
- Modify: `tests/agents/print_agent/test_agent.py`

- [ ] **Step 2.1: Write the failing tests**

Append to `tests/agents/print_agent/test_agent.py`:

```python
import uuid
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.agents.print_agent.agent import create_print_job, get_pending_jobs, update_job_status
from app.models.orders import Order


@pytest.mark.asyncio
async def test_create_print_job(db_session):
    order = Order(market_order_id="YM-TEST-001", status="waiting")
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    job = await create_print_job(db_session, order.id, "redis:print:pdf:abc123")

    assert job.id is not None
    assert job.status == "pending"
    assert job.label_url == "redis:print:pdf:abc123"
    assert job.order_id == order.id
    assert job.completed_at is None


@pytest.mark.asyncio
async def test_get_pending_jobs_returns_pending_and_sent(db_session):
    order = Order(market_order_id="YM-TEST-002", status="waiting")
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    job_pending = await create_print_job(db_session, order.id, "redis:print:pdf:p1")
    job_sent = await create_print_job(db_session, order.id, "redis:print:pdf:p2")
    await update_job_status(db_session, job_sent.id, "sent")
    job_done = await create_print_job(db_session, order.id, "redis:print:pdf:p3")
    await update_job_status(db_session, job_done.id, "done")

    jobs = await get_pending_jobs(db_session)
    ids = {j.id for j in jobs}
    assert job_pending.id in ids
    assert job_sent.id in ids
    assert job_done.id not in ids


@pytest.mark.asyncio
async def test_update_job_status_done_sets_completed_at(db_session):
    order = Order(market_order_id="YM-TEST-003", status="waiting")
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    job = await create_print_job(db_session, order.id, "redis:print:pdf:x1")
    updated = await update_job_status(db_session, job.id, "done")

    assert updated.status == "done"
    assert updated.completed_at is not None


@pytest.mark.asyncio
async def test_update_job_status_unknown_id_returns_none(db_session):
    result = await update_job_status(db_session, uuid.uuid4(), "done")
    assert result is None
```

- [ ] **Step 2.2: Run tests to verify they fail**

```
pytest tests/agents/print_agent/test_agent.py -v
```
Expected: 4 new tests FAIL with `ImportError`.

- [ ] **Step 2.3: Implement DB functions**

Append to `app/agents/print_agent/agent.py`:

```python
import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.print_jobs import PrintJob


async def create_print_job(
    db: AsyncSession, order_id: uuid.UUID, redis_key: str
) -> PrintJob:
    job = PrintJob(order_id=order_id, status="pending", label_url=redis_key)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def get_pending_jobs(db: AsyncSession) -> list[PrintJob]:
    result = await db.execute(
        select(PrintJob)
        .where(PrintJob.status.in_(["pending", "sent"]))
        .order_by(PrintJob.created_at)
    )
    return list(result.scalars().all())


async def update_job_status(
    db: AsyncSession, job_id: uuid.UUID, status: str
) -> PrintJob | None:
    result = await db.execute(select(PrintJob).where(PrintJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        return None
    job.status = status
    if status in ("done", "failed"):
        job.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(job)
    return job
```

- [ ] **Step 2.4: Run tests**

```
pytest tests/agents/print_agent/test_agent.py -v
```
Expected: all 6 tests PASSED.

- [ ] **Step 2.5: Commit**

```bash
git add app/agents/print_agent/agent.py tests/agents/print_agent/test_agent.py
git commit -m "feat(print-agent): add PrintJob DB operations"
```

---

## Task 3: WebSocket single-client guard + callback hooks

**Files:**
- Modify: `app/api/ws_print.py`
- Create: `tests/agents/print_agent/test_ws_print.py`

- [ ] **Step 3.1: Write the failing tests**

```python
# tests/agents/print_agent/test_ws_print.py
import json
import pytest
from unittest.mock import AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
import app.api.ws_print as ws_mod


@pytest.fixture(autouse=True)
def reset_ws_state():
    ws_mod._active_client = None
    ws_mod._on_connect = None
    ws_mod._on_ack = None
    yield
    ws_mod._active_client = None
    ws_mod._on_connect = None
    ws_mod._on_ack = None


def make_app():
    from fastapi import FastAPI
    a = FastAPI()
    a.include_router(ws_mod.router)
    return a


@pytest.mark.asyncio
async def test_second_client_rejected():
    app = make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async with client.stream("GET", "/ws/print", headers={"Upgrade": "websocket", "Connection": "Upgrade", "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==", "Sec-WebSocket-Version": "13"}):
            pass

    # After first client connects, manually set _active_client to a mock
    ws_mod._active_client = AsyncMock()

    received = []
    app2 = make_app()

    from starlette.testclient import TestClient as StarletteTestClient
    with StarletteTestClient(app2) as client2:
        with client2.websocket_connect("/ws/print") as ws:
            msg = ws.receive_text()
            received.append(json.loads(msg))

    assert received[0]["error"] == "already_connected"


@pytest.mark.asyncio
async def test_on_connect_callback_called():
    app = make_app()
    callback = AsyncMock()
    ws_mod.set_callbacks(on_connect=callback, on_ack=AsyncMock())

    from starlette.testclient import TestClient
    with TestClient(app) as client:
        with client.websocket_connect("/ws/print"):
            pass

    callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_ack_callback_called():
    app = make_app()
    ack_callback = AsyncMock()
    ws_mod.set_callbacks(on_connect=AsyncMock(), on_ack=ack_callback)

    from starlette.testclient import TestClient
    with TestClient(app) as client:
        with client.websocket_connect("/ws/print") as ws:
            ws.send_text(json.dumps({"job_id": "abc", "status": "done"}))

    ack_callback.assert_awaited_once_with({"job_id": "abc", "status": "done"})


@pytest.mark.asyncio
async def test_send_print_job_returns_false_when_no_client():
    result = await ws_mod.send_print_job("job-1", "base64data")
    assert result is False


@pytest.mark.asyncio
async def test_send_print_job_sends_json_when_client_connected():
    mock_ws = AsyncMock()
    ws_mod._active_client = mock_ws

    result = await ws_mod.send_print_job("job-1", "pdfbase64")

    assert result is True
    mock_ws.send_text.assert_awaited_once()
    sent = json.loads(mock_ws.send_text.call_args[0][0])
    assert sent["job_id"] == "job-1"
    assert sent["pdf_data"] == "pdfbase64"
```

- [ ] **Step 3.2: Run tests to verify they fail**

```
pytest tests/agents/print_agent/test_ws_print.py -v
```
Expected: FAIL — `set_callbacks`, `_on_connect`, `_on_ack` not in ws_print.

- [ ] **Step 3.3: Rewrite ws_print.py**

```python
# app/api/ws_print.py
import json
from typing import Callable, Awaitable
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

_active_client: WebSocket | None = None
_on_connect: Callable[[], Awaitable[None]] | None = None
_on_ack: Callable[[dict], Awaitable[None]] | None = None


def set_callbacks(
    on_connect: Callable[[], Awaitable[None]],
    on_ack: Callable[[dict], Awaitable[None]],
) -> None:
    global _on_connect, _on_ack
    _on_connect, _on_ack = on_connect, on_ack


async def send_print_job(job_id: str, pdf_b64: str) -> bool:
    if _active_client is None:
        return False
    await _active_client.send_text(
        json.dumps({"job_id": job_id, "pdf_data": pdf_b64})
    )
    return True


@router.websocket("/ws/print")
async def websocket_print(websocket: WebSocket):
    global _active_client
    if _active_client is not None:
        await websocket.accept()
        await websocket.send_text(json.dumps({"error": "already_connected"}))
        await websocket.close()
        return
    await websocket.accept()
    _active_client = websocket
    if _on_connect is not None:
        await _on_connect()
    try:
        while True:
            raw = await websocket.receive_text()
            ack = json.loads(raw)
            if _on_ack is not None:
                await _on_ack(ack)
    except WebSocketDisconnect:
        _active_client = None
```

- [ ] **Step 3.4: Run tests**

```
pytest tests/agents/print_agent/test_ws_print.py -v
```
Expected: 5 PASSED.

- [ ] **Step 3.5: Confirm existing webhook tests still pass**

```
pytest tests/ -v
```
Expected: all PASSED.

- [ ] **Step 3.6: Commit**

```bash
git add app/api/ws_print.py tests/agents/print_agent/test_ws_print.py
git commit -m "feat(print-agent): refactor ws_print — single-client guard, callback hooks"
```

---

## Task 4: PrintAgent class — handle_order_created

**Files:**
- Modify: `app/agents/print_agent/agent.py`
- Modify: `tests/agents/print_agent/test_agent.py`

- [ ] **Step 4.1: Write the failing tests**

Append to `tests/agents/print_agent/test_agent.py`:

```python
import base64
import fakeredis.aioredis
from unittest.mock import AsyncMock, patch, MagicMock
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.agents.print_agent.agent import PrintAgent


@pytest.fixture
def fake_redis():
    return fakeredis.aioredis.FakeRedis()


@pytest.fixture
def mock_settings():
    s = MagicMock()
    s.market_campaign_id = 148807227
    s.market_api_token = "test_token"
    s.owner_telegram_id = 123456789
    return s


@pytest.fixture
def mock_bot():
    return AsyncMock()


@pytest.mark.asyncio
async def test_handle_order_created_creates_job_and_sends(
    fake_redis, db_session, mock_bot, mock_settings, test_engine
):
    db_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    agent = PrintAgent(fake_redis, db_factory, mock_bot, mock_settings)

    order = Order(market_order_id="YM-SEND-001", status="waiting")
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    fake_pdf = b"%PDF-1.4 fakedata"
    with patch(
        "app.agents.print_agent.agent._fetch_label_bytes",
        new_callable=AsyncMock,
        return_value=fake_pdf,
    ), patch(
        "app.api.ws_print.send_print_job",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_send:
        await agent.handle_order_created(
            "order.created",
            {"order_id": str(order.id), "market_order_id": "YM-SEND-001"},
        )

    mock_send.assert_awaited_once()
    call_args = mock_send.call_args
    assert base64.b64decode(call_args[0][1]) == fake_pdf

    async with db_factory() as db:
        jobs = await get_pending_jobs(db)
    assert any(j.status == "sent" for j in jobs)


@pytest.mark.asyncio
async def test_handle_order_created_alerts_when_printer_offline(
    fake_redis, db_session, mock_bot, mock_settings, test_engine
):
    db_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    agent = PrintAgent(fake_redis, db_factory, mock_bot, mock_settings)

    order = Order(market_order_id="YM-OFFLINE-001", status="waiting")
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    with patch(
        "app.agents.print_agent.agent._fetch_label_bytes",
        new_callable=AsyncMock,
        return_value=b"%PDF-1.4 x",
    ), patch(
        "app.api.ws_print.send_print_job",
        new_callable=AsyncMock,
        return_value=False,
    ):
        await agent.handle_order_created(
            "order.created",
            {"order_id": str(order.id), "market_order_id": "YM-OFFLINE-001"},
        )

    mock_bot.send_message.assert_awaited_once()
    text = mock_bot.send_message.call_args[0][1]
    assert "офлайн" in text.lower()


@pytest.mark.asyncio
async def test_handle_order_created_alerts_on_api_error(
    fake_redis, db_session, mock_bot, mock_settings, test_engine
):
    db_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    agent = PrintAgent(fake_redis, db_factory, mock_bot, mock_settings)

    order = Order(market_order_id="YM-ERR-001", status="waiting")
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    with patch(
        "app.agents.print_agent.agent._fetch_label_bytes",
        new_callable=AsyncMock,
        side_effect=httpx.HTTPStatusError(
            "404",
            request=httpx.Request("GET", "http://x"),
            response=httpx.Response(404),
        ),
    ):
        await agent.handle_order_created(
            "order.created",
            {"order_id": str(order.id), "market_order_id": "YM-ERR-001"},
        )

    mock_bot.send_message.assert_awaited_once()
    text = mock_bot.send_message.call_args[0][1]
    assert "ярлык" in text.lower()
```

- [ ] **Step 4.2: Run tests to verify they fail**

```
pytest tests/agents/print_agent/test_agent.py::test_handle_order_created_creates_job_and_sends -v
```
Expected: FAIL — `PrintAgent` not defined.

- [ ] **Step 4.3: Implement PrintAgent class**

Append to `app/agents/print_agent/agent.py`:

```python
import base64
import logging
import uuid as uuid_module
from sqlalchemy.ext.asyncio import async_sessionmaker
from redis.asyncio import Redis
from aiogram import Bot
from app.api.ws_print import send_print_job

logger = logging.getLogger(__name__)


class PrintAgent:
    def __init__(
        self,
        redis: Redis,
        db_factory: async_sessionmaker,
        owner_bot: Bot,
        settings,
    ):
        self._redis = redis
        self._db_factory = db_factory
        self._owner_bot = owner_bot
        self._settings = settings

    async def handle_order_created(self, channel: str, data: dict) -> None:
        market_order_id = data.get("market_order_id")
        order_id_str = data.get("order_id")
        if not market_order_id or not order_id_str:
            logger.error("order.created missing fields: %s", data)
            return

        try:
            pdf_bytes = await download_label(
                market_order_id,
                self._settings.market_campaign_id,
                self._settings.market_api_token,
            )
        except Exception as exc:
            logger.error("Label download failed for %s: %s", market_order_id, exc)
            await self._alert(f"Не удалось скачать ярлык заказа #{market_order_id}")
            return

        job_id = str(uuid_module.uuid4())
        redis_key = f"print:pdf:{job_id}"
        await self._redis.setex(redis_key, 86400, pdf_bytes)

        async with self._db_factory() as db:
            job = await create_print_job(
                db, uuid_module.UUID(order_id_str), redis_key
            )

        pdf_b64 = base64.b64encode(pdf_bytes).decode()
        sent = await send_print_job(str(job.id), pdf_b64)

        if sent:
            async with self._db_factory() as db:
                await update_job_status(db, job.id, "sent")
        else:
            await self._alert(
                f"Принтер офлайн. Ярлык заказа #{market_order_id} "
                f"будет напечатан при подключении."
            )

    async def _alert(self, message: str) -> None:
        try:
            await self._owner_bot.send_message(
                self._settings.owner_telegram_id, message
            )
        except Exception as exc:
            logger.error("Failed to send alert: %s", exc)
```

- [ ] **Step 4.4: Run tests**

```
pytest tests/agents/print_agent/test_agent.py -v
```
Expected: all 9 tests PASSED.

- [ ] **Step 4.5: Commit**

```bash
git add app/agents/print_agent/agent.py tests/agents/print_agent/test_agent.py
git commit -m "feat(print-agent): add PrintAgent.handle_order_created"
```

---

## Task 5: flush_pending_jobs + handle_ack

**Files:**
- Modify: `app/agents/print_agent/agent.py`
- Modify: `tests/agents/print_agent/test_agent.py`

- [ ] **Step 5.1: Write the failing tests**

Append to `tests/agents/print_agent/test_agent.py`:

```python
from app.agents.print_agent.agent import create_print_job, update_job_status


@pytest.mark.asyncio
async def test_flush_pending_sends_all_pending_jobs(
    fake_redis, db_session, mock_bot, mock_settings, test_engine
):
    db_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    agent = PrintAgent(fake_redis, db_factory, mock_bot, mock_settings)

    order = Order(market_order_id="YM-FLUSH-001", status="waiting")
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    fake_pdf = b"%PDF-1.4 flush"
    job1 = await create_print_job(db_session, order.id, "print:pdf:job-flush-1")
    job2 = await create_print_job(db_session, order.id, "print:pdf:job-flush-2")
    await fake_redis.setex("print:pdf:job-flush-1", 86400, fake_pdf)
    await fake_redis.setex("print:pdf:job-flush-2", 86400, fake_pdf)

    sent_ids = []
    async def mock_send(job_id, pdf_b64):
        sent_ids.append(job_id)
        return True

    with patch("app.api.ws_print.send_print_job", side_effect=mock_send):
        await agent.flush_pending_jobs()

    assert str(job1.id) in sent_ids
    assert str(job2.id) in sent_ids

    async with db_factory() as db:
        from sqlalchemy import select
        from app.models.print_jobs import PrintJob
        result = await db.execute(
            select(PrintJob).where(PrintJob.id.in_([job1.id, job2.id]))
        )
        jobs = result.scalars().all()
    assert all(j.status == "sent" for j in jobs)


@pytest.mark.asyncio
async def test_flush_skips_expired_pdf(
    fake_redis, db_session, mock_bot, mock_settings, test_engine
):
    db_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    agent = PrintAgent(fake_redis, db_factory, mock_bot, mock_settings)

    order = Order(market_order_id="YM-EXPIRE-001", status="waiting")
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    await create_print_job(db_session, order.id, "print:pdf:expired-key")
    # do NOT put anything in Redis — simulates TTL expiry

    with patch("app.api.ws_print.send_print_job", new_callable=AsyncMock) as mock_send:
        await agent.flush_pending_jobs()

    mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_ack_done_updates_status(
    fake_redis, db_session, mock_bot, mock_settings, test_engine
):
    db_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    agent = PrintAgent(fake_redis, db_factory, mock_bot, mock_settings)

    order = Order(market_order_id="YM-ACK-001", status="waiting")
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    job = await create_print_job(db_session, order.id, "print:pdf:ack-1")
    await update_job_status(db_session, job.id, "sent")

    await agent.handle_ack({"job_id": str(job.id), "status": "done"})

    async with db_factory() as db:
        from sqlalchemy import select
        from app.models.print_jobs import PrintJob
        result = await db.execute(select(PrintJob).where(PrintJob.id == job.id))
        updated = result.scalar_one()
    assert updated.status == "done"
    assert updated.completed_at is not None


@pytest.mark.asyncio
async def test_handle_ack_failed_alerts_owner(
    fake_redis, db_session, mock_bot, mock_settings, test_engine
):
    db_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    agent = PrintAgent(fake_redis, db_factory, mock_bot, mock_settings)

    order = Order(market_order_id="YM-ACK-FAIL-001", status="waiting")
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    job = await create_print_job(db_session, order.id, "print:pdf:ack-fail-1")
    await update_job_status(db_session, job.id, "sent")

    await agent.handle_ack(
        {"job_id": str(job.id), "status": "failed", "error": "paper jam"}
    )

    mock_bot.send_message.assert_awaited_once()
    text = mock_bot.send_message.call_args[0][1]
    assert "ошибка" in text.lower()
    assert "paper jam" in text.lower()
```

- [ ] **Step 5.2: Run tests to verify they fail**

```
pytest tests/agents/print_agent/test_agent.py::test_flush_pending_sends_all_pending_jobs -v
```
Expected: FAIL — `flush_pending_jobs` not defined.

- [ ] **Step 5.3: Implement flush_pending_jobs and handle_ack in PrintAgent**

Add these methods to the `PrintAgent` class in `app/agents/print_agent/agent.py`:

```python
    async def flush_pending_jobs(self) -> None:
        async with self._db_factory() as db:
            jobs = await get_pending_jobs(db)

        for job in jobs:
            pdf_bytes = await self._redis.get(job.label_url)
            if pdf_bytes is None:
                logger.warning("PDF expired in Redis for job %s, skipping", job.id)
                continue
            pdf_b64 = base64.b64encode(pdf_bytes).decode()
            sent = await send_print_job(str(job.id), pdf_b64)
            if sent:
                async with self._db_factory() as db:
                    await update_job_status(db, job.id, "sent")

    async def handle_ack(self, ack: dict) -> None:
        job_id_str = ack.get("job_id")
        status = ack.get("status")
        if not job_id_str or status not in ("done", "failed"):
            return
        try:
            job_id = uuid_module.UUID(job_id_str)
        except ValueError:
            logger.warning("Invalid job_id in ACK: %s", job_id_str)
            return

        async with self._db_factory() as db:
            await update_job_status(db, job_id, status)

        if status == "failed":
            error = ack.get("error", "неизвестная ошибка")
            await self._alert(f"Ошибка печати задания {job_id_str}: {error}")
```

- [ ] **Step 5.4: Run all agent tests**

```
pytest tests/agents/ -v
```
Expected: all 13 tests PASSED.

- [ ] **Step 5.5: Commit**

```bash
git add app/agents/print_agent/agent.py tests/agents/print_agent/test_agent.py
git commit -m "feat(print-agent): add flush_pending_jobs and handle_ack"
```

---

## Task 6: Wire into main.py + webhook publishes order.created

**Files:**
- Modify: `app/main.py`
- Modify: `app/api/webhooks.py`

- [ ] **Step 6.1: Rewrite app/main.py to init Redis, EventBus, PrintAgent**

```python
# app/main.py
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from app.api.webhooks import router as webhooks_router
from app.api.ws_print import router as ws_router, set_callbacks
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

    florist_task = None
    florist_bot = None
    florist_result = create_florist_bot()
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
```

- [ ] **Step 6.2: Update webhooks.py to create Order and publish event**

```python
# app/api/webhooks.py
from fastapi import APIRouter, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.orders import Order

router = APIRouter()


@router.post("/market")
async def market_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await request.json()
    event_type = payload.get("type", "unknown")
    market_order_id = str(payload.get("orderId", "")).strip()

    if not market_order_id:
        return {"status": "ignored", "reason": "no orderId"}

    result = await db.execute(
        select(Order).where(Order.market_order_id == market_order_id)
    )
    order = result.scalar_one_or_none()

    if order is None:
        order = Order(market_order_id=market_order_id, status="waiting")
        db.add(order)
        await db.commit()
        await db.refresh(order)

        bus = getattr(request.app.state, "event_bus", None)
        if bus is not None:
            await bus.publish(
                "order.created",
                {
                    "order_id": str(order.id),
                    "market_order_id": market_order_id,
                },
            )

    return {"status": "ok", "order_id": str(order.id)}
```

- [ ] **Step 6.3: Run existing tests to confirm nothing is broken**

```
pytest tests/ -v
```
Expected: all PASSED.

- [ ] **Step 6.4: Smoke test — send a test webhook**

Start the server locally (requires `.env` with valid tokens, or use docker-compose):

```bash
docker compose up -d
curl -s -X POST http://localhost:8000/webhooks/market \
  -H "Content-Type: application/json" \
  -d '{"type": "NEW_ORDER", "orderId": "TEST-9999"}' | python -m json.tool
```

Expected response:
```json
{"status": "ok", "order_id": "<some-uuid>"}
```

Check the DB has an order:
```bash
docker compose exec postgres psql -U buds -d buds -c "SELECT market_order_id, status FROM orders WHERE market_order_id = 'TEST-9999';"
```

- [ ] **Step 6.5: Commit**

```bash
git add app/main.py app/api/webhooks.py
git commit -m "feat(print-agent): wire PrintAgent into app, webhook publishes order.created"
```

---

## Task 7: Rewrite print_client — lock + PyMuPDF + ESC/POS

**Files:**
- Modify: `print_client/print_client.py`
- Create: `tests/print_client/__init__.py`
- Create: `tests/print_client/test_print_client.py`

Note: These tests run on the dev machine (without a physical printer). The ESC/POS print call is mocked.

- [ ] **Step 7.1: Write the failing tests**

```python
# tests/print_client/__init__.py  (empty)
```

```python
# tests/print_client/test_print_client.py
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "print_client"))
import print_client as pc


def make_minimal_pdf() -> bytes:
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=164, height=113)  # 58×40mm at 72 DPI
    page.insert_text((10, 30), "Test label")
    buf = doc.write()
    doc.close()
    return bytes(buf)


def test_render_pdf_to_image_returns_correct_width():
    from PIL import Image
    pdf_bytes = make_minimal_pdf()
    img = pc.render_pdf_to_image(pdf_bytes, target_width_mm=58.0, dpi=203)
    assert isinstance(img, Image.Image)
    expected_width = int(58.0 * 203 / 25.4)  # 464
    assert abs(img.width - expected_width) <= 2  # allow 2px rounding


def test_render_pdf_to_image_is_monochrome():
    pdf_bytes = make_minimal_pdf()
    img = pc.render_pdf_to_image(pdf_bytes)
    assert img.mode == "1"


def test_acquire_lock_succeeds_first_time():
    lock_path = os.path.join(tempfile.gettempdir(), "buds_test_lock.lock")
    if os.path.exists(lock_path):
        os.remove(lock_path)
    try:
        assert pc.acquire_lock(lock_path) is True
    finally:
        pc.release_lock(lock_path)


def test_acquire_lock_fails_when_self_already_holds():
    lock_path = os.path.join(tempfile.gettempdir(), "buds_test_lock2.lock")
    if os.path.exists(lock_path):
        os.remove(lock_path)
    try:
        assert pc.acquire_lock(lock_path) is True
        # Write own PID → simulates same process trying again
        assert pc.acquire_lock(lock_path) is False
    finally:
        pc.release_lock(lock_path)


def test_release_lock_removes_file():
    lock_path = os.path.join(tempfile.gettempdir(), "buds_test_lock3.lock")
    pc.acquire_lock(lock_path)
    pc.release_lock(lock_path)
    assert not os.path.exists(lock_path)
```

- [ ] **Step 7.2: Run tests to verify they fail**

```
pytest tests/print_client/ -v
```
Expected: FAIL — `render_pdf_to_image`, `acquire_lock`, `release_lock` not defined.

- [ ] **Step 7.3: Rewrite print_client.py**

```python
# print_client/print_client.py
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

import websockets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("buds_print")

BUDS_WS_URL = os.environ.get("BUDS_WS_URL", "ws://localhost:8000/ws/print")
PRINTER_USB_VENDOR = int(os.environ.get("PRINTER_USB_VENDOR", "0x1FC9"), 16)
PRINTER_USB_PRODUCT = int(os.environ.get("PRINTER_USB_PRODUCT", "0x0082"), 16)
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
    page = doc[0]
    target_px = int(target_width_mm * dpi / 25.4)
    zoom = target_px / page.rect.width
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
    img = Image.open(io.BytesIO(pix.tobytes("ppm")))
    doc.close()
    return img.convert("1")


def print_label(pdf_bytes: bytes, job_id: str) -> bool:
    try:
        from escpos.printer import Usb
        img = render_pdf_to_image(pdf_bytes)
        printer = Usb(PRINTER_USB_VENDOR, PRINTER_USB_PRODUCT)
        printer.image(img)
        printer.cut()
        return True
    except Exception as exc:
        logger.error("Print error for job %s: %s", job_id, exc)
        return False


async def run():
    if not acquire_lock():
        logger.info("Another instance is running. Exiting.")
        sys.exit(0)

    try:
        logger.info("Connecting to %s", BUDS_WS_URL)
        async for websocket in websockets.connect(BUDS_WS_URL, ping_interval=20):
            try:
                logger.info("Connected")
                async for raw in websocket:
                    msg = json.loads(raw)
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
```

- [ ] **Step 7.4: Add pymupdf and Pillow to VPS requirements.txt**

Add to `requirements.txt`:
```
pymupdf==1.24.11
Pillow==10.4.0
```

- [ ] **Step 7.5: Run print_client tests**

```
pytest tests/print_client/ -v
```
Expected: 5 PASSED (requires `pymupdf` and `Pillow` installed in dev env: `pip install pymupdf Pillow`).

- [ ] **Step 7.6: Commit**

```bash
git add print_client/print_client.py tests/print_client/ requirements.txt
git commit -m "feat(print-agent): rewrite print_client — PyMuPDF rendering, single-instance lock"
```

---

## Task 8: Deployment files for florist's PC

**Files:**
- Modify: `print_client/requirements.txt`
- Create: `print_client/install_task.ps1`
- Create: `print_client/.env.example`

- [ ] **Step 8.1: Update print_client/requirements.txt**

```
websockets==13.0
python-escpos==3.1
pymupdf==1.24.11
Pillow==10.4.0
```

- [ ] **Step 8.2: Create install_task.ps1**

```powershell
# print_client/install_task.ps1
# Run once as Administrator to register BUDS Print Client as a Windows scheduled task.
# The task starts automatically when any user logs in.

$ErrorActionPreference = "Stop"

$pythonExe = (Get-Command pythonw.exe -ErrorAction SilentlyContinue)
if (-not $pythonExe) {
    $pythonExe = (Get-Command python.exe -ErrorAction Stop)
}
$pythonPath = $pythonExe.Path

$scriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptPath = Join-Path $scriptDir "print_client.py"
$envFile    = Join-Path $scriptDir ".env"

if (-not (Test-Path $envFile)) {
    Write-Error ".env file not found at $envFile. Copy .env.example to .env and fill in values."
    exit 1
}

$envContent = Get-Content $envFile | Where-Object { $_ -match "^\s*\w+\s*=" }
$envVars = @()
foreach ($line in $envContent) {
    $parts = $line -split "=", 2
    $envVars += [System.Environment]::ExpandEnvironmentVariables($parts[0].Trim() + "=" + $parts[1].Trim())
}

$action = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument "`"$scriptPath`"" `
    -WorkingDirectory $scriptDir

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

$principal = New-ScheduledTaskPrincipal `
    -GroupId "BUILTIN\Administrators" `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName "BUDS Print Client" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "Task 'BUDS Print Client' registered successfully."
Write-Host "It will start automatically when any administrator logs in."
Write-Host "To start it now: Start-ScheduledTask -TaskName 'BUDS Print Client'"
```

- [ ] **Step 8.3: Create .env.example**

```
# print_client/.env.example
# Copy this file to .env and fill in the values.

BUDS_WS_URL=ws://82.22.3.55:8000/ws/print

# USB Vendor and Product IDs for Xprinter XP-365B
# Find them: Device Manager → Xprinter → Properties → Details → Hardware IDs
PRINTER_USB_VENDOR=0x1FC9
PRINTER_USB_PRODUCT=0x0082
```

- [ ] **Step 8.4: Run full test suite one last time**

```
pytest tests/ -v
```
Expected: all tests PASSED.

- [ ] **Step 8.5: Commit**

```bash
git add print_client/requirements.txt print_client/install_task.ps1 print_client/.env.example
git commit -m "feat(print-agent): add print_client deployment files (requirements, Task Scheduler script)"
```

---

## Self-review checklist (do not skip)

- [x] **Spec: label download** → Task 1
- [x] **Spec: create print_jobs pending** → Task 2
- [x] **Spec: send via WebSocket → sent** → Task 4
- [x] **Spec: ACK → done** → Task 5
- [x] **Spec: no client → failed + alert** → Task 4
- [x] **Spec: flush pending on client connect** → Task 5
- [x] **Spec: single-client guard** → Task 3
- [x] **Spec: PyMuPDF PDF→image, 58×40mm** → Task 7
- [x] **Spec: Windows Task Scheduler any-user autostart** → Task 8
- [x] **Spec: single-instance lock** → Task 7
- [x] **Spec: event bus wire-up** → Task 6
- [x] **Spec: order created in webhook** → Task 6
- [x] **Spec: VID/PID note for XP-365B** → Task 8 (.env.example)
- [x] **Spec: deployment instructions** → Task 8
