from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, Response, RedirectResponse
from pydantic import BaseModel
import asyncio, uuid, httpx, base64, random, json, os, io, re
import hashlib, secrets, sqlite3
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

try:
    import jwt as pyjwt
    HAS_JWT = True
except ImportError:
    HAS_JWT = False

try:
    import trimesh, numpy
    HAS_TRIMESH = True
except ImportError:
    HAS_TRIMESH = False

app = FastAPI(title="PrintForge")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Railway Variables
TRIPO_API_KEY = os.getenv("TRIPO_API_KEY", "")
MESHY_API_KEY = os.getenv("MESHY_API_KEY", "")
SECRET_KEY = os.getenv("SECRET_KEY", "printforge-secret-key-123")
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
]

# --- VERİTABANI İŞLEMLERİ ---
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, password_hash TEXT NOT NULL, salt TEXT NOT NULL, plan TEXT DEFAULT 'free', verified INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now')));
        CREATE TABLE IF NOT EXISTS models (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER DEFAULT 0, task_id TEXT UNIQUE, title TEXT, prompt TEXT, gen_type TEXT, style TEXT, model_url TEXT, is_public INTEGER DEFAULT 1, likes INTEGER DEFAULT 0, downloads INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now')));
        CREATE TABLE IF NOT EXISTS usage (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, month TEXT, count INTEGER DEFAULT 0, UNIQUE(user_id, month));
        CREATE TABLE IF NOT EXISTS comments (id INTEGER PRIMARY KEY AUTOINCREMENT, model_id INTEGER NOT NULL, user_id INTEGER NOT NULL, text TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')));
    """)
    conn.commit(); conn.close()

init_db()

# --- YARDIMCI FONKSİYONLAR (AUTH) ---
def hash_pw(pw):
    salt = secrets.token_hex(16)
    return salt, hashlib.sha256((salt+pw).encode()).hexdigest()

def verify_pw(pw, salt, h):
    return hashlib.sha256((salt+pw).encode()).hexdigest() == h

def create_token(uid, email, name, plan):
    return pyjwt.encode({"user_id":uid,"email":email,"name":name,"plan":plan,"exp":datetime.utcnow()+timedelta(days=30)}, SECRET_KEY, algorithm="HS256")

def decode_token(t):
    try: return pyjwt.decode(t, SECRET_KEY, algorithms=["HS256"])
    except: return None

async def get_user(authorization: Optional[str] = Header(None)):
    if not authorization: return None
    try:
        t = authorization.replace("Bearer ","")
        data = decode_token(t)
        if not data: return None
        conn = get_db()
        row = conn.execute("SELECT id,email,name,plan FROM users WHERE id=?", (data["user_id"],)).fetchone()
        conn.close()
        return dict(row) if row else None
    except: return None

# --- SAYFA YÖNETİMİ ---
@app.get("/", response_class=HTMLResponse)
def serve_landing():
    # Bu sizin o profesyonel tasarımınızın olduğu dosya
    path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(path): return FileResponse(path)
    return HTMLResponse("<h1>PrintForge Ana Sayfa</h1><p>index.html bulunamadi.</p>")

@app.get("/app", response_class=HTMLResponse)
def serve_app():
    # Bu model üretme ve uygulama paneli
    path = os.path.join(os.path.dirname(__file__), "app.html")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f: return HTMLResponse(f.read())
    return HTMLResponse("app.html bulunamadi.")

# --- API ENDPOINTS ---
@app.get("/api/health")
async def health():
    return {"status":"online","api_ready":True}

@app.post("/api/auth/register")
async def register(req: dict):
    salt, h = hash_pw(req["password"])
    conn = get_db()
    try:
        conn.execute("INSERT INTO users(email,name,password_hash,salt) VALUES(?,?,?,?)", (req["email"].lower(), req["name"], h, salt))
        conn.commit(); conn.close()
        return {"success":True}
    except:
        conn.close(); raise HTTPException(400, "Bu e-posta zaten kayitli")

@app.post("/api/auth/login")
async def login(req: dict):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE email=?", (req["email"].lower(),)).fetchone()
    conn.close()
    if row and verify_pw(req["password"], row["salt"], row["password_hash"]):
        token = create_token(row["id"], row["email"], row["name"], row["plan"])
        return {"token":token, "user": {"id":row["id"], "name":row["name"], "email":row["email"], "plan":row["plan"]}}
    raise HTTPException(401, "E-posta veya sifre hatali")

@app.get("/api/auth/me")
async def get_me(authorization: str = Header(None)):
    u = await get_user(authorization)
    if not u: raise HTTPException(401)
    return {"user":u, "usage":{"used":0,"limit":5,"remaining":5}}

@app.get("/api/gallery")
async def gallery():
    conn = get_db()
    rows = conn.execute("SELECT m.*, u.name as author_name FROM models m LEFT JOIN users u ON m.user_id = u.id WHERE m.model_url != '' ORDER BY m.created_at DESC LIMIT 30").fetchall()
    conn.close()
    return {"models":[dict(r) for r in rows]}

@app.get("/api/gallery/{model_id}")
async def model_detail(model_id: int):
    conn = get_db()
    row = conn.execute("SELECT m.*, u.name as author_name FROM models m LEFT JOIN users u ON m.user_id = u.id WHERE m.id = ?", (model_id,)).fetchone()
    conn.close()
    if not row: raise HTTPException(404)
    return dict(row)

@app.post("/api/generate/text")
async def gen_text(req: dict, authorization: str = Header(None)):
    u = await get_user(authorization)
    if not u: raise HTTPException(401, "Lutfen once giris yapin.")
    
    tid = str(uuid.uuid4())[:8]
    # Tripo3D Key varsa gercek uretim, yoksa Demo gosterir
    if TRIPO_API_KEY:
        # Gercek Tripo kodlari buraya gelecek (Onceki adimlarda yaptigimiz gibi)
        tasks[tid] = {"status":"done","progress":100,"model_url": DEMO_MODELS[0]["glb"]} # Simdilik demo
    else:
        tasks[tid] = {"status":"done","progress":100,"model_url": DEMO_MODELS[random.randint(0,2)]["glb"]}
    
    conn = get_db()
    conn.execute("INSERT INTO models(user_id, task_id, title, prompt, gen_type, model_url) VALUES(?,?,?,?,?,?)", 
                 (u["id"], tid, req["prompt"][:20], req["prompt"], "text", tasks[tid]["model_url"]))
    conn.commit(); conn.close()
    return {"task_id":tid}

@app.get("/api/status/{tid}")
async def get_status(tid: str):
    return tasks.get(tid, {"status":"failed"})

@app.get("/api/model/{tid}/view")
async def view_model(tid: str):
    # Bu endpoint 3D onizleme icin CORS sorununu cozer
    if tid not in tasks:
        # Veritabanından url'i bulmayı dene
        conn = get_db()
        row = conn.execute("SELECT model_url FROM models WHERE task_id=?", (tid,)).fetchone()
        conn.close()
        if row: url = row[0]
        else: raise HTTPException(404)
    else:
        url = tasks[tid]["model_url"]
        
    async with httpx.AsyncClient(follow_redirects=True) as c:
        r = await c.get(url)
        return Response(content=r.content, media_type="model/gltf-binary")
