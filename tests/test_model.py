from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from fpga_agent import model


class RequestJsonTests(unittest.TestCase):
    def test_native_codex_planner_keeps_login_config_and_readonly_permissions(self):
        captured=[]
        def fake_run(args,**kwargs):
            captured.extend(args)
            Path(args[args.index('--output-last-message')+1]).write_text('{"ok":true}')
            return CompletedProcess(args,0,stdout='',stderr='')
        with tempfile.TemporaryDirectory() as directory, patch.object(model.subprocess,'run',side_effect=fake_run):
            self.assertEqual(model.request_json('plan',model='native',workspace=Path(directory)),{'ok':True})
        self.assertNotIn('--ignore-user-config',captured)
        self.assertNotIn('--model',captured)
        self.assertIn('read-only',captured)
        self.assertIn('approval_policy="never"',captured)

    def test_codex_uses_read_only_cli_and_parses_last_message(self) -> None:
        response_text = '{"ok": true}'
        captured: dict[str, object] = {}

        def fake_run(args, *, input, text, capture_output, timeout, cwd, check):
            captured.update(args=args, input=input, timeout=timeout, cwd=cwd)
            last_message = Path(args[args.index("--output-last-message") + 1])
            last_message.write_text(response_text, encoding="utf-8")
            return CompletedProcess(args, 0, stdout='{"type":"turn.completed"}\n', stderr="")

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            with patch.object(model.shutil, "which", return_value="/opt/codex"), patch.object(
                model.subprocess, "run", side_effect=fake_run
            ):
                result = model.request_json(
                    "return json",
                    model="gpt-6-luna",
                    workspace=workspace,
                    schema={"type": "object"},
                )

        self.assertEqual(result, {"ok": True})
        args = captured["args"]
        self.assertIsInstance(args, list)
        self.assertIn("--sandbox", args)
        self.assertIn("read-only", args)
        self.assertIn("--ignore-user-config", args)
        self.assertIn("--ephemeral", args)
        self.assertIn("--skip-git-repo-check", args)
        self.assertIn("--output-schema", args)
        self.assertEqual(captured["input"], "return json")
        self.assertEqual(captured["cwd"], str(workspace))
        self.assertGreater(captured["timeout"], 0)

    def test_responses_api_transport_uses_responses_and_no_temperature(self) -> None:
        api_result = {"output_text": '{"answer": 42}'}
        captured: dict[str, object] = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(api_result).encode("utf-8")

        def fake_open(request, timeout):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse()

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch.object(
            model.urllib.request, "urlopen", side_effect=fake_open
        ):
            result = model.request_json("make a JSON object", provider="api", model="gpt-6-luna")

        self.assertEqual(result, {"answer": 42})
        self.assertTrue(str(captured["url"]).endswith("/responses"))
        body = captured["body"]
        self.assertEqual(body["model"], "gpt-6-luna")
        self.assertNotIn("temperature", body)
        self.assertGreater(captured["timeout"], 0)

    def test_chat_mode_is_explicit_compatibility_transport(self) -> None:
        api_result = {"choices": [{"message": {"content": '{"ok": true}'}}]}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(api_result).encode("utf-8")

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch.object(
            model.urllib.request, "urlopen", return_value=FakeResponse()
        ) as open_mock:
            result = model.request_json("prompt", provider="chat")

        self.assertEqual(result, {"ok": True})
        self.assertTrue(open_mock.call_args.args[0].full_url.endswith("/chat/completions"))

    def test_exchange_reads_external_luna_response_and_audits_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "luna-response.json").write_text(
                json.dumps({"model": "gpt-6-luna", "response": {"generated": True}}), encoding="utf-8"
            )
            audit_dir = workspace / "audit"
            result = model.request_json(
                "prompt", provider="exchange", model="gpt-6-luna", workspace=workspace, audit_dir=audit_dir
            )
            self.assertEqual(result, {"generated": True})
            provenance = next(audit_dir.glob("*.provenance.json"))
            self.assertEqual(json.loads(provenance.read_text(encoding="utf-8"))["provider"], "exchange")

    def test_exchange_rejects_model_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "luna-response.json").write_text(
                json.dumps({"model": "other-model", "response": {"ok": True}}), encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "model='gpt-6-luna'"):
                model.request_json("prompt", provider="exchange", workspace=workspace)

    def test_invalid_json_is_rejected_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(model, "_codex_request", return_value=("not json", '{"event":"done"}\n')):
                with self.assertRaisesRegex(RuntimeError, "有效 JSON"):
                    model.request_json("prompt", audit_dir=Path(directory))
            files = list(Path(directory).iterdir())
            self.assertEqual(len(files), 4)
            response_file = next(path for path in files if path.name.endswith(".response.txt"))
            jsonl_file = next(path for path in files if path.name.endswith(".jsonl"))
            provenance_file = next(path for path in files if path.name.endswith(".provenance.json"))
            self.assertEqual(response_file.read_text(encoding="utf-8"), "not json")
            self.assertIn('"event":"done"', jsonl_file.read_text(encoding="utf-8"))
            provenance = json.loads(provenance_file.read_text(encoding="utf-8"))
            self.assertEqual(provenance["model"], "gpt-6-luna")
            self.assertEqual(provenance["sha256"]["prompt"], hashlib.sha256(b"prompt").hexdigest())
            self.assertEqual(provenance["sha256"]["schema"], hashlib.sha256(b"null").hexdigest())
            self.assertEqual(provenance["sha256"]["response"], hashlib.sha256(b"not json").hexdigest())
            self.assertNotIn("test-key", "".join(path.read_text(encoding="utf-8") for path in files))

    def test_codex_model_not_found_is_extracted_from_jsonl_stdout(self) -> None:
        jsonl_error = json.dumps(
            {
                "type": "error",
                "error": {
                    "code": "model_not_found",
                    "message": "The gpt-6-luna model is not supported for this account.",
                },
            }
        ) + "\n"

        def fake_run(args, **kwargs):
            return CompletedProcess(args, 1, stdout=jsonl_error, stderr="generic CLI failure")

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(model.shutil, "which", return_value="codex.exe"), patch.object(
                model.subprocess, "run", side_effect=fake_run
            ):
                with self.assertRaises(RuntimeError) as caught:
                    model.request_json("prompt", audit_dir=Path(directory))
            message = str(caught.exception)
            self.assertIn("model_not_found", message)
            self.assertIn("not supported for this account", message)
            self.assertIn("generic CLI failure", message)
            jsonl_file = next(Path(directory).glob("*.jsonl"))
            self.assertEqual(jsonl_file.read_text(encoding="utf-8"), jsonl_error)

    def test_payload_array_normalization_rejects_malformed_and_duplicate_entries(self) -> None:
        malformed_payloads = [
            {"files": [{"path": "rtl/a.v"}]},
            {"files": [{"path": "rtl/a.v", "content": "a"}, {"path": "rtl/a.v", "content": "b"}]},
            {"board_io": [{"port": "led"}]},
            {
                "board_io": [
                    {"port": "led0", "signal": "LED0"},
                    {"port": "led0", "signal": "LED1"},
                ]
            },
            {
                "board_io": [
                    {"port": "led0", "signal": "LED0"},
                    {"port": "led1", "signal": "LED0"},
                ]
            },
        ]
        for payload in malformed_payloads:
            with self.subTest(payload=payload), patch.object(model, "request_json", return_value=payload):
                with self.assertRaises(ValueError):
                    model.generate_payload("brief", "profile")

    def test_payload_arrays_normalize_to_mapping_shapes(self) -> None:
        generated = {
            "files": [
                {"path": "rtl/top.v", "content": "module top; endmodule"},
                {"path": "tb/top_tb.v", "content": "module top_tb; endmodule"},
            ],
            "board_io": [{"port": "led", "signal": "LED0"}],
        }
        with patch.object(model, "request_json", return_value=generated) as request_mock:
            result = model.generate_payload("brief", "profile", model="gpt-6-luna")

        self.assertEqual(
            result["files"],
            {"rtl/top.v": "module top; endmodule", "tb/top_tb.v": "module top_tb; endmodule"},
        )
        self.assertEqual(result["board_io"], {"led": "LED0"})
        self.assertEqual(result["_generation"], {"provider": "codex", "model": "gpt-6-luna"})
        self.assertEqual(request_mock.call_args.kwargs["model"], "gpt-6-luna")

    def test_payload_generation_metadata_overwrites_model_assertion(self) -> None:
        with patch.object(
            model,
            "request_json",
            return_value={"_generation": {"provider": "forged", "model": "other"}, "files": {}},
        ):
            result = model.generate_payload("brief", "profile", provider="exchange", model="gpt-6-luna")
        self.assertEqual(result["_generation"], {"provider": "exchange", "model": "gpt-6-luna"})


if __name__ == "__main__":
    unittest.main()
