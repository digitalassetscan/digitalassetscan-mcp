# Digital Asset Scan MCP

<!-- mcp-name: org.digitalassetscan/digitalassetscan-mcp -->

`digitalassetscan-mcp` is a lightweight stdio Model Context Protocol adapter
for the public Digital Asset Scan API. Digital Asset Scan is the product; CAI
is its analytical engine and methodology.

The package exposes four tools: `analyze_asset`, `get_analysis_job`,
`list_assets`, and `get_methodology`. It contains no analytical or blockchain
RPC implementation, stores no responses, needs no credentials, and has no
third-party runtime dependencies. It requires outbound HTTPS access to
`https://api.digitalassetscan.org`.

Analysis admission is asynchronous. Use `get_analysis_job` to poll an admitted
job to a terminal operational state. Analytical `UNKNOWN` and `UNRESOLVED`
states are valid results, not MCP transport failures. Score is currently
withheld because no validated scoring construct is published; Confidence is
reported independently when valid.

This source tree can be built and installed in an isolated environment. Public
PyPI publication has not occurred:

```console
python -m build
python -m venv /tmp/digitalassetscan-mcp-venv
/tmp/digitalassetscan-mcp-venv/bin/python -m pip install dist/digitalassetscan_mcp-0.1.0-py3-none-any.whl
```

Generic stdio client configuration after installation:

```json
{
  "mcpServers": {
    "digitalassetscan": {
      "command": "digitalassetscan-mcp",
      "args": []
    }
  }
}
```

The process reads one JSON-RPC message per line from stdin and writes only MCP
JSON-RPC framing to stdout. Closing stdin shuts it down. Analysis admission is
asynchronous: poll the returned job with `get_analysis_job` until terminal.
Results are neutral analytical evidence for informational use, not investment
advice or a prediction of asset quality, safety, merit, price, or future
performance.

Digital Asset Scan MCP is released under the MIT License. Copyright (c) 2026
CurrenC Corporation.
