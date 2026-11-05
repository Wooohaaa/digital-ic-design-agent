import random
import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from fpga_agent.evidence import sha
from fpga_agent.video import tmds, geometry, wireframe, evidence_valid
import fpga_agent.video as video


class VideoReferenceTests(unittest.TestCase):
    def test_near_black_default_and_explicit_strict_black_are_distinct_contracts(self):
        project=Path(__file__).resolve().parents[1]/'benchmarks/hdmi_cube'
        try:checker=video.VideoChecks(project)
        except ValueError:self.skipTest('Icarus is unavailable')
        checker.run('background-level-contract',[
            'video_timing.v','tmds_encoder.v','cube_vertices.v','cube_framebuffer.v','hdmi_phy.v','hdmi_cube_top.v'],'''
`timescale 1ps/1ps
module tb;
reg sys=0;always #10000 sys=~sys;
hdmi_cube_top compatible(.I_sys_clk(sys));
hdmi_cube_top #(.BACKGROUND_LEVEL(8'd0)) strict_black(.I_sys_clk(sys));
initial begin
wait(compatible.pixel_reset===0 && strict_black.pixel_reset===0);
repeat(10)begin @(posedge compatible.clk_pixel);#1;
if(compatible.pixel_on!==0 || strict_black.pixel_on!==0)$fatal(1,"front buffer must start off");
if({compatible.red_data,compatible.green_data,compatible.blue_data}!==24'h010101)$fatal(1,"default compatibility background");
if({strict_black.red_data,strict_black.green_data,strict_black.blue_data}!==24'h000000)$fatal(1,"explicit strict black background");
end
$display("OPERATOR_PASS");$finish;end
endmodule
''')

    def test_failed_rerun_invalidates_previous_success_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'reports').mkdir()
            (root/'reports/simulation.json').write_text('{"status":"pass"}')
            with patch.object(video,'load_manifest',return_value={'verification':{'kind':'hdmi-cube'}}), \
                 patch.object(video,'simulation_input_hashes',return_value={}), \
                 patch.object(video.VideoChecks,'board',side_effect=ValueError('bad pin mapping')):
                with self.assertRaisesRegex(ValueError,'bad pin'):
                    video.validate_project(root)
            report=json.loads((root/'reports/simulation.json').read_text())
            self.assertEqual(report['status'],'fail')
            self.assertIn('bad pin',report['error'])

    def test_tmds_roundtrip_and_actual_transmitted_bit_disparity(self):
        rd=0; rng=random.Random(1915)
        for data in list(range(256))*4+[rng.randrange(256) for _ in range(2000)]:
            symbol,next_rd=tmds(data,True,0,rd)
            self.assertEqual(next_rd,rd+2*symbol.bit_count()-10)
            transition_word=(symbol&255)^(255 if symbol&512 else 0)
            decoded=transition_word&1
            for bit in range(1,8):
                value=((transition_word>>bit)^(transition_word>>(bit-1)))&1
                if not symbol&256:value^=1
                decoded|=value<<bit
            self.assertEqual(decoded,data)
            self.assertLessEqual(abs(next_rd),8)
            rd=next_rd
        for control,value in enumerate((852,171,340,683)):
            self.assertEqual(tmds(19,False,control,rd),(value,0))

    def test_all_poses_are_bounded_and_twelve_edges_contain_every_vertex(self):
        for angle in range(256):
            points=geometry(angle)
            self.assertEqual(len(points),8)
            self.assertTrue(all(0<=x<640 and 0<=y<480 for x,y in points))
        for angle in (0,1,16,32,63,64,127,128,191,255):
            self.assertTrue(set(geometry(angle))<=wireframe(angle))
        self.assertNotEqual(wireframe(0),wireframe(1))

    def test_missing_checks_stale_checker_or_repeated_frames_cannot_grant_gate(self):
        report={'verification_kind':'hdmi-cube','trusted_checker_sha256':sha(Path(video.__file__)),
                'checks':[{'id':x,'status':'pass'} for x in ['board','timing','tmds','geometry','framebuffer','serializer','top']],
                'frames':[{'angle':i,'bitmap_sha256':str(i)} for i in range(3)]}
        report['checks'][5].update(oddr_rtl_simulation='pass',oddr_phase_cases=16)
        self.assertTrue(evidence_valid(Path('.'),{},report)[0])
        report['frames'][1]['bitmap_sha256']='0'
        self.assertFalse(evidence_valid(Path('.'),{},report)[0])
        report['frames'][1]['bitmap_sha256']='1'
        report['checks'].pop()
        self.assertFalse(evidence_valid(Path('.'),{},report)[0])
        report['trusted_checker_sha256']='changed'
        self.assertFalse(evidence_valid(Path('.'),{},report)[0])


if __name__=='__main__':unittest.main()
