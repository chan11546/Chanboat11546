import csv, gzip, io, json, os, subprocess, sys, urllib.request
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
NOW = datetime.now(JST)
TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
MANUAL = os.environ.get("MANUAL_RUN", "false").lower() == "true"
STATE_FILE = Path("state.json")
CANDIDATES_FILE = Path("candidates.csv")
SUMMARY_FILE = Path("daily_summary.csv")
DATA_DIR = Path("data")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "boat-162-monitor/2.0"})
    with urllib.request.urlopen(req, timeout=45) as res:
        return res.read()


def notify(title, message, priority=4):
    if not TOPIC:
        raise RuntimeError("GitHub Secret NTFY_TOPIC is not configured")
    payload = {"topic": TOPIC, "title": title, "message": message,
               "priority": priority, "tags": ["boat", "test_tube"]}
    req = urllib.request.Request(
        "https://ntfy.sh",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.status


def value(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else default


def number(v):
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return 0.0


def urls_for(day_string):
    d = datetime.strptime(day_string, "%Y-%m-%d")
    ymd, yyyy, mm, dd = d.strftime("%Y%m%d"), d.strftime("%Y"), d.strftime("%m"), d.strftime("%d")
    api = "https://boatraceopenapi.github.io/api/v1/" + yyyy + "/" + ymd + ".json"
    odds = "https://boatracecsv.github.io/data/previews/od3/" + yyyy + "/" + mm + "/" + dd + ".csv"
    return ymd, api, odds


def load_day(day_string, cache):
    if day_string in cache:
        return cache[day_string]
    ymd, api_url, odds_url = urls_for(day_string)
    api_bytes = fetch(api_url)
    data = json.loads(api_bytes.decode("utf-8-sig"))
    try:
        odds_bytes = fetch(odds_url)
        odds_text = odds_bytes.decode("utf-8-sig")
        odds_rows = list(csv.DictReader(io.StringIO(odds_text)))
    except Exception:
        odds_bytes, odds_rows = b"", []
    odds_by = {row.get("レースコード", ""): row for row in odds_rows}
    cache[day_string] = (ymd, data, api_bytes, odds_bytes, odds_rows, odds_by)
    return cache[day_string]


def find_race(data, stadium_no, race_no):
    stadiums = value(value(data, "programs", {}), "stadiums", {})
    stadium = value(stadiums, str(stadium_no), {})
    races = value(stadium, "races", {})
    for race in races.values():
        if int(value(race, "race_number", 0) or 0) == int(race_no):
            return race
    return None


if MANUAL:
    notify("1-6-2監視テスト", "通知・候補記録・結果収集の設定は正常です。")
    print("Test notification sent")

minutes = NOW.hour * 60 + NOW.minute
if not (7 * 60 + 50 <= minutes <= 22 * 60):
    print("Outside operation hours (07:50-22:00 JST)")
    sys.exit(0)

try:
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
except Exception:
    state = {"seen": [], "summary_sent": [], "archived": []}
seen = set(state.get("seen", []))
summary_sent = set(state.get("summary_sent", []))
archived = set(state.get("archived", []))

fields = ["Key", "Date", "DetectedAt", "Stadium", "StadiumNo", "Race", "Close",
          "Odds162", "B6ExRank", "Result", "Payout", "Hit", "Profit", "SettledAt"]
candidates = []
if CANDIDATES_FILE.exists():
    with CANDIDATES_FILE.open(encoding="utf-8-sig", newline="") as f:
        candidates = list(csv.DictReader(f))

cache = {}
today = NOW.strftime("%Y-%m-%d")
ymd, data, api_bytes, odds_bytes, odds_rows, odds_by = load_day(today, cache)
stadium_names = {
    "1":"桐生","2":"戸田","3":"江戸川","4":"平和島","5":"多摩川","6":"浜名湖",
    "7":"蒲郡","8":"常滑","9":"津","10":"三国","11":"びわこ","12":"住之江",
    "13":"尼崎","14":"鳴門","15":"丸亀","16":"児島","17":"宮島","18":"徳山",
    "19":"下関","20":"若松","21":"芦屋","22":"福岡","23":"唐津","24":"大村"
}

# Detect new candidates only while races are running.
if minutes <= 21 * 60 + 30:
    stadiums = value(value(data, "programs", {}), "stadiums", {})
    for sid, stadium in stadiums.items():
        for race in value(stadium, "races", {}).values():
            rno = int(value(race, "race_number", 0) or 0)
            preview_racers = value(value(race, "preview", {}), "racers", {})
            if not preview_racers:
                continue
            times = []
            for boat in range(1, 7):
                ex_time = number(value(value(preview_racers, str(boat), {}), "exhibition_time"))
                if ex_time > 0:
                    times.append((ex_time, boat))
            if len(times) != 6:
                continue
            times.sort()
            rank6 = next(i + 1 for i, (_, boat) in enumerate(times) if boat == 6)
            code = f"{ymd}{int(sid):02d}{rno:02d}"
            odds162 = number(odds_by.get(code, {}).get("3連単_1-6-2"))
            if not (rank6 <= 2 and 40.0 <= odds162 < 150.0):
                continue
            close_raw = str(value(race, "closed_at", ""))
            try:
                close_time = datetime.fromisoformat(close_raw.replace("Z", "+00:00"))
                if close_time.tzinfo is None:
                    close_time = close_time.replace(tzinfo=JST)
                if close_time.astimezone(JST) <= NOW:
                    continue
                close_display = close_time.astimezone(JST).strftime("%H:%M")
            except Exception:
                close_display = close_raw
            key = f"{ymd}-{sid}-{rno}"
            if key in seen:
                continue
            stadium_name = stadium_names.get(str(sid), str(sid))
            notify("1-6-2 紙上候補",
                   f"{stadium_name} {rno}R\n締切 {close_display}\n1-6-2：{odds162:.1f}倍\n"
                   f"6号艇展示：{rank6}位\n紙上検証候補")
            seen.add(key)
            candidates.append({
                "Key": key, "Date": today, "DetectedAt": NOW.strftime("%Y-%m-%d %H:%M:%S"),
                "Stadium": stadium_name, "StadiumNo": str(sid), "Race": str(rno),
                "Close": close_display, "Odds162": f"{odds162:.1f}", "B6ExRank": str(rank6),
                "Result": "", "Payout": "", "Hit": "", "Profit": "", "SettledAt": ""
            })
            print("Candidate notified:", key)

# Settle every pending candidate, including candidates from previous days.
for row in candidates:
    if row.get("SettledAt"):
        continue
    try:
        _, day_data, _, _, _, _ = load_day(row["Date"], cache)
        race = find_race(day_data, row["StadiumNo"], row["Race"])
        result = value(race, "result", {})
        payouts = value(value(result, "payouts", {}), "trifecta", []) or []
        if not payouts:
            continue
        combo = str(value(payouts[0], "combination", ""))
        amount = int(number(value(payouts[0], "amount", 0)))
        hit = combo == "1-6-2"
        row["Result"] = combo
        row["Payout"] = str(amount if hit else 0)
        row["Hit"] = "True" if hit else "False"
        row["Profit"] = str((amount if hit else 0) - 100)
        row["SettledAt"] = NOW.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as exc:
        print("Settlement pending:", row.get("Key"), exc)

# Always rewrite the candidate ledger so results accumulate automatically.
with CANDIDATES_FILE.open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    for row in candidates:
        writer.writerow({key: row.get(key, "") for key in fields})

# Archive complete raw source data and save one daily summary for the PC report.
# No result notification is sent to the iPhone.
if minutes >= 21 * 60 + 30:
    DATA_DIR.mkdir(exist_ok=True)
    result_count = 0
    race_count = 0
    for stadium in value(value(data, "programs", {}), "stadiums", {}).values():
        for race in value(stadium, "races", {}).values():
            race_count += 1
            payouts = value(value(value(race, "result", {}), "payouts", {}), "trifecta", []) or []
            if payouts:
                result_count += 1
    if today not in archived and race_count and result_count / race_count >= 0.90 and odds_bytes:
        (DATA_DIR / f"{ymd}.json.gz").write_bytes(gzip.compress(api_bytes))
        (DATA_DIR / f"{ymd}-odds.csv.gz").write_bytes(gzip.compress(odds_bytes))
        archived.add(today)
        print("Raw daily data archived:", today)
    today_rows = [r for r in candidates if r.get("Date") == today and r.get("SettledAt")]
    pending_today = [r for r in candidates if r.get("Date") == today and not r.get("SettledAt")]
    if today not in summary_sent and (not pending_today or minutes >= 21 * 60 + 55):
        bets = len(today_rows)
        hits = sum(1 for r in today_rows if r.get("Hit") == "True")
        returned = sum(int(number(r.get("Payout"))) for r in today_rows)
        invested = bets * 100
        profit = returned - invested
        roi = (returned / invested * 100) if invested else 0.0
        summary_fields = ["Date", "Bets", "Hits", "Investment", "Return", "Profit", "ROI"]
        summaries = []
        if SUMMARY_FILE.exists():
            with SUMMARY_FILE.open(encoding="utf-8-sig", newline="") as f:
                summaries = [r for r in csv.DictReader(f) if r.get("Date") != today]
        summaries.append({"Date": today, "Bets": bets, "Hits": hits,
                          "Investment": invested, "Return": returned,
                          "Profit": profit, "ROI": f"{roi:.2f}"})
        summaries.sort(key=lambda r: r["Date"])
        with SUMMARY_FILE.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=summary_fields)
            writer.writeheader()
            writer.writerows(summaries)
        summary_sent.add(today)
        print("Daily PC summary saved:", today)

state["seen"] = sorted(seen)
state["summary_sent"] = sorted(summary_sent)
state["archived"] = sorted(archived)
STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

# Commit all accumulated data here as well, so upgrading monitor.py alone is sufficient.
if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
    subprocess.run(["git", "config", "user.name", "boat-monitor-bot"], check=False)
    subprocess.run(["git", "config", "user.email", "actions@users.noreply.github.com"], check=False)
    subprocess.run(["git", "add", "state.json", "candidates.csv", "daily_summary.csv", "data"], check=False)
    changed = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False).returncode != 0
    if changed:
        subprocess.run(["git", "commit", "-m", "Update candidate results and daily data"], check=True)
        subprocess.run(["git", "push"], check=True)

print("Finished. Candidates:", len(candidates))
