from pathlib import Path
import shutil
import subprocess
import pytest


def test_plan_interface_states_and_encrypted_transfer_controls():
    node = shutil.which('node')
    if not node:
        pytest.skip('Optional interface fixture needs Node.js')
    result = subprocess.run([node, '--test', str(Path(__file__).parent / 'js/plan_ui.test.js'),
                             str(Path(__file__).parent / 'js/voice_ui.test.js')],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
