"""Headless rendering, atom picking and bounded-memory regression coverage."""
import copy
import math
import unittest

import numpy as np

from irc_ibo_studio.core import Frame
from irc_ibo_studio.viewer import (
    SceneRenderer, COLORS, COVALENT_RADII, MAX_CACHE_ITEMS, MAX_CACHE_BYTES,
    hit_test, infer_bonds, trajectory_extent, load_font,
)


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.renderer = SceneRenderer()

    def test_white_background_rgb_correct_size_empty_geometry(self):
        image, p = self.renderer.render([], [], (313, 217))
        self.assertEqual(image.mode, 'RGB')
        self.assertEqual(image.size, (313, 217))
        self.assertEqual(image.getextrema(), ((255, 255),) * 3)
        self.assertEqual(p.points.shape, (0, 2))
        self.assertEqual(p.rotated.shape, (0, 3))
        self.assertEqual(p.radii.shape, (0,))
        self.assertIsNone(hit_test(p, 10, 10))
        self.assertEqual(trajectory_extent([]), 1.0)

    def test_reference_colors_radial_highlight_and_radii(self):
        self.assertEqual(COLORS['C'], '#424a57')
        self.assertEqual(COLORS['O'], '#e74747')
        self.assertEqual(COLORS['H'], '#e7e9ed')
        for symbol in ('H', 'C', 'N', 'O', 'Pd', 'He'):
            with self.subTest(symbol=symbol):
                image, p = self.renderer.render([symbol], [[0, 0, 0]], (300, 300))
                self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
                self.assertAlmostEqual(p.radii[0] / p.scale, max(.18, min(.5, COVALENT_RADII[symbol] * .38)))
                x, y = p.points[0]
                r = p.radii[0]
                light = image.getpixel((round(x - .3*r), round(y - .3*r)))
                dark = image.getpixel((round(x + .5*r), round(y + .5*r)))
                self.assertGreater(sum(light), sum(dark))
                if symbol == 'O':
                    red, green, blue = image.getpixel((150, 150))
                    self.assertGreater(red, 1.3 * green)
                    self.assertGreater(red, 1.3 * blue)
                    # Qt's lighter(155) desaturates bright red into pale pink,
                    # rather than merely clipping the red RGB component.
                    self.assertGreater(light[1], 170)
                    self.assertGreater(light[2], 170)

    def test_camera_rotation_and_screen_conventions_match_grrm(self):
        coords = np.array([[-1., 0., 0.], [1., 0., 0.]])
        _, p = self.renderer.render(['C', 'C'], coords, (400, 300), yaw=math.pi/2, pitch=0, bonds=False)
        np.testing.assert_allclose(p.rotated, [[0, 0, 1], [0, 0, -1]], atol=1e-15)
        np.testing.assert_allclose(p.points, [[200, 150], [200, 150]], atol=1e-12)
        self.assertEqual(hit_test(p, 200, 150), 0)
        _, p = self.renderer.render(['C', 'C'], [[0, -1, 0], [0, 1, 0]], (400, 300), yaw=0, pitch=math.pi/2)
        np.testing.assert_allclose(p.rotated, [[0, 0, -1], [0, 0, 1]], atol=1e-15)
        self.assertEqual(hit_test(p, 200, 150), 1)

    def test_pan_zoom_and_extent_in_pixel_units(self):
        coords = [[-1., 0., 0.], [1., 0., 0.]]
        _, a = self.renderer.render(['C', 'C'], coords, (400, 300), yaw=0, pitch=0)
        _, b = self.renderer.render(['C', 'C'], coords, (400, 300), yaw=0, pitch=0, zoom=2, pan=(25, -10))
        self.assertEqual(b.scale, 2*a.scale)
        np.testing.assert_allclose(b.points, (a.points - [200, 150])*2 + [225, 140])
        _, c = self.renderer.render(['C', 'C'], coords, (400, 300), extent=3.2)
        self.assertAlmostEqual(c.scale, a.scale/2)

    def test_frontmost_atom_picking_follows_depth_painter(self):
        image, p = self.renderer.render(['O', 'C'], [[0, 0, -1], [0, 0, 1]], (200, 200), yaw=0, pitch=0, bonds=False)
        self.assertEqual(hit_test(p, 100, 100), 1)
        self.assertIsNone(hit_test(p, 0, 0))
        self.assertIsNone(hit_test(None, 0, 0))
        self.assertIsNone(hit_test(p, float('nan'), 10))
        color = image.getpixel((100, 100))
        self.assertLess(max(color) - min(color), 60)  # Frontmost carbon, not red oxygen.

    def test_small_atom_touch_target(self):
        _, p = self.renderer.render(['H'], [[0, 0, 0]], (100, 100), zoom=.01)
        self.assertEqual(hit_test(p, 54, 50), 0)
        self.assertIsNone(hit_test(p, 56, 50))

    def test_bond_cutoff_matches_display_reference(self):
        boundary = 1.2 * (.76 + .31)
        self.assertEqual(infer_bonds(['C', 'H'], [[0, 0, 0], [boundary, 0, 0]]), [(0, 1)])
        self.assertEqual(infer_bonds(['C', 'H'], [[0, 0, 0], [boundary + 1e-6, 0, 0]]), [])
        self.assertEqual(infer_bonds(['C', 'H'], [[0, 0, 0], [.1, 0, 0]]), [])
        self.assertEqual(infer_bonds(['C', 'H'], [[0, 0, 0], [0, 0, 0]]), [])
        self.assertEqual(infer_bonds([], []), [])

    def test_colored_half_bonds_and_toggle(self):
        coords = [[-.65, 0, 0], [.65, 0, 0]]
        image, p = self.renderer.render(['C', 'O'], coords, (400, 240), yaw=0, pitch=0)
        off, _ = self.renderer.render(['C', 'O'], coords, (400, 240), yaw=0, pitch=0, bonds=False)
        self.assertNotEqual(image.tobytes(), off.tobytes())
        self.assertEqual(off.getpixel((200, 120)), (255, 255, 255))
        left, right = image.getpixel((190, 120)), image.getpixel((210, 120))
        self.assertLess(left[0], 100)
        self.assertGreater(right[0], 2 * right[1])

    def test_raw_frames_not_mutated_and_playback_scale_stable(self):
        frames = [Frame(['C', 'O'], [[-1., 0., 0.], [1., 0., 0.]], 0, energy=-100.),
                  Frame(['C', 'O'], [[8., 3., 0.], [12., 3., 0.]], 1, energy=-101.)]
        before = copy.deepcopy(frames)
        extent = trajectory_extent(iter(frames))
        self.assertEqual(extent, 2.6)
        projections = []
        for f in frames:
            _, p = self.renderer.render(f.symbols, f.coords, (300, 200), labels=True, picked=(0, 1), extent=extent)
            projections.append(p)
        self.assertEqual(projections[0].scale, projections[1].scale)
        self.assertEqual(frames, before)
        original = np.asarray(frames[0].coords)
        expected = original.copy()
        self.renderer.render(frames[0].symbols, original, (200, 200), yaw=1, pitch=2, pan=(40, 10))
        np.testing.assert_array_equal(original, expected)

    def test_labels_and_selection_change_pixels_without_changing_projection(self):
        symbols, coords = ['C', 'O'], [[-1, 0, 0], [1, 0, 0]]
        plain, p = self.renderer.render(symbols, coords, (300, 200))
        labels, q = self.renderer.render(symbols, coords, (300, 200), labels=True)
        selected, r = self.renderer.render(symbols, coords, (300, 200), picked=(0, 1))
        self.assertNotEqual(plain.tobytes(), labels.tobytes())
        self.assertNotEqual(plain.tobytes(), selected.tobytes())
        np.testing.assert_array_equal(p.points, q.points)
        np.testing.assert_array_equal(p.points, r.points)
        self.assertIsNotNone(load_font(12))

    def test_sprite_cache_bounded_and_clearable(self):
        for index in range(100):
            self.renderer.render(['C', 'O', 'H'], [[-1, 0, 0], [0, 0, 0], [1, 0, 0]], (160 + index, 200), zoom=.6 + index*.003)
        self.assertLessEqual(self.renderer.cache_info['items'], MAX_CACHE_ITEMS)
        self.assertLessEqual(self.renderer.cache_info['bytes'], MAX_CACHE_BYTES)
        self.assertGreater(self.renderer.cache_info['items'], 0)
        self.renderer.clear_cache()
        self.assertEqual(self.renderer.cache_info, {'items': 0, 'bytes': 0})

    def test_extreme_zoom_offscreen_and_resize_bounded(self):
        image, p = self.renderer.render(['C'], [[0, 0, 0]], (180, 120), zoom=1000000)
        self.assertEqual(image.size, (180, 120))
        self.assertNotEqual(image.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(self.renderer.cache_info['items'], 0)
        image, _ = self.renderer.render(['C'], [[0, 0, 0]], (180, 120), pan=(1e9, 1e9))
        self.assertEqual(image.getextrema(), ((255, 255),) * 3)
        for size in ((1, 1), (63, 37), (10, 4096)):
            image, _ = self.renderer.render(['H'], [[0, 0, 0]], size)
            self.assertEqual(image.size, size)
        with np.errstate(all='raise'):
            tiny, _ = self.renderer.render(['H'], [[0, 0, 0]], (100, 100), zoom=1e-300)
            self.assertEqual(tiny.getextrema(), ((255, 255),) * 3)
        for size in ((0, 20), (4097, 5), (4096, 4096), (10.5, 30)):
            with self.subTest(size=size), self.assertRaises(ValueError):
                self.renderer.render([], [], size)

    def test_invalid_inputs_rejected(self):
        for symbols, coords in [(['C'], []), (['C'], [[0, 0, float('nan')]]),
                                (['C'], [[0, 0, float('inf')]]), (['X'], [[0, 0, 0]]),
                                (['C'], [[0, 0]])]:
            with self.subTest(coords=coords), self.assertRaises(ValueError):
                self.renderer.render(symbols, coords, (200, 200))
        for kwargs in ({'zoom': 0}, {'zoom': float('inf')}, {'zoom': 1e308},
                       {'pan': (float('nan'), 0)}, {'pan': (0,)}, {'extent': 0},
                       {'extent': float('nan')}, {'picked': (0, 0)}, {'picked': (1,)},
                       {'picked': (0, 1, 2)}, {'picked': ([],)}, {'yaw': float('inf')}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.renderer.render(['C'], [[0, 0, 0]], (200, 200), **kwargs)


if __name__ == '__main__':
    unittest.main()
