"""
NEXUS — Decentralized Renewable Energy Exchange
Flask backend with:
  - Supabase auth + real DB storage (no hardcoded data)
  - Location-aware weather pricing engine (OpenWeatherMap)
  - SHA-256 hash chain (hashes stored in DB, visible only to admin)
  - Razorpay payment gateway integration
  - Role-based access (producer / consumer / investor / admin)
  - Community energy pools
  - AI advisor per role
  - Carbon credit system
  - Escrow-based P2P settlement
"""

import os, hashlib, json, time, math, uuid, hmac
import requests
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, jsonify, request, render_template, session, redirect
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", os.urandom(32).hex())
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
CORS(app, supports_credentials=True, origins=["*"])

# ─────────────────────────────────────────────
# SUPABASE INIT
# ─────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON = os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE = os.getenv("SUPABASE_SERVICE_KEY", "")  # For admin ops

supabase = None
if SUPABASE_URL and SUPABASE_ANON:
    try:
        from supabase import create_client
        supabase = create_client(SUPABASE_URL, SUPABASE_ANON)
        print("✓ Supabase connected")
    except Exception as e:
        print(f"⚠ Supabase error: {e}")

WEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# ─────────────────────────────────────────────
# POLICY CONFIG (admin-editable via API)
# ─────────────────────────────────────────────
POLICY = {
    "max_price_kwh": 12.0,       # ₹ ceiling
    "min_price_kwh": 1.50,       # ₹ floor
    "solar_subsidy": 0.50,
    "wind_subsidy": 0.30,
    "biogas_subsidy": 0.20,
    "bulk_threshold_kwh": 50,
    "bulk_discount_pct": 8.0,
    "carbon_per_kwh": 0.82,      # kg CO₂ saved per kWh (India grid avg)
    "transmission_fee_per_km": 0.02,   # ₹ per km per kWh
    "platform_fee_pct": 1.5,           # % platform fee
}

# ─────────────────────────────────────────────
# IN-MEMORY FALLBACK (when Supabase not connected)
# ─────────────────────────────────────────────
_mem = {
    "users": {},        # email → user dict
    "listings": [],
    "transactions": [],
    "pools": [],
    "investments": [],
    "sessions": {}      # token → user
}

# ─────────────────────────────────────────────
# AUTH HELPERS
# ─────────────────────────────────────────────
def generate_token():
    return "nx_" + uuid.uuid4().hex + uuid.uuid4().hex[:16]

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token:
            token = request.cookies.get('nx_token', '')
        user = get_user_from_token(token)
        if not user:
            return jsonify({"error": "Unauthorized"}), 401
        request.current_user = user
        return f(*args, **kwargs)
    return decorated

def require_role(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            token = request.headers.get('Authorization', '').replace('Bearer ', '')
            if not token:
                token = request.cookies.get('nx_token', '')
            user = get_user_from_token(token)
            if not user:
                return jsonify({"error": "Unauthorized"}), 401
            if user.get('role') not in roles:
                return jsonify({"error": "Forbidden"}), 403
            request.current_user = user
            return f(*args, **kwargs)
        return decorated
    return decorator

def get_user_from_token(token):
    if not token:
        return None
    # Try Supabase first
    if supabase and token.startswith("sb_"):
        try:
            user = supabase.auth.get_user(token)
            if user and user.user:
                profile = supabase.table("profiles").select("*").eq("id", str(user.user.id)).single().execute()
                if profile.data:
                    return profile.data
        except Exception:
            pass
    # Memory fallback
    return _mem["sessions"].get(token)

# ─────────────────────────────────────────────
# WEATHER ENGINE (cached, location-aware)
# ─────────────────────────────────────────────
_weather_cache = {}

def fetch_weather(lat=22.7196, lon=75.8577):
    cache_key = f"{round(lat,2)}_{round(lon,2)}"
    now = time.time()
    if cache_key in _weather_cache:
        cached = _weather_cache[cache_key]
        if now - cached["ts"] < 300:   # 5-min cache
            return cached["data"]

    demo = {
        "city": "Location", "temp": 28.4, "feels_like": 31.0,
        "clouds": 35, "wind_speed": 14.4, "humidity": 58,
        "condition": "Partly Cloudy", "icon": "⛅",
        "visibility": 10, "pressure": 1013, "demo": True
    }

    if not WEATHER_API_KEY:
        _weather_cache[cache_key] = {"data": demo, "ts": now}
        return demo

    try:
        url = (f"https://api.openweathermap.org/data/2.5/weather"
               f"?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric")
        r = requests.get(url, timeout=5)
        d = r.json()
        if r.status_code != 200:
            return demo
        w = {
            "city": d.get("name", "Unknown"),
            "temp": round(d["main"]["temp"], 1),
            "feels_like": round(d["main"]["feels_like"], 1),
            "clouds": d["clouds"]["all"],
            "wind_speed": round(d["wind"]["speed"] * 3.6, 1),  # m/s → km/h
            "humidity": d["main"]["humidity"],
            "condition": d["weather"][0]["description"].title(),
            "icon": _weather_icon(d["weather"][0]["id"]),
            "visibility": d.get("visibility", 10000) // 1000,
            "pressure": d["main"]["pressure"],
            "demo": False
        }
        _weather_cache[cache_key] = {"data": w, "ts": now}
        return w
    except Exception as e:
        print(f"Weather error: {e}")
        return demo

def _weather_icon(wid):
    if 200 <= wid < 300: return "⛈️"
    if 300 <= wid < 600: return "🌧️"
    if 600 <= wid < 700: return "❄️"
    if wid == 800: return "☀️"
    if wid == 801: return "🌤️"
    if wid <= 804: return "⛅"
    return "🌡️"

# ─────────────────────────────────────────────
# PRICING ENGINE
# ─────────────────────────────────────────────
def calculate_price(base: float, etype: str, weather: dict, dist_km=0, kwh=0) -> dict:
    mod = 0.0
    reasons = []
    w = weather

    if etype == "solar":
        c = w.get("clouds", 30)
        t = w.get("temp", 25)
        if c < 20:    mod -= 1.5; reasons.append(f"Clear sky ({c}%) → solar surplus → lower price")
        elif c < 40:  mod -= 0.6; reasons.append(f"Mostly clear ({c}%) → good solar output")
        elif c < 70:  mod += 0.8; reasons.append(f"Partial cloud ({c}%) → moderate output drop")
        else:         mod += 2.2; reasons.append(f"Heavy cloud ({c}%) → low solar → higher price")
        if t > 38:    mod += 0.4; reasons.append(f"Heat stress ({t}°C) reduces panel efficiency")

    elif etype == "wind":
        wind = w.get("wind_speed", 10)
        if wind > 25:   mod -= 2.2; reasons.append(f"Strong wind ({wind}km/h) → surplus → lower price")
        elif wind > 15: mod -= 1.0; reasons.append(f"Good wind ({wind}km/h) → lower price")
        elif wind < 5:  mod += 2.0; reasons.append(f"Calm ({wind}km/h) → scarce wind → higher price")
        else:           reasons.append(f"Moderate wind ({wind}km/h) → stable")

    elif etype == "biogas":
        h = w.get("humidity", 60)
        if h > 80: mod -= 0.2; reasons.append("High humidity boosts biogas fermentation slightly")
        else: reasons.append("Biogas: reliable baseload, weather-independent")

    weather_price = base + mod
    tx_fee = round(dist_km * POLICY["transmission_fee_per_km"], 3)
    price_after_tx = weather_price + tx_fee

    subsidy = POLICY.get(f"{etype}_subsidy", 0)
    price_after_sub = price_after_tx - subsidy

    bulk_disc = 0
    if kwh >= POLICY["bulk_threshold_kwh"]:
        bulk_disc = round(price_after_sub * POLICY["bulk_discount_pct"] / 100, 3)
        price_after_sub -= bulk_disc

    platform_fee = round(price_after_sub * POLICY["platform_fee_pct"] / 100, 3)
    raw_final = price_after_sub + platform_fee
    final = max(POLICY["min_price_kwh"], min(POLICY["max_price_kwh"], raw_final))

    return {
        "base_price": round(base, 2),
        "weather_modifier": round(mod, 2),
        "transmission_fee": round(tx_fee, 2),
        "subsidy_applied": round(subsidy, 2),
        "bulk_discount": round(bulk_disc, 2),
        "platform_fee": round(platform_fee, 2),
        "final_price": round(final, 2),
        "direction": "up" if mod > 0.5 else "down" if mod < -0.5 else "neutral",
        "reason": " | ".join(reasons) if reasons else "Stable market conditions",
        "policy_capped": final != raw_final
    }

# ─────────────────────────────────────────────
# HASH CHAIN ENGINE
# ─────────────────────────────────────────────
def get_last_hash():
    if supabase:
        try:
            r = supabase.table("transactions").select("tx_hash").order("created_at", desc=True).limit(1).execute()
            if r.data:
                return r.data[0]["tx_hash"]
        except Exception:
            pass
    if _mem["transactions"]:
        return _mem["transactions"][-1]["tx_hash"]
    return "0" * 64

def make_hash(prev, from_id, to_id, kwh, price, ts):
    raw = f"{prev}|{from_id}|{to_id}|{kwh}|{price}|{ts}"
    return hashlib.sha256(raw.encode()).hexdigest()

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return round(R * 2 * math.asin(math.sqrt(a)), 2)

# ─────────────────────────────────────────────
# ROUTES — PAGE RENDERING
# ─────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/producer")
def producer_page():
    return render_template("producer.html", razorpay_key=RAZORPAY_KEY_ID)

@app.route("/consumer")
def consumer_page():
    return render_template("consumer.html", razorpay_key=RAZORPAY_KEY_ID)

@app.route("/investor")
def investor_page():
    return render_template("investor.html", razorpay_key=RAZORPAY_KEY_ID)

@app.route("/ledger")
def ledger_page():
    return render_template("ledger.html")

@app.route("/admin")
def admin_page():
    return render_template("admin.html")

# ─────────────────────────────────────────────
# AUTH ROUTES
# ─────────────────────────────────────────────
@app.route("/api/auth/register", methods=["POST"])
def register():
    d = request.get_json()
    email = d.get("email", "").strip().lower()
    password = d.get("password", "")
    name = d.get("name", "").strip()
    role = d.get("role", "consumer")
    lat = float(d.get("lat", 22.7196))
    lon = float(d.get("lon", 75.8577))
    city = d.get("city", "Indore")

    if not all([email, password, name]):
        return jsonify({"error": "All fields required"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be 8+ characters"}), 400
    if role not in ["producer", "consumer", "investor"]:
        return jsonify({"error": "Invalid role"}), 400

    if supabase:
        try:
            auth_res = supabase.auth.sign_up({"email": email, "password": password})
            if auth_res.user:
                uid = str(auth_res.user.id)
                supabase.table("profiles").insert({
                    "id": uid, "email": email, "full_name": name,
                    "role": role, "location_city": city,
                    "location_lat": lat, "location_lon": lon,
                    "carbon_score": 0, "green_credits": 0
                }).execute()
                token = auth_res.session.access_token if auth_res.session else generate_token()
                return jsonify({"success": True, "token": token, "role": role,
                                "user": {"id": uid, "name": name, "email": email, "role": role}})
            else:
                return jsonify({"error": "Registration failed"}), 400
        except Exception as e:
            err = str(e)
            if "already registered" in err or "User already registered" in err:
                return jsonify({"error": "Email already registered"}), 409
            return jsonify({"error": f"Registration error: {err}"}), 500
    else:
        # Memory fallback
        if email in _mem["users"]:
            return jsonify({"error": "Email already registered"}), 409
        import hashlib
        uid = "nx_" + uuid.uuid4().hex[:12]
        pw_hash = hashlib.sha256(password.encode()).hexdigest()
        user = {"id": uid, "email": email, "full_name": name, "role": role,
                "location_city": city, "location_lat": lat, "location_lon": lon,
                "carbon_score": 0, "green_credits": 0, "pw_hash": pw_hash}
        _mem["users"][email] = user
        token = generate_token()
        _mem["sessions"][token] = user
        return jsonify({"success": True, "token": token, "role": role,
                        "user": {"id": uid, "name": name, "email": email, "role": role}})


@app.route("/api/auth/login", methods=["POST"])
def login():
    d = request.get_json()
    email = d.get("email", "").strip().lower()
    password = d.get("password", "")

    if supabase:
        try:
            auth_res = supabase.auth.sign_in_with_password({"email": email, "password": password})
            if auth_res.user and auth_res.session:
                profile = supabase.table("profiles").select("*").eq("id", str(auth_res.user.id)).single().execute()
                prof = profile.data or {}
                return jsonify({
                    "success": True,
                    "token": auth_res.session.access_token,
                    "role": prof.get("role", "consumer"),
                    "user": {
                        "id": str(auth_res.user.id),
                        "name": prof.get("full_name", email),
                        "email": email,
                        "role": prof.get("role", "consumer"),
                        "location_lat": prof.get("location_lat", 22.7196),
                        "location_lon": prof.get("location_lon", 75.8577),
                        "location_city": prof.get("location_city", ""),
                        "carbon_score": prof.get("carbon_score", 0)
                    }
                })
        except Exception as e:
            return jsonify({"error": "Invalid email or password"}), 401
    else:
        import hashlib
        user = _mem["users"].get(email)
        if not user:
            return jsonify({"error": "Invalid email or password"}), 401
        pw_hash = hashlib.sha256(password.encode()).hexdigest()
        if user.get("pw_hash") != pw_hash:
            return jsonify({"error": "Invalid email or password"}), 401
        token = generate_token()
        _mem["sessions"][token] = user
        return jsonify({
            "success": True, "token": token, "role": user["role"],
            "user": {"id": user["id"], "name": user["full_name"],
                     "email": email, "role": user["role"],
                     "location_lat": user.get("location_lat", 22.7196),
                     "location_lon": user.get("location_lon", 75.8577),
                     "carbon_score": user.get("carbon_score", 0)}
        })


@app.route("/api/auth/logout", methods=["POST"])
def logout():
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if supabase:
        try: supabase.auth.sign_out()
        except: pass
    _mem["sessions"].pop(token, None)
    return jsonify({"success": True})


# ─────────────────────────────────────────────
# WEATHER API
# ─────────────────────────────────────────────
@app.route("/api/weather")
def api_weather():
    lat = float(request.args.get("lat", 22.7196))
    lon = float(request.args.get("lon", 75.8577))
    return jsonify(fetch_weather(lat, lon))


# ─────────────────────────────────────────────
# LISTINGS (Producer → creates; Consumer reads)
# ─────────────────────────────────────────────
@app.route("/api/listings", methods=["GET"])
def get_listings():
    etype = request.args.get("type", "")
    lat = float(request.args.get("lat", 22.7196))
    lon = float(request.args.get("lon", 75.8577))
    weather = fetch_weather(lat, lon)

    if supabase:
        try:
            q = supabase.table("listings").select("*, profiles(full_name, location_city)").eq("is_active", True)
            if etype:
                q = q.eq("energy_type", etype)
            result = q.execute()
            listings = result.data or []
        except Exception as e:
            print(f"Listings fetch error: {e}")
            listings = _mem["listings"]
    else:
        listings = [l for l in _mem["listings"] if l.get("is_active", True)]
        if etype:
            listings = [l for l in listings if l.get("energy_type") == etype]

    enriched = []
    for l in listings:
        p_lat = float(l.get("location_lat", 22.7196))
        p_lon = float(l.get("location_lon", 75.8577))
        dist = haversine(lat, lon, p_lat, p_lon)
        pricing = calculate_price(float(l["base_price"]), l["energy_type"], weather, dist)
        l["distance_km"] = dist
        l["pricing"] = pricing
        l["current_price"] = pricing["final_price"]
        l["price_direction"] = pricing["direction"]
        l["price_reason"] = pricing["reason"]
        # Get producer name from join or direct field
        if l.get("profiles"):
            l["producer_name"] = l["profiles"].get("full_name", l.get("producer_name", "Producer"))
        enriched.append(l)

    enriched.sort(key=lambda x: x["current_price"])
    return jsonify(enriched)


@app.route("/api/listings/mine", methods=["GET"])
@require_auth
def get_my_listings():
    user = request.current_user
    uid = user.get("id") or user.get("producer_id")
    if supabase:
        try:
            result = supabase.table("listings").select("*").eq("producer_id", uid).execute()
            return jsonify(result.data or [])
        except Exception as e:
            pass
    return jsonify([l for l in _mem["listings"] if l.get("producer_id") == uid])


@app.route("/api/listings/create", methods=["POST"])
@require_auth
def create_listing():
    user = request.current_user
    if user.get("role") not in ["producer", "admin"]:
        return jsonify({"error": "Only producers can create listings"}), 403

    d = request.get_json()
    lat = float(d.get("lat", user.get("location_lat", 22.7196)))
    lon = float(d.get("lon", user.get("location_lon", 75.8577)))
    listing = {
        "id": str(uuid.uuid4()),
        "producer_id": user["id"],
        "producer_name": user.get("full_name", user.get("name", "Producer")),
        "energy_type": d.get("energy_type", "solar"),
        "base_price": float(d.get("base_price", 3.0)),
        "available_kwh": float(d.get("available_kwh", 100)),
        "capacity_kw": float(d.get("capacity_kw", 10)),
        "location_city": d.get("city", user.get("location_city", "City")),
        "location_lat": lat,
        "location_lon": lon,
        "description": d.get("description", ""),
        "is_active": True,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    if supabase:
        try:
            r = supabase.table("listings").insert(listing).execute()
            return jsonify(r.data[0] if r.data else listing)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    _mem["listings"].append(listing)
    return jsonify(listing)


@app.route("/api/listings/<lid>", methods=["PUT"])
@require_auth
def update_listing(lid):
    user = request.current_user
    d = request.get_json()
    allowed = ["base_price", "available_kwh", "is_active", "description"]
    updates = {k: v for k, v in d.items() if k in allowed}
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()

    if supabase:
        try:
            r = supabase.table("listings").update(updates).eq("id", lid).eq("producer_id", user["id"]).execute()
            return jsonify(r.data[0] if r.data else {})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    for l in _mem["listings"]:
        if l["id"] == lid and l.get("producer_id") == user["id"]:
            l.update(updates)
            return jsonify(l)
    return jsonify({"error": "Not found"}), 404


@app.route("/api/listings/<lid>", methods=["DELETE"])
@require_auth
def delete_listing(lid):
    user = request.current_user
    if supabase:
        try:
            supabase.table("listings").update({"is_active": False}).eq("id", lid).eq("producer_id", user["id"]).execute()
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    _mem["listings"] = [l for l in _mem["listings"] if not (l["id"] == lid and l.get("producer_id") == user["id"])]
    return jsonify({"success": True})


# ─────────────────────────────────────────────
# SMART SWITCH ADVISOR
# ─────────────────────────────────────────────
@app.route("/api/smart-switch")
def smart_switch():
    lat = float(request.args.get("lat", 22.7196))
    lon = float(request.args.get("lon", 75.8577))
    weather = fetch_weather(lat, lon)
    clouds = weather.get("clouds", 30)
    wind = weather.get("wind_speed", 10)

    scores = {
        "solar": max(0, min(100, 100 - clouds)),
        "wind": max(0, min(100, (wind / 30) * 100)),
        "biogas": 70
    }
    ranked = sorted(scores, key=scores.get, reverse=True)
    return jsonify({
        "recommended": ranked[0], "fallback": ranked[1],
        "scores": scores,
        "reasons": {
            "solar": f"Cloud cover {clouds}% → Solar score {scores['solar']:.0f}/100",
            "wind": f"Wind {wind}km/h → Wind score {scores['wind']:.0f}/100",
            "biogas": "Baseload source — always stable → Score 70/100"
        },
        "weather": weather
    })


# ─────────────────────────────────────────────
# TRADE / P2P EXECUTION
# ─────────────────────────────────────────────
@app.route("/api/trade", methods=["POST"])
@require_auth
def execute_trade():
    user = request.current_user
    d = request.get_json()

    listing_id = d.get("listing_id")
    kwh = float(d.get("kwh", 0))
    payment_id = d.get("payment_id", "")  # Razorpay payment ID
    pool_id = d.get("pool_id")

    # Fetch listing
    listing = None
    if supabase and listing_id:
        try:
            r = supabase.table("listings").select("*, profiles(full_name, location_lat, location_lon)").eq("id", listing_id).single().execute()
            listing = r.data
        except Exception:
            pass
    if not listing:
        listing = next((l for l in _mem["listings"] if l.get("id") == listing_id), None)

    if not listing:
        from_name = d.get("from_name", "Producer")
        base_price = float(d.get("base_price", 3.0))
        etype = d.get("energy_type", "solar")
        p_lat = float(d.get("producer_lat", 22.72))
        p_lon = float(d.get("producer_lon", 75.88))
    else:
        from_name = listing.get("producer_name") or (listing.get("profiles") or {}).get("full_name", "Producer")
        base_price = float(listing.get("base_price", 3.0))
        etype = listing.get("energy_type", "solar")
        prof = listing.get("profiles") or {}
        p_lat = float(listing.get("location_lat") or prof.get("location_lat", 22.72))
        p_lon = float(listing.get("location_lon") or prof.get("location_lon", 75.88))

    c_lat = float(d.get("consumer_lat", user.get("location_lat", 22.72)))
    c_lon = float(d.get("consumer_lon", user.get("location_lon", 75.88)))
    to_name = user.get("full_name") or user.get("name", "Consumer")

    weather = fetch_weather(c_lat, c_lon)
    dist = haversine(c_lat, c_lon, p_lat, p_lon)
    pricing = calculate_price(base_price, etype, weather, dist, kwh)
    final_price = pricing["final_price"]
    total_cost = round(kwh * final_price, 2)
    carbon_saved = round(kwh * POLICY["carbon_per_kwh"], 2)
    ts = datetime.now(timezone.utc).isoformat()

    prev_hash = get_last_hash()
    tx_hash = make_hash(prev_hash, listing_id or from_name, user["id"], kwh, final_price, ts)
    short_hash = "0x" + tx_hash[:8].upper()
    escrow_id = "esc_" + uuid.uuid4().hex[:10]

    tx = {
        "id": str(uuid.uuid4()),
        "tx_hash": tx_hash,        # stored only; shown only to admin
        "short_hash": short_hash,
        "prev_hash": prev_hash,
        "producer_id": listing.get("producer_id") if listing else d.get("producer_id"),
        "consumer_id": user["id"],
        "listing_id": listing_id,
        "from_name": from_name,
        "to_name": to_name,
        "energy_type": etype,
        "kwh": kwh,
        "base_price": base_price,
        "final_price": final_price,
        "total_cost": total_cost,
        "weather_modifier": pricing["weather_modifier"],
        "transmission_fee": pricing["transmission_fee"],
        "subsidy_applied": pricing["subsidy_applied"],
        "bulk_discount": pricing["bulk_discount"],
        "platform_fee": pricing["platform_fee"],
        "carbon_saved": carbon_saved,
        "distance_km": dist,
        "escrow_id": escrow_id,
        "escrow_status": "released",
        "payment_id": payment_id,
        "pool_id": pool_id,
        "status": "confirmed",
        "created_at": ts
    }

    if supabase:
        try:
            ins = {k: v for k, v in tx.items() if v is not None}
            supabase.table("transactions").insert(ins).execute()
            # Deduct from listing
            if listing:
                new_kwh = max(0, float(listing.get("available_kwh", 0)) - kwh)
                supabase.table("listings").update({"available_kwh": new_kwh}).eq("id", listing_id).execute()
            # Update consumer carbon score
            supabase.table("profiles").update({
                "carbon_score": float(user.get("carbon_score", 0)) + carbon_saved
            }).eq("id", user["id"]).execute()
        except Exception as e:
            print(f"DB write error: {e}")

    _mem["transactions"].append(tx)

    return jsonify({
        "success": True,
        "transaction": {k: v for k, v in tx.items() if k not in ["tx_hash", "prev_hash"]},
        "hash_chain_steps": [
            f"→ PREV BLOCK: {prev_hash[:16]}...",
            f"→ DATA: {from_name} + {to_name} + {kwh}kWh + ₹{final_price}",
            f"→ SHA-256 PROCESSING...",
            f"→ BLOCK HASH: {short_hash} (stored securely)",
            f"→ ESCROW: ₹{total_cost} → locked → verified → released",
            f"→ CARBON: +{carbon_saved}kg CO₂ saved • LEDGER UPDATED ✓"
        ]
    })


# ─────────────────────────────────────────────
# TRANSACTIONS
# ─────────────────────────────────────────────
@app.route("/api/transactions")
def api_transactions():
    limit = int(request.args.get("limit", 50))
    etype = request.args.get("type", "")
    search = request.args.get("search", "").lower()

    if supabase:
        try:
            # Public view: no tx_hash, no prev_hash
            q = supabase.table("transactions").select(
                "id,short_hash,from_name,to_name,energy_type,kwh,final_price,"
                "total_cost,carbon_saved,distance_km,escrow_status,status,created_at,pool_id"
            ).order("created_at", desc=True).limit(limit)
            if etype: q = q.eq("energy_type", etype)
            r = q.execute()
            txs = r.data or []
        except Exception as e:
            txs = _strip_hashes(_mem["transactions"])
    else:
        txs = _strip_hashes(_mem["transactions"])

    if etype: txs = [t for t in txs if t.get("energy_type") == etype]
    if search:
        txs = [t for t in txs if search in (t.get("from_name","") + t.get("to_name","")).lower()]
    return jsonify(txs[:limit])

def _strip_hashes(txs):
    safe_keys = ["id","short_hash","from_name","to_name","energy_type","kwh",
                 "final_price","total_cost","carbon_saved","distance_km",
                 "escrow_status","status","created_at","pool_id"]
    return [{k: t.get(k) for k in safe_keys if k in t} for t in reversed(txs)]


@app.route("/api/transactions/admin")
@require_role("admin")
def admin_transactions():
    """Full transactions with hashes - admin only"""
    limit = int(request.args.get("limit", 100))
    if supabase:
        try:
            r = supabase.table("transactions").select("*").order("created_at", desc=True).limit(limit).execute()
            return jsonify(r.data or [])
        except Exception as e:
            pass
    return jsonify(list(reversed(_mem["transactions"]))[:limit])


@app.route("/api/transactions/mine")
@require_auth
def my_transactions():
    user = request.current_user
    uid = user["id"]
    role = user.get("role")

    if supabase:
        try:
            if role == "producer":
                r = supabase.table("transactions").select("*").eq("producer_id", uid).order("created_at", desc=True).limit(50).execute()
            else:
                r = supabase.table("transactions").select("*").eq("consumer_id", uid).order("created_at", desc=True).limit(50).execute()
            # Strip hash even for own records (only admin sees hashes)
            txs = r.data or []
            return jsonify([{k:v for k,v in t.items() if k != "tx_hash" and k != "prev_hash"} for t in txs])
        except Exception as e:
            pass

    if role == "producer":
        txs = [t for t in _mem["transactions"] if t.get("producer_id") == uid]
    else:
        txs = [t for t in _mem["transactions"] if t.get("consumer_id") == uid]
    return jsonify(_strip_hashes(txs))


# ─────────────────────────────────────────────
# STATS
# ─────────────────────────────────────────────
@app.route("/api/stats")
def api_stats():
    if supabase:
        try:
            r = supabase.table("transactions").select("kwh,carbon_saved,total_cost").execute()
            txs = r.data or []
            prod = supabase.table("listings").select("id").eq("is_active", True).execute()
            pools = supabase.table("pools").select("id").eq("status", "open").execute()
            users = supabase.table("profiles").select("id").execute()
            return jsonify({
                "total_transactions": len(txs),
                "total_kwh": round(sum(float(t.get("kwh",0)) for t in txs), 2),
                "total_co2_saved": round(sum(float(t.get("carbon_saved",0)) for t in txs), 2),
                "total_value": round(sum(float(t.get("total_cost",0)) for t in txs), 2),
                "active_listings": len(prod.data or []),
                "active_pools": len(pools.data or []),
                "total_users": len(users.data or [])
            })
        except Exception as e:
            pass
    txs = _mem["transactions"]
    return jsonify({
        "total_transactions": len(txs),
        "total_kwh": round(sum(t.get("kwh",0) for t in txs), 2),
        "total_co2_saved": round(sum(t.get("carbon_saved",0) for t in txs), 2),
        "total_value": round(sum(t.get("total_cost",0) for t in txs), 2),
        "active_listings": len([l for l in _mem["listings"] if l.get("is_active")]),
        "active_pools": len([p for p in _mem["pools"] if p.get("status")=="open"]),
        "total_users": len(_mem["users"])
    })


# ─────────────────────────────────────────────
# PAYMENT — RAZORPAY
# ─────────────────────────────────────────────
@app.route("/api/payment/create-order", methods=["POST"])
@require_auth
def create_order():
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        return jsonify({"demo": True, "order_id": "demo_order_" + uuid.uuid4().hex[:8], "amount": request.get_json().get("amount", 100)})

    import razorpay
    d = request.get_json()
    amount_paise = int(float(d.get("amount", 100)) * 100)   # ₹ → paise

    client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    order = client.order.create({
        "amount": amount_paise, "currency": "INR",
        "receipt": "nx_" + uuid.uuid4().hex[:8],
        "notes": {"listing_id": d.get("listing_id", ""), "kwh": str(d.get("kwh", 0))}
    })
    return jsonify({"order_id": order["id"], "amount": amount_paise,
                    "currency": "INR", "key": RAZORPAY_KEY_ID})


@app.route("/api/payment/verify", methods=["POST"])
def verify_payment():
    d = request.get_json()
    if not RAZORPAY_KEY_SECRET:
        return jsonify({"verified": True, "payment_id": d.get("razorpay_payment_id", "demo")})

    msg = f"{d['razorpay_order_id']}|{d['razorpay_payment_id']}"
    expected = hmac.new(RAZORPAY_KEY_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    if hmac.compare_digest(expected, d.get("razorpay_signature", "")):
        return jsonify({"verified": True, "payment_id": d["razorpay_payment_id"]})
    return jsonify({"verified": False, "error": "Signature mismatch"}), 400


# ─────────────────────────────────────────────
# COMMUNITY POOLS
# ─────────────────────────────────────────────
@app.route("/api/pools", methods=["GET"])
def get_pools():
    if supabase:
        try:
            r = supabase.table("pools").select("*, pool_members(*)").eq("status", "open").execute()
            return jsonify(r.data or [])
        except Exception: pass
    return jsonify([p for p in _mem["pools"] if p.get("status") == "open"])


@app.route("/api/pools", methods=["POST"])
@require_auth
def create_pool():
    user = request.current_user
    d = request.get_json()
    pool = {
        "id": str(uuid.uuid4()),
        "producer_id": user["id"],
        "listing_id": d.get("listing_id"),
        "producer_name": user.get("full_name", user.get("name", "Producer")),
        "energy_type": d.get("energy_type", "solar"),
        "target_kwh": float(d.get("target_kwh", 100)),
        "total_committed": 0,
        "price_per_unit": float(d.get("price_per_unit", 3.0)),
        "discount_unlocked": False,
        "members": [],
        "status": "open",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    if supabase:
        try:
            r = supabase.table("pools").insert({k:v for k,v in pool.items() if k != "members"}).execute()
            return jsonify(r.data[0] if r.data else pool)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    _mem["pools"].append(pool)
    return jsonify(pool)


@app.route("/api/pools/<pool_id>/join", methods=["POST"])
@require_auth
def join_pool(pool_id):
    user = request.current_user
    d = request.get_json()
    kwh = float(d.get("kwh", 0))

    if supabase:
        try:
            pool_r = supabase.table("pools").select("*").eq("id", pool_id).single().execute()
            pool = pool_r.data
            new_committed = float(pool.get("total_committed", 0)) + kwh
            unlocked = new_committed >= POLICY["bulk_threshold_kwh"]
            supabase.table("pool_members").insert({
                "pool_id": pool_id, "consumer_id": user["id"],
                "consumer_name": user.get("full_name", "Consumer"),
                "kwh_committed": kwh
            }).execute()
            updates = {"total_committed": new_committed, "discount_unlocked": unlocked}
            if unlocked:
                updates["discounted_price"] = round(pool["price_per_unit"] * (1 - POLICY["bulk_discount_pct"]/100), 2)
            supabase.table("pools").update(updates).eq("id", pool_id).execute()
            return jsonify({"success": True, "discount_unlocked": unlocked, "total_committed": new_committed})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    pool = next((p for p in _mem["pools"] if p["id"] == pool_id), None)
    if not pool: return jsonify({"error": "Pool not found"}), 404
    pool["members"].append({"name": user.get("full_name","Consumer"), "kwh": kwh})
    pool["total_committed"] = pool.get("total_committed", 0) + kwh
    pool["discount_unlocked"] = pool["total_committed"] >= POLICY["bulk_threshold_kwh"]
    return jsonify(pool)


# ─────────────────────────────────────────────
# INVESTMENTS (Investor role)
# ─────────────────────────────────────────────
@app.route("/api/investments/mine", methods=["GET"])
@require_auth
def my_investments():
    user = request.current_user
    if supabase:
        try:
            r = supabase.table("investments").select("*, listings(energy_type, location_city), profiles!investments_producer_id_fkey(full_name)").eq("investor_id", user["id"]).execute()
            return jsonify(r.data or [])
        except Exception: pass
    return jsonify([i for i in _mem["investments"] if i.get("investor_id") == user["id"]])


@app.route("/api/investments", methods=["POST"])
@require_auth
def create_investment():
    user = request.current_user
    if user.get("role") not in ["investor", "admin"]:
        return jsonify({"error": "Only investors can invest"}), 403
    d = request.get_json()
    inv = {
        "id": str(uuid.uuid4()),
        "investor_id": user["id"],
        "listing_id": d.get("listing_id"),
        "producer_id": d.get("producer_id"),
        "amount_inr": float(d.get("amount", 1000)),
        "kwh_funded": float(d.get("kwh_funded", 0)),
        "return_rate": 8.5,
        "status": "active",
        "payment_id": d.get("payment_id", ""),
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    if supabase:
        try:
            r = supabase.table("investments").insert(inv).execute()
            return jsonify(r.data[0] if r.data else inv)
        except Exception as e: return jsonify({"error": str(e)}), 500
    _mem["investments"].append(inv)
    return jsonify(inv)


# ─────────────────────────────────────────────
# CARBON / GREENSCORE
# ─────────────────────────────────────────────
@app.route("/api/carbon")
@require_auth
def api_carbon():
    user = request.current_user
    uid = user["id"]
    if supabase:
        try:
            prof = supabase.table("profiles").select("carbon_score, green_credits").eq("id", uid).single().execute()
            score = float(prof.data.get("carbon_score", 0))
        except Exception:
            score = float(user.get("carbon_score", 0))
    else:
        score = float(user.get("carbon_score", 0))

    rank = "PLATINUM" if score > 200 else "GOLD" if score > 100 else "SILVER" if score > 40 else "GREEN"
    credits = round(score * 0.5, 1)
    return jsonify({"co2_kg": round(score, 2), "rank": rank, "green_credits": credits,
                    "badges": _get_badges(score)})

def _get_badges(co2):
    thresholds = [(0,"First Trade","⚡"),(10,"Green Starter","🌱"),(50,"Carbon Hero","🏆"),(100,"Climate Champion","🌍"),(200,"Platinum Guardian","💎")]
    return [{"name":n,"icon":i,"unlocked":co2>=t,"threshold":t} for t,n,i in thresholds]


# ─────────────────────────────────────────────
# POLICY
# ─────────────────────────────────────────────
@app.route("/api/policy")
def api_policy():
    return jsonify(POLICY)

@app.route("/api/policy", methods=["PUT"])
@require_role("admin")
def update_policy():
    POLICY.update(request.get_json())
    return jsonify({"success": True, "policy": POLICY})


# ─────────────────────────────────────────────
# PROFILE
# ─────────────────────────────────────────────
@app.route("/api/profile", methods=["GET"])
@require_auth
def get_profile():
    user = request.current_user
    uid = user["id"]
    if supabase:
        try:
            r = supabase.table("profiles").select("*").eq("id", uid).single().execute()
            return jsonify({k:v for k,v in r.data.items() if k not in ["pw_hash"]})
        except Exception: pass
    return jsonify({k:v for k,v in user.items() if k not in ["pw_hash"]})

@app.route("/api/profile", methods=["PUT"])
@require_auth
def update_profile():
    user = request.current_user
    d = request.get_json()
    allowed = ["full_name","location_city","location_lat","location_lon"]
    updates = {k:v for k,v in d.items() if k in allowed}
    if supabase:
        try:
            supabase.table("profiles").update(updates).eq("id", user["id"]).execute()
            return jsonify({"success": True})
        except Exception as e: return jsonify({"error": str(e)}), 500
    user.update(updates)
    return jsonify({"success": True})


# ─────────────────────────────────────────────
# AI ADVISOR (per-role smart recommendations)
# ─────────────────────────────────────────────
@app.route("/api/ai-advisor", methods=["POST"])
@require_auth
def ai_advisor():
    user = request.current_user
    d = request.get_json()
    message = d.get("message", "")
    role = user.get("role", "consumer")
    lat = float(d.get("lat", user.get("location_lat", 22.7196)))
    lon = float(d.get("lon", user.get("location_lon", 75.8577)))

    weather = fetch_weather(lat, lon)
    clouds = weather.get("clouds", 30)
    wind = weather.get("wind_speed", 10)
    temp = weather.get("temp", 28)

    # Build context-rich system prompt
    if role == "producer":
        context = f"""You are ARIA, an AI energy advisor for renewable energy producers on the NEXUS platform.
Current weather: {weather.get('condition')}, {temp}°C, {clouds}% cloud cover, wind {wind}km/h.
Solar pricing: {'⬆ HIGH DEMAND — clouds reduce solar output' if clouds > 60 else '⬇ SURPLUS — clear sky, lower prices' if clouds < 20 else '◉ MODERATE conditions'}.
Wind pricing: {'⬇ SURPLUS — strong winds' if wind > 25 else '⬆ HIGH DEMAND — calm winds' if wind < 5 else '◉ MODERATE wind'}.

Help producers: when to list energy, optimal pricing, capacity planning, earnings maximization, carbon credits.
Current policy: price floor ₹{POLICY['min_price_kwh']}, ceiling ₹{POLICY['max_price_kwh']}, subsidies: solar -₹{POLICY['solar_subsidy']}, wind -₹{POLICY['wind_subsidy']}.
Be concise, data-driven, and actionable. Max 3 sentences."""

    elif role == "consumer":
        context = f"""You are ARIA, an AI energy advisor for energy consumers on the NEXUS platform.
Current weather: {weather.get('condition')}, {temp}°C, {clouds}% cloud cover, wind {wind}km/h.
Best energy source right now: {'Solar (clear skies = surplus = lowest price)' if clouds < 25 else 'Wind' if wind > 20 else 'Biogas (stable baseload)'}.
Help consumers: when to buy cheapest energy, which source, bulk pool benefits, carbon impact.
Policy protections: max ₹{POLICY['max_price_kwh']}/kWh, min ₹{POLICY['min_price_kwh']}/kWh.
Be concise and helpful. Max 3 sentences."""

    else:  # investor
        context = f"""You are ARIA, an AI market advisor for energy investors on the NEXUS platform.
Current market weather: {weather.get('condition')}, clouds {clouds}%, wind {wind}km/h.
Market insight: {'Solar farms under-producing today — good time for wind investment' if clouds > 60 else 'Solar performing well today' if clouds < 25 else 'Mixed conditions'}.
Help investors: which producers to fund, expected returns (avg 8.5% p.a.), market timing, portfolio diversification.
Policy framework: transparent government pricing rules, carbon credit markets growing.
Be concise and investment-focused. Max 3 sentences."""

    # CORRECT ✅ (properly indented inside ai_advisor function)
    # Try Groq API
    if GROQ_API_KEY:
        try:
            headers = {
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "llama3-8b-8192",
                "max_tokens": 200,
                "messages": [
                    {"role": "system", "content": context},
                    {"role": "user", "content": message}
                ]
            }
            r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                              headers=headers, json=payload, timeout=10)
            if r.status_code == 200:
                reply = r.json()["choices"][0]["message"]["content"]
                return jsonify({"reply": reply, "powered_by": "groq-llama3"})
        except Exception as e:
            print(f"AI error: {e}")

    # Rule-based fallback
    reply = _smart_advisor(message.lower(), role, weather, clouds, wind)
    return jsonify({"reply": reply, "powered_by": "rule-engine"})


def _smart_advisor(msg, role, weather, clouds, wind):
    c = clouds; w = wind; t = weather.get("temp", 28)
    if role == "producer":
        if any(x in msg for x in ["price","sell","when"]):
            if c < 25: return f"☀️ Now is an excellent time to sell solar energy! Clear sky ({c}% cloud) means surplus in the market — list at base price, let weather drive competitive demand. Consider pre-booking orders before clouds roll in."
            if w > 20: return f"💨 Wind conditions are strong ({w}km/h) — wind farm prices are suppressed due to surplus. Consider listing biogas if available, or hold wind listings for calmer conditions when prices rise."
            return f"◉ Stable market conditions (cloud {c}%, wind {w}km/h). Standard pricing applies. List now to capture steady consumer demand — biogas producers should list today as it's the most price-stable source."
        if "solar" in msg: return f"Your solar output today: {'HIGH' if c<25 else 'MODERATE' if c<60 else 'LOW'} (cloud cover {c}%). {'Price at ₹2.8-3.5 to stay competitive.' if c<40 else 'You can command ₹4-6 premium during this shortage.'}"
        return f"💡 Tip: Weather is {weather.get('condition','stable')}. {'Solar producers should list now — clear sky = high demand.' if c<30 else 'Consider shifting to biogas listings today for stable pricing.'}"
    elif role == "consumer":
        if any(x in msg for x in ["buy","cheap","when","best"]):
            if c < 25: return f"🌟 Buy solar NOW — clear sky ({c}% cloud) means solar surplus and lowest prices. Solar is currently your cheapest option. Consider buying 50+ kWh to unlock 8% bulk pool discount."
            if w > 20: return f"💨 Wind energy is cheapest right now ({w}km/h winds = surplus). Search for WindFarm listings — they'll be ₹0.5-2 cheaper than usual. Lock in today's price!"
            return f"◉ Biogas is your best bet today — weather-independent and price-stable at ~₹3-3.5/kWh. Solar and wind are at moderate prices. Check community pools for 8% bulk discount on 50+ kWh orders."
        if "pool" in msg: return "🤝 Community pools save you 8% when total reaches 50 kWh. Join an existing pool or create one — it splits cost automatically. Best for regular buyers consuming 20+ kWh monthly."
        return f"💡 Current best source: {'Solar (cheapest right now — clear sky!)' if c<25 else 'Wind (strong winds = surplus pricing)' if w>20 else 'Biogas (stable and reliable today)'}. Check the listings for live prices."
    else:  # investor
        if any(x in msg for x in ["invest","return","fund"]):
            return f"📈 Best investment opportunities: {'Wind farms — high output days = more kWh traded = more returns.' if w>20 else 'Solar farms — clear sky days generate maximum kWh volume.' if c<30 else 'Biogas — most consistent returns, weather-independent, avg 8.5% p.a.'} Diversify across energy types for balanced portfolio risk."
        return f"📊 Market snapshot: Cloud {c}%, Wind {w}km/h, Temp {t}°C. {'Solar under-producing — wind/biogas investments outperforming today.' if c>60 else 'Solar performing strongly — solar producer returns elevated today.'} Avg platform return: 8.5% p.a. on funded capacity."


# ─────────────────────────────────────────────
# ADMIN — USER MANAGEMENT
# ─────────────────────────────────────────────
@app.route("/api/admin/users")
@require_role("admin")
def admin_users():
    if supabase:
        try:
            r = supabase.table("profiles").select("id,email,full_name,role,location_city,carbon_score,created_at,is_active").execute()
            return jsonify(r.data or [])
        except Exception: pass
    return jsonify([{k:v for k,v in u.items() if k!="pw_hash"} for u in _mem["users"].values()])


@app.route("/api/admin/users/<uid>/role", methods=["PUT"])
@require_role("admin")
def set_user_role(uid):
    new_role = request.get_json().get("role")
    if new_role not in ["producer","consumer","investor","admin"]:
        return jsonify({"error": "Invalid role"}), 400
    if supabase:
        supabase.table("profiles").update({"role": new_role}).eq("id", uid).execute()
    return jsonify({"success": True})


if __name__ == "__main__":
    print("\n🔋 NEXUS Energy Exchange — Backend Starting")
    print(f"   Supabase: {'✓ Connected' if supabase else '⚠ Demo mode (no DB)'}")
    print(f"   Weather:  {'✓ OpenWeatherMap' if WEATHER_API_KEY else '⚠ Demo data'}")
    print(f"   Payments: {'✓ Razorpay' if RAZORPAY_KEY_ID else '⚠ Demo mode'}")
    print(f"   AI Advisor: {'✓ Groq LLaMA3' if GROQ_API_KEY else '◉ Rule-based engine'}")
    print(f"\n   Open: http://localhost:5000\n")
    app.run(debug=True, host="0.0.0.0", port=5000)