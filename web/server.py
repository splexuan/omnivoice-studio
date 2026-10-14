"""OmniVoice Studio — WebUI（FastAPI + 自研前端）。

本文件是**唯一的界面实现**。早先用 Gradio 写过一版（带 build_ui()，放在 app/app.py 里），
已于 2026-10-14 连同 run.bat 一起移除 —— 换掉它的原因只有一个：
**把界面完全握在自己手里**。Gradio 会在组件标签、主题变量、DOM 结构上
反复和你抢控制权（哈希类名随版本变、注入 CSS 的选择器被重写），
想让每个细节都到位，成本比自研更高。

分工：
  · 模型调用与音色库读写 —— 直接复用 app/app.py 的**模块级函数**，不重复实现
    （load_model / synthesize / list_voices / load_voice_meta / _to_instruct …）
  · 界面与交互 —— 全部由 web/static/index.html 自持（正式版；旧版设计存档在
    web/static/index-v2.html，不再演进）
  · 接口文档 —— 内容在 web/api_docs.py，本文件只把它挂成 GET /api/docs
本文件不修改 app/app.py（它现在只剩生成核心，不含任何界面代码）。

接口：
    GET  /api/bootstrap                    运行环境 + 语种/属性 + 音色列表 + 裁剪建议
    POST /api/upload                       上传参考音频到临时目录
    POST /api/transcribe                   试转写（可带裁剪），不落盘
    GET  /api/voices                       音色列表（含时长与缓存状态）
    POST /api/voices                       保存音色（可带裁剪；保存时补上参考文本）
    POST /api/voices/{name}/trim           就地裁剪已有音色的参考音频
    POST /api/voices/{name}/rename         重命名
    POST /api/voices/{name}/retranscribe   重新转写参考文本
    POST /api/voices/delete                批量删除
    POST /api/jobs  GET /api/jobs/{id}     提交合成任务 / 轮询进度
    GET  /api/history                      合成记录（倒序，含文件是否还在）
    POST /api/history/delete               删除指定记录（不动 outputs/ 里的 wav）
    POST /api/history/clear                清空全部记录
    GET  /media/{out|upload|voice}/{name}  音频回放
    GET  /api/docs                         接口清单（与真实路由对账后的结果）
    GET  /openapi.json                     FastAPI 自动生成的机器可读规格

启动：
    runtime\\python.exe web\\server.py
    或  run_web.bat
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import api_docs as api_docs_mod  # noqa: E402  —— 接口文档的唯一来源（纯数据，无副作用）
import config  # noqa: E402
import app as core  # noqa: E402  —— 复用现有生成逻辑（import 不会加载模型）

import soundfile as sf  # noqa: E402
from fastapi import FastAPI, File, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"
UPLOAD_DIR = Path(__file__).resolve().parent / "_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 允许被当作「参考音频来源」读取的目录白名单（防止把任意本地文件复制进音色库）
_READABLE_DIRS = (UPLOAD_DIR.resolve(), config.VOICES_DIR.resolve())

app = FastAPI(title="OmniVoice Studio", docs_url=None, redoc_url=None)

AUDIO_MIME = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac",
              ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".aac": "audio/aac"}


# ---------------------------------------------------------------------------
# 合成记录（历史）
# ---------------------------------------------------------------------------
# 存在 outputs/ 下而不是单独目录：记录里引用的就是这些 wav，放一起才不会出现
# 「记录还在、文件已被人手工删掉」的错位。只记参数、不存音频副本。
HISTORY_MAX = 300
HISTORY_FILE = config.OUTPUT_DIR / "history.json"
_HIST_LOCK = threading.Lock()


def _history_read() -> list[dict]:
    try:
        raw = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []                                  # 首次运行没有这个文件，属正常
    return [x for x in raw if isinstance(x, dict)] if isinstance(raw, list) else []


def _history_write(items: list[dict]) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = HISTORY_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(HISTORY_FILE)                      # 原子替换，别让中断留下半截 json


def history_add(entry: dict) -> dict:
    entry = {"id": uuid.uuid4().hex[:12], "ts": round(time.time(), 3), **entry}
    with _HIST_LOCK:
        items = _history_read()
        items.append(entry)
        _history_write(items[-HISTORY_MAX:])
    return entry


# ---------------------------------------------------------------------------
# 生成任务（单用户本地工具：一个线程跑一个任务，前端轮询进度）
# ---------------------------------------------------------------------------
_JOBS: dict[str, dict] = {}
_JOB_LOCK = threading.Lock()


def _job_set(jid: str, **kw) -> None:
    with _JOB_LOCK:
        _JOBS[jid].update(kw)


def _hist_entry(p: dict, kind: str, message: str = "", **extra) -> dict:
    """把一次任务的参数摊平成一条记录。

    只留「重建这次合成所需要的东西」：文本、音色、语种、指令与关键参数 ——
    够前端的「重新生成」把界面填回去，也够人回头看当时到底用了什么。
    """
    return {
        "kind": kind,                              # ok / error
        "mode": p.get("mode") or "clone",
        "text": p.get("text") or "",
        "language": p.get("language") or "Auto",
        "voice": p.get("voice_name") or "",
        "instruct": p.get("instruct") or "",
        "groups": p.get("groups") or [],
        "num_step": p.get("num_step"),
        "guidance_scale": p.get("guidance_scale"),
        "speed": p.get("speed"),
        "chunk_duration": p.get("chunk_duration"),
        # 三个开关也一并记下：界面上的「生成参数」要把这次合成完整摊开，
        # 少了这几项就无法回答「当时到底开没开降噪 / 后处理」。
        # （老记录里没有这几个键，前端按缺失跳过，不会显示成「关」。）
        "denoise": p.get("denoise"),
        "preprocess_prompt": p.get("preprocess_prompt"),
        "postprocess_output": p.get("postprocess_output"),
        "message": message,
        **extra,
    }


def _run_job(jid: str, p: dict) -> None:
    try:
        first_load = core._MODEL is None
        if first_load:
            _job_set(jid, state="running", progress=0.05,
                     message="正在加载模型（首次约 2.3 GB，之后常驻显存）…")
            core.load_model()          # 先把模型就绪，避免加载耗时被算进 RTF
        _job_set(jid, state="running", progress=0.30, message="正在合成…")

        before = set(f.name for f in config.OUTPUT_DIR.glob("*.wav"))
        t0 = time.time()
        audio, status, level = core.synthesize(
            p["text"], p["language"], p.get("ref_audio"), p.get("ref_text"),
            p.get("instruct"), p["num_step"], p["guidance_scale"], p["denoise"],
            p["speed"], p["duration"], p["preprocess_prompt"],
            p["postprocess_output"], p["chunk_duration"], p["mode"],
        )
        cost = time.time() - t0

        if audio is None:
            _job_set(jid, state="error", level=level, message=status, progress=1.0)
            history_add(_hist_entry(p, "error", message=status))
            return

        after = set(f.name for f in config.OUTPUT_DIR.glob("*.wav"))
        new = sorted(after - before)
        if not new:                                   # 理论上不会发生，兜底
            _job_set(jid, state="error", level="error",
                     message="生成完成，但未能在 outputs/ 找到输出文件。", progress=1.0)
            history_add(_hist_entry(p, "error", message="生成完成，但未能在 outputs/ 找到输出文件。"))
            return

        fname = new[-1]
        path = config.OUTPUT_DIR / fname
        try:
            info = sf.info(str(path))
            dur = info.frames / max(info.samplerate, 1)
        except Exception:                              # noqa: BLE001
            dur = 0.0

        # 状态行首行是核心给出的权威「音频 x s，耗时 y s，RTF z」——它的计时不含
        # 参考音频的转写与预处理。直接采用它，与命令行/Gradio 版保持一致；自己测的
        # 墙钟时间只用于在明显更慢时提示瓶颈在哪。
        mo = re.search(r"音频\s*([\d.]+)s[，,]\s*耗时\s*([\d.]+)s[，,]\s*RTF\s*([\d.]+)", status)
        gen_dur = float(mo.group(1)) if mo else dur
        gen_cost = float(mo.group(2)) if mo else cost
        gen_rtf = float(mo.group(3)) if mo else (cost / dur if dur else None)

        extra = "\n".join(status.splitlines()[1:]).strip()
        if cost - gen_cost > 5.0:
            extra = (f"参考音频处理约 {cost - gen_cost:.0f}s（含转写与预处理；"
                     f"当前转写引擎：{core.asr.describe()}）。"
                     "这个音色还没有缓存过参考文本，缓存一次之后就不会再走这一步。\n"
                     + extra).strip()

        _job_set(
            jid, state="done", progress=1.0, level=level,
            message=extra or "合成完成。",
            audio_url=f"/media/out/{fname}", file_name=fname,
            metrics={"duration": round(gen_dur, 2), "cost": round(gen_cost, 2),
                     "rtf": round(gen_rtf, 2) if gen_rtf else None,
                     "total": round(cost, 2)},
        )
        history_add(_hist_entry(
            p, "ok",
            file=fname, url=f"/media/out/{fname}",
            duration=round(gen_dur, 2), cost=round(gen_cost, 2),
            total=round(cost, 2), rtf=round(gen_rtf, 2) if gen_rtf else None,
            first_load=first_load, message=extra or "",
        ))
    except Exception as e:                             # noqa: BLE001
        msg = f"{type(e).__name__}: {e}"
        _job_set(jid, state="error", level="error", progress=1.0, message=msg)
        # 失败也留痕：界面上的错误提示一刷新就没了，回头排查得靠这条
        history_add(_hist_entry(p, "error", message=msg))


# ---------------------------------------------------------------------------
# 引导信息
# ---------------------------------------------------------------------------
@app.get("/api/bootstrap")
def bootstrap():
    prof = core.active_profile()
    return {
        "profile": {
            "device": prof.device, "device_name": prof.device_name,
            "vram_gib": prof.vram_gib, "dtype": prof.dtype,
            "num_step": prof.num_step,
            "chunk_duration": prof.audio_chunk_duration,
            "backend": prof.backend, "short": prof.short(),
            "rows": list(prof.rows()) + [("参考音频转写", core.asr.describe(detail=True))],
            "notes": prof.notes,
            "model": Path(config.resolve_model_path()).name,
        },
        "languages": core._ALL_LANGUAGES,
        "categories": [{"label": k, "choices": ["Auto"] + v}
                       for k, v in core._CATEGORIES.items()],
        # 供前端即时预览「将发送的指令」，避免与后端规则分叉
        "dialects": sorted(core._DIALECT_VALUES),
        "accent_map": core._ACCENT_TO_EN,
        "voices": _voices_payload(),
        "load_note": core._LOAD_NOTE,
        "asr": core.asr.describe(),
        # 参考音频的时长建议，界面据此给出「偏长 / 偏短」的即时反馈。
        # 放这里而不是写死在 index.html：这是音频语义，与后端同一份口径。
        "trim": {"min": core.TRIM_MIN_SECONDS,
                 "recommended": list(core.TRIM_RECOMMENDED)},
    }


# 参考音频时长缓存：音色列表每次刷新都会问一遍，而 sf.info 要走一次磁盘。
# 以 (size, mtime) 为键，文件一被改动就自然失效。
_DUR_CACHE: dict[str, tuple[tuple, float]] = {}


def _voice_duration(path: str | None) -> float:
    if not path:
        return 0.0
    try:
        st = Path(path).stat()
    except OSError:
        return 0.0
    key = (st.st_size, int(st.st_mtime))
    hit = _DUR_CACHE.get(str(path))
    if hit and hit[0] == key:
        return hit[1]
    dur = round(core.audio_duration(path), 2)
    _DUR_CACHE[str(path)] = (key, dur)
    return dur


def _voices_payload() -> list[dict]:
    out = []
    for n in core.list_voices():
        meta = core.load_voice_meta(n) or {}
        out.append({"name": n, "ref_text": meta.get("ref_text", ""),
                    "ref_audio": meta.get("ref_audio", ""),
                    # 参考音频已完成转写与编码（生成时无需重复处理）
                    "cached": core._voice_prompt_file(n).exists(),
                    # 时长是「这个音色合成快不快」的直接读数，列表里就该看得见
                    "duration": _voice_duration(meta.get("ref_audio")),
                    "url": f"/media/voice/{n}" if meta.get("ref_audio") else None})
    return out


@app.get("/api/voices")
def voices():
    return _voices_payload()


def _readable(src: str | None) -> Path:
    """把前端给的路径校验成「允许读取的真实文件」。

    参考音频只可能来自「刚上传的临时文件」或「音色库里已有的文件」，白名单把
    任意文件复制挡在外面。
    """
    if not src:
        raise HTTPException(400, "还没有参考音频，先上传一个。")
    p = Path(src).resolve()
    if not any(p.is_relative_to(base) for base in _READABLE_DIRS) or not p.is_file():
        raise HTTPException(400, "参考音频路径无效。")
    return p


def _voice_or_404(name: str) -> dict:
    """取音色的元信息；名字里带路径分隔符的**一律当成不存在**。

    `{name}` 是 URL 路径段，`%2e%2e%2f` 解出来就是 `../` —— 少了这一层，
    `config.VOICES_DIR / f"{name}.json"` 会被带出 voices/ 目录。名字本身由
    `core.sanitize_voice_name()` 在创建时就净化过，这里是第二道闸：老数据、
    或者别人直接打接口，都拦得住。
    """
    if "/" in name or "\\" in name or name in (".", ".."):
        raise HTTPException(404, f"音色「{name}」不存在。")
    meta = core.load_voice_meta(name)
    if not meta:
        raise HTTPException(404, f"音色「{name}」不存在。")
    return meta


def _trim_arg(payload: dict) -> tuple[float, float] | None:
    """解析选区，返回 (起点秒, 终点秒)；缺省或非法则为 None（表示「不裁剪」）。

    两种写法都接受：``{"trim": {"start": .., "end": ..}}``（保存音色、试转写时
    用，因为这两个请求还带着别的字段），以及顶层的 ``{"start": .., "end": ..}``
    （「裁剪某个音色」时选区就是整个请求的主体，再套一层反而绕）。
    """
    nested = payload.get("trim")
    src = nested if isinstance(nested, dict) else payload
    try:
        start, end = float(src.get("start")), float(src.get("end"))
    except (TypeError, ValueError):
        return None
    return (start, end) if end > start else None


@app.post("/api/transcribe")
def transcribe(payload: dict):
    """转写一段音频（可带裁剪），**不落盘**。

    存在的意义是让「先看看这段裁剪转成什么」成为可能：参考文本会被写进音色
    prompt，转错了整段合成都会跟着跑偏，所以值得在保存之前就让人看到。
    """
    srcp = _readable(payload.get("path"))
    trim = _trim_arg(payload)
    try:
        wave, sr = core.read_reference_audio(srcp)
    except Exception as e:                              # noqa: BLE001
        raise HTTPException(400, f"读不出这段音频（{type(e).__name__}: {e}）。")
    if trim:
        wave = core.trim_reference_audio(wave, sr, *trim)
    text, engine = core.transcribe_audio(wave, sr)
    if not text:
        reason = (core.asr.unavailable_reason() if not core.asr.enabled()
                  else "没转出内容（可能没有人声，或语种不在 zh/en/ja/ko/yue 内）")
        raise HTTPException(400, reason)
    return {"text": text, "engine": engine,
            "duration": round(len(wave) / max(sr, 1), 2)}


# 参考音频可能落盘的扩展名。换格式时要按这个集合清掉旧文件。
_AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".opus", ".aac", ".wma"}


def _name_note(typed: str, final: str) -> str:
    """名字被改动过就说明原因（净化 / 去重），没改就返回空串。

    界面直接显示这句话。不写死在界面里：是否被改只有后端知道，前端只能靠猜。
    """
    clean = core.sanitize_voice_name(typed)
    parts = []
    if clean != typed.strip():
        parts.append("名称里的空格与特殊字符已替换为下划线")
    if final != clean:
        parts.append("已有同名音色，自动加了序号")
    return "；".join(parts)


def _place_reference(stem: str, srcp: Path, trim, typed_text: str = "",
                     previous_meta: dict | None = None) -> dict:
    """把参考音频落到 ``voices/<stem>.*``，定下参考文本，写 json 并重建音色缓存。

    「新建音色」与「重新上传音频」共用这一段。**必须共用**：落盘方式、缓存失效
    口径、参考文本的来源判定，只要两条路径各写一遍，换个入口进来行为就会不一样。

    没裁剪就原样搬文件（省一次无谓的重编码）；裁过就编码成 wav。
    参考文本按 手填 → 转写 → **保留旧值** 的顺序定：
    把旧文本抹掉比留着更糟 —— 音色会变成「没有参考文本」，得靠人重新来一遍。
    """
    wave = sr = None
    if trim:
        try:
            wave, sr = core.read_reference_audio(srcp)
        except Exception as e:                          # noqa: BLE001
            raise HTTPException(400, f"读不出这段音频（{type(e).__name__}: {e}）。")
        wave = core.trim_reference_audio(wave, sr, *trim)
        dst = config.VOICES_DIR / f"{stem}.wav"
        core.write_reference_audio(wave, sr, dst)
        duration = len(wave) / max(sr, 1)
    else:
        dst = config.VOICES_DIR / f"{stem}{srcp.suffix or '.wav'}"
        shutil.copyfile(srcp, dst)                      # 没裁剪就别重编码，原样搬
        duration = core.audio_duration(dst)

    # 换了格式（wav ↔ mp3）时旧文件要清掉，否则 voices/ 里会留下一个没人引用的音频，
    # 而它还会被 /api/voices 的时长统计读到。
    # 刻意用 iterdir + stem 精确比对而不是 glob(f"{stem}.*")：音色名里合法地可以含
    # `[` `]`，那种名字进 glob 会变成字符类，模式会匹配到**别的音色**的文件。
    for old in config.VOICES_DIR.iterdir():
        if old.is_file() and old.stem == stem and old.suffix.lower() in _AUDIO_SUFFIXES \
                and old.resolve() != dst.resolve():
            old.unlink(missing_ok=True)

    previous = (previous_meta or {}).get("ref_text") or ""
    text = (typed_text or "").strip()
    source = "手填" if text else None
    failure = None
    if not text:
        if wave is not None:
            text, source = core.transcribe_audio(wave, sr)
        else:
            text, source = core._transcribe_ref(str(dst))
        if not text:
            failure = source                      # 例如「SenseVoice 无结果」，用来解释为什么没换文本
    kept_old = not text and bool(previous)
    if kept_old:
        text = previous

    # 音频换过了，旧 prompt 存的还是旧音频编码出的 token，留着只会让下次生成用错声音。
    # 缓存指纹本来也会因 size/mtime 变化而失效，这里显式作废是为了不去读它。
    core._voice_prompt_file(stem).unlink(missing_ok=True)
    meta = {k: v for k, v in (previous_meta or {}).items() if k != "prompt_cache"}
    meta.update({"name": stem, "ref_audio": str(dst), "ref_text": text})
    (config.VOICES_DIR / f"{stem}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # 模型已加载时顺手把 prompt 建好，首次生成即命中缓存；未加载则留给首次生成
    return {"audio": str(dst), "duration": duration, "ref_text": text, "source": source,
            "failure": failure, "kept_old": kept_old, "note": core.warm_voice_prompt(stem)}


@app.post("/api/voices")
def save_voice(payload: dict):
    """把一份参考音频存成音色：先按需裁剪，再落盘音频与参考文本。

    这一版刻意做成**同步**的：裁剪在内存里完成、转写是亚秒级，两者加起来一两秒，
    换来「点保存 = 音频和转写都已经就位」这个明确结果 —— 不必再去别的页面补一步。
    （只有建 prompt 缓存仍留在后台，因为那需要 2.3 GB 的主模型已加载。）
    """
    disp = (payload.get("name") or "").strip()
    if not disp:
        raise HTTPException(400, "请填写音色名称。")
    srcp = _readable(payload.get("path"))
    trim = _trim_arg(payload)

    # 名字会经 sanitize_voice_name 收敛（它同时是文件名），还可能因重名加序号 ——
    # 两件事都要如实告诉用户，否则他会发现「我打的字怎么变了」。
    stem = core._unique_name(disp)
    placed = _place_reference(stem, srcp, trim, payload.get("ref_text") or "")

    duration, text, source = placed["duration"], placed["ref_text"], placed["source"]
    # 转写没成功也照样把音色留下：音频是好的，缺的只是一段文本，
    # 事后可以在音色库里手填或重新转写。整条回滚反而把用户的上传白费掉。
    if text:
        msg = (f"已保存音色「{stem}」（{duration:.2f}s）。参考文本由 {source} 生成。"
               if source != "手填" else f"已保存音色「{stem}」（{duration:.2f}s）。")
        level = "ok"
    else:
        reason = (core.asr.unavailable_reason() if not core.asr.enabled()
                  else (placed["failure"] or "没转出内容"))
        msg = (f"已保存音色「{stem}」（{duration:.2f}s），但没能自动转写参考文本："
               f"{reason}。可在列表里选中它，手填或点「重新转写」。")
        level = "warn"

    return {"name": stem, "renamed": stem != disp, "name_note": _name_note(disp, stem),
            "note": placed["note"],
            "ref_text": text, "text_source": source,
            "duration": round(duration, 2),
            "message": msg, "level": level,
            "voices": _voices_payload()}


@app.post("/api/voices/{name}/audio")
def replace_voice_audio(name: str, payload: dict):
    """就地换掉某个音色的参考音频 —— 界面上的「重新上传音频」。

    为什么不复用 ``POST /api/voices``：那条路会走 `_unique_name` 去重，对「新建」
    是对的，对「替换」就错了 —— 它会另起一个新音色，而不是覆盖原来那个。
    所以单开一条，语义写死在路由里：**名字必须已存在，音频就地替换**。

    代价是原音频与旧 prompt 缓存一并作废（不可撤销），界面上有明确告警。
    """
    meta = _voice_or_404(name)
    srcp = _readable(payload.get("path"))
    trim = _trim_arg(payload)

    placed = _place_reference(name, srcp, trim, payload.get("ref_text") or "",
                              previous_meta=meta)
    duration, text, source = placed["duration"], placed["ref_text"], placed["source"]
    tail = "原音频与旧缓存已作废。" + (placed["note"] or "")

    if placed["kept_old"]:
        reason = (core.asr.unavailable_reason() if not core.asr.enabled()
                  else (placed["failure"] or "没转出内容"))
        msg = (f"已替换「{name}」的参考音频（{duration:.2f}s）。新音频没能自动转写："
               f"{reason} —— 参考文本仍沿用替换前那段，建议核对后手填或点「重新转写」。")
        level = "warn"
    elif text:
        msg = (f"已替换「{name}」的参考音频（{duration:.2f}s）。"
               + ("参考文本已按你填写的内容更新。" if source == "手填"
                  else f"参考文本已用 {source} 重新转写。"))
        level = "ok"
    else:
        reason = (core.asr.unavailable_reason() if not core.asr.enabled()
                  else (placed["failure"] or "没转出内容"))
        msg = (f"已替换「{name}」的参考音频（{duration:.2f}s），但没能转出参考文本："
               f"{reason}。请在右侧手填，或点「重新转写」。")
        level = "warn"

    return {"name": name, "duration": round(duration, 2), "ref_text": text,
            "text_source": source, "message": f"{msg}{tail}", "level": level,
            "voices": _voices_payload()}


@app.post("/api/voices/{name}/trim")
def trim_voice(name: str, payload: dict):
    """就地裁剪已有音色的参考音频（可选重新转写参考文本）。

    这是「参考音频太长导致合成慢」的最短修复路径：不必删掉音色重存一遍。
    音频被换掉后，旧 prompt 缓存必然失效 —— 它保存的是旧音频编码出的
    ref_audio_tokens，留着只会让下次生成用错声音。
    """
    meta = _voice_or_404(name)
    src = meta.get("ref_audio")
    if not src or not Path(src).exists():
        raise HTTPException(400, "这个音色没有可用的参考音频文件。")
    trim = _trim_arg(payload)
    if not trim:
        raise HTTPException(400, "选区无效：终点必须大于起点。")

    try:
        wave, sr = core.read_reference_audio(src)
    except Exception as e:                              # noqa: BLE001
        raise HTTPException(400, f"读不出参考音频（{type(e).__name__}: {e}）。")
    wave = core.trim_reference_audio(wave, sr, *trim)

    dst = config.VOICES_DIR / f"{name}.wav"
    core.write_reference_audio(wave, sr, dst)
    old = Path(src)
    if old.resolve() != dst.resolve() and old.exists():
        old.unlink(missing_ok=True)                     # mp3 → wav，旧文件要清掉
    meta["ref_audio"] = str(dst)
    duration = len(wave) / max(sr, 1)

    want_retr = payload.get("retranscribe", True) is not False
    text = (payload.get("ref_text") or "").strip()
    source = "手填" if text else None
    failure = None
    if not text and want_retr:
        got, engine = core.transcribe_audio(wave, sr)
        if got:
            text, source = got, engine
        else:
            failure = engine          # 例如「SenseVoice 无结果」，用来解释为什么没换文本
    if text:
        meta["ref_text"] = text

    core._voice_prompt_file(name).unlink(missing_ok=True)
    meta.pop("prompt_cache", None)
    (config.VOICES_DIR / f"{name}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    note = core.warm_voice_prompt(name)

    parts = [f"已把音色「{name}」的参考音频裁到 {duration:.2f}s。"]
    if source == "手填":
        parts.append("参考文本已按你填写的更新。")
    elif source:
        parts.append(f"参考文本已用 {source} 重新转写。")
    elif want_retr:
        parts.append(f"但没能重新转写（{failure or '没转出内容'}），原参考文本保持不变。")
    else:
        parts.append("参考文本保持原样。")
    parts.append("音色缓存已作废，下次生成时重建。")
    if note:
        parts.append(note)
    # 级别看的是「这个音色最终有没有参考文本」，而不是本轮有没有转写：
    # 保留原文、选区重裁都是正常结果，不该报成需要留意。
    return {"name": name, "duration": round(duration, 2),
            "message": " ".join(parts),
            "level": "ok" if (meta.get("ref_text") or "").strip() else "warn",
            "voices": _voices_payload()}


@app.post("/api/voices/{name}/rename")
def rename_voice(name: str, payload: dict):
    meta = _voice_or_404(name)
    new = (payload.get("to") or "").strip()
    if not new:
        raise HTTPException(400, "请输入新的音色名称。")
    if new == name:
        raise HTTPException(400, "新旧名称相同，未做修改。")

    stem = core._unique_name(new)
    src = Path(meta["ref_audio"]) if meta.get("ref_audio") else None
    moved: Path | None = None
    if src and src.exists():
        moved = config.VOICES_DIR / f"{stem}{src.suffix or '.wav'}"
        src.replace(moved)
    (config.VOICES_DIR / f"{name}.json").unlink(missing_ok=True)
    # `prompt_cache` 必须跟着搬：指纹看的是源音频的 size/mtime（刻意不含文件名），
    # 而 replace() 保留这两项 —— 所以改名后缓存依然有效。曾经这里只写回
    # name/ref_audio/ref_text，把指纹丢掉了，于是「注释说缓存不受影响、实际每次都重建」。
    new_meta = {k: v for k, v in meta.items() if k != "name"}
    new_meta.update({"name": stem, "ref_audio": str(moved) if moved else ""})
    (config.VOICES_DIR / f"{stem}.json").write_text(
        json.dumps(new_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    # prompt 缓存文件本身也跟着搬
    old_pt = core._voice_prompt_file(name)
    if old_pt.exists():
        old_pt.replace(core._voice_prompt_file(stem))
    return {"name": stem, "renamed": stem != new, "name_note": _name_note(new, stem),
            "voices": _voices_payload()}


@app.post("/api/voices/{name}/retranscribe")
def retranscribe_voice(name: str):
    """重新转写某个音色的参考音频，并重建它的音色缓存。

    用的是 core.retranscribe_voice()：音色的参考文本过期时能就地刷新，
    不必删掉音色再重存一遍（历史版本用 Whisper 转出的是无标点文本）。
    """
    _voice_or_404(name)
    msg, level = core.retranscribe_voice(name)
    if level == "error":
        raise HTTPException(400, msg)
    return {"message": msg, "level": level, "voices": _voices_payload()}


@app.post("/api/voices/delete")
def delete_voices(payload: dict):
    names = list(payload.get("names") or [])
    if not names:
        raise HTTPException(400, "请先选择要删除的音色。")
    removed, failed = [], []
    for n in names:
        if "/" in n or "\\" in n or n in (".", ".."):
            failed.append({"name": n, "error": "名称非法，已跳过。"})
            continue
        meta = core.load_voice_meta(n) or {}
        errs = []
        for target in (meta.get("ref_audio"), config.VOICES_DIR / f"{n}.json",
                       core._voice_prompt_file(n)):
            if not target:
                continue
            try:
                Path(target).unlink(missing_ok=True)
            except (OSError, SystemExit) as e:
                # 逐个文件兜住，而不是整段 try：一个文件删不掉（被占用、权限、
                # 宿主环境的删除守卫等）不该让整个音色都算失败，更不该让请求 500。
                # SystemExit 不是 Exception —— 某些沙箱/托管环境会在 unlink 上挂
                # 删除守卫，拒绝时直接 SystemExit(1)，只写 except Exception 会被它穿透。
                errs.append(f"{Path(target).name}（{type(e).__name__}）")
        if errs:
            failed.append({"name": n, "error": "删除失败：" + "、".join(errs)})
        else:
            removed.append(n)
    return {"removed": removed, "failed": failed, "voices": _voices_payload()}


# ---------------------------------------------------------------------------
# 参考音频上传
# ---------------------------------------------------------------------------
@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix or ".wav"
    fname = f"{uuid.uuid4().hex[:12]}{suffix}"
    (UPLOAD_DIR / fname).write_bytes(await file.read())
    return {"file": fname, "name": file.filename,
            "path": str((UPLOAD_DIR / fname).resolve()),
            "url": f"/media/upload/{fname}"}


# ---------------------------------------------------------------------------
# 合成
# ---------------------------------------------------------------------------
_FLOAT_KEYS = {"guidance_scale", "speed", "chunk_duration"}
_INT_KEYS = {"num_step"}
_BOOL_KEYS = {"denoise", "preprocess_prompt", "postprocess_output"}


# ---------------------------------------------------------------------------
# 合成记录
# ---------------------------------------------------------------------------
@app.get("/api/history")
def get_history():
    """按时间倒序返回（新的在前），并标出音频文件是否还在。

    文件可能被人手工清理过 outputs/，那条记录仍值得留着（它记的是当时用了什么
    参数），但要能一眼看出已经播不了。
    """
    items = list(reversed(_history_read()))
    for x in items:
        f = x.get("file")
        x["exists"] = bool(f) and (config.OUTPUT_DIR / f).is_file()
    return {"items": items}


@app.post("/api/history/delete")
def delete_history(payload: dict):
    """只删记录，不动 outputs/ 里的 wav —— 文件是产物，用户可能还要用。"""
    ids = {str(x) for x in (payload.get("ids") or [])}
    if not ids:
        return JSONResponse({"error": "没有指定要删除的记录。"}, status_code=400)
    with _HIST_LOCK:
        items = _history_read()
        left = [x for x in items if x.get("id") not in ids]
        _history_write(left)
    return {"removed": len(items) - len(left), "items": list(reversed(left))}


@app.post("/api/history/clear")
def clear_history():
    with _HIST_LOCK:
        n = len(_history_read())
        _history_write([])
    return {"removed": n, "items": []}


@app.post("/api/jobs")
def create_job(payload: dict):
    text = (payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "请先输入要合成的文本。"}, status_code=400)

    mode = payload.get("mode") or "clone"
    prof = core.active_profile()

    groups = payload.get("groups") or []
    instruct = payload.get("instruct") or None
    if mode == "design":
        instruct = core._to_instruct([g for g in groups if g and g != "Auto"])

    params = {
        "mode": mode,
        "text": text,
        "language": payload.get("language") or "Auto",
        "ref_audio": payload.get("ref_audio") or None,
        "ref_text": payload.get("ref_text") or None,
        "instruct": instruct,
        # 仅用于合成记录里显示「用的哪个音色」。可由 ref_audio 反查，但记录该是
        # 自足的 —— 音色被改名或删掉后，历史条目仍应说得出当时用的是谁。
        "voice_name": (payload.get("voice_name") or "").strip(),
        # 记下属性下拉的原始选择（而不只是合并后的 instruct 串），
        # 「重新生成」才能把设计页的下拉原样填回去
        "groups": [g for g in groups if g and g != "Auto"],
        "num_step": int(payload.get("num_step") or prof.num_step),
        "guidance_scale": float(payload.get("guidance_scale") or 2.0),
        "denoise": bool(payload.get("denoise", True)),
        "speed": float(payload.get("speed") or 1.0),
        "duration": (float(payload["duration"])
                     if payload.get("duration") else None),
        "preprocess_prompt": bool(payload.get("preprocess_prompt", True)),
        "postprocess_output": bool(payload.get("postprocess_output", True)),
        "chunk_duration": float(payload.get("chunk_duration")
                                or prof.audio_chunk_duration),
    }
    if mode == "clone" and not params["ref_audio"]:
        return JSONResponse(
            {"error": "请先从音色库选择一个音色（还没有音色就去「音色库」页新建一个）。"},
            status_code=400)

    jid = uuid.uuid4().hex[:12]
    with _JOB_LOCK:
        _JOBS[jid] = {"state": "queued", "message": "排队中…", "progress": 0.0,
                      "started": time.time()}
    threading.Thread(target=_run_job, args=(jid, params), daemon=True).start()
    return {"job_id": jid}


@app.get("/api/jobs/{jid}")
def job_status(jid: str):
    job = _JOBS.get(jid)
    if not job:
        raise HTTPException(404, "任务不存在或已过期。")
    return job


# ---------------------------------------------------------------------------
# 媒体
# ---------------------------------------------------------------------------
@app.get("/media/out/{name}")
def media_out(name: str):
    return _send(config.OUTPUT_DIR, name)


@app.get("/media/upload/{name}")
def media_upload(name: str):
    return _send(UPLOAD_DIR, name)


@app.get("/media/voice/{name}")
def media_voice(name: str):
    meta = core.load_voice_meta(name)
    if not meta or not meta.get("ref_audio"):
        raise HTTPException(404, "该音色没有参考音频。")
    p = Path(meta["ref_audio"])
    if not p.exists():
        raise HTTPException(404, "参考音频文件已丢失。")
    return FileResponse(str(p), media_type=AUDIO_MIME.get(p.suffix.lower(),
                                                          "application/octet-stream"))


def _send(base: Path, name: str) -> FileResponse:
    p = (base / name).resolve()
    if base.resolve() not in p.parents or not p.is_file():   # 防目录穿越
        raise HTTPException(404, "文件不存在。")
    return FileResponse(str(p), media_type=AUDIO_MIME.get(p.suffix.lower(),
                                                          "application/octet-stream"))


# ---------------------------------------------------------------------------
# 接口文档
# ---------------------------------------------------------------------------
@app.get("/api/docs")
def api_docs(request: Request):
    """给「API 对接」页用的接口清单。

    内容全在 web/api_docs.py，这里只负责把**当前真实的服务地址**传进去
    （换端口 / 换 host 都不用改文档），以及把 app 交出去做「文档 vs 代码」核对。
    """
    return api_docs_mod.build_spec(app, str(request.base_url).rstrip("/"))


# ---------------------------------------------------------------------------
# 静态资源（必须最后挂载，否则会吃掉 /api 与 /media）
# ---------------------------------------------------------------------------
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    print(f"OmniVoice Studio (WebUI)  →  http://{config.HOST}:{config.PORT}")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")
