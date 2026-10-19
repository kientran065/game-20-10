"""
Trò chơi "2/3 trung bình" - máy chủ Python (Flask).
Chạy:  pip install flask   rồi   python server.py
Mở:    http://localhost:5000  (người chơi cùng mạng LAN dùng http://<IP-máy-bạn>:5000)
"""
import random
import string
import threading
import time

from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder="static", static_url_path="/static")
LOCK = threading.Lock()
ROOMS = {}

WIN_POINTS = 100
MIN_NUM, MAX_NUM = 1, 99
BINS = [(1, 10), (11, 20), (21, 30), (31, 40), (41, 50),
        (51, 60), (61, 70), (71, 80), (81, 90), (91, 99)]


# ---------------------------------------------------------------- tiện ích
def new_code():
    while True:
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
        code = code.replace("O", "X").replace("0", "7").replace("I", "J").replace("1", "2")
        if code not in ROOMS:
            return code


def new_pid():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=12))


def clamp_int(value, lo, hi, default):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))


def clean_name(name):
    return (name or "").strip()[:20]


def err(message, status=400):
    return jsonify({"error": message}), status


def compute_result(room):
    """Tính kết quả vòng chơi. Người không điền số bị loại khỏi vòng này (0 điểm)."""
    picks = {pid: p["pick"] for pid, p in room["players"].items() if p["pick"] is not None}
    result = {"picks": {}, "target": None, "closest": [], "winners": [],
              "gain": {}, "histogram": [], "participants": len(picks)}

    result["histogram"] = [
        {"label": f"{lo}-{hi}",
         "count": sum(1 for v in picks.values() if lo <= v <= hi)}
        for lo, hi in BINS
    ]
    for pid, p in room["players"].items():
        result["picks"][pid] = p["pick"]
        result["gain"][pid] = 0

    if not picks:
        return result

    target = (2 / 3) * (sum(picks.values()) / len(picks))
    dist = {pid: abs(v - target) for pid, v in picks.items()}
    best = min(dist.values())
    winners = [pid for pid, d in dist.items() if abs(d - best) < 1e-9]
    share = round(WIN_POINTS / len(winners), 2)

    for pid in winners:
        room["players"][pid]["score"] = round(room["players"][pid]["score"] + share, 2)
        result["gain"][pid] = share

    result["target"] = round(target, 2)
    result["winners"] = winners
    result["closest"] = sorted({picks[pid] for pid in winners})
    return result


def finish_round(room):
    room["status"] = "results"
    room["result"] = compute_result(room)
    room["deadline"] = None


def tick(room):
    """Kiểm tra hết giờ / tất cả đã điền (gọi mỗi khi có truy vấn)."""
    if room["status"] != "playing":
        return
    all_done = all(p["pick"] is not None for p in room["players"].values())
    if time.time() >= room["deadline"] or (room["settings"]["early_end"] and all_done):
        finish_round(room)


def fmt(n):
    if n is None:
        return None
    return int(n) if float(n).is_integer() else n


def public_state(room, pid):
    tick(room)
    me = room["players"][pid]
    state = {
        "code": room["code"],
        "status": room["status"],
        "settings": room["settings"],
        "round": room["round"],
        "me": {"id": pid, "name": me["name"], "is_host": pid == room["host"],
               "pick": me["pick"]},
        "players": [
            {"id": i, "name": p["name"], "score": fmt(p["score"]),
             "is_host": i == room["host"],
             "submitted": p["pick"] is not None}
            for i, p in room["players"].items()
        ],
        "remaining": None,
    }
    if room["status"] == "playing":
        state["remaining"] = max(0, round(room["deadline"] - time.time(), 1))
    if room["status"] == "results":
        r = room["result"]
        names = {i: p["name"] for i, p in room["players"].items()}
        state["result"] = {
            "target": r["target"],
            "closest": r["closest"],
            "winners": [names[w] for w in r["winners"]],
            "winner_ids": r["winners"],
            "histogram": r["histogram"],
            "participants": r["participants"],
            "my_pick": r["picks"].get(pid),
            "gain": {i: fmt(g) for i, g in r["gain"].items()},
        }
    return state


def get_room_and_player(data):
    code = (data.get("code") or "").upper()
    pid = data.get("pid")
    room = ROOMS.get(code)
    if not room or pid not in room["players"]:
        return None, None
    return room, pid


# ---------------------------------------------------------------- API
@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.post("/api/create")
def create():
    data = request.get_json(force=True)
    name = clean_name(data.get("name"))
    if not name:
        return err("Hãy nhập tên của bạn.")
    with LOCK:
        code, pid = new_code(), new_pid()
        ROOMS[code] = {
            "code": code, "host": pid, "status": "lobby", "round": 0,
            "deadline": None, "result": None,
            "settings": {
                "max_players": clamp_int(data.get("max_players"), 2, 50, 10),
                "time_limit": clamp_int(data.get("time_limit"), 5, 600, 30),
                "early_end": bool(data.get("early_end", True)),
            },
            "players": {pid: {"name": name, "pick": None, "score": 0}},
        }
    return jsonify({"code": code, "pid": pid})


@app.post("/api/join")
def join():
    data = request.get_json(force=True)
    code = (data.get("code") or "").strip().upper()
    name = clean_name(data.get("name"))
    if not name:
        return err("Hãy nhập tên của bạn.")
    with LOCK:
        room = ROOMS.get(code)
        if not room:
            return err("Không tìm thấy phòng với mã này.", 404)
        if room["status"] != "lobby":
            return err("Phòng đã bắt đầu chơi, không thể vào thêm.")
        if len(room["players"]) >= room["settings"]["max_players"]:
            return err("Phòng đã đủ người.")
        if any(p["name"].lower() == name.lower() for p in room["players"].values()):
            return err("Tên này đã có người dùng trong phòng.")
        pid = new_pid()
        room["players"][pid] = {"name": name, "pick": None, "score": 0}
    return jsonify({"code": code, "pid": pid})


@app.get("/api/state")
def state():
    with LOCK:
        room = ROOMS.get((request.args.get("code") or "").upper())
        pid = request.args.get("pid")
        if not room or pid not in room["players"]:
            return err("Phòng không còn tồn tại.", 404)
        return jsonify(public_state(room, pid))


@app.post("/api/settings")
def settings():
    data = request.get_json(force=True)
    with LOCK:
        room, pid = get_room_and_player(data)
        if not room:
            return err("Phòng không tồn tại.", 404)
        if pid != room["host"]:
            return err("Chỉ quản trò mới chỉnh được cài đặt.", 403)
        if room["status"] == "playing":
            return err("Không thể đổi cài đặt khi đang chơi.")
        s = room["settings"]
        s["max_players"] = max(clamp_int(data.get("max_players"), 2, 50, s["max_players"]),
                               len(room["players"]))
        s["time_limit"] = clamp_int(data.get("time_limit"), 5, 600, s["time_limit"])
        s["early_end"] = bool(data.get("early_end", s["early_end"]))
    return jsonify({"ok": True})


def start_round(room):
    for p in room["players"].values():
        p["pick"] = None
    room["round"] += 1
    room["result"] = None
    room["status"] = "playing"
    room["deadline"] = time.time() + room["settings"]["time_limit"]


@app.post("/api/start")
def start():
    data = request.get_json(force=True)
    with LOCK:
        room, pid = get_room_and_player(data)
        if not room:
            return err("Phòng không tồn tại.", 404)
        if pid != room["host"]:
            return err("Chỉ quản trò mới được bắt đầu.", 403)
        if room["status"] == "playing":
            return err("Trò chơi đang diễn ra.")
        if len(room["players"]) < 2:
            return err("Cần ít nhất 2 người chơi để bắt đầu.")
        start_round(room)
    return jsonify({"ok": True})


@app.post("/api/submit")
def submit():
    data = request.get_json(force=True)
    with LOCK:
        room, pid = get_room_and_player(data)
        if not room:
            return err("Phòng không tồn tại.", 404)
        tick(room)
        if room["status"] != "playing":
            return err("Vòng chơi đã kết thúc.")
        try:
            number = int(data.get("number"))
        except (TypeError, ValueError):
            return err("Hãy nhập một số nguyên.")
        if not MIN_NUM <= number <= MAX_NUM:
            return err(f"Số phải nằm trong khoảng {MIN_NUM} đến {MAX_NUM}.")
        if room["players"][pid]["pick"] is not None:
            return err("Bạn đã chốt số rồi.")
        room["players"][pid]["pick"] = number
        tick(room)
    return jsonify({"ok": True})


@app.post("/api/restart")
def restart():
    """Chơi lại với đúng những người chơi hiện tại (điểm được cộng dồn)."""
    data = request.get_json(force=True)
    with LOCK:
        room, pid = get_room_and_player(data)
        if not room:
            return err("Phòng không tồn tại.", 404)
        if pid != room["host"]:
            return err("Chỉ quản trò mới được bắt đầu lại.", 403)
        if room["status"] != "results":
            return err("Chỉ có thể chơi lại sau khi vòng kết thúc.")
        start_round(room)
    return jsonify({"ok": True})


@app.post("/api/leave")
def leave():
    data = request.get_json(force=True)
    with LOCK:
        room, pid = get_room_and_player(data)
        if not room:
            return jsonify({"ok": True})
        if pid == room["host"] or len(room["players"]) == 1:
            ROOMS.pop(room["code"], None)  # quản trò rời -> đóng phòng
        else:
            del room["players"][pid]
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
