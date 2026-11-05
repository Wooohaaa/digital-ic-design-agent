from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
import zipfile
import subprocess
from pathlib import Path
from unittest.mock import patch

from fpga_agent.cli import do_package
from fpga_agent.project import create_project, load_manifest
from fpga_agent.evidence import input_hashes, simulation_valid
from fpga_agent.evidence import sha
from fpga_agent.quality import lint_project, review_project
from fpga_agent.verify import verify_project


class EngineeringReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project=create_project('quality',Path(self.temp.name)/'quality')

    def test_missing_lint_tool_is_blocked_not_passed(self):
        with patch('fpga_agent.quality.shutil.which',return_value=None):
            result=lint_project(self.project)
        self.assertEqual(result['status'],'blocked')
        self.assertIsNone(result['returncode'])
        report=review_project(self.project,'simulation')
        gates={gate['id']:gate for gate in report['gates']}
        self.assertEqual(gates['RTL-001']['status'],'blocked')
        self.assertEqual(report['engineering_signoff'],'not_granted')
        self.assertFalse(report['artifact_ready'])

    def test_lint_uses_documented_flags_and_preserves_failure(self):
        with patch('fpga_agent.quality.shutil.which',return_value='verilator'), patch(
            'fpga_agent.quality.subprocess.run',side_effect=[
                subprocess.CompletedProcess([],0,'Verilator test-version',''),
                subprocess.CompletedProcess([],1,'','%Warning-WIDTH: mismatch')]):
            report=lint_project(self.project)
        self.assertIn('-Wall',report['command'])
        self.assertNotIn('-Wno-fatal',report['command'])
        self.assertEqual(report['status'],'fail')

    def test_malformed_report_produces_actionable_blocker(self):
        (self.project/'reports').mkdir(exist_ok=True)
        (self.project/'reports/simulation.json').write_text('[]')
        result=review_project(self.project)
        self.assertFalse(result['artifact_ready'])
        self.assertTrue(result['next_actions'])

    def test_pinned_hdl_testbench_is_reported_as_independent_verification(self):
        manifest=load_manifest(self.project)
        testbench=manifest['testbench_sources'][0]
        manifest['verification']={'kind':'trusted-testbench','trusted_files':[testbench]}
        manifest['trusted_testbench_hashes']={testbench:sha(self.project/testbench)}
        (self.project/'project.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
        simulation={'status':'pass','source_hashes':{},'returncode':0}
        with patch('fpga_agent.quality.read_report',side_effect=lambda path: simulation if path.name=='simulation.json' else {}), patch('fpga_agent.quality.simulation_valid',return_value=(True,'pass')):
            review=review_project(self.project,'simulation',write=False)
        gates={item['id']:item for item in review['gates']}
        self.assertEqual(review['verification_profile'],'trusted-hdl-testbench')
        self.assertEqual(gates['DV-002']['status'],'pass')
        self.assertEqual(gates['DV-003']['status'],'pass')

    def require_simulator(self):
        if not shutil.which('iverilog') and not (Path(__file__).resolve().parents[1]/'.tools/icarus/bin/iverilog.exe').exists():
            self.skipTest('Icarus required')

    def test_real_simulation_delivery_has_inventory_and_honest_review(self):
        self.require_simulator()
        self.assertEqual(verify_project(self.project).status,'pass')
        self.assertEqual(do_package(self.project,simulation_only=True),0)
        artifact=self.project/'deliverables/quality-simulation.zip'
        with zipfile.ZipFile(artifact) as archive:
            for line in archive.read('MANIFEST.sha256').decode().splitlines():
                expected,name=line.split('  ',1)
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(),expected)
            review=json.loads(archive.read('reports/engineering-review.json'))
            self.assertTrue(review['artifact_ready'])
            self.assertGreater(review['required_open_items'],0)
            self.assertEqual(json.loads(archive.read('delivery.json'))['engineering_signoff'],'not_granted')
            restored=Path(self.temp.name)/'restored'
            archive.extractall(restored)
            restored_manifest=load_manifest(restored)
            self.assertEqual(input_hashes(restored,restored_manifest),json.loads(archive.read('delivery.json'))['inputs'])
            self.assertTrue(simulation_valid(restored,restored_manifest,json.loads(archive.read('reports/simulation.json')))[0])
        self.assertTrue(artifact.with_suffix('.zip.sha256').is_file())

    def test_archive_detects_transient_source_tampering_and_keeps_prior_release(self):
        self.require_simulator()
        self.assertEqual(verify_project(self.project).status,'pass')
        self.assertEqual(do_package(self.project,simulation_only=True),0)
        artifact=self.project/'deliverables/quality-simulation.zip'
        prior=artifact.read_bytes()
        original_write=zipfile.ZipFile.write
        source=self.project/'rtl/hx1p35a_heartbeat.v'

        def corrupt(archive,filename,*args,**kwargs):
            path=Path(filename)
            if path == source:
                original=path.read_bytes()
                try:
                    path.write_bytes(original+b'\n// not the verified source\n')
                    return original_write(archive,filename,*args,**kwargs)
                finally:
                    path.write_bytes(original)
            return original_write(archive,filename,*args,**kwargs)

        with patch.object(zipfile.ZipFile,'write',new=corrupt):
            with self.assertRaisesRegex(ValueError,'Archived verification input differs'):
                do_package(self.project,simulation_only=True)
        self.assertEqual(artifact.read_bytes(),prior)


if __name__ == '__main__':
    unittest.main()
