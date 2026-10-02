import csv, io, json, os, sys, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
NOW = datetime.now(JST)
TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
MANUAL = os.environ.get("MANUAL_RUN", "false").lower() == "true"
STATE_FILE = Path("state.json")
HISTORY_FILE = Path("candidates.csv")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "boat-162-monitor/1.0"})
    with urllib.request.urlopen(req, timeout=40) as res:
        return res.read()


def notify(title, message):
    if not TOPIC:
        raise RuntimeError("GitHub Secret NTFY_TOPIC is not configured")
    body = json.dumps({"topic": TOPIC, "title": title, "message": message,
                       "priority": 4, "tags": ["boat", "test_tube"]}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("https://ntfy.sh", data=body,
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


if MANUAL:
    notify("1-6-2監視テスト", "GitHubからiPhoneへの通知設定は正常です。")
    print("Test notification sent")

# Scheduled runs outside the monitoring window do nothing.
minutes = NOW.hour * 60 + NOW.minute
if not (7 * 60 + 50 <= minutes <= 21 * 60 + 30):
    print("Outside monitoring hours (07:50-21:30 JST)")
    sys.exit(0)

ymd = NOW.strftime("%Y%m%d")
yyyy, mm, dd = NOW.strftime("%Y"), NOW.strftime("%m"), NOW.strftime("%d")
api_url = "https://boatraceopenapi.github.io/api/v1/" + yyyy + "/" + ymd + ".json"
odds_url = "https://boatracecsv.github.io/data/previews/od3/" + yyyy + "/" + mm + "/" + dd + ".csv"

data = json.loads(fetch(api_url).decode("utf-8-sig"))
odds_text = fetch(odds_url).decode("utf-8-sig")
odds_by_code = {row.get("レースコード", ""): row for row in csv.DictReader(io.StringIO(odds_text))}

try:
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
except Exception:
    state = {"seen": []}
seen = set(state.get("seen", []))
new_rows = []
stadium_names = {
    "1":"桐生","2":"戸田","3":"江戸川","4":"平和島","5":"多摩川","6":"浜名湖",
    "7":"蒲郡","8":"常滑","9":"津","10":"三国","11":"びわこ","12":"住之江",
    "13":"尼崎","14":"鳴門","15":"丸亀","16":"児島","17":"宮島","18":"徳山",
    "19":"下関","20":"若松","21":"芦屋","22":"福岡","23":"唐津","24":"大村"
}

stadiums = value(value(data, "programs", {}), "stadiums", {})
for sid, stadium in stadiums.items():
    races = value(stadium, "races", {})
    for _, race in races.items():
        rno = int(value(race, "race_number", 0) or 0)
        preview = value(race, "preview", {})
        preview_racers = value(preview, "racers", {})
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
        odds_row = odds_by_code.get(code, {})
        odds162 = number(odds_row.get("3連単_1-6-2"))
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
        message = (f"{stadium_name} {rno}R\n締切 {close_display}\n"
                   f"1-6-2：{odds162:.1f}倍\n6号艇展示：{rank6}位\n紙上検証候補")
        notify("1-6-2 紙上候補", message)
        seen.add(key)
        new_rows.append([key, NOW.strftime("%Y-%m-%d %H:%M:%S"), stadium_name,
                         rno, close_display, f"{odds162:.1f}", rank6])
        print("Notified:", key, odds162, rank6)

state["seen"] = sorted(seen)
STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
if new_rows:
    exists = HISTORY_FILE.exists()
    with HISTORY_FILE.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(["Key", "DetectedAt", "Stadium", "Race", "Close", "Odds162", "B6ExRank"])
        writer.writerows(new_rows)
print(f"Finished. New candidates: {len(new_rows)}")
