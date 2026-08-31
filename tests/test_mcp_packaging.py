import json
import pathlib
import re
import tomllib
import unittest

import digitalassetscan_mcp


ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestMCPPackaging(unittest.TestCase):
    def test_distribution_identity_and_console_entry_point_are_consistent(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertEqual(metadata["project"]["name"], "digitalassetscan-mcp")
        self.assertEqual(metadata["project"]["version"], digitalassetscan_mcp.__version__)
        self.assertEqual(metadata["project"]["dependencies"], [])
        self.assertEqual(metadata["project"]["authors"],
                         [{"name": "CurrenC Corporation"}])
        self.assertEqual(metadata["project"]["license"], "MIT")
        self.assertEqual(metadata["project"]["license-files"], ["LICENSE.md"])
        self.assertEqual(
            metadata["project"]["urls"]["Repository"],
            "https://github.com/digitalassetscan/digitalassetscan-mcp",
        )
        self.assertEqual(
            metadata["project"]["scripts"]["digitalassetscan-mcp"],
            "digitalassetscan_mcp.server:main",
        )

    def test_distribution_contains_only_the_standalone_adapter_package(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertEqual(metadata["tool"]["setuptools"]["packages"],
                         ["digitalassetscan_mcp"])

    def test_license_and_registry_identity_are_release_consistent(self):
        license_text = (ROOT / "LICENSE.md").read_text()
        self.assertIn("MIT License", license_text)
        self.assertIn("Copyright (c) 2026 CurrenC Corporation", license_text)

        readme = (ROOT / "README.md").read_text()
        registry = json.loads((ROOT / "server.json").read_text())
        name = "org.digitalassetscan/digitalassetscan-mcp"
        self.assertIn(f"<!-- mcp-name: {name} -->", readme)
        self.assertEqual(registry["name"], name)
        self.assertEqual(registry["version"], digitalassetscan_mcp.__version__)
        self.assertEqual(registry["title"], "Digital Asset Scan MCP")
        self.assertLessEqual(len(registry["description"]), 100)
        self.assertEqual(
            registry["repository"],
            {
                "url": "https://github.com/digitalassetscan/digitalassetscan-mcp",
                "source": "github",
            },
        )
        self.assertEqual(
            registry["packages"],
            [{
                "registryType": "pypi",
                "identifier": "digitalassetscan-mcp",
                "version": digitalassetscan_mcp.__version__,
                "transport": {"type": "stdio"},
            }],
        )

        manifest = (ROOT / "MANIFEST.in").read_text().splitlines()
        self.assertIn("include LICENSE.md", manifest)
        self.assertIn("include server.json", manifest)
        self.assertEqual(
            len(re.findall(r"mcp-name:\s*org\.digitalassetscan/digitalassetscan-mcp(?=\s|-->)",
                           readme)),
            1,
        )


if __name__ == "__main__":
    unittest.main()
