import pandas as pd
import re
import numpy as np
from datetime import datetime
from urllib.parse import quote
import gspread
from google.oauth2.service_account import Credentials
from gspread_dataframe import set_with_dataframe
import streamlit as st


# =========================================================
# 1. AUTH — Service Account
# =========================================================

def get_gspread_client():
    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    try:
        creds = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=scope,
        )
    except Exception:
        creds = Credentials.from_service_account_file(
            "service_account.json",
            scopes=scope,
        )
    return gspread.authorize(creds)


# =========================================================
# 2. FUNGSI & KAMUS PEMBANTU
# =========================================================

def normalize_name(name):
    return str(name).strip()


def extract_date(text):
    match = re.search(r"(\d{1,2})\s+(\w+)\s+(\w+)\s+(\d{4})", str(text))
    if match:
        day_num    = match.group(1)
        indo_month = match.group(3)
        year       = match.group(4)
        month_map = {
            "Januari": "January",  "Februari": "February", "Maret": "March",
            "April":   "April",    "Mei":       "May",      "Juni":  "June",
            "Juli":    "July",     "Agustus":   "August",   "September": "September",
            "Oktober": "October",  "November":  "November", "Desember": "December",
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
# 3. FUNGSI MEMBACA DATA KETIDAKTERSEDIAAN PEMANDU
# =========================================================

def parse_unavailability_sheet(gc, spreadsheet_id):
    encoded_sheet = quote("CHECK UNAVAILABILITY MONTHLY")
    csv_url = (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
        f"/gviz/tq?tqx=out:csv&sheet={encoded_sheet}"
    )

    # Baca dulu tanpa header untuk deteksi otomatis
    raw = pd.read_csv(csv_url, header=None)

    # Cari baris yang mengandung kolom "Guide"
    header_row = None
    for i, row in raw.iterrows():
        if row.astype(str).str.strip().str.lower().eq("guide").any():
            header_row = i
            break

    if header_row is None:
        raise ValueError("Kolom 'Guide' tidak ditemukan di sheet unavailability.")

    df = pd.read_csv(csv_url, header=header_row)
    ...


# =========================================================
# 4. FUNGSI MEMBACA RATING PEMANDU
# =========================================================

def load_ratings(spreadsheet_id):
    encoded_rating = quote("RATING GUIDE")
    csv_url = (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
        f"/gviz/tq?tqx=out:csv&sheet={encoded_rating}"
    )
    ratings_gs = pd.read_csv(csv_url, header=0)
    rating_col = [c for c in ratings_gs.columns if "RATING" in c.upper()]
    rating_col = rating_col[0] if rating_col else ratings_gs.columns[1]
    ratings_gs = ratings_gs.rename(columns={"Guide": "Name", rating_col: "Rating"})
    ratings_gs["Name"]   = ratings_gs["Name"].apply(normalize_name)
    ratings_gs["Rating"] = pd.to_numeric(ratings_gs["Rating"], errors="coerce").fillna(3.0)
    return ratings_gs[["Name", "Rating"]]


# =========================================================
# 5. FUNGSI PEMBENTUKAN MATRIKS
# =========================================================

def build_matrices(dashboard_week, guide_dict):
    guide_names = sorted(guide_dict.keys())
    n_g = len(guide_names)
    n_j = len(dashboard_week)

    F = np.zeros((n_g, n_j), dtype=int)    # Matriks Ketersediaan
    W = np.zeros((n_g, n_j), dtype=float)  # Matriks Pembobotan (k=0)
    X = np.zeros((n_g, n_j), dtype=int)    # Matriks Solusi

    for j_idx, row in enumerate(dashboard_week.itertuples()):
        day_num = int(row.DAY_NUM)
        shift   = row.SHIFT
        for g_idx, gname in enumerate(guide_names):
            info = guide_dict[gname]
            if (day_num, shift) not in info["unavailable"]:
                F[g_idx, j_idx]      = 1
                W[g_idx, j_idx] = round(info["rating"] / 1, 3)  # bobot awal k=0

    return F, W, X, guide_names


# =========================================================
# 6. FUNGSI PENGISIAN MATRIKS SOLUSI
# =========================================================

def fill_solution_matrix(X, guide_names, assignment_output):
    for j_idx, record in enumerate(assignment_output):
        chosen_guide = record.get("GUIDE_DITUGASKAN", "")
        if chosen_guide in guide_names:
            g_idx = guide_names.index(chosen_guide)
            X[g_idx, j_idx] = 1
    return X


# =========================================================
# 7. FUNGSI UTAMA — RUN ASSIGNMENT
# =========================================================

def run_assignment():
    SPREADSHEET_ID       = "1oYpIm7qRNS69oWxgWPVPx1eOywvOsanr2VLaH7_pnSY"
    GS_UNAVAILABILITY_ID = "1jS8KUIYfCHAHafgibzr74GwCEBQvaObHSgoCqRiyGCA"
    GS_RATING_ID         = "1jS8KUIYfCHAHafgibzr74GwCEBQvaObHSgoCqRiyGCA"

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
    dashboard            = dashboard[dashboard["DATE"].notna()].copy()
    dashboard["DAY_NUM"] = dashboard["TANGGAL & RUTE"].apply(extract_day_number)
    dashboard["SHIFT"]   = dashboard["TANGGAL & RUTE"].apply(extract_shift)
    dashboard["WEEK"]    = dashboard["DATE"].dt.isocalendar().week
    dashboard            = dashboard.sort_values(by="DATE").reset_index(drop=True)

    # ---- Load Unavailability & Rating ----
    gc      = get_gspread_client()
    unavail = parse_unavailability_sheet(gc, GS_UNAVAILABILITY_ID)
    ratings = load_ratings(GS_RATING_ID)

    # ---- Bangun guide_dict ----
    guide_dict = {}
    for _, row in ratings.iterrows():
        gname = normalize_name(row["Name"])
        guide_dict[gname] = {
            "rating":         float(row["Rating"]),
            "unavailable":    unavail.get(gname, set()),
            "assigned_count": 0,
        }

    # ---- Proses penugasan per pekan ----
    all_results = []
    weeks       = sorted(dashboard["WEEK"].dropna().unique())

    for current_week in weeks:
        dashboard_week = (
            dashboard[dashboard["WEEK"] == current_week]
            .copy()
            .sort_values(by="DATE")
            .reset_index(drop=True)
        )

        for g in guide_dict:
            guide_dict[g]["assigned_count"] = 0

        F, W, X, guide_names = build_matrices(dashboard_week, guide_dict)

        assignment_output = []

        for j_idx, row in enumerate(dashboard_week.itertuples()):
            jadwal  = row._asdict()["TANGGAL & RUTE"]
            day_num = int(row.DAY_NUM)
            shift   = row.SHIFT

            feasible = []
            for g_idx, gname in enumerate(guide_names):
                info       = guide_dict[gname]
                is_unavail = (day_num, shift) in info["unavailable"]
                if not is_unavail:
                    k      = info["assigned_count"]
                    rating = info["rating"]
                    weight = rating / (k + 1)
                    W[g_idx, j_idx] = round(weight, 3)
                    feasible.append({
                        "guide":  gname,
                        "g_idx":  g_idx,
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
            total = str(guide_dict[chosen["guide"]]["assigned_count"])

            assignment_output.append({
                "WEEK":             str(current_week),
                "JADWAL":           jadwal,
                "SHIFT":            shift,
                "GUIDE_DITUGASKAN": chosen["guide"],
                "RATING":           chosen["rating"],
                "k_i":              chosen["k"],
                "BOBOT":            round(chosen["weight"], 3),
                "TOTAL_DITUGASKAN": total,
            })

        X = fill_solution_matrix(X, guide_names, assignment_output)
        all_results.append(pd.DataFrame(assignment_output))

    # ---- Gabung & Sort ----
    assignment_df = pd.concat(all_results, ignore_index=True)
    assignment_df = assignment_df.merge(
        dashboard[["TANGGAL & RUTE", "DATE"]],
        left_on="JADWAL",
        right_on="TANGGAL & RUTE",
        how="left",
    )
    assignment_df = (
        assignment_df
        .sort_values(by="DATE")
        .reset_index(drop=True)
        .drop(columns=["TANGGAL & RUTE"])
    )
    return assignment_df


# =========================================================
# 8. FUNGSI EKSPOR KE GOOGLE SHEETS
# =========================================================

def export_to_sheets(assignment_df):
    gc                    = get_gspread_client()
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
