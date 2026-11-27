import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from title_audio import attach,missing_title_sounds
import quality_contract as q
class TitleSoundTest(unittest.TestCase):
 def draft(self):return {'composition':{'fps':30,'durationInFrames':120},'clips':[{'id':'fx1','trackId':'effect','from':30,'durationInFrames':30,'props':{'params':{'text':'花香'}}},{'id':'fx2','trackId':'effect','from':80,'durationInFrames':30,'props':{'params':{'text':'柔顺'}}},{'id':'voice','trackId':'voiceover','props':{'src':'voice'}}]}
 def test_preserves_existing_and_is_idempotent(self):
  d=self.draft();pool=[{'id':str(i),'url':'https://x/'+str(i),'duration':.5,'categories':['产品出场']} for i in range(3)];a,rec=attach(d,pool,'video1');self.assertEqual(a['clips'][:3],d['clips']);self.assertEqual(len(rec),2);self.assertNotEqual(rec[0]['sound_id'],rec[1]['sound_id']);self.assertFalse(missing_title_sounds(a));self.assertEqual(attach(a,pool,'again')[0],a)
 def test_cta_music_silent_or_offset_sound_cannot_cover_titles(self):
  for change in [{'trackId':'audio'},{'props':{'src':'s','volume':0}},{'from':34},{'props':{'src':'s','muted':True}}]:
   d=self.draft();c={'id':'sound','trackId':'sound_effects','component':'AudioClip','from':30,'durationInFrames':10,'props':{'src':'s','volume':.3}};c.update(change);d['clips'].append(c);self.assertEqual(len(missing_title_sounds(d)),2)
 def test_zero_titles_blocked_and_rule_brand_scoped(self):
  d={'composition':{'fps':30,'durationInFrames':0},'clips':[]};self.assertIn('整条视频没有足够的花字特效',q.cut_issues(d,{'quality_contract':'quality/lux.json'}));self.assertEqual(q.cut_issues(d,{}),[])
