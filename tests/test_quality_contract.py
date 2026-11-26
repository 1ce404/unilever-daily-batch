import copy,json,sys,tempfile,unittest,urllib.error
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import quality_contract as q
import asset_health
import clipo_batch as cb

class QualityContractTest(unittest.TestCase):
 def setUp(self):self.cfg={'quality_contract':'quality/lux.json'}
 def script(self):
  b={'quantity':10,'segments':[{'visual_tags':['AI-1111-追光']}]+[{'visual_tags':['头发顺滑']} for _ in range(5)]}
  s={'config':{'subtitle_style':{'style_ids':list('abcde')},'bgm':{'props':{'ids':list('abcdef')}},'tts':{'voices':[{'id':x} for x in 'abc']}},'segments':[{'id':str(i+1),'visual':{'mode':'material_segment_ids','props':{'segment_ids':[str(i)+'-'+str(j) for j in range(5 if i==0 else 9)]}},'effect':{'params':{'text':'花字'}} if i in [0,2,5] else None} for i in range(6)]}
  return b,s
 def draft(self):
  return {'composition':{'fps':30,'durationInFrames':60},'clips':[{'id':'1-0','trackId':'main','from':0,'durationInFrames':30,'props':{'src':'https://x/a.mp4?q=1'}},{'id':'2-0','trackId':'main','from':30,'durationInFrames':30,'props':{'src':'https://x/b.mp4'}},{'id':'s1','trackId':'overlay','from':0,'durationInFrames':25,'props':{'text':'力士精油香氛洗发水','font_size':36,'font_family':'f','top':78,'stroke_width':4}},{'id':'s2','trackId':'overlay','from':30,'durationInFrames':25,'props':{'text':'柔顺有光泽','font_size':36,'font_family':'f','top':78,'stroke_width':4,'text_color':'#ffd16a'}},{'id':'voice-continuous','trackId':'voiceover','props':{}},{'id':'bgm','trackId':'audio','props':{'src':'https://x/music.mp3?token=private'}}]}
 def test_valid_native_choices(self):
  b,s=self.script();self.assertEqual(q.config_issues(s,b,self.cfg),[])
 def test_single_style_single_music_and_demo_pool_cannot_bulk(self):
  b,s=self.script();s['config']['subtitle_style']['style_ids']=['a'];s['config']['bgm']['props']['ids']=['m'];s['segments'][0]['visual']['props']['segment_ids']=['one'];s['segments'][2]['visual']['props']['segment_ids']=['x','y'];errors=';'.join(q.config_issues(s,b,self.cfg));self.assertIn('字幕样式',errors);self.assertIn('BGM',errors);self.assertIn('素材池1条',errors);self.assertIn('素材池2条',errors)
 def test_alias_ids_do_not_make_distinct_sources(self):
  b,s=self.script();ids=s['segments'][0]['visual']['props']['segment_ids'];library=[{'id':x,'object_url':'https://x/same.mp4?signature='+x} for x in ids];self.assertTrue(any('同一源素材' in x for x in q.config_issues(s,b,self.cfg,library)))
 def test_uniform_cues_can_highlight_product(self):self.assertEqual(q.cut_issues(self.draft(),self.cfg),[])
 def test_duplicate_file_despite_fresh_signed_url_is_blocked(self):
  d=self.draft();d['clips'][1]['props']['src']='https://x/a.mp4?q=other';self.assertTrue(any('重复源素材' in x for x in q.cut_issues(d,self.cfg)))
 def test_changed_font_or_size_within_video_is_blocked(self):
  d=self.draft();d['clips'][3]['props']['font_family']='other';self.assertTrue(any('不统一' in x for x in q.cut_issues(d,self.cfg)))
 def test_multiline_or_overlong_cue_blocked(self):
  for text in ['头发\n光泽','这是一条明显超过了十二个字的字幕']:
   d=self.draft();d['clips'][2]['props']['text']=text;self.assertTrue(any('单行' in x for x in q.cut_issues(d,self.cfg)))
 def test_native_upper_title_newline_blocked_in_config_and_cut(self):
  b,s=self.script();s['segments'][0]['effect']['params']['text']='柔顺想安\n排';self.assertTrue(any('花字含换行' in x for x in q.config_issues(s,b,self.cfg)))
  d=self.draft();d['clips'].append({'id':'3-x0','trackId':'effect','from':0,'props':{'code':'native','params':{'text':'柔顺想安\n排'}}});self.assertTrue(any('花字未满足单行' in x for x in q.cut_issues(d,self.cfg)))
 def test_native_effect_titles_require_three_actual_samples_per_title(self):
  d=self.draft();d['clips'].append({'id':'3-x0','trackId':'effect','from':0,'props':{'code':'native','params':{'text':'精油润养'}}});facts=q.cut_facts(d);self.assertEqual(facts['upper_title_count'],1);self.assertTrue(facts['upper_style_signature'])
  v={'quality_facts':facts,'render_job':'j','object_key':'a','ai_review':'pass','media_qc':{'technical_pass':True,'render_job':'j','output_object_key':'a','visual_pass':True,'reviewer':'agent','review_note':'实际看图'}};self.assertTrue(any('逐处抽帧' in x for x in q.delivery_issues(v,self.cfg)));v['media_qc']['upper_title_sample_count']=3;self.assertEqual(q.delivery_issues(v,self.cfg),[])
 def test_config_variety_does_not_substitute_actual_variety(self):
  b,_=self.script();v=[(str(i),{'quality_facts':q.cut_facts(self.draft())}) for i in range(10)];errors=q.batch_issues(b,v,self.cfg);self.assertEqual(len(errors),3)
 def test_duplicate_copy_blocks_partial_batch_too(self):
  b,_=self.script();v=[(str(i),{'quality_facts':q.cut_facts(self.draft())}) for i in range(2)];self.assertTrue(any('文案重复' in x for x in q.batch_issues(b,v,self.cfg)))
 def test_copy_fingerprint_ignores_punctuation(self):
  a=self.draft();b=self.draft();b['clips'][2]['props']['text']+='，。！';self.assertEqual(q.cut_facts(a)['copy_signature'],q.cut_facts(b)['copy_signature'])
 def test_user_rejection_survives_native_review_pass(self):
  self.assertTrue(q.delivery_issues({'ai_review':'pass','customer_rework_required':'重复文案'},self.cfg))
 def test_delivery_needs_current_media_and_visual_proof(self):
  v={'render_job':'new','object_key':'new.mp4','media_qc':{'technical_pass':True,'render_job':'old','output_object_key':'old.mp4','visual_pass':True,'reviewer':'agent','review_note':'看图'}};self.assertTrue(q.delivery_issues(v,self.cfg));v['media_qc'].update(render_job='new',output_object_key='new.mp4');self.assertEqual(q.delivery_issues(v,self.cfg),[])
 def test_dead_asset_error_does_not_expose_query(self):
  err=urllib.error.HTTPError('https://x/a.mp4?secret=bad',404,'missing',{},None)
  errors=asset_health.check_sources(self.draft(),opener=lambda *a,**k:(_ for _ in ()).throw(err));self.assertTrue(errors);self.assertFalse(any('secret' in x for x in errors))
 def test_whole_voice_rework_does_not_parse_voice_as_number(self):
  cb.CFG={'products':{'p':{}},'bgm_volume':.2};d=self.draft();self.assertEqual(cb.with_bgm_volume(d,'p')['composition'],d['composition'])
 def test_pending_media_is_qc_work_not_a_generation_shortfall(self):
  import progress
  with patch.object(cb,'CFG',self.cfg):
   v={'stage':'done','ai_review':'pass','render_job':'new','object_key':'new.mp4'}
   self.assertEqual(progress.video_bucket(v),'quality_pending')
   stage,next_action=progress.next_step(self.cfg,{'brief_id':'B1','quantity':10},{},{'ok':0,'quality_pending':10},0,False,lambda *x:'qc')
   self.assertEqual(stage,'成片自查');self.assertNotIn('topup',next_action)
if __name__=='__main__':unittest.main()
