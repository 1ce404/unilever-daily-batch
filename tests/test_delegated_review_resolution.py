import tempfile,json,hashlib,copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from delegated_review_resolution import valid
class ResolutionTest(unittest.TestCase):
 def fixture(self,t):
  p=Path(t)/'frame';p.write_bytes(b'actual frame');ref={'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()};d={'key':'k','decision':'platform_false_positive','reviewer':'Agent','delegated_user_instruction':'检查完再批量','render_job':'new','object_key':'out','video_sha256':'media','raw_platform_reason':'wrong','finding':'Actual frame is one line','evidence':[ref]};report=Path(t)/'report.json';report.write_text(json.dumps(d));v={'ai_review':'reject','ai_reason':'wrong','render_job':'new','object_key':'out','media_qc':{'render_job':'new','output_object_key':'out','sha256':'media','technical_pass':True,'visual_pass':True},'delegated_quality_resolution':{'path':str(report),'sha256':hashlib.sha256(report.read_bytes()).hexdigest()}};return v,p,report
 def test_precise_resolution_keeps_raw_reject(self):
  with tempfile.TemporaryDirectory() as t:
   v,_,_=self.fixture(t);old=copy.deepcopy(v);self.assertTrue(valid(v));self.assertEqual(old,v);self.assertEqual(v['ai_review'],'reject')
 def test_stale_render_reason_or_unchecked_visual_fails(self):
  with tempfile.TemporaryDirectory() as t:
   v,_,_=self.fixture(t)
   for field,value in [('render_job','old'),('object_key','old'),('ai_reason','new reason')]:
    x=copy.deepcopy(v);x[field]=value;self.assertFalse(valid(x))
   v['media_qc']['visual_pass']=False;self.assertFalse(valid(v))
 def test_changed_report_or_evidence_fails(self):
  with tempfile.TemporaryDirectory() as t:
   v,p,_=self.fixture(t);p.write_bytes(b'changed');self.assertFalse(valid(v))
  with tempfile.TemporaryDirectory() as t:
   v,_,p=self.fixture(t);p.write_text('{}');self.assertFalse(valid(v))
 def test_missing_resolution_fails(self):self.assertFalse(valid({}))
