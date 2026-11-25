import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from group_no_transitions import without,check
class TransitionOnly(unittest.TestCase):
 def fixture(self):return {'composition':{'durationInFrames':100},'clips':[{'id':'main','from':0,'durationInFrames':100,'component':'VideoClip','props':{'src':'source','startFrom':5},'transitionIn':{'type':'hardCutFlash','durationInFrames':4},'transitionOut':None},{'id':'vo','from':0,'durationInFrames':100,'component':'AudioClip','props':{'src':'voice','volume':1,'fadeOut':6}},{'id':'arrow','from':80,'durationInFrames':20,'component':'StickerClip','props':{'src':'avatar-arrow','x':0,'y':0,'scale':1}},{'id':'upper','from':10,'durationInFrames':30,'component':'RemotionClip','props':{'code':'approved-animation','params':{'text':'approved headline'}}}]}
 def test_all_other_tracks_sources_timing_and_audio_fades_survive(self):
  before=self.fixture();snapshot=copy.deepcopy(before);after=without(before);check(before,after);self.assertEqual(before,snapshot);self.assertIsNone(after['clips'][0]['transitionIn']);self.assertEqual(after['clips'][1]['props']['fadeOut'],6)
 def test_narrow_scope_rejects_other_changes(self):
  for index,key,value in [(0,'startFrom',6),(1,'volume',.5),(2,'x',-800),(3,'code','different')]:
   before=self.fixture();after=without(before);after['clips'][index]['props'][key]=value
   with self.assertRaises(RuntimeError):check(before,after)
if __name__=='__main__':unittest.main()
