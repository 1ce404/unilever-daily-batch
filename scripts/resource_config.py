#!/usr/bin/env python3
"""Task-scoped Clipo resource discovery and script configuration. No generation or shared prompt edits."""
import argparse,json,sys,urllib.request,urllib.error
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from common import load_config
import clipo_batch as cb

def schema(args,cfg):
    base=cfg['clipo']['base_url'].rstrip('/')
    out=Path(args.run)/'clipo'/'resources';out.mkdir(parents=True,exist_ok=True)
    found=None
    for suffix in ['/openapi.json','/api/openapi.json','/api/v1/openapi.json']:
        try:
            with urllib.request.urlopen(base+suffix,timeout=30) as r:data=json.load(r)
            if 'paths' in data:
                found=data;(out/'openapi.json').write_text(json.dumps(data,ensure_ascii=False));print('OpenAPI:',base+suffix);break
        except (ValueError,urllib.error.URLError,TimeoutError) as e:print('Schema unavailable:',suffix,type(e).__name__)
    if found:
        for k,v in found['paths'].items():
            if any(w in k.lower() for w in ['voice','speaker','subtitle','transition','effect','bgm','music','resource']):
                print(json.dumps({'path':k,'methods':list(v),'summary':{m:x.get('summary') for m,x in v.items() if isinstance(x,dict)}},ensure_ascii=False))
    else:raise SystemExit('Clipo OpenAPI not exposed at standard routes; no write performed.')

def inventory(args,cfg):
    cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';cb.CFG=cfg
    out=Path(args.run)/'clipo'/'resources';out.mkdir(parents=True,exist_ok=True)
    endpoints={'script-generations':'/video-script-generations?limit=100','scripts-jingyou':'/video-scripts?product_id='+cfg['products']['jingyou']['clipo_product_id']+'&limit=100','scripts-spray':'/video-scripts?product_id='+cfg['products']['spray']['clipo_product_id']+'&limit=100','subtitle-styles':'/resource/subtitle-style?limit=100','transitions':'/resource/transition','effects':'/resource/remotion-effect-template?limit=100','bgm-catalog':'/resource/bgm/catalog','subtitle-animations':'/resource/subtitle-animation'}
    for name,path in endpoints.items():
        data=cb.api('GET',path);(out/(name+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2))
        arr=data if isinstance(data,list) else data.get('items',[])
        print(name, 'items',len(arr),'total',data.get('total') if isinstance(data,dict) else len(arr))
    for i,q in enumerate(['普通话 女声 自然亲切 姐妹分享 轻快清晰 香氛种草','普通话 女声 温柔知性 有质感 时尚 香氛广告','普通话 女声 年轻明亮 自信活泼 日常推荐 不夸张'],1):
        data=cb.api('POST','/tts/speaker/search',{'query':q,'platform':'volcengine','gender':'女','include_disabled':False,'limit':30})
        (out/f'voices-{i}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
        print('voice-search',i,'saved')

def recover(args,cfg):
    cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';cb.CFG=cfg
    out=Path(args.run)/'clipo'/'script-snapshots';out.mkdir(parents=True,exist_ok=True)
    generations=cb.api('GET','/video-script-generations?limit=100')['items']
    st=cb.State(args.run)
    for b in cb.load_briefs(args.run):
        if b['brief_id'] not in ['1125-L01','1125-L02','1125-L03','1125-L05']:continue
        matched=[g for g in generations if g.get('product_id')==cfg['products'][b['product_key']]['clipo_product_id'] and all(x['text'] in g.get('requirement','') for x in b['segments'])]
        if len(matched)!=1:raise SystemExit(f"{b['brief_id']} matched generations {len(matched)}; refusing duplicate or ambiguous recovery")
        g=matched[0]
        if g['status']!='success':print(b['brief_id'],'generation',g['id'],g['status']);continue
        d=cb.api('GET',f"/video-scripts/{g['script_id']}")
        if d.get('ai_status')=='generating':raise SystemExit('Still generating; no update')
        (out/f"{b['brief_id']}-recovered.json").write_text(json.dumps(d,ensure_ascii=False,indent=2))
        job=st.data.setdefault('briefs',{}).setdefault(b['brief_id'],{})
        if job.get('generation_id'):raise SystemExit('Video generation already exists; recovery will not replace it')
        job.update(script_id=g['script_id'],script_generation_id=g['id'],texts=[x['text'] for x in b['segments']],stage='script_ready')
        st.save()
        print(json.dumps({'brief':b['brief_id'],'script_id':g['script_id'],'copy_exact':[x.get('content') for x in d['segments']]==job['texts'],'tts':d.get('config',{}).get('tts'),'config_keys':list(d.get('config',{})),'first_segment':d['segments'][0]},ensure_ascii=False)[:3200])

def configure(args,cfg):
    import copy,hashlib
    cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';cb.CFG=cfg
    plan=json.loads(Path(args.plan).read_text())
    voices=[{k:v[k] for k in ['id','platform','voice_type','config']} for v in plan['voices']]
    if len({v['voice_type'] for v in voices})<3:raise SystemExit('用户禁止仅1或2个音色，拒绝配置')
    st=cb.State(args.run);root=Path(args.run)/'clipo'/'voice-packaging-correction';root.mkdir(parents=True,exist_ok=True)
    for b in cb.load_briefs(args.run):
        bid=b['brief_id']
        if bid not in plan['profiles']:continue
        job=(st.data.get('briefs') or {}).get(bid) or {};sid=job.get('script_id')
        if not sid:raise SystemExit(f'{bid} no recovered script; not creating duplicates')
        if job.get('generation_id'):raise SystemExit(f'{bid} video generation exists; need explicit revision handling')
        before=cb.api('GET',f'/video-scripts/{sid}')
        if before.get('ai_status')=='generating':raise SystemExit('Script is generating; refusing overwrite')
        if [x.get('content') for x in before['segments']]!=[x['text'] for x in b['segments']]:raise SystemExit('Approved copy mismatch')
        profile=plan['profiles'][bid];d=copy.deepcopy(before)
        d['config']['tts']['voices']=voices
        d['config'].setdefault('subtitle_style',{}).update(style_ids=profile['subtitle_style_ids'],animations=profile['subtitle_animations'])
        d['config']['bgm']={'mode':'category','volume':cfg.get('bgm_volume',0.2),'props':{'categories':profile['bgm_categories'],'ids':[]}}
        effects={x['segment_id']:x for x in profile['effects']}
        transitions=profile['transition_candidates']
        for seg in d['segments']:
            if profile.get('sound_effects') and seg['id'] in profile['sound_effects']:
                seg['sound_effects']=copy.deepcopy(profile['sound_effects'][seg['id']])
            if seg.get('tts') and seg['tts'].get('voice'):seg['tts']['voice']=None
            seg['effect']=({k:effects[seg['id']][k] for k in ['template_id','params']} if seg['id'] in effects else None)
            seg['transition']=copy.deepcopy(transitions if seg['id'] in ['2','5'] else [x for x in transitions if x['type']=='quickFade'] if seg['id']=='4' else [])
        if [x.get('content') for x in d['segments']]!=[x.get('content') for x in before['segments']]:raise SystemExit('Copy changed unexpectedly')
        if [x.get('visual') for x in d['segments']]!=[x.get('visual') for x in before['segments']]:raise SystemExit('Visual sources changed unexpectedly')
        if args.dry_run:
            print(json.dumps({'brief':bid,'script_id':sid,'voice_candidates':len(voices),'subtitle_styles':len(profile['subtitle_style_ids']),'bgm_categories':profile['bgm_categories'],'effect_nodes':len(effects),'copy_unchanged':True},ensure_ascii=False));continue
        backup=root/f'{bid}-before.json'
        if not backup.exists():backup.write_text(json.dumps(before,ensure_ascii=False,indent=2))
        body={k:d[k] for k in ['product_id','title','video','config','vo_rewrite_prompt','segments']}
        (root/f'{bid}-intended.json').write_text(json.dumps(body,ensure_ascii=False,indent=2))
        cb.api('PUT',f'/video-scripts/{sid}',body)
        after=cb.api('GET',f'/video-scripts/{sid}')
        (root/f'{bid}-after.json').write_text(json.dumps(after,ensure_ascii=False,indent=2))
        if any(after.get(k)!=body.get(k) for k in body):raise SystemExit(f'{bid} readback differs; stopped')
        job['voice_packaging_revision']=1;job['voice_candidate_count']=len(voices);job['packaging_plan_hash']=hashlib.sha256(Path(args.plan).read_bytes()).hexdigest();st.data['briefs'][bid]=job;st.save()
        print(bid,sid,'verified: voices',len(voices),'styles',len(profile['subtitle_style_ids']),'effect nodes',len(effects),'copy unchanged')

def preview_aigc(args,cfg):
    import subprocess
    import aigc_gen
    cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';cb.CFG=cfg
    r=aigc_gen.Run(cfg,args.plan);out=Path(args.run)/'aigc'/'preview';out.mkdir(parents=True,exist_ok=True)
    for key,slot in r.st['slots'].items():
        if not slot.get('segment_id'):continue
        g=cb.api('GET',f"/material-generations/{slot['attempts'][-1]['generation_id']}")
        url=g.get('object_url')
        if not url:raise SystemExit('Ready generation has no signed media URL')
        video=out/f'{key}.mp4';sheet=out/f'{key}-frames.jpg'
        cb.fetch_file(url,video)
        subprocess.run(['ffmpeg','-v','error','-y','-i',str(video),'-vf','fps=1/2,scale=270:-1,tile=3x1','-frames:v','1',str(sheet)],check=True)
        print(json.dumps({'segment_id':slot['segment_id'],'video':str(video.resolve()),'frames':str(sheet.resolve()),'status':'ready_for_user_review_not_approved'},ensure_ascii=False))

def create_draft(args,cfg):
    import copy
    cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';cb.CFG=cfg
    bid=args.brief;b=next(x for x in cb.load_briefs(args.run) if x['brief_id']==bid)
    if not b.get('configuration_authorization'):raise SystemExit('Missing user authorization to configure the draft')
    st=cb.State(args.run);pid=cfg['products'][b['product_key']]['clipo_product_id'];title=cb.video_title(cfg,b)
    rows=cb.api('GET',f'/video-scripts?product_id={pid}&limit=100')['items']
    found=[x for x in rows if x.get('title')==title]
    if len(found)>1:raise SystemExit('Ambiguous existing draft, no creation')
    if found:
        d=cb.api('GET',f"/video-scripts/{found[0]['id']}")
        if [x.get('content') for x in d['segments']]!=[x['text'] for x in b['segments']]:raise SystemExit('Existing draft copy mismatch')
    else:
        template_id=st.data['briefs']['1125-L03']['script_id'];template=cb.api('GET',f'/video-scripts/{template_id}')
        body={k:copy.deepcopy(template[k]) for k in ['product_id','title','video','config','vo_rewrite_prompt','segments']};body.update(product_id=pid,title=title)
        body['segments']=[]
        for sg in b['segments']:
            item=copy.deepcopy(template['segments'][0]);item.update(id=sg['id'],content=sg['text'],effect=None,transition=[],sound_effects=None,tts=None,sticker=None)
            item['visual']={'mode':'material_segment','props':{'product_id':pid,'tags':sg['visual_tags']}}
            body['segments'].append(item)
        body['vo_rewrite_prompt']=cb.alt_prompt(b['segments'],cfg['products'][b['product_key']])[:2000]
        out=Path(args.run)/'clipo'/'draft-configuration';out.mkdir(parents=True,exist_ok=True)
        intent=out/f'{bid}-intent.json'
        if intent.exists() and not (out/f'{bid}-created.json').exists():raise SystemExit('Prior draft create result uncertain; reconcile before retry')
        intent.write_text(json.dumps(body,ensure_ascii=False,indent=2))
        d=cb.api('POST','/video-scripts',body,tries=1)
        (out/f'{bid}-created.json').write_text(json.dumps(d,ensure_ascii=False,indent=2))
    actual=cb.api('GET',f"/video-scripts/{d['id']}")
    if [x.get('content') for x in actual['segments']]!=[x['text'] for x in b['segments']]:raise SystemExit('Draft readback copy mismatch')
    st.data.setdefault('briefs',{}).setdefault(bid,{}).update(script_id=d['id'],texts=[x['text'] for x in b['segments']],configuration_only=True)
    st.save();print(bid,d['id'],'draft configured and verified; user approval remains pending')

def retire_aigc(args,cfg):
    import aigc_gen,copy
    cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';cb.CFG=cfg
    r=aigc_gen.Run(cfg,args.plan)
    for key,slot in r.st['slots'].items():
        if slot.get('retired_by_user'):continue
        if slot.get('segment_id'):
            r.st.setdefault('rejected',{}).setdefault(key,[]).append(copy.deepcopy(slot));r.save()
            cb.api('DELETE',f"/materials/segments/{slot['segment_id']}",tries=1)
            slot['retired_segment_id']=slot.pop('segment_id')
        slot.update(retired_by_user='好丑，重生',gave_up='用户弃用旧版；由新的六方向计划替代，不自动重试旧构思')
        r.save();print(key,'retired from candidate library; original history preserved')

def review_bundle(args,cfg):
    import html,datetime
    import aigc_gen
    from zoneinfo import ZoneInfo
    cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';cb.CFG=cfg
    run=Path(args.run).resolve();out=run/'验收';out.mkdir(parents=True,exist_ok=True)
    plan=aigc_gen.Run(cfg,args.plan);st=cb.State(run);cards=[];scripts=[];receipt={'review_date':'2026-11-25上午','updated_at':datetime.datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'sample_status':[],'scripts':[]}
    esc=lambda x:html.escape(str(x))
    for b in cb.load_briefs(run):
        job=(st.data.get('briefs') or {}).get(b['brief_id']) or {};sid=job.get('script_id')
        if not sid:continue
        actual=cb.api('GET',f'/video-scripts/{sid}');exact=[x.get('content') for x in actual['segments']]==[x['text'] for x in b['segments']]
        if not exact:raise RuntimeError('系统文案回读不一致：'+b['brief_id'])
        vc=len(actual['config']['tts']['voices']);styles=len(actual['config']['subtitle_style']['style_ids']);sfx=sum(bool(x.get('sound_effects')) for x in actual['segments'])
        if vc<3:raise RuntimeError('音色候选被缩为1或2个：'+b['brief_id'])
        receipt['scripts'].append({'brief':b['brief_id'],'script_id':sid,'copy_exact':exact,'voices':vc,'subtitle_styles':styles,'sfx_nodes':sfx})
        copy=''.join('<p>'+esc(x['text'])+'</p>' for x in b['segments'])
        scripts.append('<details><summary>'+esc(b['brief_id']+' · '+b['direction'])+'</summary><div class="meta">系统脚本已配置 · '+str(vc)+'个音色候选 · '+str(styles)+'种字幕候选</div>'+copy+'</details>')
    for sc in plan.plan['scenes']:
        key=sc['id']+'-01';slot=plan.st['slots'][key];ready=bool(slot.get('segment_id'));video=run/'aigc'/'preview'/f'{key}.mp4';frame=run/'aigc'/'preview'/f'{key}-frames.jpg'
        status='候选样片，等待验收' if ready and video.exists() else '生成中' if plan.status(slot) in ['inflight','todo','retry','tagging'] else '生成失败，待查看原因'
        media=('<video controls playsinline preload="metadata" poster="../aigc/preview/'+esc(frame.name)+'" src="../aigc/preview/'+esc(video.name)+'"></video>') if ready and video.exists() else '<div class="pending">'+esc(status)+'</div>'
        cards.append('<article><h2>'+esc(sc.get('title') or sc['id'])+'</h2>'+media+'<p class="meta">5秒 · '+esc(status)+'</p></article>')
        receipt['sample_status'].append({'id':sc['id'],'title':sc.get('title'),'state':plan.status(slot),'preview_ready':ready and video.exists(),'segment_id':slot.get('segment_id'),'error':slot.get('gave_up')})
    doc='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LUX 11月25日上午验收</title><style>body{font:16px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;background:#f8f4ef;color:#302824;margin:0;padding:28px;max-width:1180px;margin:auto}h1{font-size:28px}h2{font-size:19px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:20px}article,details{background:white;border:1px solid #e8ddd0;border-radius:14px;padding:18px}video{width:100%;max-height:540px;background:#eee5dc;border-radius:10px}.meta{font-size:13px;color:#75675b}.pending{height:280px;display:grid;place-items:center;background:#f2ebe4}details{margin-top:12px}summary{cursor:pointer;font-weight:600}p{margin:8px 0}</style><h1>LUX · 11月25日上午验收</h1><p>6个AI钩子方向，各1条5秒样片。旧版已弃用。选择喜欢的方向后，再进入对应demo。</p><p class="meta">样片与系统脚本均待你验收；候选音色和包装组合不代表实际成片已检查通过。</p><div class="grid">'+''.join(cards)+'</div><h2>5组系统脚本</h2>'+''.join(scripts)+'</html>'
    (out/'index.html').write_text(doc);(out/'验收清单.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    print('Review bundle:',out/'index.html','previews',sum(x['preview_ready'] for x in receipt['sample_status']),'/6; scripts',len(receipt['scripts']))

def overnight_review(args,cfg):
    import subprocess
    root=Path(__file__).resolve().parent
    command=[sys.executable,'-I',str(root/'aigc_gen.py'),'poll','--config',args.config,'--run',args.run,'--plan',args.plan,'--wait','21600','--interval','45']
    result=subprocess.run(command,check=False)
    print('Generation polling returned',result.returncode,flush=True)
    try:preview_aigc(args,cfg)
    finally:review_bundle(args,cfg)

def options_board(args,cfg):
    import subprocess,json
    run=Path(args.run).resolve();plan=json.loads(Path(args.plan).read_text());out=run/'验收';out.mkdir(parents=True,exist_ok=True)
    inputs=[];filters=[];files=[]
    for i,sc in enumerate(plan['scenes']):
        video=run/'aigc'/'preview'/(sc['id']+'-01.mp4')
        if not video.exists():raise SystemExit('Missing actual sample: '+str(video))
        inputs+=['-ss','2','-i',str(video)]
        filters.append(f"[{i}:v]scale=270:480,setsar=1[v{i}]")
        files.append({'option':i+1,'title':sc.get('title'),'video':str(video)})
    filters.append(''.join(f'[v{i}]' for i in range(len(files)))+'xstack=inputs=6:layout=0_0|270_0|540_0|0_480|270_480|540_480[board]')
    dest=out/'AI六选项对照.jpg'
    subprocess.run(['ffmpeg','-v','error','-y']+inputs+['-filter_complex',';'.join(filters),'-map','[board]','-frames:v','1',str(dest)],check=True)
    (out/'AI选项编号.json').write_text(json.dumps(files,ensure_ascii=False,indent=2));print(dest)

def ui_links(args,cfg):
    import re,urllib.parse
    base=cfg['clipo']['base_url'].rstrip('/')
    out=Path(args.run)/'clipo'/'ui-route-evidence';out.mkdir(parents=True,exist_ok=True)
    page=urllib.request.urlopen(base+(args.ui_path or ''),timeout=30).read().decode()
    (out/'index.html').write_text(page)
    sources=re.findall(r'<script[^>]+src=["\']([^"\']+)',page)
    print('Observed HTML links:',re.findall(r'href="([^"]+)"',page));print('Observed script sources:',sources)
    for i,source in enumerate(sources):
        url=urllib.parse.urljoin(base+'/',source)
        if urllib.parse.urlparse(url).netloc!=urllib.parse.urlparse(base).netloc:continue
        js=urllib.request.urlopen(url,timeout=60).read().decode();(out/f'entry-{i}.js').write_text(js)
        for match in list(re.finditer(r'(?:path|to|href):["\'][^"\']*(?:product|script)[^"\']*["\']',js))[:40]:
            print(js[max(0,match.start()-70):min(len(js),match.end()+100)])
        # Also print nearby observed product paths used in template literals.
        for match in list(re.finditer(r'/products?[/?:][^\s,;]{0,110}',js))[:25]:print(js[max(0,match.start()-40):match.end()+60])

def product_ui_links(args,cfg):
    import urllib.parse
    base=cfg['clipo']['base_url'].rstrip('/');cb.CFG=cfg;st=cb.State(args.run)
    products=[];scripts=[]
    for key in ['jingyou','spray']:
        prod=cfg['products'][key];pid=prod['clipo_product_id']
        row={'product':prod['full_name'],'product_id':pid,'info_url':base+'/dashboard/products/info?product='+pid,'scripts_url':base+'/dashboard/products/materials/scripts?product='+pid}
        with urllib.request.urlopen(row['scripts_url'],timeout=30) as response:row['http_status']=response.status
        products.append(row)
    for b in cb.load_briefs(args.run):
        sid=(st.data.get('briefs',{}).get(b['brief_id']) or {}).get('script_id')
        if sid:scripts.append({'brief':b['brief_id'],'url':base+'/dashboard/products/materials/scripts/'+sid+'?product='+cfg['products'][b['product_key']]['clipo_product_id']})
    doc={'route_evidence':'已读取Clipo前端实际products路径常量与?product=参数构造函数','products':products,'scripts':scripts}
    (Path(args.run)/'clipo/product-ui-links.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2))
    print(json.dumps(doc,ensure_ascii=False,indent=2))

def demo_layout(args,cfg):
    import copy
    cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';cb.CFG=cfg
    st=cb.State(args.run);out=Path(args.run)/'clipo'/'demo-layout';out.mkdir(parents=True,exist_ok=True)
    styles={'1125-L01':('6ab0980646ba4a9c28773861',{'code':'inAnim-flickRight','name':'向右弹入'}),'1125-L02':('6ab0980646ba4a9c28773860',{'code':'float_up_animation','name':'向上浮出'}),'1125-L03':('6ab0980646ba4a9c28773859',None),'1125-L04':('6ab0980646ba4a9c2877385b',{'code':'inAnim-typewriter','name':'打字机入'}),'1125-L05':('6ab0980646ba4a9c28773857',{'code':'float_up_animation','name':'向上浮出'})}
    for b in cb.load_briefs(args.run):
        bid=b['brief_id'];job=st.data['briefs'][bid]
        if job.get('generation_id'):raise SystemExit('Generation exists; refusing demo reconfiguration')
        sid=job['script_id'];before=cb.api('GET',f'/video-scripts/{sid}')
        if [x['content'] for x in before['segments']]!=[x['text'] for x in b['segments']]:raise SystemExit('Copy mismatch')
        if len(before['config']['tts']['voices'])<3:raise SystemExit('User prohibits one/two-voice pool')
        d=copy.deepcopy(before);style,animation=styles[bid]
        d['config']['subtitle_style'].update(style_ids=[style],animations=[animation] if animation else [],font_size=36)
        selected=(b.get('aigc_plan') or {}).get('selected_segment_id')
        if selected:
            d['segments'][0]['visual']={'mode':'material_segment_ids','props':{'segment_ids':[selected]}}
        body={k:d[k] for k in ['product_id','title','video','config','vo_rewrite_prompt','segments']}
        if args.dry_run:print(bid,'demo subtitle',style,'voices',len(before['config']['tts']['voices']));continue
        backup=out/f'{bid}-before.json'
        if not backup.exists():backup.write_text(json.dumps(before,ensure_ascii=False,indent=2))
        (out/f'{bid}-intended.json').write_text(json.dumps(body,ensure_ascii=False,indent=2))
        cb.api('PUT',f'/video-scripts/{sid}',body);after=cb.api('GET',f'/video-scripts/{sid}')
        if any(after.get(k)!=body.get(k) for k in body):raise SystemExit('Demo layout readback differs')
        (out/f'{bid}-after.json').write_text(json.dumps(after,ensure_ascii=False,indent=2));print(bid,'distinct demo layout verified, voice pool retained')

def demo_inspect(args,cfg):
    cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';cb.CFG=cfg
    st=cb.State(args.run);briefs={b['brief_id']:b for b in cb.load_briefs(args.run)};out=Path(args.run)/'clipo'/'demo-evidence';out.mkdir(parents=True,exist_ok=True)
    rows=[]
    for key,v in sorted(st.videos.items()):
        if not key.endswith('-V01'):continue
        c=cb.api('GET',f"/video-cuts/{v['cut_id']}");(out/f'{key}-cut.json').write_text(json.dumps(c,ensure_ascii=False,indent=2))
        dr=c['draft'];rr=c.get('random_result') or {};b=briefs[v['brief_id']]
        main=sorted([x for x in dr['clips'] if x['trackId']=='main'],key=lambda x:x['from']);expected=(b.get('aigc_plan') or {}).get('selected_segment_id');exact=None
        if expected:
            idx=cb.footage_index(b['product_key']);actual=idx.get(main[0]['props']['src'].split('?')[0].rsplit('/',1)[-1]) or {};exact=actual.get('id')==expected
            if not exact:raise SystemExit('Demo opener does not match the approved exact AI segment')
        row={'video':key,'cut_id':v['cut_id'],'voice':v.get('voice') or rr.get('tts_speaker_id'),'subtitle_style':v.get('native_subtitle_style') or rr.get('subtitle_style_id'),'bgm':rr.get('bgm_id'),'duration_seconds':round(dr['composition']['durationInFrames']/dr['composition'].get('fps',30),2),'effects':sum(x.get('trackId')=='effect' for x in dr['clips']),'voice_segments':sum(x.get('trackId')=='voiceover' for x in dr['clips']),'selected_hook_exact':exact}
        rows.append(row);print(json.dumps(row,ensure_ascii=False))
    summary={'videos':rows,'distinct_voices':len({x['voice'] for x in rows if x['voice']}),'distinct_subtitle_styles':len({x['subtitle_style'] for x in rows if x['subtitle_style']}),'status':'actual composition metadata checked; listening and visual review remain separate'}
    (out/'demo-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print('Distinct actual voices',summary['distinct_voices'],'subtitle styles',summary['distinct_subtitle_styles'])

def render_previews(args,cfg):
    import subprocess
    cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';cb.CFG=cfg
    run=Path(args.run).resolve();st=cb.State(run);out=run/'demo检查';out.mkdir(exist_ok=True)
    for key,v in sorted(st.videos.items()):
        if not key.endswith('-V01'):continue
        if args.brief and v['brief_id']!=args.brief:continue
        job=cb.api('GET',f"/render-jobs/{v['render_job']}");url=job.get('download_url')
        if not url:print(key,'render download not ready');continue
        video=out/f'{key}.mp4';cb.fetch_file(url,video)
        c=cb.api('GET',f"/video-cuts/{v['cut_id']}");dr=c['draft'];fps=dr['composition'].get('fps',30);duration=dr['composition']['durationInFrames']/fps
        main=sorted([x for x in dr['clips'] if x['trackId']=='main'],key=lambda x:x['from'])
        product=next((x for x in main if str(x['id']).startswith('3-')),main[len(main)//2])
        ingredient=next((x for x in main if str(x['id']).startswith('4-')),main[len(main)//2])
        times=[.6,(product['from']+product['durationInFrames']*.5)/fps,(ingredient['from']+ingredient['durationInFrames']*.5)/fps,max(0,duration-1),max(0,duration-.1)]
        images=[]
        for i,t in enumerate(times):
            dest=out/f'{key}-{i}.jpg';subprocess.run(['ffmpeg','-v','error','-y','-ss',str(t),'-i',str(video),'-vf','scale=270:-1','-frames:v','1',str(dest)],check=True);images.append(dest)
        command=['ffmpeg','-v','error','-y']
        for f in images:command+=['-i',str(f)]
        command+=['-filter_complex','hstack=inputs=5','-frames:v','1',str(out/f'{key}-frames.jpg')];subprocess.run(command,check=True)
        print(key,video,out/f'{key}-frames.jpg')

def fit_captions(args,cfg):
    import copy
    cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';cb.CFG=cfg
    st=cb.State(args.run);out=Path(args.run)/'clipo'/'single-line-repair';out.mkdir(parents=True,exist_ok=True)
    canon=lambda obj: {k:canon(v) for k,v in obj.items()} if isinstance(obj,dict) else [canon(v) for v in obj] if isinstance(obj,list) else obj.split('?')[0] if isinstance(obj,str) and obj.startswith('https://') else obj
    for b in cb.load_briefs(args.run):
        sid=st.data['briefs'][b['brief_id']]['script_id'];script=cb.api('GET',f'/video-scripts/{sid}');script['config']['subtitle_style']['font_size']=cfg.get('subtitle_font_max',36)
        body={k:script[k] for k in ['product_id','title','video','config','vo_rewrite_prompt','segments']};cb.api('PUT',f'/video-scripts/{sid}',body)
        check=cb.api('GET',f'/video-scripts/{sid}')
        if check['config']['subtitle_style']['font_size']!=36:raise SystemExit('System script font readback mismatch')
    for key,v in sorted(st.videos.items()):
        if v.get('caption_repair_key')=='single-line-01':continue
        before=cb.api('GET',f"/video-cuts/{v['cut_id']}");draft=cb.fit_single_line_subtitles(before['draft'])
        if [x for x in draft['clips'] if x['trackId']!='overlay']!=[x for x in before['draft']['clips'] if x['trackId']!='overlay']:raise SystemExit('Non-subtitle tracks changed')
        (out/f'{key}-before.json').write_text(json.dumps(before,ensure_ascii=False,indent=2));(out/f'{key}-intended.json').write_text(json.dumps(draft,ensure_ascii=False,indent=2))
        cb.api('PUT',f"/video-cuts/{v['cut_id']}",{'script_id':before['script_id'],'draft':draft})
        after=cb.api('GET',f"/video-cuts/{v['cut_id']}")
        if canon(after['draft'])!=canon(draft):raise SystemExit('Font-only draft readback mismatch')
        (out/f'{key}-after.json').write_text(json.dumps(after,ensure_ascii=False,indent=2))
        job=cb.api('POST','/render-jobs',{'spec':after['draft'],'options':{'codec':'h264','quality':'high','scale':1},'cut_id':v['cut_id'],'idempotency_key':'udb-'+v['cut_id']+'-single-line-01'},tries=1)
        v.update(render_job=job['job_id'],stage='rendering',caption_repair_key='single-line-01',ai_review=None,ai_reason=None)
        v.pop('object_key',None);st.save()
        sizes=[x['props'].get('font_size') for x in draft['clips'] if x['trackId']=='overlay' and x.get('props',{}).get('text')]
        print(key,'font only',min(sizes),max(sizes),'rerender',job['job_id'],flush=True)

def graphics_only(args,cfg):
    """Replace decorative text layers with brand light effects; preserve captions/media/audio/timing."""
    import copy
    cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';cb.CFG=cfg
    st=cb.State(args.run);out=Path(args.run)/'clipo'/'single-line-graphics';out.mkdir(parents=True,exist_ok=True)
    root=Path(__file__).resolve().parent.parent/'templates'/'lux-effects'
    manifest=json.loads((root/'manifest.json').read_text())['templates']
    orders=[['sweep','particles','glow'],['particles','glow','sweep'],['glow','sweep','particles'],['sweep','glow','particles'],['glow','particles','sweep']]
    colors=['#E7C78B','#D8B6DE','#F0D6A7','#E8BDCA','#C7DAD5']
    canon=lambda obj: {k:canon(v) for k,v in obj.items()} if isinstance(obj,dict) else [canon(v) for v in obj] if isinstance(obj,list) else obj.split('?')[0] if isinstance(obj,str) and obj.startswith('https://') else obj
    for n,(key,v) in enumerate(sorted(st.videos.items())):
        if v.get('graphics_repair_key')=='single-line-graphics-01':continue
        before=cb.api('GET',f"/video-cuts/{v['cut_id']}");draft=copy.deepcopy(before['draft'])
        effects=[x for x in draft['clips'] if x['trackId']=='effect']
        for i,clip in enumerate(effects):
            kind=orders[n%5][i%3];template=manifest[kind]
            clip['props']['code']=(root/template['source']).read_text()
            clip['props']['params']={**template['params'],'durationFrames':min(90,clip['durationInFrames']),'accent':colors[n%5]}
            if kind=='particles':clip['props']['params'].update(seed=17+n*13+i,count=10+n*2)
        if [x for x in draft['clips'] if x['trackId']!='effect']!=[x for x in before['draft']['clips'] if x['trackId']!='effect']:raise SystemExit('Core tracks unexpectedly changed')
        if args.dry_run:print(key,'replace decorative text',len(effects),'core tracks unchanged');continue
        backup=out/f'{key}-before.json'
        if not backup.exists():backup.write_text(json.dumps(before,ensure_ascii=False,indent=2))
        (out/f'{key}-intended.json').write_text(json.dumps(draft,ensure_ascii=False,indent=2))
        cb.api('PUT',f"/video-cuts/{v['cut_id']}",{'script_id':before['script_id'],'draft':draft})
        after=cb.api('GET',f"/video-cuts/{v['cut_id']}")
        if canon(after['draft'])!=canon(draft):raise SystemExit('Graphics draft readback mismatch')
        (out/f'{key}-after.json').write_text(json.dumps(after,ensure_ascii=False,indent=2))
        job=cb.api('POST','/render-jobs',{'spec':after['draft'],'options':{'codec':'h264','quality':'high','scale':1},'cut_id':v['cut_id'],'idempotency_key':'udb-'+v['cut_id']+'-single-line-graphics-01'},tries=1)
        v.update(render_job=job['job_id'],stage='rendering',graphics_repair_key='single-line-graphics-01',ai_review=None,ai_reason=None)
        v.pop('object_key',None);st.save()
        print(key,'no-text graphics',len(effects),'core tracks unchanged; rerender',job['job_id'],flush=True)

def demo_bundle(args,cfg):
    import html,shutil,datetime
    run=Path(args.run).resolve();out=run/'验收';out.mkdir(exist_ok=True)
    cb.CFG=cfg;st=cb.State(run);summary=json.loads((run/'clipo/demo-evidence/demo-summary.json').read_text())
    esc=lambda x:html.escape(str(x),quote=True)
    voices={'zh_female_zhixingnv_uranus_bigtts':'知性女声','zh_female_qinqienv_uranus_bigtts':'亲切女声','zh_female_qingxinnvsheng_uranus_bigtts':'清新女声','zh_female_tiexinnvsheng_uranus_bigtts':'贴心女声','zh_female_shuangkuaisisi_uranus_bigtts':'爽快思思','ICL_uranus_zh_female_kailangtingting_tob':'开朗婷婷','zh_female_zhishuaiyingzi_uranus_bigtts':'直率英子','ICL_uranus_zh_female_tiexinguimi_tob':'贴心闺蜜'}
    briefs={b['brief_id']:b for b in cb.load_briefs(run)};cards=[];scripts=[]
    for i,row in enumerate(summary['videos'],1):
        key=row['video'];bid=key.rsplit('-V',1)[0];b=briefs[bid]
        if not (run/'demo检查'/f'{key}.mp4').exists():raise SystemExit('Missing demo export '+key)
        share=st.data.get('shares',{}).get(bid,{}).get('id')
        link=cfg['clipo']['base_url'].rstrip('/')+'/share/'+share if share else ''
        label='第'+str(i)+'组 · '+b['direction']
        review_note='<p class="meta">自动排版审核把独立上方花字计作两行字幕；记录保留，本条仅供你人工验收。</p>' if st.videos[key].get('layout_review_note') else '';cards.append('<article><h3>'+esc(label)+'</h3>'+review_note+'<video controls playsinline preload="metadata" poster="../demo检查/'+key+'-0.jpg" src="../demo检查/'+key+'.mp4?v='+esc(st.videos[key]['render_job'])+'"></video><p class="meta">'+esc(voices.get(row['voice'],row['voice']))+' · '+str(row['duration_seconds'])+'秒 · '+('demo已确认，批量生产中' if b.get('demo_approval') else '待你验收')+'</p><a href="'+esc(link)+'" target="_blank">Clipo demo</a></article>')
        sid=st.data['briefs'][bid]['script_id'];pid=cfg['products'][b['product_key']]['clipo_product_id'];url=cfg['clipo']['base_url'].rstrip('/')+'/dashboard/products/materials/scripts/'+sid+'?product='+pid
        plan_file=run/'revisions/批量资源纠正'/(bid+'-sfx-config-after.json');plan_file=plan_file if plan_file.exists() else run/'revisions/切片去重'/(bid+'-script-pool-after.json');plan_file=plan_file if plan_file.exists() else run/'revisions/声音节奏与人物重选/native-final/layout-corrected'/(key+'-script-after.json');native_script=json.loads(plan_file.read_text()) if plan_file.exists() else {};flowers=[x.get('effect',{}).get('params',{}).get('text') for x in native_script.get('segments',[]) if x.get('effect')];pool_counts=[len((x.get('visual') or {}).get('props',{}).get('segment_ids',[])) for x in native_script.get('segments',[])];pool_html='<p class="meta">系统各段候选素材数：'+esc(' / '.join(map(str,pool_counts)))+'</p>' if pool_counts else '';flower_html='<p class="meta">已回读上方花字配置：'+esc(' / '.join(flowers))+'</p>' if flowers else '';scripts.append('<details><summary>'+esc(label)+' · 系统配置脚本</summary><p><a target="_blank" href="'+esc(url)+'">打开Clipo脚本配置</a></p>'+pool_html+flower_html+''.join('<p>'+esc(x['text'])+'</p>' for x in b['segments'])+'<p class="meta">以上为系统主脚本；demo采用系统逐句改写口播，以视频实听为准。</p></details>')
    ai=[('petal_spotlight','原选 · 聚光灯只追头发','已选作第1组开场'),('spotlight_wave','同类02 · 微卷长发回头追光','已绑定brief1批量钩子池'),('spotlight_rise','同类03 · 黑直发起身追光','已绑定brief1批量钩子池'),('spotlight_tuck','同类04 · 栗棕卷发拢发追光','已绑定brief1批量钩子池'),('spotlight_book','同类05 · 披肩长发抬头追光','已绑定brief1批量钩子池')]
    aicards=[]
    for key,title,status in ai:
        name=key+'-01.mp4'
        if not (run/'aigc/preview'/name).exists():raise SystemExit('Missing AI preview '+name)
        aicards.append('<article><h3>'+esc(title)+'</h3><video controls playsinline preload="metadata" src="../aigc/preview/'+name+'"></video><p class="meta">5秒 · '+esc(status)+'</p></article>')
    group=st.data.get('share_group',{}).get('id');gurl=cfg['clipo']['base_url'].rstrip('/')+'/share/group/'+group if group else ''
    css='body{font:16px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;background:#f7f3ed;color:#342c28;margin:0;padding:24px;max-width:1200px;margin:auto}h1{font-size:28px}h2{margin-top:32px}h3{font-size:17px;margin-top:0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px}article,details{background:white;border:1px solid #e5d9ca;border-radius:14px;padding:18px}video{width:100%;max-height:560px;border-radius:9px;background:#ded8d2}.meta{font-size:13px;color:#76685e}details{margin:12px 0}summary{cursor:pointer;font-weight:600}a{color:#785624}'
    doc='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LUX · 5组demo与追光钩子</title><style>'+css+'</style><h1>LUX · 11月25日 Demo验收</h1><p>本轮修订：demo保留已认可版本；批量每组5套Clipo原生字幕和6首BGM轮换，单条片内保持统一、单行36号；上方花字为Clipo现有资源库的独立配置，每组3处。产品名与关键卖点短句只改字色，字体、字号、描边和位置不变。brief1原口播提高4dB；另外4组采用4种音色、整篇1.4倍配音，配不同节奏音乐和分组造型素材。重复镜头已替换；brief2、3、5各84条独立候选，brief4为71条，各段素材池互不共用来源。brief2–5已获准批量，各补9条；brief1也已获准量产，5条AI追光画面已全部绑定；新增9条在产。</p><p><a href="'+esc(gurl)+'" target="_blank">打开Clipo · 5组demo合集</a></p><h2>每组1条demo</h2><div class="grid">'+''.join(cards)+'</div><h2>5个同类型AI追光钩子</h2><p class="meta">原选1条 + 新增4条。5条均已写入brief1系统素材池，批量轮换。</p><div class="grid">'+''.join(aicards)+'</div><h2>5组系统脚本配置</h2>'+''.join(scripts)+'</html>'
    previous=out/'index.html';backup=run/'revisions/旧验收页-六方向.html';backup.parent.mkdir(exist_ok=True)
    if previous.exists() and not backup.exists():shutil.copy2(previous,backup)
    previous.write_text(doc)
    (out/'demo验收清单.json').write_text(json.dumps({'updated_at':datetime.datetime.now().isoformat(),'demos':summary['videos'],'same_type_ai_samples':[{'key':x[0],'title':x[1],'status':x[2]} for x in ai],'group_url':gurl,'status':'awaiting_user_acceptance'},ensure_ascii=False,indent=2))
    print(previous)

def adopt_repair(args,cfg):
    """Adopt a verified native footage_qc render receipt into the existing batch, without resubmission."""
    import footage_qc as fq
    from qc import cut_checks
    cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';cb.CFG=cfg
    receipt=json.loads(Path(args.plan).read_text());record=json.loads(Path(receipt['backup']).read_text())
    cid=receipt['cut_id'];cut=cb.api('GET',f'/video-cuts/{cid}');job=cb.api('GET',f"/render-jobs/{receipt['job_id']}")
    fq.validate_plan(record['before']['draft'],cut['draft'],record['plan'],allow_render_cover=True)
    if fq.normalized_main(fq.main_clips(cut['draft']))!=fq.normalized_main(record['plan']['replacement_main']):raise SystemExit('Receipt draft mismatch')
    st=cb.State(args.run);matches=[(k,v) for k,v in st.videos.items() if v['cut_id']==cid]
    if len(matches)!=1:raise SystemExit('Repair cut must match exactly one existing video')
    key,v=matches[0]
    if v.get('render_job')==receipt['job_id']:print(key,'receipt already adopted; no duplicate render');return
    if v.get('stage') not in ['done','failed']:raise SystemExit('Batch cut currently active; refusing overwrite')
    brief=next(b for b in cb.load_briefs(args.run) if b['brief_id']==v['brief_id']);issues=cut_checks(brief,cut['draft'],v.get('voice'))
    if issues:raise SystemExit('Repair QC: '+str(issues))
    (Path(args.plan).parent/'batch-before-adoption.json').write_text(json.dumps({'video':key,'previous':v},ensure_ascii=False,indent=2))
    v.update(render_job=receipt['job_id'],stage='rendering',ai_review=None,ai_reason=None,qc_issues=issues,repair_receipt=str(Path(args.plan).resolve()))
    v.pop('object_key',None);st.save();print(key,'adopted native repair render',receipt['job_id'],'for fresh review')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('cmd',choices=['schema','inventory','recover','configure','preview-aigc','create-draft','retire-aigc','review-bundle','overnight-review','options-board','ui-links','product-ui-links','demo-layout','demo-inspect','render-previews','fit-captions','graphics-only','demo-bundle','adopt-repair']);ap.add_argument('--config',required=True);ap.add_argument('--plan');ap.add_argument('--brief');ap.add_argument('--ui-path');ap.add_argument('--dry-run',action='store_true');ap.add_argument('--run',required=True);args=ap.parse_args();cfg=load_config(args.config);{'schema':schema,'inventory':inventory,'recover':recover,'configure':configure,'preview-aigc':preview_aigc,'create-draft':create_draft,'retire-aigc':retire_aigc,'review-bundle':review_bundle,'overnight-review':overnight_review,'options-board':options_board,'ui-links':ui_links,'product-ui-links':product_ui_links,'demo-layout':demo_layout,'demo-inspect':demo_inspect,'render-previews':render_previews,'fit-captions':fit_captions,'graphics-only':graphics_only,'demo-bundle':demo_bundle,'adopt-repair':adopt_repair}[args.cmd](args,cfg)
if __name__=='__main__':main()
