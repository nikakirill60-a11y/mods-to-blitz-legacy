"""Tests for dava.py / scgtool.py on the synthetic fixture.  Run:  python tools/scgviewer/tests/test_dava.py"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import dava  # noqa: E402
import scgtool  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
SC2 = os.path.join(FIX, "demo_tank.sc2.dvpl")


class FixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = dava.load_scene(SC2)

    def test_counts(self):
        s = self.s
        self.assertEqual((s.sc2["version"], s.sc2["layout"], s.sc2["trailing"]), (43, "modern", 0))
        self.assertEqual((len(s.meshes), len(s.materials), s.lods()), (7, 6, [-1, 0, 1]))

    def test_world_matches_stored(self):
        for e in self.s.entities():
            tc = dava.component(e.raw, "TransformComponent")
            w = dava.trs_matrix(tc["tc.worldTranslation"], tc["tc.worldRotation"], tc["tc.worldScale"])
            self.assertLess(max(abs(a - b) for a, b in zip(w, e.world)), 1e-5, e.name)

    def test_draw_list(self):
        names = [e.name for e, _b, _m in self.s.draw_list(0, include_service=False)]
        self.assertEqual(names, ["hull", "chassis_track", "turret_01", "gun_01"])
        self.assertEqual(len(list(self.s.draw_list(0))), 6)

    def test_materials(self):
        _e, b, _m = next(x for x in self.s.draw_list(0, False) if x[0].name == "chassis_track")
        self.assertEqual(self.s.material_name(b.material), "demo_track_mtr")
        self.assertIn("Alphatest", self.s.material_fx(b.material))
        self.assertEqual(self.s.textures_for(b.material), {"albedo": "images/demo_track.tex"})
        self.assertTrue(scgtool.find_texture("images/demo_track.tex", SC2).endswith("demo_track.dx11.dds.dvpl"))

    def test_obj_export(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "t.obj")
            scgtool.main(["obj", SC2, "-o", out])
            txt = open(out, encoding="utf-8").read()
            self.assertEqual(txt.count("\nf "), 92)
            self.assertIn("map_Kd", open(out[:-4] + ".mtl", encoding="utf-8").read())

    def test_crc(self):
        raw = bytearray(open(SC2, "rb").read())
        raw[5] ^= 0xFF
        with self.assertRaises(dava.DavaError):
            dava.decode_dvpl(bytes(raw))


if __name__ == "__main__":
    unittest.main(verbosity=2)
