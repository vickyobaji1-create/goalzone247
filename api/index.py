# Goalzone247 Backend v6.4 - Vercel + Supabase + requests
# Daily fixtures, accumulator cards, and standings are stored in Supabase.
# Historical reads never call API-Football. Estimated fallback prices are
# explicitly labelled and must never be represented as bookmaker odds.

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
REQUEST_TIMEOUT = 15

TABLES = ("saved_markets", "saved_cards", "saved_standings")

MARKETS_12 = [
    "Over 1.5", "Over 2.5", "BTTS", "Home Over 1.5", "Away Over 1.5",
    "1X + BTTS", "X2 + BTTS", "Home Win + Over 1.5",
    "Away Win + Over 1.5", "First-Half Over 0.5",
    "Home Score Over 0.5", "Away Score Over 0.5",
]

# Pure goal markets (no BTTS) for 2.50 Rollover and time/mix accumulators
GOAL_MARKETS = [
    "Over 1.5", "Over 2.5", "Home Over 1.5", "Away Over 1.5",
    "Home Win + Over 1.5", "Away Win + Over 1.5",
    "First-Half Over 0.5", "Home Score Over 0.5", "Away Score Over 0.5",
]

FALLBACK_ODDS = {
    "Over 1.5": 1.32, "Over 2.5": 1.95, "BTTS": 1.85,
    "Home Over 1.5": 1.75, "Away Over 1.5": 2.10,
    "1X + BTTS": 2.30, "X2 + BTTS": 2.40,
    "Home Win + Over 1.5": 2.05, "Away Win + Over 1.5": 2.60,
    "First-Half Over 0.5": 1.45, "Home Score Over 0.5": 1.25,
    "Away Score Over 0.5": 1.40,
}


def local_today():
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


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
    if value in ("1H", "2H", "HT", "ET", "P", "LIVE", "INT"):
        return "Live"
    if value in ("FT", "AET", "PEN"):
        return "Finished"
    return "Upcoming"


def sort_status(legs):
    order = {"Live": 0, "Upcoming": 1, "Finished": 2}
    return sorted(legs, key=lambda x: order.get(x.get("status", "Upcoming"), 1))


def _fixture_to_match(fx, date_str):
    fixture = fx.get("fixture") or {}
    teams = fx.get("teams") or {}
    league = fx.get("league") or {}
    status = (fixture.get("status") or {}).get("short", "")
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    return {
        "date": date_str,
        "market": "fixture",
        "fixture_id": fixture.get("id"),
        "home": home.get("name"),
        "away": away.get("name"),
        "league": league.get("name"),
        "league_id": league.get("id"),
        "kickoff": fixture.get("date"),
        "odds": None,
        "result": status or "NS",
        "status": _normalise_status(status),
    }


def _total_odds(items):
    price = 1.0
    for item in items:
        price *= float(item["odds"])
    return price


def build_card(market, candidates, used, min_legs=5, max_legs=8, target_odds=12.0, target_label="12.00+"):
    candidates = sort_status(candidates)
    pool = (
        [c for c in candidates if c.get("fixture_id") not in used]
        + [c for c in candidates if c.get("fixture_id") in used]
    )
    legs, seen = [], set()

    for candidate in pool:
        fid = candidate.get("fixture_id")
        if fid is None or fid in seen:
            continue
        legs.append(dict(candidate))
        seen.add(fid)
        if len(legs) == min_legs:
            break

    while len(legs) < max_legs and legs and _total_odds(legs) < target_odds:
        extra = next(
            (dict(c) for c in sorted(pool, key=lambda x: float(x["odds"]), reverse=True)
             if c.get("fixture_id") is not None and c.get("fixture_id") not in seen),
            None,
        )
        if extra is None:
            break
        legs.append(extra)
        seen.add(extra["fixture_id"])

    legs = sort_status(legs)
    used.update(x["fixture_id"] for x in legs)
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
        "league": match.get("league"),
        "kickoff": match.get("kickoff"),
        "status": match.get("status", "Upcoming"),
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


def _collect_candidates(matches, markets, filter_fn=None):
    items = []
    subset = [m for m in matches if filter_fn(m)] if filter_fn else list(matches)
    for mkt in markets:
        for match in subset:
            cand = _candidate_for_match(match, mkt)
            if cand:
                items.append(cand)
    return items


def build_extra_cards(matches, used):
    extra_cards = []

    morning_250_pool = _collect_candidates(
        matches, ("Over 1.5", "Home Score Over 0.5", "Away Score Over 0.5"),
        lambda m: _kickoff_hour_wat(m) < 14,
    ) or _collect_candidates(matches, ("Over 1.5", "Home Score Over 0.5", "Away Score Over 0.5"))

    evening_250_pool = _collect_candidates(
        matches, ("Over 1.5", "Home Score Over 0.5", "Away Score Over 0.5"),
        lambda m: _kickoff_hour_wat(m) >= 14,
    ) or morning_250_pool

    extra_cards.append(build_card("2.50 Rollover - Morning Card 1", morning_250_pool, used, 2, 4, 2.50, "2.50+"))
    extra_cards.append(build_card("2.50 Rollover - Morning Card 2", morning_250_pool, used, 2, 4, 2.50, "2.50+"))
    extra_cards.append(build_card("2.50 Rollover - Evening Card 1", evening_250_pool, used, 2, 4, 2.50, "2.50+"))
    extra_cards.append(build_card("2.50 Rollover - Evening Card 2", evening_250_pool, used, 2, 4, 2.50, "2.50+"))

    early_morning_pool = _collect_candidates(matches, GOAL_MARKETS, lambda m: _kickoff_hour_wat(m) < 12) or _collect_candidates(matches, GOAL_MARKETS)
    morning_pool = _collect_candidates(matches, GOAL_MARKETS, lambda m: 6 <= _kickoff_hour_wat(m) < 15) or _collect_candidates(matches, GOAL_MARKETS)
    midnight_pool = _collect_candidates(matches, GOAL_MARKETS, lambda m: _kickoff_hour_wat(m) >= 21 or _kickoff_hour_wat(m) < 4) or _collect_candidates(matches, GOAL_MARKETS)
    mega_mix_pool = _collect_candidates(matches, ("Over 2.5", "Over 1.5", "Home Over 1.5", "Away Over 1.5", "Home Win + Over 1.5", "Away Win + Over 1.5"))

    extra_cards.append(build_card("Early Morning Accumulator", early_morning_pool, used, 5, 8, 12.0, "12.00+"))
    extra_cards.append(build_card("Morning Accumulator", morning_pool, used, 5, 8, 12.0, "12.00+"))
    extra_cards.append(build_card("Midnight Accumulator", midnight_pool, used, 5, 8, 12.0, "12.00+"))
    extra_cards.append(build_card("Mega Mix Accumulator", mega_mix_pool, used, 6, 12, 12.0, "12.00+"))

    return extra_cards


def build_daily_cards(matches):
    used, cards = set(), []
    for market in MARKETS_12:
        candidates = []
        for match in matches:
            cand = _candidate_for_match(match, market)
            if cand:
                candidates.append(cand)
        cards.append(build_card(market, candidates, used))
    cards.extend(build_extra_cards(matches, used))
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


def run_daily_build(date_str=None):
    today = date_str or local_today()
    if not valid_date(today):
        return {"ok": False, "error": "Invalid date; expected YYYY-MM-DD"}

    fixture_data = af_get(f"/fixtures?date={urllib.parse.quote(today, safe='')}")
    if _has_error(fixture_data):
        return {"ok": False, "error": fixture_data["error"], "records_preserved": True}
    fixtures = fixture_data.get("response")
    if not isinstance(fixtures, list) or not fixtures:
        return {
            "ok": False,
            "error": "API-Football returned no fixtures; existing records were preserved",
            "records_preserved": True,
            "fixtures_fetched": 0,
        }

    matches = []
    for fixture in fixtures:
        try:
            match = _fixture_to_match(fixture, today)
            if match["fixture_id"] and match["home"] and match["away"] and match["kickoff"]:
                matches.append(match)
        except Exception:
            continue
    if not matches:
        return {
            "ok": False,
            "error": "No valid fixtures could be parsed; existing records were preserved",
            "records_preserved": True,
            "fixtures_fetched": len(fixtures),
        }

    match_rows = [{
        "date": today, "market": "fixture", "home": m["home"], "away": m["away"],
        "league": m["league"], "kickoff": m["kickoff"], "odds": None,
        "result": m["result"],
    } for m in matches]

    cards = build_daily_cards(matches)
    cards_to_save = _card_rows(today, cards)

    backups = {}
    for table in TABLES:
        old = sb_get(table, f"date=eq.{today}", service=True)
        if _has_error(old):
            return {
                "ok": False, "error": f"Could not back up {table}: {old['error']}",
                "records_preserved": True,
            }
        backups[table] = old

    standing_rows, standing_errors, league_ids = [], [], {}
    for match in matches:
        if match.get("league_id") is not None:
            league_ids[match["league_id"]] = match.get("league")
    season = int(today[:4])
    for league_id, league_name in list(league_ids.items())[:MAX_STANDINGS_LEAGUES]:
        response = af_get(f"/standings?league={league_id}&season={season}")
        if _has_error(response):
            standing_errors.append(response["error"])
            continue
        try:
            groups = response.get("response", [])[0]["league"]["standings"][0][:5]
            for row in groups:
                standing_rows.append({
                    "date": today, "league": league_name, "league_id": league_id,
                    "position": row["rank"], "team": row["team"]["name"],
                    "played": row["all"]["played"], "points": row["points"],
                })
        except (IndexError, KeyError, TypeError):
            continue

    new_rows = {
        "saved_markets": match_rows,
        "saved_cards": cards_to_save,
        "saved_standings": standing_rows,
    }

    for table in TABLES:
        deleted = sb_delete_date(table, today)
        if _has_error(deleted):
            for restore_table in TABLES:
                _restore_date(restore_table, today, backups[restore_table])
            return {"ok": False, "error": f"Could not clear old {table} rows", "detail": deleted,
                    "rollback_attempted": True}

    save_errors = {}
    for table in TABLES:
        saved = sb_post(table, new_rows[table])
        if _has_error(saved):
            save_errors[table] = saved["error"]
    if save_errors:
        rollback = {
            table: _restore_date(table, today, backups[table])
            for table in TABLES
        }
        return {
            "ok": False, "error": "Supabase save failed; previous rows were restored where possible",
            "save_errors": save_errors, "rollback": rollback,
        }

    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    cleanup = {table: sb_delete_old(table, cutoff) for table in TABLES}
    cleanup_errors = {k: v for k, v in cleanup.items() if _has_error(v)}

    return {
        "ok": True,
        "date": today,
        "fetch_time": "00:00 Africa/Lagos (Vercel cron at 23:00 UTC)",
        "fixtures_fetched": len(fixtures),
        "fixtures_saved": len(match_rows),
        "cards_built": len(cards),
        "cards_saved": len(cards_to_save),
        "cards": cards,
        "fallback_odds_are_estimates": True,
        "standings_saved": len(standing_rows),
        "standings_errors": standing_errors,
        "kept_days": KEEP_DAYS,
        "deleted_before": cutoff,
        "cleanup": cleanup,
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
            "supabase_configured": bool(SUPABASE_URL and SUPABASE_SERVICE),
            "api_key_configured": bool(API_FOOTBALL_KEY),
            "markets": len(MARKETS_12),
        })

    if path == "/api/cron":
        result = run_daily_build(query.get("date"))
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
            matches, err2 = _read_rows("saved_markets", f"date=eq.{safe}&order=created_at.asc")
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
        matches, e2 = _read_rows("saved_markets", f"date=eq.{safe}&order=created_at.asc")
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
        data = af_get("/fixtures?live=all")
        live = data.get("response", []) if isinstance(data, dict) else []
        return _json({"date": date_str, "live": len(live), "fixtures": live,
                      "source": "api-football - live score update",
                      "error": data.get("error") if isinstance(data, dict) else None})

    if path == "/api/saved-markets":
        if method == "POST":
            try:
                payload = json.loads(body) if body else {}
            except ValueError:
                return _json({"error": "Invalid JSON body"}, 400)
            result = sb_post("saved_markets", payload)
            return _json(result, 503 if _has_error(result) else 200)
        rows, error = _read_rows("saved_markets", "order=created_at.desc&limit=200")
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
