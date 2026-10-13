"""把 D 盘现成的 torch cu128 通过目录链接（junction）接入本项目 venv。

零拷贝、零下载。只链接 torch / torchaudio 及其元数据。
"""

import os
import subprocess

SP_E = r"E:\OmniVoice\omnivoice-studio\.venv\Lib\site-packages"
SP_D = r"D:\python_www\LuxTTS\python\Lib\site-packages"

NAMES = [
    "torch",
    "torch-2.8.0+cu128.dist-info",
    "torchaudio",
    "torchaudio-2.8.0+cu128.dist-info",
    "torchvision",
    "torchvision-0.23.0+cu128.dist-info",
    "torchgen",
]

for name in NAMES:
    link = os.path.join(SP_E, name)
    target = os.path.join(SP_D, name)

    if not os.path.exists(target):
        print(f"SKIP   {name}  (源不存在)")
        continue
    if os.path.lexists(link):
        print(f"EXISTS {name}  (已存在，跳过)")
        continue

    r = subprocess.run(
        ["cmd", "/c", "mklink", "/J", link, target],
        capture_output=True, text=True, encoding="utf-8", errors="ignore",
    )
    out = (r.stdout or "").strip() or (r.stderr or "").strip()
    print(f"{'OK  ' if r.returncode == 0 else 'FAIL'} {name}  rc={r.returncode}  {out}")
