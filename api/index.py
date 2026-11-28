# Goalzone247 Backend v7.0 - Vercel + Supabase + requests
# Daily fixtures, accumulator cards, and standings are stored in Supabase.
# Live scores use a strict 30-minute Supabase cooldown so visitor refreshes never burn API quota.

import os
import json
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Africa/Lagos")
except Exception:
    LOCAL_TZ = timezone(timedelta(hours=1))

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_ANON = os.environ.get("SUPABASE_ANON_KEY", "").strip()
SUPABASE_SERVICE = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "") or SUPABASE_ANON).strip()
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "").strip()
HOST = "v3.football.api-sports.io"
KEEP_DAYS = 10
MAX_STANDINGS_LEAGUES = 12
LIVE_COOLDOWN_SECONDS = 1800  # 30 minutes strict cooldown
REQUEST_TIMEOUT = 15

TABLES = ("saved_markets", "saved_cards", "saved_standings")

MARKETS_12 = [
    "Over 1.5", "Over 2.5", "BTTS", "Home Over 1.5", "Away Over 1.5",
    "1X + BTTS", "X2 + BTTS", "Home Win + Over 1.5",
    "Away Win + Over 1.5", "First-Half Over 0.5",
    "Home Score Over 0.5", "Away Score Over 0.5",
]

# Strictly Over 1.5 and Over 2.5 only for 2.50 Rollover cards
ROLLOVER_250_MARKETS = (
    "Over 1.5",
    "Over 2.5",
)

# Mixed goal markets for Early Morning, time-window, and Mega Mix accumulators
MIX_GOAL_MARKETS = (
    "Over 2.5",
    "Home Over 1.5",
    "Away Over 1.5",
    "Home Win + Over 1.5",
    "Away Win + Over 1.5",
    "First-Half Over 0.5",
    "Over 1.5",
)

FALLBACK_ODDS = {
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

# Broad global league weightings (Europe, Americas, Asia/Oceania, Continental Cups)
PRIORITY_LEAGUE_IDS = {
    # Top European & Continental
    39: 100,   # Premier League (England)
    40: 82,    # Championship (England)
    41: 74,    # League One (England)
    45: 78,    # FA Cup (England)
    48: 75,    # EFL Cup (England)
    140: 98,   # La Liga (Spain)
    141: 76,   # Segunda División (Spain)
    143: 75,   # Copa del Rey (Spain)
    78: 98,    # Bundesliga (Germany)
    79: 78,    # 2. Bundesliga (Germany)
    81: 75,    # DFB Pokal (Germany)
    135: 98,   # Serie A (Italy)
    136: 75,   # Serie B (Italy)
    137: 74,   # Coppa Italia (Italy)
    61: 95,    # Ligue 1 (France)
    62: 74,    # Ligue 2 (France)
    2: 97,     # UEFA Champions League
    3: 90,     # UEFA Europa League
    848: 82,   # UEFA Conference League
    # Strong European secondary / midweek-active leagues
    88: 90,    # Eredivisie (Netherlands)
    89: 75,    # Eerste Divisie (Netherlands)
    94: 88,    # Primeira Liga (Portugal)
    144: 84,   # Jupiler Pro League (Belgium)
    179: 80,   # Premiership (Scotland)
    203: 84,   # Süper Lig (Turkey)
    197: 76,   # Super League 1 (Greece)
    218: 78,   # Liga I (Romania)
    106: 76,   # Ekstraklasa (Poland)
    207: 78,   # Super League (Switzerland)
    210: 76,   # HNL (Croatia)
    119: 78,   # Superliga (Denmark)
    113: 78,   # Allsvenskan (Sweden)
    103: 78,   # Eliteserien (Norway)
    # Americas (great for evening & midnight WAT windows + midweek fixtures)
    71: 90,    # Serie A (Brazil)
    72: 75,    # Serie B (Brazil)
    128: 86,   # Liga Profesional (Argentina)
    253: 84,   # Major League Soccer (USA)
    262: 82,   # Liga MX (Mexico)
    13: 88,    # CONMEBOL Libertadores
    11: 80,    # CONMEBOL Sudamericana
    # Asia & Oceania (strong morning & early-morning WAT coverage)
    98: 84,    # J1 League (Japan)
    99: 74,    # J2 League (Japan)
    292: 80,   # K League 1 (Korea Republic)
    188: 78,   # A-League (Australia)
    169: 74,   # Super League (China)
    307: 82,   # Pro League (Saudi Arabia)
}

HIGH_SCORE_KEYWORDS = (
    "premier", "bundesliga", "eredivisie", "la liga", "serie a", "ligue 1",
    "championship", "primeira", "super lig", "pro league", "j1", "j2", "k league",
    "a-league", "mls", "liga mx", "allsvenskan", "eliteserien", "superliga",
    "champions league", "europa", "libertadores", "sudamericana", "cup",
)


def local_today():
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


def local_tomorrow():
    return (datetime.now(LOCAL_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")


def valid_date(value):
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


def _headers(service=False, prefer=None):
    key = SUPABASE_SERVICE if service else SUPABASE_ANON
    result = {
        "apikey": key,
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
    }
    if prefer:
        result["Prefer"] = prefer
    return result


def _clean_supabase_url():
    clean_url = SUPABASE_URL.rstrip("/")
    if clean_url.endswith("/rest/v1"):
        clean_url = clean_url[: -len("/rest/v1")]
    return clean_url


def _sb_request(method, table, query="", data=None, service=True, prefer=None):
    if not SUPABASE_URL:
        return {"error": "SUPABASE_URL is not configured"}
    clean_url = _clean_supabase_url()
    key = SUPABASE_SERVICE if service else SUPABASE_ANON
    if not key:
        return {"error": "Supabase key is not configured"}
    url = f"{clean_url}/rest/v1/{table}"
    if query:
        url += "?" + query

    last_err = ""
    for attempt in range(3):
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=_headers(service, prefer),
                json=data if data is not None else None,
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code >= 400:
                return {"error": f"Supabase HTTP {response.status_code}: {response.text}"}
            if not response.text:
                return {"ok": True, "status": response.status_code}
            try:
                return response.json()
            except ValueError:
                return {"ok": True, "body": response.text, "status": response.status_code}
        except Exception as exc:
            last_err = str(exc)
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))

    return {"error": last_err}


def _has_error(value):
    return isinstance(value, dict) and bool(value.get("error"))


def sb_get(table, query="", service=False):
    result = _sb_request("GET", table, query, service=service)
    if isinstance(result, list):
        return result
    if _has_error(result):
        return result
    return []


def sb_post(table, data):
    if not data:
        return {"ok": True, "saved": 0}
    return _sb_request(
        "POST", table, data=data, service=True, prefer="return=representation"
    )


def sb_patch(table, query, data):
    return _sb_request(
        "PATCH", table, query=query, data=data, service=True, prefer="return=minimal"
    )


def sb_delete_date(table, date_str):
    return _sb_request(
        "DELETE", table,
        f"date=eq.{urllib.parse.quote(date_str, safe='')}",
        service=True, prefer="return=minimal",
    )


def sb_delete_old(table, cutoff_date):
    return _sb_request(
        "DELETE", table,
        f"date=lt.{urllib.parse.quote(cutoff_date, safe='')}",
        service=True, prefer="return=minimal",
    )


def af_get(path):
    if not API_FOOTBALL_KEY:
        return {"response": [], "error": "API_FOOTBALL_KEY is not configured"}
    last_err = ""
    for attempt in range(3):
        try:
            response = requests.get(
                f"https://{HOST}{path}",
                headers={"x-apisports-key": API_FOOTBALL_KEY},
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code >= 400:
                return {"response": [], "error": f"API-Football HTTP {response.status_code}: {response.text}"}
            data = response.json()
            if not isinstance(data, dict):
                return {"response": [], "error": "Unexpected API-Football response"}
            if data.get("errors"):
                return {"response": [], "error": str(data["errors"])}
            return data
        except Exception as exc:
            last_err = str(exc)
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
    return {"response": [], "error": last_err}


def team_stats(last10):
    if not last10:
        return None
    home_games = [x for x in last10 if x.get("is_home")]
    away_games = [x for x in last10 if not x.get("is_home")]

    def rate(items, predicate):
        return sum(1 for item in items if predicate(item)) / max(1, len(items))

    return {
        "scored1": rate(last10, lambda g: g.get("scored", 0) >= 1),
        "scored2": rate(last10, lambda g: g.get("scored", 0) >= 2),
        "conceded1": rate(last10, lambda g: g.get("conceded", 0) >= 1),
        "avg_goals": sum(g.get("scored", 0) + g.get("conceded", 0) for g in last10) / len(last10),
        "win_rate": rate(last10, lambda g: bool(g.get("won"))),
        "loss_rate": rate(last10, lambda g: bool(g.get("lost"))),
        "fh": rate(last10, lambda g: bool(g.get("scored_first_half"))),
        "home_scored1": rate(home_games, lambda g: g.get("scored", 0) >= 1),
        "away_scored1": rate(away_games, lambda g: g.get("scored", 0) >= 1),
        "home_scored2": rate(home_games, lambda g: g.get("scored", 0) >= 2),
        "away_scored2": rate(away_games, lambda g: g.get("scored", 0) >= 2),
    }


def qualifies(market, hs, aws):
    if not hs or not aws:
        return False
    total_avg = (hs["avg_goals"] + aws["avg_goals"]) / 2
    if market == "Over 1.5":
        return (hs["scored1"] >= .60 and aws["scored1"] >= .50) or total_avg >= 2.2
    if market == "Over 2.5":
        return total_avg >= 2.8 and hs["scored1"] >= .70 and aws["scored1"] >= .70
    if market == "BTTS":
        return all(x >= .60 for x in (hs["scored1"], aws["scored1"], hs["conceded1"], aws["conceded1"]))
    if market == "Home Over 1.5":
        return hs["home_scored1"] >= .75 and hs["home_scored2"] >= .50
    if market == "Away Over 1.5":
        return aws["away_scored1"] >= .75 and aws["away_scored2"] >= .50
    if market == "1X + BTTS":
        return hs["loss_rate"] <= .20 and qualifies("BTTS", hs, aws)
    if market == "X2 + BTTS":
        return aws["loss_rate"] <= .20 and qualifies("BTTS", hs, aws)
    if market == "Home Win + Over 1.5":
        return hs["win_rate"] >= .60 and qualifies("Over 1.5", hs, aws)
    if market == "Away Win + Over 1.5":
        return aws["win_rate"] >= .50 and qualifies("Over 1.5", hs, aws)
    if market == "First-Half Over 0.5":
        return hs["fh"] >= .60 and aws["fh"] >= .60
    if market == "Home Score Over 0.5":
        return hs["home_scored1"] >= .70
    if market == "Away Score Over 0.5":
        return aws["away_scored1"] >= .70
    return False


def _normalise_status(value):
    val = str(value or "").strip().upper()
    if val in ("1H", "2H", "HT", "ET", "P", "LIVE", "INT", "IN_PLAY"):
        return "Live"
    if val in ("FT", "AET", "PEN", "FINISHED", "WON", "LOST"):
        return "Finished"
    return "Upcoming"


def _kickoff_epoch(raw):
    if not raw:
        return 0.0
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


def sort_status(legs):
    order = {"Live": 0, "Upcoming": 1, "Finished": 2}
    return sorted(
        legs,
        key=lambda x: (
            order.get(_normalise_status(x.get("status", "Upcoming")), 1),
            _kickoff_epoch(x.get("kickoff") or x.get("date")),
        ),
    )


def _league_performance_score(league_id, league_name, fixture_count=1):
    score = float(PRIORITY_LEAGUE_IDS.get(league_id, 45))
    name_l = str(league_name or "").lower()
    if any(kw in name_l for kw in HIGH_SCORE_KEYWORDS):
        score += 18.0
    score += min(25.0, max(0, fixture_count - 1) * 3.0)
    return score


def _rank_matches_by_quality(matches):
    counts = {}
    for m in matches:
        lid = m.get("league_id")
        counts[lid] = counts.get(lid, 0) + 1

    def quality_key(m):
        lid = m.get("league_id")
        lg = m.get("league") or ""
        q_score = _league_performance_score(lid, lg, counts.get(lid, 1))
        hs = team_stats(m.get("home_last10", []))
        aws = team_stats(m.get("away_last10", []))
        if hs and aws:
            q_score += ((hs["avg_goals"] + aws["avg_goals"]) / 2.0) * 10.0
        return (-q_score, _kickoff_epoch(m.get("kickoff") or m.get("date")))

    return sorted(matches, key=quality_key)


def _fixture_to_match(fx, date_str):
    fixture = fx.get("fixture") or {}
    teams = fx.get("teams") or {}
    league = fx.get("league") or {}
    goals = fx.get("goals") or {}
    status_obj = fixture.get("status") or {}
    status_short = status_obj.get("short", "")
    elapsed = status_obj.get("elapsed")
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    hg = goals.get("home")
    ag = goals.get("away")
    score_str = f"{hg} - {ag}" if hg is not None and ag is not None else None
    lid = league.get("id")
    lname = league.get("name")
    norm_status = _normalise_status(status_short)
    result_payload = score_str or status_short or "NS"
    if norm_status == "Live" and elapsed is not None:
        result_payload = f"{score_str or '0 - 0'} ({status_short} {elapsed}')"
    return {
        "date": date_str,
        "market": "fixture",
        "fixture_id": fixture.get("id"),
        "home": home.get("name"),
        "away": away.get("name"),
        "country": league.get("country") or "",
        "league": lname,
        "league_id": lid,
        "league_score": _league_performance_score(lid, lname),
        "kickoff": fixture.get("date"),
        "elapsed": elapsed,
        "status_short": status_short or "NS",
        "home_goals": hg,
        "away_goals": ag,
        "score": score_str,
        "odds": None,
        "result": result_payload,
        "status": norm_status,
    }


def _total_odds(items):
    price = 1.0
    for item in items:
        price *= float(item["odds"])
    return price


def build_card(market, candidates, used, min_legs=5, max_legs=8, target_odds=12.0, target_label="12.00+"):
    candidates = sort_status(candidates)
    # Prefer unused fixtures first, then allow reuse of strong candidates if needed so cards stay full
    pool = (
        [c for c in candidates if c.get("fixture_id") not in used]
        + [c for c in candidates if c.get("fixture_id") in used]
    )
    legs, seen_pairs, seen_fixtures = [], set(), set()

    for candidate in pool:
        fid = candidate.get("fixture_id")
        mkt = candidate.get("market") or market
        pair_key = (fid, mkt)
        if fid is None or pair_key in seen_pairs or fid in seen_fixtures:
            continue
        legs.append(dict(candidate))
        seen_pairs.add(pair_key)
        seen_fixtures.add(fid)
        if len(legs) >= min_legs and _total_odds(legs) >= target_odds:
            break

    while len(legs) < max_legs and _total_odds(legs) < target_odds:
        extra = next(
            (dict(c) for c in sorted(pool, key=lambda x: float(x["odds"]), reverse=True)
             if c.get("fixture_id") is not None
             and (c.get("fixture_id"), c.get("market") or market) not in seen_pairs),
            None,
        )
        if extra is None:
            break
        legs.append(extra)
        seen_pairs.add((extra["fixture_id"], extra.get("market") or market))

    legs = sort_status(legs)
    used.update(x["fixture_id"] for x in legs if x.get("fixture_id") is not None)
    combined = _total_odds(legs) if legs else 0.0
    estimated = any(x.get("fallback_odds") for x in legs)
    return {
        "market": market,
        "legs": legs,
        "legs_count": len(legs),
        "total_odds": round(combined, 2),
        "target": target_label,
        "meets_target": combined >= target_odds,
        "status": "PENDING",
        "odds_type": "ESTIMATED_FALLBACK" if estimated else "SUPPLIED",
        "note": "Estimated fallback prices; not verified bookmaker odds" if estimated else "",
    }


def _candidate_for_match(match, market):
    if match.get("fixture_id") is None:
        return None
    hs = team_stats(match.get("home_last10", []))
    aws = team_stats(match.get("away_last10", []))
    has_stats = bool(hs and aws)
    if has_stats and not qualifies(market, hs, aws):
        return None
    supplied = (match.get("market_odds") or {}).get(market)
    if supplied is None and match.get("market") == market:
        supplied = match.get("odds")
    try:
        price = float(supplied) if supplied is not None else FALLBACK_ODDS[market]
    except (TypeError, ValueError):
        return None
    if price <= 1.0:
        return None
    return {
        "fixture_id": match["fixture_id"],
        "home": match.get("home"),
        "away": match.get("away"),
        "country": match.get("country") or "",
        "league": match.get("league"),
        "kickoff": match.get("kickoff"),
        "elapsed": match.get("elapsed"),
        "status_short": match.get("status_short", ""),
        "home_goals": match.get("home_goals"),
        "away_goals": match.get("away_goals"),
        "score": match.get("score"),
        "status": _normalise_status(match.get("status", "Upcoming")),
        "odds": price,
        "market": market,
        "fallback_odds": supplied is None,
        "selection_mode": "STATISTICS" if has_stats else "FALLBACK_NO_STATS",
    }


def _kickoff_hour_wat(match):
    raw = str(match.get("kickoff") or "")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(LOCAL_TZ).hour
    except Exception:
        return 12


def _collect_window_candidates(matches, markets, window_fn):
    """Prioritize matches inside the requested WAT time window, then pad with top-ranked global matches."""
    ranked = _rank_matches_by_quality(matches)
    in_window = [m for m in ranked if window_fn(m)]
    outside_window = [m for m in ranked if not window_fn(m)]
    ordered = in_window + outside_window

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
    return mixed


def build_extra_cards(matches, used):
    extra_cards = []

    morning_250_pool = _collect_window_candidates(
        matches, ROLLOVER_250_MARKETS, lambda m: _kickoff_hour_wat(m) < 14
    )
    evening_250_pool = _collect_window_candidates(
        matches, ROLLOVER_250_MARKETS, lambda m: _kickoff_hour_wat(m) >= 14
    )

    extra_cards.append(build_card("2.50 Rollover - Morning Card 1", morning_250_pool, used, 2, 4, 2.50, "2.50+"))
    extra_cards.append(build_card("2.50 Rollover - Morning Card 2", morning_250_pool, used, 2, 4, 2.50, "2.50+"))
    extra_cards.append(build_card("2.50 Rollover - Evening Card 1", evening_250_pool, used, 2, 4, 2.50, "2.50+"))
    extra_cards.append(build_card("2.50 Rollover - Evening Card 2", evening_250_pool, used, 2, 4, 2.50, "2.50+"))

    early_morning_pool = _collect_window_candidates(
        matches, MIX_GOAL_MARKETS, lambda m: _kickoff_hour_wat(m) < 12
    )
    morning_pool = _collect_window_candidates(
        matches, MIX_GOAL_MARKETS, lambda m: 6 <= _kickoff_hour_wat(m) < 15
    )
    midnight_pool = _collect_window_candidates(
        matches, MIX_GOAL_MARKETS, lambda m: _kickoff_hour_wat(m) >= 21 or _kickoff_hour_wat(m) < 4
    )
    mega_mix_pool = _collect_window_candidates(
        matches, MIX_GOAL_MARKETS, lambda m: True
    )

    extra_cards.append(build_card("Early Morning Accumulator", early_morning_pool, used, 5, 7, 12.0, "12.00+"))
    extra_cards.append(build_card("Morning Accumulator", morning_pool, used, 5, 7, 12.0, "12.00+"))
    extra_cards.append(build_card("Midnight Accumulator", midnight_pool, used, 5, 7, 12.0, "12.00+"))
    extra_cards.append(build_card("Mega Mix Accumulator (12.00+)", mega_mix_pool, used, 5, 8, 12.0, "12.00+"))
    extra_cards.append(build_card("Mega Mix Accumulator (30.00+)", mega_mix_pool, used, 6, 10, 30.0, "30.00+"))

    return extra_cards


def build_daily_cards(matches):
    ranked_matches = _rank_matches_by_quality(matches)
    used, cards = set(), []
    for market in MARKETS_12:
        candidates = []
        for match in ranked_matches:
            cand = _candidate_for_match(match, market)
            if cand:
                candidates.append(cand)
        cards.append(build_card(market, candidates, used))
    cards.extend(build_extra_cards(ranked_matches, used))
    return cards


def _card_rows(date_str, cards):
    return [{
        "date": date_str,
        "market": card["market"],
        "legs": json.dumps(card["legs"], separators=(",", ":")),
        "total_odds": card["total_odds"],
        "target": card.get("target", "12.00+"),
        "result": "PENDING",
    } for card in cards]


def _restore_date(table, date_str, rows):
    deleted = sb_delete_date(table, date_str)
    if _has_error(deleted):
        return deleted
    if rows:
        restored = sb_post(table, rows)
        if _has_error(restored):
            return {"error": "Rollback restore failed", "detail": restored}
    return {"ok": True}


def _fetch_standings_for_matches(matches, date_str, already_fetched_leagues=None):
    standing_rows, standing_errors, league_meta = [], [], {}
    skip_ids = set(already_fetched_leagues or ())
    for match in matches:
        lid = match.get("league_id")
        if lid is not None and lid not in skip_ids:
            existing = league_meta.get(lid)
            cnt = (existing["count"] + 1) if existing else 1
            lname = match.get("league")
            league_meta[lid] = {
                "name": lname,
                "count": cnt,
                "score": _league_performance_score(lid, lname, cnt),
            }

    ranked_leagues = sorted(
        league_meta.items(),
        key=lambda kv: kv[1]["score"],
        reverse=True,
    )[:MAX_STANDINGS_LEAGUES]

    season = int(date_str[:4])
    fetched_ids = set()
    for league_id, info in ranked_leagues:
        league_name = info["name"]
        response = af_get(f"/standings?league={league_id}&season={season}")
        if _has_error(response):
            standing_errors.append(response["error"])
            continue
        try:
            groups = response.get("response", [])[0]["league"]["standings"][0][:5]
            for row in groups:
                standing_rows.append({
                    "date": date_str,
                    "league": league_name,
                    "league_id": league_id,
                    "position": row["rank"],
                    "team": row["team"]["name"],
                    "played": row["all"]["played"],
                    "points": row["points"],
                })
            fetched_ids.add(league_id)
        except (IndexError, KeyError, TypeError):
            continue

    return standing_rows, standing_errors, fetched_ids


def _build_single_day(target_date, skip_standings_leagues=None):
    fixture_data = af_get(f"/fixtures?date={urllib.parse.quote(target_date, safe='')}")
    if _has_error(fixture_data):
        return {"ok": False, "error": fixture_data["error"], "records_preserved": True}
    fixtures = fixture_data.get("response")
    if not isinstance(fixtures, list) or not fixtures:
        return {
            "ok": False,
            "error": f"API-Football returned no fixtures for {target_date}; existing records preserved",
            "records_preserved": True,
            "fixtures_fetched": 0,
        }

    matches = []
    for fixture in fixtures:
        try:
            match = _fixture_to_match(fixture, target_date)
            if match["fixture_id"] and match["home"] and match["away"] and match["kickoff"]:
                matches.append(match)
        except Exception:
            continue
    if not matches:
        return {
            "ok": False,
            "error": f"No valid fixtures could be parsed for {target_date}",
            "records_preserved": True,
            "fixtures_fetched": len(fixtures),
        }

    ranked_matches = _rank_matches_by_quality(matches)
    match_rows = [{
        "date": target_date, "market": "fixture", "home": m["home"], "away": m["away"],
        "league": m["league"], "kickoff": m["kickoff"], "odds": None,
        "result": m["result"],
    } for m in ranked_matches]

    cards = build_daily_cards(ranked_matches)
    cards_to_save = _card_rows(target_date, cards)
    standing_rows, standing_errors, fetched_league_ids = _fetch_standings_for_matches(
        ranked_matches, target_date, skip_standings_leagues
    )

    backups = {}
    for table in TABLES:
        old = sb_get(table, f"date=eq.{target_date}", service=True)
        if _has_error(old):
            return {
                "ok": False, "error": f"Could not back up {table}: {old['error']}",
                "records_preserved": True,
            }
        backups[table] = old

    new_rows = {
        "saved_markets": match_rows,
        "saved_cards": cards_to_save,
        "saved_standings": standing_rows,
    }

    for table in TABLES:
        deleted = sb_delete_date(table, target_date)
        if _has_error(deleted):
            for restore_table in TABLES:
                _restore_date(restore_table, target_date, backups[restore_table])
            return {"ok": False, "error": f"Could not clear old {table} rows", "detail": deleted,
                    "rollback_attempted": True}

    save_errors = {}
    for table in TABLES:
        saved = sb_post(table, new_rows[table])
        if _has_error(saved):
            save_errors[table] = saved["error"]
    if save_errors:
        rollback = {
            table: _restore_date(table, target_date, backups[table])
            for table in TABLES
        }
        return {
            "ok": False, "error": "Supabase save failed; previous rows were restored",
            "save_errors": save_errors, "rollback": rollback,
        }

    return {
        "ok": True,
        "date": target_date,
        "fixtures_fetched": len(fixtures),
        "fixtures_saved": len(match_rows),
        "cards_built": len(cards),
        "cards_saved": len(cards_to_save),
        "cards": cards,
        "ranked_matches": ranked_matches,
        "standings_saved": len(standing_rows),
        "standings_errors": standing_errors,
        "fetched_league_ids": list(fetched_league_ids),
    }


def run_daily_build(date_str=None, include_tomorrow=True):
    today = date_str or local_today()
    if not valid_date(today):
        return {"ok": False, "error": "Invalid date; expected YYYY-MM-DD"}

    today_build = _build_single_day(today)
    if not today_build.get("ok"):
        return today_build

    tomorrow_summary = None
    if not date_str and include_tomorrow:
        tmr = local_tomorrow()
        tmr_build = _build_single_day(tmr, skip_standings_leagues=today_build.get("fetched_league_ids"))
        tomorrow_summary = {
            "date": tmr,
            "ok": bool(tmr_build.get("ok")),
            "fixtures_saved": tmr_build.get("fixtures_saved", 0),
            "cards_saved": tmr_build.get("cards_saved", 0),
            "standings_saved": tmr_build.get("standings_saved", 0),
            "error": tmr_build.get("error"),
        }

    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    cleanup = {table: sb_delete_old(table, cutoff) for table in TABLES}
    cleanup_errors = {k: v for k, v in cleanup.items() if _has_error(v)}

    return {
        "ok": True,
        "date": today,
        "fetch_time": "00:00 Africa/Lagos (Vercel cron at 23:00 UTC)",
        "fixtures_fetched": today_build["fixtures_fetched"],
        "fixtures_saved": today_build["fixtures_saved"],
        "cards_built": today_build["cards_built"],
        "cards_saved": today_build["cards_saved"],
        "cards": today_build["cards"],
        "tomorrow": tomorrow_summary,
        "standings_saved": today_build["standings_saved"],
        "standings_errors": today_build["standings_errors"],
        "kept_days": KEEP_DAYS,
        "deleted_before": cutoff,
        "cleanup_errors": cleanup_errors,
    }


def win_lose(market, hg, ag, fh_h=None, fh_a=None):
    if hg is None or ag is None:
        return "PENDING"
    total = hg + ag
    btts = hg >= 1 and ag >= 1
    if market == "Over 1.5":
        return "WON" if total >= 2 else "LOST"
    if market == "Over 2.5":
        return "WON" if total >= 3 else "LOST"
    if market == "BTTS":
        return "WON" if btts else "LOST"
    if market == "Home Over 1.5":
        return "WON" if hg >= 2 else "LOST"
    if market == "Away Over 1.5":
        return "WON" if ag >= 2 else "LOST"
    if market == "Home Score Over 0.5":
        return "WON" if hg >= 1 else "LOST"
    if market == "Away Score Over 0.5":
        return "WON" if ag >= 1 else "LOST"
    if market == "First-Half Over 0.5":
        if fh_h is None or fh_a is None:
            return "PENDING"
        return "WON" if fh_h + fh_a >= 1 else "LOST"
    if market == "Home Win + Over 1.5":
        return "WON" if hg > ag and total >= 2 else "LOST"
    if market == "Away Win + Over 1.5":
        return "WON" if ag > hg and total >= 2 else "LOST"
    if market == "1X + BTTS":
        return "WON" if hg >= ag and btts else "LOST"
    if market == "X2 + BTTS":
        return "WON" if ag >= hg and btts else "LOST"
    return "PENDING"


def _sync_live_scores_with_cooldown(date_str):
    """Fetch live scores at most once every 30 minutes and persist to Supabase."""
    now_ts = time.time()
    cache_rows = sb_get("saved_markets", f"date=eq.{date_str}&market=eq.live_cache&limit=1", service=True)
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
        entry = {
            "fixture_id": fid,
            "home": home,
            "away": away,
            "home_goals": hg,
            "away_goals": ag,
            "score": score_str,
            "status_short": short_st,
            "elapsed": elapsed,
            "status": _normalise_status(short_st),
        }
        compact_items.append(entry)
        if fid is not None:
            by_fid[fid] = entry
        if home and away:
            by_pair[f"{home.lower()} vs {away.lower()}"] = entry

    cards_rows = sb_get("saved_cards", f"date=eq.{date_str}", service=True)
    if isinstance(cards_rows, list) and compact_items:
        for row in cards_rows:
            row_id = row.get("id")
            if not row_id:
                continue
            try:
                legs = json.loads(row.get("legs") or "[]")
            except Exception:
                continue
            changed = False
            for leg in legs:
                hit = by_fid.get(leg.get("fixture_id")) or by_pair.get(
                    f"{str(leg.get('home', '')).lower()} vs {str(leg.get('away', '')).lower()}"
                )
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

    _sb_request(
        "DELETE", "saved_markets",
        f"date=eq.{date_str}&market=eq.live_cache",
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


def _json(data, status=200):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
        },
        "body": json.dumps(data, default=str),
    }


def _read_rows(table, query):
    result = sb_get(table, query)
    if _has_error(result):
        return None, result["error"]
    return result, None


def handler(event, context):
    raw_path = event.get("path", "/") or "/"
    path = raw_path.split("?", 1)[0]
    method = event.get("httpMethod", "GET")
    body = event.get("body") or ""
    query = event.get("queryStringParameters") or {}

    if method == "OPTIONS":
        return _json({"ok": True})

    if path == "/api/health":
        return _json({
            "ok": True, "timezone": "Africa/Lagos", "keep_days": KEEP_DAYS,
            "live_cooldown_minutes": LIVE_COOLDOWN_SECONDS // 60,
            "supabase_configured": bool(SUPABASE_URL and SUPABASE_SERVICE),
            "api_key_configured": bool(API_FOOTBALL_KEY),
            "markets": len(MARKETS_12),
        })

    if path == "/api/cron":
        target_date = query.get("date")
        include_tmr = str(query.get("tomorrow", "1")).lower() not in ("0", "false", "no")
        result = run_daily_build(target_date, include_tomorrow=include_tmr)
        return _json(result, 200 if result.get("ok") else 503)

    if path == "/api/build-cards" and method == "POST":
        try:
            payload = json.loads(body) if body else {}
        except ValueError:
            return _json({"error": "Invalid JSON body"}, 400)
        date_str = payload.get("date") or local_today()
        matches = payload.get("matches")
        if not valid_date(date_str) or not isinstance(matches, list) or not matches:
            return _json({"error": "A valid date and non-empty matches array are required"}, 400)
        cards = build_daily_cards(matches)
        saved = _save_cards_for_route(date_str, cards)
        if _has_error(saved):
            return _json({"ok": False, "error": saved["error"]}, 503)
        return _json({"ok": True, "date": date_str, "cards_built": len(cards),
                      "saved": saved, "cards": cards})

    if path == "/api/history":
        date_str = query.get("date")
        if date_str:
            if not valid_date(date_str):
                return _json({"error": "Invalid date"}, 400)
            safe = urllib.parse.quote(date_str, safe="")
            cards, err1 = _read_rows("saved_cards", f"date=eq.{safe}&order=created_at.asc")
            matches, err2 = _read_rows("saved_markets", f"date=eq.{safe}&market=eq.fixture&order=created_at.asc")
            if err1 or err2:
                return _json({"error": err1 or err2}, 503)
            return _json({"date": date_str, "cards": cards, "matches": matches,
                          "source": "supabase - no API call"})
        try:
            days = max(1, min(int(query.get("days", KEEP_DAYS)), KEEP_DAYS))
        except (TypeError, ValueError):
            days = KEEP_DAYS
        cutoff = (datetime.now(LOCAL_TZ).date() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        cards, error = _read_rows("saved_cards", f"date=gte.{cutoff}&order=date.desc,created_at.asc&limit=500")
        if error:
            return _json({"error": error}, 503)
        return _json({"days": days, "cards": cards, "source": "supabase - no API call"})

    if path == "/api/today":
        date_str = query.get("date") or local_today()
        if not valid_date(date_str):
            return _json({"error": "Invalid date"}, 400)
        safe = urllib.parse.quote(date_str, safe="")
        cards, e1 = _read_rows("saved_cards", f"date=eq.{safe}&order=created_at.asc")
        matches, e2 = _read_rows("saved_markets", f"date=eq.{safe}&market=eq.fixture&order=created_at.asc")
        standings, e3 = _read_rows("saved_standings", f"date=eq.{safe}&order=league.asc,position.asc")
        if e1 or e2 or e3:
            return _json({"error": e1 or e2 or e3}, 503)
        return _json({"date": date_str, "cards": cards, "matches": matches,
                      "standings": standings, "source": "supabase - no API call"})

    if path == "/api/standings":
        date_str = query.get("date") or local_today()
        if not valid_date(date_str):
            return _json({"error": "Invalid date"}, 400)
        safe = urllib.parse.quote(date_str, safe="")
        rows, error = _read_rows("saved_standings", f"date=eq.{safe}&order=league.asc,position.asc")
        if error:
            return _json({"error": error}, 503)
        grouped = {}
        for row in rows:
            if isinstance(row, dict) and row.get("league"):
                grouped.setdefault(row["league"], []).append({
                    "position": row.get("position"), "team": row.get("team"),
                    "played": row.get("played"), "points": row.get("points"),
                })
        return _json({"date": date_str, "leagues": list(grouped), "standings": grouped,
                      "source": "supabase - no API call"})

    if path == "/api/scores":
        date_str = query.get("date") or local_today()
        if not valid_date(date_str):
            return _json({"error": "Invalid date"}, 400)
        result = _sync_live_scores_with_cooldown(date_str)
        return _json(result)

    if path == "/api/saved-markets":
        if method == "POST":
            try:
                payload = json.loads(body) if body else {}
            except ValueError:
                return _json({"error": "Invalid JSON body"}, 400)
            result = sb_post("saved_markets", payload)
            return _json(result, 503 if _has_error(result) else 200)
        rows, error = _read_rows("saved_markets", "market=eq.fixture&order=created_at.desc&limit=200")
        return _json({"error": error} if error else rows, 503 if error else 200)

    if path == "/api/saved-cards":
        if method != "GET":
            return _json({"error": "Use POST /api/build-cards"}, 405)
        rows, error = _read_rows("saved_cards", "order=date.desc,created_at.asc&limit=500")
        return _json({"error": error} if error else rows, 503 if error else 200)

    if path == "/api/update-result" and method == "POST":
        try:
            payload = json.loads(body) if body else {}
        except ValueError:
            return _json({"error": "Invalid JSON body"}, 400)
        return _json({
            "market": payload.get("market"),
            "result": win_lose(payload.get("market", ""), payload.get("home_goals"),
                               payload.get("away_goals"), payload.get("fh_home"),
                               payload.get("fh_away")),
        })

    if path == "/":
        try:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            with open(os.path.join(root, "index.html"), encoding="utf-8") as file:
                return {"statusCode": 200,
                        "headers": {"Content-Type": "text/html; charset=utf-8"},
                        "body": file.read()}
        except Exception as exc:
            return _json({"error": "index.html could not be loaded", "detail": str(exc)}, 500)

    return _json({"error": "Not found", "path": path}, 404)


def _save_cards_for_route(date_str, cards):
    rows = _card_rows(date_str, cards)
    if not rows:
        return {"error": "No cards to save"}
    return sb_post("saved_cards", rows)


from flask import Flask, request, Response

app = Flask(__name__)


@app.route("/", defaults={"subpath": ""}, methods=["GET", "POST", "OPTIONS"])
@app.route("/<path:subpath>", methods=["GET", "POST", "OPTIONS"])
def vercel_entry(subpath):
    result = handler({
        "path": request.path,
        "httpMethod": request.method,
        "body": request.get_data(as_text=True) or "",
        "queryStringParameters": dict(request.args),
    }, None)
    return Response(result.get("body", ""), status=result.get("statusCode", 200),
                    headers=result.get("headers", {}))