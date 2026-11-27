import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from quality_contract import cut_issues
class CaptionMeaning(unittest.TestCase):
 def errors(self,words):
  d={'composition':{'durationInFrames':0},'clips':[{'id':str(i),'trackId':'overlay','from':i*30,'props':{'text':t}} for i,t in enumerate(words)]}
  return [x for x in cut_issues(d,{'quality_contract':'quality/lux.json'}) if '语义词组' in x]
 def test_cross_sentence_head_hair_token_does_not_cut_fasi(self):self.assertTrue(self.errors(['洗个','头发','丝也想香香地出场']))
 def test_preserves_complete_words_without_requiring_one_long_line(self):self.assertEqual(self.errors(['洗个头','发丝也想香香地出场']),[])
 def test_does_not_impose_terms_absent_from_actual_copy(self):self.assertEqual(self.errors(['头发有光泽','来直播间']),[])
