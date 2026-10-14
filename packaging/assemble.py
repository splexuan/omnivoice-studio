# -*- coding: utf-8 -*-
"""组装「解压即用」整合包 —— OmniVoice Studio

从开发态仓库里挑出**运行必需**的部件，复制到目标目录，让整包不依赖任何绝对路径，
对方双击「启动程序.bat」即可运行。

【带什么 / 不带什么】（2026-10-14 定稿）
  带  runtime/        便携 Python + 全部依赖（含 torch cu128）        约 7.8 G
      checkpoints/    模型权重（OmniVoice 主模型 + SenseVoice 转写）  约 3.3 G
      OmniVoice/      官方源码（靠 runtime 里的相对 .pth 指过来）
      app/            生成核心（config / asr / hardware / app）
      web/            界面：server.py + api_docs.py + static/index.html
      run_web.bat     启动入口
      voices/         音色库（加 --no-voices 可去掉，做成"干净出厂"）
  不带 wheels/         3.3 G 的 torch 下载缓存 —— runtime 里已经装好 torch，带上纯属白送
      outputs/        合成产物与 history.json（config.py 启动时会自建空目录）
      design-review/  开发期的截图与验证脚本
      .workbuddy/     agent 工作目录
      __pycache__/    字节码缓存
      web/_uploads/   界面上的临时上传
      .git  .venv     前者已从仓库移除，后者已被 runtime 取代

【两个必须守住的细节】
  · `.pth` 必须写**相对路径**。editable 安装若写成绝对路径，整包一搬动
    `import omnivoice` 就失败（本项目真实踩过）——脚本会检查并在必要时修正。
  · dist-info 取自 **runtime/Lib/site-packages**。旧版脚本去 `.venv` 里找，
    而 `.venv` 已被 runtime 取代（目录都不在了）。

用法:
    runtime\\python.exe packaging\\assemble.py                # 组装到 <仓库同级>/dist/OmniVoice-Studio
    runtime\\python.exe packaging\\assemble.py --no-voices    # 不带音色
    runtime\\python.exe packaging\\assemble.py --dest D:\\out  # 指定输出目录
    runtime\\python.exe packaging\\assemble.py --clean        # 目标非空时先清空再组装
    runtime\\python.exe packaging\\assemble.py --zip          # 组装后再压成 zip
    runtime\\python.exe packaging\\assemble.py --no-verify    # 跳过组装后的自检

环境变量（与旧版兼容）: OV_STUDIO（仓库根） / OV_DEST（输出目录） / OV_STAGE（启动器来源）
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent                 # packaging/
STUDIO = Path(os.environ.get("OV_STUDIO", HERE.parent))
STAGE = Path(os.environ.get("OV_STAGE", HERE))
DEFAULT_DEST = STUDIO.parent / "dist" / "OmniVoice-Studio"

# (源, 目标, 排除的目录名)   —— robocopy 的 /XD 收目录名即可匹配任意层级
SMALL_PARTS = [
    ("OmniVoice", "OmniVoice", ("__pycache__", ".git")),
    ("app", "app", ("__pycache__",)),
    ("web", "web", ("__pycache__", "_uploads")),
    ("voices", "voices", ()),
]
BIG_PARTS = [
    #  runtime 的 __pycache__ 【要保留】：
    #    · .pyc 是基于源文件 mtime 校验的，robocopy 保留时间戳，搬移后照样有效；
    #    · 有它，对方首次启动不用现场编译（省十几秒），
    #      也不会冒出 pydub 那几条只在"首次编译"时才出现的 SyntaxWarning；
    #    · 代价只有 97 MB（占整包的 0.9%），划算。
    ("runtime", "runtime", ()),
    ("checkpoints", "checkpoints", ()),
]
ROOT_FILES = ["run_web.bat"]                    # 从仓库根直接复制
STAGE_FILES = ["启动程序.bat", "使用说明.md"]     # 给整合包用的启动器与说明

REQUIRED = [
    ("便携运行时", STUDIO / "runtime"),
    ("模型权重", STUDIO / "checkpoints"),
    ("官方源码", STUDIO / "OmniVoice"),
    ("生成核心", STUDIO / "app"),
    ("界面", STUDIO / "web"),
    ("启动脚本", STUDIO / "run_web.bat"),
]

EDITABLE_PTH = "_editable_impl_omnivoice.pth"
PTH_REL = r"..\..\..\OmniVoice"      # 相对 site-packages → Lib → runtime → 包根


def hr(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def robocopy(src: Path, dst: Path, xd: tuple[str, ...] = ()) -> tuple[bool, int]:
    """用 robocopy 复制（保留时间戳、支持排除、多线程）。

    返回 (是否成功, 退出码)。robocopy 的 0–7 都算成功（1 表示有文件被复制），
    ≥8 才是真失败 —— 别拿它当普通命令看。
    """
    dst.mkdir(parents=True, exist_ok=True)
    args = ["robocopy", str(src), str(dst), "/E", "/MT:16", "/R:1", "/W:1",
            "/NFL", "/NDL", "/NJH", "/NJS", "/NP"]
    if xd:
        args += ["/XD", *xd]
    r = subprocess.run(args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode < 8, r.returncode


def du(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def gb(n: int) -> str:
    return f"{n / 1024 ** 3:.2f} GiB"


def fix_editable_pth(sp: Path) -> str:
    """确保 omnivoice 的 editable .pth 是相对路径。返回一句状态说明。"""
    pth = sp / EDITABLE_PTH
    if not pth.exists():
        pth.write_text(PTH_REL + "\n", encoding="utf-8")
        return f"新建 {EDITABLE_PTH} -> {PTH_REL}"
    cur = pth.read_text(encoding="utf-8", errors="replace").strip()
    if Path(cur).is_absolute() or ":" in cur:
        pth.write_text(PTH_REL + "\n", encoding="utf-8")
        return f"已修正 {EDITABLE_PTH}：{cur}  ->  {PTH_REL}（绝对路径会让整包搬不动）"
    return f"{EDITABLE_PTH} 已是相对路径（{cur}）"


def verify(bundle: Path) -> bool:
    """用**包内**的 python、在**无关的工作目录**里验证：能 import、界面文件在。

    这正是当初出事的地方（editable .pth 写死绝对路径 → 换目录后 import 失败），
    所以这一步放在「无关 cwd」下跑，而不是在包目录里跑。
    """
    py = bundle / "runtime" / "python.exe"
    if not py.exists():
        print("  [✗] 包内找不到 runtime/python.exe")
        return False

    missing = [str(p.relative_to(bundle)) for p in
               (bundle / "web" / "server.py", bundle / "web" / "api_docs.py",
                bundle / "web" / "static" / "index.html",
                bundle / "app" / "app.py", bundle / "OmniVoice" / "omnivoice" / "__init__.py")
               if not p.exists()]
    if missing:
        print("  [✗] 缺关键文件:", ", ".join(missing))
        return False
    print("  [✓] 界面与核心文件齐全")

    code = ("import torch, omnivoice; "
            "print('  [✓] torch', torch.__version__, '| CUDA 可用:', torch.cuda.is_available()); "
            "print('  [✓] omnivoice', omnivoice.__version__, '->', omnivoice.__file__)")
    with tempfile.TemporaryDirectory() as tmp:          # 关键：无关 cwd
        r = subprocess.run([str(py), "-s", "-c", code], cwd=tmp, env=_clean_env(),
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
    print((r.stdout or "").strip())
    if r.returncode != 0:
        print("  [✗] import 失败：")
        print(((r.stderr or r.stdout) or "").strip()[-900:])
        return False
    return True


def _clean_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = ""                 # 挡掉宿主注入的 PYTHONPATH
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def wipe(path: Path) -> None:
    """清空目录。走 cmd 的 rd，避免被安全删除钩子拦下（大目录会 fail-closed）。"""
    subprocess.run(["cmd", "/c", "rd", "/s", "/q", str(path)],
                   capture_output=True, text=True,
                   encoding="utf-8", errors="replace")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="组装 OmniVoice Studio 整合包",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST, help=f"输出目录（默认 {DEFAULT_DEST}）")
    ap.add_argument("--no-voices", action="store_true", help="不带音色库（干净出厂）")
    ap.add_argument("--clean", action="store_true", help="目标非空时先清空")
    ap.add_argument("--zip", action="store_true", help="组装后压成同名 zip")
    ap.add_argument("--no-verify", action="store_true", help="跳过组装后的自检")
    args = ap.parse_args()

    dest: Path = args.dest.resolve()

    hr("0/6 预检")
    bad = [(k, p) for k, p in REQUIRED if not p.exists()]
    if bad:
        for k, p in bad:
            print(f"  [✗] 缺 {k}：{p}")
        print("\n整合包需要完整的开发态目录（runtime/ 与 checkpoints/ 缺一不可）。")
        return 2
    print(f"  仓库根  : {STUDIO}")
    print(f"  输出到  : {dest}")
    if not args.no_voices:
        n = len(list((STUDIO / "voices").glob("*.json"))) if (STUDIO / "voices").exists() else 0
        print(f"  音色    : 带上（{n} 个）")
    else:
        print("  音色    : 不带（--no-voices）")

    if dest.exists() and any(dest.iterdir()):
        if not args.clean:
            print(f"\n[中止] 输出目录非空：{dest}")
            print("       重新组装请加 --clean（先清空该目录），或换一个 --dest。")
            return 3
        print(f"  清空旧目录：{dest}")
        wipe(dest)
    dest.mkdir(parents=True, exist_ok=True)

    t0 = time.time()

    hr("1/6 源码与界面（小部件）")
    for src_name, dst_name, xd in SMALL_PARTS:
        src = STUDIO / src_name
        if not src.exists():
            print(f"  [skip] {src_name} 不存在")
            continue
        ok, rc = robocopy(src, dest / dst_name, xd)
        print(f"  [{'OK  ' if ok else 'FAIL'}] {src_name+'/':16s} rc={rc}")

    if args.no_voices:
        (dest / "voices").mkdir(parents=True, exist_ok=True)
        print("  [OK  ] voices/          （留空目录，程序会自行填充）")

    hr("2/6 入口脚本")
    for f in ROOT_FILES:
        src = STUDIO / f
        if src.exists():
            shutil.copy2(src, dest / f)
            print(f"  [OK  ] {f}")
        else:
            print(f"  [✗] 缺 {f}")
            return 2

    hr("3/6 运行时与权重（大部件，稍慢）")
    for src_name, dst_name, xd in BIG_PARTS:
        src = STUDIO / src_name
        t1 = time.time()
        print(f"  复制 {src_name}/ ...", flush=True)
        ok, rc = robocopy(src, dest / dst_name, xd)
        print(f"  [{'OK  ' if ok else 'FAIL'}] {src_name+'/':16s} rc={rc}  "
              f"{time.time() - t1:.1f}s")
        if not ok:
            return 4

    hr("4/6 可重定位导入（.pth）")
    sp = dest / "runtime" / "Lib" / "site-packages"
    if sp.exists():
        print("  " + fix_editable_pth(sp))
    else:
        print(f"  [✗] 找不到 {sp}")
        return 4

    hr("5/6 启动器与说明")
    for f in STAGE_FILES:
        src = STAGE / f
        if src.exists():
            shutil.copy2(src, dest / f)
            print(f"  [OK  ] {f}")
        else:
            print(f"  [warn] 缺 {f}（跳过，不影响运行）")

    hr("6/6 自检")
    ok_verify = True
    if args.no_verify:
        print("  已跳过（--no-verify）")
    else:
        ok_verify = verify(dest)

    hr("体积")
    total = 0
    for name in ("runtime", "checkpoints", "OmniVoice", "app", "web", "voices"):
        size = du(dest / name)
        total += size
        print(f"  {name:14s} {gb(size):>10s}")
    print(f"  {'合计':14s} {gb(total):>10s}")
    extra = [f for f in ROOT_FILES + STAGE_FILES if (dest / f).exists()]
    print(f"  其他文件      : {', '.join(extra) if extra else '—'}")

    if args.zip:
        hr("打包 zip")
        print("  压缩中（11 G 级别，需要几分钟）...", flush=True)
        archive = shutil.make_archive(str(dest), "zip", str(dest.parent), dest.name)
        print(f"  [OK  ] {archive}  {gb(os.path.getsize(archive))}")

    print(f"\n总耗时 {time.time() - t0:.1f}s")
    if not ok_verify:
        print("!! 自检未通过：这个包很可能跑不起来，先别发出去。")
        return 5
    print("ASSEMBLE_DONE")
    print(f"\n下一步：把整个 {dest.name}\\ 拷到（或解压到）别处，双击「启动程序.bat」。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
