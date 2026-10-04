"""Meaningful parser and data-integrity checks; run with python -m unittest discover -s tests."""
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from irc_ibo_studio.core import Frame, Trajectory, align_frames, load_project, load_trajectory, save_project, save_xyz

ROOT = Path(__file__).resolve().parents[1]


class CoreTests(unittest.TestCase):
    def test_synthetic_irc_accepted_and_trials(self):
        from test_merge import write_log
        with tempfile.TemporaryDirectory() as directory:
            path=write_log(Path(directory)/'synthetic.log','forward',n=4)
            accepted=load_trajectory(path)
            evaluations=load_trajectory(path,include_trials=True)
            self.assertEqual(len(accepted.frames),5)
            self.assertEqual(len(evaluations.frames),9)
            self.assertEqual(sum(f.status=='trial' for f in evaluations.frames),4)
            self.assertEqual([f.point for f in accepted.frames],list(range(5)))
            self.assertEqual(accepted.frames[0].energy,-100.)
            self.assertAlmostEqual(accepted.frames[-1].energy,-100.16)
            self.assertAlmostEqual(accepted.frames[-1].reaction_coordinate,.4)

    def sample(self):
        return Frame(["O", "H", "H"], [[0., 0., 0.], [.8, .6, 0.], [-.8, .6, 0.]], 10,
                     energy=-76.0, reaction_coordinate=1.0, source_line=50)

    def test_xyz_roundtrip_keeps_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "points.xyz"
            frames = [self.sample(), self.sample()]
            frames[1].point, frames[1].status = 11, "trial"
            save_xyz(frames, path)
            loaded = load_trajectory(path)
            self.assertEqual(loaded.frames, frames)

    def test_input_standard_not_duplicate(self):
        orientation = (" {kind} orientation:\n -----\n Center Atomic Atomic Coordinates (Angstroms)\n"
                       " Number Number Type X Y Z\n -----\n 1 1 0 0.0 0.0 0.0\n -----\n")
        content = orientation.format(kind="Input") + orientation.format(kind="Standard")
        content += " SCF Done: E(UHF) = -5.0D-01 A.U.\n Point Number: 0 Path Number: 1\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mini.log"
            path.write_text(content)
            loaded = load_trajectory(path, include_trials=True)
            self.assertEqual(len(loaded.frames), 1)
            self.assertEqual(loaded.frames[0].energy, -.5)
            self.assertEqual(loaded.frames[0].source_line, 8)

    def test_alignment_preserves_geometry_and_rejects_reflection(self):
        first = Frame(["C", "N", "O", "F"], [[0., 0., 0.], [1., 0., 0.], [0., 2., 0.], [0., 0., 3.]], 0)
        rotation = np.array([[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]])
        moved = Frame(first.symbols[:], (np.array(first.coords) @ rotation + [4., 8., -2.]).tolist(), 1)
        aligned = align_frames([first, moved])
        np.testing.assert_allclose(aligned[1].coords, first.coords, atol=1e-12)
        self.assertNotEqual(moved.coords, aligned[1].coords)
        mirror = Frame(first.symbols[:], (np.array(first.coords) * [-1, 1, 1]).tolist(), 2)
        aligned_mirror = align_frames([first, mirror])[1]
        self.assertGreater(np.linalg.norm(np.array(aligned_mirror.coords) - first.coords), .5)
        invalid = Frame(["N", "C", "O", "F"], first.coords, 3)
        with self.assertRaisesRegex(ValueError, "order"):
            align_frames([first, invalid])

    def test_project_schema_and_version_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            trajectory = Trajectory([self.sample()], "source.xyz", method="B3LYP/def2SVP")
            save_project(trajectory, path, {"selected_frame": 0, "camera": [1., 2., 3.]})
            loaded, settings = load_project(path)
            self.assertEqual(trajectory, loaded)
            self.assertEqual(settings["selected_frame"], 0)
            record = json.loads(path.read_text())
            record["version"] = 99
            path.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "version"):
                load_project(path)
            record["version"] = 1
            record["trajectory"]["frames"][0]["coords"][0][0] = "invalid"
            path.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "finite"):
                load_project(path)



if __name__ == "__main__":
    unittest.main()
