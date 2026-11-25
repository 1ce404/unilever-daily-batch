import shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from media_qc import inspect
@unittest.skipUnless(shutil.which('ffmpeg'),'ffmpeg not available')
class RenderedMediaTest(unittest.TestCase):
 def test_real_black_video_is_blocked_and_good_video_needs_visual_review(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);draft={'composition':{'fps':30,'durationInFrames':30},'clips':[{'id':'1-0','trackId':'main','from':0,'durationInFrames':30}]}
   for color in ['black','blue']:
    video=root/(color+'.mp4');subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c='+color+':s=108x192:r=30:d=1','-f','lavfi','-i','sine=frequency=700:duration=1','-shortest','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(video)],check=True)
    evidence=inspect(video,draft,root/color,{})
    self.assertEqual(evidence['technical_pass'],color=='blue');self.assertEqual(evidence['visual_review'],'pending');self.assertEqual(len(evidence['frames']),3)
    if color=='black':self.assertTrue(evidence['black_intervals'])
if __name__=='__main__':unittest.main()
