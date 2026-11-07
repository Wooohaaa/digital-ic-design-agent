from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fpga_agent import task_planner


class TaskPlannerTests(unittest.TestCase):
    def test_main_writes_typed_decision_and_redacts_environment_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            request_path = Path(folder) / "request.json"
            request_path.write_text(json.dumps({
                "context": {"objective": "inspect the project"},
                "provider": "api",
                "model": "gpt-6-luna",
                "workspace": folder,
            }), encoding="utf-8")
            result = {
                "decision": "execute",
                "message": "Use one read-only step",
                "plan": ["inspect"],
                "memory": "",
                "action": "inspect",
                "arguments": {"path": "README.md", "api_key": "unit-test-secret"},
            }
            with patch.object(task_planner.sys, "argv", ["task_planner", str(request_path)]), \
                 patch.object(task_planner, "request_json", return_value=result) as request_json, \
                 patch.dict(os.environ, {"SERVICE_API_KEY": "unit-test-secret"}, clear=True):
                self.assertEqual(task_planner.main(), 0)
            request_json.assert_called_once()
            self.assertIn("schema", request_json.call_args.kwargs)
            written = json.loads((Path(folder) / "decision.json").read_text(encoding="utf-8"))
            self.assertEqual(written["decision"], "execute")
            self.assertEqual(written["arguments"]["api_key"], "[redacted]")

    def test_clean_redacts_nested_secret_values(self):
        with patch.dict(os.environ, {"SERVICE_TOKEN": "nested-secret"}, clear=True):
            result = task_planner.clean({"rows": [{"value": "nested-secret"}]})
        self.assertEqual(result, {"rows": [{"value": "[redacted]"}]})


if __name__ == "__main__":
    unittest.main()
