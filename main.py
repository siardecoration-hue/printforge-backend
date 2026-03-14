from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, Response
from pydantic import BaseModel
import asyncio, uuid, httpx, base64, random, json, os, io
import hashlib, secrets, sqlite3
from datetime import datetime, timedelta
from typing import Optional

try:
    import jwt as pyjwt
    HAS_JWT = True
except ImportError:
    HAS_JWT = False
    print("[WARN] PyJWT yok — auth devre dışı")

try:
    import trimesh, numpy
    HAS_TRIMESH = True
except ImportError:
    HAS_TRIMESH = False
    print("[WARN] trimesh yok — STL devre dışı")

# ══════════════════════════════════════════════
#              AYARLAR
# ══════════════════════════════════════════════
app = FastAPI(title="PrintForge")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

TRIPO_API_KEY = os.getenv("TRIPO_API_KEY", "")
MESHY_API_KEY = os.getenv("MESHY_API_KEY", "")
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))
STRIPE_KEY = os.getenv("STRIPE_SECRET_KEY", "")
DB_PATH = os.getenv("DB_PATH", "printforge.db")

TRIPO_BASE = "https://api.tripo3d.ai/v2/openapi"
MESHY_BASE = "https://api.meshy.ai/openapi/v2"

tasks = {}
model_cache = {}
MAX_CACHE = 50

PLAN_LIMITS = {"free": 5, "pro": 100, "business": 999999}

DEMO_MODELS = [
    {"name": "Damaged Helmet", "glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb"},
    {"name": "Avocado", "glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/Avocado/glTF-Binary/Avocado.glb"},
    {"name": "Duck", "glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/Duck/glTF-Binary/Duck.glb"},
    {"name": "Lantern", "glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/Lantern/glTF-Binary/Lantern.glb"},
    {"name": "Water Bottle", "glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/WaterBottle/glTF-Binary/WaterBottle.glb"},
]


# ══════════════════════════════════════════════
#              VERİTABANI
# ══════════════════════════════════════════════
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            plan TEXT DEFAULT 'free',
            plan_expires TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER DEFAULT 0,
            task_id TEXT UNIQUE,
            title TEXT,
            prompt TEXT,
            gen_type TEXT,
            style TEXT,
            model_url TEXT,
            is_public INTEGER DEFAULT 1,
            likes INTEGER DEFAULT 0,
            downloads INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            month TEXT,
            count INTEGER DEFAULT 0,
            UNIQUE(user_id, month)
        );
        CREATE TABLE IF NOT EXISTS user_likes (
            user_id INTEGER,
            model_id INTEGER,
            PRIMARY KEY(user_id, model_id)
        );
    """)
    conn.commit()
    conn.close()
    print("[DB] Veritabanı hazır")

init_db()


# ══════════════════════════════════════════════
#              AUTH YARDIMCILAR
# ══════════════════════════════════════════════
def hash_pw(password):
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return salt, h

def verify_pw(password, salt, h):
    return hashlib.sha256((salt + password).encode()).hexdigest() == h

def create_token(user_id, email, name, plan):
    if not HAS_JWT: return "no-jwt"
    return pyjwt.encode({
        "user_id": user_id, "email": email, "name": name, "plan": plan,
        "exp": datetime.utcnow() + timedelta(days=30)
    }, SECRET_KEY, algorithm="HS256")

def decode_token(token):
    if not HAS_JWT: return None
    try:
        return pyjwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except:
        return None

async def get_user(authorization: Optional[str] = Header(None)):
    if not authorization: return None
    token = authorization.replace("Bearer ", "")
    data = decode_token(token)
    if not data: return None
    conn = get_db()
    row = conn.execute("SELECT id,email,name,plan FROM users WHERE id=?", (data["user_id"],)).fetchone()
    conn.close()
    if row: return {"id": row[0], "email": row[1], "name": row[2], "plan": row[3]}
    return None

def get_usage(user_id):
    month = datetime.now().strftime("%Y-%m")
    conn = get_db()
    row = conn.execute("SELECT count FROM usage WHERE user_id=? AND month=?", (user_id, month)).fetchone()
    conn.close()
    return row[0] if row else 0

def add_usage(user_id):
    month = datetime.now().strftime("%Y-%m")
    conn = get_db()
    conn.execute("INSERT INTO usage(user_id,month,count) VALUES(?,?,1) ON CONFLICT(user_id,month) DO UPDATE SET count=count+1", (user_id, month))
    conn.commit()
    conn.close()

def save_model(user_id, task_id, title, prompt, gen_type, style, model_url):
    conn = get_db()
    try:
        conn.execute("INSERT INTO models(user_id,task_id,title,prompt,gen_type,style,model_url) VALUES(?,?,?,?,?,?,?)",
                     (user_id, task_id, title, prompt, gen_type, style, model_url))
        conn.commit()
    except: pass
    conn.close()

def get_api():
    if TRIPO_API_KEY: return "tripo"
    if MESHY_API_KEY: return "meshy"
    return "demo"


# ══════════════════════════════════════════════
#              SAYFALAR
# ══════════════════════════════════════════════
@app.get("/", response_class=HTMLResponse)
def serve_landing():
    for name in ["index.html", "printforge.html"]:
        path = os.path.join(os.path.dirname(__file__), name)
        if os.path.exists(path): return FileResponse(path, media_type="text/html")
    return HTMLResponse('<html><body style="background:#04080a;color:#00e5ff;font-family:monospace;display:flex;align-items:center;justify-content:center;height:100vh"><a href="/app" style="color:#00e5ff;font-size:24px">PrintForge /app</a></body></html>')

@app.get("/app", response_class=HTMLResponse)
def serve_app():
    return HTMLResponse(APP_HTML)


# ══════════════════════════════════════════════
#              AUTH API
# ══════════════════════════════════════════════
class RegisterReq(BaseModel):
    name: str
    email: str
    password: str

class LoginReq(BaseModel):
    email: str
    password: str

@app.post("/api/auth/register")
async def register(req: RegisterReq):
    if len(req.password) < 6:
        raise HTTPException(400, "Şifre en az 6 karakter olmalı")
    if not req.email or "@" not in req.email:
        raise HTTPException(400, "Geçerli e-posta girin")

    salt, h = hash_pw(req.password)
    conn = get_db()
    try:
        conn.execute("INSERT INTO users(email,name,password_hash,salt) VALUES(?,?,?,?)",
                     (req.email.lower().strip(), req.name.strip(), h, salt))
        conn.commit()
        user_id = conn.execute("SELECT id FROM users WHERE email=?", (req.email.lower().strip(),)).fetchone()[0]
        conn.close()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(400, "Bu e-posta zaten kayıtlı")

    token = create_token(user_id, req.email, req.name, "free")
    return {"token": token, "user": {"id": user_id, "name": req.name, "email": req.email, "plan": "free"}}


@app.post("/api/auth/login")
async def login(req: LoginReq):
    conn = get_db()
    row = conn.execute("SELECT id,email,name,password_hash,salt,plan FROM users WHERE email=?",
                       (req.email.lower().strip(),)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(401, "E-posta veya şifre hatalı")
    if not verify_pw(req.password, row["salt"], row["password_hash"]):
        raise HTTPException(401, "E-posta veya şifre hatalı")

    token = create_token(row["id"], row["email"], row["name"], row["plan"])
    return {"token": token, "user": {"id": row["id"], "name": row["name"], "email": row["email"], "plan": row["plan"]}}


@app.get("/api/auth/me")
async def get_me(authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giriş yapın")
    used = get_usage(user["id"])
    limit = PLAN_LIMITS.get(user["plan"], 5)
    return {
        "user": user,
        "usage": {"used": used, "limit": limit, "remaining": max(0, limit - used)},
    }


@app.post("/api/auth/update")
async def update_profile(authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Giriş yapın")
    return {"ok": True}


# ══════════════════════════════════════════════
#              MODEL ÜRETİMİ
# ══════════════════════════════════════════════
class TextRequest(BaseModel):
    prompt: str
    style: str = "realistic"

@app.post("/api/generate/text")
async def generate_text(req: TextRequest, authorization: Optional[str] = Header(None)):
    api = get_api()
    user = await get_user(authorization)

    # Gerçek API kullanımı için giriş ve limit kontrolü
    if api != "demo":
        if not user:
            raise HTTPException(401, "3D model üretmek için giriş yapın")
        used = get_usage(user["id"])
        limit = PLAN_LIMITS.get(user["plan"], 5)
        if used >= limit:
            raise HTTPException(403, f"Aylık limitinize ulaştınız ({limit} model). Planınızı yükseltin.")
        add_usage(user["id"])

    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {"status": "processing", "progress": 0, "step": "Başlatılıyor...",
                      "type": "text", "api": api, "prompt": req.prompt, "style": req.style,
                      "user_id": user["id"] if user else 0}

    if api == "tripo": asyncio.create_task(_tripo_text(task_id, req.prompt, req.style))
    elif api == "meshy": asyncio.create_task(_meshy_text(task_id, req.prompt, req.style))
    else: asyncio.create_task(_demo_generate(task_id))
    return {"task_id": task_id}


@app.post("/api/generate/image")
async def generate_image(file: UploadFile = File(...), authorization: Optional[str] = Header(None)):
    api = get_api()
    user = await get_user(authorization)

    if api != "demo":
        if not user:
            raise HTTPException(401, "3D model üretmek için giriş yapın")
        used = get_usage(user["id"])
        limit = PLAN_LIMITS.get(user["plan"], 5)
        if used >= limit:
            raise HTTPException(403, f"Aylık limitinize ulaştınız ({limit} model). Planınızı yükseltin.")
        add_usage(user["id"])

    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(400, "Max 10MB")

    task_id = str(uuid.uuid4())[:8]
    filename = file.filename or "image.jpg"
    tasks[task_id] = {"status": "processing", "progress": 0, "step": "Görsel hazırlanıyor...",
                      "type": "image", "api": api, "prompt": filename, "style": "",
                      "user_id": user["id"] if user else 0}

    if api == "tripo": asyncio.create_task(_tripo_image(task_id, contents, filename))
    elif api == "meshy": asyncio.create_task(_meshy_image(task_id, contents, filename))
    else: asyncio.create_task(_demo_generate(task_id))
    return {"task_id": task_id}


@app.get("/api/status/{task_id}")
async def get_status(task_id: str):
    if task_id not in tasks: raise HTTPException(404, "Görev bulunamadı")
    t = tasks[task_id]
    return {
        "task_id": task_id, "status": t["status"], "progress": t["progress"],
        "step": t.get("step", ""), "model_url": t.get("model_url", ""),
        "is_demo": t.get("api") == "demo", "cached": task_id in model_cache,
        "error": t.get("error", ""),
    }


# ══════════════════════════════════════════════
#         MODEL SUNMA — ÖNİZLEME & İNDİRME
# ══════════════════════════════════════════════
async def cache_model(task_id, url):
    if task_id in model_cache: return True
    while len(model_cache) >= MAX_CACHE:
        oldest = next(iter(model_cache))
        del model_cache[oldest]
    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code == 200 and len(r.content) > 100:
                model_cache[task_id] = r.content
                print(f"[CACHE] {task_id} OK ({round(len(r.content)/1024/1024,2)} MB)")
                return True
    except Exception as e:
        print(f"[CACHE] {task_id} hata: {e}")
    return False

async def ensure_cached(task_id):
    if task_id in model_cache: return True
    if task_id in tasks and tasks[task_id].get("model_url"):
        return await cache_model(task_id, tasks[task_id]["model_url"])
    return False

@app.get("/api/model/{task_id}/view")
async def model_view(task_id: str):
    if not await ensure_cached(task_id): raise HTTPException(404, "Model bulunamadı")
    return Response(content=model_cache[task_id], media_type="model/gltf-binary",
                    headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=3600"})

@app.get("/api/model/{task_id}/glb")
async def download_glb(task_id: str):
    if not await ensure_cached(task_id): raise HTTPException(404, "Model bulunamadı")
    # İndirme sayısını artır
    conn = get_db()
    conn.execute("UPDATE models SET downloads=downloads+1 WHERE task_id=?", (task_id,))
    conn.commit()
    conn.close()
    return Response(content=model_cache[task_id], media_type="model/gltf-binary",
                    headers={"Content-Disposition": f'attachment; filename="printforge_{task_id}.glb"'})

@app.get("/api/model/{task_id}/stl")
async def download_stl(task_id: str):
    if not HAS_TRIMESH: raise HTTPException(500, "STL dönüştürme yüklü değil")
    if not await ensure_cached(task_id): raise HTTPException(404, "Model bulunamadı")
    try:
        scene = trimesh.load(io.BytesIO(model_cache[task_id]), file_type="glb", force="scene")
        if isinstance(scene, trimesh.Scene):
            meshes = [g for g in scene.geometry.values() if isinstance(g, trimesh.Trimesh)]
            if not meshes: raise Exception("Mesh bulunamadı")
            combined = trimesh.util.concatenate(meshes)
        else: combined = scene
        stl_bytes = combined.export(file_type="stl")
        conn = get_db()
        conn.execute("UPDATE models SET downloads=downloads+1 WHERE task_id=?", (task_id,))
        conn.commit()
        conn.close()
        return Response(content=stl_bytes, media_type="application/vnd.ms-pki.stl",
                        headers={"Content-Disposition": f'attachment; filename="printforge_{task_id}.stl"'})
    except Exception as e:
        raise HTTPException(500, f"STL dönüştürme hatası: {e}")

@app.get("/api/model/{task_id}/obj")
async def download_obj(task_id: str):
    if not HAS_TRIMESH: raise HTTPException(500, "OBJ dönüştürme yüklü değil")
    if not await ensure_cached(task_id): raise HTTPException(404, "Model bulunamadı")
    try:
        scene = trimesh.load(io.BytesIO(model_cache[task_id]), file_type="glb", force="scene")
        if isinstance(scene, trimesh.Scene):
            meshes = [g for g in scene.geometry.values() if isinstance(g, trimesh.Trimesh)]
            combined = trimesh.util.concatenate(meshes)
        else: combined = scene
        obj_bytes = combined.export(file_type="obj")
        return Response(content=obj_bytes, media_type="text/plain",
                        headers={"Content-Disposition": f'attachment; filename="printforge_{task_id}.obj"'})
    except Exception as e:
        raise HTTPException(500, f"OBJ hatası: {e}")


# ══════════════════════════════════════════════
#              GALERİ & MODELLERİM
# ══════════════════════════════════════════════
@app.get("/api/gallery")
async def gallery(page: int = 1, limit: int = 20, sort: str = "newest", search: str = ""):
    conn = get_db()
    offset = (page - 1) * limit

    where = "WHERE is_public=1 AND model_url != ''"
    params = []
    if search:
        where += " AND (title LIKE ? OR prompt LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]

    order = "ORDER BY created_at DESC"
    if sort == "popular": order = "ORDER BY likes DESC"
    elif sort == "downloads": order = "ORDER BY downloads DESC"

    rows = conn.execute(f"""
        SELECT m.*, u.name as author_name
        FROM models m LEFT JOIN users u ON m.user_id = u.id
        {where} {order} LIMIT ? OFFSET ?
    """, params + [limit, offset]).fetchall()

    total = conn.execute(f"SELECT COUNT(*) FROM models {where}", params).fetchone()[0]
    conn.close()

    return {
        "models": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
    }


@app.get("/api/my-models")
async def my_models(authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Giriş yapın")

    conn = get_db()
    rows = conn.execute("SELECT * FROM models WHERE user_id=? ORDER BY created_at DESC", (user["id"],)).fetchall()
    conn.close()
    return {"models": [dict(r) for r in rows]}


@app.post("/api/gallery/{model_id}/like")
async def toggle_like(model_id: int, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Giriş yapın")

    conn = get_db()
    existing = conn.execute("SELECT 1 FROM user_likes WHERE user_id=? AND model_id=?",
                            (user["id"], model_id)).fetchone()
    if existing:
        conn.execute("DELETE FROM user_likes WHERE user_id=? AND model_id=?", (user["id"], model_id))
        conn.execute("UPDATE models SET likes=likes-1 WHERE id=?", (model_id,))
        liked = False
    else:
        conn.execute("INSERT INTO user_likes(user_id,model_id) VALUES(?,?)", (user["id"], model_id))
        conn.execute("UPDATE models SET likes=likes+1 WHERE id=?", (model_id,))
        liked = True
    conn.commit()
    conn.close()
    return {"liked": liked}


@app.delete("/api/my-models/{model_id}")
async def delete_model(model_id: int, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Giriş yapın")
    conn = get_db()
    conn.execute("DELETE FROM models WHERE id=? AND user_id=?", (model_id, user["id"]))
    conn.commit()
    conn.close()
    return {"deleted": True}


# ══════════════════════════════════════════════
#              ÖDEME (Stripe)
# ══════════════════════════════════════════════
@app.post("/api/payment/create")
async def create_payment(authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Giriş yapın")

    if not STRIPE_KEY:
        # Simüle et (gerçek Stripe yoksa)
        return {"simulated": True, "message": "Stripe key eklenmedi. Simüle edildi.", "success": True}

    # Gerçek Stripe entegrasyonu
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post("https://api.stripe.com/v1/checkout/sessions",
                data={
                    "mode": "subscription",
                    "success_url": f"{os.getenv('RAILWAY_PUBLIC_DOMAIN', 'http://localhost:8000')}/app?payment=success",
                    "cancel_url": f"{os.getenv('RAILWAY_PUBLIC_DOMAIN', 'http://localhost:8000')}/app?payment=cancel",
                    "line_items[0][price]": os.getenv("STRIPE_PRICE_ID", ""),
                    "line_items[0][quantity]": "1",
                    "client_reference_id": str(user["id"]),
                },
                headers={"Authorization": f"Bearer {STRIPE_KEY}"}
            )
            data = r.json()
            return {"url": data.get("url", ""), "session_id": data.get("id", "")}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/payment/upgrade")
async def upgrade_plan(authorization: Optional[str] = Header(None)):
    """Simüle plan yükseltme (Stripe yoksa)"""
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Giriş yapın")

    conn = get_db()
    conn.execute("UPDATE users SET plan='pro' WHERE id=?", (user["id"],))
    conn.commit()
    conn.close()
    return {"success": True, "plan": "pro"}


# ══════════════════════════════════════════════
#              SAĞLIK & DEBUG
# ══════════════════════════════════════════════
@app.get("/api/health")
async def health():
    api = get_api()
    return {
        "status": "online", "active_api": api, "api_ready": True,
        "is_demo": api == "demo", "stl_ready": HAS_TRIMESH,
        "auth_ready": HAS_JWT, "cached_models": len(model_cache),
    }

@app.get("/api/debug/{task_id}")
async def debug_task(task_id: str):
    if task_id not in tasks: return {"error": "Bulunamadı"}
    return {"task_id": task_id, "data": tasks[task_id], "cached": task_id in model_cache}


# ══════════════════════════════════════════════
#         URL ÇIKARMA
# ══════════════════════════════════════════════
def extract_model_url(data):
    if not data: return ""
    if isinstance(data, str) and data.startswith("http"): return data
    if not isinstance(data, dict): return ""
    for key in ["model", "pbr_model", "base_model", "rendered_model"]:
        val = data.get(key, "")
        if isinstance(val, str) and val.startswith("http"): return val
        if isinstance(val, dict):
            url = val.get("url", "") or val.get("download_url", "")
            if url and url.startswith("http"): return url
    for key, val in data.items():
        if isinstance(val, str) and val.startswith("http"):
            if any(ext in val.lower() for ext in [".glb", ".gltf", ".fbx", ".obj", "model"]): return val
        if isinstance(val, dict):
            for sv in val.values():
                if isinstance(sv, str) and sv.startswith("http"): return sv
    return ""


# ══════════════════════════════════════════════
#         TRIPO3D
# ══════════════════════════════════════════════
async def _tripo_text(task_id, prompt, style):
    try:
        headers = {"Authorization": f"Bearer {TRIPO_API_KEY}"}
        tasks[task_id]["progress"] = 10
        tasks[task_id]["step"] = "Prompt gönderiliyor..."
        async with httpx.AsyncClient(timeout=600) as client:
            r = await client.post(f"{TRIPO_BASE}/task", json={"type": "text_to_model", "prompt": f"{prompt}, {style} style"},
                                  headers={**headers, "Content-Type": "application/json"})
            print(f"[TRIPO] text: {r.status_code} - {r.text[:300]}")
            if r.status_code != 200: raise Exception(f"Tripo hata {r.status_code}")
            tripo_id = r.json().get("data", {}).get("task_id")
            if not tripo_id: raise Exception("Task ID alınamadı")
            tasks[task_id]["progress"] = 25
            await _tripo_poll(client, headers, task_id, tripo_id)
    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)

async def _tripo_image(task_id, contents, filename):
    try:
        headers = {"Authorization": f"Bearer {TRIPO_API_KEY}"}
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext not in ("jpg", "jpeg", "png", "webp"): ext = "jpeg"
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        tasks[task_id]["progress"] = 10
        tasks[task_id]["step"] = "Görsel yükleniyor..."
        async with httpx.AsyncClient(timeout=600) as client:
            ur = await client.post(f"{TRIPO_BASE}/upload", files={"file": (filename, contents, mime)}, headers=headers)
            if ur.status_code != 200: raise Exception(f"Upload hata {ur.status_code}")
            token = ur.json().get("data", {}).get("image_token")
            if not token: raise Exception("image_token alınamadı")
            tasks[task_id]["progress"] = 25
            tr = await client.post(f"{TRIPO_BASE}/task", json={"type": "image_to_model",
                "file": {"type": ext if ext != "jpg" else "jpeg", "file_token": token}},
                headers={**headers, "Content-Type": "application/json"})
            if tr.status_code != 200: raise Exception(f"Task hata {tr.status_code}")
            tripo_id = tr.json().get("data", {}).get("task_id")
            if not tripo_id: raise Exception("Task ID alınamadı")
            tasks[task_id]["progress"] = 35
            await _tripo_poll(client, headers, task_id, tripo_id)
    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)

async def _tripo_poll(client, headers, task_id, tripo_id):
    for _ in range(200):
        await asyncio.sleep(3)
        try:
            r = await client.get(f"{TRIPO_BASE}/task/{tripo_id}", headers=headers)
            d = r.json().get("data", {})
            st, pr = d.get("status", ""), d.get("progress", 0)
            tasks[task_id]["progress"] = 35 + int(pr * 0.55)
            tasks[task_id]["step"] = f"Model üretiliyor... %{pr}"
            if st == "success":
                output = d.get("output", {})
                model_url = extract_model_url(output)
                print(f"[TRIPO] OK! URL: {model_url}")
                tasks[task_id]["model_url"] = model_url
                tasks[task_id]["progress"] = 92
                tasks[task_id]["step"] = "Model indiriliyor..."
                if model_url: await cache_model(task_id, model_url)
                tasks[task_id]["status"] = "done"
                tasks[task_id]["progress"] = 100
                tasks[task_id]["step"] = "Tamamlandı!"
                # Veritabanına kaydet
                uid = tasks[task_id].get("user_id", 0)
                prompt = tasks[task_id].get("prompt", "")
                style = tasks[task_id].get("style", "")
                title = prompt[:50] if prompt else f"Model {task_id}"
                save_model(uid, task_id, title, prompt, tasks[task_id].get("type", ""), style, model_url)
                return
            elif st in ("failed", "cancelled"):
                raise Exception(f"Tripo: {st}")
        except Exception as e:
            if any(x in str(e) for x in ["Tripo", "failed", "cancelled"]):
                tasks[task_id]["status"] = "failed"
                tasks[task_id]["error"] = str(e)
                return
            continue
    tasks[task_id]["status"] = "failed"
    tasks[task_id]["error"] = "Zaman aşımı"


# ══════════════════════════════════════════════
#         MESHY (Yedek)
# ══════════════════════════════════════════════
async def _meshy_text(task_id, prompt, style):
    try:
        headers = {"Authorization": f"Bearer {MESHY_API_KEY}", "Content-Type": "application/json"}
        tasks[task_id]["progress"] = 10
        async with httpx.AsyncClient(timeout=600) as client:
            r = await client.post(f"{MESHY_BASE}/text-to-3d", json={"mode": "preview", "prompt": prompt, "art_style": "realistic"}, headers=headers)
            if r.status_code not in (200, 202): raise Exception(f"Meshy hata {r.status_code}")
            meshy_id = r.json().get("result")
            tasks[task_id]["progress"] = 20
            await _meshy_poll(client, headers, task_id, meshy_id, "text-to-3d")
    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)

async def _meshy_image(task_id, contents, filename):
    try:
        headers = {"Authorization": f"Bearer {MESHY_API_KEY}", "Content-Type": "application/json"}
        ext = filename.rsplit(".", 1)[-1].lower()
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        b64 = base64.b64encode(contents).decode()
        tasks[task_id]["progress"] = 15
        async with httpx.AsyncClient(timeout=600) as client:
            r = await client.post(f"{MESHY_BASE}/image-to-3d", json={"image_url": f"data:{mime};base64,{b64}", "enable_pbr": True}, headers=headers)
            if r.status_code not in (200, 202): raise Exception(f"Meshy hata {r.status_code}")
            meshy_id = r.json().get("result")
            tasks[task_id]["progress"] = 25
            await _meshy_poll(client, headers, task_id, meshy_id, "image-to-3d")
    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)

async def _meshy_poll(client, headers, task_id, meshy_id, endpoint):
    for _ in range(200):
        await asyncio.sleep(3)
        try:
            r = await client.get(f"{MESHY_BASE}/{endpoint}/{meshy_id}", headers=headers)
            if r.status_code != 200: continue
            data = r.json()
            if data.get("status") == "SUCCEEDED":
                glb = data.get("model_urls", {}).get("glb", "")
                tasks[task_id]["model_url"] = glb
                if glb: await cache_model(task_id, glb)
                tasks[task_id]["status"] = "done"
                tasks[task_id]["progress"] = 100
                uid = tasks[task_id].get("user_id", 0)
                save_model(uid, task_id, tasks[task_id].get("prompt", "")[:50], tasks[task_id].get("prompt", ""), tasks[task_id].get("type", ""), "", glb)
                return
            elif data.get("status") == "FAILED": raise Exception("Meshy üretilemedi")
            tasks[task_id]["progress"] = 25 + int(data.get("progress", 0) * 0.7)
        except Exception as e:
            if "üretilemedi" in str(e): raise
    raise Exception("Zaman aşımı")


# ══════════════════════════════════════════════
#         DEMO MOD
# ══════════════════════════════════════════════
async def _demo_generate(task_id):
    try:
        for pr, st in [(8,"Analiz ediliyor..."),(22,"AI yükleniyor..."),(40,"Geometri oluşturuluyor..."),(58,"Mesh üretiliyor..."),(72,"Texture uygulanıyor..."),(88,"Optimize ediliyor..."),(95,"Hazırlanıyor...")]:
            tasks[task_id]["progress"] = pr
            tasks[task_id]["step"] = st
            await asyncio.sleep(random.uniform(1.0, 2.0))
        model = random.choice(DEMO_MODELS)
        tasks[task_id]["model_url"] = model["glb"]
        await cache_model(task_id, model["glb"])
        tasks[task_id]["status"] = "done"
        tasks[task_id]["progress"] = 100
        tasks[task_id]["step"] = f"Demo: {model['name']}"
        save_model(0, task_id, model["name"], "demo", "demo", "", model["glb"])
    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)


# ══════════════════════════════════════════════
#     /app HTML — TAM ÖZELLİKLİ
# ══════════════════════════════════════════════
APP_HTML = r"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PrintForge — 3D Model Uret</title>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Mono:wght@300;400;500&display=swap" rel="stylesheet">
<script type="module" src="https://unpkg.com/@google/model-viewer@3.5.0/dist/model-viewer.min.js"></script>
<style>
:root{--bg:#04080a;--bg2:#070d10;--border:#0e2028;--accent:#00e5ff;--accent2:#00ff9d;--text:#c8dde5;--muted:#2a4a5a;--card:#060c10;--red:#ff4466;--orange:#ffaa00}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'DM Mono',monospace;background:var(--bg);color:var(--text);min-height:100vh}
::-webkit-scrollbar{width:4px}::-webkit-scrollbar-thumb{background:var(--muted)}
.bg-grid{position:fixed;inset:0;z-index:0;pointer-events:none;background-image:linear-gradient(rgba(0,229,255,0.02) 1px,transparent 1px),linear-gradient(90deg,rgba(0,229,255,0.02) 1px,transparent 1px);background-size:72px 72px}
.bg-orb{position:fixed;border-radius:50%;filter:blur(90px);pointer-events:none;z-index:0}
.bg-orb1{width:500px;height:500px;background:radial-gradient(circle,rgba(0,229,255,0.08),transparent 70%);top:-150px;left:-150px}
.bg-orb2{width:400px;height:400px;background:radial-gradient(circle,rgba(0,255,157,0.06),transparent 70%);bottom:-100px;right:-100px}

.nav{position:sticky;top:0;z-index:100;display:flex;justify-content:space-between;align-items:center;padding:14px 30px;background:rgba(4,8,10,0.92);backdrop-filter:blur(20px);border-bottom:1px solid rgba(0,229,255,0.07);gap:12px;flex-wrap:wrap}
.nav-logo{display:flex;align-items:center;gap:8px;text-decoration:none}
.nlm{width:22px;height:22px;border:1.5px solid var(--accent);transform:rotate(45deg);display:flex;align-items:center;justify-content:center}
.nli{width:6px;height:6px;background:var(--accent);transform:rotate(-45deg)}
.nlt{font-family:'Syne',sans-serif;font-size:14px;font-weight:800;color:var(--accent);letter-spacing:0.1em}
.nav-right{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.nav-status{font-size:9px;color:var(--muted);display:flex;align-items:center;gap:5px}
.nav-dot{width:6px;height:6px;border-radius:50%;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
.nav-user{display:flex;align-items:center;gap:8px}
.nav-avatar{width:28px;height:28px;border-radius:50%;background:rgba(0,229,255,0.15);border:1px solid var(--accent);display:flex;align-items:center;justify-content:center;font-size:11px;color:var(--accent);cursor:pointer}
.nav-uname{font-size:10px;color:var(--text)}
.nav-usage{font-size:9px;color:var(--accent2);background:rgba(0,255,157,0.08);padding:3px 8px;border:1px solid rgba(0,255,157,0.2)}
.nbtn{padding:6px 14px;font-family:'DM Mono',monospace;font-size:9px;letter-spacing:0.12em;cursor:pointer;transition:all 0.2s;border:1px solid var(--border);background:transparent;color:var(--text)}
.nbtn:hover{border-color:var(--accent);color:var(--accent)}
.nbtn.accent{background:var(--accent);color:#04080a;border-color:var(--accent)}
.nbtn.accent:hover{background:var(--accent2)}
.nbtn.red{color:var(--red);border-color:rgba(255,68,102,0.3)}

.banner{padding:10px 20px;text-align:center;font-size:10px;border-bottom:1px solid rgba(255,170,0,0.15);display:none;position:relative;z-index:1}
.banner.demo{background:rgba(255,170,0,0.05);color:var(--orange)}
.banner.usage{background:rgba(0,255,157,0.04);color:var(--accent2)}

.container{position:relative;z-index:1;max-width:800px;margin:0 auto;padding:30px 20px 80px}

/* AUTH OVERLAY */
.auth-overlay{display:none;position:fixed;inset:0;z-index:200;background:rgba(4,8,10,0.92);align-items:center;justify-content:center;padding:20px}
.auth-overlay.on{display:flex}
.auth-box{background:var(--card);border:1px solid var(--border);padding:36px 32px;width:100%;max-width:380px;position:relative}
.auth-close{position:absolute;top:12px;right:14px;background:none;border:none;color:var(--muted);font-size:18px;cursor:pointer}
.auth-title{font-family:'Syne',sans-serif;font-size:20px;font-weight:800;text-align:center;margin-bottom:6px}
.auth-sub{font-size:10px;color:var(--muted);text-align:center;margin-bottom:20px}
.auth-tabs{display:flex;border:1px solid var(--border);margin-bottom:20px}
.auth-tab{flex:1;padding:10px;background:transparent;border:none;color:var(--muted);font-family:'DM Mono',monospace;font-size:10px;cursor:pointer;transition:all 0.15s}
.auth-tab.on{background:rgba(0,229,255,0.06);color:var(--accent)}
.fg{margin-bottom:12px}
.fg label{font-size:8px;letter-spacing:0.15em;color:var(--muted);margin-bottom:5px;display:block}
.fg input{width:100%;background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:10px 12px;font-size:12px;font-family:'DM Mono',monospace}
.fg input:focus{outline:none;border-color:rgba(0,229,255,0.4)}
.auth-btn{width:100%;padding:12px;background:var(--accent);color:#04080a;border:none;font-family:'DM Mono',monospace;font-size:11px;letter-spacing:0.15em;cursor:pointer;margin-top:6px;transition:all 0.2s}
.auth-btn:hover{background:var(--accent2)}
.auth-msg{padding:8px 12px;font-size:10px;margin-bottom:10px;display:none}
.auth-msg.err{background:rgba(255,68,102,0.08);border:1px solid rgba(255,68,102,0.2);color:var(--red);display:block}
.auth-msg.ok{background:rgba(0,255,157,0.08);border:1px solid rgba(0,255,157,0.2);color:var(--accent2);display:block}

/* TABS */
.tabs{display:flex;border:1px solid var(--border);margin-bottom:28px;flex-wrap:wrap}
.tab{flex:1;padding:12px 8px;background:transparent;border:none;color:var(--muted);font-family:'DM Mono',monospace;font-size:10px;letter-spacing:0.1em;cursor:pointer;transition:all 0.2s;display:flex;align-items:center;justify-content:center;gap:6px;min-width:80px}
.tab.on{background:rgba(0,229,255,0.06);color:var(--accent);border-bottom:2px solid var(--accent)}
.tab .badge{background:var(--accent);color:#04080a;padding:1px 6px;font-size:8px;border-radius:2px}

.panel{display:none}.panel.on{display:block}
.card{background:var(--card);border:1px solid var(--border);padding:28px;margin-bottom:16px}
.label{font-size:8px;letter-spacing:0.18em;color:var(--muted);margin-bottom:6px;display:block}
textarea{width:100%;background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:12px;font-size:12px;font-family:'DM Mono',monospace;resize:vertical;min-height:70px;transition:border-color 0.2s}
textarea:focus{outline:none;border-color:rgba(0,229,255,0.4)}
textarea::placeholder{color:var(--muted)}
.examples{margin-top:10px;display:flex;gap:5px;flex-wrap:wrap}
.ex-btn{padding:5px 10px;border:1px solid var(--border);background:transparent;color:var(--muted);font-family:'DM Mono',monospace;font-size:8px;cursor:pointer;transition:all 0.15s}
.ex-btn:hover{border-color:var(--accent);color:var(--accent)}
.style-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:6px}
.style-opt{padding:12px 8px;border:1px solid var(--border);background:transparent;color:var(--muted);font-family:'DM Mono',monospace;font-size:9px;cursor:pointer;transition:all 0.2s;text-align:center}
.style-opt:hover{border-color:rgba(0,229,255,0.3);color:var(--text)}
.style-opt.on{border-color:var(--accent);color:var(--accent);background:rgba(0,229,255,0.04)}
.style-opt .ico{font-size:18px;display:block;margin-bottom:4px}
.upload{border:2px dashed var(--border);padding:40px 20px;text-align:center;cursor:pointer;transition:all 0.3s;position:relative;overflow:hidden}
.upload:hover,.upload.drag{border-color:var(--accent)}
.upload.has{border-color:var(--accent2);border-style:solid}
.upload input{position:absolute;inset:0;opacity:0;cursor:pointer}
.upload .ico{font-size:32px;margin-bottom:10px;color:var(--accent)}
.upload p{font-size:11px;color:var(--muted)}
.preview{margin-top:14px;display:none;position:relative}
.preview.on{display:block}
.preview img{max-width:100%;max-height:200px;display:block;margin:0 auto;border:1px solid var(--border)}
.preview .rm{position:absolute;top:4px;right:4px;width:26px;height:26px;background:rgba(255,68,102,0.85);border:none;color:#fff;border-radius:50%;cursor:pointer;font-size:11px}
.gen-btn{width:100%;padding:14px;background:var(--accent);color:#04080a;border:none;font-family:'DM Mono',monospace;font-size:11px;letter-spacing:0.18em;cursor:pointer;font-weight:600;transition:all 0.2s;margin-top:16px}
.gen-btn:hover:not(:disabled){background:var(--accent2);transform:translateY(-1px)}
.gen-btn:disabled{opacity:0.4;cursor:not-allowed}

.sec{display:none;margin-bottom:20px}.sec.on{display:block}
.prog-card{background:var(--card);border:1px solid var(--border);padding:24px}
.prog-top{display:flex;justify-content:space-between;margin-bottom:14px}
.prog-title{font-family:'Syne',sans-serif;font-size:14px;font-weight:700}
.prog-pct{font-family:'Syne',sans-serif;font-size:20px;font-weight:800;color:var(--accent)}
.prog-bar-bg{width:100%;height:6px;background:var(--bg2);overflow:hidden;margin-bottom:10px}
.prog-bar{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));width:0%;transition:width 0.5s}
.prog-step{font-size:10px;color:var(--muted);display:flex;align-items:center;gap:6px}
.spinner{display:inline-block;width:10px;height:10px;border:2px solid var(--muted);border-top-color:var(--accent);border-radius:50%;animation:spin 0.8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

.result-card{background:var(--card);border:1px solid var(--accent2);padding:24px;text-align:center}
.result-card h3{font-family:'Syne',sans-serif;font-size:18px;font-weight:800;margin-bottom:6px}
.result-card>p{font-size:10px;color:var(--muted);margin-bottom:16px}
.viewer{width:100%;height:350px;background:var(--bg2);border:1px solid var(--border);margin-bottom:16px;overflow:hidden;display:flex;align-items:center;justify-content:center}
.viewer model-viewer{width:100%;height:100%}
.dl-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:10px}
.dl-btn{padding:12px 8px;border:1px solid var(--border);background:var(--card);color:var(--text);font-family:'DM Mono',monospace;font-size:10px;cursor:pointer;transition:all 0.2s;text-decoration:none;text-align:center;display:flex;flex-direction:column;align-items:center;gap:3px}
.dl-btn:hover{border-color:var(--accent);color:var(--accent)}
.dl-btn .dl-fmt{font-size:7px;color:var(--muted)}
.dl-btn.primary{border-color:var(--accent2);background:rgba(0,255,157,0.05)}
.new-btn{width:100%;padding:11px;border:1px solid var(--border);background:transparent;color:var(--text);font-family:'DM Mono',monospace;font-size:9px;letter-spacing:0.1em;cursor:pointer;transition:all 0.2s}
.new-btn:hover{border-color:var(--accent);color:var(--accent)}
.err-card{background:rgba(255,68,102,0.06);border:1px solid rgba(255,68,102,0.2);padding:24px;text-align:center}
.err-card h3{color:var(--red);font-family:'Syne',sans-serif;font-size:15px;margin-bottom:6px}
.err-card p{font-size:10px;color:var(--muted);margin-bottom:14px}

/* GALLERY */
.gal-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}
.gal-card{background:var(--card);border:1px solid var(--border);transition:all 0.2s;cursor:pointer}
.gal-card:hover{border-color:rgba(0,229,255,0.3);transform:translateY(-2px)}
.gal-thumb{height:160px;background:var(--bg2);overflow:hidden;display:flex;align-items:center;justify-content:center;position:relative}
.gal-thumb model-viewer{width:100%;height:100%}
.gal-body{padding:12px}
.gal-title{font-family:'Syne',sans-serif;font-size:12px;font-weight:700;margin-bottom:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gal-meta{font-size:9px;color:var(--muted);display:flex;justify-content:space-between;margin-bottom:8px}
.gal-actions{display:flex;gap:6px}
.gal-btn{flex:1;padding:6px;border:1px solid var(--border);background:transparent;color:var(--text);font-family:'DM Mono',monospace;font-size:8px;cursor:pointer;transition:all 0.15s;text-align:center}
.gal-btn:hover{border-color:var(--accent);color:var(--accent)}
.gal-btn.liked{color:var(--red);border-color:rgba(255,68,102,0.3)}
.gal-empty{text-align:center;padding:60px 20px;color:var(--muted);font-size:11px}
.gal-load{text-align:center;padding:20px}

@media(max-width:600px){.nav{padding:12px 14px}.container{padding:20px 12px}.card{padding:20px 14px}.style-grid{grid-template-columns:repeat(2,1fr)}.viewer{height:250px}.dl-grid{grid-template-columns:1fr 1fr 1fr}.gal-grid{grid-template-columns:repeat(auto-fill,minmax(160px,1fr))}.tabs{font-size:9px}}
</style>
</head>
<body>
<div class="bg-grid"></div>
<div class="bg-orb bg-orb1"></div>
<div class="bg-orb bg-orb2"></div>

<!-- AUTH OVERLAY -->
<div class="auth-overlay" id="authOverlay">
  <div class="auth-box">
    <button class="auth-close" onclick="closeAuth()">&times;</button>
    <div style="text-align:center;margin-bottom:16px;font-family:'Syne',sans-serif;font-size:18px;font-weight:800;color:var(--accent)">PRINTFORGE</div>
    <div class="auth-tabs">
      <button class="auth-tab on" id="aLoginTab" onclick="authTab('login')">GIRIS YAP</button>
      <button class="auth-tab" id="aRegTab" onclick="authTab('register')">KAYIT OL</button>
    </div>
    <div id="authMsg" class="auth-msg"></div>
    <div id="loginForm">
      <div class="fg"><label>E-POSTA</label><input type="email" id="lEmail" placeholder="ornek@mail.com"></div>
      <div class="fg"><label>SIFRE</label><input type="password" id="lPass" placeholder="******"></div>
      <button class="auth-btn" onclick="doLogin()">GIRIS YAP</button>
    </div>
    <div id="regForm" style="display:none">
      <div class="fg"><label>AD SOYAD</label><input type="text" id="rName" placeholder="Adiniz Soyadiniz"></div>
      <div class="fg"><label>E-POSTA</label><input type="email" id="rEmail" placeholder="ornek@mail.com"></div>
      <div class="fg"><label>SIFRE</label><input type="password" id="rPass" placeholder="En az 6 karakter"></div>
      <button class="auth-btn" onclick="doRegister()">KAYIT OL</button>
    </div>
  </div>
</div>

<nav class="nav">
  <a href="/" class="nav-logo"><div class="nlm"><div class="nli"></div></div><span class="nlt">PRINTFORGE</span></a>
  <div class="nav-right">
    <div class="nav-status" id="apiSt"><div class="nav-dot" style="background:var(--orange)"></div><span>...</span></div>
    <div id="navGuest"><button class="nbtn" onclick="openAuth('login')">GIRIS</button><button class="nbtn accent" onclick="openAuth('register')">KAYIT OL</button></div>
    <div id="navUser" style="display:none" class="nav-user">
      <span class="nav-usage" id="navUsage">-</span>
      <span class="nav-uname" id="navName">-</span>
      <div class="nav-avatar" id="navAvatar">U</div>
      <button class="nbtn red" onclick="doLogout()">CIKIS</button>
    </div>
  </div>
</nav>
<div class="banner demo" id="demoBanner">DEMO MOD - Ornek modeller gosterilir</div>
<div class="banner usage" id="usageBanner" style="display:none"></div>

<div class="container">
  <div class="tabs">
    <button class="tab on" onclick="swTab('gen')">URET</button>
    <button class="tab" onclick="swTab('gallery')">GALERI</button>
    <button class="tab" onclick="swTab('mymodels')">MODELLERIM</button>
  </div>

  <!-- GENERATE PANEL -->
  <div class="panel on" id="pGen">
    <div class="tabs" style="margin-bottom:20px">
      <button class="tab on" onclick="swSub('text')">METIN</button>
      <button class="tab" onclick="swSub('image')">GORSEL</button>
    </div>
    <div class="panel on" id="pText">
      <div class="card">
        <label class="label">PROMPT</label>
        <textarea id="prompt" placeholder="Orn: a cute robot toy..." rows="3"></textarea>
        <div class="examples">
          <button class="ex-btn" onclick="setP('a cute cartoon robot toy')">Robot</button>
          <button class="ex-btn" onclick="setP('a medieval stone castle')">Kale</button>
          <button class="ex-btn" onclick="setP('a futuristic sports car')">Araba</button>
          <button class="ex-btn" onclick="setP('a dragon miniature figure')">Ejderha</button>
          <button class="ex-btn" onclick="setP('a geometric modern vase')">Vazo</button>
        </div>
        <label class="label" style="margin-top:16px">STIL</label>
        <div class="style-grid">
          <button class="style-opt on" data-s="realistic" onclick="selS(this)"><span class="ico">&#128247;</span>GERCEKCI</button>
          <button class="style-opt" data-s="cartoon" onclick="selS(this)"><span class="ico">&#127912;</span>CARTOON</button>
          <button class="style-opt" data-s="lowpoly" onclick="selS(this)"><span class="ico">&#128142;</span>LOW POLY</button>
          <button class="style-opt" data-s="sculpture" onclick="selS(this)"><span class="ico">&#128511;</span>HEYKEL</button>
          <button class="style-opt" data-s="mechanical" onclick="selS(this)"><span class="ico">&#9881;</span>MEKANIK</button>
          <button class="style-opt" data-s="miniature" onclick="selS(this)"><span class="ico">&#9823;</span>MINYATUR</button>
        </div>
      </div>
      <button class="gen-btn" id="txtBtn" onclick="genText()">3D MODEL URET</button>
    </div>
    <div class="panel" id="pImage">
      <div class="card">
        <label class="label">GORSEL YUKLE</label>
        <div class="upload" id="upArea">
          <div class="ico">&#11042;</div>
          <p>Surukle-birak veya tikla</p>
          <input type="file" id="fInp" accept="image/*" onchange="onFile(this)">
        </div>
        <div class="preview" id="prev"><img id="prevImg" src=""><button class="rm" onclick="rmFile()">X</button></div>
      </div>
      <button class="gen-btn" id="imgBtn" onclick="genImage()" disabled>3D MODEL URET</button>
    </div>

    <div class="sec" id="progSec">
      <div class="prog-card">
        <div class="prog-top"><span class="prog-title">Model Uretiliyor</span><span class="prog-pct" id="progPct">0%</span></div>
        <div class="prog-bar-bg"><div class="prog-bar" id="progBar"></div></div>
        <div class="prog-step" id="progStep"><div class="spinner"></div><span>Baslatiliyor...</span></div>
      </div>
    </div>
    <div class="sec" id="resSec">
      <div class="result-card">
        <h3>Model Hazir!</h3>
        <p>3D modeliniz olusturuldu</p>
        <div class="viewer" id="viewer3d"></div>
        <div class="dl-grid" id="dlGrid"></div>
        <button class="new-btn" onclick="resetGen()">+ YENI MODEL</button>
      </div>
    </div>
    <div class="sec" id="errSec">
      <div class="err-card">
        <h3>Hata</h3>
        <p id="errMsg">-</p>
        <button class="new-btn" onclick="resetGen()">TEKRAR DENE</button>
      </div>
    </div>
  </div>

  <!-- GALLERY -->
  <div class="panel" id="pGallery">
    <div class="card" style="padding:16px;margin-bottom:16px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <input type="text" id="galSearch" placeholder="Ara..." style="flex:1;min-width:150px;background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:8px 12px;font-family:'DM Mono',monospace;font-size:11px">
      <select id="galSort" style="background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:8px;font-family:'DM Mono',monospace;font-size:10px">
        <option value="newest">En Yeni</option><option value="popular">Populer</option><option value="downloads">Indirilen</option>
      </select>
      <button class="nbtn" onclick="loadGallery()">ARA</button>
    </div>
    <div class="gal-grid" id="galGrid"><div class="gal-empty">Yukleniyor...</div></div>
  </div>

  <!-- MY MODELS -->
  <div class="panel" id="pMyModels">
    <div class="gal-grid" id="myGrid"><div class="gal-empty">Giris yaparak modellerinizi gorun</div></div>
  </div>
</div>

<script>
var API=window.location.origin;
var token=localStorage.getItem('pf_token')||'';
var user=null;
var style='realistic',selFile=null,poll=null;

function headers(){var h={'Content-Type':'application/json'};if(token)h['Authorization']='Bearer '+token;return h}
function authHeaders(){var h={};if(token)h['Authorization']='Bearer '+token;return h}

// === API STATUS ===
function checkApi(){
  fetch(API+'/api/health').then(function(r){return r.json()}).then(function(d){
    var el=document.getElementById('apiSt');
    if(d.is_demo){el.innerHTML='<div class="nav-dot" style="background:var(--orange);animation:pulse 2s infinite"></div><span style="color:var(--orange)">DEMO</span>';document.getElementById('demoBanner').style.display='block'}
    else{el.innerHTML='<div class="nav-dot" style="background:var(--accent2);animation:pulse 2s infinite"></div><span style="color:var(--accent2)">'+d.active_api.toUpperCase()+'</span>'}
  }).catch(function(){});
}

// === AUTH ===
function openAuth(tab){document.getElementById('authOverlay').classList.add('on');authTab(tab||'login')}
function closeAuth(){document.getElementById('authOverlay').classList.remove('on');document.getElementById('authMsg').className='auth-msg'}
function authTab(t){
  document.getElementById('loginForm').style.display=t==='login'?'block':'none';
  document.getElementById('regForm').style.display=t==='register'?'block':'none';
  document.getElementById('aLoginTab').className='auth-tab'+(t==='login'?' on':'');
  document.getElementById('aRegTab').className='auth-tab'+(t==='register'?' on':'');
  document.getElementById('authMsg').className='auth-msg';
}
function authErr(m){var e=document.getElementById('authMsg');e.className='auth-msg err';e.textContent=m}
function authOk(m){var e=document.getElementById('authMsg');e.className='auth-msg ok';e.textContent=m}

function doLogin(){
  var email=document.getElementById('lEmail').value.trim();
  var pass=document.getElementById('lPass').value;
  if(!email||!pass){authErr('Tum alanlari doldurun');return}
  fetch(API+'/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email,password:pass})})
  .then(function(r){if(!r.ok)return r.json().then(function(e){throw new Error(e.detail)});return r.json()})
  .then(function(d){token=d.token;user=d.user;localStorage.setItem('pf_token',token);authOk('Giris basarili!');setTimeout(function(){closeAuth();updateUI()},800)})
  .catch(function(e){authErr(e.message)});
}

function doRegister(){
  var name=document.getElementById('rName').value.trim();
  var email=document.getElementById('rEmail').value.trim();
  var pass=document.getElementById('rPass').value;
  if(!name||!email||!pass){authErr('Tum alanlari doldurun');return}
  if(pass.length<6){authErr('Sifre en az 6 karakter');return}
  fetch(API+'/api/auth/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name,email:email,password:pass})})
  .then(function(r){if(!r.ok)return r.json().then(function(e){throw new Error(e.detail)});return r.json()})
  .then(function(d){token=d.token;user=d.user;localStorage.setItem('pf_token',token);authOk('Hesap olusturuldu!');setTimeout(function(){closeAuth();updateUI()},800)})
  .catch(function(e){authErr(e.message)});
}

function doLogout(){token='';user=null;localStorage.removeItem('pf_token');updateUI()}

function checkAuth(){
  if(!token)return;
  fetch(API+'/api/auth/me',{headers:authHeaders()})
  .then(function(r){if(!r.ok)throw new Error();return r.json()})
  .then(function(d){user=d.user;updateUI();
    var ub=document.getElementById('usageBanner');
    ub.textContent=d.usage.remaining+' / '+d.usage.limit+' model kaldi bu ay';
    ub.style.display='block';
    document.getElementById('navUsage').textContent=d.usage.remaining+'/'+d.usage.limit;
  })
  .catch(function(){token='';localStorage.removeItem('pf_token');updateUI()});
}

function updateUI(){
  if(user){
    document.getElementById('navGuest').style.display='none';
    document.getElementById('navUser').style.display='flex';
    document.getElementById('navName').textContent=user.name;
    document.getElementById('navAvatar').textContent=user.name[0].toUpperCase();
  }else{
    document.getElementById('navGuest').style.display='flex';
    document.getElementById('navUser').style.display='none';
    document.getElementById('usageBanner').style.display='none';
  }
}

// === TABS ===
function swTab(t){
  ['gen','gallery','mymodels'].forEach(function(x){document.getElementById('p'+x.charAt(0).toUpperCase()+x.slice(1)).className='panel'+(x===t?' on':'')});
  var tabs=document.querySelectorAll('.container > .tabs .tab');
  tabs[0].className='tab'+(t==='gen'?' on':'');
  tabs[1].className='tab'+(t==='gallery'?' on':'');
  tabs[2].className='tab'+(t==='mymodels'?' on':'');
  if(t==='gallery')loadGallery();
  if(t==='mymodels')loadMyModels();
}
// Fix panel IDs
document.getElementById('pGen').id='pGen';
document.getElementById('pGallery').id='pGallery';
document.getElementById('pMyModels').id='pMymodels';

function swSub(t){
  document.getElementById('pText').className='panel'+(t==='text'?' on':'');
  document.getElementById('pImage').className='panel'+(t==='image'?' on':'');
  var st=document.querySelectorAll('#pGen > .tabs .tab');
  st[0].className='tab'+(t==='text'?' on':'');
  st[1].className='tab'+(t==='image'?' on':'');
}

function setP(t){document.getElementById('prompt').value=t}
function selS(el){document.querySelectorAll('.style-opt').forEach(function(s){s.className='style-opt'});el.className='style-opt on';style=el.getAttribute('data-s')}

// === FILE ===
var upArea=document.getElementById('upArea');
upArea.addEventListener('dragover',function(e){e.preventDefault();upArea.classList.add('drag')});
upArea.addEventListener('dragleave',function(){upArea.classList.remove('drag')});
upArea.addEventListener('drop',function(e){e.preventDefault();upArea.classList.remove('drag');if(e.dataTransfer.files[0]){document.getElementById('fInp').files=e.dataTransfer.files;onFile(document.getElementById('fInp'))}});
function onFile(inp){var f=inp.files[0];if(!f)return;if(f.size>10485760){alert('Max 10MB');return}selFile=f;var rd=new FileReader();rd.onload=function(e){document.getElementById('prevImg').src=e.target.result;document.getElementById('prev').className='preview on';upArea.classList.add('has');document.getElementById('imgBtn').disabled=false};rd.readAsDataURL(f)}
function rmFile(){selFile=null;document.getElementById('fInp').value='';document.getElementById('prev').className='preview';upArea.classList.remove('has');document.getElementById('imgBtn').disabled=true}

// === GENERATE ===
function genText(){
  var p=document.getElementById('prompt').value.trim();if(!p){alert('Prompt girin!');return}
  showProg();disable(true);
  fetch(API+'/api/generate/text',{method:'POST',headers:headers(),body:JSON.stringify({prompt:p,style:style})})
  .then(function(r){if(!r.ok)return r.json().then(function(e){throw new Error(e.detail)});return r.json()})
  .then(function(d){startPoll(d.task_id)})
  .catch(function(e){showErr(e.message)});
}
function genImage(){
  if(!selFile)return;showProg();disable(true);
  var fd=new FormData();fd.append('file',selFile);
  var h={};if(token)h['Authorization']='Bearer '+token;
  fetch(API+'/api/generate/image',{method:'POST',body:fd,headers:h})
  .then(function(r){if(!r.ok)return r.json().then(function(e){throw new Error(e.detail)});return r.json()})
  .then(function(d){startPoll(d.task_id)})
  .catch(function(e){showErr(e.message)});
}

function startPoll(tid){
  if(poll)clearInterval(poll);
  poll=setInterval(function(){
    fetch(API+'/api/status/'+tid).then(function(r){return r.json()}).then(function(d){
      updProg(d.progress,d.step||'...');
      if(d.status==='done'){clearInterval(poll);showRes(tid,d);if(user)checkAuth()}
      else if(d.status==='failed'){clearInterval(poll);showErr(d.error||'Uretilemedi')}
    }).catch(function(){});
  },2500);
}

function showProg(){hide('resSec');hide('errSec');show('progSec');updProg(0,'Baslatiliyor...')}
function updProg(p,s){document.getElementById('progBar').style.width=p+'%';document.getElementById('progPct').textContent=p+'%';document.getElementById('progStep').innerHTML='<div class="spinner"></div><span>'+s+'</span>'}
function showRes(tid,d){
  hide('progSec');show('resSec');disable(false);
  var vw=API+'/api/model/'+tid+'/view';
  document.getElementById('viewer3d').innerHTML='<model-viewer src="'+vw+'" auto-rotate camera-controls style="width:100%;height:100%;background:#070d10" loading="eager" shadow-intensity="1" environment-image="neutral" camera-orbit="45deg 55deg auto"></model-viewer>';
  document.getElementById('dlGrid').innerHTML='<a class="dl-btn primary" href="'+API+'/api/model/'+tid+'/glb" download>GLB<span class="dl-fmt">3D VIEWER</span></a><a class="dl-btn" href="'+API+'/api/model/'+tid+'/stl" download>STL<span class="dl-fmt">3D BASKI</span></a><a class="dl-btn" href="'+API+'/api/model/'+tid+'/obj" download>OBJ<span class="dl-fmt">MODELLEME</span></a>';
}
function showErr(m){hide('progSec');show('errSec');disable(false);document.getElementById('errMsg').textContent=m}
function resetGen(){if(poll)clearInterval(poll);hide('progSec');hide('resSec');hide('errSec');disable(false);document.getElementById('viewer3d').innerHTML=''}
function show(id){document.getElementById(id).classList.add('on')}
function hide(id){document.getElementById(id).classList.remove('on')}
function disable(v){document.getElementById('txtBtn').disabled=v;document.getElementById('imgBtn').disabled=v||!selFile}

// === GALLERY ===
function loadGallery(){
  var search=document.getElementById('galSearch').value||'';
  var sort=document.getElementById('galSort').value||'newest';
  document.getElementById('galGrid').innerHTML='<div class="gal-empty">Yukleniyor...</div>';
  fetch(API+'/api/gallery?search='+encodeURIComponent(search)+'&sort='+sort)
  .then(function(r){return r.json()})
  .then(function(d){
    if(!d.models||d.models.length===0){document.getElementById('galGrid').innerHTML='<div class="gal-empty">Henuz model yok</div>';return}
    var html='';
    d.models.forEach(function(m){
      var viewUrl=m.task_id?API+'/api/model/'+m.task_id+'/view':'';
      var thumb=viewUrl?'<model-viewer src="'+viewUrl+'" auto-rotate camera-controls style="width:100%;height:100%;background:#070d10" loading="lazy"></model-viewer>':'<div style="color:var(--muted);font-size:10px">3D</div>';
      html+='<div class="gal-card"><div class="gal-thumb">'+thumb+'</div><div class="gal-body"><div class="gal-title">'+(m.title||'Model')+'</div><div class="gal-meta"><span>'+(m.author_name||'Anonim')+'</span><span>'+m.likes+' &#9829; '+m.downloads+' &#8595;</span></div><div class="gal-actions">';
      if(m.task_id)html+='<a class="gal-btn" href="'+API+'/api/model/'+m.task_id+'/glb" download>GLB</a><a class="gal-btn" href="'+API+'/api/model/'+m.task_id+'/stl" download>STL</a>';
      html+='<button class="gal-btn" onclick="likeModel('+m.id+',this)">&#9829;</button></div></div></div>';
    });
    document.getElementById('galGrid').innerHTML=html;
  }).catch(function(){document.getElementById('galGrid').innerHTML='<div class="gal-empty">Yuklenemedi</div>'});
}

function likeModel(id,btn){
  if(!token){openAuth('login');return}
  fetch(API+'/api/gallery/'+id+'/like',{method:'POST',headers:authHeaders()})
  .then(function(r){return r.json()})
  .then(function(d){btn.className='gal-btn'+(d.liked?' liked':'');loadGallery()})
  .catch(function(){});
}

// === MY MODELS ===
function loadMyModels(){
  if(!token){document.getElementById('myGrid').innerHTML='<div class="gal-empty">Modellerinizi gormek icin <a href="#" onclick="openAuth(\'login\');return false" style="color:var(--accent)">giris yapin</a></div>';return}
  document.getElementById('myGrid').innerHTML='<div class="gal-empty">Yukleniyor...</div>';
  fetch(API+'/api/my-models',{headers:authHeaders()})
  .then(function(r){return r.json()})
  .then(function(d){
    if(!d.models||d.models.length===0){document.getElementById('myGrid').innerHTML='<div class="gal-empty">Henuz modeliniz yok. Uret sekmesinden baslayabilirsiniz.</div>';return}
    var html='';
    d.models.forEach(function(m){
      var viewUrl=m.task_id?API+'/api/model/'+m.task_id+'/view':'';
      var thumb=viewUrl?'<model-viewer src="'+viewUrl+'" auto-rotate camera-controls style="width:100%;height:100%;background:#070d10" loading="lazy"></model-viewer>':'';
      html+='<div class="gal-card"><div class="gal-thumb">'+thumb+'</div><div class="gal-body"><div class="gal-title">'+(m.title||'Model')+'</div><div class="gal-meta"><span>'+m.gen_type+'</span><span>'+(m.created_at||'').slice(0,10)+'</span></div><div class="gal-actions">';
      if(m.task_id){html+='<a class="gal-btn" href="'+API+'/api/model/'+m.task_id+'/glb" download>GLB</a><a class="gal-btn" href="'+API+'/api/model/'+m.task_id+'/stl" download>STL</a>'}
      html+='<button class="gal-btn" style="color:var(--red)" onclick="deleteModel('+m.id+')">SIL</button></div></div></div>';
    });
    document.getElementById('myGrid').innerHTML=html;
  }).catch(function(){});
}

function deleteModel(id){
  if(!confirm('Bu modeli silmek istiyor musunuz?'))return;
  fetch(API+'/api/my-models/'+id,{method:'DELETE',headers:authHeaders()})
  .then(function(){loadMyModels()}).catch(function(){});
}

// === INIT ===
checkApi();
checkAuth();
</script>
</body>
</html>"""
