"""API 文档的**唯一来源**（给 web/static/index.html 的「API 对接」页 + 给 Agent 用）。

为什么不直接在页面里手写一份文档：路径和方法会随代码变，手写的那份必然在
某次改动之后悄悄过期，而且过期了没人会发现。所以这里的做法是：

  · 人写的部分 = 路径 / 方法 / 参数含义 / 返回说明 / 示例
  · `build_spec()` 再把它 **和 app.routes 里实际登记的路由对一遍**
  · 两个方向对不上都报出来：
      undocumented —— 代码里有、文档里没有（新加了接口，忘了写文档）
      ghost        —— 文档里有、代码里没有（接口删了或改名，文档没跟上）
    前端把这两个数直接摆在页面上。**文档一旦偏离代码，页面上是看得见的。**

`/openapi.json` 是 FastAPI 自动生成的机器可读规格（Agent 可用），但它对
`payload: dict` 这类入参只有结构、没有字段含义 —— 本文件补的正是**语义**那一层。
"""

from __future__ import annotations

import json
from typing import Any

# ---------------------------------------------------------------------------
# 分组
# ---------------------------------------------------------------------------
GROUPS: list[dict] = [
    {"id": "meta", "label": "元信息", "note": "接入后先读一次，拿到设备、语种与音色清单。"},
    {"id": "voice", "label": "音色库",
     "note": "上传 → 裁剪 → 保存 → 试转写；已保存的音色可就地换参考音频（/audio）或裁剪（/trim）。"},
    {"id": "synth", "label": "语音合成", "note": "提交任务 + 轮询进度；完成后从 /media/out/ 取音频。"},
    {"id": "history", "label": "合成记录", "note": "只记参数，不存音频副本；删记录不会删 outputs/ 里的 wav。"},
    {"id": "media", "label": "媒体文件", "note": "按文件名取音频流；带目录穿越校验。"},
    {"id": "docs", "label": "文档", "note": "本页的数据来源与机器可读规格。"},
]

# 只读、可安全「试一试」的接口（页面里直接发真实请求）。写操作一律不给，
# 免得点一下就在别人机器上建了任务或删了音色。
TRYABLE = {"/api/bootstrap", "/api/voices", "/api/history"}

# 参数写法：(位置, 名称, 类型, 是否必填, 说明)
#   位置 ∈ body / path / query / form
ENDPOINTS: list[dict[str, Any]] = [
    # ---------------- 元信息 ----------------
    {
        "group": "meta", "method": "GET", "path": "/api/bootstrap",
        "title": "启动元信息",
        "desc": "设备 / 精度 / 语种表 / 说话人属性 / 音色列表 / 裁剪建议 / 转写引擎。"
                "没有参数，一次请求就能知道这台机器上能做什么。",
        "params": [],
        "returns": "{ profile{ device, device_name, vram_gib, dtype, num_step, chunk_duration, "
                   "backend, short, rows, notes, model }, languages[], categories[], "
                   "dialects[], accent_map, voices[], load_note, asr, trim{min, recommended} }",
        "note": "languages 有 647 项且**首项是 \"Auto\"**；accent_map 是「口音显示名 → 模型认的英文名」的映射；"
                "categories 是 [{label, choices[]}]，choices 首项同样是 \"Auto\"。"
                "模型名在 profile.model 里，不在顶层。",
    },

    # ---------------- 音色库 ----------------
    {
        "group": "voice", "method": "GET", "path": "/api/voices",
        "title": "音色列表",
        "desc": "所有已保存的音色，含参考音频时长与 prompt 缓存状态。",
        "params": [],
        "returns": "[{ name, ref_text, ref_audio, cached, duration, url }] —— **裸数组**，没有外层包装",
        "note": "合成时要用的 `ref_audio` 就是这里的**绝对路径**，直接回传给 /api/jobs。"
                "`cached` 为 true 表示音色 prompt 已建好，下次合成直接命中（否则要转写+编码一次）。"
                "注意 `url` 在极少数没有参考音频的音色上会是 null。",
    },
    {
        "group": "voice", "method": "POST", "path": "/api/upload",
        "title": "上传参考音频",
        "desc": "把一段音频放进临时上传目录，返回可被后续接口引用的绝对路径。",
        "params": [("form", "file", "file", True, "音频文件（multipart/form-data 的字段名固定为 file）")],
        "returns": "{ file, name, path, url }",
        "form": {"file": "@/path/to/ref.wav"},
        "note": "`path` 落在本机的 uploads/ 目录；play 一遍用返回的 `url`。",
    },
    {
        "group": "voice", "method": "POST", "path": "/api/voices",
        "title": "保存音色",
        "desc": "把一份参考音频存成音色：按需裁剪，落盘音频与参考文本。**同步完成**，"
                "返回时音频和文本都已就位（只有 prompt 缓存留后台建）。",
        "params": [
            ("body", "name", "string", True, "音色名称；重名会自动加后缀去重。**界面默认填上传文件名**（去扩展名）"),
            ("body", "path", "string", True, "参考音频的绝对路径，须在 uploads/ 或 voices/ 内"),
            ("body", "ref_text", "string", False, "参考文本。**填了就不转写**，质量更可控"),
            ("body", "trim", "object", False, "选区 {\"start\": 秒, \"end\": 秒}；不传则原样保存（不重编码）"),
        ],
        "returns": "{ name, renamed, name_note, note, ref_text, text_source, duration, message, "
                   "level, voices: [...] }",
        "body": {"name": "我的音色", "path": "<upload 返回的 path>", "ref_text": "大家好。", "trim": {"start": 0.5, "end": 6.5}},
        "note": "参考音频官方建议 3–10 秒；超过会让前向耗时超线性变长（实测 16.9s 参考比 3s 慢 30 倍）。"
                "`level` 为 \"warn\" 表示音色存下来了、但没自动转出参考文本（可在音色库补填或重转）。"
                "返回的 `voices` 是**最新全量列表**，可以直接替换本地缓存，不必再 GET 一次。"
                "`name` 是**最终落盘的名字**，可能已被净化（空格与 `<>:\"/\\\\|?*` 换成下划线、限长 60）"
                "或去重加序号；`renamed` 表示它与传入值不同，`name_note` 是给人看的原因说明（没改动则为空串）。",
    },
    {
        "group": "voice", "method": "POST", "path": "/api/voices/{name}/audio",
        "title": "替换音色参考音频",
        "desc": "**就地**换掉某个已保存音色的参考音频（界面上的「重新上传音频」），可选裁剪，"
                "参考文本留空则由新音频重新转写。原音频文件与旧 prompt 缓存一并作废。",
        "params": [
            ("path", "name", "string", True, "现有音色名；不存在则 404"),
            ("body", "path", "string", True, "新参考音频的绝对路径，须在 uploads/ 或 voices/ 内"),
            ("body", "ref_text", "string", False, "参考文本。填了就不转写；留空则转写新音频"),
            ("body", "trim", "object", False, "选区 {\"start\", \"end\"}；不传则原样搬文件"),
        ],
        "returns": "{ name, duration, ref_text, text_source, message, level, voices: [...] }",
        "body": {"path": "<upload 返回的 path>", "trim": {"start": 0.5, "end": 6.5}},
        "note": "**不可撤销**：原音频会被删掉。名字保持原样 —— 这条路由刻意不走 `_unique_name` 去重，"
                "否则「替换」会变成另起一个新音色。转写失败时**沿用替换前那段参考文本**并返回 level=\"warn\"，"
                "不会把文本清空（音色一旦没有参考文本，生成质量会明显掉）。",
    },
    {
        "group": "voice", "method": "POST", "path": "/api/transcribe",
        "title": "试转写（不落盘）",
        "desc": "看看这段音频（可带裁剪）会被转成什么文本。参考文本会写进音色 prompt，"
                "转错整段合成都跟着跑偏，所以值得在保存前先看一眼。",
        "params": [
            ("body", "path", "string", True, "音频绝对路径"),
            ("body", "trim", "object", False, "选区 {\"start\", \"end\"}；也可用顶层 start/end"),
        ],
        "returns": "{ text, engine, duration }",
        "body": {"path": "<upload 返回的 path>", "trim": {"start": 0.5, "end": 6.5}},
    },
    {
        "group": "voice", "method": "POST", "path": "/api/voices/{name}/rename",
        "title": "重命名音色",
        "desc": "改名会连参考音频与 prompt 缓存一起搬过去（缓存指纹只看 size/mtime，改名不影响有效性）。",
        "params": [
            ("path", "name", "string", True, "现有音色名"),
            ("body", "to", "string", True, "新名称；重名会自动加后缀"),
        ],
        "returns": "{ name, renamed, name_note, voices: [...] }",
        "body": {"to": "新名字"},
        "note": "新名字同样会被净化并去重（规则见 `POST /api/voices`），`name_note` 说明原因；"
                "重名会自动加序号，例如 `于谦` → `于谦-2`。",
    },
    {
        "group": "voice", "method": "POST", "path": "/api/voices/{name}/retranscribe",
        "title": "重转参考文本",
        "desc": "就地重新转写参考音频并**重建音色缓存**。参考文本参与生成，所以改文本必须连 prompt 一起重建。",
        "params": [("path", "name", "string", True, "音色名")],
        "returns": "{ message, level, voices: [...] }",
    },
    {
        "group": "voice", "method": "POST", "path": "/api/voices/{name}/trim",
        "title": "裁剪音色参考音频",
        "desc": "就地裁剪已有音色的参考音频，可选重新转写。作废旧 prompt 缓存。",
        "params": [
            ("path", "name", "string", True, "音色名"),
            ("body", "start", "number", True, "起点秒"),
            ("body", "end", "number", True, "终点秒"),
            ("body", "retranscribe", "boolean", False, "默认 true；只有显式传 false 才跳过重新转写"),
            ("body", "ref_text", "string", False, "直接指定裁剪后的参考文本；填了就不转写"),
        ],
        "returns": "{ name, duration, message, level, voices: [...] }",
        "body": {"start": 0.2, "end": 6.5, "retranscribe": True},
        "note": "裁剪必然作废旧的 prompt 缓存（缓存里存的是旧音频编码出的 token），返回时已重新建好。",
    },
    {
        "group": "voice", "method": "POST", "path": "/api/voices/delete",
        "title": "删除音色",
        "desc": "批量删除。逐个文件捕获异常，失败的会列在 failed 里，不会因为一个删不掉就整批失败。",
        "params": [("body", "names", "string[]", True, "要删的音色名数组")],
        "returns": "{ removed: [...], failed: [...], voices: [...] }",
        "body": {"names": ["临时音色"]},
        "note": "`failed` 里每项是 {name, error}；逐个文件兜异常，一个删不掉不会让整批失败，"
                "也不会 500。返回的 `voices` 已是最新列表。",
    },

    # ---------------- 合成 ----------------
    {
        "group": "synth", "method": "POST", "path": "/api/jobs",
        "title": "提交合成任务",
        "desc": "提交后立即返回 job_id，真正合成在后台线程里跑。**必须轮询 /api/jobs/{job_id}**。",
        "params": [
            ("body", "text", "string", True, "要合成的文本（超长会按 chunk_duration 切块拼接）"),
            ("body", "mode", "string", False, "clone（默认，用参考音频） / design（用说话人属性描述）"),
            ("body", "language", "string", False, "语种，默认 \"Auto\" 自动识别；取值来自 bootstrap.languages"),
            ("body", "ref_audio", "string", False, "参考音频绝对路径。**mode=clone 时必填**，取自 /api/voices"),
            ("body", "ref_text", "string", False, "参考文本覆盖。一般不传 —— 音色自带的参考文本会参与生成"),
            ("body", "voice_name", "string", False, "只用于合成记录里显示用了谁；可由 ref_audio 反查，但记录该是自足的"),
            ("body", "instruct", "string", False, "附加指令自由文本（如 \"Chinese dialect, Sichuan\"）。mode=design 时被 groups 覆盖"),
            ("body", "groups", "string[]", False, "mode=design 用：说话人属性下拉值数组，服务端会合成 instruct"),
            ("body", "num_step", "integer", False, "推理步数，默认取设备自动档（本机 32）。越低越快"),
            ("body", "guidance_scale", "number", False, "引导强度，默认 2.0"),
            ("body", "speed", "number", False, "语速，默认 1.0"),
            ("body", "chunk_duration", "number", False, "长文本分段秒数，默认取设备档（本机 10）"),
            ("body", "duration", "number", False, "固定输出时长秒；不传则按语速估算"),
            ("body", "denoise", "boolean", False, "降噪，默认 true"),
            ("body", "preprocess_prompt", "boolean", False, "预处理参考音频（去静音、裁剪、补标点），默认 true"),
            ("body", "postprocess_output", "boolean", False, "输出后处理（去长静音、归一化、淡入淡出），默认 true"),
        ],
        "returns": "{ job_id }；参数非法时返回 HTTP 400 + { error }"
                   "（部分校验走 FastAPI 的 HTTPException，那种情况字段名是 detail）",
        "body": {
            "mode": "clone",
            "text": "欢迎收听今天的节目。",
            "language": "Auto",
            "ref_audio": "<取自 GET /api/voices 的 ref_audio>",
            "voice_name": "于谦",
            "num_step": 32,
            "guidance_scale": 2.0,
            "speed": 1.0,
            "chunk_duration": 10.0,
        },
        "note": "首次合成会先加载模型（约 2.3 GB，一两分钟），之后常驻显存；"
                "所以**第一次轮询会明显慢**，别把它当成卡死。",
    },
    {
        "group": "synth", "method": "GET", "path": "/api/jobs/{jid}",
        "title": "查询任务进度",
        "desc": "轮询这个接口直到 state 变成 done 或 error。",
        "params": [("path", "jid", "string", True, "提交时拿到的 job_id")],
        "returns": "{ state: queued|running|done|error, progress: 0–1, message, level?, "
                   "audio_url?, file_name?, metrics?: { duration, cost, rtf, total } }",
        "note": "任务只存在内存里，重启服务就没了；音频已经落盘到 outputs/，不受影响。",
    },

    # ---------------- 合成记录 ----------------
    {
        "group": "history", "method": "GET", "path": "/api/history",
        "title": "合成记录",
        "desc": "按时间倒序（新的在前）。成功和失败都记；失败也留痕是因为界面上的报错一刷新就没了。",
        "params": [],
        "returns": "{ items: [{ id, ts, kind, mode, text, voice, language, instruct, groups, "
                   "num_step, guidance_scale, speed, chunk_duration, denoise, preprocess_prompt, "
                   "postprocess_output, file, url, duration, cost, rtf, total, exists }] }",
        "note": "`duration` 是**音频时长**；提交参数里的 `duration` 是「固定时长」，同名不同义，别混。",
    },
    {
        "group": "history", "method": "POST", "path": "/api/history/delete",
        "title": "删除记录",
        "desc": "只删 json 里的记录，**不动 outputs/ 里的 wav** —— 文件是产物，人可能还要用。",
        "params": [("body", "ids", "string[]", True, "记录 id 数组")],
        "returns": "{ removed: 删掉的条数, items: 剩余记录（倒序） }",
    },
    {
        "group": "history", "method": "POST", "path": "/api/history/clear",
        "title": "清空记录",
        "desc": "清空全部记录，同样不影响 outputs/ 里的音频文件。",
        "params": [],
        "returns": "{ removed: 清掉的条数, items: [] （恒为空数组） }",
    },

    # ---------------- 媒体 ----------------
    {
        "group": "media", "method": "GET", "path": "/media/out/{name}",
        "title": "取合成结果",
        "desc": "按文件名取 outputs/ 里的 wav。",
        "params": [("path", "name", "string", True, "文件名，如 clone-20261014-205110.wav")],
        "returns": "audio/wav 二进制流",
        "raw": True,
    },
    {
        "group": "media", "method": "GET", "path": "/media/upload/{name}",
        "title": "取上传的音频",
        "desc": "取 uploads/ 里的临时音频（试听上传的参考音频用）。",
        "params": [("path", "name", "string", True, "文件名")],
        "returns": "音频二进制流",
        "raw": True,
    },
    {
        "group": "media", "method": "GET", "path": "/media/voice/{name}",
        "title": "取音色参考音频",
        "desc": "取某个音色的参考音频，用于试听。",
        "params": [("path", "name", "string", True, "音色名")],
        "returns": "音频二进制流",
        "raw": True,
    },

    # ---------------- 文档 ----------------
    {
        "group": "docs", "method": "GET", "path": "/api/docs",
        "title": "本页的数据来源",
        "desc": "返回这份接口清单本身（含与代码的核对结果）。Agent 也可以读它来发现能力。",
        "params": [],
        "returns": "{ base, groups[], endpoints[], flows[], undocumented[], ghost[], counts }",
    },
    {
        "group": "docs", "method": "GET", "path": "/openapi.json",
        "title": "OpenAPI 规格",
        "desc": "FastAPI 自动生成的机器可读规格，适合直接喂给支持 OpenAPI 的 Agent 框架。",
        "params": [],
        "returns": "OpenAPI 3.1 JSON",
    },
]


# ---------------------------------------------------------------------------
# 典型流程（Agent 最需要的是这个，而不是一堆散装接口）
# ---------------------------------------------------------------------------
_FLOW_SYNTH_CURL = """# 1) 找一个音色，记下它的 ref_audio
curl -s http://127.0.0.1:7866/api/voices

# 2) 提交合成
curl -s -X POST http://127.0.0.1:7866/api/jobs \\
  -H 'Content-Type: application/json' \\
  -d '{"mode":"clone","text":"欢迎收听今天的节目。",
       "ref_audio":"<上一步拿到的 ref_audio>","voice_name":"于谦"}'
# -> {"job_id":"a1b2c3d4e5f6"}

# 3) 轮询到 done（首次会先加载模型，耐心等）
curl -s http://127.0.0.1:7866/api/jobs/a1b2c3d4e5f6

# 4) 下载
curl -s -o out.wav http://127.0.0.1:7866/media/out/<file_name>"""

_FLOW_SYNTH_PY = """import time, requests

B = "http://127.0.0.1:7866"
voice = requests.get(f"{B}/api/voices").json()["voices"][0]
ref_audio = voice["ref_audio"]

jid = requests.post(f"{B}/api/jobs", json={
    "mode": "clone", "text": "欢迎收听今天的节目。",
    "ref_audio": ref_audio, "voice_name": voice["name"],
}).json()["job_id"]

while True:
    job = requests.get(f"{B}/api/jobs/{jid}").json()
    if job["state"] in ("done", "error"):
        break
    time.sleep(0.6)                       # 界面用的也是 0.55s

assert job["state"] == "done", job.get("message")
wav = requests.get(B + job["audio_url"]).content   # /media/out/xxx.wav
open("out.wav", "wb").write(wav)"""

_FLOW_NEW_VOICE = """# 1) 上传参考音频（字段名固定是 file）
curl -s -X POST http://127.0.0.1:7866/api/upload -F 'file=@/path/to/ref.wav'
# -> {"file":"1a2b3c4d5e6f.wav","path":"C:\\\\...\\\\uploads\\\\1a2b3c4d5e6f.wav", ...}

# 2) 保存成音色（3–10 秒最合适，这里顺便裁一下）
curl -s -X POST http://127.0.0.1:7866/api/voices \\
  -H 'Content-Type: application/json' \\
  -d '{"name":"我的音色","path":"<上一步的 path>","trim":{"start":0.5,"end":6.5}}'

# 3) 确认它在列表里
curl -s http://127.0.0.1:7866/api/voices"""

_FLOW_REPLACE_VOICE = """# 已保存的音色，参考音频不合适了 —— 换一段，不必删掉重建。
# 1) 上传新音频（字段名固定是 file）
curl -s -X POST http://127.0.0.1:7866/api/upload -F 'file=@/path/to/new-ref.wav'

# 2) 就地替换。名字保持不变；ref_text 留空则用新音频重新转写。
#    路径里的音色名若含中文/空格，记得先做 URL 编码（curl 不会替你做）。
curl -s -X POST http://127.0.0.1:7866/api/voices/my-voice/audio \\
  -H 'Content-Type: application/json' \\
  -d '{"path":"<上一步的 path>","trim":{"start":0.5,"end":6.5}}'
# -> {"name":"my-voice","duration":6.0,"ref_text":"...","level":"ok", "voices":[...]}"""

_FLOW_DESIGN = """# 不需要任何参考音频：直接描述说话人（属性取值见 /api/bootstrap 的 categories）
curl -s -X POST http://127.0.0.1:7866/api/jobs \\
  -H 'Content-Type: application/json' \\
  -d '{"mode":"design","text":"欢迎收听今天的节目。",
       "groups":["female","young adult","standard"]}'"""

FLOWS: list[dict[str, Any]] = [
    {
        "title": "音色克隆合成（最常用）",
        "note": "四步。任务**异步**：提交只拿到 job_id，必须轮询到 done 才有音频。",
        "steps": [
            ("GET", "/api/voices", "拿音色列表，记下要用的 ref_audio"),
            ("POST", "/api/jobs", "提交合成，拿到 job_id"),
            ("GET", "/api/jobs/{job_id}", "轮询到 state=done（running/queued 就继续等）"),
            ("GET", "/media/out/{file_name}", "下载 wav"),
        ],
        "code": [{"lang": "bash", "text": _FLOW_SYNTH_CURL},
                 {"lang": "python", "text": _FLOW_SYNTH_PY}],
    },
    {
        "title": "新建一个音色",
        "note": "上传拿到 path，再保存。保存是同步的，返回时音频与参考文本都已落盘。",
        "steps": [
            ("POST", "/api/upload", "上传参考音频，拿到绝对路径 path"),
            ("POST", "/api/voices", "保存为音色（可带 trim 裁剪）"),
        ],
        "code": [{"lang": "bash", "text": _FLOW_NEW_VOICE}],
    },
    {
        "title": "给已有音色换参考音频",
        "note": "名字不变、就地覆盖。**不可撤销**：原音频与旧 prompt 缓存都会作废。",
        "steps": [
            ("POST", "/api/upload", "上传新的参考音频，拿到 path"),
            ("POST", "/api/voices/{name}/audio", "就地替换（可带 trim）；留空 ref_text 则重新转写"),
        ],
        "code": [{"lang": "bash", "text": _FLOW_REPLACE_VOICE}],
    },
    {
        "title": "语音设计（不需要参考音频）",
        "note": "mode=design，用 groups 描述说话人。groups 的取值来自 bootstrap.categories。",
        "steps": [
            ("GET", "/api/bootstrap", "读 categories，挑属性值"),
            ("POST", "/api/jobs", "mode=design + groups"),
        ],
        "code": [{"lang": "bash", "text": _FLOW_DESIGN}],
    },
]


# ---------------------------------------------------------------------------
# 生成 curl 示例 / 接入说明 / 与代码核对
# ---------------------------------------------------------------------------
def _curl(ep: dict) -> str:
    """给一条接口生成可直接粘贴的 curl。有 body/form 就带上示例值。"""
    url = "http://127.0.0.1:7866" + ep["path"]
    lines = [f"curl -s -X {ep['method']} {url}"]
    if ep.get("form"):
        for k, v in ep["form"].items():
            lines.append(f"  -F '{k}={v}'")
    elif ep.get("body") is not None:
        lines.append("  -H 'Content-Type: application/json'")
        lines.append("  -d '" + json.dumps(ep["body"], ensure_ascii=False, indent=2)
                     .replace("\n", "\n       ") + "'")
    return " \\\n".join(lines)


def _agent_brief(spec: dict) -> str:
    """给 Agent 软件的一段接入说明（Markdown）。**由清单生成**，不会和接口列表脱节。"""
    L: list[str] = []
    L.append("# OmniVoice Studio · 本地语音合成接口")
    L.append("")
    L.append(f"- 服务地址：`{spec['base']}`")
    L.append("- 鉴权：**无**。服务只在本机 127.0.0.1 监听，请勿把端口暴露到公网。")
    L.append("- 机器可读规格：`/openapi.json`；人工清单：`/api/docs`。")
    L.append("- 全部请求与响应都是 JSON（媒体接口返回音频流）。")
    L.append("")
    L.append("## 典型流程：文本 → 语音")
    L.append("")
    L.append("1. `GET /api/voices` 拿音色列表，取其中一个的 `ref_audio`（绝对路径）。")
    L.append("2. `POST /api/jobs` 提交 `{mode:\"clone\", text, ref_audio, voice_name}`，得到 `job_id`。")
    L.append("3. 每 0.5–1 秒 `GET /api/jobs/{job_id}` 轮询，直到 `state` 为 `done` 或 `error`。")
    L.append("4. 成功后 `GET {audio_url}` 下载 wav（形如 `/media/out/xxx.wav`）。")
    L.append("")
    L.append("注意：**首次合体会先加载模型（约 2.3 GB，可能一两分钟）**，第一次轮询慢属正常；"
             "之后模型常驻显存。任务只存在内存里，服务重启即失效。")
    L.append("")
    L.append("## 接口清单")
    L.append("")
    by_group: dict[str, list[dict]] = {}
    for ep in spec["endpoints"]:
        by_group.setdefault(ep["group"], []).append(ep)
    for g in spec["groups"]:
        eps = by_group.get(g["id"]) or []
        if not eps:
            continue
        L.append(f"### {g['label']}")
        L.append("")
        for ep in eps:
            L.append(f"- `{ep['method']} {ep['path']}` — {ep['title']}。{ep['desc']}")
        L.append("")
    L.append("## 约束")
    L.append("")
    L.append("- `ref_audio` / `path` 必须是本机绝对路径，且落在 `uploads/` 或 `voices/` 内"
             "（服务端有白名单校验，防止把任意本地文件读进音色库）。")
    L.append("- `language` 取值来自 `bootstrap.languages`（647 项，首项是 `Auto`）。")
    L.append("- 参考音频建议 3–10 秒：越长合成越慢（耗时随序列长度超线性增长）。")
    return "\n".join(L)


def build_spec(app: Any, base: str) -> dict:
    """组装文档 + 与应用实际登记的路由核对。"""
    live: set[tuple[str, str]] = set()
    for r in getattr(app, "routes", []):
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None)
        if not path or not methods:
            continue
        if not (path.startswith("/api/") or path.startswith("/media/")):
            continue
        for m in methods:                       # 只关心业务路由
            if m in ("HEAD", "OPTIONS"):
                continue
            live.add((m.upper(), path))

    # 只把 /api/ 与 /media/ 下的条目算进核对集合 —— 这样「代码里 N 个」和
    # 「文档登记 N 个」才是同一把尺子（`/openapi.json` 是 FastAPI 自带的，
    # 不在 app.routes 的业务扫描范围里，单独计入 listed）。
    documented = {(e["method"], e["path"]) for e in ENDPOINTS
                  if e["path"].startswith(("/api/", "/media/"))}
    spec: dict[str, Any] = {
        "base": base,
        "groups": GROUPS,
        "flows": FLOWS,
        "endpoints": [
            {
                "group": e["group"], "method": e["method"], "path": e["path"],
                "title": e["title"], "desc": e["desc"],
                # 元组转 dict，前端好读
                "params": [
                    {"in": p[0], "name": p[1], "type": p[2],
                     "required": p[3], "desc": p[4]}
                    for p in e.get("params", [])
                ],
                "returns": e.get("returns", ""),
                "note": e.get("note"),
                "raw": bool(e.get("raw")),
                "try": e["path"] in TRYABLE and e["method"] == "GET",
                "curl": _curl(e),
            }
            for e in ENDPOINTS
        ],
        # 代码里有、文档里没有（新加了接口忘了写文档）
        "undocumented": sorted(f"{m} {p}" for m, p in live - documented),
        # 文档里有、代码里没有（接口删了/改名了，文档没跟上）
        "ghost": sorted(f"{m} {p}" for m, p in documented - live),
        "counts": {
            "live": len(live),                      # app.routes 里实际登记的业务路由数
            "documented": len(documented),          # 文档里登记的（同口径）
            "matched": len(live & documented),      # 两边都对得上的
            "listed": len(ENDPOINTS),               # 页面实际列出的条目（含 /openapi.json）
            "groups": len(GROUPS),
        },
        "openapi": "/openapi.json",
    }
    spec["agent_brief"] = _agent_brief(spec)
    return spec
