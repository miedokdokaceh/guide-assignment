import pandas as pd
import numpy as np
import re
from datetime import datetime
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
            st.secrets["gcp_service_account"], scopes=scope,
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
# FUNGSI HELPER
# =========================================================

def normalize_name(name):
    return str(name).strip()


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


def extract_day_number(text):
    match = re.match(r"(\d{1,2})\s", str(text).strip())
    if match:
        return int(match.group(1))
    return None


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
# BACA UNAVAILABILITY
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
        header_row    = all_values[h_idx]
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
            row        = all_values[row_idx]
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
# LOADING RATING GUIDE
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
        raise ValueError("Baris header 'Guide' + 'RATING' tidak ditemukan di sheet RATING GUIDE")

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
# BUAT MATRIKS F DAN W AWAL
#
# F[i, j] = 1  → guide i feasible untuk jadwal j
# F[i, j] = 0  → tidak bisa (unavailable di hari/shift itu)
#
# W[i, j] = rating_i / 1  → bobot awal (k=0, belum ada penugasan)
# W[i, j] = 0             → infeasible
#
# Baris  = guide (sorted by name), indeks disimpan di guide_index
# Kolom  = jadwal (urut tanggal),  indeks disimpan di jadwal_index
# =========================================================

def build_matrices(dashboard_week, guide_dict):
    guide_names  = sorted(guide_dict.keys())
    jadwal_list  = dashboard_week["TANGGAL & RUTE"].tolist()
    day_shift    = list(zip(
        dashboard_week["DAY_NUM"].astype(int).tolist(),
        dashboard_week["SHIFT"].tolist(),
    ))

    n_g = len(guide_names)
    n_j = len(jadwal_list)

    F = np.zeros((n_g, n_j), dtype=int)
    W = np.zeros((n_g, n_j), dtype=float)

    for g_idx, gname in enumerate(guide_names):
        rating   = guide_dict[gname]["rating"]
        unavail  = guide_dict[gname]["unavailable"]
        for j_idx, (day_num, shift) in enumerate(day_shift):
            if (day_num, shift) not in unavail:
                F[g_idx, j_idx] = 1
                W[g_idx, j_idx] = rating  # bobot awal: rating / (0+1)

    guide_index  = {g: i for i, g in enumerate(guide_names)}
    jadwal_index = {j: idx for idx, j in enumerate(jadwal_list)}

    return F, W, guide_names, jadwal_list, guide_index, jadwal_index


# =========================================================
# GREEDY ASSIGNMENT BERBASIS MATRIKS
#
# Untuk setiap jadwal j (kolom):
#   1. Ambil kolom W[:, j]
#   2. Cari baris i dengan W[i, j] maksimum (> 0 dan belum penuh)
#   3. Tandai X[i, j] = 1
#   4. Update seluruh baris i di W:
#      W[i, j'] = rating_i / (k_i + 1)  untuk semua j' yang F[i,j']=1
#      (karena k_i naik 1, bobot guide itu turun di semua jadwal lain)
#   5. Jika guide sudah mencapai MAX_ASSIGNMENTS, nolkan seluruh baris i
# =========================================================

def greedy_from_matrices(F, W, guide_names, jadwal_list, guide_dict,
                         guide_index, current_week, MAX_ASSIGNMENTS=5):
    n_g, n_j = F.shape
    X        = np.zeros((n_g, n_j), dtype=int)

    # Salin assigned_count ke array lokal (sudah di-reset sebelum masuk sini)
    k = np.array([guide_dict[g]["assigned_count"] for g in guide_names], dtype=float)

    assignment_output = []

    for j_idx, jadwal in enumerate(jadwal_list):
        col_w = W[:, j_idx].copy()  # bobot semua guide untuk jadwal ini

        # Guide yang sudah penuh → bobot 0 (tidak bisa dipilih)
        col_w[k >= MAX_ASSIGNMENTS] = 0.0

        if col_w.max() == 0:
            # Tidak ada guide yang bisa/tersedia
            assignment_output.append({
                "WEEK":             str(current_week),
                "JADWAL":           jadwal,
                "GUIDE_DITUGASKAN": "TIDAK ADA GUIDE",
                "RATING":           "",
                "k_i":              "",
                "BOBOT":            "",
                "TOTAL_DITUGASKAN": "0",
            })
            continue

        # Pilih guide dengan bobot tertinggi di kolom ini
        i_chosen  = int(np.argmax(col_w))
        gname     = guide_names[i_chosen]
        rating    = guide_dict[gname]["rating"]
        k_before  = int(k[i_chosen])
        bobot_val = round(col_w[i_chosen], 3)

        # Tandai solusi
        X[i_chosen, j_idx] = 1
        k[i_chosen]        += 1

        # Update seluruh baris guide terpilih di W:
        # bobot baru = rating / (k_baru + 1) untuk semua jadwal yang masih feasible
        k_new = k[i_chosen]
        for j2 in range(n_j):
            if F[i_chosen, j2] == 1:
                if k_new >= MAX_ASSIGNMENTS:
                    W[i_chosen, j2] = 0.0   # nolkan jika sudah penuh
                else:
                    W[i_chosen, j2] = rating / (k_new + 1)

        # Simpan hasil
        guide_dict[gname]["assigned_count"] = int(k_new)

        assignment_output.append({
            "WEEK":             str(current_week),
            "JADWAL":           jadwal,
            "GUIDE_DITUGASKAN": gname,
            "RATING":           rating,
            "k_i":              k_before,
            "BOBOT":            bobot_val,
            "TOTAL_DITUGASKAN": str(int(k_new)),
        })

    return assignment_output, X, W


# =========================================================
# MAIN
# =========================================================

def run_assignment():
    SPREADSHEET_ID       = "1oYpIm7qRNS69oWxgWPVPx1eOywvOsanr2VLaH7_pnSY"
    GS_UNAVAILABILITY_ID = "1jS8KUIYfCHAHafgibzr74GwCEBQvaObHSgoCqRiyGCA"
    GS_RATING_ID         = "1jS8KUIYfCHAHafgibzr74GwCEBQvaObHSgoCqRiyGCA"

    gc = get_gspread_client()

    # ---- 1. Load Dashboard ----
    csv_url = (
        f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}"
        f"/gviz/tq?tqx=out:csv&sheet=DASHBOARD"
    )
    dashboard = pd.read_csv(csv_url, header=1)
    dashboard.columns = dashboard.columns.str.strip()  # hapus spasi tersembunyi di nama kolom
    dashboard = dashboard[dashboard["SUDAH DIKIRIM"].notna()].copy()
    dashboard = dashboard[dashboard["SUDAH DIKIRIM"].astype(str).str.strip() != ""].copy()
    dashboard["DATE"]    = dashboard["TANGGAL & RUTE"].apply(extract_date)
    dashboard["DAY_NUM"] = dashboard["TANGGAL & RUTE"].apply(extract_day_number)
    dashboard["SHIFT"]   = dashboard["TANGGAL & RUTE"].apply(extract_shift)
    dashboard = dashboard[dashboard["DATE"].notna()].copy()
    dashboard = dashboard[dashboard["DAY_NUM"].notna()].copy()
    dashboard = dashboard[dashboard["SHIFT"] != "UNKNOWN"].copy()
    dashboard["WEEK"] = dashboard["DATE"].dt.isocalendar().week
    dashboard = dashboard.sort_values("DATE", ascending=True).reset_index(drop=True)

    # ---- 2. Load Unavailability & Rating ----
    unavail_dict = parse_unavailability_sheet(gc, GS_UNAVAILABILITY_ID, "CHECK UNAVAILABILITY MONTHLY")
    ratings_gs   = load_ratings(gc, GS_RATING_ID, "RATING GUIDE")

    # ---- 3. Bangun guide_dict ----
    all_guide_names = set(unavail_dict.keys()) | set(
        ratings_gs["Name"].dropna().apply(normalize_name).tolist()
    )
    guide_dict = {}
    for name in all_guide_names:
        name = normalize_name(name)
        if not name or name == "nan":
            continue
        rating_row = ratings_gs[ratings_gs["Name"] == name]
        if len(rating_row) > 0 and not pd.isna(rating_row["Rating"].values[0]):
            rating = float(rating_row["Rating"].values[0])
        else:
            rating = 3.0
        guide_dict[name] = {
            "rating":         rating,
            "unavailable":    unavail_dict.get(name, set()),
            "assigned_count": 0,
        }

    MAX_ASSIGNMENTS   = 5
    all_results       = []
    matrices_per_week = {}
    weeks = sorted(dashboard["WEEK"].dropna().unique())

    for current_week in weeks:
        dashboard_week = dashboard[dashboard["WEEK"] == current_week].copy()
        dashboard_week = dashboard_week.sort_values("DATE", ascending=True).reset_index(drop=True)

        # Reset counter tiap minggu
        for guide in guide_dict:
            guide_dict[guide]["assigned_count"] = 0

        # ---- 4. Bangun F dan W awal ----
        F, W, guide_names, jadwal_list, guide_index, jadwal_index = build_matrices(
            dashboard_week, guide_dict
        )

        # Simpan snapshot W awal sebelum diubah greedy
        W_awal = W.copy()

        # ---- 5. Greedy berbasis matriks — W diupdate live ----
        assignment_output, X, W_akhir = greedy_from_matrices(
            F, W, guide_names, jadwal_list, guide_dict,
            guide_index, current_week, MAX_ASSIGNMENTS,
        )

        # ---- 6. Simpan semua matriks minggu ini ----
        matrices_per_week[int(current_week)] = {
            "guide_names": guide_names,
            "jadwal_list": jadwal_list,
            "F":      F,        # Feasibility (tidak berubah)
            "W_awal": W_awal,   # Bobot sebelum assignment
            "W_akhir":W_akhir,  # Bobot setelah semua jadwal diproses
            "X":      X,        # Solusi akhir
        }

        all_results.append(pd.DataFrame(assignment_output))

    # ---- Gabung & sort ----
    assignment_df = pd.concat(all_results, ignore_index=True)
    assignment_df = assignment_df.merge(
        dashboard[["TANGGAL & RUTE", "DATE"]],
        left_on="JADWAL", right_on="TANGGAL & RUTE", how="left",
    )
    assignment_df = assignment_df.sort_values("DATE", ascending=True).reset_index(drop=True)
    assignment_df = assignment_df.drop(columns=["TANGGAL & RUTE"])

    return assignment_df, matrices_per_week


# =========================================================
# EXPORT
# =========================================================

def export_to_sheets(assignment_df):
    gc = get_gspread_client()
    SPREADSHEET_ID_EXPORT = "1oYpIm7qRNS69oWxgWPVPx1eOywvOsanr2VLaH7_pnSY"
    sheet_name_export     = "Penugasan"

    spreadsheet = gc.open_by_key(SPREADSHEET_ID_EXPORT)
    try:
        worksheet = spreadsheet.worksheet(sheet_name_export)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=sheet_name_export, rows=5000, cols=20)

    worksheet.clear()
    set_with_dataframe(
        worksheet=worksheet,
        dataframe=assignment_df,
        include_index=False,
        include_column_header=True,
        resize=True,
    )
