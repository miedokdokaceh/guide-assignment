import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use("Agg")

from assignment import run_assignment, export_to_sheets, B_CAPACITY

st.set_page_config(page_title="Sistem Penugasan Pemandu", layout="wide")
st.title("🗺️ Sistem Penugasan Pemandu — Jogja Good Guide")
st.caption(
    f"Model: *Generalized Assignment Problem* (GAP) | "
    f"Algoritma: Greedy | Kapasitas b = {B_CAPACITY} per minggu"
)

# =========================================================
# TOMBOL GENERATE
# =========================================================

if st.button("▶️ Generate Assignment"):
    with st.spinner("Membaca data dan menjalankan algoritma GAP..."):
        try:
            assignment_df, matrices_by_week = run_assignment()
            st.session_state["assignment_df"]    = assignment_df
            st.session_state["matrices_by_week"] = matrices_by_week
            st.success("✅ Penugasan berhasil dihasilkan!")
        except Exception as e:
            st.error(f"❌ Error: {e}")
            st.stop()

# =========================================================
# TAMPILKAN HASIL (jika sudah ada di session state)
# =========================================================

if "assignment_df" in st.session_state:
    assignment_df    = st.session_state["assignment_df"]
    matrices_by_week = st.session_state["matrices_by_week"]

    # ---- Statistik ringkasan ----
    total_jadwal   = len(assignment_df)
    berhasil       = (assignment_df["GUIDE_DITUGASKAN"] != "TIDAK ADA GUIDE").sum()
    guide_terlibat = assignment_df[
        assignment_df["GUIDE_DITUGASKAN"] != "TIDAK ADA GUIDE"
    ]["GUIDE_DITUGASKAN"].nunique()

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Jadwal",          total_jadwal)
    col2.metric("Berhasil Ditugaskan",   berhasil)
    col3.metric("Guide Terlibat",        guide_terlibat)

    # ---- Tabel hasil penugasan ----
    st.subheader("📋 Hasil Penugasan")
    display_cols = ["WEEK", "JADWAL", "SHIFT", "GUIDE_DITUGASKAN",
                    "RATING", "k_i", "BOBOT", "TOTAL_DITUGASKAN"]
    st.dataframe(assignment_df[display_cols], use_container_width=True)

    # =========================================================
    # PANEL MATEMATIS PER MINGGU
    # =========================================================

    st.subheader("📐 Model Matematis per Minggu")
    st.markdown(
        """
        Sistem mengimplementasikan model **Generalized Assignment Problem (GAP)**:

        **Fungsi Objektif:**  
        $\\max \\displaystyle\\sum_i \\sum_j c_{ij} \\cdot x_{ij}$

        **Fungsi Bobot:**  
        $c_{ij} = w(p_i, j_j) = \\dfrac{r_{p_i}}{k_i + 1}$

        **Kendala:**  
        $\\sum_i x_{ij} = 1 \\;\\;\\forall j$ &nbsp;|&nbsp;
        $\\sum_j x_{ij} \\leq b \\;\\;\\forall i$ &nbsp;|&nbsp;
        $x_{ij} \\in \\{0,1\\}$
        """
    )

    week_options = sorted(matrices_by_week.keys())
    selected_week = st.selectbox(
        "Pilih minggu untuk melihat Matriks M (Ketersediaan) dan B (Solusi):",
        week_options,
        format_func=lambda w: f"Minggu ke-{w}",
    )

    wdata       = matrices_by_week[selected_week]
    guide_names = wdata["guides"]
    jadwal_list = wdata["jadwal"]
    M_week      = wdata["M"]
    B_df        = wdata["B"]
    Z_week      = wdata["Z"]

    tab_m, tab_b = st.tabs(["🔷 Matriks Ketersediaan M", "🔶 Matriks Solusi B"])

    with tab_m:
        st.markdown(
            r"""
            **Matriks Ketersediaan $M = [m_{ij}]$** — merepresentasikan Graf Bipartit
            $K = (P,\, J,\, S)$ dengan $S = \{(p_i, j_j) \mid a_{ij} = 1\}$

            $m_{ij} = 1$ → pemandu $p_i$ **tersedia** pada jadwal $j_j$ &nbsp;|&nbsp;
            $m_{ij} = 0$ → **tidak tersedia**
            """
        )
        # Tampilkan sebagai DataFrame dengan warna
        M_df = pd.DataFrame(M_week, index=guide_names, columns=jadwal_list)
        # Potong label kolom agar tidak terlalu panjang
        M_df.columns = [c[:30] + "…" if len(c) > 30 else c for c in M_df.columns]
        st.dataframe(
            M_df.style.applymap(
                lambda v: "background-color: #d4edda; color: #155724" if v == 1
                else "background-color: #f8d7da; color: #721c24"
            ),
            use_container_width=True,
        )

    with tab_b:
        st.markdown(
            r"""
            **Matriks Solusi $B = [b_{ij}]$** — merepresentasikan Graf Solusi
            $Q = (P,\, J,\, T)$ dengan $T \subseteq S$, $T = \{(p_i, j_j) \mid x_{ij} = 1\}$

            $b_{ij} = 1$ → pemandu $p_i$ **ditugaskan** pada jadwal $j_j$
            """
        )
        B_display = B_df.copy()
        B_display.columns = [
            c[:30] + "…" if len(c) > 30 else c for c in B_display.columns
        ]
        st.dataframe(
            B_display.style.applymap(
                lambda v: "background-color: #cce5ff; color: #004085; font-weight: bold"
                if v == 1 else ""
            ),
            use_container_width=True,
        )

        st.info(
            f"**Nilai Fungsi Objektif Minggu ke-{selected_week}:**  "
            f"$Z = \\sum_i \\sum_j c_{{ij}} \\cdot x_{{ij}} = {Z_week}$"
        )

    # =========================================================
    # DIAGRAM DISTRIBUSI PENUGASAN
    # =========================================================

    st.subheader("📊 Distribusi Penugasan per Guide")

    dist_df = (
        assignment_df[assignment_df["GUIDE_DITUGASKAN"] != "TIDAK ADA GUIDE"]
        .groupby("GUIDE_DITUGASKAN")
        .size()
        .reset_index(name="JUMLAH_TUGAS")
        .sort_values("JUMLAH_TUGAS", ascending=False)
    )

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(dist_df["GUIDE_DITUGASKAN"], dist_df["JUMLAH_TUGAS"], color="#4A90D9")
    ax.set_xlabel("Guide")
    ax.set_ylabel("Jumlah Penugasan")
    ax.set_title("Distribusi Total Penugasan per Guide")
    for bar, val in zip(ax.patches, dist_df["JUMLAH_TUGAS"]):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.1,
                str(val), ha="center", va="bottom", fontsize=9)
    plt.tight_layout()
    st.pyplot(fig)

    # Statistik distribusi
    std_dev = dist_df["JUMLAH_TUGAS"].std(ddof=1)
    cv      = (std_dev / dist_df["JUMLAH_TUGAS"].mean() * 100) if dist_df["JUMLAH_TUGAS"].mean() > 0 else 0
    gap_val = dist_df["JUMLAH_TUGAS"].max() - dist_df["JUMLAH_TUGAS"].min()
    c1, c2, c3 = st.columns(3)
    c1.metric("Standar Deviasi",   f"{std_dev:.2f}")
    c2.metric("Koefisien Variasi", f"{cv:.1f}%")
    c3.metric("Maks − Min",        int(gap_val))

    # ---- Tabel ringkasan per guide ----
    st.subheader("📌 Ringkasan per Guide")
    summary = (
        assignment_df[assignment_df["GUIDE_DITUGASKAN"] != "TIDAK ADA GUIDE"]
        .groupby("GUIDE_DITUGASKAN")
        .agg(
            Total_Penugasan  = ("GUIDE_DITUGASKAN", "count"),
            Rating           = ("RATING", "first"),
        )
        .reset_index()
        .rename(columns={"GUIDE_DITUGASKAN": "Guide"})
        .sort_values("Total_Penugasan", ascending=False)
    )
    st.dataframe(summary, use_container_width=True)

    # =========================================================
    # EKSPOR
    # =========================================================

    st.subheader("📤 Ekspor Hasil")
    if st.button("📤 Tulis ke Sheet 'Penugasan'"):
        with st.spinner("Mengekspor ke Google Sheets..."):
            try:
                export_to_sheets(assignment_df)
                st.success("✅ Berhasil ditulis ke sheet 'Penugasan'!")
            except Exception as e:
                st.error(f"❌ Gagal ekspor: {e}")
