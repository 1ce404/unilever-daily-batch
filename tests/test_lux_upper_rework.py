import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import quality_contract as q
from lux_upper_rework import validate
class UpperRevision(unittest.TestCase):
 def draft(self):
  titles=[{'id':f'upper-{n}','trackId':'effect','from':n*30,'durationInFrames':30,'props':{'code':'native-mark','params':{'text':'保留原文','position':'top_center','safeTop':.02}}} for n in [1,3,6]]
  return {'composition':{'fps':30},'clips':titles+[{'id':'s1','trackId':'overlay','props':{'font_size':36,'text':'底部字幕'}},{'id':'voice','trackId':'voiceover','props':{'src':'https://x/approved.wav','volume':1}}]}
 def test_protects_caption_voice_and_upper_text(self):
  before=self.draft();after=copy.deepcopy(before);after['clips'][0]['props']['code']='native-metal';validate(before,after)
  for mutate in [lambda d:d['clips'][3]['props'].update(font_size=40),lambda d:d['clips'][4]['props'].update(volume=.5),lambda d:d['clips'][0]['props']['params'].update(text='偷改原文')]:
   d=copy.deepcopy(after);mutate(d)
   with self.assertRaises(RuntimeError):validate(before,d)
 def test_sound_repair_cannot_exempt_voice_track(self):
  before=self.draft();after=copy.deepcopy(before);after['clips'][4]['props']['volume']=.5
  with self.assertRaises(RuntimeError):validate(before,after,[{'clip':'voice'}])
 def test_more_caption_styles_or_colors_cannot_hide_upper_repetition(self):
  cfg={'quality_contract':'quality/lux.json'};b={'quantity':10,'upper_title_variation':{'min_actual_sets':4}};rows=[]
  for i in range(10):
   d=self.draft();d['clips'][0]['props']['params'].update(accent=str(i),text='第'+str(i)+'句');d['clips'][3]['props']['font_size']=30+i
   rows.append((str(i),{'quality_facts':q.cut_facts(d)}))
  self.assertTrue(any('上方花字实际仅1套' in e for e in q.batch_issues(b,rows,cfg)))
  for i,(_,v) in enumerate(rows):
   d=self.draft()
   for c in d['clips'][:3]:c['props']['code']='native-family-'+str(i%4)
   v['quality_facts']=q.cut_facts(d)
  self.assertFalse(any('上方花字' in e for e in q.batch_issues(b,rows,cfg)))
