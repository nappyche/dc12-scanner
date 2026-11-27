import streamlit as st
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import math

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
def api_get(endpoint: str, params: dict, api_key: str):
    """Cached GET wrapper to protect daily RapidAPI quota."""
    url = f"{BASE_URL}/{endpoint.lstrip('/')}"
    try:
        resp = requests.get(url, headers=get_headers(api_key), params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json(), None
        return None, f"HTTP {resp.status_code}: {resp.text[:160]}"
    except Exception as e:
        return None, str(e)

def extract_matches_list(payload) -> list:
    """Flexibly extracts match objects from Creativesdev JSON responses."""
    if not payload:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        # Common wrapper keys in free-api-live-football-data
        for key in ["response", "matches", "fixtures", "events", "data", "result"]:
            val = payload.get(key)
            if isinstance(val, list):
                return val
            if isinstance(val, dict):
                for subkey in ["matches", "fixtures", "events", "all"]:
                    if isinstance(val.get(subkey), list):
                        return val[subkey]
                # Sometimes grouped by league list
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
    """Normalizes match fields across slightly varying endpoint schemas."""
    match_id = m.get("id") or m.get("eventId") or m.get("matchId") or ""
    home_obj = m.get("home") if isinstance(m.get("home"), dict) else {}
    away_obj = m.get("away") if isinstance(m.get("away"), dict) else {}

    home_name = (
        home_obj.get("name")
        or home_obj.get("longName")
        or m.get("homeTeam")
        or m.get("home_name")
        or "Home"
    )
    away_name = (
        away_obj.get("name")
        or away_obj.get("longName")
        or m.get("awayTeam")
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
    kickoff = m.get("time") or m.get("status", {}).get("utcTime") if isinstance(m.get("status"), dict) else m.get("startTime", "")
    status_str = ""
    if isinstance(m.get("status"), dict):
        status_str = m["status"].get("reason", {}).get("short", "") or ("FT" if m["status"].get("finished") else "NS")
    else:
        status_str = str(m.get("status", ""))

    # Score parsing (for completed matches in history/H2H)
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
        "home_name": str(home_name),
        "away_name": str(away_name),
        "home_id": str(home_id) if home_id is not None else "",
        "away_id": str(away_id) if away_id is not None else "",
        "home_score": h_score,
        "away_score": a_score,
    }

def results_from_matches(matches_raw: list, team_name: str = "") -> list:
    """
    Converts list of finished matches (ordered most-recent-first) into W/D/L and goal pairs.
    Returns list of dicts: [{'res': 'D', 'gf': 1, 'ga': 1}, ...]
    """
    parsed = [parse_match_item(x) for x in matches_raw]
    out = []
    for p in parsed:
        try:
            hs = int(p["home_score"])
            as_ = int(p["away_score"])
        except (TypeError, ValueError):
            continue
        if hs == as_:
            res = "D"
        elif team_name and team_name.lower() in p["away_name"].lower():
            res = "W" if as_ > hs else "L"
        else:
            res = "W" if hs > as_ else "L"

        gf, ga = (as_, hs) if (team_name and team_name.lower() in p["away_name"].lower()) else (hs, as_)
        out.append({"res": res, "gf": gf, "ga": ga})
    return out[:5]

# ==========================================
# CORE PREDICTION ENGINE (YOUR RULES + QUANT)
# ==========================================
def count_leading_draws(seq: list) -> int:
    """Counts consecutive draws immediately preceding the upcoming fixture (index 0 = most recent)."""
    streak = 0
    for item in seq:
        r = item["res"] if isinstance(item, dict) else str(item).upper()
        if r == "D":
            streak += 1
        else:
            break
    return streak

def poisson_12_probability(avg_home_goals: float, avg_away_goals: float) -> float:
    """Computes 1 - P(Draw) from bivariate independent Poisson goal distributions (0-0 to 6-6)."""
    lh = max(0.35, min(3.5, avg_home_goals))
    la = max(0.35, min(3.5, avg_away_goals))
    p_draw = 0.0
    for g in range(7):
        ph = (math.exp(-lh) * (lh ** g)) / math.factorial(g)
        pa = (math.exp(-la) * (la ** g)) / math.factorial(g)
        p_draw += ph * pa
    return max(0.50, min(0.95, 1.0 - p_draw))

def evaluate_fixture(home_last5: list, away_last5: list, h2h_last5: list) -> dict:
    """
    Combines User's Consecutive-Draw Streak Rules with Quantitative 12 Probability Modeling.
    - home_last5, away_last5, h2h_last5: lists ordered MOST RECENT FIRST.
    """
    h_streak = count_leading_draws(home_last5)
    a_streak = count_leading_draws(away_last5)
    h2h_streak = count_leading_draws(h2h_last5)

    # User Rules Check
    rule_h2h_hit = h2h_streak >= 2          # 2 or 3+ consecutive draws in H2H
    rule_home_hit = h_streak >= 2           # Home team 2+ straight draws
    rule_away_hit = a_streak >= 2           # Away team 2+ straight draws
    rule_form_hit = rule_home_hit or rule_away_hit

    triggers = []
    if rule_h2h_hit:
        triggers.append(f"H2H Draw Streak ({h2h_streak} straight)")
    if rule_home_hit:
        triggers.append(f"Home Draw Streak ({h_streak} straight)")
    if rule_away_hit:
        triggers.append(f"Away Draw Streak ({a_streak} straight)")

    # Quantitative Layer: Goal Expectancy + Historical Non-Draw Rate + Streak Mean-Reversion
    all_samples = home_last5 + away_last5 + h2h_last5
    if all_samples and isinstance(all_samples[0], dict):
        avg_gf = np.mean([x["gf"] for x in all_samples])
        avg_ga = np.mean([x["ga"] for x in all_samples])
        poisson_12 = poisson_12_probability(avg_gf, avg_ga)
    else:
        poisson_12 = 0.74  # Baseline global football non-draw rate (~74%)

    # Decisive non-draw rate in last 5
    non_draws = [1 for x in all_samples if (x["res"] if isinstance(x, dict) else x) != "D"]
    hist_non_draw = (len(non_draws) / len(all_samples)) if all_samples else 0.74

    # Streak-break probability boost calibrated to your rules
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

    # Tier Classification
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

    max_scan = st.slider("Max Fixtures to Deep-Scan per Batch", min_value=5, max_value=40, value=15, step=5,
                         help="Each match checks H2H + Home Last 5 + Away Last 5. Keep batches at 15-25 to preserve free daily quota.")
    show_all = st.checkbox("Show Non-Triggered Matches Too", value=False)

    st.divider()
    st.caption("**Active Rules:**\n- H2H: 2–3+ straight draws → `12`\n- Home or Away: 2+ straight draws → `12`\n- Quant: Poisson + Non-Draw %")

# ==========================================
# MAIN INTERFACE
# ==========================================
st.title("⚽ Global `12` (Home or Away) Double Chance Scanner")
st.caption("Scans global leagues & lower tiers for consecutive-draw streak breakouts + Poisson non-draw probability.")

tab_live, tab_manual = st.tabs(["🌍 Live Global Fixture Scanner", "🧮 Instant Match Analyzer (Zero API Cost)"])

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
                # Primary endpoint for matches by date
                raw_data, err = api_get("football-get-matches-by-date", {"date": date_str}, api_key)
                if err or not extract_matches_list(raw_data):
                    # Fallback endpoint naming if needed
                    raw_data, err = api_get("football-get-all-matches-by-date", {"date": date_str}, api_key)

            if err and not raw_data:
                st.error(f"API Connection Issue: {err}")
            else:
                matches_raw = extract_matches_list(raw_data)
                parsed_fixtures = [parse_match_item(m) for m in matches_raw]

                if league_filter.strip():
                    lf = league_filter.strip().lower()
                    parsed_fixtures = [f for f in parsed_fixtures if lf in f["league"].lower() or lf in f["home_name"].lower() or lf in f["away_name"].lower()]

                st.info(f"Found **{len(parsed_fixtures)}** global fixtures matching your filter. Deep-scanning the first **{min(len(parsed_fixtures), max_scan)}** matches...")

                results_rows = []
                progress = st.progress(0)
                batch = parsed_fixtures[:max_scan]

                for idx, fx in enumerate(batch):
                    progress.progress((idx + 1) / max(1, len(batch)))

                    # 1. Fetch H2H
                    h2h_seq = []
                    if fx["match_id"]:
                        h2h_json, _ = api_get("football-get-head-to-head", {"eventid": fx["match_id"]}, api_key)
                        if not h2h_json and fx["home_id"] and fx["away_id"]:
                            h2h_json, _ = api_get("football-get-h2h", {"homeid": fx["home_id"], "awayid": fx["away_id"]}, api_key)
                        h2h_seq = results_from_matches(extract_matches_list(h2h_json))

                    # 2. Fetch Home Team Last 5
                    home_seq = []
                    if fx["home_id"]:
                        h_json, _ = api_get("football-get-team-matches", {"teamid": fx["home_id"]}, api_key)
                        home_seq = results_from_matches(extract_matches_list(h_json), fx["home_name"])

                    # 3. Fetch Away Team Last 5
                    away_seq = []
                    if fx["away_id"]:
                        a_json, _ = api_get("football-get-team-matches", {"teamid": fx["away_id"]}, api_key)
                        away_seq = results_from_matches(extract_matches_list(a_json), fx["away_name"])

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
                    st.warning("No fixtures in this batch triggered the consecutive-draw rules. Try increasing the batch size or switching dates.")

# ------------------------------------------
# TAB 2: INSTANT MANUAL / OFFLINE ANALYZER
# ------------------------------------------
with tab_manual:
    st.subheader("Check Any Match Instantly (Uses 0 API Calls)")
    st.write("Enter the last 5 results from **MOST RECENT to OLDEST** using `W`, `D`, `L` (e.g. `D, D, W, L, W`).")

    c1, c2 = st.columns(2)
    home_team = c1.text_input("Home Team", value="Team A")
    away_team = c2.text_input("Away Team", value="Team B")

    c3, c4, c5 = st.columns(3)
    h_input = c3.text_input(f"{home_team} Last 5 (Most recent first)", value="D, D, W, L, W")
    a_input = c4.text_input(f"{away_team} Last 5 (Most recent first)", value="W, L, D, W, L")
    h2h_input = c5.text_input("H2H Last 5 (Most recent first)", value="D, D, D, W, L")

    def parse_manual_seq(txt: str) -> list:
        tokens = [t.strip().upper() for t in txt.replace("-", ",").split(",") if t.strip().upper() in {"W", "D", "L"}]
        out = []
        for t in tokens[:5]:
            if t == "D":
                out.append({"res": "D", "gf": 1, "ga": 1})
            elif t == "W":
                out.append({"res": "W", "gf": 2, "ga": 0})
            else:
                out.append({"res": "L", "gf": 0, "ga": 2})
        return out

    if st.button("⚡ Run 12 Model Check"):
        res = evaluate_fixture(
            parse_manual_seq(h_input),
            parse_manual_seq(a_input),
            parse_manual_seq(h2h_input)
        )
        colA, colB, colC = st.columns(3)
        colA.metric("Verdict", res["tier"])
        colB.metric("12 Win Probability", f"{res['prob_12']}%")
        colC.metric("Fair 12 Odds", f"{res['fair_odds']}")
        st.success(f"**Triggered Logic:** {res['triggers']}")
