# Sea44ScheduleV2.py — Scheduler Helper (Streamlit) + AR/Backup (V2) + Partial Remove
# Run:
#   .\venv\Scripts\python.exe -m streamlit run Sea44ScheduleV2.py
#
# Legacy Swing rules (kept):
#   1) Each SP gets exactly TWO Swing slots (TS1/TS2/Z2 cells).
#   2) No back-to-back rows for the same SP (not r-1, r, or r+1) & not same row.
#   3) Zone 2 (Z2) at most once per SP.
#   4) Respect blocked cells and already-assigned cells.
# Extra:
#   • Second-slot choices are filtered by a feasibility solver so the whole Swing schedule remains solvable.
#
# V2 Add-on:
#   • Adds AR + Backup posts (row-based) from 2:30–10:30.
#   • Rules:
#       - No double-booking in same time block across Swing vs AR/BK.
#       - AR at most once per SP.
#       - Backup at most once per SP.
#       - Cannot be both AR and Backup in same time block.
#   • AR/BK choices are feasibility-filtered to avoid dead-ends.
#   • Auto-fill button completes remaining AR/BK assignments.
#
# Partial Remove:
#   • Sidebar tools to remove ONLY one person's assignments (or specific slots) without resetting everyone.
#
# IMPORTANT WORKFLOW (current behavior):
#   • Assign SWING first, then AR/BK.
#   • (Swing picker is legacy and does not avoid AR/BK if AR/BK is assigned first.)

from typing import Dict, List, Tuple, Set, Optional
import pandas as pd
import streamlit as st

# ──────────────────────────────────────────────────────────────────────────────
# Legacy Swing columns (DO NOT CHANGE THESE — keep mechanism intact)
# ──────────────────────────────────────────────────────────────────────────────
SWING_COL_LABELS = ["T/S 1", "T/S 2", "Zone 2"]   # visual header row
SWING_COL_KEYS   = ["TS1",  "TS2",   "Z2"]       # internal keys

# Display grid columns (Swing + V2 add-ons)
GRID_COL_LABELS = ["T/S 1", "T/S 2", "Zone 2", "AR", "Backup"]

TIME_LABELS = {
    1: "2:30 – 4:00",
    2: "4:00 – 5:30",
    3: "5:30 – 7:00",
    4: "7:00 – 8:30",
    5: "8:30 – 9:30",
    6: "9:30 – 10:30",
}

ALL_SWING_CELLS: Set[str] = {f"{r}_{ck}" for r in range(1, 7) for ck in SWING_COL_KEYS}

# Blocked pattern (updated: 2_TS2 and 5_TS1 reopened)
BLOCKED_CELLS: Set[str] = {
    # "2_TS2",                 # reopened: 4:00–5:30 • T/S 2
    "4_TS2", "4_Z2",
    # "5_TS1",                 # reopened: 8:30–9:30 • T/S 1
    "5_TS2", "5_Z2",
    "6_TS2", "6_Z2",
}
WHITE_CELLS: Set[str] = ALL_SWING_CELLS - BLOCKED_CELLS
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

# V2 AR/Backup state (row-based)
if "ar_by_row" not in st.session_state:
    st.session_state.ar_by_row: Dict[int, str] = {}       # row -> specialist
if "bk_by_row" not in st.session_state:
    st.session_state.bk_by_row: Dict[int, str] = {}       # row -> specialist
if "sp_used_AR" not in st.session_state:
    st.session_state.sp_used_AR: Dict[str, bool] = {}     # sp -> bool
if "sp_used_BK" not in st.session_state:
    st.session_state.sp_used_BK: Dict[str, bool] = {}     # sp -> bool


def reset_all():
    # Legacy Swing
    st.session_state.assigned_by_cell.clear()
    st.session_state.sp_assignments.clear()
    st.session_state.sp_used_Z2.clear()
    # V2 AR/BK
    st.session_state.ar_by_row.clear()
    st.session_state.bk_by_row.clear()
    st.session_state.sp_used_AR.clear()
    st.session_state.sp_used_BK.clear()


def reset_ar_bk_only():
    st.session_state.ar_by_row.clear()
    st.session_state.bk_by_row.clear()
    st.session_state.sp_used_AR.clear()
    st.session_state.sp_used_BK.clear()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers & legacy Swing rules
# ──────────────────────────────────────────────────────────────────────────────
def parse_cell(cell: str) -> Tuple[int, str]:
    r_str, ck = cell.split("_", 1)
    return int(r_str), ck


def cell_pretty(cell: str) -> str:
    r, ck = parse_cell(cell)
    return f"{TIME_LABELS[r]} • {SWING_COL_LABELS[SWING_COL_KEYS.index(ck)]}"


def neighbors_forbidden_rows(r: int) -> Set[int]:
    return {r - 1, r, r + 1}


def allowed_first_for_sp(sp: str, assigned_by_cell: Dict[str, str]) -> List[str]:
    """First pick: any free white Swing cell (feasibility applied later on second pick)."""
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
        rr, _ = parse_cell(cell)
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
# Legacy Swing feasibility solver (prevents last-person dead ends)
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
            rr, _ = parse_cell(cell)
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
# V2 AR/Backup helpers + feasibility
# ──────────────────────────────────────────────────────────────────────────────
def row_label(r: int) -> str:
    return TIME_LABELS[r]


def busy_specialists_in_row_swing(r: int) -> Set[str]:
    """Specialists already assigned to Swing posts TS1/TS2/Z2 in row r."""
    busy = set()
    for ck in SWING_COL_KEYS:
        who = st.session_state.assigned_by_cell.get(f"{r}_{ck}")
        if who:
            busy.add(who)
    return busy


def busy_specialists_in_row_all(r: int,
                               ar_by_row: Dict[int, str],
                               bk_by_row: Dict[int, str]) -> Set[str]:
    """Busy in row considering Swing + AR/BK currently assigned."""
    busy = set(busy_specialists_in_row_swing(r))
    if r in ar_by_row:
        busy.add(ar_by_row[r])
    if r in bk_by_row:
        busy.add(bk_by_row[r])
    return busy


def allowed_ar_rows_for_sp(sp: str) -> List[int]:
    if st.session_state.sp_used_AR.get(sp, False):
        return []
    rows = []
    for r in range(1, 7):
        if r in st.session_state.ar_by_row:
            continue
        if sp in busy_specialists_in_row_swing(r):
            continue
        if r in st.session_state.bk_by_row and st.session_state.bk_by_row[r] == sp:
            continue
        rows.append(r)
    return rows


def allowed_bk_rows_for_sp(sp: str, chosen_ar_row: Optional[int] = None) -> List[int]:
    if st.session_state.sp_used_BK.get(sp, False):
        return []
    rows = []
    for r in range(1, 7):
        if r in st.session_state.bk_by_row:
            continue
        if chosen_ar_row is not None and r == chosen_ar_row:
            continue
        if sp in busy_specialists_in_row_swing(r):
            continue
        if r in st.session_state.ar_by_row and st.session_state.ar_by_row[r] == sp:
            continue
        rows.append(r)
    return rows


def _feasible_ar_bk_completion(specialists: List[str],
                              ar_by_row: Dict[int, str],
                              bk_by_row: Dict[int, str],
                              used_ar: Dict[str, bool],
                              used_bk: Dict[str, bool]) -> bool:
    """Backtracking: can we complete remaining AR/BK assignments under constraints?"""
    open_ar_rows = [r for r in range(1, 7) if r not in ar_by_row]
    open_bk_rows = [r for r in range(1, 7) if r not in bk_by_row]

    need_ar = [s for s in specialists if not used_ar.get(s, False)]
    need_bk = [s for s in specialists if not used_bk.get(s, False)]

    if len(open_ar_rows) != len(need_ar):
        return False
    if len(open_bk_rows) != len(need_bk):
        return False

    if not open_ar_rows and not open_bk_rows:
        return True

    decisions: List[Tuple[str, int, List[str]]] = []

    for r in open_ar_rows:
        cand = []
        busy = busy_specialists_in_row_all(r, ar_by_row, bk_by_row)
        for s in need_ar:
            if s in busy:
                continue
            if r in bk_by_row and bk_by_row[r] == s:
                continue
            cand.append(s)
        if not cand:
            return False
        decisions.append(("AR", r, cand))

    for r in open_bk_rows:
        cand = []
        busy = busy_specialists_in_row_all(r, ar_by_row, bk_by_row)
        for s in need_bk:
            if s in busy:
                continue
            if r in ar_by_row and ar_by_row[r] == s:
                continue
            cand.append(s)
        if not cand:
            return False
        decisions.append(("BK", r, cand))

    kind, r, cand = min(decisions, key=lambda t: len(t[2]))

    for s in cand:
        na = dict(ar_by_row)
        nb = dict(bk_by_row)
        nua = dict(used_ar)
        nub = dict(used_bk)

        if kind == "AR":
            na[r] = s
            nua[s] = True
        else:
            nb[r] = s
            nub[s] = True

        if _feasible_ar_bk_completion(specialists, na, nb, nua, nub):
            return True

    return False


def _solve_ar_bk(specialists: List[str],
                 ar_by_row: Dict[int, str],
                 bk_by_row: Dict[int, str],
                 used_ar: Dict[str, bool],
                 used_bk: Dict[str, bool]) -> Optional[Tuple[Dict[int, str], Dict[int, str], Dict[str, bool], Dict[str, bool]]]:
    """Return one completed solution (or None)."""
    if not _feasible_ar_bk_completion(specialists, ar_by_row, bk_by_row, used_ar, used_bk):
        return None

    open_ar_rows = [r for r in range(1, 7) if r not in ar_by_row]
    open_bk_rows = [r for r in range(1, 7) if r not in bk_by_row]
    if not open_ar_rows and not open_bk_rows:
        return ar_by_row, bk_by_row, used_ar, used_bk

    need_ar = [s for s in specialists if not used_ar.get(s, False)]
    need_bk = [s for s in specialists if not used_bk.get(s, False)]

    decisions: List[Tuple[str, int, List[str]]] = []
    for r in open_ar_rows:
        busy = busy_specialists_in_row_all(r, ar_by_row, bk_by_row)
        cand = [s for s in need_ar if s not in busy and not (r in bk_by_row and bk_by_row[r] == s)]
        if not cand:
            return None
        decisions.append(("AR", r, cand))
    for r in open_bk_rows:
        busy = busy_specialists_in_row_all(r, ar_by_row, bk_by_row)
        cand = [s for s in need_bk if s not in busy and not (r in ar_by_row and ar_by_row[r] == s)]
        if not cand:
            return None
        decisions.append(("BK", r, cand))

    kind, r, cand = min(decisions, key=lambda t: len(t[2]))

    for s in cand:
        na = dict(ar_by_row)
        nb = dict(bk_by_row)
        nua = dict(used_ar)
        nub = dict(used_bk)

        if kind == "AR":
            na[r] = s
            nua[s] = True
        else:
            nb[r] = s
            nub[s] = True

        res = _solve_ar_bk(specialists, na, nb, nua, nub)
        if res is not None:
            return res

    return None


def filter_rows_by_feasibility_for_sp(sp: str,
                                      kind: str,
                                      candidate_rows: List[int],
                                      specialists: List[str],
                                      chosen_ar_row: Optional[int] = None) -> List[int]:
    """Keep only choices that still allow a full AR/BK completion for everyone else."""
    kept: List[int] = []
    for r in candidate_rows:
        ar_by_row = dict(st.session_state.ar_by_row)
        bk_by_row = dict(st.session_state.bk_by_row)
        used_ar = dict(st.session_state.sp_used_AR)
        used_bk = dict(st.session_state.sp_used_BK)

        if kind == "AR":
            ar_by_row[r] = sp
            used_ar[sp] = True
        else:
            if chosen_ar_row is not None and r == chosen_ar_row:
                continue
            bk_by_row[r] = sp
            used_bk[sp] = True

        if _feasible_ar_bk_completion(specialists, ar_by_row, bk_by_row, used_ar, used_bk):
            kept.append(r)
    return kept


# ──────────────────────────────────────────────────────────────────────────────
# Partial remove helpers
# ──────────────────────────────────────────────────────────────────────────────
def recompute_used_flags_for_sp(sp: str):
    z2_used = any(who == sp and parse_cell(cell)[1] == "Z2"
                  for cell, who in st.session_state.assigned_by_cell.items())
    if z2_used:
        st.session_state.sp_used_Z2[sp] = True
    else:
        st.session_state.sp_used_Z2.pop(sp, None)

    ar_used = any(who == sp for who in st.session_state.ar_by_row.values())
    if ar_used:
        st.session_state.sp_used_AR[sp] = True
    else:
        st.session_state.sp_used_AR.pop(sp, None)

    bk_used = any(who == sp for who in st.session_state.bk_by_row.values())
    if bk_used:
        st.session_state.sp_used_BK[sp] = True
    else:
        st.session_state.sp_used_BK.pop(sp, None)


def remove_swing_cells(sp: str, cells_to_remove: List[str]):
    for cell in cells_to_remove:
        if st.session_state.assigned_by_cell.get(cell) == sp:
            st.session_state.assigned_by_cell.pop(cell, None)

    if sp in st.session_state.sp_assignments:
        st.session_state.sp_assignments[sp] = [
            c for c in st.session_state.sp_assignments[sp] if c not in cells_to_remove
        ]
        if not st.session_state.sp_assignments[sp]:
            st.session_state.sp_assignments.pop(sp, None)

    recompute_used_flags_for_sp(sp)


def remove_ar_bk_rows(sp: str, ar_rows: List[int], bk_rows: List[int]):
    for r in ar_rows:
        if st.session_state.ar_by_row.get(r) == sp:
            st.session_state.ar_by_row.pop(r, None)
    for r in bk_rows:
        if st.session_state.bk_by_row.get(r) == sp:
            st.session_state.bk_by_row.pop(r, None)

    recompute_used_flags_for_sp(sp)


def remove_all_for_sp(sp: str):
    swing_cells = [cell for cell, who in st.session_state.assigned_by_cell.items() if who == sp]
    remove_swing_cells(sp, swing_cells)

    ar_rows = [r for r, who in st.session_state.ar_by_row.items() if who == sp]
    bk_rows = [r for r, who in st.session_state.bk_by_row.items() if who == sp]
    remove_ar_bk_rows(sp, ar_rows, bk_rows)

    recompute_used_flags_for_sp(sp)


# ──────────────────────────────────────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="SEA44 Schedule Generator V2", layout="wide")
st.title("SEA44 Schedule Generator V2 (AR + Backup)")

with st.sidebar:
    st.subheader("Specialists")

    sp_list_str = st.text_area(
        "Enter specialist names (one per line).",
        value="A\nB\nC\nD\nE\nF",
        height=140
    )
    specialists = [s.strip() for s in sp_list_str.splitlines() if s.strip()]

    completed_swing = {s for s in specialists if len(st.session_state.sp_assignments.get(s, [])) >= 2}

    if specialists:
        items = []
        for s in specialists:
            if s in completed_swing:
                items.append(f"<li style='color:#16a34a;'>✓ {s}</li>")
            else:
                items.append(f"<li>{s}</li>")
        st.markdown("<div><strong>Roster</strong></div>"
                    "<ul style='margin-top:4px;'>" + "".join(items) + "</ul>",
                    unsafe_allow_html=True)

    st.caption("Workflow: Assign Swing first, then AR/Backup.")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔁 Reset ALL", type="secondary"):
            reset_all()
            st.success("Cleared Swing + AR/Backup.")
            st.rerun()
    with c2:
        if st.button("🧹 Reset AR/BK only", type="secondary"):
            reset_ar_bk_only()
            st.success("Cleared AR/Backup only.")
            st.rerun()

    st.divider()
    st.subheader("Remove / Fix (no full reset)")

    sp_fix = st.selectbox("Select specialist to edit", options=["—"] + specialists, key="sp_fix")
    if sp_fix != "—":
        swing_cells = [cell for cell, who in st.session_state.assigned_by_cell.items() if who == sp_fix]
        swing_cells_sorted = sorted(swing_cells, key=lambda x: (parse_cell(x)[0], parse_cell(x)[1]))
        swing_pretty = [cell_pretty(c) for c in swing_cells_sorted]

        ar_rows = sorted([r for r, who in st.session_state.ar_by_row.items() if who == sp_fix])
        bk_rows = sorted([r for r, who in st.session_state.bk_by_row.items() if who == sp_fix])

        ar_pretty = [TIME_LABELS[r] for r in ar_rows]
        bk_pretty = [TIME_LABELS[r] for r in bk_rows]

        st.caption("Current assignments")
        st.write("**Swing:**", ", ".join(swing_pretty) if swing_pretty else "none")
        st.write("**AR:**", ", ".join(ar_pretty) if ar_pretty else "none")
        st.write("**Backup:**", ", ".join(bk_pretty) if bk_pretty else "none")

        to_remove_swing = st.multiselect("Remove Swing slots", options=swing_pretty, default=[], key="rm_swing")
        pretty_to_cell = {cell_pretty(c): c for c in swing_cells_sorted}
        rm_cells = [pretty_to_cell[p] for p in to_remove_swing if p in pretty_to_cell]

        to_remove_ar = st.multiselect("Remove AR time blocks", options=ar_pretty, default=[], key="rm_ar")
        rm_ar_rows = [r for r in ar_rows if TIME_LABELS[r] in to_remove_ar]

        to_remove_bk = st.multiselect("Remove Backup time blocks", options=bk_pretty, default=[], key="rm_bk")
        rm_bk_rows = [r for r in bk_rows if TIME_LABELS[r] in to_remove_bk]

        colA, colB = st.columns(2)
        with colA:
            if st.button("🧽 Remove selected", type="secondary", key="btn_remove_selected"):
                if not rm_cells and not rm_ar_rows and not rm_bk_rows:
                    st.warning("Nothing selected to remove.")
                else:
                    if rm_cells:
                        remove_swing_cells(sp_fix, rm_cells)
                    if rm_ar_rows or rm_bk_rows:
                        remove_ar_bk_rows(sp_fix, rm_ar_rows, rm_bk_rows)
                    st.success(f"Removed selected assignments for {sp_fix}.")
                    st.rerun()
        with colB:
            if st.button("🗑️ Remove ALL for this person", type="secondary", key="btn_remove_all_person"):
                remove_all_for_sp(sp_fix)
                st.success(f"Removed ALL assignments for {sp_fix}.")
                st.rerun()


def current_grid_df() -> pd.DataFrame:
    rows = []
    for r in range(1, 7):
        row = {"Time": TIME_LABELS[r]}

        for ck, label in zip(SWING_COL_KEYS, SWING_COL_LABELS):
            cell = f"{r}_{ck}"
            if cell in BLOCKED_CELLS:
                row[label] = "███"
            else:
                row[label] = st.session_state.assigned_by_cell.get(cell, "—")

        row["AR"] = st.session_state.ar_by_row.get(r, "—")
        row["Backup"] = st.session_state.bk_by_row.get(r, "—")

        rows.append(row)

    return pd.DataFrame(rows, columns=["Time"] + GRID_COL_LABELS)


st.subheader("Swing Shift + AR/Backup")

def style_grid(df: pd.DataFrame):
    styler = df.style
    for label in SWING_COL_LABELS:
        styler = styler.apply(
            lambda col: ['background-color: #000000; color: #000000' if v == "███" else '' for v in col],
            subset=[label]
        )
    return styler

_df = current_grid_df()
st.dataframe(style_grid(_df), width="stretch", hide_index=True)

# ──────────────────────────────────────────────────────────────────────────────
# Legacy Swing picker
# ──────────────────────────────────────────────────────────────────────────────
st.divider()
st.subheader("Pick Swing Slots (Legacy)")

col_left, col_right = st.columns(2)

with col_left:
    display_options = []
    display_to_actual = {}
    for s in specialists:
        label = f"✅ {s}" if len(st.session_state.sp_assignments.get(s, [])) >= 2 else s
        display_options.append(label)
        display_to_actual[label] = s

    sp_display = st.selectbox("Specialist (Swing)", options=display_options, index=0 if display_options else None, key="sp_select")
    sp = display_to_actual.get(sp_display)
    if not sp:
        st.stop()

    allowed_first = allowed_first_for_sp(sp, st.session_state.assigned_by_cell)
    first_map = {cell_pretty(c): c for c in allowed_first}
    first_choice_pretty = st.selectbox("First slot (valid & free)", options=["— choose —"] + list(first_map.keys()), index=0, key="swing_first")
    first_choice = first_map.get(first_choice_pretty)

    current = st.session_state.sp_assignments.get(sp, [])
    st.caption(f"Current Swing assignments for **{sp}**: {', '.join(map(cell_pretty, current)) or 'none'}")

with col_right:
    if first_choice:
        local_seconds = allowed_second_for_sp_given_first(sp, first_choice, st.session_state.assigned_by_cell, st.session_state.sp_used_Z2)
        feasible_seconds = filter_second_choices_by_global_feasibility(sp, first_choice, local_seconds, specialists)

        second_map = {cell_pretty(c): c for c in feasible_seconds}
        second_choice_pretty = st.selectbox("Second slot (computed — feasible for everyone)", options=["— choose —"] + list(second_map.keys()), index=0, key="swing_second")
        second_choice = second_map.get(second_choice_pretty)

        if not feasible_seconds and local_seconds:
            st.warning("All rule-legal seconds would break the Swing schedule for others. Pick a different *First slot*.")

        if st.button("✅ Commit Swing First + Second", key="commit_swing"):
            if not second_choice:
                st.error("Pick a valid second slot.")
            else:
                st.session_state.assigned_by_cell[first_choice] = sp
                st.session_state.assigned_by_cell[second_choice] = sp
                st.session_state.sp_assignments.setdefault(sp, []).extend([first_choice, second_choice])
                if parse_cell(first_choice)[1] == "Z2" or parse_cell(second_choice)[1] == "Z2":
                    st.session_state.sp_used_Z2[sp] = True
                st.success(f"Assigned {sp} to Swing:\n• {cell_pretty(first_choice)}\n• {cell_pretty(second_choice)}")
                st.rerun()
    else:
        st.info("Select a *First slot* on the left to compute *Second slot* options.")

# ──────────────────────────────────────────────────────────────────────────────
# V2 AR/Backup picker
# ──────────────────────────────────────────────────────────────────────────────
st.divider()
st.subheader("Pick AR + Backup (V2)")

st.caption("AR/Backup options avoid double-booking and keep remaining AR/BK solvable.")

ar_left, ar_right = st.columns(2)

with ar_left:
    sp2 = st.selectbox("Specialist (AR/Backup)", options=specialists, key="sp_ar_bk")

    ar_rows_local = allowed_ar_rows_for_sp(sp2)
    ar_rows = filter_rows_by_feasibility_for_sp(sp2, "AR", ar_rows_local, specialists)

    ar_label_to_row = {row_label(r): r for r in ar_rows}
    ar_pick_label = st.selectbox("AR time block (valid & feasible)", options=["— choose —"] + list(ar_label_to_row.keys()), index=0, key="ar_pick_label")
    ar_pick = ar_label_to_row.get(ar_pick_label)

with ar_right:
    bk_rows_local = allowed_bk_rows_for_sp(sp2, chosen_ar_row=ar_pick)
    bk_rows = filter_rows_by_feasibility_for_sp(sp2, "BK", bk_rows_local, specialists, chosen_ar_row=ar_pick)

    bk_label_to_row = {row_label(r): r for r in bk_rows}
    bk_pick_label = st.selectbox("Backup time block (valid & feasible)", options=["— choose —"] + list(bk_label_to_row.keys()), index=0, key="bk_pick_label")
    bk_pick = bk_label_to_row.get(bk_pick_label)

    if st.button("✅ Commit AR + Backup", key="commit_ar_bk"):
        if not ar_pick or not bk_pick:
            st.error("Pick both an AR time block and a Backup time block.")
        elif ar_pick == bk_pick:
            st.error("AR and Backup cannot be the same time block.")
        else:
            st.session_state.ar_by_row[ar_pick] = sp2
            st.session_state.bk_by_row[bk_pick] = sp2
            st.session_state.sp_used_AR[sp2] = True
            st.session_state.sp_used_BK[sp2] = True
            st.success(f"Assigned {sp2}:\n• AR: {row_label(ar_pick)}\n• Backup: {row_label(bk_pick)}")
            st.rerun()

st.caption("If people get stuck after preferences, use Auto-fill to complete remaining AR/Backup safely.")
if st.button("🧠 Auto-fill remaining AR/Backup", key="autofill_ar_bk"):
    base_ar = dict(st.session_state.ar_by_row)
    base_bk = dict(st.session_state.bk_by_row)
    base_used_ar = dict(st.session_state.sp_used_AR)
    base_used_bk = dict(st.session_state.sp_used_BK)

    for _, who in base_ar.items():
        base_used_ar[who] = True
    for _, who in base_bk.items():
        base_used_bk[who] = True

    res = _solve_ar_bk(specialists, base_ar, base_bk, base_used_ar, base_used_bk)
    if res is None:
        st.error("No valid AR/Backup completion exists with the current partial choices.")
    else:
        na, nb, nua, nub = res
        st.session_state.ar_by_row = na
        st.session_state.bk_by_row = nb
        st.session_state.sp_used_AR = nua
        st.session_state.sp_used_BK = nub
        st.success("Auto-fill completed remaining AR/Backup assignments.")
        st.rerun()

# ──────────────────────────────────────────────────────────────────────────────
# Tables
# ──────────────────────────────────────────────────────────────────────────────
st.divider()
st.subheader("All Assignments (Swing)")

if st.session_state.assigned_by_cell:
    data = []
    for cell, who in sorted(st.session_state.assigned_by_cell.items(),
                            key=lambda kv: (parse_cell(kv[0])[0], parse_cell(kv[0])[1])):
        data.append({"Specialist": who, "Slot": cell_pretty(cell)})
    df = pd.DataFrame(data)
    df.index = range(1, len(df) + 1)
    st.table(df)
else:
    st.write("No Swing assignments yet.")

st.subheader("All Assignments (AR + Backup)")
ar_bk_rows = []
for r in range(1, 7):
    ar_bk_rows.append({
        "Time": TIME_LABELS[r],
        "AR": st.session_state.ar_by_row.get(r, "—"),
        "Backup": st.session_state.bk_by_row.get(r, "—"),
    })
st.table(pd.DataFrame(ar_bk_rows))

st.caption("Tip: change the Swing blocked pattern by editing BLOCKED_CELLS at the top of the file.")
