# ---------- 文件契约 ----------
# [INPUT]: 依赖 sync_skill 的版本校验与原地更新；本地 Git 仓库提供离线更新源。
# [OUTPUT]: 验证原目录更新、不生成备份包、文件指纹及本地改动保护。
# [POS]: tests 的安装更新回归；不连接 GitHub，不改真实安装。
# [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
import hashlib,json,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import sync_skill as s
class SyncSkillTest(unittest.TestCase):
 def test_stale_install_blocks_instead_of_claiming_latest(self):
  with tempfile.TemporaryDirectory() as t,patch.object(s,'latest',return_value='new'):
   with self.assertRaisesRegex(RuntimeError,'校验清单|未同步'):s.assert_current(t)
 def test_current_manifest_matches_exact_remote(self):
  with tempfile.TemporaryDirectory() as t,patch.object(s,'latest',return_value='commit'):
   Path(t,'SKILL.md').write_text('same');Path(t,'.skill-version.json').write_text(json.dumps({'commit':'commit','files':{'SKILL.md':hashlib.sha256(b'same').hexdigest()}}));self.assertEqual(s.assert_current(t),'commit')
 def test_modified_copied_install_is_not_overwritten(self):
  with tempfile.TemporaryDirectory() as t,patch.object(s,'latest',return_value='new'):
   p=Path(t,'SKILL.md');p.write_text('local edit');Path(t,'.skill-version.json').write_text(json.dumps({'commit':'old','files':{'SKILL.md':hashlib.sha256(b'upstream').hexdigest()}}))
   with self.assertRaisesRegex(RuntimeError,'本地文件被修改'):s.apply(t)
   self.assertEqual(p.read_text(),'local edit')
 def test_real_copied_install_updates_in_place_without_backup(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);repo=root/'repo';repo.mkdir()
   run=lambda *args:subprocess.run(['git',*args],cwd=repo,check=True,capture_output=True)
   run('init','-b','main');run('config','user.name','Skill QA');run('config','user.email','qa@example.invalid')
   (repo/'SKILL.md').write_text('latest');run('add','SKILL.md');run('commit','-m','fixture')
   target=root/'installed';target.mkdir();(target/'SKILL.md').write_text('old')
   identity=target.stat().st_ino
   sha=s.apply(target,str(repo),'main')
   self.assertEqual((target/'SKILL.md').read_text(),'latest')
   self.assertEqual(target.stat().st_ino,identity)
   manifest=json.loads((target/'.skill-version.json').read_text())
   self.assertEqual(manifest['commit'],s.git('rev-parse','HEAD',cwd=repo))
   self.assertEqual(manifest['files']['SKILL.md'],hashlib.sha256(b'latest').hexdigest())
   # ---------- 再次更新及无变化重跑仍只保留一个安装包 ----------
   (repo/'SKILL.md').write_text('newest');run('commit','-am','second fixture')
   newest=s.apply(target,str(repo),'main');self.assertNotEqual(newest,sha)
   self.assertEqual((target/'SKILL.md').read_text(),'newest')
   self.assertEqual(s.apply(target,str(repo),'main'),newest)
   self.assertEqual(target.stat().st_ino,identity)
   self.assertEqual({p.name for p in root.iterdir()},{'repo','installed'})
   self.assertEqual(list(target.rglob('*.updating')),[])
 def test_ahead_git_install_does_not_report_false_update(self):
  with tempfile.TemporaryDirectory() as t,patch.object(s,'latest',return_value='old'):
   Path(t,'.git').mkdir()
   def git(*args,**kwargs):
    if args[:2]==('rev-parse','HEAD'):return 'ahead'
    if args[:2]==('rev-parse','FETCH_HEAD'):return 'old'
    return ''
   with patch.object(s,'git',side_effect=git),self.assertRaisesRegex(RuntimeError,'尚未发布'):s.apply(t)
 def test_matching_commit_does_not_hide_changed_installed_file(self):
  with tempfile.TemporaryDirectory() as t,patch.object(s,'latest',return_value='commit'):
   Path(t,'SKILL.md').write_text('changed');Path(t,'.skill-version.json').write_text(json.dumps({'commit':'commit','files':{'SKILL.md':hashlib.sha256(b'upstream').hexdigest()}}))
   with self.assertRaisesRegex(RuntimeError,'不一致'):s.assert_current(t)
 def test_multi_digit_release_numbers_compare_numerically(self):
  self.assertLess(s.version_key('2026.11.26.9'),s.version_key('2026.11.26.10'))
  self.assertGreater(s.version_key('2026.11.26.100'),s.version_key('2026.11.26.90'))
  with self.assertRaises(RuntimeError):s.version_key('unknown')
if __name__=='__main__':unittest.main()
