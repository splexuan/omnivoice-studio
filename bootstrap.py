"""一键把这套工具在**新机器**上搭起来（自适应各种显卡/配置）。

它按顺序做六件事，每一步都可单独跳过：

1. 找一个 Python 3.12（uv 管理的 / py launcher / PATH 里的）
2. 建 ``.venv``
3. 装 torch 家族 —— 交给 ``wheels/_fetch_torch.py`` 按驱动自动选 CUDA 版本，
   选不出就装 CPU 版（没有 N 卡、驱动太老、或想纯 CPU 跑都能过）
4. 装 omnivoice 及运行依赖（默认走国内镜像）
5. 下模型权重到 ``checkpoints/``（默认走 hf-mirror）
6. 跑 ``app/verify_env.py`` 自检，并打印本机适配档位

用法
----
    python bootstrap.py                # 全流程
    python bootstrap.py --cpu          # 明确装 CPU 版 torch
    python bootstrap.py --cuda 12.6    # 强制某个 CUDA 版本
    python bootstrap.py --no-asr       # 不下转写权重（省 0.24GB，参考文本改为手工填）
    python bootstrap.py --skip-weights # 只搭环境不下权重
    python bootstrap.py --mirror https://mirrors.aliyun.com/pypi/simple/

只依赖标准库，用任何 Python 3.8+ 都能跑起来。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEIGHTS = ROOT / "checkpoints"
REPOS = {
    "k2-fsa/OmniVoice": "OmniVoice",
}
# 参考音频转写：SenseVoice Small（sherpa-onnx，int8 约 0.24 GB，自带中文标点）。
# 只取这两个文件 —— 整个仓库里还有 fp32 权重与测试音频，多下 1 GB 没必要。
# 这里**不再**下载 Whisper：0.2.x 早期版本把它当兜底引擎，现已整体移除
# （省 1.6 GB 权重与同等常驻内存），转写只此一条路径，见 app/asr.py。
SENSEVOICE_REPO = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
SENSEVOICE_FILES = ("model.int8.onnx", "tokens.txt")
DEFAULT_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
DEFAULT_HF = "https://hf-mirror.com"
DEPS = ["transformers>=5.3.0", "accelerate", "pydub", "gradio", "tensorboardX",
        "webdataset", "soundfile", "librosa", "num2words",
        # 参考音频转写：SenseVoice Small（见 app/asr.py），项目唯一的转写引擎
        "sherpa-onnx"]


def log(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


def which(name: str) -> bool:
    return shutil.which(name) is not None


def run(cmd: list[str], env: dict | None = None) -> int:
    print("$", " ".join(cmd[:8]), "..." if len(cmd) > 8 else "", flush=True)
    return subprocess.run(cmd, env=env).returncode


def capture(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return (r.stdout or "").strip().splitlines()[-1].strip() if r.stdout.strip() else ""
    except Exception:  # noqa: BLE001
        return ""


def venv_python() -> Path:
    for rel in ("Scripts/python.exe", "bin/python", "Scripts/python"):
        p = ROOT / ".venv" / rel
        if p.exists():
            return p
    return ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def pip_cmd(vpy: Path) -> list[str]:
    if which("uv"):
        return ["uv", "pip", "install", "--python", str(vpy)]
    return [str(vpy), "-m", "pip", "install"]


# ---------------------------------------------------------------------------
def step_python() -> list[str]:
    log("1/6 查找 Python 3.12")
    if which("uv"):
        p = capture(["uv", "python", "find", "3.12"])
        if p:
            print("使用 uv 管理的 Python 3.12:", p)
            return [p]
    if sys.platform == "win32" and which("py"):
        p = capture(["py", "-3.12", "-c", "import sys;print(sys.executable)"])
        if p:
            print("使用 py launcher 的 Python 3.12:", p)
            return [p]
    for name in ("python3.12", "python"):
        if which(name):
            if capture([name, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"]) == "3.12":
                print("使用 PATH 中的:", name)
                return [name]
    if sys.version_info[:2] == (3, 12):
        print("使用当前解释器:", sys.executable)
        return [sys.executable]

    print("没找到 Python 3.12。OmniVoice 需要 3.10+，推荐 3.12。请任选一种方式：")
    print("  A) 装 uv（推荐，最省事）:  pip install uv   然后重跑本脚本")
    print("  B) 从 python.org 安装 Python 3.12 并勾选 Add to PATH")
    raise SystemExit(1)


def step_venv(py: list[str]) -> Path:
    log("2/6 准备虚拟环境 .venv")
    vpy = venv_python()
    if vpy.exists():
        ver = capture([str(vpy), "-c", "import sys;print('%d.%d' % sys.version_info[:2])"])
        if ver == "3.12":
            print("复用已有 .venv:", vpy)
            return vpy
        print(f"现有 .venv 是 Python {ver}，不是 3.12，将重建")
        shutil.rmtree(ROOT / ".venv", ignore_errors=True)

    if which("uv"):
        run(["uv", "venv", ".venv", "--python", "3.12"])
    else:
        run(py + ["-m", "venv", str(ROOT / ".venv")])
    if not venv_python().exists():
        raise SystemExit("虚拟环境创建失败")
    return venv_python()


def step_torch(vpy: Path, extra: list[str]) -> None:
    log("3/6 安装 torch（按本机显卡自动选 CUDA 版本）")
    rc = run([str(vpy), str(ROOT / "wheels" / "_fetch_torch.py"), *extra])
    if rc != 0:
        print("torch 安装未成功，后续步骤大概率也会失败；可手工重试：")
        print(f"  {vpy} wheels\\_fetch_torch.py --cpu")


def step_deps(vpy: Path, mirror: str) -> None:
    log("4/6 安装 omnivoice 及其依赖")
    if not (ROOT / "OmniVoice" / "pyproject.toml").exists():
        if which("git"):
            print("未找到上游源码，正在 clone k2-fsa/OmniVoice ...")
            run(["git", "clone", "--depth", "1",
                 "https://github.com/k2-fsa/OmniVoice.git", str(ROOT / "OmniVoice")])
        else:
            raise SystemExit("缺少 OmniVoice/ 源码目录，且没有 git 可用。请手动 clone 到 OmniVoice/")
    run(pip_cmd(vpy) + ["--index-url", mirror, *DEPS])
    run(pip_cmd(vpy) + ["--no-deps", "-e", str(ROOT / "OmniVoice")])


def _download_sensevoice(vpy: Path, hf: Path, env: dict) -> None:
    """下载 SenseVoice Small 的两个文件到 checkpoints/sensevoice-small/。

    跳过时返回 False —— 转写是参考文本的唯一自动来源，缺了它必须让使用者知道。
    """
    dst = WEIGHTS / "sensevoice-small"
    if (dst / "tokens.txt").exists() and (dst / "model.int8.onnx").exists():
        print(f"已存在，跳过：{dst}")
        return True
    print(f"下载 {SENSEVOICE_REPO} -> {dst}")
    if hf.exists():
        rc = run([str(hf), "download", SENSEVOICE_REPO, *SENSEVOICE_FILES,
                  "--local-dir", str(dst)], env=env)
    else:
        rc = run([str(vpy), "-c",
                  "import sys;from huggingface_hub import hf_hub_download as d;"
                  "[d(sys.argv[1], f, local_dir=sys.argv[2]) for f in sys.argv[3:]]",
                  SENSEVOICE_REPO, str(dst), *SENSEVOICE_FILES], env=env)
    if rc != 0:
        print(f"下载失败：{SENSEVOICE_REPO}（可稍后重跑本脚本；"
              "缺它时参考音频无法自动转写，需要手工填写参考文本）")
        return False
    return True


def step_weights(vpy: Path, hf_mirror: str, with_asr: bool) -> None:
    log("5/6 下载模型权重到 checkpoints/")
    env = {**os.environ, "HF_ENDPOINT": hf_mirror, "HF_HUB_DISABLE_TELEMETRY": "1"}
    hf = venv_python().parent / ("hf.exe" if sys.platform == "win32" else "hf")

    if with_asr:
        _download_sensevoice(vpy, hf, env)
    else:
        print("按 --no-asr 跳过 SenseVoice 转写权重：参考音频需手工填写文本")

    for repo, sub in REPOS.items():
        dst = WEIGHTS / sub
        if (dst / "config.json").exists():
            print(f"已存在，跳过：{dst}")
            continue
        print(f"下载 {repo} -> {dst}")
        if hf.exists():
            rc = run([str(hf), "download", repo, "--local-dir", str(dst), "--max-workers", "4"], env=env)
        else:
            rc = run([str(vpy), "-c",
                      "import sys;from huggingface_hub import snapshot_download as s;"
                      "s(sys.argv[1], local_dir=sys.argv[2])", repo, str(dst)], env=env)
        if rc != 0:
            print(f"下载失败：{repo}（可稍后重跑本脚本，已下好的文件会跳过）")
    print(f"权重目录：{WEIGHTS}")


def step_verify(vpy: Path) -> None:
    log("6/6 环境自检")
    run([str(vpy), str(ROOT / "app" / "verify_env.py")])
    print("\n下一步：")
    print(f"  启动界面  : {vpy} app\\app.py      （或双击 run.bat）")
    print(f"  冒烟自测  : {vpy} app\\smoke_test.py")
    print(f"  看档位    : {vpy} app\\hardware.py")


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(description="一键搭建 OmniVoice Studio 环境")
    ap.add_argument("--cpu", action="store_true", help="强制 CPU 版 torch")
    ap.add_argument("--cuda", help="强制 CUDA 版本，如 12.6 / 12.8")
    ap.add_argument("--no-asr", action="store_true",
                    help="不下 SenseVoice 转写权重（参考音频改为手工填文本）")
    ap.add_argument("--skip-deps", action="store_true", help="跳过依赖安装")
    ap.add_argument("--skip-weights", action="store_true", help="跳过权重下载")
    ap.add_argument("--mirror", default=os.environ.get("PIP_INDEX_URL", DEFAULT_MIRROR),
                    help="PyPI 镜像")
    ap.add_argument("--hf-mirror", default=DEFAULT_HF, help="HuggingFace 镜像")
    args = ap.parse_args()

    print("=" * 64)
    print("OmniVoice Studio — 环境引导")
    print(f"项目目录: {ROOT}")
    print("=" * 64)

    py = step_python()
    vpy = step_venv(py)

    torch_args: list[str] = []
    if args.cpu:
        torch_args.append("--cpu")
    if args.cuda:
        torch_args += ["--cuda", args.cuda]
    step_torch(vpy, torch_args)

    if not args.skip_deps:
        step_deps(vpy, args.mirror)
    if not args.skip_weights:
        step_weights(vpy, args.hf_mirror, with_asr=not args.no_asr)
    step_verify(vpy)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
