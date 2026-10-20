"""Windows/Mac/Linux共通の導入・道路データ・Docker管理。Python標準ライブラリのみ。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener
import uuid

ROOT = Path(__file__).resolve().parents[1]
GIB = 1024 ** 3
STAGES = ['copied', 'extract', 'partition', 'customize', 'validated']


class SetupError(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('w', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def operation_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise SetupError(f'別のセットアップが実行中か、中断時のロックが残っています: {path}。実行中でないことを確認してから削除してください') from exc
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({'pid': os.getpid(), 'started_at': now()}, stream)
        yield
    finally:
        path.unlink(missing_ok=True)


def valid_url(url):
    try:
        p = urlsplit(url)
        return p.scheme == 'https' and p.hostname == 'download.geofabrik.de' and p.port in (None,443) and not p.username and not p.password and not p.query and not p.fragment and '..' not in p.path.split('/')
    except (TypeError, ValueError):
        return False


def asset_name(url):
    if not valid_url(url): raise SetupError('地図の取得先はGeofabrikの許可済みHTTPS URLにしてください')
    name = Path(urlsplit(url).path).name
    if not re.fullmatch(r'[A-Za-z0-9_-]+\.osm\.pbf', name): raise SetupError('地図ファイル名が不正です')
    return name


def load_settings(path: Path, root: Path):
    try:
        def unique(pairs):
            result={}
            for key,value in pairs:
                if key in result: raise ValueError('重複設定キー: '+key)
                result[key]=value
            return result
        data = json.loads(path.read_text(encoding='utf-8-sig'),object_pairs_hook=unique)
        for name in ('map_name', 'map_url', 'downloads_directory', 'venv_directory', 'image'):
            if not isinstance(data[name], str) or not data[name].strip(): raise ValueError(name)
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,40}', data['map_name']): raise ValueError('map_name')
        asset_name(data['map_url'])
        if not re.fullmatch(r'ghcr\.io/project-osrm/osrm-backend@sha256:[a-f0-9]{64}', data['image']): raise ValueError('image')
        ranges = {'cpus':(1,256), 'build_memory_gib':(1,1024), 'serve_memory_gib':(1,1024),
                  'docker_memory_reserve_gib':(1,128), 'minimum_free_disk_gib':(1,100000),
                  'host_port':(1,65535), 'validation_port':(1,65535), 'ready_timeout_seconds':(1,3600),
                  'download_timeout_seconds':(30,86400), 'max_snap_distance_m':(0,10000)}
        allowed=set(ranges)|{'map_name','map_url','downloads_directory','venv_directory','image','serve_mmap','smoke_tests'}
        if set(data)-allowed: raise ValueError('未知の設定キー: '+','.join(sorted(set(data)-allowed)))
        for key,(low,high) in ranges.items():
            if type(data[key]) is not int or not low <= data[key] <= high: raise ValueError(key)
        if data['host_port'] == data['validation_port']: raise ValueError('ports must differ')
        if type(data['serve_mmap']) is not bool: raise ValueError('serve_mmap')
        if not isinstance(data['smoke_tests'],list) or not data['smoke_tests']: raise ValueError('smoke_tests')
        for case in data['smoke_tests']:
            if not isinstance(case['name'],str) or not case['name']: raise ValueError('smoke test name')
            for key in ('origin','destination'):
                coords=case[key]
                if not isinstance(coords,list) or len(coords)!=2: raise ValueError('coordinates')
                for value,limit in zip(coords,(180,90)):
                    if type(value) not in (int,float) or not math.isfinite(value) or abs(value)>limit: raise ValueError('coordinates')
        for name in ('downloads_directory','venv_directory'):
            p=Path(data[name]).expanduser()
            data[name]=p.resolve() if p.is_absolute() else (root/p).resolve()
        if data['venv_directory']==root.resolve(): raise ValueError('venv must not be project root')
        return data
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise SetupError(f'セットアップ設定が不正です: {path} / {exc}') from exc


def file_hash(path, algorithm='sha256'):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,algorithm).hexdigest()


def verify_file(path, descriptor):
    if not path.is_file() or path.stat().st_size != descriptor['bytes']:
        raise SetupError(f'取得済み原本のサイズが一致しません: {path}')
    if file_hash(path) != descriptor['sha256'] or file_hash(path,'md5') != descriptor['md5']:
        raise SetupError(f'取得済み原本のハッシュが一致しません: {path}。退避してから再取得してください')


def build_identity(config, descriptor):
    value={'pbf_sha256':descriptor['sha256'],'image':config['image'],'profile':'/opt/car.lua','algorithm':'mld','map_name':config['map_name'],'format_version':1}
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def completed_stages(marker, identity):
    if marker is None: return []
    completed=marker.get('completed')
    if marker.get('build_id')!=identity or not isinstance(completed,list) or completed != STAGES[:len(completed)] or len(completed)>len(STAGES):
        raise SetupError('Docker volumeのチェックポイントが入力原本/イメージ/工程順と一致しません')
    return completed.copy()


def resource_report(config, info, free_disk):
    memory=info.get('MemTotal',0)/GIB
    reserve=config['docker_memory_reserve_gib']
    common=[]
    if info.get('OSType')!='linux': common.append('Docker DesktopをLinuxコンテナモードで起動してください')
    if info.get('NCPU',0)<config['cpus']: common.append('DockerのCPU割当が設定値より少ないです')
    build=common.copy(); serve=common.copy()
    if memory < config['build_memory_gib']+reserve: build.append(f"Dockerメモリ {memory:.1f}GiB < 前処理上限{config['build_memory_gib']}+予備{reserve}GiB")
    if memory < config['serve_memory_gib']+reserve: serve.append(f"Dockerメモリ {memory:.1f}GiB < 配信上限{config['serve_memory_gib']}+予備{reserve}GiB")
    if free_disk < config['minimum_free_disk_gib']*GIB: build.append('ホスト保存先の空き容量が設定した下限を下回っています')
    return {'docker_memory_gib':round(memory,2),'docker_cpus':info.get('NCPU',0),'host_free_disk_gib':round(free_disk/GIB,2),
            'build_ready':not build,'serve_ready':not serve,'build_problems':build,'serve_problems':serve,'problems':list(dict.fromkeys(build+serve)),
            'note':'必要容量の実測保証ではありません。Docker仮想ディスクの空き容量も別途確認してください。'}


def venv_python(path, *, windows=None):
    windows=os.name=='nt' if windows is None else windows
    return path/('Scripts/python.exe' if windows else 'bin/python')


def initialize_app_files(root):
    for source,target in [('config.example.json','config.json'),('data/customers.example.csv','data/customers.csv')]:
        dst=root/target
        if not dst.exists():
            dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(root/source,dst)


class Runner:
    def __init__(self, root): self.root=root

    def run(self,args,*,capture=False,check=True,stdin=None,log=None):
        args=[str(a) for a in args]
        try:
            if log:
                log.parent.mkdir(parents=True,exist_ok=True)
                with log.open('a',encoding='utf-8') as stream:
                    stream.write('\n--- '+now()+' ---\n')
                    with subprocess.Popen(args,cwd=self.root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace') as process:
                        try:
                            for line in process.stdout:
                                print(line,end='',flush=True);stream.write(line);stream.flush()
                            code=process.wait()
                        except KeyboardInterrupt:
                            process.terminate()
                            try: process.wait(timeout=15)
                            except subprocess.TimeoutExpired: process.kill();process.wait()
                            raise
                result=subprocess.CompletedProcess(args,code,'','')
            else:
                result=subprocess.run(args,cwd=self.root,input=stdin,text=True,encoding='utf-8',errors='replace',capture_output=capture)
        except FileNotFoundError as exc:
            raise SetupError(f'必要なコマンドが見つかりません: {args[0]}') from exc
        if check and result.returncode:
            detail=(result.stderr or result.stdout or '')[-1800:]
            raise SetupError(f'コマンド失敗（終了{result.returncode}）: {args[0]} / {detail}')
        return result


def validate_route(payload,max_snap):
    try:
        if payload['code']!='Ok' or not payload['routes'] or len(payload['waypoints'])!=2: raise ValueError('no route')
        route=payload['routes'][0]
        numbers=[route['distance'],route['duration'],*[p['distance'] for p in payload['waypoints']]]
        if any(type(n) not in (int,float) or not math.isfinite(n) or n<0 for n in numbers): raise ValueError('invalid measurements')
        if max(numbers[2:])>max_snap: raise ValueError('snap distance exceeds threshold')
        return {'distance_m':numbers[0],'duration_s':numbers[1],'snap_distances_m':numbers[2:]}
    except (KeyError,TypeError,ValueError,IndexError) as exc:
        raise SetupError(f'候補OSRMの経路応答が不正または精度不足です: {exc}') from exc


class Provisioner:
    def __init__(self,root,settings_path=None,runner=None):
        self.root=Path(root).resolve()
        settings_path=Path(settings_path) if settings_path else self.root/'deploy/setup.json'
        if not settings_path.exists():
            settings_path.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(self.root/'deploy/setup.example.json',settings_path)
        self.config=load_settings(settings_path,self.root)
        self.state=self.root/'state/setup';self.state.mkdir(parents=True,exist_ok=True)
        self.runner=runner or Runner(self.root)
        self.docker=shutil.which('docker') or 'docker'
        if os.name=='nt' and self.docker=='docker':
            candidate=Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'Docker/Docker/resources/bin/docker.exe'
            if candidate.exists(): self.docker=str(candidate)
        self.curl=shutil.which('curl') or 'curl'

    def execute(self,*args,**kwargs): return self.runner.run(args,**kwargs)

    def environment(self):
        print('[Python] 仮想環境と依存パッケージを準備します',flush=True)
        path=self.config['venv_directory'];python=venv_python(path)
        if path.exists():
            valid=False
            if python.exists():
                try:
                    result=self.execute(python,'-c','import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)',capture=True,check=False)
                    valid=result.returncode==0
                except (SetupError,OSError): pass
            if not valid:
                if not path.is_dir() or (any(path.iterdir()) and not (path/'pyvenv.cfg').is_file()):
                    raise SetupError(f'venv以外の既存ディレクトリを変更しません: {path}')
                if (path/'pyvenv.cfg').is_file():
                    backup=path.with_name(path.name+'.backup-'+uuid.uuid4().hex[:8]);path.rename(backup)
                    print(f'OS/版が異なるvenvを退避しました: {backup}')
        if not python.exists(): self.execute(sys.executable,'-m','venv',path)
        self.execute(python,'-m','pip','install','--disable-pip-version-check','-r',self.root/'requirements.txt')
        self.execute(python,'-m','pip','install','--disable-pip-version-check','-e',self.root)
        initialize_app_files(self.root)
        return python

    def docker_info(self,install=False):
        if not shutil.which(self.docker):
            if install and os.name=='nt' and shutil.which('winget'):
                self.execute('winget','install','--id','Docker.DockerDesktop','--exact')
                candidate=Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'Docker/Docker/resources/bin/docker.exe'
                if candidate.exists(): self.docker=str(candidate)
            if not shutil.which(self.docker): raise SetupError('Docker Desktopがありません。公式配布版をインストールし起動してください: https://www.docker.com/products/docker-desktop/')
        result=self.execute(self.docker,'info','--format','{{json .}}',capture=True,check=False)
        if result.returncode and install and sys.platform in ('win32','darwin'):
            print('Docker Desktopの起動を試します（最大60秒）',flush=True)
            self.execute(self.docker,'desktop','start','--timeout','60',capture=True,check=False)
            result=self.execute(self.docker,'info','--format','{{json .}}',capture=True,check=False)
        if result.returncode: raise SetupError('Dockerエンジンへ接続できません。Docker Desktop起動、WSL2/仮想化、Linuxコンテナ設定と権限を確認してください')
        try: return json.loads(result.stdout)
        except ValueError as exc: raise SetupError('Docker情報のJSON形式が不正です') from exc

    def pull(self):
        self.execute(self.docker,'pull',self.config['image'])
        self.execute(self.docker,'compose','version')

    def doctor(self, *, strict=None):
        directory=self.config['downloads_directory'];directory.mkdir(parents=True,exist_ok=True)
        info=self.docker_info()
        report=resource_report(self.config,info,shutil.disk_usage(directory).free)
        atomic_json(self.state/'resources.json',dict(report,checked_at=now()))
        print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
        if strict=='build' and not report['build_ready']: raise SetupError('全国前処理を開始しません。deploy/setup.jsonとDocker Desktopの割当/空き容量を調整してください')
        if strict=='serve' and not report['serve_ready']: raise SetupError('配信を開始しません。Dockerメモリ/CPU割当と設定を調整してください')
        return report

    def download_path(self): return self.state/'downloads'/f"{self.config['map_name']}.json"

    def downloaded(self):
        path=self.download_path()
        if not path.exists(): raise SetupError('道路データが未取得です。download_japan.bat またはdownloadコマンドを実行してください')
        descriptor=json.loads(path.read_text(encoding='utf-8'))
        if descriptor.get('source_url')!=self.config['map_url']: raise SetupError('地図URLが変わっています。download --refreshで取得対象を確認してください')
        filename=asset_name(descriptor['resolved_url'])
        file=self.config['downloads_directory']/filename
        verify_file(file,descriptor)
        return file,descriptor

    def curl_args(self):
        return [self.curl,'--fail','--location','--silent','--show-error','--proto','=https','--proto-redir','=https','--connect-timeout','15','--retry','3']

    def download(self,refresh=False):
        if self.download_path().exists() and not refresh:
            path,descriptor=self.downloaded();print(f'[取得済み・ハッシュ確認] {path}',flush=True);return descriptor
        directory=self.config['downloads_directory'];directory.mkdir(parents=True,exist_ok=True)
        if shutil.disk_usage(directory).free < self.config['minimum_free_disk_gib']*GIB:
            raise SetupError('地図保存先の空き容量が設定下限を下回っています')
        headers=self.state/'download-headers.txt'
        result=self.runner.run(self.curl_args()+['--head','--max-time','120','--output',str(headers),'--write-out','%{url_effective}',self.config['map_url']],capture=True)
        resolved=result.stdout.strip();filename=asset_name(resolved)
        if 'latest' in filename: raise SetupError('日付付き固定原本への解決ができません。最新URLからMD5だけを別取得しないでください')
        md5text=self.runner.run(self.curl_args()+['--max-time','120',resolved+'.md5'],capture=True).stdout
        expected=md5text.split()[0].lower() if md5text.split() else ''
        if not re.fullmatch(r'[a-f0-9]{32}',expected): raise SetupError('配布元MD5の形式が不正です')
        target=directory/filename;partial=target.with_suffix('.pbf.part')
        if target.exists():
            if file_hash(target,'md5')!=expected: raise SetupError(f'既存原本のMD5が不一致です。退避して再取得してください: {target}')
        else:
            print(f'[全国原本取得・中断再開対応] {resolved}\n保存先: {partial}',flush=True)
            self.runner.run(self.curl_args()+['--max-time',str(self.config['download_timeout_seconds']),'--continue-at','-','--output',str(partial),resolved])
            if file_hash(partial,'md5')!=expected: raise SetupError('全国PBFのMD5が一致しません。部分ファイルを保持して停止しました')
            os.replace(partial,target)
        descriptor={'source_url':self.config['map_url'],'resolved_url':resolved,'file_name':filename,'bytes':target.stat().st_size,
                    'md5':expected,'sha256':file_hash(target),'verified_at':now()}
        atomic_json(self.download_path(),descriptor)
        target.with_suffix('.pbf.md5').write_text(md5text,encoding='utf-8')
        print(f"[取得・検証完了] {target} / {descriptor['bytes']:,} bytes / SHA-256 {descriptor['sha256']}",flush=True)
        return descriptor

    def helper(self,volume,*args,readonly=False,stdin=None,check=True):
        command=[self.docker,'run','--rm']
        if stdin is not None: command.append('-i')
        command+=['-v',volume+':/data'+(':ro' if readonly else ''),self.config['image'],*args]
        return self.runner.run(command,capture=True,stdin=stdin,check=check)

    def marker(self,volume):
        result=self.helper(volume,'cat','/data/.setup-checkpoint.json',readonly=True,check=False)
        if result.returncode:
            if 'No such file' in (result.stderr or ''): return None
            raise SetupError('Docker volumeのチェックポイントを読めません: '+(result.stderr or '')[-500:])
        try: return json.loads(result.stdout)
        except ValueError as exc: raise SetupError('volumeのチェックポイントJSONが不正です') from exc

    def save_marker(self,volume,value):
        self.helper(volume,'sh','-c','cat > /data/.setup-checkpoint.json.tmp && mv /data/.setup-checkpoint.json.tmp /data/.setup-checkpoint.json',stdin=json.dumps(value))
        atomic_json(self.state/'builds'/(value['build_id']+'.json'),value)

    def route_request(self,port,case):
        a,b=case['origin'],case['destination']
        url=f'http://127.0.0.1:{port}/route/v1/driving/{a[0]},{a[1]};{b[0]},{b[1]}?overview=false&steps=false'
        with build_opener(ProxyHandler({})).open(url,timeout=3) as response:
            body=response.read(1_000_001)
            if len(body)>1_000_000: raise SetupError('OSRM応答サイズ超過')
            return validate_route(json.loads(body),self.config['max_snap_distance_m'])

    def wait_ready(self,port,container_name=None):
        end=time.monotonic()+self.config['ready_timeout_seconds'];last='未応答'
        next_inspect=0
        while time.monotonic()<end:
            try: self.route_request(port,self.config['smoke_tests'][0]);return
            except (OSError,ValueError,SetupError) as exc: last=str(exc)
            if container_name and time.monotonic()>=next_inspect:
                state=self.execute(self.docker,'inspect','--format','{{json .State}}',container_name,capture=True,check=False)
                if state.returncode: raise SetupError('OSRMコンテナの状態を確認できません')
                info=json.loads(state.stdout)
                if not info.get('Running'):
                    raise SetupError(f"OSRMが起動中に停止しました（exit={info.get('ExitCode')}, OOM={info.get('OOMKilled')}）")
                next_inspect=time.monotonic()+5
            time.sleep(1)
        raise SetupError('OSRMの準備完了を待てませんでした: '+last)

    def smoke(self,port,container_name=None):
        self.wait_ready(port,container_name)
        results=[]
        for case in self.config['smoke_tests']:
            result=self.route_request(port,case);results.append(dict(name=case['name'],**result))
        return results

    def routed_args(self):
        args=['osrm-routed','--algorithm','mld','--threads',str(self.config['cpus']),'--verbosity','WARNING']
        if self.config['serve_mmap']: args.append('--mmap=1')
        return args+['/data/'+self.config['map_name']+'.osrm']

    def validate_candidate(self,volume,identity):
        name='route-estimator-validate-'+identity[:12]
        args=[self.docker,'create','--name',name,'--memory',f"{self.config['serve_memory_gib']}g",'--cpus',str(self.config['cpus']),
              '-p',f"127.0.0.1:{self.config['validation_port']}:5000",'-v',volume+':/data:ro',self.config['image'],*self.routed_args()]
        # create成功時のIDを使い、start失敗時も自分の候補だけを確実に後片付けする。
        container_id=self.runner.run(args,capture=True).stdout.strip()
        if not re.fullmatch(r'[a-f0-9]{64}',container_id): raise SetupError('作成した候補コンテナIDを確認できません')
        try:
            self.execute(self.docker,'start',container_id,capture=True)
            return self.smoke(self.config['validation_port'],container_name=container_id)
        except (SetupError,OSError,ValueError,KeyboardInterrupt):
            try:
                logs=self.execute(self.docker,'logs',container_id,capture=True,check=False)
                status=self.execute(self.docker,'inspect','--format','{{json .State}}',container_id,capture=True,check=False)
                path=self.state/'logs'/('validation-'+identity+'.log');path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text((logs.stdout or '')+(logs.stderr or '')+'\n'+(status.stdout or ''),encoding='utf-8')
                print('候補起動失敗の記録: '+str(path),flush=True)
            except (SetupError,OSError): pass
            raise
        finally:
            self.execute(self.docker,'rm','-f',container_id,capture=True,check=False)

    def build(self):
        self.doctor(strict='build')
        path,descriptor=self.downloaded();identity=build_identity(self.config,descriptor)
        volume='route-estimator-'+self.config['map_name']+'-'+identity[:16]
        result=self.execute(self.docker,'volume','inspect',volume,capture=True,check=False)
        if result.returncode:
            if 'no such volume' not in (result.stderr or '').lower(): raise SetupError('Docker volumeを確認できません: '+(result.stderr or ''))
            self.execute(self.docker,'volume','create',volume,capture=True)
            marker=None
        else: marker=self.marker(volume)
        completed=completed_stages(marker,identity)
        record={'build_id':identity,'volume':volume,'map_name':self.config['map_name'],'image':self.config['image'],
                'download':descriptor,'completed':completed,'updated_at':now()}
        if 'copied' not in completed:
            loader='route-estimator-loader-'+uuid.uuid4().hex[:12]
            self.execute(self.docker,'create','--name',loader,'-v',volume+':/data',self.config['image'],capture=True)
            try: self.execute(self.docker,'cp',path,loader+':/data/'+self.config['map_name']+'.osm.pbf')
            finally: self.execute(self.docker,'rm','-f',loader,capture=True,check=False)
            digest=self.helper(volume,'sha256sum','/data/'+self.config['map_name']+'.osm.pbf',readonly=True).stdout.split()[0]
            if digest!=descriptor['sha256']: raise SetupError('DockerへコピーしたPBFのSHA-256が不一致です')
            completed.append('copied');self.save_marker(volume,record)
        for stage in ('extract','partition','customize'):
            if stage in completed:
                print(f'[再利用] {stage}',flush=True);continue
            name='route-estimator-'+stage+'-'+identity[:12]
            command=[self.docker,'run','--rm','--name',name,'--memory',f"{self.config['build_memory_gib']}g",'--cpus',str(self.config['cpus']),'-v',volume+':/data',self.config['image'],'osrm-'+stage,'-t',str(self.config['cpus'])]
            if stage=='extract': command+=['-p','/opt/car.lua','/data/'+self.config['map_name']+'.osm.pbf']
            else: command+=['/data/'+self.config['map_name']+'.osrm']
            print(f'[前処理] {stage}',flush=True)
            try:
                self.runner.run(command,log=self.state/'logs'/(identity+'-'+stage+'.log'))
            except KeyboardInterrupt:
                self.execute(self.docker,'stop',name,capture=True,check=False)
                raise
            completed.append(stage);record['updated_at']=now();self.save_marker(volume,record)
        # 中断・再実行時も配信前に毎回候補の経路を検証する。
        record['smoke_results']=self.validate_candidate(volume,identity)
        if 'validated' not in completed: completed.append('validated')
        record['updated_at']=now();self.save_marker(volume,record)
        atomic_json(self.state/'ready.json',record)
        print('[構築と候補サーバー検証完了] '+volume,flush=True)
        return record

    def compose_env(self,record):
        return {'OSRM_IMAGE':record['image'],'OSRM_MAP_VOLUME':record['volume'],'OSRM_DATASET_BASENAME':record['map_name'],
                'OSRM_CPUS':str(self.config['cpus']),'OSRM_SERVE_MEMORY':f"{self.config['serve_memory_gib']}g",'OSRM_HOST_PORT':str(self.config['host_port']),
                'OSRM_MMAP':'--mmap=1' if self.config['serve_mmap'] else ''}

    def compose(self,env,*,legacy=False):
        path=self.state/'runtime.env'
        path.write_text(''.join(k+'='+v+'\n' for k,v in env.items()),encoding='utf-8')
        file=self.root/'deploy'/('compose.kansai.yaml' if legacy else 'compose.yaml')
        return [self.docker,'compose','--env-file',str(path),'-f',str(file)]

    def existing_server(self):
        result=self.execute(self.docker,'inspect','route-estimator-osrm',capture=True,check=False)
        if result.returncode:
            if 'no such' in (result.stderr or '').lower(): return None
            raise SetupError('既存サーバーを確認できません: '+(result.stderr or ''))
        data=json.loads(result.stdout)[0]
        labels=data['Config'].get('Labels') or {}
        if labels.get('com.docker.compose.project')!='route-estimator' or labels.get('com.docker.compose.service')!='osrm':
            raise SetupError('同名の別用途コンテナが存在するため変更しません')
        return data

    def start(self):
        self.doctor(strict='serve')
        ready_path=self.state/'ready.json'
        if not ready_path.exists(): raise SetupError('全国データはまだ前処理/経路検証が完了していません。メモリを調整後にwindows\\setup.batを実行してください。既存の近畿サーバーは変更しません')
        record=json.loads(ready_path.read_text(encoding='utf-8'))
        _,descriptor=self.downloaded();identity=build_identity(self.config,descriptor)
        if record['build_id']!=identity: raise SetupError('設定/原本が構築済みデータと違います。buildを実行してください')
        if completed_stages(self.marker(record['volume']),identity)!=STAGES: raise SetupError('volumeの構築完了を確認できません')
        # 前回の検証結果だけを信用せず、現在の候補を再確認する。
        record['smoke_results']=self.validate_candidate(record['volume'],identity)
        active_path=self.state/'active.json'
        previous=json.loads(active_path.read_text(encoding='utf-8')) if active_path.exists() else None
        existing=self.existing_server();legacy=False
        if existing and previous is None:
            commands=existing['Config'].get('Cmd') or []
            volumes=[m.get('Name') for m in existing.get('Mounts',[])]
            legacy='/data/kansai-latest.osrm' in commands and 'route-estimator-maps' in volumes
            if not legacy: raise SetupError('管理記録のない既存サーバーのため自動切替しません')
        env=self.compose_env(record)
        try:
            self.runner.run(self.compose(env)+['up','-d'])
            record['smoke_results']=self.smoke(self.config['host_port'],container_name='route-estimator-osrm')
        except (SetupError,OSError,ValueError,KeyboardInterrupt):
            print('起動検証に失敗したため直前の配信設定へ戻します',flush=True)
            if previous: self.runner.run(self.compose(previous['compose_env'])+['up','-d'])
            elif legacy: self.runner.run(self.compose({},legacy=True)+['up','-d'])
            else: self.runner.run(self.compose(env)+['down'])
            raise
        atomic_json(active_path,dict(record,compose_env=env,activated_at=now()))
        print('[配信開始] http://127.0.0.1:'+str(self.config['host_port']),flush=True)

    def stop(self):
        self.docker_info();existing=self.existing_server()
        if existing:
            self.execute(self.docker,'stop','route-estimator-osrm')
        print('OSRM停止。地図volumeと取得原本は保持しています')

    def status(self):
        print('設定:',self.config['map_name'],self.config['map_url'])
        for label,path in [('取得記録',self.download_path()),('構築済み候補',self.state/'ready.json'),('全国配信記録',self.state/'active.json')]:
            print(label+': '+('あり（詳細は '+str(path)+'）' if path.exists() else '未作成'))
        try:
            self.docker_info();existing=self.existing_server()
            if existing:
                cmd=' '.join(existing['Config'].get('Cmd') or [])
                mode='近畿（従来構成）' if 'kansai-latest.osrm' in cmd else '構築済み地図（active.jsonと照合してください）'
                print('実コンテナ:',existing['State']['Status'],'/',mode)
            else: print('配信コンテナ: なし')
        except SetupError as exc: print(str(exc))

    def run_cui(self,demo=False):
        python=venv_python(self.config['venv_directory'])
        if not python.exists(): raise SetupError('venv未作成です。windows\\setup.bat / mac/setup.command --prepare-onlyを実行してください')
        config=self.root/('config.demo.json' if demo else 'config.json')
        data=json.loads(config.read_text(encoding='utf-8-sig'))
        expected='http://127.0.0.1:'+str(self.config['host_port'])
        provider=data.get('routing',{}).get('provider','osrm')
        if provider=='osrm':
            if data['osrm']['base_url'].rstrip('/')!=expected: raise SetupError(f'{config} のosrm.base_urlを {expected} に合わせてください（自動上書きしません）')
            with operation_lock(self.state/'operation.lock'):
                self.start()
        elif provider!='geoapify':raise SetupError('routing.providerが不正です')
        args=[python,self.root/'from_to.py','--config',config]
        if demo: args+=['--customers',self.root/'data/customers.demo.csv']
        return self.runner.run(args,check=False).returncode


def main(argv=None):
    parser=argparse.ArgumentParser(description='全国OSRMとPython環境の一式セットアップ')
    parser.add_argument('action',choices=['all','prepare','env','download','doctor','build','start','run','stop','status','key'])
    parser.add_argument('--settings',type=Path)
    parser.add_argument('--prepare-only',action='store_true',help='全国の前処理をせず、環境・原本・イメージの準備まで')
    parser.add_argument('--refresh',action='store_true',help='最新URLを再確認して新しい日付付き原本を取得')
    parser.add_argument('--demo',action='store_true',help='公開地点デモのCUIを使用')
    args=parser.parse_args(argv)
    if args.prepare_only and args.action not in ('all','prepare'):
        parser.error('--prepare-onlyはsetup(all)/prepareで使用してください')
    if args.refresh and args.action not in ('all','prepare','download'):
        parser.error('--refreshは取得を含むall/prepare/downloadで使用してください')
    if args.demo and args.action!='run':
        parser.error('--demoはwindows\\start.bat / mac/start.command(run)で使用してください')
    try:
        tool=Provisioner(ROOT,args.settings)
        action='prepare' if args.prepare_only and args.action=='all' else args.action
        if action=='run': return tool.run_cui(args.demo)
        config_path=ROOT/'config.json'
        if not config_path.exists():config_path=ROOT/'config.example.json'
        provider=json.loads(config_path.read_text(encoding='utf-8-sig')).get('routing',{}).get('provider','osrm') if config_path.exists() else 'osrm'
        if provider not in ('osrm','geoapify'):raise SetupError('routing.providerが不正です')
        if provider=='geoapify' and action in ('start','stop','status'):
            print('[Geoapify API方式] 常駐サーバーは不要です。windows\\start.bat / mac/start.commandでCUIを起動し、doctorでキー読込を確認できます。')
            return 0
        if provider=='geoapify' and action=='doctor':
            python=venv_python(tool.config['venv_directory'])
            if not python.exists():raise SetupError('先にsetupを実行してください')
            return tool.runner.run([python,ROOT/'from_to.py','--check'],check=False).returncode
        if action=='status':tool.status();return 0
        if action in ('all','prepare') and provider=='geoapify':
            with operation_lock(tool.state/'operation.lock'):tool.environment()
            print('[API方式の準備完了] Docker/地図取得は不要です。configure_api_keyでキー登録後にstartを実行してください',flush=True)
            return 0
        if action=='key':
            python=venv_python(tool.config['venv_directory'])
            if not python.exists():raise SetupError('先にsetupを実行してください')
            return tool.runner.run([python,ROOT/'configure_api_key.py'],check=False).returncode
        with operation_lock(tool.state/'operation.lock'):
            if action in ('all','prepare','env'): tool.environment()
            if action in ('all','prepare'): tool.docker_info(install=True)
            if action in ('all','prepare','download'): tool.download(args.refresh)
            if action in ('all','prepare'): tool.pull()
            if action in ('prepare','doctor'): tool.doctor()
            if action in ('all','build'): tool.build()
            if action in ('all','start'): tool.start()
            if action=='stop': tool.stop()
        return 0
    except KeyboardInterrupt:
        print('\n中断しました。取得済み原本/成功した前処理は保持しています。',file=sys.stderr);return 130
    except (SetupError,OSError,ValueError,KeyError) as exc:
        print('セットアップ停止: '+str(exc),file=sys.stderr);return 2


if __name__=='__main__':
    if sys.version_info < (3,12): raise SystemExit('Python 3.12以上が必要です')
    raise SystemExit(main())
