"""GitHub Releases update client. Standard library only; never auto-executes."""
from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, unquote
from urllib.request import Request, HTTPSHandler, HTTPRedirectHandler, build_opener
import uuid

from state_lock import state_file_lock

BASE = Path(__file__).resolve().parent
DEFAULT_REPOSITORY = "1245047209-dotcom/1"
CHECK_INTERVAL = 6 * 3600
MAX_INSTALLER_SIZE = 512 * 1024 * 1024


class UpdateError(Exception):
    pass


class InstallerFileError(UpdateError):
    """The downloaded package must be fetched again before installation."""


def version_tuple(value):
    match = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(value).strip())
    if not match:
        raise UpdateError("版本号格式不正确")
    return tuple(int(part or 0) for part in match.groups())


def current_version():
    try:
        value = (BASE / "VERSION").read_text(encoding="utf-8-sig").strip()
        version_tuple(value)
        return value
    except (OSError, UpdateError):
        return "v70"


def repository_name(value):
    value = str(value).strip().removesuffix(".git").rstrip("/")
    if value.startswith("https://github.com/"):
        value = value[len("https://github.com/"):]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", value):
        raise UpdateError("GitHub 仓库地址格式不正确")
    return value


def _https_url(url, hosts):
    try:
        parts = urlsplit(url)
        valid = parts.scheme == "https" and parts.hostname in hosts and parts.port in (None, 443) and not parts.username and not parts.password
    except ValueError:
        valid = False
    if not valid:
        raise UpdateError("更新地址不是允许的 HTTPS 地址")
    return url


_HOSTS = {"api.github.com", "github.com", "release-assets.githubusercontent.com",
          "objects.githubusercontent.com", "github-releases.githubusercontent.com"}


class SecureRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _https_url(newurl, _HOSTS)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url):
    _https_url(url, _HOSTS)
    request = Request(url, headers={"User-Agent": "NexusBatteryCenter/" + current_version(),
                                   "Accept": "application/vnd.github+json" if urlsplit(url).hostname == "api.github.com" else "application/octet-stream",
                                   "X-GitHub-Api-Version": "2022-11-28"})
    return build_opener(HTTPSHandler(), SecureRedirect()).open(request, timeout=15)


def _read_small(url, limit=2 * 1024 * 1024):
    with _open(url) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("更新服务器回复过大")
    return data


def asset_url(url, repository):
    _https_url(url, {"github.com"})
    parts = urlsplit(url)
    prefix = "/" + repository.lower() + "/releases/download/"
    path = unquote(parts.path)
    if not path.lower().startswith(prefix) or parts.fragment or any(part in (".", "..") for part in path.split("/")):
        raise UpdateError("安装包不属于配置的 GitHub 仓库")
    return url


@dataclass(frozen=True)
class Release:
    tag: str
    version: str
    name: str
    notes: str
    page_url: str
    installer_name: str
    download_url: str
    size: int
    sha256: str
    checksum_url: str = ""


def parse_release(data, repository, installed):
    if not isinstance(data, dict):
        raise UpdateError("更新服务器回复格式不正确")
    if data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name", ""))
    latest = version_tuple(tag)
    if latest <= version_tuple(installed):
        return None
    assets = data.get("assets", [])
    if not isinstance(assets, list):
        raise UpdateError("更新附件格式不正确")
    installers = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name", ""))
        match = re.fullmatch(r"Nexus-v(\d+(?:\.\d+){0,2})-Windows-x64-Setup\.exe", name)
        if match and version_tuple(match[1]) == latest and asset.get("state", "uploaded") == "uploaded":
            installers.append(asset)
    if len(installers) != 1:
        raise UpdateError("此版本没有唯一匹配的 Windows x64 安装包，请联系发布者")
    asset = installers[0]
    size = asset.get("size")
    if type(size) is not int or not 64 <= size <= MAX_INSTALLER_SIZE:
        raise UpdateError("安装包大小不正确")
    digest = str(asset.get("digest") or "")
    checksum = digest[7:].lower() if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest) else ""
    checksum_url = ""
    if not checksum:
        for item in assets:
            if not isinstance(item, dict):
                continue
            match = re.fullmatch(r"Nexus-v(\d+(?:\.\d+){0,2})-SHA256\.txt", str(item.get("name", "")))
            if match and version_tuple(match[1]) == latest:
                checksum_url = asset_url(item.get("browser_download_url", ""), repository)
                break
        if not checksum_url:
            raise UpdateError("发布者未提供安装包 SHA-256 校验信息")
    page = "https://github.com/" + repository + "/releases"
    return Release(tag, tag, str(data.get("name") or tag)[:200], str(data.get("body") or "暂无更新说明")[:12000],
                   page, asset["name"], asset_url(asset.get("browser_download_url", ""), repository), size, checksum, checksum_url)


def fetch_latest(repository, installed=None):
    repository = repository_name(repository)
    try:
        raw = _read_small("https://api.github.com/repos/" + repository + "/releases/latest")
        return parse_release(json.loads(raw.decode("utf-8-sig")), repository, installed or current_version())
    except HTTPError as exc:
        if exc.code == 404:
            return None  # A new public repository may not have a release yet.
        if exc.code in (403, 429):
            raise UpdateError("GitHub 暂时限制请求次数，请稍后重试") from exc
        raise UpdateError(f"GitHub 更新服务暂不可用（{exc.code}）") from exc
    except (URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateError("无法连接 GitHub，请检查网络后重试") from exc


def load_state(base=BASE):
    state = {"auto_check": True, "last_check": 0, "notified_tag": "", "ignored_tag": ""}
    try:
        value = json.loads((Path(base) / "update_state.json").read_text(encoding="utf-8"))
        if isinstance(value, dict):
            state.update({key: value[key] for key in state if key in value})
    except (OSError, ValueError):
        pass
    return state


def _write_state(path, state):
    temporary = path.with_name("update-state-" + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_state(base=BASE, **changes):
    path = Path(base) / "update_state.json"
    with state_file_lock(path):
        state = load_state(base)
        state.update({key: val for key, val in changes.items() if key in state})
        _write_state(path, state)
    return state


def check_for_updates(*, force=False, base=BASE):
    path = Path(base) / "update_state.json"
    with state_file_lock(path):
        state = load_state(base)
        now = time.time()
        last = state.get("last_check", 0)
        if not force and (not state.get("auto_check") or isinstance(last, (int, float)) and 0 <= now - last < CHECK_INTERVAL):
            return None
        state["last_check"] = now
        _write_state(path, state)
    release = fetch_latest(DEFAULT_REPOSITORY)
    if not force and release is not None:
        state = load_state(base)
        if not state.get("auto_check") or release.tag in (state.get("ignored_tag"), state.get("notified_tag")):
            return None
    return release


def _checksum_for(release):
    if release.sha256:
        return release.sha256
    asset_url(release.checksum_url, DEFAULT_REPOSITORY)
    text = _read_small(release.checksum_url, 65536).decode("utf-8-sig")
    matches = []
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9A-Fa-f]{64})\s+\*?(.+)", line.strip())
        if match and match[2] == release.installer_name:
            matches.append(match[1].lower())
    if len(matches) != 1:
        raise UpdateError("校验文件中没有唯一匹配的安装包记录")
    return matches[0]


def _is_pe(path):
    with Path(path).open("rb") as stream:
        header = stream.read(64)
        if len(header) < 64 or header[:2] != b"MZ":
            return False
        offset = struct.unpack_from("<I", header, 60)[0]
        if not 64 <= offset < Path(path).stat().st_size - 3:
            return False
        stream.seek(offset)
        return stream.read(4) == b"PE\0\0"


def verify_installer(path, release, checksum):
    path = Path(path)
    if path.stat().st_size != release.size or not _is_pe(path):
        raise UpdateError("下载内容不是完整的 Windows 安装程序")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != checksum:
        raise UpdateError("安装包 SHA-256 校验失败，请重新下载")


def download_installer(release, *, progress=None, cancel=None, directory=None):
    # Revalidate even when a Release object came from a cache or another caller.
    asset_url(release.download_url, DEFAULT_REPOSITORY)
    if not re.fullmatch(r"Nexus-v\d+(?:\.\d+){0,2}-Windows-x64-Setup\.exe", release.installer_name):
        raise UpdateError("安装包文件名不正确")
    checksum = _checksum_for(release)
    if not re.fullmatch(r"[a-f0-9]{64}", checksum):
        raise UpdateError("安装包校验值格式不正确")
    directory = Path(directory) if directory else Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "NexusBatteryCenter" / "updates"
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / (uuid.uuid4().hex + ".part")
    target = directory / release.installer_name
    try:
        received = 0
        with _open(release.download_url) as response, temporary.open("xb") as stream:
            while True:
                if cancel is not None and cancel.is_set():
                    raise UpdateError("下载已取消")
                chunk = response.read(128 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > release.size or received > MAX_INSTALLER_SIZE:
                    raise UpdateError("安装包大小与发布记录不符")
                stream.write(chunk)
                if progress:
                    progress(received, release.size)
        verify_installer(temporary, release, checksum)
        os.replace(temporary, target)
        return target, checksum
    except (URLError, HTTPError, OSError, TimeoutError) as exc:
        raise UpdateError("下载失败，请检查网络与磁盘空间后重试") from exc
    finally:
        temporary.unlink(missing_ok=True)


def launch_installer(path, release, checksum, *, install_dir=BASE):
    """Called only after the user's explicit install click; verify again."""
    try:
        verify_installer(path, release, checksum)
    except OSError as exc:
        raise InstallerFileError("已下载的安装包无法读取，请重新下载") from exc
    except UpdateError as exc:
        raise InstallerFileError(str(exc)) from exc
    install_dir = Path(install_dir).resolve()
    if not (install_dir / "runtime" / "pythonw.exe").is_file():
        raise UpdateError("源码运行模式请手动安装；自动覆盖更新仅用于安装版")
    args = [str(Path(path).resolve()), "/SP-", "/NORESTART", "/DIR=" + str(install_dir)]
    try:
        return subprocess.Popen(args, cwd=str(Path(path).parent), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError as exc:
        raise InstallerFileError("已下载的安装包已找不到，请重新下载") from exc
    except OSError as exc:
        raise UpdateError("无法启动安装程序，请重新尝试") from exc
