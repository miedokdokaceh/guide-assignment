import streamlit as st
import matplotlib.pyplot as plt
import pandas as pd
from guide_assignment import run_assignment, export_to_sheets

st.set_page_config(
    page_title="Guide Assignment System",
    page_icon="🗺️",
    layout="wide",
)
st.title("🗺️ Guide Assignment System")
st.caption("Sistem penugasan guide otomatis dari Google Sheets")
st.divider()

if st.button("▶ Generate Assignment", type="primary", use_container_width=True):
    with st.spinner("Memuat data dari Google Sheets..."):
        try:
            assignment_df, matrices_per_week = run_assignment()
            st.session_state["assignment_df"]     = assignment_df
            st.session_state["matrices_per_week"] = matrices_per_week
            st.success(f"✅ Berhasil memproses **{len(assignment_df)}** jadwal.")
        except Exception as e:
            st.error(f"❌ Error saat generate: {e}")
            st.exception(e)

if "assignment_df" in st.session_state:
    assignment_df     = st.session_state["assignment_df"]
    matrices_per_week = st.session_state["matrices_per_week"]

    # ---- Hasil Penugasan ----
    st.subheader("Hasil Penugasan")
    st.dataframe(assignment_df, use_container_width=True)

    # ---- Statistik ----
    st.subheader("Statistik")
    total_jadwal = len(assignment_df)
    tidak_ada    = (assignment_df["GUIDE_DITUGASKAN"] == "TIDAK ADA GUIDE").sum()
    berhasil     = total_jadwal - tidak_ada
    guide_unik   = assignment_df[
        assignment_df["GUIDE_DITUGASKAN"] != "TIDAK ADA GUIDE"
    ]["GUIDE_DITUGASKAN"].nunique()

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Jadwal", total_jadwal)
    c2.metric("Berhasil Ditugaskan", berhasil)
    c3.metric("Guide Terlibat", guide_unik)

    # ---- Distribusi ----
    st.subheader("Distribusi Penugasan Guide")
    guide_stats = (
        assignment_df[
            assignment_df["GUIDE_DITUGASKAN"] != "TIDAK ADA GUIDE"
        ]["GUIDE_DITUGASKAN"].value_counts()
    )
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(guide_stats.index, guide_stats.values, color="#4C72B0")
    ax.set_xlabel("Guide")
    ax.set_ylabel("Jumlah Penugasan")
    ax.set_title("Distribusi Penugasan Guide")
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    st.pyplot(fig)

    # ---- Summary per Guide ----
    st.subheader("Total Penugasan per Guide")
    summary_df = (
        assignment_df[assignment_df["GUIDE_DITUGASKAN"] != "TIDAK ADA GUIDE"]
        .groupby("GUIDE_DITUGASKAN")
        .agg(
            Total_Penugasan=("GUIDE_DITUGASKAN", "count"),
            Rating=("RATING", "first"),
        )
        .reset_index()
        .rename(columns={"GUIDE_DITUGASKAN": "Guide"})
        .sort_values("Total_Penugasan", ascending=False)
        .reset_index(drop=True)
    )
    summary_df.index += 1
    st.dataframe(summary_df, use_container_width=True)

    # ---- Matriks ----
    st.divider()
    st.subheader("🔢 Matriks per Minggu")
    st.caption(
        "**F** = Feasibility (1 = bisa, 0 = tidak). "
        "**W awal** = Bobot sebelum assignment dimulai. "
        "**W akhir** = Bobot setelah semua jadwal diproses (turun tiap guide dipilih). "
        "**X** = Solusi (1 = ditugaskan)."
    )

    if matrices_per_week:
        week_keys    = sorted(matrices_per_week.keys())
        sel_week_mat = st.selectbox(
            "Pilih minggu:", week_keys,
            format_func=lambda w: f"Minggu ke-{w}",
        )
        mat         = matrices_per_week[sel_week_mat]
        guide_names = mat["guide_names"]
        jadwal_list = mat["jadwal_list"]
        col_labels  = [f"J{i+1}" for i in range(len(jadwal_list))]

        tab_f, tab_wa, tab_wz, tab_x = st.tabs([
            "Feasibility (F)", "Bobot Awal (W)", "Bobot Akhir (W')", "Solusi (X)"
        ])

        with tab_f:
            st.dataframe(
                pd.DataFrame(mat["F"], index=guide_names, columns=col_labels),
                use_container_width=True,
            )

        with tab_wa:
            st.dataframe(
                pd.DataFrame(mat["W_awal"], index=guide_names, columns=col_labels)
                .style.format("{:.3f}"),
                use_container_width=True,
            )

        with tab_wz:
            st.caption("Bobot 0 = infeasible atau sudah mencapai batas penugasan minggu ini.")
            st.dataframe(
                pd.DataFrame(mat["W_akhir"], index=guide_names, columns=col_labels)
                .style.format("{:.3f}"),
                use_container_width=True,
            )

        with tab_x:
            df_X = pd.DataFrame(mat["X"], index=guide_names, columns=col_labels)
            st.dataframe(df_X, use_container_width=True)
            st.dataframe(
                df_X.sum(axis=1).rename("Total Ditugaskan (minggu ini)"),
                use_container_width=True,
            )

        #  ---- HISTORY JADWAL ----
        with st.expander("Keterangan kode kolom J1…Jn"):
            st.dataframe(
                pd.DataFrame({"Kode": col_labels, "Jadwal": jadwal_list}),
                use_container_width=True, hide_index=True,
            )

    # ---- Export ----
    st.divider()
    st.subheader("Export ke Google Sheets")
    if st.button("📤 Tulis ke Sheet 'Penugasan'", use_container_width=True):
        with st.spinner("Menulis ke Google Sheets..."):
            try:
                export_to_sheets(assignment_df)
                st.success("✅ Data berhasil ditulis ke Google Sheets!")
            except Exception as e:
                st.error(f"❌ Gagal export: {e}")
                st.exception(e)
