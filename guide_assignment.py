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
# HELPER
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
# LOAD RATING
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
# BUILD MATRICES
# Membangun tiga matriks untuk satu minggu:
#   - F  : Feasibility  (n_guide × n_jadwal), nilai 0/1
#   - W  : Weight/Bobot (n_guide × n_jadwal), nilai float atau 0
#   - X  : Solusi       (n_guide × n_jadwal), nilai 0/1
# Baris = guide (sorted by name), Kolom = jadwal (sorted by date)
# =========================================================

def build_matrices(dashboard_week, guide_dict):
    guide_names = sorted(guide_dict.keys())
    jadwal_list = dashboard_week["TANGGAL & RUTE"].tolist()

    n_g = len(guide_names)
    n_j = len(jadwal_list)

    F = np.zeros((n_g, n_j), dtype=int)    # Feasibility
    W = np.zeros((n_g, n_j), dtype=float)  # Bobot awal (k=0 belum diupdate)
    X = np.zeros((n_g, n_j), dtype=int)    # Solusi

    # Isi F dan W berdasarkan unavailability + rating
    # Catatan: W di sini adalah bobot AWAL (k=0 untuk semua),
    # karena urutan assignment mengubah k secara dinamis.
    # W tetap berguna sebagai representasi potensi bobot awal.
    for j_idx, row in enumerate(dashboard_week.itertuples()):
        day_num = int(row.DAY_NUM)
        shift   = row.SHIFT
        for g_idx, gname in enumerate(guide_names):
            info = guide_dict[gname]
            is_unavail = (day_num, shift) in info["unavailable"]
            if not is_unavail:
                F[g_idx, j_idx] = 1
                W[g_idx, j_idx] = round(info["rating"] / 1, 3)  # k=0 → bobot awal

    return F, W, X, guide_names, jadwal_list


def fill_solution_matrix(X, guide_names, assignment_output):
    """Isi matriks solusi X berdasarkan hasil assignment."""
    jadwal_index = {rec["JADWAL"]: j for j, rec in enumerate(assignment_output)}
    guide_index  = {g: i for i, g in enumerate(guide_names)}

    for rec in assignment_output:
        g = rec["GUIDE_DITUGASKAN"]
        j = jadwal_index.get(rec["JADWAL"])
        i = guide_index.get(g)
        if i is not None and j is not None:
            X[i, j] = 1

    return X


# =========================================================
# HITUNG GAP
# GAP = distribusi aktual penugasan per guide
#       vs distribusi ideal (total_jadwal / n_guide_aktif)
#
# Disimpan sebagai DataFrame: Guide | Aktual | Ideal | GAP | GAP_PCT
# GAP positif  = guide dapat lebih banyak dari ideal
# GAP negatif  = guide dapat lebih sedikit dari ideal
# =========================================================

def compute_gap(assignment_output, guide_dict, week_label):
    records = [r for r in assignment_output if r["GUIDE_DITUGASKAN"] != "TIDAK ADA GUIDE"]
    if not records:
        return pd.DataFrame()

    aktual_counts = {}
    for r in records:
        g = r["GUIDE_DITUGASKAN"]
        aktual_counts[g] = aktual_counts.get(g, 0) + 1

    # Guide aktif = yang feasible minimal 1 jadwal (assigned_count > 0 or in aktual)
    # Gunakan semua guide yang terdaftar di guide_dict sebagai pembagi ideal
    n_guide_aktif = len([g for g in guide_dict if guide_dict[g]["rating"] > 0])
    total_jadwal  = len(records)
    ideal         = total_jadwal / n_guide_aktif if n_guide_aktif > 0 else 0

    rows = []
    for gname in sorted(guide_dict.keys()):
        aktual = aktual_counts.get(gname, 0)
        gap    = aktual - ideal
        gap_pct = (gap / ideal * 100) if ideal > 0 else 0
        rows.append({
            "WEEK":    str(week_label),
            "Guide":   gname,
            "Aktual":  aktual,
            "Ideal":   round(ideal, 2),
            "GAP":     round(gap, 2),
            "GAP_PCT": round(gap_pct, 1),
        })

    return pd.DataFrame(rows)


# =========================================================
# MAIN
# =========================================================

def run_assignment():
    SPREADSHEET_ID       = "1oYpIm7qRNS69oWxgWPVPx1eOywvOsanr2VLaH7_pnSY"
    GS_UNAVAILABILITY_ID = "1jS8KUIYfCHAHafgibzr74GwCEBQvaObHSgoCqRiyGCA"
    GS_RATING_ID         = "1jS8KUIYfCHAHafgibzr74GwCEBQvaObHSgoCqRiyGCA"

    gc = get_gspread_client()

    # ---- Load Dashboard ----
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

    # ---- Load Unavailability & Rating ----
    unavail_dict = parse_unavailability_sheet(
        gc, GS_UNAVAILABILITY_ID, "CHECK UNAVAILABILITY MONTHLY"
    )
    ratings_gs = load_ratings(gc, GS_RATING_ID, "RATING GUIDE")

    # ---- Buat guide_dict ----
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

    MAX_ASSIGNMENTS = 5

    all_results   = []
    all_gap       = []

    # Storage matriks per minggu — disimpan di dict, key = week
    matrices_per_week = {}

    weeks = sorted(dashboard["WEEK"].dropna().unique())

    for current_week in weeks:
        dashboard_week = dashboard[dashboard["WEEK"] == current_week].copy()
        dashboard_week = dashboard_week.sort_values(
            "DATE", ascending=True
        ).reset_index(drop=True)

        # Reset assigned_count
        for guide in guide_dict:
            guide_dict[guide]["assigned_count"] = 0

        # --- Bangun matriks F dan W (sebelum assignment) ---
        F, W, X, guide_names, jadwal_list = build_matrices(dashboard_week, guide_dict)

        assignment_output = []

        for _, row in dashboard_week.iterrows():
            jadwal  = row["TANGGAL & RUTE"]
            day_num = int(row["DAY_NUM"])
            shift   = row["SHIFT"]

            feasible = []
            for guide, info in guide_dict.items():
                is_unavailable = (day_num, shift) in info["unavailable"]
                has_capacity   = info["assigned_count"] < MAX_ASSIGNMENTS
                if not is_unavailable and has_capacity:
                    k      = info["assigned_count"]
                    rating = info["rating"]
                    weight = rating / (k + 1)
                    feasible.append({
                        "guide":  guide,
                        "rating": rating,
                        "weight": weight,
                        "k":      k,
                    })

            if not feasible:
                assignment_output.append({
                    "WEEK":             str(current_week),
                    "JADWAL":           jadwal,
                    "SHIFT":            shift,
                    "GUIDE_DITUGASKAN": "TIDAK ADA GUIDE",
                    "RATING":           "",
                    "k_i":              "",
                    "BOBOT":            "",
                    "TOTAL_DITUGASKAN": "0",
                })
                continue

            chosen = max(feasible, key=lambda x: x["weight"])
            guide_dict[chosen["guide"]]["assigned_count"] += 1

            assignment_output.append({
                "WEEK":             str(current_week),
                "JADWAL":           jadwal,
                "SHIFT":            shift,
                "GUIDE_DITUGASKAN": chosen["guide"],
                "RATING":           chosen["rating"],
                "k_i":              chosen["k"],
                "BOBOT":            round(chosen["weight"], 3),
                "TOTAL_DITUGASKAN": str(guide_dict[chosen["guide"]]["assigned_count"]),
            })

        # --- Isi matriks solusi X ---
        X = fill_solution_matrix(X, guide_names, assignment_output)

        # --- Hitung GAP minggu ini ---
        gap_df = compute_gap(assignment_output, guide_dict, current_week)

        # --- Simpan semua matriks minggu ini ---
        matrices_per_week[int(current_week)] = {
            "guide_names": guide_names,
            "jadwal_list": jadwal_list,
            "F": F,   # Feasibility matrix
            "W": W,   # Weight matrix (bobot awal, k=0)
            "X": X,   # Solution matrix
            "gap_df": gap_df,
        }

        all_results.append(pd.DataFrame(assignment_output))
        if not gap_df.empty:
            all_gap.append(gap_df)

    # ---- Gabung ----
    assignment_df = pd.concat(all_results, ignore_index=True)
    assignment_df = assignment_df.merge(
        dashboard[["TANGGAL & RUTE", "DATE"]],
        left_on="JADWAL",
        right_on="TANGGAL & RUTE",
        how="left",
    )
    assignment_df = assignment_df.sort_values(
        "DATE", ascending=True
    ).reset_index(drop=True)
    assignment_df = assignment_df.drop(columns=["TANGGAL & RUTE"])

    gap_df_all = pd.concat(all_gap, ignore_index=True) if all_gap else pd.DataFrame()

    return assignment_df, gap_df_all, matrices_per_week


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
