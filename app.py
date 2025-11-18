import re
import json
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime
import io
import csv

import streamlit as st
import streamlit.components.v1 as components
import pyphen

# ================== 基础设置 ==================

st.set_page_config(page_title="Phonics 英语音节工具", page_icon="🔤")

# 英语（美式）音节拆分器
dic = pyphen.Pyphen(lang="en_US")

# 初始化 session_state
if "input_box_id" not in st.session_state:
    st.session_state["input_box_id"] = 0

# 字典缓存：避免对同一个单词重复请求 API
# { word_lower: { "ipa": str|None, "pos": str|None, "definition": str|None,
#                 "example": str|None, "synonyms": list[str] } }
if "dict_cache" not in st.session_state:
    st.session_state["dict_cache"] = {}

# 历史记录：按“标准单词”去重
# { base_word: { 时间, 次数, 原始输入, 标准单词, 音节分隔, 音节数, IPA } }
if "history" not in st.session_state:
    st.session_state["history"] = {}


# ================== 分音节规则解释器（简单版） ==================

def explain_syllable_rules(word: str, syllables: list[str]) -> list[str]:
    """
    简易版分音节规则解释器（不依赖 IPA）
    根据单词拼写和拆出的音节，生成几条可读的规则说明。
    """
    rules: list[str] = []
    base = word.lower()

    # 固定后缀规则
    suffix_rules = {
        "tion": "后缀 -tion 通常构成一个独立音节",
        "sion": "后缀 -sion 通常构成一个独立音节",
        "ing": "后缀 -ing 通常构成一个独立音节",
        "er": "后缀 -er 常单独成音节（如 teacher, computer）",
        "or": "后缀 -or 常单独成音节（如 actor, doctor）",
        "ment": "后缀 -ment 常作为独立音节（如 movement）",
        "ness": "后缀 -ness 常作为独立音节（如 kindness）",
        "able": "后缀 -able 通常为独立音节（如 comfortable）",
        "ible": "后缀 -ible 通常为独立音节（如 possible）",
    }

    for suf, text in suffix_rules.items():
        if base.endswith(suf):
            rules.append(f"· {text}")
            break

    # 双辅音断点（VCC → VC-C）
    if re.search(r"[aeiou][bcdfghjklmnpqrstvwxyz]{2}", base):
        rules.append("· VCC 结构中，元音后跟两个辅音时，一般在第一个辅音后断开（V-C-C）")

    # CVC 结构
    if re.search(r"[bcdfghjklmnpqrstvwxyz][aeiou][bcdfghjklmnpqrstvwxyz]", base):
        rules.append("· CVC 结构中，短元音后往往在辅音处断开，形成一个自然音节")

    # 多音节提醒
    if len(syllables) >= 3:
        rules.append("· 多音节单词通常从左到右按发音节奏自然分段")

    # 没命中任何规则，给一个兜底提示
    if not rules:
        rules.append("· 根据常见发音节奏拆分音节（本词不属于常见规则范畴）")

    return rules


# ================== 字典 API：获取 IPA + 释义 + 例句 + 同义词 ==================

def fetch_word_info_from_api(word: str):
    """
    向免费字典 API 请求该单词的详细信息：
    - IPA 音标
    - 词性（part of speech）
    - 第一条英文释义
    - 一个例句
    - 同义词列表
    查不到或网络异常时，返回一个字段都为 None 的 dict。
    """
    base_result = {
        "ipa": None,
        "pos": None,
        "definition": None,
        "example": None,
        "synonyms": [],
    }

    try:
        url = (
            "https://api.dictionaryapi.dev/api/v2/entries/en/"
            + urllib.parse.quote(word)
        )
        with urllib.request.urlopen(url, timeout=5) as resp:
            if resp.status != 200:
                return base_result
            data = resp.read().decode("utf-8")
        js = json.loads(data)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, Exception):
        return base_result

    # 正常情况下是一个列表：[{...}]
    if not (isinstance(js, list) and js):
        return base_result

    entry = js[0]

    # -------- IPA --------
    ipa = entry.get("phonetic")
    if not ipa:
        phonetics = entry.get("phonetics") or []
        if isinstance(phonetics, list):
            for p in phonetics:
                text = p.get("text")
                if isinstance(text, str) and text.strip():
                    ipa = text.strip()
                    break
    if isinstance(ipa, str) and ipa.strip():
        ipa = ipa.strip()
        # 一般 API 会自带斜杠，如果没有，我们补一个
        if not (ipa.startswith("/") or ipa.startswith("[")):
            ipa = f"/{ipa}/"
        base_result["ipa"] = ipa

    # -------- meanings: 词性 + 释义 + 例句 + 同义词 --------
    meanings = entry.get("meanings") or []
    if isinstance(meanings, list) and meanings:
        m = meanings[0]  # 只看第一条大类
        pos = m.get("partOfSpeech")
        if isinstance(pos, str) and pos.strip():
            base_result["pos"] = pos.strip()

        defs = m.get("definitions") or []
        if isinstance(defs, list) and defs:
            d0 = defs[0]
            # 释义
            definition = d0.get("definition")
            if isinstance(definition, str) and definition.strip():
                base_result["definition"] = definition.strip()

            # 例句
            example = d0.get("example")
            if isinstance(example, str) and example.strip():
                base_result["example"] = example.strip()

            # 同义词
            syns = d0.get("synonyms") or []
            if isinstance(syns, list):
                uniq = []
                for s in syns:
                    if isinstance(s, str):
                        s_clean = s.strip()
                        if s_clean and s_clean not in uniq:
                            uniq.append(s_clean)
                base_result["synonyms"] = uniq[:5]

    return base_result


def get_word_info(word: str):
    """
    对外接口：先查缓存，没有再请求 API。
    返回 dict:
    {
      "ipa": str|None,
      "pos": str|None,
      "definition": str|None,
      "example": str|None,
      "synonyms": [str, ...]
    }
    """
    cache = st.session_state["dict_cache"]
    key = word.lower()
    if key in cache:
        return cache[key]

    info = fetch_word_info_from_api(key)
    cache[key] = info
    return info


# ================== 顶部标题区域 ==================

st.markdown(
    """
    <h1 style="text-align:center; margin-bottom:0.2rem;">Phonics 英语音节工具</h1>
    <p style="text-align:center; color:#9CA3AF; font-size:0.9rem;">
      拆音节 · 看音标 · 听发音 · 查释义 · 可视化分音节规则 · 自动生成你的专属单词本
    </p>
    <hr style="margin-top:0.8rem; margin-bottom:1.2rem; border-color:#374151;">
    """,
    unsafe_allow_html=True,
)

# 输入区 + 右侧使用提示
col_left, col_right = st.columns([2, 1])

with col_left:
    # 当前这一轮输入框使用的 key（用于“清空”）
    input_key = f"user_input_{st.session_state['input_box_id']}"
    text = st.text_input("请输入英文单词或句子：", key=input_key)

    if st.button("清空当前输入"):
        st.session_state["input_box_id"] += 1
        st.rerun()

with col_right:
    st.markdown(
        """
        <div style="padding:0.75rem 0.9rem; border-radius:0.75rem;
                    background-color:#111827; border:1px solid #1F2937; font-size:0.85rem;">
          <b>使用提示</b><br/>
          · 支持一次输入多个单词或一个短句；<br/>
          · 每个单词会拆音节，并给出 IPA、释义、例句和发音；<br/>
          · 会自动给出简单的“分音节规则”说明，帮助理解为什么这样拆；<br/>
          · 下方会自动记录历史，可导出为 CSV / TXT 用作单词本。
        </div>
        """,
        unsafe_allow_html=True,
    )

st.write("")  # 小间距

# ================== 主逻辑：拆音节 + IPA + 释义 + 规则 + 发音 ==================

if text.strip():
    words = text.strip().split()
    total_syllables = 0
    total_words = 0

    st.markdown("### 拆音节、音标、释义与规则说明")

    for w in words:
        # 去掉标点，只保留字母和 '
        clean_word = re.sub(r"[^A-Za-z']", "", w)
        if not clean_word:
            continue

        base = clean_word.lower()
        total_words += 1

        # 拆音节
        hyphenated = dic.inserted(base)          # e.g. computer -> com-put-er
        syllables = hyphenated.split("-")
        cnt = len(syllables)
        total_syllables += cnt
        pretty = "·".join(syllables)            # com·put·er

        # ======== 字典信息：IPA + 释义 + 例句 + 同义词 ========
        info = get_word_info(base)
        ipa_text = info.get("ipa")
        pos = info.get("pos")
        definition = info.get("definition")
        example = info.get("example")
        synonyms = info.get("synonyms") or []

        # ======== 分音节规则解释 ========
        rules = explain_syllable_rules(base, syllables)

        # ======== 单词卡片 UI ========
        card_html = f"""
        <div style="padding:0.75rem 1rem; margin-bottom:0.8rem; border-radius:0.9rem;
                    background-color:#020617; border:1px solid #1E293B;">
          <div style="font-size:1.1rem; font-weight:600; margin-bottom:0.25rem;">
            {w}
          </div>
          <div style="color:#E5E7EB; margin-bottom:0.15rem;">
            {pretty}（{cnt} 个音节）
          </div>
        """

        # IPA
        if ipa_text:
            card_html += (
                f'<div style="color:#A5B4FC; font-size:0.9rem; margin-bottom:0.15rem;">'
                f'音标（IPA）：<code>{ipa_text}</code></div>'
            )

        # 释义（带词性）
        if definition:
            if pos:
                card_html += (
                    f'<div style="color:#D1D5DB; font-size:0.9rem; margin-top:0.15rem;">'
                    f'<b>释义：</b><i>{pos}</i> – {definition}'
                    f'</div>'
                )
            else:
                card_html += (
                    f'<div style="color:#D1D5DB; font-size:0.9rem; margin-top:0.15rem;">'
                    f'<b>释义：</b>{definition}</div>'
                )

        # 例句
        if example:
            card_html += (
                f'<div style="color:#9CA3AF; font-size:0.85rem; margin-top:0.15rem;">'
                f'<b>例句：</b>{example}</div>'
            )

        # 同义词
        if synonyms:
            syn_str = ", ".join(synonyms)
            card_html += (
                f'<div style="color:#FBBF24; font-size:0.85rem; margin-top:0.15rem;">'
                f'<b>同义词：</b>{syn_str}</div>'
            )

        # 分音节规则说明块
        rule_html = (
            "<div style='color:#60A5FA; font-size:0.85rem; "
            "margin-top:0.25rem;'><b>分音节规则（简要）：</b><br/>"
        )
        for r in rules:
            rule_html += f"{r}<br/>"
        rule_html += "</div>"

        card_html += rule_html
        card_html += "</div>"

        st.markdown(card_html, unsafe_allow_html=True)

        # ======== 发音（有道 MP3） ========
        audio_url = (
            "https://dict.youdao.com/dictvoice?audio="
            + urllib.parse.quote(base)
            + "&type=2"
        )

        components.html(
            f"""
<audio controls style="width: 230px; margin-top:-0.35rem; margin-bottom:0.75rem;">
  <source src="{audio_url}" type="audio/mpeg">
  您的浏览器不支持音频播放。
</audio>
""",
            height=60,
        )

        # ======== 写入历史记录（去重 + 次数；历史里暂时只存 IPA，不存释义） ========
        history = st.session_state["history"]
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ipa_for_history = ipa_text or ""

        if base in history:
            entry = history[base]
            entry["时间"] = now_str
            entry["次数"] += 1
            entry["原始输入"] = w
            entry["音节分隔"] = pretty
            entry["音节数"] = cnt
            entry["IPA"] = ipa_for_history
        else:
            history[base] = {
                "时间": now_str,
                "次数": 1,
                "原始输入": w,
                "标准单词": base,
                "音节分隔": pretty,
                "音节数": cnt,
                "IPA": ipa_for_history,
            }

    # 小统计卡片
    st.markdown(
        f"""
        <div style="margin-top:0.8rem; margin-bottom:1.2rem; padding:0.65rem 0.9rem;
                    border-radius:0.75rem; background-color:#020617;
                    border:1px dashed #374151; font-size:0.9rem; color:#E5E7EB;">
          本次输入共包含 <b>{total_words}</b> 个有效单词，合计 <b>{total_syllables}</b> 个音节。
        </div>
        """,
        unsafe_allow_html=True,
    )

else:
    st.write("例如试试：computer / stereotype / information / pineapple")

# ================== 历史记录 + 导出 ==================

history_dict = st.session_state["history"]

st.markdown("---")
st.markdown("### 查询历史（按单词去重，本次运行）")

if not history_dict:
    st.write("暂无历史记录。")
else:
    # 把 dict 转成列表，并按“时间”倒序（最近的在最上）
    records = list(history_dict.values())
    records_sorted = sorted(records, key=lambda x: x["时间"], reverse=True)

    st.table(records_sorted)

    # 生成 CSV 内容
    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    headers = ["时间", "次数", "原始输入", "标准单词", "音节分隔", "音节数", "IPA"]
    writer.writerow(headers)
    # 导出时按时间正序导出，方便复习
    for item in sorted(records, key=lambda x: x["时间"]):
        writer.writerow(
            [
                item["时间"],
                item["次数"],
                item["原始输入"],
                item["标准单词"],
                item["音节分隔"],
                item["音节数"],
                item["IPA"],
            ]
        )
    csv_bytes = csv_buffer.getvalue().encode("utf-8-sig")

    # 生成 TXT 内容（制表符分隔）
    lines = [
        "时间\t次数\t原始输入\t标准单词\t音节分隔\t音节数\tIPA",
    ]
    for item in sorted(records, key=lambda x: x["时间"]):
        line = (
            f"{item['时间']}\t{item['次数']}\t{item['原始输入']}\t{item['标准单词']}\t"
            f"{item['音节分隔']}\t{item['音节数']}\t{item['IPA']}"
        )
        lines.append(line)
    txt_data = "\n".join(lines)

    st.write("")
    st.markdown("**导出历史记录：**")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.download_button(
            "下载 CSV（Excel）",
            data=csv_bytes,
            file_name="phonics_history.csv",
            mime="text/csv",
        )
    with col2:
        st.download_button(
            "下载 TXT（文本）",
            data=txt_data,
            file_name="phonics_history.txt",
            mime="text/plain",
        )
    with col3:
        if st.button("清空历史记录"):
            st.session_state["history"] = {}
            st.rerun()
