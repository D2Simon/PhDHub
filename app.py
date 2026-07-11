import time
import hashlib
import base64
import html
import streamlit as st
import google.generativeai as genai
from openai import OpenAI
import json
import re
from streamlit_autorefresh import st_autorefresh
import pandas as pd
from datetime import datetime, timedelta
import os
import uuid

from phdhub.ai_services import (
    classify_phd_email,
    evaluate_mock_interview_session,
    extract_category,
    generate_high_frequency_answer,
    generate_mock_interview_turn,
    generate_interview_advice,
    generate_professor_list,
    parse_professor_payload,
    stream_professor_list,
    verify_homepages_bulk,
    generate_resume_analysis,
    generate_rp_analysis,
    generate_interview_questions,
    test_claude_connection,
    verify_professor_homepage,
)
from phdhub.constants import EMAILS_CACHE_FILE
from phdhub.email_client import (
    fetch_all_emails as fetch_all_emails_impl,
    test_imap_connection,
)
from phdhub.email_sync import (
    fetch_once,
    get_cached_emails,
    start_background_email_fetch as start_background_email_fetch_worker,
)
from phdhub.i18n import translate
from phdhub.interview_prep import (
    format_interview_time,
    get_homepage_text_excerpt,
    get_interview_records,
    get_interview_picker_defaults,
    resolve_recent_papers,
)
from phdhub.resume_store import add_resume, delete_resume, get_resume, list_resumes
from phdhub.rp_store import add_rp, delete_rp, get_rp, list_rps
from phdhub.resume_utils import build_pdf_thumbnail_png
from phdhub.stats import get_email_stats_from_emails, get_recent_7d_email_stats_from_emails
from phdhub.storage import (
    load_config,
    load_db,
    load_interview_reviews,
    load_lite_emails,
    load_templates,
    save_config,
    save_db,
    save_interview_reviews,
    save_lite_emails,
    save_templates,
)
from phdhub.timezone_utils import format_local_time
from phdhub.university import (
    get_world_universities as get_world_universities_impl,
    canonical_school_name,
    qs_rank_for,
    country_for,
    qs_top_by_country,
    usnews_top100_list,
    usnews_rank_for,
)
# 洲 -> 国家/地区 级联数据：来自内置 QS 2027 前 500 静态清单（唯一标准）
from phdhub.qs_top500 import CONTINENTS, QS_EDITION
from phdhub.usnews_top100 import USNEWS_EDITION


COUNTRY_LABELS = {
    "China": "China 中国", "Hong Kong": "Hong Kong 香港", "Taiwan": "Taiwan 台湾",
    "Singapore": "Singapore 新加坡", "Japan": "Japan 日本", "South Korea": "South Korea 韩国",
    "Malaysia": "Malaysia 马来西亚", "United Kingdom": "United Kingdom 英国", "Germany": "Germany 德国",
    "France": "France 法国", "Netherlands": "Netherlands 荷兰", "Switzerland": "Switzerland 瑞士",
    "Sweden": "Sweden 瑞典", "Denmark": "Denmark 丹麦", "Norway": "Norway 挪威", "Finland": "Finland 芬兰",
    "Italy": "Italy 意大利", "Spain": "Spain 西班牙", "Ireland": "Ireland 爱尔兰", "Austria": "Austria 奥地利",
    "Belgium": "Belgium 比利时", "United States": "United States 美国", "Canada": "Canada 加拿大",
    "Mexico": "Mexico 墨西哥", "Australia": "Australia 澳大利亚", "New Zealand": "New Zealand 新西兰",
}

# 批量导入：给 GPT 的提示词模板（站内填好需求后生成下载，整段贴给 ChatGPT）
# __REQUIREMENTS__ 占位符会被用户在页面上填写的「我的需求」替换。
IMPORT_PROMPT_TEMPLATE = """# 导师库批量导入 —— 给 GPT 的提示词

用法：把下面「===」之间的全部内容复制给 ChatGPT。它会先做检索计划与漏检自查，
最后用 Python 工具把结果存成 JSON 文件，并给你一个**下载链接**；你点链接下载 .json，
回到 PhDHub「导师库管理 → 批量导入」上传即可（无需手动复制粘贴）。

===

你是一个学术导师信息整理助手。请根据我的需求，整理一份导师名单，并把它存成一个 JSON 文件供我下载。

## 输出要求（必须严格遵守）

1. 请先按下方【执行流程】完成检索计划与漏检自查（这部分可简要写出来）。
2. **最终结果必须用你的 Python 工具（代码解释器 / Advanced Data Analysis）写成一个文件并给我下载链接**，不要只把 JSON 贴在聊天里让我复制。具体做法：
   - 用 Python 把最终名单写入文件 `phdhub_import.json`，务必 `json.dump(data, f, ensure_ascii=False, indent=2)`（保留中文、UTF-8 编码）。
   - 文件保存到 `/mnt/data/phdhub_import.json`，然后给我一个可点击的下载链接。
   - 如果你当前没有 Python / 代码解释器工具，再退而求其次：以一个完整的 JSON 代码块结尾（数组正确闭合、绝不截断），我自行复制保存。
3. 顶层是一个对象，唯一的键是 "professors"，值是导师对象的数组：{ "professors": [ {…}, {…} ] }
4. 每个导师对象必须包含下列全部字段，键名一字不差（都是中文键名）：

   - 导师/教授 ：老师姓名（必填）。英文母语老师用英文原名，中文老师可用中文。
   - 学校名称 ：学校（必填）。用学校官方英文全名，如 Stanford University、University of Oxford。
   - 导师邮箱 ：邮箱（选填）。不确定就留空字符串 ""，不要编造邮箱。
   - 国家/地区 ：学校所在国家/地区的英文名（选填），如 United States、United Kingdom、Hong Kong、China；不确定填 "未知"。
   - 院系 ：学院/系（选填）。如 Computer Science、Robotics Institute；不确定填 ""。
   - 主页链接 ：个人主页（选填）。完整 URL（http/https 开头）；不确定就留空 ""，不要编造链接。
   - 研究方向 ：研究方向（选填）。几个关键词用逗号分隔，如 Robotics, Reinforcement Learning；不确定填 "未明确"。
   - 推荐级 ：只能是 "T0" / "T1" / "T2"，默认 "T1"。
   - 阶段 ：新导入一律填 "未联系"。
   - 面试时间 ：一律填 ""。
   - 更新时间 ：一律填 ""。
   - 创建时间 ：一律填 ""。
   - 关联邮件ID ：一律填 ""。
   - LLM摘要 ：用一句话概括这位导师 / 为什么推荐（作为 LLM 生成的摘要，不确定填 ""）。

5. 不要编造邮箱和主页链接。不确定就把对应字段留成空字符串 ""，我会自己补。研究方向、院系可基于公开认知合理填写。
6. 学校名称用官方英文全名即可；我的系统会自动把它对齐到 QS 2027 前 500 的标准名并补上 QS 排名，所以带不带 "The"、大小写都没关系，但请不要写缩写（如别写 "MIT"，写全称）。

7. 【执行流程 ｜ 必须严格按顺序执行，不可跳过、不可偷懒】

   第①步 制定检索计划（先把计划写出来）：
     - 把我要的研究方向拆成 3-8 个子方向 / 同义关键词，包含交叉领域（例如「机器人」会横跨 CS、ECE、ME/MAE、AI/Robotics Institute、认知科学等）。
     - 对每一所目标学校，逐一列出需要排查的院系 / 研究所 / 实验室清单（不要只看一个系）。

   第②步 逐项排查（按计划机械执行）：
     - 对【每一所学校 × 每一个相关院系 / 实验室】逐格排查，把符合方向的老师全部记录下来。
     - 覆盖所有职级：助理教授(Assistant)、副教授(Associate)、正教授(Full / Chair)，以及 Lecturer / Research Fellow / PI。
     - 不要在一所学校只列两三位就跳到下一所；先把这所学校扫完，再去下一所。

   第③步 漏检自查（输出前必做一遍 review，逐条核对，发现遗漏就补回去）：
     - 每所学校的每个相关院系，是否都排查过了？有没有漏掉的系或实验室？
     - 是否漏掉了交叉学科的老师（同一方向可能挂在不同院系 / 学院）？
     - 是否漏掉了：① 刚入职的年轻 faculty；② 非常知名的资深教授；③ 第一直觉之外、但确实做该方向的人？
     - 把自查中新想到的老师补进名单，重复本步，直到再也想不出新的为止。

   第④步 输出：用 Python 工具把最终名单写入 `/mnt/data/phdhub_import.json`（UTF-8、ensure_ascii=False），并给我一个可点击的下载链接（见上方「输出要求」第 2 条）。

   底线：**只列真实存在的老师，绝不为凑数编造**；研究方向不完全确定时可合理归类，但人必须是真实的。

## 输出示例（格式参照——实际请按「我的需求」尽量多列，不要只给一条）

{
  "professors": [
    {
      "导师/教授": "Sergey Levine",
      "学校名称": "University of California, Berkeley",
      "导师邮箱": "",
      "国家/地区": "United States",
      "院系": "Electrical Engineering and Computer Sciences",
      "主页链接": "https://people.eecs.berkeley.edu/~svlevine/",
      "研究方向": "Robotics, Reinforcement Learning, Robot Learning",
      "推荐级": "T1",
      "阶段": "未联系",
      "面试时间": "",
      "更新时间": "",
      "创建时间": "",
      "关联邮件ID": "",
      "LLM摘要": ""
    }
  ]
}

## 我的需求

__REQUIREMENTS__

===
"""


def build_import_prompt(direction, regions, count, extra):
    """把用户在页面填写的需求，套进提示词模板的「我的需求」段落。"""
    lines = []
    if str(direction).strip():
        lines.append(f"研究方向：{direction.strip()}")
    if str(regions).strip():
        lines.append(f"地区 / 学校：{regions.strip()}")
    if str(count).strip():
        lines.append(f"数量：{count.strip()}")
    else:
        lines.append("数量：尽可能列全，每所学校把符合方向的老师都列出来（宁多勿少，不要只给两三位）。")
    if str(extra).strip():
        lines.append(f"补充说明：{extra.strip()}")
    if not str(direction).strip() and not str(regions).strip():
        lines.insert(0, "（请补充：研究方向、目标地区/学校）")
    return IMPORT_PROMPT_TEMPLATE.replace("__REQUIREMENTS__", "\n".join(lines))


def _ui_local(zh, en):
    return en if st.session_state.get("app_lang", "zh-CN") == "en" else zh


def lite_country_picker(key_prefix):
    """先选大洲，再选国家/地区；选『其他』时手动输入。返回最终国家字符串（英文）。"""
    conts = list(CONTINENTS.keys())
    cont = st.selectbox(_ui_local("所在大洲", "Continent"), conts, key=f"{key_prefix}_cont")
    countries = CONTINENTS.get(cont, [])
    if countries:
        return st.selectbox(
            _ui_local("国家 / 地区", "Country / Region"), countries,
            format_func=lambda c: COUNTRY_LABELS.get(c, c), key=f"{key_prefix}_country",
        )
    return st.text_input(_ui_local("国家 / 地区（手动输入）", "Country / Region (manual)"), key=f"{key_prefix}_country_manual").strip()


def lite_prof_form(key_prefix, compact=False, show_contact=True):
    """渲染导师资料输入并返回字段字典（含洲->国家级联）。
    compact=两行紧凑布局；show_contact=是否在表单内渲染导师邮箱/主页（False 时由调用方自行渲染）。"""
    if compact:
        # 第一行：所在大洲 / 国家 / 学校
        r1 = st.columns(3)
        with r1[0]:
            conts = list(CONTINENTS.keys())
            cont = st.selectbox(_ui_local("所在大洲", "Continent"), conts, key=f"{key_prefix}_cont")
        with r1[1]:
            countries = CONTINENTS.get(cont, [])
            if countries:
                country = st.selectbox(_ui_local("国家 / 地区", "Country / Region"), countries,
                                       format_func=lambda c: COUNTRY_LABELS.get(c, c), key=f"{key_prefix}_country")
            else:
                country = st.text_input(_ui_local("国家 / 地区", "Country / Region"), key=f"{key_prefix}_country_manual").strip()
        with r1[2]:
            univ_candidates = []
            if country:
                world_univ = get_world_universities()
                for k, v in world_univ.items():
                    kname = k.split(" ", 1)[1] if " " in k else k
                    if kname.strip().lower() == str(country).strip().lower():
                        univ_candidates = list(v)
                        break
            if univ_candidates:
                manual_label = _ui_local("其他（手动输入）", "Other (manual)")
                choice = st.selectbox(_ui_local("学校", "University"), univ_candidates + [manual_label], key=f"{key_prefix}_univsel")
                if choice == manual_label:
                    univ = st.text_input(_ui_local("学校名称", "University name"), key=f"{key_prefix}_puniv").strip()
                else:
                    univ = re.sub(r"\s*\([^)]*\)\s*$", "", choice).strip()
            else:
                univ = st.text_input(_ui_local("学校", "University"), key=f"{key_prefix}_puniv").strip()
        # 第二行：导师姓名 / 学院 / 研究方向 / 意向级别
        r2 = st.columns(4)
        with r2[0]:
            name = st.text_input(_ui_local("导师姓名", "Professor name"), key=f"{key_prefix}_pname")
        with r2[1]:
            dept = st.text_input(_ui_local("学院", "Department"), key=f"{key_prefix}_pdept")
        with r2[2]:
            direction = st.text_input(_ui_local("研究方向", "Research direction"), key=f"{key_prefix}_pdir")
        with r2[3]:
            prio = st.selectbox(_ui_local("意向级别", "Priority"), ["T0", "T1", "T2"], index=1, key=f"{key_prefix}_pprio")
        if show_contact:
            r3 = st.columns(2)
            with r3[0]:
                email = st.text_input(_ui_local("导师邮箱（选填）", "Professor email (optional)"), key=f"{key_prefix}_pemail")
            with r3[1]:
                home = st.text_input(_ui_local("主页链接（选填）", "Homepage (optional)"), key=f"{key_prefix}_phome")
        else:
            email = ""
            home = ""
    else:
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input(_ui_local("导师姓名（必填）", "Professor name (required)"), key=f"{key_prefix}_pname")
            univ = st.text_input(_ui_local("学校名称（必填）", "University (required)"), key=f"{key_prefix}_puniv")
            country = lite_country_picker(key_prefix)
        with c2:
            dept = st.text_input(_ui_local("院系 / 专业", "Department"), key=f"{key_prefix}_pdept")
            direction = st.text_input(_ui_local("研究方向", "Research direction"), key=f"{key_prefix}_pdir")
            prio = st.selectbox(_ui_local("意向级别", "Priority"), ["T0", "T1", "T2"], index=1, key=f"{key_prefix}_pprio")
        c3, c4 = st.columns(2)
        with c3:
            email = st.text_input(_ui_local("导师邮箱（选填）", "Professor email (optional)"), key=f"{key_prefix}_pemail")
        with c4:
            home = st.text_input(_ui_local("主页链接（选填）", "Homepage (optional)"), key=f"{key_prefix}_phome")
    return {
        "name": name.strip(), "univ": univ.strip(), "country": (country or "").strip(),
        "dept": dept.strip(), "dir": direction.strip(), "prio": prio,
        "email": email.strip(), "home": home.strip(),
    }


@st.dialog("新建导师 / Add Professor")
def add_professor_dialog():
    """浮窗：直接向导师库新增一位导师（默认未联系）。"""
    stage_opts = ["未联系", "已发首封邮件", "收到积极回复", "收到中等回复", "收到消极回复", "面试预约阶段", "面试结束阶段", "口头offer", "终止状态"]
    p = lite_prof_form("db_new", compact=True)
    stage = st.selectbox(_ui_local("当前阶段", "Stage"), stage_opts, index=0, key="db_new_stage")
    note = st.text_area(_ui_local("备注（选填）", "Note (optional)"),
                        key="db_new_note",
                        placeholder=_ui_local("例如：横向项目很多 / 回复很快", "e.g. lots of industry projects"))
    st.caption(_ui_local("已套瓷的导师建议从『邮件记录』登记，以便计入看板指标。",
                         "For contacted professors, log them via 'Email Records' so they count in metrics."))
    if st.button(_ui_local("添加到导师库", "Add to Professor DB"), type="primary", use_container_width=True, key="db_new_submit"):
        if not (p["name"] and p["univ"]):
            st.error(_ui_local("请填写导师姓名和学校名称。", "Please fill in professor name and university."))
        else:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cur = load_db()
            cur.append({
                "导师/教授": p["name"], "导师邮箱": p["email"],
                "国家/地区": (country_for(p["univ"]) or p["country"] or "未知"),
                "学校名称": canonical_school_name(p["univ"]), "院系": p["dept"], "主页链接": p["home"],
                "研究方向": p["dir"] or "未明确", "推荐级": p["prio"], "阶段": stage,
                "面试时间": "", "更新时间": now, "创建时间": now, "关联邮件ID": "",
                "首封邮件时间": now if stage == "已发首封邮件" else "",
                "LLM摘要": "", "备注": note.strip(), "来源": "手动", "QS排名": qs_rank_for(p["univ"]),
                "USNews排名": usnews_rank_for(p["univ"]),
            })
            save_db(cur)
            for k in ["db_new_pname", "db_new_puniv", "db_new_pdept", "db_new_pdir",
                      "db_new_pemail", "db_new_phome", "db_new_country_manual", "db_new_note"]:
                st.session_state.pop(k, None)
            st.rerun()


@st.dialog("编辑导师 / Edit Professor")
def edit_professor_dialog(real_idx):
    """浮窗：编辑导师库中某条导师的全部资料。"""
    db = load_db()
    if not (0 <= real_idx < len(db)):
        st.error(_ui_local("记录不存在，请刷新后重试。", "Record not found, please refresh."))
        return
    rec = db[real_idx]
    stage_opts = ["未联系", "已发首封邮件", "收到积极回复", "收到中等回复", "收到消极回复", "面试预约阶段", "面试结束阶段", "口头offer", "终止状态"]
    prio_opts = ["T0", "T1", "T2"]
    k = f"edit_{real_idx}"

    cur_country = str(rec.get("国家/地区", "") or "").strip()
    cur_univ = str(rec.get("学校名称", "") or "").strip()

    # 大洲默认值：反查包含当前国家的大洲，未匹配则落到「其他」
    conts = list(CONTINENTS.keys())
    def_cont_idx = next((i for i, c in enumerate(conts) if cur_country in CONTINENTS.get(c, [])),
                        conts.index("其他 / Other") if "其他 / Other" in conts else 0)

    c1, c2 = st.columns(2)
    with c1:
        name = st.text_input(_ui_local("导师姓名（必填）", "Professor name (required)"),
                             value=str(rec.get("导师/教授", "")), key=f"{k}_name")
        # 大洲 -> 国家 级联（与邮件登记表一致）
        cont = st.selectbox(_ui_local("所在大洲", "Continent"), conts, index=def_cont_idx, key=f"{k}_cont")
        countries = CONTINENTS.get(cont, [])
        if countries:
            c_idx = countries.index(cur_country) if cur_country in countries else 0
            country = st.selectbox(_ui_local("国家 / 地区", "Country / Region"), countries, index=c_idx,
                                   format_func=lambda c: COUNTRY_LABELS.get(c, c), key=f"{k}_countrysel")
        else:
            country = st.text_input(_ui_local("国家 / 地区（手动输入）", "Country / Region (manual)"),
                                    value=cur_country, key=f"{k}_countryman").strip()
        dept = st.text_input(_ui_local("院系 / 专业", "Department"),
                             value=str(rec.get("院系", "")), key=f"{k}_dept")
    with c2:
        # 学校：依据所选国家给出预选项；命中则预选，否则切到手动并回填原值
        univ_candidates = []
        if country:
            world_univ = get_world_universities()
            for wk, wv in world_univ.items():
                kname = wk.split(" ", 1)[1] if " " in wk else wk
                if kname.strip().lower() == str(country).strip().lower():
                    univ_candidates = list(wv)
                    break
        manual_label = _ui_local("其他（手动输入）", "Other (manual)")
        if univ_candidates:
            cleaned = [re.sub(r"\s*\([^)]*\)\s*$", "", x).strip() for x in univ_candidates]
            opts = univ_candidates + [manual_label]
            u_idx = cleaned.index(cur_univ) if cur_univ in cleaned else len(opts) - 1
            choice = st.selectbox(_ui_local("学校（必填）", "University (required)"), opts, index=u_idx, key=f"{k}_univsel")
            if choice == manual_label:
                univ = st.text_input(_ui_local("学校名称", "University name"), value=cur_univ, key=f"{k}_univman").strip()
            else:
                univ = re.sub(r"\s*\([^)]*\)\s*$", "", choice).strip()
        else:
            univ = st.text_input(_ui_local("学校（必填）", "University (required)"),
                                 value=cur_univ, key=f"{k}_univman").strip()
        direction = st.text_input(_ui_local("研究方向", "Research direction"),
                                  value=str(rec.get("研究方向", "")), key=f"{k}_dir")
        email = st.text_input(_ui_local("导师邮箱（选填）", "Professor email (optional)"),
                              value=str(rec.get("导师邮箱", "")), key=f"{k}_email")
        home = st.text_input(_ui_local("主页链接（选填）", "Homepage (optional)"),
                             value=str(rec.get("主页链接", "")), key=f"{k}_home")

    pcol, scol = st.columns(2)
    prio_cur = str(rec.get("推荐级", "T1"))
    prio = pcol.selectbox(_ui_local("意向级别", "Priority"), prio_opts,
                          index=prio_opts.index(prio_cur) if prio_cur in prio_opts else 1, key=f"{k}_prio")
    stage_cur = str(rec.get("阶段", "未联系"))
    stage = scol.selectbox(_ui_local("当前阶段", "Stage"), stage_opts,
                           index=stage_opts.index(stage_cur) if stage_cur in stage_opts else 0, key=f"{k}_stage")
    llm_summary = st.text_area(_ui_local("LLM Summary（AI 生成，可编辑）", "LLM Summary (AI-generated, editable)"),
                               value=clean_note(rec.get("LLM摘要", "")), key=f"{k}_llm",
                               placeholder=_ui_local("批量导入时由 GPT 生成的一句话摘要", "One-line summary generated by GPT on bulk import"))
    note = st.text_area(_ui_local("备注（人工填写，选填）", "Note (your own, optional)"),
                        value=clean_note(rec.get("备注", "")), key=f"{k}_note",
                        placeholder=_ui_local("例如：横向项目很多 / 回复很快", "e.g. lots of industry projects"))

    if st.button(_ui_local("保存修改", "Save changes"), type="primary", use_container_width=True, key=f"{k}_save"):
        if not (name.strip() and univ.strip()):
            st.error(_ui_local("请填写导师姓名和学校名称。", "Please fill in professor name and university."))
            return
        # 重新载入，避免覆盖期间发生的其他改动
        cur = load_db()
        if not (0 <= real_idx < len(cur)):
            st.error(_ui_local("记录已变化，请刷新后重试。", "Record changed, please refresh."))
            return
        previous_stage = str(cur[real_idx].get("阶段", "未联系"))
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur[real_idx].update({
            "导师/教授": name.strip(),
            "学校名称": canonical_school_name(univ.strip()),
            "国家/地区": (country_for(univ.strip()) or country.strip() or "未知"),
            "院系": dept.strip(),
            "研究方向": direction.strip() or "未明确",
            "导师邮箱": email.strip(),
            "主页链接": home.strip(),
            "推荐级": prio,
            "阶段": stage,
            "LLM摘要": llm_summary.strip(),
            "备注": note.strip(),
            "QS排名": qs_rank_for(univ.strip()),
            "USNews排名": usnews_rank_for(univ.strip()),
            "更新时间": now_str,
        })
        if stage == "已发首封邮件" and previous_stage != stage:
            cur[real_idx]["首封邮件时间"] = now_str
        save_db(cur)
        st.session_state["db_edit_toast"] = _ui_local("已保存修改", "Changes saved")
        st.rerun()


def t(key):
    return translate(key, st.session_state.get("app_lang", "zh-CN"))


def tr(zh, en):
    return en if st.session_state.get("app_lang", "zh-CN") == "en" else zh


def _save_ai_settings_from_state():
    config = load_config()
    if "settings_ai_provider" in st.session_state:
        config["ai_provider"] = st.session_state.get("settings_ai_provider", "通义千问 (Qwen)")
    if "settings_qwen_api_key" in st.session_state:
        config["qwen_api_key"] = st.session_state.get("settings_qwen_api_key", "")
    if "settings_gemini_api_key" in st.session_state:
        config["gemini_api_key"] = st.session_state.get("settings_gemini_api_key", "")
    if "settings_claude_api_key" in st.session_state:
        config["claude_api_key"] = st.session_state.get("settings_claude_api_key", "")
    if "settings_claude_base_url" in st.session_state:
        config["claude_base_url"] = st.session_state.get("settings_claude_base_url", "")
    if "settings_claude_model" in st.session_state:
        config["claude_model"] = st.session_state.get("settings_claude_model", "")
    save_config(config)


def _ai_cfg_with_app_lang(cfg):
    ai_cfg = dict(cfg or {})
    ai_cfg["app_lang"] = st.session_state.get("app_lang", "zh-CN")
    return ai_cfg


_GEMINI_MODEL = "gemini-2.5-flash"


def _gemini_generate_content_with_fallback(prompt, stream=False):
    if "HTTP_PROXY" not in os.environ and "HTTPS_PROXY" not in os.environ:
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
    try:
        model = genai.GenerativeModel(_GEMINI_MODEL)
        response = model.generate_content(prompt, stream=stream)
        return response, _GEMINI_MODEL
    except Exception as e:
        raise Exception(f"{_GEMINI_MODEL}: {e}")


@st.cache_data(ttl=86400)
def get_world_universities():
    return get_world_universities_impl()


def school_ranking_text(university, qs_rank=""):
    """Return a compact QS/US News label for professor and email views."""
    labels = []
    qs_value = qs_rank or qs_rank_for(university)
    usnews_value = usnews_rank_for(university)
    if str(qs_value).strip() not in ("", "-", "0"):
        labels.append(f"QS #{qs_value}")
    if str(usnews_value).strip() not in ("", "-", "0"):
        labels.append(f"US News #{usnews_value}")
    return " · ".join(labels)


def get_recent_7d_email_stats():
    success, emails = get_cached_emails(limit=5000)
    if not success:
        emails = []
    emails = list(emails) + load_lite_emails()
    return get_recent_7d_email_stats_from_emails(emails)


def get_total_email_stats():
    success, emails = get_cached_emails(limit=5000)
    if not success:
        emails = []
    emails = list(emails) + load_lite_emails()
    return get_email_stats_from_emails(emails)


def get_recent_7d_scheduled_interviews_count():
    db = load_db()
    if not db:
        return 0
    today = datetime.now().date()
    window_start = today - timedelta(days=6)
    count = 0
    for row in db:
        if row.get("阶段") != "面试预约阶段":
            continue
        raw = str(row.get("更新时间", "")).strip()
        if not raw:
            continue
        parsed = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except Exception:
                continue
        if parsed and window_start <= parsed.date() <= today:
            count += 1
    return count


@st.cache_data(ttl=60)
def fetch_all_emails(email_add, password, imap_server, limit=15):
    return fetch_all_emails_impl(email_add, password, imap_server, limit=limit)


st.set_page_config(
    page_title="PhDHub - 智能申博辅助系统",
    page_icon="assets/phdhub-mark.svg",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 启动后台邮件拉取线程（确保在所有工具函数之后执行）
@st.cache_resource
def init_background_fetch():
    start_background_email_fetch_worker()
    return True

init_background_fetch()

# 注入自定义 CSS
st.markdown("""
<style>
    :root {
        --bg-main: #0c0c0d;
        --bg-sidebar: #111114;
        --panel: #16161a;
        --panel-strong: #1d1d22;
        --line: #26262c;
        --line-soft: rgba(255, 255, 255, 0.07);
        --text-main: #ececee;
        --text-soft: #9b9ba3;
        --text-muted: #6b6b73;
        --accent: #8e6bef;
        --accent-bright: #a78bfa;
        --accent-dim: #473b70;
        --accent-soft: rgba(142, 107, 239, 0.16);
        --ok: #56d197;
        --card-shadow: 0 1px 2px rgba(0, 0, 0, 0.4);
    }

    html, body, [class*="css"] {
        font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", "PingFang SC", "Noto Sans SC", sans-serif;
        color: var(--text-main);
    }

    [data-testid="stAppViewContainer"] {
        background: var(--bg-main);
        color: var(--text-main);
    }

    [data-testid="stHeader"] {
        background: var(--bg-main);
        border-bottom: 1px solid var(--line);
    }

    /* Hide Streamlit top-right actions (Deploy + overflow menu) */
    [data-testid="stToolbar"],
    [data-testid="stHeaderActionElements"] {
        display: none !important;
        visibility: hidden !important;
    }

    /* 禁止收起侧边栏：隐藏收起/展开按钮，侧边栏始终常驻 */
    [data-testid="stSidebarCollapseButton"],
    [data-testid="stSidebarCollapsedControl"],
    [data-testid="collapsedControl"] {
        display: none !important;
    }

    section[data-testid="stSidebar"],
    [data-testid="stSidebar"] {
        transform: none !important;
        visibility: visible !important;
        min-width: 244px !important;
        width: 244px !important;
        margin-left: 0 !important;
    }

    [data-testid="stSidebar"] > div {
        background: var(--bg-sidebar);
        border-right: 1px solid var(--line);
    }

    [data-testid="stSidebar"] * {
        color: var(--text-main) !important;
    }

    /* 品牌字体 wordmark（替代原 logo 图标） */
    .phd-brand {
        font-family: "SF Pro Display", "Helvetica Neue", "Segoe UI", "PingFang SC", sans-serif;
        font-size: 30px;
        font-weight: 800;
        letter-spacing: -0.8px;
        line-height: 1.15;
        padding: 6px 0 2px;
        color: var(--text-main) !important;
    }

    .phd-brand-accent {
        background: linear-gradient(135deg, var(--accent-bright), var(--accent));
        -webkit-background-clip: text;
        background-clip: text;
        -webkit-text-fill-color: transparent;
        color: transparent !important;
    }

    h1, h2, h3 {
        color: var(--text-main);
        letter-spacing: -0.01em;
        font-weight: 700;
    }

    p, .stCaption, label, small {
        color: var(--text-soft) !important;
    }

    div[data-testid="stAlert"],
    div[data-testid="stMetric"],
    div[data-testid="stDataFrame"],
    div[data-testid="stExpander"],
    div[data-testid="stFileUploaderDropzone"],
    div[data-testid="stForm"],
    div[data-testid="stTextInputRootElement"],
    div[data-testid="stTextAreaRootElement"],
    div[data-testid="stDateInputFieldContainer"],
    div[data-testid="stSelectbox"],
    div[data-testid="stMultiSelect"] {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 14px;
        box-shadow: var(--card-shadow);
    }

    div[data-testid="stFileUploaderDropzone"] {
        padding: 12px;
        border-style: dashed;
    }

    .stButton > button,
    .stDownloadButton > button {
        border: 1px solid var(--line);
        background: var(--panel-strong);
        color: var(--text-main);
        border-radius: 12px;
        min-height: 40px;
        box-shadow: var(--card-shadow);
        transition: all 0.18s ease;
    }

    .stButton > button:hover,
    .stDownloadButton > button:hover {
        border-color: var(--accent);
        background: #24242b;
    }

    .stButton > button[kind="primary"] {
        background: var(--accent);
        border: 1px solid var(--accent);
        color: #ffffff;
        box-shadow: 0 2px 8px rgba(142, 107, 239, 0.35);
    }

    .stButton > button[kind="primary"]:hover {
        background: var(--accent-bright);
        border-color: var(--accent-bright);
    }

    /* 确保按钮文字清晰（覆盖 p 的灰色规则） */
    .stButton > button p,
    .stButton > button span,
    .stButton > button div,
    .stDownloadButton > button p,
    .stDownloadButton > button span,
    .stDownloadButton > button div {
        color: var(--text-main) !important;
    }
    .stButton > button[kind="primary"],
    .stButton > button[kind="primary"] p,
    .stButton > button[kind="primary"] span,
    .stButton > button[kind="primary"] div {
        color: #ffffff !important;
    }

    .stRadio > div,
    .stSelectbox > div > div {
        border-radius: 10px;
    }

    .stSelectbox,
    .stMultiSelect {
        width: 100%;
        min-width: 0;
    }

    .stSelectbox [data-baseweb="select"],
    .stMultiSelect [data-baseweb="select"] {
        width: 100%;
        min-width: 0;
        max-width: 100%;
        border: 1px solid var(--line) !important;
        border-radius: 10px !important;
        background: var(--panel-strong) !important;
    }

    .stSelectbox [data-baseweb="select"] > div,
    .stMultiSelect [data-baseweb="select"] > div {
        color: var(--text-main) !important;
        background: transparent !important;
        min-width: 0;
        max-width: 100%;
    }

    .stSelectbox label p,
    .stMultiSelect label p {
        white-space: normal !important;
        overflow-wrap: anywhere;
        word-break: break-word;
    }

    [data-testid="stHorizontalBlock"] > div,
    [data-testid="stColumn"] {
        min-width: 0;
    }

    .stTabs [data-baseweb="tab-list"] {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 12px;
        padding: 4px;
    }

    .stTabs [data-baseweb="tab"] {
        border-radius: 8px;
        color: var(--text-soft) !important;
    }

    .stTabs [aria-selected="true"] {
        background: var(--accent-soft) !important;
        color: var(--text-main) !important;
        font-weight: 700;
        border: 1px solid var(--accent);
    }

    hr {
        border: none;
        height: 1px;
        background: var(--line);
    }

    .status-bar {
        background-color: transparent;
        padding: 8px 0;
        margin-bottom: 14px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 6px;
    }

    .status-item {
        font-size: 12.5px;
        font-weight: 600;
        color: var(--text-soft);
        background: var(--panel-strong);
        border: 1px solid var(--line);
        padding: 6px 10px;
        border-radius: 999px;
        display: flex;
        align-items: center;
    }

    .status-item.active {
        color: var(--text-main);
        background: var(--accent-soft);
        border-color: var(--accent);
    }

    /* 回复态情感配色：积极绿 / 消极红 / 中立紫 */
    .status-item.active.pos {
        color: #d5f6e6;
        background: rgba(86, 209, 151, 0.20);
        border-color: #56d197;
    }
    .status-item.active.neg {
        color: #fecaca;
        background: rgba(248, 113, 113, 0.20);
        border-color: #f87171;
    }
    .status-item.active.neu {
        color: var(--text-main);
        background: var(--accent-soft);
        border-color: var(--accent);
    }

    .status-item.completed {
        color: #d5f6e6;
        background: rgba(86, 209, 151, 0.18);
        border-color: rgba(86, 209, 151, 0.50);
    }

    .status-line {
        flex-grow: 1;
        height: 2px;
        background-color: var(--line);
        margin: 0 8px;
    }

    .status-line.completed {
        background-color: var(--ok);
    }

    .tag {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 9999px;
        font-size: 12px;
        font-weight: 600;
        background: var(--accent-soft);
        color: #d9ccff;
        border: 1px solid var(--accent);
    }

    .analysis-module-card {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 14px;
        padding: 14px 14px 10px;
        min-height: 220px;
        box-shadow: var(--card-shadow);
    }

    .analysis-module-head {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 10px;
        color: var(--text-main);
        font-size: 15px;
        font-weight: 700;
    }

    .analysis-module-icon {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 22px;
        height: 22px;
        border-radius: 999px;
        background: var(--accent-soft);
        border: 1px solid var(--accent);
        font-size: 12px;
    }

    .analysis-module-list {
        margin: 0;
        padding-left: 18px;
    }

    .analysis-module-list li {
        color: var(--text-soft);
        margin: 0 0 8px;
        line-height: 1.5;
        font-size: 13.5px;
    }

    .analysis-empty {
        color: var(--text-muted) !important;
        list-style: none;
        margin-left: -18px !important;
        font-style: italic;
    }

    [data-testid="stTextInputRootElement"] input,
    [data-testid="stTextAreaRootElement"] textarea,
    [data-testid="stDateInputFieldContainer"] input {
        color: var(--text-main) !important;
        background: var(--panel-strong) !important;
        border: 1px solid var(--line) !important;
        border-radius: 10px !important;
    }

    [data-testid="stTextInputRootElement"] input:focus,
    [data-testid="stTextAreaRootElement"] textarea:focus,
    [data-testid="stDateInputFieldContainer"] input:focus {
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 3px rgba(142, 107, 239, 0.25) !important;
    }

    div[data-testid="stMetric"] {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        text-align: center;
        padding: 14px 10px;
    }

    /* 看板「回复分布」合并小格子 */
    .reply-box {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 14px;
        box-shadow: var(--card-shadow);
        padding: 14px 10px;
        text-align: center;
        height: 100%;
    }
    .reply-box-label {
        color: var(--text-soft);
        font-size: 0.85rem;
        margin-bottom: 8px;
    }
    .reply-box-nums {
        display: flex;
        justify-content: space-around;
        gap: 6px;
        flex-wrap: wrap;
        font-weight: 700;
        font-size: 1.0rem;
    }

    div[data-testid="stMetric"] > div,
    div[data-testid="stMetricLabel"],
    div[data-testid="stMetricValue"],
    div[data-testid="stMetricDelta"] {
        width: 100%;
        text-align: center;
        align-items: center;
        justify-content: center;
    }

    div[data-testid="stMetric"] * {
        text-align: center !important;
        justify-content: center !important;
    }

    @media (max-width: 900px) {
        div[data-testid="stAlert"],
        div[data-testid="stMetric"],
        div[data-testid="stDataFrame"],
        div[data-testid="stExpander"],
        div[data-testid="stFileUploaderDropzone"],
        div[data-testid="stForm"],
        div[data-testid="stTextInputRootElement"],
        div[data-testid="stTextAreaRootElement"],
        div[data-testid="stDateInputFieldContainer"],
        div[data-testid="stSelectbox"],
        div[data-testid="stMultiSelect"] {
            border-radius: 12px;
        }

        .stButton > button,
        .stDownloadButton > button {
            width: 100%;
        }

        .analysis-module-card {
            min-height: 180px;
        }

    }

    /* 放大对话框宽度，用于 PDF 预览 */
    div[data-testid="stDialog"] div[role="dialog"] {
        width: min(1200px, 95vw);
        border-radius: 16px;
        border: 1px solid var(--line);
        background: var(--panel);
        box-shadow: 0 18px 40px rgba(0, 0, 0, 0.62);
    }

    /* 卡片式容器（st.container(border=True)，用于导师库卡片） */
    [data-testid="stVerticalBlockBorderWrapper"] {
        background: var(--panel);
        border: 1px solid var(--line) !important;
        border-radius: 14px;
        padding: 6px 4px;
    }

    /* 导师库卡片内文字提亮（避免灰色看不清） */
    [data-testid="stVerticalBlockBorderWrapper"] p,
    [data-testid="stVerticalBlockBorderWrapper"] small,
    [data-testid="stVerticalBlockBorderWrapper"] [data-testid="stCaptionContainer"],
    [data-testid="stVerticalBlockBorderWrapper"] [data-testid="stCaptionContainer"] * {
        color: #d6d8de !important;
    }

    /* 内容贴顶，减少顶部空白 */
    .block-container,
    [data-testid="stMainBlockContainer"] {
        padding-top: 2.2rem !important;
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 数据获取
# ==========================================

def purge_lite_emails_for_record(record):
    """删除导师记录时，连带移除它关联的 Lite 手动邮件，使看板指标同步更新。"""
    linked = str((record or {}).get("关联邮件ID", "") or "").strip()
    if not linked or linked == "None":
        return
    ids = {x.strip() for x in linked.split(",") if x.strip() and x.strip() != "None"}
    if not ids:
        return
    lite = load_lite_emails()
    new_lite = [em for em in lite if str(em.get("id", "")) not in ids]
    if len(new_lite) != len(lite):
        save_lite_emails(new_lite)


_STAGE_ORDER = {s: i for i, s in enumerate(
    ["未联系", "已发首封邮件", "收到消极回复", "收到中等回复", "收到积极回复", "面试预约阶段", "面试结束阶段", "口头offer", "终止状态"]
)}


def outreach_waiting_state(record, today=None):
    """Return follow_up/14_days when a first email is still unanswered."""
    if record is None:
        record = {}
    if str(record.get("阶段", "")) != "已发首封邮件":
        return ""
    raw = next((str(record.get(key, "") or "").strip() for key in
                ("首封邮件时间", "更新时间", "创建时间")
                if str(record.get(key, "") or "").strip()), "")
    try:
        sent_date = datetime.fromisoformat(raw[:19]).date()
    except (TypeError, ValueError):
        return ""
    elapsed_days = ((today or datetime.now().date()) - sent_date).days
    if elapsed_days >= 14:
        return "14_days"
    if elapsed_days > 7:
        return "follow_up"
    return ""


def _merge_prof(a, b):
    """把 b 合并进 a：保留更靠前的阶段、并集关联邮件ID、补全空白字段。"""
    out = dict(a)
    if _STAGE_ORDER.get(b.get("阶段", "未联系"), 0) > _STAGE_ORDER.get(out.get("阶段", "未联系"), 0):
        out["阶段"] = b.get("阶段", out.get("阶段"))
        out["面试时间"] = b.get("面试时间", out.get("面试时间", ""))
    for f in ["导师邮箱", "国家/地区", "院系", "主页链接", "研究方向", "推荐级", "面试时间"]:
        cur = str(out.get(f, "")).strip()
        if cur in ("", "未知", "未明确", "None"):
            nv = str(b.get(f, "")).strip()
            if nv and nv not in ("未知", "未明确", "None"):
                out[f] = b.get(f)
    ids = []
    for src in (a, b):
        for x in str(src.get("关联邮件ID", "") or "").split(","):
            x = x.strip()
            if x and x != "None" and x not in ids:
                ids.append(x)
    out["关联邮件ID"] = ",".join(ids)
    if str(b.get("更新时间", "")) > str(out.get("更新时间", "")):
        out["更新时间"] = b.get("更新时间")
    return out


def dedupe_db(db):
    """按（导师姓名 + 学校名称）去重合并，保持原顺序。返回 (新列表, 是否有变化)。"""
    seen = {}
    result = []
    changed = False
    for r in db:
        name = str(r.get("导师/教授", "")).strip().lower()
        univ = str(r.get("学校名称", "")).strip().lower()
        key = (name, univ) if name else None
        if key is not None and key in seen:
            idx = seen[key]
            result[idx] = _merge_prof(result[idx], r)
            changed = True
        else:
            if key is not None:
                seen[key] = len(result)
            result.append(dict(r))
    return result, changed


def dedupe_db_inplace():
    """加载导师库并就地去重（仅在确有重复时写回）。"""
    db = load_db()
    deduped, changed = dedupe_db(db)
    if changed:
        save_db(deduped)


@st.dialog("从看板移除 / Remove from board")
def confirm_delete_dialog(prof_name, univ_name, delete_idx=None):
    st.warning(tr(f"将 **{prof_name}** ({univ_name}) 移出套瓷看板？\n\n导师仍保留在导师库中，状态会重置为「未联系」，关联的邮件记录会被清除。",
                  f"Remove **{prof_name}** ({univ_name}) from the board?\n\nThe professor stays in the Professor DB, the stage resets to '未联系', and linked email records are cleared."))
    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button(tr("返回 / 取消", "Back / Cancel"), use_container_width=True):
            st.rerun()
    with c2:
        if st.button(tr("移出看板", "Remove from board"), use_container_width=True, type="primary"):
            current_db = load_db()
            if delete_idx is not None and 0 <= delete_idx < len(current_db):
                targets = [delete_idx]
            else:
                targets = [i for i, r in enumerate(current_db)
                           if r.get("导师/教授") == prof_name and r.get("学校名称") == univ_name]
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for i in targets:
                purge_lite_emails_for_record(current_db[i])
                current_db[i]["阶段"] = "未联系"
                current_db[i]["面试时间"] = ""
                current_db[i]["关联邮件ID"] = ""
                current_db[i]["更新时间"] = now_str
            save_db(current_db)
            st.success(tr("已移出看板（导师保留在导师库，状态为未联系）。",
                          "Removed from board (kept in Professor DB as 未联系)."))
            st.rerun()


@st.dialog("批量删除确认 / Confirm bulk delete")
def bulk_delete_dialog(idxs):
    """弹窗确认批量删除选中的导师（彻底从导师库移除）。"""
    db = load_db()
    valid = [i for i in idxs if 0 <= i < len(db)]
    if not valid:
        st.info(_ui_local("没有可删除的记录。", "Nothing to delete."))
        if st.button(_ui_local("关闭", "Close"), use_container_width=True):
            st.rerun()
        return
    st.warning(_ui_local(f"确认彻底删除选中的 {len(valid)} 位导师？此操作不可恢复，关联邮件记录也会一并清除。",
                         f"Permanently delete the {len(valid)} selected professors? This cannot be undone; linked email records are also removed."))
    preview = "、".join(f"{db[i].get('导师/教授', '未知')}（{db[i].get('学校名称', '未知')}）" for i in valid[:10])
    if len(valid) > 10:
        preview += _ui_local(f" …等 {len(valid)} 位", f" …and {len(valid)} total")
    st.caption(preview)
    c1, c2 = st.columns(2)
    if c1.button(_ui_local("取消", "Cancel"), use_container_width=True, key="bulkdlg_cancel"):
        st.rerun()
    if c2.button(_ui_local(f"确认删除 {len(valid)} 位", f"Delete {len(valid)}"),
                 type="primary", use_container_width=True, key="bulkdlg_confirm"):
        for i in sorted(valid, reverse=True):
            purge_lite_emails_for_record(db[i])
            db.pop(i)
        save_db(db)
        for _k in [k for k in list(st.session_state) if str(k).startswith("db_sel_")]:
            st.session_state.pop(_k, None)
        st.session_state["db_bulk_import_toast"] = _ui_local(
            f"已删除 {len(valid)} 位导师。", f"Deleted {len(valid)} professors.")
        st.rerun()


@st.dialog("简历预览 / Resume Preview")
def show_resume_pdf_modal(pdf_path, title="简历"):
    st.markdown(f"**{title}**")
    if pdf_path and os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        st.markdown(
            f"""
            <iframe
                src="data:application/pdf;base64,{b64}"
                width="100%"
                height="900"
                type="application/pdf"
            ></iframe>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.warning(tr("找不到该简历文件。", "Resume file not found."))

def clean_note(value):
    """Normalize a 备注 value to a display string ('' when empty / NaN / placeholder)."""
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
    except Exception:
        pass
    text = str(value).strip()
    return "" if text in ("", "-", "nan", "None") else text


def migrate_notes_to_llm_summary():
    """One-time: 批量导入记录里放在「备注」的 AI 说明迁移到「LLM摘要」，备注留给人工。

    幂等：由 config 里的 notes_migrated_v1 守卫，只跑一次；只动批量导入来源，不碰手动备注。
    """
    cfg = load_config()
    if cfg.get("notes_migrated_v1"):
        return
    db = load_db()
    changed = False
    for rec in db:
        if str(rec.get("来源", "")).strip() != "批量导入":
            continue
        note = clean_note(rec.get("备注", ""))
        if note and not clean_note(rec.get("LLM摘要", "")):
            rec["LLM摘要"] = note
            rec["备注"] = ""
            changed = True
    if changed:
        save_db(db)
    cfg["notes_migrated_v1"] = True
    save_config(cfg)


def _save_template_cb(tid):
    """实时保存：套瓷信模版某条的名称/内容写回本地 JSON（text 组件 on_change 触发）。"""
    tpls = load_templates()
    for tp in tpls:
        if tp.get("id") == tid:
            tp["name"] = st.session_state.get(f"tpl_name_{tid}", tp.get("name", ""))
            tp["subject"] = st.session_state.get(f"tpl_subject_{tid}", tp.get("subject", ""))
            tp["content"] = st.session_state.get(f"tpl_content_{tid}", tp.get("content", ""))
            tp["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            break
    save_templates(tpls)


def get_dashboard_data():
    db = load_db()
    if not db:
        return pd.DataFrame(columns=["导师/教授", "国家/地区", "学校名称", "院系", "研究方向", "推荐级", "阶段", "更新时间"])
    return pd.DataFrame(db)

# ==========================================
# 核心 UI 组件
# ==========================================
def render_status_bar(current_status, interview_time=""):
    step_mapping = {
        "未联系": 0, "已发首封邮件": 1, "收到回复": 2, "收到积极回复": 2, "收到中等回复": 2, "收到消极回复": 2,
        "面试准备": 3, "面试预约阶段": 3, "面试结束阶段": 3, "口头offer": 4, "终止状态": 4
    }
    
    current_index = step_mapping.get(current_status, 0)
    en = st.session_state.get("app_lang", "zh-CN") == "en"
    statuses = (["Not contacted", "First email", "Reply", "Interview", "Offer"] if en
                else ["未联系", "首封邮件", "收到回复", "面试环节", "口头offer"])

    reply_label = {
        "收到积极回复": ("Positive" if en else "积极回复"),
        "收到中等回复": ("Neutral" if en else "中等回复"),
        "收到消极回复": ("Negative" if en else "消极回复"),
    }
    if current_status in reply_label:
        statuses[2] = reply_label[current_status]
    elif current_index > 2:
        statuses[2] = ("Replied" if en else "收回复")

    if current_status == "面试预约阶段":
        import math
        iv_time = "" if interview_time is None or (isinstance(interview_time, float) and math.isnan(interview_time)) else str(interview_time)
        time_str = f"<br/><span style='font-size:10.5px;color:#f59e0b;'>{iv_time}</span>" if iv_time else ""
        statuses[3] = (f"Scheduled{time_str}" if en else f"预约{time_str}")
    elif current_status == "面试结束阶段":
        statuses[3] = ("Done" if en else "结束")
    elif current_status == "终止状态":
        statuses[4] = ("Terminated" if en else "终止")
        
    html_parts = ["<div class='status-bar'>"]
    
    sentiment_class = {"收到积极回复": " pos", "收到消极回复": " neg", "收到中等回复": " neu", "终止状态": " neg"}
    for i, status in enumerate(statuses):
        extra = ""
        if i < current_index:
            state_class, icon = "completed", "✓"
        elif i == current_index:
            state_class, icon = "active", "◉"
            extra = sentiment_class.get(current_status, "")
        else:
            state_class, icon = "", "○"

        html_parts.append(f"<div class='status-item {state_class}{extra}'>{icon} {status}</div>")
        
        if i < len(statuses) - 1:
            line_class = "completed" if i < current_index else ""
            html_parts.append(f"<div class='status-line {line_class}'></div>")
            
    html_parts.append("</div>")
    st.markdown("".join(html_parts), unsafe_allow_html=True)

def render_analysis_modules(section_title, modules):
    st.markdown(f"### {section_title}")
    cols = st.columns(len(modules))
    for col, (icon, title, items) in zip(cols, modules):
        normalized_items = []
        if isinstance(items, list):
            normalized_items = [str(x).strip() for x in items if str(x).strip()]
        elif isinstance(items, str) and items.strip():
            normalized_items = [items.strip()]

        list_html = "".join(f"<li>{html.escape(x)}</li>" for x in normalized_items)
        if not list_html:
            list_html = "<li class='analysis-empty'>暂无可展示内容</li>"

        col.markdown(
            f"""
            <div class="analysis-module-card">
                <div class="analysis-module-head">
                    <span class="analysis-module-icon">{html.escape(icon)}</span>
                    <span>{html.escape(title)}</span>
                </div>
                <ul class="analysis-module-list">{list_html}</ul>
            </div>
            """,
            unsafe_allow_html=True,
        )

def get_active_resume_text(cfg):
    resume_text = ""
    active_id = cfg.get("active_resume_id", "")
    if active_id:
        active_resume = get_resume(active_id)
        if active_resume:
            resume_text = active_resume.get("text", "")
    if not resume_text:
        resume_text = cfg.get("resume_text", "")
    return resume_text


@st.dialog("AI 模拟面试官 / Mock Interview")
def show_mock_interview_dialog(idx, row):
    prof_name = row.get("导师/教授", tr("未知导师", "Unknown Professor"))
    univ_name = row.get("学校名称", tr("未知学校", "Unknown University"))
    direction = row.get("研究方向", tr("未明确", "Not specified"))
    hp = row.get("主页链接", "")
    state_key = f"mock_interview_chat_{idx}"
    input_key_prefix = f"mock_interview_input_{idx}"
    input_nonce_key = f"mock_interview_input_nonce_{idx}"
    eval_key = f"mock_interview_eval_{idx}"
    ended_key = f"mock_interview_ended_{idx}"

    cfg = load_config()
    ai_cfg = _ai_cfg_with_app_lang(cfg)
    resume_text = get_active_resume_text(cfg)
    if not resume_text:
        st.warning(tr("请先在【我的简历】上传并设置当前使用简历，再进行模拟面试。",
                      "Please upload and set an active resume before starting mock interview."))
        return

    homepage_text = get_homepage_text_excerpt(hp, limit=3000) if hp else ""
    papers_for_mark = row.get("最近论文", [])
    if not isinstance(papers_for_mark, list):
        papers_for_mark = []

    def _save_high_frequency_point(question_text):
        q_text = str(question_text or "").strip()
        if not q_text:
            return False, tr("问题内容为空。", "Question is empty.")
        with st.spinner(tr("AI 正在生成该考察点的建议回答...", "AI is generating suggested answer...")):
            ok_hf, hf_payload, hf_raw = generate_high_frequency_answer(
                question=q_text,
                prof_name=prof_name,
                univ_name=univ_name,
                research_direction=direction,
                homepage_url=hp,
                homepage_text=homepage_text,
                papers=papers_for_mark,
                resume_text=resume_text,
                config=ai_cfg,
            )
        if not ok_hf:
            return False, f"生成建议回答失败：{hf_raw}"

        db = load_db()
        if not (0 <= int(idx) < len(db)):
            return False, tr("未找到对应导师记录。", "Target professor record not found.")

        points = db[idx].get("高频考察点", [])
        if not isinstance(points, list):
            points = []

        existed = False
        for item in points:
            if isinstance(item, dict) and str(item.get("question", "")).strip() == q_text:
                item["ai_answer"] = hf_payload.get("suggested_answer", "")
                item["key_points"] = hf_payload.get("key_points", [])
                item["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                existed = True
                break
        if not existed:
            points.append(
                {
                    "question": q_text,
                    "ai_answer": hf_payload.get("suggested_answer", ""),
                    "key_points": hf_payload.get("key_points", []),
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        db[idx]["高频考察点"] = points
        save_db(db)
        return True, tr("已标记为高频考察点，并生成 AI 建议回答。", "Marked as high-frequency question and generated AI answer.")

    if state_key not in st.session_state:
        opening = (
            tr(
                f"你好，我是 {univ_name} 的 {prof_name}。请你先用 1-2 分钟做自我介绍，并说明你与我们课题组的匹配点。",
                f"Hi, I'm {prof_name} from {univ_name}. Please introduce yourself in 1-2 minutes and explain your fit with our group."
            )
        )
        st.session_state[state_key] = [{"role": "interviewer", "content": opening}]
        st.session_state[eval_key] = None
        st.session_state[ended_key] = False
        st.session_state[input_nonce_key] = 0

    if input_nonce_key not in st.session_state:
        st.session_state[input_nonce_key] = 0

    chat = st.session_state.get(state_key, [])
    ended = bool(st.session_state.get(ended_key, False))

    st.caption(tr(f"导师：{prof_name} | 学校：{univ_name} | 方向：{direction}",
                  f"Professor: {prof_name} | University: {univ_name} | Direction: {direction}"))
    if hp:
        st.caption(tr(f"主页：{hp}", f"Homepage: {hp}"))

    chat_holder = st.empty()
    input_holder = st.empty()
    live_reply_placeholder = None

    def render_chat_area(chat_items):
        nonlocal live_reply_placeholder
        with chat_holder.container():
            st.markdown(f"#### {tr('对话记录', 'Conversation')}")
            for turn_idx, turn in enumerate(chat_items):
                if turn.get("role") == "candidate":
                    st.markdown(f"**{tr('你：', 'You:')}** {turn.get('content', '')}")
                else:
                    q_text = str(turn.get("content", "")).strip()
                    q_col, mark_col = st.columns([8.8, 1.2])
                    with q_col:
                        st.markdown(f"**{tr('面试官：', 'Interviewer:')}** {q_text}")
                    with mark_col:
                        if st.button(tr("标记", "Mark"), key=f"mock_mark_hf_{idx}_{turn_idx}", help=tr("标记为高频考察点", "Mark as high-frequency question")):
                            ok_mark, msg_mark = _save_high_frequency_point(q_text)
                            if ok_mark:
                                st.success(msg_mark)
                            else:
                                st.error(msg_mark)
            live_reply_placeholder = st.empty()

    def render_input_area(is_ended):
        current_key = f"{input_key_prefix}_{st.session_state.get(input_nonce_key, 0)}"
        with input_holder.container():
            if is_ended:
                st.info(tr("本场模拟已结束。你可以查看评分，或点击“重新开始”。",
                           "This mock interview has ended. You can review scores or click restart."))
            else:
                st.text_area(
                    tr("你的回答", "Your Answer"),
                    key=current_key,
                    height=120,
                    placeholder=tr("请输入你的回答，尽量具体，给出证据和方法细节。", "Write your answer with concrete methods and evidence."),
                )
        return current_key

    render_chat_area(chat)
    current_input_key = render_input_area(ended)

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button(tr("发送回答", "Send Answer"), key=f"mock_send_{idx}", use_container_width=True, disabled=ended):
            answer = str(st.session_state.get(current_input_key, "")).strip()
            if not answer:
                st.warning(tr("请先输入你的回答。", "Please enter your answer first."))
            else:
                chat.append({"role": "candidate", "content": answer})
                live_chat_prefix = f"**{tr('你：', 'You:')}** {answer}\n\n"
                live_reply_placeholder.markdown(live_chat_prefix)
                st.session_state[input_nonce_key] = int(st.session_state.get(input_nonce_key, 0)) + 1
                next_input_key = f"{input_key_prefix}_{st.session_state[input_nonce_key]}"
                st.session_state[next_input_key] = ""
                render_input_area(False)
                with st.spinner(tr("面试官正在追问...", "Interviewer is generating follow-up...")):
                    ok, reply, raw = generate_mock_interview_turn(
                        prof_name=prof_name,
                        univ_name=univ_name,
                        research_direction=direction,
                        homepage_url=hp,
                        homepage_text=homepage_text,
                        resume_text=resume_text,
                        conversation=chat,
                        config=ai_cfg,
                    )
                if ok:
                    rendered = ""
                    for ch in str(reply):
                        rendered += ch
                        live_reply_placeholder.markdown(
                            live_chat_prefix + f"**{tr('面试官：', 'Interviewer:')}** {rendered}▌"
                        )
                        time.sleep(0.012)
                    live_reply_placeholder.markdown(
                        live_chat_prefix + f"**{tr('面试官：', 'Interviewer:')}** {rendered}"
                    )
                    chat.append({"role": "interviewer", "content": reply})
                    st.session_state[state_key] = chat
                else:
                    live_reply_placeholder.markdown(live_chat_prefix)
                    st.error(tr(f"生成失败：{raw}", f"Generation failed: {raw}"))

    with c2:
        if st.button(tr("结束面试并评分", "End & Score"), key=f"mock_end_{idx}", use_container_width=True):
            candidate_turns = [x for x in chat if x.get("role") == "candidate"]
            if len(candidate_turns) < 1:
                st.warning(tr("请至少回答 1 轮后再结束评分。", "Answer at least one round before scoring."))
            else:
                with st.spinner(tr("面试官正在评分...", "Scoring interview...")):
                    ok, result, raw = evaluate_mock_interview_session(
                        prof_name=prof_name,
                        univ_name=univ_name,
                        research_direction=direction,
                        resume_text=resume_text,
                        conversation=chat,
                        config=ai_cfg,
                    )
                if ok:
                    st.session_state[eval_key] = result
                    st.session_state[ended_key] = True
                else:
                    st.error(tr(f"评分失败：{raw}", f"Scoring failed: {raw}"))

    with c3:
        if st.button(tr("重新开始", "Restart"), key=f"mock_reset_{idx}", use_container_width=True):
            opening = (
                tr(
                    f"你好，我是 {univ_name} 的 {prof_name}。请你先用 1-2 分钟做自我介绍，并说明你与我们课题组的匹配点。",
                    f"Hi, I'm {prof_name} from {univ_name}. Please introduce yourself in 1-2 minutes and explain your fit with our group."
                )
            )
            for k in list(st.session_state.keys()):
                if str(k).startswith(f"{input_key_prefix}_"):
                    st.session_state.pop(k, None)
            st.session_state[state_key] = [{"role": "interviewer", "content": opening}]
            st.session_state[eval_key] = None
            st.session_state[ended_key] = False
            st.session_state[input_nonce_key] = 0
            render_chat_area(st.session_state[state_key])
            next_input_key = f"{input_key_prefix}_{st.session_state[input_nonce_key]}"
            st.session_state[next_input_key] = ""
            render_input_area(False)

    eval_result = st.session_state.get(eval_key)
    if isinstance(eval_result, dict) and eval_result:
        st.markdown(f"#### {tr('面试评分结果', 'Interview Score')}")
        score = eval_result.get("overall_score", 0)
        tendency = eval_result.get("admission_tendency", tr("待定", "Pending"))
        summary = eval_result.get("summary", "")
        dims = eval_result.get("dimension_scores", {}) if isinstance(eval_result.get("dimension_scores", {}), dict) else {}
        d1, d2, d3, d4, d5 = st.columns(5)
        d1.metric(tr("综合分", "Overall"), score)
        d2.metric(tr("录取倾向", "Admission"), tendency)
        d3.metric(tr("匹配度", "Fit"), dims.get("research_fit", "-"))
        d4.metric(tr("方法深度", "Method"), dims.get("method_depth", "-"))
        d5.metric(tr("表达能力", "Communication"), dims.get("communication", "-"))
        if summary:
            st.info(summary)

        strengths = eval_result.get("strengths", [])
        weaknesses = eval_result.get("weaknesses", [])
        improvements = eval_result.get("improvements", [])
        render_analysis_modules(
            tr("面试复盘", "Interview Review"),
            [
                ("", tr("表现亮点", "Strengths"), strengths),
                ("", tr("主要短板", "Weaknesses"), weaknesses),
                ("", tr("改进建议", "Improvements"), improvements),
            ],
        )


@st.dialog("导师档案及邮件记录")
def show_professor_details(row):
    st.markdown(f"#### {row.get('导师/教授', _ui_local('未知导师', 'Unknown'))} | {row.get('学校名称', _ui_local('未知学校', 'Unknown'))}")
    c1, c2, c3 = st.columns(3)
    with c1: st.write(f"**{row.get('推荐级', '-')}**")
    with c2: st.write(f"**{row.get('院系', '-')}**")
    with c3: st.write(f"**{row.get('阶段', '-')}**")

    stage_options = ["未联系", "已发首封邮件", "收到积极回复", "收到中等回复", "收到消极回复",
                     "面试预约阶段", "面试结束阶段", "口头offer", "终止状态"]
    current_stage = str(row.get("阶段", "未联系"))
    selected_stage = st.selectbox(
        _ui_local("修改当前状态", "Change current stage"),
        stage_options,
        index=stage_options.index(current_stage) if current_stage in stage_options else 0,
        key=f"details_stage_{getattr(row, 'name', 'prof')}",
    )
    if st.button(_ui_local("保存状态", "Save stage"), type="primary", key=f"details_stage_save_{getattr(row, 'name', 'prof')}"):
        db = load_db()
        prof_name = str(row.get("导师/教授", ""))
        university = str(row.get("学校名称", ""))
        target_idx = next((i for i, record in enumerate(db)
                           if str(record.get("导师/教授", "")) == prof_name
                           and str(record.get("学校名称", "")) == university), None)
        if target_idx is None:
            st.error(_ui_local("导师记录不存在，请刷新后重试。", "Professor record not found. Refresh and retry."))
        else:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            old_stage = str(db[target_idx].get("阶段", "未联系"))
            db[target_idx]["阶段"] = selected_stage
            db[target_idx]["更新时间"] = now_str
            if selected_stage == "已发首封邮件" and old_stage != selected_stage:
                db[target_idx]["首封邮件时间"] = now_str
            save_db(db)
            st.session_state["db_edit_toast"] = _ui_local("状态已更新", "Stage updated")
            st.rerun()

    st.write(f"**{_ui_local('研究方向', 'Research')}:** `{row.get('研究方向', _ui_local('未明确', 'N/A'))}`")

    if row.get("导师邮箱"):
        st.write(f"**{_ui_local('联系邮箱', 'Email')}:** `{row.get('导师邮箱')}`")

    if row.get('主页链接'):
        st.markdown(f"**{_ui_local('个人主页', 'Homepage')}:** [{row.get('主页链接')}]({row.get('主页链接')})")

    _llm = clean_note(row.get('LLM摘要', ''))
    if _llm:
        st.markdown(
            f"<div style='margin:.4rem 0;padding:.5rem .65rem;border-left:3px solid #60a5fa;"
            f"background:rgba(96,165,250,.10);border-radius:6px;font-size:14px;color:#bfdbfe;"
            f"white-space:pre-wrap;'><b>LLM Summary</b> · {html.escape(_llm)}</div>",
            unsafe_allow_html=True,
        )

    _note = clean_note(row.get('备注', ''))
    if _note:
        st.markdown(
            f"<div style='margin:.4rem 0;padding:.5rem .65rem;border-left:3px solid #a78bfa;"
            f"background:rgba(167,139,250,.10);border-radius:6px;font-size:14px;color:#d6cffb;"
            f"white-space:pre-wrap;'>{html.escape(_note)}</div>",
            unsafe_allow_html=True,
        )

    st.markdown("---")
    
    prof_email = row.get("导师邮箱")
    email_id = row.get("关联邮件ID")
    prof_name = row.get("导师/教授", "")

    # 合并 IMAP 缓存邮件 + Lite 手动邮件
    cached_ok, cached = get_cached_emails(limit=2000)
    all_emails = (cached if cached_ok and cached else []) + load_lite_emails()

    pe_clean = prof_email.strip().lower() if prof_email else ""
    manual_ids = set(x.strip() for x in str(email_id or "").split(",") if x.strip() and x.strip() != "None")

    thread_mails = []
    thread_mail_ids = set()
    for m in all_emails:
        mid = str(m.get("id", ""))
        matched = False
        if pe_clean and (pe_clean in str(m.get("from", "")).lower() or pe_clean in str(m.get("to", "")).lower()):
            matched = True
        if mid and mid in manual_ids:
            matched = True
        if prof_name and str(m.get("linked_prof", "")) == prof_name:
            matched = True
        if matched and (not mid or mid not in thread_mail_ids):
            thread_mails.append(m)
            if mid:
                thread_mail_ids.add(mid)

    # 按时间正序（最旧在上）
    from email.utils import parsedate_to_datetime

    def _mdt(m):
        try:
            dt = parsedate_to_datetime(m.get("date", ""))
            if dt is not None:
                return dt.timestamp()
        except Exception:
            pass
        return 0.0
    thread_mails.sort(key=_mdt)

    cat_tag = {1: "已发送套磁信", 2: "积极回复", 3: "消极回复", 4: "中立回复", 5: "面试预约", 6: "面试结束", 7: "口头Offer"}
    if thread_mails:
        st.markdown(f"##### {_ui_local('往来邮件记录', 'Correspondence')} ({len(thread_mails)})")
        for idx, tm in enumerate(thread_mails):
            tag = cat_tag.get(tm.get("phd_category"), "")
            header = f"{tm.get('date', '')}  ·  {tag}" if tag else f"{tm.get('date', '')}"
            with st.expander(header, expanded=(idx == len(thread_mails) - 1)):
                st.caption(f"**From:** `{tm.get('from', '')}`  |  **To:** `{tm.get('to', '')}`")
                body = tm.get("body", "") or ""
                if body.strip():
                    st.text_area("body", body, height=160, disabled=True, label_visibility="collapsed",
                                 key=f"thread_{prof_name}_{tm.get('id')}_{idx}")
    else:
        st.info(_ui_local("该导师暂无往来邮件记录。", "No correspondence for this professor yet."))

# ==========================================
# 主界面构建
# ==========================================
def main():
    ui = lambda zh, en: en if st.session_state.get("app_lang", "zh-CN") == "en" else zh

    # ==== 国际化语言切换 (右上角) ====
    if "app_lang" not in st.session_state:
        st.session_state["app_lang"] = "zh-CN"
    if st.session_state.get("app_lang") not in ("zh-CN", "en"):
        st.session_state["app_lang"] = "zh-CN"

    # 导师库去重（同名+同校自动合并）
    dedupe_db_inplace()

    # 一次性迁移：批量导入的旧「备注」→「LLM摘要」
    migrate_notes_to_llm_summary()

    # 侧边栏导航
    with st.sidebar:
        st.markdown(
            "<div class='phd-brand'>PhD<span class='phd-brand-accent'>Hub</span></div>",
            unsafe_allow_html=True,
        )
        st.caption(ui("AI 智能博士申请辅助系统", "AI-Powered PhD Application Assistant"))

        lang_options = {"zh-CN": "中文", "en": "English"}
        st.selectbox(ui("语言 / Language", "语言 / Language"),
                     options=list(lang_options.keys()),
                     format_func=lambda x: lang_options[x],
                     key="app_lang")

        # Lite 轻量模式开关（记忆到本地配置）
        if "lite_mode" not in st.session_state:
            st.session_state["lite_mode"] = bool(load_config().get("lite_mode", False))

        def _on_lite_toggle():
            cfg = load_config()
            cfg["lite_mode"] = bool(st.session_state.get("lite_mode_toggle", False))
            save_config(cfg)
            st.session_state["lite_mode"] = cfg["lite_mode"]

        st.toggle(
            ui("Lite 轻量模式", "Lite Mode"),
            value=st.session_state["lite_mode"],
            key="lite_mode_toggle",
            on_change=_on_lite_toggle,
            help=ui("仅保留套瓷看板、邮件记录与导师库，关闭邮箱与 AI 功能。数据仅保存在本地以保障隐私。",
                    "Keeps only the Dashboard, Email Records and Professor DB; email & AI are off. Data stays local for privacy."),
        )
        lite_mode = st.session_state["lite_mode"]

        resume_menu_label = ui("我的简历", "My Resume")
        rp_menu_label = ui("我的RP", "My RP")
        settings_menu_label = ui("系统配置", "System Config")
        lite_email_label = ui("邮件记录", "Email Records")
        data_mgmt_label = ui("资料管理", "Data Management")
        templates_menu_label = ui("套瓷信模版", "Cold-Email Templates")
        review_menu_label = ui("面试回顾", "Interview Review")
        clock_menu_label = ui("世界时钟", "World Clock")
        schoollist_menu_label = ui("院校榜单", "School List")

        # 用稳定的页面 id 作为导航选项，切换语言时保持当前页面不变
        label_map = {
            "resume": resume_menu_label,
            "rp": rp_menu_label,
            "dashboard": t("menu_dashboard"),
            "email": (lite_email_label if lite_mode else t("menu_email")),
            "db": t("menu_db"),
            "templates": templates_menu_label,
            "interview": t("menu_interview"),
            "review": review_menu_label,
            "clock": clock_menu_label,
            "schoollist": schoollist_menu_label,
            "settings": settings_menu_label,
            "data": data_mgmt_label,
        }
        if lite_mode:
            page_ids = ["dashboard", "email", "db", "templates", "review", "clock", "schoollist", "settings", "data"]
        else:
            page_ids = ["resume", "rp", "dashboard", "email", "db", "templates", "interview", "review", "clock", "schoollist", "settings"]

        # 清理早期版本可能残留的旧导航 key（其值可能是标签而非页面 id）
        for _stale in ("nav_radio_lite", "nav_radio_full"):
            st.session_state.pop(_stale, None)

        cur_id = st.session_state.get("nav_page_id", page_ids[0])
        if cur_id not in page_ids:
            cur_id = page_ids[0]
        # 不传 key：用稳定的页面 id 作选项 + index 控制选中，切换语言不会重置当前页
        menu_id = st.radio(
            t("nav_menu"), page_ids, index=page_ids.index(cur_id),
            format_func=lambda pid: label_map.get(pid, pid),
        )
        if menu_id not in label_map:
            menu_id = cur_id
        st.session_state["nav_page_id"] = menu_id
        menu = label_map[menu_id]

    # 主体内容
    if menu == resume_menu_label:
        st.title(ui("我的简历", "My Resume"))
        st.markdown(f"<p style='color: #9b9ba3; margin-bottom: 1rem;'>{ui('上传 PDF 后将自动保存、自动设为当前简历并自动分析。', 'After PDF upload, it is saved automatically, set as active, and analyzed by AI.')}</p>", unsafe_allow_html=True)

        cfg = load_config()
        resumes = list_resumes()
        preview_resume = None
        if resumes:
            preview_idx = st.session_state.get("resume_pick_idx", 0)
            if not isinstance(preview_idx, int):
                preview_idx = 0
            preview_idx = max(0, min(preview_idx, len(resumes) - 1))
            preview_resume = resumes[preview_idx]

        resume_left, resume_right = st.columns([4.8, 5.2])
        with resume_left:
            upload_file = st.file_uploader(ui("上传 PDF 简历", "Upload Resume PDF"), type=["pdf"], accept_multiple_files=False, key="resume_auto_uploader")
            st.markdown(f"#### {ui('已上传简历', 'Uploaded Resumes')}")
            if not resumes:
                st.info(ui("还没有简历，请先上传。", "No resumes yet. Upload one to get started."))
            else:
                def _resume_label(i):
                    fn = str(resumes[i].get("filename", "简历"))
                    return fn if len(fn) <= 28 else (fn[:25] + "...")

                c_pick, c_del = st.columns([3.2, 1.3])
                with c_pick:
                    idx = st.selectbox(
                        ui("选择简历", "Select Resume"),
                        range(len(resumes)),
                        format_func=_resume_label,
                        key="resume_pick_idx",
                        label_visibility="collapsed",
                    )
                sel = resumes[idx]
                rid = sel.get("id")

                with c_del:
                    st.markdown("<div style='height:2px'></div>", unsafe_allow_html=True)
                    if st.button(ui("删除", "Delete"), key=f"resume_del_icon_{rid}", help=ui("删除这份简历", "Delete this resume"), type="primary", use_container_width=True):
                        st.session_state["resume_del_pending_id"] = rid

                if st.session_state.get("resume_del_pending_id") == rid:
                    st.warning(ui(f"确认删除：{sel.get('filename', '简历')} ?", f"Confirm deletion: {sel.get('filename', 'Resume')} ?"))
                    d1, d2 = st.columns(2)
                    with d1:
                        if st.button(ui("取消", "Cancel"), key=f"resume_del_cancel_{rid}", use_container_width=True):
                            st.session_state.pop("resume_del_pending_id", None)
                            st.rerun()
                    with d2:
                        if st.button(ui("确认删除", "Confirm Delete"), key=f"resume_del_confirm_{rid}", use_container_width=True, type="primary"):
                            delete_resume(rid)
                            cache_after_del = cfg.get("resume_analysis_cache", {})
                            if isinstance(cache_after_del, dict):
                                cache_after_del.pop(str(rid), None)
                                cfg["resume_analysis_cache"] = cache_after_del
                            if cfg.get("active_resume_id") == rid:
                                cfg["active_resume_id"] = ""
                                cfg["resume_text"] = ""
                                cfg["resume_filename"] = ""
                                cfg["resume_analysis"] = {}
                                save_config(cfg)
                            st.session_state.pop("resume_del_pending_id", None)
                            st.success(ui("已删除。", "Deleted."))
                            st.rerun()
        with resume_right:
            st.markdown(f"#### {ui('简历缩略图', 'Resume Thumbnail')}")
            if resumes:
                thumb_idx = st.session_state.get("resume_pick_idx", 0)
                if not isinstance(thumb_idx, int):
                    thumb_idx = 0
                thumb_idx = max(0, min(thumb_idx, len(resumes) - 1))
                thumb_resume = resumes[thumb_idx]
                preview_resume_id = thumb_resume.get("id", "")
                preview_resume_path = thumb_resume.get("path", "")
                thumb_w = 150
                thumb_png = build_pdf_thumbnail_png(pdf_path=preview_resume_path, width=thumb_w) if preview_resume_path else b""
                if thumb_png:
                    thumb_b64 = base64.b64encode(thumb_png).decode("utf-8")
                    st.markdown(
                        f"""
                        <a href="?resume_preview={preview_resume_id}" style="text-decoration:none;">
                            <img src="data:image/png;base64,{thumb_b64}" style="width:{thumb_w}px;height:190px;object-fit:contain;background:#fff;border:1px solid #ddd;border-radius:8px;cursor:pointer;display:block;" />
                        </a>
                        """,
                        unsafe_allow_html=True,
                    )
                    st.caption(ui("点击缩略图可打开预览", "Click thumbnail to preview"))
                else:
                    st.caption(ui("当前简历暂无可用缩略图", "No thumbnail available for this resume"))
            else:
                st.caption(ui("上传简历后显示缩略图", "Thumbnail appears after upload"))

        resume_analysis_cache = cfg.get("resume_analysis_cache", {})
        if not isinstance(resume_analysis_cache, dict):
            resume_analysis_cache = {}

        legacy_active_resume_id = str(cfg.get("active_resume_id", "") or "")
        legacy_analysis = cfg.get("resume_analysis", {})
        legacy_updated_at = cfg.get("resume_analysis_updated_at", "")
        if (
            legacy_active_resume_id
            and isinstance(legacy_analysis, dict)
            and legacy_analysis
            and legacy_active_resume_id not in resume_analysis_cache
        ):
            resume_analysis_cache[legacy_active_resume_id] = {
                "analysis": legacy_analysis,
                "updated_at": legacy_updated_at,
            }
        if upload_file is not None:
            file_bytes = upload_file.getvalue()
            file_sha = hashlib.sha1(file_bytes).hexdigest()
            if st.session_state.get("resume_last_upload_sha") != file_sha:
                with st.spinner("thinking... 正在解析并分析简历"):
                    ok, rec, err = add_resume(upload_file.name, file_bytes)
                    if ok and rec:
                        cfg["active_resume_id"] = rec.get("id")
                        cfg["resume_text"] = rec.get("text", "")
                        cfg["resume_filename"] = rec.get("filename", "")
                        cfg["resume_updated_at"] = rec.get("uploaded_at", "")
                        ai_cfg = dict(cfg)
                        ai_cfg["app_lang"] = st.session_state.get("app_lang", "zh-CN")
                        ok_a, result, raw = generate_resume_analysis(rec.get("text", ""), ai_cfg)
                        if ok_a:
                            cfg["resume_analysis"] = result
                            cfg["resume_analysis_updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            rec_id = str(rec.get("id", ""))
                            if rec_id:
                                resume_analysis_cache[rec_id] = {
                                    "analysis": result,
                                    "updated_at": cfg.get("resume_analysis_updated_at", ""),
                                }
                                cfg["resume_analysis_cache"] = resume_analysis_cache
                        save_config(cfg)
                        st.session_state["resume_last_upload_sha"] = file_sha
                    else:
                        st.error(ui(f"{upload_file.name} 保存失败：{err}", f"{upload_file.name} failed to save: {err}"))
                if ok and rec:
                    st.success(ui("上传成功", "Upload successful"))
                    st.rerun()

        if resumes:
            sel_idx = st.session_state.get("resume_pick_idx", 0)
            if not isinstance(sel_idx, int):
                sel_idx = 0
            sel_idx = max(0, min(sel_idx, len(resumes) - 1))
            sel = resumes[sel_idx]
            rid = str(sel.get("id", ""))

            selected_cache = resume_analysis_cache.get(rid, {})
            selected_analysis = {}
            selected_analysis_updated_at = ""
            if isinstance(selected_cache, dict):
                if isinstance(selected_cache.get("analysis"), dict):
                    selected_analysis = selected_cache.get("analysis", {})
                    selected_analysis_updated_at = selected_cache.get("updated_at", "")
                elif selected_cache:
                    selected_analysis = selected_cache

            if not selected_analysis and str(cfg.get("active_resume_id", "")) == rid and isinstance(cfg.get("resume_analysis", {}), dict):
                fallback_analysis = cfg.get("resume_analysis", {})
                if fallback_analysis:
                    selected_analysis = fallback_analysis
                    selected_analysis_updated_at = cfg.get("resume_analysis_updated_at", "")
                    resume_analysis_cache[rid] = {
                        "analysis": selected_analysis,
                        "updated_at": selected_analysis_updated_at,
                    }

            need_sync_active = str(cfg.get("active_resume_id", "")) != rid
            if need_sync_active:
                cfg["active_resume_id"] = rid
                cfg["resume_text"] = sel.get("text", "")
                cfg["resume_filename"] = sel.get("filename", "")
                cfg["resume_updated_at"] = sel.get("uploaded_at", "")

            if selected_analysis:
                cfg["resume_analysis"] = selected_analysis
                cfg["resume_analysis_updated_at"] = selected_analysis_updated_at

            cfg["resume_analysis_cache"] = resume_analysis_cache
            if need_sync_active or selected_analysis:
                save_config(cfg)

            pdf_path = sel.get("path", "")
            if not (preview_resume and str(preview_resume.get("id", "")) == str(rid)):
                if st.button(ui("打开简历预览", "Open Resume Preview"), key=f"resume_open_fallback_{rid}"):
                    show_resume_pdf_modal(pdf_path, sel.get("filename", "简历"))

            preview_id = st.query_params.get("resume_preview", "")
            if isinstance(preview_id, list):
                preview_id = preview_id[0] if preview_id else ""
            if str(preview_id) == str(rid):
                show_resume_pdf_modal(pdf_path, sel.get("filename", "简历"))
                try:
                    st.query_params.pop("resume_preview")
                except Exception:
                    try:
                        del st.query_params["resume_preview"]
                    except Exception:
                        pass

            analysis = selected_analysis
            if isinstance(analysis, dict) and analysis:
                render_analysis_modules(
                    ui("AI 简历分析", "AI Resume Analysis"),
                    [
                        ("", ui("申博优势", "Strengths"), analysis.get("strengths", [])),
                        ("", ui("申博劣势", "Weaknesses"), analysis.get("weaknesses", [])),
                        ("", ui("改进建议", "Improvements"), analysis.get("improvements", [])),
                    ],
                )

    elif menu == rp_menu_label:
        st.title(ui("我的RP", "My RP"))
        st.markdown(f"<p style='color: #9b9ba3; margin-bottom: 1rem;'>{ui('上传 RP PDF 后自动分析：写得好的点、缺陷、改进建议。', 'After RP PDF upload, AI analyzes strengths, issues, and improvements.')}</p>", unsafe_allow_html=True)

        cfg = load_config()
        rps = list_rps()
        preview_rp = None
        if rps:
            preview_rp_idx = st.session_state.get("rp_pick_idx", 0)
            if not isinstance(preview_rp_idx, int):
                preview_rp_idx = 0
            preview_rp_idx = max(0, min(preview_rp_idx, len(rps) - 1))
            preview_rp = rps[preview_rp_idx]

        rp_left, rp_right = st.columns([4.8, 5.2])
        with rp_left:
            rp_file = st.file_uploader(ui("上传 RP PDF", "Upload RP PDF"), type=["pdf"], accept_multiple_files=False, key="rp_auto_uploader")
            st.markdown(f"#### {ui('已上传RP', 'Uploaded RPs')}")
            if not rps:
                st.info(ui("还没有RP，请先上传。", "No RP files yet. Upload one to get started."))
            else:
                def _rp_label(i):
                    fn = str(rps[i].get("filename", "RP"))
                    return fn if len(fn) <= 28 else (fn[:25] + "...")

                c_pick, c_del = st.columns([3.2, 1.3])
                with c_pick:
                    ridx = st.selectbox(ui("选择RP", "Select RP"), range(len(rps)), format_func=_rp_label, key="rp_pick_idx", label_visibility="collapsed")
                sel_rp = rps[ridx]
                rp_id = sel_rp.get("id")

                with c_del:
                    st.markdown("<div style='height:2px'></div>", unsafe_allow_html=True)
                    if st.button(ui("删除", "Delete"), key=f"rp_del_icon_{rp_id}", help=ui("删除这份RP", "Delete this RP"), type="primary", use_container_width=True):
                        st.session_state["rp_del_pending_id"] = rp_id

                if st.session_state.get("rp_del_pending_id") == rp_id:
                    st.warning(ui(f"确认删除：{sel_rp.get('filename', 'RP')} ?", f"Confirm deletion: {sel_rp.get('filename', 'RP')} ?"))
                    d1, d2 = st.columns(2)
                    with d1:
                        if st.button(ui("取消", "Cancel"), key=f"rp_del_cancel_{rp_id}", use_container_width=True):
                            st.session_state.pop("rp_del_pending_id", None)
                            st.rerun()
                    with d2:
                        if st.button(ui("确认删除", "Confirm Delete"), key=f"rp_del_confirm_{rp_id}", use_container_width=True, type="primary"):
                            delete_rp(rp_id)
                            if cfg.get("active_rp_id") == rp_id:
                                cfg["active_rp_id"] = ""
                                cfg["rp_analysis"] = {}
                                save_config(cfg)
                            st.session_state.pop("rp_del_pending_id", None)
                            st.success(ui("已删除。", "Deleted."))
                            st.rerun()
        with rp_right:
            st.markdown(f"#### {ui('RP缩略图', 'RP Thumbnail')}")
            if rps:
                thumb_idx = st.session_state.get("rp_pick_idx", 0)
                if not isinstance(thumb_idx, int):
                    thumb_idx = 0
                thumb_idx = max(0, min(thumb_idx, len(rps) - 1))
                thumb_rp = rps[thumb_idx]
                preview_rp_id = thumb_rp.get("id", "")
                preview_rp_path = thumb_rp.get("path", "")
                rp_thumb_w = 150
                rp_thumb_png = build_pdf_thumbnail_png(pdf_path=preview_rp_path, width=rp_thumb_w) if preview_rp_path else b""
                if rp_thumb_png:
                    rp_thumb_b64 = base64.b64encode(rp_thumb_png).decode("utf-8")
                    st.markdown(
                        f"""
                        <a href="?rp_preview={preview_rp_id}" style="text-decoration:none;">
                            <img src="data:image/png;base64,{rp_thumb_b64}" style="width:{rp_thumb_w}px;height:190px;object-fit:contain;background:#fff;border:1px solid #ddd;border-radius:8px;cursor:pointer;display:block;" />
                        </a>
                        """,
                        unsafe_allow_html=True,
                    )
                    st.caption(ui("点击缩略图可打开预览", "Click thumbnail to preview"))
                else:
                    st.caption(ui("当前RP暂无可用缩略图", "No thumbnail available for this RP"))
            else:
                st.caption(ui("上传RP后显示缩略图", "Thumbnail appears after upload"))
        if rp_file is not None:
            file_bytes = rp_file.getvalue()
            file_sha = hashlib.sha1(file_bytes).hexdigest()
            if st.session_state.get("rp_last_upload_sha") != file_sha:
                with st.spinner("thinking... 正在解析并分析RP"):
                    ok, rec, err = add_rp(rp_file.name, file_bytes)
                    if ok and rec:
                        ai_cfg = dict(cfg)
                        ai_cfg["app_lang"] = st.session_state.get("app_lang", "zh-CN")
                        ok_a, result, raw = generate_rp_analysis(rec.get("text", ""), ai_cfg)
                        if ok_a:
                            cfg["active_rp_id"] = rec.get("id")
                            cfg["rp_analysis"] = result
                            cfg["rp_analysis_updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            save_config(cfg)
                        st.session_state["rp_last_upload_sha"] = file_sha
                    else:
                        st.error(ui(f"{rp_file.name} 保存失败：{err}", f"{rp_file.name} failed to save: {err}"))
                if ok and rec:
                    st.success(ui("上传成功", "Upload successful"))
                    st.rerun()

        if rps:
            rp_sel_idx = st.session_state.get("rp_pick_idx", 0)
            if not isinstance(rp_sel_idx, int):
                rp_sel_idx = 0
            rp_sel_idx = max(0, min(rp_sel_idx, len(rps) - 1))
            sel_rp = rps[rp_sel_idx]
            rp_id = sel_rp.get("id")
            rp_pdf_path = sel_rp.get("path", "")
            if not (preview_rp and str(preview_rp.get("id", "")) == str(rp_id)):
                if st.button(ui("打开RP预览", "Open RP Preview"), key=f"rp_open_fallback_{rp_id}"):
                    show_resume_pdf_modal(rp_pdf_path, sel_rp.get("filename", "RP"))

            rp_preview_id = st.query_params.get("rp_preview", "")
            if isinstance(rp_preview_id, list):
                rp_preview_id = rp_preview_id[0] if rp_preview_id else ""
            if str(rp_preview_id) == str(rp_id):
                show_resume_pdf_modal(rp_pdf_path, sel_rp.get("filename", "RP"))
                try:
                    st.query_params.pop("rp_preview")
                except Exception:
                    try:
                        del st.query_params["rp_preview"]
                    except Exception:
                        pass

            active_rp_id = cfg.get("active_rp_id", "")
            if active_rp_id:
                active_rp = get_rp(active_rp_id)
                if active_rp:
                    st.caption(ui(f"当前使用RP：{active_rp.get('filename')} | 上传时间：{active_rp.get('uploaded_at')}",
                                  f"Active RP: {active_rp.get('filename')} | Uploaded: {active_rp.get('uploaded_at')}"))

            rp_analysis = cfg.get("rp_analysis", {})
            if isinstance(rp_analysis, dict) and rp_analysis:
                rp_strengths = rp_analysis.get("good_points", []) or rp_analysis.get("strengths", [])
                rp_weaknesses = rp_analysis.get("weaknesses", [])
                rp_improvements = rp_analysis.get("improvements", []) or rp_analysis.get("suggestions", [])
                render_analysis_modules(
                    ui("AI RP 分析", "AI RP Analysis"),
                    [
                        ("", ui("优点", "Strengths"), rp_strengths),
                        ("", ui("缺点", "Weaknesses"), rp_weaknesses),
                        ("", ui("改进建议", "Improvements"), rp_improvements),
                    ],
                )

    elif menu == t("menu_dashboard"):
        st.title(ui("套瓷进度大盘", "Outreach Dashboard"))
        st.markdown(f"<p style='color: #9b9ba3; margin-bottom: 2rem;'>{ui('可视化管理你与各大院校导师的沟通时间线。', 'Visualize and manage your communication timeline with professors.')}</p>", unsafe_allow_html=True)
        
        recent_stats = get_recent_7d_email_stats()
        total_stats = get_total_email_stats()
        recent_scheduled_count = get_recent_7d_scheduled_interviews_count()
        interview_scheduled_total = len([r for r in load_db() if r.get("阶段") == "面试预约阶段"])

        def _stat_box(label, items):
            spans = "".join(f"<span style='color:{c}'>{txt} {val}</span>" for txt, val, c in items)
            return (
                "<div class='reply-box'>"
                f"<div class='reply-box-label'>{label}</div>"
                f"<div class='reply-box-nums'>{spans}</div>"
                "</div>"
            )

        def _outreach_box(stats, scheduled):
            return _stat_box(ui("套瓷进度", "Outreach"), [
                (ui("已发送", "Sent"), stats["sent_inquiry"], "#a78bfa"),
                (ui("已回复", "Replied"), stats["replied_total"], "#ececee"),
                (ui("面试", "Interview"), scheduled, "#56d197"),
            ])

        def _reply_box(stats):
            return _stat_box(ui("回复分布", "Reply breakdown"), [
                (ui("积极", "Pos"), stats["positive_reply"], "#56d197"),
                (ui("中立", "Neu"), stats["neutral_reply"], "#ececee"),
                (ui("消极", "Neg"), stats["negative_reply"], "#f87171"),
            ])

        col_recent, col_total = st.columns(2)

        with col_recent:
            st.markdown(f"### {ui('最近 7 天套瓷沟通指标', 'Last 7 Days Outreach Metrics')}")
            rc = st.columns(2)
            with rc[0]:
                st.markdown(_outreach_box(recent_stats, recent_scheduled_count), unsafe_allow_html=True)
            with rc[1]:
                st.markdown(_reply_box(recent_stats), unsafe_allow_html=True)

        with col_total:
            st.markdown(f"### {ui('累计套瓷沟通指标', 'All-Time Outreach Metrics')}")
            ac = st.columns(2)
            with ac[0]:
                st.markdown(_outreach_box(total_stats, interview_scheduled_total), unsafe_allow_html=True)
            with ac[1]:
                st.markdown(_reply_box(total_stats), unsafe_allow_html=True)
        st.divider()
        
        # 加载数据
        df = get_dashboard_data()
        
        if df.empty:
            st.info(t("dashboard_empty"))
        else:
            import plotly.express as px
            
            # Prepare data
            if "创建时间" not in df.columns:
                df["创建时间"] = df.get("更新时间", datetime.now().strftime("%Y-%m-%d"))
                
            # Date calculations
            today = datetime.now()
            seven_days_ago = today - timedelta(days=7)
            
            df['创建日期'] = pd.to_datetime(df['创建时间']).dt.date
            recent_df = df[df['创建日期'] > seven_days_ago.date()]
            
            # Chart 1: 7-day creations
            daily_creates = recent_df.groupby('创建日期').size().reset_index(name='发信数量')
            # Fill missing dates
            date_range = pd.date_range(end=today.date(), periods=7).date
            daily_creates = daily_creates.set_index('创建日期').reindex(date_range, fill_value=0).reset_index()
            daily_creates.rename(columns={'创建日期': '日期'}, inplace=True)
            if 'index' in daily_creates.columns:
                daily_creates.rename(columns={'index': '日期'}, inplace=True)
            
            fig_bar = px.bar(daily_creates, x='日期', y='发信数量', title="最近 7 天套瓷发送数量 (按创建时间)",
                             color_discrete_sequence=['#8e6bef'], text='发信数量')
            fig_bar.update_layout(xaxis_title="日期", yaxis_title="发信数量", plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                                  font=dict(color='#ececee'))
            
            # Chart 2: Global Map
            import pycountry
            # Define country mapping helper
            def get_iso3(country_name):
                try:
                    c = pycountry.countries.search_fuzzy(country_name)
                    return c[0].alpha_3
                except:
                    # fallback explicit maps
                    mapping = {"United States": "USA", "USA": "USA", "United Kingdom": "GBR", "UK": "GBR", 
                               "China": "CHN", "Hong Kong": "HKG", "Singapore": "SGP", "Canada": "CAN", 
                               "Australia": "AUS"}
                    for k, v in mapping.items():
                        if k.lower() in str(country_name).lower(): return v
                    return None
            
            df['iso_alpha'] = df['国家/地区'].apply(get_iso3)
            # 地图只统计已进入套瓷流程（已发送）的导师，未联系的目标不计入
            map_src = df[df['阶段'].astype(str) != '未联系'] if '阶段' in df.columns else df
            map_recent = map_src[map_src['创建日期'] > seven_days_ago.date()]

            c_chart1, c_chart2 = st.columns(2)
            with c_chart1:
                st.plotly_chart(fig_bar, use_container_width=True)
            with c_chart2:
                if map_src.empty:
                    st.info(ui("还没有已发送套瓷的导师，地图暂无数据。",
                               "No professors contacted yet — map has no data."))
                else:
                    country_stats = map_src.groupby(['国家/地区', 'iso_alpha']).size().reset_index(name='总套瓷数')
                    recent_country_stats = map_recent.groupby(['国家/地区', 'iso_alpha']).size().reset_index(name='本周新增')

                    map_df = pd.merge(country_stats, recent_country_stats, on=['国家/地区', 'iso_alpha'], how='left').fillna(0)
                    map_df['hover_text'] = map_df['国家/地区'] + "<br>总套瓷数: " + map_df['总套瓷数'].astype(str) + "<br>本周新增: " + map_df['本周新增'].astype(str)

                    import plotly.graph_objects as go
                    fig_map = px.choropleth(map_df, locations="iso_alpha",
                                            color="总套瓷数", hover_name="hover_text",
                                            color_continuous_scale=[[0, '#1d1d22'], [0.5, '#473b70'], [1, '#8e6bef']],
                                            range_color=[1, 100],
                                            title="全球套瓷地区分布图（仅已发送）")

                    # 为所有有 ISO 编码的地区都打上气泡点，确保香港/新加坡等小面积地区也能显示
                    pt_df = map_df[map_df['iso_alpha'].notna() & (map_df['iso_alpha'].astype(str) != "")].copy()
                    if not pt_df.empty:
                        counts = pt_df['总套瓷数'].astype(float)
                        cmax = counts.max() if counts.max() > 0 else 1
                        sizes = 11 + (counts / cmax) * 20  # 11~31，按套瓷数缩放
                        fig_map.add_trace(go.Scattergeo(
                            locations=pt_df['iso_alpha'],
                            text=pt_df['国家/地区'] + " (" + pt_df['总套瓷数'].astype(int).astype(str) + ")",
                            mode='markers+text',
                            marker=dict(size=sizes, color='#a78bfa', opacity=0.9,
                                        line=dict(width=1.5, color='#ececee')),
                            textfont=dict(color='#ececee', size=12),
                            textposition="top center",
                            hoverinfo="text",
                            showlegend=False
                        ))

                    fig_map.update_layout(geo=dict(showframe=False, showcoastlines=True, bgcolor='rgba(0,0,0,0)',
                                                   landcolor='#16161a', lakecolor='#0c0c0d', coastlinecolor='#26262c',
                                                   showland=True, showocean=True, oceancolor='#0c0c0d'),
                                          paper_bgcolor='rgba(0,0,0,0)', font=dict(color='#ececee'))
                    st.plotly_chart(fig_map, use_container_width=True)

            st.divider()
        
        st.subheader(t("active_applications"))

        # 看板只展示已进入套瓷流程的导师；未联系的仅保留在导师库
        if "阶段" in df.columns:
            active_df = df[df["阶段"].astype(str) != "未联系"]
        else:
            active_df = df

        # 大洲 / 国家 / 阶段 筛选
        all_label = ui("全部", "All")
        # 活跃流程的阶段（不含「未联系」），并提供「面试环节」等快捷分组
        ACTIVE_STAGES = ["已发首封邮件", "收到积极回复", "收到中等回复", "收到消极回复",
                         "面试预约阶段", "面试结束阶段", "口头offer", "终止状态"]
        STAGE_GROUPS = {
            ui("📨 首封邮件", "📨 First email"): ["已发首封邮件"],
            ui("💬 收到回复", "💬 Got reply"): ["收到积极回复", "收到中等回复", "收到消极回复"],
            ui("🎤 面试环节", "🎤 Interview"): ["面试预约阶段", "面试结束阶段"],
            ui("🎉 口头 offer", "🎉 Verbal offer"): ["口头offer"],
            ui("终止", "Terminated"): ["终止状态"],
        }
        STAGE_EN = {
            "已发首封邮件": "First email sent", "收到积极回复": "Positive reply",
            "收到中等回复": "Neutral reply", "收到消极回复": "Negative reply",
            "面试预约阶段": "Interview scheduled", "面试结束阶段": "Interview done",
            "口头offer": "Verbal offer",
            "终止状态": "Terminated",
        }
        stage_options = [all_label] + list(STAGE_GROUPS.keys()) + ACTIVE_STAGES

        def _stage_fmt(s):
            if s == all_label or s in STAGE_GROUPS:
                return s
            return STAGE_EN.get(s, s) if st.session_state.get("app_lang") == "en" else s

        fcol1, fcol2, fcol3, _fc = st.columns([1, 1, 1.3, 2.7])
        with fcol1:
            cont_sel = st.selectbox(ui("大洲", "Continent"), [all_label] + list(CONTINENTS.keys()), key="dash_cont")
        with fcol2:
            if cont_sel != all_label:
                country_pool = list(CONTINENTS.get(cont_sel, []))
            else:
                country_pool = sorted({str(c) for c in active_df.get("国家/地区", pd.Series(dtype=str)).tolist() if str(c).strip()})
            country_sel = st.selectbox(ui("国家 / 地区", "Country / Region"), [all_label] + country_pool,
                                       format_func=lambda c: COUNTRY_LABELS.get(c, c), key="dash_country")
        with fcol3:
            stage_sel = st.selectbox(ui("阶段", "Stage"), stage_options, format_func=_stage_fmt, key="dash_stage")
        if cont_sel != all_label and "国家/地区" in active_df.columns:
            active_df = active_df[active_df["国家/地区"].isin(CONTINENTS.get(cont_sel, []))]
        if country_sel != all_label and "国家/地区" in active_df.columns:
            active_df = active_df[active_df["国家/地区"].astype(str) == country_sel]
        if stage_sel != all_label and "阶段" in active_df.columns:
            wanted_stages = STAGE_GROUPS.get(stage_sel, [stage_sel])
            active_df = active_df[active_df["阶段"].astype(str).isin(wanted_stages)]

        if active_df.empty:
            st.caption(ui("暂无进行中的套瓷记录。未联系的导师可在『导师库管理』中查看。",
                          "No active outreach yet. Un-contacted professors live in the Professor DB."))

        # 为每位导师渲染卡片
        for index, row in active_df.iterrows():

            c1, c2, c3 = st.columns([1, 2, 1], vertical_alignment="center")
            with c1:
                prof_name = row.get('导师/教授', '未知导师')
                homepage = row.get('主页链接', '')
                if isinstance(homepage, str) and homepage.strip():
                    st.markdown(f"### <a href='{homepage}' target='_blank' style='text-decoration:none; color:inherit;'>{prof_name}</a>", unsafe_allow_html=True)
                else:
                    st.markdown(f"### {prof_name}")
                st.markdown(f"**{row.get('学校名称', ui('未知学校', 'Unknown'))}**")
                st.markdown(f"<span class='tag'>{row.get('推荐级', '-')}</span>", unsafe_allow_html=True)

            with c2:
                # 渲染用户要求的进度条
                render_status_bar(row.get('阶段', '未联系'), row.get('面试时间', ''))
                _waiting = outreach_waiting_state(row)
                if _waiting:
                    st.caption(ui("待 Follow up", "Follow up due") if _waiting == "follow_up"
                               else ui("14 天未回复", "No reply for 14 days"))

            with c3:
                st.markdown(f"**{ui('研究方向', 'Research')}:**<br><span style='font-size:15px; color:#d6e4ff; font-weight:600;'>{row.get('研究方向', ui('未明确', 'N/A'))}</span>", unsafe_allow_html=True)
                _llm = clean_note(row.get('LLM摘要', ''))
                if _llm:
                    st.markdown(
                        f"<div style='margin:.3rem 0;padding:.4rem .55rem;border-left:3px solid #60a5fa;"
                        f"background:rgba(96,165,250,.10);border-radius:6px;font-size:13px;color:#bfdbfe;"
                        f"white-space:pre-wrap;'>🤖 <b>LLM Summary</b> · {html.escape(_llm)}</div>",
                        unsafe_allow_html=True,
                    )
                _note = clean_note(row.get('备注', ''))
                if _note:
                    st.markdown(
                        f"<div style='margin:.3rem 0;padding:.4rem .55rem;border-left:3px solid #a78bfa;"
                        f"background:rgba(167,139,250,.10);border-radius:6px;font-size:13px;color:#d6cffb;"
                        f"white-space:pre-wrap;'>📝 {html.escape(_note)}</div>",
                        unsafe_allow_html=True,
                    )
                st.markdown(f"**{ui('最后互动', 'Last update')}:** {row.get('更新时间', '')}")
                st.markdown(f"**{ui('导师当地时间', 'Local time')}:** {format_local_time(row.get('国家/地区', '')).replace('未知', ui('未知', 'Unknown'))}")
                # 操作按钮
                btn_col1, btn_col2 = st.columns(2)
                with btn_col1:
                    if st.button(ui("查看详情", "Details"), key=f"btn_{index}", use_container_width=True):
                        show_professor_details(row)
                with btn_col2:
                    if st.button(ui("删除记录", "Delete"), key=f"del_dash_{index}", use_container_width=True, type="secondary"):
                        confirm_delete_dialog(row.get("导师/教授"), row.get("学校名称"))
            
            
    elif menu == data_mgmt_label:
        st.title(ui("资料管理", "Data Management"))
        _imp_toast = st.session_state.pop("data_import_toast", None)
        if _imp_toast:
            st.toast(_imp_toast, icon="✅")
        if st.session_state.pop("data_cleared_flag", False):
            st.toast(ui("已清空所有资料", "All data cleared"), icon="🗑️")
        st.markdown(
            f"<p style='color: #9b9ba3; margin-bottom: 1.2rem;'>"
            f"{ui('备份、导入或清空你的全部本地数据（导师库 + 邮件记录）。', 'Back up, import, or clear all your local data (Professor DB + email records).')}</p>",
            unsafe_allow_html=True,
        )

        st.subheader(ui("备份资料", "Backup"))
        st.caption(ui("一键导出全部资料为 JSON 文件（导师库 + 邮件记录）。", "Export everything as one JSON file (Professor DB + email records)."))
        backup_payload = json.dumps({
            "version": 1,
            "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "professors": load_db(),
            "emails": load_lite_emails(),
        }, ensure_ascii=False, indent=2)
        st.download_button(
            ui("备份资料（导出全部）", "Backup (export all)"),
            data=backup_payload.encode("utf-8"),
            file_name=f"phdhub_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            mime="application/json",
            type="primary",
            key="data_backup_btn",
        )

        st.caption(ui("或：点「另存为」弹出系统保存窗口，自行选择保存位置。", "Or: click 'Save as' to open a native dialog and pick the location."))
        if st.button(ui("另存为…", "Save as…"), key="data_saveas_btn"):
            import subprocess
            import sys
            initial_name = f"phdhub_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            picker_code = (
                "import tkinter as tk\n"
                "from tkinter import filedialog\n"
                "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
                "p = filedialog.asksaveasfilename(title='Save backup as', defaultextension='.json',"
                f" initialfile={initial_name!r}, filetypes=[('JSON','*.json'),('All files','*.*')])\n"
                "r.destroy()\n"
                "import sys; sys.stdout.write(p or '')\n"
            )
            try:
                with st.spinner(ui("已弹出保存窗口，请在系统对话框中选择位置…", "A save dialog has opened — pick a location…")):
                    res = subprocess.run([sys.executable, "-c", picker_code], capture_output=True, text=True, timeout=180)
                target = (res.stdout or "").strip()
                if target:
                    with open(target, "w", encoding="utf-8") as _bf:
                        _bf.write(backup_payload)
                    st.toast(ui(f"已保存到：{target}", f"Saved to: {target}"), icon="✅")
                else:
                    st.toast(ui("已取消保存", "Save canceled"))
            except Exception as e:
                st.error(ui(f"无法打开保存窗口（请改用上方下载按钮）：{e}",
                            f"Cannot open save dialog (use the download button above): {e}"))

        st.divider()
        st.subheader(ui("导入资料", "Import"))

        up = st.file_uploader(ui("选择备份文件 (JSON)", "Backup file (JSON)"), type=["json"], key="data_import_file")
        replace_label = ui("清空后导入", "Replace all")
        merge_label = ui("合并导入（保留现有）", "Merge (keep existing)")
        imp_mode = st.radio(ui("导入方式", "Import mode"), [merge_label, replace_label], horizontal=True, key="data_import_mode")
        if up is not None and st.button(ui("执行导入", "Import"), key="data_import_btn"):
            try:
                payload = json.loads(up.getvalue().decode("utf-8"))
                imp_profs = payload.get("professors", []) or []
                imp_mails = payload.get("emails", []) or []
                if imp_mode == replace_label:
                    save_db(imp_profs)
                    save_lite_emails(imp_mails)
                else:
                    merged_db, _ = dedupe_db(load_db() + imp_profs)
                    save_db(merged_db)
                    cur_mails = load_lite_emails()
                    seen_ids = {str(m.get("id")) for m in cur_mails}
                    cur_mails.extend(m for m in imp_mails if str(m.get("id")) not in seen_ids)
                    save_lite_emails(cur_mails)
                st.session_state["data_import_toast"] = ui("导入成功！", "Imported successfully!")
                st.rerun()
            except Exception as e:
                st.error(ui(f"导入失败：{e}", f"Import failed: {e}"))

        st.divider()
        st.subheader(ui("清空所有资料", "Clear all data"))
        st.caption(ui("将永久删除所有导师库与邮件记录，不可恢复。建议先备份。",
                      "Permanently deletes all professors and email records. Back up first."))
        clear_phrase = ui("确认清空", "CLEAR")
        typed = st.text_input(ui(f"输入「{clear_phrase}」以启用", f"Type '{clear_phrase}' to enable"), key="data_clear_confirm")
        if st.button(ui("清空所有资料", "Clear all data"), disabled=(typed.strip() != clear_phrase), key="data_clear_btn"):
            save_db([])
            save_lite_emails([])
            st.session_state.pop("data_clear_confirm", None)
            st.session_state["data_cleared_flag"] = True
            st.rerun()

    elif menu == lite_email_label:
        st.title(lite_email_label)
        st.markdown(
            f"<p style='color: #9b9ba3; margin-bottom: 1.5rem;'>"
            f"{ui('手动记录一封邮件并联动套瓷看板，无需邮箱授权与任何 AI 功能。', 'Log an email by hand and sync it to the dashboard — no email auth or AI needed.')}</p>",
            unsafe_allow_html=True,
        )

        _lite_saved = st.session_state.pop("lite_saved_msg", None)
        if _lite_saved:
            st.success("✓ " + _lite_saved)
            st.balloons()

        cat_to_status = {1: "已发首封邮件", 2: "收到积极回复", 3: "收到消极回复", 4: "收到中等回复", 5: "面试预约阶段", 6: "面试结束阶段", 7: "口头offer"}
        cat_options = [
            (1, ui("已发送套磁信（首次联系）", "Inquiry sent (first contact)")),
            (2, ui("收到积极回复", "Positive reply")),
            (4, ui("收到中立回复", "Neutral reply")),
            (3, ui("收到消极回复", "Negative reply")),
            (5, ui("面试预约", "Interview scheduled")),
            (6, ui("面试结束", "Interview done")),
            (7, ui("口头 Offer", "Verbal offer")),
        ]
        cat_label_map = dict(cat_options)

        lc_form, _lc_rest = st.columns([3, 2])
        with lc_form:
            st.subheader(ui("新建邮件记录", "New email record"))

            db = load_db()
            sel_idx = None
            new_prof = None
            fc_create_label = ui("新建导师", "Create new professor")
            fc_pick_label = ui("从导师库选择", "Pick from Professor DB")
            fc_mode = fc_create_label

            # 一行：邮件类型 + 导师来源 + 发件人
            sender_me = ui("我", "Me")
            sender_prof = ui("教授", "Professor")
            top_a, top_b, top_c = st.columns(3)
            with top_a:
                cat_id = st.selectbox(
                    ui("这封邮件是什么？", "What is this email?"),
                    options=[c for c, _ in cat_options],
                    format_func=lambda c: cat_label_map.get(c, str(c)),
                    key="lite_cat",
                )
            is_first_contact = (cat_id == 1)
            with top_b:
                if is_first_contact:
                    fc_mode = st.radio(ui("导师来源", "Professor source"), [fc_create_label, fc_pick_label],
                                       horizontal=True, key="lite_fc_mode")
            with top_c:
                sender = st.radio(ui("发件人", "Sender"), [sender_me, sender_prof],
                                  index=(0 if is_first_contact else 1), horizontal=True, key="lite_sender")

            if is_first_contact:
                if fc_mode == fc_create_label:
                    new_prof = lite_prof_form("lite_new", compact=True, show_contact=False)
                elif not db:
                    st.warning(ui("导师库为空，请改用『新建导师』，或先到『导师库管理』里添加。",
                                  "Professor DB is empty. Use 'Create new professor', or add one on the Professor DB page."))
                else:
                    prof_options = {
                        f"{r.get('导师/教授', '?')} ({r.get('学校名称', '?')})"
                        f" · {school_ranking_text(r.get('学校名称', ''), r.get('QS排名', ''))}"
                        f" · {r.get('阶段', '未联系')}": i for i, r in enumerate(db)
                    }
                    sel_str = st.selectbox(ui("选择导师库中的导师", "Select a professor from the DB"), list(prof_options.keys()), key="lite_fc_selprof")
                    sel_idx = prof_options[sel_str]
            else:
                if not db:
                    st.warning(ui("导师库为空，请先用『已发送套磁信』登记一位导师。",
                                  "Professor DB is empty. Add a professor via 'Inquiry sent' first."))
                else:
                    prof_options = {
                        f"{r.get('导师/教授', '?')} ({r.get('学校名称', '?')})"
                        f" · {school_ranking_text(r.get('学校名称', ''), r.get('QS排名', ''))}": i
                        for i, r in enumerate(db)
                    }
                    sel_str = st.selectbox(ui("选择已有导师", "Select existing professor"), list(prof_options.keys()), key="lite_selprof")
                    sel_idx = prof_options[sel_str]
            creating = bool(is_first_contact and fc_mode == fc_create_label)

            interview_time = ""
            if cat_id == 5:
                dft_date, dft_time = get_interview_picker_defaults("")
                ci1, ci2 = st.columns(2)
                with ci1:
                    iv_date = st.date_input(ui("面试日期", "Interview date"), value=dft_date, key="lite_ivdate")
                with ci2:
                    iv_time = st.time_input(ui("面试时间", "Interview time"), value=dft_time, key="lite_ivtime")
                interview_time = format_interview_time(iv_date, iv_time)

            st.text_area(ui("邮件正文 / 备注（选填）", "Email body / notes (optional)"), key="lite_body", height=120)

            # 一行：导师邮箱 / 主页链接 / 邮件日期（新建导师时三项同行；否则仅邮件日期）
            if creating and new_prof is not None:
                rc = st.columns(3)
                with rc[0]:
                    new_prof["email"] = st.text_input(ui("导师邮箱（选填）", "Professor email (optional)"), key="lite_new_pemail").strip()
                with rc[1]:
                    new_prof["home"] = st.text_input(ui("主页链接（选填）", "Homepage (optional)"), key="lite_new_phome").strip()
                with rc[2]:
                    lite_date = st.date_input(ui("邮件日期", "Email date"), value=datetime.now().date(), key="lite_date")
            else:
                lite_date = st.date_input(ui("邮件日期", "Email date"), value=datetime.now().date(), key="lite_date")

            if st.button(ui("保存到套瓷看板", "Save to dashboard"), type="primary", use_container_width=True, key="lite_save"):
                ok = True
                if creating:
                    if not (new_prof and new_prof["name"] and new_prof["univ"]):
                        st.error(ui("请填写导师姓名和学校名称。", "Please fill in professor name and university."))
                        ok = False
                elif sel_idx is None:
                    st.error(ui("请选择一位导师。", "Please select a professor."))
                    ok = False

                if ok:
                    from email.utils import format_datetime as _fmt_dt
                    dt_aware = datetime.combine(lite_date, datetime.now().time()).astimezone()
                    mail_date_rfc = _fmt_dt(dt_aware)
                    created_str = dt_aware.strftime("%Y-%m-%d %H:%M:%S")
                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    my_email = load_config().get("email", "") or "me"
                    mail_id = "lite-" + datetime.now().strftime("%Y%m%d%H%M%S%f")

                    if creating:
                        prof_name = new_prof["name"]
                        prof_email = new_prof["email"]
                    else:
                        sel_r = db[sel_idx]
                        prof_name = sel_r.get("导师/教授", "")
                        prof_email = sel_r.get("导师邮箱", "")

                    counter = prof_email or prof_name
                    # 发件人由用户选择：我 / 教授
                    em_from, em_to = (my_email, counter) if sender == sender_me else (counter, my_email)

                    lite_rec = {
                        "id": mail_id,
                        "subject": st.session_state.get("lite_subject", "").strip(),
                        "from": em_from,
                        "to": em_to,
                        "date": mail_date_rfc,
                        "body": st.session_state.get("lite_body", "").strip(),
                        "is_phd_related": True,
                        "phd_category": cat_id,
                        "phd_reasoning": "manual (Lite)",
                        "phd_details": {},
                        "lite_manual": True,
                        "linked_prof": prof_name,
                    }
                    lite_list = load_lite_emails()
                    lite_list.insert(0, lite_rec)
                    save_lite_emails(lite_list)

                    cur_db = load_db()
                    if creating:
                        cur_db.append({
                            "导师/教授": prof_name,
                            "导师邮箱": prof_email,
                            "国家/地区": (country_for(new_prof["univ"]) or new_prof["country"] or "未知"),
                            "学校名称": canonical_school_name(new_prof["univ"]),
                            "院系": new_prof["dept"],
                            "主页链接": new_prof["home"],
                            "研究方向": new_prof["dir"] or "未明确",
                            "推荐级": new_prof["prio"],
                            "阶段": cat_to_status[cat_id],
                            "面试时间": interview_time,
                            "更新时间": now_str,
                            "创建时间": created_str,
                            "首封邮件时间": created_str if cat_id == 1 else "",
                            "关联邮件ID": mail_id,
                            "备注": "",
                            "来源": "手动",
                            "QS排名": qs_rank_for(new_prof["univ"]),
                            "USNews排名": usnews_rank_for(new_prof["univ"]),
                        })
                        save_db(cur_db)
                        st.session_state["lite_saved_msg"] = ui(f"已登记导师 {prof_name} 并加入套瓷看板。", f"Added {prof_name} to the dashboard.")
                        for k in ["lite_new_pname", "lite_new_puniv", "lite_new_pdept", "lite_new_pdir",
                                  "lite_new_pemail", "lite_new_phome", "lite_new_country_manual", "lite_subject", "lite_body"]:
                            st.session_state.pop(k, None)
                    else:
                        cur_db[sel_idx]["阶段"] = cat_to_status[cat_id]
                        if cat_id == 1 and not cur_db[sel_idx].get("首封邮件时间"):
                            cur_db[sel_idx]["首封邮件时间"] = now_str
                        if interview_time:
                            cur_db[sel_idx]["面试时间"] = interview_time
                        cur_db[sel_idx]["更新时间"] = now_str
                        old_mid = str(cur_db[sel_idx].get("关联邮件ID", "")).strip()
                        cur_db[sel_idx]["关联邮件ID"] = f"{old_mid},{mail_id}" if old_mid and old_mid != "None" else mail_id
                        save_db(cur_db)
                        st.session_state["lite_saved_msg"] = ui(f"已更新 {prof_name} 的阶段为「{cat_to_status[cat_id]}」。", f"Updated {prof_name} -> {cat_to_status[cat_id]}.")
                        for k in ["lite_subject", "lite_body"]:
                            st.session_state.pop(k, None)
                    st.rerun()

        st.divider()
        st.subheader(ui("已有邮件记录", "Email records"))
        cat_name_map = {
            1: ui("已发送套磁信", "Inquiry sent"), 2: ui("积极回复", "Positive"), 3: ui("消极回复", "Negative"),
            4: ui("中立回复", "Neutral"), 5: ui("面试预约", "Interview scheduled"), 6: ui("面试结束", "Interview done"),
            7: ui("口头Offer", "Verbal offer"),
        }
        lite_list = load_lite_emails()
        if not lite_list:
            st.caption(ui("还没有手动邮件记录。", "No manual email records yet."))
        else:
            _db_by_prof = {str(r.get("导师/教授", "")): r for r in load_db()}
            for i, em in enumerate(lite_list):
                cc1, cc2 = st.columns([5, 1])
                with cc1:
                    prof_title = em.get("linked_prof") or ui("(未关联导师)", "(no professor)")
                    _linked_record = _db_by_prof.get(str(prof_title), {})
                    _rank_text = school_ranking_text(
                        _linked_record.get("学校名称", ""), _linked_record.get("QS排名", "")
                    )
                    _linked_school = str(_linked_record.get("学校名称", "") or "").strip()
                    st.markdown(
                        f"**{html.escape(prof_title)}**"
                        + (f" ({html.escape(_linked_school)})" if _linked_school else "")
                        + f"  ·  <span class='tag'>{cat_name_map.get(em.get('phd_category'), '?')}</span>"
                        + (f" · {html.escape(_rank_text)}" if _rank_text else ""),
                        unsafe_allow_html=True,
                    )
                    st.caption(f"{em.get('date', '')}")
                    body = (em.get("body") or "").strip()
                    if body:
                        st.caption(body[:200] + ("…" if len(body) > 200 else ""))
                with cc2:
                    if st.button(ui("删除", "Delete"), key=f"lite_del_{em.get('id', i)}", use_container_width=True):
                        save_lite_emails([x for x in load_lite_emails() if x.get("id") != em.get("id")])
                        st.rerun()
                st.divider()

    elif menu == t("menu_email"):
        st.title(t("email_center"))
        config = load_config()
        if not config.get("email") or not config.get("password"):
            st.warning(t("no_email_config"))
        else:
            col_ctrl_1, col_ctrl_2, _ = st.columns([0.85, 1.15, 4.0])
            with col_ctrl_1:
                st.caption(t("show_emails_count"))
                limit = st.selectbox(
                    t("show_emails_count"),
                    [5, 10, 15, 30, 50],
                    index=2,
                    label_visibility="collapsed",
                )
            with col_ctrl_2:
                st.markdown("<div style='height: 2.08rem;'></div>", unsafe_allow_html=True)
                if st.button(ui("手动拉取最新邮件", "Fetch Latest Emails"), use_container_width=True):
                    with st.spinner(t("fetching_info")):
                        fetch_once()
                    st.rerun()
            
            with st.spinner(t("reading_cache")):
                success, emails = get_cached_emails(limit)
                
            if not success:
                st.error(ui(f"拉取失败: {emails}", f"Fetch failed: {emails}"))
            elif not emails:
                st.info(t("inbox_empty"))
            else:
                db = load_db()
                marked_emails = {}
                for record in db:
                    if "关联邮件ID" in record and record["关联邮件ID"]:
                        for mid in str(record["关联邮件ID"]).split(","):
                            marked_emails[mid.strip()] = record

                col_list, col_mail, col_form = st.columns([1, 1.8, 1])
                
                with col_list:
                    st.subheader(t("inbox_list"))
                    
                    # Radio button layout formatting with marked icon
                    def format_email_label(idx):
                        subj = emails[idx]['subject'][:15].replace('\n', ' ')
                        m_id = emails[idx]['id']
                        if m_id in marked_emails:
                            return f"{subj}..."
                        if emails[idx].get('is_phd_related'):
                            return f"{subj}..."
                        return f"{subj}..."
                        
                    selected_idx = st.radio(t("switch_email"), range(len(emails)), format_func=format_email_label, label_visibility="collapsed", key="email_list_selector")
                
                mail = emails[selected_idx]
                mail_id = mail['id']
                
                with col_mail:
                    if mail_id in marked_emails:
                        _marked_prof = marked_emails[mail_id]
                        _marked_name = str(_marked_prof.get("导师/教授", ""))
                        _marked_school = str(_marked_prof.get("学校名称", ""))
                        _marked_ranks = school_ranking_text(
                            _marked_school, _marked_prof.get("QS排名", "")
                        )
                        _marked_summary = " · ".join(
                            value for value in (_marked_name, _marked_school, _marked_ranks) if value
                        )
                        col_alert, col_action = st.columns([4, 1.5])
                        with col_alert:
                            st.success(ui(f"**已提取！** {_marked_summary}",
                                          f"**Extracted!** {_marked_summary}"))
                        with col_action:
                            if st.button(t("cancel"), key=f"unmark_{mail_id}", use_container_width=True):
                                current_db = load_db()
                                for r in current_db:
                                    if "关联邮件ID" in r and r["关联邮件ID"]:
                                        mids = [m.strip() for m in str(r["关联邮件ID"]).split(",")]
                                        if mail_id in mids:
                                            mids.remove(mail_id)
                                            r["关联邮件ID"] = ",".join(mids)
                                new_db = current_db
                                save_db(new_db)
                                st.rerun()
                    
                    st.markdown(f"### {mail['subject']}")
                    
                    # Manual category override
                    CAT_NAMES = {
                        0: "非博士申请相关邮件 (Not PhD Related)",
                        1: "已发送询问信 (Sent Inquiry)",
                        2: "得到导师积极回复 (Positive Reply)",
                        3: "得到导师消极回复 (Negative Reply)",
                        4: "得到导师中立回复 (Neutral Reply)",
                        5: "面试预约 (Interview Scheduling)",
                        6: "面试结果告知 (Interview Result)",
                        7: "口头offer (Verbal Offer)",
                        8: "其他沟通 (Other Communication)"
                    }
                    
                    current_cat = mail.get('phd_category') if mail.get('is_phd_related') and mail.get('phd_category') in CAT_NAMES else 0
                    
                    new_cat = st.selectbox(ui("邮件分类状态", "Email Category"), 
                                           options=list(CAT_NAMES.keys()), 
                                           format_func=lambda x: CAT_NAMES[x],
                                           index=list(CAT_NAMES.keys()).index(current_cat),
                                           key=f"cat_override_{mail_id}")
                                           
                    if new_cat != current_cat:
                        updated = False
                        if os.path.exists(EMAILS_CACHE_FILE):
                            try:
                                with open(EMAILS_CACHE_FILE, "r", encoding="utf-8") as f:
                                    cache_data = json.load(f)
                                    if cache_data.get("success"):
                                        for cache_mail in cache_data.get("emails", []):
                                            if str(cache_mail.get("id")) == str(mail_id):
                                                if new_cat == 0:
                                                    cache_mail["is_phd_related"] = False
                                                    cache_mail["phd_category"] = None
                                                else:
                                                    cache_mail["is_phd_related"] = True
                                                    cache_mail["phd_category"] = new_cat
                                                updated = True
                                                break
                                        if updated:
                                            with open(EMAILS_CACHE_FILE, "w", encoding="utf-8") as fw:
                                                json.dump(cache_data, fw, ensure_ascii=False)
                                            st.toast(ui("状态已自动修改并保存！即将刷新...", "Category updated and saved. Refreshing..."))
                                            import time
                                            time.sleep(0.5)
                                            st.rerun()
                            except Exception as e:
                                st.error(ui(f"保存失败: {str(e)}", f"Save failed: {str(e)}"))
                        if not updated:
                            st.warning(ui("缓存中难以修改数据。", "Failed to update cache data."))

                    # Using container to box the headers
                    with st.container():
                        st.caption(f"**From:** `{mail['from']}`\n\n**To:** `{mail['to']}`\n\n**Time:** `{mail['date']}`")
                        st.markdown("---")
                        st.write(t("email_body"))
                        st.text_area(t("email_body"), mail['body'], height=450, key=f"email_body_{mail_id}", label_visibility="collapsed")
                        st.caption(ui("最左侧菜单栏可以点击顶部的 `>` 或 `X` 隐藏以获得更大视野。",
                                      "Click `>` or `X` on the left sidebar to hide it for a wider workspace."))
                        
                        # Phase 3 Verification Display
                        phd_details = mail.get("phd_details", {})
                        verification_result = phd_details.get("verification_result") if isinstance(phd_details, dict) else None
                        if verification_result:
                            st.markdown(f"### {ui('URL防幻觉网页抓取与二次审核', 'URL Hallucination Check & Verification')}")
                            st.info(ui(f"**尝试抓取的导师主页:** {phd_details.get('scraped_url', '')}",
                                       f"**Fetched URL:** {phd_details.get('scraped_url', '')}"))
                            scraped_text = verification_result.get("scraped_text", "")
                            if scraped_text:
                                with st.expander(ui("爬取到的网页纯净脱水文本 (点击查看)", "Cleaned Web Text (click to view)")):
                                    st.code(scraped_text, language="text")
                            
                            st.markdown(f"#### {ui('AI 审查与提取过程', 'AI Review & Extraction')}")
                            is_real = verification_result.get('is_real_homepage')
                            ai_reasoning = verification_result.get('reasoning', '')
                            ai_keywords = verification_result.get('research_keywords', '')
                            
                            if is_real:
                                st.success(ui(f"**验证通过!**\n\n**审查判断:** {ai_reasoning}\n\n**提取到的最新关键词:** {ai_keywords}",
                                              f"**Verified!**\n\n**Reasoning:** {ai_reasoning}\n\n**Extracted Keywords:** {ai_keywords}"))
                            else:
                                st.error(ui(f"**验证驳回! (幻觉或死链)**\n\n**驳回原因:** {ai_reasoning}\n\n系统已自动清空该错误的主页链接。",
                                            f"**Verification Rejected (hallucination or dead link)**\n\n**Reason:** {ai_reasoning}\n\nThe invalid homepage URL has been cleared automatically."))

                    st.markdown("---")
                    reasoning_col, thinking_col = st.columns(2)
                    
                    with reasoning_col:
                        st.markdown(f"### {ui('邮件分类分析', 'Email Classification')}")
                        reasoning = mail.get("phd_reasoning")
                        if reasoning:
                            st.info(reasoning)
                            
                    with thinking_col:
                        st.markdown(f"### {ui('信息抽取分析', 'Information Extraction')}")
                        thinking_box = st.empty()
                        if f"thinking_{mail_id}" in st.session_state:
                            thinking_box.success(st.session_state[f"thinking_{mail_id}"])
                    
                with col_form:
                    st.subheader(t("tagging_card"))
                    if mail_id in marked_emails:
                        st.info(t("prof_in_db"))
                    else:
                        st.info(t("quick_fill"))
                        
                    # 读取全球大学动态数据
                    # === 【新增：Gemini 智能解析与信息填充大屏】 ===
                    config = load_config()
                    ai_provider = config.get("ai_provider", "通义千问 (Qwen)")
                    api_key = config.get("gemini_api_key", "")
                    qwen_key = config.get("qwen_api_key", "")
                    btn_label = ui("提取信息", "Extract Info")
                    cat_id = mail.get('phd_category')
                    
                    top_default_url = ""
                    p_details = mail.get("phd_details", {})
                    if isinstance(p_details, dict):
                        t_url = p_details.get("verified_homepage", "")
                        if t_url and t_url != "None":
                            top_default_url = t_url
                    
                    st.info(ui("**提取必备**：为了精准抽取导师档案并将内容入库，本系统限制必须通过导师官方网页抓取。\n**请先在此提供真实的导师主页链接**：",
                               "**Required for extraction**: To ensure reliable professor profiling and storage, extraction is limited to official webpages.\n**Please provide a real homepage URL first**:"))
                    hp_input_col, hp_btn_col = st.columns([2.9, 1.1])
                    with hp_input_col:
                        manual_hp_url = st.text_input(
                            ui("导师主页链接（用于大模型推理读取）", "Professor Homepage URL (for model extraction)"),
                            value=top_default_url,
                            key=f"ai_manual_hp_input_{mail_id}",
                            placeholder=ui("必须要填，以 http 开头", "Required, starts with http/https"),
                        )

                    with hp_btn_col:
                        st.markdown("<div style='height: 0.15rem;'></div>", unsafe_allow_html=True)
                        ai_extract_clicked = st.button(btn_label, key=f"ai_btn_{mail_id}", type="primary", use_container_width=True)

                    if ai_extract_clicked:
                        if not manual_hp_url.startswith("http"):
                            st.warning(ui("必须要填写真实的导师个人主页链接 (请以 http 或 https 开头) 才能进行解析并展示录入表单！",
                                          "A valid professor homepage URL (http/https) is required for extraction."))
                        elif ai_provider == "Google Gemini" and not api_key:
                            st.warning(ui("请先前往【系统配置】填写 Gemini API Key", "Please set Gemini API Key in System Config first."))
                        elif ai_provider == "通义千问 (Qwen)" and not qwen_key:
                            st.warning(ui("请先前往【系统配置】填写 通义千问 API Key", "Please set Qwen API Key in System Config first."))
                        else:
                            with st.spinner(f"{ai_provider} 正在阅读邮件、分析背景..."):
                                try:
                                    if ai_provider == "Google Gemini":
                                        genai.configure(api_key=api_key)

                                    web_text = ""
                                    raw_html = ""
                                    if manual_hp_url.startswith("http"):
                                        st.session_state[f"thinking_{mail_id}"] = f"**正在请求网页：** `{manual_hp_url}`...\n\n"
                                        thinking_box.info(st.session_state[f"thinking_{mail_id}"])
                                        import urllib.request
                                        import re
                                        try:
                                            req = urllib.request.Request(manual_hp_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0)'})
                                            page_html = urllib.request.urlopen(req, timeout=10).read().decode('utf-8', errors='ignore')
                                            raw_html = page_html
                                            t_text = re.sub(r'<style.*?>.*?</style>', '', page_html, flags=re.DOTALL|re.IGNORECASE)
                                            t_text = re.sub(r'<script.*?>.*?</script>', '', t_text, flags=re.DOTALL|re.IGNORECASE)
                                            t_text = re.sub(r'<[^>]+>', ' ', t_text)
                                            t_text = re.sub(r'\s+', ' ', t_text).strip()
                                            web_text = t_text[:5000]
                                            st.session_state[f"thinking_{mail_id}"] += f"**成功爬取纯净网页文本({len(web_text)}字符)。开始投喂大模型...**\n\n---\n"
                                            thinking_box.info(st.session_state[f"thinking_{mail_id}"])
                                        except Exception as e:
                                            st.session_state[f"thinking_{mail_id}"] += f"网页请求失败: {str(e)}\n\n---\n"
                                            thinking_box.error(st.session_state[f"thinking_{mail_id}"])
                                            
                                        prompt = f"""
请阅读下面提取出的导师主页纯文本信息，以及通讯邮件上下文。请以此抽取出该教授的档案详情。
你不需要阐述搜索推理，提取完后，请直接输出以下 JSON 对象，且用 ```json 包裹：
{{
    "name": "从网页或邮件中提取教授的名字（纯英文）",
    "country": "该大学所在国家（比如 United States, Hong Kong 等纯英文首字母大写）",
    "university": "大学官方正式全称",
    "department": "导师所在的学院/院系/专业",
    "email": "请判断导师邮箱来自发件人还是收件人。值严格返回 'from' 或 'to' 或留空。",
    "homepage": "{manual_hp_url}",
    "research": "导师的研究方向、兴趣或实验室名字（提取3-5个关键词）"
}}

邮件通讯上下文：
发件人 (From): {mail.get('from', '')}
收件人 (To): {mail.get('to', '')}
主题 (Subject): {mail.get('subject', '')}

导师网页文本 (前5000字符):
{web_text}
"""
                                    else:
                                        prompt = f"""
                                        请阅读这封留学生申请博士或套瓷的上下文邮件。帮我提取这名指导教授的完整信息。
                                        请务必结合你的知识库或在线搜索功能（如果被启用）查找到这位教授的官方学术主页或实验室网站。
                                        你可以先简略用文本阐述你的搜索和推理过程，然后把最终完整信息放入 ```json 代码块中返回。
                                        JSON 应严格遵循以下字段（值必须全为字符串）：
                                        {{
                                            "name": "教授的名字（纯英文）",
                                            "country": "该大学所在国家（比如 United States, Hong Kong 等）",
                                            "university": "大学官方正式全称",
                                            "department": "导师所在的学院/院系/专业（例：Computer Science）",
                                            "email": "请判断导师邮箱来自发件人还是收件人。值严格返回 'from' 或 'to' 或留空",
                                            "homepage": "导师的学术个人主页或实验室网站链接（查不到就留空）",
                                            "research": "导师的研究方向、兴趣或实验室名字（提取3-5个关键词）"
                                        }}
                                        
                                        邮件通讯上下文：
                                        发件人 (From): {mail.get('from', '')}
                                        收件人 (To): {mail.get('to', '')}
                                        主题 (Subject): {mail.get('subject', '')}
                                        邮件原文：
                                        {str(mail['body'])[:2000]}
                                        """
                                    
                                    # Request generation
                                    res_text = ""
                                    
                                    if ai_provider == "通义千问 (Qwen)":
                                        client = OpenAI(
                                            api_key=qwen_key, 
                                            base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
                                        )
                                        completion = client.chat.completions.create(
                                            model="qwen-plus",
                                            messages=[{'role': 'user', 'content': prompt}],
                                            stream=True,
                                            extra_body={"enable_search": True}
                                        )
                                        for chunk in completion:
                                            if chunk.choices and chunk.choices[0].delta.content:
                                                res_text += chunk.choices[0].delta.content
                                                thinking_box.info(res_text + "▌")
                                        thinking_box.success(res_text)
                                    else:
                                        response, used_model = _gemini_generate_content_with_fallback(prompt, stream=True)
                                        st.session_state[f"thinking_{mail_id}"] += f"Gemini model: `{used_model}`\n\n"
                                        for chunk in response:
                                            chunk_text = getattr(chunk, "text", "") or ""
                                            if not chunk_text:
                                                continue
                                            res_text += chunk_text
                                            thinking_box.info(res_text + "▌")
                                        thinking_box.success(res_text)
                                                
                                    # Ensure the thinking process is always saved before strict parsing
                                    st.session_state[f"thinking_{mail_id}"] = res_text
                                    
                                    # Parse json text
                                    text = res_text
                                    if '```json' in text:
                                        text = text.split('```json')[1].split('```')[0].strip()
                                    elif '```' in text:
                                        text = text.replace('```', '').strip()
                                        
                                    data = json.loads(text)
                                    valid_res = {}
                                    
                                    # [Phase 3 UI Interaction: URL Verification]
                                    inferred_hp = data.get("homepage", "")
                                    if cat_id != 1 and inferred_hp and inferred_hp != "None" and inferred_hp.startswith("http"):
                                        st.session_state[f"thinking_{mail_id}"] += f"\n\n---\n\n### URL防幻觉探针介入\n\nAI 首次回答中提供了 URL: `{inferred_hp}`。\n\n**正在发起代码级探针爬取...**\n"
                                        thinking_box.info(st.session_state[f"thinking_{mail_id}"])
                                        
                                        valid_res = verify_professor_homepage(inferred_hp, mail.get("to", ""), config)
                                        scraped = valid_res.get("scraped_text", "")
                                        
                                        st.session_state[f"thinking_{mail_id}"] += f"\n**抓取成功! 获得纯净脱水正文 {len(scraped)} 字符。**\n\n> {scraped[:200]}...\n\n"
                                        thinking_box.info(st.session_state[f"thinking_{mail_id}"])
                                        
                                        # Show Secondary AI verification result
                                        ai_reasoning = valid_res.get("reasoning", "")
                                        if valid_res.get("is_real_homepage"):
                                            st.session_state[f"thinking_{mail_id}"] += f"**AI 二次确权通过!**\n- **推理过程:** {ai_reasoning}\n- **重新总结研究点:** {valid_res.get('research_keywords', '')}"
                                            thinking_box.success(st.session_state[f"thinking_{mail_id}"])
                                            
                                            # Override with more accurate data
                                            data["research"] = valid_res.get("research_keywords", data.get("research"))
                                        else:
                                            st.session_state[f"thinking_{mail_id}"] += f"\n\n**AI 二次确权驳回 (属于假连接或无权限):**\n- {ai_reasoning}\n\n系统已强制清空该幻觉 URL。"
                                            thinking_box.error(st.session_state[f"thinking_{mail_id}"])
                                            data["homepage"] = ""
                                    
                                    # Fill form state
                                    def _safe_str(val):
                                        if isinstance(val, list): return ", ".join(str(v) for v in val)
                                        return "" if val is None else str(val)
                                    
                                    st.session_state[f"prof_{mail_id}"] = _safe_str(data.get("name", ""))
                                    
                                    import re
                                    def extract_email_address(s):
                                        if not s:
                                            return ""
                                        matches = re.findall(
                                            r'(?i)\b[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}\b',
                                            str(s),
                                        )
                                        if not matches:
                                            return ""
                                        return str(matches[0]).strip()

                                    def infer_professor_side():
                                        # 1) Priority: use existing categorized mail type
                                        if cat_id == 1:
                                            return "to"
                                        if cat_id in [2, 3, 4, 5, 6, 7, 8]:
                                            return "from"

                                        # 2) Fallback: use AI judgement from extraction JSON
                                        ai_side = _safe_str(data.get("email", "")).lower().strip()
                                        if ai_side in ["from", "to"]:
                                            return ai_side

                                        # 3) Fallback: lightweight content heuristic
                                        body_text = str(mail.get("body", "") or "").lower()
                                        sent_cues = [
                                            "dear professor",
                                            "i am interested in",
                                            "i would like to apply",
                                            "my cv",
                                            "my research proposal",
                                            "申请博士",
                                            "套磁",
                                        ]
                                        reply_cues = [
                                            "thank you for your email",
                                            "thanks for reaching out",
                                            "unfortunately",
                                            "interview",
                                            "we can schedule",
                                            "best regards",
                                        ]
                                        sent_score = sum(1 for c in sent_cues if c in body_text)
                                        reply_score = sum(1 for c in reply_cues if c in body_text)
                                        return "to" if sent_score >= reply_score else "from"

                                    professor_side = infer_professor_side()
                                    if professor_side == "to":
                                        extracted_email = extract_email_address(mail.get("to", ""))
                                    else:
                                        extracted_email = extract_email_address(mail.get("from", ""))
                                        
                                    st.session_state[f"prof_email_{mail_id}"] = extracted_email
                                    st.session_state[f"dept_{mail_id}"] = _safe_str(data.get("department", ""))
                                    st.session_state[f"hp_{mail_id}"] = _safe_str(data.get("homepage", ""))
                                    st.session_state[f"dir_{mail_id}"] = _safe_str(data.get("research", ""))
                                    
                                    ai_country = data.get("country", "")
                                    ai_univ = data.get("university", "")
                                    world_univ_data = get_world_universities()
                                    
                                    matched_country = "[不在列表中] 手动补充"
                                    matched_univ = "不在对应院校列表中..."
                                    
                                    if ai_country:
                                        for c_key in sorted(list(world_univ_data.keys())):
                                            if ai_country.lower() in c_key.lower() or c_key.lower() in ai_country.lower():
                                                matched_country = c_key
                                                break
                                                
                                    if matched_country != "[不在列表中] 手动补充" and ai_univ:
                                        univ_list = world_univ_data.get(matched_country, [])
                                        for u in univ_list:
                                            if ai_univ.lower() == u.lower() or ai_univ.lower() in u.lower() or u.lower() in ai_univ.lower() or ai_univ.replace("The ", "").lower() in u.lower():
                                                matched_univ = u
                                                break
                                                
                                    st.session_state[f"country_{mail_id}"] = matched_country
                                    if matched_country != "[不在列表中] 手动补充":
                                        if matched_univ != "不在对应院校列表中...":
                                            st.session_state[f"univ_{mail_id}"] = matched_univ
                                            st.session_state[f"mc_{mail_id}"] = ""
                                            st.session_state[f"mu_{mail_id}"] = ""
                                        else:
                                            # 如果国家选出来了但学校没匹配上，依然切回手动模式，让用户检查
                                            st.session_state[f"country_{mail_id}"] = "[不在列表中] 手动补充"
                                            st.session_state[f"mc_{mail_id}"] = ai_country
                                            st.session_state[f"mu_{mail_id}"] = ai_univ
                                    else:
                                        st.session_state[f"mc_{mail_id}"] = ai_country
                                        st.session_state[f"mu_{mail_id}"] = ai_univ
                                    
                                    # Save extracted details directly to cache to prevent loss when switching tabs
                                    if os.path.exists(EMAILS_CACHE_FILE):
                                        try:
                                            with open(EMAILS_CACHE_FILE, "r", encoding="utf-8") as f:
                                                cache_data = json.load(f)
                                                if cache_data.get("success"):
                                                    for cache_mail in cache_data.get("emails", []):
                                                        if str(cache_mail.get("id")) == str(mail_id):
                                                            cache_mail["phd_details"] = {
                                                                "extracted_prof_name": st.session_state.get(f"prof_{mail_id}", ""),
                                                                "extracted_prof_email": st.session_state.get(f"prof_email_{mail_id}", ""),
                                                                "department": st.session_state.get(f"dept_{mail_id}", ""),
                                                                "verified_homepage": st.session_state.get(f"hp_{mail_id}", ""),
                                                                "research_direction": st.session_state.get(f"dir_{mail_id}", ""),
                                                                "country_guess": st.session_state.get(f"country_{mail_id}", ""),
                                                                "university_name": st.session_state.get(f"univ_{mail_id}", ""),
                                                                "manual_country": st.session_state.get(f"mc_{mail_id}", ""),
                                                                "manual_univ": st.session_state.get(f"mu_{mail_id}", ""),
                                                                "priority_guess": "T1 (平替)"
                                                            }
                                                            break
                                                    with open(EMAILS_CACHE_FILE, "w", encoding="utf-8") as fw:
                                                        json.dump(cache_data, fw, ensure_ascii=False)
                                        except:
                                            pass
                                            
                                    # Force rerender form
                                    st.rerun()
                                    
                                except Exception as e:
                                    st.error(ui(f"解析未成功，可能是 API 密钥无效或解析格式错误: {str(e)}",
                                                f"Extraction failed. Possible invalid API key or malformed response: {str(e)}"))
                    # ===================================================

                    world_univ_data = get_world_universities()
                    country_list = ["[不在列表中] 手动补充"] + sorted(list(world_univ_data.keys()))
                    
                    # 将这几个常申大国置顶显示以方便查找
                    priority_countries = ["United States", "United Kingdom", "Hong Kong", "Singapore", "Canada", "Australia", "China"]
                    found_priorities = []
                    for pc in priority_countries:
                        for cl in country_list:
                            if pc in cl:
                                found_priorities.append(cl)
                                break
                    for c in reversed(found_priorities):
                        if c in country_list:
                            country_list.remove(c)
                            country_list.insert(1, c) # 插在"手动补充"之后
                    
                    
                    # 自动带入AI分析结果
                    cat_id = mail.get('phd_category')
                    is_phd = mail.get('is_phd_related')
                    
                    import email.utils
                    default_action_idx = 0
                    default_status_idx = 0
                    default_prof_name = ""
                    default_prof_email = ""
                    default_prio = "T1 (平替)"
                    default_dept = ""
                    default_url = ""
                    default_dir = ""
                    default_country = None
                    default_univ = None
                    
                    if is_phd:
                        if cat_id == 1:
                            default_action_idx = 0 # 新建
                            parsed_name, parsed_email = email.utils.parseaddr(mail.get("to", ""))
                        else:
                            default_action_idx = 1 # 同步
                            cat_to_status = {2: 1, 3: 3, 4: 2, 5: 4, 6: 5, 7: 6}
                            default_status_idx = cat_to_status.get(cat_id, 0)
                            parsed_name, parsed_email = email.utils.parseaddr(mail.get("from", ""))
                        
                        default_prof_email = parsed_email
                        
                        # Leverage second pass info (for ANY category where extraction happened)
                        phd_details = mail.get("phd_details", {})
                        if phd_details:
                            if "extracted_prof_name" in phd_details and phd_details["extracted_prof_name"]:
                                default_prof_name = phd_details["extracted_prof_name"]
                            if "extracted_prof_email" in phd_details and phd_details["extracted_prof_email"]:
                                default_prof_email = phd_details["extracted_prof_email"]
                                
                            default_prio = phd_details.get("priority_guess", "T1 (平替)")
                            if default_prio not in ["T0 (强选)", "T1 (平替)", "T2 (保底)"]:
                                default_prio = "T1 (平替)"
                            default_dept = phd_details.get("department") if phd_details.get("department") != "None" else ""
                            default_url = phd_details.get("verified_homepage") if phd_details.get("verified_homepage") != "None" else ""
                            default_dir = phd_details.get("research_direction") if phd_details.get("research_direction") != "None" else ""
                            
                            c_g = phd_details.get("country_guess")
                            if c_g and c_g != "None": default_country = c_g
                            u_g = phd_details.get("university_name")
                            if u_g and u_g != "None": default_univ = u_g
                            
                            # Store manual values in session state directly if they exist so text inputs grab them
                            if "manual_country" in phd_details and f"mc_{mail_id}" not in st.session_state:
                                st.session_state[f"mc_{mail_id}"] = phd_details["manual_country"]
                            if "manual_univ" in phd_details and f"mu_{mail_id}" not in st.session_state:
                                st.session_state[f"mu_{mail_id}"] = phd_details["manual_univ"]
                            
                    current_db = load_db()
                    if default_action_idx == 1 and not current_db:
                        default_action_idx = 0 # 数据库为空，强制回退至新建模式
                    
                    # 动态级联选择不使用 st.form，使用普通 layout 以支持联动的交互刷新
                    action_mode = st.radio("记录操作模式", ["新建导师记录", "同步至已有导师"], index=default_action_idx, horizontal=True, key=f"radio_mode_{mail_id}")
                    if action_mode == "新建导师记录":
                        with st.container():
                            col_p1, col_p2 = st.columns(2)
                            with col_p1:
                                prof_name = st.text_input(t("prof_name_req"), value=default_prof_name, placeholder=t("prof_name_ph"), key=f"prof_{mail_id}")
                            with col_p2:
                                prof_email = st.text_input("导师邮箱地址", value=default_prof_email, placeholder="example@univ.edu", key=f"prof_email_{mail_id}")
                            
                            c_idx = 0
                            if default_country in country_list: c_idx = country_list.index(default_country)
                            st.caption(t("target_country"))
                            selected_country = st.selectbox(
                                t("target_country"),
                                country_list,
                                index=c_idx,
                                key=f"country_{mail_id}",
                                label_visibility="collapsed",
                            )
                            
                            univ_options = ["不在对应院校列表中..."]
                            if selected_country != "[不在列表中] 手动补充":
                                univ_options = world_univ_data.get(selected_country, ["不在对应院校列表中..."])
                                
                            u_idx = 0
                            if default_univ in univ_options: u_idx = univ_options.index(default_univ)
                            st.caption(t("target_univ"))
                            selected_univ = st.selectbox(
                                t("target_univ"),
                                univ_options,
                                index=u_idx,
                                key=f"univ_{mail_id}",
                                label_visibility="collapsed",
                            )
                            
                            manual_country = ""
                            manual_univ = ""
                            if selected_country == "[不在列表中] 手动补充":
                                col_m1, col_m2 = st.columns([1, 2])
                                with col_m1:
                                    manual_country = st.text_input(t("manual_country"), value=default_country if default_country else "", placeholder="例: 荷兰", key=f"mc_{mail_id}")
                                with col_m2:
                                    manual_univ = st.text_input(t("manual_univ"), value=default_univ if default_univ else "", placeholder="例: TU Delft", key=f"mu_{mail_id}")
                                
                            department = st.text_input("院系/专业", value=default_dept, placeholder="例: CS / AI / EE", key=f"dept_{mail_id}")
                            
                            col_s1, col_s2 = st.columns(2)
                            with col_s1:
                                opts_p = ["T0 (强选)", "T1 (平替)", "T2 (保底)"]
                                p_i = opts_p.index(default_prio) if default_prio in opts_p else 1
                                priority = st.selectbox("意向推荐级", opts_p, index=p_i, key=f"prio_{mail_id}")
                            with col_s2:
                                status = st.selectbox("当前阶段", ["已发首封邮件", "收到积极回复", "收到中等回复", "收到消极回复", "面试预约阶段", "面试结束阶段", "口头offer", "终止状态"], index=default_status_idx, key=f"stat_{mail_id}")
                            interview_time = ""
                            if status == "面试预约阶段":
                                dft_date, dft_time = get_interview_picker_defaults(st.session_state.get(f"intv_{mail_id}", ""))
                                c_dt1, c_dt2 = st.columns(2)
                                with c_dt1:
                                    intv_date = st.date_input("面试日期", value=dft_date, key=f"intv_date_{mail_id}")
                                with c_dt2:
                                    intv_time = st.time_input("面试时间", value=dft_time, key=f"intv_time_{mail_id}")
                                interview_time = format_interview_time(intv_date, intv_time)
                                
                            # 强制使用顶部提取栏填写的链接或默认推断的链接，不在底部展示重复框
                            homepage = manual_hp_url if manual_hp_url else default_url
                            
                            direction = st.text_input("导师研究方向 (Keywords)", value=default_dir, key=f"dir_{mail_id}")
                            
                            if st.button("保存进度至看板", use_container_width=True, key=f"submit_{mail_id}", type="primary"):
                                # 解析最终数据
                                final_country = manual_country.strip() if selected_country == "[不在列表中] 手动补充" else selected_country
                                final_univ = manual_univ.strip() if selected_country == "[不在列表中] 手动补充" else selected_univ
    
                                if prof_name and final_univ:
                                    current_db = load_db()
                                    
                                    from email.utils import parsedate_to_datetime
                                    try:
                                        mail_dt = parsedate_to_datetime(mail.get("date", "")).astimezone().strftime("%Y-%m-%d %H:%M:%S")
                                    except Exception:
                                        mail_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                                    new_row = {
                                        "导师/教授": prof_name,
                                        "导师邮箱": prof_email,
                                        "国家/地区": (country_for(final_univ) or final_country or "未知"),
                                        "学校名称": canonical_school_name(final_univ),
                                        "院系": department,
                                        "主页链接": homepage if homepage else "",
                                        "研究方向": direction if direction else "未明确",
                                        "推荐级": priority,
                                        "阶段": status,
                                        "面试时间": interview_time,
                                        "更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                        "创建时间": mail_dt,
                                        "关联邮件ID": mail_id,
                                        "来源": "手动",
                                        "QS排名": qs_rank_for(final_univ),
                                        "USNews排名": usnews_rank_for(final_univ),
                                    }
                                    current_db.append(new_row)
                                    save_db(current_db)
                                    with st.spinner("正在自动抓取该导师最近10篇论文..."):
                                        try:
                                            result = resolve_recent_papers(
                                                prof_name=prof_name,
                                                univ_name=final_univ,
                                                homepage_url=homepage if homepage else "",
                                                preset_scholar_url="",
                                                limit=10,
                                            )
                                        except Exception:
                                            result = {"papers": [], "status": "exception", "scholar_url": "", "source": ""}

                                    papers = result.get("papers", []) if isinstance(result, dict) else []
                                    if papers:
                                        new_row["Scholar链接"] = result.get("scholar_url", "")
                                        new_row["最近论文"] = papers
                                        new_row["最近论文更新时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                        save_db(current_db)
                                        st.success(f"成功! {prof_name} 已加入大盘，并自动抓取 {len(papers)} 篇最近论文。")
                                    else:
                                        st.success(f"成功! {prof_name} 已加入大盘。")
                                        st.info("已尝试自动抓取最近论文，但暂未成功（可能被 Scholar 限流/验证码影响）。")
                                    st.rerun()
                                else:
                                    st.error(t("save_error"))
                    else:
                        with st.container():
                            current_db = load_db()
                            if not current_db:
                                st.warning(ui("导师数据库为空，请先在『新建导师记录』模式下创建！",
                                              "Professor DB is empty. Please create a new record first."))
                            else:
                                prof_options = {
                                    f"{r['导师/教授']} ({r.get('学校名称','未知')} - {r.get('院系','')})"
                                    f" · {school_ranking_text(r.get('学校名称', ''), r.get('QS排名', ''))}": i
                                    for i, r in enumerate(current_db)
                                }
                                
                                preselect_prof_idx = 0
                                if default_prof_email:
                                    for i, r in enumerate(current_db):
                                        if r.get("导师邮箱") == default_prof_email or (default_prof_name and r.get("导师/教授") == default_prof_name):
                                            preselect_prof_idx = i
                                            break
                                            
                                sel_prof_str = st.selectbox("选择要同步关联的导师", list(prof_options.keys()), index=preselect_prof_idx, key=f"sel_prof_{mail_id}")
                                sel_idx = prof_options[sel_prof_str]
                                sel_r = current_db[sel_idx]
                                
                                val_email = default_prof_email if default_prof_email else sel_r.get("导师邮箱", "")
                                
                                status_choices = ["已发首封邮件", "收到积极回复", "收到中等回复", "收到消极回复", "面试预约阶段", "面试结束阶段", "口头offer", "终止状态"]
                                cur_stat = sel_r.get("阶段", "已发首封邮件")
                                if cur_stat not in status_choices: cur_stat = "已发首封邮件"
                                
                                cat_to_status_name = {
                                    1: "已发首封邮件", 2: "收到积极回复", 3: "收到消极回复", 
                                    4: "收到中等回复", 5: "面试预约阶段", 6: "面试结束阶段", 7: "口头offer"
                                }
                                new_status = cat_to_status_name.get(cat_id, cur_stat)
                                
                                st.info(f"**同步后，导师当前申请阶段将自动更新为：** `{new_status}` (取自上方修正确认的邮件分类)")
                                
                                new_time = sel_r.get("面试时间", "")
                                if new_status == "面试预约阶段":
                                    dft_date, dft_time = get_interview_picker_defaults(new_time)
                                    c_dt1, c_dt2 = st.columns(2)
                                    with c_dt1:
                                        new_date = st.date_input("面试日期", value=dft_date, key=f"new_intv_date_{mail_id}")
                                    with c_dt2:
                                        new_clock = st.time_input("面试时间", value=dft_time, key=f"new_intv_time_{mail_id}")
                                    new_time = format_interview_time(new_date, new_clock)
                                
                                if st.button("同步更新进度并接管本邮件", use_container_width=True, key=f"upd_submit_{mail_id}", type="primary"):
                                    current_db[sel_idx]["阶段"] = new_status
                                    current_db[sel_idx]["面试时间"] = new_time
                                    if not sel_r.get("导师邮箱") and val_email:
                                        current_db[sel_idx]["导师邮箱"] = val_email
                                    current_db[sel_idx]["更新时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    
                                    old_mail_id = str(current_db[sel_idx].get("关联邮件ID", ""))
                                    if mail_id not in old_mail_id:
                                        if old_mail_id and old_mail_id.strip() != "None":
                                            current_db[sel_idx]["关联邮件ID"] = f"{old_mail_id},{mail_id}"
                                        else:
                                            current_db[sel_idx]["关联邮件ID"] = mail_id
                                            
                                    save_db(current_db)
                                    st.success(f"成功! 已将本邮件同步关联至 {sel_r['导师/教授']}。")
                                    st.rerun()

    elif menu == t("menu_db"):
        st.title(ui("导师库管理", "Professor DB"))
        # Keep the minute-level local clocks current while this page is open.
        st_autorefresh(interval=30000, key="professor_db_local_time_tick")
        _edit_toast = st.session_state.pop("db_edit_toast", None)
        if _edit_toast:
            st.toast(_edit_toast)
        st.markdown(f"<p style='color: #9b9ba3; margin-bottom: 1.2rem;'>{ui('此处聚合所有导师：既有已套瓷的，也可以是还没套瓷、仅作为目标登记的。', 'All professors live here — both contacted ones and not-yet-contacted targets.')}</p>", unsafe_allow_html=True)

        top_l, top_r = st.columns([3, 1])
        with top_l:
            bulk_mode = st.toggle(ui("批量删除模式", "Bulk delete mode"), key="db_bulk_mode")
        with top_r:
            if st.button(ui("＋ 新建导师", "＋ Add professor"), type="primary", use_container_width=True, key="db_open_add"):
                add_professor_dialog()

        _bulk_toast = st.session_state.pop("db_bulk_import_toast", None)
        if _bulk_toast:
            st.toast(_bulk_toast)

        _open_bulk_import = st.button(
            ui("批量导入导师", "Bulk import"),
            key="db_open_bulk_import",
        )
        if _open_bulk_import:
            st.session_state["db_bulk_dialog_open"] = True

        @st.dialog(ui("批量导入导师", "Bulk import professors"), width="large")
        def bulk_import_professors_dialog():
            if st.button(ui("关闭", "Close"), key="db_close_bulk_import"):
                st.session_state["db_bulk_dialog_open"] = False
                st.rerun()
            st.markdown(ui(
                "**第 1 步**：填写你的需求 → 点「生成并下载提示词」。\n\n"
                "**第 2 步**：把下载的内容整段贴给 ChatGPT（建议用带代码解释器的模型，如 GPT‑5 Thinking）。它会把结果存成文件并给你一个 `.json` **下载链接**，点链接下载即可——无需手动复制粘贴。\n\n"
                "**第 3 步**：在下方上传该 JSON 完成导入。系统会自动把学校名对齐到 QS 2027 前 500 标准名、补上 QS 排名。",
                "**Step 1**: fill in your request → click 'Generate & download prompt'.\n\n"
                "**Step 2**: paste it whole into ChatGPT (use a model with the code-interpreter tool). It saves the result to a file and gives you a `.json` **download link** — no copy-paste needed.\n\n"
                "**Step 3**: upload that JSON below. The system auto-aligns school names to the QS 2027 top-500 and fills QS rank."))

            # 表单值持久化：会话首次进入时，从本地配置回填（重启后仍可复用）
            if not st.session_state.get("_db_tpl_loaded"):
                _saved_reqs = load_config().get("import_tpl_reqs", {}) or {}
                for _sk, _sf in [("db_tpl_dir", "dir"), ("db_tpl_region", "region"),
                                 ("db_tpl_count", "count"), ("db_tpl_extra", "extra")]:
                    if _sk not in st.session_state:
                        st.session_state[_sk] = _saved_reqs.get(_sf, "")
                st.session_state["_db_tpl_loaded"] = True

            st.markdown(f"**{ui('① 填写需求', '① Your request')}**")
            rq1, rq2 = st.columns(2)
            req_dir = rq1.text_input(ui("研究方向", "Research area"), key="db_tpl_dir",
                                     placeholder=ui("如：Robotics / 具身智能 / 计算机视觉", "e.g. Robotics / Embodied AI / CV"))
            req_region = rq2.text_input(ui("地区 / 目标学校", "Region / target schools"), key="db_tpl_region",
                                        placeholder=ui("如：美国、香港 或 ANU、NUS", "e.g. US, Hong Kong or ANU, NUS"))
            rq3, rq4 = st.columns(2)
            req_count = rq3.text_input(ui("数量（留空=尽量列全）", "Count (blank = as many as possible)"), key="db_tpl_count",
                                       placeholder=ui("留空则尽量列全 / 或填如：每校至少 10 位", "blank = exhaustive / or e.g. 10+ per school"))
            req_extra = rq4.text_input(ui("补充说明（选填）", "Extra notes (optional)"), key="db_tpl_extra",
                                       placeholder=ui("如：只要做视觉、排除纯硬件方向", "e.g. CV only, exclude pure-hardware"))

            prompt_text = build_import_prompt(req_dir, req_region, req_count, req_extra)
            if st.download_button(
                ui("生成并下载提示词", "Generate & download prompt"),
                data=prompt_text.encode("utf-8"),
                file_name="导师库导入_GPT提示词.md",
                mime="text/markdown",
                type="primary",
                use_container_width=True,
                key="db_import_prompt_tpl_btn",
            ):
                # 记住这次填写的需求，下次自动带出
                _cfg = load_config()
                _cfg["import_tpl_reqs"] = {"dir": req_dir, "region": req_region,
                                          "count": req_count, "extra": req_extra}
                save_config(_cfg)
            st.caption(ui("你填写的需求会自动记住，下次打开仍在。", "Your inputs are remembered for next time."))
            with st.popover(ui("预览提示词", "Preview prompt"), use_container_width=True):
                st.code(prompt_text, language="markdown")

            # ② 直接用 AI 生成（免去下载提示词→喂 GPT→再上传的手动流程）
            st.divider()
            st.markdown(f"**{ui('② 直接用 AI 生成（推荐）', '② Generate with AI directly (recommended)')}**")
            _gen_provider = load_config().get("ai_provider", "通义千问 (Qwen)")
            st.caption(ui(f"将用当前引擎「{_gen_provider}」检索并列出老师，你确认后再导入（每位老师都会附个人主页或学院教职页）。可在【系统配置】切换引擎。",
                          f"Uses the current engine ‘{_gen_provider}’ to search and list professors; you confirm before importing (each comes with a personal or faculty page). Switch engines in System Config."))
            if st.button(ui("用 AI 生成导师名单", "Generate professors with AI"),
                         type="primary", use_container_width=True, key="db_ai_gen_btn"):
                # 记住需求
                _cfg = load_config()
                _cfg["import_tpl_reqs"] = {"dir": req_dir, "region": req_region,
                                          "count": req_count, "extra": req_extra}
                save_config(_cfg)
                if not str(req_dir).strip() and not str(req_region).strip():
                    st.warning(ui("请至少填写「研究方向」或「地区 / 目标学校」。",
                                  "Please fill in at least Research area or Region / target schools."))
                else:
                    ai_cfg = _ai_cfg_with_app_lang(load_config())

                    def _finalize_gen(profs):
                        """归一化学校名 / QS / 国家 / 来源 + 校验主页真实性，写入预览。"""
                        for p in profs:
                            canon = canonical_school_name(str(p.get("学校名称", "")).strip())
                            p["学校名称"] = canon
                            p["QS排名"] = qs_rank_for(canon)
                            p["USNews排名"] = usnews_rank_for(canon)
                            _qs_country = country_for(canon)
                            if _qs_country:
                                p["国家/地区"] = _qs_country
                            elif not str(p.get("国家/地区", "")).strip():
                                p["国家/地区"] = "未知"
                            p["来源"] = "AI 生成"
                        # 校验每个主页是否真实存在、是否对得上老师（挡住 LLM 捏造链接）
                        with st.spinner(ui("正在核验每位老师的主页是否真实存在……",
                                           "Verifying each professor's homepage is real…")):
                            items = [(i, str(p.get("主页链接", "")).strip(), p.get("导师/教授", ""))
                                     for i, p in enumerate(profs)]
                            verdicts = verify_homepages_bulk(items)
                        for i, p in enumerate(profs):
                            v = verdicts.get(i, {"status": "no_url", "ok": False, "reason": ""})
                            p["_home_status"] = v.get("status", "no_url")
                            p["_home_ok"] = bool(v.get("ok"))
                            p["_home_reason"] = v.get("reason", "")
                        st.session_state["db_ai_preview"] = profs

                    provider_now = ai_cfg.get("ai_provider", "通义千问 (Qwen)")
                    gen_ok, gen_profs, gen_err = False, [], ""

                    if provider_now == "Claude (中转)":
                        # 流式：实时显示模型正在产出的内容 + 已找到的老师数
                        status_ph = st.empty()
                        count_ph = st.empty()
                        text_ph = st.empty()
                        acc_text, last_count = "", 0
                        try:
                            status_ph.info(ui("AI 正在检索并实时输出……", "AI is searching and streaming…"))
                            for kind, payload, n in stream_professor_list(
                                    req_dir, req_region, req_count, req_extra, ai_cfg):
                                if kind == "chunk":
                                    acc_text = payload
                                    if n != last_count:
                                        last_count = n
                                        count_ph.caption(ui(f"已找到约 {n} 位老师…", f"~{n} professors so far…"))
                                    # 只展示尾部，避免刷屏
                                    tail = acc_text[-1500:]
                                    text_ph.code(tail, language="json")
                                elif kind == "done":
                                    gen_ok, gen_profs, gen_err = parse_professor_payload(payload)
                                elif kind == "error":
                                    gen_err = payload
                            status_ph.empty(); count_ph.empty(); text_ph.empty()
                        except Exception as e:
                            gen_err = f"{e}"
                        # 流式失败 → 回退非流式
                        if not gen_ok:
                            with st.spinner(ui("流式不可用，改用普通方式生成……",
                                               "Streaming unavailable, falling back…")):
                                gen_ok, gen_profs, gen_err = generate_professor_list(
                                    req_dir, req_region, req_count, req_extra, ai_cfg)
                    else:
                        with st.spinner(ui("AI 正在检索并整理导师名单，可能需要 10-60 秒……",
                                           "AI is compiling the professor list, this may take 10-60s…")):
                            gen_ok, gen_profs, gen_err = generate_professor_list(
                                req_dir, req_region, req_count, req_extra, ai_cfg)

                    if not gen_ok:
                        st.session_state.pop("db_ai_preview", None)
                        st.error(ui(f"生成失败：{gen_err}", f"Generation failed: {gen_err}"))
                    else:
                        _finalize_gen(gen_profs)
                        st.rerun()

            # 生成结果预览 + 确认导入
            _preview = st.session_state.get("db_ai_preview")
            if _preview:
                _verified = [p for p in _preview if p.get("_home_ok")]
                _bad = [p for p in _preview if not p.get("_home_ok")]
                st.success(ui(f"AI 共找到 {len(_preview)} 位老师；主页核验通过 {len(_verified)} 位。",
                              f"AI found {len(_preview)}; {len(_verified)} passed homepage verification."))
                if _bad:
                    st.warning(ui(
                        f"⚠️ {len(_bad)} 位主页**未通过核验**（打不开 / 404 / 页面查无此人，很可能是 AI 捏造的链接），默认不勾选。",
                        f"⚠️ {len(_bad)} have an UNVERIFIED homepage (unreachable / 404 / name not found — likely fabricated). Unchecked by default."))

                # 校验状态 → 中文标签
                _status_label = {
                    "ok": ui("✅ 真实", "✅ Verified"),
                    "name_mismatch": ui("❌ 查无此人", "❌ Name not found"),
                    "unreachable": ui("❌ 打不开", "❌ Unreachable"),
                    "no_url": ui("— 无主页", "— No URL"),
                }

                cselA, cselB = st.columns(2)
                select_all = cselA.checkbox(ui("全选", "Select all"), value=False, key="db_ai_select_all")
                only_verified = cselB.checkbox(ui("仅选主页核验通过的老师", "Only homepage-verified professors"),
                                               value=True, key="db_ai_only_verified")

                # 每行默认勾选：全选优先；否则按“仅核验通过”
                _col_sel = ui("导入", "Import")
                _col_home = ui("主页", "Homepage")
                _col_chk = ui("主页核验", "Homepage check")

                def _default_pick(p):
                    if select_all:
                        return True
                    if only_verified:
                        return bool(p.get("_home_ok"))
                    return True

                _prev_df = pd.DataFrame([{
                    _col_sel: _default_pick(p),
                    ui("导师", "Professor"): p.get("导师/教授", ""),
                    ui("学校", "School"): p.get("学校名称", ""),
                    _col_chk: _status_label.get(p.get("_home_status", "no_url"), p.get("_home_status", "")),
                    ui("研究方向", "Research"): p.get("研究方向", ""),
                    _col_home: p.get("主页链接", "") or "—",
                } for p in _preview])

                _editor_key = f"db_ai_editor_{int(select_all)}_{int(only_verified)}_{len(_preview)}"
                edited = st.data_editor(
                    _prev_df, use_container_width=True, hide_index=True, key=_editor_key,
                    column_config={
                        _col_sel: st.column_config.CheckboxColumn(_col_sel, help=ui("勾选后导入", "Tick to import")),
                        _col_home: st.column_config.LinkColumn(),
                        ui("导师", "Professor"): st.column_config.TextColumn(disabled=True),
                        ui("学校", "School"): st.column_config.TextColumn(disabled=True),
                        _col_chk: st.column_config.TextColumn(disabled=True),
                        ui("研究方向", "Research"): st.column_config.TextColumn(disabled=True),
                    },
                )
                _sel_mask = list(edited[_col_sel]) if _col_sel in edited else [True] * len(_preview)
                _sel_count = sum(1 for v in _sel_mask if v)
                st.caption(ui(f"已选择 {_sel_count} / {len(_preview)} 位。",
                              f"Selected {_sel_count} / {len(_preview)}."))

                cimp1, cimp2 = st.columns(2)
                if cimp1.button(ui(f"确认导入所选（{_sel_count}）", f"Import selected ({_sel_count})"),
                                type="primary", use_container_width=True, key="db_ai_import_confirm"):
                    to_import = [p for p, keep in zip(_preview, _sel_mask) if keep]
                    if not to_import:
                        st.warning(ui("请至少勾选一位老师。", "Please select at least one professor."))
                    else:
                        # 去掉内部校验字段再入库
                        cleaned = [{k: v for k, v in p.items() if not k.startswith("_home_")} for p in to_import]
                        new_db, _ = dedupe_db(load_db() + cleaned)
                        save_db(new_db)
                        for _k in ("db_ai_preview", "db_ai_only_verified", "db_ai_select_all"):
                            st.session_state.pop(_k, None)
                        st.session_state["db_bulk_import_toast"] = ui(
                            f"已导入 {len(cleaned)} 位，导师库现有 {len(new_db)} 位。",
                            f"Imported {len(cleaned)}; DB now has {len(new_db)} professors.")
                        st.session_state["db_bulk_dialog_open"] = False
                        st.rerun()
                if cimp2.button(ui("放弃这批结果", "Discard"), use_container_width=True, key="db_ai_import_discard"):
                    for _k in ("db_ai_preview", "db_ai_only_verified", "db_ai_select_all"):
                        st.session_state.pop(_k, None)
                    st.session_state["db_bulk_dialog_open"] = False
                    st.rerun()

            st.divider()
            st.markdown(f"**{ui('③ 或：上传 GPT 生成的 JSON', '③ Or: upload the JSON from GPT')}**")
            db_up = st.file_uploader(ui("上传导师 JSON 文件", "Upload professor JSON file"), type=["json"], key="db_import_file")
            st.caption(ui("导入方式：合并导入（保留现有，按姓名+学校自动去重）。",
                          "Import mode: merge — keeps existing records, de-duplicated by name + school."))
            if db_up is not None and st.button(ui("执行导入", "Import"), type="primary", key="db_import_btn"):
                try:
                    db_payload = json.loads(db_up.getvalue().decode("utf-8"))
                    if isinstance(db_payload, list):
                        imp_profs = db_payload
                    else:
                        imp_profs = db_payload.get("professors", []) or []
                    imp_profs = [p for p in imp_profs
                                 if str(p.get("导师/教授", "")).strip() and str(p.get("学校名称", "")).strip()]
                    # 归一化：学校名对齐到权威清单、按校名回填 QS 排名与国家、打来源标记
                    for p in imp_profs:
                        canon = canonical_school_name(str(p.get("学校名称", "")).strip())
                        p["学校名称"] = canon
                        p["QS排名"] = qs_rank_for(canon)
                        p["USNews排名"] = usnews_rank_for(canon)
                        _qs_country = country_for(canon)
                        if _qs_country:
                            p["国家/地区"] = _qs_country
                        elif not str(p.get("国家/地区", "")).strip():
                            p["国家/地区"] = "未知"
                        p["来源"] = "批量导入"
                        # 笔记字段：新模板产出 LLM摘要；兼容旧文件里放在「备注」的 AI 说明
                        if not clean_note(p.get("LLM摘要", "")) and clean_note(p.get("备注", "")):
                            p["LLM摘要"] = clean_note(p.get("备注", ""))
                            p["备注"] = ""
                    if not imp_profs:
                        st.error(ui("文件里没有有效导师记录（需至少含「导师/教授」和「学校名称」）。",
                                    "No valid professor records found (each needs 「导师/教授」 and 「学校名称」)."))
                    else:
                        # 始终合并导入：保留现有，按姓名+学校去重
                        new_db, _ = dedupe_db(load_db() + imp_profs)
                        save_db(new_db)
                        st.session_state["db_bulk_import_toast"] = ui(
                            f"成功导入 {len(imp_profs)} 条，导师库现有 {len(new_db)} 位。",
                            f"Imported {len(imp_profs)} records; DB now has {len(new_db)} professors.")
                        st.session_state["db_bulk_dialog_open"] = False
                        st.rerun()
                except Exception as e:
                    st.error(ui(f"导入失败：{e}", f"Import failed: {e}"))

        if st.session_state.get("db_bulk_dialog_open", False):
            bulk_import_professors_dialog()

        current_db = load_db()
        if not current_db:
            st.info(ui("尚无导师数据，点击右上角『新建导师』添加，或在『邮件记录』中创建。", "No professor data yet. Use 'Add professor' at the top right, or create via Email Records."))
        else:
            df = pd.DataFrame(current_db)

            # Ensure sorting/formatting of missing fields
            if "创建时间" not in df.columns:
                df["创建时间"] = df.get("更新时间", "未知")
            if "国家/地区" not in df.columns:
                df["国家/地区"] = "未知"
            if "学校名称" not in df.columns:
                df["学校名称"] = "未知"
            if "院系" not in df.columns:
                df["院系"] = "未知"

            df.fillna("-", inplace=True)

            # Create Filters
            col_f1, col_f2, col_f3, _col_f4 = st.columns([1, 1, 1, 1])
            countries = [ui("所有", "All")] + sorted(list(df["国家/地区"].unique()))
            selected_country = col_f1.selectbox(ui("筛选国家", "Filter Country"), countries)

            # Filter univs based on country
            if selected_country != ui("所有", "All"):
                univs_in_country = df[df["国家/地区"] == selected_country]["学校名称"].unique()
            else:
                univs_in_country = df["学校名称"].unique()

            univs = [ui("所有", "All")] + sorted(list(univs_in_country))
            selected_univ = col_f2.selectbox(ui("筛选学校", "Filter University"), univs)

            # Contact status filter: 阶段 == "未联系" 视为未联系，其余视为已联系
            status_all = ui("所有", "All")
            status_contacted = ui("已联系", "Contacted")
            status_uncontacted = ui("未联系", "Not contacted")
            selected_status = col_f3.selectbox(ui("联系状态", "Contact status"),
                                               [status_all, status_contacted, status_uncontacted])

            # Apply Filters
            filtered_df = df
            if selected_country != ui("所有", "All"):
                filtered_df = filtered_df[filtered_df["国家/地区"] == selected_country]
            if selected_univ != ui("所有", "All"):
                filtered_df = filtered_df[filtered_df["学校名称"] == selected_univ]
            if selected_status != status_all and "阶段" in filtered_df.columns:
                is_uncontacted = filtered_df["阶段"].astype(str) == "未联系"
                filtered_df = filtered_df[~is_uncontacted if selected_status == status_contacted else is_uncontacted]

            st.markdown(f"#### {ui('导师列表', 'Professor List')}")
            rows = list(filtered_df.reset_index().iterrows())
            visible_idxs = [int(r["index"]) for _, r in rows]

            # 批量操作栏：仅在「批量删除模式」开启时显示
            selected_idxs = [vi for vi in visible_idxs if st.session_state.get(f"db_sel_{vi}", False)]
            if bulk_mode:
                bc1, bc2, bc3 = st.columns([1, 1, 2])
                if bc1.button(ui("全选当前", "Select all"), key="db_bulk_select_all", use_container_width=True):
                    for vi in visible_idxs:
                        st.session_state[f"db_sel_{vi}"] = True
                    st.rerun()
                if bc2.button(ui("清除选择", "Clear"), key="db_bulk_clear", use_container_width=True):
                    for vi in visible_idxs:
                        st.session_state[f"db_sel_{vi}"] = False
                    st.rerun()
                if selected_idxs:
                    if bc3.button(ui(f"删除选中（{len(selected_idxs)}）", f"Delete selected ({len(selected_idxs)})"),
                                  key="db_bulk_delete_btn", type="primary", use_container_width=True):
                        bulk_delete_dialog(list(selected_idxs))
                else:
                    bc3.caption(ui("勾选卡片左上角的复选框可批量删除。", "Tick the box on each card to bulk-delete."))

            per_row = 3
            for i in range(0, len(rows), per_row):
                cols = st.columns(per_row)
                for j in range(per_row):
                    if i + j >= len(rows):
                        break
                    _, row = rows[i + j]
                    real_idx = int(row["index"])
                    local_time = format_local_time(row.get("国家/地区", "")).replace("未知", ui("未知", "Unknown"))
                    prof = str(row.get("导师/教授", "-"))
                    homepage = str(row.get("主页链接", "") or "")
                    with cols[j]:
                        with st.container(border=True):
                            if bulk_mode:
                                st.checkbox(ui("选择", "Select"), key=f"db_sel_{real_idx}")
                            if homepage.strip():
                                st.markdown(f"#### <a href='{homepage}' target='_blank' style='text-decoration:none;color:inherit;'>{prof}</a>", unsafe_allow_html=True)
                            else:
                                st.markdown(f"#### {prof}")
                            _src = str(row.get("来源", "") or "").strip()
                            _src_badge = ""
                            if _src and _src != "-":
                                _src_color = "#34d399" if _src == ui("批量导入", "Bulk import") or _src == "批量导入" else "#93c5fd"
                                _src_badge = (f"<span style='margin-left:.4rem;font-size:11px;padding:1px 7px;border-radius:8px;"
                                              f"background:rgba(148,163,184,.15);color:{_src_color};border:1px solid {_src_color}55;'>"
                                              f"{html.escape(_src)}</span>")
                            qs_rank = str(row.get("QS排名", "") or "").strip()
                            qs_badge = ""
                            if qs_rank and qs_rank not in ("-", "0"):
                                qs_badge = (f"<span style='margin-left:.4rem;font-size:11px;padding:1px 7px;border-radius:8px;"
                                            f"background:rgba(167,139,250,.15);color:#c4b5fd;border:1px solid #a78bfa55;'>QS #{html.escape(qs_rank)}</span>")
                            usnews_rank = str(
                                row.get("USNews排名", "")
                                or usnews_rank_for(row.get("学校名称", ""))
                                or ""
                            ).strip()
                            usnews_badge = ""
                            if usnews_rank and usnews_rank not in ("-", "0"):
                                usnews_badge = (
                                    f"<span style='margin-left:.4rem;font-size:11px;padding:1px 7px;border-radius:8px;"
                                    f"background:rgba(52,211,153,.12);color:#6ee7b7;border:1px solid #34d39955;'>"
                                    f"US News #{html.escape(usnews_rank)}</span>"
                                )
                            _waiting = outreach_waiting_state(row)
                            _waiting_badge = ""
                            if _waiting:
                                _waiting_label = (ui("待 Follow up", "Follow up due") if _waiting == "follow_up"
                                                  else ui("14 天未回复", "No reply for 14 days"))
                                _waiting_badge = (
                                    f"<span style='margin-left:.4rem;font-size:11px;padding:1px 7px;border-radius:8px;"
                                    f"background:rgba(245,158,11,.12);color:#fbbf24;border:1px solid #f59e0b55;'>"
                                    f"{html.escape(_waiting_label)}</span>"
                                )
                            st.markdown(
                                f"<span class='tag'>{row.get('阶段', '-')}</span>{_waiting_badge}{_src_badge}{qs_badge}{usnews_badge}",
                                unsafe_allow_html=True,
                            )
                            st.markdown(f"**{row.get('学校名称', '-')}**")
                            dept = str(row.get("院系", "") or "").strip()
                            st.caption(f"{row.get('国家/地区', '-')}" + (f" · {dept}" if dept and dept != "-" else ""))
                            st.caption(ui("研究方向：", "Research: ") + str(row.get("研究方向", "-")))
                            st.markdown(
                                f"<div style='margin:.3rem 0;color:#c9b8ff;font-size:15px;"
                                f"font-weight:650;font-variant-numeric:tabular-nums;'>"
                                f"{ui('导师当地时间', 'Professor local time')}："
                                f"{html.escape(local_time)}</div>",
                                unsafe_allow_html=True,
                            )
                            st.caption(ui("更新：", "Updated: ") + str(row.get("更新时间", "-")))

                            llm_val = clean_note(row.get("LLM摘要", ""))
                            if llm_val:
                                st.markdown(
                                    f"<div style='margin:.35rem 0;padding:.45rem .6rem;border-left:3px solid #60a5fa;"
                                    f"background:rgba(96,165,250,.10);border-radius:6px;font-size:13px;color:#bfdbfe;"
                                    f"white-space:pre-wrap;'><b>LLM Summary</b> · {html.escape(llm_val)}</div>",
                                    unsafe_allow_html=True,
                                )

                            note_val = clean_note(row.get("备注", ""))
                            if note_val:
                                st.markdown(
                                    f"<div style='margin:.35rem 0;padding:.45rem .6rem;border-left:3px solid #a78bfa;"
                                    f"background:rgba(167,139,250,.10);border-radius:6px;font-size:13px;color:#d6cffb;"
                                    f"white-space:pre-wrap;'>{html.escape(note_val)}</div>",
                                    unsafe_allow_html=True,
                                )

                            edit_col, del_col = st.columns(2)
                            if edit_col.button(ui("编辑", "Edit"), key=f"db_edit_btn_{real_idx}", use_container_width=True):
                                for _sk in [kk for kk in st.session_state if str(kk).startswith(f"edit_{real_idx}_")]:
                                    st.session_state.pop(_sk, None)
                                edit_professor_dialog(real_idx)
                            if del_col.button(ui("删除", "Delete"), key=f"db_del_btn_{real_idx}", use_container_width=True):
                                st.session_state["db_delete_idx"] = real_idx

            if "db_delete_idx" in st.session_state:
                idx_to_del = st.session_state["db_delete_idx"]
                if 0 <= idx_to_del < len(current_db):
                    r = current_db[idx_to_del]
                    st.warning(ui(f"确认删除：{r.get('导师/教授', '未知')} - {r.get('学校名称', '未知')} ?",
                                  f"Confirm deletion: {r.get('导师/教授', 'Unknown')} - {r.get('学校名称', 'Unknown')} ?"))
                    d1, d2 = st.columns(2)
                    if d1.button(ui("取消", "Cancel"), key="db_del_cancel", use_container_width=True):
                        st.session_state.pop("db_delete_idx", None)
                        st.rerun()
                    if d2.button(ui("确认删除", "Confirm Delete"), key="db_del_confirm", use_container_width=True, type="primary"):
                        purge_lite_emails_for_record(current_db[idx_to_del])
                        current_db.pop(idx_to_del)
                        save_db(current_db)
                        st.session_state.pop("db_delete_idx", None)
                        st.success(ui("已删除。", "Deleted."))
                        st.rerun()

    elif menu == templates_menu_label:
        st.title(ui("套瓷信模版", "Cold-Email Templates"))
        st.markdown(
            f"<p style='color: #9b9ba3; margin-bottom: 1rem;'>"
            f"{ui('在这里手写你自己的套瓷信模版，编辑即实时保存到本地。可建多个模版分场景使用。', 'Write your own cold-email templates here — every edit is saved locally in real time. Create multiple templates for different scenarios.')}"
            f"</p>",
            unsafe_allow_html=True,
        )

        templates = load_templates()

        _tpl_toast = st.session_state.pop("tpl_toast", None)
        if _tpl_toast:
            st.toast(_tpl_toast, icon="✅")

        # 新建模版按钮在选择器之前渲染：点击后立即 rerun，选择器本轮不会实例化，
        # 因此可以安全地把 tpl_picker 直接指向新模版。
        if st.button(ui("＋ 新建模版", "＋ New template"), type="primary", key="tpl_new_btn"):
            new_id = uuid.uuid4().hex
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            existing = [t2.get("name", "") for t2 in templates]
            base = ui("未命名模版", "Untitled template")
            name = base
            n = 2
            while name in existing:
                name = f"{base} {n}"
                n += 1
            templates.append({"id": new_id, "name": name, "subject": "", "content": "", "updated_at": now})
            save_templates(templates)
            st.session_state["tpl_picker"] = new_id
            st.session_state["tpl_toast"] = ui("已新建模版", "Template created")
            st.rerun()

        if not templates:
            st.info(ui("还没有模版。点上方『＋ 新建模版』开始写你的第一份套瓷信模版。",
                       "No templates yet. Click '＋ New template' above to write your first one."))
        else:
            ids = [t2["id"] for t2 in templates]
            name_by_id = {t2["id"]: (clean_note(t2.get("name", "")) or ui("未命名模版", "Untitled template"))
                          for t2 in templates}
            # 规范化当前选择（须在实例化 radio 之前完成，删除后落到第一个）
            if st.session_state.get("tpl_picker") not in ids:
                st.session_state["tpl_picker"] = ids[0]

            left, right = st.columns([1, 2.4], gap="large")
            with left:
                st.caption(ui("我的模版", "My templates"))
                sel_id = st.radio(
                    ui("选择模版", "Select template"),
                    ids,
                    format_func=lambda i: name_by_id.get(i, i),
                    label_visibility="collapsed",
                    key="tpl_picker",
                )

            with right:
                sel = next(t2 for t2 in templates if t2["id"] == sel_id)
                name_key = f"tpl_name_{sel_id}"
                subject_key = f"tpl_subject_{sel_id}"
                content_key = f"tpl_content_{sel_id}"
                if name_key not in st.session_state:
                    st.session_state[name_key] = sel.get("name", "")
                if subject_key not in st.session_state:
                    st.session_state[subject_key] = sel.get("subject", "")
                if content_key not in st.session_state:
                    st.session_state[content_key] = sel.get("content", "")

                st.text_input(
                    ui("模版名称", "Template name"),
                    key=name_key,
                    on_change=_save_template_cb,
                    args=(sel_id,),
                )
                st.text_input(
                    ui("邮件主题", "Email subject"),
                    key=subject_key,
                    on_change=_save_template_cb,
                    args=(sel_id,),
                    placeholder=ui("套瓷信的邮件标题，例如：Prospective PhD student interested in [研究方向]",
                                   "Cold-email subject line, e.g. Prospective PhD student interested in [Research area]"),
                )
                st.text_area(
                    ui("模版内容", "Template content"),
                    key=content_key,
                    on_change=_save_template_cb,
                    args=(sel_id,),
                    height=420,
                    placeholder=ui("在此粘贴 / 手写你的套瓷信模版……可用占位符如 [导师姓名]、[研究方向]。",
                                   "Paste or type your cold-email template here… use placeholders like [Professor], [Research area]."),
                )
                cap_l, cap_r = st.columns([3, 1], vertical_alignment="center")
                cap_l.caption(ui(f"✅ 已自动保存 · 更新于 {sel.get('updated_at', '-')}",
                                 f"✅ Auto-saved · updated {sel.get('updated_at', '-')}"))
                # 删除按钮在 radio 之后：不能改动已实例化的 tpl_picker，交给下轮规范化重选。
                # 先弹出确认，再真正删除。
                if cap_r.button(ui("🗑 删除此模版", "🗑 Delete"), key=f"tpl_del_{sel_id}", use_container_width=True):
                    st.session_state["tpl_delete_id"] = sel_id
                    st.rerun()

                if st.session_state.get("tpl_delete_id") == sel_id:
                    st.warning(ui(f"确认删除模版「{name_by_id.get(sel_id, '')}」？此操作不可撤销。",
                                  f"Delete template “{name_by_id.get(sel_id, '')}”? This cannot be undone."))
                    dc1, dc2 = st.columns(2)
                    if dc1.button(ui("取消", "Cancel"), key=f"tpl_del_cancel_{sel_id}", use_container_width=True):
                        st.session_state.pop("tpl_delete_id", None)
                        st.rerun()
                    if dc2.button(ui("确认删除", "Confirm delete"), key=f"tpl_del_confirm_{sel_id}",
                                  type="primary", use_container_width=True):
                        remaining = [t2 for t2 in templates if t2["id"] != sel_id]
                        save_templates(remaining)
                        st.session_state.pop("tpl_delete_id", None)
                        st.session_state["tpl_toast"] = ui("已删除模版", "Template deleted")
                        st.rerun()

    elif menu == t("menu_interview"):
        st.title(ui("面试准备舱", "Interview Prep"))
        st.markdown(f"<p style='color: #9b9ba3; margin-bottom: 1rem;'>{ui('可对任意导师提前准备面试；已预约面试会显示具体时间。', 'Prepare for interviews with any professor; scheduled interviews show exact time.')}</p>", unsafe_allow_html=True)

        current_db = load_db()
        scheduled_count = len([r for r in current_db if r.get("阶段") == "面试预约阶段"])
        c_m1, c_m2 = st.columns(2)
        c_m1.metric(ui("导师总数", "Total Professors"), len(current_db))
        c_m2.metric(ui("已预约面试导师数", "Scheduled Interviews"), scheduled_count)

        if not current_db:
            st.info(ui("当前没有导师记录。", "No professor records currently available."))
        else:
            scheduled_records = get_interview_records(current_db)
            scheduled_idx_set = {x.get("idx") for x in scheduled_records}
            scheduled_no_time = []
            unscheduled_records = []
            for idx, row in enumerate(current_db):
                if row.get("阶段") == "面试预约阶段":
                    if idx not in scheduled_idx_set:
                        scheduled_no_time.append({"idx": idx, "row": row, "raw_time": row.get("面试时间", "")})
                else:
                    unscheduled_records.append({"idx": idx, "row": row})

            def render_interview_item(idx, row):
                status_show = row.get("阶段", "未联系")
                intv = row.get("面试时间", "")
                if status_show == "面试预约阶段" and intv:
                    title = f"{row.get('导师/教授', '未知导师')} | {row.get('学校名称', '未知学校')} | 状态: {status_show} | 面试: {intv}"
                else:
                    title = f"{row.get('导师/教授', '未知导师')} | {row.get('学校名称', '未知学校')} | 状态: {status_show}"
                with st.expander(title, expanded=False):
                    st.write(f"**院系**: {row.get('院系', '未知')}")
                    st.write(f"**研究方向**: {row.get('研究方向', '未明确')}")
                    hp = row.get("主页链接", "")
                    if hp:
                        st.markdown(f"**{ui('主页链接', 'Homepage')}**: [{hp}]({hp})")
                    else:
                        st.warning(ui("该导师尚未保存主页链接，Scholar 命中率可能下降。",
                                      "Homepage URL is missing; Scholar hit rate may be lower."))

                    scholar_url = row.get("Scholar链接", "")
                    if scholar_url:
                        st.markdown(f"**Google Scholar**: [{scholar_url}]({scholar_url})")

                    cached_papers = row.get("最近论文", [])
                    if isinstance(cached_papers, list) and cached_papers:
                        st.caption(ui(f"最近论文更新时间: {row.get('最近论文更新时间', '未知')}",
                                      f"Papers updated: {row.get('最近论文更新时间', 'Unknown')}"))
                        st.dataframe(pd.DataFrame(cached_papers), use_container_width=True, hide_index=True)

                    cached_questions = row.get("面试问题", [])
                    if isinstance(cached_questions, list) and cached_questions:
                        with st.expander(ui("AI 面试问题（基于最近论文）", "AI Interview Questions (from recent papers)"), expanded=False):
                            st.caption(ui(f"问题更新时间: {row.get('面试问题更新时间', '未知')}",
                                          f"Questions updated: {row.get('面试问题更新时间', 'Unknown')}"))
                            for qi, q in enumerate(cached_questions, start=1):
                                q_col, mark_col = st.columns([8.8, 1.2])
                                with q_col:
                                    st.markdown(f"{qi}. {q}")
                                with mark_col:
                                    mark_clicked = st.button(ui("标记", "Mark"), key=f"mark_high_freq_{idx}_{qi}", help=ui("标记为高频考察点", "Mark as high-frequency question"))
                                if mark_clicked:
                                    cfg = load_config()
                                    resume_text = get_active_resume_text(cfg)
                                    if not resume_text:
                                        st.warning(ui("请先在【我的简历】上传并设置当前使用简历。", "Please upload and set an active resume first."))
                                    else:
                                        with st.spinner(ui("AI 正在生成该考察点的建议回答...", "AI is generating a suggested answer...")):
                                            ai_cfg = _ai_cfg_with_app_lang(cfg)
                                            ok_hf, hf_payload, hf_raw = generate_high_frequency_answer(
                                                question=str(q),
                                                prof_name=row.get("导师/教授", ""),
                                                univ_name=row.get("学校名称", ""),
                                                research_direction=row.get("研究方向", ""),
                                                homepage_url=hp,
                                                homepage_text=get_homepage_text_excerpt(hp, limit=3000) if hp else "",
                                                papers=cached_papers if isinstance(cached_papers, list) else [],
                                                resume_text=resume_text,
                                                config=ai_cfg,
                                            )
                                        if ok_hf:
                                            current_points = current_db[idx].get("高频考察点", [])
                                            if not isinstance(current_points, list):
                                                current_points = []

                                            existed = False
                                            for item in current_points:
                                                if isinstance(item, dict) and str(item.get("question", "")).strip() == str(q).strip():
                                                    item["ai_answer"] = hf_payload.get("suggested_answer", "")
                                                    item["key_points"] = hf_payload.get("key_points", [])
                                                    item["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                                    existed = True
                                                    break
                                            if not existed:
                                                current_points.append(
                                                    {
                                                        "question": str(q).strip(),
                                                        "ai_answer": hf_payload.get("suggested_answer", ""),
                                                        "key_points": hf_payload.get("key_points", []),
                                                        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                                    }
                                                )
                                            current_db[idx]["高频考察点"] = current_points
                                            save_db(current_db)
                                            st.success(ui("已收录到高频考察点，并生成 AI 建议回答。", "Saved to high-frequency list and generated AI answer."))
                                            st.rerun()
                                        else:
                                            st.error(ui(f"生成建议回答失败：{hf_raw}", f"Failed to generate suggested answer: {hf_raw}"))

                    high_freq_points = row.get("高频考察点", [])
                    if isinstance(high_freq_points, list) and high_freq_points:
                        with st.expander(ui("高频考察点", "High-Frequency Questions"), expanded=False):
                            for pi, item in enumerate(high_freq_points, start=1):
                                q_text = ""
                                a_text = ""
                                k_points = []
                                updated_at = ""
                                if isinstance(item, dict):
                                    q_text = str(item.get("question", "")).strip()
                                    a_text = str(item.get("ai_answer", "")).strip()
                                    raw_k = item.get("key_points", [])
                                    if isinstance(raw_k, list):
                                        k_points = [str(x).strip() for x in raw_k if str(x).strip()]
                                    updated_at = str(item.get("updated_at", "")).strip()
                                elif isinstance(item, str):
                                    q_text = item.strip()

                                if not q_text:
                                    continue
                                st.markdown(f"**{pi}. {q_text}**")
                                if updated_at:
                                    st.caption(ui(f"更新时间: {updated_at}", f"Updated: {updated_at}"))
                                if a_text:
                                    st.markdown(f"**{ui('AI建议回答：', 'AI Suggested Answer:')}** {a_text}")
                                if k_points:
                                    st.markdown(f"**{ui('答题要点：', 'Key Points:')}** {'；'.join(k_points)}")
                                st.markdown("---")

                    cached_advice = row.get("面试建议", [])
                    if isinstance(cached_advice, list) and cached_advice:
                        with st.expander(ui("面试建议（简历 + 导师主页 + Scholar）", "Interview Advice (Resume + Homepage + Scholar)"), expanded=False):
                            st.caption(ui(f"建议更新时间: {row.get('面试建议更新时间', '未知')}",
                                          f"Advice updated: {row.get('面试建议更新时间', 'Unknown')}"))
                            for ai, adv in enumerate(cached_advice, start=1):
                                st.markdown(f"{ai}. {adv}")

                    b1, b2 = st.columns(2)
                    with b1:
                        if st.button(ui("模拟面试", "Mock Interview"), key=f"open_mock_interview_{idx}", use_container_width=True):
                            show_mock_interview_dialog(idx, row)

                    with b2:
                        gen_q = st.button(ui("生成5个高频面试问题", "Generate 5 High-Frequency Questions"), key=f"gen_questions_{idx}", use_container_width=True)
                    if gen_q:
                        with st.spinner(ui("AI 正在生成高频综合面试问题...", "AI is generating high-frequency interview questions...")):
                            cfg = load_config()
                            ai_cfg = _ai_cfg_with_app_lang(cfg)
                            ok, questions, raw = generate_interview_questions(
                                prof_name=row.get("导师/教授", ""),
                                univ_name=row.get("学校名称", ""),
                                research_direction=row.get("研究方向", ""),
                                papers=cached_papers if isinstance(cached_papers, list) else [],
                                config=ai_cfg,
                            )
                        if ok:
                            current_db[idx]["面试问题"] = questions
                            current_db[idx]["面试问题更新时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            save_db(current_db)
                            st.success(ui(f"已生成 {len(questions)} 条高频问题", f"Generated {len(questions)} high-frequency questions"))
                            st.rerun()
                        else:
                            st.error(ui(f"生成失败：{raw}", f"Generation failed: {raw}"))

                    if st.button(ui("生成面试建议（结合简历+导师）", "Generate Interview Advice (Resume + Professor)"), key=f"gen_advice_{idx}", use_container_width=True):
                        cfg = load_config()
                        ai_cfg = _ai_cfg_with_app_lang(cfg)
                        resume_text = get_active_resume_text(cfg)
                        if not resume_text:
                            st.warning(ui("请先在【我的简历】上传并设置当前使用简历。", "Please upload and set an active resume first."))
                        else:
                            with st.spinner(ui("AI 正在基于简历与导师研究方向生成面试建议...", "AI is generating interview advice from resume and professor profile...")):
                                homepage_text = get_homepage_text_excerpt(hp, limit=3000) if hp else ""
                                ok, advice, raw = generate_interview_advice(
                                    prof_name=row.get("导师/教授", ""),
                                    univ_name=row.get("学校名称", ""),
                                    research_direction=row.get("研究方向", ""),
                                    homepage_url=hp,
                                    homepage_text=homepage_text,
                                    papers=cached_papers if isinstance(cached_papers, list) else [],
                                    resume_text=resume_text,
                                    config=ai_cfg,
                                )
                            if ok:
                                current_db[idx]["面试建议"] = advice
                                current_db[idx]["面试建议更新时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                save_db(current_db)
                                st.success(ui(f"已生成 {len(advice)} 条面试建议", f"Generated {len(advice)} interview advice items"))
                                st.rerun()
                            else:
                                st.error(ui(f"生成失败：{raw}", f"Generation failed: {raw}"))

            st.markdown(f"### {ui('已预约面试的老师', 'Scheduled Interview Professors')}")
            if not scheduled_records and not scheduled_no_time:
                st.info(ui("当前没有已预约面试的老师。", "No scheduled interview professors."))
            else:
                for rec in scheduled_records:
                    render_interview_item(rec.get("idx"), rec.get("row", {}))
                for rec in scheduled_no_time:
                    render_interview_item(rec.get("idx"), rec.get("row", {}))

            st.markdown(f"### {ui('还没预约面试的老师', 'Not Yet Scheduled')}")
            if not unscheduled_records:
                st.info(ui("当前没有未预约面试的老师。", "No unscheduled professors."))
            else:
                for rec in unscheduled_records:
                    render_interview_item(rec.get("idx"), rec.get("row", {}))

    elif menu == review_menu_label:
        st.title(ui("面试回顾", "Interview Review"))
        st.markdown(
            f"<p style='color: #9b9ba3; margin-bottom: 1rem;'>"
            f"{ui('以一场面试为单位，记录面试老师（从导师库选择）、面试学校与面试内容，方便复盘。', 'Log each interview as a unit — the interviewer (picked from your Professor DB), the school, and what was asked — for later review.')}"
            f"</p>",
            unsafe_allow_html=True,
        )

        reviews = load_interview_reviews()
        review_db = load_db()

        _rv_toast = st.session_state.pop("rv_toast", None)
        if _rv_toast:
            st.toast(_rv_toast, icon="✅")

        MANUAL_PROF = "__manual__"

        # ==== 新建一场面试回顾 ====
        with st.expander(ui("＋ 新建面试回顾", "＋ New interview review"), expanded=not reviews):
            if not review_db:
                st.warning(ui("导师库为空，请先到『导师库管理』添加老师；也可在下方手动填写面试老师姓名。",
                              "Professor DB is empty — add someone on the Professor DB page, or type the interviewer's name manually below."))

            prof_opts = {MANUAL_PROF: ui("手动输入老师", "Type manually")}
            for i, r in enumerate(review_db):
                label = f"{r.get('导师/教授', '?')} · {r.get('学校名称', '?')}"
                prof_opts[i] = label

            sel_prof = st.selectbox(
                ui("面试老师（从导师库选择）", "Interviewer (pick from Professor DB)"),
                list(prof_opts.keys()),
                format_func=lambda k: prof_opts[k],
                key="rv_new_prof",
            )

            if sel_prof == MANUAL_PROF:
                new_prof_name = st.text_input(ui("面试老师姓名", "Interviewer name"), key="rv_new_prof_manual").strip()
                default_univ = ""
            else:
                new_prof_name = str(review_db[sel_prof].get("导师/教授", "")).strip()
                default_univ = str(review_db[sel_prof].get("学校名称", "")).strip()

            rc1, rc2 = st.columns([2, 1])
            with rc1:
                new_univ = st.text_input(ui("面试学校", "Interview school"),
                                         value=default_univ, key=f"rv_new_univ_{sel_prof}")
            with rc2:
                new_date = st.date_input(ui("面试日期", "Interview date"),
                                         value=datetime.now().date(), key="rv_new_date")

            new_content = st.text_area(
                ui("面试内容", "Interview content"),
                key="rv_new_content", height=200,
                placeholder=ui("记录面试考察的问题、你的回答、老师的反馈、整体感受等……",
                               "Record the questions asked, your answers, the interviewer's feedback, overall impression…"),
            )

            if st.button(ui("保存面试回顾", "Save review"), type="primary", key="rv_new_save"):
                if not new_prof_name:
                    st.error(ui("请填写面试老师姓名。", "Please provide the interviewer's name."))
                elif not str(new_content).strip():
                    st.error(ui("请填写面试内容。", "Please fill in the interview content."))
                else:
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    reviews.append({
                        "id": uuid.uuid4().hex,
                        "prof_name": new_prof_name,
                        "univ_name": str(new_univ).strip(),
                        "interview_date": new_date.strftime("%Y-%m-%d"),
                        "content": str(new_content).strip(),
                        "created_at": now,
                        "updated_at": now,
                    })
                    save_interview_reviews(reviews)
                    for _k in ("rv_new_content", "rv_new_prof_manual"):
                        st.session_state.pop(_k, None)
                    st.session_state["rv_toast"] = ui("已保存面试回顾", "Review saved")
                    st.rerun()

        # ==== 已有的面试回顾列表 ====
        if not reviews:
            st.info(ui("还没有面试回顾。用上方『＋ 新建面试回顾』记录你的第一场面试。",
                       "No reviews yet. Use '＋ New interview review' above to log your first interview."))
        else:
            st.markdown(f"### {ui('我的面试回顾', 'My interview reviews')} · {len(reviews)}")
            ordered = sorted(reviews, key=lambda x: str(x.get("interview_date", "")), reverse=True)
            prof_name_by_key = {i: f"{r.get('导师/教授', '?')} · {r.get('学校名称', '?')}" for i, r in enumerate(review_db)}

            for rv in ordered:
                rid = rv.get("id")
                title_bits = [rv.get("prof_name", "") or ui("未知老师", "Unknown"),
                              rv.get("univ_name", "") or ui("未知学校", "Unknown school")]
                if rv.get("interview_date"):
                    title_bits.append(rv["interview_date"])
                with st.expander(" | ".join(title_bits), expanded=False):
                    name_k = f"rv_name_{rid}"
                    univ_k = f"rv_univ_{rid}"
                    content_k = f"rv_content_{rid}"
                    if name_k not in st.session_state:
                        st.session_state[name_k] = rv.get("prof_name", "")
                    if univ_k not in st.session_state:
                        st.session_state[univ_k] = rv.get("univ_name", "")
                    if content_k not in st.session_state:
                        st.session_state[content_k] = rv.get("content", "")

                    ec1, ec2 = st.columns(2)
                    with ec1:
                        st.text_input(ui("面试老师", "Interviewer"), key=name_k)
                    with ec2:
                        st.text_input(ui("面试学校", "Interview school"), key=univ_k)
                    st.text_area(ui("面试内容", "Interview content"), key=content_k, height=200)

                    st.caption(ui(f"创建于 {rv.get('created_at', '-')} · 更新于 {rv.get('updated_at', '-')}",
                                  f"Created {rv.get('created_at', '-')} · updated {rv.get('updated_at', '-')}"))

                    sc1, sc2 = st.columns([1, 1])
                    if sc1.button(ui("💾 保存修改", "💾 Save changes"), key=f"rv_save_{rid}",
                                  type="primary", use_container_width=True):
                        cur = load_interview_reviews()
                        for item in cur:
                            if item.get("id") == rid:
                                item["prof_name"] = st.session_state.get(name_k, "").strip()
                                item["univ_name"] = st.session_state.get(univ_k, "").strip()
                                item["content"] = st.session_state.get(content_k, "").strip()
                                item["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                break
                        save_interview_reviews(cur)
                        st.session_state["rv_toast"] = ui("已保存修改", "Changes saved")
                        st.rerun()

                    if sc2.button(ui("🗑 删除", "🗑 Delete"), key=f"rv_del_{rid}", use_container_width=True):
                        st.session_state["rv_delete_id"] = rid
                        st.rerun()

                    if st.session_state.get("rv_delete_id") == rid:
                        st.warning(ui("确认删除这场面试回顾？此操作不可撤销。",
                                      "Delete this interview review? This cannot be undone."))
                        dc1, dc2 = st.columns(2)
                        if dc1.button(ui("取消", "Cancel"), key=f"rv_del_cancel_{rid}", use_container_width=True):
                            st.session_state.pop("rv_delete_id", None)
                            st.rerun()
                        if dc2.button(ui("确认删除", "Confirm delete"), key=f"rv_del_confirm_{rid}",
                                      type="primary", use_container_width=True):
                            remaining = [x for x in load_interview_reviews() if x.get("id") != rid]
                            save_interview_reviews(remaining)
                            for _k in (name_k, univ_k, content_k):
                                st.session_state.pop(_k, None)
                            st.session_state.pop("rv_delete_id", None)
                            st.session_state["rv_toast"] = ui("已删除面试回顾", "Review deleted")
                            st.rerun()

    elif menu == schoollist_menu_label:
        st.title(ui("院校榜单", "School List"))
        st.markdown(
            f"<p style='color: #9b9ba3; margin-bottom: 1rem;'>"
            f"{ui('常用申请地区的院校排名清单，供选校参考。', 'Ranking lists for common application regions, for school selection.')}</p>",
            unsafe_allow_html=True,
        )

        def _rank_table(rank_name_pairs, rank_col_label):
            df = pd.DataFrame(
                [{rank_col_label: rk, ui("学校", "University"): name} for rk, name in rank_name_pairs]
            )
            st.dataframe(df, use_container_width=True, hide_index=True, height=680,
                         column_config={rank_col_label: st.column_config.NumberColumn(width="small")})

        _extra_qs_countries = [
            ("🇸🇬", "新加坡", "Singapore", 5),
            ("🇲🇴", "澳门", "Macau", None),
            ("🇯🇵", "日本", "Japan", None),
            ("🇨🇦", "加拿大", "Canada", None),
            ("🇩🇪", "德国", "Germany", None),
            ("🇳🇱", "荷兰", "Netherlands", None),
            ("🇨🇭", "瑞士", "Switzerland", None),
            ("🇸🇪", "瑞典", "Sweden", None),
            ("🇩🇰", "丹麦", "Denmark", None),
            ("🇫🇮", "芬兰", "Finland", None),
            ("🇫🇷", "法国", "France", None),
            ("🇦🇹", "奥地利", "Austria", None),
            ("🇳🇴", "挪威", "Norway", None),
            ("🇮🇪", "爱尔兰", "Ireland", None),
            ("🇮🇹", "意大利", "Italy", None),
            ("🇪🇸", "西班牙", "Spain", None),
            ("🇵🇹", "葡萄牙", "Portugal", None),
            ("🇬🇷", "希腊", "Greece", None),
        ]
        _ranking_options = [
            ("australia", ui("🇦🇺 澳洲 QS 前 8", "🇦🇺 Australia · QS Top 8")),
            ("new_zealand", ui("🇳🇿 新西兰 QS 前 5", "🇳🇿 New Zealand · QS Top 5")),
            ("united_states", ui("🇺🇸 美国 US News 前 100", "🇺🇸 US · USNews Top 100")),
            ("united_kingdom", ui("🇬🇧 英国 QS 前 500", "🇬🇧 UK · QS Top 500")),
        ] + [
            (_country, ui(f"{_flag} {_country_cn} QS 前 {_limit or 500}",
                          f"{_flag} {_country} · QS Top {_limit or 500}"))
            for _flag, _country_cn, _country, _limit in _extra_qs_countries
        ]
        _selected_ranking = st.selectbox(
            ui("选择国家 / 榜单", "Select country / ranking"),
            [option_id for option_id, _label in _ranking_options],
            format_func=dict(_ranking_options).get,
            key="schoollist_ranking",
        )

        if _selected_ranking == "australia":
            st.caption(ui(f"数据来源：QS World University Rankings {QS_EDITION}（澳洲八大名校 Group of Eight）",
                          f"Source: QS World University Rankings {QS_EDITION} (Australia Group of Eight)"))
            _rank_table(qs_top_by_country("Australia", 8), ui("QS 排名", "QS Rank"))
        elif _selected_ranking == "new_zealand":
            st.caption(ui(f"数据来源：QS World University Rankings {QS_EDITION}（新西兰前 5）",
                          f"Source: QS World University Rankings {QS_EDITION} (New Zealand top 5)"))
            _rank_table(qs_top_by_country("New Zealand", 5), ui("QS 排名", "QS Rank"))
        elif _selected_ranking == "united_states":
            _usnews = usnews_top100_list()
            st.caption(ui(f"数据来源：U.S. News & World Report {USNEWS_EDITION} 全美大学排名（Best National Universities，含并列，共 {len(_usnews)} 所）",
                          f"Source: U.S. News & World Report {USNEWS_EDITION} Best National Universities ({len(_usnews)} schools incl. ties)"))
            _rank_table(_usnews, ui("US News 排名", "USNews Rank"))
        elif _selected_ranking == "united_kingdom":
            _uk = qs_top_by_country("United Kingdom")
            st.caption(ui(f"数据来源：QS World University Rankings {QS_EDITION}（英国进入全球前 500 的院校，共 {len(_uk)} 所）",
                          f"Source: QS World University Rankings {QS_EDITION} (UK universities within the global top 500, {len(_uk)} total)"))
            _rank_table(_uk, ui("QS 排名", "QS Rank"))
        else:
            _flag, _country_cn, _country, _limit = next(
                item for item in _extra_qs_countries if item[2] == _selected_ranking
            )
            _schools = qs_top_by_country(_country, _limit)
            _scope_cn = f"{_country_cn}排名前 {_limit}" if _limit else f"{_country_cn}进入全球前 500 的院校"
            _scope_en = f"{_country} top {_limit}" if _limit else f"{_country} universities within the global top 500"
            st.caption(ui(
                f"数据来源：QS World University Rankings {QS_EDITION}"
                f"（{_scope_cn}，共 {len(_schools)} 所）",
                f"Source: QS World University Rankings {QS_EDITION} "
                f"({_scope_en}, {len(_schools)} total)",
            ))
            _rank_table(_schools, ui("QS 排名", "QS Rank"))

    elif menu == clock_menu_label:
        import pytz
        from zoneinfo import ZoneInfo

        st.title(ui("世界时钟", "World Clock"))
        st.markdown(
            f"<p style='color: #9b9ba3; margin-bottom: 1rem;'>"
            f"{ui('选择国家与城市，查看当地当前时间。', 'Pick a country and city to see its current local time.')}</p>",
            unsafe_allow_html=True,
        )
        # 每 15 秒自动刷新，保证分钟跳动
        st_autorefresh(interval=15000, key="world_clock_tick")

        def _tz_city(tz_name):
            return tz_name.split("/")[-1].replace("_", " ")

        def _render_clock(tz_name, title):
            try:
                now = datetime.now(ZoneInfo(tz_name))
            except Exception:
                st.warning(ui(f"无法解析时区：{tz_name}", f"Unknown timezone: {tz_name}"))
                return
            date_str = now.strftime("%Y-%m-%d %a")
            time_str = now.strftime("%H:%M")
            offset = now.strftime("%z")
            offset_fmt = f"UTC{offset[:3]}:{offset[3:]}" if offset else ""
            st.markdown(
                "<div style='padding:1rem 1.2rem;border:1px solid #26262c;border-radius:12px;"
                "background:rgba(142,107,239,.06);'>"
                f"<div style='color:#c9b8ff;font-size:20px;font-weight:700;line-height:1.25;'>{html.escape(title)}</div>"
                f"<div style='color:#8e8e96;font-size:13px;margin-top:.25rem;'>{date_str} · {offset_fmt}</div>"
                f"<div style='color:#ececee;font-size:44px;font-weight:700;line-height:1.1;"
                f"font-variant-numeric:tabular-nums;margin-top:.2rem;'>{time_str}</div>"
                "</div>",
                unsafe_allow_html=True,
            )

        # 国家（按名称排序）→ 城市（该国的 IANA 时区）
        country_items = sorted(pytz.country_names.items(), key=lambda kv: kv[1])
        country_codes = [code for code, _ in country_items if pytz.country_timezones.get(code)]
        country_label = {code: pytz.country_names[code] for code in country_codes}
        default_code = "US" if "US" in country_codes else country_codes[0]

        cc1, cc2 = st.columns(2)
        with cc1:
            sel_country = st.selectbox(
                ui("国家 / 地区", "Country / Region"),
                country_codes,
                index=country_codes.index(default_code),
                format_func=lambda c: country_label.get(c, c),
                key="wc_country",
            )
        tz_list = pytz.country_timezones.get(sel_country, [])
        with cc2:
            sel_tz = st.selectbox(
                ui("城市", "City"),
                tz_list,
                format_func=_tz_city,
                key=f"wc_city_{sel_country}",
            )

        # 已添加的时钟列表（保存标题，避免删除国家后丢失显示名）
        if "wc_added" not in st.session_state:
            _saved_clocks = load_config().get("world_clocks", [])
            st.session_state["wc_added"] = (
                _saved_clocks if isinstance(_saved_clocks, list) else []
            )  # [{"tz":..., "title":...}]

        if sel_tz:
            sel_title = f"{country_label.get(sel_country, sel_country)} · {_tz_city(sel_tz)}"
            prev_col, btn_col = st.columns([4, 1], vertical_alignment="center")
            with prev_col:
                _render_clock(sel_tz, sel_title)
            with btn_col:
                _already = any(x.get("tz") == sel_tz for x in st.session_state["wc_added"])
                if st.button(ui("＋ 添加", "＋ Add"), key="wc_add_btn",
                             use_container_width=True, disabled=_already,
                             help=ui("已在下方列表", "Already added") if _already else None):
                    st.session_state["wc_added"].append({"tz": sel_tz, "title": sel_title})
                    _clock_cfg = load_config()
                    _clock_cfg["world_clocks"] = st.session_state["wc_added"]
                    save_config(_clock_cfg)
                    st.rerun()

        st.divider()
        st.markdown(f"##### {ui('我的世界时钟', 'My World Clocks')}")
        added = st.session_state["wc_added"]
        if not added:
            st.info(ui("上面选好国家与城市，点「＋ 添加」把时钟固定到这里。",
                       "Pick a country and city above, then click ＋ Add to pin a clock here."))
        else:
            # 按国家 / 城市名（title）首字母排序显示
            display = sorted(added, key=lambda x: str(x.get("title", x.get("tz", ""))).lower())
            cols = st.columns(min(3, len(display)))
            for i, item in enumerate(display):
                with cols[i % len(cols)]:
                    _render_clock(item.get("tz", ""), item.get("title", item.get("tz", "")))
                    if st.button(ui("移除", "Remove"), key=f"wc_rm_{item.get('tz','')}",
                                 use_container_width=True):
                        # 按时区删除（时区唯一），避免排序后索引错位
                        st.session_state["wc_added"] = [
                            x for x in st.session_state["wc_added"] if x.get("tz") != item.get("tz")
                        ]
                        _clock_cfg = load_config()
                        _clock_cfg["world_clocks"] = st.session_state["wc_added"]
                        save_config(_clock_cfg)
                        st.rerun()

    elif menu == settings_menu_label:
        st.title(settings_menu_label)
        st.markdown(f"<p style='color: #9b9ba3; margin-bottom: 2rem;'>{ui('配置你的邮箱与系统连接参数，数据仅保存在本地文件中。', 'Configure email and system connection settings. Data is stored locally only.')}</p>", unsafe_allow_html=True)

        config = load_config()

        # Lite 模式关闭邮箱功能，这里只保留 AI 模型配置；Full 模式才显示邮箱绑定。
        if not lite_mode:
            st.subheader(ui("邮箱账号绑定", "Email Account Binding"))
            st.info(ui("""**常用邮箱配置指南：**
- **Gmail**: IMAP 为 `imap.gmail.com`，SMTP 为 `smtp.gmail.com`。(密码须用 [应用专用密码](https://myaccount.google.com/apppasswords))
- **个人版 Outlook/Hotmail**: IMAP 为 `outlook.office365.com`。(需生成 [个人应用密码](https://account.live.com/proofs/manage/additional))
- **大学/机构邮箱 (如 connect.polyu.hk)**: 
  - IMAP: `outlook.office365.com`
  - SMTP: `smtp.office365.com`
  - **密码获取**: 这是组织账户，请务必前往专属安全页：[https://mysignins.microsoft.com/security-info](https://mysignins.microsoft.com/security-info) 登录并添加“应用密码(App Password)”。
  - **如果连接一直被拒绝**: 学校可能禁用了IMAP基础认证。建议在网页版Outlook中设置【自动转发】到你个人的Gmail邮箱，然后在本系统绑定该Gmail，不仅稳定而且不用去学校后台折腾安全策略！
""", """**Common Email Setup Guide:**
- **Gmail**: IMAP `imap.gmail.com`, SMTP `smtp.gmail.com` (use [App Password](https://myaccount.google.com/apppasswords)).
- **Personal Outlook/Hotmail**: IMAP `outlook.office365.com` (set [App Password](https://account.live.com/proofs/manage/additional)).
- **University/Organization mailbox (e.g., connect.polyu.hk)**:
  - IMAP: `outlook.office365.com`
  - SMTP: `smtp.office365.com`
  - **Password setup**: For org accounts, visit [https://mysignins.microsoft.com/security-info](https://mysignins.microsoft.com/security-info) and add an App Password.
  - **If IMAP is rejected**: Your institution may disable basic auth. A practical fallback is auto-forwarding to Gmail and binding Gmail here.
"""))

            with st.form("email_config_form"):
                col1, col2 = st.columns(2)
                with col1:
                    email = st.text_input(ui("邮箱地址", "Email Address"), value=config.get("email", ""))
                    imap_server = st.text_input(ui("IMAP 服务器 (收件)", "IMAP Server (Inbox)"), value=config.get("imap_server", "imap.gmail.com"))
                with col2:
                    password = st.text_input(ui("应用密码 (App Password)", "App Password"), value=config.get("password", ""), type="password")
                    smtp_server = st.text_input(ui("SMTP 服务器 (发件)", "SMTP Server (Outbox)"), value=config.get("smtp_server", "smtp.gmail.com"))
                submit_btn = st.form_submit_button(ui("保存邮箱配置", "Save Email Config"))

            if submit_btn:
                if email and password:
                    config["email"] = email
                    config["password"] = password
                    config["imap_server"] = imap_server
                    config["smtp_server"] = smtp_server
                save_config(config)
                st.success(ui("邮箱及网络配置已成功保存！配置将持久化保留在本地。", "Email and network settings saved locally."))

        st.subheader(ui("AI 模型配置", "AI Model Config"))
        _ai_providers = ["通义千问 (Qwen)", "Google Gemini", "Claude (中转)"]
        _cur_provider = config.get("ai_provider", "通义千问 (Qwen)")
        ai_provider = st.selectbox(
            ui("选择 AI 分析引擎", "Select AI Engine"),
            _ai_providers,
            index=_ai_providers.index(_cur_provider) if _cur_provider in _ai_providers else 0,
            key="settings_ai_provider",
            on_change=_save_ai_settings_from_state,
        )
        qwen_api_key_stored = st.session_state.get("settings_qwen_api_key", config.get("qwen_api_key", ""))
        gemini_api_key_stored = st.session_state.get("settings_gemini_api_key", config.get("gemini_api_key", ""))
        claude_api_key_stored = st.session_state.get("settings_claude_api_key", config.get("claude_api_key", ""))
        if ai_provider == "通义千问 (Qwen)":
            st.text_input(
                ui("通义千问 API Key (sk-...)", "Qwen API Key (sk-...)"),
                type="password",
                value=qwen_api_key_stored,
                key="settings_qwen_api_key",
                on_change=_save_ai_settings_from_state,
            )
        elif ai_provider == "Google Gemini":
            st.text_input(
                "Gemini API Key (AIzaSy...)",
                type="password",
                value=gemini_api_key_stored,
                key="settings_gemini_api_key",
                on_change=_save_ai_settings_from_state,
            )
        else:  # Claude (中转)
            st.caption(ui("通过 Anthropic 兼容中转站调用 Claude（如 capi.aerolink.lat），无需科学上网。",
                          "Call Claude via an Anthropic-compatible relay (e.g. capi.aerolink.lat)."))
            st.text_input(
                ui("Claude API Key", "Claude API Key"),
                type="password",
                value=claude_api_key_stored,
                key="settings_claude_api_key",
                on_change=_save_ai_settings_from_state,
            )
            st.text_input(
                ui("中转地址 (Base URL)", "Relay Base URL"),
                value=st.session_state.get("settings_claude_base_url", config.get("claude_base_url", "https://capi.aerolink.lat/")),
                key="settings_claude_base_url",
                on_change=_save_ai_settings_from_state,
                placeholder="https://capi.aerolink.lat/",
            )
            st.text_input(
                ui("模型名", "Model"),
                value=st.session_state.get("settings_claude_model", config.get("claude_model", "claude-opus-4-8")),
                key="settings_claude_model",
                on_change=_save_ai_settings_from_state,
                placeholder="claude-opus-4-8",
            )

        # === AI API 连通性测试模块 ===
        provider = ai_provider
        st.markdown(f"### {ui('', '')} {provider} {ui('连通性测试', 'Connectivity Test')}")
        
        if provider == "通义千问 (Qwen)":
            test_api_key = qwen_api_key_stored
        elif provider == "Claude (中转)":
            test_api_key = claude_api_key_stored
        else:
            test_api_key = gemini_api_key_stored

        if test_api_key:
            if st.button(ui(f"测试 {provider} 接口", f"Test {provider} API"), type="secondary"):
                with st.spinner(ui("正在连接 AI 服务器进行测试...", "Connecting to AI server for testing...")):
                    try:
                        if provider == "通义千问 (Qwen)":
                            client = OpenAI(
                                api_key=test_api_key,
                                base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                            )
                            completion = client.chat.completions.create(
                                model="qwen-plus",
                                messages=[{'role': 'user', 'content': "Please strictly reply: 'API is working!' in English without any other words."}]
                            )
                            resp_text = completion.choices[0].message.content
                            st.success(ui(f"**测试通过！** 模型回复内容: `{resp_text.strip()}`", f"**Test passed!** Response: `{resp_text.strip()}`"))

                        elif provider == "Claude (中转)":
                            _test_cfg = {
                                "claude_api_key": test_api_key,
                                "claude_base_url": st.session_state.get("settings_claude_base_url", config.get("claude_base_url", "")),
                                "claude_model": st.session_state.get("settings_claude_model", config.get("claude_model", "")),
                            }
                            ok_c, resp_c = test_claude_connection(_test_cfg)
                            if ok_c:
                                st.success(ui(f"**测试通过！** 模型回复内容: `{resp_c}`", f"**Test passed!** Response: `{resp_c}`"))
                            else:
                                st.error(ui("连通失败！", "Connectivity test failed: ") + str(resp_c))

                        else: # Gemini
                            genai.configure(api_key=test_api_key)
                            resp, used_model = _gemini_generate_content_with_fallback(
                                "Please strictly reply: 'API is working!' in English without any other words.",
                                stream=False,
                            )
                            resp_text = (getattr(resp, "text", "") or "").strip()
                            st.success(
                                ui(
                                    f"**测试通过！** 模型 `{used_model}` 回复内容: `{resp_text}`",
                                    f"**Test passed!** Model `{used_model}` response: `{resp_text}`",
                                )
                            )
                    
                    except Exception as e:
                        st.error(ui("连通失败！", "Connectivity test failed: ") + str(e))
        else:
            st.info(ui(f"请先在上方填入 {provider} API Key。", f"Please enter your {provider} API key above first."))
        # ==================================
                
        # 始终为已配置的邮箱显示最新 5 封邮件
        if config.get("email") and config.get("password"):
            st.subheader(ui("连接状态与最新邮件", "Connection Status & Latest Emails"))
            with st.spinner(ui("正在挂载 IMAP 协议并拉取近期邮件...", "Mounting IMAP and fetching recent emails...")):
                success, result = test_imap_connection(config["email"], config["password"], config["imap_server"])
                if success:
                    st.success(ui("连接成功！您的网络与邮箱均状态良好。", "Connection successful. Network and mailbox are healthy."))
                    st.markdown(f"#### {ui('最近收到的 5 封邮件：', 'Latest 5 Emails:')}")
                    st.dataframe(pd.DataFrame(result), use_container_width=True)
                else:
                    st.error(ui(f"连接被拒绝。这通常是因为密码错误、未开启 IMAP，或被安全组拦截。\\n\\n**错误详情：** `{result}`",
                                  f"Connection rejected. Usually caused by wrong password, IMAP disabled, or security policy.\\n\\n**Error:** `{result}`"))
                    st.info(ui("**自救指南：**\\n1. **Gmail 用户**: 必须使用 [App Password](https://myaccount.google.com/apppasswords) 代替登录密码。\\n2. **Outlook 用户**: 确保在设置中开启了 POP/IMAP 选项。\\n3. 检查 IMAP 服务器地址是否正确。",
                               "**Troubleshooting:**\\n1. **Gmail**: Use [App Password](https://myaccount.google.com/apppasswords) instead of account password.\\n2. **Outlook**: Ensure POP/IMAP is enabled.\\n3. Verify IMAP server address is correct."))

    else:
        st.title(menu)
        st.write(ui("该功能模块正在基于 UI UX Pro Max 设计规范开发中...", "This module is under development based on UI UX Pro Max design guidelines..."))

if __name__ == "__main__":
    main()
