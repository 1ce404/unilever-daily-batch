#!/usr/bin/env python3
"""Resolve missing/deleted media before submitting another paid render; never print signed queries."""
from urllib.parse import urlparse
import urllib.request,urllib.error

def check_sources(draft,opener=urllib.request.urlopen):
 urls={c.get('props',{}).get('src') for c in draft.get('clips',[]) if c.get('props',{}).get('src','').startswith(('https://','http://'))}
 def one(url):
  issues=[]
  try:
   request=urllib.request.Request(url,method='GET',headers={'Range':'bytes=0-0'})
   with opener(request,timeout=30) as response:
    if response.status not in (200,206):issues.append('媒体不可用 '+urlparse(url).path+' HTTP '+str(response.status))
  except (urllib.error.URLError,TimeoutError,OSError) as e:issues.append('媒体读取失败 '+urlparse(url).path+' '+str(getattr(e,'code',type(e).__name__)))
  return issues
 from concurrent.futures import ThreadPoolExecutor
 with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(one,sorted(urls)))
 return [issue for result in results for issue in result]
