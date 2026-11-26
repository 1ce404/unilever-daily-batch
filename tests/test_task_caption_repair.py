import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from task_caption_repair import semantic_cues, norm, reflow


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


if __name__=='__main__':
    unittest.main()
