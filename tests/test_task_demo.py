"""Whole native speech timeline must preserve copy and avoid guessed or bisected word cues."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from task_demo import timing,norm,fit_source_count
class DemoTimingTest(unittest.TestCase):
 def sample(self):
  rows=[{'id':'1','text':'看看力士精油香氛洗发水。'},{'id':'2','text':'快进直播间，给头发安排上！'}];text=''.join(norm(x['text']) for x in rows);tokens=[{'text':c,'start_ms':i*100,'end_ms':(i+1)*100} for i,c in enumerate(text)];return rows,{'audio':{'duration_ms':len(text)*100},'timeline':{'items':tokens}}
 def test_protected_product_name_and_native_word_times(self):
  rows,t=self.sample();total,bounds,cues=timing(t,rows,30);self.assertEqual(''.join(x['text'] for x in cues),''.join(norm(x['text']) for x in rows));self.assertTrue(any('力士精油香氛洗发水' in x['text'] for x in cues));self.assertTrue(all(len(x['text'])<=12 for x in cues));self.assertEqual(bounds[0],0);self.assertEqual(bounds[-1],total)
 def test_missing_native_words_stops(self):
  rows,t=self.sample();t['timeline']['items']=t['timeline']['items'][1:]
  with self.assertRaises(RuntimeError):timing(t,rows,30)
 def test_short_real_sources_require_more_unique_shots(self):
  rows=[{'object_url':'https://x/'+str(i)+'.mp4','begin_ms':0,'end_ms':2000} for i in range(4)]
  self.assertEqual(fit_source_count(rows,set(),150,2,{},30),3)
 def test_alias_sources_cannot_cover_longer_segment(self):
  rows=[{'object_url':'https://x/same.mp4?q='+str(i),'begin_ms':0,'end_ms':2000} for i in range(4)]
  with self.assertRaises(RuntimeError):fit_source_count(rows,set(),150,2,{},30)
if __name__=='__main__':unittest.main()
