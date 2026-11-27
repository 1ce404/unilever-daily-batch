#!/usr/bin/env python3
"""Read-only current Clipo frontend assets; no API writes or production state changes."""
import argparse,hashlib,json,re,sys,urllib.request
from pathlib import Path
from urllib.parse import urljoin,urlparse
sys.path.insert(0,str(Path(__file__).resolve().parent))
from common import load_config

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--out',required=True);p.add_argument('--path',default='/');a=p.parse_args();cfg=load_config(a.config);base=cfg['clipo']['base_url'].rstrip('/')+'/';out=Path(a.out);out.mkdir(parents=True,exist_ok=True);origin=urlparse(base).netloc
 def get(url):
  if urlparse(url).netloc!=origin:raise RuntimeError('Only current frontend origin allowed')
  with urllib.request.urlopen(url,timeout=30) as r:data=r.read(15000000)
  if len(data)>=15000000:raise RuntimeError('Frontend file exceeded read bound')
  return data
 
 if not a.path.startswith('/') or a.path.startswith('//'):raise RuntimeError('Same-origin entry path required')
 html=get(urljoin(base,a.path)).decode();(out/'index.html').write_text(html);queue=[urljoin(base,x) for x in re.findall(r'(?:src|href)=["\']([^"\']+\.js(?:\?[^"\']*)?)["\']',html)];seen=set();rows=[];matches=[]
 while queue and len(seen)<40:
  url=queue.pop(0)
  if url in seen:continue
  seen.add(url);raw=get(url);code=raw.decode();name=urlparse(url).path.rsplit('/',1)[-1];dest=out/name;dest.write_bytes(raw);rows.append({'url':url,'file':str(dest.resolve()),'sha256':hashlib.sha256(raw).hexdigest()})
  for m in re.finditer('VideoClip',code):matches.append({'file':str(dest),'offset':m.start(),'context':code[max(0,m.start()-1800):m.end()+3000]})
  for part in re.findall(r'["\'](\./[^"\']+\.js)["\']',code):
   if any(w.lower() in part.lower() for w in ['remotion','video','composition','editor','component','render']):queue.append(urljoin(url,part))
 (out/'assets.json').write_text(json.dumps(rows,indent=2));(out/'VideoClip-contexts.json').write_text(json.dumps(matches,ensure_ascii=False,indent=2));print('Read-only frontend files',len(rows),'VideoClip contexts',len(matches),'at',out)
if __name__=='__main__':main()
