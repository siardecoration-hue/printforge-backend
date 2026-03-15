from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, Response, RedirectResponse
from pydantic import BaseModel
import asyncio, uuid, httpx, base64, random, json, os, io, re
import hashlib, secrets, sqlite3
from datetime import datetime, timedelta
from typing import Optional

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

TRIPO_API_KEY = os.getenv("TRIPO_API_KEY", "")
SECRET_KEY = os.getenv("SECRET_KEY", "printforge-key-8899")
DB_PATH = os.getenv("DB_PATH", "printforge.db")
TRIPO_BASE = "https://api.tripo3d.ai/v2/openapi"

tasks = {}
DEMO_MODELS = [
    {"glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb"},
    {"glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/Avocado/glTF-Binary/Avocado.glb"}
]

# --- DB ---
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, password_hash TEXT NOT NULL, salt TEXT NOT NULL, plan TEXT DEFAULT 'free');
        CREATE TABLE IF NOT EXISTS models (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, task_id TEXT UNIQUE, title TEXT, prompt TEXT, model_url TEXT, created_at TEXT DEFAULT (datetime('now')));
    """)
    conn.commit(); conn.close()
init_db()

# --- AUTH ---
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
    t = authorization.replace("Bearer ","")
    data = decode_token(t)
    if not data: return None
    conn = get_db()
    row = conn.execute("SELECT id,email,name,plan FROM users WHERE id=?", (data["user_id"],)).fetchone()
    conn.close()
    return dict(row) if row else None

# --- ROUTES ---
@app.get("/", response_class=HTMLResponse)
def serve_landing():
    return FileResponse("index.html")

@app.get("/app", response_class=HTMLResponse)
def serve_app():
    return FileResponse("app.html")

@app.post("/api/auth/register")
async def register(req: dict):
    salt, h = hash_pw(req["password"])
    conn = get_db()
    try:
        conn.execute("INSERT INTO users(email,name,password_hash,salt) VALUES(?,?,?,?)", (req["email"].lower(), req["name"], h, salt))
        conn.commit(); conn.close()
        return {"success":True}
    except: raise HTTPException(400, "E-posta zaten kayitli.")

@app.post("/api/auth/login")
async def login(req: dict):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE email=?", (req["email"].lower(),)).fetchone()
    conn.close()
    if row and verify_pw(req["password"], row["salt"], row["password_hash"]):
        token = create_token(row["id"], row["email"], row["name"], row["plan"])
        return {"token":token, "user":dict(row)}
    raise HTTPException(401, "Giris hatali.")

@app.get("/api/gallery")
async def gallery():
    conn = get_db()
    rows = conn.execute("SELECT m.*, u.name as author_name FROM models m LEFT JOIN users u ON m.user_id = u.id ORDER BY m.created_at DESC LIMIT 20").fetchall()
    conn.close()
    return {"models":[dict(r) for r in rows]}

# --- GENERATION ---
async def poll_tripo(tid, tripo_id, headers, uid, prompt):
    async with httpx.AsyncClient(timeout=600) as client:
        for _ in range(100):
            await asyncio.sleep(5)
            r = await client.get(f"{TRIPO_BASE}/task/{tripo_id}", headers=headers)
            data = r.json().get("data", {})
            if data.get("status") == "success":
                url = data.get("output", {}).get("model")
                tasks[tid] = {"status":"done", "model_url": url}
                conn = get_db()
                conn.execute("INSERT INTO models(user_id, task_id, title, prompt, model_url) VALUES(?,?,?,?,?)", (uid, tid, prompt[:20], prompt, url))
                conn.commit(); conn.close()
                return
            if data.get("status") == "failed":
                tasks[tid] = {"status":"failed"}
                return

@app.post("/api/generate/text")
async def gen_text(req: dict, authorization: str = Header(None)):
    u = await get_user(authorization)
    if not u: raise HTTPException(401)
    tid = str(uuid.uuid4())[:8]
    tasks[tid] = {"status":"processing"}
    
    if not TRIPO_API_KEY:
        tasks[tid] = {"status":"done", "model_url": DEMO_MODELS[0]["glb"]}
        return {"task_id":tid}

    headers = {"Authorization": f"Bearer {TRIPO_API_KEY}"}
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{TRIPO_BASE}/task", headers=headers, json={"type":"text_to_model", "prompt":req["prompt"]})
        tripo_id = r.json()["data"]["task_id"]
        asyncio.create_task(poll_tripo(tid, tripo_id, headers, u["id"], req["prompt"]))
    return {"task_id":tid}

@app.get("/api/status/{tid}")
async def get_status(tid: str):
    return tasks.get(tid, {"status":"processing"})

@app.get("/api/model/{tid}/view")
async def view_model(tid: str):
    conn = get_db()
    row = conn.execute("SELECT model_url FROM models WHERE task_id=?", (tid,)).fetchone()
    conn.close()
    url = row[0] if row else tasks.get(tid, {}).get("model_url")
    if not url: raise HTTPException(404)
    async with httpx.AsyncClient(follow_redirects=True) as c:
        r = await c.get(url)
        return Response(content=r.content, media_type="model/gltf-binary")
        @app.post("/api/generate/image")
async def gen_image(file: UploadFile = File(...), authorization: str = Header(None)):
    u = await get_user(authorization)
    if not u: raise HTTPException(401)
    
    tid = str(uuid.uuid4())[:8]
    tasks[tid] = {"status":"processing"}
    
    # Görsel işleme ve Tripo'ya gönderme mantığı
    # Şimdilik Demo Modda Çalışır (Gerçek Tripo API için Tripo dökümanına göre upload eklenmeli)
    tasks[tid] = {"status":"done", "progress":100, "model_url": DEMO_MODELS[1]["glb"]}
    
    conn = get_db()
    conn.execute("INSERT INTO models(user_id, task_id, title, prompt, model_url) VALUES(?,?,?,?,?)", 
                 (u["id"], tid, "Görselden Model", file.filename, tasks[tid]["model_url"]))
    conn.commit(); conn.close()
    
    return {"task_id":tid}
