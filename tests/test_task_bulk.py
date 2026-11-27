"""Paid whole-speech work cannot escape the approved video keys or dry-run time window."""
import sys,unittest,tempfile,json,argparse
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from task_bulk import validate_budget,allocate,trim_title_ends,random_title_templates,title_template_instance
class ContinuousFramingTest(unittest.TestCase):
 def test_variable_shot_count_uses_actual_source_identity(self):
  from task_bulk import continuous_source_framing
  d={'clips':[{'id':'8-0','trackId':'main'},{'id':'8-1','trackId':'main'}]};e={'source_windows':[{'clip':'8-0','source_segment_id':'a'},{'clip':'8-1','source_segment_id':'b'}]};v={'framing_segment_id':'8','source_framing':{'a':{'scale':1.3},'b':{'scale':1.2},'unused':{'scale':1.4}}}
  with patch('task_caption_repair.frame_main',return_value=d) as fn:
   continuous_source_framing(d,e,v);self.assertEqual(fn.call_args.args[1],{'8-0':{'scale':1.3},'8-1':{'scale':1.2}})
  e['source_windows'][0]['clip']='3-0'
  with self.assertRaisesRegex(RuntimeError,'outside explicit'):continuous_source_framing(d,e,v)

class TaskBulkBudgetTest(unittest.TestCase):
 def setUp(self):self.plan={'at':100,'keys':['B-V02','B-V03']}
 def test_approved_remaining_video_is_allowed(self):validate_budget(self.plan,'B-V02',101)
 def test_demo_and_extra_video_cannot_consume_budget(self):
  for key in ['B-V01','B-V04','OTHER-V02']:
   with self.assertRaises(RuntimeError):validate_budget(self.plan,key,101)
 def test_stale_budget_stops_before_paid_work(self):
  with self.assertRaises(RuntimeError):validate_budget(self.plan,'B-V02',7301)
 def test_title_may_end_before_new_product_closeup_but_cannot_extend(self):
  clips=[{'id':'title','trackId':'effect','from':100,'durationInFrames':66}];trim_title_ends(clips,{'title':140});self.assertEqual(clips[0]['durationInFrames'],40)
  for end in [100,180]:
   with self.assertRaises(RuntimeError):trim_title_ends(clips,{'title':end})
  clips[0]['trackId']='voiceover'
  with self.assertRaises(RuntimeError):trim_title_ends(clips,{'title':120})
 def test_cross_video_same_person_cannot_be_hidden_by_new_source_ids(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);assets=root/'task-bulk';assets.mkdir();keys=['B-V02','B-V03'];budget={'keys':keys,'at':1};(assets/'B-budget.json').write_text(json.dumps(budget))
   library=[{'id':sid,'object_url':'https://x/'+sid+'.mp4','review_result':'pass','tags':['头发顺滑']} for sid in ['a','b']];(assets/'B-library.json').write_text(json.dumps(library))
   plan={'authorization':'用户要求人物差异','reviewer':'Agent','evidence':'actual covers','videos':{key:{'segments':{'1':[sid]}} for key,sid in zip(keys,['a','b'])},'source_groups':{'a':'same-person','b':'same-person'},'max_videos_per_person_group':1};p=root/'plan.json';p.write_text(json.dumps(plan));args=argparse.Namespace(run=tmp,brief='B',plan=str(p),dry_run=True)
   with patch('task_bulk.cb.load_briefs',return_value=[{'brief_id':'B','segments':[{'id':'1','visual_tags':['头发顺滑']}]}]):
    with self.assertRaisesRegex(RuntimeError,'frequency cap'):allocate(args,{})
    plan['source_groups']['b']='second-person';p.write_text(json.dumps(plan));allocate(args,{})
class RandomTitleSelectionTest(unittest.TestCase):
 def test_any_current_style_can_be_selected_without_named_type_filter(self):
  rows=[{'id':str(i),'name':name} for i,name in enumerate(['漫画','电商','新闻','蓝金','日常','综艺'])]
  chosen=[random_title_templates(rows,'B-V%02d'%i,2) for i in range(2,32)]
  self.assertEqual({x['id'] for pair in chosen for x in pair},{x['id'] for x in rows})
  self.assertEqual(len({tuple(x['id'] for x in p) for p in chosen}),30)
  for pair in chosen:self.assertEqual(len({x['id'] for x in pair}),2)
 def test_same_video_is_stable_and_catalog_is_not_mutated(self):
  rows=[{'id':str(i)} for i in range(5)];before=json.dumps(rows)
  self.assertEqual(random_title_templates(rows,'B-V02',2),random_title_templates(rows,'B-V02',2));self.assertEqual(json.dumps(rows),before)
 def test_missing_and_duplicate_templates_are_rejected(self):
  for rows in [[{'id':'a'}],[{'id':'a'},{'id':'a'}]]:
   with self.assertRaises(RuntimeError):random_title_templates(rows,'B-V02',2)

class TitleAdapterTest(unittest.TestCase):
 def test_real_copy_is_preserved_and_inspected_coordinates_are_applied(self):
  import hashlib
  code='const Y = 1;';template={'id':'native','code':code};cfg={'batch_native_variants':{'require_title_adapters':True,'title_adapters':{'native':{'source_code_sha256':hashlib.sha256(code.encode()).hexdigest(),'params':{'text':'DEFAULT','y':0,'scale':.92},'code_replacements':[{'from':'Y = 1','to':'Y = 2'}]}}}}
  result,params=title_template_instance(template,{'text':'柔顺亮泽','y':-80},cfg)
  self.assertEqual(result,'const Y = 2;');self.assertEqual(params,{'text':'柔顺亮泽','y':0,'scale':.92});self.assertEqual(template['code'],code)
 def test_changed_or_uninspected_template_stops_before_paid_work(self):
  cfg={'batch_native_variants':{'require_title_adapters':True,'title_adapters':{'native':{'source_code_sha256':'wrong'}}}}
  for item in [{'id':'native','code':'changed'},{'id':'new','code':'new'}]:
   with self.assertRaises(RuntimeError):title_template_instance(item,{'text':'柔顺亮泽'},cfg)

if __name__=='__main__':unittest.main()
