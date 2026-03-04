# -*- coding: utf-8 -*-

import streamlit as st
import pandas as pd
import io
import plotly.express as px

# ============================================================
# 页面设置
# ============================================================
st.set_page_config(page_title="Custom Attribution Model", layout="wide")
st.title("📊 Custom Attribution Model")

st.markdown(
    """
**使用说明 / Logic Overview**

1. 上传 **GA4 导出的 CSV 文件**
2. 自动定位表头行并读取核心数据
3. 固定字段名称（Sessions / Purchases / Total revenue 等）
4. 拆分 `source / medium`
5. 基于关键词规则标记 **Paid / Non-paid**
6. 可视化（保留原有两块）：
   - Revenue：Paid vs Non-paid；Paid 内 Ad Channels
   - Purchases：Paid vs Non-paid；Paid 内 Ad Channels
7. 新增 funnel（只在 Paid 内）：
   - Paid funnel 总览：lower / middle / high / No funnel
   - Paid 渠道 × funnel：各渠道内部 funnel 构成
8. 支持下载清洗后的宽表 CSV
"""
)

uploaded_file = st.file_uploader("Upload original CSV file", type=["csv"])

# ============================================================
# 工具函数
# ============================================================
def normalize_space_series(s: pd.Series) -> pd.Series:
    return (
        s.astype(str)
         .str.replace('\u00A0', ' ', regex=False)
         .str.replace('\u2007', ' ', regex=False)
         .str.replace('\u202F', ' ', regex=False)
         .str.replace(r'\s+', ' ', regex=True)
         .str.strip()
    )

def split_source_medium(col: pd.Series, src_name: str, med_name: str) -> pd.DataFrame:
    col_norm = normalize_space_series(col)
    sp = col_norm.str.split(r'\s*[\/／]\s*', n=1, regex=True, expand=True)
    if sp.shape[1] == 1:
        sp[1] = None

    out = pd.DataFrame({src_name: sp[0], med_name: sp[1]}, index=col.index)

    no_sep = ~col_norm.str.contains(r'[\/／]', regex=True)
    out.loc[no_sep, [src_name, med_name]] = "Unrecognized"
    out[src_name] = out[src_name].fillna("Unrecognized")
    out[med_name] = out[med_name].fillna("Unrecognized")
    return out

def find_header_row(lines: list[str], header_key: str = "Session default channel group") -> int:
    """在原始文本行中定位真正的表头行（兼容 download.csv / funnel.csv）"""
    for i, line in enumerate(lines):
        if line.strip().startswith(header_key):
            return i
    raise ValueError(f"Cannot find header row by key: {header_key}")

def to_number_series(s: pd.Series) -> pd.Series:
    """把可能带逗号/空值的数字列转成 numeric"""
    return pd.to_numeric(
        s.astype(str).str.replace(",", "", regex=False).str.strip(),
        errors="coerce"
    )

def extract_funnel(x) -> str:
    """
    funnel = manual ad content 最后一段（按 '-' 切分）
    只认 lower/middle/high；其他/空/(not set) => No funnel
    """
    if pd.isna(x):
        return "No funnel"
    s = str(x).strip()
    if s == "" or s.lower() in {"(not set)", "not set", "nan", "none"}:
        return "No funnel"
    # 按 '-' 取末段
    last = s.split("-")[-1].strip().lower()
    if last in {"lower", "middle", "high"}:
        return last
    return "No funnel"

def allocate_paid_attribution(
    paid_df: pd.DataFrame,
    metric_col: str,
    paid_keywords: list[str],
) -> pd.DataFrame:
    """
    把 Paid 行按规则拆成“归因明细”：
    - 渠道分摊：source1/source2（100% 或 50/50）
    - funnel 分摊绑定渠道：给 source1 的那份用 funnel1；给 source2 的那份用 funnel2
    返回列：
    - Ad Channel
    - Funnel
    - value
    """
    rows = []

    for _, row in paid_df.iterrows():
        val = row.get(metric_col, 0)
        val = 0 if pd.isna(val) else float(val)

        m1 = str(row.get("medium1", "")).lower()
        m2 = str(row.get("medium2", "")).lower()
        s1 = row.get("source1", "Unrecognized")
        s2 = row.get("source2", "Unrecognized")

        f1 = row.get("funnel1", "No funnel")
        f2 = row.get("funnel2", "No funnel")

        has_m1 = any(k in m1 for k in paid_keywords)
        has_m2 = any(k in m2 for k in paid_keywords)

        if has_m1 and has_m2:
            rows.append({"Ad Channel": s1, "Funnel": f1, "value": val * 0.5})
            rows.append({"Ad Channel": s2, "Funnel": f2, "value": val * 0.5})
        elif has_m1 and not has_m2:
            rows.append({"Ad Channel": s1, "Funnel": f1, "value": val})
        elif has_m2 and not has_m1:
            rows.append({"Ad Channel": s2, "Funnel": f2, "value": val})

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    # 统一 funnel 展示顺序（可选）
    funnel_order = ["lower", "middle", "high", "No funnel"]
    out["Funnel"] = out["Funnel"].where(out["Funnel"].isin({"lower", "middle", "high"}), "No funnel")
    out["Funnel"] = pd.Categorical(out["Funnel"], categories=funnel_order, ordered=True)
    return out

# ============================================================
# 主逻辑
# ============================================================
if uploaded_file is not None:
    try:
        # ----------------------------------------------------
        # 1) 读取原始文本并自动定位表头
        # ----------------------------------------------------
        raw_text = uploaded_file.getvalue().decode("utf-8", errors="ignore").splitlines()
        header_row = find_header_row(raw_text, "Session default channel group")

        csv_buffer = io.StringIO("\n".join(raw_text))
        df_raw = pd.read_csv(csv_buffer, header=header_row)

        # ----------------------------------------------------
        # 2) 删除 Grand total 行（GA4 导出通常会有）
        # ----------------------------------------------------
        # 常见特征：维度列为空，最后一列出现 "Grand total"
        if df_raw.shape[1] >= 1:
            last_col = df_raw.columns[-1]
            is_grand_total = df_raw[last_col].astype(str).str.contains("Grand total", na=False)
            df_raw = df_raw[~is_grand_total].copy()

        # 再保险：删掉 Session default channel group 为空的行
        if "Session default channel group" in df_raw.columns:
            df_raw = df_raw[~df_raw["Session default channel group"].isna()].copy()

        # ----------------------------------------------------
        # 3) 只保留“核心列”（兼容旧/新底稿）
        # ----------------------------------------------------
        has_funnel_cols = (
            ("Session manual ad content" in df_raw.columns)
            and ("First user manual ad content" in df_raw.columns)
        )

        if has_funnel_cols:
            core_cols = [
                "Session default channel group",
                "Session source / medium",
                "Session manual ad content",
                "First user source / medium",
                "First user manual ad content",
                "Sessions",
                "Total users",
                "Add to carts",
                "Checkouts",
                "Purchases",
                "Total revenue",
            ]
        else:
            core_cols = [
                "Session default channel group",
                "Session source / medium",
                "First user source / medium",
                "Sessions",
                "Total users",
                "Add to carts",
                "Checkouts",
                "Purchases",
                "Total revenue",
            ]

        missing = [c for c in core_cols if c not in df_raw.columns]
        if missing:
            raise Value
