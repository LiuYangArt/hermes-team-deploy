import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('hook', Path(__file__).resolve().parents[2] / 'scripts/codex/task_docs_hook.py')
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class GateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('core', 'deploy'):
            repo = self.root / name
            repo.mkdir()
            self.git(name, 'init', '-q')
            self.git(name, 'config', 'user.email', 'test@example.invalid')
            self.git(name, 'config', 'user.name', 'Test')
            self.write(name + '/.gitignore', 'artifacts/\n')
            self.write(name + '/code.py', 'before\n')
            self.git(name, 'add', '.')
            self.git(name, 'commit', '-qm', 'baseline')
        self.event = {'session_id': 'test', 'hook_event_name': 'UserPromptSubmit'}
        self.start()

    def git(self, repo, *args):
        return subprocess.check_output(['git', '-C', str(self.root / repo), *args])

    def write(self, path, text):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def start(self):
        return hook.handle(self.event, self.root)

    def stop(self, **extra):
        return hook.handle(dict(self.event, hook_event_name='Stop', **extra), self.root)

    def document(self):
        self.write('deploy/docs/PLAN.md', '- [x] Verified\n')
        self.write('deploy/docs/FEATURE.md', 'Behavior and verification\n')

    def test_readonly(self):
        self.assertEqual(self.stop(), {})

    def test_code_requires_plan_and_doc(self):
        self.write('core/code.py', 'after\n')
        self.assertEqual(self.stop()['decision'], 'block')
        self.write('deploy/docs/PLAN.md', '- [x] Verified\n')
        self.assertIn('PLAN.md 以外', self.stop()['reason'])
        self.write('deploy/docs/FEATURE.md', 'Verified\n')
        self.assertEqual(self.stop(), {})

    def test_old_dirty_docs_do_not_count(self):
        self.document()
        self.assertEqual(self.stop(), {})
        self.start()
        self.write('deploy/code.py', 'after\n')
        self.assertEqual(self.stop()['decision'], 'block')

    def test_continuation_preserves_baseline(self):
        self.write('core/code.py', 'after\n')
        self.assertEqual(self.stop()['decision'], 'block')
        self.start()
        self.assertEqual(self.stop(stop_hook_active=True)['decision'], 'block')
        self.document()
        self.assertEqual(self.stop(stop_hook_active=True), {})

    def test_commit_does_not_hide_changes(self):
        self.write('core/code.py', 'after\n')
        self.git('core', 'add', '.')
        self.git('core', 'commit', '-qm', 'change')
        self.assertEqual(self.stop()['decision'], 'block')

    def test_new_prompt_resets_after_success(self):
        self.write('core/code.py', 'after\n')
        self.document()
        self.assertEqual(self.stop(), {})
        self.start()
        self.write('core/code.py', 'again\n')
        self.assertEqual(self.stop()['decision'], 'block')

    def test_task_only_docs_stay_in_task(self):
        self.write('deploy/jobs/a/run.py', 'changed\n')
        self.write('deploy/jobs/b/README.md', '- [x] Unrelated\n')
        self.assertIn('jobs/a/', self.stop()['reason'])
        self.write('deploy/jobs/a/README.md', 'Verified\n- [x] Implemented\n')
        self.assertEqual(self.stop(), {})

    def test_task_doc_without_checklist_blocks(self):
        self.write('deploy/jobs/a/run.py', 'changed\n')
        self.write('deploy/jobs/a/README.md', 'Verified\n')
        self.assertIn('复选任务项', self.stop()['reason'])

    def test_deleted_doc_does_not_count(self):
        self.document()
        self.assertEqual(self.stop(), {})
        self.start()
        (self.root / 'deploy/docs/PLAN.md').unlink()
        self.assertEqual(self.stop()['decision'], 'block')

    def test_unicode_space_and_rename(self):
        self.write('core/有 空格.py', 'after\n')
        (self.root / 'core/code.py').rename(self.root / 'core/new.py')
        self.assertEqual(self.stop()['decision'], 'block')
        self.document()
        self.assertEqual(self.stop(), {})

    def test_missing_baseline_blocks(self):
        event = dict(self.event, hook_event_name='Stop', session_id='new')
        self.assertEqual(hook.handle(event, self.root)['decision'], 'block')

    def test_interrupted_before_first_stop_keeps_baseline(self):
        self.write('core/code.py', 'after\n')
        self.start()
        self.assertEqual(self.stop()['decision'], 'block')

    def test_whitespace_docs_do_not_pass(self):
        self.write('core/code.py', 'after\n')
        self.write('deploy/docs/PLAN.md', ' \n')
        self.write('deploy/docs/FEATURE.md', ' \n')
        self.assertEqual(self.stop()['decision'], 'block')

    def test_existing_checklist_must_change(self):
        self.write('deploy/jobs/a/README.md', '- [x] Done\nDescription\n')
        self.assertEqual(self.stop(), {})
        self.start()
        self.write('deploy/jobs/a/run.py', 'changed\n')
        self.write('deploy/jobs/a/README.md', '- [x] Done\nOther description\n')
        self.assertEqual(self.stop()['decision'], 'block')
        self.write('deploy/jobs/a/README.md', '* [X] Done and verified\nOther description\n')
        self.assertEqual(self.stop(), {})

    def test_install_is_idempotent_preserves_other_hooks(self):
        self.write('.codex/hooks.json', json.dumps({'hooks': {'Stop': [{'hooks': [{'command': 'existing'}]}]}}))
        hook.install(self.root)
        hook.install(self.root)
        config = json.loads((self.root / '.codex/hooks.json').read_text())
        self.assertEqual(len(config['hooks']['Stop']), 2)
        self.assertEqual(len(config['hooks']['UserPromptSubmit']), 1)
        self.assertEqual(config['hooks']['Stop'][0]['hooks'][0]['command'], 'existing')


if __name__ == '__main__':
    unittest.main()
