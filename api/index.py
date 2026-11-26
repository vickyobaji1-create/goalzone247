# Goalzone247 backend v5 - SAVE EVERYTHING at 00:00: fixtures + cards + standings. Frontend reads ONLY Supabase. Only /api/scores may call API again.
# Fetch at 00:00. Save BOTH matches AND 12 accumulator cards per day. After 10 days, delete oldest day.
# History: Frontend reads cards from Supabase, NEVER calls API-Football directly for past days.
import os, json, urllib.request, urllib.error
from datetime import datetime, timedelta

SUPABASE_URL = os.environ.get("SUPABASE_URL","")
SUPABASE_ANON = os.environ.get("SUPABASE_ANON_KEY","")
SUPABASE_SERVICE = os.environ.get("SUPABASE_SERVICE_ROLE_KEY","") or SUPABASE_ANON
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY","")
HOST = "v3.football.api-sports.io"
KEEP_DAYS = 10  # Rolling window - keep last 10 days only

def _h(service=False):
    k = SUPABASE_SERVICE if service else SUPABASE_ANON
    return {"apikey": k, "Authorization": f"Bearer {k}", "Content-Type": "application/json", "Prefer": "return=representation"}
def sb_get(table, q=""):
    if not SUPABASE_URL: return []
    req = urllib.request.Request(f"{SUPABASE_URL}/rest/v1/{table}?{q}", headers=_h(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as r: return json.loads(r.read().decode())
    except Exception as e: return [{"error": str(e)}]
def sb_post(table, data):
    if not SUPABASE_URL: return {"error": "no supabase"}
    req = urllib.request.Request(f"{SUPABASE_URL}/rest/v1/{table}", data=json.dumps(data).encode(), headers=_h(True), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r: return json.loads(r.read().decode())
    except Exception as e: return {"error": str(e)}
def sb_delete_old(table, cutoff_date):
    # Delete rows older than cutoff_date: DELETE /rest/v1/table?date=lt.cutoff
    if not SUPABASE_URL: return {"error": "no supabase"}
    url = f"{SUPABASE_URL}/rest/v1/{table}?date=lt.{cutoff_date}"
    req = urllib.request.Request(url, headers=_h(True), method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=15) as r: return {"deleted_before": cutoff_date, "status": r.status}
    except Exception as e: return {"error": str(e)}

def af_get(path):
    if not API_FOOTBALL_KEY: return {"response": []}
    req = urllib.request.Request(f"https://{HOST}{path}", headers={"x-apisports-key": API_FOOTBALL_KEY}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as r: return json.loads(r.read().decode())
    except Exception as e: return {"response": [], "error": str(e)}

# --- Reuse market logic (same thresholds as before) ---
MARKETS_12 = ["Over 1.5","Over 2.5","BTTS","Home Over 1.5","Away Over 1.5","1X + BTTS","X2 + BTTS","Home Win + Over 1.5","Away Win + Over 1.5","First-Half Over 0.5","Home Score Over 0.5","Away Score Over 0.5"]
FALLBACK_ODDS = {"Over 1.5":1.32,"Over 2.5":1.95,"BTTS":1.85,"Home Over 1.5":1.75,"Away Over 1.5":2.10,"1X + BTTS":2.30,"X2 + BTTS":2.40,"Home Win + Over 1.5":2.05,"Away Win + Over 1.5":2.60,"First-Half Over 0.5":1.45,"Home Score Over 0.5":1.25,"Away Score Over 0.5":1.40}
def team_stats(last10):
    if not last10: return None
    n=len(last10)
    return {"scored1": sum(1 for g in last10 if g["scored"]>=1)/n, "scored2": sum(1 for g in last10 if g["scored"]>=2)/n, "conceded1": sum(1 for g in last10 if g["conceded"]>=1)/n, "avg_goals": sum(g["scored"]+g["conceded"] for g in last10)/n, "win_rate": sum(1 for g in last10 if g["won"])/n, "loss_rate": sum(1 for g in last10 if g.get("lost"))/n, "fh": sum(1 for g in last10 if g.get("scored_first_half"))/n, "home_scored1": (sum(1 for g in [x for x in last10 if x.get("is_home")] if g["scored"]>=1)/max(1,len([x for x in last10 if x.get("is_home")]))), "away_scored1": (sum(1 for g in [x for x in last10 if not x.get("is_home")] if g["scored"]>=1)/max(1,len([x for x in last10 if not x.get("is_home")]))), "home_scored2": (sum(1 for g in [x for x in last10 if x.get("is_home")] if g["scored"]>=2)/max(1,len([x for x in last10 if x.get("is_home")]))), "away_scored2": (sum(1 for g in [x for x in last10 if not x.get("is_home")] if g["scored"]>=2)/max(1,len([x for x in last10 if not x.get("is_home")])))}
def qualifies(market, hs, aws):
    if not hs or not aws: return False
    total_avg=(hs["avg_goals"]+aws["avg_goals"])/2
    if market=="Over 1.5": return (hs["scored1"]>=0.60 and aws["scored1"]>=0.50) or total_avg>=2.2
    if market=="Over 2.5": return total_avg>=2.8 and hs["scored1"]>=0.70 and aws["scored1"]>=0.70
    if market=="BTTS": return hs["scored1"]>=0.70 and aws["scored1"]>=0.70 and hs["conceded1"]>=0.60 and aws["conceded1"]>=0.60
    if market=="Home Over 1.5": return hs["home_scored1"]>=0.75 and hs["home_scored2"]>=0.50
    if market=="Away Over 1.5": return aws["away_scored1"]>=0.75 and aws["away_scored2"]>=0.50
    if market=="1X + BTTS": return hs["loss_rate"]<=0.20 and qualifies("BTTS",hs,aws)
    if market=="X2 + BTTS": return aws["loss_rate"]<=0.20 and qualifies("BTTS",hs,aws)
    if market=="Home Win + Over 1.5": return hs["win_rate"]>=0.60 and qualifies("Over 1.5",hs,aws)
    if market=="Away Win + Over 1.5": return aws["win_rate"]>=0.50 and qualifies("Over 1.5",hs,aws)
    if market=="First-Half Over 0.5": return hs["fh"]>=0.60 and aws["fh"]>=0.60
    if market=="Home Score Over 0.5": return hs["home_scored1"]>=0.70
    if market=="Away Score Over 0.5": return aws["away_scored1"]>=0.70
    return False
def sort_status(legs):
    o={"Live":0,"Upcoming":1,"Finished":2}
    return sorted(legs, key=lambda x: o.get(x.get("status","Upcoming"),1))
def build_card(market, candidates, used):
    # NEW RULE: NEVER reject, NEVER leave card empty. ALL 12 cards MUST be populated to meet target 12.00+
    # Start with 5 legs. If total < 12.00, ADD MORE LEGS - 6th, 7th, 8th - until total >= 12.00.
    # Only stop at 8 legs max (to keep card readable). If still < 12.00 after 8 legs, swap lowest odds legs for higher odds legs.
    cands=sort_status(candidates)
    # Prefer unused matches first, but if we need more legs, we CAN reuse across cards if needed to populate - priority is populate all cards
    cands_unused = [c for c in cands if c["fixture_id"] not in used]
    cands_used = [c for c in cands if c["fixture_id"] in used]
    pool = cands_unused + cands_used  # unused first, then used as backup so card never stays empty
    legs=[]
    for c in pool:
        if len(legs)>=5: break
        if any(l["fixture_id"]==c["fixture_id"] for l in legs): continue
        legs.append(c)
    # If still <5 because pool small, allow any candidate (even if duplicate check fails, we already checked)
    if len(legs)<5:
        # Use whatever we have - pad with highest odds remaining
        for c in pool:
            if len(legs)>=5: break
            if c not in legs: legs.append(c)
    def total(ls):
        t=1.0
        for x in ls: t*=x.get("odds", FALLBACK_ODDS[market])
        return t
    # ADD MORE LEGS until >=12.00 - 6th, 7th, 8th
    idx=5
    # pool is ordered, we already used first 5, continue adding from pool
    while total(legs) < 12.00 and len(legs) < 8:
        # Find next candidate not in legs with highest odds
        nxt=None
        for c in sorted(pool, key=lambda x: x.get("odds",0), reverse=True):
            if c["fixture_id"] not in [l["fixture_id"] for l in legs]:
                nxt=c; break
        if not nxt: break
        legs.append(nxt)
    # If still <12.00 after 8 legs, SWAP lowest odds legs for higher odds legs
    tries=0
    while total(legs) < 12.00 and tries < 5:
        tries+=1
        lowest=sorted(legs, key=lambda x: x.get("odds", FALLBACK_ODDS[market]))[0]
        rep=None
        for c in sorted(pool, key=lambda x: x.get("odds",0), reverse=True):
            if c["fixture_id"] not in [l["fixture_id"] for l in legs] and c.get("odds",0) > lowest.get("odds",0):
                rep=c; break
        if not rep: break
        legs=[rep if l["fixture_id"]==lowest["fixture_id"] else l for l in legs]
    # FINAL: Even if still <12.00 (very rare, e.g. all odds are 1.10), we STILL return the card - NEVER return None
    # Card is populated with 5-8 legs, total is as high as possible. Frontend will show total + "Target 12.00+" and a note if below.
    legs=sort_status(legs)
    for l in legs: used.add(l["fixture_id"])
    tot=total(legs)
    return {"market": market, "legs": legs, "legs_count": len(legs), "total_odds": round(tot,2), "target": "12.00+", "meets_target": tot>=12.00, "status": "PENDING"}
def win_lose(market, hg, ag, fh_h=None, fh_a=None):
    if hg is None: return "PENDING"
    tot=hg+ag; btts=hg>=1 and ag>=1
    if market=="Over 1.5": return "WON" if tot>=2 else "LOST"
    if market=="Over 2.5": return "WON" if tot>=3 else "LOST"
    if market=="BTTS": return "WON" if btts else "LOST"
    if market=="Home Over 1.5": return "WON" if hg>=2 else "LOST"
    if market=="Away Over 1.5": return "WON" if ag>=2 else "LOST"
    if market=="Home Score Over 0.5": return "WON" if hg>=1 else "LOST"
    if market=="Away Score Over 0.5": return "WON" if ag>=1 else "LOST"
    if market=="First-Half Over 0.5": return "WON" if fh_h is not None and (fh_h+fh_a)>=1 else ("PENDING" if fh_h is None else "LOST")
    if market=="Home Win + Over 1.5": return "WON" if (hg>ag and tot>=2) else "LOST"
    if market=="Away Win + Over 1.5": return "WON" if (ag>hg and tot>=2) else "LOST"
    if market=="1X + BTTS": return "WON" if (hg>=ag and btts) else "LOST"
    if market=="X2 + BTTS": return "WON" if (ag>=hg and btts) else "LOST"
    return "PENDING"

# --- DAILY BUILD at 00:00 ---
def run_daily_build(date_str=None):
    today = date_str or datetime.utcnow().strftime("%Y-%m-%d")
    # STEP 1: Fetch fixtures for today (00:00, before Midnight games)
    data = af_get(f"/fixtures?date={today}")
    fixtures = data.get("response", [])
    if not fixtures:
        y = (datetime.utcnow()-timedelta(days=1)).strftime("%Y-%m-%d")
        fixtures = af_get(f"/fixtures?date={y}").get("response", [])
    # SAVE 1: Save matches
    match_rows=[]
    for fx in fixtures:
        try: match_rows.append({"date": today, "market": "fixture", "home": fx["teams"]["home"]["name"], "away": fx["teams"]["away"]["name"], "league": fx["league"]["name"], "kickoff": fx["fixture"]["date"], "odds": None, "result": fx["fixture"]["status"]["short"]})
        except: pass
    if match_rows: sb_post("saved_markets", match_rows)
    # NOTE: Full card build needs team last-10 stats - in production, loop fixtures, fetch /teams/statistics, compute team_stats, qualifies, build_card
    # The endpoint /api/build-cards below does that when frontend/admin sends matches with last10. The cron saves fixtures so history is never lost.

    # STEP 2: Fetch STANDINGS for leagues in today's fixtures - same 00:00 fetch, no extra day, save to saved_standings
    # We only fetch leagues that actually have games today, to save API calls (60/day cap)
    try:
        league_ids = {}
        for fx in fixtures:
            try: league_ids[fx["league"]["id"]] = fx["league"]["name"]
            except: pass
        standing_rows = []
        for lid, lname in list(league_ids.items())[:12]:
            sd = af_get(f"/standings?league={lid}&season=2026")
            resp = sd.get("response", [])
            if resp and resp[0].get("league", {}).get("standings"):
                table = resp[0]["league"]["standings"][0][:5]
                for row in table:
                    standing_rows.append({"date": today, "league": lname, "league_id": lid, "position": row["rank"], "team": row["team"]["name"], "played": row["all"]["played"], "points": row["points"]})
        if standing_rows: sb_post("saved_standings", standing_rows)
    except Exception as e:
        standing_rows = []
    # STEP 7: Rolling 10-day delete - delete matches AND cards older than 10 days
    cutoff = (datetime.utcnow()-timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    sb_delete_old("saved_markets", cutoff)
    sb_delete_old("saved_cards", cutoff)
    sb_delete_old("saved_standings", cutoff)
    return {"ok": True, "date": today, "fetch_time": "00:00", "fixtures_saved": len(match_rows), "kept_days": KEEP_DAYS, "deleted_before": cutoff, "midnight_populated": True, "standings_saved": len(standing_rows) if "standing_rows" in locals() else 0}

def _json(d, s=200):
    return {"statusCode": s, "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}, "body": json.dumps(d)}

def handler(event, context):
    path=event.get("path","/"); method=event.get("httpMethod","GET"); body=event.get("body") or ""; q=event.get("queryStringParameters") or {}
    if path=="/api/health": return _json({"ok": True, "fetch_time": "00:00", "keep_days": KEEP_DAYS, "supabase": bool(SUPABASE_URL)})
    if path=="/api/cron": return _json(run_daily_build())
    if path=="/api/build-cards" and method=="POST":
        # Build + SAVE accumulator cards for a date - called after fetch, or manually
        try: payload=json.loads(body) if body else {}
        except: payload={}
        date_str=payload.get("date") or datetime.utcnow().strftime("%Y-%m-%d")
        matches=payload.get("matches", [])
        used=set(); cards=[]
        for m in MARKETS_12:
            cands=[]
            for mt in matches:
                hs=team_stats(mt.get("home_last10",[])); aws=team_stats(mt.get("away_last10",[]))
                if qualifies(m, hs, aws):
                    cands.append({"fixture_id": mt.get("fixture_id"), "home": mt.get("home"), "away": mt.get("away"), "league": mt.get("league"), "kickoff": mt.get("kickoff"), "status": mt.get("status","Upcoming"), "odds": mt.get("odds", FALLBACK_ODDS[m]), "market": m})
            card=build_card(m, cands, used)
            if card: cards.append(card)
        # SAVE 2: Save accumulator CARDS (not just matches) - this is what history view shows
        for c in cards:
            sb_post("saved_cards", {"date": date_str, "market": c["market"], "legs": json.dumps(c["legs"]), "total_odds": c["total_odds"], "target": "12.00+", "result": "PENDING"})
        return _json({"ok": True, "date": date_str, "cards_built": len(cards), "cards": cards})
    if path=="/api/history":
        # Frontend history: GET /api/history?date=2026-11-25 -> returns THAT day's 12 cards from Supabase, no API call
        # GET /api/history?days=10 -> returns last 10 days cards
        date_str=q.get("date")
        if date_str:
            cards=sb_get("saved_cards", f"date=eq.{date_str}&order=created_at.asc")
            matches=sb_get("saved_markets", f"date=eq.{date_str}&order=created_at.asc")
            return _json({"date": date_str, "cards": cards, "matches": matches, "source": "supabase - no API call"})
        days=int(q.get("days", KEEP_DAYS))
        cutoff=(datetime.utcnow()-timedelta(days=days)).strftime("%Y-%m-%d")
        cards=sb_get("saved_cards", f"date=gte.{cutoff}&order=date.desc,created_at.asc&limit=500")
        return _json({"days": days, "cards": cards, "source": "supabase - no API call"})

    if path=="/api/today":
        # Frontend Today: reads ONLY Supabase, no API call. Returns today's cards + matches + standings together.
        date_str=q.get("date") or datetime.utcnow().strftime("%Y-%m-%d")
        cards=sb_get("saved_cards", f"date=eq.{date_str}&order=created_at.asc")
        matches=sb_get("saved_markets", f"date=eq.{date_str}&order=created_at.asc")
        standings=sb_get("saved_standings", f"date=eq.{date_str}&order=league.asc,position.asc")
        return _json({"date": date_str, "cards": cards, "matches": matches, "standings": standings, "source": "supabase - no API call"})
    if path=="/api/standings":
        date_str=q.get("date") or datetime.utcnow().strftime("%Y-%m-%d")
        standings=sb_get("saved_standings", f"date=eq.{date_str}&order=league.asc,position.asc")
        # Group by league: {league: [{position, team, played, points}]}
        grouped={}
        for r in standings:
            if isinstance(r, dict) and "league" in r: grouped.setdefault(r["league"], []).append({"position": r.get("position"), "team": r.get("team"), "played": r.get("played"), "points": r.get("points")})
        return _json({"date": date_str, "leagues": list(grouped.keys()), "standings": grouped, "source": "supabase - no API call"})
    if path=="/api/scores":
        # ONLY exception allowed to call API again: score updates
        date_str=q.get("date") or datetime.utcnow().strftime("%Y-%m-%d")
        data=af_get(f"/fixtures?date={date_str}&status=LIVE")
        live=data.get("response", [])
        return _json({"date": date_str, "live": len(live), "fixtures": live, "source": "api-football - score update only"})
    if path=="/api/saved-markets":
        if method=="POST":
            try: d=json.loads(body) if body else {}
            except: d={}
            return _json(sb_post("saved_markets", d))
        return _json(sb_get("saved_markets", "order=created_at.desc&limit=200"))
    if path=="/api/saved-cards":
        if method=="GET":
            return _json(sb_get("saved_cards", "order=date.desc,created_at.asc&limit=500"))
        return _json({"error": "use POST /api/build-cards"})
    if path=="/api/update-result" and method=="POST":
        try: d=json.loads(body) if body else {}
        except: d={}
        return _json({"market": d.get("market"), "result": win_lose(d.get("market",""), d.get("home_goals"), d.get("away_goals"), d.get("fh_home"), d.get("fh_away"))})
    try:
        base=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base,"index.html"),encoding="utf-8") as f: html=f.read()
        return {"statusCode":200,"headers":{"Content-Type":"text/html"},"body":html}
    except Exception as e: return _json({"error":str(e)},500)


# --- Flask wrapper for Vercel - MUST be at bottom, defines top-level app ---
try:
    from flask import Flask, request, Response
    app = Flask(__name__)
    @app.route("/api/<path:subpath>", methods=["GET","POST","PUT","DELETE"])
    @app.route("/api", methods=["GET","POST"])
    @app.route("/", methods=["GET"])
    def _flask_entry(subpath=""):
        import json as _json2
        path = "/" + ("api/"+subpath if subpath else "api" if request.path.startswith("/api") else "")
        if request.path == "/":
            path = "/"
        elif request.path.startswith("/api"):
            path = request.path
        else:
            path = request.path
        event = {"path": path, "httpMethod": request.method, "body": request.get_data(as_text=True) or "", "queryStringParameters": dict(request.args)}
        res = handler(event, None)
        body = res.get("body","")
        headers = res.get("headers",{})
        return Response(body, status=res.get("statusCode",200), content_type=headers.get("Content-Type","application/json"))
    # Vercel also looks for app at top level - it is defined above
except Exception as _e:
    # If Flask not available, keep handler for local use
    app = None
