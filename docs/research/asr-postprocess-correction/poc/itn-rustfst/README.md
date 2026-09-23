# itn-rustfst：纯 Rust ITN 验证

用 `rustfst`（MIT/Apache-2.0）在进程内直接加载 WeTextProcessing 预编译的 OpenFst 二进制，跑
tagger → TokenParser 重排 → verbalizer，把中英文口语数字转成阿拉伯数字。零 Python、零 C++、零模型。

```bash
cargo run --release
```

- `fsts/zh/itn/`、`fsts/en/itn/`：取自 PyPI `wetext==0.1.8` wheel 的 `wetext/fsts/{zh,en}/itn/{tagger,verbalizer}.fst`
  （上游 WeTextProcessing，Apache-2.0）。
- `fsts/sherpa/itn_zh_number.fst`：sherpa-onnx 官方 `asr-models` release 里的单段规则，仅作能力对照。
- `src/main.rs`：约 150 行，含 `ITN_ORDERS` 重排的最小移植；date/time 的渲染格式可在重排阶段改成自己想要的。

本机（Apple M2）实测：4 个 FST 加载 22 ms，常驻 29 MB，26 字中文句 3.5 ms。
详细结论见 [../../README.md](../../README.md)（调研报告）第 2 节。
