"""Small CPU ball-and-stick renderer, styled like GRRM Input Builder.

The covalent radii, palette and camera convention follow the user's existing
GRRM viewer. Bonds are distance-based drawing guides, never bond-order data.
Depth-sorted spheres/half-bonds approximate overlap; this is not a ray tracer.
No electronic data, coordinates, or frame order are changed by this module.
"""
from __future__ import annotations

from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from itertools import product
import colorsys
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .core import ELEMENTS

COVALENT_RADII = {
    'H': .31, 'He': .28, 'Li': 1.28, 'Be': .96, 'B': .84, 'C': .76,
    'N': .71, 'O': .66, 'F': .57, 'Ne': .58, 'Na': 1.66, 'Mg': 1.41,
    'Al': 1.21, 'Si': 1.11, 'P': 1.07, 'S': 1.05, 'Cl': 1.02,
    'Ar': 1.06, 'K': 2.03, 'Ca': 1.76, 'Sc': 1.70, 'Ti': 1.60,
    'V': 1.53, 'Cr': 1.39, 'Mn': 1.39, 'Fe': 1.32, 'Co': 1.26,
    'Ni': 1.24, 'Cu': 1.32, 'Zn': 1.22, 'Ga': 1.22, 'Ge': 1.20,
    'As': 1.19, 'Se': 1.20, 'Br': 1.20, 'Kr': 1.16, 'Rb': 2.20,
    'Sr': 1.95, 'Y': 1.90, 'Zr': 1.75, 'Nb': 1.64, 'Mo': 1.54,
    'Tc': 1.47, 'Ru': 1.46, 'Rh': 1.42, 'Pd': 1.39, 'Ag': 1.45,
    'Cd': 1.44, 'In': 1.42, 'Sn': 1.39, 'Sb': 1.39, 'Te': 1.38,
    'I': 1.39, 'Xe': 1.40, 'Cs': 2.44, 'Ba': 2.15, 'La': 2.07,
    'Ce': 2.04, 'Hf': 1.75, 'Ta': 1.70, 'W': 1.62, 'Re': 1.51,
    'Os': 1.44, 'Ir': 1.41, 'Pt': 1.36, 'Au': 1.36, 'Hg': 1.32,
    'Tl': 1.45, 'Pb': 1.46, 'Bi': 1.48, 'U': 1.96,
}
COLORS = {
    'H': '#e7e9ed', 'C': '#424a57', 'N': '#3264dd', 'O': '#e74747',
    'F': '#68ca73', 'Cl': '#38b85b', 'Br': '#a24b36', 'I': '#884ac0',
    'P': '#ed952f', 'S': '#e3bd24', 'Si': '#c29b78', 'B': '#d59895',
    'Pd': '#448eae', 'Pt': '#9ab0ba', 'Au': '#d4a326', 'Fe': '#c66b35',
    'Cu': '#b7764c', 'Ag': '#b7c2cd', 'Zn': '#8c91b5',
}
MAX_SIDE = 4096
MAX_PIXELS = 8_388_608
MAX_CACHE_ITEMS = 64
MAX_CACHE_BYTES = 8 * 1024 * 1024
MAX_SPRITE_SIDE = 256
_ELEMENTS = set(ELEMENTS[1:])


@dataclass(frozen=True)
class Projection:
    """Atom projection in final image pixels, increasing z toward the viewer."""
    points: np.ndarray
    rotated: np.ndarray
    radii: np.ndarray
    scale: float


def _geometry(symbols, coords):
    symbols = tuple(symbols)
    if any(not isinstance(s, str) or s not in _ELEMENTS for s in symbols):
        raise ValueError('原子の元素記号が不正です。ダミー原子は表示できません。')
    try:
        xyz = np.array(coords, dtype=float, copy=True)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('各原子には有限の x y z 座標が必要です。') from exc
    if not symbols and xyz.size == 0:
        xyz = np.empty((0, 3), dtype=float)
    if xyz.shape != (len(symbols), 3) or not np.isfinite(xyz).all():
        raise ValueError('元素と有限の x y z 座標の数が一致しません。')
    return symbols, xyz


def _centered(xyz):
    if not len(xyz):
        return xyz.copy()
    try:
        with np.errstate(over='raise', invalid='raise'):
            # Divide before summing to avoid overflowing ordinary large offsets.
            centered = xyz - (xyz / len(xyz)).sum(axis=0)
    except FloatingPointError as exc:
        raise ValueError('座標の数値範囲が大きすぎて表示できません。') from exc
    if not np.isfinite(centered).all():
        raise ValueError('座標の数値範囲が大きすぎて表示できません。')
    return centered


def _extent(xyz):
    return max(1.0, max((math.hypot(*row) for row in xyz), default=0.0) + .6)


def trajectory_extent(frames):
    """Constant playback size from each frame's centered geometry, without copies retained."""
    result = 1.0
    for frame in frames:
        _, xyz = _geometry(frame.symbols, frame.coords)
        result = max(result, _extent(_centered(xyz)))
    if not math.isfinite(result):
        raise ValueError('構造の表示範囲が大きすぎます。')
    return result


def infer_bonds(symbols, positions):
    """Zero-based display pairs, using the GRRM reference covalent-radius cutoff."""
    symbols, xyz = _geometry(symbols, positions)
    if not symbols:
        return []
    radii = [COVALENT_RADII.get(symbol, 1.4) for symbol in symbols]
    width = 2.0 * max(radii) * 1.2
    cells, pairs = defaultdict(list), []
    for i, row in enumerate(xyz):
        cell = tuple(math.floor(float(x) / width) for x in row)
        for offset in product((-1, 0, 1), repeat=3):
            key = tuple(cell[axis] + offset[axis] for axis in range(3))
            for j in cells.get(key, ()):
                distance = math.dist(row, xyz[j])
                if .1 < distance <= 1.2 * (radii[i] + radii[j]):
                    pairs.append((j, i))
        cells[cell].append(i)
    return pairs


def hit_test(projection, x, y):
    """Frontmost painted atom, with a 5-pixel touch target for tiny atoms."""
    if projection is None or not math.isfinite(x) or not math.isfinite(y):
        return None
    hits, near = [], []
    for i, (point, radius) in enumerate(zip(projection.points, projection.radii)):
        distance = math.hypot(x - point[0], y - point[1])
        if distance <= radius:
            hits.append((projection.rotated[i, 2], i))
        elif distance <= max(5., radius):
            near.append((distance - radius, -projection.rotated[i, 2], -i))
    if hits:
        return max(hits)[1]
    return -min(near)[2] if near else None


@lru_cache(maxsize=8)
def load_font(size):
    """Portable ASCII model labels, with a platform font fallback."""
    size = max(6, min(96, int(size)))
    for name in ('DejaVuSans.ttf', 'arial.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Older supported Pillow versions.
        return ImageFont.load_default()


def _rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def _lighter(color, factor=1.55):
    """QColor.lighter's HSV overflow desaturation, without a Qt dependency."""
    hue, saturation, value = colorsys.rgb_to_hsv(*(c / 255. for c in color))
    value *= factor
    if value > 1.:
        saturation = max(0., saturation - (value - 1.))
        value = 1.
    return np.asarray(colorsys.hsv_to_rgb(hue, saturation, value), dtype=np.float32) * 255.


def _clip_line(a, b, box):
    """Liang-Barsky clipping before passing possibly far-offscreen points to PIL."""
    x0, y0 = map(np.longdouble, a)
    x1, y1 = map(np.longdouble, b)
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = np.longdouble(0), np.longdouble(1)
    for p, q in ((-dx, x0 - box[0]), (dx, box[2] - x0),
                 (-dy, y0 - box[1]), (dy, box[3] - y0)):
        if p == 0:
            if q < 0:
                return None
        elif p < 0:
            t0 = max(t0, q / p)
        else:
            t1 = min(t1, q / p)
        if t0 > t1:
            return None
    return (float(x0 + t0 * dx), float(y0 + t0 * dy)), (float(x0 + t1 * dx), float(y0 + t1 * dy))


def _round_line(draw, a, b, color, width, size):
    width = max(1, min(int(round(width)), 2 * max(size)))
    r = width / 2
    segment = _clip_line(a, b, (-r, -r, size[0] + r, size[1] + r))
    if segment is None:
        return
    a, b = segment
    draw.line((a, b), fill=color, width=width)
    for x, y in (a, b):
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color)


def _sphere_tile(x0, y0, width, height, cx, cy, radius, color, aa):
    """A clipped, bounded-workspace radial-shaded sphere tile."""
    x = (np.arange(x0, x0 + width, dtype=np.float32) + .5 - cx) / radius
    y = (np.arange(y0, y0 + height, dtype=np.float32) + .5 - cy) / radius
    distance = np.hypot(y[:, None], x[None, :])
    light = np.clip(np.hypot(y[:, None] + .3, x[None, :] + .3) / 1.6, 0., 1.)
    base = np.asarray(color, dtype=np.float32)
    high, low = _lighter(color), base / 1.25
    pixels = high + (low - high) * light[:, :, None]
    outline = distance >= max(0., 1. - .8 * aa / radius)
    pixels[outline] = base / 1.4
    result = np.empty((height, width, 4), dtype=np.uint8)
    result[:, :, :3] = np.clip(pixels, 0, 255).astype(np.uint8)
    result[:, :, 3] = (distance <= 1.) * 255
    return Image.fromarray(result, 'RGBA')


class SceneRenderer:
    """Reusable renderer; only small atom glyphs are cached, never whole frames."""
    def __init__(self):
        self._sprite_cache = OrderedDict()
        self._cache_bytes = 0

    @property
    def cache_info(self):
        return {'items': len(self._sprite_cache), 'bytes': self._cache_bytes}

    def clear_cache(self):
        self._sprite_cache.clear()
        self._cache_bytes = 0

    def _paint_atom(self, image, center, radius, color, aa):
        cx, cy = map(float, center)
        width, height = image.size
        if radius < .05:
            return  # Sub-pixel coverage is negligible; avoid floating underflow.
        if cx + radius < 0 or cy + radius < 0 or cx - radius > width or cy - radius > height:
            return
        if radius <= (MAX_SPRITE_SIDE - 3) / 2:
            side = math.ceil(2 * radius) + 3
            key = (color, round(radius, 2), aa)
            if key in self._sprite_cache:
                sprite = self._sprite_cache.pop(key)
                self._sprite_cache[key] = sprite
            else:
                center_px = side / 2
                sprite = _sphere_tile(0, 0, side, side, center_px, center_px, radius, color, aa)
                self._sprite_cache[key] = sprite
                self._cache_bytes += side * side * 4
                while len(self._sprite_cache) > MAX_CACHE_ITEMS or self._cache_bytes > MAX_CACHE_BYTES:
                    _, old = self._sprite_cache.popitem(last=False)
                    self._cache_bytes -= old.width * old.height * 4
            image.paste(sprite, (round(cx - sprite.width / 2), round(cy - sprite.height / 2)), sprite)
            return
        # Huge zoom must not allocate a radius-sized sprite. Paint only visible
        # tiles (max 256x128) and keep temporary arrays independent of zoom.
        left, top = max(0, math.floor(cx - radius)), max(0, math.floor(cy - radius))
        right, bottom = min(width, math.ceil(cx + radius)), min(height, math.ceil(cy + radius))
        for y in range(top, bottom, 128):
            for x in range(left, right, 256):
                tile = _sphere_tile(x, y, min(256, right - x), min(128, bottom - y), cx, cy, radius, color, aa)
                image.paste(tile, (x, y), tile)

    def render(self, symbols, coords, size, *, yaw=-.45, pitch=.65, zoom=1.0,
               pan=(0., 0.), labels=False, bonds=True, picked=(), extent=None):
        symbols, xyz = _geometry(symbols, coords)
        if len(size) != 2 or any(type(v) is not int or v < 1 or v > MAX_SIDE for v in size):
            raise ValueError(f'表示サイズは1〜{MAX_SIDE}ピクセルの整数で指定してください。')
        width, height = size
        if width * height > MAX_PIXELS:
            raise ValueError('表示サイズの総ピクセル数が上限を超えています。')
        try:
            yaw, pitch, zoom = float(yaw), float(pitch), float(zoom)
            pan = tuple(float(v) for v in pan)
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError('視点は有限の数値で指定してください。') from exc
        if len(pan) != 2 or not all(math.isfinite(v) for v in (yaw, pitch, zoom, *pan)) or zoom <= 0:
            raise ValueError('視点は有限の数値、拡大率は正の数で指定してください。')
        picked = tuple(picked)
        if (len(picked) > 2 or any(type(v) is not int or not 0 <= v < len(symbols) for v in picked)
                or len(set(picked)) != len(picked)):
            raise ValueError('距離測定では異なる原子を最大2つ指定してください。')
        centered = _centered(xyz)
        extent = _extent(centered) if extent is None else float(extent)
        if not math.isfinite(extent) or extent <= 0:
            raise ValueError('表示範囲は有限の正の数で指定してください。')
        cy, sy, cx, sx = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
        rotation = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]]) @ np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
        scale = min(width, height) * .43 / extent * zoom
        with np.errstate(over='ignore', invalid='ignore'):
            rotated = centered @ rotation.T
            points = rotated[:, :2] * np.array([scale, -scale]) + [width / 2 + pan[0], height / 2 + pan[1]]
            radii = np.array([max(.18, min(.5, COVALENT_RADII.get(s, 1.4) * .38)) * scale for s in symbols])
        if (not math.isfinite(scale) or not np.isfinite(points).all() or not np.isfinite(radii).all()
                or not np.isfinite(rotated).all() or scale > 1e12 or np.any(np.abs(points) > 1e12)):
            raise ValueError('視点または座標の数値範囲が大きすぎて表示できません。')
        projection = Projection(points, rotated, radii, scale)
        # AA at 2x for ordinary views; bound total render area on large screens.
        aa = 2 if width * height * 4 <= MAX_PIXELS else 1
        canvas_size = width * aa, height * aa
        image = Image.new('RGB', canvas_size, 'white')
        draw = ImageDraw.Draw(image)
        pts, rs = points * aa, radii * aa
        items = []
        if bonds:
            for i, j in infer_bonds(symbols, xyz):
                middle = (pts[i] + pts[j]) * .5
                zmid = rotated[i, 2] * .5 + rotated[j, 2] * .5
                for atom in (i, j):
                    items.append((rotated[atom, 2] * .5 + zmid * .5, 'bond', atom, middle))
        items.extend((rotated[i, 2], 'atom', i, None) for i in range(len(symbols)))
        for _, kind, i, middle in sorted(items, key=lambda item: item[0]):
            color = _rgb(COLORS.get(symbols[i], '#a58cc3'))
            if kind == 'bond':
                vector = middle - pts[i]
                length = math.hypot(*vector)
                # A half-bond directed toward the viewer can sort after its
                # own atom. Starting at the projected sphere surface avoids
                # painting a flat stripe across that sphere's highlight.
                if length > rs[i]:
                    start = pts[i] + vector * (rs[i] / length)
                    _round_line(draw, start, middle, color, max(1., .10 * scale) * aa, canvas_size)
            elif rs[i] > 0:
                self._paint_atom(image, pts[i], rs[i], color, aa)
        font = load_font(12 * aa)
        if labels and len(symbols) <= 2000:
            for i, point in enumerate(pts):
                if i not in picked and -50 <= point[0] <= canvas_size[0] and 0 <= point[1] <= canvas_size[1] + 30:
                    draw.text((point[0] + 7 * aa, point[1] - 19 * aa), f'{i + 1} {symbols[i]}', font=font, fill='#172b40')
        if len(picked) == 2:
            segment = _clip_line(pts[picked[0]], pts[picked[1]], (0, 0, *canvas_size))
            if segment:
                a, b = map(np.asarray, segment)
                _round_line(draw, a, b, 'white', 4 * aa, canvas_size)
                length = float(np.linalg.norm(b - a))
                if length > 0:
                    step = (b - a) / length
                    for offset in np.arange(0, length, 10 * aa):
                        draw.line((tuple(a + step * offset), tuple(a + step * min(offset + 6 * aa, length))), fill='#005b96', width=2 * aa)
        for slot, i in enumerate(picked):
            color = '#005b96' if slot == 0 else '#a33b27'
            background = '#b4e6fa' if slot == 0 else '#ffd5cc'
            x, y = pts[i]
            r = rs[i] + 4 * aa
            # Avoid huge ellipse coordinates in Pillow. Visible huge spheres
            # need no ring until their actual boundary enters the viewport.
            if r < 4 * max(canvas_size) and abs(x) < 5 * max(canvas_size) and abs(y) < 5 * max(canvas_size):
                box = x - r, y - r, x + r, y + r
                draw.ellipse(box, outline='white', width=5 * aa)
                draw.ellipse(box, outline=color, width=max(1, round(2.5 * aa)))
            text = f'{i + 1} {symbols[i]}'
            bounds = draw.textbbox((0, 0), text, font=font)
            tw, th = bounds[2] - bounds[0] + 12 * aa, bounds[3] - bounds[1] + 6 * aa
            tx = min(max(3 * aa, x + r + 5 * aa), max(3 * aa, canvas_size[0] - tw - 3 * aa))
            ty = min(max(3 * aa, y - r - th - 5 * aa), max(3 * aa, canvas_size[1] - th - 3 * aa))
            draw.rounded_rectangle((tx, ty, tx + tw, ty + th), radius=3 * aa, fill=background, outline=color, width=aa)
            draw.text((tx + 6 * aa - bounds[0], ty + 3 * aa - bounds[1]), text, font=font, fill=color)
        if aa != 1:
            image = image.resize((width, height), Image.Resampling.LANCZOS)
        return image, projection
