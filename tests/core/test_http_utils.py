"""Tests for HTTP helpers."""
from unittest import mock

import aiohttp
import pytest

from udemy_enroller.http_utils import http_get


class MockResponse:
    def __init__(self, error=None):
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error

    async def read(self):
        return b"response"

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass


class MockSession:
    def __init__(self, connection_error=None, response_error=None):
        self.connection_error = connection_error
        self.response_error = response_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    def get(self, url, headers):
        if self.connection_error:
            raise self.connection_error
        return MockResponse(self.response_error)


@pytest.mark.asyncio
async def test_http_get_uses_threaded_resolver():
    resolver = object()
    connector = object()
    session = MockSession()

    with (
        mock.patch.object(aiohttp, "ThreadedResolver", return_value=resolver) as create_resolver,
        mock.patch.object(aiohttp, "TCPConnector", return_value=connector) as create_connector,
        mock.patch.object(aiohttp, "ClientSession", return_value=session) as create_session,
    ):
        result = await http_get("https://example.com", headers={"X-Test": "yes"})

    assert result == b"response"
    create_resolver.assert_called_once_with()
    create_connector.assert_called_once_with(resolver=resolver)
    create_session.assert_called_once_with(connector=connector)


@pytest.mark.asyncio
async def test_http_get_returns_empty_bytes_on_connection_error():
    session = MockSession(connection_error=aiohttp.ClientConnectionError("DNS failed"))

    with mock.patch.object(aiohttp, "ClientSession", return_value=session):
        result = await http_get("https://example.invalid")

    assert result == b""


@pytest.mark.asyncio
async def test_http_get_returns_empty_bytes_on_http_error():
    error = aiohttp.ClientResponseError(
        request_info=mock.Mock(real_url="https://example.com"),
        history=(),
        status=503,
    )
    session = MockSession(response_error=error)

    with mock.patch.object(aiohttp, "ClientSession", return_value=session):
        result = await http_get("https://example.com")

    assert result == b""
