#!/usr/bin/env python3
"""Read-only reference-study inventory. No uploads, generation, shared media or prompt edits."""
import argparse,json,sys,concurrent.futures
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import clipo_batch as cb
from common import load_config

def save(path,data):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2))

def inventory(a,cfg):
 out=Path(a.run)/'参考学习'/'clipo现有包装';out.mkdir(parents=True,exist_ok=True)
 endpoints={'effects':'/resource/remotion-effect-template','stickers':'/resource/sticker','sounds':'/resource/sound-effect','subtitle-styles':'/resource/subtitle-style'}
 def one(item):
  name,path=item;rows=[];skip=0
  for page_no in range(30):
   result=cb.api('GET',path+f'?limit=100&skip={skip}',timeout=30,tries=2);batch=result if isinstance(result,list) else result.get('items',[]);rows.extend(batch)
   if isinstance(result,list) or len(batch)<100 or len(rows)>=result.get('total',len(rows)):break
   skip+=len(batch)
  else:raise RuntimeError('Inventory exceeded bounded pagination '+name)
  save(out/(name+'.json'),rows);print(name,len(rows),[(x.get('id'),x.get('name')) for x in rows][:40],flush=True);return name,rows
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:catalog=dict(pool.map(one,endpoints.items()))
 for name,path in {'sticker-categories':'/resource/sticker/catalog','sound-categories':'/resource/sound-effect/catalog','subtitle-animations':'/resource/subtitle-animation'}.items():save(out/(name+'.json'),cb.api('GET',path,timeout=30,tries=2))
 selected=[x for x in catalog['effects'] if x.get('name') in ['好评弹幕','贴纸爆点','闪光冲击','卖点三连']]
 for meta in selected:save(out/('effect-'+meta['id']+'.json'),cb.api('GET','/resource/remotion-effect-template/'+meta['id'],timeout=30,tries=2))
 print('Read-only native packaging inventory saved',out,flush=True)

def previews(a,cfg):
 from PIL import Image,ImageDraw
 import math
 rows=json.loads(Path(a.inventory_file).read_text());rows=rows if isinstance(rows,list) else rows['items'];rows=[x for x in rows if a.category in (x.get('categories') or [])]
 out=Path(a.run)/'参考学习'/'clipo现有包装'/'原生贴纸实看';out.mkdir(parents=True,exist_ok=True)
 board=Image.new('RGB',(1080,math.ceil(len(rows)/4)*520),'#bdbdbd');draw=ImageDraw.Draw(board);evidence=[]
 for i,x in enumerate(rows):
  path=out/(x['id']+'.gif')
  if not path.exists():cb.fetch_file(x['url'],path)
  im=Image.open(path);im.seek(min(im.n_frames-1,max(0,im.n_frames//2)));frame=im.convert('RGBA');bbox=frame.getbbox();frame.thumbnail((270,480));xx=i%4*270;yy=i//4*520;board.paste(frame,(xx,yy),frame);draw.text((xx+3,yy+480),x['name'].split('-')[-1]+' '+x['id'][-6:],fill='black');evidence.append({'id':x['id'],'name':x['name'],'file':str(path),'actual_midpoint_alpha_bbox':bbox,'canvas':[im.width,im.height]})
 board.save(out/'实际可见区域.jpg');save(out/'可见区域证据.json',evidence);print('Read-only actual native sticker previews',len(rows),out)

def main():
 p=argparse.ArgumentParser();p.add_argument('cmd',choices=['inventory','previews']);p.add_argument('--config',required=True);p.add_argument('--run',required=True);p.add_argument('--inventory-file');p.add_argument('--category');a=p.parse_args();cfg=load_config(a.config);cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';globals()[a.cmd](a,cfg)
if __name__=='__main__':main()
