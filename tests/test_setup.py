import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from deploy import setup as setup

ROOT = Path(__file__).parents[1]


@pytest.fixture
def config(tmp_path):
    return setup.load_settings(ROOT / 'deploy/setup.example.json', tmp_path)


@pytest.mark.parametrize('key,value', [
    ('cpus', True), ('build_memory_gib', 0), ('host_port', 0),
    ('image', 'untrusted/image:latest'), ('map_name', '../japan'),
    ('map_url', 'http://download.geofabrik.de/asia/japan-latest.osm.pbf'),
    ('map_url', 'https://evil.example/japan.osm.pbf'), ('ready_timeout_seconds', -1),
])
def test_invalid_settings(tmp_path, key, value):
    data = json.loads((ROOT / 'deploy/setup.example.json').read_text())
    data[key] = value
    p = tmp_path / 'setup.json'; p.write_text(json.dumps(data))
    with pytest.raises(setup.SetupError): setup.load_settings(p, tmp_path)


def test_distinct_ports(tmp_path):
    data = json.loads((ROOT / 'deploy/setup.example.json').read_text())
    data['validation_port'] = data['host_port']
    p=tmp_path/'setup.json'; p.write_text(json.dumps(data))
    with pytest.raises(setup.SetupError): setup.load_settings(p,tmp_path)


def test_snapshot_url_is_pinned_and_validated():
    assert setup.asset_name('https://download.geofabrik.de/asia/japan-261018.osm.pbf') == 'japan-261018.osm.pbf'
    with pytest.raises(setup.SetupError): setup.asset_name('https://evil.example/japan-261018.osm.pbf')


def test_identity_changes_for_inputs_not_resource_limits(config):
    a = setup.build_identity(config, {'sha256': 'a'*64})
    changed = dict(config, build_memory_gib=32, cpus=8)
    assert setup.build_identity(changed, {'sha256':'a'*64}) == a
    assert setup.build_identity(config, {'sha256':'b'*64}) != a


def test_resource_guard_and_prepare_report(config):
    report = setup.resource_report(config, {'MemTotal':8*1024**3,'NCPU':8,'OSType':'linux'},100*1024**3)
    assert not report['build_ready'] and not report['serve_ready']
    assert any('メモリ' in p for p in report['problems'])
    ok = setup.resource_report(config, {'MemTotal':32*1024**3,'NCPU':8,'OSType':'linux'},100*1024**3)
    assert ok['build_ready'] and ok['serve_ready']


def test_lock_and_exception_cleanup(tmp_path):
    lock = tmp_path/'operation.lock'
    with pytest.raises(RuntimeError):
        with setup.operation_lock(lock):
            with pytest.raises(setup.SetupError):
                with setup.operation_lock(lock): pass
            raise RuntimeError('interrupted')
    assert not lock.exists()


def test_templates_never_overwrite(tmp_path):
    (tmp_path/'data').mkdir()
    (tmp_path/'config.example.json').write_text('{"sample":true}')
    (tmp_path/'data/customers.example.csv').write_text('example')
    (tmp_path/'config.json').write_text('user config')
    setup.initialize_app_files(tmp_path)
    assert (tmp_path/'config.json').read_text()=='user config'
    assert (tmp_path/'data/customers.csv').read_text()=='example'


def test_cached_file_corruption_is_detected(tmp_path):
    path=tmp_path/'japan.osm.pbf'; path.write_bytes(b'abc')
    descriptor={'bytes':3,'sha256':hashlib.sha256(b'abc').hexdigest(),'md5':hashlib.md5(b'abc').hexdigest()}
    setup.verify_file(path,descriptor)
    path.write_bytes(b'xyz')
    with pytest.raises(setup.SetupError): setup.verify_file(path,descriptor)


def test_checkpoint_requires_prefix_and_identity():
    good={'build_id':'x','completed':['copied','extract']}
    assert setup.completed_stages(good,'x')==['copied','extract']
    with pytest.raises(setup.SetupError): setup.completed_stages(good,'y')
    with pytest.raises(setup.SetupError): setup.completed_stages({'build_id':'x','completed':['partition']},'x')


def test_smoke_measurements_and_no_fabrication():
    good={'code':'Ok','routes':[{'distance':664,'duration':87.3}], 'waypoints':[{'distance':6.2},{'distance':16.4}]}
    assert setup.validate_route(good,200)['distance_m']==664
    for field,value in [('distance',-1),('duration',float('nan')),('duration',True)]:
        broken=json.loads(json.dumps(good)); broken['routes'][0][field]=value
        with pytest.raises(setup.SetupError): setup.validate_route(broken,200)
    good['waypoints'][0]['distance']=201
    with pytest.raises(setup.SetupError): setup.validate_route(good,200)


def test_venv_executable_layout(tmp_path):
    assert setup.venv_python(tmp_path,windows=True)==tmp_path/'Scripts/python.exe'
    assert setup.venv_python(tmp_path,windows=False)==tmp_path/'bin/python'


def test_atomic_json_preserves_valid_document(tmp_path):
    p=tmp_path/'state.json'; setup.atomic_json(p,{'stage':'extract'})
    setup.atomic_json(p,{'stage':'partition'})
    assert json.loads(p.read_text())=={'stage':'partition'}
    assert not list(tmp_path.glob('*.tmp'))


class DownloadRunner:
    def __init__(self, content=b'road data', bad=False):
        self.content=content;self.bad=bad;self.calls=[]
    def run(self,args,**kwargs):
        self.calls.append(args)
        if '--head' in args:
            Path(args[args.index('--output')+1]).write_text('Content-Length: 9\n')
            return SimpleNamespace(returncode=0,stdout='https://download.geofabrik.de/asia/japan-261018.osm.pbf',stderr='')
        if str(args[-1]).endswith('.md5'):
            return SimpleNamespace(returncode=0,stdout=hashlib.md5(self.content).hexdigest()+'  japan-261018.osm.pbf\n',stderr='')
        Path(args[args.index('--output')+1]).write_bytes(b'wrong' if self.bad else self.content)
        return SimpleNamespace(returncode=0,stdout='',stderr='')


def tool_at(tmp_path, runner=None):
    settings=tmp_path/'setup.json'
    values=json.loads((ROOT/'deploy/setup.example.json').read_text())
    values['minimum_free_disk_gib']=1
    settings.write_text(json.dumps(values))
    return setup.Provisioner(tmp_path,settings,runner=runner)


def test_download_pins_checksum_to_snapshot_and_reuses(tmp_path):
    runner=DownloadRunner();tool=tool_at(tmp_path,runner)
    descriptor=tool.download()
    assert descriptor['bytes']==9
    assert descriptor['resolved_url'].endswith('japan-261018.osm.pbf')
    assert runner.calls[1][-1].endswith('japan-261018.osm.pbf.md5')
    assert '--continue-at' in runner.calls[2]
    count=len(runner.calls)
    assert tool.download()==descriptor and len(runner.calls)==count


def test_download_mismatch_keeps_partial_without_success(tmp_path):
    tool=tool_at(tmp_path,DownloadRunner(bad=True))
    with pytest.raises(setup.SetupError,match='MD5'): tool.download()
    assert not tool.download_path().exists()
    assert list(tool.config['downloads_directory'].glob('*.part'))
    assert not list(tool.config['downloads_directory'].glob('*.osm.pbf'))


def test_venv_does_not_rename_unrelated_directory(tmp_path):
    tool=tool_at(tmp_path)
    p=tool.config['venv_directory'];p.mkdir();(p/'important.txt').write_text('keep')
    with pytest.raises(setup.SetupError,match='venv以外'): tool.environment()
    assert (p/'important.txt').read_text()=='keep'


class BuildRunner:
    def __init__(self, fail=None): self.fail=fail;self.calls=[]
    def run(self,args,**kwargs):
        self.calls.append([str(a) for a in args])
        if self.fail and 'osrm-'+self.fail in args: raise setup.SetupError('injected '+self.fail+' failure')
        return SimpleNamespace(returncode=0,stdout='',stderr='')


def build_tool(tmp_path, fail=None):
    runner=BuildRunner(fail);tool=tool_at(tmp_path,runner)
    descriptor={'sha256':'a'*64}
    identity=setup.build_identity(tool.config,descriptor)
    tool.doctor=lambda **kwargs: None
    tool.downloaded=lambda: (tmp_path/'road.pbf',descriptor)
    tool.execute=lambda *args,**kwargs: SimpleNamespace(returncode=0,stdout='',stderr='')
    tool.marker=lambda volume: {'build_id':identity,'completed':['copied','extract']}
    saved=[]
    tool.save_marker=lambda volume,record: saved.append(record['completed'].copy())
    tool.validate_candidate=lambda volume,identity: [{'name':'fixture','distance_m':1,'duration_s':1}]
    return tool,runner,saved


def test_failed_stage_does_not_advance_or_publish(tmp_path):
    tool,runner,saved=build_tool(tmp_path,fail='partition')
    with pytest.raises(setup.SetupError): tool.build()
    assert not saved and not (tool.state/'ready.json').exists()
    assert not (tool.state/'active.json').exists()


def test_build_resumes_successful_stages(tmp_path):
    tool,runner,saved=build_tool(tmp_path)
    result=tool.build()
    assert not any('osrm-extract' in call for call in runner.calls)
    assert any('osrm-partition' in call for call in runner.calls)
    assert any('osrm-customize' in call for call in runner.calls)
    assert saved[-1]==setup.STAGES
    assert result['completed']==setup.STAGES and (tool.state/'ready.json').exists()
    assert not (tool.state/'active.json').exists()


def test_candidate_failure_does_not_publish(tmp_path):
    tool,runner,saved=build_tool(tmp_path)
    def fail(*args): raise setup.SetupError('candidate no route')
    tool.validate_candidate=fail
    with pytest.raises(setup.SetupError): tool.build()
    assert saved[-1]==['copied','extract','partition','customize']
    assert not (tool.state/'ready.json').exists()


def test_start_does_not_touch_existing_server_when_unbuilt(tmp_path):
    tool=tool_at(tmp_path,BuildRunner())
    tool.doctor=lambda **kwargs: None
    with pytest.raises(setup.SetupError,match='まだ前処理'): tool.start()
    assert not tool.runner.calls


def test_setup_unknown_and_duplicate_keys(tmp_path):
    data=(ROOT/'deploy/setup.example.json').read_text()
    p=tmp_path/'setup.json'
    p.write_text(data.replace('"cpus": 4','"cpus": 4, "cpus": 8'))
    with pytest.raises(setup.SetupError,match='重複'): setup.load_settings(p,tmp_path)
    values=json.loads(data);values['cpu']=8;p.write_text(json.dumps(values))
    with pytest.raises(setup.SetupError,match='未知'): setup.load_settings(p,tmp_path)


def test_prepare_only_never_builds_or_starts(tmp_path,monkeypatch):
    monkeypatch.setattr(setup,'ROOT',tmp_path)
    calls=[]
    class FakeTool:
        state=tmp_path
        def __init__(self,*args): pass
        def environment(self): calls.append('env')
        def docker_info(self,**kwargs): calls.append('docker')
        def download(self,*args): calls.append('download')
        def pull(self): calls.append('pull')
        def doctor(self): calls.append('doctor')
        def build(self): pytest.fail('prepare-only must not build')
        def start(self): pytest.fail('prepare-only must not start')
    monkeypatch.setattr(setup,'Provisioner',FakeTool)
    assert setup.main(['all','--prepare-only'])==0
    assert calls==['env','docker','download','pull','doctor']


def test_api_setup_never_downloads_or_contacts_docker(tmp_path,monkeypatch):
    (tmp_path/'config.json').write_text('{"routing":{"provider":"geoapify"}}')
    calls=[]
    class FakeTool:
        state=tmp_path
        def __init__(self,*args):pass
        def environment(self):calls.append('env')
        def __getattr__(self,name):pytest.fail('API setup must not call '+name)
    monkeypatch.setattr(setup,'ROOT',tmp_path)
    monkeypatch.setattr(setup,'Provisioner',FakeTool)
    assert setup.main(['all'])==0 and calls==['env']


def test_api_cui_never_starts_osrm(tmp_path):
    class CuiRunner(BuildRunner):
        def run(self,args,**kwargs):return SimpleNamespace(returncode=130,stdout='',stderr='')
    tool=tool_at(tmp_path,CuiRunner())
    python=setup.venv_python(tool.config['venv_directory']);python.parent.mkdir(parents=True);python.touch()
    (tmp_path/'config.json').write_text('{"routing":{"provider":"geoapify"}}')
    tool.start=lambda:pytest.fail('API mode must not start OSRM')
    assert tool.run_cui()==130


def test_failed_start_restores_legacy_without_publishing(tmp_path):
    class FailingComposeRunner(BuildRunner):
        def run(self,args,**kwargs):
            self.calls.append([str(a) for a in args])
            if any(str(a).endswith('/compose.yaml') for a in args) and 'up' in args:
                raise setup.SetupError('injected compose failure')
            return SimpleNamespace(returncode=0,stdout='',stderr='')
    runner=FailingComposeRunner();tool=tool_at(tmp_path,runner)
    descriptor={'sha256':'a'*64};identity=setup.build_identity(tool.config,descriptor)
    record={'build_id':identity,'volume':'route-estimator-fixture','map_name':'japan','image':tool.config['image']}
    setup.atomic_json(tool.state/'ready.json',record)
    tool.doctor=lambda **kwargs: None
    tool.downloaded=lambda: (tmp_path/'source.pbf',descriptor)
    tool.marker=lambda volume: {'build_id':identity,'completed':setup.STAGES}
    tool.validate_candidate=lambda *args: []
    tool.existing_server=lambda: {'Config':{'Cmd':['osrm-routed','/data/kansai-latest.osrm']},'Mounts':[{'Name':'route-estimator-maps'}]}
    with pytest.raises(setup.SetupError,match='compose failure'): tool.start()
    assert any(any(str(a).endswith('/compose.kansai.yaml') for a in call) for call in runner.calls)
    assert not (tool.state/'active.json').exists()


def test_cui_releases_setup_lock_and_preserves_exit_code(tmp_path):
    class CuiRunner(BuildRunner):
        def run(self,args,**kwargs):
            assert not (tmp_path/'state/setup/operation.lock').exists()
            return SimpleNamespace(returncode=130,stdout='',stderr='')
    tool=tool_at(tmp_path,CuiRunner())
    python=setup.venv_python(tool.config['venv_directory']);python.parent.mkdir(parents=True);python.touch()
    (tmp_path/'config.json').write_text('{"osrm":{"base_url":"http://127.0.0.1:5000"}}')
    def started(): assert (tool.state/'operation.lock').exists()
    tool.start=started
    assert tool.run_cui()==130


@pytest.mark.parametrize('args',[
    ['build','--prepare-only'], ['start','--prepare-only'],
    ['build','--refresh'], ['all','--demo'],
])
def test_options_are_not_silently_ignored(args,monkeypatch):
    def forbidden(*args): pytest.fail('invalid options must not initialize or mutate anything')
    monkeypatch.setattr(setup,'Provisioner',forbidden)
    with pytest.raises(SystemExit) as result: setup.main(args)
    assert result.value.code==2


def test_candidate_start_failure_keeps_logs_and_cleans_own_container(tmp_path):
    container_id='b'*64
    class CandidateRunner(BuildRunner):
        def run(self,args,**kwargs):
            self.calls.append([str(a) for a in args])
            if 'create' in args: return SimpleNamespace(returncode=0,stdout=container_id+'\n',stderr='')
            if 'start' in args: raise setup.SetupError('port in use')
            if 'logs' in args: return SimpleNamespace(returncode=0,stdout='start failure details',stderr='')
            return SimpleNamespace(returncode=0,stdout='{}',stderr='')
    runner=CandidateRunner();tool=tool_at(tmp_path,runner)
    with pytest.raises(setup.SetupError,match='port in use'): tool.validate_candidate('fixture-volume','fixture')
    assert 'start failure details' in (tool.state/'logs/validation-fixture.log').read_text()
    assert runner.calls[-1][-3:]==['rm','-f',container_id]
    assert not (tool.state/'active.json').exists()
