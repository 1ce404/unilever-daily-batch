import copy,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from group_bgm_rework import music_only,validate_mix
from group_rework_apply import digest
class MusicRepair(unittest.TestCase):
 def fixture(self):
  return {'composition':{'durationInFrames':300},'clips':[{'id':'bgm-body','trackId':'audio','component':'AudioClip','from':30,'durationInFrames':270,'props':{'src':'old','volume':.1,'startFrom':0}}, {'id':'vo','trackId':'voiceover','component':'AudioClip','from':30,'durationInFrames':270,'props':{'src':'approvedvoice','volume':1}}, {'id':'arrow','trackId':'sticker','component':'StickerClip','from':260,'durationInFrames':40,'props':{'x':0,'y':0,'scale':1}}]}
 def test_only_source_window_and_mix_can_change(self):
  before=self.fixture();after=copy.deepcopy(before);after['clips'][0]['props'].update(src='uploadedreference',startFrom=600,volume=.18,fadeOut=9);music_only(before,after);self.assertEqual(before,self.fixture())
 def test_voice_arrow_timing_or_track_loss_rejected(self):
  for mode in ['voice','arrow','music_timing','music_remove','transition']:
   with self.subTest(mode=mode):
    old=self.fixture();new=copy.deepcopy(old)
    if mode=='voice':new['clips'][1]['props']['volume']=.8
    if mode=='arrow':new['clips'][2]['props']['x']=10
    if mode=='music_timing':new['clips'][0]['durationInFrames']=260
    if mode=='music_remove':new['clips'].pop(0)
    if mode=='transition':new['clips'][1]['transitionIn']={'type':'flash'}
    with self.assertRaises(RuntimeError):music_only(old,new)
 def test_render_gate_rejects_quiet_clipping_and_stale_mix(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'bgm-reference').mkdir();p={'key':'B01-V01','draft':self.fixture()};m={'key':p['key'],'draft_fingerprint':digest(p['draft']),'gain':.1,'expected_music_vs_vo_db':-5,'predicted_source_peak':.8};f=root/'bgm-reference/mix-measurements.json';f.write_text(json.dumps([m]));validate_mix(p,root)
   for field,value in [('draft_fingerprint','stale'),('gain',.025),('expected_music_vs_vo_db',-20),('predicted_source_peak',1.01)]:
    bad=dict(m);bad[field]=value;f.write_text(json.dumps([bad]))
    with self.assertRaises(RuntimeError):validate_mix(p,root)
if __name__=='__main__':unittest.main()
