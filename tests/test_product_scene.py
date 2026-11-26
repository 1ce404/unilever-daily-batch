"""Immutable product image/mask validation and deterministic native composition."""
import copy,hashlib,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from product_scene import validate,spec
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
if __name__=='__main__':unittest.main()
