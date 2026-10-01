#!/usr/bin/env python3
"""Project-local Codex documentation gate; Python standard library only."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], timeout=20)


def fingerprint(path):
    if path.is_symlink():
        data = os.fsencode(os.readlink(path))
        mode = '120000'
    elif path.is_file():
        mode = '100755' if path.stat().st_mode & stat.S_IXUSR else '100644'
        digest = hashlib.sha1(b'blob ' + str(path.stat().st_size).encode() + b'\0')
        with path.open('rb') as source:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(block)
        return mode + ':' + digest.hexdigest()
    else:
        return None
    return mode + ':' + hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def repo_snapshot(root):
    # Index hashes keep a large clean Core cheap; only dirty/untracked files are read.
    files = {}
    for item in git(root, 'ls-files', '--stage', '-z').split(b'\0'):
        if not item:
            continue
        meta, name = item.split(b'\t', 1)
        mode, digest, stage = meta.decode().split()
        if stage != '0':
            raise ValueError('仓库存在未解决的合并冲突')
        files[os.fsdecode(name)] = mode + ':' + digest
    dirty = git(root, 'diff', '--name-only', '-z')
    untracked = git(root, 'ls-files', '--others', '--exclude-standard', '-z')
    for raw in (dirty + untracked).split(b'\0'):
        if raw:
            name = os.fsdecode(raw)
            value = fingerprint(root / name)
            if value is None:
                files.pop(name, None)
            else:
                files[name] = value
    return files


def snapshot(workspace):
    files = {}
    for name in ('core', 'deploy'):
        for path, digest in repo_snapshot(workspace / name).items():
            if name == 'deploy' and is_doc(path) and (path.startswith('docs/') or path.startswith('jobs/')):
                content = (workspace / name / path).read_text(errors='replace')
                digest += ':' + text_digest(content) + ':' + text_digest('\n'.join(checklist(content)))
            files[name + '/' + path] = digest
    for path in (workspace / 'AGENTS.md', workspace / 'README.md'):
        value = fingerprint(path)
        if value:
            files[path.name] = value
    if (workspace / '.codex').exists():
        for path in (workspace / '.codex').rglob('*'):
            value = fingerprint(path)
            if value:
                files[str(path.relative_to(workspace))] = value
    return files


def is_doc(path):
    return Path(path).suffix.lower() in ('.md', '.rst', '.adoc')


def text_digest(text):
    return hashlib.sha256(re.sub(r'\s+', '', text).encode()).hexdigest()


def checklist(text):
    return re.findall(r'^\s*[-*+]\s+\[[ xX]\]\s+.+$', text, re.MULTILINE)


def doc_changed(path, before, after, checklist_only=False):
    index = 3 if checklist_only else 2
    new = after[path].split(':')
    old = before.get(path, '').split(':')
    return (len(new) > index and new[index] != text_digest('')
            and (len(old) <= index or old[index] != new[index]))


def missing_requirements(before, after, workspace):
    changed = {p for p in before.keys() | after.keys() if before.get(p) != after.get(p)}
    updated = {p for p in changed if p in after}
    tasks = {p.split('/')[2] for p in changed if p.startswith('deploy/jobs/') and len(p.split('/')) > 3}
    missing = []
    for task in sorted(tasks):
        docs = [p for p in updated if p.startswith('deploy/jobs/' + task + '/') and is_doc(p) and doc_changed(p, before, after)]
        if not docs:
            missing.append('更新 deploy/jobs/' + task + '/ 内的说明文档')
        if not any(doc_changed(p, before, after, checklist_only=True) for p in docs):
            missing.append('更新 deploy/jobs/' + task + '/ 内含复选任务项的清单（可与说明文档为同一文件）')
    public = {p for p in changed if not (p.startswith('deploy/jobs/') and len(p.split('/')) > 3)}
    if public:
        if ('deploy/docs/PLAN.md' not in updated or
                not doc_changed('deploy/docs/PLAN.md', before, after)):
            missing.append('更新 deploy/docs/PLAN.md 中对应任务的状态、验证结果或阻碍')
        if any(not is_doc(p) for p in public) and not any(p.startswith('deploy/docs/') and p != 'deploy/docs/PLAN.md' and is_doc(p) and doc_changed(p, before, after) for p in updated):
            missing.append('更新 deploy/docs/ 下与本次改动对应的说明文档（PLAN.md 以外）')
    return missing


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, ensure_ascii=False)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def handle(event, workspace):
    kind = event.get('hook_event_name')
    if kind not in ('UserPromptSubmit', 'Stop'):
        return {}
    session = event.get('session_id')
    if not session:
        raise ValueError('hook 缺少 session_id，不能隔离本轮记录')
    key = hashlib.sha256(session.encode()).hexdigest()
    path = workspace / 'deploy/artifacts/codex-docs-hook' / (key + '.json')
    state = json.loads(path.read_text()) if path.exists() else None
    if kind == 'UserPromptSubmit':
        # Stop continuation is another prompt; keep the original baseline until resolved.
        if not state or not state.get('pending'):
            atomic_json(path, {'before': snapshot(workspace), 'pending': True})
        return {'hookSpecificOutput': {'hookEventName': kind, 'additionalContext':
            '本项目启用了完成前文档检查。公共改动同步 deploy/docs/PLAN.md 和对应说明；'
            '单任务改动只同步所属 jobs/<task>/ 的说明及任务清单。必须如实记录验证或未完成原因；'
            '不能为通过检查虚报完成、批量勾选或修改无关文档。只读答疑无需制造文档改动。'}}
    if not state:
        return {'decision': 'block', 'reason': '文档检查缺少开始快照。请说明本轮未完成核验，修复 hook 加载后重新执行；不要声称已通过。'}
    missing = missing_requirements(state['before'], snapshot(workspace), workspace)
    state['pending'] = bool(missing)
    state['last_result'] = missing or ['PASS']
    atomic_json(path, state)
    if missing:
        return {'decision': 'block', 'reason': '结束前文档检查未通过：\n- ' + '\n- '.join(missing) +
                '\n根据实际工作补齐后再结束。未完成项保留未完成并记录阻碍。不要仅触碰文件、增加无关文字或禁用检查。'}
    return {}


def install(workspace):
    command = shlex.join(['/usr/bin/python3', str(Path(__file__).resolve()), '--workspace', str(workspace)])
    config = workspace / '.codex/hooks.json'
    value = json.loads(config.read_text()) if config.exists() else {'hooks': {}}
    for kind in ('UserPromptSubmit', 'Stop'):
        groups = value.setdefault('hooks', {}).setdefault(kind, [])
        if not any(h.get('command') == command for group in groups for h in group.get('hooks', [])):
            groups.append({'hooks': [{'type': 'command', 'command': command, 'timeout': 30,
                                      'statusMessage': '检查项目文档和任务清单'}]})
    atomic_json(config, value)
    print(config)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    if args.install:
        install(workspace)
        return
    event = {}
    try:
        event = json.load(sys.stdin)
        result = handle(event, workspace)
    except Exception as exc:
        message = '项目文档检查失败：' + str(exc) + '。请修复检查，不要声称验收通过。'
        result = ({'decision': 'block', 'reason': message} if event.get('hook_event_name') == 'Stop'
                  else {'continue': False, 'stopReason': message})
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
