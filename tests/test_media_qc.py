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
    draft['clips']=[draft['clips'][0]]
    if color=='blue':draft['clips'].append({'id':'3-x0','trackId':'effect','from':0,'durationInFrames':30,'props':{'params':{'text':'精油润养'}}})
    evidence=inspect(video,draft,root/color,{})
    self.assertEqual(evidence['technical_pass'],color=='blue');self.assertEqual(evidence['visual_review'],'pending');self.assertEqual(len(evidence['frames']),3)
    if color=='black':self.assertTrue(evidence['black_intervals'])
    else:self.assertEqual(evidence['upper_title_sample_count'],3);self.assertTrue(all(Path(x['file']).exists() for x in evidence['upper_title_frames']))
 def test_one_frame_black_at_30fps_is_not_hidden_by_old_35ms_threshold(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);video=root/'single-frame.mp4';graph='color=c=blue:s=108x192:r=30:d=0.333333[a];color=c=black:s=108x192:r=30:d=0.033333[b];color=c=green:s=108x192:r=30:d=0.333333[c];[a][b][c]concat=n=3:v=1:a=0[v]'
   subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','sine=frequency=700:duration=0.7','-filter_complex',graph,'-map','[v]','-map','0:a','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(video)],check=True)
   draft={'composition':{'fps':30,'durationInFrames':21},'clips':[{'id':'main','trackId':'main','from':0,'durationInFrames':21}]};e=inspect(video,draft,root/'qc',{'max_black_interval_seconds':.035});self.assertFalse(e['technical_pass']);self.assertTrue(any(0<x['duration']<=1/30+.001 for x in e['black_intervals']))
if __name__=='__main__':unittest.main()
