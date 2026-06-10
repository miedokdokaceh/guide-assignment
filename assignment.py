import streamlit as st
import matplotlib.pyplot as plt
import pandas as pd
from assignment import run_assignment, export_to_sheets

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
            assignment_df, gap_df_all, matrices_per_week = run_assignment()
            st.session_state["assignment_df"]      = assignment_df
            st.session_state["gap_df_all"]         = gap_df_all
            st.session_state["matrices_per_week"]  = matrices_per_week
            st.success(f"✅ Berhasil memproses **{len(assignment_df)}** jadwal.")
        except Exception as e:
            st.error(f"❌ Error saat generate: {e}")
            st.exception(e)

if "assignment_df" in st.session_state:
    assignment_df     = st.session_state["assignment_df"]
    gap_df_all        = st.session_state["gap_df_all"]
    matrices_per_week = st.session_state["matrices_per_week"]

    # =========================================================
    # HASIL PENUGASAN
    # =========================================================
    st.subheader("Hasil Penugasan")
    st.dataframe(assignment_df, use_container_width=True)

    # =========================================================
    # STATISTIK RINGKAS
    # =========================================================
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

    # =========================================================
    # DISTRIBUSI PENUGASAN
    # =========================================================
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

    # =========================================================
    # SUMMARY PER GUIDE
    # =========================================================
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

    # =========================================================
    # GAP ANALYSIS
    # =========================================================
    st.divider()
    st.subheader("📊 GAP Analysis — Aktual vs Ideal per Minggu")
    st.caption(
        "GAP = Aktual − Ideal. Positif → guide mendapat lebih banyak dari rata-rata; "
        "negatif → kurang dari rata-rata."
    )

    if not gap_df_all.empty:
        weeks_available = sorted(gap_df_all["WEEK"].unique())
        selected_week   = st.selectbox(
            "Pilih minggu:", weeks_available,
            format_func=lambda w: f"Minggu ke-{w}"
        )
        gap_week = gap_df_all[gap_df_all["WEEK"] == selected_week].copy()
        gap_week = gap_week.sort_values("GAP", ascending=False).reset_index(drop=True)
        gap_week.index += 1
        st.dataframe(gap_week, use_container_width=True)

        # Chart GAP
        fig2, ax2 = plt.subplots(figsize=(10, 4))
        colors = ["#e74c3c" if v > 0 else "#2ecc71" for v in gap_week["GAP"]]
        ax2.bar(gap_week["Guide"], gap_week["GAP"], color=colors)
        ax2.axhline(0, color="black", linewidth=0.8, linestyle="--")
        ax2.set_xlabel("Guide")
        ax2.set_ylabel("GAP (Aktual − Ideal)")
        ax2.set_title(f"GAP Distribusi Penugasan — Minggu ke-{selected_week}")
        plt.xticks(rotation=45, ha="right")
        plt.tight_layout()
        st.pyplot(fig2)
    else:
        st.info("Tidak ada data GAP yang dapat ditampilkan.")

    # =========================================================
    # MATRIKS DETAIL
    # =========================================================
    st.divider()
    st.subheader("🔢 Matriks Detail per Minggu")
    st.caption(
        "**F** = Feasibility (1 = bisa, 0 = tidak bisa/penuh). "
        "**W** = Bobot awal (rating/1, 0 jika infeasible). "
        "**X** = Solusi assignment (1 = ditugaskan)."
    )

    if matrices_per_week:
        week_keys = sorted(matrices_per_week.keys())
        sel_week_mat = st.selectbox(
            "Pilih minggu:", week_keys,
            format_func=lambda w: f"Minggu ke-{w}",
            key="mat_week_sel"
        )
        mat = matrices_per_week[sel_week_mat]
        guide_names = mat["guide_names"]
        jadwal_list = mat["jadwal_list"]

        # Label kolom dipersingkat agar tabel tidak terlalu lebar
        col_labels = [f"J{i+1}" for i in range(len(jadwal_list))]

        tab_f, tab_w, tab_x = st.tabs(["Feasibility (F)", "Bobot Awal (W)", "Solusi (X)"])

        with tab_f:
            df_F = pd.DataFrame(mat["F"], index=guide_names, columns=col_labels)
            st.dataframe(df_F, use_container_width=True)
            st.caption("Keterangan kolom J1…Jn (urut tanggal):")
            st.dataframe(
                pd.DataFrame({"Kode": col_labels, "Jadwal": jadwal_list}),
                use_container_width=True, hide_index=True
            )

        with tab_w:
            df_W = pd.DataFrame(mat["W"], index=guide_names, columns=col_labels)
            st.dataframe(df_W.style.format("{:.3f}"), use_container_width=True)

        with tab_x:
            df_X = pd.DataFrame(mat["X"], index=guide_names, columns=col_labels)
            st.dataframe(df_X, use_container_width=True)
            assigned_per_guide = df_X.sum(axis=1).rename("Total Ditugaskan (minggu ini)")
            st.dataframe(assigned_per_guide, use_container_width=True)

    # =========================================================
    # EXPORT
    # =========================================================
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
