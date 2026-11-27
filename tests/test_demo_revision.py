import copy,hashlib,tempfile,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from task_demo import continuous_demo_hook
from task_configuration import permits_demo_revision,digest
from common import copy_hash
class DemoRevisionTest(unittest.TestCase):
 def fixture(self,tmp):
  p=Path(tmp)/'proof';p.write_text('actual reviewed full opening')
  main=[{'id':'1-0','from':0,'durationInFrames':75,'props':{'src':'hook','startFrom':0}}, {'id':'2-0','from':75,'durationInFrames':100,'props':{'src':'real','startFrom':10}}, {'id':'3-0','from':175,'durationInFrames':40,'props':{'src':'bottle','startFrom':3}}]
  sources=[{'clip':x['id'],'source_segment_id':x['props']['src'],'source_available_frames':200,'used_frames':x['durationInFrames'],'startFrom':x['props']['startFrom']} for x in main]
  rule={'key':'D-V01','durationInFrames':150,'segment_id':'hook','evidence_path':str(p),'evidence_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
  return main,sources,rule,p
 def test_one_continuous_source_crosses_captions_with_real_remainder(self):
  with tempfile.TemporaryDirectory() as tmp:
   main,sources,rule,_=self.fixture(tmp);prior=copy.deepcopy(main);out,records=continuous_demo_hook(main,sources,rule,30,'D-V01')
   self.assertEqual(main,prior);self.assertEqual(out[0]['durationInFrames'],150);self.assertEqual(out[1]['from'],150);self.assertEqual(out[1]['props']['startFrom'],85);self.assertEqual(out[1]['durationInFrames'],25);self.assertEqual(out[2],main[2]);self.assertEqual(len(records),3)
 def test_wrong_scope_proof_or_short_source_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   main,sources,rule,proof=self.fixture(tmp)
   with self.assertRaises(RuntimeError):continuous_demo_hook(main,sources,rule,30,'OTHER-V01')
   sources[0]['source_available_frames']=120
   with self.assertRaises(RuntimeError):continuous_demo_hook(main,sources,rule,30,'D-V01')
   sources[0]['source_available_frames']=200;proof.write_text('changed')
   with self.assertRaises(RuntimeError):continuous_demo_hook(main,sources,rule,30,'D-V01')
 def test_existing_longer_first_sentence_is_not_truncated(self):
  with tempfile.TemporaryDirectory() as tmp:
   main,sources,rule,_=self.fixture(tmp);main[0]['durationInFrames']=160;main[1]['from']=160;main[1]['durationInFrames']=15
   out,_=continuous_demo_hook(main,sources,rule,30,'D-V01');self.assertEqual(out[0]['durationInFrames'],160);self.assertEqual(out[1]['from'],160)
 def test_revision_binds_current_script_and_exact_new_demo(self):
  b={'brief_id':'D','status':'draft','demo_first':True,'segments':[{'id':'1','role':'hook','text':'开箱','alt':[],'visual_tags':['AI-1127-开箱']}]}
  b['demo_production_authorization']={'mode':'demo_only_before_review','by':'用户','instruction':'立即出demo','quantity':1,'copy_hash':copy_hash(b),'ai_tags':['AI-1127-开箱'],'review_pending':True}
  current={'segments':['old']};item={'revision_authorization':{'by':'用户','instruction':'立即出demo','brief_id':'D','quantity':1,'previous_body_digest':digest(current),'new_copy_hash':copy_hash(b)}}
  self.assertTrue(permits_demo_revision(item,b,current));self.assertFalse(permits_demo_revision(item,b,{'segments':['changed']}))
  for field,value in [('quantity',True),('new_copy_hash','old'),('brief_id','other')]:
   altered=copy.deepcopy(item);altered['revision_authorization'][field]=value;self.assertFalse(permits_demo_revision(altered,b,current))
