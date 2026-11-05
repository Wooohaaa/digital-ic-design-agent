import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from fpga_agent.video import wireframe
from fpga_agent.video_hardware import inspect_frame, analyze_frames, hardware_run

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image=None


@unittest.skipIf(Image is None,'Pillow optional video dependency is not installed')
class VideoHardwareTests(unittest.TestCase):
    def frame(self,root,name,angle):
        path=root/name
        image=Image.new('RGB',(640,480))
        draw=ImageDraw.Draw(image)
        draw.point(list(wireframe(angle)),fill=(40,220,255))
        image.save(path)
        return path

    def test_rotating_reference_wireframe_is_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            paths=[self.frame(root,f'{i}.png',i*8) for i in range(8)]
            result=analyze_frames(paths)
            self.assertEqual(result['status'],'pass',result)
            self.assertEqual(result['valid_frames'],8)

    def test_static_valid_cube_is_not_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            path=self.frame(root,'still.png',24)
            self.assertEqual(inspect_frame(path)['status'],'pass')
            self.assertEqual(analyze_frames([path]*8)['status'],'fail')

    def test_black_no_signal_bars_and_unrelated_cyan_shape_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            images=[Image.new('RGB',(640,480)) for _ in range(4)]
            ImageDraw.Draw(images[1]).text((290,240),'No Signal',fill='white')
            ImageDraw.Draw(images[2]).rectangle((160,0,240,480),fill=(0,255,255))
            ImageDraw.Draw(images[3]).rectangle((200,100,440,380),outline=(40,220,255))
            for i,image in enumerate(images):
                path=root/f'invalid-{i}.png';image.save(path)
                self.assertEqual(inspect_frame(path)['status'],'fail',str(path))

    def test_failed_preflight_invalidates_previous_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'reports').mkdir()
            report=root/'reports/hardware-video.json';report.write_text('{"status":"pass"}')
            # This tests report invalidation, independently of a real capture
            # that may own the default board while the test suite is running.
            with patch('fpga_agent.hardware_lock.board_lease',return_value=nullcontext()), \
                 patch('fpga_agent.video_hardware._hardware_run',side_effect=RuntimeError('device occupied')):
                with self.assertRaisesRegex(RuntimeError,'device occupied'):hardware_run(root)
            result=json.loads(report.read_text())
            self.assertEqual(result['status'],'fail')
            self.assertEqual(result['error'],'device occupied')


if __name__=='__main__':unittest.main()
