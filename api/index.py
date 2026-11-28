from datetime import datetime, timedelta, timezone
from functools import lru_cache
from http.server import BaseHTTPRequestHandler
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
SUPABASE_ANON_KEY = (os.environ.get("SUPABASE_ANON_KEY") or "").strip()
SUPABASE_SERVICE_ROLE_KEY = (
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    or os.environ.get("SUPABASE_SERVICE_KEY")
    or ""
).strip()
API_FOOTBALL_KEY = (os.environ.get("API_FOOTBALL_KEY") or "").strip()
API_FOOTBALL_BASE = (os.environ.get("API_FOOTBALL_BASE") or "https://v3.football.api-sports.io").strip().rstrip("/")
CRON_SECRET = (os.environ.get("CRON_SECRET") or "").strip()

LAGOS_TZ = timezone(timedelta(hours=1))
LIVE_COOLDOWN_SECONDS = 30 * 60

MAX_FIXTURES_FETCH = 40
MAX_STANDINGS_LEAGUES = 12
TOP_TEAMS_PER_LEAGUE = 5

PRIORITY_LEAGUE_IDS = [
    39, 140, 135, 78, 61,       # Top 5 European leagues
    2, 3, 848,                  # UEFA UCL, UEL, UECL
    88, 94, 144, 203, 179,      # Strong European goal leagues
    98, 99, 292, 293, 307, 188, # Asia / Australia / Middle East
    253, 262, 71, 128, 13, 11   # Americas & CONMEBOL
]

PRIORITY_LEAGUE_SET = set(PRIORITY_LEAGUE_IDS)

HIGH_SCORING_LEAGUE_IDS = {
    39, 140, 135, 78, 61, 2, 3, 848, 88, 94, 144, 203, 179,
    98, 99, 292, 293, 307, 188, 253, 262, 71, 128, 13, 11,
    40, 41, 141, 136, 79, 62, 89, 95, 218, 197, 207, 103, 113, 119
}

MARKETS_12 = [
    "Over 1.5",
    "Over 2.5",
    "BTTS",
    "Home Over 1.5",
    "Away Over 1.5",
    "1X + BTTS",
    "X2 + BTTS",
    "Home Win + Over 1.5",
    "Away Win + Over 1.5",
    "First-Half Over 0.5",
    "Home Score Over 0.5",
    "Away Score Over 0.5",
]

ROLLOVER_250_MARKETS = [
    "Over 1.5",
    "Home Score Over 0.5",
    "Away Score Over 0.5",
    "First-Half Over 0.5",
    "BTTS",
    "Over 2.5",
]

MIX_GOAL_MARKETS = [
    "Over 1.5",
    "Over 2.5",
    "BTTS",
    "Home Over 1.5",
    "Away Over 1.5",
    "1X + BTTS",
    "X2 + BTTS",
    "Home Win + Over 1.5",
    "Away Win + Over 1.5",
    "First-Half Over 0.5",
    "Home Score Over 0.5",
    "Away Score Over 0.5",
]

DEFAULT_BASE_ODDS = {
    "Over 1.5": 1.24,
    "Over 2.5": 1.72,
    "BTTS": 1.68,
    "Home Over 1.5": 1.55,
    "Away Over 1.5": 1.72,
    "1X + BTTS": 1.85,
    "X2 + BTTS": 1.95,
    "Home Win + Over 1.5": 1.78,
    "Away Win + Over 1.5": 2.10,
    "First-Half Over 0.5": 1.32,
    "Home Score Over 0.5": 1.18,
    "Away Score Over 0.5": 1.28,
}


def _today_wat():
    return datetime.now(LAGOS_TZ).date()


def _date_str(offset_days=0):
    return (_today_wat() + timedelta(days=offset_days)).isoformat()


def _parse_iso_dt(raw):
    if not raw:
        return None
    s = str(raw).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(LAGOS_TZ)
    except Exception:
        return None


def _kickoff_epoch(match):
    dt = _parse_iso_dt(match.get("kickoff") or match.get("date"))
    return dt.timestamp() if dt else 0.0


def _kickoff_hour_wat(match):
    dt = _parse_iso_dt(match.get("kickoff") or match.get("date"))
    return dt.hour if dt else 12


def _normalise_status(raw):
    val = str(raw or "").strip().upper()
    if val in {"LIVE", "1H", "2H", "HT", "ET", "P", "INT", "IN_PLAY"}:
        return "Live"
    if val in {"FINISHED", "FT", "AET", "PEN", "WON", "LOST", "LOSS"}:
        return "Finished"
    return "Upcoming"


def _status_rank(match):
    st = _normalise_status(match.get("status") or "")
    res = str(match.get("result") or "").upper()
    if st == "Upcoming":
        if any(tok in res for tok in ("1H", "2H", "HT", "ET", "LIVE", "IN_PLAY")):
            st = "Live"
        elif any(tok in res for tok in ("FT", "AET", "PEN", "FINISHED")):
            st = "Finished"
        else:
            ko = _kickoff_epoch(match)
            if ko > 0:
                diff = time.time() - ko
                if diff >= 135 * 60:
                    st = "Finished"
    return {"Live": 0, "Upcoming": 1, "Finished": 2}.get(st, 1)


def sort_status(items):
    return sorted(items, key=lambda x: (_status_rank(x), _kickoff_epoch(x)))


@lru_cache(maxsize=1)
def _load_html():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base_dir, "index.html"),
        os.path.join(os.path.dirname(base_dir), "index.html"),
        "/var/task/index.html",
        "index.html",
    ]
    for path in candidates:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
    return "<!DOCTYPE html><html><body><h1>GOALZONE247</h1><p>index.html not found.</p></body></html>"


def _sb_headers(service=False, prefer=None):
    key = SUPABASE_SERVICE_ROLE_KEY if (service and SUPABASE_SERVICE_ROLE_KEY) else SUPABASE_ANON_KEY
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _sb_request(method, table, query="", body=None, service=False, prefer=None):
    if not SUPABASE_URL:
        return {"error": "SUPABASE_URL not set"}
    key = SUPABASE_SERVICE_ROLE_KEY if (service and SUPABASE_SERVICE_ROLE_KEY) else SUPABASE_ANON_KEY
    if not key:
        return {"error": "Supabase key not set"}

    url = f"{SUPABASE_URL}/rest/v1/{table}"
    if query:
        url = f"{url}?{query}"

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=_sb_headers(service=service, prefer=prefer), method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return []
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:300]
        except Exception:
            pass
        return {"error": f"Supabase HTTP {exc.code}: {detail}"}
    except Exception as exc:
        return {"error": f"Supabase request error: {exc}"}


def sb_get(table, query="", service=False):
    return _sb_request("GET", table, query=query, service=service)


def sb_post(table, rows, service=True, prefer="return=representation"):
    return _sb_request("POST", table, body=rows, service=service, prefer=prefer)


def sb_patch(table, query, patch_obj, service=True):
    return _sb_request("PATCH", table, query=query, body=patch_obj, service=service, prefer="return=minimal")


def _has_error(payload):
    return isinstance(payload, dict) and "error" in payload


def af_get(path):
    if not API_FOOTBALL_KEY:
        return {"error": "API_FOOTBALL_KEY not set"}
    url = f"{API_FOOTBALL_BASE}{path}"
    req = urllib.request.Request(
        url,
        headers={
            "x-apisports-key": API_FOOTBALL_KEY,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:250]
        except Exception:
            pass
        return {"error": f"API-Football HTTP {exc.code}: {detail}"}
    except Exception as exc:
        return {"error": f"API-Football error: {exc}"}


def _league_performance_score(league_id, country="", league_name=""):
    score = 0
    if league_id in PRIORITY_LEAGUE_IDS:
        idx = PRIORITY_LEAGUE_IDS.index(league_id)
        score += max(20, 120 - idx * 4)
    if league_id in HIGH_SCORING_LEAGUE_IDS:
        score += 35
    cname = str(country or "").lower()
    lname = str(league_name or "").lower()
    if any(k in lname for k in ("u19", "u21", "u23", "reserves", "ii", "b ", "women", "youth")):
        score -= 25
    if any(k in cname for k in ("england", "spain", "germany", "italy", "france", "netherlands", "portugal")):
        score += 10
    return score


def _poisson_prob_at_least(lam, k_min):
    if lam <= 0:
        return 0.0
    cdf = 0.0
    for k in range(0, k_min):
        cdf += math.exp(-lam) * (lam ** k) / math.factorial(k)
    return max(0.01, min(0.99, 1.0 - cdf))


def _prob_to_odd(prob, margin=0.90):
    if prob <= 0.03:
        return 6.50
    raw = (margin / prob)
    return round(max(1.08, min(7.50, raw)), 2)


def _compute_market_odds(match):
    league_id = match.get("league_id")
    is_high = league_id in HIGH_SCORING_LEAGUE_IDS
    home_lam = 1.68 if is_high else 1.42
    away_lam = 1.38 if is_high else 1.18
    total_lam = home_lam + away_lam

    p_o15 = _poisson_prob_at_least(total_lam, 2)
    p_o25 = _poisson_prob_at_least(total_lam, 3)
    p_hs05 = _poisson_prob_at_least(home_lam, 1)
    p_as05 = _poisson_prob_at_least(away_lam, 1)
    p_ho15 = _poisson_prob_at_least(home_lam, 2)
    p_ao15 = _poisson_prob_at_least(away_lam, 2)
    p_btts = max(0.15, min(0.88, p_hs05 * p_as05 * 1.04))
    p_fh05 = max(0.35, min(0.90, 1.0 - math.exp(-total_lam * 0.42)))
    p_hw_o15 = max(0.12, min(0.75, p_o15 * 0.56))
    p_aw_o15 = max(0.10, min(0.70, p_o15 * 0.44))
    p_1x_btts = max(0.12, min(0.72, p_btts * 0.72))
    p_x2_btts = max(0.12, min(0.70, p_btts * 0.65))

    return {
        "Over 1.5": _prob_to_odd(p_o15, 0.92),
        "Over 2.5": _prob_to_odd(p_o25, 0.90),
        "BTTS": _prob_to_odd(p_btts, 0.90),
        "Home Over 1.5": _prob_to_odd(p_ho15, 0.89),
        "Away Over 1.5": _prob_to_odd(p_ao15, 0.89),
        "1X + BTTS": _prob_to_odd(p_1x_btts, 0.87),
        "X2 + BTTS": _prob_to_odd(p_x2_btts, 0.87),
        "Home Win + Over 1.5": _prob_to_odd(p_hw_o15, 0.88),
        "Away Win + Over 1.5": _prob_to_odd(p_aw_o15, 0.88),
        "First-Half Over 0.5": _prob_to_odd(p_fh05, 0.91),
        "Home Score Over 0.5": _prob_to_odd(p_hs05, 0.93),
        "Away Score Over 0.5": _prob_to_odd(p_as05, 0.92),
    }


def _fetch_fixtures_for_date(date_str):
    data = af_get(f"/fixtures?date={urllib.parse.quote(date_str)}")
    if _has_error(data):
        return [], data["error"]

    raw_items = data.get("response", []) if isinstance(data, dict) else []
    scored = []
    for item in raw_items:
        fixture = item.get("fixture") or {}
        league = item.get("league") or {}
        teams = item.get("teams") or {}
        goals = item.get("goals") or {}
        status_obj = fixture.get("status") or {}

        lid = league.get("id")
        cname = league.get("country") or ""
        lname = league.get("name") or "League"
        perf = _league_performance_score(lid, cname, lname)

        home_name = (teams.get("home") or {}).get("name") or "Home"
        away_name = (teams.get("away") or {}).get("name") or "Away"
        hg = goals.get("home")
        ag = goals.get("away")
        short_st = status_obj.get("short") or "NS"
        elapsed = status_obj.get("elapsed")

        norm_st = _normalise_status(short_st)
        score_str = f"{hg} - {ag}" if (hg is not None and ag is not None) else ""
        if norm_st == "Live" and elapsed is not None and score_str:
            res_text = f"{score_str} ({short_st} {elapsed}')"
        elif norm_st == "Finished" and score_str:
            res_text = f"{score_str} ({short_st})"
        else:
            res_text = score_str

        rec = {
            "fixture_id": fixture.get("id"),
            "date": date_str,
            "kickoff": fixture.get("date") or "",
            "league_id": lid,
            "season": league.get("season"),
            "league": lname,
            "country": cname,
            "home": home_name,
            "away": away_name,
            "home_goals": hg,
            "away_goals": ag,
            "score": score_str,
            "result": res_text,
            "status": norm_st,
            "status_short": short_st,
            "elapsed": elapsed,
            "_perf": perf,
        }
        rec["market_odds"] = _compute_market_odds(rec)
        scored.append(rec)

    scored.sort(key=lambda r: (-r["_perf"], _kickoff_epoch(r)))
    selected = scored[:MAX_FIXTURES_FETCH]
    for item in selected:
        item.pop("_perf", None)
    return sort_status(selected), None


def _fetch_standings_for_fixtures(fixtures, date_str):
    seen_leagues = {}
    for fx in fixtures:
        lid = fx.get("league_id")
        season = fx.get("season")
        lname = fx.get("league") or ""
        cname = fx.get("country") or ""
        if lid and season and lid not in seen_leagues:
            seen_leagues[lid] = {
                "league_id": int(lid),
                "season": int(season),
                "league": lname,
                "country": cname,
                "perf": _league_performance_score(lid, cname, lname),
            }

    ranked_leagues = sorted(seen_leagues.values(), key=lambda x: -x["perf"])[:MAX_STANDINGS_LEAGUES]
    grouped = {}
    flat_rows = []

    for lg in ranked_leagues:
        data = af_get(f"/standings?league={lg['league_id']}&season={lg['season']}")
        if _has_error(data):
            continue
        resp_list = data.get("response", []) if isinstance(data, dict) else []
        if not resp_list:
            continue
        lg_block = (resp_list[0] or {}).get("league") or {}
        standings_groups = lg_block.get("standings") or []
        if not standings_groups or not isinstance(standings_groups[0], list):
            continue

        league_title = lg_block.get("name") or lg["league"]
        top_rows = []
        for entry in standings_groups[0][:TOP_TEAMS_PER_LEAGUE]:
            team_obj = entry.get("team") or {}
            all_stats = entry.get("all") or {}
            row = {
                "date": date_str,
                "league": league_title,
                "league_id": lg["league_id"],
                "position": entry.get("rank"),
                "team": team_obj.get("name") or "Team",
                "played": all_stats.get("played"),
                "points": entry.get("points"),
            }
            top_rows.append(row)
            flat_rows.append(row)

        if top_rows:
            grouped[league_title] = top_rows

    return grouped, flat_rows


def _rank_matches_by_quality(matches):
    return sorted(
        matches,
        key=lambda m: (
            -_league_performance_score(m.get("league_id"), m.get("country"), m.get("league")),
            _kickoff_epoch(m),
        ),
    )


def _candidate_for_match(match, market):
    odds_map = match.get("market_odds") or {}
    odd_val = odds_map.get(market)
    if odd_val is None:
        odd_val = DEFAULT_BASE_ODDS.get(market, 1.50)
    return {
        "fixture_id": match.get("fixture_id"),
        "teams": f"{match.get('home', 'Home')} vs {match.get('away', 'Away')}",
        "home": match.get("home", "Home"),
        "away": match.get("away", "Away"),
        "country": match.get("country", ""),
        "league": match.get("league", "Football"),
        "kickoff": match.get("kickoff", ""),
        "market": market,
        "odds": round(float(odd_val), 2),
        "status": match.get("status", "Upcoming"),
        "score": match.get("score", ""),
        "home_goals": match.get("home_goals"),
        "away_goals": match.get("away_goals"),
    }


def _is_early_morning_wat(match):
    h = _kickoff_hour_wat(match)
    return 0 <= h < 7


def _is_morning_wat(match):
    h = _kickoff_hour_wat(match)
    return 7 <= h < 12


def _is_midnight_wat(match):
    h = _kickoff_hour_wat(match)
    return h >= 22 or h < 4


def _is_rollover_morning_wat(match):
    return _kickoff_hour_wat(match) < 14


def _is_rollover_evening_wat(match):
    return _kickoff_hour_wat(match) >= 14


def _collect_window_candidates(matches, markets, window_fn, strict_window=False):
    """Collect candidates from the requested WAT window; spill outside only when not strict_window."""
    ranked = sort_status(_rank_matches_by_quality(matches))
    in_window = [m for m in ranked if window_fn(m)]
    ordered = in_window if strict_window else (in_window + [m for m in ranked if not window_fn(m)])

    per_market = []
    for mkt in markets:
        bucket = []
        for match in ordered:
            cand = _candidate_for_match(match, mkt)
            if cand:
                bucket.append(cand)
        if bucket:
            per_market.append(bucket)

    mixed = []
    max_len = max((len(b) for b in per_market), default=0)
    for idx in range(max_len):
        for bucket in per_market:
            if idx < len(bucket):
                mixed.append(bucket[idx])
    return sort_status(mixed)


def build_card(title, pool, used, min_legs, max_legs, min_total_odds, target_label):
    if not pool:
        return {
            "market": title,
            "legs": [],
            "total_odds": 0.0,
            "target": target_label,
            "result": "PENDING",
        }

    unused_pool = [c for c in pool if c.get("fixture_id") not in used]
    fallback_pool = [c for c in pool if c.get("fixture_id") in used]
    ordered = unused_pool + fallback_pool

    chosen = []
    prod = 1.0
    for cand in ordered:
        if len(chosen) >= max_legs:
            break
        chosen.append(cand)
        prod *= float(cand.get("odds") or 1.0)
        if len(chosen) >= min_legs and prod >= min_total_odds:
            break

    for leg in chosen:
        if leg.get("fixture_id") is not None:
            used.add(leg["fixture_id"])

    sorted_legs = sort_status(chosen)
    return {
        "market": title,
        "legs": sorted_legs,
        "total_odds": round(prod, 2) if sorted_legs else 0.0,
        "target": target_label,
        "result": "PENDING",
    }


def build_extra_cards(matches, used):
    extra_cards = []

    morning_250_pool = _collect_window_candidates(
        matches, ROLLOVER_250_MARKETS, _is_rollover_morning_wat, strict_window=True
    )
    evening_250_pool = _collect_window_candidates(
        matches, ROLLOVER_250_MARKETS, _is_rollover_evening_wat, strict_window=True
    )

    extra_cards.append(build_card("2.50 Rollover - Morning Card 1", morning_250_pool, used, 2, 4, 2.50, "2.50+"))
    extra_cards.append(build_card("2.50 Rollover - Morning Card 2", morning_250_pool, used, 2, 4, 2.50, "2.50+"))
    extra_cards.append(build_card("2.50 Rollover - Evening Card 1", evening_250_pool, used, 2, 4, 2.50, "2.50+"))
    extra_cards.append(build_card("2.50 Rollover - Evening Card 2", evening_250_pool, used, 2, 4, 2.50, "2.50+"))

    early_morning_pool = _collect_window_candidates(
        matches, MIX_GOAL_MARKETS, _is_early_morning_wat, strict_window=True
    )
    morning_pool = _collect_window_candidates(
        matches, MIX_GOAL_MARKETS, _is_morning_wat, strict_window=True
    )
    midnight_pool = _collect_window_candidates(
        matches, MIX_GOAL_MARKETS, _is_midnight_wat, strict_window=True
    )
    mega_mix_pool = _collect_window_candidates(
        matches, MIX_GOAL_MARKETS, lambda m: True, strict_window=False
    )

    extra_cards.append(build_card("Early Morning Accumulator", early_morning_pool, used, 5, 7, 12.0, "12.00+"))
    extra_cards.append(build_card("Morning Accumulator", morning_pool, used, 5, 7, 12.0, "12.00+"))
    extra_cards.append(build_card("Midnight Accumulator", midnight_pool, used, 5, 7, 12.0, "12.00+"))
    extra_cards.append(build_card("Mega Mix Accumulator (12.00+)", mega_mix_pool, used, 5, 8, 12.0, "12.00+"))
    extra_cards.append(build_card("Mega Mix Accumulator (30.00+)", mega_mix_pool, used, 6, 10, 30.0, "30.00+"))

    return extra_cards


def build_all_cards(matches):
    ranked = sort_status(_rank_matches_by_quality(matches))
    used = set()
    cards = []

    for mkt in MARKETS_12:
        pool = []
        for match in ranked:
            cand = _candidate_for_match(match, mkt)
            if cand:
                pool.append(cand)
        cards.append(build_card(mkt, pool, used, 5, 8, 12.0, "12.00+"))

    cards.extend(build_extra_cards(matches, used))
    return cards


def _persist_date_snapshot(date_str, matches, cards, standings_flat):
    safe_date = urllib.parse.quote(date_str, safe="")
    _sb_request("DELETE", "saved_markets", f"date=eq.{safe_date}", service=True, prefer="return=minimal")
    _sb_request("DELETE", "saved_cards", f"date=eq.{safe_date}", service=True, prefer="return=minimal")
    _sb_request("DELETE", "saved_standings", f"date=eq.{safe_date}", service=True, prefer="return=minimal")

    market_rows = []
    for m in matches:
        odds_map = m.get("market_odds") or {}
        market_rows.append({
            "date": date_str,
            "market": "fixture",
            "home": m.get("home"),
            "away": m.get("away"),
            "league": m.get("league"),
            "kickoff": m.get("kickoff"),
            "odds": odds_map.get("Over 1.5"),
            "result": m.get("result") or m.get("status") or "",
        })
        for mkt_name, odd_val in odds_map.items():
            market_rows.append({
                "date": date_str,
                "market": mkt_name,
                "home": m.get("home"),
                "away": m.get("away"),
                "league": m.get("league"),
                "kickoff": m.get("kickoff"),
                "odds": odd_val,
                "result": m.get("result") or m.get("status") or "",
            })

    card_rows = []
    for c in cards:
        card_rows.append({
            "date": date_str,
            "market": c.get("market"),
            "legs": json.dumps(c.get("legs") or [], separators=(",", ":")),
            "total_odds": c.get("total_odds"),
            "target": c.get("target", "12.00+"),
            "result": c.get("result", "PENDING"),
        })

    if market_rows:
        sb_post("saved_markets", market_rows, service=True, prefer="return=minimal")
    if card_rows:
        sb_post("saved_cards", card_rows, service=True, prefer="return=minimal")
    if standings_flat:
        sb_post("saved_standings", standings_flat, service=True, prefer="return=minimal")


def _load_saved_date_data(date_str):
    safe_date = urllib.parse.quote(date_str, safe="")
    market_rows = sb_get("saved_markets", f"date=eq.{safe_date}&order=id.asc", service=False)
    card_rows = sb_get("saved_cards", f"date=eq.{safe_date}&order=id.asc", service=False)
    standing_rows = sb_get("saved_standings", f"date=eq.{safe_date}&order=position.asc", service=False)

    if _has_error(market_rows) and _has_error(card_rows):
        return None, None, None, market_rows.get("error") or card_rows.get("error")

    matches_map = {}
    if isinstance(market_rows, list):
        for r in market_rows:
            if not isinstance(r, dict):
                continue
            mkt = r.get("market") or ""
            if mkt == "live_cache":
                continue
            key = f"{r.get('kickoff') or ''}|{r.get('home') or ''}|{r.get('away') or ''}"
            if key not in matches_map:
                matches_map[key] = {
                    "date": r.get("date") or date_str,
                    "kickoff": r.get("kickoff") or "",
                    "league": r.get("league") or "Football",
                    "country": "",
                    "home": r.get("home") or "Home",
                    "away": r.get("away") or "Away",
                    "result": r.get("result") or "",
                    "status": _normalise_status(r.get("result") or ""),
                    "market_odds": {},
                }
            if mkt == "fixture":
                matches_map[key]["result"] = r.get("result") or matches_map[key]["result"]
                matches_map[key]["status"] = _normalise_status(r.get("result") or "")
            elif mkt in MARKETS_12 and r.get("odds") is not None:
                matches_map[key]["market_odds"][mkt] = r.get("odds")

    matches = sort_status(list(matches_map.values()))

    cards = []
    if isinstance(card_rows, list):
        for c in card_rows:
            if not isinstance(c, dict):
                continue
            raw_legs = c.get("legs")
            if isinstance(raw_legs, str):
                try:
                    raw_legs = json.loads(raw_legs)
                except Exception:
                    raw_legs = []
            if not isinstance(raw_legs, list):
                raw_legs = []
            cards.append({
                "market": c.get("market") or "",
                "legs": sort_status(raw_legs),
                "total_odds": c.get("total_odds"),
                "target": c.get("target") or "12.00+",
                "result": c.get("result") or "PENDING",
            })

    standings_grouped = {}
    if isinstance(standing_rows, list):
        for s in standing_rows:
            if not isinstance(s, dict):
                continue
            lg = s.get("league") or "League"
            standings_grouped.setdefault(lg, []).append({
                "position": s.get("position"),
                "team": s.get("team") or "Team",
                "played": s.get("played"),
                "points": s.get("points"),
            })

    return matches, cards, standings_grouped, None


def _sync_live_scores_with_cooldown(date_str):
    """Fetch live scores at most once every 30 minutes and persist to Supabase."""
    now_ts = time.time()
    safe_date = urllib.parse.quote(date_str, safe="")
    cache_rows = sb_get("saved_markets", f"date=eq.{safe_date}&market=eq.live_cache&limit=1", service=True)
    cached_row = cache_rows[0] if isinstance(cache_rows, list) and cache_rows else None

    if cached_row and cached_row.get("result"):
        try:
            payload = json.loads(cached_row["result"])
            last_ts = float(payload.get("ts", 0))
            age = now_ts - last_ts
            if 0 <= age < LIVE_COOLDOWN_SECONDS:
                return {
                    "date": date_str,
                    "cached": True,
                    "next_update_in_sec": int(LIVE_COOLDOWN_SECONDS - age),
                    "live": len(payload.get("items", [])),
                    "items": payload.get("items", []),
                    "source": "supabase cache (30m cooldown - 0 API calls)",
                }
        except Exception:
            pass

    data = af_get("/fixtures?live=all")
    if _has_error(data):
        return {
            "date": date_str,
            "cached": True,
            "live": 0,
            "items": [],
            "error": data["error"],
            "source": "api-football error - using existing records",
        }

    raw_live = data.get("response", []) if isinstance(data, dict) else []
    compact_items = []
    by_fid = {}
    by_pair = {}

    for fx in raw_live:
        fixture = fx.get("fixture") or {}
        teams = fx.get("teams") or {}
        goals = fx.get("goals") or {}
        status_obj = fixture.get("status") or {}
        fid = fixture.get("id")
        home = (teams.get("home") or {}).get("name") or ""
        away = (teams.get("away") or {}).get("name") or ""
        hg = goals.get("home")
        ag = goals.get("away")
        short_st = status_obj.get("short") or "LIVE"
        elapsed = status_obj.get("elapsed")
        score_str = f"{hg} - {ag}" if hg is not None and ag is not None else short_st
        norm_st = _normalise_status(short_st)
        result_label = f"{score_str} ({short_st} {elapsed}')" if (norm_st == "Live" and elapsed is not None) else score_str
        entry = {
            "fixture_id": fid,
            "home": home,
            "away": away,
            "home_goals": hg,
            "away_goals": ag,
            "score": score_str,
            "result": result_label,
            "status_short": short_st,
            "elapsed": elapsed,
            "status": norm_st,
        }
        compact_items.append(entry)
        if fid is not None:
            by_fid[fid] = entry
        if home and away:
            by_pair[f"{home.lower().strip()} vs {away.lower().strip()}"] = entry

    def _find_live_hit(obj):
        fid = obj.get("fixture_id")
        if fid is not None and fid in by_fid:
            return by_fid[fid]
        pair_key = f"{str(obj.get('home', '')).lower().strip()} vs {str(obj.get('away', '')).lower().strip()}"
        return by_pair.get(pair_key)

    cards_rows = sb_get("saved_cards", f"date=eq.{safe_date}", service=True)
    if isinstance(cards_rows, list) and compact_items:
        for row in cards_rows:
            row_id = row.get("id")
            if not row_id:
                continue
            try:
                legs = json.loads(row.get("legs") or "[]") if isinstance(row.get("legs"), str) else (row.get("legs") or [])
            except Exception:
                continue
            changed = False
            for leg in legs:
                if not isinstance(leg, dict):
                    continue
                hit = _find_live_hit(leg)
                if hit:
                    leg["home_goals"] = hit["home_goals"]
                    leg["away_goals"] = hit["away_goals"]
                    leg["score"] = hit["score"]
                    leg["status_short"] = hit["status_short"]
                    leg["elapsed"] = hit["elapsed"]
                    leg["status"] = hit["status"]
                    changed = True
            if changed:
                sb_patch("saved_cards", f"id=eq.{row_id}", {"legs": json.dumps(legs, separators=(",", ":"))})

    fixture_rows = sb_get("saved_markets", f"date=eq.{safe_date}&market=eq.fixture", service=True)
    if isinstance(fixture_rows, list) and compact_items:
        for frow in fixture_rows:
            fid_row = frow.get("id")
            if not fid_row:
                continue
            hit = _find_live_hit(frow)
            if hit:
                sb_patch("saved_markets", f"id=eq.{fid_row}", {"result": hit["result"]})

    _sb_request(
        "DELETE", "saved_markets",
        f"date=eq.{safe_date}&market=eq.live_cache",
        service=True, prefer="return=minimal",
    )
    sb_post("saved_markets", [{
        "date": date_str,
        "market": "live_cache",
        "home": "SYSTEM",
        "away": "CACHE",
        "league": "SYSTEM",
        "kickoff": datetime.now(timezone.utc).isoformat(),
        "odds": None,
        "result": json.dumps({"ts": now_ts, "items": compact_items}, separators=(",", ":")),
    }])

    return {
        "date": date_str,
        "cached": False,
        "next_update_in_sec": LIVE_COOLDOWN_SECONDS,
        "live": len(compact_items),
        "items": compact_items,
        "source": "api-football - refreshed (30m cooldown started)",
    }


def _run_cron_for_date(date_str):
    matches, fx_err = _fetch_fixtures_for_date(date_str)
    if fx_err:
        return {"date": date_str, "ok": False, "error": fx_err}

    standings_grouped, standings_flat = _fetch_standings_for_fixtures(matches, date_str)
    cards = build_all_cards(matches)
    _persist_date_snapshot(date_str, matches, cards, standings_flat)

    return {
        "date": date_str,
        "ok": True,
        "fixtures_saved": len(matches),
        "cards_saved": len(cards),
        "standings_leagues_saved": len(standings_grouped),
    }


class handler(BaseHTTPRequestHandler):
    def _send_json(self, code, payload):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, code, html_str):
        body = html_str.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path in {"/", "/index.html"}:
            self._send_html(200, _load_html())
            return

        if path == "/api/today":
            req_date = (qs.get("date") or [_date_str(0)])[0]
            matches, cards, standings, err = _load_saved_date_data(req_date)
            if err and matches is None:
                self._send_json(500, {"date": req_date, "matches": [], "cards": [], "standings": {}, "error": err})
                return
            self._send_json(200, {
                "date": req_date,
                "matches": matches or [],
                "cards": cards or [],
                "standings": standings or {},
                "source": "supabase",
            })
            return

        if path == "/api/standings":
            req_date = (qs.get("date") or [_date_str(0)])[0]
            _, _, standings, err = _load_saved_date_data(req_date)
            if err:
                self._send_json(500, {"date": req_date, "standings": {}, "error": err})
                return
            self._send_json(200, {
                "date": req_date,
                "standings": standings or {},
                "source": "supabase",
            })
            return

        if path == "/api/history":
            req_date = (qs.get("date") or [_date_str(-1)])[0]
            matches, cards, _, err = _load_saved_date_data(req_date)
            if err and matches is None:
                self._send_json(500, {"date": req_date, "matches": [], "cards": [], "error": err})
                return
            self._send_json(200, {
                "date": req_date,
                "matches": matches or [],
                "cards": cards or [],
                "source": "supabase history",
            })
            return

        if path == "/api/scores":
            req_date = (qs.get("date") or [_date_str(0)])[0]
            res = _sync_live_scores_with_cooldown(req_date)
            self._send_json(200, res)
            return

        if path in {"/api/cron", "/api/cron/run"}:
            if CRON_SECRET:
                auth = (self.headers.get("Authorization") or "").strip()
                q_secret = (qs.get("secret") or [""])[0].strip()
                if auth != f"Bearer {CRON_SECRET}" and q_secret != CRON_SECRET:
                    self._send_json(401, {"ok": False, "error": "Unauthorized cron request"})
                    return

            today_str = _date_str(0)
            tmr_str = _date_str(1)
            today_res = _run_cron_for_date(today_str)
            tmr_res = _run_cron_for_date(tmr_str)
            self._send_json(200, {
                "ok": bool(today_res.get("ok") or tmr_res.get("ok")),
                "today": today_res,
                "tomorrow": tmr_res,
            })
            return

        self._send_json(404, {"error": "Not found"})