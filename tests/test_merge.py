"""Synthetic Gaussian-format fixtures test bookkeeping, not chemical validity."""
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from irc_ibo_studio.core import load_trajectory, save_xyz, save_project, load_project
from irc_ibo_studio.merge import merge_irc_logs, selection_indices

ROOT = Path(__file__).resolve().parents[1]
BASE = np.array([[0.,0.,0.],[1.1,0.,0.],[0.,1.3,0.],[0.,0.,1.2]])
MOVE = np.array([[0.,0.,0.],[.12,.02,0.],[0.,-.07,.01],[0.,0.,.06]])


def write_log(path, direction, n=3, rotation=None, shift=None, perturb=None,
              level='hf/sto-3g', solvent='', ts_energy=-100., direction_sign=None,
              charge=0, mult=1, symbols=(6,6,6,6), error=False, start=0,
              route_extra='', signed_rc=False):
    sign = direction_sign if direction_sign is not None else (1 if direction == 'forward' else -1)
    def block(point, trial=False):
        coords=BASE + sign*MOVE*point
        if perturb is not None:coords=coords+perturb
        if rotation is not None:coords=coords@rotation
        if shift is not None:coords=coords+shift
        lines=[' Standard orientation:', ' -----', ' Center Atomic Atomic Coordinates (Angstroms)',
               ' Number Number Type X Y Z', ' -----']
        lines += [f' {i+1} {z} 0 {v[0]:.8f} {v[1]:.8f} {v[2]:.8f}' for i,(z,v) in enumerate(zip(symbols,coords))]
        lines += [' -----',f' SCF Done: E(RHF) = {ts_energy-.01*point**2:.9f} A.U.']
        if not trial:
            rc=point*.1*(-1 if signed_rc else 1)
            lines += [f' Point Number: {point} Path Number: 1',f' NET REACTION COORDINATE UP TO THIS POINT = {rc:.6f}']
        return '\n'.join(lines)+'\n'
    content=f' #p irc=({direction},calcfc,stepsize=5,maxpoints=100) {level} {solvent} geom=connectivity {route_extra}\n -----\n Charge = {charge} Multiplicity = {mult}\n'
    for point in range(start,n+1):
        if point>0:content+=block(point-.2,trial=True)
        content+=block(point)
    if error:content+=block(n+.3,trial=True)+' Error termination via Lnk1e\n'
    else:content+=' Normal termination of Gaussian 16\n'
    path.write_text(content,encoding='utf-8')
    return path


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.f=write_log(self.root/'forward.log','forward',n=3)
        rotation=np.array([[0.,-1.,0.],[1.,0.,0.],[0.,0.,1.]])
        self.r=write_log(self.root/'reverse.log','reverse',n=2,rotation=rotation,shift=[3.,4.,-2.],signed_rc=True)

    def test_order_ts_dedup_provenance_energy_and_no_coordinate_edits(self):
        original_f=load_trajectory(self.f);original_r=load_trajectory(self.r)
        result=merge_irc_logs(self.f,self.r)
        self.assertEqual([f.point for f in result.frames],[2,1,0,1,2,3])
        self.assertEqual([f.branch for f in result.frames],['reverse','reverse','ts','forward','forward','forward'])
        np.testing.assert_allclose([f.reaction_coordinate for f in result.frames],[-.2,-.1,0,.1,.2,.3])
        self.assertEqual([f.energy for f in result.frames],[-100.04,-100.01,-100.,-100.01,-100.04,-100.09])
        self.assertEqual(result.frames[0].coords,original_r.frames[-1].coords)
        self.assertEqual(result.frames[-1].coords,original_f.frames[-1].coords)
        self.assertEqual(result.frames[0].source_file,str(self.r))
        self.assertEqual(result.frames[0].source_line,original_r.frames[-1].source_line)
        self.assertEqual(result.frames[0].original_reaction_coordinate,-.2)
        self.assertLess(result.merge_info['ts_rmsd_angstrom'],1e-12)
        self.assertLess(result.merge_info['first_step_cosine'],-.99)
        self.assertEqual(sum(f.point==0 for f in result.frames),1)

    def test_reverse_display_order_reverses_full_sequence(self):
        a=merge_irc_logs(self.f,self.r);b=merge_irc_logs(self.f,self.r,'forward')
        self.assertEqual([f.point for f in b.frames],list(reversed([f.point for f in a.frames])))
        self.assertEqual([f.energy for f in b.frames],list(reversed([f.energy for f in a.frames])))
        np.testing.assert_allclose([f.reaction_coordinate for f in b.frames],[-.3,-.2,-.1,0,.1,.2])

    def test_roundtrip_exports_and_ts_retained_when_downsampling(self):
        merged=merge_irc_logs(self.f,self.r)
        indexes=selection_indices(merged.frames,0,5,4)
        self.assertEqual(indexes,[0,2,4,5])
        subset=[merged.frames[i] for i in indexes]
        xyz=self.root/'merged.xyz';save_xyz(subset,xyz)
        self.assertEqual(load_trajectory(xyz).frames,subset)
        project=self.root/'project.json';save_project(merged,project,{'stride':'4'})
        self.assertEqual(load_project(project)[0],merged)
        self.assertEqual(selection_indices(merged.frames,3,5,4),[3,5])

    def test_old_project_v1_still_loads(self):
        t=load_trajectory(self.f)
        data=asdict(t);data.pop('route');data.pop('merge_info')
        for f in data['frames']:
            for k in ['source_file','branch','original_reaction_coordinate']:f.pop(k)
        p=self.root/'v1.json';p.write_text(json.dumps(dict(format='IRC_IBO_Studio',version=1,trajectory=data,settings={})))
        loaded,_=load_project(p)
        self.assertEqual(len(loaded.frames),4);self.assertEqual(loaded.merge_info,{})

    def test_error_end_uses_only_completed_points(self):
        write_log(self.r,'reverse',n=2,error=True)
        result=merge_irc_logs(self.f,self.r)
        self.assertEqual(len(result.frames),6)
        self.assertTrue(any('エラー終了' in w for w in result.warnings))
        self.assertTrue(all(f.status=='accepted' for f in result.frames))

    def test_same_file_wrong_direction_and_same_direction_geometry_rejected(self):
        with self.assertRaisesRegex(ValueError,'同じファイル'):merge_irc_logs(self.f,self.f)
        with self.assertRaisesRegex(ValueError,'方向指定'):merge_irc_logs(self.r,self.f)
        write_log(self.r,'reverse',direction_sign=1)
        with self.assertRaisesRegex(ValueError,'同じ向き'):merge_irc_logs(self.f,self.r)

    def test_different_ts_or_mirrored_ts_or_energy_rejected(self):
        for delta,rotation,energy in [(MOVE,None,-100.),(None,np.diag([-1,1,1]),-100.),(None,None,-99.)]:
            with self.subTest(delta=delta,rotation=rotation):
                write_log(self.r,'reverse',perturb=delta,rotation=rotation,ts_energy=energy)
                with self.assertRaisesRegex(ValueError,'同じTS|エネルギー差'):merge_irc_logs(self.f,self.r)

    def test_conditions_atom_order_charge_and_multiple_paths_rejected(self):
        cases=[({'level':'b3lyp/sto-3g'},'計算条件'),({'solvent':'scrf=(pcm,solvent=water)'},'計算条件'),
               ({'mult':3},'多重度'),({'symbols':(6,7,6,6)},'元素順')]
        for kwargs,message in cases:
            with self.subTest(kwargs=kwargs):
                write_log(self.r,'reverse',**kwargs)
                with self.assertRaisesRegex(ValueError,message):merge_irc_logs(self.f,self.r)
        write_log(self.r,'reverse');s=self.r.read_text().replace('Point Number: 2 Path Number: 1','Point Number: 2 Path Number: 2');self.r.write_text(s)
        with self.assertRaisesRegex(ValueError,'片方向'):merge_irc_logs(self.f,self.r)

    def test_missing_ts_missing_point_and_restart_rejected(self):
        write_log(self.r,'reverse',start=1)
        with self.assertRaisesRegex(ValueError,'点0'):merge_irc_logs(self.f,self.r)
        write_log(self.r,'reverse');self.r.write_text(self.r.read_text().replace('Point Number: 1','Point Number: 9'))
        with self.assertRaisesRegex(ValueError,'点番号'):merge_irc_logs(self.f,self.r)
        write_log(self.r,'reverse');self.r.write_text(self.r.read_text().replace('reverse,calcfc','reverse,restart'))
        with self.assertRaisesRegex(ValueError,'Restart'):merge_irc_logs(self.f,self.r)

    def test_synthetic_forward_route_and_trials_remain_correct(self):
        real=write_log(self.root/'synthetic-forward.log','forward',n=70)
        t=load_trajectory(real)
        self.assertIn('geom=connectivity',t.route)
        self.assertEqual(len(t.frames),71)
        # Synthetic format fixture only; not a chemically computed branch.
        from irc_ibo_studio.merge import _branch
        verified,_=_branch(real,'forward')
        self.assertEqual(verified.frames[-1].point,70)

if __name__=='__main__':unittest.main()
