import json
import subprocess

import pytest

from deploy import measure_build
from deploy import setup


def test_cgroup_cache_swap_and_oom_evidence_remain_distinct():
    values = measure_build.parse_cgroup(
        '@@memory.current\n4096\n@@memory.peak\n8192\n'
        '@@memory.swap.current\n2048\n@@memory.swap.peak\nmissing\n'
        '@@memory.events\nmax 7\noom 2\noom_kill 1\n'
        '@@memory.stat\nanon 1024\nfile 3072\n'
    )
    assert values['memory.current'] == 4096
    assert values['memory.peak'] == 8192
    assert values['memory.swap.current'] == 2048
    assert 'memory.swap.peak' not in values
    assert values['memory.events']['oom_kill'] == 1
    assert values['memory.stat'] == {'anon': 1024, 'file': 3072}


def test_failed_measured_stage_retains_evidence_and_removes_only_created_id(tmp_path, monkeypatch):
    calls = []
    container_id = 'a' * 64

    def fake_run(self, args, **kwargs):
        calls.append(args)
        if args[1] == 'create':
            return subprocess.CompletedProcess(args, 0, container_id + '\n', '')
        assert args[1:3] == ['start', '--attach']
        return subprocess.CompletedProcess(args, 137, '', '')

    def fake_capture(args, timeout=15):
        calls.append(args)
        stdout = json.dumps({'Running': False, 'ExitCode': 137, 'OOMKilled': True}) if args[1] == 'inspect' else ''
        return {'returncode': 0, 'stdout': stdout, 'stderr': ''}

    monkeypatch.setattr(setup.Runner, 'run', fake_run)
    monkeypatch.setattr(measure_build, 'capture', fake_capture)
    runner = measure_build.MeasuredRunner(tmp_path, tmp_path, 60)
    args = ['docker', 'run', '--rm', '--name', 'test-build', '--memory', '5g',
            'ghcr.io/project-osrm/osrm-backend@sha256:' + 'b' * 64, 'osrm-extract', '/data/japan.osm.pbf']
    with pytest.raises(setup.SetupError, match='137'):
        runner.run(args, log=tmp_path / 'build.log')
    summary = json.loads((tmp_path / 'extract/summary.json').read_text())
    assert summary['container_state']['OOMKilled']
    assert summary['returncode'] == 137
    assert ['docker', 'rm', container_id] in calls
    assert '--rm' not in calls[0]
    assert not any('route-estimator-osrm' in call for call in calls)
