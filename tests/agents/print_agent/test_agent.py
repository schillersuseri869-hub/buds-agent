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
