# Sea44Schedule.py — Scheduler Helper (Streamlit) with feasibility check & UI cues
# Run (no venv activation needed):
#   .\venv\Scripts\python.exe -m streamlit run Sea44Schedule.py
#
# Rules:
#   1) Each SP gets exactly TWO slots.
#   2) No back-to-back rows for the same SP (not r-1, r, or r+1) & not same row.
#   3) Zone 2 (Z2) at most once per SP.
#   4) Respect blocked cells and already-assigned cells.
# Extra:
#   • Second-slot choices are filtered by a feasibility solver so the whole
#     schedule remains solvable for everyone.
#   • “All Assignments” table starts from index 1.
#   • Sidebar roster: completed specialists (2 picks) shown in green; dropdown
#     shows ✅ next to completed names.

from typing import Dict, List, Tuple, Set
import pandas as pd
import streamlit as st

# ──────────────────────────────────────────────────────────────────────────────
# Visual headers + time column (labels only)
# ──────────────────────────────────────────────────────────────────────────────
COL_LABELS = ["T/S 1", "T/S 2", "Zone 2"]   # visual header row
COL_KEYS    = ["TS1",  "TS2",   "Z2"]       # internal keys

TIME_LABELS = {
    1: "2:30 – 4:00",
    2: "4:00 – 5:30",
    3: "5:30 – 7:00",
    4: "7:00 – 8:30",
    5: "8:30 – 9:30",
    6: "9:30 – 10:30",
}

ALL_CELLS: Set[str] = {f"{r}_{ck}" for r in range(1, 7) for ck in COL_KEYS}

# Blocked pattern (updated: 2_TS2 and 5_TS1 reopened)
BLOCKED_CELLS: Set[str] = {
    # "2_TS2",                 # reopened: 4:00–5:30 • T/S 2
    "4_TS2", "4_Z2",
    # "5_TS1",                 # reopened: 8:30–9:30 • T/S 1
    "5_TS2", "5_Z2",
    "6_TS2", "6_Z2",
}
WHITE_CELLS: Set[str] = ALL_CELLS - BLOCKED_CELLS
MAX_SLOTS_PER_SP = 2

# ──────────────────────────────────────────────────────────────────────────────
# Session state (acts as DB)
# ──────────────────────────────────────────────────────────────────────────────
if "assigned_by_cell" not in st.session_state:
    st.session_state.assigned_by_cell: Dict[str, str] = {}
if "sp_assignments" not in st.session_state:
    st.session_state.sp_assignments: Dict[str, List[str]] = {}
if "sp_used_Z2" not in st.session_state:
    st.session_state.sp_used_Z2: Dict[str, bool] = {}

def reset_all():
    st.session_state.assigned_by_cell.clear()
    st.session_state.sp_assignments.clear()
    st.session_state.sp_used_Z2.clear()

# ──────────────────────────────────────────────────────────────────────────────
# Helpers & rules
# ──────────────────────────────────────────────────────────────────────────────
def parse_cell(cell: str) -> Tuple[int, str]:
    r_str, ck = cell.split("_", 1)
    return int(r_str), ck

def cell_pretty(cell: str) -> str:
    r, ck = parse_cell(cell)
    return f"{TIME_LABELS[r]} • {COL_LABELS[COL_KEYS.index(ck)]}"

def neighbors_forbidden_rows(r: int) -> Set[int]:
    return {r - 1, r, r + 1}

def allowed_first_for_sp(sp: str, assigned_by_cell: Dict[str, str]) -> List[str]:
    """First pick: any free white cell (feasibility applied later on second pick)."""
    if len(st.session_state.sp_assignments.get(sp, [])) >= MAX_SLOTS_PER_SP:
        return []
    return sorted(
        [c for c in WHITE_CELLS if c not in assigned_by_cell],
        key=lambda x: (parse_cell(x)[0], parse_cell(x)[1])
    )

def allowed_second_for_sp_given_first(sp: str,
                                      first_cell: str,
                                      assigned_by_cell: Dict[str, str],
                                      sp_used_Z2: Dict[str, bool]) -> List[str]:
    """Second pick respecting rules relative to first_cell and current state."""
    if len(st.session_state.sp_assignments.get(sp, [])) >= MAX_SLOTS_PER_SP:
        return []

    r, ck = parse_cell(first_cell)
    forbidden = neighbors_forbidden_rows(r)

    pool = []
    for cell in WHITE_CELLS:
        if cell in assigned_by_cell:
            continue
        rr, cc = parse_cell(cell)
        if rr in forbidden:         # blocks r-1, r, r+1 (includes same row)
            continue
        pool.append(cell)

    # Z2 at most once per SP
    first_is_Z2 = (ck == "Z2")
    already_Z2 = sp_used_Z2.get(sp, False)
    if first_is_Z2 or already_Z2:
        pool = [c for c in pool if parse_cell(c)[1] != "Z2"]

    # also block same row with any prior picks of this SP
    existing = st.session_state.sp_assignments.get(sp, [])
    existing_rows = {parse_cell(x)[0] for x in existing + [first_cell]}
    pool = [c for c in pool if parse_cell(c)[0] not in existing_rows]

    return sorted(set(pool), key=lambda x: (parse_cell(x)[0], parse_cell(x)[1]))

# ──────────────────────────────────────────────────────────────────────────────
# Global feasibility solver (prevents last-person dead ends)
# ──────────────────────────────────────────────────────────────────────────────
def candidate_pairs_for_unassigned_sp(sp: str,
                                      avail_cells: Set[str],
                                      sp_used_Z2: Dict[str, bool]) -> List[Tuple[str, str]]:
    """All valid (first, second) pairs for an SP with 0 picks yet, under avail_cells."""
    pairs: List[Tuple[str, str]] = []
    for first in sorted(avail_cells, key=lambda x: (parse_cell(x)[0], parse_cell(x)[1])):
        r, ck = parse_cell(first)
        forbidden = neighbors_forbidden_rows(r)
        second_pool = []
        for cell in avail_cells:
            if cell == first:
                continue
            rr, cc = parse_cell(cell)
            if rr in forbidden:
                continue
            second_pool.append(cell)
        first_is_Z2 = (ck == "Z2")
        already_Z2 = sp_used_Z2.get(sp, False)
        if first_is_Z2 or already_Z2:
            second_pool = [c for c in second_pool if parse_cell(c)[1] != "Z2"]
        second_pool = [c for c in second_pool if parse_cell(c)[0] != r]
        for second in second_pool:
            a, b = sorted([first, second], key=lambda x: (parse_cell(x)[0], parse_cell(x)[1]))
            pairs.append((a, b))
    # dedupe while keeping order
    uniq = list(dict.fromkeys(pairs))
    return uniq

def feasible_completion(avail_cells: Set[str],
                        remaining_sps: List[str],
                        sp_used_Z2: Dict[str, bool]) -> bool:
    """Backtracking: can every remaining SP get a VALID PAIR from avail_cells?"""
    if not remaining_sps:
        return True
    pairs_per_sp: Dict[str, List[Tuple[str, str]]] = {}
    for sp in remaining_sps:
        pairs_per_sp[sp] = candidate_pairs_for_unassigned_sp(sp, avail_cells, sp_used_Z2)
        if not pairs_per_sp[sp]:
            return False
    sp0 = min(remaining_sps, key=lambda s: len(pairs_per_sp[s]))
    for a, b in pairs_per_sp[sp0]:
        if a not in avail_cells or b not in avail_cells or a == b:
            continue
        new_avail = set(avail_cells)
        new_avail.discard(a)
        new_avail.discard(b)
        new_used = dict(sp_used_Z2)
        if parse_cell(a)[1] == "Z2" or parse_cell(b)[1] == "Z2":
            new_used[sp0] = True
        next_sps = [s for s in remaining_sps if s != sp0]
        if feasible_completion(new_avail, next_sps, new_used):
            return True
    return False

def filter_second_choices_by_global_feasibility(current_sp: str,
                                                first_cell: str,
                                                second_choices: List[str],
                                                specialists: List[str]) -> List[str]:
    """Only keep those second choices that still allow a full completion for others."""
    filtered: List[str] = []
    for second in second_choices:
        taken_now = {first_cell, second}
        avail = {c for c in WHITE_CELLS
                 if c not in st.session_state.assigned_by_cell and c not in taken_now}
        remaining_sps = [s for s in specialists
                         if s != current_sp and len(st.session_state.sp_assignments.get(s, [])) == 0]
        used = dict(st.session_state.sp_used_Z2)
        if parse_cell(first_cell)[1] == "Z2" or parse_cell(second)[1] == "Z2":
            used[current_sp] = True
        if len(avail) < 2 * len(remaining_sps):
            continue
        if feasible_completion(avail, remaining_sps, used):
            filtered.append(second)
    return filtered

# ──────────────────────────────────────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="SEA44 Schedule Generator", layout="wide")
st.title("SEA44 Schedule Generator")

with st.sidebar:
    st.subheader("Specialists")

    # Default to 6 names now (edit freely)
    sp_list_str = st.text_area(
        "Enter specialist names (one per line).",
        value="A\nB\nC\nD\nE\nF",
        height=140
    )
    specialists = [s.strip() for s in sp_list_str.splitlines() if s.strip()]

    # Compute completed (2 picks)
    completed = {s for s in specialists if len(st.session_state.sp_assignments.get(s, [])) >= 2}

    # Visual roster with green for completed
    if specialists:
        items = []
        for s in specialists:
            if s in completed:
                items.append(f"<li style='color:#16a34a;'>✓ {s}</li>")
            else:
                items.append(f"<li>{s}</li>")
        st.markdown("<div><strong>Roster</strong></div>"
                    "<ul style='margin-top:4px;'>" + "".join(items) + "</ul>",
                    unsafe_allow_html=True)

    st.caption("Names can change anytime. Assignments persist until reset.")
    if st.button("🔁 Reset All Assignments", type="secondary"):
        reset_all()
        st.success("Cleared assignments.")

def current_grid_df() -> pd.DataFrame:
    rows = []
    for r in range(1, 7):
        row = {"Time": TIME_LABELS[r]}
        for ck, label in zip(COL_KEYS, COL_LABELS):
            cell = f"{r}_{ck}"
            if cell in BLOCKED_CELLS:
                row[label] = "███"
            else:
                row[label] = st.session_state.assigned_by_cell.get(cell, "—")
        rows.append(row)
    return pd.DataFrame(rows, columns=["Time"] + COL_LABELS)

st.subheader("Swing Shift")

def style_grid(df: pd.DataFrame):
    styler = df.style
    for label in COL_LABELS:
        styler = styler.apply(
            lambda col: ['background-color: #000000; color: #000000' if v == "███" else '' for v in col],
            subset=[label]
        )
    return styler

_df = current_grid_df()
st.dataframe(style_grid(_df), use_container_width=True, hide_index=True)


st.divider()
st.subheader("Pick Slots")

col_left, col_right = st.columns(2)

with col_left:
    # Build display labels with ✅ for completed
    display_options = []
    display_to_actual = {}
    for s in specialists:
        label = f"✅ {s}" if len(st.session_state.sp_assignments.get(s, [])) >= 2 else s
        display_options.append(label)
        display_to_actual[label] = s

    sp_display = st.selectbox(
        "Specialist",
        options=display_options,
        index=0 if display_options else None,
        key="sp_select"
    )
    sp = display_to_actual.get(sp_display)
    if not sp:
        st.stop()

    allowed_first = allowed_first_for_sp(sp, st.session_state.assigned_by_cell)
    first_map = {cell_pretty(c): c for c in allowed_first}
    first_choice_pretty = st.selectbox(
        "First slot (valid & free)",
        options=["— choose —"] + list(first_map.keys()),
        index=0
    )
    first_choice = first_map.get(first_choice_pretty)

    current = st.session_state.sp_assignments.get(sp, [])
    st.caption(f"Current assignments for **{sp}**: {', '.join(map(cell_pretty, current)) or 'none'}")

with col_right:
    if first_choice:
        # Local allowed seconds by rules
        local_seconds = allowed_second_for_sp_given_first(
            sp, first_choice, st.session_state.assigned_by_cell, st.session_state.sp_used_Z2
        )
        # Filter by global feasibility
        feasible_seconds = filter_second_choices_by_global_feasibility(
            sp, first_choice, local_seconds, specialists
        )

        second_map = {cell_pretty(c): c for c in feasible_seconds}
        second_choice_pretty = st.selectbox(
            "Second slot (computed — feasible for everyone)",
            options=["— choose —"] + list(second_map.keys()),
            index=0,
            key="second_select"
        )
        second_choice = second_map.get(second_choice_pretty)

        if not feasible_seconds and local_seconds:
            st.warning("All rule-legal seconds would break the schedule for others. "
                       "Pick a different *First slot*.")

        if st.button("✅ Commit First + Second"):
            if not second_choice:
                st.error("Pick a valid second slot.")
            else:
                st.session_state.assigned_by_cell[first_choice] = sp
                st.session_state.assigned_by_cell[second_choice] = sp
                st.session_state.sp_assignments.setdefault(sp, []).extend([first_choice, second_choice])
                if parse_cell(first_choice)[1] == "Z2" or parse_cell(second_choice)[1] == "Z2":
                    st.session_state.sp_used_Z2[sp] = True
                st.success(f"Assigned {sp} to:\n• {cell_pretty(first_choice)}\n• {cell_pretty(second_choice)}")
                # rerun for fresh UI
                try:
                    st.rerun()
                except Exception:
                    st.experimental_rerun()
    else:
        st.info("Select a *First slot* on the left to compute *Second slot* options.")

st.divider()
st.subheader("All Assignments")
if st.session_state.assigned_by_cell:
    data = []
    for cell, who in sorted(st.session_state.assigned_by_cell.items(),
                            key=lambda kv: (parse_cell(kv[0])[0], parse_cell(kv[0])[1])):
        data.append({"Specialist": who, "Slot": cell_pretty(cell)})
    df = pd.DataFrame(data)
    df.index = range(1, len(df) + 1)   # start numbering at 1
    st.table(df)
else:
    st.write("No assignments yet.")

st.caption("Tip: change the blocked pattern by editing BLOCKED_CELLS at the top of the file.")
