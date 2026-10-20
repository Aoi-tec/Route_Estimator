"""APIキーの読込・保存。キーはリポジトリ直下の .env（Git対象外）に置く。

優先順位: 環境変数 > .env > 旧形式ファイル(state/geoapify_api_key.txt)
.env は `NAME=value` 形式。旧形式（キー1行のみ）のファイルも読める。
"""
import os
from pathlib import Path
import uuid

MAX_BYTES = 64 * 1024
LEGACY_KEY_FILE = Path('state/geoapify_api_key.txt')


def _parse_line(line):
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
        return None
    if line.startswith('export '):
        line = line[len('export '):].lstrip()
    name, value = line.split('=', 1)
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1]
    return name.strip(), value


def read_key_file(path, env_name):
    """ファイルからキーを返す。無ければNone。"""
    path = Path(path)
    with path.open(encoding='utf-8-sig') as stream:
        text = stream.read(MAX_BYTES)
    lines = text.splitlines()
    if any(_parse_line(line) for line in lines):
        for line in lines:
            parsed = _parse_line(line)
            if parsed and parsed[0] == env_name:
                return parsed[1]
        return None
    raw = text.strip()  # 旧形式: キー1行のみ
    return raw or None


def resolve_api_key(env_name, key_file, root=None):
    """環境変数 > key_file(.env) > 旧形式ファイル の順に探す。見つからなければNone。"""
    value = os.environ.get(env_name)
    if value is not None:
        return value
    candidates = [Path(key_file)]
    if root is not None:
        legacy = Path(root) / LEGACY_KEY_FILE
        if legacy not in candidates:
            candidates.append(legacy)
    for candidate in candidates:
        try:
            key = read_key_file(candidate, env_name)
        except FileNotFoundError:
            continue
        except (OSError, UnicodeError):
            return None
        if key:
            return key
    return None


def write_key_file(path, env_name, key):
    """.env の env_name 行だけを置換（無ければ追記）。他の行は保持し、0600で原子的に書き込む。"""
    path = Path(path)
    lines = []
    try:
        with path.open(encoding='utf-8-sig') as stream:
            lines = stream.read(MAX_BYTES).splitlines()
    except FileNotFoundError:
        pass
    if lines and not any(_parse_line(line) for line in lines):
        lines = []  # 旧形式ファイルは .env 形式へ置き換える
    out, replaced = [], False
    for line in lines:
        parsed = _parse_line(line)
        if parsed and parsed[0] == env_name:
            if not replaced:
                out.append(f'{env_name}={key}')
                replaced = True
            continue
        out.append(line)
    if not replaced:
        out.append(f'{env_name}={key}')
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write('\n'.join(out) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
