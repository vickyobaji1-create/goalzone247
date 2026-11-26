# Goalzone247 Backend v5 - Vercel + Supabase
# Daily fixtures, 12 accumulator cards and standings are saved at midnight.
# Frontend reads saved data from Supabase. Only /api/scores calls API-Football
# after the daily build.
#
# IMPORTANT FALLBACK:
# The free API-Football plan may not provide enough statistics. When last-10
# statistics are unavailable, this backend still builds all 12 cards from
# today's fixtures using the configured FALLBACK_ODDS. Those odds are estimates,
# not verified bookmaker prices. Each card records whether it used fallback odds.

import os
import json
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Africa/Lagos")
except Exception:
    LOCAL_TZ = timezone(timedelta(hours=1))

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "") or SUPABASE_ANON
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "")
HOST = "v3.football.api-sports.io"
KEEP_DAYS = 10
MAX_STANDINGS_LEAGUES = 12
REQUEST_TIMEOUT = 15

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

FALLBACK_ODDS = {
    "Over 1.5": 1.32,
    "Over 2.5": 1.95,
    "BTTS": 1.85,
    "Home Over 1.5": 1.75,
    "Away Over 1.5": 2.10,
    "1X + BTTS": 2.30,
    "X2 + BTTS": 2.40,
    "Home Win + Over 1.5": 2.05,
    "Away Win + Over 1.5": 2.60,
    "First-Half Over 0.5": 1.45,
    "Home Score Over 0.5": 1.25,
    "Away Score Over 0.5": 1.40,
}


def local_today():
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


def _headers(service=False, prefer=None):
    key = SUPABASE_SERVICE if service else SUPABASE_ANON
    headers = {
        "apikey": key,
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _sb_request(method, table, query="", data=None, service=True, prefer=None):
    if not SUPABASE_URL:
        return {"error": "SUPABASE_URL is not configured"}
    if not (SUPABASE_SERVICE if service else SUPABASE_ANON):
        return {"error": "Supabase key is not configured"}
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    if query:
        url += "?" + query
    payload = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers=_headers(service, prefer), method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
            raw = response.read().decode("utf-8")
            if not raw:
                return {"ok": True, "status": response.status}
            try:
                return json.loads(raw)
            except Exception:
                return {"ok": True, "body": raw, "status": response.status}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {"error": f"Supabase HTTP {exc.code}: {detail}"}
    except Exception as exc:
        return {"error": str(exc)}


def sb_get(table, query=""):
    result = _sb_request("GET", table, query, service=False)
    return result if isinstance(result, list) else [result] if isinstance(result, dict) and "error" in result else []


def sb_post(table, data):
    return _sb_request("POST", table, data=data, service=True, prefer="return=representation")


def sb_delete_date(table, date_str):
    # Remove any existing rows for this date before rebuilding, preventing
    # duplicate cards/matches when the cron is run more than once.
    return _sb_request(
        "DELETE", table, f"date=eq.{urllib.parse.quote(date_str, safe='')}",
        service=True, prefer="return=minimal"
    )


def sb_delete_old(table, cutoff_date):
    return _sb_request(
        "DELETE", table, f"date=lt.{urllib.parse.quote(cutoff_date, safe='')}",
        service=True, prefer="return=minimal"
    )


def af_get(path):
    if not API_FOOTBALL_KEY:
        return {"response": [], "error": "API_FOOTBALL_KEY is not configured"}
    req = urllib.request.Request(
        f"https://{HOST}{path}",
        headers={"x-apisports-key": API_FOOTBALL_KEY},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {"response": [], "error": f"API-Football HTTP {exc.code}: {detail}"}
    except Exception as exc:
        return {"response": [], "error": str(exc)}


def team_stats(last10):
    if not last10:
        return None
    n = len(last10)
    home_games = [x for x in last10 if x.get("is_home")]
    away_games = [x for x in last10 if not x.get("is_home")]

    def rate(items, predicate):
        return sum(1 for item in items if predicate(item)) / max(1, len(items))

    return {
        "scored1": rate(last10, lambda g: g.get("scored", 0) >= 1),
        "scored2": rate(last10, lambda g: g.get("scored", 0) >= 2),
        "conceded1": rate(last10, lambda g: g.get("conceded", 0) >= 1),
        "avg_goals": sum(g.get("scored", 0) + g.get("conceded", 0) for g in last10) / n,
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
        return (hs["scored1"] >= 0.60 and aws["scored1"] >= 0.50) or total_avg >= 2.2
    if market == "Over 2.5":
        return total_avg >= 2.8 and hs["scored1"] >= 0.70 and aws["scored1"] >= 0.70
    if market == "BTTS":
        return hs["scored1"] >= 0.70 and aws["scored1"] >= 0.70 and hs["conceded1"] >= 0.60 and aws["conceded1"] >= 0.60
    if market == "Home Over 1.5":
        return hs["home_scored1"] >= 0.75 and hs["home_scored2"] >= 0.50
    if market == "Away Over 1.5":
        return aws["away_scored1"] >= 0.75 and aws["away_scored2"] >= 0.50
    if market == "1X + BTTS":
        return hs["loss_rate"] <= 0.20 and qualifies("BTTS", hs, aws)
    if market == "X2 + BTTS":
        return aws["loss_rate"] <= 0.20 and qualifies("BTTS", hs, aws)
    if market == "Home Win + Over 1.5":
        return hs["win_rate"] >= 0.60 and qualifies("Over 1.5", hs, aws)
    if market == "Away Win + Over 1.5":
        return aws["win_rate"] >= 0.50 and qualifies("Over 1.5", hs, aws)
    if market == "First-Half Over 0.5":
        return hs["fh"] >= 0.60 and aws["fh"] >= 0.60
    if market == "Home Score Over 0.5":
        return hs["home_scored1"] >= 0.70
    if market == "Away Score Over 0.5":
        return aws["away_scored1"] >= 0.70
    return False


def sort_status(legs):
    order = {"Live": 0, "Upcoming": 1, "Finished": 2}
    return sorted(legs, key=lambda x: order.get(x.get("status", "Upcoming"), 1))


def _normalise_status(value):
    if value in ("1H", "2H", "HT", "ET", "P", "LIVE", "INT"):
        return "Live"
    if value in ("FT", "AET", "PEN"):
        return "Finished"
    return "Upcoming"


def _fixture_to_match(fx, date_str):
    fixture = fx.get("fixture", {})
    teams = fx.get("teams", {})
    league = fx.get("league", {})
    status = fixture.get("status", {}).get("short", "")
    return {
        "date": date_str,
        "market": "fixture",
        "fixture_id": fixture.get("id"),
        "home": teams.get("home", {}).get("name", "Home"),
        "away": teams.get("away", {}).get("name", "Away"),
        "league": league.get("name", "Unknown league"),
        "league_id": league.get("id"),
        "kickoff": fixture.get("date"),
        "odds": None,
        "result": status or "NS",
        "status": _normalise_status(status),
    }


def build_card(market, candidates, used):
    # Five legs initially; add up to eight to try to reach 12.00+.
    # Matches can be reused between different market cards if necessary,
    # but never duplicated inside the same card.
    candidates = sort_status(candidates)
    unused = [c for c in candidates if c.get("fixture_id") not in used]
    already_used = [c for c in candidates if c.get("fixture_id") in used]
    pool = unused + already_used
    legs = []
    seen = set()

    for candidate in pool:
        fid = candidate.get("fixture_id")
        if fid is None or fid in seen:
            continue
        legs.append(dict(candidate))
        seen.add(fid)
        if len(legs) >= 5:
            break

    def total(items):
        result = 1.0
        for item in items:
            result *= float(item.get("odds") or FALLBACK_ODDS[market])
        return result

    while len(legs) < 8 and total(legs) < 12.0:
        next_leg = None
        for candidate in sorted(pool, key=lambda x: float(x.get("odds") or 0), reverse=True):
            fid = candidate.get("fixture_id")
            if fid is not None and fid not in seen:
                next_leg = dict(candidate)
                break
        if next_leg is None:
            break
        legs.append(next_leg)
        seen.add(next_leg.get("fixture_id"))

    # If enough unique fixtures exist, improve the combined price by replacing
    # the lowest-price leg with a higher-price unused candidate.
    attempts = 0
    while legs and total(legs) < 12.0 and attempts < 8:
        attempts += 1
        lowest = min(legs, key=lambda x: float(x.get("odds") or FALLBACK_ODDS[market]))
        replacement = None
        for candidate in sorted(pool, key=lambda x: float(x.get("odds") or 0), reverse=True):
            fid = candidate.get("fixture_id")
            if fid is not None and fid not in seen and float(candidate.get("odds") or 0) > float(lowest.get("odds") or 0):
                replacement = dict(candidate)
                break
        if replacement is None:
            break
        seen.discard(lowest.get("fixture_id"))
        legs[legs.index(lowest)] = replacement
        seen.add(replacement.get("fixture_id"))

    legs = sort_status(legs)
    for leg in legs:
        used.add(leg.get("fixture_id"))

    combined = total(legs) if legs else 0.0
    fallback_used = any(bool(leg.get("fallback_odds")) for leg in legs)
    return {
        "market": market,
        "legs": legs,
        "legs_count": len(legs),
        "total_odds": round(combined, 2),
        "target": "12.00+",
        "meets_target": combined >= 12.0,
        "status": "PENDING",
        "odds_type": "ESTIMATED_FALLBACK" if fallback_used else "SUPPLIED",
        "note": "Fallback estimated odds; not verified bookmaker odds" if fallback_used else "",
    }


def build_daily_cards(matches):
    used = set()
    cards = []
    for market in MARKETS_12:
        candidates = []
        for match in matches:
            fixture_id = match.get("fixture_id")
            if fixture_id is None:
                continue

            hs = team_stats(match.get("home_last10", []))
            aws = team_stats(match.get("away_last10", []))
            has_stats = bool(hs and aws)

            # Use the original qualification rules if both teams' statistics
            # were supplied. Otherwise use the explicit fallback path so cards
            # are still generated without paid statistics endpoints.
            if has_stats and not qualifies(market, hs, aws):
                continue

            supplied_odds = match.get("market_odds", {}).get(market)
            if supplied_odds is None:
                supplied_odds = match.get("odds") if match.get("market") == market else None
            fallback = supplied_odds is None
            odds = float(supplied_odds) if supplied_odds is not None else FALLBACK_ODDS[market]

            candidates.append({
                "fixture_id": fixture_id,
                "home": match.get("home"),
                "away": match.get("away"),
                "league": match.get("league"),
                "kickoff": match.get("kickoff"),
                "status": match.get("status", "Upcoming"),
                "odds": odds,
                "market": market,
                "fallback_odds": fallback,
                "selection_mode": "STATISTICS" if has_stats else "FALLBACK_NO_STATS",
            })

        card = build_card(market, candidates, used)
        cards.append(card)
    return cards


def _save_cards(date_str, cards):
    rows = []
    for card in cards:
        rows.append({
            "date": date_str,
            "market": card["market"],
            "legs": json.dumps(card["legs"], separators=(",", ":")),
            "total_odds": card["total_odds"],
            "target": "12.00+",
            "result": "PENDING",
        })
    if rows:
        return sb_post("saved_cards", rows)
    return {"error": "No cards to save"}


def run_daily_build(date_str=None):
    # Vercel cron uses UTC. This deliberately uses Nigeria local date (WAT)
    # for the site's daily football slate.
    today = date_str or local_today()
    fixture_data = af_get(f"/fixtures?date={today}")
    fixtures = fixture_data.get("response", []) if isinstance(fixture_data, dict) else []

    matches = []
    for fixture in fixtures:
        try:
            matches.append(_fixture_to_match(fixture, today))
        except Exception:
            continue

    # Rebuild this date cleanly if cron is retried.
    for table in ("saved_markets", "saved_cards", "saved_standings"):
        sb_delete_date(table, today)

    match_rows = []
    for match in matches:
        match_rows.append({
            "date": today,
            "market": "fixture",
            "home": match["home"],
            "away": match["away"],
            "league": match["league"],
            "kickoff": match["kickoff"],
            "odds": None,
            "result": match["result"],
        })
    match_save = sb_post("saved_markets", match_rows) if match_rows else {"ok": True, "saved": 0}

    # Build and save all 12 cards automatically, even when statistics are
    # unavailable. In that case the configured fallback odds are used.
    cards = build_daily_cards(matches)
    card_save = _save_cards(today, cards)

    # Standings: only leagues appearing in today's fixtures, capped to reduce
    # free-tier API usage. A failure here does not stop fixture/card saving.
    standing_rows = []
    standing_errors = []
    league_ids = {}
    for match in matches:
        if match.get("league_id") is not None:
            league_ids[match["league_id"]] = match["league"]

    season = int(today[:4])
    for league_id, league_name in list(league_ids.items())[:MAX_STANDINGS_LEAGUES]:
        response = af_get(f"/standings?league={league_id}&season={season}")
        if response.get("error"):
            standing_errors.append(response["error"])
            continue
        try:
            standings = response.get("response", [])[0]["league"]["standings"][0][:5]
            for row in standings:
                standing_rows.append({
                    "date": today,
                    "league": league_name,
                    "league_id": league_id,
                    "position": row["rank"],
                    "team": row["team"]["name"],
                    "played": row["all"]["played"],
                    "points": row["points"],
                })
        except Exception:
            continue

    standing_save = sb_post("saved_standings", standing_rows) if standing_rows else {"ok": True, "saved": 0}

    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    cleanup = {
        table: sb_delete_old(table, cutoff)
        for table in ("saved_markets", "saved_cards", "saved_standings")
    }

    return {
        "ok": True,
        "date": today,
        "fetch_time": "00:00 Africa/Lagos (cron schedule must be configured)",
        "fixtures_fetched": len(fixtures),
        "fixtures_saved": len(match_rows),
        "cards_built": len(cards),
        "cards_saved": len(cards) if not (isinstance(card_save, dict) and card_save.get("error")) else 0,
        "cards": cards,
        "statistics_mode": "statistics when supplied; fallback odds when unavailable",
        "fallback_odds_are_estimates": True,
        "standings_saved": len(standing_rows),
        "kept_days": KEEP_DAYS,
        "deleted_before": cutoff,
        "supabase_match_save": match_save,
        "supabase_card_save": card_save,
        "supabase_standings_save": standing_save,
        "cleanup": cleanup,
        "fixture_api_error": fixture_data.get("error") if isinstance(fixture_data, dict) else None,
        "standings_errors": standing_errors,
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


def handler(event, context):
    path = event.get("path", "/")
    method = event.get("httpMethod", "GET")
    body = event.get("body") or ""
    query = event.get("queryStringParameters") or {}

    if method == "OPTIONS":
        return _json({"ok": True})

    if path == "/api/health":
        return _json({
            "ok": True,
            "fetch_time": "00:00 Africa/Lagos (configure Vercel Cron)",
            "keep_days": KEEP_DAYS,
            "supabase": bool(SUPABASE_URL and SUPABASE_SERVICE),
            "api_key_configured": bool(API_FOOTBALL_KEY),
            "markets": len(MARKETS_12),
        })

    if path == "/api/cron":
        return _json(run_daily_build())

    if path == "/api/build-cards" and method == "POST":
        try:
            payload = json.loads(body) if body else {}
        except Exception:
            payload = {}
        date_str = payload.get("date") or local_today()
        matches = payload.get("matches", [])
        cards = build_daily_cards(matches)
        sb_delete_date("saved_cards", date_str)
        saved = _save_cards(date_str, cards)
        return _json({
            "ok": True,
            "date": date_str,
            "cards_built": len(cards),
            "saved": saved,
            "cards": cards,
        })

    if path == "/api/history":
        date_str = query.get("date")
        if date_str:
            cards = sb_get("saved_cards", f"date=eq.{urllib.parse.quote(date_str, safe='')}&order=created_at.asc")
            matches = sb_get("saved_markets", f"date=eq.{urllib.parse.quote(date_str, safe='')}&order=created_at.asc")
            return _json({"date": date_str, "cards": cards, "matches": matches, "source": "supabase - no API call"})
        try:
            days = max(1, min(int(query.get("days", KEEP_DAYS)), KEEP_DAYS))
        except Exception:
            days = KEEP_DAYS
        cutoff = (datetime.now(LOCAL_TZ).date() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        cards = sb_get("saved_cards", f"date=gte.{cutoff}&order=date.desc,created_at.asc&limit=500")
        return _json({"days": days, "cards": cards, "source": "supabase - no API call"})

    if path == "/api/today":
        date_str = query.get("date") or local_today()
        safe_date = urllib.parse.quote(date_str, safe="")
        cards = sb_get("saved_cards", f"date=eq.{safe_date}&order=created_at.asc")
        matches = sb_get("saved_markets", f"date=eq.{safe_date}&order=created_at.asc")
        standings = sb_get("saved_standings", f"date=eq.{safe_date}&order=league.asc,position.asc")
        return _json({
            "date": date_str,
            "cards": cards,
            "matches": matches,
            "standings": standings,
            "source": "supabase - no API call",
        })

    if path == "/api/standings":
        date_str = query.get("date") or local_today()
        safe_date = urllib.parse.quote(date_str, safe="")
        rows = sb_get("saved_standings", f"date=eq.{safe_date}&order=league.asc,position.asc")
        grouped = {}
        for row in rows:
            if isinstance(row, dict) and "league" in row:
                grouped.setdefault(row["league"], []).append({
                    "position": row.get("position"),
                    "team": row.get("team"),
                    "played": row.get("played"),
                    "points": row.get("points"),
                })
        return _json({
            "date": date_str,
            "leagues": list(grouped.keys()),
            "standings": grouped,
            "source": "supabase - no API call",
        })

    if path == "/api/scores":
        # This is the only normal frontend endpoint that calls API-Football
        # after the daily build.
        date_str = query.get("date") or local_today()
        data = af_get(f"/fixtures?date={urllib.parse.quote(date_str, safe='')}&live=all")
        live = data.get("response", []) if isinstance(data, dict) else []
        return _json({
            "date": date_str,
            "live": len(live),
            "fixtures": live,
            "source": "api-football - score update only",
            "error": data.get("error") if isinstance(data, dict) else None,
        })

    if path == "/api/saved-markets":
        if method == "POST":
            try:
                payload = json.loads(body) if body else {}
            except Exception:
                payload = {}
            return _json(sb_post("saved_markets", payload))
        return _json(sb_get("saved_markets", "order=created_at.desc&limit=200"))

    if path == "/api/saved-cards":
        if method == "GET":
            return _json(sb_get("saved_cards", "order=date.desc,created_at.asc&limit=500"))
        return _json({"error": "use POST /api/build-cards"}, 405)

    if path == "/api/update-result" and method == "POST":
        try:
            payload = json.loads(body) if body else {}
        except Exception:
            payload = {}
        return _json({
            "market": payload.get("market"),
            "result": win_lose(
                payload.get("market", ""),
                payload.get("home_goals"),
                payload.get("away_goals"),
                payload.get("fh_home"),
                payload.get("fh_away"),
            ),
        })

    # Serve the site's root page when the deployment routes "/" here.
    if path == "/":
        try:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            with open(os.path.join(root, "index.html"), encoding="utf-8") as html_file:
                return {
                    "statusCode": 200,
                    "headers": {"Content-Type": "text/html; charset=utf-8"},
                    "body": html_file.read(),
                }
        except Exception as exc:
            return _json({"error": "index.html could not be loaded", "detail": str(exc)}, 500)

    return _json({"error": "Not found", "path": path}, 404)


# Vercel Python/Flask entry point. This must exist at module top level.
from flask import Flask, request, Response

app = Flask(__name__)


@app.route("/", defaults={"subpath": ""}, methods=["GET", "POST", "OPTIONS"])
@app.route("/<path:subpath>", methods=["GET", "POST", "OPTIONS"])
def vercel_entry(subpath):
    path = request.path
    event = {
        "path": path,
        "httpMethod": request.method,
        "body": request.get_data(as_text=True) or "",
        "queryStringParameters": dict(request.args),
    }
    result = handler(event, None)
    return Response(
        result.get("body", ""),
        status=result.get("statusCode", 200),
        headers=result.get("headers", {}),
    )
