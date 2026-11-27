import math
import re
from datetime import datetime, timezone
import requests
import streamlit as st

st.set_page_config(page_title="Double Chance (12) Scanner", layout="wide")

API_HOST = "free-api-live-football-data.p.rapidapi.com"
BASE_URL = f"https://{API_HOST}"


def get_api_key() -> str:
    try:
        return st.secrets["RAPIDAPI_KEY"].strip()
    except Exception:
        return ""


def api_get(endpoint: str, params: dict, api_key: str) -> tuple[int, dict]:
    headers = {
        "x-rapidapi-key": api_key,
        "x-rapidapi-host": API_HOST,
    }
    try:
        resp = requests.get(
            f"{BASE_URL}/{endpoint.lstrip('/')}",
            headers=headers,
            params=params,
            timeout=15,
        )
        try:
            data = resp.json()
        except Exception:
            data = {"raw_text": resp.text[:300]}
        return resp.status_code, data
    except Exception as exc:
        return 0, {"error": str(exc)}


def extract_matches_list(payload) -> list:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    response_obj = payload.get("response", payload)
    if isinstance(response_obj, list):
        return response_obj
    if isinstance(response_obj, dict):
        for key in ("matches", "events", "fixtures", "h2h", "headToHead", "items", "list"):
            val = response_obj.get(key)
            if isinstance(val, list):
                return val
        for val in response_obj.values():
            if isinstance(val, list) and val and isinstance(val[0], dict):
                return val
    return []


def parse_match_item(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None

    event_id = (
        item.get("id")
        or item.get("eventId")
        or item.get("eventid")
        or item.get("matchId")
        or item.get("fixtureId")
    )

    home_obj = item.get("home") or item.get("homeTeam") or item.get("teams", {}).get("home") or {}
    away_obj = item.get("away") or item.get("awayTeam") or item.get("teams", {}).get("away") or {}

    home_name = (
        home_obj.get("name")
        if isinstance(home_obj, dict)
        else item.get("homeName") or item.get("home_team") or str(home_obj or "")
    )
    away_name = (
        away_obj.get("name")
        if isinstance(away_obj, dict)
        else item.get("awayName") or item.get("away_team") or str(away_obj or "")
    )

    if not home_name or not away_name:
        return None

    league_obj = item.get("league") or item.get("tournament") or item.get("competition") or {}
    league_name = (
        league_obj.get("name")
        if isinstance(league_obj, dict)
        else item.get("leagueName") or str(league_obj or "Unknown League")
    )
    country_name = (
        league_obj.get("country")
        if isinstance(league_obj, dict)
        else item.get("country") or item.get("countryName") or ""
    )

    kickoff = (
        item.get("time")
        or item.get("startTime")
        or item.get("date")
        or item.get("utcDate")
        or ""
    )

    home_score = None
    away_score = None

    if isinstance(home_obj, dict) and home_obj.get("score") is not None:
        home_score = home_obj.get("score")
    if isinstance(away_obj, dict) and away_obj.get("score") is not None:
        away_score = away_obj.get("score")

    score_obj = item.get("score") or item.get("goals") or item.get("result")
    if (home_score is None or away_score is None) and isinstance(score_obj, dict):
        ft = score_obj.get("fulltime") or score_obj.get("ft") or score_obj
        if isinstance(ft, dict):
            home_score = ft.get("home", home_score)
            away_score = ft.get("away", away_score)
    elif (home_score is None or away_score is None) and isinstance(score_obj, str):
        m = re.search(r"(\d+)\s*[-:]\s*(\d+)", score_obj)
        if m:
            home_score, away_score = int(m.group(1)), int(m.group(2))

    outcome = None
    try:
        if home_score is not None and away_score is not None:
            hs = int(home_score)
            aws = int(away_score)
            home_score, away_score = hs, aws
            if hs > aws:
                outcome = "1"
            elif hs < aws:
                outcome = "2"
            else:
                outcome = "X"
    except (ValueError, TypeError):
        pass

    return {
        "eventid": str(event_id) if event_id is not None else "",
        "home": str(home_name).strip(),
        "away": str(away_name).strip(),
        "league": f"{country_name} - {league_name}".strip(" -"),
        "kickoff": str(kickoff),
        "home_score": home_score,
        "away_score": away_score,
        "outcome": outcome,
    }


def fetch_daily_fixtures(date_str: str, api_key: str) -> tuple[list[dict], str]:
    status, data = api_get("football-get-matches-by-date", {"date": date_str}, api_key)
    if status != 200:
        return [], f"HTTP {status}: {data}"
    raw_list = extract_matches_list(data)
    parsed = [m for item in raw_list if (m := parse_match_item(item)) is not None]
    return parsed, f"HTTP 200 ({len(parsed)} fixtures parsed)"


def fetch_h2h_by_eventid(eventid: str, api_key: str) -> tuple[list[dict], str]:
    if not eventid:
        return [], "Missing eventid"

    candidate_endpoints = [
        "football-get-head-to-head",
        "football-head-to-head",
        "football-get-h2h",
    ]
    last_note = ""
    for ep in candidate_endpoints:
        status, data = api_get(ep, {"eventid": str(eventid)}, api_key)
        if status == 200:
            raw_list = extract_matches_list(data)
            parsed = [
                m
                for item in raw_list
                if (m := parse_match_item(item)) is not None and m["outcome"] in ("1", "X", "2")
            ]
            if parsed:
                return parsed, f"HTTP 200 via {ep} ({len(parsed)} completed H2H matches)"
            last_note = f"HTTP 200 via {ep} (0 completed H2H matches returned)"
        else:
            last_note = f"HTTP {status} via {ep}"
    return [], last_note


def leading_draw_streak(outcomes: list[str]) -> int:
    streak = 0
    for res in outcomes:
        if res == "X":
            streak += 1
        else:
            break
    return streak


def parse_manual_sequence(raw_text: str) -> list[dict]:
    """
    Accepts most-recent-first tokens separated by commas, spaces, or newlines:
    - Scores like '1-1, 0:0, 2-1, 1-2, 0-1'
    - Codes like 'X, X, W, L, D' or 'D, D, 1, 2, 1'
    """
    if not raw_text.strip():
        return []
    tokens = [t.strip() for t in re.split(r"[,;\n\s]+", raw_text.strip()) if t.strip()]
    results = []
    for tok in tokens:
        score_match = re.match(r"^(\d+)\s*[-:]\s*(\d+)$", tok)
        if score_match:
            hg, ag = int(score_match.group(1)), int(score_match.group(2))
            out = "X" if hg == ag else ("1" if hg > ag else "2")
            results.append({"label": f"{hg}-{ag}", "outcome": out, "hg": hg, "ag": ag})
            continue

        upper = tok.upper()
        if upper in ("X", "D", "DRAW"):
            results.append({"label": "X", "outcome": "X", "hg": None, "ag": None})
        elif upper in ("1", "W", "WIN"):
            results.append({"label": upper, "outcome": "1", "hg": None, "ag": None})
        elif upper in ("2", "L", "LOSS"):
            results.append({"label": upper, "outcome": "2", "hg": None, "ag": None})
    return results[:5]


def poisson_12_probability(lambda_home: float, lambda_away: float, max_goals: int = 8) -> float:
    p_draw = 0.0
    for g in range(max_goals + 1):
        p_h = (math.exp(-lambda_home) * (lambda_home**g)) / math.factorial(g)
        p_a = (math.exp(-lambda_away) * (lambda_away**g)) / math.factorial(g)
        p_draw += p_h * p_a
    return max(0.0, min(1.0, 1.0 - p_draw))


def evaluate_12_model(
    h2h_outcomes: list[str],
    home_form: list[dict],
    away_form: list[dict],
    lambda_home: float = 1.45,
    lambda_away: float = 1.25,
) -> dict:
    h2h_streak = leading_draw_streak(h2h_outcomes)
    home_streak = leading_draw_streak([m["outcome"] for m in home_form])
    away_streak = leading_draw_streak([m["outcome"] for m in away_form])

    rule1_triggered = h2h_streak in (2, 3)
    rule2_triggered = (home_streak >= 2) or (away_streak >= 2)

    p_poisson_12 = poisson_12_probability(lambda_home, lambda_away)

    all_outcomes = h2h_outcomes + [m["outcome"] for m in home_form] + [m["outcome"] for m in away_form]
    if all_outcomes:
        non_draws = sum(1 for o in all_outcomes if o in ("1", "2"))
        p_hist_12 = non_draws / len(all_outcomes)
    else:
        p_hist_12 = p_poisson_12

    base_12 = 0.65 * p_poisson_12 + 0.35 * p_hist_12
    streak_boost = 0.0
    if rule1_triggered:
        streak_boost += 0.07
    if rule2_triggered:
        streak_boost += 0.06

    final_12_prob = min(0.96, base_12 + streak_boost)

    reasons = []
    if rule1_triggered:
        reasons.append(f"Rule 1 Triggered: {h2h_streak} consecutive H2H draws immediately prior")
    if home_streak >= 2:
        reasons.append(f"Rule 2 Triggered: Home on {home_streak} consecutive draws")
    if away_streak >= 2:
        reasons.append(f"Rule 2 Triggered: Away on {away_streak} consecutive draws")

    return {
        "triggered": rule1_triggered or rule2_triggered,
        "rule1": rule1_triggered,
        "rule2": rule2_triggered,
        "h2h_streak": h2h_streak,
        "home_streak": home_streak,
        "away_streak": away_streak,
        "p_poisson_12": p_poisson_12,
        "p_hist_12": p_hist_12,
        "final_12_prob": final_12_prob,
        "reasons": reasons,
    }


# --- UI ---
st.title("Double Chance (12) Scanner")
st.caption("Scans daily fixtures via `football-get-matches-by-date` + `eventid` H2H & quantitative 12 probability.")

api_key = get_api_key()
if not api_key:
    st.error("Missing `RAPIDAPI_KEY` in Streamlit Secrets.")
    st.stop()

with st.sidebar:
    st.header("Scanner Settings")
    today_utc = datetime.now(timezone.utc).strftime("%Y%m%d")
    scan_date = st.text_input("Fixture Date (YYYYMMDD)", value=today_utc)
    max_h2h_checks = st.slider("Max Fixtures to Check H2H", min_value=5, max_value=40, value=15, step=5)
    show_all = st.checkbox("Show Non-Triggered Matches Too", value=False)
    show_debug = st.checkbox("Show API Endpoint Status", value=True)

tab1, tab2 = st.tabs(["Live Fixture & H2H Scanner", "Instant Match Analyzer (Auto H2H + Form)"])

with tab1:
    league_filter = st.text_input(
        "Optional Country / League Filter (leave blank for ALL global leagues)",
        placeholder="e.g. England, Brazil, Germany, Serie B, Liga...",
    )

    if st.button("Scan Global Fixtures", type="primary", use_container_width=True):
        with st.spinner("Fetching fixtures..."):
            fixtures, fix_status = fetch_daily_fixtures(scan_date.strip(), api_key)

        if league_filter.strip():
            q = league_filter.strip().lower()
            fixtures = [
                f for f in fixtures
                if q in f["league"].lower() or q in f["home"].lower() or q in f["away"].lower()
            ]

        if not fixtures:
            st.warning(f"No fixtures matched. ({fix_status})")
        else:
            batch = fixtures[:max_h2h_checks]
            st.info(f"Found {len(fixtures)} fixtures ({fix_status}). Checking H2H by `eventid` for {len(batch)} matches...")

            progress = st.progress(0)
            results_rows = []
            debug_logs = []

            for idx, fx in enumerate(batch):
                h2h_matches, h2h_status = fetch_h2h_by_eventid(fx["eventid"], api_key)
                h2h_outcomes = [m["outcome"] for m in h2h_matches]
                eval_res = evaluate_12_model(h2h_outcomes, [], [])

                if show_debug:
                    debug_logs.append(
                        f"**{fx['home']} vs {fx['away']}** (`eventid={fx['eventid']}`): "
                        f"{h2h_status} | H2H seq: `{h2h_outcomes[:5]}`"
                    )

                if eval_res["triggered"] or show_all:
                    results_rows.append((fx, h2h_outcomes, eval_res))

                progress.progress((idx + 1) / len(batch))

            if not results_rows:
                st.warning(
                    "No fixtures in this batch triggered the 2-3 consecutive H2H draw rule. "
                    "Turn on 'Show Non-Triggered Matches Too' in the sidebar or analyze any match below in Tab 2."
                )
            else:
                results_rows.sort(key=lambda r: (r[2]["triggered"], r[2]["final_12_prob"]), reverse=True)
                for fx, h2h_outcomes, eval_res in results_rows:
                    badge = "TRIGGERED 12 SIGNAL" if eval_res["triggered"] else "WATCHLIST"
                    with st.container(border=True):
                        st.subheader(f"[{badge}] {fx['home']} vs {fx['away']}")
                        st.caption(f"{fx['league']} | Kickoff: {fx['kickoff']} | eventid: {fx['eventid']}")
                        c1, c2, c3 = st.columns(3)
                        c1.metric("12 Win Probability", f"{eval_res['final_12_prob']*100:.1f}%")
                        c2.metric("H2H Draw Streak", f"{eval_res['h2h_streak']}")
                        c3.metric("Recent H2H (Latest First)", " - ".join(h2h_outcomes[:5]) or "None")
                        for r in eval_res["reasons"]:
                            st.success(r)

            if show_debug and debug_logs:
                with st.expander("API Endpoint Debug Log", expanded=False):
                    for line in debug_logs:
                        st.markdown(f"- {line}")

with tab2:
    st.markdown("Enter an **Event ID** from any fixture to auto-fetch H2H, and paste each team's **Last 5 results** (most recent first).")

    col_id, col_btn = st.columns([2, 1])
    with col_id:
        manual_eventid = st.text_input("Fixture Event ID (`eventid`)", value="4621624")
    with col_btn:
        st.write("")
        st.write("")
        fetch_btn = st.button("Auto-Fetch H2H", use_container_width=True)

    if "fetched_h2h" not in st.session_state:
        st.session_state["fetched_h2h"] = []
        st.session_state["fetched_h2h_note"] = ""

    if fetch_btn:
        h2h_list, h2h_note = fetch_h2h_by_eventid(manual_eventid.strip(), api_key)
        st.session_state["fetched_h2h"] = [m["outcome"] for m in h2h_list]
        st.session_state["fetched_h2h_note"] = h2h_note

    if st.session_state["fetched_h2h_note"]:
        st.caption(f"H2H Status: {st.session_state['fetched_h2h_note']}")

    c_h, c_a = st.columns(2)
    with c_h:
        home_team = st.text_input("Home Team", value="Home Team")
        home_last5_raw = st.text_input(
            "Home Last 5 (most recent first — e.g. 1-1, 0-0, 2-1, 1-0, 0-2 or X, X, W, W, L)",
            value="1-1, 0-0, 2-1, 1-0, 1-2",
        )
        lam_h = st.number_input("Home Expected Goals (λ)", min_value=0.2, max_value=4.5, value=1.45, step=0.05)

    with c_a:
        away_team = st.text_input("Away Team", value="Away Team")
        away_last5_raw = st.text_input(
            "Away Last 5 (most recent first — e.g. 2-1, 1-1, 0-1, 3-1, 1-0 or W, X, L, W, W)",
            value="2-1, 1-1, 0-1, 2-0, 1-1",
        )
        lam_a = st.number_input("Away Expected Goals (λ)", min_value=0.2, max_value=4.5, value=1.25, step=0.05)

    h2h_override = st.text_input(
        "H2H Sequence (most recent first — auto-filled if fetched, or type e.g. X, X, 1, 2, X)",
        value=", ".join(st.session_state["fetched_h2h"][:5]),
    )

    if st.button("Calculate Double Chance (12) Signal", type="primary", use_container_width=True):
        home_seq = parse_manual_sequence(home_last5_raw)
        away_seq = parse_manual_sequence(away_last5_raw)
        h2h_seq = [m["outcome"] for m in parse_manual_sequence(h2h_override)]

        res = evaluate_12_model(h2h_seq, home_seq, away_seq, lam_h, lam_a)

        st.divider()
        st.subheader(f"{home_team} vs {away_team} — Double Chance (12) Analysis")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Final 12 Probability", f"{res['final_12_prob']*100:.1f}%")
        m2.metric("Poisson 12 Prob", f"{res['p_poisson_12']*100:.1f}%")
        m3.metric("H2H Draw Streak", res["h2h_streak"])
        m4.metric("Home / Away Streaks", f"{res['home_streak']} / {res['away_streak']}")

        if res["triggered"]:
            st.success("RECOMMENDATION: BACK DOUBLE CHANCE (12) — Draw-Breakout Signal Active")
            for reason in res["reasons"]:
                st.write(f"- {reason}")
        else:
            st.info("No 2+ consecutive draw streak detected in H2H or Team Form; rely on Poisson 12 probability above.")
