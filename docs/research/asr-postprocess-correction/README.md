# 本地语音输入法 ASR 后处理纠错调研

- 日期：2026-09-22
- 环境：Apple M2 / 8 GB 内存 / 纯 CPU / Rust；ASR 为 Qwen3-ASR-0.6B 与 SenseVoice-Small
- 目标：数字转阿拉伯数字、内部术语与人名纠错、句尾标点风格、内部词典，且不引入会拉低 ASR 准确率的通用后处理模型
- 附带产物：`poc/itn-rustfst/`，本机已跑通的纯 Rust ITN 验证程序（见附录）
- 归档说明：本调研属于内部语音输入法项目，与千里眼主线无关，按 issue #42 归档在本仓库 `docs/research/` 下

---

## 0. 一页结论

| 问题 | 归属层 | 结论 |
|---|---|---|
| 中英文数字 → 阿拉伯数字 | 规则 ITN（FST） | **已在本机 Rust 跑通**：`rustfst` 直接加载 WeTextProcessing 预编译 FST，零 Python、零 C++、零模型。zh+en 四个 FST 常驻 29 MB，26 字句 3.5 ms |
| 内部术语 / 同事名 / 产品名 | ASR 层 context 注入（仅 Qwen3-ASR，官方定性实验性）+ **拼音词典纠错（主力，确定性）** | 不需要神经纠错模型；SenseVoice 在模型层完全不能做热词 |
| 句尾句号 → 逗号 / 空格 | 纯字符串规则 | 两个 ASR 都无法在模型层控制标点风格；FunASR 的 ct-punc 句尾句号是源码硬编码 |
| 通用错字纠错小模型 | 可选、默认关闭 | 通用模型误纠率有实测：1.5B LLM 18%、4B 23%；BERT 类 0.46%–0.76%。只在词典冻结 + 拼音相似约束下使用 |

推荐流水线（每步确定性、可单独开关、可回归测试）：

```
ASR 原始文本
  → ① 清理：剥 SenseVoice 富标签、去多余空格
  → ② 术语词典纠错：拼音/音素模糊匹配 + 黑名单窗口（内部词典）
  → ③ ITN：中英分段 → FST tagger → 重排 → verbalizer；date/time 在 Rust 侧自定义渲染
  → ④ 标点风格：句尾「。」→ 逗号 / 空格 / 空；保留「？」
  → 上屏
  → ⑤ 用户修改回流：diff(ASR, 最终文本) → SQLite → 计数达阈值后升级进词典
```

② 放在 ③ 之前，因为词典匹配靠拼音，文本还是纯汉字时最好匹配，且词典命中可以保护「十三五」「三方」这类不该被 ITN 改动的词。ITN 对已含阿拉伯数字的文本是幂等的（实测），所以顺序安全。

顺带一提：这次需求描述末尾那句「你这里的千问，其实你并没用 Qwen」本身就是一条现成测试用例——「Qwen」被写成了「千问」，正是拼音词典层要解决的问题。

---

## 1. 背景与约束

- **内存**：Qwen3-ASR-0.6B 的 `model.safetensors` 实为 1.88 GB（bf16），真实参数量约 0.94B（0.6B LLM + 音频编码器），这解释了观察到的约 3 GB 常驻。8 GB 机器上任何新增模块都要以 MB 计。
- **隐私**：全部本地，不能引入云端服务，也就不能用 Qwen3-ASR-Flash API 的「上下文增强」。
- **对通用后处理模型的担心是对的**：见第 5 节的误纠率数据。设计原则是「只在确定性证据（词典命中、拼音高相似、数字模式）处动手，其余一律不改」。
- **仓库变动**：SenseVoice 仓库已迁到 `QwenAudio/SenseVoice`，旧地址 302 跳转。

---

## 2. 数字 ITN（逆文本正则化）

### 2.1 推荐路线：rustfst + WeTextProcessing 预编译 FST（已验证）

- **FST 来源**：`pip download wetext==0.1.8` 得到的 wheel（5 MB）里有 zh / en / ja 的 tn 与 itn 全部 FST，直接抠出 `wetext/fsts/{zh,en}/itn/{tagger,verbalizer}.fst` 随程序分发，运行时不需要 Python。GitHub Release 1.0.4 的 FST 是 2024 年的旧版且没有英文，不要用。
- **格式**：OpenFst 二进制，`fst_type=vector, arc_type=standard, version=2`，正是 `rustfst 1.3.1`（MIT/Apache-2.0，纯 Rust）文档明确支持的类型：「`VectorFst<TropicalWeight>` corresponds directly to the OpenFST `StdVectorFst`, and can be used to load its files」。
- **三段流程里有一段不是 FST**：tagger → **TokenParser 重排** → verbalizer。中间的重排是宿主语言手写的（C++ 201 行 / Python 190 行），按 `ITN_ORDERS` 把字段排成 verbalizer 要求的顺序。跳过它，money / fraction / time / date 会输出空串。PoC 里已移植（约 60 行 Rust）。
- **本机实测**（M2，release 构建，见附录完整输出）：

| 项目 | 结果 |
|---|---|
| 加载 zh+en 四个 FST | 22 ms |
| 常驻内存（zh+en） | 29 MB；仅 zh 时 12.5 MB |
| 26 字中文句端到端 | 3.5 ms |
| 一点三 | 1.3 |
| 同比增长百分之六点三 | 同比增长6.3% |
| 可以打我手机幺三五零幺二三四五六七 | 可以打我手机13501234567 |
| 重达二十五千克 | 重达25kg |
| 我要一个苹果一下就好 | 原样（白名单保护「一个」「一下」） |
| 我有1.3个和一点三个 | 我有1.3个和1.3个（幂等） |
| one point three | 1.3 |
| it costs twelve dollars fifty | it costs $12.50 |

### 2.2 其他候选与否定理由

| 方案 | 结论 |
|---|---|
| sherpa-onnx `rule_fsts` + 官方 `itn_zh_number.fst`（25 KB） | **能力不够**。实测「一点三」→「1点3」，「百分之六点三」不转 %，「我要一个苹果」→「我要1个苹果」（正好踩中量词陷阱）。只是逐字数字映射器 |
| WeTextProcessing C++ runtime + C API（FFI） | 可行但要编译 OpenFst fork，没必要，rustfst 已够 |
| NeMo-text-processing 中文 ITN | 缺 measure / telephone / electronic，依赖 pynini 锁版本，非 Python 路径要走 Sparrowhawk（无 Rust 绑定） |
| 纯规则 Rust crate：`text-processing-rs`（NeMo 规则移植，无 FST，279 KB） | 更轻的备选，但 crates.io 版本滞后且有已知 bug，顶层接口无上下文贪婪扫描（「一下」可能误转，未实跑） |
| 中文数字 crate（`chinese-number`、`zh_num`、`chinese2digits`） | 只做「数字串 → 数值」，不做句中定位、量词保护、单位、日期；彼此对「一百二」的解析还不一致（120 vs 102） |
| 英文 `text2num` 2.8.0（Rust 原生，MIT） | 可作英文补充；缺 trillion、负数、年份读法 |
| `wetext-rs` 0.1.2（WeText 的 Rust 移植，用 rustfst） | 只有 zh + ja，2025-12 后停滞；但它的 `token_parser.rs` 值得参考 |

### 2.3 已知陷阱（★ 为本机复现）

1. ★ **量词白名单**：「一个 / 一下 / 一些 / 一共 / 一起 / 星期一 / 十三五 / 十几万」等 82 条在 `data/default/whitelist.tsv` 里恒等保护。要加自己的词必须装 pynini 重编译 FST，这一步只在构建机做一次。
2. ★ **「十一点」歧义**：实测 → 「11点」（不是 11:00），「十一点五」→「11.5」，「上午八点半」→「8:30a.m.」。上游 issue #180 至今 open，靠上下文启发式，无法彻底解决。建议输入法场景在 Rust 侧对 `time` token 自定义渲染（如「8:30」或「8点半」）。
3. ★ **日期格式**：verbalizer 硬编码 `/`，「二零二六年九月」→「2026/09」；但走 measure 的「两千零二十六年」→「2026年」，两者不一致。**不要为此重编译 FST**：tagger 已给出 `date { year: "2026" month: "09" }` 结构化字段，在 Rust 的重排阶段直接渲染成「2026年9月」即可。
4. ★ **序列歧义**：「三十万三十万」→「303000 100000」（issue #237 未修），「十一比七十一比六」→「11:71:6」（issue #282，维护者确认白名单和权重都无效）。这是规则 ITN 的根本性缺陷，靠回归集兜底。
5. ★ **中英混说**：中文 FST 对英文安全透传（「hello world 一点三」→「hello world 1.3」），但不会转英文数字。做法是按中英字符边界切段，各段用各自的 FST，再拼回。
6. ★ **幂等性成立**，但上游测试集没有任何带阿拉伯数字的用例，需要自建回归。
7. **编译期烘焙的选项**：`enable_0_to_9`、`remove_interjections`（默认删「呃 / 啊」）等不是运行时参数，wetext 分发了 `tagger.fst` 与 `tagger_enable_0_to_9.fst` 两个变体。语气词删除对输入法可能是想要的，也可能不是，注意它关不掉。
8. `compose()` 有 6 个类型参数必须显式标注；不要走 `fstprint` 文本中转（rustfst 文本解析只认数字标签且静默截断）。

---

## 3. 内部术语 / 人名 / 产品名纠错

### 3.1 ASR 层能做到什么

**Qwen3-ASR（开源 0.6B / 1.7B）：支持 context 注入，官方定性实验性。**

- 接口：`qwen-asr` 包 `model.transcribe(audio, context="...")`，源码把 context 原样放进 system 角色；transformers 路径（`-hf` 模型卡「Context / hotwords」小节）用 `prompt="Vocabulary: Quilter, apostle, gospel."`。GitHub README 和技术报告对此零提及，只在模型卡与源码里。
- 官方态度：维护者在 issue #13 回复「experimental… cannot guarantee a very stable effect… technical report does not report any relevant indicators」。
- **有 issue 实证的失败模式**：热词原样泄漏进转写（#106）、流式复读整个热词表（#140）、音近或声音弱时被改成热词表里的词（#186，open）。社区缓解是给词表加前缀「Technical terms:」，提问者复测「有点效果，偶尔还是出现」。
- 过度偏置有量化数据：中文 LLM-ASR 热词论文显示每句注入 top-2 最优（KER 8.29%），注入 top-10 退化到 15.21%（arXiv 2512.21828）。
- sherpa-onnx 的 `--qwen3-asr-hotwords` 是同一机制（源码注释：放进 system 段），默认 `max_total_len=512`，热词直接吃 prompt 预算。llama.cpp 的 `/v1/audio/transcriptions` 端点把 prompt 放进 user 消息而 Qwen3-ASR 模板只从 user 取音频，所以从该端点注入大概率无效（源码推断），要走 chat completions + system 消息。
- 用法建议：**每句只注入 top-2 到 top-5 个候选**（按上一句上下文或拼音召回），加前缀，热词表短；后处理层仍需兜底。这是 L0，不是主力。

**SenseVoice-Small：模型层不支持热词，官方无计划。**

- 维护者 LauraGPT 在 SenseVoice issue #3 回复「We have no plans. You could finetune the model.」源码 `sense_voice/model.py` 全文零次 `hotword`，传了会被静默忽略。
- sherpa-onnx 官方文档原文：「Only transducer models support hotwords… All other models don't support hotwords」，且必须 `modified_beam_search`。SenseVoice 是 CTC + greedy。
- 官方给的两个替代都是识别后的文本级替换，本质与本文第 3.2 节相同：
  - FunASR ≥ v1.3.15 的 `postprocess_hotwords`（2026-07 新增）：pypinyin 转拼音 + rapidfuzz 相似度，阈值默认 0.85，滑窗长度为目标词 ±1。是很好的参考实现，但它作用在还带 `<|zh|><|NEUTRAL|>` 标签的文本上，且每个窗口现算拼音，术语表大时慢。
  - sherpa-onnx Homophone Replacer：jieba 分词 → 拼音 → `replace.fst` 整串全匹配替换。规则文件不支持动态修改，改词表要 pynini 重生成。
- 若一定要声学级热词，只能换 `paraformer-zh`（即 SeACo-Paraformer），代价是官方同一基准上中文 CER 10.18 vs SenseVoice 7.81。

### 3.2 L1：拼音 / 音素词典纠错（主力，确定性）

**照抄的蓝本：HaujetZhao/asr-hotword**（MIT，Python，2026-06 活跃），设计几乎完全对应本需求：

- 音素单元：中文用声母 / 韵母 / 声调三元组，英文按字母；天然支持「Qwen ↔ 千问」这类中英同音匹配。
- 模糊音簇：前后鼻音（in/ing、en/eng、an/ang）、平翘舌（z/zh、c/ch、s/sh）、n/l、f/h、r/l。
- 两层检索：粗筛（快速召回）+ 精算（加权编辑距离）。
- **双阈值**：≥ 0.85 才替换；0.65–0.85 只记录不替换（供后续分析和自学习）。
- **黑名单窗口**：词典语法 `目标词|别名1|别名2 ~~~ 黑名单1|黑名单2`，黑名单词出现在替换点 5 个语义单元内则不替换。README 的例子正是防止「扶贫办工作人员」被改成「傅平办工作人员」。
- 性能：5000 条热词处理一句 20 ms（Python）。Rust 实现预期 < 5 ms。

**Rust 组件**（crates.io 数据，2026-09-22）：

| crate | 版本 | 用途 | 备注 |
|---|---|---|---|
| `jieba-rs` 0.11 | 成熟（390 万下载） | 分词、多音字消歧 | |
| `pinyin` 0.11 | 成熟（97 万下载） | 字 → 拼音，`ToPinyinMulti` 多音字 | 按字不按词，配 jieba |
| `ib-matcher` 0.4.5 | 79★ MIT | 拼音子串匹配 + 多音字 + `mix_lang` 中英混合，性能接近 regex | 不做模糊音、不做编辑距离，只适合召回层 |
| `inputx-phonetic-edit` 1.4.0 | 5★ MIT/Apache | 加权音素编辑距离，自带普通话模糊音代价表 | 需求完全吻合但成熟度低，需自测 |
| `aho-corasick` 1.1 | 成熟 | 精确别名多模式匹配 | |
| `strsim` 0.11 | 成熟 | 通用编辑距离 | |
| `rphonetic` 4.0 | 成熟 | double metaphone，英文专名音似 | |
| `symspell` 0.5 | 可选 | 拼写候选生成 | |

Rust 侧没有现成的中文热词纠错 crate，需要自行组合：`ib-matcher` 召回 → `inputx-phonetic-edit` 精算 → 自写双阈值 + 黑名单窗口。

**抑制误替换的清单**（全部是确定性开关）：

- 最小 2 个音节，单字词不参与匹配。
- 双阈值（0.85 替换 / 0.65 仅标记），声调作弱特征不作硬约束。
- 黑名单 + 上下文窗口；词典条目可标「只在该词左右出现 X 时替换」。
- 每句只激活 top-k 候选而非全词典（与 L0 一致）。
- 中英分开算音素；英文专名用 metaphone 通道。
- 低置信度门控：SenseVoice ONNX 输出 `ctc_logits`，softmax 后逐 token 后验可作门控信号，优先在低置信区触发；Qwen3-ASR 若运行时暴露 token logprob 同理。
- 词典命中的片段整体冻结，后续 ITN 与可选模型不得再改。

### 3.3 内部数据库

建议 SQLite（Rust 用 `rusqlite`），三张表：

- `terms`：`term, aliases, pinyin_key, category(人名/产品/术语), weight, source(manual/learned), created_at`。启动时全量载入内存索引，词典改动即时生效（这一点优于 sherpa-onnx 的静态 FST）。
- `corrections`：`asr_raw, user_final, pinyin_sim, edit_distance, count, first_seen, last_seen, promoted`。
- `blacklist`：`term_id, context_word`。

**从用户修改中学习**：输入法领域没有现成的 ASR 版实现（VocoType、MyVoiceTyping 等都只有静态词表），但 RIME / libpinyin 的用户词典模式可以照搬：

- 上屏后若用户在短时间内编辑了该段文本，做 diff，只收「拼音相似且编辑距离小」的替换对（过滤掉用户的改写润色）。
- `count ≥ 3` 才升级进 `terms`，带时间衰减和条数上限。
- **自学习词典与手工词典物理分离、UI 可见可撤销、可一键清空**。RIME 社区 issue 证实「词典污染」是真实痛点，这三条是缓解手段。

---

## 4. 标点风格

- **Qwen3-ASR**：自带标点，无开关，句尾是句号（sherpa-onnx 文档贴的 0.6B int8 实际输出可见）。用 prompt 控制标点风格未找到官方说明。
- **SenseVoice**：`use_itn` 一个开关同时管 ITN 和标点，是拼在编码器输入前的控制 token（`withitn=14` / `woitn=15`）。`use_itn=False` 时完全没有标点；`use_itn=True` 时有标点且中文数字已转阿拉伯数字（所以走 SenseVoice 时 ITN 由模型自带，本文第 2 节的 FST 作为兜底叠加，幂等安全）。维护者在 issue #140 承认两者无法分离。FunASR 里 `use_itn` 默认 `False`，别忘了传。**由此推论：走 SenseVoice 时不要用「`use_itn=False` + 自研 ITN」的组合**，那样会连标点一起丢掉，只能再外挂约 274 MiB 的 ct-punc-c 补回来；正确组合是 `use_itn=True` 拿模型自带的标点与数字，再叠加本文第 2 节的 FST ITN 作幂等兜底。`rich_transcription_postprocess` 会把情感 / 事件标签换成 emoji，输入法不要用它，自己 `re.sub(r'<\|[^|]+\|>', '', text)` 剥标签即可。
- **外挂 ct-punc**：标点集合只有「，。？、」（原论文原文：comma, period, question mark, enumeration comma），`inference()` 末尾无条件把末尾逗号改句号、无标点则补句号，源码硬编码，无开关。想改风格可用 `generate()` 返回的 `punc_array`（逐 token 标点类别）或 `return_raw_text=True` 自己重建文本。
  - 两个离线模型：`ct-punc-c`（zh-cn，精确参数 73.0M，`model.pt` 278.5 MiB）与 `ct-punc`（cn-en，281.4M，1073 MiB）。FunASR README 写的「290M」与两者都对不上，不要引用。官方自采集测试集上大模型 F1 只高 2.3 点（58.8 vs 56.5），官方 HTTP server 默认用的就是 `ct-punc-c`。
  - **ModelScope 模型卡原文：`ct-punc` 「现阶段只能在 Linux-x86_64 运行，不支持 Mac 和 Windows」，`ct-punc-c` 「支持 Linux-x86_64、Mac 和 Windows」**。在 Darwin 上只考虑 `ct-punc-c`；它在 HuggingFace 上不存在，必须走 ModelScope（`hub="ms"`）。
  - **官方 int8 ONNX 几乎不减体积**：`ct-punc-c` int8 269.7 MiB（仅 −3.2%），因为 95.65% 的体积是 272727×256 的 embedding 表，而 ONNX Runtime 动态量化只量化 MatMul 不量化 Gather。想压到 100 MB 以内必须自己裁词表并单独量化 embedding，官方无此路径。此前有「sherpa-onnx 72 MB int8」的说法，与官方 ONNX 仓库字节数冲突，按未验证处理。
  - CPU 速度：官方无任何 punc 基准（模型卡里的 `cpu: 1, memory: 4096` 是在线体验配额，不是性能指标）。
  - 本需求不需要它：Qwen3-ASR 自带标点；SenseVoice 走 `use_itn=True` 也自带标点。
- **结论**：句尾风格只能自己做，且是最便宜的一步。规则：去掉句尾「。」「.」「！」，按用户设置替换为逗号 / 空格 / 空；保留「？」；句中标点不动；中英混排时统一全角半角。

---

## 5. 可选的小纠错模型（默认关闭）

### 5.1 为什么不做主力：误纠率数据

macro-correct 项目在 15 个数据集横评 13 个模型，其中「人民日报 4636 句 + 学习强国 5000 句」是完全无错语料，专测过度纠错（thr=0.75）：

| 模型 | 正确句被改坏的比例 |
|---|---|
| Macropodus/macbert4mdcspell_v2 | 0.46% |
| shibing624/macbert4csc | 0.76%（但跨域 F1 仅 45.8，基本不干活） |
| relm_v1 | 6.5% |
| shibing624/chinese-text-correction-1.5b | 18% |
| twnlp/ChineseErrorCorrector3-4B | 23% |

作者原话：「大模型过拟合更加严重，特别容易把正确的句子修改润色」。学术侧交叉印证：BERT baseline 在 SIGHAN13/14/15 句级误报率 37.9% / 17.0% / 15.1%；另一篇论文明确指出「训练数据或词表中罕见的词，如技术术语和人名，常被误判为拼写错误」——通用模型会主动破坏本需求要保护的东西。

### 5.2 小 LLM 后编辑：证据是负面的

- 首个中文 ASR 纠错基准 ASR-EC（arXiv 2412.03075）摘要原文：「prompting is not effective for ASR error correction」，只有音频 + 文本多模态才到 SOTA。纯文本 1-best 后编辑正是被判定无效的那一档。
- arXiv 2405.15216：低错误率场景 LLM 会过度修正、产生幻觉；参数量仅其 1/15 的专用小模型反而更准。
- GER / HyPoradise 的收益建立在 N-best 候选列表上，1-best 时候选不足。
- 模型选择上，Gemma 3 270M 与 SmolLM2 官方未声称支持中文；唯一稳妥的 Qwen2.5-0.5B-Instruct Q4 已 398 MB，加 KV cache 后在 8 GB 机器上很紧。llama.cpp CPU 速度无公开实测，需用 `llama-bench` 自测。

### 5.3 如果一定要上

选 `Macropodus/macbert4mdcspell_v2`（横评中同时最高平均 F1 71.23 与最低误纠 0.46%），约束：

- ONNX 导出 + 动态 int8（fp32 390 MB，int8 约 110 MB，推算），Rust 用 `ort` + `tokenizers`。
- 阈值 ≤ 0.5（作者原话：超过 0.5 后对效果影响较大）。
- 只接受拼音相似的替换（用 L1 的音素距离二次校验，非音似一律拒绝）。
- 词典命中片段冻结；改动超过 N 个字符的输出整句丢弃。
- 先在「全正确句子集」上测 acc_true，净收益为负就不上线。

---

## 6. 运行时与内存预算

| 组件 | 选项 | 体积 / 内存 | 速度（官方数据） | 热词 |
|---|---|---|---|---|
| Qwen3-ASR-0.6B | 现状（transformers bf16） | 1.88 GB 权重，约 3 GB RSS | — | `context=` |
| Qwen3-ASR-0.6B | **sherpa-onnx int8**（官方 Rust crate `sherpa-onnx` 1.13.8） | 约 937 MB | macOS 2 线程 RTF 0.11–0.15 | `qwen3_asr.hotwords` |
| Qwen3-ASR-0.6B | llama.cpp GGUF Q8_0（`ggml-org/Qwen3-ASR-0.6B-GGUF`，2026-05 合并） | 805 MB + mmproj 214 MB | — | 需走 chat completions；输出带 `language English<asr_text>` 前缀要剥 |
| Qwen3-ASR-0.6B | MLX 4bit | 708 MB | — | 未验证 |
| SenseVoice-Small | sherpa-onnx int8 | 228 MB | RTF 0.05–0.11 | 无 |
| SenseVoice-Small | llama.cpp GGUF q8（官方 runtime，内置 C++ VAD） | 242 MB | 8 线程约 20× 实时，CER 7.81/8.17 | 无；默认 `woitn` 要改 `withitn=14` |
| ITN（本文第 2 节） | rustfst + WeText FST | 29 MB | 3.5 ms/句 | — |
| 术语词典（第 3.2 节） | 自研 | < 20 MB（推算） | < 5 ms/句（推算） | — |
| 可选 CSC（第 5.3 节） | macbert4mdcspell_v2 int8 | 约 110 MB（推算） | 无公开实测 | — |

两条落地路线，都能把总内存压到 1.3 GB 以内：

- **保留 Qwen3-ASR**：换 sherpa-onnx int8（约 937 MB，官方 Rust crate），用热词字段注入少量高价值术语，Rust 侧补 ITN + 词典 + 标点规则。
- **追求最小最快**：SenseVoice int8（228 MB）+ `use_itn=True`（顺带解决数字和标点），术语纠正全靠 Rust 侧词典。代价是没有任何 ASR 层的语义偏置。

**GPU 迁移**：以上都不需要改后处理代码。sherpa-onnx 有 CUDA 构建，llama.cpp 有 Metal / CUDA，`ort` 有 CUDA / CoreML EP；ITN 与词典层是纯 CPU 代码，本来就与设备无关。

---

## 7. 评估方法

必须双轨，缺一不可：

1. **纠正率**：内部术语测试集上的 NE-WER / NE-FNR（命名实体错误率 / 漏检率）下降。
2. **误纠率**：在完全正确的句子集上测 acc_true，每次改动词典或阈值都跑。

资源：

- ContextASR-Bench（arXiv 2507.05727，CC0）：4 万条音频 / 30 万命名实体 / 10+ 领域，指标正是 NE-WER 与 NE-FNR，与本需求最匹配。
- 自建内部术语集：把术语嵌入自然句 → 多音色 / 多语速 / 加噪 TTS 合成 → 跑 ASR → 固化为回归集（ContextASR-Bench 就是这么构造的）。
- 自建 ITN 回归集：覆盖第 2.3 节全部陷阱，加带阿拉伯数字输入的幂等用例。
- Rust 侧无成熟 WER/CER crate（`rwer` 0.2.2 下载量仅 210），用 `strsim` 自实现字符级对齐，口径对齐 Python `jiwer`。

---

## 8. 建议的实施顺序

| 里程碑 | 内容 | 预期 |
|---|---|---|
| M1 | 标点规则 + 把 `poc/itn-rustfst` 接进主程序：中英分段、date/time 在 Rust 侧渲染、ITN 回归集 | 1–2 天，数字与句号问题解决 |
| M2 | 术语词典层：SQLite + 拼音索引 + 双阈值 + 黑名单窗口；内部术语测试集与全正确句子集 | 3–5 天，人名 / 产品名问题解决大半 |
| M3 | Qwen3-ASR 迁 sherpa-onnx int8 降内存；A/B 测 top-k context 注入是否有净收益 | 视 A/B 结果决定是否保留 L0 |
| M4 | 用户修改回流的自学习（与手工词典分离、可清空） | |
| M5（可选） | macbert4mdcspell_v2 int8 门控接入，先测误纠率 | 净收益为负则不上 |

---

## 9. 未验证 / 推测项

- Qwen3-ASR context 注入的实际增益：官方未公布任何指标。
- llama.cpp transcriptions 端点丢弃 prompt 文本：源码推断，未实机验证。
- SenseVoice 输出标点集合与句尾行为：官方无说明，未实测。
- macbert4mdcspell_v2 int8 后的体积与 CPU 延迟：无公开数据。
- 小 LLM 在 llama.cpp CPU 上的 tok/s：无公开数据。
- `inputx-phonetic-edit` 的实际质量：5★，需自测。
- `text-processing-rs` 顶层接口对「一下 / 一起」的误转：从源码推导，未实跑。
- rustfst 与 OpenFst 1.8.x 的官方互操作：其 CI 只测 1.7.2，但本机实测 1.8.x 产物可读。
- sherpa-onnx 的 `itn_zh_number.fst` 是否由 WeTextProcessing 编译：仓库内零命中。

---

## 10. 来源

ITN：
- https://github.com/wenet-e2e/WeTextProcessing （Apache-2.0，2026-07 活跃；`itn/chinese/data/default/whitelist.tsv`、`runtime/processor/wetext_token_parser.cc`）
- https://github.com/pengzhendong/wetext （PyPI `wetext` 0.1.8，依赖 kaldifst 而非 pynini）
- https://docs.rs/rustfst/latest/rustfst/ ；https://github.com/Garvys/rustfst （issue #316 lookahead、#288 determinize）
- https://github.com/wenet-e2e/WeTextProcessing/issues/180 、/issues/237 、/issues/282 、/pull/343
- https://github.com/k2-fsa/kaldifst/blob/master/kaldifst/csrc/text-normalizer.cc
- https://github.com/NVIDIA/NeMo-text-processing
- https://crates.io/crates/text2num ；https://crates.io/crates/text-processing-rs ；https://crates.io/crates/wetext-rs

Qwen3-ASR：
- https://github.com/QwenLM/Qwen3-ASR ；https://huggingface.co/Qwen/Qwen3-ASR-0.6B-hf （Context / hotwords 小节）
- https://github.com/QwenLM/Qwen3-ASR/issues/13 、/issues/106 、/issues/140 、/issues/186
- https://k2-fsa.github.io/sherpa/onnx/qwen3-asr/ ；https://huggingface.co/ggml-org/Qwen3-ASR-0.6B-GGUF ；https://github.com/ggml-org/llama.cpp/issues/26749
- https://arxiv.org/html/2512.21828 （热词注入 top-2 vs top-10）

SenseVoice / FunASR：
- https://github.com/QwenAudio/SenseVoice （issue #3 热词无计划、#140 use_itn 与标点捆绑、#270）
- https://github.com/modelscope/FunASR/blob/main/docs/python_api.md （Text-Level Hotword Correction）；`funasr/utils/postprocess_hotwords.py`
- https://github.com/modelscope/FunASR/issues/2534 ；https://github.com/k2-fsa/sherpa-onnx/issues/3373
- https://k2-fsa.github.io/sherpa/onnx/hotwords/index.html ；https://k2-fsa.github.io/sherpa/onnx/homophone-replacer/index.html ；https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html
- https://github.com/QwenAudio/SenseVoice/blob/main/runtime/llama.cpp/BENCHMARKS.md
- `funasr/models/ct_transformer/model.py` （句尾句号硬编码）
- https://modelscope.cn/models/iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch ；https://modelscope.cn/models/iic/punc_ct-transformer_cn-en-common-vocab471067-large （运行范围说明）
- https://modelscope.cn/models/iic/punc_ct-transformer_zh-cn-common-vocab272727-onnx （int8 体积）；https://arxiv.org/pdf/2003.01309.pdf （CT-Transformer 论文）；https://github.com/modelscope/FunASR/issues/1314

纠错模型与词典：
- https://github.com/HaujetZhao/asr-hotword ；https://github.com/Towdium/PinIn
- https://github.com/yongzhuo/macro-correct （误纠率横评表 3.3.3）
- https://huggingface.co/shibing624/macbert4csc-base-chinese ；https://github.com/Claude-Liu/ReLM
- https://arxiv.org/abs/2412.03075 （ASR-EC）；https://arxiv.org/abs/2405.15216 ；https://arxiv.org/abs/2309.15701 （HyPoradise）；https://arxiv.org/pdf/2310.05129 （ED-CEC）；https://arxiv.org/abs/2506.10779
- https://arxiv.org/html/2407.15498 ；https://arxiv.org/html/2308.08796v2 ；http://tcci.ccf.org.cn/conference/2019/papers/CN117.pdf
- https://github.com/rime/home/wiki/UserGuide ；https://github.com/libpinyin/libpinyin/wiki/n-gram-model-format
- https://github.com/MrSupW/ContextASR-Bench ；https://aclanthology.org/W15-3106/
- crates：`ib-matcher`、`inputx-phonetic-edit`、`pinyin`、`jieba-rs`、`rphonetic`、`rwer`

---

## 附录：`poc/itn-rustfst` 运行记录（2026-09-22，M2，release）

```
cd poc/itn-rustfst && cargo run --release
```

```
[wetext 0.1.8 zh+en ITN] 4 个 FST 加载耗时 22.3ms（zh tagger 29402 状态 / verbalizer 32513 状态）

--- 中文 ITN（wetext 预编译 + Rust TokenParser 移植）---
一点三                                => 1.3
二零二六年九月                            => 2026/09
同比增长百分之六点三                         => 同比增长6.3%
价格是十三点五元                           => 价格是¥13.5
总量的五分之一以上                          => 总量的1/5以上
上午八点半准时开会                          => 8:30a.m.准时开会
我要一个苹果一下就好                         => 我要一个苹果一下就好
可以打我手机幺三五零幺二三四五六七                  => 可以打我手机13501234567
重达二十五千克                            => 重达25kg
十一点                                => 11点
两千零二十六年                            => 2026年
hello world 一点三                    => hello world 1.3
我有1.3个和一点三个                        => 我有1.3个和1.3个
三十万三十万                             => 303000 100000
比分是十一比七十一比六                        => 比分是11:71:6

--- 英文 ITN（wetext 预编译）---
one point three                                => 1.3
call me at five five five one two three four   => call me at 5551234
twenty twenty six                              => 2026
three point one four percent                   => 3.14 %
it costs twelve dollars fifty                  => it costs $12.50

--- 对照：sherpa-onnx 官方 itn_zh_number.fst（单段，25 KB）---
一点三                      => 1点3
二零二六年九月                  => 2026年9月
同比增长百分之六点三               => 同比增长百分之6点3
我要一个苹果                   => 我要1个苹果
上午八点半                    => 上午8点半

中文 ITN 端到端平均耗时（26 字，200 次）：3.543 ms
maximum resident set size: 29392896 (29 MB)
```
