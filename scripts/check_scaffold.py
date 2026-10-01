from pathlib import Path
root = Path(__file__).resolve().parents[1]
required = ['README.md', 'AGENTS.md', 'docs/PLAN.md', 'docs/PATCHES.md', 'deploy/README.md', 'extensions/README.md', 'jobs/README.md', 'rules/README.md']
for name in required:
    assert (root / name).is_file(), name
print('PASS: scaffold documents present; no runtime deployment claimed')
