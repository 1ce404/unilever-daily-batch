"""Paid whole-speech work cannot escape the approved video keys or dry-run time window."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from task_bulk import validate_budget
class TaskBulkBudgetTest(unittest.TestCase):
 def setUp(self):self.plan={'at':100,'keys':['B-V02','B-V03']}
 def test_approved_remaining_video_is_allowed(self):validate_budget(self.plan,'B-V02',101)
 def test_demo_and_extra_video_cannot_consume_budget(self):
  for key in ['B-V01','B-V04','OTHER-V02']:
   with self.assertRaises(RuntimeError):validate_budget(self.plan,key,101)
 def test_stale_budget_stops_before_paid_work(self):
  with self.assertRaises(RuntimeError):validate_budget(self.plan,'B-V02',7301)
if __name__=='__main__':unittest.main()
