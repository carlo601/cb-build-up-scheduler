import streamlit as st
import pandas as pd
from datetime import datetime
from pulp import LpProblem, LpMaximize, LpVariable, lpSum, LpStatus, LpStatusOptimal, PULP_CBC_CMD

# --- Page Configuration ---
st.set_page_config(layout="wide", page_title="Circus Bella — Shift Scheduler", page_icon="🎪")

# --- Google Sheets Configuration ---
SHEET_ID = "15XBLzxZaRYYmUKahJh7Bx-ggPh2-qjW8PETZBRRyT3Q"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv"

# --- Master Shift Blueprint ---
DEFAULT_SHIFTS = [
    {"Shift ID": "Nov24_AM", "Date": "2026-11-24", "Start": "10:00", "End": "13:00", "Day Label": "Tues, Nov 24", "Task": "Stake holes & base plates", "Needed": 2},
    {"Shift ID": "Nov24_PM", "Date": "2026-11-24", "Start": "14:00", "End": "18:00", "Day Label": "Tues, Nov 24", "Task": "Stake holes & base plates", "Needed": 3},
    {"Shift ID": "Nov25_AM", "Date": "2026-11-25", "Start": "10:00", "End": "13:00", "Day Label": "Wed, Nov 25", "Task": "Unload Mayflower, arch & signage", "Needed": 2},
    {"Shift ID": "Nov25_PM", "Date": "2026-11-25", "Start": "14:00", "End": "18:00", "Day Label": "Wed, Nov 25", "Task": "Back-of-house stakes", "Needed": 2},
    {"Shift ID": "Nov27_AM", "Date": "2026-11-27", "Start": "10:00", "End": "13:00", "Day Label": "Fri, Nov 27", "Task": "Tent setup, lacing, cupola lights", "Needed": 3},
    {"Shift ID": "Nov27_PM", "Date": "2026-11-27", "Start": "14:00", "End": "18:00", "Day Label": "Fri, Nov 27", "Task": "Tent raising, side poles, rigging", "Needed": 4},
    {"Shift ID": "Nov28_AM", "Date": "2026-11-28", "Start": "10:00", "End": "14:00", "Day Label": "Sat, Nov 28", "Task": "Sidewalls, Power, Signage", "Needed": 1},
    {"Shift ID": "Nov28_MID", "Date": "2026-11-28", "Start": "12:00", "End": "16:00", "Day Label": "Sat, Nov 28", "Task": "Midday Sidewalls & Power", "Needed": 1},
    {"Shift ID": "Nov28_PM", "Date": "2026-11-28", "Start": "14:00", "End": "18:00", "Day Label": "Sat, Nov 28", "Task": "Afternoon Sidewalls & Power", "Needed": 2},
    {"Shift ID": "Nov29_AM", "Date": "2026-11-29", "Start": "10:00", "End": "14:00", "Day Label": "Sun, Nov 29", "Task": "Tent Wash, remaining sidewall", "Needed": 2},
    {"Shift ID": "Nov29_PM", "Date": "2026-11-29", "Start": "14:00", "End": "18:00", "Day Label": "Sun, Nov 29", "Task": "Tent Wash, remaining sidewall", "Needed": 2},
    {"Shift ID": "Nov30_AM", "Date": "2026-11-30", "Start": "10:00", "End": "14:00", "Day Label": "Mon, Nov 30", "Task": "Bandstand, header, façade", "Needed": 2},
    {"Shift ID": "Nov30_PM", "Date": "2026-11-30", "Start": "14:00", "End": "18:00", "Day Label": "Mon, Nov 30", "Task": "Green room & bandstand", "Needed": 2},
    {"Shift ID": "Dec01_AM", "Date": "2026-12-01", "Start": "10:00", "End": "14:00", "Day Label": "Tues, Dec 1", "Task": "Truss, curtain rails, ladders", "Needed": 2},
    {"Shift ID": "Dec01_PM", "Date": "2026-12-01", "Start": "14:00", "End": "18:00", "Day Label": "Tues, Dec 1", "Task": "Backstage & green room", "Needed": 2},
]

# --- Live Data Fetching ---
@st.cache_data(ttl=60)
def fetch_live_data(url):
    """Pulls the live CSV export directly from Google Sheets."""
    return pd.read_csv(url)

def normalize_time_slot(day_key, time_str):
    """Parses freeform multi-line poll checkboxes into exact shift IDs."""
    t = str(time_str).lower().strip()
    if not t or t == "nan":
        return []
    
    slots = []
    lines = [line.strip() for line in t.split("\n") if line.strip()]
    for line in lines:
        if "12:00" in line and "4:00" in line:
            slots.append(f"{day_key}_MID")
        elif any(x in line for x in ["10:00", "10am", "10:00a"]):
            slots.append(f"{day_key}_AM")
        elif any(x in line for x in ["2:00", "14:00", "2pm", "2:00p"]):
            slots.append(f"{day_key}_PM")
    return list(set(slots))

def parse_crew_availability(df):
    """Dynamically parses all submitted responses from the Google Sheet."""
    # 1. Ignore test submissions
    first_col = next((c for c in df.columns if "first" in c.lower()), df.columns[0])
    df_clean = df[~df[first_col].astype(str).str.contains('TEST|test', case=False, na=False)].copy()
    
    # 2. De-duplicate multiple submissions by the same person (keeps the latest submission)
    last_col = next((c for c in df.columns if "last" in c.lower()), df.columns[1])
    email_col = next((c for c in df.columns if "email" in c.lower()), None)
    dedup_subset = [first_col, last_col]
    if email_col:
        dedup_subset.append(email_col)
    df_clean = df_clean.drop_duplicates(subset=dedup_subset, keep="last")

    # 3. Date mapping
    day_cols = {
        "Nov 24": "Nov24",
        "Nov 25": "Nov25",
        "Nov 27": "Nov27",
        "Nov 28": "Nov28",
        "Nov 29": "Nov29",
        "Nov 30": "Nov30",
        "Dec 1": "Dec01"
    }

    # Find notes column
    notes_col = next((c for c in df.columns if any(k in c.lower() for k in ["conflict", "notes", "beyond", "aforementioned"])), None)

    crew_list = []
    for _, row in df_clean.iterrows():
        first = str(row.get(first_col, '')).strip()
        last = str(row.get(last_col, '')).strip()
        full_name = f"{first} {last}".strip()
        if not full_name:
            continue

        notes = str(row[notes_col]).strip() if notes_col and pd.notna(row[notes_col]) else ""
        if notes == "nan":
            notes = ""

        available_shifts = set()
        for col_name in df_clean.columns:
            for day_match, day_key in day_cols.items():
                if day_match in col_name:
                    cell_val = row[col_name]
                    available_shifts.update(normalize_time_slot(day_key, cell_val))

        crew_list.append({
            "Name": full_name,
            "Email": str(row.get(email_col, '')).strip() if email_col else "",
            "Available_Shifts": available_shifts,
            "Notes": notes
        })
    return pd.DataFrame(crew_list)

def check_overlap(s1, s2):
    """Detects if two shifts on the same date overlap in time."""
    if s1['Date'] != s2['Date']:
        return False
    s1_start = datetime.strptime(s1['Start'], "%H:%M")
    s1_end = datetime.strptime(s1['End'], "%H:%M")
    s2_start = datetime.strptime(s2['Start'], "%H:%M")
    s2_end = datetime.strptime(s2['End'], "%H:%M")
    return max(s1_start, s2_start) < min(s1_end, s2_end)

# --- Sidebar Controls ---
st.sidebar.title("🎪 Configuration")

# Manual reload button for Google Sheets
if st.sidebar.button("🔄 Sync with Google Sheet", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

max_shifts = st.sidebar.slider("Maximum Shifts Per Person", min_value=1, max_value=10, value=4)

# --- Load Live Data ---
try:
    df_raw = fetch_live_data(SHEET_URL)
    crew_df = parse_crew_availability(df_raw)
except Exception as e:
    st.error(f"⚠️ Could not load data from Google Sheets: {e}")
    st.info("Ensure the Google Sheet is set to 'Anyone with the link can view'.")
    st.stop()

# --- Main Dashboard ---
st.title("🎪 Circus Bella Bravura 2026")
st.subheader("Tent Buildup Shift Optimizer (Live Data)")

# Metric counters
col1, col2, col3 = st.columns(3)
col1.metric("Registered Crew Members", len(crew_df))
col2.metric("Total Scheduled Shifts", len(DEFAULT_SHIFTS))
col3.metric("Data Source", "Live Google Sheet")

tab_shifts, tab_crew = st.tabs(["1. Shift Requirements (Staff Needed)", "2. Crew Availability & Notes"])

with tab_shifts:
    st.markdown("Adjust the **'Needed'** column below to set how many crew members are required for each slot:")
    shifts_table = pd.DataFrame(DEFAULT_SHIFTS)
    edited_shifts = st.data_editor(
        shifts_table[["Shift ID", "Day Label", "Start", "End", "Task", "Needed"]],
        use_container_width=True,
        num_rows="fixed",
        disabled=["Shift ID", "Day Label", "Start", "End", "Task"]
    )
    shifts_to_use = shifts_table.copy()
    shifts_to_use['Needed'] = edited_shifts['Needed']

with tab_crew:
    st.markdown("Live responses pulled from your poll (test submissions automatically filtered):")
    display_crew = crew_df.copy()
    display_crew['Available Shifts'] = display_crew['Available_Shifts'].apply(lambda x: ", ".join(sorted(list(x))))
    st.dataframe(display_crew[['Name', 'Email', 'Available Shifts', 'Notes']], use_container_width=True, hide_index=True)

st.markdown("---")

# --- Schedule Generator ---
if st.button("🚀 Generate Optimized Schedule", type="primary", use_container_width=True):
    shifts_dict = {row['Shift ID']: row for _, row in shifts_to_use.iterrows()}
    shift_ids = list(shifts_dict.keys())
    crew_names = crew_df['Name'].tolist()

    # Expand required slots
    shift_slots = []
    for s_id in shift_ids:
        needed = int(shifts_dict[s_id]['Needed'])
        for slot_num in range(1, needed + 1):
            shift_slots.append(f"{s_id}_slot{slot_num}")

    slot_to_shift = {s: s.split("_slot")[0] for s in shift_slots}

    # Initialize Optimization Model
    prob = LpProblem("Tent_Buildup_Scheduling", LpMaximize)
    x = LpVariable.dicts("assign", ((c, s) for c in crew_names for s in shift_slots), 0, 1, cat="Binary")

    # Objective: Fill maximum possible slots
    prob += lpSum(x[c, s] for c in crew_names for s in shift_slots)

    # Constraint 1: At most one person per slot
    for s in shift_slots:
        prob += lpSum(x[c, s] for c in crew_names) <= 1

    # Constraint 2: Availability adherence
    for _, crew_row in crew_df.iterrows():
        c_name = crew_row['Name']
        avail = crew_row['Available_Shifts']
        for s in shift_slots:
            s_id = slot_to_shift[s]
            if s_id not in avail:
                prob += x[c_name, s] == 0

    # Constraint 3: No person assigned twice to the same shift
    for c in crew_names:
        for s_id in shift_ids:
            slots_for_shift = [s for s in shift_slots if slot_to_shift[s] == s_id]
            prob += lpSum(x[c, s] for s in slots_for_shift) <= 1

    # Constraint 4: No overlapping shifts on the same day (e.g. Sat, Nov 28 midday vs morning/afternoon)
    for c in crew_names:
        for i in range(len(shift_ids)):
            s1_id = shift_ids[i]
            for j in range(i + 1, len(shift_ids)):
                s2_id = shift_ids[j]
                if check_overlap(shifts_dict[s1_id], shifts_dict[s2_id]):
                    slots_s1 = [s for s in shift_slots if slot_to_shift[s] == s1_id]
                    slots_s2 = [s for s in shift_slots if slot_to_shift[s] == s2_id]
                    prob += lpSum(x[c, s] for s in slots_s1) + lpSum(x[c, s] for s in slots_s2) <= 1

    # Constraint 5: Cap maximum shifts per person
    for c in crew_names:
        prob += lpSum(x[c, s] for s in shift_slots) <= max_shifts

    try:
        prob.solve(PULP_CBC_CMD(msg=0))
    except Exception as e:
        st.error(f"Solver Error: {e}")
        st.info("If you are on an Apple Silicon Mac, run `softwareupdate --install-rosetta --agree-to-license` in Terminal.")
        st.stop()

    if prob.status == LpStatusOptimal:
        st.success("🎉 Schedule successfully generated!")

        # Master Schedule Construction
        schedule_records = []
        total_unfilled = 0
        for s_id in shift_ids:
            s_info = shifts_dict[s_id]
            slots = [s for s in shift_slots if slot_to_shift[s] == s_id]
            assigned_people = []
            for slot in slots:
                for c in crew_names:
                    if x[c, slot].varValue == 1:
                        assigned_people.append(c)

            unfilled = len(slots) - len(assigned_people)
            total_unfilled += unfilled
            schedule_records.append({
                "Date": s_info['Day Label'],
                "Hours": f"{s_info['Start']} - {s_info['End']}",
                "Task": s_info['Task'],
                "Required": len(slots),
                "Assigned Crew": ", ".join(assigned_people) if assigned_people else "⚠️ UNFILLED",
                "Unfilled Slots": unfilled
            })

        schedule_res_df = pd.DataFrame(schedule_records)

        if total_unfilled > 0:
            st.warning(f"⚠️ Notice: {total_unfilled} slots could not be filled with the current availability and maximum shift cap.")

        st.subheader("📋 Master Buildup Schedule")
        st.dataframe(schedule_res_df, use_container_width=True, hide_index=True)

        # Download Button
        csv_data = schedule_res_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download Master Schedule as CSV",
            data=csv_data,
            file_name="circus_bella_buildup_schedule.csv",
            mime="text/csv"
        )

        # Crew Summary
        crew_counts = []
        for c in crew_names:
            assigned = [slot_to_shift[s] for s in shift_slots if x[c, s].varValue == 1]
            crew_counts.append({
                "Crew Member": c,
                "Total Shifts Assigned": len(assigned),
                "Assigned Shifts": ", ".join(assigned) if assigned else "None"
            })

        st.subheader("👥 Crew Assignment Summary")
        st.dataframe(pd.DataFrame(crew_counts).sort_values(by="Total Shifts Assigned", ascending=False), use_container_width=True, hide_index=True)

    else:
        st.error("Could not find a feasible schedule satisfying all constraints. Try increasing the 'Maximum Shifts Per Person' slider.")