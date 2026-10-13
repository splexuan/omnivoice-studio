# -*- coding: utf-8 -*-
"""复制可重定位运行时：便携 Python（排除旧 torch）+ cu128 torch 家族真实文件。"""
import subprocess, sys, shutil
from pathlib import Path

SRC   = Path(r"E:\OmniVoice\OmniVoice-v260717\walkingwithai")
DEST  = Path(r"E:\OmniVoice\dist\OmniVoice-Studio\runtime")
SP    = SRC / "Lib" / "site-packages"
DP    = Path(r"D:\python_www\LuxTTS\python\Lib\site-packages")
DSTSP = DEST / "Lib" / "site-packages"

EXCLUDE = [
    # 关键：排除原包自带的旧版 omnivoice（0.1.2 源码），否则会遮蔽我们装入的 0.2.1
    SP / "omnivoice", SP / "omnivoice-0.1.2.dist-info",
    SP / "torch", SP / "torchaudio", SP / "torchvision", SP / "torchgen",
    SP / "triton", SP / "functorch",
    SP / "torch-2.11.0+cu130.dist-info",
    SP / "torchaudio-2.11.0+cu130.dist-info",
    SP / "torchvision-0.26.0+cu130.dist-info",
    SP / "triton_windows-3.6.0.post26.dist-info",
]

# 需要从 D 盘灌入的 cu128 家族（目录名）
TORCH_ITEMS = ["torch", "torchaudio", "torchvision", "torchgen", "torio",
               "torch-2.8.0+cu128.dist-info",
               "torchaudio-2.8.0+cu128.dist-info",
               "torchvision-0.23.0+cu128.dist-info"]

def run(args, label):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    ok = r.returncode < 8
    print(f"[{'OK  ' if ok else 'FAIL'}] {label}  rc={r.returncode}", flush=True)
    if not ok:
        print(r.stdout[-1000:], flush=True)
        print(r.stderr[-1000:], flush=True)
    return ok

def robocopy(src, dst, extra=("/E", "/MT:16")):
    args = ["robocopy", str(src), str(dst)] + list(extra) + \
           ["/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/R:1", "/W:1"]
    return args

allok = True

print("=== [1/2] 复制便携 Python 运行时（排除旧 torch 家族）===", flush=True)
args = robocopy(SRC, DEST)
args += ["/XD"] + [str(d) for d in EXCLUDE]
allok &= run(args, "runtime(core)")

print("=== [2/2] 灌入 cu128 torch 家族 ===", flush=True)
for name in TORCH_ITEMS:
    s = DP / name
    if not s.exists():
        print(f"[SKIP] 源不存在 {s}", flush=True); continue
    allok &= run(robocopy(s, DSTSP / name), name)

# 校验
print("\n=== 校验结果 ===", flush=True)
for name in ["torch", "torchaudio", "torchvision", "torio"]:
    p = DSTSP / name
    n = sum(1 for _ in p.rglob("*")) if p.exists() else 0
    print(f"  {name:14s} exists={p.exists()}  files~{n}", flush=True)

def du(p):
    if not p.exists(): return 0
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
print(f"  runtime 体积: {du(DEST)/1024**3:.2f} GiB", flush=True)
print("ALL_DONE" if allok else "HAD_ERRORS", flush=True)
