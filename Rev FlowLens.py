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
3. 固定字段名称（按列位置）
4. 拆分 `source / medium`
5. 标记 **Paid / Non-paid**（仍然只用 medium1 / medium2 判断）
6. 可视化（全部保留）：
   - Paid vs Non-paid（Revenue / Purchases）
   - Paid 渠道归因分摊（100% 或 50/50）
   - Paid Funnel 分布（Revenue / Purchases）
   - Paid Funnel by Channel（Revenue / Purchases，100% 堆叠图）
7. 支持输入 **真实总收入** 自动重分配 Revenue
8. 支持下载清洗后的 CSV
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

def extract_funnel(x):
    if pd.isna(x):
        return "No funnel"

    s = str(x).strip()
    if s == "" or s.lower() in {"(not set)", "not set", "nan", "none"}:
        return "No funnel"

    last = s.split("-")[-1].strip().lower()
    if last in ["lower", "middle", "high"]:
        return last

    return "No funnel"

def make_sm_key(source: str, medium: str) -> str:
    """
    ✅ 展示颗粒度改为 source/medium
    - Paid 判定仍然只用 medium1/medium2，不受影响
    - 这里仅用于展示与归因维度
    """
    s = "Unrecognized" if pd.isna(source) else str(source).strip()
    m = "Unrecognized" if pd.isna(medium) else str(medium).strip()
    if s.lower() == "unrecognized" or m.lower() == "unrecognized" or s == "" or m == "":
        return "Unrecognized"
    return f"{s} / {m}"


# ============================================================
# 主逻辑
# ============================================================
if uploaded_file is not None:

    try:
        # ----------------------------------------------------
        # 1. 读取 CSV（旧版强假设：GA4 导出结构固定）
        # ----------------------------------------------------
        raw_text = uploaded_file.getvalue().decode("utf-8", errors="ignore").splitlines()
        csv_buffer = io.StringIO("\n".join(raw_text))
        df = pd.read_csv(csv_buffer, header=7)

        # 删掉常见的 grand total 行（旧版逻辑）
        if 8 in df.index:
            df = df.drop(index=8)

        # ----------------------------------------------------
        # 2. 只保留前 11 列（包含 funnel 两列）
        # ----------------------------------------------------
        if df.shape[1] > 11:
            df = df.iloc[:, :11]

        # ----------------------------------------------------
        # 3. 按位置重命名列
        # ----------------------------------------------------
        rename_map = {
            df.columns[0]: "Session default channel group",
            df.columns[1]: "Session source / medium",
            df.columns[2]: "Session manual ad content",
            df.columns[3]: "First user source / medium",
            df.columns[4]: "First user manual ad content",
            df.columns[5]: "Sessions",
            df.columns[6]: "Total users",
            df.columns[7]: "Add to carts",
            df.columns[8]: "Checkouts",
            df.columns[9]: "Purchases",
            df.columns[10]: "Total revenue"
        }
        df.rename(columns=rename_map, inplace=True)

        # ----------------------------------------------------
        # 4. 拆 source / medium
        # ----------------------------------------------------
        sm1 = split_source_medium(df["Session source / medium"], "source1", "medium1")
        sm2 = split_source_medium(df["First user source / medium"], "source2", "medium2")
        df = pd.concat([df, sm1, sm2], axis=1)

        # ✅ 新增：source/medium 展示维度（两侧分别生成）
        df["sm1"] = df.apply(lambda r: make_sm_key(r["source1"], r["medium1"]), axis=1)
        df["sm2"] = df.apply(lambda r: make_sm_key(r["source2"], r["medium2"]), axis=1)

        # ----------------------------------------------------
        # 5. Funnel 提取
        # ----------------------------------------------------
        df["funnel1"] = df["Session manual ad content"].apply(extract_funnel)
        df["funnel2"] = df["First user manual ad content"].apply(extract_funnel)

        # ----------------------------------------------------
        # 6. Paid / Non-paid 判定（仍然只用 medium1/medium2）
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
        # 7. 数值字段转化
        # ----------------------------------------------------
        df["Total revenue"] = pd.to_numeric(df["Total revenue"], errors="coerce").fillna(0)
        df["Purchases"] = pd.to_numeric(df["Purchases"], errors="coerce").fillna(0)

        # ----------------------------------------------------
        # 8. Revenue 重分配（输入真实总收入）
        # ----------------------------------------------------
        st.subheader("💰 Actual Revenue Input")

        raw_revenue = float(df["Total revenue"].sum())

        actual_revenue = st.number_input(
            "Enter actual total revenue (Revenue values will be rescaled; structure stays the same)",
            min_value=0.0,
            value=float(raw_revenue),
            step=1000.0
        )

        scale = (actual_revenue / raw_revenue) if raw_revenue > 0 else 0.0
        df["Adjusted revenue"] = df["Total revenue"] * scale

        # ----------------------------------------------------
        # 9. 数据预览
        # ----------------------------------------------------
        st.success("✅ Data cleaned successfully!")
        st.dataframe(df.head(20))

        # ====================================================
        # 📈 Revenue Distribution Visualization
        # ====================================================
        st.subheader("📈 Revenue Distribution Visualization (Rescaled)")
        col1, col2 = st.columns(2)

        revenue_mother = (
            df.groupby("Paid or Non-paid")["Adjusted revenue"]
              .sum()
              .reset_index()
        )
        revenue_mother = revenue_mother[revenue_mother["Paid or Non-paid"] != "Unrecognized"]

        with col1:
            fig1 = px.pie(
                revenue_mother,
                names="Paid or Non-paid",
                values="Adjusted revenue",
                title="Paid vs Non-paid (Revenue - Rescaled)",
                color_discrete_sequence=px.colors.qualitative.Set2
            )
            fig1.update_traces(textinfo="none", hovertemplate="%{label}<br>Revenue: %{value:,.0f}<br>Share: %{percent}")
            st.plotly_chart(fig1, use_container_width=True)

        paid_df = df[df["Paid or Non-paid"] == "Paid"].copy()

        # ----------------------------------------------------
        # Revenue 按 source/medium 归因分摊（只改展示颗粒度）
        # ----------------------------------------------------
        revenue_alloc = {}

        for _, row in paid_df.iterrows():
            rev = float(row["Adjusted revenue"]) if not pd.isna(row["Adjusted revenue"]) else 0
            m1, m2 = str(row["medium1"]).lower(), str(row["medium2"]).lower()

            k1, k2 = row["sm1"], row["sm2"]

            has_m1 = any(k in m1 for k in paid_keywords)
            has_m2 = any(k in m2 for k in paid_keywords)

            if has_m1 and has_m2:
                revenue_alloc[k1] = revenue_alloc.get(k1, 0) + rev * 0.5
                revenue_alloc[k2] = revenue_alloc.get(k2, 0) + rev * 0.5
            elif has_m1:
                revenue_alloc[k1] = revenue_alloc.get(k1, 0) + rev
            elif has_m2:
                revenue_alloc[k2] = revenue_alloc.get(k2, 0) + rev

        with col2:
            if revenue_alloc:
                rev_df = pd.DataFrame(revenue_alloc.items(), columns=["Ad Channel (source/medium)", "Revenue"])
                fig2 = px.pie(
                    rev_df,
                    names="Ad Channel (source/medium)",
                    values="Revenue",
                    title="Ad Channels (source/medium) (Revenue - Rescaled)",
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

        # ----------------------------------------------------
        # Purchase 按 source/medium 归因分摊（只改展示颗粒度）
        # ----------------------------------------------------
        purchase_alloc = {}

        for _, row in paid_df.iterrows():
            pur = float(row["Purchases"]) if not pd.isna(row["Purchases"]) else 0
            m1, m2 = str(row["medium1"]).lower(), str(row["medium2"]).lower()

            k1, k2 = row["sm1"], row["sm2"]

            has_m1 = any(k in m1 for k in paid_keywords)
            has_m2 = any(k in m2 for k in paid_keywords)

            if has_m1 and has_m2:
                purchase_alloc[k1] = purchase_alloc.get(k1, 0) + pur * 0.5
                purchase_alloc[k2] = purchase_alloc.get(k2, 0) + pur * 0.5
            elif has_m1:
                purchase_alloc[k1] = purchase_alloc.get(k1, 0) + pur
            elif has_m2:
                purchase_alloc[k2] = purchase_alloc.get(k2, 0) + pur

        with col4:
            if purchase_alloc:
                pur_df = pd.DataFrame(purchase_alloc.items(), columns=["Ad Channel (source/medium)", "Purchases"])
                fig4 = px.pie(
                    pur_df,
                    names="Ad Channel (source/medium)",
                    values="Purchases",
                    title="Ad Channels (source/medium) (Purchases)",
                    color_discrete_sequence=px.colors.qualitative.Pastel
                )
                fig4.update_traces(textinfo="none", hovertemplate="%{label}<br>Purchases: %{value:,.0f}<br>Share: %{percent}")
                st.plotly_chart(fig4, use_container_width=True)

        # ====================================================
        # 📊 Paid Funnel Distribution（Revenue + Purchase，channel = source/medium）
        # ====================================================
        st.subheader("📊 Paid Funnel Distribution")

        alloc_rows_rev = []
        alloc_rows_pur = []

        for _, row in paid_df.iterrows():
            rev = float(row["Adjusted revenue"]) if not pd.isna(row["Adjusted revenue"]) else 0
            pur = float(row["Purchases"]) if not pd.isna(row["Purchases"]) else 0

            m1, m2 = str(row["medium1"]).lower(), str(row["medium2"]).lower()
            k1, k2 = row["sm1"], row["sm2"]
            f1, f2 = row["funnel1"], row["funnel2"]

            has_m1 = any(k in m1 for k in paid_keywords)
            has_m2 = any(k in m2 for k in paid_keywords)

            if has_m1 and has_m2:
                alloc_rows_rev.append({"Ad Channel (source/medium)": k1, "Funnel": f1, "Value": rev * 0.5})
                alloc_rows_rev.append({"Ad Channel (source/medium)": k2, "Funnel": f2, "Value": rev * 0.5})

                alloc_rows_pur.append({"Ad Channel (source/medium)": k1, "Funnel": f1, "Value": pur * 0.5})
                alloc_rows_pur.append({"Ad Channel (source/medium)": k2, "Funnel": f2, "Value": pur * 0.5})

            elif has_m1 and not has_m2:
                alloc_rows_rev.append({"Ad Channel (source/medium)": k1, "Funnel": f1, "Value": rev})
                alloc_rows_pur.append({"Ad Channel (source/medium)": k1, "Funnel": f1, "Value": pur})

            elif has_m2 and not has_m1:
                alloc_rows_rev.append({"Ad Channel (source/medium)": k2, "Funnel": f2, "Value": rev})
                alloc_rows_pur.append({"Ad Channel (source/medium)": k2, "Funnel": f2, "Value": pur})

        alloc_rev_df = pd.DataFrame(alloc_rows_rev)
        alloc_pur_df = pd.DataFrame(alloc_rows_pur)

        funnel_order = ["lower", "middle", "high", "No funnel"]

        def normalize_funnel(df_in: pd.DataFrame) -> pd.DataFrame:
            if df_in.empty:
                return df_in
            df_in = df_in.copy()
            df_in["Funnel"] = df_in["Funnel"].where(df_in["Funnel"].isin(["lower", "middle", "high"]), "No funnel")
            df_in["Funnel"] = pd.Categorical(df_in["Funnel"], categories=funnel_order, ordered=True)
            return df_in

        alloc_rev_df = normalize_funnel(alloc_rev_df)
        alloc_pur_df = normalize_funnel(alloc_pur_df)

        # 1) Funnel 总览：Revenue 饼图
        if alloc_rev_df.empty or alloc_rev_df["Value"].sum() == 0:
            st.warning("⚠️ No valid paid funnel revenue.")
        else:
            funnel_rev_summary = alloc_rev_df.groupby("Funnel", observed=True)["Value"].sum().reset_index()
            fig5 = px.pie(
                funnel_rev_summary,
                names="Funnel",
                values="Value",
                title="Paid Funnel Share (Revenue - Rescaled)",
                color_discrete_sequence=px.colors.qualitative.Set2
            )
            fig5.update_traces(textinfo="percent", hovertemplate="%{label}<br>Revenue: %{value:,.0f}<br>Share: %{percent}")
            st.plotly_chart(fig5, use_container_width=True)

        # 2) Funnel 总览：Purchase 饼图
        if alloc_pur_df.empty or alloc_pur_df["Value"].sum() == 0:
            st.warning("⚠️ No valid paid funnel purchases.")
        else:
            funnel_pur_summary = alloc_pur_df.groupby("Funnel", observed=True)["Value"].sum().reset_index()
            fig5b = px.pie(
                funnel_pur_summary,
                names="Funnel",
                values="Value",
                title="Paid Funnel Share (Purchase)",
                color_discrete_sequence=px.colors.qualitative.Set2
            )
            fig5b.update_traces(textinfo="percent", hovertemplate="%{label}<br>Purchases: %{value:,.0f}<br>Share: %{percent}")
            st.plotly_chart(fig5b, use_container_width=True)

        # 3) Channel × Funnel 100% 堆叠图（Revenue）
        if not alloc_rev_df.empty and alloc_rev_df["Value"].sum() > 0:
            st.subheader("📊 Paid Funnel by Channel (100% Stacked) - Revenue")
            cf = (
                alloc_rev_df.groupby(["Ad Channel (source/medium)", "Funnel"], observed=True)["Value"]
                .sum()
                .reset_index()
            )
            cf["Channel Total"] = cf.groupby("Ad Channel (source/medium)")["Value"].transform("sum")
            cf["Share"] = cf["Value"] / cf["Channel Total"]

            channel_order = (
                cf.groupby("Ad Channel (source/medium)")["Value"]
                .sum()
                .sort_values(ascending=False)
                .index
                .tolist()
            )

            fig6 = px.bar(
                cf,
                x="Ad Channel (source/medium)",
                y="Share",
                color="Funnel",
                category_orders={"Ad Channel (source/medium)": channel_order, "Funnel": funnel_order},
                title="Funnel Share within Each Paid Channel (Revenue - Rescaled)",
                hover_data={"Value": ":,.0f", "Share": ":.2%", "Channel Total": ":,.0f"}
            )
            fig6.update_layout(
                barmode="stack",
                yaxis_tickformat=".0%",
                xaxis_title="Ad Channel (source/medium)",
                yaxis_title="Share (100%)",
                legend_title="Funnel"
            )
            st.plotly_chart(fig6, use_container_width=True)

        # 4) Channel × Funnel 100% 堆叠图（Purchase）
        if not alloc_pur_df.empty and alloc_pur_df["Value"].sum() > 0:
            st.subheader("📊 Paid Funnel by Channel (100% Stacked) - Purchase")
            cf2 = (
                alloc_pur_df.groupby(["Ad Channel (source/medium)", "Funnel"], observed=True)["Value"]
                .sum()
                .reset_index()
            )
            cf2["Channel Total"] = cf2.groupby("Ad Channel (source/medium)")["Value"].transform("sum")
            cf2["Share"] = cf2["Value"] / cf2["Channel Total"]

            channel_order2 = (
                cf2.groupby("Ad Channel (source/medium)")["Value"]
                .sum()
                .sort_values(ascending=False)
                .index
                .tolist()
            )

            fig6b = px.bar(
                cf2,
                x="Ad Channel (source/medium)",
                y="Share",
                color="Funnel",
                category_orders={"Ad Channel (source/medium)": channel_order2, "Funnel": funnel_order},
                title="Funnel Share within Each Paid Channel (Purchase)",
                hover_data={"Value": ":,.0f", "Share": ":.2%", "Channel Total": ":,.0f"}
            )
            fig6b.update_layout(
                barmode="stack",
                yaxis_tickformat=".0%",
                xaxis_title="Ad Channel (source/medium)",
                yaxis_title="Share (100%)",
                legend_title="Funnel"
            )
            st.plotly_chart(fig6b, use_container_width=True)

        # ----------------------------------------------------
        # 下载 CSV
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
