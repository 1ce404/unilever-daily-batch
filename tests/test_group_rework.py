import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import group_rework_apply as g

def fixture():
 def clip(cid,track,start,n,component='VideoClip',**props):return {'id':cid,'trackId':track,'from':start,'durationInFrames':n,'component':component,'props':props}
 intro=clip('1-0','main',0,20,src='https://example.test/intro.mp4',volume=1,muted=False)
 voice=clip('vo-full','voiceover',20,80,'AudioClip',src='https://example.test/vo.mp3',volume=1)
 sub=clip('s6-0','overlay',80,10,'SubtitleClip',text='戳直播间看这款',font_size=36,top=78)
 main=clip('2-repair-0','main',20,80,src='https://example.test/body.mp4',volume=0,muted=True)
 old={'composition':{'durationInFrames':100},'clips':[intro,main,voice,sub]}
 new=copy.deepcopy(old);new['clips'] += [clip('cta-arrow','sticker',76,24,'StickerClip',x=0,y=0,scale=1,rotation=0),clip('sfx-cta','sound_effects',80,4,'AudioClip')]
 return old,{'key':'B01-V01','shift':0,'draft':new,'assignments':[{'end':82,'physical_end':85}]}

class ReworkContracts(unittest.TestCase):
 def test_valid_and_cover_numeric_normalization(self):
  old,p=fixture();g.validate(old,p);self.assertEqual(g.digest({'x':1,'cover_url':'a'}),g.digest({'x':1.0,'cover_url':'b'}))
 def test_protected_original_dialogue_and_whole_voice(self):
  for cid,field,value in [('1-0','volume',0),('vo-full','src','https://example.test/other.mp3')]:
   old,p=fixture();next(x for x in p['draft']['clips'] if x['id']==cid)['props'][field]=value
   with self.assertRaises(RuntimeError):g.validate(old,p)
 def test_no_fake_dialogue_subtitles_or_shift(self):
  old,p=fixture();next(x for x in p['draft']['clips'] if x['id']=='s6-0')['from']=79
  with self.assertRaises(RuntimeError):g.validate(old,p)
 def test_banned_source_and_duplicate_canonical_paths(self):
  old,p=fixture();p['draft']['clips'][1]['props']['src']='https://example.test'+g.EXCLUDED_PATH
  with self.assertRaises(RuntimeError):g.validate(old,p)
  old,p=fixture();p['draft']['clips'][1]['props']['src']='https://example.test/intro.mp4?different-signature=1'
  with self.assertRaises(RuntimeError):g.validate(old,p)
 def test_no_blank_gap_overrun_or_wrapped_caption(self):
  old,p=fixture();p['draft']['clips'][1]['from']=21
  with self.assertRaises(RuntimeError):g.validate(old,p)
  old,p=fixture();p['assignments'][0]['end']=86
  with self.assertRaises(RuntimeError):g.validate(old,p)
  old,p=fixture();next(x for x in p['draft']['clips'] if x['component']=='SubtitleClip')['props']['font_size']=50
  with self.assertRaises(RuntimeError):g.validate(old,p)
 def test_native_avatar_arrow_must_not_be_moved_to_product_or_blank_corner(self):
  old,p=fixture();next(x for x in p['draft']['clips'] if x['id']=='cta-arrow')['props']['x']=-800
  with self.assertRaises(RuntimeError):g.validate(old,p)
 def test_cta_arrow_and_sound_alignment(self):
  for cid,newfrom in [('cta-arrow',82),('sfx-cta',79)]:
   old,p=fixture();next(x for x in p['draft']['clips'] if x['id']==cid)['from']=newfrom
   with self.assertRaises(RuntimeError):g.validate(old,p)

if __name__=='__main__':unittest.main()
