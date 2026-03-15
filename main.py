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

TRIPO_API_KEY = os.getenv("TRIPO_API_KEY", "")
MESHY_API_KEY = os.getenv("MESHY_API_KEY", "")
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))
DB_PATH = os.getenv("DB_PATH", "printforge.db")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
TRIPO_BASE = "https://api.tripo3d.ai/v2/openapi"
MESHY_BASE = "https://api.meshy.ai/openapi/v2"

def get_site_url():
    d = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
    return f"https://{d}" if d else "http://localhost:8000"

tasks = {}
model_cache = {}
MAX_CACHE = 50
PLAN_LIMITS = {"free": 5, "pro": 100, "business": 999999}
PLAN_NAMES = {"free": "Ucretsiz", "pro": "Pro", "business": "Isletme"}

DEMO_MODELS = [
    {"name": "Damaged Helmet", "glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb"},
    {"name": "Avocado", "glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/Avocado/glTF-Binary/Avocado.glb"},
    {"name": "Duck", "glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/Duck/glTF-Binary/Duck.glb"},
    {"name": "Lantern", "glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/Lantern/glTF-Binary/Lantern.glb"},
    {"name": "Water Bottle", "glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/WaterBottle/glTF-Binary/WaterBottle.glb"},
]

BLOCKED_DOMAINS = set([
    "tempmail.com","throwaway.email","guerrillamail.com","mailinator.com",
    "yopmail.com","sharklasers.com","guerrillamailblock.com","grr.la",
    "dispostable.com","trashmail.com","trashmail.net","10minutemail.com",
    "temp-mail.org","tempail.com","tmpmail.net","mohmal.com","getnada.com",
    "emailondeck.com","33mail.com","maildrop.cc","inboxbear.com",
    "fakeinbox.com","tmpmail.org","tempinbox.com","bupmail.com",
    "burnermail.io","discard.email","discardmail.com","mytemp.email",
    "temp-mail.io","wegwerfmail.de","trash-mail.com","safetymail.info",
    "spamgourmet.com","mailnesia.com","mailcatch.com","jetable.org",
    "filzmail.com","trbvm.com","harakirimail.com","crazymailing.com",
    "guerrillamail.info","guerrillamail.net","guerrillamail.org",
    "guerrillamail.de","zetmail.com","spamfree24.org","trashymail.com",
    "kasmail.com","mytrashmail.com","mailexpire.com","throwam.com",
    "mailnull.com","e4ward.com","mailmoat.com","incognitomail.org",
    "mailshell.com","mailzilla.com","tempmailaddress.com",
    "meltmail.com","getairmail.com","mailsac.com","drdrb.com",
])

ALLOWED_DOMAINS = set([
    "gmail.com","googlemail.com","outlook.com","outlook.com.tr",
    "hotmail.com","hotmail.com.tr","live.com","live.com.tr",
    "yahoo.com","yahoo.com.tr","yandex.com","yandex.com.tr",
    "icloud.com","me.com","mac.com","protonmail.com","proton.me",
    "aol.com","mail.com","zoho.com","gmx.com","gmx.net","msn.com",
])

BLOCKED_PATTERNS = [
    r"^test\d*@", r"^fake\d*@", r"^spam\d*@", r"^trash\d*@",
    r"^temp\d*@", r"^dummy\d*@", r"^noreply@", r"^no-reply@",
    r"^asdf+@", r"^qwer+@", r"^xxx+@", r"^aaa+@",
    r"^111+@", r"^123+@", r"^\d{8,}@",
]

async def verify_email_dns(domain):
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"https://dns.google/resolve?name={domain}&type=MX")
            if r.json().get("Answer"): return True
            r2 = await c.get(f"https://dns.google/resolve?name={domain}&type=A")
            return bool(r2.json().get("Answer"))
    except:
        return True

async def validate_email(email):
    email = email.lower().strip()
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9._%+-]*@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return False, "Gecerli bir e-posta girin (ornek: isim@gmail.com)"
    local, domain = email.split("@", 1)
    if len(local) < 2: return False, "E-posta cok kisa"
    if len(domain) < 4: return False, "Gecerli bir e-posta saglayicisi kullanin"
    if domain in BLOCKED_DOMAINS: return False, "Gecici e-posta kabul edilmiyor. Gmail, Outlook veya Yahoo kullanin."
    for b in BLOCKED_DOMAINS:
        if domain.endswith("."+b): return False, "Bu e-posta saglayicisi kabul edilmiyor."
    for pat in BLOCKED_PATTERNS:
        if re.match(pat, email): return False, "Bu e-posta gecersiz. Gercek e-posta adresinizi kullanin."
    if local.replace(".","").replace("-","").replace("_","").isdigit(): return False, "Gercek bir e-posta kullanin"
    for ch in set(local):
        if ch * 4 in local: return False, "Gecerli bir e-posta girin"
    if domain not in ALLOWED_DOMAINS:
        if not await verify_email_dns(domain): return False, "Bu e-posta domaini bulunamadi."
    return True, "OK"

class TextRequest(BaseModel):
    prompt: str
    style: str = "realistic"
class RegisterReq(BaseModel):
    name: str
    email: str
    password: str
class LoginReq(BaseModel):
    email: str
    password: str
class UpdateProfileReq(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None

STYLE_MAP = {"realistic":"realistic","cartoon":"cartoon","lowpoly":"low-poly","sculpture":"sculpture","mechanical":"pbr","miniature":"sculpture","geometric":"realistic"}

def get_api():
    if TRIPO_API_KEY: return "tripo"
    if MESHY_API_KEY: return "meshy"
    return "demo"

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, password_hash TEXT NOT NULL, salt TEXT NOT NULL, plan TEXT DEFAULT 'free', google_id TEXT, avatar_url TEXT, created_at TEXT DEFAULT (datetime('now')));
        CREATE TABLE IF NOT EXISTS models (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER DEFAULT 0, task_id TEXT UNIQUE, title TEXT, prompt TEXT, gen_type TEXT, style TEXT, model_url TEXT, is_public INTEGER DEFAULT 1, likes INTEGER DEFAULT 0, downloads INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now')));
        CREATE TABLE IF NOT EXISTS usage (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, month TEXT, count INTEGER DEFAULT 0, UNIQUE(user_id, month));
        CREATE TABLE IF NOT EXISTS user_likes (user_id INTEGER, model_id INTEGER, PRIMARY KEY(user_id, model_id));
    """)
    conn.commit(); conn.close()

init_db()

@app.on_event("startup")
async def startup():
    init_db()
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        try: os.makedirs(db_dir, exist_ok=True)
        except: pass

def hash_pw(pw):
    salt = secrets.token_hex(16)
    return salt, hashlib.sha256((salt+pw).encode()).hexdigest()
def verify_pw(pw, salt, h):
    return hashlib.sha256((salt+pw).encode()).hexdigest() == h
def create_token(uid, email, name, plan):
    if not HAS_JWT: return "no-jwt"
    return pyjwt.encode({"user_id":uid,"email":email,"name":name,"plan":plan,"exp":datetime.utcnow()+timedelta(days=30)}, SECRET_KEY, algorithm="HS256")
def decode_token(t):
    if not HAS_JWT: return None
    try: return pyjwt.decode(t, SECRET_KEY, algorithms=["HS256"])
    except: return None

async def get_user(authorization: Optional[str] = Header(None)):
    if not authorization: return None
    data = decode_token(authorization.replace("Bearer ",""))
    if not data: return None
    conn = get_db()
    row = conn.execute("SELECT id,email,name,plan,avatar_url,created_at FROM users WHERE id=?", (data["user_id"],)).fetchone()
    conn.close()
    return {"id":row[0],"email":row[1],"name":row[2],"plan":row[3],"avatar_url":row[4],"created_at":row[5]} if row else None

def get_usage(uid):
    month = datetime.now().strftime("%Y-%m")
    conn = get_db()
    row = conn.execute("SELECT count FROM usage WHERE user_id=? AND month=?", (uid, month)).fetchone()
    conn.close()
    return row[0] if row else 0
def add_usage(uid):
    month = datetime.now().strftime("%Y-%m")
    conn = get_db()
    conn.execute("INSERT INTO usage(user_id,month,count) VALUES(?,?,1) ON CONFLICT(user_id,month) DO UPDATE SET count=count+1", (uid, month))
    conn.commit(); conn.close()
def save_model(uid, tid, title, prompt, gtype, style, url):
    conn = get_db()
    try: conn.execute("INSERT INTO models(user_id,task_id,title,prompt,gen_type,style,model_url) VALUES(?,?,?,?,?,?,?)", (uid,tid,title,prompt,gtype,style,url))
    except: pass
    conn.commit(); conn.close()

def get_user_stats(uid):
    conn = get_db()
    model_count = conn.execute("SELECT COUNT(*) FROM models WHERE user_id=?", (uid,)).fetchone()[0]
    total_likes = conn.execute("SELECT COALESCE(SUM(likes),0) FROM models WHERE user_id=?", (uid,)).fetchone()[0]
    total_downloads = conn.execute("SELECT COALESCE(SUM(downloads),0) FROM models WHERE user_id=?", (uid,)).fetchone()[0]
    conn.close()
    return {"models": model_count, "likes": total_likes, "downloads": total_downloads}

# ════════ PAGES ════════
@app.get("/", response_class=HTMLResponse)
def serve_landing():
    for name in ["index.html","printforge.html"]:
        path = os.path.join(os.path.dirname(__file__), name)
        if os.path.exists(path): return FileResponse(path, media_type="text/html")
    return HTMLResponse('<html><body style="background:#04080a;color:#00e5ff;display:flex;align-items:center;justify-content:center;height:100vh"><a href="/app" style="color:#00e5ff;font-size:24px">PrintForge /app</a></body></html>')

@app.get("/app", response_class=HTMLResponse)
def serve_app():
    return HTMLResponse(APP_HTML)

# ════════ AUTH ════════
@app.post("/api/auth/register")
async def register(req: RegisterReq):
    if len(req.password) < 6: raise HTTPException(400, "Sifre en az 6 karakter")
    if not req.name.strip() or len(req.name.strip()) < 2: raise HTTPException(400, "Gecerli bir isim girin")
    valid, msg = await validate_email(req.email)
    if not valid: raise HTTPException(400, msg)
    salt, h = hash_pw(req.password)
    conn = get_db()
    try:
        conn.execute("INSERT INTO users(email,name,password_hash,salt) VALUES(?,?,?,?)", (req.email.lower().strip(), req.name.strip(), h, salt))
        conn.commit()
        uid = conn.execute("SELECT id FROM users WHERE email=?", (req.email.lower().strip(),)).fetchone()[0]
        conn.close()
    except sqlite3.IntegrityError:
        conn.close(); raise HTTPException(400, "Bu e-posta zaten kayitli")
    token = create_token(uid, req.email, req.name, "free")
    return {"token": token, "user": {"id":uid,"name":req.name,"email":req.email,"plan":"free"}}

@app.post("/api/auth/login")
async def login(req: LoginReq):
    conn = get_db()
    row = conn.execute("SELECT id,email,name,password_hash,salt,plan FROM users WHERE email=?", (req.email.lower().strip(),)).fetchone()
    conn.close()
    if not row: raise HTTPException(401, "E-posta veya sifre hatali")
    if not verify_pw(req.password, row["salt"], row["password_hash"]): raise HTTPException(401, "E-posta veya sifre hatali")
    token = create_token(row["id"], row["email"], row["name"], row["plan"])
    return {"token": token, "user": {"id":row["id"],"name":row["name"],"email":row["email"],"plan":row["plan"]}}

@app.get("/api/auth/me")
async def get_me(authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Giris yapin")
    used = get_usage(user["id"])
    limit = PLAN_LIMITS.get(user["plan"], 5)
    stats = get_user_stats(user["id"])
    return {"user": user, "usage": {"used":used,"limit":limit,"remaining":max(0,limit-used)}, "stats": stats}

@app.post("/api/auth/update-profile")
async def update_profile(req: UpdateProfileReq, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Giris yapin")
    conn = get_db()
    if req.name and len(req.name.strip()) >= 2:
        conn.execute("UPDATE users SET name=? WHERE id=?", (req.name.strip(), user["id"]))
    if req.password and len(req.password) >= 6:
        salt, h = hash_pw(req.password)
        conn.execute("UPDATE users SET password_hash=?, salt=? WHERE id=?", (h, salt, user["id"]))
    conn.commit(); conn.close()
    return {"success": True}

# ════════ GOOGLE LOGIN ════════
@app.get("/api/auth/google")
async def google_login():
    if not GOOGLE_CLIENT_ID: raise HTTPException(400, "Google login yapilandirilmamis")
    redirect_uri = f"{get_site_url()}/api/auth/google/callback"
    params = urlencode({"client_id":GOOGLE_CLIENT_ID,"redirect_uri":redirect_uri,"response_type":"code","scope":"openid email profile","access_type":"offline","prompt":"select_account"})
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")

@app.get("/api/auth/google/callback")
async def google_callback(code: str = ""):
    if not code: raise HTTPException(400, "Google login basarisiz")
    redirect_uri = f"{get_site_url()}/api/auth/google/callback"
    async with httpx.AsyncClient() as client:
        tr = await client.post("https://oauth2.googleapis.com/token", data={"client_id":GOOGLE_CLIENT_ID,"client_secret":GOOGLE_CLIENT_SECRET,"code":code,"grant_type":"authorization_code","redirect_uri":redirect_uri})
        if tr.status_code != 200: raise HTTPException(400, "Google token alinamadi")
        at = tr.json().get("access_token")
        ur = await client.get("https://www.googleapis.com/oauth2/v2/userinfo", headers={"Authorization":f"Bearer {at}"})
        if ur.status_code != 200: raise HTTPException(400, "Google bilgi alinamadi")
        gu = ur.json()
    email = gu.get("email","").lower(); name = gu.get("name",email.split("@")[0]); gid = gu.get("id",""); avatar = gu.get("picture","")
    conn = get_db()
    ex = conn.execute("SELECT id,name,plan FROM users WHERE email=?", (email,)).fetchone()
    if ex:
        uid, name, plan = ex["id"], ex["name"], ex["plan"]
        conn.execute("UPDATE users SET google_id=?,avatar_url=? WHERE id=?", (gid,avatar,uid))
    else:
        salt, h = hash_pw(secrets.token_hex(16))
        conn.execute("INSERT INTO users(email,name,password_hash,salt,google_id,avatar_url) VALUES(?,?,?,?,?,?)", (email,name,h,salt,gid,avatar))
        uid = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()[0]; plan = "free"
    conn.commit(); conn.close()
    jwt_token = create_token(uid, email, name, plan)
    return HTMLResponse(f'<html><head><script>localStorage.setItem("pf_token","{jwt_token}");window.location.href="/app";</script></head><body style="background:#04080a;color:#00e5ff;display:flex;align-items:center;justify-content:center;height:100vh">Giris yapiliyor...</body></html>')

# ════════ GENERATE ════════
@app.post("/api/generate/text")
async def generate_text(req: TextRequest, authorization: Optional[str] = Header(None)):
    api = get_api(); user = await get_user(authorization)
    if api != "demo":
        if not user: raise HTTPException(401, "Giris yapin")
        used = get_usage(user["id"]); limit = PLAN_LIMITS.get(user["plan"], 5)
        if used >= limit: raise HTTPException(403, f"Aylik limit doldu ({limit})")
        add_usage(user["id"])
    tid = str(uuid.uuid4())[:8]
    tasks[tid] = {"status":"processing","progress":0,"step":"Baslatiliyor...","type":"text","api":api,"prompt":req.prompt,"style":req.style,"user_id":user["id"] if user else 0}
    if api == "tripo": asyncio.create_task(_tripo_text(tid, req.prompt, req.style))
    elif api == "meshy": asyncio.create_task(_meshy_text(tid, req.prompt, req.style))
    else: asyncio.create_task(_demo_generate(tid))
    return {"task_id": tid}

@app.post("/api/generate/image")
async def generate_image(file: UploadFile = File(...), authorization: Optional[str] = Header(None)):
    api = get_api(); user = await get_user(authorization)
    if api != "demo":
        if not user: raise HTTPException(401, "Giris yapin")
        used = get_usage(user["id"]); limit = PLAN_LIMITS.get(user["plan"], 5)
        if used >= limit: raise HTTPException(403, f"Aylik limit doldu ({limit})")
        add_usage(user["id"])
    contents = await file.read()
    if len(contents) > 10*1024*1024: raise HTTPException(400, "Max 10MB")
    tid = str(uuid.uuid4())[:8]; fname = file.filename or "image.jpg"
    tasks[tid] = {"status":"processing","progress":0,"step":"Gorsel hazirlaniyor...","type":"image","api":api,"prompt":fname,"style":"","user_id":user["id"] if user else 0}
    if api == "tripo": asyncio.create_task(_tripo_image(tid, contents, fname))
    elif api == "meshy": asyncio.create_task(_meshy_image(tid, contents, fname))
    else: asyncio.create_task(_demo_generate(tid))
    return {"task_id": tid}

@app.get("/api/status/{task_id}")
async def get_status(task_id: str):
    if task_id not in tasks: raise HTTPException(404, "Bulunamadi")
    t = tasks[task_id]
    return {"task_id":task_id,"status":t["status"],"progress":t["progress"],"step":t.get("step",""),"model_url":t.get("model_url",""),"is_demo":t.get("api")=="demo","cached":task_id in model_cache,"error":t.get("error","")}

# ════════ MODEL SERVING ════════
async def cache_model(tid, url):
    if tid in model_cache: return True
    while len(model_cache) >= MAX_CACHE: del model_cache[next(iter(model_cache))]
    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
            r = await c.get(url)
            if r.status_code == 200 and len(r.content) > 100: model_cache[tid] = r.content; return True
    except: pass
    return False

async def ensure_cached(tid):
    if tid in model_cache: return True
    if tid in tasks and tasks[tid].get("model_url"): return await cache_model(tid, tasks[tid]["model_url"])
    return False

@app.get("/api/model/{task_id}/view")
async def model_view(task_id: str):
    if not await ensure_cached(task_id): raise HTTPException(404, "Model bulunamadi")
    return Response(content=model_cache[task_id], media_type="model/gltf-binary", headers={"Access-Control-Allow-Origin":"*","Cache-Control":"public, max-age=3600"})

@app.get("/api/model/{task_id}/glb")
async def download_glb(task_id: str):
    if not await ensure_cached(task_id): raise HTTPException(404, "Model bulunamadi")
    conn = get_db(); conn.execute("UPDATE models SET downloads=downloads+1 WHERE task_id=?", (task_id,)); conn.commit(); conn.close()
    return Response(content=model_cache[task_id], media_type="model/gltf-binary", headers={"Content-Disposition":f'attachment; filename="printforge_{task_id}.glb"'})

@app.get("/api/model/{task_id}/stl")
async def download_stl(task_id: str):
    if not HAS_TRIMESH: raise HTTPException(500, "STL yuklu degil")
    if not await ensure_cached(task_id): raise HTTPException(404, "Model bulunamadi")
    try:
        scene = trimesh.load(io.BytesIO(model_cache[task_id]), file_type="glb", force="scene")
        meshes = [g for g in scene.geometry.values() if isinstance(g, trimesh.Trimesh)] if isinstance(scene, trimesh.Scene) else [scene]
        stl = trimesh.util.concatenate(meshes).export(file_type="stl")
        conn = get_db(); conn.execute("UPDATE models SET downloads=downloads+1 WHERE task_id=?", (task_id,)); conn.commit(); conn.close()
        return Response(content=stl, media_type="application/vnd.ms-pki.stl", headers={"Content-Disposition":f'attachment; filename="printforge_{task_id}.stl"'})
    except Exception as e: raise HTTPException(500, f"STL hatasi: {e}")

@app.get("/api/model/{task_id}/obj")
async def download_obj(task_id: str):
    if not HAS_TRIMESH: raise HTTPException(500, "OBJ yuklu degil")
    if not await ensure_cached(task_id): raise HTTPException(404, "Model bulunamadi")
    try:
        scene = trimesh.load(io.BytesIO(model_cache[task_id]), file_type="glb", force="scene")
        meshes = [g for g in scene.geometry.values() if isinstance(g, trimesh.Trimesh)] if isinstance(scene, trimesh.Scene) else [scene]
        obj = trimesh.util.concatenate(meshes).export(file_type="obj")
        return Response(content=obj, media_type="text/plain", headers={"Content-Disposition":f'attachment; filename="printforge_{task_id}.obj"'})
    except Exception as e: raise HTTPException(500, f"OBJ hatasi: {e}")

# ════════ GALLERY ════════
@app.get("/api/gallery")
async def gallery(page: int = 1, limit: int = 20, sort: str = "newest", search: str = ""):
    conn = get_db(); offset = (page-1)*limit
    where = "WHERE is_public=1 AND model_url != ''"; params = []
    if search: where += " AND (title LIKE ? OR prompt LIKE ?)"; params += [f"%{search}%", f"%{search}%"]
    order = {"popular":"ORDER BY likes DESC","downloads":"ORDER BY downloads DESC"}.get(sort, "ORDER BY created_at DESC")
    rows = conn.execute(f"SELECT m.*, u.name as author_name FROM models m LEFT JOIN users u ON m.user_id=u.id {where} {order} LIMIT ? OFFSET ?", params+[limit,offset]).fetchall()
    total = conn.execute(f"SELECT COUNT(*) FROM models {where}", params).fetchone()[0]; conn.close()
    return {"models":[dict(r) for r in rows],"total":total,"page":page,"pages":max(1,(total+limit-1)//limit)}

@app.get("/api/gallery/{model_id}")
async def model_detail(model_id: int):
    conn = get_db()
    row = conn.execute("SELECT m.*, u.name as author_name FROM models m LEFT JOIN users u ON m.user_id=u.id WHERE m.id=?", (model_id,)).fetchone()
    conn.close()
    if not row: raise HTTPException(404, "Bulunamadi")
    return dict(row)

@app.get("/api/gallery/{model_id}/similar")
async def similar_models(model_id: int, limit: int = 6):
    conn = get_db()
    cur = conn.execute("SELECT style, gen_type FROM models WHERE id=?", (model_id,)).fetchone()
    if not cur: conn.close(); raise HTTPException(404, "Bulunamadi")
    rows = conn.execute("SELECT m.*, u.name as author_name FROM models m LEFT JOIN users u ON m.user_id=u.id WHERE m.id!=? AND m.is_public=1 AND m.model_url!='' AND (m.style=? OR m.gen_type=?) ORDER BY m.likes DESC LIMIT ?", (model_id, cur["style"] or "", cur["gen_type"] or "", limit)).fetchall()
    if len(rows) < limit:
        extra = conn.execute("SELECT m.*, u.name as author_name FROM models m LEFT JOIN users u ON m.user_id=u.id WHERE m.id!=? AND m.is_public=1 AND m.model_url!='' ORDER BY RANDOM() LIMIT ?", (model_id, limit-len(rows))).fetchall()
        rows = list(rows) + list(extra)
    conn.close()
    return {"models": [dict(r) for r in rows]}

@app.post("/api/gallery/{model_id}/like")
async def toggle_like(model_id: int, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Giris yapin")
    conn = get_db()
    ex = conn.execute("SELECT 1 FROM user_likes WHERE user_id=? AND model_id=?", (user["id"], model_id)).fetchone()
    if ex:
        conn.execute("DELETE FROM user_likes WHERE user_id=? AND model_id=?", (user["id"], model_id))
        conn.execute("UPDATE models SET likes=likes-1 WHERE id=?", (model_id,)); liked = False
    else:
        conn.execute("INSERT INTO user_likes(user_id,model_id) VALUES(?,?)", (user["id"], model_id))
        conn.execute("UPDATE models SET likes=likes+1 WHERE id=?", (model_id,)); liked = True
    conn.commit(); conn.close()
    return {"liked": liked}

@app.get("/api/my-models")
async def my_models(authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Giris yapin")
    conn = get_db(); rows = conn.execute("SELECT * FROM models WHERE user_id=? ORDER BY created_at DESC", (user["id"],)).fetchall(); conn.close()
    return {"models": [dict(r) for r in rows]}

@app.delete("/api/my-models/{model_id}")
async def delete_model(model_id: int, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Giris yapin")
    conn = get_db(); conn.execute("DELETE FROM models WHERE id=? AND user_id=?", (model_id, user["id"])); conn.commit(); conn.close()
    return {"deleted": True}

@app.post("/api/payment/upgrade")
async def upgrade_plan(authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Giris yapin")
    conn = get_db(); conn.execute("UPDATE users SET plan='pro' WHERE id=?", (user["id"],)); conn.commit(); conn.close()
    return {"success": True, "plan": "pro"}

@app.get("/api/health")
async def health():
    api = get_api()
    return {"status":"online","active_api":api,"api_ready":True,"is_demo":api=="demo","stl_ready":HAS_TRIMESH,"auth_ready":HAS_JWT,"google_ready":bool(GOOGLE_CLIENT_ID),"cached_models":len(model_cache)}

# ════════ URL EXTRACT ════════
def extract_model_url(data):
    if not data: return ""
    if isinstance(data, str) and data.startswith("http"): return data
    if not isinstance(data, dict): return ""
    for key in ["model","pbr_model","base_model"]:
        val = data.get(key, "")
        if isinstance(val, str) and val.startswith("http"): return val
        if isinstance(val, dict):
            url = val.get("url","") or val.get("download_url","")
            if url and url.startswith("http"): return url
    for k,v in data.items():
        if isinstance(v, str) and v.startswith("http") and any(x in v.lower() for x in [".glb",".gltf","model"]): return v
    return ""

# ════════ TRIPO ════════
async def _tripo_text(tid, prompt, style):
    try:
        h = {"Authorization":f"Bearer {TRIPO_API_KEY}"}; tasks[tid]["progress"]=10; tasks[tid]["step"]="Prompt gonderiliyor..."
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.post(f"{TRIPO_BASE}/task", json={"type":"text_to_model","prompt":f"{prompt}, {style} style"}, headers={**h,"Content-Type":"application/json"})
            if r.status_code!=200: raise Exception(f"Tripo {r.status_code}")
            tid2 = r.json().get("data",{}).get("task_id")
            if not tid2: raise Exception("ID yok"); tasks[tid]["progress"]=25
            await _tripo_poll(c,h,tid,tid2)
    except Exception as e: tasks[tid]["status"]="failed"; tasks[tid]["error"]=str(e)

async def _tripo_image(tid, contents, fname):
    try:
        h = {"Authorization":f"Bearer {TRIPO_API_KEY}"}
        ext = fname.rsplit(".",1)[-1].lower()
        if ext not in ("jpg","jpeg","png","webp"): ext="jpeg"
        mime = "image/jpeg" if ext in ("jpg","jpeg") else f"image/{ext}"
        tasks[tid]["progress"]=10; tasks[tid]["step"]="Gorsel yukleniyor..."
        async with httpx.AsyncClient(timeout=600) as c:
            ur = await c.post(f"{TRIPO_BASE}/upload", files={"file":(fname,contents,mime)}, headers=h)
            if ur.status_code!=200: raise Exception(f"Upload {ur.status_code}")
            tk = ur.json().get("data",{}).get("image_token")
            if not tk: raise Exception("Token yok"); tasks[tid]["progress"]=25
            tr = await c.post(f"{TRIPO_BASE}/task", json={"type":"image_to_model","file":{"type":ext if ext!="jpg" else "jpeg","file_token":tk}}, headers={**h,"Content-Type":"application/json"})
            if tr.status_code!=200: raise Exception(f"Task {tr.status_code}")
            tid2 = tr.json().get("data",{}).get("task_id")
            if not tid2: raise Exception("ID yok"); tasks[tid]["progress"]=35
            await _tripo_poll(c,h,tid,tid2)
    except Exception as e: tasks[tid]["status"]="failed"; tasks[tid]["error"]=str(e)

async def _tripo_poll(client, headers, tid, tripo_id):
    for _ in range(200):
        await asyncio.sleep(3)
        try:
            r = await client.get(f"{TRIPO_BASE}/task/{tripo_id}", headers=headers)
            d = r.json().get("data",{}); st=d.get("status",""); pr=d.get("progress",0)
            tasks[tid]["progress"]=35+int(pr*0.55); tasks[tid]["step"]=f"Uretiliyor %{pr}"
            if st=="success":
                url=extract_model_url(d.get("output",{})); tasks[tid]["model_url"]=url; tasks[tid]["progress"]=92
                if url: await cache_model(tid,url)
                tasks[tid]["status"]="done"; tasks[tid]["progress"]=100; tasks[tid]["step"]="Tamamlandi!"
                save_model(tasks[tid].get("user_id",0),tid,tasks[tid].get("prompt","")[:50],tasks[tid].get("prompt",""),tasks[tid].get("type",""),tasks[tid].get("style",""),url)
                return
            elif st in ("failed","cancelled"): raise Exception(f"Tripo: {st}")
        except Exception as e:
            if any(x in str(e) for x in ["Tripo","failed","cancelled"]): tasks[tid]["status"]="failed"; tasks[tid]["error"]=str(e); return
    tasks[tid]["status"]="failed"; tasks[tid]["error"]="Zaman asimi"

async def _meshy_text(tid, prompt, style):
    try:
        h={"Authorization":f"Bearer {MESHY_API_KEY}","Content-Type":"application/json"}; tasks[tid]["progress"]=10
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.post(f"{MESHY_BASE}/text-to-3d", json={"mode":"preview","prompt":prompt,"art_style":"realistic"}, headers=h)
            if r.status_code not in (200,202): raise Exception(f"Meshy {r.status_code}")
            mid=r.json().get("result"); tasks[tid]["progress"]=20; await _meshy_poll(c,h,tid,mid,"text-to-3d")
    except Exception as e: tasks[tid]["status"]="failed"; tasks[tid]["error"]=str(e)

async def _meshy_image(tid, contents, fname):
    try:
        h={"Authorization":f"Bearer {MESHY_API_KEY}","Content-Type":"application/json"}
        ext=fname.rsplit(".",1)[-1].lower(); mime="image/jpeg" if ext in ("jpg","jpeg") else f"image/{ext}"
        b64=base64.b64encode(contents).decode(); tasks[tid]["progress"]=15
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.post(f"{MESHY_BASE}/image-to-3d", json={"image_url":f"data:{mime};base64,{b64}","enable_pbr":True}, headers=h)
            if r.status_code not in (200,202): raise Exception(f"Meshy {r.status_code}")
            mid=r.json().get("result"); tasks[tid]["progress"]=25; await _meshy_poll(c,h,tid,mid,"image-to-3d")
    except Exception as e: tasks[tid]["status"]="failed"; tasks[tid]["error"]=str(e)

async def _meshy_poll(client,h,tid,mid,ep):
    for _ in range(200):
        await asyncio.sleep(3)
        try:
            r = await client.get(f"{MESHY_BASE}/{ep}/{mid}", headers=h)
            if r.status_code!=200: continue
            d=r.json()
            if d.get("status")=="SUCCEEDED":
                glb=d.get("model_urls",{}).get("glb",""); tasks[tid]["model_url"]=glb
                if glb: await cache_model(tid,glb)
                tasks[tid]["status"]="done"; tasks[tid]["progress"]=100
                save_model(tasks[tid].get("user_id",0),tid,tasks[tid].get("prompt","")[:50],tasks[tid].get("prompt",""),tasks[tid].get("type",""),"",glb); return
            elif d.get("status")=="FAILED": raise Exception("Meshy uretilemedi")
            tasks[tid]["progress"]=25+int(d.get("progress",0)*0.7)
        except Exception as e:
            if "uretilemedi" in str(e): raise
    raise Exception("Zaman asimi")

async def _demo_generate(tid):
    try:
        for pr,st in [(8,"Analiz..."),(22,"AI yukleniyor..."),(40,"Geometri..."),(58,"Mesh..."),(72,"Texture..."),(88,"Optimize..."),(95,"Hazirlaniyor...")]:
            tasks[tid]["progress"]=pr; tasks[tid]["step"]=st; await asyncio.sleep(random.uniform(1.0,2.0))
        m=random.choice(DEMO_MODELS); tasks[tid]["model_url"]=m["glb"]
        await cache_model(tid,m["glb"])
        tasks[tid]["status"]="done"; tasks[tid]["progress"]=100; tasks[tid]["step"]=f"Demo: {m['name']}"
        save_model(0,tid,m["name"],"demo","demo","",m["glb"])
    except Exception as e: tasks[tid]["status"]="failed"; tasks[tid]["error"]=str(e)
def load_app_html():
    path = os.path.join(os.path.dirname(__file__), "app.html")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
def load_app_html():
    path = os.path.join(os.path.dirname(__file__), "app.html")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return "<html><body><h1>app.html bulunamadi</h1></body></html>"


@app.get("/app", response_class=HTMLResponse)
def serve_app():
    return HTMLResponse(load_app_html())
