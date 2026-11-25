import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lux_final_qc import canonical

class ExactDraftEvidence(unittest.TestCase):
 def test_only_oss_signatures_may_refresh(self):
  old={'clips':[{'props':{'src':'https://a.aliyuncs.com/v.mp4?start=4&x-oss-signature=old'}}]}
  fresh={'clips':[{'props':{'src':'https://a.aliyuncs.com/v.mp4?x-oss-signature=new&start=4'}}]}
  self.assertEqual(canonical(old),canonical(fresh))
  fresh['clips'][0]['props']['src']='https://a.aliyuncs.com/v.mp4?start=5&x-oss-signature=new'
  self.assertNotEqual(canonical(old),canonical(fresh))
 def test_timing_volume_and_other_hosts_are_protected(self):
  old={'from':5,'volume':.2,'src':'https://example.com/v?token=old'}
  for field,value in [('from',6),('volume',.13),('src','https://example.com/v?token=new')]:
   changed=dict(old);changed[field]=value
   self.assertNotEqual(canonical(old),canonical(changed))
