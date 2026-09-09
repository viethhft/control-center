import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from pipeline import command, compose, duration, sentences, workflow


class PipelineTest(unittest.TestCase):
    def test_vietnamese(self):
        self.assertEqual(sentences('Mẹ nhìn tôi.\n“Con đi đâu?”\nTôi im lặng…'), ['Mẹ nhìn tôi.', '“Con đi đâu?”', 'Tôi im lặng…'])

    def test_reference_workflow(self):
        graph = workflow('scene', ['alice.png', 'bob.png'], 1280, 720, 1)
        self.assertEqual(graph['5']['class_type'], 'TextEncodeQwenImageEditPlus')
        self.assertEqual(graph['5']['inputs']['image2'], ['21', 0])

    def test_real_ffmpeg_with_music_and_subtitles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', str(root / 'voice.wav')])
            command(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=220:duration=1', str(root / 'music.wav')])
            scenes = []
            for i, motion in enumerate(['push', 'right']):
                Image.new('RGB', (320, 180), ['red', 'blue'][i]).save(root / ('image%d.png' % i))
                scenes.append(dict(start=i, end=i + 1, image='image%d.png' % i, motion=motion))
            rows = [dict(start=0, end=1, text='Mẹ nhìn tôi.'), dict(start=1, end=2, text='Tôi bước vào nhà.')]
            p = dict(width=320, height=180, duration=2, audio='voice.wav', music='music.wav', burn_subtitles=True)
            compose(root, p, {'scenes': scenes}, rows, lambda _: None)
            self.assertAlmostEqual(duration(root / 'final.mp4'), 2, delta=.1)
            info = json.loads(command(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(root / 'final.mp4')]))
            self.assertEqual({s['codec_type'] for s in info['streams']}, {'video', 'audio'})
            clips = {f.name: f.stat().st_mtime_ns for f in root.glob('*.mp4') if f.name != 'final.mp4'}
            p['burn_subtitles'] = False
            compose(root, p, {'scenes': scenes}, rows, lambda _: None)
            self.assertEqual(clips, {f.name: f.stat().st_mtime_ns for f in root.glob('*.mp4') if f.name != 'final.mp4'})


if __name__ == '__main__':
    unittest.main()
