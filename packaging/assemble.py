# -*- coding: utf-8 -*-
"""组装整合包：源码 + 工具代码 + 权重 + 可重定位导入 + 启动器。

路径默认从本脚本位置推导，换机一般无需改；也可用环境变量覆盖：

===============  ==========================================================
``OV_STUDIO``    开发态仓库根目录（默认 = 本脚本上一级）
``OV_DEST``      整合包输出目录（默认 = 仓库同级的 ``dist/OmniVoice-Studio``）
``OV_WEIGHTS``   权重来源目录，需含 ``OmniVoice/`` 与 ``sensevoice-small/``
                 （默认 = 仓库内 ``checkpoints/``）
``OV_STAGE``     启动器与说明的来源目录（默认 = 本目录 ``packaging/``）
===============  ==========================================================
"""
import os
import shutil
import subprocess
from pathlib import Path

_here = Path(__file__).resolve().parent          # packaging/
STUDIO = Path(os.environ.get("OV_STUDIO", _here.parent))
BUNDLE = Path(os.environ.get("OV_DEST", STUDIO.parent / "dist" / "OmniVoice-Studio"))
WEIGHTS = Path(os.environ.get("OV_WEIGHTS", STUDIO / "checkpoints"))
STAGE  = Path(os.environ.get("OV_STAGE", _here))
SP     = BUNDLE / "runtime" / "Lib" / "site-packages"

# 预检：缺什么就说清缺什么，别让 robocopy 静默失败
_missing = [("源码 OmniVoice/", STUDIO / "OmniVoice"),
            ("权重目录", WEIGHTS),
            ("启动器/说明", STAGE / "启动程序.bat"),
            ("运行时 runtime/", BUNDLE / "runtime")]
_bad = [(k, p) for k, p in _missing if not p.exists()]
if _bad:
    print("以下输入缺失，无法组装：")
    for k, p in _bad:
        print(f"  {k}: {p}")
    print("\n提示：runtime/ 需要先跑 packaging/copy_runtime.py；"
          "权重下载见 app/config.py 说明或 setup.bat。")
    raise SystemExit(1)

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

# 6) 权重：主模型 + 转写模型。转写只有 SenseVoice 一条路径，
#    不再有 whisper-large-v3-turbo（省 1.6 GB）。
run(robocopy(WEIGHTS/"OmniVoice",
             BUNDLE/"checkpoints"/"OmniVoice"), "权重 OmniVoice/")
run(robocopy(WEIGHTS/"sensevoice-small",
             BUNDLE/"checkpoints"/"sensevoice-small"), "权重 sensevoice/")

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
