import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import quality_contract as q
class FontVariety(unittest.TestCase):
 def test_color_and_style_ids_do_not_substitute_for_fonts(self):
  b={'quantity':10,'segments':[]};s={'config':{'subtitle_style':{'style_ids':list('abcde'),'font_ids':['one']},'bgm':{'props':{'ids':['x','y']}},'tts':{'voices':[{'id':x} for x in 'abc']}},'segments':[]}
  self.assertTrue(any('字体' in x for x in q.config_issues(s,b,{'quality_contract':'quality/lux.json'})))
 def test_actual_font_family_is_tracked_separately(self):
  d={'clips':[{'id':'s','trackId':'overlay','props':{'text':'发丝','font_family':'F','font_size':36}}]};self.assertEqual(q.cut_facts(d)['font_family'],'F')
