"""Paid whole-speech work cannot escape the approved video keys or dry-run time window."""
import sys,unittest,tempfile,json,argparse
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from task_bulk import validate_budget,allocate
class TaskBulkBudgetTest(unittest.TestCase):
 def setUp(self):self.plan={'at':100,'keys':['B-V02','B-V03']}
 def test_approved_remaining_video_is_allowed(self):validate_budget(self.plan,'B-V02',101)
 def test_demo_and_extra_video_cannot_consume_budget(self):
  for key in ['B-V01','B-V04','OTHER-V02']:
   with self.assertRaises(RuntimeError):validate_budget(self.plan,key,101)
 def test_stale_budget_stops_before_paid_work(self):
  with self.assertRaises(RuntimeError):validate_budget(self.plan,'B-V02',7301)
 def test_cross_video_same_person_cannot_be_hidden_by_new_source_ids(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);assets=root/'task-bulk';assets.mkdir();keys=['B-V02','B-V03'];budget={'keys':keys,'at':1};(assets/'B-budget.json').write_text(json.dumps(budget))
   library=[{'id':sid,'object_url':'https://x/'+sid+'.mp4','review_result':'pass','tags':['头发顺滑']} for sid in ['a','b']];(assets/'B-library.json').write_text(json.dumps(library))
   plan={'authorization':'用户要求人物差异','reviewer':'Agent','evidence':'actual covers','videos':{key:{'segments':{'1':[sid]}} for key,sid in zip(keys,['a','b'])},'source_groups':{'a':'same-person','b':'same-person'},'max_videos_per_person_group':1};p=root/'plan.json';p.write_text(json.dumps(plan));args=argparse.Namespace(run=tmp,brief='B',plan=str(p),dry_run=True)
   with patch('task_bulk.cb.load_briefs',return_value=[{'brief_id':'B','segments':[{'id':'1','visual_tags':['头发顺滑']}]}]):
    with self.assertRaisesRegex(RuntimeError,'frequency cap'):allocate(args,{})
    plan['source_groups']['b']='second-person';p.write_text(json.dumps(plan));allocate(args,{})
if __name__=='__main__':unittest.main()
