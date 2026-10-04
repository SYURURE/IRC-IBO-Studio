"""Join two completed Gaussian IRC branches at their shared, verified point zero.

No interpolation, optimization, atom permutation, or coordinate rotation is
performed on stored structures. Proper rotations are used only for checks.
"""
from __future__ import annotations
from dataclasses import asdict, replace
from pathlib import Path
import copy
import re
import numpy as np
from .core import Trajectory, align_frames, load_trajectory, _validate_trajectory

TS_RMSD_TOL = 1e-4  # Angstrom, all atoms, fixed atom correspondence
TS_ENERGY_TOL = 1e-5  # Hartree; printed SCF or checkpoint point-zero energy


def _tokens(route):
    tokens, word, depth = [], [], 0
    for char in route.lower():
        if char.isspace() and depth == 0:
            if word: tokens.append(''.join(word)); word = []
        else:
            word.append(char)
            if char == '(': depth += 1
            elif char == ')': depth -= 1
            if depth < 0: raise ValueError('ルート行の括弧を解析できません。')
    if depth: raise ValueError('ルート行の括弧を解析できません。')
    if word: tokens.append(''.join(word))
    return tokens


def _route_info(route):
    tokens = _tokens(route)
    irc = [t for t in tokens if re.match(r'^irc(?:$|[=(])', t)]
    if len(irc) != 1:
        raise ValueError('IRCのルート行を一つだけ持つログを選んでください。')
    options = set(re.findall(r'[a-z]+', irc[0]))
    if options & {'restart', 'downhill'}:
        raise ValueError('Restart/Downhillログ単独の統合には対応しません。同じTSの点0から始まるログを選んでください。')
    if 'readisotopes' in options:
        raise ValueError('ReadIsotopesの質量定義を自動照合できないため、この統合では扱えません。')
    signature = []
    for token in tokens:
        if token.startswith('#'):
            token = re.sub(r'^#[pnt]?(?=\b|$)', '', token)
            if not token: continue
        if token in irc: continue
        # Reading coordinates/MOs and printing orbitals do not change the
        # explicitly stated Hamiltonian. Everything else is compared strictly.
        if re.match(r'^(geom|guess|pop)(?:$|[=(])', token) or token in {'gfinput', 'gfprint'}:
            continue
        signature.append(token)
    if 'cartesian' in options: signature.append('irc_coordinates=cartesian')
    else: signature.append('irc_coordinates=massweighted')
    return options, sorted(signature)


def _branch(path, expected):
    path = Path(path).resolve()
    if path.suffix.lower() not in {'.log', '.out'}:
        raise ValueError('統合にはGaussian IRCの.log / .outを選んでください。')
    text = path.read_text(encoding='utf-8', errors='replace')
    if len(re.findall(r'^[ \t]*#(?![ \t]+OF\b)', text, re.M | re.I)) != 1:
        raise ValueError('複数ジョブを含むログは統合できません。IRCジョブだけのログを選んでください。')
    trajectory = load_trajectory(path)
    frames = trajectory.frames
    if len({f.path for f in frames}) != 1 or any(f.branch for f in frames):
        raise ValueError('各ファイルには片方向のIRCだけを含めてください。')
    if len(frames) < 2 or frames[0].point != 0:
        raise ValueError('点0（TS）と少なくとも一つの完了点が必要です。再開部分だけのログは統合できません。')
    if any(b.point != a.point + 1 for a, b in zip(frames, frames[1:])):
        raise ValueError('IRC点番号の重複・欠落・逆転を検出しました。点0から続く単一経路が必要です。')
    if any(f.status != 'accepted' for f in frames):
        raise ValueError('確定したIRC点だけを統合できます。')
    if not trajectory.method or re.search(r'/(gen|genecp|chkbasis)\b', trajectory.method, re.I):
        raise ValueError('方法・基底をルート行から照合できません。明示した標準基底のIRCログを選んでください。')
    options, signature = _route_info(trajectory.route)
    directions = options & {'forward', 'reverse'}
    if directions and directions != {expected}:
        raise ValueError(f'{expected.title()}欄のログと、ルート行の方向指定が一致しません。ファイルを確認してください。')
    if not directions:
        trajectory.warnings.append('方向指定のないログです。完了点は一経路ですが、Forward/Reverseの割当は選択欄によります。')
    distances = [abs(f.reaction_coordinate) if f.reaction_coordinate is not None else None for f in frames]
    if all(x is not None for x in distances):
        if any(b <= a for a, b in zip(distances, distances[1:])):
            raise ValueError('TSからの反応座標が単調に増えていません。ログの継続・重複を確認してください。')
    return trajectory, signature


def merge_irc_logs(forward_path, reverse_path, start_side='reverse') -> Trajectory:
    """Return endpoint -> TS -> endpoint. start_side selects the negative side.

    The user assigns chemical reactant/product meaning after inspecting endpoints.
    Raw source path/point/line and original printed reaction coordinates survive.
    """
    if start_side not in {'forward', 'reverse'}:
        raise ValueError('開始側はforwardまたはreverseで指定してください。')
    if Path(forward_path).resolve() == Path(reverse_path).resolve():
        raise ValueError('同じファイルが選ばれています。ForwardとReverseを別々に選んでください。')
    forward, fs = _branch(forward_path, 'forward')
    reverse, rs = _branch(reverse_path, 'reverse')
    if forward.frames[0].symbols != reverse.frames[0].symbols:
        raise ValueError('原子数または元素順が一致しません。原子番号の対応を確認してください。')
    if (forward.charge, forward.multiplicity) != (reverse.charge, reverse.multiplicity):
        raise ValueError('電荷・スピン多重度が一致しません。')
    if forward.method.lower() != reverse.method.lower() or fs != rs:
        raise ValueError('計算条件が一致しません。方法・基底・溶媒などのルート行を確認してください。\nForward: '+forward.route+'\nReverse: '+reverse.route)
    tf, tr = forward.frames[0], reverse.frames[0]
    aligned = align_frames([tf, tr, forward.frames[1], reverse.frames[1]], heavy_only=False)
    delta = np.asarray(aligned[1].coords) - np.asarray(tf.coords)
    rmsd = float(np.sqrt(np.mean(np.sum(delta**2, axis=1))))
    maximum = float(np.max(np.linalg.norm(delta, axis=1)))
    if rmsd > TS_RMSD_TOL or maximum > 3*TS_RMSD_TOL:
        raise ValueError(f'開始構造が同じTSと確認できません（全原子RMSD={rmsd:.6g} Å）。同じTS・原子順で計算してください。')
    if tf.energy is None or tr.energy is None:
        raise ValueError('TSの電子エネルギー（SCFまたはCHK由来）が見つかりません。')
    de = abs(tf.energy-tr.energy)
    if de > TS_ENERGY_TOL:
        raise ValueError(f'TSの電子エネルギー差が大きいため統合しません（{de:.6g} Hartree）。計算条件を確認してください。')
    a = (np.asarray(aligned[2].coords)-np.asarray(aligned[0].coords)).ravel()
    b = (np.asarray(aligned[3].coords)-np.asarray(aligned[1].coords)).ravel()
    norm = np.linalg.norm(a)*np.linalg.norm(b)
    if norm < 1e-14:
        raise ValueError('最初の完了点がTSと区別できません。TS直後の座標を確認してください。')
    cosine = float(np.clip(np.dot(a,b)/norm,-1.,1.))
    if cosine > 0.5:
        raise ValueError('二つの経路がTSから同じ向きに進んでいる可能性があります。Forward/Reverseの計算設定を確認してください。')
    sides = {'forward': forward, 'reverse': reverse}
    end_side = 'forward' if start_side == 'reverse' else 'reverse'
    def copied(frame, branch, sign):
        rc = frame.reaction_coordinate
        return replace(frame, symbols=frame.symbols[:], coords=copy.deepcopy(frame.coords),
                       branch=branch, original_reaction_coordinate=rc,
                       reaction_coordinate=sign*abs(rc) if rc is not None else None)
    # Keep the TS from the starting branch; retain the other TS in merge metadata.
    kept_ts = sides[start_side].frames[0]
    frames = [copied(f,start_side,-1) for f in reversed(sides[start_side].frames[1:])]
    frames.append(copied(kept_ts,'ts',0))
    frames.extend(copied(f,end_side,1) for f in sides[end_side].frames[1:])
    ts_index = len(sides[start_side].frames)-1
    warnings = [f'統合順序：{start_side.title()}終点 → TS → {end_side.title()}終点。TSは位置{ts_index}に1構造だけ保持しました。',
                '反応物側・生成物側は自動判定しません。両端の構造を確認してください。',
                '原子番号を固定して照合しました。同元素の原子を自動で並べ替える処理は行いません。',
                '反応座標は開始側を負、終了側を正に統一。元の印字値・ファイル・行番号は保存しています。',
                f'TS照合：RMSD={rmsd:.6g} Å、電子エネルギー差={de:.6g} Hartree。']
    if cosine > -0.5:
        warnings.append('TS直後の変位が十分に逆向きではありません。経路の接続を構造表示で確認してください。')
    if any(f.reaction_coordinate is None for f in frames):
        warnings.append('反応座標の印字がない点があります。位置番号で並べ、距離は補間していません。')
    for name,t in sides.items():
        warnings.extend(f'{name.title()}: {w}' for w in t.warnings)
    info = {'forward_source': forward.source, 'reverse_source': reverse.source,
            'forward_route': forward.route, 'reverse_route': reverse.route,
            'start_side': start_side, 'ts_index': ts_index,
            'ts_rmsd_angstrom': rmsd, 'ts_energy_difference_hartree': de,
            'first_step_cosine': cosine, 'dropped_ts': asdict(sides[end_side].frames[0])}
    result = Trajectory(frames, f'{Path(forward.source).name} + {Path(reverse.source).name}',
                        forward.method, forward.charge, forward.multiplicity, warnings,
                        route=forward.route, merge_info=info)
    _validate_trajectory(result)
    return result


def selection_indices(frames, start, end, stride):
    """Downsample while retaining the selected endpoints and any verified TS."""
    if not 0 <= start <= end < len(frames) or stride < 1:
        raise ValueError('開始・終了は一覧の位置番号の範囲内、間隔は1以上にしてください。')
    selected = set(range(start,end+1,stride)) | {end}
    selected.update(i for i in range(start,end+1) if frames[i].branch == 'ts')
    return sorted(selected)
