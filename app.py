import streamlit as st
import requests
import pandas as pd
import numpy as np
from datetime import datetime
import math
import re

# ==========================================
# PAGE CONFIG & STYLING
# ==========================================
st.set_page_config(
    page_title="Global 12 Double Chance Scanner",
    page_icon="⚽",
    layout="wide"
)

st.markdown("""
<style>
    .signal-ultra {background-color: #14532d; color: #bbf7d0; padding: 4px 10px; border-radius: 6px; font-weight: bold;}
    .signal-strong {background-color: #1e3a8a; color: #bfdbfe; padding: 4px 10px; border-radius: 6px; font-weight: bold;}
    .signal-watch {background-color: #78350f; color: #fde68a; padding: 4px 10px; border-radius: 6px; font-weight: bold;}
</style>
""", unsafe_allow_html=True)

# ==========================================
# API CLIENT & SMART PARSER
# ==========================================
API_HOST = "free-api-live-football-data.p.rapidapi.com"
BASE_URL = f"https://{API_HOST}"

def get_headers(api_key: str) -> dict:
    return {
        "x-rapidapi-key": api_key.strip(),
        "x-rapidapi-host": API_HOST,
        "accept": "application/json"
    }

@st.cache_data(ttl=3600, show_spinner=False)
def api_get(endpoint: str, params_tuple: tuple, api_key: str):
    """Cached GET wrapper to protect daily RapidAPI quota."""
    params = dict(params_tuple)
    url = f"{BASE_URL}/{endpoint.lstrip('/')}"
    try:
        resp = requests.get(url, headers=get_headers(api_key), params=params, timeout=15)
        if resp.status_code == 200:
            try:
                return resp.json(), None
            except Exception as e:
                return None, f"JSON decode error: {e}"
        return None, f"HTTP {resp.status_code}: {resp.text[:180]}"
    except Exception as e:
        return None, str(e)

def try_endpoints(candidates: list, api_key: str):
    last_err = "No endpoint responded with match data"
    for ep, params in candidates:
        data, err = api_get(ep, tuple(sorted(params.items())), api_key)
        if err:
            last_err = f"{ep} {params} -> {err}"
            continue
        matches = extract_matches_list(data)
        if matches:
            return data, matches, None, f"{ep} {params}"
        last_err = f"{ep} {params} -> 200 OK but 0 matches parsed"
    return None, [], last_err, None

def extract_matches_list(payload) -> list:
    if not payload:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ["response", "matches", "fixtures", "events", "data", "result", "previousMatches", "lastMatches"]:
            val = payload.get(key)
            if isinstance(val, list) and val:
                return val
            if isinstance(val, dict):
                for subkey in ["matches", "fixtures", "events", "all", "previous", "history"]:
                    if isinstance(val.get(subkey), list) and val[subkey]:
                        return val[subkey]
                if isinstance(val.get("leagues"), list):
                    flat = []
                    for lg in val["leagues"]:
                        lg_name = lg.get("name") or lg.get("leagueName") or "Unknown League"
                        country = lg.get("ccode") or lg.get("country") or ""
                        for m in lg.get("matches", []):
                            m["_league_name"] = f"{country} - {lg_name}".strip(" -")
                            flat.append(m)
                    if flat:
                        return flat
    return []

def parse_match_item(m: dict) -> dict:
    match_id = m.get("id") or m.get("eventId") or m.get("matchId") or m.get("fixtureId") or ""
    home_obj = m.get("home") if isinstance(m.get("home"), dict) else (m.get("homeTeam") if isinstance(m.get("homeTeam"), dict) else {})
    away_obj = m.get("away") if isinstance(m.get("away"), dict) else (m.get("awayTeam") if isinstance(m.get("awayTeam"), dict) else {})

    home_name = (
        home_obj.get("name")
        or home_obj.get("longName")
        or home_obj.get("shortName")
        or (m.get("homeTeam") if isinstance(m.get("homeTeam"), str) else None)
        or m.get("home_name")
        or "Home"
    )
    away_name = (
        away_obj.get("name")
        or away_obj.get("longName")
        or away_obj.get("shortName")
        or (m.get("awayTeam") if isinstance(m.get("awayTeam"), str) else None)
        or m.get("away_name")
        or "Away"
    )
    home_id = home_obj.get("id") or m.get("homeId") or m.get("home_id")
    away_id = away_obj.get("id") or m.get("awayId") or m.get("away_id")

    league = (
        m.get("_league_name")
        or (m.get("league", {}).get("name") if isinstance(m.get("league"), dict) else m.get("leagueName"))
        or "Global League"
    )

    status_obj = m.get("status") if isinstance(m.get("status"), dict) else {}
    kickoff = m.get("time") or status_obj.get("utcTime") or m.get("startTime") or ""
    finished = bool(status_obj.get("finished"))
    status_str = status_obj.get("reason", {}).get("short", "") if isinstance(status_obj.get("reason"), dict) else str(m.get("status", ""))

    h_score = home_obj.get("score") if "score" in home_obj else m.get("homeScore")
    a_score = away_obj.get("score") if "score" in away_obj else m.get("awayScore")
    if h_score is None and isinstance(m.get("score"), dict):
        h_score = m["score"].get("home")
        a_score = m["score"].get("away")

    return {
        "match_id": str(match_id),
        "league": str(league),
        "kickoff": str(kickoff),
        "status": str(status_str),
        "finished": finished,
        "home_name": str(home_name),
        "away_name": str(away_name),
        "home_id": str(home_id) if home_id is not None else "",
        "away_id": str(away_id) if away_id is not None else "",
        "home_score": h_score,
        "away_score": a_score,
    }

def results_from_matches(matches_raw: list, team_name: str = "", team_id: str = "") -> list:
    parsed = [parse_match_item(x) for x in matches_raw]
    out = []
    for p in parsed:
        try:
            hs = int(p["home_score"])
            as_ = int(p["away_score"])
        except (TypeError, ValueError):
            continue

        is_away = False
        if team_id and p["away_id"] == str(team_id):
            is_away = True
        elif team_name and team_name.lower() in p["away_name"].lower() and team_name.lower() not in p["home_name"].lower():
            is_away = True

        if hs == as_:
            res = "D"
        elif is_away:
            res = "W" if as_ > hs else "L"
        else:
            res = "W" if hs > as_ else "L"

        gf, ga = (as_, hs) if is_away else (hs, as_)
        out.append({"res": res, "gf": gf, "ga": ga, "label": f"{p['home_name']} {hs}-{as_} {p['away_name']} ({res})"})
    return out[:5]

def find_team_id_by_name(team_query: str, api_key: str) -> tuple:
    q = team_query.strip().lower()
    if not q:
        return None, None, "Empty team name"

    search_candidates = [
        ("football-search", {"q": team_query.strip()}),
        ("football-search-team", {"name": team_query.strip()}),
        ("football-get-matches-by-date", {"date": datetime.utcnow().strftime("%Y%m%d")}),
    ]
    for ep, params in search_candidates:
        data, err = api_get(ep, tuple(sorted(params.items())), api_key)
        if not err and data:
            for m in extract_matches_list(data):
                p = parse_match_item(m)
                if q in p["home_name"].lower() and p["home_id"]:
                    return p["home_id"], p["home_name"], f"Matched via {ep}"
                if q in p["away_name"].lower() and p["away_id"]:
                    return p["away_id"], p["away_name"], f"Matched via {ep}"

    for offset in [-1, 1]:
        d_str = (datetime.utcnow().date() + pd.Timedelta(days=offset)).strftime("%Y%m%d")
        data, err = api_get("football-get-matches-by-date", (("date", d_str),), api_key)
        if not err and data:
            for m in extract_matches_list(data):
                p = parse_match_item(m)
                if q in p["home_name"].lower() and p["home_id"]:
                    return p["home_id"], p["home_name"], f"Matched on {d_str}"
                if q in p["away_name"].lower() and p["away_id"]:
                    return p["away_id"], p["away_name"], f"Matched on {d_str}"

    return None, team_query.strip(), "Team ID not found in active fixture feed"

def fetch_team_last5(team_id: str, team_name: str, api_key: str):
    candidates = [
        ("football-get-team-matches", {"teamid": team_id}),
        ("football-get-team-matches", {"id": team_id}),
        ("football-get-previous-matches-by-team", {"teamid": team_id}),
        ("football-get-team-fixtures", {"teamid": team_id}),
    ]
    _, raw_matches, err, ep_used = try_endpoints(candidates, api_key)
    seq = results_from_matches(raw_matches, team_name=team_name, team_id=team_id)
    return seq, err, ep_used

def fetch_h2h_last5(home_id: str, away_id: str, match_id: str, api_key: str):
    candidates = []
    if match_id:
        candidates.append(("football-get-head-to-head", {"eventid": match_id}))
        candidates.append(("football-get-head-to-head", {"id": match_id}))
    if home_id and away_id:
        candidates.append(("football-get-head-to-head", {"homeid": home_id, "awayid": away_id}))
        candidates.append(("football-get-h2h", {"homeid": home_id, "awayid": away_id}))
        candidates.append(("football-get-h2h", {"team1": home_id, "team2": away_id}))
    _, raw_matches, err, ep_used = try_endpoints(candidates, api_key)
    seq = results_from_matches(raw_matches)
    return seq, err, ep_used

# ==========================================
# SMART INPUT PARSER (SCORES, W/D/L, 1/X/2)
# ==========================================
def parse_smart_sequence(txt: str, home_label: str = "Home", away_label: str = "Away") -> list:
    """
    Parses last-5 sequences in ANY of these formats (comma-separated, most recent first):
      1. Scorelines (HomeTeamGoals - AwayTeamGoals): e.g. '2-1, 1-1, 0-2, 0-0, 3-1'
      2. W / D / L (from HomeTeam perspective): e.g. 'W, D, L, D, W'
      3. 1 / X / 2 or H / D / A: e.g. '1, X, 2, X, 1'
    """
    items = [t.strip() for t in txt.split(",") if t.strip()]
    out = []
    for raw in items[:5]:
        # Check for scoreline like "2-1" or "2:1"
        m = re.match(r"^(\d+)\s*[-:]\s*(\d+)$", raw)
        if m:
            gf, ga = int(m.group(1)), int(m.group(2))
            if gf > ga:
                res = "W"
                desc = f"{home_label} Won ({gf}-{ga})"
            elif gf < ga:
                res = "L"
                desc = f"{away_label} Won ({gf}-{ga})"
            else:
                res = "D"
                desc = f"Draw ({gf}-{ga})"
            out.append({"res": res, "gf": gf, "ga": ga, "desc": desc})
            continue

        tok = raw.upper()
        if tok in {"W", "1", "H"}:
            out.append({"res": "W", "gf": 2, "ga": 0, "desc": f"{home_label} Won"})
        elif tok in {"L", "2", "A"}:
            out.append({"res": "L", "gf": 0, "ga": 2, "desc": f"{away_label} Won"})
        elif tok in {"D", "X"}:
            out.append({"res": "D", "gf": 1, "ga": 1, "desc": "Draw"})
    return out

# ==========================================
# CORE PREDICTION ENGINE (YOUR RULES + QUANT)
# ==========================================
def count_leading_draws(seq: list) -> int:
    streak = 0
    for item in seq:
        r = item["res"] if isinstance(item, dict) else str(item).upper()
        if r == "D":
            streak += 1
        else:
            break
    return streak

def poisson_12_probability(avg_home_goals: float, avg_away_goals: float) -> float:
    lh = max(0.35, min(3.5, avg_home_goals))
    la = max(0.35, min(3.5, avg_away_goals))
    p_draw = 0.0
    for g in range(7):
        ph = (math.exp(-lh) * (lh ** g)) / math.factorial(g)
        pa = (math.exp(-la) * (la ** g)) / math.factorial(g)
        p_draw += ph * pa
    return max(0.50, min(0.95, 1.0 - p_draw))

def evaluate_fixture(home_last5: list, away_last5: list, h2h_last5: list) -> dict:
    h_streak = count_leading_draws(home_last5)
    a_streak = count_leading_draws(away_last5)
    h2h_streak = count_leading_draws(h2h_last5)

    rule_h2h_hit = h2h_streak >= 2
    rule_home_hit = h_streak >= 2
    rule_away_hit = a_streak >= 2
    rule_form_hit = rule_home_hit or rule_away_hit

    triggers = []
    if rule_h2h_hit:
        triggers.append(f"H2H Draw Streak ({h2h_streak} straight)")
    if rule_home_hit:
        triggers.append(f"Home Draw Streak ({h_streak} straight)")
    if rule_away_hit:
        triggers.append(f"Away Draw Streak ({a_streak} straight)")

    all_samples = home_last5 + away_last5 + h2h_last5
    if all_samples and isinstance(all_samples[0], dict):
        avg_gf = np.mean([x["gf"] for x in all_samples])
        avg_ga = np.mean([x["ga"] for x in all_samples])
        poisson_12 = poisson_12_probability(avg_gf, avg_ga)
    else:
        poisson_12 = 0.74

    non_draws = [1 for x in all_samples if (x["res"] if isinstance(x, dict) else x) != "D"]
    hist_non_draw = (len(non_draws) / len(all_samples)) if all_samples else 0.74

    streak_boost = 0.0
    if rule_h2h_hit:
        streak_boost += 0.08 if h2h_streak == 2 else 0.11
    if rule_home_hit:
        streak_boost += 0.06
    if rule_away_hit:
        streak_boost += 0.06

    raw_prob = (0.50 * poisson_12) + (0.35 * hist_non_draw) + (0.15 * 0.75) + streak_boost
    prob_12 = round(min(0.94, max(0.55, raw_prob)) * 100, 1)
    fair_odds = round(100.0 / prob_12, 2)

    if rule_h2h_hit and rule_form_hit:
        tier = "🔥 ULTRA 12 LOCK"
        is_pick = True
    elif rule_h2h_hit or rule_form_hit:
        tier = "⚡ STRONG 12 SIGNAL"
        is_pick = True
    elif prob_12 >= 78.0:
        tier = "📊 QUANT 12 LEAN"
        is_pick = True
    else:
        tier = "⏸️ SKIP / NO STREAK"
        is_pick = False

    def fmt_seq(seq):
        return "-".join([x["res"] if isinstance(x, dict) else str(x) for x in seq]) if seq else "N/A"

    return {
        "is_pick": is_pick,
        "tier": tier,
        "prob_12": prob_12,
        "fair_odds": fair_odds,
        "h2h_streak": h2h_streak,
        "home_streak": h_streak,
        "away_streak": a_streak,
        "home_form": fmt_seq(home_last5),
        "away_form": fmt_seq(away_last5),
        "h2h_form": fmt_seq(h2h_last5),
        "triggers": " + ".join(triggers) if triggers else "High Non-Draw Goal Model"
    }

# ==========================================
# SIDEBAR CONFIGURATION
# ==========================================
with st.sidebar:
    st.header("⚙️ Scanner Controls")
    default_key = st.secrets.get("RAPIDAPI_KEY", "") if hasattr(st, "secrets") else ""
    api_key = st.text_input("RapidAPI Key", value=default_key, type="password", help="Stored safely in Streamlit Secrets")

    scan_date = st.date_input("Fixture Date", value=datetime.utcnow().date())
    date_str = scan_date.strftime("%Y%m%d")

    max_scan = st.slider("Max Fixtures to Deep-Scan per Batch", min_value=5, max_value=40, value=15, step=5)
    show_all = st.checkbox("Show Non-Triggered Matches Too", value=False)
    show_debug = st.checkbox("Show API Endpoint Debug Info", value=False)

    st.divider()
    st.caption("**Active Rules:**\n- H2H: 2–3+ straight draws → `12`\n- Home or Away: 2+ straight draws → `12`\n- Quant: Poisson + Non-Draw %")

# ==========================================
# MAIN INTERFACE
# ==========================================
st.title("⚽ Global `12` (Home or Away) Double Chance Scanner")
st.caption("Scans global leagues & lower tiers for consecutive-draw streak breakouts + Poisson non-draw probability.")

tab_live, tab_manual = st.tabs(["🌍 Live Global Fixture Scanner", "🧮 Instant Match Analyzer (Auto-Fetch + Manual)"])

# ------------------------------------------
# TAB 1: LIVE API SCANNER
# ------------------------------------------
with tab_live:
    col1, col2 = st.columns([2, 1])
    with col1:
        league_filter = st.text_input("Optional Country / League Filter (leave blank for ALL global leagues)", placeholder="e.g. England, Brazil, Germany, Serie B, Liga...")
    with col2:
        st.write("")
        st.write("")
        run_scan = st.button("🚀 Scan Global Fixtures", type="primary", use_container_width=True)

    if run_scan:
        if not api_key:
            st.error("Please enter your RapidAPI key in the sidebar (or add `RAPIDAPI_KEY` in Streamlit Secrets).")
        else:
            with st.spinner(f"Fetching global fixtures for {scan_date.strftime('%Y-%m-%d')}..."):
                _, matches_raw, err, ep_fixtures = try_endpoints([
                    ("football-get-matches-by-date", {"date": date_str}),
                    ("football-get-all-matches-by-date", {"date": date_str}),
                    ("football-get-matches-by-date-and-league", {"date": date_str}),
                ], api_key)

            if not matches_raw:
                st.error(f"Could not load fixtures for {date_str}. Last detail: {err}")
            else:
                parsed_fixtures = [parse_match_item(m) for m in matches_raw]

                if league_filter.strip():
                    lf = league_filter.strip().lower()
                    parsed_fixtures = [
                        f for f in parsed_fixtures
                        if lf in f["league"].lower() or lf in f["home_name"].lower() or lf in f["away_name"].lower()
                    ]

                st.info(f"Found **{len(parsed_fixtures)}** global fixtures (via `{ep_fixtures}`). Deep-scanning **{min(len(parsed_fixtures), max_scan)}** matches...")

                results_rows = []
                debug_logs = []
                progress = st.progress(0)
                batch = parsed_fixtures[:max_scan]

                for idx, fx in enumerate(batch):
                    progress.progress((idx + 1) / max(1, len(batch)))

                    h2h_seq, h2h_err, _ = fetch_h2h_last5(fx["home_id"], fx["away_id"], fx["match_id"], api_key)
                    home_seq, h_err, _ = fetch_team_last5(fx["home_id"], fx["home_name"], api_key) if fx["home_id"] else ([], "No home_id", None)
                    away_seq, a_err, _ = fetch_team_last5(fx["away_id"], fx["away_name"], api_key) if fx["away_id"] else ([], "No away_id", None)

                    if show_debug and idx < 3:
                        debug_logs.append({
                            "Match": f"{fx['home_name']} vs {fx['away_name']}",
                            "H2H_Count": len(h2h_seq),
                            "Home_Count": len(home_seq),
                            "Away_Count": len(away_seq),
                            "H2H_Note": h2h_err or "OK",
                            "Home_Note": h_err or "OK",
                            "Away_Note": a_err or "OK",
                        })

                    eval_res = evaluate_fixture(home_seq, away_seq, h2h_seq)

                    if eval_res["is_pick"] or show_all:
                        results_rows.append({
                            "Signal": eval_res["tier"],
                            "League": fx["league"],
                            "Match": f"{fx['home_name']} vs {fx['away_name']}",
                            "Pick": "12 (Home/Away)",
                            "12 Prob %": eval_res["prob_12"],
                            "Fair 12 Odds": eval_res["fair_odds"],
                            "H2H (Last 5)": eval_res["h2h_form"],
                            "Home (Last 5)": eval_res["home_form"],
                            "Away (Last 5)": eval_res["away_form"],
                            "Triggered Rule": eval_res["triggers"]
                        })

                progress.empty()

                if debug_logs:
                    with st.expander("🛠️ API Endpoint Diagnostic Sample (First 3 Matches)"):
                        st.dataframe(pd.DataFrame(debug_logs), use_container_width=True, hide_index=True)

                if results_rows:
                    df = pd.DataFrame(results_rows).sort_values(by="12 Prob %", ascending=False)
                    m1, m2, m3 = st.columns(3)
                    m1.metric("Qualified 12 Picks", len(df[df["Signal"] != "⏸️ SKIP / NO STREAK"]))
                    m2.metric("Ultra Locks (H2H + Form)", len(df[df["Signal"] == "🔥 ULTRA 12 LOCK"]))
                    m3.metric("Top 12 Confidence", f"{df['12 Prob %'].max()}%")

                    st.dataframe(df, use_container_width=True, hide_index=True)
                    st.download_button(
                        "⬇️ Download Picks (CSV)",
                        data=df.to_csv(index=False).encode("utf-8"),
                        file_name=f"12_picks_{date_str}.csv",
                        mime="text/csv"
                    )
                else:
                    st.warning("No fixtures in this batch triggered the consecutive-draw rules (or history returned N/A). Try turning on 'Show Non-Triggered Matches Too' or 'Show API Endpoint Debug Info' in the sidebar.")

# ------------------------------------------
# TAB 2: INSTANT ANALYZER (AUTO-FETCH + MANUAL)
# ------------------------------------------
with tab_manual:
    st.subheader("Auto-Fetch Last 5 & H2H — or Enter Manually")
    st.write("Enter **MOST RECENT to OLDEST**. You can type **`W, D, L`** (for that team), **`1, X, 2`** (`1`=Home won, `2`=Away won, `X`=Draw), or exact **scores** like **`1-1, 0-0, 2-1`** (HomeTeamGoals - AwayTeamGoals).")

    for key, val in [
        ("auto_h_seq", "D, D, W, L, W"),
        ("auto_a_seq", "W, L, D, W, L"),
        ("auto_h2h_seq", "1-1, 0-0, 2-1, 1-2, 1-0"),
        ("auto_h_details", []),
        ("auto_a_details", []),
        ("auto_h2h_details", []),
        ("auto_status", ""),
    ]:
        if key not in st.session_state:
            st.session_state[key] = val

    c1, c2 = st.columns(2)
    home_team = c1.text_input("Home Team", value="Arsenal", key="manual_home_team")
    away_team = c2.text_input("Away Team", value="Manchester City", key="manual_away_team")

    if st.button("🔎 Auto-Fetch Last 5 & H2H"):
        if not api_key:
            st.error("Add your RapidAPI key in the sidebar first to auto-fetch team history.")
        else:
            with st.spinner("Searching teams and fetching last 5 matches + H2H..."):
                h_id, h_canon, h_src = find_team_id_by_name(home_team, api_key)
                a_id, a_canon, a_src = find_team_id_by_name(away_team, api_key)

                h_seq_list, h_err, _ = fetch_team_last5(h_id, h_canon or home_team, api_key) if h_id else ([], h_src, None)
                a_seq_list, a_err, _ = fetch_team_last5(a_id, a_canon or away_team, api_key) if a_id else ([], a_src, None)
                h2h_seq_list, h2h_err, _ = fetch_h2h_last5(h_id, a_id, "", api_key)

                if h_seq_list:
                    st.session_state["auto_h_seq"] = ", ".join([x["res"] for x in h_seq_list])
                    st.session_state["auto_h_details"] = [x["label"] for x in h_seq_list]
                if a_seq_list:
                    st.session_state["auto_a_seq"] = ", ".join([x["res"] for x in a_seq_list])
                    st.session_state["auto_a_details"] = [x["label"] for x in a_seq_list]
                if h2h_seq_list:
                    st.session_state["auto_h2h_seq"] = ", ".join([f"{x['gf']}-{x['ga']}" for x in h2h_seq_list])
                    st.session_state["auto_h2h_details"] = [x["label"] for x in h2h_seq_list]

                st.session_state["auto_status"] = (
                    f"Home ({h_canon}, ID={h_id or 'N/A'}): {len(h_seq_list)} matches ({h_err or 'OK'}) | "
                    f"Away ({a_canon}, ID={a_id or 'N/A'}): {len(a_seq_list)} matches ({a_err or 'OK'}) | "
                    f"H2H: {len(h2h_seq_list)} matches ({h2h_err or 'OK'})"
                )

    if st.session_state["auto_status"]:
        st.caption(st.session_state["auto_status"])

    c3, c4, c5 = st.columns(3)
    h_input = c3.text_input(f"{home_team} Last 5 (Most recent first)", value=st.session_state["auto_h_seq"])
    a_input = c4.text_input(f"{away_team} Last 5 (Most recent first)", value=st.session_state["auto_a_seq"])
    h2h_input = c5.text_input(f"H2H Last 5 ({home_team} vs {away_team})", value=st.session_state["auto_h2h_seq"])

    if st.session_state["auto_h_details"] or st.session_state["auto_a_details"] or st.session_state["auto_h2h_details"]:
        with st.expander("📋 Fetched Match Scorelines"):
            if st.session_state["auto_h_details"]:
                st.write(f"**{home_team} Last 5:** " + " | ".join(st.session_state["auto_h_details"]))
            if st.session_state["auto_a_details"]:
                st.write(f"**{away_team} Last 5:** " + " | ".join(st.session_state["auto_a_details"]))
            if st.session_state["auto_h2h_details"]:
                st.write("**H2H Last 5:** " + " | ".join(st.session_state["auto_h2h_details"]))

    if st.button("⚡ Run 12 Model Check", type="primary"):
        h_parsed = parse_smart_sequence(h_input, home_team, "Opponent")
        a_parsed = parse_smart_sequence(a_input, away_team, "Opponent")
        h2h_parsed = parse_smart_sequence(h2h_input, home_team, away_team)

        res = evaluate_fixture(h_parsed, a_parsed, h2h_parsed)
        colA, colB, colC = st.columns(3)
        colA.metric("Verdict", res["tier"])
        colB.metric("12 Win Probability", f"{res['prob_12']}%")
        colC.metric("Fair 12 Odds", f"{res['fair_odds']}")
        st.success(f"**Triggered Logic:** {res['triggers']}")

        if h2h_parsed:
            st.caption("**Parsed H2H Breakdown (Most Recent First):** " + " → ".join([x["desc"] for x in h2h_parsed]))
