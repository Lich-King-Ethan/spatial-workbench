"""Exercise actual QML presentation JavaScript; native Plasma needs its own host."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "requires Node for QML JavaScript logic checks")
class PlaybackPresentation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (Path(__file__).resolve().parents[1] / "plasma/PlaybackState.js").read_text()
        # The QML library pragma is not part of ECMAScript; functions are shared.
        cls.source = cls.source.replace(".pragma library\n", "", 1)

    def evaluate(self, expression):
        result = subprocess.run(["node", "-e", self.source + "\nconsole.log(JSON.stringify(" + expression + "));"],
                                capture_output=True, text=True, timeout=5, check=True)
        return json.loads(result.stdout)

    def test_atmos_requires_live_decode_evidence(self):
        self.assertEqual(self.evaluate('sourceKind({running:true, loaded:true, content_format:"Dolby Atmos", source_mode:"spatial", object_count:16, renderer_ready:false})'), "stereo")
        self.assertEqual(self.evaluate('sourceKind({running:true, loaded:true, content_format:"Dolby Atmos", source_mode:"spatial", object_count:16, renderer_ready:true})'), "atmos")

    def test_atmos_badge_without_objects_is_not_atmos(self):
        self.assertEqual(self.evaluate('sourceKind({running:true, loaded:true, renderer_ready:true, content_format:"Dolby Atmos", source_mode:"spatial", object_count:0})'), "binaural")
        self.assertEqual(self.evaluate('sourceKind({running:false, loaded:true, renderer_ready:true, content_format:"Dolby Atmos", source_mode:"spatial", object_count:16})'), "none")

    def test_other_decoded_object_format_keeps_its_own_label(self):
        self.assertEqual(self.evaluate('sourceKind({running:true, loaded:true, renderer_ready:true, content_format:"DTS:X", source_mode:"spatial", object_count:4})'), "dtsx")

    def test_progress_bounds_and_unknown_duration(self):
        self.assertEqual(self.evaluate('[progress({position:200,duration:100}), progress({position:-1,duration:100}), progress({position:10,duration:null}), progress({position:NaN,duration:60})]'), [1, 0, 0, 0])

    def test_time_labels_support_hours_and_invalid_inputs(self):
        self.assertEqual(self.evaluate('[durationText(5),durationText(65),durationText(3661),durationText(NaN),durationText(null)]'), ["0:05", "1:05", "1:01:01", "0:00", "0:00"])
