from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fpga_agent.evidence import sha
from fpga_agent.hardware_evidence import hardware_valid


class HardwareEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.model = self.project / "model.bin"
        self.tokenizer = self.project / "tokenizer.json"
        self.transcript = self.project / "reports/hardware-uart.txt"
        self.model.write_bytes(b"model fixture")
        self.tokenizer.write_text('{"tokens": []}\n', encoding="utf-8")
        self.transcript.parent.mkdir(parents=True)
        self.transcript.write_text("verified tokens\n", encoding="utf-8")
        package = Path(__file__).resolve().parents[1] / "fpga_agent"
        self.preflight = ({"program.bit": "bit-hash"}, {}, self.model, self.tokenizer, 0x1234ABCD)
        self.report = {
            "status": "pass",
            "bitstream_hashes": {"program.bit": "bit-hash"},
            "model_sha256": sha(self.model),
            "tokenizer_sha256": sha(self.tokenizer),
            "flash_crc32": "1234abcd",
            "jedec_id": "0020ba18",
            "diagnostic_logits_count": 1536,
            "diagnostic_argmax_tokens": [403, 276, 259],
            "diagnostic_max_absolute_error": 0.001,
            "verifier_sha256": {
                "tinyllm.py": sha(package / "tinyllm.py"),
                "tinyllm_hardware.py": sha(package / "tinyllm_hardware.py"),
            },
            "uart_transcript_sha256": sha(self.transcript),
            "generated_token_ids": [403, 276],
        }

    def validate(self):
        with patch("fpga_agent.tinyllm_hardware._preflight", return_value=self.preflight):
            return hardware_valid(self.project, self.report)

    def test_complete_current_evidence_passes(self):
        valid, reason = self.validate()
        self.assertTrue(valid, reason)

    def test_changed_uart_transcript_invalidates_evidence(self):
        self.transcript.write_text("different capture\n", encoding="utf-8")
        valid, reason = self.validate()
        self.assertFalse(valid)
        self.assertIn("transcript", reason)

    def test_nonfinite_or_out_of_tolerance_error_is_rejected(self):
        self.report["diagnostic_max_absolute_error"] = float("nan")
        valid, reason = self.validate()
        self.assertFalse(valid)
        self.assertIn("error", reason)


if __name__ == "__main__":
    unittest.main()
