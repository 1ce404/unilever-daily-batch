"""Immutable product image/mask validation and deterministic native composition."""
import copy,hashlib,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from product_scene import validate,spec,lookup_segment,validate_code,CODE
from unittest.mock import patch
class ProductSceneTest(unittest.TestCase):
 def fixture(self,path):
  Path(path).write_bytes(b'owned-real-frame')
  return {'key':'sample','image':str(path),'image_sha256':hashlib.sha256(b'owned-real-frame').hexdigest(),'source_width':1080,'source_height':1920,'bounds':[300,400,400,1100],'polygon':[[300,400],[500,400],[700,600],[700,1400],[500,1500],[300,1400]],'placement':{'height':700,'bottom':1050,'center_x':360},'source_segment_id':'a'*24,'source_frame_time':0.2,'final_tag':'AI-1126-产品场景'}
 def test_changed_pixels_or_bad_masks_stop(self):
  with tempfile.TemporaryDirectory() as d:
   i=self.fixture(Path(d)/'real.png');validate(i)
   bad=copy.deepcopy(i);bad['polygon'][0][0]=100
   with self.assertRaises(RuntimeError):validate(bad)
   Path(i['image']).write_bytes(b'altered')
   with self.assertRaises(RuntimeError):validate(i)
 def test_native_composition_keeps_background_and_real_pixels_separate(self):
  with tempfile.TemporaryDirectory() as d:
   i=self.fixture(Path(d)/'real.png');s=spec(i,'bg','real');self.assertEqual(s,spec(i,'bg','real'))
   self.assertEqual(s['clips'][0]['props']['src'],'bg');self.assertEqual(s['clips'][1]['props']['params']['src'],'real')
   self.assertEqual(s['clips'][1]['props']['params']['polygon'],i['polygon'])
   self.assertEqual(s['composition']['durationInFrames'],120)
   self.assertNotIn('Text',s['clips'][1]['props']['code'])
 def test_source_lookup_uses_product_list_not_unsupported_get(self):
  with patch('product_scene.cb.list_segments',return_value=[{'id':'source','object_url':'video'}]),patch('product_scene.cb.api',side_effect=AssertionError('No direct segment GET')):
   self.assertEqual(lookup_segment('product','source')['object_url'],'video')
   with self.assertRaises(RuntimeError):lookup_segment('product','missing')
 def test_unsupported_sandbox_export_stops_before_render(self):
  validate_code(CODE)
  with self.assertRaises(RuntimeError):validate_code("import {OffthreadVideo} from 'remotion';")
 def test_real_video_uses_builtin_picture_in_picture(self):
  with tempfile.TemporaryDirectory() as d:
   i=self.fixture(Path(d)/'real.png');i.update(source_mode='video',source_start=3.8,video_src='real-video')
   s=spec(i,'AI-bg','real-photo');self.assertEqual(s['clips'][0]['component'],'SplitScreenClip')
   video=s['clips'][0]['props']['items'][1]['props'];self.assertEqual(video['startFrom'],114);self.assertEqual(video['playbackRate'],1)
   self.assertNotIn('OffthreadVideo',s['clips'][1]['props']['code'])
if __name__=='__main__':unittest.main()
