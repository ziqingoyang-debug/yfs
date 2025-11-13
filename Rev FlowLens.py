# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
import io
import re
import plotly.express as px

# ========== 页面设置 ==========
st.set_page_config(page_title="Custom Attribution Model", layout="wide")
st.title("📊 Custom Attribution Model")

uploaded_file = st.file_uploader("Upload original CSV file", type=["csv"])

# ========== 工具函数 ==========
def normalize_space_series(s: pd.Series) -> pd.Series:
    """Clean spaces and slashes"""
    return (
        s.astype(str)
         .str.replace('\u00A0', ' ', regex=False)
         .str.replace('\u2007', ' ', regex=False)
         .str.replace('\u202F', ' ', regex=False)
         .str.replace(r'\s+', ' ', regex=True)
         .str.strip()
    )

def split_source_medium(col: pd.Series, src_name: str, med_name: str) -> pd.DataFrame:
    """Split 'source / medium'"""
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

# ========== 主逻辑 ==========
if uploaded_file is not None:
    try:
        # ---------- 1. Read and preprocess ----------
        raw_text = uploaded_file.getvalue().decode("utf-8", errors="ignore").splitlines()
        csv_buffer = io.StringIO("\n".join(raw_text))
        df = pd.read_csv(csv_buffer, header=7)

        # Drop 9th line (summary)
        if 8 in df.index:
            df = df.drop(index=8)

        # Keep first 9 columns
        if df.shape[1] > 9:
            df = df.iloc[:, :9]

        # ---------- 2. Rename columns ----------
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

        # ---------- 3. Split sources ----------
        sm1 = split_source_medium(df["Session source / medium"], "source1", "medium1")
        sm2 = split_source_medium(df["First user source / medium"], "source2", "medium2")
        df = pd.concat([df, sm1, sm2], axis=1)

        # ---------- 4. Paid or Non-paid ----------
        paid_keywords = ["cpc", "paid", "shopping", "summersale"]

        def judge_paid(row):
            m1 = str(row["medium1"]).lower()
            m2 = str(row["medium2"]).lower()
            if m1 == "unrecognized" and m2 == "unrecognized":
                return "Unrecognized"
            elif any(k in m1 for k in paid_keywords) or any(k in m2 for k in paid_keywords):
                return "Paid"
            else:
                return "Non-paid"

        df["Paid or Non-paid"] = df.apply(judge_paid, axis=1)

        # ---------- 5. Display cleaned data ----------
        st.success("✅ Data cleaned successfully! Preview below:")
        st.dataframe(df.head(20))

        # ---------- 6. Visualization ----------
        st.subheader("📈 Revenue Distribution Visualization")

        col1, col2 = st.columns(2)

        # Mother chart: Paid vs Non-paid
        mother_group = df.groupby("Paid or Non-paid")["Total revenue"].sum().reset_index()
        mother_group = mother_group[mother_group["Paid or Non-paid"] != "Unrecognized"]

        with col1:
            fig1 = px.pie(
                mother_group,
                names="Paid or Non-paid",
                values="Total revenue",
                title="Paid vs Non-paid",
                hole=0.0,
                color_discrete_sequence=px.colors.qualitative.Set2
            )
            fig1.update_traces(textinfo="none", hovertemplate="%{label}<br>Revenue: %{value:,.0f}<br>Share: %{percent}")
            fig1.update_layout(title_font_size=16)
            st.plotly_chart(fig1, use_container_width=True)

        # Child chart: Ad channels (only paid)
        paid_df = df[df["Paid or Non-paid"] == "Paid"].copy()
        revenue_alloc = {}

        for _, row in paid_df.iterrows():
            total_rev = float(row["Total revenue"]) if not pd.isna(row["Total revenue"]) else 0
            m1, m2 = str(row["medium1"]).lower(), str(row["medium2"]).lower()
            s1, s2 = row["source1"], row["source2"]

            has_m1 = any(k in m1 for k in paid_keywords)
            has_m2 = any(k in m2 for k in paid_keywords)

            if has_m1 and has_m2:
                revenue_alloc[s1] = revenue_alloc.get(s1, 0) + total_rev * 0.5
                revenue_alloc[s2] = revenue_alloc.get(s2, 0) + total_rev * 0.5
            elif has_m1 and not has_m2:
                revenue_alloc[s1] = revenue_alloc.get(s1, 0) + total_rev
            elif has_m2 and not has_m1:
                revenue_alloc[s2] = revenue_alloc.get(s2, 0) + total_rev

        if len(revenue_alloc) == 0:
            st.warning("⚠️ No valid paid channels or revenue = 0.")
        else:
            right_df = pd.DataFrame(list(revenue_alloc.items()), columns=["Ad Channel", "Total revenue"])
            right_df = right_df.sort_values(by="Total revenue", ascending=False)

            with col2:
                fig2 = px.pie(
                    right_df,
                    names="Ad Channel",
                    values="Total revenue",
                    title="Ad Channels",
                    hole=0.0,
                    color_discrete_sequence=px.colors.qualitative.Pastel
                )
                fig2.update_traces(textinfo="none", hovertemplate="%{label}<br>Revenue: %{value:,.0f}<br>Share: %{percent}")
                fig2.update_layout(title_font_size=16)
                st.plotly_chart(fig2, use_container_width=True)

        # ---------- 7. Download cleaned CSV ----------
        output = io.BytesIO()
        df.to_csv(output, index=False, encoding="utf-8-sig")
        st.download_button(
            label="📥 Download cleaned CSV",
            data=output.getvalue(),
            file_name="cleaned_data.csv",
            mime="text/csv",
        )

    except Exception as e:
        st.error(f"❌ Error during data processing: {e}")
