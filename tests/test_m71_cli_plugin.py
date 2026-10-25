from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]


class M71CliPluginTests(unittest.TestCase):
    def _powershell(self, script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        command = ["powershell", "-NoProfile", "-File", str(REPOSITORY / "scripts" / script), *arguments]
        return subprocess.run(command, cwd=REPOSITORY, text=True, capture_output=True, timeout=30, check=False)

    def test_install_what_if_is_explicit_and_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._powershell(
                "install-m71-codex.ps1",
                "-Mode",
                "Compat",
                "-CodexHome",
                temporary,
                "-WhatIf",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            evidence = json.loads(result.stdout)
            self.assertEqual(evidence["status"], "WHAT_IF")
            self.assertFalse((Path(temporary) / "config.toml").exists())

    def test_package_contains_m71_native_and_compat_surfaces(self) -> None:
        manifest = json.loads((REPOSITORY / "plugins" / "shutu-skill-workshop" / "plugin.json").read_text(encoding="utf-8"))
        mcp = json.loads((REPOSITORY / "plugins" / "shutu-skill-workshop" / "mcp.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["extensions"]["com.openai"]["requires_local_executor"])
        self.assertEqual(mcp["mcpServers"]["shutu-workshop"]["type"], "stdio")
        self.assertTrue((REPOSITORY / "servers" / "shutu_workshop" / "capture" / "hook_import.py").is_file())
        self.assertTrue((REPOSITORY / "scripts" / "verify-m71-codex.ps1").is_file())


if __name__ == "__main__":
    unittest.main()
