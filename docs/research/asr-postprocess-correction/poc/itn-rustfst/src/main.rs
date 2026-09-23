//! ITN（逆文本正则化）验证：纯 Rust 进程内加载 WeTextProcessing 预编译的 OpenFst 二进制，
//! 跑 tagger -> TokenParser(重排) -> verbalizer 三段流程。
//! FST 来自 `pip download wetext==0.1.8` 的 wheel（wetext/fsts/{zh,en}/itn/{tagger,verbalizer}.fst）。
//! 运行：cargo run --release   （在本目录下执行，FST 用相对路径）
use rustfst::prelude::*;
use std::sync::Arc;
use std::time::Instant;

type VF = VectorFst<TropicalWeight>;

/// WeTextProcessing ITN_ORDERS：verbalizer 要求各字段按固定顺序出现，否则输出空串。
fn orders(name: &str) -> Option<&'static [&'static str]> {
    Some(match name {
        "date" => &["year", "month", "day", "preserve_order"],
        "fraction" => &["sign", "numerator", "denominator"],
        "measure" => &["numerator", "denominator", "value", "units"],
        "money" => &["currency", "value", "decimal", "quantity"],
        "time" => &["hour", "minute", "second", "noon", "zone"],
        "telephone" => &["country_code", "number_part"],
        "electronic" => &["username", "domain", "protocol"],
        _ => return None,
    })
}

/// WeTextProcessing TokenParser::Reorder 的最小移植：解析 `name { key: "value" ... }` 串并按 ITN_ORDERS 重排。
fn reorder(tagged: &str) -> String {
    let c: Vec<char> = tagged.chars().collect();
    let mut i = 0usize;
    let mut out: Vec<String> = vec![];
    while i < c.len() {
        while i < c.len() && c[i] == ' ' { i += 1; }
        if i >= c.len() { break; }
        let mut name = String::new();
        while i < c.len() && c[i] != ' ' && c[i] != '{' { name.push(c[i]); i += 1; }
        while i < c.len() && (c[i] == ' ' || c[i] == '{') { i += 1; }
        let mut members: Vec<(String, String)> = vec![];
        while i < c.len() && c[i] != '}' {
            while i < c.len() && c[i] == ' ' { i += 1; }
            let mut key = String::new();
            while i < c.len() && c[i] != ':' && c[i] != '}' { key.push(c[i]); i += 1; }
            if i < c.len() && c[i] == ':' { i += 1; }
            while i < c.len() && c[i] == ' ' { i += 1; }
            if i < c.len() && c[i] == '"' {
                i += 1;
                let mut val = String::new();
                while i < c.len() && c[i] != '"' {
                    if c[i] == '\\' && i + 1 < c.len() { i += 1; }
                    val.push(c[i]);
                    i += 1;
                }
                i += 1;
                members.push((key.trim().to_string(), val));
            }
            while i < c.len() && c[i] == ' ' { i += 1; }
        }
        if i < c.len() && c[i] == '}' { i += 1; }
        let preserve = members.iter().any(|(k, v)| k == "preserve_order" && v == "true");
        if let (Some(ord), false) = (orders(&name), preserve) {
            let mut sorted: Vec<(String, String)> = vec![];
            for k in ord {
                if let Some(m) = members.iter().find(|(kk, _)| kk == k) { sorted.push(m.clone()); }
            }
            for m in &members {
                if !ord.contains(&m.0.as_str()) { sorted.push(m.clone()); }
            }
            members = sorted;
        }
        let body: Vec<String> = members.iter().map(|(k, v)| format!("{k}: \"{v}\"")).collect();
        out.push(format!("{} {{ {} }}", name, body.join(" ")));
    }
    out.join(" ")
}

/// 把 UTF-8 字节串变成线性 acceptor（WeText 的 FST 以字节为符号）。
fn linear(input: &str) -> anyhow::Result<VF> {
    let mut lin = VF::new();
    let mut prev = lin.add_state();
    lin.set_start(prev)?;
    for b in input.as_bytes() {
        let s = lin.add_state();
        lin.add_tr(prev, Tr::new(*b as Label, *b as Label, TropicalWeight::one(), s))?;
        prev = s;
    }
    lin.set_final(prev, TropicalWeight::one())?;
    Ok(lin)
}

/// compose + shortest_path，读出输出标签。
fn apply(fst: &Arc<VF>, input: &str) -> anyhow::Result<String> {
    let lat: VF = rustfst::algorithms::compose::compose::<TropicalWeight, VF, VF, VF, Arc<VF>, Arc<VF>>(
        Arc::new(linear(input)?),
        Arc::clone(fst),
    )?;
    let sp: VF = shortest_path(&lat)?;
    let mut out: Vec<u8> = vec![];
    let mut st = sp.start();
    while let Some(s) = st {
        let mut next = None;
        for tr in sp.get_trs(s)?.iter() {
            if tr.olabel != 0 { out.push(tr.olabel as u8); }
            next = Some(tr.nextstate);
            break;
        }
        if next == Some(s) { break; }
        st = next;
    }
    Ok(String::from_utf8_lossy(&out).to_string())
}

fn pipeline(t: &Arc<VF>, v: &Arc<VF>, s: &str) -> anyhow::Result<String> {
    let tagged = apply(t, s)?;
    apply(v, &reorder(&tagged))
}

fn load(path: &str) -> anyhow::Result<Arc<VF>> {
    Ok(Arc::new(VectorFst::read(path)?))
}

fn main() -> anyhow::Result<()> {
    let t0 = Instant::now();
    let zt = load("fsts/zh/itn/tagger.fst")?;
    let zv = load("fsts/zh/itn/verbalizer.fst")?;
    let et = load("fsts/en/itn/tagger.fst")?;
    let ev = load("fsts/en/itn/verbalizer.fst")?;
    println!("[wetext 0.1.8 zh+en ITN] 4 个 FST 加载耗时 {:?}（zh tagger {} 状态 / verbalizer {} 状态）\n",
        t0.elapsed(), zt.num_states(), zv.num_states());

    println!("--- 中文 ITN（wetext 预编译 + Rust TokenParser 移植）---");
    for s in [
        "一点三", "二零二六年九月", "同比增长百分之六点三", "价格是十三点五元", "总量的五分之一以上",
        "上午八点半准时开会", "我要一个苹果一下就好", "可以打我手机幺三五零幺二三四五六七",
        "重达二十五千克", "十一点", "两千零二十六年", "hello world 一点三", "我有1.3个和一点三个",
        "三十万三十万", "比分是十一比七十一比六",
    ] {
        println!("{:<34} => {}", s, pipeline(&zt, &zv, s)?);
    }

    println!("\n--- 英文 ITN（wetext 预编译）---");
    for s in [
        "one point three", "call me at five five five one two three four",
        "twenty twenty six", "three point one four percent", "it costs twelve dollars fifty",
    ] {
        println!("{:<46} => {}", s, pipeline(&et, &ev, s)?);
    }

    println!("\n--- 对照：sherpa-onnx 官方 itn_zh_number.fst（单段，25 KB）---");
    let sf = load("fsts/sherpa/itn_zh_number.fst")?;
    for s in ["一点三", "二零二六年九月", "同比增长百分之六点三", "我要一个苹果", "上午八点半"] {
        println!("{:<24} => {}", s, apply(&sf, s)?);
    }

    let bench = "同比增长百分之六点三上午八点半准时开会重达二十五千克";
    let n = 200;
    let t = Instant::now();
    for _ in 0..n { pipeline(&zt, &zv, bench)?; }
    println!("\n中文 ITN 端到端平均耗时（{} 字，{} 次）：{:.3} ms", bench.chars().count(), n, t.elapsed().as_secs_f64() * 1000.0 / n as f64);
    Ok(())
}
