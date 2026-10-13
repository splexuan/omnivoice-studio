# OmniVoice Studio

自用的**本地语音合成工具**，基于官方 [`k2-fsa/OmniVoice`](https://github.com/k2-fsa/OmniVoice) 最新源码（0.2.1）构建。

界面分四个标签页：**语音合成**（从音色库挑一个音色 → 推理）、**语音设计**（按文字描述造音色）、
**音色库**（上传 + 波形裁剪 + 转写 + 管理）、**合成记录**（历史与一键回填）。
支持 **语音克隆 / 600+ 语种 / 长文本合成 / 输出音量稳定**，全程离线运行。

播放器与音色库里的波形编辑器是同一套：波形 + 播放头 + 时间戳；合成结果上还能直接拖选一段剪辑并下载。

> 从零搭建、常见坑、打成"解压即用"整合包的完整步骤，见 **[操作教程.md](操作教程.md)**。

---

## 快速开始

**换新机器（首次）**——一键搭建，自动识别显卡并装对应版本的 torch：

```bash
setup.bat                        # 双击，或
python bootstrap.py              # 不想用 bat 就用它（任意 Python 3.8+ 都能跑）
```

依次完成：找 Python 3.12 → 建 `.venv` → 按驱动选 CUDA 版本装 torch（选不出就装 CPU 版）
→ 装 omnivoice 依赖 → 下权重 → 自检。**没有 N 卡、驱动太老、或想纯 CPU 跑，都能走通。**

**日常启动**（两套界面二选一，可同时开）：

```bash
run_web.bat                      # 自研界面（推荐），端口 7866  →  http://127.0.0.1:7866
run.bat                          # Gradio 界面，端口 7865  →  http://127.0.0.1:7865
```

自研界面即上面那三个标签页；Gradio 界面保留作为对照与兜底。
两个入口共用 `app/app.py` 里的生成核心，**不是两套实现**。

**排障三件套**：

```bash
.venv\Scripts\python.exe app\verify_env.py   # 环境自检 + 打印本机适配档位
.venv\Scripts\python.exe app\hardware.py     # 只看档位（设备/精度/步数是怎么定的）
.venv\Scripts\python.exe app\smoke_test.py   # 端到端冒烟：加载权重 → 生成 wav
```

---

## 目录结构

```
omnivoice-studio/
├── app/
│   ├── app.py          生成核心 + 音色库逻辑（两套界面共用，不重复实现）
│   ├── asr.py          参考音频转写（SenseVoice Small，sherpa-onnx int8）
│   ├── hardware.py     硬件探测与自动适配档位（设备/精度/步数/分段）
│   ├── config.py       路径与运行参数（默认全 auto，可强制覆盖）
│   ├── smoke_test.py   端到端冒烟测试（加载权重→生成 wav）
│   ├── clone_test.py   语音克隆验证
│   └── verify_env.py   环境自检 + 打印本机档位
├── web/                自研 WebUI（run_web.bat，端口 7866）
│   ├── server.py       FastAPI 后端（只调 app/app.py 的模块级函数）
│   ├── static/
│   │   └── index.html  单文件前端（语音合成 / 语音设计 / 音色库 / 合成记录）
│   └── _uploads/       界面上的临时上传（不入库）
├── bootstrap.py        一键环境引导（自动识别显卡选 torch）
├── setup.bat           bootstrap 的 Windows 入口
├── packaging/          打成"解压即用"整合包的脚本与说明
├── wheels/
│   ├── _fetch_torch.py 按驱动/平台自动选型并并发下载 torch
│   └── _link_torch.py  复用本机已有 torch 的链接脚本（省下载）
├── checkpoints/        模型权重（体积大，不入库）
├── OmniVoice/          官方源码（setup.bat 会自动 clone）
├── voices/             音色库（个人数据，不入库）
├── outputs/            合成结果（不入库）
│   └── history.json    合成记录（新功能，随 outputs 一起清理即可）
├── design-review/      界面截图与验证脚本（开发用）
├── run_web.bat         一键启动自研界面（7866）
├── run.bat             一键启动 Gradio 界面（7865）
├── 操作教程.md          完整操作教程
└── README.md
```

---

## 环境自适应

所有运行参数默认 `auto`，由 `app/hardware.py` 按本机情况决定，**一般不需要改配置**：

| 维度 | 自动决策 |
|------|---------|
| 设备 | `cuda` > `xpu`（Intel）> `mps`（Apple）> `cpu` |
| 精度 | Ampere 及更新（算力 ≥ 8.0）→ `bfloat16`；Pascal/Turing/新 Xe → `float16`；Maxwell 等老卡、MPS、CPU → `float32` |
| 参考音频转写 | 固定用 SenseVoice Small（纯 CPU，0.24 GB）。与显卡无关，不参与显存分档 |
| 推理步数 | ≥ 6 GiB → 32，≥ 4 GiB → 24，更小 / CPU → 16 |
| 长文本分段 | ≥ 8 GiB → 15s，≥ 4 GiB → 10s，更小 → 6s，CPU → 8s（越短峰值显存越低） |

另有四层兜底：

1. **显卡加载失败**（显存不足 / 精度不支持 / 驱动不匹配）→ 自动回退 CPU，界面顶部给出提示。
2. **生成时显存不足** → 自动降级重试：步数减半 → 分段时长减半，降到 steps=8 / 分段 3s 仍失败才报错。
3. **装 torch 时**：按驱动支持的 CUDA 版本挑（优先上游验证过的 cu128）→ 该组合没轮子就换别的 tag → 全拿不到才装 CPU 版。
4. **参考音频转写拿不到文本时**（转写被关掉 / 没人声 / 语种不在此模型覆盖范围）→ 明确报错要求手工填写，
   不再静默换引擎 —— **Whisper 已于本次移除**，详见下节。

想强制指定，改 `app/config.py`：

```python
DEVICE = "auto"              # 或 "cpu" / "cuda" / "cuda:1" / "xpu" / "mps"
DTYPE = "auto"               # 或 "float16" / "bfloat16" / "float32"
ASR_ENABLED = True           # 参考音频自动转写（SenseVoice Small），关掉需手工填参考文本
ASR_THREADS = 0              # 转写线程数：0=自动（按 CPU 核数取 2~4）
NUM_STEP = 0                 # 0=自动，或直接写 16/24/32
AUDIO_CHUNK_DURATION = 0.0   # 0=自动（秒）
```

也可用环境变量临时覆盖：`OMNIVOICE_ASR=0`（等同 `ASR_ENABLED=False`）、
`OMNIVOICE_SENSEVOICE_DIR=<模型目录>`。

---

## 模型权重

`app/config.py` 按以下顺序查找权重目录，命中即用：

1. 本目录下的 `checkpoints/`（整合包布局）
2. 环境变量 `OMNIVOICE_CHECKPOINTS`
3. 开发机上的原整合包路径
4. 都找不到 → 回退 HuggingFace 仓库 id 联网下载

| 用途 | 仓库 id |
|------|---------|
| 主模型 | `k2-fsa/OmniVoice` |
| 参考音频转写 | `csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17`（只取 int8 权重 + tokens，约 0.24 GB） |

> **Whisper 已移除。** 0.2.x 早期版本用 `openai/whisper-large-v3-turbo`（1.6 GB）作转写与兜底，
> 现已整体删除：权重与本机 CPU 上约 20 秒的转写耗时都不划算，而 SenseVoice 转出的字与它
> **完全一致**、还自带中文标点、快约 40 倍。移除后项目少占 1.6 GB 磁盘与 1.6 GB 常驻内存。
> 相应地，`ref_text` 为空时不再交给内置 ASR 处理 —— 拿不到文本会直接报错提示手填，
> 避免模型按 `asr_model_name` 偷偷联网重新下载这份权重。

---

## 与原整合包的关系

| 维度 | 原整合包 | 本工具 |
|------|----------|--------|
| 代码版本 | OmniVoice 0.1.2（编译为 `.pyd`，不可改） | **官方 0.2.1 源码**，可自由修改 |
| 长文本 / 音量优化 | 有 | 官方已内置，且更完善（还多了文本归一化） |
| 运行环境 | 便携 Python + torch **cu130** | 独立 venv + torch **cu128**（匹配本机驱动，GPU 可用） |
| 依赖关系 | 独立 | 复用其依赖，但**代码互不影响** |

> 原整合包里的 torch 是 `2.11.0+cu130`（CUDA 13.0），而本机驱动只到 CUDA 12.7，
> 所以它的 GPU 实际用不起来、一直跑 CPU。本工具换成 `cu128` 后才能真正调用显卡。

---

## 常见问题（详见操作教程）

- **有 N 卡却用不上 GPU**：跑 `app\verify_env.py`，看「驱动」与「torch」两行。torch 的 CUDA 版本
  必须 ≤ 驱动支持的版本（`nvidia-smi` 右上角那个数字）。不匹配时重跑 `setup.bat` 会自动选对。
- **显存不足**：程序会先自动降级（降步数、缩短分段），仍失败才报错。还可手工调低
  *Inference Steps* /「长文本分段」，把参考音频缩到 3–5 秒，或把 `config.DEVICE` 改成 `"cpu"`。
- **纯 CPU 也能用**：没 N 卡时 `setup.bat` 会自动装 CPU 版 torch，只是慢得多（降步数、短文本可缓解）。
- **合成慢**：先查参考音频长度 —— 这是第一变量，单步前向耗时随它**超线性**增长。
  到「音色库」把音频裁到 3–10 秒（页面有长度提示，超 10 秒标黄），实测可提速数倍到数十倍。
- **首次生成较慢**：需加载约 2.3GB 主模型；转写用 0.24GB 的 SenseVoice，
  两者之后常驻内存。同一音色的第二次生成会直接复用缓存的音色 prompt，省掉转写与参考音频编码。
- **不装转写也能用**：`--no-asr` 会跳过 SenseVoice 权重（省 0.24 GB），代价是参考音频必须手工填文本。
- **长文本**：自动分段串行合成 + 交叉淡入淡出，段数越多越慢，属正常。
- **合成记录在哪、会占空间吗**：写在 `outputs/history.json`，只存参数不存音频副本，最多留最近 300 条。
  「清空记录」只删记录，`outputs/` 里的 wav 保留 —— 那是产物，要清得自己清。
- **「附加指令」能自己随便填吗**：**能**，那是自由文本，直接透传模型，可用值与「语音设计」的属性下拉同一份
  （性别 / 年龄 / 音调 / 耳语 / 英文口音 / 中文方言）。规则有三条：多项用逗号分隔；
  **中文方言与英文口音不能混用**（方言只对中文生效、口音只对英文生效）；同一类别只能写一项。
  用词写错时模型会回提示最接近的正确写法，照改即可。

---

## 许可与致谢

- 模型与代码：**k2-fsa/OmniVoice**（Xiaomi Corp.，Apache-2.0）
- ASR：**SenseVoice Small**（sherpa-onnx 权重，Apache-2.0）
- 本仓库仅做运行时封装与界面集成。
