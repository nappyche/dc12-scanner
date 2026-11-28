import math
import re
from datetime import datetime, timezone

import requests
import streamlit as st

st.set_page_config(page_title="Double Chance (12) Scanner", layout="wide")

BASE_URL = "https://v3.football.api-sports.io"
FINISHED = "FT-AET-PEN"
FINISHED_SET = {"FT", "AET", "PEN"}
NOT_STARTED = {"NS", "TBD"}


class ApiError(Exception):
    pass


def get_api_key() -> str:
    try:
        return st.secrets["API_FOOTBALL_KEY"].strip()
    except Exception:
        return ""


def api_get(endpoint: str, params: dict, api_key: str):
    """Calls API-Football. Raises ApiError on any problem (so failures are never cached)."""
    try:
        resp = requests.get(
            f"{BASE_URL}/{endpoint}",
            headers={"x-apisports-key": api_key},
            params=params,
            timeout=20,
        )
    except Exception as exc:
        raise ApiError(f"Network error: {exc}")

    if resp.status_code != 200:
        raise ApiError(f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except Exception:
        raise ApiError("Response was not valid JSON")

    errors = data.get("errors")
    if errors:
        if isinstance(errors, dict):
            msg = "; ".join(f"{k}: {v}" for k, v in errors.items())
        else:
            msg = "; ".join(str(e) for e in errors)
        raise ApiError(msg)
    return data.get("response", [])


# ---- Cached API calls (repeat scans cost nothing) ----
@st.cache_data(ttl=6 * 3600, show_spinner=False)
def cached_fixtures_by_date(date_str: str, _api_key: str):
    return api_get("fixtures", {"date": date_str}, _api_key)


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def cached_h2h(home_id: int, away_id: int, _api_key: str):
    return api_get("fixtures/headtohead", {"h2h": f"{home_id}-{away_id}"}, _api_key)


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def cached_team_season(team_id: int, season: int, _api_key: str):
    return api_get(
        "fixtures",
        {"team": team_id, "season": season, "status": FINISHED},
        _api_key,
    )


@st.cache_data(ttl=3600, show_spinner=False)
def cached_fixture_by_id(fixture_id: str, _api_key: str):
    return api_get("fixtures", {"id": fixture_id}, _api_key)


# ---- Parsing ----
def parse_fixture(item: dict) -> dict | None:
    try:
        fx = item["fixture"]
        teams = item["teams"]
        league = item.get("league") or {}
        status = (fx.get("status") or {}).get("short", "")
        ft = (item.get("score") or {}).get("fulltime") or {}
        hg, ag = ft.get("home"), ft.get("away")

        outcome = None
        if status in FINISHED_SET and hg is not None and ag is not None:
            outcome = "1" if hg > ag else ("2" if hg < ag else "X")

        return {
            "id": fx.get("id"),
            "ts": fx.get("timestamp") or 0,
            "kickoff": str(fx.get("date", ""))[:16].replace("T", " "),
            "status": status,
            "home": teams["home"]["name"],
            "away": teams["away"]["name"],
            "home_id": teams["home"]["id"],
            "away_id": teams["away"]["id"],
            "league": f"{league.get('country', '')} - {league.get('name', '')}".strip(" -"),
            "season": league.get("season"),
            "hg": hg,
            "ag": ag,
            "outcome": outcome,
        }
    except (KeyError, TypeError):
        return None


def h2h_from(items: list) -> list[str]:
    rows = [m for m in (parse_fixture(i) for i in items) if m and m["outcome"]]
    rows.sort(key=lambda m: m["ts"], reverse=True)
    return [m["outcome"] for m in rows[:5]]


def form_from(items: list, team_id: int) -> list[dict]:
    rows = [m for m in (parse_fixture(i) for i in items) if m and m["outcome"]]
    rows.sort(key=lambda m: m["ts"], reverse=True)
    rows = rows[:5]
    for m in rows:
        if m["home_id"] == team_id:
            m["gf"], m["ga"] = m["hg"], m["ag"]
        else:
            m["gf"], m["ga"] = m["ag"], m["hg"]
    return rows


def estimate_lambdas(home_form: list[dict], away_form: list[dict]) -> tuple[float, float]:
    def avg(rows, key):
        return sum(r[key] for r in rows) / len(rows) if rows else None

    hf, ha = avg(home_form, "gf"), avg(home_form, "ga")
    af, aa = avg(away_form, "gf"), avg(away_form, "ga")
    lam_h = (hf + aa) / 2 if hf is not None and aa is not None else 1.45
    lam_a = (af + ha) / 2 if af is not None and ha is not None else 1.25
    return max(0.3, min(3.5, lam_h)), max(0.3, min(3.5, lam_a))


# ---- Model ----
def leading_draw_streak(outcomes: list[str]) -> int:
    streak = 0
    for res in outcomes:
        if res == "X":
            streak += 1
        else:
            break
    return streak


def parse_manual_sequence(raw_text: str) -> list[dict]:
    """Most recent first. Accepts scores (1-1, 2:0) or codes (X, D, W, L, 1, 2)."""
    if not raw_text.strip():
        return []
    tokens = [t.strip() for t in re.split(r"[,;\n\s]+", raw_text.strip()) if t.strip()]
    results = []
    for tok in tokens:
        m = re.match(r"^(\d+)\s*[-:]\s*(\d+)$", tok)
        if m:
            hg, ag = int(m.group(1)), int(m.group(2))
            out = "X" if hg == ag else ("1" if hg > ag else "2")
            results.append({"outcome": out})
            continue
        up = tok.upper()
        if up in ("X", "D", "DRAW"):
            results.append({"outcome": "X"})
        elif up in ("1", "W", "WIN"):
            results.append({"outcome": "1"})
        elif up in ("2", "L", "LOSS"):
            results.append({"outcome": "2"})
    return results[:5]


def poisson_12_probability(lambda_home: float, lambda_away: float, max_goals: int = 8) -> float:
    p_draw = 0.0
    for g in range(max_goals + 1):
        p_h = (math.exp(-lambda_home) * (lambda_home**g)) / math.factorial(g)
        p_a = (math.exp(-lambda_away) * (lambda_away**g)) / math.factorial(g)
        p_draw += p_h * p_a
    return max(0.0, min(1.0, 1.0 - p_draw))


def evaluate_12_model(h2h_outcomes, home_form, away_form, lambda_home=1.45, lambda_away=1.25) -> dict:
    h2h_streak = leading_draw_streak(h2h_outcomes)
    home_streak = leading_draw_streak([m["outcome"] for m in home_form])
    away_streak = leading_draw_streak([m["outcome"] for m in away_form])

    rule1 = h2h_streak in (2, 3)
    rule2 = home_streak >= 2 or away_streak >= 2

    p_poisson = poisson_12_probability(lambda_home, lambda_away)

    all_out = list(h2h_outcomes) + [m["outcome"] for m in home_form] + [m["outcome"] for m in away_form]
    p_hist = (sum(1 for o in all_out if o in ("1", "2")) / len(all_out)) if all_out else p_poisson

    base = 0.65 * p_poisson + 0.35 * p_hist
    boost = (0.07 if rule1 else 0.0) + (0.06 if rule2 else 0.0)
    final = min(0.96, base + boost)

    reasons = []
    if rule1:
        reasons.append(f"Rule 1: {h2h_streak} consecutive H2H draws immediately prior")
    if home_streak >= 2:
        reasons.append(f"Rule 2: Home on {home_streak} consecutive draws")
    if away_streak >= 2:
        reasons.append(f"Rule 2: Away on {away_streak} consecutive draws")

    return {
        "triggered": rule1 or rule2,
        "h2h_streak": h2h_streak,
        "home_streak": home_streak,
        "away_streak": away_streak,
        "p_poisson_12": p_poisson,
        "p_hist_12": p_hist,
        "final_12_prob": final,
        "lam_h": lambda_home,
        "lam_a": lambda_away,
        "reasons": reasons,
    }


def analyze_fixture(fx: dict, api_key: str, use_form: bool):
    errs = []
    h2h = []
    home_form, away_form = [], []

    try:
        h2h = h2h_from(cached_h2h(fx["home_id"], fx["away_id"], api_key))
    except ApiError as exc:
        errs.append(f"H2H: {exc}")

    if use_form and fx.get("season"):
        try:
            home_form = form_from(cached_team_season(fx["home_id"], fx["season"], api_key), fx["home_id"])
        except ApiError as exc:
            errs.append(f"Home form: {exc}")
        try:
            away_form = form_from(cached_team_season(fx["away_id"], fx["season"], api_key), fx["away_id"])
        except ApiError as exc:
            errs.append(f"Away form: {exc}")

    lam_h, lam_a = estimate_lambdas(home_form, away_form)
    res = evaluate_12_model(h2h, home_form, away_form, lam_h, lam_a)
    return res, h2h, home_form, away_form, errs


def hit_limit(errs: list[str]) -> bool:
    text = " ".join(errs).lower()
    return "limit" in text or "429" in text


# ---- UI ----
st.title("Double Chance (12) Scanner")
st.caption("Fixtures, head-to-head and team form from API-Football, with a 12 probability model.")

api_key = get_api_key()
with st.sidebar:
    st.header("Scanner Settings")
    if not api_key:
        api_key = st.text_input("API-Football key", type="password")
        if not api_key:
            st.warning("Add API_FOOTBALL_KEY in Streamlit Secrets, or paste your key above.")
    scan_date = st.date_input("Fixture date", value=datetime.now(timezone.utc).date())
    max_checks = st.slider("Max fixtures to analyze", min_value=5, max_value=25, value=10, step=5)
    use_form = st.checkbox("Include team form (2 extra calls per match)", value=True)
    show_all = st.checkbox("Show non-triggered matches too", value=False)
    calls = 1 + max_checks * (3 if use_form else 1)
    st.caption(f"A full scan uses up to ~{calls} calls (free plan: 100 per day). Repeat scans are cached.")

    if st.button("Check API status", use_container_width=True):
        if not api_key:
            st.error("No key found.")
        else:
            try:
                info = api_get("status", {}, api_key)
                if isinstance(info, dict):
                    req = info.get("requests", {})
                    sub = info.get("subscription", {})
                    st.success(f"Plan: {sub.get('plan', '?')}")
                    st.write(f"Requests today: {req.get('current', '?')} / {req.get('limit_day', '?')}")
            except ApiError as exc:
                st.error(f"Status check failed: {exc}")

if not api_key:
    st.stop()

tab1, tab2 = st.tabs(["Live Fixture & H2H Scanner", "Instant Match Analyzer"])

with tab1:
    league_filter = st.text_input(
        "Optional Country / League Filter (leave blank for ALL)",
        placeholder="e.g. England, Brazil, Germany, Serie B...",
    )

    if st.button("Scan Global Fixtures", type="primary", use_container_width=True):
        date_str = scan_date.strftime("%Y-%m-%d")
        try:
            raw = cached_fixtures_by_date(date_str, api_key)
        except ApiError as exc:
            st.error(f"Could not load fixtures: {exc}")
            st.stop()

        fixtures = [m for m in (parse_fixture(i) for i in raw) if m and m["status"] in NOT_STARTED]
        if league_filter.strip():
            q = league_filter.strip().lower()
            fixtures = [
                f for f in fixtures
                if q in f["league"].lower() or q in f["home"].lower() or q in f["away"].lower()
            ]

        if not fixtures:
            st.warning("No upcoming fixtures found for that date and filter.")
        else:
            batch = fixtures[:max_checks]
            st.info(f"Found {len(fixtures)} upcoming fixtures. Analyzing {len(batch)}...")
            progress = st.progress(0)
            rows = []
            problems = []

            for idx, fx in enumerate(batch):
                res, h2h, hform, aform, errs = analyze_fixture(fx, api_key, use_form)
                if errs:
                    problems.append(f"{fx['home']} vs {fx['away']}: " + " | ".join(errs))
                if hit_limit(errs):
                    st.error("Daily request limit reached. Showing results found so far.")
                    break
                if res["triggered"] or show_all:
                    rows.append((fx, h2h, res))
                progress.progress((idx + 1) / len(batch))

            if not rows:
                st.warning("No fixtures triggered the rules. Tick 'Show non-triggered matches too' to see all.")
            else:
                rows.sort(key=lambda r: (r[2]["triggered"], r[2]["final_12_prob"]), reverse=True)
                for fx, h2h, res in rows:
                    badge = "TRIGGERED" if res["triggered"] else "WATCHLIST"
                    with st.container(border=True):
                        st.subheader(f"[{badge}] {fx['home']} vs {fx['away']}")
                        st.caption(f"{fx['league']} | {fx['kickoff']} UTC | fixture id: {fx['id']}")
                        c1, c2, c3 = st.columns(3)
                        c1.metric("12 Probability", f"{res['final_12_prob'] * 100:.1f}%")
                        c2.metric("H2H Draw Streak", res["h2h_streak"])
                        c3.metric("Recent H2H", " - ".join(h2h) or "None")
                        for r in res["reasons"]:
                            st.success(r)

            if problems:
                with st.expander("API problems (tap to view)"):
                    for p in problems:
                        st.write("- " + p)

with tab2:
    st.markdown("Enter a **fixture id** (shown on the scanner cards) to auto-fetch H2H and team form.")
    fid = st.text_input("Fixture id")
    if st.button("Analyze fixture", type="primary", use_container_width=True):
        if not fid.strip():
            st.warning("Enter a fixture id first.")
        else:
            try:
                raw = cached_fixture_by_id(fid.strip(), api_key)
                fx = parse_fixture(raw[0]) if raw else None
            except ApiError as exc:
                fx = None
                st.error(f"Could not load fixture: {exc}")
            if fx:
                res, h2h, hform, aform, errs = analyze_fixture(fx, api_key, True)
                st.subheader(f"{fx['home']} vs {fx['away']}")
                st.caption(f"{fx['league']} | {fx['kickoff']} UTC")
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("12 Probability", f"{res['final_12_prob'] * 100:.1f}%")
                m2.metric("Poisson 12", f"{res['p_poisson_12'] * 100:.1f}%")
                m3.metric("H2H Draw Streak", res["h2h_streak"])
                m4.metric("Home / Away Streaks", f"{res['home_streak']} / {res['away_streak']}")
                st.write(f"H2H (latest first): {' - '.join(h2h) or 'None'}")
                st.write(f"Home form: {' - '.join(m['outcome'] for m in hform) or 'None'}")
                st.write(f"Away form: {' - '.join(m['outcome'] for m in aform) or 'None'}")
                st.write(f"Expected goals used: {res['lam_h']:.2f} (home), {res['lam_a']:.2f} (away)")
                if res["triggered"]:
                    st.success("Draw-breakout signal active")
                    for r in res["reasons"]:
                        st.write(f"- {r}")
                else:
                    st.info("No 2+ draw streak found; rely on the probability above.")
                for e in errs:
                    st.warning(e)
            elif fid.strip():
                st.warning("No fixture found for that id.")

    with st.expander("Or enter results manually"):
        h2h_raw = st.text_input("H2H (latest first, e.g. X, X, 1, 2, X)")
        home_raw = st.text_input("Home last 5 (e.g. 1-1, 0-0, 2-1, 1-0, 1-2)")
        away_raw = st.text_input("Away last 5 (e.g. 2-1, 1-1, 0-1, 2-0, 1-1)")
        lam_h = st.number_input("Home expected goals", 0.2, 4.5, 1.45, 0.05)
        lam_a = st.number_input("Away expected goals", 0.2, 4.5, 1.25, 0.05)
        if st.button("Calculate manually", use_container_width=True):
            res = evaluate_12_model(
                [m["outcome"] for m in parse_manual_sequence(h2h_raw)],
                parse_manual_sequence(home_raw),
                parse_manual_sequence(away_raw),
                lam_h,
                lam_a,
            )
            st.metric("12 Probability", f"{res['final_12_prob'] * 100:.1f}%")
            for r in res["reasons"]:
                st.write(f"- {r}")
