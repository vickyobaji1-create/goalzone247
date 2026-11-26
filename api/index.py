# Goalzone247 backend - Flask for Vercel
# Fetch at 00:00. Save matches + cards in Supabase. 10-day rolling. History from Supabase only, 0 API calls for history.
import os, json, urllib.request
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL","")
SUPABASE_ANON = os.environ.get("SUPABASE_ANON_KEY","")
SUPABASE_SERVICE = os.environ.get("SUPABASE_SERVICE_ROLE_KEY","") or SUPABASE_ANON
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY","")
HOST = "v3.football.api-sports.io"
KEEP_DAYS = 10

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
    cands=sort_status(candidates)
    pool=[c for c in cands if c["fixture_id"] not in used]+[c for c in cands if c["fixture_id"] in used]
    legs=[]
    for c in pool:
        if len(legs)>=5: break
        if any(l["fixture_id"]==c["fixture_id"] for l in legs): continue
        legs.append(c)
    def total(ls):
        t=1.0
        for x in ls: t*=x.get("odds", FALLBACK_ODDS[market])
        return t
    while total(legs) < 12.00 and len(legs) < 8:
        nxt=None
        for c in sorted(pool, key=lambda x: x.get("odds",0), reverse=True):
            if c["fixture_id"] not in [l["fixture_id"] for l in legs]:
                nxt=c; break
        if not nxt: break
        legs.append(nxt)
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

def run_daily_build(date_str=None):
    today = date_str or datetime.utcnow().strftime("%Y-%m-%d")
    data = af_get(f"/fixtures?date={today}")
    fixtures = data.get("response", [])
    if not fixtures:
        y = (datetime.utcnow()-timedelta(days=1)).strftime("%Y-%m-%d")
        fixtures = af_get(f"/fixtures?date={y}").get("response", [])
    match_rows=[]
    for fx in fixtures:
        try: match_rows.append({"date": today, "market": "fixture", "home": fx["teams"]["home"]["name"], "away": fx["teams"]["away"]["name"], "league": fx["league"]["name"], "kickoff": fx["fixture"]["date"], "odds": None, "result": fx["fixture"]["status"]["short"]})
        except: pass
    if match_rows: sb_post("saved_markets", match_rows)
    cutoff = (datetime.utcnow()-timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    sb_delete_old("saved_markets", cutoff)
    sb_delete_old("saved_cards", cutoff)
    return {"ok": True, "date": today, "fetch_time": "00:00", "fixtures_saved": len(match_rows), "kept_days": KEEP_DAYS, "deleted_before": cutoff, "midnight_populated": True}

@app.route("/api/health")
def health():
    return jsonify({"ok": True, "fetch_time": "00:00", "keep_days": KEEP_DAYS, "supabase": bool(SUPABASE_URL)})

@app.route("/api/cron")
def cron():
    return jsonify(run_daily_build())

@app.route("/api/build-cards", methods=["POST"])
def build_cards():
    payload=request.get_json(silent=True) or {}
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
    for c in cards:
        sb_post("saved_cards", {"date": date_str, "market": c["market"], "legs": json.dumps(c["legs"]), "total_odds": c["total_odds"], "target": "12.00+", "result": "PENDING"})
    return jsonify({"ok": True, "date": date_str, "cards_built": len(cards), "cards": cards})

@app.route("/api/history")
def history():
    date_str=request.args.get("date")
    if date_str:
        cards=sb_get("saved_cards", f"date=eq.{date_str}&order=created_at.asc")
        matches=sb_get("saved_markets", f"date=eq.{date_str}&order=created_at.asc")
        return jsonify({"date": date_str, "cards": cards, "matches": matches, "source": "supabase - no API call"})
    days=int(request.args.get("days", KEEP_DAYS))
    cutoff=(datetime.utcnow()-timedelta(days=days)).strftime("%Y-%m-%d")
    cards=sb_get("saved_cards", f"date=gte.{cutoff}&order=date.desc,created_at.asc&limit=500")
    return jsonify({"days": days, "cards": cards, "source": "supabase - no API call"})

@app.route("/api/saved-markets", methods=["GET","POST"])
def saved_markets():
    if request.method=="POST":
        return jsonify(sb_post("saved_markets", request.get_json(silent=True) or {}))
    return jsonify(sb_get("saved_markets", "order=created_at.desc&limit=200"))

@app.route("/api/saved-cards")
def saved_cards():
    return jsonify(sb_get("saved_cards", "order=date.desc,created_at.asc&limit=500"))

@app.route("/api/update-result", methods=["POST"])
def update_result():
    d=request.get_json(silent=True) or {}
    return jsonify({"market": d.get("market"), "result": win_lose(d.get("market",""), d.get("home_goals"), d.get("away_goals"), d.get("fh_home"), d.get("fh_away"))})

@app.route("/")
def home():
    base=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return send_from_directory(base, "index.html")

# This line is what Vercel needs - DO NOT REMOVE
# app is the Flask instance Vercel looks for