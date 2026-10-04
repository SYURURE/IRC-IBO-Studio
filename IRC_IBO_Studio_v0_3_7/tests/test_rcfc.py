"""RCFC point-zero regression cases; optional user-log checks are opt-in.

STUDIO_TEST_IRC_DIR points at a folder containing sigmatropic_IRC.log and
sigmatropic_IRC_reverse.log. The private research logs are not redistributed.
"""
import os
from pathlib import Path
import re
import tempfile
import unittest

import numpy as np

from irc_ibo_studio.core import load_trajectory, load_project, save_project, save_xyz
from irc_ibo_studio.merge import merge_irc_logs


CHECKPOINT = ''' #p rb3lyp/6-31g(d) irc=(forward,rcfc,maxpoints=1)
 -----
 Structure from the checkpoint file: "test.chk"
 Charge = 0 Multiplicity = 1
 Redundant internal coordinates found in file.  (old form).
 C,0,1.2345678901,-2.3456789012,3.4567890123
 H,0,0.0,0.0,0.0
 Recover connectivity data from disk.
 NAtoms= 2
 Energy From Chk = -3.80000001D+01
 Point Number: 0 Path Number: 1
'''


def orientation(x=2.0, energy=-38.1):
    return f''' Input orientation:
 -----
 Center Atomic Atomic Coordinates (Angstroms)
 Number Number Type X Y Z
 -----
 1 6 0 {x} 0.0 0.0
 2 1 0 0.0 0.0 0.0
 -----
 SCF Done: E(RB3LYP) = {energy} A.U.
'''


class RCFCParserTests(unittest.TestCase):
    def load_text(self, text, include_trials=False):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rcfc.log'
            path.write_text(text, encoding='utf-8')
            return load_trajectory(path, include_trials=include_trials)

    def test_checkpoint_ts_preserves_energy_coordinates_and_source(self):
        t = self.load_text(CHECKPOINT + orientation() + ' Point Number: 1 Path Number: 1\n')
        self.assertEqual(len(t.frames), 2)
        ts = t.frames[0]
        self.assertEqual(ts.point, 0)
        self.assertEqual(ts.energy, -38.0000001)
        self.assertEqual(ts.coords[0], [1.2345678901, -2.3456789012, 3.4567890123])
        self.assertEqual(ts.source_line, 6)
        self.assertEqual(ts.reaction_coordinate, 0.0)
        self.assertEqual(ts.status, 'accepted')
        self.assertTrue(ts.source_file.endswith('rcfc.log'))
        self.assertEqual(t.frames[1].energy, -38.1)
        self.assertNotEqual(t.frames[1].coords, ts.coords)
        warning = next(w for w in t.warnings if '点0（TS）' in w)
        self.assertIn('Energy From Chk行10', warning)
        self.assertIn('印字精度', warning)

    def test_never_borrows_future_geometry_for_ts(self):
        text = ' Energy From Chk = -38.0\n Point Number: 0 Path Number: 1\n' + orientation()
        with self.assertRaisesRegex(ValueError, 'preceding checkpoint Cartesian'):
            self.load_text(text)

    def test_no_checkpoint_structure_marker_no_fallback(self):
        text = CHECKPOINT.replace(' Structure from the checkpoint file: "test.chk"\n', '')
        with self.assertRaisesRegex(ValueError, 'preceding checkpoint Cartesian'):
            self.load_text(text)

    def test_checkpoint_energy_must_precede_completion(self):
        text = CHECKPOINT.replace(' Energy From Chk = -3.80000001D+01\n', '')
        text += ' Energy From Chk = -3.80000001D+01\n'
        with self.assertRaisesRegex(ValueError, 'no electronic energy'):
            self.load_text(text)

    def test_nonzero_point_cannot_use_checkpoint_fallback(self):
        with self.assertRaisesRegex(ValueError, 'no electronic energy'):
            self.load_text(CHECKPOINT.replace('Point Number: 0', 'Point Number: 1'))

    def test_checkpoint_energy_not_recycled_for_next_point(self):
        with self.assertRaisesRegex(ValueError, 'Multiple IRC point markers'):
            self.load_text(CHECKPOINT + ' Point Number: 1 Path Number: 1\n')

    def test_repeated_ts_summary_not_duplicated(self):
        t = self.load_text(CHECKPOINT + ' Point Number: 0 Path Number: 1\n')
        self.assertEqual(len(t.frames), 1)

    def test_incomplete_or_unsupported_checkpoint_geometry_rejected(self):
        for old, new, reason in [
            ('NAtoms= 2', 'NAtoms= 3', 'atom count'),
            ('H,0,0.0,0.0,0.0', 'H,0,0.0,0.0', 'coordinate'),
            ('H,0,0.0,0.0,0.0', 'Bq,0,0.0,0.0,0.0', 'atom'),
            ('H,0,0.0,0.0,0.0', 'H,0,1E999,0.0,0.0', 'Non-finite'),
        ]:
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                self.load_text(CHECKPOINT.replace(old, new))

    def test_checkpoint_block_must_finish(self):
        with self.assertRaisesRegex(ValueError, 'Incomplete checkpoint'):
            self.load_text(CHECKPOINT.split(' Recover connectivity')[0])

    def test_scf_takes_precedence_if_initial_scf_was_evaluated(self):
        text = CHECKPOINT.replace(' Point Number: 0 Path Number: 1\n', '')
        text += orientation(4.0, -38.01) + ' Point Number: 0 Path Number: 1\n'
        t = self.load_text(text)
        self.assertEqual(t.frames[0].coords[0], [4.0, 0.0, 0.0])
        self.assertEqual(t.frames[0].energy, -38.01)
        self.assertFalse(any('点0（TS）' in w for w in t.warnings))

    def test_trial_evaluations_remain_separate_from_accepted_points(self):
        text = CHECKPOINT + orientation(2.0, -38.1) + ' Pt 1 Step number 1\n'
        text += orientation(2.1, -38.2) + ' Pt 1 Step number 2\n'
        text += ' Point Number: 1 Path Number: 1\n NET REACTION COORDINATE UP TO THIS POINT = 0.25\n'
        accepted = self.load_text(text)
        all_frames = self.load_text(text, include_trials=True)
        self.assertEqual([f.status for f in all_frames.frames], ['accepted', 'trial', 'accepted'])
        self.assertEqual([f.point for f in all_frames.frames], [0, 1, 1])
        self.assertEqual(len(accepted.frames), 2)
        self.assertEqual(accepted.frames[-1].coords[0], [2.1, 0.0, 0.0])
        self.assertEqual(accepted.frames[-1].energy, -38.2)
        self.assertEqual(accepted.frames[-1].reaction_coordinate, 0.25)

    def test_hpc_and_maxpoints_cautions_even_after_normal_termination(self):
        t = self.load_text(CHECKPOINT + ' Integration scheme = HPC\n Maximum number of steps reached.\n Normal termination\n')
        self.assertTrue(any('Normal terminationでも極小構造' in w for w in t.warnings))
        self.assertTrue(any('HPC法' in w and '補正後座標' in w for w in t.warnings))

    def test_project_and_xyz_roundtrip_checkpoint_frame(self):
        t = self.load_text(CHECKPOINT)
        with tempfile.TemporaryDirectory() as directory:
            xyz, project = Path(directory) / 'ts.xyz', Path(directory) / 'ts.ircibo'
            save_xyz(t.frames, xyz)
            self.assertEqual(load_trajectory(xyz).frames, t.frames)
            save_project(t, project, {})
            self.assertEqual(load_project(project)[0], t)


FIXTURE_DIR = Path(os.environ.get('STUDIO_TEST_IRC_DIR', '__missing_private_irc_fixtures__'))
FORWARD = FIXTURE_DIR / 'sigmatropic_IRC.log'
REVERSE = FIXTURE_DIR / 'sigmatropic_IRC_reverse.log'


@unittest.skipUnless(FORWARD.is_file() and REVERSE.is_file(), 'set STUDIO_TEST_IRC_DIR for private RCFC integration tests')
class RealRCFCIntegrationTests(unittest.TestCase):
    def test_branches_all_coordinates_match_printed_source(self):
        for path, end_energy, end_rc, total_evaluations in [
            (FORWARD, -1130.54316706, 4.79792, 81),
            (REVERSE, -1130.54420143, 4.79884, 82),
        ]:
            with self.subTest(log=path.name):
                t = load_trajectory(path)
                text_lines = path.read_text().splitlines()
                self.assertEqual(len(t.frames), 81)
                self.assertEqual([f.point for f in t.frames], list(range(81)))
                self.assertEqual(len(load_trajectory(path, include_trials=True).frames), total_evaluations)
                self.assertEqual(t.frames[0].source_line, 123)
                self.assertEqual(t.frames[0].energy, -1130.5205095)
                self.assertEqual(t.frames[-1].energy, end_energy)
                self.assertEqual(t.frames[-1].reaction_coordinate, end_rc)
                self.assertTrue(all(len(f.symbols) == 72 for f in t.frames))
                self.assertTrue(all(f.source_file == str(path.resolve()) for f in t.frames))
                # Independent source-coordinate check, not merely parser counts.
                ts_rows = text_lines[122:194]
                np.testing.assert_array_equal(t.frames[0].coords,
                    [[float(x) for x in row.split(',')[2:]] for row in ts_rows])
                for frame in t.frames[1:]:
                    index = frame.source_line - 1
                    rows = text_lines[index + 5:index + 5 + 72]
                    np.testing.assert_array_equal(frame.coords,
                        [[float(x) for x in row.split()[3:6]] for row in rows])
                    scf_line = next(line for line in text_lines[index:] if 'SCF Done:' in line)
                    self.assertEqual(frame.energy, float(re.search(r'=\s*([-0-9.]+)', scf_line)[1]))
                self.assertEqual(t.frames[1].source_line, 321)
                self.assertNotEqual(t.frames[0].coords, t.frames[1].coords)

    def test_shared_ts_and_merge_161_keep_original_coordinate_data(self):
        f, r = load_trajectory(FORWARD), load_trajectory(REVERSE)
        self.assertEqual(f.frames[0].coords, r.frames[0].coords)
        self.assertEqual(f.frames[0].energy, r.frames[0].energy)
        for start_side in ('forward', 'reverse'):
            with self.subTest(start_side=start_side):
                t = merge_irc_logs(FORWARD, REVERSE, start_side=start_side)
                self.assertEqual(len(t.frames), 161)
                self.assertEqual(t.merge_info['ts_index'], 80)
                self.assertEqual(sum(x.branch == 'ts' for x in t.frames), 1)
                self.assertEqual(t.frames[80].energy, -1130.5205095)
                self.assertEqual(t.frames[80].coords, f.frames[0].coords)
                branches = {'forward': f.frames, 'reverse': r.frames}
                expected = list(reversed(branches[start_side])) + branches['reverse' if start_side == 'forward' else 'forward'][1:]
                for original, merged in zip(expected, t.frames):
                    self.assertEqual(original.coords, merged.coords)
                    self.assertEqual(original.energy, merged.energy)
                    self.assertEqual(original.source_line, merged.source_line)
                    self.assertEqual(original.source_file, merged.source_file)
                self.assertLess(t.merge_info['first_step_cosine'], -0.99)


if __name__ == '__main__':
    unittest.main()
