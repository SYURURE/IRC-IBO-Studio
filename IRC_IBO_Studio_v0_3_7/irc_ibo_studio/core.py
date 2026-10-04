"""IRC coordinates, provenance, alignment, and XYZ/project exchange.

Coordinates are Angstrom; energies are Hartree.  This module does not infer or
calculate orbitals. Electronic-state calculation and IBO localization are
performed by the external IboView application.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
import copy
import json
import math
import re

import numpy as np

ELEMENTS = ("X H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn "
            "Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd "
            "In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu "
            "Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu "
            "Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv "
            "Ts Og").split()
_SYMBOLS = set(ELEMENTS[1:])
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[DEde][-+]?\d+)?"
_ORIENT = re.compile(r"^\s*(?:Input|Standard|Z-Matrix) orientation:\s*$")
_SCF = re.compile(r"SCF Done:.*?=\s*(" + _NUMBER + r")")
_CHK_ENERGY = re.compile(r"^\s*Energy From Chk\s*=\s*(" + _NUMBER + r")\s*$", re.I)
_CHK_ATOM = re.compile(r"^\s*([A-Za-z]{1,2})\s*,\s*(-?\d+)\s*,\s*(" + _NUMBER +
                       r")\s*,\s*(" + _NUMBER + r")\s*,\s*(" + _NUMBER + r")\s*$")
_POINT = re.compile(r"Point Number:\s*(\d+)\s+Path Number:\s*(\d+)")
_STEP = re.compile(r"\bPt\s+(\d+)\s+Step number")
_REACTION = re.compile(r"NET REACTION COORDINATE UP TO THIS POINT\s*=\s*(" + _NUMBER + r")")


@dataclass
class Frame:
    symbols: list[str]
    coords: list[list[float]]
    point: int
    path: int = 1
    energy: float | None = None
    reaction_coordinate: float | None = None
    status: str = "accepted"
    source_line: int | None = None
    source_file: str = ""
    branch: str = ""
    original_reaction_coordinate: float | None = None


@dataclass
class Trajectory:
    frames: list[Frame]
    source: str
    method: str = ""
    charge: int = 0
    multiplicity: int = 1
    warnings: list[str] = field(default_factory=list)
    route: str = ""
    merge_info: dict = field(default_factory=dict)


def _float(value: str) -> float:
    result = float(value.replace("D", "E").replace("d", "e"))
    if not math.isfinite(result):
        raise ValueError("Non-finite coordinate or energy is not supported.")
    return result


def _integer(value, name: str, minimum: int | None = None):
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ValueError(f"{name} must be an integer" + (f" >= {minimum}." if minimum is not None else "."))


def _validate_frame(frame: Frame) -> None:
    if not isinstance(frame, Frame):
        raise ValueError("Expected a Frame record.")
    if not isinstance(frame.symbols, list) or not frame.symbols:
        raise ValueError("A frame must contain atoms.")
    if any(not isinstance(s, str) or s not in _SYMBOLS for s in frame.symbols):
        raise ValueError("Only recognized element symbols (H–Og) are supported; dummy/ghost atoms are unsupported.")
    if not isinstance(frame.coords, list) or len(frame.coords) != len(frame.symbols):
        raise ValueError("Coordinate and atom counts do not match.")
    for xyz in frame.coords:
        if not isinstance(xyz, list) or len(xyz) != 3:
            raise ValueError("Each atom needs exactly three coordinates.")
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in xyz):
            raise ValueError("Coordinates must be finite numbers.")
    _integer(frame.point, "point", 0)
    _integer(frame.path, "path", 1)
    for name in ("energy", "reaction_coordinate", "original_reaction_coordinate"):
        v = getattr(frame, name)
        if v is not None and (type(v) not in (int, float) or not math.isfinite(v)):
            raise ValueError(f"{name} must be a finite number or null.")
    if frame.status not in {"accepted", "trial", "imported"}:
        raise ValueError("Unknown frame status.")
    if not isinstance(frame.source_file, str) or frame.branch not in {"", "forward", "reverse", "ts"}:
        raise ValueError("Invalid frame provenance or IRC branch.")
    if frame.source_line is not None:
        _integer(frame.source_line, "source_line", 1)


def _validate_frames(frames: list[Frame]) -> None:
    if not isinstance(frames, list) or not frames:
        raise ValueError("No structures were found.")
    for f in frames:
        _validate_frame(f)
        if f.symbols != frames[0].symbols:
            raise ValueError("Atom count or element order changes along the trajectory. Atom mapping must be fixed.")


def _validate_trajectory(trajectory: Trajectory) -> None:
    _validate_frames(trajectory.frames)
    _integer(trajectory.charge, "charge")
    _integer(trajectory.multiplicity, "multiplicity", 1)
    if not isinstance(trajectory.source, str) or not isinstance(trajectory.method, str):
        raise ValueError("Source and method must be strings.")
    if not isinstance(trajectory.route, str) or not isinstance(trajectory.merge_info, dict):
        raise ValueError("Invalid route or merge metadata.")
    _safe_json(trajectory.merge_info)
    if not isinstance(trajectory.warnings, list) or any(not isinstance(w, str) for w in trajectory.warnings):
        raise ValueError("Warnings must be a list of strings.")


def _read_orientation(lines: list[str], start: int):
    """Return a table only after both header separators, rejecting malformed rows."""
    i, separators = start + 1, 0
    while i < len(lines) and i < start + 12:
        if re.fullmatch(r"\s*-{5,}\s*", lines[i]):
            separators += 1
            if separators == 2:
                i += 1
                break
        i += 1
    if separators != 2:
        raise ValueError(f"Malformed Gaussian orientation header at line {start + 1}.")
    symbols, coords = [], []
    while i < len(lines) and not re.fullmatch(r"\s*-{5,}\s*", lines[i]):
        words = lines[i].split()
        if len(words) != 6:
            raise ValueError(f"Malformed Gaussian coordinate at line {i + 1}.")
        center, z = int(words[0]), int(words[1])
        if center != len(symbols) + 1 or not 1 <= z < len(ELEMENTS):
            raise ValueError(f"Unsupported atom numbering or dummy/ghost atom at line {i + 1}.")
        symbols.append(ELEMENTS[z])
        coords.append([_float(x) for x in words[3:6]])
        i += 1
    if not symbols or i >= len(lines):
        raise ValueError(f"Incomplete Gaussian orientation at line {start + 1}.")
    return symbols, coords, i


def _read_checkpoint_geometry(lines: list[str], charge_line: int):
    """Read only the Cartesian echo immediately following checkpoint charge.

    Gaussian RCFC can accept point zero before printing any orientation/SCF.
    Do not search ahead to the first ordinary orientation: that may be point 1.
    Z-matrix, ghost atoms and annotated/QM-MM formats are deliberately not guessed.
    """
    i = charge_line + 1
    while i < len(lines) and (not lines[i].strip() or
                              lines[i].strip().startswith("Redundant internal coordinates found in file.")):
        i += 1
    first = i
    symbols, coords = [], []
    while i < len(lines):
        match = _CHK_ATOM.fullmatch(lines[i])
        if not match:
            # A comma-separated atom-like row is not a valid block terminator.
            if re.match(r"^\s*[A-Za-z][A-Za-z0-9()\-]*\s*,", lines[i]):
                raise ValueError(f"Unsupported checkpoint Cartesian coordinate at line {i + 1}.")
            break
        symbol = match[1][:1].upper() + match[1][1:].lower()
        if symbol not in _SYMBOLS:
            raise ValueError(f"Unsupported checkpoint atom at line {i + 1}.")
        symbols.append(symbol)
        coords.append([_float(match[j]) for j in (3, 4, 5)])
        i += 1
    if not symbols:
        return None
    if i == len(lines):
        raise ValueError(f"Incomplete checkpoint Cartesian block at line {first + 1}.")
    return symbols, coords, first + 1


def _load_gaussian(path: Path, include_trials: bool) -> Trajectory:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    evaluations: list[Frame] = []
    accepted: list[Frame] = []
    last_geometry = None
    last_point = None
    path_number, next_point = 1, 0
    charge, mult, method = 0, 1, ""
    in_route, route = False, []
    checkpoint_section = False
    checkpoint_geometry = None
    checkpoint_energy = None
    checkpoint_warnings = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("#") and not route:
            in_route = True
        if in_route:
            if re.fullmatch(r"\s*-{5,}\s*", line):
                in_route = False
            else:
                # Gaussian wraps the echoed route even inside a keyword. Remove
                # its one-column print margin, then concatenate without new spaces.
                route.append(line[1:] if line.startswith(" ") else line)
        if "Structure from the checkpoint file:" in line:
            checkpoint_section = True
            checkpoint_geometry = None
            checkpoint_energy = None
        m = re.search(r"Charge\s*=\s*(-?\d+)\s+Multiplicity\s*=\s*(\d+)", line)
        if m:
            charge, mult = int(m[1]), int(m[2])
            if checkpoint_section:
                checkpoint_geometry = _read_checkpoint_geometry(lines, i)
                checkpoint_section = False
        if checkpoint_geometry is not None and (m := re.search(r"\bNAtoms\s*=\s*(\d+)", line)):
            if int(m[1]) != len(checkpoint_geometry[0]):
                raise ValueError("Checkpoint Cartesian atom count disagrees with NAtoms; TS geometry is incomplete.")
        if _ORIENT.match(line):
            symbols, coords, end = _read_orientation(lines, i)
            last_geometry = (symbols, coords, i + 1)
            i = end
        elif m := _CHK_ENERGY.match(line):
            # This is a candidate for the initial point zero ONLY, not an SCF
            # evaluation to recycle for any later completion marker.
            if not evaluations and not accepted:
                checkpoint_energy = (_float(m[1]), i + 1, m[1])
        elif m := _SCF.search(line):
            if last_geometry is None:
                raise ValueError(f"SCF energy at line {i + 1} has no preceding printed geometry.")
            symbols, coords, source_line = last_geometry
            evaluations.append(Frame(symbols=list(symbols), coords=copy.deepcopy(coords),
                point=next_point, path=path_number, energy=_float(m[1]),
                status="trial", source_line=source_line, source_file=str(path.resolve())))
        elif m := _STEP.search(line):
            if evaluations:
                evaluations[-1].point = int(m[1])
        elif m := _POINT.search(line):
            point, path_number = int(m[1]), int(m[2])
            if not evaluations:
                if point != 0 or checkpoint_energy is None or checkpoint_geometry is None:
                    raise ValueError(f"IRC completion at line {i + 1} has no electronic energy evaluation. "
                                     "RCFC point 0 requires both a preceding checkpoint Cartesian block and Energy From Chk.")
                symbols, coords, source_line = checkpoint_geometry
                energy, energy_line, printed_energy = checkpoint_energy
                evaluations.append(Frame(symbols=list(symbols), coords=copy.deepcopy(coords),
                    point=0, path=path_number, energy=energy, status="trial",
                    source_line=source_line, source_file=str(path.resolve())))
                checkpoint_warnings.append(
                    f"点0（TS）はCHK由来の印字情報を使用：{path.name}の座標開始行{source_line}、"
                    f"Energy From Chk行{energy_line}（{printed_energy} Hartree）。"
                    "座標・エネルギーとも印字精度のまま保持し、点1の値で代用していません。")
                checkpoint_energy = None
            f = evaluations[-1]
            # A repeated summary marker must not duplicate an evaluation.
            if f.status == "accepted" and (f.point, f.path) != (point, path_number):
                raise ValueError("Multiple IRC point markers share one energy evaluation; this log layout is unsupported.")
            f.point, f.path, f.status = point, path_number, "accepted"
            if point == 0:
                f.reaction_coordinate = 0.0
            if not accepted or accepted[-1] is not f:
                accepted.append(f)
            last_point = f
            next_point = point + 1
        elif m := _REACTION.search(line):
            if last_point is not None:
                last_point.reaction_coordinate = _float(m[1])
        i += 1
    if not accepted:
        raise ValueError("No completed Gaussian IRC points were found. Load an IRC .log/.out or a multi-frame XYZ.")
    route_text = "".join(route).strip()
    m = re.search(r"\b([A-Za-z][A-Za-z0-9+\-]*)/([^\s]+)", route_text)
    if m:
        method = m[0]
    warnings = [
        "座標は各IRC点の完了記録に対応する最後のSCF計算済み印字座標です（Å・印字精度）。CHK由来の点0は別記します。",
        "エネルギーはSCF Doneの値（Hartree）、RCFC開始点はEnergy From Chkの印字値です。MP2などの相関エネルギーは抽出しません。",
        "反応座標はログの印字値を保持します。経路番号だけでは反応物側・生成物側を区別できません。",
    ]
    warnings.extend(checkpoint_warnings)
    if any(re.search(r"Integration scheme\s*=\s*HPC\b", line) for line in lines):
        warnings.append("HPC法では補間・補正後の厳密な終点座標が各点で印字されない場合があります。"
                        "本アプリは完了記録に対応する最後のSCF評価座標を使うため、CHK内の補正後座標とはわずかに異なり得ます。")
    if any("Maximum number of steps reached." in line for line in lines):
        warnings.insert(0, "IRCは指定した点数・ステップ上限に達しています。Normal terminationでも極小構造への到達を意味しません。")
    if any("Error termination" in line for line in lines):
        warnings.insert(0, "計算はエラー終了しています。確定点を抽出しましたが、終点が極小構造かは未確認です。")
    if len(evaluations) != len(accepted):
        warnings.append(f"補正途中の{len(evaluations) - len(accepted)}構造は" +
                        ("trialと区別して表示しています。" if include_trials else "確定点の経路から除外しました。"))
    trajectory = Trajectory(evaluations if include_trials else accepted, str(path), method, charge, mult, warnings, route=route_text)
    _validate_trajectory(trajectory)
    return trajectory


def _load_xyz(path: Path) -> Trajectory:
    lines = path.read_text(encoding="utf-8-sig", errors="strict").splitlines()
    frames: list[Frame] = []
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        start = i + 1
        try:
            natoms = int(lines[i].strip())
        except ValueError as exc:
            raise ValueError(f"Expected XYZ atom count at line {i + 1}.") from exc
        if natoms < 1 or i + natoms + 1 >= len(lines):
            raise ValueError(f"Incomplete XYZ frame at line {i + 1}.")
        comment = lines[i + 1]
        symbols, coords = [], []
        for j in range(i + 2, i + 2 + natoms):
            words = lines[j].split()
            if len(words) < 4:
                raise ValueError(f"Malformed XYZ atom at line {j + 1}.")
            raw = words[0]
            if raw.isdigit() and 1 <= int(raw) < len(ELEMENTS):
                raw = ELEMENTS[int(raw)]
            symbol = raw[:1].upper() + raw[1:].lower()
            symbols.append(symbol)
            coords.append([_float(s) for s in words[1:4]])
        data = dict(point=len(frames), path=1, energy=None, reaction_coordinate=None,
                    status="imported", source_line=start, source_file="", branch="", original_reaction_coordinate=None)
        if comment.startswith("IRCIBO "):
            metadata = json.loads(comment[7:])
            if not isinstance(metadata, dict) or set(metadata) - set(data):
                raise ValueError("Unsupported XYZ metadata fields.")
            data.update(metadata)
        else:
            if m := re.search(r"path_(\d+)_point_(\d+)", comment, re.I):
                data.update(path=int(m[1]), point=int(m[2]))
            if m := re.search(r"\b(?:energy|E)\s*=\s*(" + _NUMBER + r")", comment, re.I):
                data["energy"] = _float(m[1])
        frames.append(Frame(symbols=symbols, coords=coords, **data))
        i += natoms + 2
    trajectory = Trajectory(frames, str(path), warnings=[
        "XYZ座標の単位はÅとして読み込みます。メタデータがないXYZでは収束状態・波動関数は確認できません。"])
    if any(line.startswith("SYNTHETIC_DEMO ") for line in lines):
        trajectory.warnings.append("人工サンプル：操作確認用に数式で作成した構造列です。IRC・最適化・実験の結果ではありません。エネルギーや反応機構の解釈には使えません。")
    _validate_trajectory(trajectory)
    return trajectory


def load_trajectory(path, include_trials: bool = False) -> Trajectory:
    path = Path(path)
    if path.suffix.lower() == ".xyz":
        return _load_xyz(path)
    if path.suffix.lower() in {".log", ".out"}:
        return _load_gaussian(path, include_trials)
    raise ValueError("Supported trajectory formats: Gaussian IRC .log/.out and multi-frame .xyz.")


def save_xyz(frames: list[Frame], path) -> None:
    _validate_frames(frames)
    chunks = []
    for frame in frames:
        metadata = {k: v for k, v in asdict(frame).items() if k not in {"symbols", "coords"}}
        chunks.extend([str(len(frame.symbols)), "IRCIBO " + json.dumps(metadata, ensure_ascii=True, allow_nan=False)])
        chunks.extend(f"{s:2s} {xyz[0]: .10f} {xyz[1]: .10f} {xyz[2]: .10f}" for s, xyz in zip(frame.symbols, frame.coords))
    Path(path).write_text("\n".join(chunks) + "\n", encoding="utf-8")


def _safe_json(value, depth=0):
    if depth > 30:
        raise ValueError("Project settings are nested too deeply.")
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, list):
        for v in value:
            _safe_json(v, depth + 1)
        return
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        for v in value.values():
            _safe_json(v, depth + 1)
        return
    raise ValueError("Project settings must contain only finite JSON values.")


def save_project(trajectory: Trajectory, path, settings: dict) -> None:
    _validate_trajectory(trajectory)
    if not isinstance(settings, dict):
        raise ValueError("Project settings must be a dictionary.")
    _safe_json(settings)
    record = {"format": "IRC_IBO_Studio", "version": 2, "trajectory": asdict(trajectory), "settings": settings}
    Path(path).write_text(json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def load_project(path) -> tuple[Trajectory, dict]:
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(record, dict) or set(record) != {"format", "version", "trajectory", "settings"}:
        raise ValueError("Invalid IRC IBO Studio project schema.")
    if record["format"] != "IRC_IBO_Studio" or type(record["version"]) is not int or record["version"] not in (1, 2):
        raise ValueError("Unsupported project format/version; this version reads project versions 1 and 2.")
    data = record["trajectory"]
    required = {"frames", "source", "method", "charge", "multiplicity", "warnings"}
    allowed = required | {"route", "merge_info"}
    if not isinstance(data, dict) or not required <= set(data) <= allowed or not isinstance(data["frames"], list):
        raise ValueError("Invalid trajectory fields in project.")
    fields = set(Frame.__dataclass_fields__)
    required_frame = fields - {"source_file", "branch", "original_reaction_coordinate"}
    parsed = []
    for f in data["frames"]:
        if not isinstance(f, dict) or not required_frame <= set(f) <= fields:
            raise ValueError("Invalid frame fields in project.")
        parsed.append(Frame(**f))
    trajectory = Trajectory(**{**data, "frames": parsed})
    _validate_trajectory(trajectory)
    settings = record["settings"]
    if not isinstance(settings, dict):
        raise ValueError("Project settings must be a dictionary.")
    _safe_json(settings)
    return trajectory, settings


def align_frames(frames: list[Frame], heavy_only: bool = True) -> list[Frame]:
    """Align nuclei to the first frame; NEVER apply alone to precomputed orbitals.

    Equal atom ordering is required. The least-squares transform is a proper
    rotation (det +1), not a reflection. IBO/cube data would need this same
    transform; callers should align before wavefunction calculations.
    """
    _validate_frames(frames)
    reference = np.asarray(frames[0].coords, dtype=float)
    mask = np.array([s != "H" if heavy_only else True for s in frames[0].symbols])
    if not mask.any():
        mask[:] = True
    ref_center = reference[mask].mean(axis=0)
    target = reference[mask] - ref_center
    results = []
    for frame in frames:
        coords = np.asarray(frame.coords, dtype=float)
        center = coords[mask].mean(axis=0)
        moving = coords[mask] - center
        u, _, vt = np.linalg.svd(moving.T @ target)
        correction = np.eye(3)
        correction[2, 2] = 1.0 if np.linalg.det(u @ vt) >= 0 else -1.0
        rotation = u @ correction @ vt
        transformed = (coords - center) @ rotation + ref_center
        results.append(replace(frame, symbols=list(frame.symbols), coords=transformed.tolist()))
    return results
