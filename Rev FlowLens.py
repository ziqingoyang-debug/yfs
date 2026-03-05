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
2. 自动跳过前几行说明，只读取核心数据表
3. 统一并固定字段名称
4. 拆分 `source / medium`
5. 基于关键词规则标记 **Paid / Non-paid**
6. 可视化：
   - Paid vs Non-paid（Revenue / Purchases）
   - Paid 收入 / 订单数按 source 归因分摊（100% 或 50/50）
7. 支持下载清洗后的宽表 CSV
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

    out = pd.DataFrame(
        {src_name: sp[0], med_name: sp[1]},
        index=col.index
    )

    no_sep = ~col_norm.str.contains(r'[\/／]', regex=True)
    out.loc[no_sep, [src_name, med_name]] = "Unrecognized"
    out[src_name] = out[src_name].fillna("Unrecognized")
    out[med_name] = out[med_name].fillna("Unrecognized")
    return out

# ============================================================
# 主逻辑
# ============================================================
if uploaded_file is not None:
    try:
        # ----------------------------------------------------
        # 1. 读取 CSV（跳过前几行）
        # ----------------------------------------------------
        raw_text = uploaded_file.getvalue().decode("utf-8", errors="ignore").splitlines()
        csv_buffer = io.StringIO("\n".join(raw_text))
        df = pd.read_csv(csv_buffer, header=7)

        if 8 in df.index:
            df = df.drop(index=8)

        if df.shape[1] > 9:
            df = df.iloc[:, :9]

        # ----------------------------------------------------
        # 2. 重命名列
        # ----------------------------------------------------
        rename_map = {
            df.columns[0]: "Session default channel group",
            df.columns[1]: "Session source / medium",
            df.columns[2]: "First user source / medium",
            df.columns[3]: "Sessions",
            df.columns[4]: "Total users",
            df.columns[5]: "Add to carts",
            df.columns[6]: "Checkouts",
            df.columns[7]: "Purchases",
            df.columns[8]: "Total revenue"
        }
        df.rename(columns=rename_map, inplace=True)

        # ----------------------------------------------------
        # 3. 拆 source / medium
        # ----------------------------------------------------
        sm1 = split_source_medium(df["Session source / medium"], "source1", "medium1")
        sm2 = split_source_medium(df["First user source / medium"], "source2", "medium2")
        df = pd.concat([df, sm1, sm2], axis=1)

        # ----------------------------------------------------
        # 4. Paid / Non-paid 判定
        # ----------------------------------------------------
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

        # ----------------------------------------------------
        # 5. 数据预览
        # ----------------------------------------------------
        st.success("✅ Data cleaned successfully!")
        st.dataframe(df.head(20))

        # ====================================================
        # 📈 Revenue Distribution Visualization
        # ====================================================
        st.subheader("📈 Revenue Distribution Visualization")
        col1, col2 = st.columns(2)

        revenue_mother = (
            df.groupby("Paid or Non-paid")["Total revenue"]
              .sum()
              .reset_index()
        )
        revenue_mother = revenue_mother[revenue_mother["Paid or Non-paid"] != "Unrecognized"]

        with col1:
            fig1 = px.pie(
                revenue_mother,
                names="Paid or Non-paid",
                values="Total revenue",
                title="Paid vs Non-paid (Revenue)",
                color_discrete_sequence=px.colors.qualitative.Set2
            )
            fig1.update_traces(textinfo="none", hovertemplate="%{label}<br>Revenue: %{value:,.0f}<br>Share: %{percent}")
            st.plotly_chart(fig1, use_container_width=True)

        paid_df = df[df["Paid or Non-paid"] == "Paid"].copy()
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
            if revenue_alloc:
                rev_df = pd.DataFrame(revenue_alloc.items(), columns=["Ad Channel", "Total revenue"])
                fig2 = px.pie(
                    rev_df,
                    names="Ad Channel",
                    values="Total revenue",
                    title="Ad Channels (Revenue)",
                    color_discrete_sequence=px.colors.qualitative.Pastel
                )
                fig2.update_traces(textinfo="none", hovertemplate="%{label}<br>Revenue: %{value:,.0f}<br>Share: %{percent}")
                st.plotly_chart(fig2, use_container_width=True)

        # ====================================================
        # 📈 Purchase Distribution Visualization
        # ====================================================
        st.subheader("📈 Purchase Distribution Visualization")
        col3, col4 = st.columns(2)

        purchase_mother = (
            df.groupby("Paid or Non-paid")["Purchases"]
              .sum()
              .reset_index()
        )
        purchase_mother = purchase_mother[purchase_mother["Paid or Non-paid"] != "Unrecognized"]

        with col3:
            fig3 = px.pie(
                purchase_mother,
                names="Paid or Non-paid",
                values="Purchases",
                title="Paid vs Non-paid (Purchases)",
                color_discrete_sequence=px.colors.qualitative.Set2
            )
            fig3.update_traces(textinfo="none", hovertemplate="%{label}<br>Purchases: %{value:,.0f}<br>Share: %{percent}")
            st.plotly_chart(fig3, use_container_width=True)

        purchase_alloc = {}

        for _, row in paid_df.iterrows():
            pur = int(row["Purchases"]) if not pd.isna(row["Purchases"]) else 0
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
            if purchase_alloc:
                pur_df = pd.DataFrame(purchase_alloc.items(), columns=["Ad Channel", "Purchases"])
                fig4 = px.pie(
                    pur_df,
                    names="Ad Channel",
                    values="Purchases",
                    title="Ad Channels (Purchases)",
                    color_discrete_sequence=px.colors.qualitative.Pastel
                )
                fig4.update_traces(textinfo="none", hovertemplate="%{label}<br>Purchases: %{value:,.0f}<br>Share: %{percent}")
                st.plotly_chart(fig4, use_container_width=True)

        # ----------------------------------------------------
        # 6. 下载 CSV
        # ----------------------------------------------------
        output = io.BytesIO()
        df.to_csv(output, index=False, encoding="utf-8-sig")
        st.download_button(
            "📥 Download cleaned CSV",
            data=output.getvalue(),
            file_name="cleaned_data.csv",
            mime="text/csv"
        )

    except Exception as e:
        st.error(f"❌ Error during data processing: {e}")
