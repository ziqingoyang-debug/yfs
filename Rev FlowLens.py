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

1. 上传 GA4 导出的 CSV 文件：Explorations 的「自定义归因模型（LND+最初来源）V2」
2. 自动跳过前几行说明，只读取核心数据表
3. 固定字段名称（按列位置）
4. 拆分 source / medium
5. 基于关键词规则标记 Paid / Non-paid
6. 可视化：
   - Paid vs Non-paid（Revenue / Purchases）
   - Paid 渠道归因分摊 Revenue
   - 20260210 新增 Paid 渠道归因分摊 Purchase
   - 20260304新增 funnel（只在 Paid 内）：
     - Paid funnel 总览：lower / middle / high / No funnel
     - Paid 渠道 × funnel：各渠道内部 funnel 构成
7. 20260304 新增支持输入 **真实总收入** 自动重分配 Revenue
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

    s = str(x)

    if s.strip() == "" or s.lower() == "(not set)":
        return "No funnel"

    last = s.split("-")[-1].lower()

    if last in ["lower","middle","high"]:
        return last

    return "No funnel"


# ============================================================
# 主逻辑
# ============================================================

if uploaded_file is not None:

    try:

        # ----------------------------------------------------
        # 1. 读取 CSV
        # ----------------------------------------------------

        raw_text = uploaded_file.getvalue().decode("utf-8", errors="ignore").splitlines()

        csv_buffer = io.StringIO("\n".join(raw_text))

        df = pd.read_csv(csv_buffer, header=7)

        if 8 in df.index:
            df = df.drop(index=8)

        # ----------------------------------------------------
        # 2. 只保留前 11 列（新增 funnel）
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

        # ----------------------------------------------------
        # 5. Funnel 提取
        # ----------------------------------------------------

        df["funnel1"] = df["Session manual ad content"].apply(extract_funnel)
        df["funnel2"] = df["First user manual ad content"].apply(extract_funnel)

        # ----------------------------------------------------
        # 6. Paid / Non-paid 判定
        # ----------------------------------------------------

        paid_keywords = ["cpc","paid","shopping","summersale"]

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
        # 7. 数值转化
        # ----------------------------------------------------

        df["Total revenue"] = pd.to_numeric(df["Total revenue"], errors="coerce").fillna(0)
        df["Purchases"] = pd.to_numeric(df["Purchases"], errors="coerce").fillna(0)

        # ----------------------------------------------------
        # 8. Revenue 重分配
        # ----------------------------------------------------

        st.subheader("💰 Actual Revenue Input")

        raw_revenue = df["Total revenue"].sum()

        actual_revenue = st.number_input(
            "Enter actual total revenue",
            min_value=0.0,
            value=float(raw_revenue),
            step=1000.0
        )

        scale = actual_revenue / raw_revenue if raw_revenue > 0 else 0

        df["Adjusted revenue"] = df["Total revenue"] * scale

        # ----------------------------------------------------
        # 9. 数据预览
        # ----------------------------------------------------

        st.success("✅ Data cleaned successfully!")

        st.dataframe(df.head(20))

        # ====================================================
        # Revenue 分布
        # ====================================================

        st.subheader("📈 Revenue Distribution")

        col1,col2 = st.columns(2)

        revenue_mother = (
            df.groupby("Paid or Non-paid")["Adjusted revenue"]
            .sum()
            .reset_index()
        )

        revenue_mother = revenue_mother[revenue_mother["Paid or Non-paid"]!="Unrecognized"]

        with col1:

            fig1 = px.pie(
                revenue_mother,
                names="Paid or Non-paid",
                values="Adjusted revenue",
                title="Paid vs Non-paid"
            )

            st.plotly_chart(fig1,use_container_width=True)

        paid_df = df[df["Paid or Non-paid"]=="Paid"].copy()

        revenue_alloc = {}

        for _,row in paid_df.iterrows():

            rev = float(row["Adjusted revenue"])

            m1 = str(row["medium1"]).lower()
            m2 = str(row["medium2"]).lower()

            s1 = row["source1"]
            s2 = row["source2"]

            has_m1 = any(k in m1 for k in paid_keywords)
            has_m2 = any(k in m2 for k in paid_keywords)

            if has_m1 and has_m2:

                revenue_alloc[s1] = revenue_alloc.get(s1,0)+rev*0.5
                revenue_alloc[s2] = revenue_alloc.get(s2,0)+rev*0.5

            elif has_m1:

                revenue_alloc[s1] = revenue_alloc.get(s1,0)+rev

            elif has_m2:

                revenue_alloc[s2] = revenue_alloc.get(s2,0)+rev

        with col2:

            rev_df = pd.DataFrame(revenue_alloc.items(),columns=["Ad Channel","Revenue"])

            fig2 = px.pie(
                rev_df,
                names="Ad Channel",
                values="Revenue",
                title="Ad Channels"
            )

            st.plotly_chart(fig2,use_container_width=True)

        # ====================================================
        # Funnel 分布
        # ====================================================

        st.subheader("📊 Paid Funnel Distribution")

        funnel_data = []

        for _,row in paid_df.iterrows():

            rev = float(row["Adjusted revenue"])

            funnel_data.append([row["funnel1"],rev*0.5])
            funnel_data.append([row["funnel2"],rev*0.5])

        funnel_df = pd.DataFrame(funnel_data,columns=["Funnel","Revenue"])

        funnel_summary = funnel_df.groupby("Funnel")["Revenue"].sum().reset_index()

        fig3 = px.pie(
            funnel_summary,
            names="Funnel",
            values="Revenue",
            title="Paid Funnel Share"
        )

        st.plotly_chart(fig3,use_container_width=True)

        # ----------------------------------------------------
        # 下载 CSV
        # ----------------------------------------------------

        output = io.BytesIO()

        df.to_csv(output,index=False,encoding="utf-8-sig")

        st.download_button(
            "📥 Download cleaned CSV",
            data=output.getvalue(),
            file_name="cleaned_data.csv",
            mime="text/csv"
        )

    except Exception as e:

        st.error(f"❌ Error during data processing: {e}")
