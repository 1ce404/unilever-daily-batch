#!/usr/bin/env python3
"""[INPUT] User-specified legacy Clipo share group and original native cuts.
[OUTPUT] Immutable source inventory, evidence, narrow repair plans and read-backed revised shares.
[POS] Audit existing shared videos using the LUX quality standard; preserve each product's own facts.
"""
import argparse,copy,hashlib,json,re,sys,urllib.parse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import clipo_batch as cb
from common import load_config
from lux_final_qc import canonical,save

def inventory(a,cfg):
 root=Path(a.out);root.mkdir(parents=True,exist_ok=True);gid=a.group.rstrip('/').rsplit('/',1)[-1]
 if not re.fullmatch('[a-f0-9]{24}',gid):raise RuntimeError('Invalid group ID')
 group=cb.api('GET',f'/video-share-groups/{gid}');save(root/'original-group.json',group);shares=[]
 for item in group['shares']:
  share=cb.api('GET',f"/video-shares/{item['id']}");save(root/'shares'/(item['id']+'.json'),share);shares.append(share);print(share['id'],share['title'],'videos',len(share['videos']),'product',share.get('product_id'),'script',share.get('script_id'),'missing_cut',sum(not v.get('cut_id') for v in share['videos']),flush=True)
 save(root/'inventory.json',{'group_id':gid,'group_name':group['name'],'shares':[{'id':s['id'],'title':s['title'],'product_id':s.get('product_id'),'script_id':s.get('script_id'),'count':len(s['videos'])} for s in shares]});print('Native group',group['name'],'shares',len(shares),'videos',sum(len(s['videos']) for s in shares),flush=True)

def snapshot(a,cfg):
 from concurrent.futures import ThreadPoolExecutor
 root=Path(a.out);inv=json.loads((root/'inventory.json').read_text());items=[]
 for summary in sorted(inv['shares'],key=lambda x:int(re.search(r'brief(\d+)',x['title']).group(1))):
  share=json.loads((root/'shares'/(summary['id']+'.json')).read_text());script=cb.api('GET',f"/video-scripts/{share['script_id']}");save(root/'scripts'/(script['id']+'.json'),script)
  bn=int(re.search(r'brief(\d+)',share['title']).group(1))
  for i,v in enumerate(share['videos'],1):items.append({'key':f'B{bn:02d}-V{i:02d}','share_id':share['id'],'share_title':share['title'],'script_id':script['id'],'product_id':share['product_id'],'original':v})
 for pid in sorted({s['product_id'] for s in inv['shares']}):
  save(root/'products'/(pid+'.json'),cb.api('GET',f'/products/{pid}'));save(root/'products'/(pid+'-prompts.json'),cb.api('GET',f'/products/{pid}/prompts'))
 def one(row):
  cut=cb.api('GET',f"/video-cuts/{row['original']['cut_id']}");save(root/'cuts'/(row['key']+'.json'),cut);d=cut['draft'];obj=urllib.parse.unquote(urllib.parse.urlparse(row['original']['video_url']).path.lstrip('/'));row['share_output_key']=obj;row['current_output_key']=cut.get('output_object_key');row['share_is_current']=obj==cut.get('output_object_key');row['duration']=d['composition']['durationInFrames']/d['composition']['fps'];row['cut_id']=row['original']['cut_id'];return row
 with ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(one,items))
 save(root/'video-manifest.json',rows);print('Current native snapshots',len(rows),'stale share outputs',sum(not r['share_is_current'] for r in rows),flush=True)
 for path in sorted((root/'scripts').glob('*.json')):
  d=json.loads(path.read_text());print('SCRIPT',d['id'],d['title']);print('CONFIG',json.dumps(d.get('config'),ensure_ascii=False)[:2500]);print('SEGMENTS',json.dumps([{k:x.get(k) for k in ['id','type','content','effect','subtitle_style','visual']} for x in d['segments']],ensure_ascii=False)[:6500])
 print('First cut keys',list(json.loads((root/'cuts'/(rows[0]['key']+'.json')).read_text())),flush=True)

def media(a,cfg):
 from concurrent.futures import ThreadPoolExecutor
 from media_qc import inspect
 root=Path(a.out);rows=json.loads((root/'video-manifest.json').read_text());rules={'max_black_interval_seconds':.035}
 def one(row):
  if not row['share_is_current']:return {'video':row['key'],'error':'Share/current draft mismatch; do not assume source timing'}
  out=root/'qc-original'/row['key'];out.mkdir(parents=True,exist_ok=True);video=out/'original.mp4'
  if not video.exists():cb.fetch_file(row['original']['video_url'],video)
  cut=json.loads((root/'cuts'/(row['key']+'.json')).read_text());evidence=out/'media-qc.json'
  if evidence.exists():result=json.loads(evidence.read_text())
  else:result=inspect(video,cut['draft'],out,rules)
  print(row['key'],'technical',result['technical_pass'],'black',result['black_intervals'],'peak',result['peak_dbfs'],flush=True);return {'video':row['key'],'technical_pass':result['technical_pass'],'black':result['black_intervals'],'peak':result['peak_dbfs']}
 with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(one,rows))
 save(root/'original-technical-checks.json',results)

def resources(a,cfg):
 root=Path(a.out);inv=json.loads((root/'inventory.json').read_text())
 for pid in {s['product_id'] for s in inv['shares']}:
  lib=cb.list_segments(pid);save(root/'resources'/(pid+'-segments.json'),lib);print('Product library',pid,len(lib),flush=True)
 for name,path in [('subtitle-styles','/resource/subtitle-style?limit=100'),('effects','/resource/remotion-effect-template?limit=100'),('sfx','/resource/sound-effect?limit=100')]:
  data=cb.api('GET',path);save(root/'resources'/(name+'.json'),data);print(name,len(data if isinstance(data,list) else data.get('items',[])),flush=True)

def boards(a,cfg):
 import math
 from PIL import Image,ImageDraw
 root=Path(a.out);out=root/'原片检查图';out.mkdir(exist_ok=True);done=[]
 for folder in sorted((root/'qc-original').glob('B*-V*')):
  record=folder/'media-qc.json'
  if not record.exists():continue
  d=json.loads(record.read_text());frames=d['frames'];im=Image.new('RGB',(864,math.ceil(len(frames)/6)*276+28),'#eee');draw=ImageDraw.Draw(im);draw.text((4,4),folder.name+' original / each shot start-middle-end',fill='black')
  for i,row in enumerate(frames):
   pic=Image.open(row['file']).convert('RGB');pic.thumbnail((144,256));x=i%6*144;y=i//6*276+28;im.paste(pic,(x,y));draw.text((x+3,y+256),row['clip']+' '+str(round(row['time'],2))+'s',fill='black')
  im.save(out/(folder.name+'.jpg'),quality=90);done.append(folder.name)
 print('Original evidence boards ready',done,flush=True)

def report(a,cfg):
 import html,subprocess
 root=Path(a.out);out=root/'问题清单';out.mkdir(exist_ok=True);issues=[
 ('包装缺失','30条的花字、贴纸、独立音效、转场配置均为0。','补充清扬调性的原生花字/贴纸、卖点与动作音效，按组轮换，逐片检查避让。','B01-V01',16.65),
 ('跨片字幕缺少变化','各组分别只绑定1种主字幕样式，30条均用同一字体；组内没有轮换。','单片统一单行，跨片轮换原生字体与样式，产品名和卖点用短句高亮。','B02-V01',14.15),
 ('同一男士镜头使用过多','25条的洗后段使用同一段白衬衫男士发型展示。','从当前产品库挑选不同人物/造型/角度，片内源路径去重。','B01-V02',20.07),
 ('技术/成分与画面错位','已确认B01-V02、B01-V05、B03-V01、B04-V01：口播讲双重酰胺或SCA，画面却是瓶身背面标签。','保留口播；相应时段换成实际出料、膏体流动或拉丝的质地实拍。','B01-V02',16.73),
 ('香型联想有事实风险','brief3/4的5条均用6秒橘子开场，原片有“柑橘木质香”字样；本产品资料没有该具体香型。','撤下含具体香型字样的素材，用通用清爽氛围与男士洗后画面承接，不新增香型承诺。','B03-V01',3.0),
 ('开场对白缺少原生字幕','前两组25条的8.07秒AI对白开场未配置原生字幕。','保留人物原声，实际转写后补单行字幕，不编台词和时间。','B01-V01',4.03),
 ('产品出场空镜/远景过长','部分产品名时段，瓶子仍在画面边缘或远处；例如B03-V01第11秒。','产品名出现时使用清晰完整的正面瓶身，删去入场前奏。','B03-V01',10.89)]
 cards=[]
 for i,(title,finding,fix,key,t) in enumerate(issues,1):
  dest=out/f'issue-{i}.jpg';subprocess.run(['ffmpeg','-v','error','-y','-ss',str(t),'-i',str(root/'qc-original'/key/'original.mp4'),'-frames:v','1','-vf','scale=432:-2',str(dest)],check=True);cards.append('<article><h2>'+str(i)+'. '+html.escape(title)+'</h2><p>'+html.escape(finding)+'</p><p>例：'+key+' · '+str(t)+'秒</p><img src="issue-'+str(i)+'.jpg"><p><b>修改：</b>'+html.escape(fix)+'</p><a href="../qc-original/'+key+'/original.mp4#t='+str(t)+'">看原片对应位置</a></article>')
 doc='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>清扬30条 · 问题清单与修改记录</title><style>body{font:16px/1.7 system-ui;max-width:1200px;margin:28px auto;padding:0 20px;color:#18342d;background:#f3f7f5}article{background:white;border-radius:12px;padding:20px}h2{font-size:20px}img{width:100%;max-width:320px}a{color:#16704f}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px}</style><h1>清扬旗舰店30条 · 问题清单</h1><p>按LUX制作与自查标准检查；产品信息按清扬控油蓬松洗发水核对。以下为当前已确认的问题与原片时间点，逐条画面复核继续进行。原链接与原成片保留。</p><p>30条原片黑帧和音频峰值检查均通过；声音审听、画面内容及包装问题独立核对。</p><div class="grid">'+''.join(cards)+'</div></html>';(out/'index.html').write_text(doc);save(out/'issues-initial.json',[{'id':i,'title':t,'finding':f,'fix':fix,'example_video':key,'time_seconds':sec} for i,(t,f,fix,key,sec) in enumerate(issues,1)]);print(out/'index.html',flush=True)

EXCLUDED_ID='6ae4757d3f3e55b93ac64d4e'
EXCLUDED_PATH='/materials/d3d465407a0f4a828279946030cace40.mp4'
def pool(a,cfg):
 import subprocess,math
 from concurrent.futures import ThreadPoolExecutor
 from PIL import Image,ImageDraw
 root=Path(a.out);lib=json.loads(next((root/'resources').glob('*-segments.json')).read_text());rows=[x for x in lib if x.get('review_result')=='pass' and x['id']!=EXCLUDED_ID and x.get('source_segment_id')!=EXCLUDED_ID and urllib.parse.urlparse(x.get('object_url','')).path!=EXCLUDED_PATH]
 def choose(tag,exclude='',limit=100):
  items=[x for x in rows if tag in x.get('tags',[]) and (not exclude or not re.search(exclude,x.get('name','')+' '+x.get('description','')))];seen=set();out=[]
  for x in items:
   path=urllib.parse.urlparse(x['object_url']).path
   if path in seen:continue
   seen.add(path);out.append(x)
  return out[:limit]
 pools={'pain':choose('头皮出油','学生|少年|教室|校园|男大|骑行|头盔|程序员|稀疏|大面积',12),'after':choose('使用后','情侣|橘子|手绘边框|黄色向下箭头',20),'product':choose('产品外观','背面|背部|入水|掉入|浸没|水下|口播|对着镜头讲话',16),'texture':choose('产品质地','背面|背部|功效细节',20),'cta':choose('CTA','移出画面|掉入|入水|浸没|水下|背面|口播|对着镜头讲话',16),'fresh':choose('AI清爽香味','橙|橘|青柠|字幕|文字|花',8)}
 allmeta={urllib.parse.urlparse(x['object_url']).path:x for values in pools.values() for x in values};save(root/'source-exclusion.json',{'ids':[EXCLUDED_ID],'canonical_paths':[EXCLUDED_PATH],'user_instruction':'这个素材的出镜率我说实话实在太高我快看吐了','scope':'当前30条全部撤掉；不删除共享素材。按路径与source_segment_id排除派生切片。'});save(root/'pool-candidates.json',pools)
 out=root/'候选素材复核';out.mkdir(exist_ok=True)
 def one(item):
  path,meta=item;stem=hashlib.sha256(path.encode()).hexdigest()[:18];file=out/(stem+'.mp4');record=out/(stem+'.json')
  if not file.exists():cb.fetch_file(meta['object_url'],file)
  if record.exists():return json.loads(record.read_text())
  info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(file)],text=True));duration=float(info['format']['duration']);im=Image.new('RGB',(480,246),'#eee');draw=ImageDraw.Draw(im)
  for i,f in enumerate([.08,.36,.66,.92]):
   dest=out/(stem+f'-{i}.jpg');subprocess.run(['ffmpeg','-v','error','-y','-ss',str(duration*f),'-i',str(file),'-frames:v','1','-vf','scale=120:-2',str(dest)],check=True);pic=Image.open(dest).convert('RGB');pic.thumbnail((120,214));im.paste(pic,(i*120,0));draw.text((i*120+3,217),str(round(duration*f,2))+'s',fill='black')
  draw.text((3,232),meta['id']+' '+str(round(duration,2))+'s',fill='black');sheet=out/(stem+'.jpg');im.save(sheet,quality=90);data={'id':meta['id'],'path':path,'duration':duration,'video':str(file),'sheet':str(sheet),'safe_start':1,'safe_end':max(1,math.floor(duration*30)-1)};save(record,data);return data
 with ThreadPoolExecutor(max_workers=6) as executor:measurements=list(executor.map(one,allmeta.items()))
 save(root/'pool-windows.json',{x['path']:x for x in measurements});byid={x['id']:x for x in measurements}
 for name,items in pools.items():
  for part in range(math.ceil(len(items)/6)):
   chunk=items[part*6:part*6+6];board=Image.new('RGB',(480,len(chunk)*246+25),'#ddd');ImageDraw.Draw(board).text((4,4),name+' '+str(part+1),fill='black')
   for i,x in enumerate(chunk):board.paste(Image.open(byid[x['id']]['sheet']),(0,i*246+25))
   board.save(out/(name+'-'+str(part+1)+'.jpg'),quality=90)
 print('Candidate native sources measured',len(measurements),'by role',{k:len(v) for k,v in pools.items()},flush=True)

def graphics(a,cfg):
 import subprocess
 from PIL import Image
 root=Path(a.out);out=root/'resources';fonts=cb.api('GET','/resource/font?limit=100');save(out/'fonts.json',fonts);print('Font library',json.dumps(fonts,ensure_ascii=False)[:4500])
 for tid in ['6afd05026af35ab34a0a5cb7','6afd05036af35ab34a0a5cc0','6afd05026af35ab34a0a5cbd','6afd05036af35ab34a0a5cc4']:
  save(out/(tid+'.json'),cb.api('GET','/resource/remotion-effect-template/'+tid))
 stickers=cb.api('POST','/resource/sticker/search',{'catalog':'戳直播间头像-箭头','limit':12});save(out/'cta-stickers.json',stickers);print('Sticker search',str(stickers)[:1800])
 sounds=json.loads((out/'sfx.json').read_text());sounds=sounds if isinstance(sounds,list) else sounds['items'];wanted=['6ae438bacb6a6ec6552f8669','6ae4389ccb6a6ec6552f85c7','6ae43893cb6a6ec6552f857b','6ae4386ccb6a6ec6552f8422','6ae43849cb6a6ec6552f833a'];bank=[]
 for x in sounds:
  if x['id'] not in wanted:continue
  url=x.get('url') or x.get('object_url');file=out/(x['id']+'.mp3');cb.fetch_file(url,file);info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-of','json',str(file)],text=True));bank.append({**x,'file':str(file),'actual_duration':float(info['format']['duration'])})
 save(out/'verified-sfx.json',bank);print('Verified current Clipo short SFX',[(x['id'],x['name']) for x in bank],flush=True)

def sound_and_stickers(a,cfg):
 import subprocess,math
 from concurrent.futures import ThreadPoolExecutor
 from PIL import Image,ImageDraw
 root=Path(a.out);out=root/'resources';manifest=json.loads((root/'video-manifest.json').read_text());sources={}
 for row in manifest:
  d=json.loads((root/'cuts'/(row['key']+'.json')).read_text())['draft']
  for c in d['clips']:
   if c['trackId'] in ['voiceover','audio']:
    url=c['props']['src'];sources[urllib.parse.urlparse(url).path]=url
 def one(item):
  path,url=item;stem=hashlib.sha256(path.encode()).hexdigest()[:18];file=out/'audio-source'/(stem+Path(path).suffix);record=file.with_suffix('.json');file.parent.mkdir(exist_ok=True)
  if not file.exists():cb.fetch_file(url,file)
  if record.exists():return json.loads(record.read_text())
  info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-of','json',str(file)],text=True));p=subprocess.run(['ffmpeg','-hide_banner','-i',str(file),'-af','volumedetect','-f','null','-'],capture_output=True,text=True,check=True);peak=re.search(r'max_volume: ([-\d.]+) dB',p.stderr);mean=re.search(r'mean_volume: ([-\d.]+) dB',p.stderr);result={'path':path,'file':str(file),'duration':float(info['format']['duration']),'peak_db':float(peak.group(1)) if peak else None,'mean_db':float(mean.group(1)) if mean else None};save(record,result);return result
 with ThreadPoolExecutor(max_workers=6) as pool:audio=list(pool.map(one,sources.items()))
 save(out/'audio-levels.json',{x['path']:x for x in audio})
 data=json.loads((out/'cta-stickers.json').read_text());verified=[];samples=[]
 for match in data['items'][:8]:
  x=match['sticker'];file=out/(x['id']+'.gif');cb.fetch_file(x['url'],file);im=Image.open(file);bbox=None;elapsed=0;first_visible=None
  for i in range(im.n_frames):
   im.seek(i);rgba=im.convert('RGBA');box=rgba.getchannel('A').getbbox()
   if box:
    if first_visible is None:first_visible=elapsed
    bbox=box if bbox is None else (min(bbox[0],box[0]),min(bbox[1],box[1]),max(bbox[2],box[2]),max(bbox[3],box[3]))
    if i==min(5,im.n_frames-1):samples.append((x['id'],rgba.copy()))
   elapsed+=im.info.get('duration',100)
  if not bbox or first_visible is None or first_visible>400:continue
  verified.append({**x,'file':str(file),'visible_bbox':bbox,'first_visible_ms':first_visible})
 save(out/'verified-stickers.json',verified);board=Image.new('RGB',(240*4,450*math.ceil(len(samples)/4)),'#d8e0dc');draw=ImageDraw.Draw(board)
 for i,(sid,pic) in enumerate(samples):
  pic.thumbnail((240,420));xx=i%4*240;yy=i//4*450;board.paste(pic,(xx,yy),pic);draw.text((xx+2,yy+422),sid[-6:],fill='black')
 board.save(out/'sticker-preview.jpg',quality=90);print('Existing audio measured',len(audio),'available native CTA stickers',len(verified),flush=True)

def decisions(a,cfg):
 root=Path(a.out);save(root/'user-decisions.json',{'original_dialogue':'继续仅用Clipo，保留开场原声暂不补对白字幕','source_exclusion':'这个素材的出镜率我说实话实在太高我快看吐了','ending':'结尾的箭头和音效别忘了','scope':'仅当前30条检查修订；沿用清扬事实、原口播与前两组开场原声，不调用外部转写或新生成配音。'})
 p=root/'问题清单/index.html';t=p.read_text().replace('逐条画面复核继续进行','30条逐镜头首中尾已复核').replace('实际转写后补单行字幕，不编台词和时间。','按用户明确选择：保留开场原声，暂不补对白字幕；不调用外部转写、不编台词和时间。').replace('25条的洗后段使用同一段白衬衫男士发型展示。','25条的洗后段使用同一段白衬衫男士发型展示，另外5条在前段也使用该素材；本批全部撤下。');p.write_text(t)
 save(root/'original-review-ledger.json',{'reviewed':[x['key'] for x in json.loads((root/'video-manifest.json').read_text())],'method':'实际逐条查看每镜首中尾检查图，结合当前工程、素材元数据和技术检查；不是以平台通过代替画面复核。','shared_issues':['包装缺失','组内主字幕不轮换','被用户指出素材覆盖全部30条','部分产品/成分画面错配','5条开场柑橘木质香字样']});print('User decisions and full original visual inspection recorded')

def main():
 p=argparse.ArgumentParser();p.add_argument('cmd',choices=['inventory','snapshot','media','resources','boards','report','pool','graphics','sound_and_stickers','decisions']);p.add_argument('--config',default='lux');p.add_argument('--group');p.add_argument('--out',required=True);a=p.parse_args();cfg=load_config(a.config);cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';globals()[a.cmd](a,cfg)
if __name__=='__main__':main()
