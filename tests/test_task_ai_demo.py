import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from task_demo import build,norm
from task_configuration import first_visual_only


class ApprovedAIDemoTest(unittest.TestCase):
    def test_only_approved_opening_is_used_once_and_labeled(self):
        rows=[{'id':'1','text':'等等这是洗发水我还以为在看香氛大片','visual_tags':['AI-1126-new']},{'id':'2','text':'力士精油香氛洗发水','visual_tags':['产品外观']}]
        b={'brief_id':'B','product_key':'p','segments':rows,'aigc_approval':{'by':'用户','tags':['AI-1126-new']}}
        clips=[{'id':str(i)+'-0','trackId':'main','from':(i-1)*90,'durationInFrames':90,'props':{'src':'https://x/'+str(i)+'.mp4'}} for i in [1,2]]
        clips += [{'id':'sub','trackId':'overlay','from':0,'durationInFrames':180,'component':'SubtitleClip','props':{'text':'old'}},{'id':'vo','trackId':'voiceover','from':0,'durationInFrames':180,'props':{'src':'https://x/old.wav'}}]
        draft={'composition':{'fps':30,'durationInFrames':180},'tracks':[],'clips':clips}
        text=''.join(norm(s['text']) for s in rows);tts={'audio':{'duration_ms':len(text)*150},'audio_url':'https://x/whole.wav','timeline':{'items':[{'text':c,'start_ms':i*150,'end_ms':(i+1)*150} for i,c in enumerate(text)]}}
        library=[{'id':str(i),'object_url':'https://x/'+str(i)+'.mp4','begin_ms':0,'end_ms':5000,'tags':rows[i-1]['visual_tags'],'review_result':'pass','ai_generated':i==1} for i in [1,2]]
        with patch('task_demo.cb.banned_words_for',return_value=[]):
            result,evidence=build(b,{'draft':draft},tts,{'config':{'bgm':{'volume':.1}}},library,{'approved_ai_hook_ids':{'B':['1']}},'B-V01')
            self.assertEqual(sum(c['trackId']=='main' and c['props']['src']=='https://x/1.mp4' for c in result['clips']),1)
            self.assertTrue(any(c['trackId']=='watermark' for c in result['clips']))
            with self.assertRaises(RuntimeError):build(b,{'draft':draft},tts,{'config':{'bgm':{'volume':.1}}},library,{},'B-V01')

    def test_refresh_of_current_script_allows_only_first_visual_change(self):
        original={'config':{'voices':['a','b','c']},'segments':[{'visual':{'tags':['old']}},{'visual':{'tags':['real']}}]}
        changed={'config':{'voices':['a','b','c']},'segments':[{'visual':{'ids':['approved']}},{'visual':{'tags':['real']}}]}
        self.assertTrue(first_visual_only(original,changed))
        changed['config']['voices']=['a']
        self.assertFalse(first_visual_only(original,changed))


if __name__=='__main__':unittest.main()
