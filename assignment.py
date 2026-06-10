import pandas as pd
import numpy as np
import re
from datetime import datetime
from urllib.parse import quote
import gspread
from google.oauth2.service_account import Credentials
from gspread_dataframe import set_with_dataframe
import streamlit as st


# =========================================================
# AUTH
# =========================================================

def get_gspread_client():
    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    if "gcp_service_account" in st.secrets:
        creds = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=scope,
        )
    else:
        import os
        if not os.path.exists("service_account.json"):
            raise FileNotFoundError(
                "Credentials tidak ditemukan. "
                "Tambahkan [gcp_service_account] di Streamlit Secrets."
            )
        creds = Credentials.from_service_account_file(
            "service_account.json", scopes=scope,
        )
    return gspread.authorize(creds)


# =========================================================
# HELPER: normalisasi nama guide
# =========================================================

def normalize_name(name):
    return str(name).strip()


# =========================================================
# HELPER: ekstrak tanggal dari teks dashboard
# Format: "3 Rabu Juni 2026, KOTABARU (15:30)"
# =========================================================

def extract_date(text):
    match = re.search(r"(\d{1,2})\s+\w+\s+(\w+)\s+(\d{4})", str(text))
    if match:
        day_num    = match.group(1)
        indo_month = match.group(2)
        year       = match.group(3)
        month_map = {
            "Januari": "January", "Februari": "February", "Maret": "March",
            "April": "April", "Mei": "May", "Juni": "June",
            "Juli": "July", "Agustus": "August", "September": "September",
            "Oktober": "October", "November": "November", "Desember": "December",
        }
        translated = month_map.get(indo_month, indo_month)
        try:
            return datetime.strptime(f"{day_num} {translated} {year}", "%d %B %Y")
        except ValueError:
            return None
    return None


# =========================================================
# HELPER: ekstrak angka tanggal dari teks dashboard
# =========================================================

def extract_day_number(text):
    match = re.match(r"(\d{1,2})\s", str(text).strip())
    if match:
        return int(match.group(1))
    return None


# =========================================================
# HELPER: tentukan shift dari jam di teks dashboard
# PAGI  = 06:00–11:59
# SORE  = 12:00–17:59
# MALAM = 18:00–23:59
# =========================================================

def extract_shift(text):
    match = re.search(r"\((\d{1,2}):(\d{2})\)", str(text))
    if match:
        hour = int(match.group(1))
        if 6 <= hour < 12:
            return "PAGI"
        elif 12 <= hour < 18:
            return "SORE"
        elif 18 <= hour <= 23:
            return "MALAM"
    return "UNKNOWN"


# =========================================================
# MAPPING dropdown → shift yang TIDAK TERSEDIA
# =========================================================

SHIFT_MAP = {
    "P":  ["PAGI"],
    "S":  ["SORE"],
    "M":  ["MALAM"],
    "TS": ["PAGI", "SORE", "MALAM"],
    "PM": ["PAGI", "MALAM"],
    "SM": ["SORE", "MALAM"],
    "PS": ["PAGI", "SORE"],
}


# =========================================================
# BACA UNAVAILABILITY via Sheets API
#
# Return: { "NamaGuide": { (tanggal_int, "SHIFT"), ... } }
# =========================================================

def parse_unavailability_sheet(gc, spreadsheet_id, sheet_name):
    spreadsheet = gc.open_by_key(spreadsheet_id)
    worksheet   = spreadsheet.worksheet(sheet_name)

    all_values = worksheet.get(
        "A1:ZZ",
        value_render_option="FORMATTED_VALUE",
        date_time_render_option="FORMATTED_STRING",
    )

    unavail = {}

    header_row_indices = []
    for i, row in enumerate(all_values):
        for cell in row:
            if str(cell).strip().lower() == "guide":
                header_row_indices.append(i)
                break

    if not header_row_indices:
        return unavail

    next_header = {header_row_indices[i]: header_row_indices[i + 1]
                   for i in range(len(header_row_indices) - 1)}

    for h_idx in header_row_indices:
        header_row = all_values[h_idx]

        guide_col_idx = None
        for ci, cell in enumerate(header_row):
            if str(cell).strip().lower() == "guide":
                guide_col_idx = ci
                break

        if guide_col_idx is None:
            continue

        col_to_day = {}
        for col_idx in range(guide_col_idx + 1, len(header_row)):
            val = str(header_row[col_idx]).strip()
            if re.match(r"^\d{1,2}$", val):
                col_to_day[col_idx] = int(val)

        block_end = next_header.get(h_idx, len(all_values))

        for row_idx in range(h_idx + 1, block_end):
            row = all_values[row_idx]

            if len(row) <= guide_col_idx:
                continue

            guide_name = normalize_name(row[guide_col_idx])

            if not guide_name:
                continue

            if guide_name.lower() == "guide":
                break

            if guide_name not in unavail:
                unavail[guide_name] = set()

            for col_idx, day_num in col_to_day.items():
                if col_idx >= len(row):
                    continue
                cell_val = str(row[col_idx]).strip().upper()
                if cell_val in SHIFT_MAP:
                    for shift in SHIFT_MAP[cell_val]:
                        unavail[guide_name].add((day_num, shift))

    return unavail


# =========================================================
# HELPER: Load Rating dari sheet RATING GUIDE
# =========================================================

def load_ratings(gc, spreadsheet_id, sheet_name="RATING GUIDE"):
    spreadsheet = gc.open_by_key(spreadsheet_id)
    worksheet   = spreadsheet.worksheet(sheet_name)
    all_values  = worksheet.get_all_values()

    header_idx = None
    for i, row in enumerate(all_values):
        row_upper = [str(c).strip().upper() for c in row]
        if "GUIDE" in row_upper and any("RATING" in c for c in row_upper):
            header_idx = i
            break

    if header_idx is None:
        raise ValueError(
            "Baris header 'Guide' + 'RATING' tidak ditemukan di sheet RATING GUIDE"
        )

    headers   = [str(c).strip() for c in all_values[header_idx]]
    data_rows = all_values[header_idx + 1:]

    ratings_gs = pd.DataFrame(data_rows, columns=headers)
    ratings_gs = ratings_gs[ratings_gs["Guide"].str.strip() != ""].copy()
    ratings_gs = ratings_gs[ratings_gs["Guide"].notna()].copy()
    ratings_gs["Guide"] = ratings_gs["Guide"].apply(normalize_name)

    rating_col = [c for c in ratings_gs.columns if "RATING" in c.upper()]
    if not rating_col:
        raise ValueError("Kolom RATING tidak ditemukan di sheet RATING GUIDE")
    rating_col = rating_col[0]

    ratings_gs = ratings_gs[["Guide", rating_col]].copy()
    ratings_gs = ratings_gs.rename(columns={"Guide": "Name", rating_col: "Rating"})
    ratings_gs["Rating"] = pd.to_numeric(ratings_gs["Rating"], errors="coerce")

    return ratings_gs


# =========================================================
# MODEL MATEMATIS — BAB IV
#
# GAP (Generalized Assignment Problem):
#   max  ΣᵢΣⱼ c_ij · x_ij
#   s.t. Σᵢ x_ij = 1        ∀ j  (setiap jadwal tepat 1 pemandu)
#        Σⱼ x_ij ≤ b        ∀ i  (kapasitas b=5 per minggu)
#        x_ij ∈ {0, 1}
#
# Koefisien prioritas:
#   c_ij = w(p_i, j_j) = r_pi / (k_i + 1)
#
# Graf bipartit ketersediaan:
#   K = (P, J, S)   dengan S = {(p_i, j_j) | a_ij = 1}
#   Matriks ketersediaan M : m_ij = a_ij
#
# Graf solusi penugasan:
#   Q = (P, J, T)   dengan T ⊆ S, T = {(p_i, j_j) | x_ij = 1}
#   Matriks solusi B : b_ij = x_ij
#
# Algoritma: Greedy — pada setiap iterasi j, pilih
#   i* = argmax { w(p_i, j_j) | (p_i, j_j) ∈ S, Σⱼ' x_{i*j'} < b }
# =========================================================

# Kapasitas maksimum per guide per minggu (konstanta b dari model GAP)
B_CAPACITY = 5


def build_availability_matrix(guide_names, jadwal_list, day_shift_pairs, unavail_dict):
    """
    Bangun matriks ketersediaan M = [m_ij] dari model Graf Bipartit K = (P, J, S).

    m_ij = 1  jika pemandu p_i TERSEDIA pada jadwal j_j
    m_ij = 0  jika pemandu p_i TIDAK TERSEDIA pada jadwal j_j

    Parameters
    ----------
    guide_names  : list[str]         — P = {p_1, ..., p_m}
    jadwal_list  : list[str]         — J = {j_1, ..., j_n} (label teks)
    day_shift_pairs : list[tuple]    — (day_num, shift) per jadwal
    unavail_dict : dict              — { nama: set((day_num, shift)) }

    Returns
    -------
    M : np.ndarray, shape (m, n)
    """
    m = len(guide_names)
    n = len(jadwal_list)
    M = np.ones((m, n), dtype=int)

    for i, guide in enumerate(guide_names):
        unavail_set = unavail_dict.get(guide, set())
        for j, (day_num, shift) in enumerate(day_shift_pairs):
            if (day_num, shift) in unavail_set:
                M[i, j] = 0

    return M


def compute_weight_matrix(guide_names, n_jadwal, ratings, k_vector):
    """
    Hitung matriks bobot C = [c_ij] = [w(p_i, j_j)].

    Fungsi bobot:  w(p_i, j_j) = r_pi / (k_i + 1)
      - r_pi : rating pemandu i
      - k_i  : frekuensi penugasan pemandu i hingga sebelum iterasi ini

    Bobot bersifat dinamis: berubah setiap iterasi sesuai k_i terkini.
    Fungsi ini menghitung snapshot C pada kondisi k_vector saat ini.

    Parameters
    ----------
    guide_names : list[str]
    n_jadwal    : int
    ratings     : dict { nama: float }
    k_vector    : np.ndarray, shape (m,) — k_i per pemandu

    Returns
    -------
    C : np.ndarray, shape (m, n)  — sama untuk semua j karena bobot
        hanya bergantung pada i (pemandu), bukan j (jadwal) secara langsung.
        Kolom diulang agar mudah di-mask dengan M.
    """
    m = len(guide_names)
    weights = np.array([
        ratings.get(name, 3.0) / (k_vector[i] + 1)
        for i, name in enumerate(guide_names)
    ])
    # Broadcast ke matriks (m x n): setiap kolom identik
    C = np.tile(weights.reshape(m, 1), (1, n_jadwal))
    return C


def greedy_assignment_week(guide_names, jadwal_list, day_shift_pairs,
                            ratings, unavail_dict):
    """
    Algoritma Greedy untuk menyelesaikan GAP pada satu minggu.

    Pada setiap iterasi j (slot jadwal):
      1. Tentukan himpunan feasible F_j = { i | m_ij = 1 AND k_i < b }
         (kolom j dari M dengan kendala kapasitas)
      2. Hitung c_ij = r_pi / (k_i + 1) untuk setiap i ∈ F_j
      3. Pilih i* = argmax_{i ∈ F_j} c_ij
      4. Set x_{i*j} = 1, update k_{i*} += 1

    Returns
    -------
    assignment_output : list[dict]
    X : np.ndarray, shape (m, n) — matriks keputusan x_ij (biner)
    M : np.ndarray, shape (m, n) — matriks ketersediaan
    """
    m = len(guide_names)
    n = len(jadwal_list)

    # --- Matriks ketersediaan M (Graf Bipartit K) ---
    M = build_availability_matrix(guide_names, jadwal_list, day_shift_pairs, unavail_dict)

    # --- Matriks keputusan X (Graf Solusi Q) — diisi selama iterasi ---
    X = np.zeros((m, n), dtype=int)

    # --- Vektor frekuensi penugasan k_i (direset tiap minggu) ---
    k_vector = np.zeros(m, dtype=int)

    assignment_output = []

    for j, jadwal in enumerate(jadwal_list):
        shift = day_shift_pairs[j][1]

        # --- Hitung bobot dinamis c_ij pada iterasi ini ---
        C_now = compute_weight_matrix(guide_names, n, ratings, k_vector)

        # --- Himpunan feasible F_j: tersedia (M[:,j]=1) dan belum penuh (k_i < b) ---
        feasible_mask = (M[:, j] == 1) & (k_vector < B_CAPACITY)
        feasible_indices = np.where(feasible_mask)[0]

        if len(feasible_indices) == 0:
            assignment_output.append({
                "JADWAL":           jadwal,
                "SHIFT":            shift,
                "GUIDE_DITUGASKAN": "TIDAK ADA GUIDE",
                "RATING":           "",
                "k_i":              "",
                "BOBOT":            "",
                "TOTAL_DITUGASKAN": "0",
            })
            continue

        # --- Greedy: pilih i* = argmax c_ij di antara feasible ---
        best_local_idx = np.argmax(C_now[feasible_indices, j])
        i_star = feasible_indices[best_local_idx]

        # --- Set x_{i*j} = 1, update k_{i*} ---
        X[i_star, j] = 1
        k_vector[i_star] += 1

        chosen_guide  = guide_names[i_star]
        chosen_rating = ratings.get(chosen_guide, 3.0)
        chosen_k      = k_vector[i_star] - 1  # k sebelum penugasan ini
        chosen_weight = round(float(C_now[i_star, j]), 3)

        assignment_output.append({
            "JADWAL":           jadwal,
            "SHIFT":            shift,
            "GUIDE_DITUGASKAN": chosen_guide,
            "RATING":           chosen_rating,
            "k_i":              chosen_k,
            "BOBOT":            chosen_weight,
            "TOTAL_DITUGASKAN": str(int(k_vector[i_star])),
        })

    return assignment_output, X, M


def build_solution_matrix(guide_names, jadwal_list, X):
    """
    Susun Matriks Solusi B = [b_ij] dari matriks keputusan X.

    b_ij = x_ij ∈ {0, 1}

    Graf solusi:  Q = (P, J, T)
      T = { (p_i, j_j) | b_ij = 1 }

    Juga hitung nilai fungsi objektif GAP:
      Z = ΣᵢΣⱼ c_ij · x_ij  (total bobot solusi)
    """
    B = pd.DataFrame(
        X,
        index=guide_names,
        columns=jadwal_list,
    )
    return B


# =========================================================
# MAIN: jalankan assignment
# =========================================================

def run_assignment():
    SPREADSHEET_ID       = "1oYpIm7qRNS69oWxgWPVPx1eOywvOsanr2VLaH7_pnSY"
    GS_UNAVAILABILITY_ID = "1jS8KUIYfCHAHafgibzr74GwCEBQvaObHSgoCqRiyGCA"
    GS_RATING_ID         = "1jS8KUIYfCHAHafgibzr74GwCEBQvaObHSgoCqRiyGCA"

    gc = get_gspread_client()

    # ---- Load Dashboard via CSV ----
    csv_url = (
        f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}"
        f"/gviz/tq?tqx=out:csv&sheet=DASHBOARD"
    )
    dashboard = pd.read_csv(csv_url, header=1)
    dashboard = dashboard[dashboard["SUDAH DIKIRIM"].notna()].copy()
    dashboard = dashboard[
        dashboard["SUDAH DIKIRIM"].astype(str).str.strip() != ""
    ]
    dashboard["DATE"]    = dashboard["TANGGAL & RUTE"].apply(extract_date)
    dashboard["DAY_NUM"] = dashboard["TANGGAL & RUTE"].apply(extract_day_number)
    dashboard["SHIFT"]   = dashboard["TANGGAL & RUTE"].apply(extract_shift)
    dashboard = dashboard[dashboard["DATE"].notna()].copy()
    dashboard = dashboard[dashboard["DAY_NUM"].notna()].copy()
    dashboard = dashboard[dashboard["SHIFT"] != "UNKNOWN"].copy()

    dashboard["WEEK"] = dashboard["DATE"].dt.isocalendar().week
    dashboard = dashboard.sort_values("DATE", ascending=True).reset_index(drop=True)

    # ---- Load Unavailability via Sheets API ----
    unavail_dict = parse_unavailability_sheet(
        gc, GS_UNAVAILABILITY_ID, "CHECK UNAVAILABILITY MONTHLY"
    )

    # ---- Load Rating via Sheets API ----
    ratings_gs = load_ratings(gc, GS_RATING_ID, "RATING GUIDE")

    # ---- Susun himpunan pemandu P dan dict rating r_pi ----
    all_guide_names = sorted(
        set(unavail_dict.keys()) |
        set(ratings_gs["Name"].dropna().apply(normalize_name).tolist())
    )
    all_guide_names = [n for n in all_guide_names if n and n != "nan"]

    ratings_dict = {}
    for name in all_guide_names:
        row = ratings_gs[ratings_gs["Name"] == name]
        if len(row) > 0 and not pd.isna(row["Rating"].values[0]):
            ratings_dict[name] = float(row["Rating"].values[0])
        else:
            ratings_dict[name] = 3.0  # default rating jika tidak terdaftar

    # =========================================================
    # LOOP PER MINGGU — implementasi model GAP per periode
    # kendala kapasitas b=5 direset setiap awal minggu
    # =========================================================

    all_results    = []
    # Simpan matriks B (solusi) dan M (ketersediaan) per minggu untuk inspeksi
    matrices_by_week = {}

    weeks = sorted(dashboard["WEEK"].dropna().unique())

    for current_week in weeks:
        dashboard_week = dashboard[dashboard["WEEK"] == current_week].copy()
        dashboard_week = dashboard_week.sort_values("DATE", ascending=True).reset_index(drop=True)

        # Himpunan jadwal J untuk minggu ini
        jadwal_list     = dashboard_week["TANGGAL & RUTE"].tolist()
        day_shift_pairs = [
            (int(row["DAY_NUM"]), row["SHIFT"])
            for _, row in dashboard_week.iterrows()
        ]

        # --- Jalankan greedy GAP per minggu ---
        week_output, X_week, M_week = greedy_assignment_week(
            guide_names      = all_guide_names,
            jadwal_list      = jadwal_list,
            day_shift_pairs  = day_shift_pairs,
            ratings          = ratings_dict,
            unavail_dict     = unavail_dict,
        )

        # --- Matriks Solusi B minggu ini (Graf Q) ---
        B_week = build_solution_matrix(all_guide_names, jadwal_list, X_week)

        # --- Hitung nilai fungsi objektif Z = ΣΣ c_ij · x_ij ---
        # Gunakan k_vector final untuk mendekati bobot yang dipakai
        # (nilai indikatif; bobot sesungguhnya dicatat per-baris di week_output)
        assigned_rows = [r for r in week_output if r["GUIDE_DITUGASKAN"] != "TIDAK ADA GUIDE"]
        Z_week = sum(float(r["BOBOT"]) for r in assigned_rows if r["BOBOT"] != "")

        matrices_by_week[int(current_week)] = {
            "M": M_week,       # matriks ketersediaan (m × n)
            "B": B_week,       # matriks solusi sebagai DataFrame
            "X": X_week,       # matriks keputusan biner (m × n)
            "Z": round(Z_week, 3),   # nilai fungsi objektif
            "guides": all_guide_names,
            "jadwal": jadwal_list,
        }

        # Tambah kolom WEEK ke output
        for row in week_output:
            row["WEEK"] = str(current_week)

        all_results.append(pd.DataFrame(week_output))

    # ---- Gabung & Sort ----
    assignment_df = pd.concat(all_results, ignore_index=True)
    assignment_df = assignment_df.merge(
        dashboard[["TANGGAL & RUTE", "DATE"]],
        left_on="JADWAL",
        right_on="TANGGAL & RUTE",
        how="left",
    )
    assignment_df = assignment_df.sort_values("DATE", ascending=True).reset_index(drop=True)
    assignment_df = assignment_df.drop(columns=["TANGGAL & RUTE"])

    return assignment_df, matrices_by_week


# =========================================================
# EXPORT KE GOOGLE SHEETS
# =========================================================

def export_to_sheets(assignment_df):
    gc = get_gspread_client()
    SPREADSHEET_ID_EXPORT = "1oYpIm7qRNS69oWxgWPVPx1eOywvOsanr2VLaH7_pnSY"
    sheet_name_export     = "Penugasan"

    spreadsheet = gc.open_by_key(SPREADSHEET_ID_EXPORT)
    try:
        worksheet = spreadsheet.worksheet(sheet_name_export)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=sheet_name_export, rows=5000, cols=20
        )

    worksheet.clear()
    set_with_dataframe(
        worksheet=worksheet,
        dataframe=assignment_df,
        include_index=False,
        include_column_header=True,
        resize=True,
    )
