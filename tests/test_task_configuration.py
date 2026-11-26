"""Native task drafts require exact human-approved copy and real source IDs before any write."""
import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import copy_hash
from task_configuration import validate_body,native_body
class TaskConfigTest(unittest.TestCase):
 def fixture(self):
  b={'status':'approved','segments':[{'id':'1','role':'hook','text':'护发精油，还能用喷的？','visual_tags':['AI-1126-轻雾']}]}
  b['approval']={'by':'用户','note':'好，开始配置','at':'2026-11-26','copy_hash':copy_hash(b)}
  body={'config':{'tts':{'voices':[{'id':'a'},{'id':'b'},{'id':'c'}]}},'segments':[{'id':'1','content':b['segments'][0]['text'],'visual':{'mode':'material_segment','props':{'tags':['AI-1126-轻雾']}}}]}
  return b,body
 def test_pending_ai_is_explicit_not_an_existing_source(self):
  b,body=self.fixture();self.assertEqual(validate_body(body,b,[]),['1'])
 def test_changed_copy_and_missing_approval_stop_write(self):
  for change in ['body','brief','approval']:
   b,body=self.fixture()
   if change=='body':body['segments'][0]['content']='改写了'
   if change=='brief':b['segments'][0]['text']='未经人审的新稿'
   if change=='approval':b.pop('approval')
   with self.subTest(change=change),self.assertRaises(RuntimeError):validate_body(body,b,[])
 def test_real_pool_rejects_missing_and_duplicate_ids(self):
  for ids in [['missing'],['a','a']]:
   b,body=self.fixture();body['segments'][0]['visual']={'mode':'material_segment_ids','props':{'segment_ids':ids}}
   with self.subTest(ids=ids),self.assertRaises(RuntimeError):validate_body(body,b,[{'id':'a'}])
 def test_native_null_subtitle_inherits_global_only(self):
  b,body=self.fixture();a=copy.deepcopy(body);a['segments'][0]['subtitle_style']=None
  self.assertEqual(native_body(a,body),native_body(body,body))
  a['segments'][0]['subtitle_style']={'mode':'override'}
  self.assertNotEqual(native_body(a,body),native_body(body,body))
  a=copy.deepcopy(body);a['config']['tts']['voices'].pop()
  self.assertNotEqual(native_body(a,body),native_body(body,body))
 def test_server_default_sfx_volume_is_semantically_equal(self):
  _,body=self.fixture();body['segments'][0]['sound_effects']={'mode':'ids','props':{'ids':['sfx']}}
  a=copy.deepcopy(body);a['segments'][0]['sound_effects']['volume']=0.5
  self.assertEqual(native_body(a,body),native_body(body,body))
  a['segments'][0]['sound_effects']['volume']=0.8
  self.assertNotEqual(native_body(a,body),native_body(body,body))
 def test_real_native_tags_keep_whole_library_selection(self):
  b,body=self.fixture();body['segments'][0]['visual']={'mode':'material_segment','props':{'tags':['产品外观']}}
  self.assertEqual(validate_body(body,b,[{'id':'one','tags':['产品外观'],'ai_generated':False}]),[])
  self.assertNotIn('segment_ids',body['segments'][0]['visual']['props'])
  with self.assertRaises(RuntimeError):validate_body(body,b,[])
if __name__=='__main__':unittest.main()
