"""把评审截图压成可嵌入 HTML 的 data URI，并输出尺寸供标注定位。"""
import base64
import io
import json
from pathlib import Path

from PIL import Image

SRC = Path(r"C:\Game\omnivoice-studio\.workbuddy\tmp_ui_review")
NAMES = [
    "01-clone-light.png",
    "02-clone-viewport.png",
    "03-design-light.png",
    "04-library-light.png",
    "05-clone-settings-open.png",
    "06-clone-dark.png",
]

MAXW = 1500
out = {}
for n in NAMES:
    im = Image.open(SRC / n).convert("RGB")
    w, h = im.size
    if w > MAXW:
        im = im.resize((MAXW, round(h * MAXW / w)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="WEBP", quality=88, method=6)
    b = buf.getvalue()
    key = n.replace(".png", "")
    out[key] = {
        "uri": "data:image/webp;base64," + base64.b64encode(b).decode(),
        "w": im.size[0],
        "h": im.size[1],
        "bytes": len(b),
    }
    print(f"{key:28s} {w}x{h} -> {im.size[0]}x{im.size[1]}  {len(b)/1024:.0f} KB")

(SRC / "imgs.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
print("total base64 KB:", sum(v["bytes"] for v in out.values()) * 4 / 3 / 1024)
