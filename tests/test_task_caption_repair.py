import copy
import tempfile
import hashlib
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from task_caption_repair import semantic_cues, norm, reflow, patch_reviewed_sources, fit_native_effect_instances, frame_main


class CaptionRepairTest(unittest.TestCase):
    def sample(self):
        texts = ['就是力士精油香氛洗发水，泡泡密，冲起来也快。', '天然鲜花精油，配上4D玻尿酸，润养发丝。']
        words = list(''.join(texts))
        items = [{'text':c, 'start_ms':i*120, 'end_ms':(i+1)*120} for i,c in enumerate(words)]
        return texts, {'timeline':{'items':items}}

    def test_product_does_not_absorb_next_sentence_and_words_preserved(self):
        texts, tts = self.sample()
        cues = semantic_cues(tts, texts, 30)
        self.assertEqual(''.join(c['text'] for c in cues), norm(''.join(texts)))
        for term in ['力士精油香氛洗发水','天然鲜花精油','4D玻尿酸']:
            self.assertTrue(any(c['text']==term for c in cues))
        self.assertTrue(all(len(c['text'])<=10 for c in cues))
        for left,right in zip(cues,cues[1:]):
            self.assertLessEqual(left['from']+left['durationInFrames'],right['from'])

    def test_missing_timing_is_not_invented(self):
        texts,tts=self.sample()
        tts['timeline']['items'].pop(0)
        with self.assertRaises(RuntimeError):
            semantic_cues(tts,texts,30)

    def test_reviewed_chunks_keep_complete_words_without_retiming_guesses(self):
        text='发丝该有的照顾一样不少。'
        tts={'timeline':{'items':[{'text':c,'start_ms':i*100,'end_ms':(i+1)*100} for i,c in enumerate(text)]}}
        cues=semantic_cues(tts,[text],30,chunks=['发丝该有的照顾','一样不少'])
        self.assertEqual([x['text'] for x in cues],['发丝该有的照顾','一样不少'])
        with self.assertRaises(RuntimeError):semantic_cues(tts,[text],30,chunks=['照顾','不少'])

    def test_previous_sentence_tail_is_not_attached_to_ingredient(self):
        text='洗着是舒服的，发丝该有的照顾一样不少。天然鲜花精油，再配上4D玻尿酸。'
        tts={'timeline':{'items':[{'text':c,'start_ms':i*100,'end_ms':(i+1)*100} for i,c in enumerate(text)]}}
        cues=semantic_cues(tts,[text],30)
        self.assertTrue(any(c['text']=='天然鲜花精油' for c in cues))
        self.assertFalse(any('少天然' in c['text'] for c in cues))

    def test_protected_tracks_are_exactly_preserved(self):
        texts,tts=self.sample()
        normal={'id':'old','trackId':'overlay','from':0,'durationInFrames':100,'props':{'text':norm(''.join(texts)),'font_size':36,'text_color':'white'}}
        # An ordinary cue is needed to recover its real base color.
        normal['props']['text']='就是'
        second=copy.deepcopy(normal);second['id']='other';second['from']=100;second['props']['text']=norm(''.join(texts))[2:]
        main={'id':'main','trackId':'main','props':{'src':'https://x/source.mp4'}}
        voice={'id':'v','trackId':'voiceover','props':{'src':'https://x/whole.wav','volume':1}}
        draft={'composition':{'fps':30},'clips':[main,normal,voice,second]}
        result=reflow(draft,tts,texts,{'caption_highlights':{'rules':[{'terms':['天然鲜花精油'],'color':'pink'}]}})
        self.assertEqual([c for c in result['clips'] if c['trackId']!='overlay'],[main,voice])
        self.assertTrue(all(c['props']['font_size']==36 for c in result['clips'] if c['trackId']=='overlay'))
        self.assertTrue(any(c['props'].get('text_color')=='pink' for c in result['clips']))


class ExplicitDisplayChunksTest(unittest.TestCase):
    def test_semantic_punctuation_preserves_real_whole_word_boundaries(self):
        text='洗出柔顺亮泽，随手一撩。'
        items=[{'text':c,'start_ms':i*100,'end_ms':(i+1)*100} for i,c in enumerate(text)]
        cues=semantic_cues({'timeline':{'items':items}},[text],30,max_units=12,chunks=['洗出柔顺亮泽，随手一撩'])
        self.assertEqual(cues[0]['text'],'洗出柔顺亮泽，随手一撩')
        self.assertEqual(norm(cues[0]['text']),norm(text))

if __name__=='__main__':
    unittest.main()

class ReviewedSourceRepairTest(unittest.TestCase):
    def test_safe_substitution_preserves_audio_and_rejects_changed_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            proof=Path(tmp)/'proof.json';proof.write_text('actual source frames')
            voice={'id':'voice','trackId':'voiceover','props':{'src':'voice.wav'}}
            draft={'composition':{'fps':30},'clips':[{'id':'4-0','trackId':'main','durationInFrames':30,'props':{'src':'https://x/old.mp4'}},voice]}
            brief={'segments':[{'id':'4','visual_tags':['texture']} ]}
            library={'new':{'id':'new','tags':['texture'],'review_result':'pass','ai_generated':False,'begin_ms':0,'end_ms':3000,'object_url':'https://x/new.mp4'}}
            changes={'4-0':{'segment_id':'new','startFrom':15,'safe_start_frame':10,'safe_end_frame':60,'evidence_path':str(proof),'evidence_sha256':hashlib.sha256(proof.read_bytes()).hexdigest()}}
            result=patch_reviewed_sources(draft,changes,library,brief)
            self.assertEqual(result['clips'][1],voice);self.assertEqual(draft['clips'][0]['props']['src'],'https://x/old.mp4');self.assertEqual(result['clips'][0]['props']['src'],'https://x/new.mp4')
            proof.write_text('changed')
            with self.assertRaises(RuntimeError):patch_reviewed_sources(draft,changes,library,brief)

class NativeInstanceFitTest(unittest.TestCase):
    def test_native_recipe_kept_and_only_one_default_export_remains(self):
        code='import React from "react"; export default function NativeTitle(props) {return null;}'
        draft={'clips':[{'id':'3-x0','trackId':'effect','props':{'code':code,'params':{'text':'花香出场'}}}]}
        rule={'3-x0':{'source_code_sha256':hashlib.sha256(code.encode()).hexdigest(),'scale':.5,'translate_x':240,'translate_y':-40}}
        actual=fit_native_effect_instances(draft,rule)
        self.assertIn('function NativeTitle(props)',actual['clips'][0]['props']['code']);self.assertEqual(actual['clips'][0]['props']['code'].count('export const Composition'),1);self.assertEqual(actual['clips'][0]['props']['params'],draft['clips'][0]['props']['params']);self.assertEqual(draft['clips'][0]['props']['code'],code)


class NativeMainFramingTest(unittest.TestCase):
    def setup_case(self,tmp,width=1080,height=1920):
        proof=Path(tmp)/'renderer.json';proof.write_text('inspected unit=min(width,height)/1080; translate then scale')
        draft={'composition':{'width':width,'height':height},'clips':[
            {'id':'7-0','trackId':'main','component':'VideoClip','props':{'src':'same.mp4','startFrom':9,'muted':True}},
            {'id':'voice','trackId':'voiceover','component':'AudioClip','props':{'src':'same-whole.wav'}}]}
        rules={'7-0':{'scale':1.3,'x':-140,'y':170,'renderer_evidence_path':str(proof),'renderer_evidence_sha256':hashlib.sha256(proof.read_bytes()).hexdigest()}}
        return draft,rules,proof

    def test_valid_center_transform_retains_source_timing_and_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft,rules,_=self.setup_case(tmp);original=copy.deepcopy(draft);out=frame_main(draft,rules)
            self.assertEqual(draft,original);self.assertEqual(out['clips'][1],original['clips'][1])
            self.assertEqual(out['clips'][0]['props'],dict(original['clips'][0]['props'],scale=1.3,x=-140,y=170))

    def test_scaled_canvas_uses_renderer_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft,rules,_=self.setup_case(tmp,540,960)
            self.assertEqual(frame_main(draft,rules)['clips'][0]['props']['y'],170)
            rules['7-0']['x']=163
            with self.assertRaises(RuntimeError):frame_main(draft,rules)

    def test_changed_evidence_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft,rules,proof=self.setup_case(tmp);proof.write_text('changed')
            with self.assertRaises(RuntimeError):frame_main(draft,rules)

    def test_non_main_or_motion_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft,rules,_=self.setup_case(tmp);draft['clips'][0]['trackId']='effect'
            with self.assertRaises(RuntimeError):frame_main(draft,rules)
            draft['clips'][0]['trackId']='main';draft['clips'][0]['motion']={'type':'zoom'}
            with self.assertRaises(RuntimeError):frame_main(draft,rules)

    def test_non_finite_bool_and_exposed_edges_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft,rules,_=self.setup_case(tmp)
            for field,value in [('x',float('nan')),('scale',True),('scale',1.6),('x',163),('y',289)]:
                candidate=copy.deepcopy(rules);candidate['7-0'][field]=value
                with self.subTest(field=field,value=value),self.assertRaises(RuntimeError):frame_main(draft,candidate)
