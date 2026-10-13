# -*- coding: utf-8 -*-
"""组装整合包：源码 + 工具代码 + 权重 + 可重定位导入 + 启动器。"""
import subprocess, shutil
from pathlib import Path

BUNDLE = Path(r"E:\OmniVoice\dist\OmniVoice-Studio")
STUDIO = Path(r"E:\OmniVoice\omnivoice-studio")
SRCPKG = Path(r"E:\OmniVoice\OmniVoice-v260717")
STAGE  = Path(r"E:\OmniVoice\_pkg")
SP     = BUNDLE / "runtime" / "Lib" / "site-packages"

def run(args, label):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    ok = r.returncode < 8
    print(f"[{'OK  ' if ok else 'FAIL'}] {label} rc={r.returncode}", flush=True)
    if not ok:
        print(r.stdout[-800:]); print(r.stderr[-800:])
    return ok

def robocopy(src, dst, xd=()):
    a = ["robocopy", str(src), str(dst), "/E", "/MT:16",
         "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/R:1", "/W:1"]
    if xd:
        a += ["/XD"] + [str(x) for x in xd]
    return a

# 1) 目录骨架
for d in [BUNDLE/"OmniVoice", BUNDLE/"app", BUNDLE/"checkpoints",
          BUNDLE/"voices", BUNDLE/"outputs"]:
    d.mkdir(parents=True, exist_ok=True)

# 2) 官方源码（排除 .git / __pycache__）
run(robocopy(STUDIO/"OmniVoice", BUNDLE/"OmniVoice",
             xd=[STUDIO/"OmniVoice"/".git", STUDIO/"OmniVoice"/"__pycache__"]),
    "源码 OmniVoice/")

# 3) 工具代码 app/
run(robocopy(STUDIO/"app", BUNDLE/"app"), "工具 app/")

# 4) omnivoice 0.2.1 元数据（可选，便于 pip show）
di_src = STUDIO/".venv"/"Lib"/"site-packages"/"omnivoice-0.2.1.dist-info"
if di_src.exists():
    dst = SP/"omnivoice-0.2.1.dist-info"
    if dst.exists(): shutil.rmtree(dst)
    shutil.copytree(di_src, dst)
    print("[OK  ] dist-info omnivoice-0.2.1")

# 5) 可重定位导入：相对 .pth 指向包根下的 OmniVoice/
pth = SP/"_omnivoice_bundle.pth"
pth.write_text("../../../OmniVoice\n", encoding="utf-8")
print(f"[OK  ] 写入 {pth.name} -> ../../../OmniVoice")

# 6) 权重
run(robocopy(SRCPKG/"checkpoints"/"OmniVoice",
             BUNDLE/"checkpoints"/"OmniVoice"), "权重 OmniVoice/")
run(robocopy(SRCPKG/"checkpoints"/"whisper-large-v3-turbo",
             BUNDLE/"checkpoints"/"whisper-large-v3-turbo"), "权重 whisper/")

# 7) 启动器与说明
for f in ["启动程序.bat", "使用说明.md"]:
    shutil.copy(STAGE/f, BUNDLE/f)
    print(f"[OK  ] 复制 {f}")

# 8) 体积报告
def du(p):
    return sum(x.stat().st_size for x in Path(p).rglob("*") if x.is_file())
print("\n=== 体积 ===")
for name in ["runtime", "OmniVoice", "app", "checkpoints"]:
    print(f"  {name:12s} {du(BUNDLE/name)/1024**3:6.2f} GiB")
print(f"  {'合计':12s} {du(BUNDLE)/1024**3:6.2f} GiB")
print("ASSEMBLE_DONE")
