"""Optional real RCFC path -> project/XYZ regression, with no Gaussian run."""
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np

from irc_ibo_studio.core import load_trajectory, load_project, save_project, save_xyz, align_frames
from irc_ibo_studio.merge import merge_irc_logs, selection_indices

DATA = Path(os.environ.get('STUDIO_TEST_IRC_DIR', '__missing_private_irc_fixtures__'))


@unittest.skipUnless(all((DATA/name).is_file() for name in ('sigmatropic_IRC.log', 'sigmatropic_IRC_reverse.log')),
                     'set STUDIO_TEST_IRC_DIR for private RCFC integration tests')
class RealPipelineTests(unittest.TestCase):
    def test_merged_path_export_roundtrip_and_atom_order(self):
        trajectory = merge_irc_logs(DATA/'sigmatropic_IRC.log', DATA/'sigmatropic_IRC_reverse.log')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_project(trajectory, root/'project.json', {'stride': '1', 'align': False})
            restored, settings = load_project(root/'project.json')
            self.assertEqual(restored, trajectory)
            self.assertEqual(settings['stride'], '1')
            save_xyz(restored.frames, root/'path.xyz')
            xyz = load_trajectory(root/'path.xyz')
            self.assertEqual(len(xyz.frames), 161)
            for source, output in zip(restored.frames, xyz.frames):
                np.testing.assert_allclose(output.coords, source.coords, atol=5e-11, rtol=0)
                self.assertEqual(output.energy, source.energy)
                self.assertEqual(output.source_line, source.source_line)
                self.assertEqual(output.branch, source.branch)
            indexes = selection_indices(trajectory.frames, 0, 160, 13)
            self.assertIn(0, indexes)
            self.assertIn(80, indexes)
            self.assertIn(160, indexes)
            originals = np.array([frame.coords for frame in trajectory.frames])
            aligned = align_frames(trajectory.frames)
            np.testing.assert_array_equal([frame.coords for frame in trajectory.frames], originals)
            for index in (0, 80, 160):
                source = originals[index]
                rotated = np.array(aligned[index].coords)
                np.testing.assert_allclose(np.linalg.norm(source[:,None]-source[None,:], axis=2),
                                           np.linalg.norm(rotated[:,None]-rotated[None,:], axis=2),
                                           rtol=0, atol=1e-10)


if __name__ == '__main__':
    unittest.main()
