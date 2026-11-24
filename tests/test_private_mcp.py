import importlib.util
import unittest

import test_publication as fixtures


@unittest.skipUnless(importlib.util.find_spec("mcp"), "選用 MCP SDK 未安裝")
class PrivateMCPTest(unittest.TestCase):
    setUp = fixtures.PublicationTest.setUp

    def test_private_tools_and_access_control(self):
        from src.anime_memes.private_mcp import create_mcp_app
        from starlette.testclient import TestClient
        token = "x" * 40
        app = create_mcp_app(self.db, token, "http://127.0.0.1:8765", ["testserver"], ["http://testserver"])
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
        with TestClient(app) as client:
            self.assertEqual(client.post("/mcp").status_code, 401)
            self.assertEqual(client.post("/mcp", headers={**headers, "Origin": "http://evil"}).status_code, 403)
            self.assertEqual(client.post("/mcp", headers={**headers, "Host": "evil"}).status_code, 403)
            def rpc(method, params=None):
                response = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
                self.assertEqual(response.status_code, 200, response.text)
                return response.json()
            rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
            tools = rpc("tools/list")["result"]["tools"]
            self.assertEqual({t["name"] for t in tools}, {"search_memes", "get_meme"})
            result = rpc("tools/call", {"name": "search_memes", "arguments": {"query": "復活", "limit": 1}})
            self.assertNotIn("PRIVATE_NOTE", str(result))
            self.assertNotIn("PRIVATE_MESSAGE", str(result))
            self.assertIn("復！活！", str(result))
            result = rpc("tools/call", {"name": "get_meme", "arguments": {"id": "meme_0"}})
            self.assertNotIn("isError': True", str(result))
            result = rpc("tools/call", {"name": "delete_meme", "arguments": {"id": "meme_0"}})
            self.assertTrue(result["result"]["isError"])

    def test_token_required(self):
        from src.anime_memes.private_mcp import create_mcp_app
        with self.assertRaises(ValueError):
            create_mcp_app(self.db, "short")
