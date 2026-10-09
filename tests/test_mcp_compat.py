"""Regression test for the MCP SDK v1.25 client transport contract."""
import asyncio
from types import SimpleNamespace


def test_mcp_tool_uses_preconfigured_httpx_client(monkeypatch):
    import agents.common as common

    observed = {}

    class FakeHTTPClient:
        def __init__(self, **kwargs):
            observed['http'] = kwargs
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass

    class FakeTransport:
        async def __aenter__(self):
            return object(), object(), lambda: None
        async def __aexit__(self, *args):
            pass

    def fake_transport(url, *, http_client):
        observed['url'] = url
        observed['has_client'] = isinstance(http_client, FakeHTTPClient)
        return FakeTransport()

    class FakeSession:
        def __init__(self, read, write):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def initialize(self):
            pass
        async def call_tool(self, name, arguments):
            observed['tool'] = name
            observed['arguments'] = arguments
            return SimpleNamespace(isError=False, structuredContent={'result': {'ok': True}}, content=[])

    monkeypatch.setattr(common.httpx, 'AsyncClient', FakeHTTPClient)
    monkeypatch.setattr(common, 'streamable_http_client', fake_transport)
    monkeypatch.setattr(common, 'ClientSession', FakeSession)
    result = asyncio.run(common.mcp_tool('get_banking_snapshot', {'customer_id': 'C001'}))

    assert result == {'ok': True}
    assert observed['has_client']
    assert observed['url'] == common.MCP_URL
    assert observed['tool'] == 'get_banking_snapshot'
    assert observed['arguments'] == {'customer_id': 'C001'}
    assert observed['http']['timeout'].read == 300.0
