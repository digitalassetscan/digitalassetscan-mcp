import io
import json
import unittest

from digitalassetscan_mcp.server import (
    CAIAPIClient,
    LEGACY_PROTOCOL_VERSION,
    MCP_PROTOCOL_VERSION,
    ProtocolSession,
    TOOLS,
    handle_message,
    serve_stdio,
)


MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "test-client", "version": "1.0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


def modern_request(request_id, method, params=None):
    merged = dict(params or {})
    merged["_meta"] = MODERN_META
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": merged}


def legacy_initialize(request_id=1):
    return {"jsonrpc": "2.0", "id": request_id, "method": "initialize", "params": {
        "protocolVersion": LEGACY_PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "legacy-test", "version": "1.0"},
    }}


class _Response:
    def __init__(self, status, payload):
        self.status = status
        self._body = json.dumps(payload).encode()

    def read(self, _limit):
        return self._body


class TestMCPServer(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def opener(request, timeout):
            self.calls.append((request.get_method(), request.full_url, timeout,
                               dict(request.header_items())))
            return _Response(200, {"canonical": True})

        self.client = CAIAPIClient("https://api.digitalassetscan.org", opener=opener)

    def test_tool_surface_is_bounded_and_read_only(self):
        self.assertEqual(
            [tool["name"] for tool in TOOLS],
            ["analyze_asset", "get_analysis_job", "list_assets", "get_methodology"],
        )
        self.assertFalse(TOOLS[0]["annotations"]["readOnlyHint"])
        self.assertTrue(all(tool["annotations"]["readOnlyHint"] for tool in TOOLS[1:]))
        self.assertTrue(all(not tool["annotations"]["destructiveHint"] for tool in TOOLS))

    def test_tools_delegate_to_canonical_api_routes(self):
        self.client.call_tool("analyze_asset", {
            "address": "0x47d13Fb0803409c7fE169A210d4e906165F1A432",
            "chain": "eip155:1", "block": 25860928,
        })
        self.client.call_tool("get_analysis_job", {"job_id": "safe_job-1"})
        self.client.call_tool("list_assets", {})
        self.client.call_tool("get_methodology", {})
        self.assertEqual([call[0] for call in self.calls], ["POST", "GET", "GET", "GET"])
        self.assertIn("/v1/jobs?", self.calls[0][1])
        self.assertTrue(self.calls[1][1].endswith("/v1/jobs/safe_job-1"))
        self.assertTrue(self.calls[2][1].endswith("/v1/assets"))
        self.assertTrue(self.calls[3][1].endswith("/.well-known/cai.json"))

    def test_canonical_requests_identify_the_adapter_without_credentials(self):
        self.client.call_tool("get_methodology", {})
        request_headers = self.calls[0][3]
        self.assertEqual(request_headers["User-agent"],
                         "digitalassetscan-cai-mcp/0.1.0")
        self.assertEqual(request_headers["Accept"], "application/json")

    def test_adapter_rejects_authority_expansion_and_ssrf_inputs(self):
        with self.assertRaises(ValueError):
            self.client.call_tool("analyze_asset", {"address": "0x" + "0" * 40, "score": 100})
        with self.assertRaises(ValueError):
            self.client.call_tool("get_analysis_job", {"job_id": "../metrics"})
        with self.assertRaises(ValueError):
            CAIAPIClient("http://example.com")
        with self.assertRaises(ValueError):
            CAIAPIClient("https://example.com/path")

    def test_current_mcp_discovery_and_tools_list(self):
        discovery = handle_message(
            modern_request(1, "server/discover"), self.client,
        )
        self.assertEqual(discovery["result"]["supportedVersions"][0], MCP_PROTOCOL_VERSION)
        self.assertEqual(discovery["result"]["resultType"], "complete")
        self.assertEqual(discovery["result"]["capabilities"], {"tools": {"listChanged": False}})
        listing = handle_message(
            modern_request(2, "tools/list"), self.client,
        )
        self.assertEqual(len(listing["result"]["tools"]), 4)
        self.assertEqual(listing["result"]["resultType"], "complete")
        self.assertEqual(
            listing["result"]["_meta"]["io.modelcontextprotocol/serverInfo"],
            {"name": "digitalassetscan-cai", "version": "0.1.0"},
        )

    def test_legacy_mcp_initialize_remains_bounded(self):
        initialize = handle_message(legacy_initialize(), self.client)
        self.assertEqual(initialize["result"]["protocolVersion"], LEGACY_PROTOCOL_VERSION)
        self.assertEqual(initialize["result"]["capabilities"], {"tools": {"listChanged": False}})

    def test_legacy_initialize_requires_complete_typed_params(self):
        valid_params = legacy_initialize()["params"]
        invalid_params = (
            None,
            [],
            {"protocolVersion": LEGACY_PROTOCOL_VERSION,
             "clientInfo": valid_params["clientInfo"]},
            {**valid_params, "capabilities": []},
            {"protocolVersion": LEGACY_PROTOCOL_VERSION, "capabilities": {}},
            {**valid_params, "clientInfo": []},
            {**valid_params, "clientInfo": {"version": "1.0"}},
            {**valid_params, "clientInfo": {"name": 1, "version": "1.0"}},
            {**valid_params, "clientInfo": {"name": "legacy-test"}},
            {**valid_params, "clientInfo": {"name": "legacy-test", "version": 1}},
        )
        for params in invalid_params:
            with self.subTest(params=params):
                request = {"jsonrpc": "2.0", "id": 10, "method": "initialize"}
                if params is not None:
                    request["params"] = params
                response = handle_message(request, self.client)
                self.assertEqual(response["error"]["code"], -32602)

    def test_tool_call_preserves_upstream_payload_and_status(self):
        response = handle_message(modern_request(
            3, "tools/call", {"name": "get_methodology", "arguments": {}},
        ), self.client)
        result = response["result"]
        self.assertEqual(result["structuredContent"], {"http_status": 200, "body": {"canonical": True}})
        self.assertFalse(result["isError"])
        self.assertEqual(result["resultType"], "complete")

    def test_current_protocol_metadata_is_required_and_versioned(self):
        missing = handle_message(
            {"jsonrpc": "2.0", "id": 5, "method": "server/discover", "params": {}},
            self.client,
        )
        self.assertEqual(missing["error"]["code"], -32602)
        bad = modern_request(6, "tools/list")
        bad["params"]["_meta"] = dict(MODERN_META)
        bad["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = "1900-01-01"
        unsupported = handle_message(bad, self.client)
        self.assertEqual(unsupported["error"]["code"], -32022)
        self.assertIn(MCP_PROTOCOL_VERSION, unsupported["error"]["data"]["supported"])

    def test_modern_client_info_is_optional_but_validated_when_present(self):
        without_client_info = modern_request(7, "server/discover")
        without_client_info["params"]["_meta"] = dict(MODERN_META)
        without_client_info["params"]["_meta"].pop(
            "io.modelcontextprotocol/clientInfo")
        accepted = handle_message(without_client_info, self.client)
        self.assertEqual(accepted["result"]["supportedVersions"], [MCP_PROTOCOL_VERSION])

        malformed = modern_request(8, "server/discover")
        malformed["params"]["_meta"] = dict(MODERN_META)
        malformed["params"]["_meta"]["io.modelcontextprotocol/clientInfo"] = "invalid"
        rejected = handle_message(malformed, self.client)
        self.assertEqual(rejected["error"]["code"], -32602)
        self.assertIn("when present", rejected["error"]["message"])

        incomplete = modern_request(9, "server/discover")
        incomplete["params"]["_meta"] = dict(MODERN_META)
        incomplete["params"]["_meta"]["io.modelcontextprotocol/clientInfo"] = {
            "name": "missing-version",
        }
        rejected = handle_message(incomplete, self.client)
        self.assertEqual(rejected["error"]["code"], -32602)

    def test_protocol_session_pins_modern_and_rejects_legacy_switch(self):
        session = ProtocolSession()
        self.assertIsNone(session.select(modern_request(1, "server/discover")))
        self.assertEqual(session.era, "modern")
        self.assertIsNone(session.select(modern_request(2, "tools/list")))
        switched = session.select(legacy_initialize(3))
        self.assertEqual(switched["error"]["code"], -32022)

    def test_protocol_session_allows_optional_discovery_for_modern_clients(self):
        session = ProtocolSession()
        self.assertIsNone(session.select(modern_request(1, "tools/list")))
        self.assertEqual(session.era, "modern")
        switched = session.select(legacy_initialize(2))
        self.assertEqual(switched["error"]["code"], -32022)

    def test_protocol_session_pins_legacy_and_rejects_modern_switch(self):
        session = ProtocolSession()
        self.assertIsNone(session.select(legacy_initialize()))
        self.assertEqual(session.era, "legacy")
        self.assertIsNone(session.select(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ))
        switched = session.select(modern_request(3, "server/discover"))
        self.assertEqual(switched["error"]["code"], -32022)

    def test_stdio_requires_opening_selection_and_rejects_cross_era_switch(self):
        opening = modern_request(1, "server/discover")
        opening["params"]["_meta"] = dict(MODERN_META)
        opening["params"]["_meta"].pop("io.modelcontextprotocol/clientInfo")
        source = io.StringIO(
            json.dumps(opening) + "\n"
            + json.dumps(legacy_initialize(2)) + "\n"
        )
        sink = io.StringIO()
        serve_stdio(self.client, source, sink)
        messages = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertIn("result", messages[0])
        self.assertEqual(messages[1]["error"]["code"], -32022)
        self.assertIn("cannot change", messages[1]["error"]["message"])

    def test_stdio_pins_legacy_and_rejects_modern_switch(self):
        source = io.StringIO(
            json.dumps(legacy_initialize()) + "\n"
            + json.dumps(modern_request(2, "server/discover")) + "\n"
        )
        sink = io.StringIO()
        serve_stdio(self.client, source, sink)
        messages = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(messages[0]["result"]["protocolVersion"], LEGACY_PROTOCOL_VERSION)
        self.assertEqual(messages[1]["error"]["code"], -32022)

    def test_stdio_malformed_opening_request_is_bounded(self):
        source = io.StringIO(
            '{"jsonrpc":"2.0","id":1,"method":"server/discover","params":[]}\n'
        )
        sink = io.StringIO()
        serve_stdio(self.client, source, sink)
        self.assertEqual(
            json.loads(sink.getvalue())["error"],
            {"code": -32600, "message": "Protocol era not selected"},
        )

    def test_tool_argument_validation_precedes_upstream_call(self):
        invalid = (
            {"address": "not-an-address"},
            {"address": "0x" + "0" * 40, "chain": "eip155:137"},
            {"address": "0x" + "0" * 40, "block": -1},
            {"address": "0x" + "0" * 40, "block": True},
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.client.call_tool("analyze_asset", arguments)
        self.assertEqual(self.calls, [])

    def test_stdio_is_newline_delimited_and_notifications_are_silent(self):
        source = io.StringIO(
            '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
            + json.dumps(legacy_initialize(3)) + "\n"
            '{"jsonrpc":"2.0","id":4,"method":"ping","params":{}}\n'
        )
        sink = io.StringIO()
        serve_stdio(self.client, source, sink)
        messages = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[1], {"jsonrpc": "2.0", "id": 4, "result": {}})

        modern_notification = modern_request(None, "notifications/cancelled")
        modern_notification.pop("id")
        source = io.StringIO(
            json.dumps(modern_notification) + "\n"
            + json.dumps(modern_request(5, "server/discover")) + "\n"
        )
        sink = io.StringIO()
        serve_stdio(self.client, source, sink)
        messages = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["id"], 5)


if __name__ == "__main__":
    unittest.main()
