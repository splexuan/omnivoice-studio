"""后端冒烟：音色名净化 + 「替换参考音频」接口（POST /api/voices/{name}/audio）。

界面上那条路（新建音色默认用文件名、音色库「重新上传音频」）由
`verify-voice-reupload.js` 用真实浏览器跑；这个脚本只管**接口本身**，
把浏览器不容易构造的几种情形补齐：

  · 名字里的非法字符被净化、空名字被兜底
  · 重名自动加序号，且 `name_note` 说得出原因
  · 替换音频：名字不变、时长变、旧音频文件被清掉、`.pt` 缓存被作废
  · 替换不存在的音色 → 404；名字里带路径分隔符 → 404（不落到 voices/ 之外）

只依赖标准库，跑法：
    .venv/Scripts/python.exe -s design-review/verify-web/verify-voice-audio-api.py [port]

自造的音色（名字统一以 `zz自测-` 开头）跑完即删，不会碰真实音色。
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

PORT = sys.argv[1] if len(sys.argv) > 1 else "7866"
BASE = f"http://127.0.0.1:{PORT}"
ROOT = Path(__file__).resolve().parents[2]
VOICES = ROOT / "voices"
UPLOADS = ROOT / "web" / "_uploads"
PREFIX = "zz自测-"

results = []


def check(name, ok, extra=""):
    results.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  → {extra}" if extra else ""))


def call(method, path, body=None, raw=None):
    """返回 (status, json_or_bytes)。不抛 HTTPError，让测试自己看状态码。"""
    url = BASE + path
    data, headers = None, {}
    if raw is not None:
        boundary = "----zz" + uuid.uuid4().hex
        name = raw.name
        payload = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + raw.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        data = payload
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            b = r.read()
            return r.status, (json.loads(b) if b[:1] in (b"{", b"[") else b)
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b)
        except Exception:                                   # noqa: BLE001
            return e.code, b


def upload(fixture: Path):
    """把本地文件塞进 uploads/，返回它落盘后的绝对路径（正是 _readable 认的那种）。"""
    st, r = call("POST", "/api/upload", raw=fixture)
    assert st == 200, (st, r)
    return r["path"]


def files_of(stem: str):
    """voices/ 里属于这个音色名的所有文件（音频 + json + pt）。"""
    return sorted(p.name for p in VOICES.iterdir() if p.is_file() and p.stem == stem)


def main():
    a = UPLOADS / "1dd85189a494.mp3"      # 16.87s
    b = UPLOADS / "94cc1775070a.mp3"      # 另一段，用于替换
    for f in (a, b):
        if not f.exists():
            print(f"缺少测试素材 {f}，跳过。")
            return 2

    path_a, path_b = upload(a), upload(b)
    created = []

    try:
        # ---------------------------------------------------------- 名字净化
        st, r = call("POST", "/api/voices",
                     {"name": f"{PREFIX}非法 a:b*c?d", "path": path_a, "ref_text": "自测"})
        check("非法字符被净化成下划线", st == 200 and r.get("name") == f"{PREFIX}非法 a_b_c_d",
              f"{st} {r.get('name')}")
        check("name_note 说明了净化原因", "特殊字符" in (r.get("name_note") or ""),
              repr(r.get("name_note")))
        check("renamed 为 true（名字确实变了）", r.get("renamed") is True)
        created.append(r["name"])

        st, r = call("POST", "/api/voices",
                     {"name": f"{PREFIX}非法 a:b*c?d", "path": path_a, "ref_text": "自测"})
        check("重名自动加序号", st == 200 and r.get("name") == f"{PREFIX}非法 a_b_c_d-2",
              f"{st} {r.get('name')}")
        check("name_note 同时说明了去重", "序号" in (r.get("name_note") or ""),
              repr(r.get("name_note")))
        created.append(r["name"])

        # 正常名字不该被改，note 应为空串
        st, r = call("POST", "/api/voices",
                     {"name": f"{PREFIX}原名", "path": path_a, "ref_text": "自测"})
        check("合法名字原样保留", r.get("name") == f"{PREFIX}原名", r.get("name"))
        check("没改动时 name_note 是空串", r.get("name_note") == "", repr(r.get("name_note")))
        created.append(r["name"])

        # ------------------------------------------------------ 替换参考音频
        stem = f"{PREFIX}替换"
        st, r = call("POST", "/api/voices", {"name": stem, "path": path_a, "ref_text": ""})
        assert st == 200, (st, r)
        created.append(stem)
        before = round(r["duration"], 2)
        before_files = files_of(stem)
        check("新建后落盘 mp3（未裁剪则原样搬，不重编码）",
              f"{stem}.mp3" in before_files, ",".join(before_files))

        # .pt 只在**模型已加载**时才会顺手建好，服务刚起来时通常是空的 ——
        # 所以不假设它存在，而是自己塞一个假的进去：替换之后它必须不见。
        # 这样测的正是 `_place_reference` 里那句 unlink，与模型加没加载无关。
        (VOICES / f"{stem}.pt").write_bytes(b"BOGUS-OLD-PROMPT")

        time.sleep(1.1)   # 让文件系统的 mtime 有足够分辨率区分新旧

        st, r = call("POST", f"/api/voices/{urllib.parse.quote(stem)}/audio",
                     {"path": path_b, "ref_text": "", "trim": {"start": 0.0, "end": 4.0}})
        check("替换接口返回 200", st == 200, f"{st} {r if st != 200 else ''}")
        check("替换后名字不变", r.get("name") == stem, r.get("name"))
        check("时长确实变了（16.87s → 约 4s）",
              abs(r.get("duration", 0) - 4.0) < 0.5 and abs(before - r.get("duration", 0)) > 5,
              f"{before} → {r.get('duration')}")
        check("裁过就落成 wav", (VOICES / f"{stem}.wav").exists())
        check("旧的 mp3 被清掉（不留孤儿音频）", not (VOICES / f"{stem}.mp3").exists(),
              ",".join(files_of(stem)))
        pt = VOICES / f"{stem}.pt"
        check("旧 prompt 缓存 .pt 被作废（不残留旧音频编出的 token）",
              not pt.exists() or pt.read_bytes() != b"BOGUS-OLD-PROMPT",
              "仍是我们塞进去的那个假缓存" if pt.exists() else "已删除（模型未加载，不会自动重建）")
        check("参考文本已按新音频重转（非空）", bool((r.get("ref_text") or "").strip()),
              (r.get("ref_text") or "")[:30])
        check("message 明确说了原音频作废", "作废" in (r.get("message") or ""))
        check("返回的 voices 是最新全量列表",
              isinstance(r.get("voices"), list) and any(v["name"] == stem for v in r["voices"]))

        # 手工填文本时不该被转写覆盖
        st, r = call("POST", f"/api/voices/{urllib.parse.quote(stem)}/audio",
                     {"path": path_b, "ref_text": "这是我手填的参考文本。"})
        check("ref_text 填了就不转写（原样落盘）",
              r.get("ref_text") == "这是我手填的参考文本。" and r.get("text_source") == "手填",
              f"{r.get('ref_text')!r} / {r.get('text_source')!r}")
        meta = json.loads((VOICES / f"{stem}.json").read_text(encoding="utf-8"))
        check("json 里的 ref_text 也更新了（会参与生成，必须落盘）",
              meta["ref_text"] == "这是我手填的参考文本。", meta["ref_text"])
        check("json 里没有残留的 prompt_cache 指纹", "prompt_cache" not in meta)

        # 不裁剪应该走「原样搬文件」，保留 mp3 后缀
        st, r = call("POST", f"/api/voices/{urllib.parse.quote(stem)}/audio", {"path": path_b})
        check("不裁剪则原样搬文件（后缀保持 mp3）", (VOICES / f"{stem}.mp3").exists()
              and not (VOICES / f"{stem}.wav").exists(), ",".join(files_of(stem)))

        # ------------------------------------------------------------ 404 与穿越
        # 路径必须自己 quote：urllib 不会替非 ASCII 编码，直接写中文会抛 UnicodeEncodeError
        miss = urllib.parse.quote("没这个音色")
        st, r = call("POST", f"/api/voices/{miss}/audio", {"path": path_b})
        check("替换不存在的音色 → 404", st == 404, f"{st} {r}")

        # %2f 解码后就是真的 `/`，于是路径多出两段、`{name}` 根本匹配不上路由，
        # 请求会落到静态挂载上（它对非 GET 一律 405）。安全，但不是 404 ——
        # 所以这里只钉住「不是 2xx」，那才意味着它真去动文件了。
        st, r = call("POST", "/api/voices/%2e%2e%2f%2e%2e%2fwindows/audio", {"path": path_b})
        check("名字里带 ../ 的进不来（4xx，绝不会是 200）", 400 <= st < 500, f"{st} {r}")

        # 反斜杠在 URL 里**不是**分隔符，会原样落进 {name} —— 这一条才真走到
        # `_voice_or_404` 的那道闸，正好验它确实拦得住。
        st, r = call("POST", "/api/voices/%5C..%5C..%5CWindows/audio", {"path": path_b})
        check("名字里带 \\ 的 → 404（_voice_or_404 挡住）", st == 404, f"{st} {r}")

        # 缺 path 字段
        st, r = call("POST", f"/api/voices/{urllib.parse.quote(stem)}/audio", {})
        check("缺 path → 400", st == 400, f"{st} {r}")

        # 白名单之外的路径
        st, r = call("POST", f"/api/voices/{urllib.parse.quote(stem)}/audio",
                     {"path": str(Path("C:/Windows/win.ini"))})
        check("白名单之外的路径 → 400", st == 400, f"{st} {r}")

        # 替换之后再合成一次，确认新音频真的能用（缓存已重建）
        st, r = call("GET", "/api/voices")
        me = next((v for v in r if v["name"] == stem), None) if isinstance(r, list) else None
        check("音色列表里该音色仍在且有 url", bool(me and me.get("url")), json.dumps(me, ensure_ascii=False))

    finally:
        st, r = call("POST", "/api/voices/delete", {"names": created})
        left = [n for n in created if (VOICES / f"{n}.json").exists()]
        check("自造音色已清理干净", not left, ",".join(left))
        for p in VOICES.iterdir():
            if p.is_file() and p.stem.startswith(PREFIX):
                print(f"  残留：{p.name}")

    bad = [n for n, ok in results if not ok]
    print(f"\n合计 {len(results)} 项，通过 {len(results) - len(bad)}，失败 {len(bad)}")
    if bad:
        print("失败项：\n" + "\n".join("  · " + n for n in bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
