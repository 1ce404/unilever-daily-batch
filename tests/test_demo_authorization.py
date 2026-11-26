import copy, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
from common import approval_problem, aigc_problem, demo_problem, copy_hash, aigc_tags
from demo_authorization import permits_preview, preview_ai_ids
class DemoPermissionTest(unittest.TestCase):
 def brief(self):
  b={"brief_id":"B","status":"draft","demo_first":True,"segments":[{"id":"1","role":"hook","text":"花香出场","visual_tags":["AI-1127-test"]}]}
  b["demo_production_authorization"]={"mode":"demo_only_before_review","by":"用户","instruction":"先做demo，我明天检查","quantity":1,"copy_hash":copy_hash(b),"ai_tags":aigc_tags(b),"review_pending":True};return b
 def test_permission_does_not_create_approval(self):
  b=self.brief();before=copy.deepcopy(b);self.assertIsNone(approval_problem(b));self.assertIsNone(aigc_problem(b));self.assertEqual(b,before);self.assertIsNotNone(demo_problem(b));self.assertEqual(b['status'],'draft')
 def test_draft_without_permission_stays_blocked(self):
  b=self.brief();b.pop('demo_production_authorization');self.assertIsNotNone(approval_problem(b));self.assertIsNotNone(aigc_problem(b))
 def test_changed_copy_and_ai_tag_invalidate_permission(self):
  for change in ['text','visual_tags']:
   b=self.brief();b['segments'][0][change]='改稿' if change=='text' else ['AI-1127-other'];self.assertFalse(permits_preview(b))
 def test_only_v01_gets_one_selected_ai_source(self):
  b=self.brief();cfg={'preview_ai_hook_ids':{'B':['a']}};self.assertEqual(preview_ai_ids(b,cfg,'B-V01'),['a']);self.assertEqual(preview_ai_ids(b,cfg,'B-V02'),[]);cfg['preview_ai_hook_ids']['B'].append('b');self.assertEqual(preview_ai_ids(b,cfg,'B-V01'),[])
 def test_no_bulk_or_pending_marker_bypass(self):
  for field,value in [('quantity',9),('by','Agent'),('review_pending',False),('instruction','')]:
   b=self.brief();b['demo_production_authorization'][field]=value;self.assertFalse(permits_preview(b))
 def test_real_approval_must_follow_normal_path(self):
  b=self.brief();b['demo_approval']={'by':'用户','copy_hash':copy_hash(b)};self.assertFalse(permits_preview(b));self.assertIsNotNone(approval_problem(b))
class PreviewDeliveryTest(unittest.TestCase):
 def test_preview_output_is_exact_and_customer_pending(self):
  from preview_delivery import checked_preview_video
  b=DemoPermissionTest().brief();b['product_key']='p';v={'brief_id':'B','stage':'done','user_review_pending':True,'cut_id':'c','render_job':'r','object_key':'o'}
  self.assertEqual(checked_preview_video(b,v,{'script_id':'s'})['expected_output_object_key'],'o')
  for change in [{'stage':'rendering'},{'user_review_pending':False},{'object_key':None},{'customer_rework_required':'user rejected'}]:
   with self.assertRaises(RuntimeError):checked_preview_video(b,{**v,**change},{'script_id':'s'})
 def test_progress_distinguishes_production_and_customer_review(self):
  from progress import next_step,video_bucket
  from collections import Counter
  b=DemoPermissionTest().brief();b['quantity']=10
  counts=Counter({'in_flight':1});self.assertEqual(next_step({},b,{'stage':'generating'},counts,0,False,lambda *x:'cmd')[0],'demo 制作中')
  counts=Counter({'quality_pending':1});stage,action=next_step({},b,{'demo_stage':'demo'},counts,0,False,lambda *x:'cmd');self.assertEqual(stage,'demo 待客户检查');self.assertNotIn('topup',action);self.assertEqual(video_bucket({'stage':'demo_prepare_pending'}),'in_flight')
if __name__=='__main__':unittest.main()
