@Library('nstut-ci') _

nstutPython(
    python: '3.11',
    command: '''#!/usr/bin/env bash
set -euo pipefail
python -m pip install -e . pytest pytest-asyncio
python -m pytest -q --junitxml=ci-evidence/tests.xml
python -m compileall -q src
python - <<'PY'
from importlib.metadata import entry_points
assert any(ep.name == 'quiz' for ep in entry_points().select(group='discord_adapter.addons'))
print('Discord addon entry point verified')
PY
''',
    tests: 'ci-evidence/tests.xml',
    artifacts: 'ci-evidence/**'
)
