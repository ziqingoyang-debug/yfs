# -*- coding: utf-8 -*-

import streamlit as st
import pandas as pd
import io
import plotly.express as px

# ============================================================
# 工具函数
# ============================================================
def normalize_space_series(s: pd.Series) -> pd.Series:
    return (
        s.astype(str)
        .str.replace("\u00A0", " ", regex=False)
        .str.replace("\u2007", " ", regex=False)
        .str.replace("\u202F", " ", regex=False)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )

def split_source_medium(col: pd.Series, src_name: str, med_name: str) -> pd.DataFrame:
    col_norm = normalize_space_series(col)
    sp = col_norm.str.split(r"\s*[\/／]\s*", n=1, regex=True, expand=True)
    if sp.shape[1] == 1:
        sp[1] = None

    out = pd.DataFrame({src_name: sp[0], med_name: sp[1]}, index=col.index)

    no_sep = ~col_norm.str.contains(r"[\/／]", regex=True)
    out.loc[no_sep, [src_name, med_name]] = "Unrecognized"

    out[src_name] = out[src_name].fillna("Unrecognized")
    out[med_name] = out[med_name].fillna("Unrecognized")
    return out

def to_number_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(
        s.astype(str).str.replace(",", "", regex=False).str.strip(),
        errors="coerce"
    )

def find_header_row(lines, header_key="Session default channel group"):
    for i, line in enumerate(lines):
        if line.strip().startswith(header_key):
            return i
    return None  # 不 raise，交给上层 st.error

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
    last = s.split("-")[-1].strip().lower()
    if last in {"lower", "middle", "high"}:
        return last
    return "No funnel"

def allocate_paid_attribution(paid_df: pd.DataFrame, metric_col: str, paid_keywords: list) -> pd.DataFrame:
    """
    Paid 行拆成归因明细：
    - 渠道分摊：source1/source2（100% 或 50/50）
    - funnel 绑定渠道：source1 用 funnel1；source2 用 funnel2
    输出列：Ad Channel, Funnel, value
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

    # funnel 归一
    out["Funnel"] = out["Funnel"].where(out["Funnel"].isin({"lower", "middle", "high"}), "No funnel")
    return out

# ============================================================
# 主程序
# ============================================================
def main():
    # 页面设置
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
6. 保留原有可视化：
   - Revenue：Paid vs Non-paid；Paid 内 Ad Channels
   - Purchases：Paid vs Non-paid；Paid 内 Ad Channels
7. 新增 funnel（只在 Paid 内）：
   - Paid funnel 总览：lower / middle / high / No funnel
   - Paid 渠道 × funnel：各渠道内部 funnel 构成
8. 支持下载清洗后的宽表 CSV
"""
    )

    uploaded_file = st.file_uploader("Upload original CSV file", type=["csv"])
    if uploaded_file is None:
        return

    try:
        # 1) 读取文本 + 自动定位 header
        raw_lines = uploaded_file.getvalue().decode("utf-8", errors="ignore").splitlines()
        header_row = find_header_row(raw_lines, "Session default channel group")
        if header_row is None:
            st.error("❌ Cannot find the header row. 请确认是 GA4 导出的 CSV，并包含 'Session default channel group' 表头。")
            st.stop()

        csv_buffer = io.StringIO("\n".join(raw_lines))
        df_raw = pd.read_csv(csv_buffer, header=header_row)

        # 2) 删除 Grand total 行（常见：最后一列含 Grand total）
        if df_raw.shape[1] >= 1:
            last_col = df_raw.columns[-1]
            df_raw = df_raw[~df_raw[last_col].astype(str).str.contains("Grand total", na=False)].copy()

        # 3) 判断是否有 funnel 两列
        has_funnel_cols = ("Session manual ad content" in df_raw.columns) and ("First user manual ad content" in df_raw.columns)

        # 4) 选择核心列（兼容旧/新底稿）
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
            st.error(f"❌ Missing required columns: {missing}")
            st.stop()

        df = df_raw[core_cols].copy()

        # 5) 数值列转 numeric
        num_cols = ["Sessions", "Total users", "Add to carts", "Checkouts", "Purchases", "Total revenue"]
        for c in num_cols:
            df[c] = to_number_series(df[c]).fillna(0)

        # 6) 拆 source / medium
        sm1 = split_source_medium(df["Session source / medium"], "source1", "medium1")
        sm2 = split_source_medium(df["First user source / medium"], "source2", "medium2")
        df = pd.concat([df, sm1, sm2], axis=1)

        # 7) Paid / Non-paid 判定（沿用旧规则）
        paid_keywords = ["cpc", "paid", "shopping", "summersale"]

        def judge_paid(row):
            m1 = str(row["medium1"]).lower()
            m2 = str(row["medium2"]).lower()
            if m1 == "unrecognized" and m2 == "unrecognized":
                return "Unrecognized"
            if any(k in m1 for k in paid_keywords) or any(k in m2 for k in paid_keywords):
                return "Paid"
            return "Non-paid"

        df["Paid or Non-paid"] = df.apply(judge_paid, axis=1)

        # 8) funnel 提取
        if has_funnel_cols:
            df["funnel1"] = df["Session manual ad content"].apply(extract_funnel)
            df["funnel2"] = df["First user manual ad content"].apply(extract_funnel)
        else:
            df["funnel1"] = "No funnel"
            df["funnel2"] = "No funnel"

        # 9) 预览
        st.success("✅ Data cleaned successfully! Preview below:")
        st.dataframe(df.head(20))

        paid_df = df[df["Paid or Non-paid"] == "Paid"].copy()

        # ====================================================
        # 旧图 1：Revenue
        # ====================================================
        st.subheader("📈 Revenue Distribution Visualization")
        col1, col2 = st.columns(2)

        revenue_mother = df.groupby("Paid or Non-paid")["Total revenue"].sum().reset_index()
        revenue_mother = revenue_mother[revenue_mother["Paid or Non-paid"] != "Unrecognized"]

        with col1:
            fig1 = px.pie(
                revenue_mother,
                names="Paid or Non-paid",
                values="Total revenue",
                title="Paid vs Non-paid (Revenue)",
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            fig1.update_traces(textinfo="none", hovertemplate="%{label}<br>Revenue: %{value:,.0f}<br>Share: %{percent}")
            st.plotly_chart(fig1, use_container_width=True)

        # Paid 内渠道（Revenue）- 原逻辑
        revenue_alloc = {}
        for _, row in paid_df.iterrows():
            rev = float(row["Total revenue"]) if not pd.isna(row["Total revenue"]) else 0
            m1, m2 = str(row["medium1"]).lower(), str(row["medium2"]).lower()
            s1, s2 = row["source1"], row["source2"]
            has_m1 = any(k in m1 for k in paid_keywords)
            has_m2 = any(k in m2 for k in paid_keywords)

            if has_m1 and has_m2:
                revenue_alloc[s1] = revenue_alloc.get(s1, 0) + rev * 0.5
                revenue_alloc[s2] = revenue_alloc.get(s2, 0) + rev * 0.5
            elif has_m1:
                revenue_alloc[s1] = revenue_alloc.get(s1, 0) + rev
            elif has_m2:
                revenue_alloc[s2] = revenue_alloc.get(s2, 0) + rev

        with col2:
            if not revenue_alloc:
                st.warning("⚠️ No valid paid channels or revenue = 0.")
            else:
                right_df = pd.DataFrame(list(revenue_alloc.items()), columns=["Ad Channel", "Total revenue"]).sort_values(
                    by="Total revenue", ascending=False
                )
                fig2 = px.pie(
                    right_df,
                    names="Ad Channel",
                    values="Total revenue",
                    title="Ad Channels (Revenue)",
                    color_discrete_sequence=px.colors.qualitative.Pastel,
                )
                fig2.update_traces(textinfo="none", hovertemplate="%{label}<br>Revenue: %{value:,.0f}<br>Share: %{percent}")
                st.plotly_chart(fig2, use_container_width=True)

        # ====================================================
        # 旧图 2：Purchases
        # ====================================================
        st.subheader("📈 Purchase Distribution Visualization")
        col3, col4 = st.columns(2)

        purchase_mother = df.groupby("Paid or Non-paid")["Purchases"].sum().reset_index()
        purchase_mother = purchase_mother[purchase_mother["Paid or Non-paid"] != "Unrecognized"]

        with col3:
            fig3 = px.pie(
                purchase_mother,
                names="Paid or Non-paid",
                values="Purchases",
                title="Paid vs Non-paid (Purchases)",
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            fig3.update_traces(textinfo="none", hovertemplate="%{label}<br>Purchases: %{value:,.0f}<br>Share: %{percent}")
            st.plotly_chart(fig3, use_container_width=True)

        purchase_alloc = {}
        for _, row in paid_df.iterrows():
            pur = float(row["Purchases"]) if not pd.isna(row["Purchases"]) else 0
            m1, m2 = str(row["medium1"]).lower(), str(row["medium2"]).lower()
            s1, s2 = row["source1"], row["source2"]
            has_m1 = any(k in m1 for k in paid_keywords)
            has_m2 = any(k in m2 for k in paid_keywords)

            if has_m1 and has_m2:
                purchase_alloc[s1] = purchase_alloc.get(s1, 0) + pur * 0.5
                purchase_alloc[s2] = purchase_alloc.get(s2, 0) + pur * 0.5
            elif has_m1:
                purchase_alloc[s1] = purchase_alloc.get(s1, 0) + pur
            elif has_m2:
                purchase_alloc[s2] = purchase_alloc.get(s2, 0) + pur

        with col4:
            if not purchase_alloc:
                st.warning("⚠️ No valid paid channels or purchases = 0.")
            else:
                pur_df = pd.DataFrame(list(purchase_alloc.items()), columns=["Ad Channel", "Purchases"]).sort_values(
                    by="Purchases", ascending=False
                )
                fig4 = px.pie(
                    pur_df,
                    names="Ad Channel",
                    values="Purchases",
                    title="Ad Channels (Purchases)",
                    color_discrete_sequence=px.colors.qualitative.Pastel,
                )
                fig4.update_traces(textinfo="none", hovertemplate="%{label}<br>Purchases: %{value:,.0f}<br>Share: %{percent}")
                st.plotly_chart(fig4, use_container_width=True)

        # ====================================================
        # 新增：Paid funnel 总览 + 渠道×funnel
        # ====================================================
        st.subheader("🧩 Paid Funnel Overview (Paid only)")
        if not has_funnel_cols:
            st.info("当前文件不包含 manual ad content 字段（旧底稿），因此不展示 funnel 相关图表。")
        else:
            col5, col6 = st.columns(2)

            alloc_rev = allocate_paid_attribution(paid_df, "Total revenue", paid_keywords)
            with col5:
                if alloc_rev.empty or alloc_rev["value"].sum() == 0:
                    st.warning("⚠️ Paid funnel (Revenue) is empty or revenue = 0.")
                else:
                    funnel_rev = alloc_rev.groupby("Funnel")["value"].sum().reset_index()
                    fig5 = px.pie(
                        funnel_rev,
                        names="Funnel",
                        values="value",
                        title="Paid Funnel Share (Revenue)",
                        color_discrete_sequence=px.colors.qualitative.Set2,
                    )
                    fig5.update_traces(textinfo="none", hovertemplate="%{label}<br>Revenue: %{value:,.0f}<br>Share: %{percent}")
                    st.plotly_chart(fig5, use_container_width=True)

            alloc_pur = allocate_paid_attribution(paid_df, "Purchases", paid_keywords)
            with col6:
                if alloc_pur.empty or alloc_pur["value"].sum() == 0:
                    st.warning("⚠️ Paid funnel (Purchases) is empty or purchases = 0.")
                else:
                    funnel_pur = alloc_pur.groupby("Funnel")["value"].sum().reset_index()
                    fig6 = px.pie(
                        funnel_pur,
                        names="Funnel",
                        values="value",
                        title="Paid Funnel Share (Purchases)",
                        color_discrete_sequence=px.colors.qualitative.Set2,
                    )
                    fig6.update_traces(textinfo="none", hovertemplate="%{label}<br>Purchases: %{value:,.0f}<br>Share: %{percent}")
                    st.plotly_chart(fig6, use_container_width=True)

            st.subheader("📊 Paid Channel × Funnel Breakdown (Paid only)")
            col7, col8 = st.columns(2)

            with col7:
                if alloc_rev.empty or alloc_rev["value"].sum() == 0:
                    st.warning("⚠️ Channel×Funnel (Revenue) is empty or revenue = 0.")
                else:
                    tmp = alloc_rev.groupby(["Ad Channel", "Funnel"])["value"].sum().reset_index()
                    tmp["channel_total"] = tmp.groupby("Ad Channel")["value"].transform("sum")
                    tmp["share"] = tmp["value"] / tmp["channel_total"]
                    fig7 = px.bar(
                        tmp.sort_values(["channel_total", "Ad Channel"], ascending=[False, True]),
                        x="Ad Channel",
                        y="share",
                        color="Funnel",
                        title="Channel × Funnel Share (Revenue, within channel)",
                        hover_data={"value": ":,.0f", "share": ":.2%", "channel_total": False},
                    )
                    fig7.update_layout(yaxis_tickformat=".0%", xaxis_title="", yaxis_title="Share")
                    st.plotly_chart(fig7, use_container_width=True)

            with col8:
                if alloc_pur.empty or alloc_pur["value"].sum() == 0:
                    st.warning("⚠️ Channel×Funnel (Purchases) is empty or purchases = 0.")
                else:
                    tmp = alloc_pur.groupby(["Ad Channel", "Funnel"])["value"].sum().reset_index()
                    tmp["channel_total"] = tmp.groupby("Ad Channel")["value"].transform("sum")
                    tmp["share"] = tmp["value"] / tmp["channel_total"]
                    fig8 = px.bar(
                        tmp.sort_values(["channel_total", "Ad Channel"], ascending=[False, True]),
                        x="Ad Channel",
                        y="share",
                        color="Funnel",
                        title="Channel × Funnel Share (Purchases, within channel)",
                        hover_data={"value": ":,.0f", "share": ":.2%", "channel_total": False},
                    )
                    fig8.update_layout(yaxis_tickformat=".0%", xaxis_title="", yaxis_title="Share")
                    st.plotly_chart(fig8, use_container_width=True)

        # 下载
        output = io.BytesIO()
        df.to_csv(output, index=False, encoding="utf-8-sig")
        st.download_button("📥 Download cleaned CSV", data=output.getvalue(), file_name="cleaned_data.csv", mime="text/csv")

    except Exception as e:
        st.error(f"❌ Error during data processing: {e}")

# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    main()
