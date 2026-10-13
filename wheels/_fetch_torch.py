"""按本机情况自动挑选并安装匹配的 torch 家族（含 CUDA 版本选择）。

为什么需要它
------------
1. **torch 的 CUDA 版本必须 ≤ 驱动支持的版本**，否则 ``torch.cuda.is_available()``
   永远是 False（装了 cu130 但驱动只到 12.7 → GPU 白装）。
2. PyPI 上 Windows 的 torch 是 **CPU-only**，带 CUDA 的只在 ``download.pytorch.org``；
   而 ``uv pip install`` 默认会从 PyPI/镜像挑到 CPU 版，必须显式指定 ``+cuXXX``。
3. 国内直连官方源单连接只有 ~1MB/s 且常抽风；分块多线程能到 ~5MB/s。

本脚本负责
----------
1. 读 nvidia-smi → 驱动支持的 CUDA 版本 → 选 cu126 / cu128 / cu129（选不出就装 CPU 版）
2. 从对应索引解析出与当前 Python / 平台匹配的 torch / torchaudio / torchvision 轮子
3. 多线程分块下载（断点续传 + sha256 校验）
4. 调 uv / pip 安装到指定虚拟环境

用法
----
    python wheels\\_fetch_torch.py                  # 自动选型 → 下载 → 安装到 .venv
    python wheels\\_fetch_torch.py --cuda 12.8      # 强制某 CUDA 版本
    python wheels\\_fetch_torch.py --cpu            # 强制 CPU 版（无显卡/驱动太老）
    python wheels\\_fetch_torch.py --download-only  # 只下载不安装
    python wheels\\_fetch_torch.py --list           # 只看会选哪个版本，不动手
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WHEEL_DIR = Path(__file__).resolve().parent

# CUDA 轮子的索引候选（第一个是实际下载域名，实测比 download.pytorch.org 稳）
CUDA_HOSTS = [
    "https://download-r2.pytorch.org/whl",
    "https://download.pytorch.org/whl",
    "https://mirror.sjtu.edu.cn/pytorch-wheels",
]
# 无 CUDA 时的 PyPI 镜像
PYPI_MIRROR = os.environ.get("PIP_INDEX_URL", "https://pypi.tuna.tsinghua.edu.cn/simple")

# 与 OmniVoice 的 uv constraint 对齐（torch==2.8.0 / torchaudio==2.8.0）
DEFAULT_TORCH = "2.8.0"
# torch 主次版本 -> (torchvision, torchaudio) 配套的完整版本号
# 必须是完整号（如 2.8.0 而不是 2.8），因为轮子文件名里就是完整号
COMPANIONS = {
    "2.5": ("0.20.0", "2.5.0"),
    "2.6": ("0.21.0", "2.6.0"),
    "2.7": ("0.22.0", "2.7.0"),
    "2.8": ("0.23.0", "2.8.0"),
    "2.9": ("0.24.0", "2.9.0"),
    "2.10": ("0.25.0", "2.10.0"),
    "2.11": ("0.26.0", "2.11.0"),
}
# 驱动 CUDA 版本（从新到旧）→ 可用的 torch CUDA tag
CUDA_TAGS = [(13.0, "cu130"), (12.9, "cu129"), (12.8, "cu128"), (12.6, "cu126")]
# 上游 OmniVoice 的 pyproject 明确指向 cu128，属于被验证过的组合，能选就优先它
PREFERRED_TAG = "cu128"

WORKERS = 8
SEG = 32 * 1024 * 1024
RETRY = 10
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_lock = threading.Lock()
_done = 0
_total = 0
_t0 = 0.0


# ---------------------------------------------------------------------------
# 探测
# ---------------------------------------------------------------------------
def driver_cuda() -> str:
    """驱动支持的最高 CUDA 版本；没有 N 卡/N 驱动返回空串。

    这里刻意不 import app/hardware.py —— 那会 import torch，
    而本脚本正是在 torch 还没装好时用的。
    """
    try:
        kwargs = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} \
            if sys.platform == "win32" else {}
        r = subprocess.run(["nvidia-smi"], capture_output=True, text=True,
                           timeout=10, **kwargs)
        if r.returncode != 0:
            return ""
        m = re.search(r"CUDA Version:\s*([\d.]+)", r.stdout or "")
        return m.group(1) if m else ""
    except Exception:  # noqa: BLE001
        return ""


def supported_tags(ver: str) -> list[str]:
    """驱动 CUDA 版本 → **可用的 tag 候选（从新到旧）**。

    返回列表而不是单个值，是因为「驱动够新」不等于「这个 torch 版本有对应轮子」：
    例如驱动支持 CUDA 13.0，但 torch 2.8.0 只发布了 cu126/cu128/cu129，
    必须退到 cu129 去试，不能认死 cu130。
    """
    if not ver:
        return []
    try:
        v = float(ver)
    except ValueError:
        return []
    tags = [tag for need, tag in CUDA_TAGS if v >= need]
    if PREFERRED_TAG in tags:
        tags.remove(PREFERRED_TAG)
        tags.insert(0, PREFERRED_TAG)
    return tags


def py_tag() -> str:
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def platform_tags() -> list[str]:
    if sys.platform == "win32":
        return ["win_amd64"] if platform.machine().lower() in ("amd64", "x86_64") else ["win_arm64"]
    if sys.platform == "darwin":
        return ["macosx_11_0_arm64", "macosx_10_9_x86_64"]
    return ["manylinux_2_28_x86_64", "manylinux_2_17_x86_64", "linux_x86_64"]


def venv_python() -> Path:
    for rel in ("Scripts/python.exe", "bin/python", "Scripts/python"):
        p = ROOT / ".venv" / rel
        if p.exists():
            return p
    return ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _get(url: str, timeout: int = 60) -> bytes:
    import urllib.error

    last: Exception | None = None
    for i in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(min(2 ** i, 10))
    raise RuntimeError(f"访问失败 {url}: {last}")


_HREF_RE = re.compile(r'href="([^"#]+)(?:#sha256=([0-9a-f]{64}))?"')


_INDEX_CACHE: dict[tuple[str, str, str], dict[str, str]] = {}


def list_package(host: str, tag: str, pkg: str) -> dict[str, str]:
    """列出某包在某索引下的所有 wheel：{文件名: sha256}。

    注意要抓「包页面」（``{host}/{tag}/torch/``）；索引根页只列子目录名，没有轮子。
    """
    key = (host, tag, pkg)
    if key in _INDEX_CACHE:
        return _INDEX_CACHE[key]
    html = _get(f"{host}/{tag}/{pkg}/").decode("utf-8", "replace")
    out: dict[str, str] = {}
    for href, sha in _HREF_RE.findall(html):
        name = urllib.parse.unquote(href.rsplit("/", 1)[-1])
        if name.endswith(".whl"):
            out[name] = sha
    _INDEX_CACHE[key] = out
    return out


def resolve(host: str, tag: str, torch_ver: str) -> tuple[dict[str, str], dict[str, str]]:
    """解析出三个轮子的文件名与 sha256。返回 (files, urls)。"""
    minor = ".".join(torch_ver.split(".")[:2])
    if minor not in COMPANIONS:
        raise SystemExit(f"未收录 torch {torch_ver} 的配套版本，请用 --torch-version 指定已收录的："
                         f"{sorted(COMPANIONS)}")
    tv_ver, ta_ver = COMPANIONS[minor]
    specs = [("torch", torch_ver), ("torchaudio", ta_ver), ("torchvision", tv_ver)]
    cp = py_tag()
    plats = platform_tags()

    picked: dict[str, str] = {}
    urls: dict[str, str] = {}
    for pkg, ver in specs:
        files = list_package(host, tag, pkg)
        if not files:
            raise SystemExit(f"{host}/{tag}/{pkg}/ 列不出任何轮子（索引不可达或路径不对）")
        cands = [
            f for f in files
            if f.startswith(f"{pkg}-{ver}+{tag}-") and f"-{cp}-" in f
            and any(f.endswith(f"-{p}.whl") for p in plats)
        ]
        if not cands:
            raise SystemExit(
                f"{host}/{tag} 里没有 {pkg} {ver}+{tag}（{cp} / {plats}）的轮子；"
                "可能是该 CUDA tag 未覆盖此 Python 版本，可改用 --cpu 或别的 --cuda。")
        chosen = sorted(cands)[-1]
        picked[chosen] = files[chosen]
        urls[chosen] = f"{host}/{tag}/{urllib.parse.quote(chosen, safe='+')}"
    return picked, urls


# ---------------------------------------------------------------------------
# 并发下载
# ---------------------------------------------------------------------------
def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _remote_size(url: str) -> int:
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            return int(r.headers["Content-Length"])
    except Exception:  # noqa: BLE001
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return int(r.headers["Content-Range"].split("/")[-1])


def _fetch_range(url: str, start: int, end: int, out: Path) -> None:
    global _done
    pos, last = start, None
    for i in range(RETRY):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Range": f"bytes={pos}-{end}"})
            with urllib.request.urlopen(req, timeout=60) as r:
                with open(out, "r+b") as f:
                    f.seek(pos)
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        pos += len(chunk)
                        with _lock:
                            _done += len(chunk)
            if pos > end:
                return
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(min(2 ** i, 15))
    raise RuntimeError(f"range {start}-{end} 失败: {last}")


def download(name: str, url: str, sha256: str) -> Path:
    global _done, _total, _t0
    dst = WHEEL_DIR / name
    if dst.exists() and sha256 and _sha256(dst) == sha256:
        print(f"[SKIP] {name} 已存在且校验通过")
        return dst

    size = _remote_size(url)
    print(f"[GET ] {name}  {size / 1024**3:.2f} GiB  ({WORKERS} 线程)")
    with open(dst, "wb") as f:
        f.truncate(size)

    bounds = [(s, min(s + SEG - 1, size - 1)) for s in range(0, size, SEG)]
    idx = 0
    with _lock:
        _done, _total, _t0 = 0, size, time.time()

    def worker() -> None:
        nonlocal idx
        while True:
            with _lock:
                if idx >= len(bounds):
                    return
                s, e = bounds[idx]
                idx += 1
            _fetch_range(url, s, e, dst)
            with _lock:
                el = max(time.time() - _t0, 1e-6)
                spd = _done / el / 1024**2
                print(f"\r  {100 * _done / _total:5.1f}%  {_done / 1024**3:.2f}/{size / 1024**3:.2f} GiB "
                      f"{spd:4.1f} MB/s  ETA {(size - _done) / max(spd, 1e-6) / 1024:.0f}s",
                      end="", flush=True)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print()

    got = _sha256(dst)
    if sha256 and got != sha256:
        raise SystemExit(f"sha256 校验失败：{name}\n  expect {sha256}\n  got    {got}")
    print(f"[OK  ] {name} 校验通过")
    return dst


# ---------------------------------------------------------------------------
# 安装
# ---------------------------------------------------------------------------
def install(paths: list[Path], py: Path) -> int:
    wheels = [str(p) for p in paths]
    cmds = []
    if _has_uv():
        cmds.append(["uv", "pip", "install", "--python", str(py), "--reinstall",
                     "--no-deps", *wheels])
    cmds.append([str(py), "-m", "pip", "install", "--force-reinstall", "--no-deps", *wheels])
    return _run_first_ok(cmds)


def install_via_index(specs: list[str], index: str, py: Path, no_deps: bool = False) -> int:
    """从某个包索引直接装。CPU 版走这条路（需要一并解析纯 Python 依赖）。"""
    extra = ["--no-deps"] if no_deps else []
    cmds = []
    if _has_uv():
        cmds.append(["uv", "pip", "install", "--python", str(py), "--reinstall",
                     *extra, *specs, "--index-url", index])
    cmds.append([str(py), "-m", "pip", "install", "--force-reinstall",
                 *extra, *specs, "-i", index])
    return _run_first_ok(cmds)


def _has_uv() -> bool:
    try:
        subprocess.run(["uv", "--version"], capture_output=True, timeout=10)
        return True
    except Exception:  # noqa: BLE001
        return False


def _run_first_ok(cmds: list[list[str]]) -> int:
    for cmd in cmds:
        print("$", " ".join(cmd[:6]), "..." if len(cmd) > 6 else "")
        r = subprocess.run(cmd)
        if r.returncode == 0:
            return 0
        print(f"  ↑ 返回码 {r.returncode}，换下一种方式重试")
    return 1


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="自动挑选并安装匹配本机显卡的 torch")
    ap.add_argument("--cuda", help="强制 CUDA 版本，如 12.8 / 12.9（cu 号可省略）")
    ap.add_argument("--cpu", action="store_true", help="强制安装 CPU 版")
    ap.add_argument("--torch-version", default=DEFAULT_TORCH, help=f"torch 版本，默认 {DEFAULT_TORCH}")
    ap.add_argument("--download-only", action="store_true", help="只下载轮子，不安装")
    ap.add_argument("--list", action="store_true", help="只显示选型结果，不下载不安装")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass

    drv = driver_cuda()
    if args.cpu:
        tags: list[str] = []
    elif args.cuda:
        tags = [f"cu{args.cuda.replace('cu', '').replace('.', '')}"]
    else:
        tags = supported_tags(drv)

    print("=" * 64)
    print("torch 选型")
    print("=" * 64)
    print(f"平台        : {sys.platform} / {platform.machine()} / {py_tag()}")
    print(f"N 驱动 CUDA : {drv or '未检测到 NVIDIA 驱动'}")
    print(f"CUDA 候选   : {' → '.join(tags) if tags else '无（装 CPU 版）'}")
    print(f"torch 版本  : {args.torch_version}")
    if not tags and not args.cpu:
        if drv:
            print(f"  说明：驱动支持的 CUDA（{drv}）低于本脚本收录的最低要求"
                  f"（{CUDA_TAGS[-1][0]}），只能装 CPU 版；升级驱动即可解锁 GPU。")
        else:
            print("  说明：没检测到 NVIDIA 显卡/驱动，装 CPU 版是正常的；"
                  "AMD/Intel 核显需另装对应后端，本脚本不管。")
    py = venv_python()
    print(f"目标 Python : {py}")
    print("=" * 64)

    parts_minor = ".".join(args.torch_version.split(".")[:2])
    tv_ver, ta_ver = COMPANIONS.get(parts_minor, ("?", "?"))

    last_err: Exception | None = None
    for tag in tags:
        for host in CUDA_HOSTS:
            try:
                files, urls = resolve(host, tag, args.torch_version)
            except SystemExit as e:
                last_err = e
                print(f"[跳过] {host}/{tag}: {e}")
                continue
            except Exception as e:  # noqa: BLE001
                last_err = e
                print(f"[跳过] {host}/{tag} 不可用：{type(e).__name__}: {e}")
                continue

            print(f"索引        : {host}/{tag}")
            for name in files:
                print(f"  {name}  sha256={files[name][:12]}…")
            if args.list:
                return 0

            try:
                paths = [download(n, urls[n], files[n]) for n in files]
            except Exception as e:  # noqa: BLE001
                last_err = e
                print(f"[失败] {host}/{tag} 下载出错：{e}")
                continue

            if args.download_only:
                print("已下载到 wheels/（--download-only，不安装）")
                return 0
            if not py.exists():
                print(f"找不到虚拟环境 Python：{py}\n轮子已下好在 wheels/，请先创建 .venv 再重跑。")
                return 1
            return install(paths, py)

    if tags:
        print("=" * 64)
        print("CUDA 轮子都没拿到（索引不可达或该组合未发布），自动回退 CPU 版。")
        print(f"最后错误：{last_err}")

    specs = [f"torch=={args.torch_version}",
             f"torchaudio=={ta_ver}" if ta_ver != "?" else "torchaudio",
             f"torchvision=={tv_ver}" if tv_ver != "?" else "torchvision"]
    if args.list:
        print("将执行（CPU 版，走 PyPI 镜像）:", " ".join(specs))
        return 0
    if not py.exists():
        print(f"找不到虚拟环境 Python：{py}\n请先创建 .venv，或直接运行 setup.bat。")
        return 1
    return install_via_index(specs, PYPI_MIRROR, py)


if __name__ == "__main__":
    raise SystemExit(main())
