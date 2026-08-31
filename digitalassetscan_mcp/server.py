"""Thin stdio MCP adapter for the canonical Digital Asset Scan HTTPS API.

This module contains no analytical logic.  It preserves the production API's
asynchronous job lifecycle and returns its JSON documents without reinterpretation.
"""

import argparse
import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


MCP_PROTOCOL_VERSION = "2026-07-28"
LEGACY_PROTOCOL_VERSION = "2025-11-25"
MODERN_PROTOCOL_VERSIONS = (MCP_PROTOCOL_VERSION,)
PROTOCOL_ERA_MODERN = "modern"
PROTOCOL_ERA_LEGACY = "legacy"
DEFAULT_API_BASE = "https://api.digitalassetscan.org"
MAX_RESPONSE_BYTES = 1024 * 1024
SERVER_INFO = {"name": "digitalassetscan-cai", "version": "0.1.0"}
USER_AGENT = "digitalassetscan-cai-mcp/0.1.0"
_ADDRESS_PATTERN = re.compile(r"0x[0-9a-fA-F]{40}\Z")


TOOLS = (
    {
        "name": "analyze_asset",
        "title": "Submit CAI asset analysis",
        "description": (
            "Submit an asynchronous analysis to the canonical CAI API. Returns "
            "the admission document; use get_analysis_job to poll. Unresolved "
            "Claims in a successful result are analytical states, not failures."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "pattern": "^0x[0-9a-fA-F]{40}$"},
                "chain": {"type": "string", "const": "eip155:1", "default": "eip155:1"},
                "block": {"type": "integer", "minimum": 0},
            },
            "required": ["address"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False,
                        "idempotentHint": False, "openWorldHint": True},
    },
    {
        "name": "get_analysis_job",
        "title": "Get CAI analysis job",
        "description": (
            "Retrieve canonical CAI job state and, after success, the unchanged "
            "schema 0.6 result. Missing/expired jobs are operational lifecycle "
            "conditions, not analytical unresolvedness."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string", "minLength": 1, "maxLength": 256}},
            "required": ["job_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": True},
    },
    {
        "name": "list_assets",
        "title": "List Digital Asset Scan launch assets",
        "description": (
            "Return the canonical API's neutral 20-asset launch catalog. The "
            "catalog is discovery-only, unranked, and does not imply endorsement."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": True},
    },
    {
        "name": "get_methodology",
        "title": "Get CAI methodology and discovery",
        "description": (
            "Return CAI's canonical machine discovery document, including scope, "
            "methodology version, endpoint links, and Score/Confidence semantics."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": True},
    },
)


class CAIAPIClient:
    """Bounded client for exactly one configured CAI API authority."""

    def __init__(self, base_url=DEFAULT_API_BASE, *, timeout=120, opener=urlopen):
        self.base_url = base_url.rstrip("/")
        parsed = urlsplit(self.base_url)
        loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
        if parsed.scheme not in ({"http", "https"} if loopback else {"https"}):
            raise ValueError("CAI API must use HTTPS except for an explicit loopback base")
        if (not parsed.hostname or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.path):
            raise ValueError("CAI API base must be an origin without a path, query, or fragment")
        try:
            parsed.port
        except ValueError as error:
            raise ValueError("CAI API base contains an invalid port") from error
        self.timeout = timeout
        self.opener = opener

    def request(self, method, path, query=None):
        url = self.base_url + path
        if query:
            url += "?" + urlencode(query)
        request = Request(url, method=method, headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        })
        try:
            response = self.opener(request, timeout=self.timeout)
            status = response.status
        except HTTPError as error:
            response = error
            status = error.code
        except (URLError, TimeoutError, OSError) as error:
            raise RuntimeError("canonical CAI API is unavailable") from error
        if hasattr(response, "geturl"):
            final = urlsplit(response.geturl())
            configured = urlsplit(self.base_url)
            if (final.scheme, final.hostname, final.port) != (
                    configured.scheme, configured.hostname, configured.port):
                raise RuntimeError("canonical CAI API redirected to another authority")
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise RuntimeError("canonical CAI API response exceeded the adapter limit")
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("canonical CAI API returned invalid JSON") from error
        return status, payload

    def call_tool(self, name, arguments):
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object")
        if name == "analyze_asset":
            allowed = {"address", "chain", "block"}
            if set(arguments) - allowed or "address" not in arguments:
                raise ValueError("analyze_asset requires address and accepts only chain and block")
            address = arguments["address"]
            if not isinstance(address, str) or _ADDRESS_PATTERN.fullmatch(address) is None:
                raise ValueError("address must be a 20-byte hexadecimal Ethereum address")
            if "chain" in arguments and arguments["chain"] != "eip155:1":
                raise ValueError("chain must be eip155:1")
            if "block" in arguments and (
                    not isinstance(arguments["block"], int)
                    or isinstance(arguments["block"], bool)
                    or arguments["block"] < 0):
                raise ValueError("block must be a non-negative integer")
            query = {"address": arguments["address"]}
            if "chain" in arguments:
                query["chain"] = arguments["chain"]
            if "block" in arguments:
                query["block"] = arguments["block"]
            return self.request("POST", "/v1/jobs", query)
        if name == "get_analysis_job":
            if set(arguments) != {"job_id"} or not isinstance(arguments["job_id"], str):
                raise ValueError("get_analysis_job requires only a job_id string")
            job_id = arguments["job_id"]
            if not job_id or len(job_id) > 256 or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for char in job_id):
                raise ValueError("job_id is malformed")
            return self.request("GET", "/v1/jobs/" + job_id)
        if name == "list_assets" and not arguments:
            return self.request("GET", "/v1/assets")
        if name == "get_methodology" and not arguments:
            return self.request("GET", "/.well-known/cai.json")
        raise ValueError("unknown tool or unexpected arguments")


def _tool_result(status, payload):
    failed = status < 200 or status >= 300
    return {
        "resultType": "complete",
        "content": [{"type": "text", "text": json.dumps(payload, sort_keys=True)}],
        "structuredContent": {"http_status": status, "body": payload},
        "isError": failed,
    }


def _modern_metadata(message):
    params = message.get("params", {})
    if not isinstance(params, dict):
        raise ValueError("request params must be an object")
    metadata = params.get("_meta")
    if not isinstance(metadata, dict):
        return None
    return metadata


def _validate_modern_request(message):
    metadata = _modern_metadata(message)
    if metadata is None:
        return False
    version = metadata.get("io.modelcontextprotocol/protocolVersion")
    if version != MCP_PROTOCOL_VERSION:
        return {
            "jsonrpc": "2.0", "id": message.get("id"),
            "error": {
                "code": -32022,
                "message": "Unsupported protocol version",
                "data": {
                    "supported": list(MODERN_PROTOCOL_VERSIONS),
                    "requested": version,
                },
            },
        }
    if not isinstance(metadata.get("io.modelcontextprotocol/clientCapabilities"), dict):
        raise ValueError("modern requests require clientCapabilities metadata")
    client_info_key = "io.modelcontextprotocol/clientInfo"
    if client_info_key in metadata:
        client_info = metadata[client_info_key]
        if (not isinstance(client_info, dict)
                or not isinstance(client_info.get("name"), str)
                or not isinstance(client_info.get("version"), str)):
            raise ValueError(
                "modern clientInfo metadata must contain string name and version when present")
    return True


def _validate_legacy_initialize(message):
    params = message.get("params")
    if not isinstance(params, dict):
        raise ValueError("legacy initialize params must be an object")
    if params.get("protocolVersion") != LEGACY_PROTOCOL_VERSION:
        raise ValueError("Unsupported legacy protocol version")
    if not isinstance(params.get("capabilities"), dict):
        raise ValueError("legacy initialize capabilities must be an object")
    client_info = params.get("clientInfo")
    if (not isinstance(client_info, dict)
            or not isinstance(client_info.get("name"), str)
            or not isinstance(client_info.get("version"), str)):
        raise ValueError(
            "legacy initialize clientInfo must contain string name and version")


class ProtocolSession:
    """Pin one stdio connection to its opening MCP protocol era."""

    def __init__(self):
        self.era = None

    def select(self, message):
        """Select or verify the era; return a bounded cross-era error if needed."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return None
        if "id" not in message:  # notifications neither select nor change an era
            return None
        method = message.get("method")
        params = message.get("params", {})
        modern = isinstance(params, dict) and isinstance(params.get("_meta"), dict)
        requested_era = None
        if modern:
            requested_era = PROTOCOL_ERA_MODERN
        elif method == "initialize" and not modern:
            requested_era = PROTOCOL_ERA_LEGACY
        elif self.era is None:
            return {
                "jsonrpc": "2.0", "id": message.get("id"),
                "error": {"code": -32600, "message": "Protocol era not selected"},
            }
        else:
            requested_era = PROTOCOL_ERA_LEGACY

        if self.era is None:
            self.era = requested_era
        elif requested_era != self.era:
            return {
                "jsonrpc": "2.0", "id": message.get("id"),
                "error": {
                    "code": -32022,
                    "message": "Protocol era cannot change within a session",
                    "data": {"selectedEra": self.era, "requestedEra": requested_era},
                },
            }
        return None


def handle_message(message, client):
    """Handle the bounded MCP request subset used by this stateless server."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                "error": {"code": -32600, "message": "Invalid Request"}}
    if "id" not in message:  # notifications require no response
        return None
    request_id = message["id"]
    method = message.get("method")
    try:
        modern = _validate_modern_request(message)
        if isinstance(modern, dict):
            return modern
        if method == "server/discover":
            if modern is not True:
                return {"jsonrpc": "2.0", "id": request_id,
                        "error": {"code": -32602, "message": "Invalid params"}}
            result = {
                "resultType": "complete",
                "supportedVersions": list(MODERN_PROTOCOL_VERSIONS),
                "capabilities": {"tools": {"listChanged": False}},
                "_meta": {
                    "io.modelcontextprotocol/serverInfo": SERVER_INFO,
                },
                "instructions": (
                    "Use CAI as read-only analytical evidence, not investment advice. "
                    "Poll admitted jobs; unresolved Claims are not transport failures."
                ),
                "ttlMs": 3600000,
                "cacheScope": "public",
            }
        elif method == "initialize":
            if modern is True:
                return {"jsonrpc": "2.0", "id": request_id,
                        "error": {"code": -32601, "message": "Method not found"}}
            _validate_legacy_initialize(message)
            result = {
                "protocolVersion": LEGACY_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": (
                    "Use CAI as read-only analytical evidence, not investment advice. "
                    "Poll admitted jobs; do not treat unresolved Claims as transport failures."
                ),
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": list(TOOLS)}
            if modern is True:
                result.update({"resultType": "complete", "ttlMs": 3600000,
                               "cacheScope": "public"})
        elif method == "tools/call":
            params = message.get("params", {})
            status, payload = client.call_tool(params.get("name"), params.get("arguments", {}))
            result = _tool_result(status, payload)
            if modern is not True:
                result.pop("resultType")
        else:
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32601, "message": "Method not found"}}
        if modern is True:
            result.setdefault("_meta", {})[
                "io.modelcontextprotocol/serverInfo"] = SERVER_INFO
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except ValueError as error:
        return {"jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32602, "message": str(error)}}
    except RuntimeError as error:
        return {"jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32603, "message": str(error)}}


def serve_stdio(client, source=sys.stdin, sink=sys.stdout):
    session = ProtocolSession()
    for line in source:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "Parse error"}}
        else:
            response = session.select(message)
            if response is None:
                response = handle_message(message, client)
        if response is not None:
            sink.write(json.dumps(response, separators=(",", ":")) + "\n")
            sink.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description="stdio MCP adapter for the canonical CAI API")
    parser.add_argument("--api-base", default=os.environ.get("CAI_API_BASE_URL", DEFAULT_API_BASE))
    args = parser.parse_args(argv)
    serve_stdio(CAIAPIClient(args.api_base))


if __name__ == "__main__":
    main()
