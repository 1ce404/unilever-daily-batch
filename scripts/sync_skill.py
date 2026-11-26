#!/usr/bin/env python3
# ---------- 文件契约 ----------
# [INPUT]: 依赖 GitHub main、Git 凭据与本地提交或文件校验清单。
# [OUTPUT]: 核验版本并原地更新 skill；不创建旧版备份，不覆盖已识别的本地改动。
# [POS]: scripts 的安装更新入口；生产命令调用版本校验，运行数据不进入更新范围。
# [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""原地同步 Git 或复制安装；旧版从 GitHub 历史恢复，不保留本地备份包。"""
import argparse,datetime,hashlib,json,os,shutil,subprocess,sys,tempfile,re
from pathlib import Path
REPO='https://github.com/1ce404/unilever-daily-batch.git'
BRANCH='main'
def git(*args,cwd=None):
 r=subprocess.run(['git',*args],cwd=cwd,text=True,capture_output=True,timeout=120)
 if r.returncode:raise RuntimeError(r.stderr.strip() or 'Git request failed')
 return r.stdout.strip()
def version_key(value):
 value=value.strip()
 if not re.fullmatch(r'\d+(?:\.\d+)*',value):raise RuntimeError('Unsupported numeric skill version '+value)
 return tuple(int(x) for x in value.split('.'))

def current(root):
 if (root/'.git').exists():return git('rev-parse','HEAD',cwd=root)
 p=root/'.skill-version.json'
 return json.loads(p.read_text()).get('commit') if p.exists() else None
def latest(repo=REPO,branch=BRANCH):
 rows=git('ls-remote','--exit-code',repo,'refs/heads/'+branch).splitlines()
 if len(rows)!=1:raise RuntimeError('Could not resolve exact upstream branch')
 return rows[0].split()[0]
def assert_current(root):
 root=Path(root);remote=latest();local=current(root)
 if (root/'.git').exists():
  if git('status','--porcelain',cwd=root):raise RuntimeError('本地有未发布修改，不能冒充已同步版本。')
 else:
  manifest=root/'.skill-version.json';files=json.loads(manifest.read_text()).get('files',{}) if manifest.exists() else {}
  if not files:raise RuntimeError('复制安装缺少文件校验清单，需先更新安装。')
  if any(not (root/rel).is_file() or hashlib.sha256((root/rel).read_bytes()).hexdigest()!=digest for rel,digest in files.items()):raise RuntimeError('本地文件与已安装提交不一致，先确认本地改动再同步。')
 if local!=remote:raise RuntimeError('Skill未同步最新main。先执行 python3 -I scripts/sync_skill.py --apply，再重新运行；不使用旧版继续生产。')
 print('Skill版本已核验 '+remote[:12],flush=True);return remote

def apply(root,repo=REPO,branch=BRANCH):
 root=Path(root).resolve();remote=latest(repo,branch);local=current(root)
 if local==remote:
  if repo==REPO and branch==BRANCH:assert_current(root)
  print('Already current '+remote);return remote
 if (root/'.git').exists():
  if git('status','--porcelain',cwd=root):raise RuntimeError('本地有修改，先提交或另行保存；未覆盖任何文件。')
  git('fetch',repo,branch,cwd=root);fetched=git('rev-parse','FETCH_HEAD',cwd=root)
  if fetched!=remote:raise RuntimeError('Upstream moved during update; retry a fresh check')
  git('merge','--ff-only','FETCH_HEAD',cwd=root)
  if git('rev-parse','HEAD',cwd=root)!=remote:raise RuntimeError('本地存在尚未发布的提交；未降级，先合并上游再更新。')
  print('Updated '+remote);return remote
 manifest=root/'.skill-version.json';old=json.loads(manifest.read_text()) if manifest.exists() else {};changes=[]
 for rel,digest in old.get('files',{}).items():
  p=root/rel
  if p.exists() and hashlib.sha256(p.read_bytes()).hexdigest()!=digest:changes.append(rel)
 if changes:raise RuntimeError('本地文件被修改，先确认本地改动再更新：'+', '.join(changes[:8]))
 with tempfile.TemporaryDirectory(prefix='unilever-skill-update-') as tmp:
  checkout=Path(tmp)/'repo';git('clone','--depth','1','--branch',branch,repo,str(checkout));sha=git('rev-parse','HEAD',cwd=checkout)
  if sha!=remote:raise RuntimeError('Upstream moved during update; retry')
  local_version=root/'VERSION';upstream_version=checkout/'VERSION'
  if local_version.exists() and (not upstream_version.exists() or version_key(local_version.read_text())>version_key(upstream_version.read_text())):raise RuntimeError('远端main尚未包含本机新版；未降级，先合并已提交PR。')
  tracked=git('ls-files',cwd=checkout).splitlines();files={}
  for rel in tracked:
   src=checkout/rel
   if src.is_symlink() or not src.is_file() or '..' in Path(rel).parts:raise RuntimeError('Unsupported update path '+rel)
   files[rel]=hashlib.sha256(src.read_bytes()).hexdigest()
  # ---------- 临时下载后原地替换；旧版恢复依赖 GitHub 历史 ----------
  for rel in tracked:
   dest=root/rel;dest.parent.mkdir(parents=True,exist_ok=True);staged=dest.with_name(dest.name+'.updating');shutil.copy2(checkout/rel,staged);os.replace(staged,dest)
  for rel in set(old.get('files',{}))-set(files):
   p=root/rel
   if p.is_file():p.unlink()
  manifest.write_text(json.dumps({'repository':repo,'branch':branch,'commit':sha,'installed_at':datetime.datetime.now().isoformat(),'files':files},ensure_ascii=False,indent=2))
  print('Updated '+sha+' in place: '+str(root));return sha

def main():
 p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');p.add_argument('--check',action='store_true');p.add_argument('--install',default=str(Path(__file__).resolve().parent.parent));a=p.parse_args()
 try:
  apply(a.install) if a.apply else assert_current(a.install)
 except (RuntimeError,subprocess.SubprocessError,OSError) as e:print('BLOCK: '+str(e),file=sys.stderr);return 2
 return 0
if __name__=='__main__':sys.exit(main())
