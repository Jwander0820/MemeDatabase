"""私人管理端的 VPS 背景發布；SSH 僅執行固定發布命令，不接收任意指令。"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import copy
import json
import logging
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import uuid

from .release_bundle import build_bundle
from .releases import VERSION_RE, validate

log = logging.getLogger(__name__)
DEFAULT_CONFIG = {'ssh_host': '', 'release_root': '/srv/memedatabase/releases'}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def config_value(data):
    if not isinstance(data, dict) or set(data) != set(DEFAULT_CONFIG):
        raise ValueError('設定只接受 ssh_host 與 release_root')
    host, root = data['ssh_host'], data['release_root']
    if not isinstance(host, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._@-]{0,199}', host):
        raise ValueError('請填入有效的 SSH 主機別名或 user@host')
    if not isinstance(root, str) or not re.fullmatch(r'/[a-zA-Z0-9._/-]+', root) or '..' in root.split('/') or root == '/':
        raise ValueError('請填入安全的遠端發布目錄絕對路徑')
    return {'ssh_host': host, 'release_root': root.rstrip('/')}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def ssh_options():
    options = ['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15']
    directory = os.environ.get('MEMES_VPS_SSH_DIR')
    if not directory:
        yield options  # 原生本機服務沿用既有 SSH config／agent。
        return
    source = Path(directory)
    if not (source/'known_hosts').is_file() or not (source/'id_ed25519').is_file():
        raise ValueError('SSH 尚未設定：請在本機專用 SSH 目錄提供 id_ed25519 與已核對的 known_hosts')
    # Docker Desktop 掛載的 Unix 權限可能不同，複製到私有暫存後再交給 OpenSSH。
    with tempfile.TemporaryDirectory(prefix='memes-ssh-') as folder:
        target = Path(folder)
        for name in ('config', 'known_hosts', 'id_ed25519'):
            if (source/name).is_file():
                shutil.copyfile(source/name, target/name)
                (target/name).chmod(0o600)
        options += ['-F', str(target/'config') if (target/'config').is_file() else os.devnull,
                    '-o', 'UserKnownHostsFile="' + (target/'known_hosts').as_posix() + '"',
                    '-o', 'IdentitiesOnly=yes', '-i', str(target/'id_ed25519')]
        yield options


class VpsPublication:
    def __init__(self, release_root, state_root, *, run=subprocess.run):
        self.root = Path(release_root).resolve()
        self.directory = Path(state_root)/'vps-publication'
        self.config_path = self.directory/'config.json'
        self.job_path = self.directory/'job.json'
        self.lock = threading.RLock()
        self.run = run
        self.job = self._read(self.job_path, None)
        if self.job and self.job.get('status') == 'running':
            self.job.update(status='interrupted', finished_at=now(),
                            error='本機服務曾重新啟動；遠端結果未確認，請先確認 VPS 狀態後再重試。')
            write_json(self.job_path, self.job)

    @staticmethod
    def _read(path, default):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return copy.deepcopy(default)

    def config(self):
        return self._read(self.config_path, DEFAULT_CONFIG)

    def snapshot(self):
        with self.lock:
            config = self.config()
            versions = []
            folder = self.root/'versions'
            if folder.is_dir():
                for path in sorted(folder.iterdir(), key=lambda p: p.name, reverse=True):
                    if not path.is_dir() or path.is_symlink() or not VERSION_RE.fullmatch(path.name):
                        continue
                    try:
                        manifest = json.loads((path/'manifest.json').read_text(encoding='utf-8'))
                        versions.append({'version': path.name, 'count': manifest['count']})
                    except (OSError, ValueError, KeyError):
                        continue
                    if len(versions) == 20:
                        break
            ssh_dir = os.environ.get('MEMES_VPS_SSH_DIR')
            ssh_ready = not ssh_dir or all((Path(ssh_dir)/name).is_file() for name in ('known_hosts', 'id_ed25519'))
            return {'config': config, 'versions': versions, 'job': copy.deepcopy(self.job),
                    'ssh_available': bool(shutil.which('ssh') and shutil.which('scp')),
                    'ssh_ready': ssh_ready}

    def save_config(self, data):
        data = config_value(data)
        with self.lock:
            if self.job and self.job['status'] == 'running':
                raise ValueError('發布進行中，請完成後再修改目標')
            write_json(self.config_path, data)
        return data

    def start(self, data):
        if (not isinstance(data, dict) or set(data) != {'version', 'activate'} or
                not isinstance(data.get('version'), str) or not VERSION_RE.fullmatch(data['version']) or
                not isinstance(data.get('activate'), bool)):
            raise ValueError('發布只接受有效的 version 與布林 activate')
        config = config_value(self.config())
        if not shutil.which('ssh') or not shutil.which('scp'):
            raise ValueError('本機服務缺少 SSH／SCP 工具')
        if not (self.root/'versions'/data['version']/'manifest.json').is_file():
            raise ValueError('找不到已匯出的本機版本')
        with self.lock:
            if self.job and self.job['status'] == 'running':
                raise ValueError('已有 VPS 發布執行中，請勿重複送出')
            self.job = {'id': uuid.uuid4().hex, **data, 'target': config['ssh_host'],
                        'status': 'running', 'stage': '準備發布', 'started_at': now(),
                        'finished_at': None, 'error': None, 'events': []}
            write_json(self.job_path, self.job)
            self.thread = threading.Thread(target=self._worker, args=(config,), daemon=True, name='vps-publication')
            self.thread.start()
            return copy.deepcopy(self.job)

    def _update(self, stage, **fields):
        with self.lock:
            self.job.update(stage=stage, **fields)
            self.job['events'].append({'time': now(), 'message': stage})
            write_json(self.job_path, self.job)

    def _command(self, args, *, timeout=1800):
        result = self.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace',
                          timeout=timeout, shell=False)
        if result.returncode:
            # 不將遠端 stdout／stderr 或 SSH config 原文送回 UI，避免洩漏敏感資訊。
            raise ValueError(f'{self.job["stage"]}失敗（結束碼 {result.returncode}）；請確認 SSH 金鑰、主機指紋、目錄權限及 VPS 映像版本')
        return result.stdout

    def _worker(self, config):
        remote_bundle = '/tmp/memedatabase-' + self.job['id'] + '.tar.gz'
        uploaded = False
        try:
            with ssh_options() as options, tempfile.TemporaryDirectory(prefix='memes-publish-') as folder:
                host = config['ssh_host']
                image = os.environ.get('MEMES_VPS_IMAGE', 'memedatabase-public-api')
                if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._/:@-]*', image):
                    raise ValueError('VPS 公開映像名稱不合法')

                def remote(args):
                    return self._command(['ssh', *options, host, shlex.join(args)])

                def container(command, *, writable=False, bundle=False):
                    mounts = ['-v', config['release_root'] + ':/releases' + ('' if writable else ':ro')]
                    if bundle:
                        mounts += ['-v', remote_bundle + ':/bundle.tar.gz:ro']
                    return remote(['docker', 'run', '--rm', '--user', '10001:10001', *mounts, image, *command])

                def runtime(*args, **kwargs):
                    return container(['python', '-m', 'src.anime_memes.runtime', *args], **kwargs)

                try:
                    self._update('驗證本機發布包')
                    manifest = validate(self.root, self.job['version'])
                    self._update('確認 VPS 是否已安裝此版')
                    info = json.loads(container(['python', '-c',
                        "import json,sys; from pathlib import Path; "
                        "from src.anime_memes.releases import validate; "
                        "r=Path('/releases'); v=sys.argv[1]; folder=r/'versions'/v; "
                        "print(json.dumps({'installed':folder.exists(), "
                        "'manifest':validate(r,v) if folder.exists() else None, "
                        "'active_version':json.loads((r/'current.json').read_text()).get('version') "
                        "if (r/'current.json').is_file() else None}))", self.job['version']]))
                    if not isinstance(info, dict) or not isinstance(info.get('installed'), bool):
                        raise ValueError('VPS 回傳的安裝狀態不符')
                    if info['installed']:
                        if info.get('manifest') != manifest:
                            raise ValueError('VPS 的同名版本內容不同，不可覆寫')
                        if self.job['activate']:
                            self._update('VPS 已有相同版本，驗證並啟用')
                            activated = json.loads(runtime('activate', '--root', '/releases', self.job['version'], writable=True))
                            if not isinstance(activated, dict) or activated.get('version') != self.job['version']:
                                raise ValueError('VPS 啟用結果不符')
                        result = {'version': self.job['version'], 'count': manifest['count'],
                                  'activated': self.job['activate'], 'already_installed': True}
                        self._update('VPS 已啟用此版' if self.job['activate'] else 'VPS 已安裝相同版本，無須重傳',
                                     status='succeeded', finished_at=now(), result=result)
                        return
                    self._update('取得 VPS 縮圖清單')
                    inventory = json.loads(runtime('media-inventory', '--root', '/releases'))
                    self._update('打包缺少的縮圖與公開資料庫')
                    bundle = Path(folder)/'release.tar.gz'
                    stats = build_bundle(self.root, self.job['version'], bundle, inventory=inventory)
                    self._update('上傳發布包', bundle=stats)
                    uploaded = True  # SCP 部分失敗時也嘗試清除暫存。
                    self._command(['scp', *options, '-q', str(bundle), host + ':' + remote_bundle])
                    remote(['chmod', '644', remote_bundle])
                    self._update('VPS 安裝與驗證' + ('，完成後啟用' if self.job['activate'] else ''))
                    args = ['install-bundle', '--root', '/releases']
                    if self.job['activate']:
                        args.append('--activate')
                    result = json.loads(runtime(*args, '/bundle.tar.gz', writable=True, bundle=True))
                    if not isinstance(result, dict) or result.get('version') != self.job['version'] or result.get('activated') is not self.job['activate']:
                        raise ValueError('VPS 回傳的發布結果不符，請確認遠端版本')
                    self._update('VPS 已啟用此版' if self.job['activate'] else 'VPS 安裝完成，尚未啟用',
                                 status='succeeded', finished_at=now(), result=result)
                finally:
                    if uploaded:
                        try:
                            remote(['rm', '-f', remote_bundle])
                        except (OSError, ValueError, subprocess.SubprocessError):
                            log.warning('VPS publication temporary bundle cleanup failed')
        except Exception:
            # 背景執行緒沒有 HTTP 錯誤處理器，所有例外都必須轉為可查詢的終止狀態。
            log.exception('VPS publication failed')
            self._update('發布未完成，遠端可能已安裝；請確認後再重試', status='failed', finished_at=now(),
                         error='請確認 SSH 認證、主機指紋、VPS 目錄與映像版本；詳細錯誤已記錄於本機服務日誌。')
