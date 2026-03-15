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
        APP_HTML = r"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PrintForge — AI 3D Model Uretici</title>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800;900&family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<script type="module" src="https://unpkg.com/@google/model-viewer@3.5.0/dist/model-viewer.min.js"></script>
<style>
:root{--bg:#04080a;--bg2:#070d10;--border:#0e2028;--accent:#00e5ff;--accent2:#00ff9d;--text:#c8dde5;--muted:#2a4a5a;--card:#060c10;--card2:#0a1018;--red:#ff4466;--orange:#ffaa00;--purple:#a855f7;--glass:rgba(6,12,16,0.85)}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;-webkit-font-smoothing:antialiased;overflow-x:hidden}
h1,h2,h3,h4{font-family:'Outfit',sans-serif}
::-webkit-scrollbar{width:4px}::-webkit-scrollbar-thumb{background:var(--muted);border-radius:4px}

/* ANIMATED BG */
#bgCanvas{position:fixed;inset:0;z-index:0;pointer-events:none}
.bg-gradient{position:fixed;inset:0;z-index:0;pointer-events:none;background:radial-gradient(ellipse 60% 50% at 20% 20%,rgba(0,229,255,0.04),transparent),radial-gradient(ellipse 50% 40% at 80% 80%,rgba(0,255,157,0.03),transparent),radial-gradient(ellipse 40% 30% at 50% 50%,rgba(168,85,247,0.02),transparent)}
.bg-grid{position:fixed;inset:0;z-index:0;pointer-events:none;background-image:linear-gradient(rgba(0,229,255,0.015) 1px,transparent 1px),linear-gradient(90deg,rgba(0,229,255,0.015) 1px,transparent 1px);background-size:60px 60px}
.scan-line{position:fixed;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(0,229,255,0.04),transparent);animation:scanLine 8s linear infinite;z-index:0;pointer-events:none}
@keyframes scanLine{from{top:-1px}to{top:100vh}}

/* NAV */
.nav{position:sticky;top:0;z-index:100;display:flex;justify-content:space-between;align-items:center;padding:12px 24px;background:var(--glass);backdrop-filter:blur(24px);border-bottom:1px solid rgba(0,229,255,0.06);gap:10px;flex-wrap:wrap}
.nav-logo{display:flex;align-items:center;gap:8px;text-decoration:none}
.nlm{width:22px;height:22px;border:1.5px solid var(--accent);transform:rotate(45deg);display:flex;align-items:center;justify-content:center;transition:all 0.3s}
.nav-logo:hover .nlm{border-color:var(--accent2);box-shadow:0 0 12px rgba(0,229,255,0.3)}
.nli{width:6px;height:6px;background:var(--accent);transform:rotate(-45deg)}
.nlt{font-family:'Outfit',sans-serif;font-size:15px;font-weight:800;color:var(--accent);letter-spacing:0.08em}
.nav-right{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.nav-status{font-size:8px;color:var(--muted);display:flex;align-items:center;gap:4px;padding:4px 10px;border:1px solid var(--border);border-radius:20px}
.nav-dot{width:5px;height:5px;border-radius:50%;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1;box-shadow:0 0 4px currentColor}50%{opacity:0.4;box-shadow:none}}
.nav-user{display:flex;align-items:center;gap:8px}
.nav-avatar{width:30px;height:30px;border-radius:50%;background:linear-gradient(135deg,rgba(0,229,255,0.2),rgba(168,85,247,0.2));border:1.5px solid var(--accent);display:flex;align-items:center;justify-content:center;font-size:12px;color:var(--accent);cursor:pointer;font-family:'Outfit',sans-serif;font-weight:700;transition:all 0.2s}
.nav-avatar:hover{border-color:var(--accent2);box-shadow:0 0 16px rgba(0,229,255,0.2)}
.nav-uname{font-size:10px;color:var(--text);font-weight:500}
.nav-usage{font-size:8px;color:var(--accent2);background:rgba(0,255,157,0.06);padding:3px 10px;border:1px solid rgba(0,255,157,0.15);border-radius:20px;font-weight:600}
.nbtn{padding:6px 14px;font-family:'Inter',sans-serif;font-size:9px;letter-spacing:0.08em;cursor:pointer;transition:all 0.2s;border:1px solid var(--border);background:transparent;color:var(--text);border-radius:8px;font-weight:500}
.nbtn:hover{border-color:var(--accent);color:var(--accent);background:rgba(0,229,255,0.04)}
.nbtn.accent{background:linear-gradient(135deg,var(--accent),#0099cc);color:#04080a;border:none;font-weight:600}
.nbtn.accent:hover{background:linear-gradient(135deg,var(--accent2),var(--accent));transform:translateY(-1px);box-shadow:0 4px 12px rgba(0,229,255,0.2)}
.nbtn.red{color:var(--red);border-color:rgba(255,68,102,0.2)}

.banner{padding:8px 20px;text-align:center;font-size:9px;display:none;position:relative;z-index:1;letter-spacing:0.04em}
.banner.demo{background:linear-gradient(90deg,rgba(255,170,0,0.06),rgba(255,170,0,0.02));color:var(--orange);border-bottom:1px solid rgba(255,170,0,0.1)}
.banner.usage{background:linear-gradient(90deg,rgba(0,255,157,0.04),rgba(0,255,157,0.01));color:var(--accent2);border-bottom:1px solid rgba(0,255,157,0.1)}

.container{position:relative;z-index:1;max-width:920px;margin:0 auto;padding:28px 20px 80px}

/* AUTH OVERLAY */
.auth-overlay{display:none;position:fixed;inset:0;z-index:200;background:rgba(4,8,10,0.94);backdrop-filter:blur(8px);align-items:center;justify-content:center;padding:20px}
.auth-overlay.on{display:flex}
.auth-box{background:var(--card);border:1px solid var(--border);padding:36px 30px;width:100%;max-width:380px;position:relative;border-radius:20px;box-shadow:0 24px 64px rgba(0,0,0,0.4)}
.auth-close{position:absolute;top:14px;right:16px;background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer;transition:color 0.2s}
.auth-close:hover{color:var(--text)}
.auth-logo{text-align:center;margin-bottom:20px;font-family:'Outfit',sans-serif;font-size:20px;font-weight:800;color:var(--accent)}
.auth-tabs{display:flex;border:1px solid var(--border);margin-bottom:18px;border-radius:10px;overflow:hidden}
.auth-tab{flex:1;padding:10px;background:transparent;border:none;color:var(--muted);font-family:'Inter',sans-serif;font-size:10px;cursor:pointer;transition:all 0.15s;letter-spacing:0.06em;font-weight:500}
.auth-tab.on{background:rgba(0,229,255,0.06);color:var(--accent)}
.fg{margin-bottom:12px}
.fg label{font-size:9px;letter-spacing:0.1em;color:var(--muted);margin-bottom:5px;display:block;font-weight:500}
.fg input{width:100%;background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:11px 14px;font-size:13px;font-family:'Inter',sans-serif;border-radius:10px;transition:all 0.2s}
.fg input:focus{outline:none;border-color:rgba(0,229,255,0.4);box-shadow:0 0 0 3px rgba(0,229,255,0.06)}
.fg input::placeholder{color:var(--muted)}
.auth-btn{width:100%;padding:13px;background:linear-gradient(135deg,var(--accent),#0099cc);color:#04080a;border:none;font-family:'Inter',sans-serif;font-size:12px;letter-spacing:0.08em;cursor:pointer;margin-top:8px;border-radius:10px;font-weight:700;transition:all 0.2s}
.auth-btn:hover{background:linear-gradient(135deg,var(--accent2),var(--accent));transform:translateY(-1px);box-shadow:0 6px 16px rgba(0,229,255,0.15)}
.auth-divider{display:flex;align-items:center;gap:12px;margin:16px 0;color:var(--muted);font-size:9px;letter-spacing:0.08em}
.auth-divider::before,.auth-divider::after{content:'';flex:1;height:1px;background:var(--border)}
.google-btn{width:100%;padding:11px;background:transparent;border:1px solid var(--border);color:var(--text);font-family:'Inter',sans-serif;font-size:11px;cursor:pointer;border-radius:10px;display:flex;align-items:center;justify-content:center;gap:10px;transition:all 0.2s;font-weight:500}
.google-btn:hover{border-color:var(--accent);background:rgba(0,229,255,0.04)}
.google-btn svg{width:16px;height:16px}
.auth-msg{padding:8px 12px;font-size:10px;margin-bottom:10px;display:none;border-radius:8px;line-height:1.6}
.auth-msg.err{background:rgba(255,68,102,0.08);border:1px solid rgba(255,68,102,0.2);color:var(--red);display:block}
.auth-msg.ok{background:rgba(0,255,157,0.08);border:1px solid rgba(0,255,157,0.2);color:var(--accent2);display:block}
.auth-footer{text-align:center;margin-top:14px;font-size:9px;color:var(--muted)}
.auth-footer a{color:var(--accent);cursor:pointer}

/* TABS */
.tabs{display:flex;border:1px solid var(--border);margin-bottom:24px;border-radius:12px;overflow:hidden;background:var(--glass);backdrop-filter:blur(10px)}
.tab{flex:1;padding:12px 8px;background:transparent;border:none;color:var(--muted);font-family:'Inter',sans-serif;font-size:10px;letter-spacing:0.06em;cursor:pointer;transition:all 0.2s;display:flex;align-items:center;justify-content:center;gap:6px;font-weight:500;position:relative}
.tab.on{color:var(--accent)}
.tab.on::after{content:'';position:absolute;bottom:0;left:20%;right:20%;height:2px;background:var(--accent);border-radius:1px}
.tab:hover:not(.on){background:rgba(0,229,255,0.02);color:var(--text)}

.panel{display:none}.panel.on{display:block}
.card{background:var(--glass);backdrop-filter:blur(10px);border:1px solid var(--border);padding:26px;margin-bottom:14px;border-radius:16px;transition:border-color 0.2s}
.card:hover{border-color:rgba(0,229,255,0.1)}
.label{font-size:9px;letter-spacing:0.12em;color:var(--muted);margin-bottom:6px;display:block;font-weight:600}
textarea{width:100%;background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:12px;font-size:13px;font-family:'Inter',sans-serif;resize:vertical;min-height:70px;transition:all 0.2s;border-radius:12px}
textarea:focus{outline:none;border-color:rgba(0,229,255,0.4);box-shadow:0 0 0 3px rgba(0,229,255,0.06)}
textarea::placeholder{color:var(--muted)}
.examples{margin-top:10px;display:flex;gap:5px;flex-wrap:wrap}
.ex-btn{padding:6px 12px;border:1px solid var(--border);background:transparent;color:var(--muted);font-family:'Inter',sans-serif;font-size:9px;cursor:pointer;transition:all 0.15s;border-radius:8px;font-weight:500}
.ex-btn:hover{border-color:var(--accent);color:var(--accent);background:rgba(0,229,255,0.04)}
.style-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:6px}
.style-opt{padding:12px 8px;border:1px solid var(--border);background:transparent;color:var(--muted);font-family:'Inter',sans-serif;font-size:9px;cursor:pointer;transition:all 0.2s;text-align:center;border-radius:12px;font-weight:500}
.style-opt:hover{border-color:rgba(0,229,255,0.3);color:var(--text)}
.style-opt.on{border-color:var(--accent);color:var(--accent);background:rgba(0,229,255,0.04);box-shadow:0 0 12px rgba(0,229,255,0.06)}
.style-opt .ico{font-size:18px;display:block;margin-bottom:4px}
.upload{border:2px dashed var(--border);padding:36px 20px;text-align:center;cursor:pointer;transition:all 0.3s;position:relative;overflow:hidden;border-radius:16px}
.upload:hover,.upload.drag{border-color:var(--accent);background:rgba(0,229,255,0.02)}
.upload.has{border-color:var(--accent2);border-style:solid}
.upload input{position:absolute;inset:0;opacity:0;cursor:pointer}
.upload .ico{font-size:30px;margin-bottom:8px;color:var(--accent)}
.upload p{font-size:11px;color:var(--muted)}
.preview{margin-top:14px;display:none;position:relative}
.preview.on{display:block}
.preview img{max-width:100%;max-height:200px;display:block;margin:0 auto;border:1px solid var(--border);border-radius:12px}
.preview .rm{position:absolute;top:6px;right:6px;width:26px;height:26px;background:rgba(255,68,102,0.85);border:none;color:#fff;border-radius:50%;cursor:pointer;font-size:11px}
.gen-btn{width:100%;padding:14px;background:linear-gradient(135deg,var(--accent),#0099cc);color:#04080a;border:none;font-family:'Inter',sans-serif;font-size:12px;letter-spacing:0.1em;cursor:pointer;font-weight:700;transition:all 0.2s;margin-top:14px;border-radius:12px;position:relative;overflow:hidden}
.gen-btn:hover:not(:disabled){background:linear-gradient(135deg,var(--accent2),var(--accent));transform:translateY(-1px);box-shadow:0 6px 20px rgba(0,229,255,0.15)}
.gen-btn:disabled{opacity:0.4;cursor:not-allowed}

/* PROGRESS */
.sec{display:none;margin-bottom:20px}.sec.on{display:block}
.prog-card{background:var(--glass);backdrop-filter:blur(10px);border:1px solid var(--border);padding:24px;border-radius:16px}
.prog-top{display:flex;justify-content:space-between;margin-bottom:14px}
.prog-title{font-family:'Outfit',sans-serif;font-size:15px;font-weight:700}
.prog-pct{font-family:'Outfit',sans-serif;font-size:22px;font-weight:800;background:linear-gradient(135deg,var(--accent),var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.prog-bar-bg{width:100%;height:6px;background:var(--bg2);overflow:hidden;margin-bottom:10px;border-radius:3px}
.prog-bar{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));width:0%;transition:width 0.5s;border-radius:3px;position:relative}
.prog-bar::after{content:'';position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,0.15),transparent);animation:shimmer 2s infinite}
@keyframes shimmer{0%{transform:translateX(-100%)}100%{transform:translateX(100%)}}
.prog-step{font-size:10px;color:var(--muted);display:flex;align-items:center;gap:6px}
.spinner{display:inline-block;width:10px;height:10px;border:2px solid var(--muted);border-top-color:var(--accent);border-radius:50%;animation:spin 0.8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

/* RESULT */
.result-card{background:var(--glass);backdrop-filter:blur(10px);border:1px solid var(--accent2);padding:24px;text-align:center;border-radius:16px;box-shadow:0 0 30px rgba(0,255,157,0.04)}
.result-card h3{font-family:'Outfit',sans-serif;font-size:20px;font-weight:800;margin-bottom:6px}
.result-card>p{font-size:11px;color:var(--muted);margin-bottom:16px}
.viewer{width:100%;height:360px;background:var(--bg2);border:1px solid var(--border);margin-bottom:16px;overflow:hidden;display:flex;align-items:center;justify-content:center;border-radius:14px}
.viewer model-viewer{width:100%;height:100%}
.dl-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:10px}
.dl-btn{padding:12px 8px;border:1px solid var(--border);background:var(--card);color:var(--text);font-family:'Inter',sans-serif;font-size:10px;cursor:pointer;transition:all 0.2s;text-decoration:none;text-align:center;display:flex;flex-direction:column;align-items:center;gap:3px;border-radius:10px;font-weight:500}
.dl-btn:hover{border-color:var(--accent);color:var(--accent)}
.dl-btn .dl-fmt{font-size:7px;color:var(--muted);letter-spacing:0.08em}
.dl-btn.primary{border-color:var(--accent2);background:rgba(0,255,157,0.05)}
.new-btn{width:100%;padding:11px;border:1px solid var(--border);background:transparent;color:var(--text);font-family:'Inter',sans-serif;font-size:10px;letter-spacing:0.06em;cursor:pointer;transition:all 0.2s;border-radius:10px;font-weight:500}
.new-btn:hover{border-color:var(--accent);color:var(--accent)}
.err-card{background:rgba(255,68,102,0.04);border:1px solid rgba(255,68,102,0.15);padding:24px;text-align:center;border-radius:16px}
.err-card h3{color:var(--red);font-size:15px;margin-bottom:6px}
.err-card p{font-size:10px;color:var(--muted);margin-bottom:14px}

/* GALLERY */
.gal-toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:16px}
.gal-toolbar input{flex:1;min-width:140px;background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:10px 14px;font-family:'Inter',sans-serif;font-size:12px;border-radius:10px}
.gal-toolbar input:focus{outline:none;border-color:rgba(0,229,255,0.3)}
.gal-toolbar select{background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:10px;font-family:'Inter',sans-serif;font-size:11px;border-radius:10px}
.gal-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px}
.gal-card{background:var(--card);border:1px solid var(--border);transition:all 0.25s;cursor:pointer;border-radius:16px;overflow:hidden}
.gal-card:hover{border-color:rgba(0,229,255,0.25);transform:translateY(-4px);box-shadow:0 16px 40px rgba(0,0,0,0.3)}
.gal-thumb{height:180px;background:var(--bg2);overflow:hidden;display:flex;align-items:center;justify-content:center;position:relative}
.gal-thumb model-viewer{width:100%;height:100%}
.gal-badge{position:absolute;top:8px;left:8px;background:var(--glass);backdrop-filter:blur(8px);border:1px solid rgba(0,229,255,0.15);padding:3px 8px;font-size:8px;color:var(--accent);letter-spacing:0.08em;border-radius:6px;font-weight:600}
.gal-body{padding:14px}
.gal-title{font-family:'Outfit',sans-serif;font-size:14px;font-weight:700;margin-bottom:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gal-meta{font-size:9px;color:var(--muted);display:flex;justify-content:space-between;margin-bottom:8px}
.gal-stats{display:flex;gap:14px;margin-bottom:10px}
.gal-stat{font-size:9px;color:var(--muted)}.gal-stat span{color:var(--accent2);font-weight:600}
.gal-actions{display:flex;gap:5px}
.gal-btn{flex:1;padding:7px;border:1px solid var(--border);background:transparent;color:var(--text);font-family:'Inter',sans-serif;font-size:9px;cursor:pointer;transition:all 0.15s;text-align:center;border-radius:8px;text-decoration:none;display:flex;align-items:center;justify-content:center;font-weight:500}
.gal-btn:hover{border-color:var(--accent);color:var(--accent)}
.gal-btn.liked{color:var(--red);border-color:rgba(255,68,102,0.3)}
.gal-btn.dl{background:rgba(0,229,255,0.04);border-color:rgba(0,229,255,0.15)}
.gal-empty{text-align:center;padding:60px 20px;color:var(--muted);font-size:12px;grid-column:1/-1}

/* DETAIL OVERLAY */
.detail-overlay{display:none;position:fixed;inset:0;z-index:150;background:rgba(4,8,10,0.96);overflow-y:auto;padding:20px}
.detail-overlay.on{display:block}
.detail-container{max-width:900px;margin:0 auto}
.detail-back{display:inline-flex;align-items:center;gap:6px;color:var(--muted);font-size:11px;cursor:pointer;margin-bottom:20px;padding:8px 16px;border:1px solid var(--border);background:transparent;border-radius:10px;font-family:'Inter',sans-serif;transition:all 0.2s;font-weight:500}
.detail-back:hover{border-color:var(--accent);color:var(--accent)}
.detail-main{display:grid;grid-template-columns:1.3fr 1fr;gap:24px;margin-bottom:36px}
.detail-viewer{background:var(--bg2);border:1px solid var(--border);border-radius:16px;overflow:hidden;height:420px}
.detail-viewer model-viewer{width:100%;height:100%}
.detail-info{display:flex;flex-direction:column}
.detail-title{font-family:'Outfit',sans-serif;font-size:26px;font-weight:800;margin-bottom:8px;line-height:1.2}
.detail-author{font-size:12px;color:var(--muted);margin-bottom:16px;display:flex;align-items:center;gap:8px}
.detail-author-avatar{width:26px;height:26px;border-radius:50%;background:linear-gradient(135deg,rgba(0,229,255,0.2),rgba(168,85,247,0.2));border:1px solid var(--accent);display:flex;align-items:center;justify-content:center;font-size:10px;color:var(--accent);font-weight:700}
.detail-stats-row{display:flex;gap:16px;margin-bottom:18px;padding:14px;background:var(--bg2);border-radius:12px}
.detail-stat{text-align:center;flex:1}
.detail-stat-num{font-family:'Outfit',sans-serif;font-size:20px;font-weight:800;color:var(--accent)}
.detail-stat-lbl{font-size:7px;color:var(--muted);letter-spacing:0.12em;margin-top:2px}
.detail-section{margin-bottom:14px}
.detail-section-title{font-size:9px;letter-spacing:0.12em;color:var(--muted);margin-bottom:8px;font-weight:600}
.detail-tags{display:flex;gap:6px;flex-wrap:wrap}
.detail-tag{padding:4px 10px;background:rgba(0,229,255,0.04);border:1px solid rgba(0,229,255,0.12);color:var(--accent);font-size:9px;border-radius:8px;font-weight:500}
.detail-prompt{background:var(--bg2);border:1px solid var(--border);padding:12px 16px;font-size:12px;color:var(--text);line-height:1.7;border-radius:12px;font-style:italic}
.detail-dl-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:8px}
.detail-dl{padding:14px;border:1px solid var(--border);background:var(--card);text-align:center;cursor:pointer;transition:all 0.2s;text-decoration:none;color:var(--text);border-radius:12px;font-weight:500}
.detail-dl:hover{border-color:var(--accent);color:var(--accent)}
.detail-dl .dl-name{font-size:12px;font-weight:600}
.detail-dl .dl-desc{font-size:8px;color:var(--muted);margin-top:2px}
.detail-dl.primary{border-color:var(--accent2);background:rgba(0,255,157,0.04)}
.detail-like-btn{width:100%;padding:12px;border:1px solid var(--border);background:transparent;color:var(--text);font-family:'Inter',sans-serif;font-size:12px;cursor:pointer;transition:all 0.2s;margin-top:10px;border-radius:12px;display:flex;align-items:center;justify-content:center;gap:8px;font-weight:600}
.detail-like-btn:hover{border-color:var(--red);color:var(--red)}
.detail-like-btn.liked{background:rgba(255,68,102,0.06);border-color:var(--red);color:var(--red)}
.similar-section{margin-bottom:40px}
.similar-title{font-family:'Outfit',sans-serif;font-size:18px;font-weight:700;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.similar-title::before{content:'';width:3px;height:18px;background:linear-gradient(var(--accent),var(--accent2));border-radius:2px}
.similar-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
.similar-card{background:var(--card);border:1px solid var(--border);border-radius:14px;overflow:hidden;cursor:pointer;transition:all 0.2s}
.similar-card:hover{border-color:rgba(0,229,255,0.25);transform:translateY(-2px)}
.similar-thumb{height:130px;background:var(--bg2);overflow:hidden}
.similar-thumb model-viewer{width:100%;height:100%}
.similar-body{padding:10px 12px}
.similar-name{font-family:'Outfit',sans-serif;font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.similar-meta{font-size:8px;color:var(--muted);margin-top:3px}

/* PROFILE PANEL */
.profile-header{background:linear-gradient(135deg,rgba(0,229,255,0.06),rgba(168,85,247,0.04));border:1px solid var(--border);border-radius:20px;padding:30px;margin-bottom:20px;position:relative;overflow:hidden}
.profile-header::before{content:'';position:absolute;inset:0;background-image:linear-gradient(rgba(0,229,255,0.03) 1px,transparent 1px),linear-gradient(90deg,rgba(0,229,255,0.03) 1px,transparent 1px);background-size:30px 30px}
.profile-top{display:flex;align-items:center;gap:18px;position:relative;z-index:1;flex-wrap:wrap}
.profile-avatar{width:64px;height:64px;border-radius:50%;background:linear-gradient(135deg,rgba(0,229,255,0.2),rgba(168,85,247,0.2));border:2px solid var(--accent);display:flex;align-items:center;justify-content:center;font-size:24px;color:var(--accent);font-family:'Outfit',sans-serif;font-weight:800}
.profile-name{font-family:'Outfit',sans-serif;font-size:22px;font-weight:800}
.profile-email{font-size:11px;color:var(--muted);margin-top:2px}
.profile-plan{display:inline-flex;align-items:center;gap:5px;margin-top:6px;padding:4px 12px;border-radius:20px;font-size:9px;font-weight:600;letter-spacing:0.08em}
.profile-plan.free{background:rgba(0,229,255,0.08);color:var(--accent);border:1px solid rgba(0,229,255,0.15)}
.profile-plan.pro{background:rgba(168,85,247,0.08);color:var(--purple);border:1px solid rgba(168,85,247,0.15)}
.profile-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:20px;position:relative;z-index:1}
.pstat{text-align:center;padding:14px;background:var(--glass);backdrop-filter:blur(10px);border:1px solid var(--border);border-radius:14px}
.pstat-num{font-family:'Outfit',sans-serif;font-size:24px;font-weight:800;color:var(--accent);line-height:1}
.pstat-lbl{font-size:8px;color:var(--muted);letter-spacing:0.12em;margin-top:4px}
.profile-tabs{display:flex;gap:4px;margin-bottom:16px}
.ptab{padding:8px 18px;border:1px solid var(--border);background:transparent;color:var(--muted);font-family:'Inter',sans-serif;font-size:10px;cursor:pointer;transition:all 0.15s;border-radius:8px;font-weight:500}
.ptab.on{background:rgba(0,229,255,0.06);border-color:var(--accent);color:var(--accent)}
.settings-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;max-width:500px}
.settings-grid .fg{margin-bottom:0}
.save-btn{padding:10px 24px;background:linear-gradient(135deg,var(--accent),#0099cc);color:#04080a;border:none;font-family:'Inter',sans-serif;font-size:11px;cursor:pointer;border-radius:10px;font-weight:600;margin-top:12px;transition:all 0.2s}
.save-btn:hover{transform:translateY(-1px);box-shadow:0 4px 12px rgba(0,229,255,0.15)}
.danger-btn{padding:10px 24px;background:transparent;border:1px solid rgba(255,68,102,0.3);color:var(--red);font-family:'Inter',sans-serif;font-size:11px;cursor:pointer;border-radius:10px;font-weight:500;margin-top:12px;margin-left:8px;transition:all 0.2s}
.danger-btn:hover{background:rgba(255,68,102,0.06)}
.usage-bar-container{margin-top:16px;margin-bottom:12px}
.usage-bar-bg{height:8px;background:var(--bg2);border-radius:4px;overflow:hidden}
.usage-bar{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));border-radius:4px;transition:width 0.5s}
.usage-text{display:flex;justify-content:space-between;margin-top:6px;font-size:10px;color:var(--muted)}

@media(max-width:768px){
  .detail-main{grid-template-columns:1fr}.detail-viewer{height:280px}
  .profile-stats{grid-template-columns:repeat(2,1fr)}
  .profile-top{text-align:center;justify-content:center;flex-direction:column}
  .settings-grid{grid-template-columns:1fr}
  .nav{padding:10px 14px}.container{padding:20px 12px}
  .style-grid{grid-template-columns:repeat(2,1fr)}.viewer{height:260px}
  .gal-grid{grid-template-columns:repeat(auto-fill,minmax(170px,1fr))}
}
</style>
</head>
<body>
<canvas id="bgCanvas"></canvas>
<div class="bg-gradient"></div>
<div class="bg-grid"></div>
<div class="scan-line"></div>

<!-- AUTH -->
<div class="auth-overlay" id="authOverlay">
  <div class="auth-box">
    <button class="auth-close" onclick="closeAuth()">&times;</button>
    <div class="auth-logo">PRINTFORGE</div>
    <div class="auth-tabs">
      <button class="auth-tab on" id="aLT" onclick="authTab('login')">GIRIS YAP</button>
      <button class="auth-tab" id="aRT" onclick="authTab('register')">KAYIT OL</button>
    </div>
    <div id="authMsg" class="auth-msg"></div>
    <div id="loginForm">
      <div class="fg"><label>E-POSTA</label><input type="email" id="lEmail" placeholder="ornek@gmail.com"></div>
      <div class="fg"><label>SIFRE</label><input type="password" id="lPass" placeholder="Sifreniz"></div>
      <button class="auth-btn" onclick="doLogin()">GIRIS YAP</button>
      <div class="auth-divider">veya</div>
      <button class="google-btn" id="googleBtn" onclick="googleLogin()"><svg viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.07 5.07 0 01-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/></svg>Google ile Devam Et</button>
      <div class="auth-footer">Hesabiniz yok mu? <a onclick="authTab('register')">Kayit Olun</a></div>
    </div>
    <div id="regForm" style="display:none">
      <div class="fg"><label>AD SOYAD</label><input type="text" id="rName" placeholder="Adiniz Soyadiniz"></div>
      <div class="fg"><label>E-POSTA</label><input type="email" id="rEmail" placeholder="ornek@gmail.com"></div>
      <div class="fg"><label>SIFRE</label><input type="password" id="rPass" placeholder="En az 6 karakter"></div>
      <p style="font-size:8px;color:var(--muted);margin-top:6px">Gmail, Outlook, Yahoo gibi gercek e-posta kullanin.</p>
      <button class="auth-btn" onclick="doRegister()">KAYIT OL</button>
      <div class="auth-divider">veya</div>
      <button class="google-btn" onclick="googleLogin()"><svg viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.07 5.07 0 01-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/></svg>Google ile Devam Et</button>
      <div class="auth-footer">Zaten hesabiniz var mi? <a onclick="authTab('login')">Giris Yapin</a></div>
    </div>
  </div>
</div>

<!-- DETAIL -->
<div class="detail-overlay" id="detailOverlay">
  <div class="detail-container">
    <button class="detail-back" onclick="closeDetail()">&#8592; Geri</button>
    <div class="detail-main">
      <div class="detail-viewer" id="detailViewer"></div>
      <div class="detail-info">
        <h2 class="detail-title" id="dTitle">-</h2>
        <div class="detail-author"><div class="detail-author-avatar" id="dAvatar">U</div><span id="dAuthor">-</span></div>
        <div class="detail-stats-row">
          <div class="detail-stat"><div class="detail-stat-num" id="dLikes">0</div><div class="detail-stat-lbl">BEGENI</div></div>
          <div class="detail-stat"><div class="detail-stat-num" id="dDls">0</div><div class="detail-stat-lbl">INDIRME</div></div>
          <div class="detail-stat"><div class="detail-stat-num" id="dType">-</div><div class="detail-stat-lbl">TUR</div></div>
        </div>
        <div class="detail-section" id="dPromptSec"><div class="detail-section-title">PROMPT</div><div class="detail-prompt" id="dPrompt">-</div></div>
        <div class="detail-section"><div class="detail-section-title">ETIKETLER</div><div class="detail-tags" id="dTags"></div></div>
        <div class="detail-section"><div class="detail-section-title">INDIR</div><div class="detail-dl-grid" id="dDlGrid"></div></div>
        <button class="detail-like-btn" id="dLikeBtn" onclick="likeDetail()">&#9829; Begen</button>
      </div>
    </div>
    <div class="similar-section" id="simSec"><div class="similar-title">Benzer Modeller</div><div class="similar-grid" id="simGrid"></div></div>
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
      <div class="nav-avatar" id="navAvatar" onclick="swTab('profile')">U</div>
      <button class="nbtn red" onclick="doLogout()">CIKIS</button>
    </div>
  </div>
</nav>
<div class="banner demo" id="demoBanner">DEMO MOD</div>
<div class="banner usage" id="usageBanner" style="display:none"></div>

<div class="container">
  <div class="tabs" id="mainTabs">
    <button class="tab on" onclick="swTab('gen')">URET</button>
    <button class="tab" onclick="swTab('gallery')">GALERI</button>
    <button class="tab" onclick="swTab('mymodels')">MODELLERIM</button>
    <button class="tab" onclick="swTab('profile')">PROFIL</button>
  </div>

  <!-- GEN -->
  <div class="panel on" id="pGen">
    <div class="tabs" style="margin-bottom:18px"><button class="tab on" onclick="swSub('text')">METIN</button><button class="tab" onclick="swSub('image')">GORSEL</button></div>
    <div class="panel on" id="pText">
      <div class="card"><label class="label">PROMPT</label><textarea id="prompt" placeholder="Orn: a cute robot toy..." rows="3"></textarea><div class="examples"><button class="ex-btn" onclick="setP('a cute cartoon robot toy')">Robot</button><button class="ex-btn" onclick="setP('a medieval stone castle')">Kale</button><button class="ex-btn" onclick="setP('a futuristic sports car')">Araba</button><button class="ex-btn" onclick="setP('a dragon miniature figure')">Ejderha</button><button class="ex-btn" onclick="setP('a geometric modern vase')">Vazo</button></div><label class="label" style="margin-top:14px">STIL</label><div class="style-grid"><button class="style-opt on" data-s="realistic" onclick="selS(this)"><span class="ico">&#128247;</span>Gercekci</button><button class="style-opt" data-s="cartoon" onclick="selS(this)"><span class="ico">&#127912;</span>Cartoon</button><button class="style-opt" data-s="lowpoly" onclick="selS(this)"><span class="ico">&#128142;</span>Low Poly</button><button class="style-opt" data-s="sculpture" onclick="selS(this)"><span class="ico">&#128511;</span>Heykel</button><button class="style-opt" data-s="mechanical" onclick="selS(this)"><span class="ico">&#9881;</span>Mekanik</button><button class="style-opt" data-s="miniature" onclick="selS(this)"><span class="ico">&#9823;</span>Minyatur</button></div></div>
      <button class="gen-btn" id="txtBtn" onclick="genText()">3D MODEL URET</button>
    </div>
    <div class="panel" id="pImage"><div class="card"><label class="label">GORSEL YUKLE</label><div class="upload" id="upArea"><div class="ico">&#11042;</div><p>Surukle-birak veya tikla</p><input type="file" id="fInp" accept="image/*" onchange="onFile(this)"></div><div class="preview" id="prev"><img id="prevImg" src=""><button class="rm" onclick="rmFile()">X</button></div></div><button class="gen-btn" id="imgBtn" onclick="genImage()" disabled>3D MODEL URET</button></div>
    <div class="sec" id="progSec"><div class="prog-card"><div class="prog-top"><span class="prog-title">Model Uretiliyor</span><span class="prog-pct" id="progPct">0%</span></div><div class="prog-bar-bg"><div class="prog-bar" id="progBar"></div></div><div class="prog-step" id="progStep"><div class="spinner"></div><span>...</span></div></div></div>
    <div class="sec" id="resSec"><div class="result-card"><h3>Model Hazir!</h3><p>Modeliniz olusturuldu</p><div class="viewer" id="viewer3d"></div><div class="dl-grid" id="dlGrid"></div><button class="new-btn" onclick="resetGen()">+ YENI MODEL</button></div></div>
    <div class="sec" id="errSec"><div class="err-card"><h3>Hata</h3><p id="errMsg">-</p><button class="new-btn" onclick="resetGen()">TEKRAR DENE</button></div></div>
  </div>

  <!-- GALLERY -->
  <div class="panel" id="pGallery">
    <div class="gal-toolbar"><input type="text" id="galSearch" placeholder="Model ara..." onkeyup="if(event.key==='Enter')loadGallery()"><select id="galSort" onchange="loadGallery()"><option value="newest">En Yeni</option><option value="popular">Populer</option><option value="downloads">Indirilen</option></select><button class="nbtn accent" onclick="loadGallery()">ARA</button></div>
    <div class="gal-grid" id="galGrid"><div class="gal-empty">Yukleniyor...</div></div>
  </div>

  <!-- MY MODELS -->
  <div class="panel" id="pMyModels"><div class="gal-grid" id="myGrid"><div class="gal-empty">Giris yapin</div></div></div>

  <!-- PROFILE -->
  <div class="panel" id="pProfile">
    <div class="profile-header">
      <div class="profile-top">
        <div class="profile-avatar" id="profAvatar">U</div>
        <div>
          <div class="profile-name" id="profName">-</div>
          <div class="profile-email" id="profEmail">-</div>
          <div class="profile-plan free" id="profPlan">UCRETSIZ</div>
        </div>
      </div>
      <div class="profile-stats">
        <div class="pstat"><div class="pstat-num" id="psModels">0</div><div class="pstat-lbl">MODEL</div></div>
        <div class="pstat"><div class="pstat-num" id="psLikes">0</div><div class="pstat-lbl">BEGENI</div></div>
        <div class="pstat"><div class="pstat-num" id="psDls">0</div><div class="pstat-lbl">INDIRME</div></div>
        <div class="pstat"><div class="pstat-num" id="psUsage">-</div><div class="pstat-lbl">KULLANIM</div></div>
      </div>
    </div>
    <div class="usage-bar-container">
      <div class="label">AYLIK KULLANIM</div>
      <div class="usage-bar-bg"><div class="usage-bar" id="usageBar" style="width:0%"></div></div>
      <div class="usage-text"><span id="usageLeft">- model kaldi</span><span id="usageTotal">- / -</span></div>
    </div>
    <div class="profile-tabs">
      <button class="ptab on" onclick="swProfTab('settings',this)">AYARLAR</button>
      <button class="ptab" onclick="swProfTab('plan',this)">PLAN</button>
    </div>
    <div id="profSettings">
      <div class="card">
        <div class="settings-grid">
          <div class="fg"><label>AD SOYAD</label><input type="text" id="sName" placeholder="Adiniz"></div>
          <div class="fg"><label>YENI SIFRE</label><input type="password" id="sPass" placeholder="Degistirmek icin girin"></div>
        </div>
        <button class="save-btn" onclick="saveProfile()">KAYDET</button>
        <button class="danger-btn" onclick="doLogout()">CIKIS YAP</button>
      </div>
    </div>
    <div id="profPlanSec" style="display:none">
      <div class="card">
        <div class="label">MEVCUT PLAN</div>
        <p style="font-size:13px;margin-bottom:16px;font-weight:600" id="planName">Ucretsiz</p>
        <p style="font-size:11px;color:var(--muted);margin-bottom:18px">Daha fazla model uretmek icin Pro plana gecin.</p>
        <button class="gen-btn" style="max-width:300px" onclick="upgradePlan()">PRO PLANA YUKSELT</button>
      </div>
    </div>
  </div>
</div>

<script>
// PARTICLES BG
(function(){
  var canvas=document.getElementById('bgCanvas'),ctx=canvas.getContext('2d');
  var particles=[],mouse={x:0,y:0};
  function resize(){canvas.width=window.innerWidth;canvas.height=window.innerHeight}
  resize();window.addEventListener('resize',resize);
  window.addEventListener('mousemove',function(e){mouse.x=e.clientX;mouse.y=e.clientY});
  for(var i=0;i<60;i++){
    particles.push({x:Math.random()*canvas.width,y:Math.random()*canvas.height,vx:(Math.random()-0.5)*0.3,vy:(Math.random()-0.5)*0.3,r:Math.random()*1.5+0.5,o:Math.random()*0.3+0.05});
  }
  function draw(){
    ctx.clearRect(0,0,canvas.width,canvas.height);
    for(var i=0;i<particles.length;i++){
      var p=particles[i];
      p.x+=p.vx;p.y+=p.vy;
      if(p.x<0)p.x=canvas.width;if(p.x>canvas.width)p.x=0;
      if(p.y<0)p.y=canvas.height;if(p.y>canvas.height)p.y=0;
      ctx.beginPath();ctx.arc(p.x,p.y,p.r,0,Math.PI*2);
      ctx.fillStyle='rgba(0,229,255,'+p.o+')';ctx.fill();
      // Lines
      for(var j=i+1;j<particles.length;j++){
        var p2=particles[j],dx=p.x-p2.x,dy=p.y-p2.y,dist=Math.sqrt(dx*dx+dy*dy);
        if(dist<120){ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(p2.x,p2.y);ctx.strokeStyle='rgba(0,229,255,'+(0.06*(1-dist/120))+')';ctx.lineWidth=0.5;ctx.stroke()}
      }
      // Mouse interaction
      var mdx=p.x-mouse.x,mdy=p.y-mouse.y,mdist=Math.sqrt(mdx*mdx+mdy*mdy);
      if(mdist<150){ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(mouse.x,mouse.y);ctx.strokeStyle='rgba(0,255,157,'+(0.08*(1-mdist/150))+')';ctx.lineWidth=0.5;ctx.stroke()}
    }
    requestAnimationFrame(draw);
  }
  draw();
})();

var API=window.location.origin,token=localStorage.getItem('pf_token')||'',user=null,userStats=null,userUsage=null,style='realistic',selFile=null,poll=null,curDetailId=null;

function hdrs(){var h={'Content-Type':'application/json'};if(token)h['Authorization']='Bearer '+token;return h}
function ahdrs(){var h={};if(token)h['Authorization']='Bearer '+token;return h}

function checkApi(){fetch(API+'/api/health').then(function(r){return r.json()}).then(function(d){var el=document.getElementById('apiSt');if(d.is_demo){el.innerHTML='<div class="nav-dot" style="background:var(--orange);animation:pulse 2s infinite"></div><span style="color:var(--orange)">DEMO</span>';document.getElementById('demoBanner').style.display='block'}else{el.innerHTML='<div class="nav-dot" style="background:var(--accent2);animation:pulse 2s infinite"></div><span style="color:var(--accent2)">'+d.active_api.toUpperCase()+'</span>'}if(!d.google_ready){var gb=document.querySelectorAll('.google-btn');for(var i=0;i<gb.length;i++)gb[i].style.display='none';var ad=document.querySelectorAll('.auth-divider');for(var i=0;i<ad.length;i++)ad[i].style.display='none'}}).catch(function(){})}

function openAuth(t){document.getElementById('authOverlay').classList.add('on');authTab(t||'login')}
function closeAuth(){document.getElementById('authOverlay').classList.remove('on');document.getElementById('authMsg').className='auth-msg'}
function authTab(t){document.getElementById('loginForm').style.display=t==='login'?'block':'none';document.getElementById('regForm').style.display=t==='register'?'block':'none';document.getElementById('aLT').className='auth-tab'+(t==='login'?' on':'');document.getElementById('aRT').className='auth-tab'+(t==='register'?' on':'');document.getElementById('authMsg').className='auth-msg'}
function authErr(m){var e=document.getElementById('authMsg');e.className='auth-msg err';e.textContent=m}
function authOk(m){var e=document.getElementById('authMsg');e.className='auth-msg ok';e.textContent=m}

function doLogin(){var e=document.getElementById('lEmail').value.trim(),p=document.getElementById('lPass').value;if(!e||!p){authErr('Alanlari doldurun');return}fetch(API+'/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:e,password:p})}).then(function(r){if(!r.ok)return r.json().then(function(e){throw new Error(e.detail)});return r.json()}).then(function(d){token=d.token;user=d.user;localStorage.setItem('pf_token',token);authOk('Basarili!');setTimeout(function(){closeAuth();updateUI();checkAuth()},600)}).catch(function(e){authErr(e.message)})}

function doRegister(){var n=document.getElementById('rName').value.trim(),e=document.getElementById('rEmail').value.trim(),p=document.getElementById('rPass').value;if(!n||!e||!p){authErr('Alanlari doldurun');return}if(p.length<6){authErr('Sifre en az 6 karakter');return}fetch(API+'/api/auth/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:n,email:e,password:p})}).then(function(r){if(!r.ok)return r.json().then(function(e){throw new Error(e.detail)});return r.json()}).then(function(d){token=d.token;user=d.user;localStorage.setItem('pf_token',token);authOk('Hesap olusturuldu!');setTimeout(function(){closeAuth();updateUI();checkAuth()},600)}).catch(function(e){authErr(e.message)})}

function googleLogin(){window.location.href=API+'/api/auth/google'}
function doLogout(){token='';user=null;userStats=null;userUsage=null;localStorage.removeItem('pf_token');updateUI();swTab('gen')}

function checkAuth(){if(!token)return;fetch(API+'/api/auth/me',{headers:ahdrs()}).then(function(r){if(!r.ok)throw new Error();return r.json()}).then(function(d){user=d.user;userStats=d.stats;userUsage=d.usage;updateUI();updateProfile()}).catch(function(){token='';localStorage.removeItem('pf_token');updateUI()})}

function updateUI(){
  if(user){document.getElementById('navGuest').style.display='none';document.getElementById('navUser').style.display='flex';document.getElementById('navName').textContent=user.name;document.getElementById('navAvatar').textContent=user.name[0].toUpperCase();if(userUsage){document.getElementById('navUsage').textContent=userUsage.remaining+'/'+userUsage.limit;var ub=document.getElementById('usageBanner');ub.textContent=userUsage.remaining+' model hakkiniz kaldi';ub.style.display='block'}}
  else{document.getElementById('navGuest').style.display='flex';document.getElementById('navUser').style.display='none';document.getElementById('usageBanner').style.display='none'}
}

function updateProfile(){
  if(!user)return;
  document.getElementById('profAvatar').textContent=user.name[0].toUpperCase();
  document.getElementById('profName').textContent=user.name;
  document.getElementById('profEmail').textContent=user.email;
  var plan=user.plan||'free';
  var pe=document.getElementById('profPlan');
  pe.textContent=plan==='pro'?'PRO':plan==='business'?'ISLETME':'UCRETSIZ';
  pe.className='profile-plan '+plan;
  document.getElementById('sName').value=user.name;
  document.getElementById('planName').textContent=plan==='pro'?'Pro Plan':plan==='business'?'Isletme Plan':'Ucretsiz Plan';
  if(userStats){document.getElementById('psModels').textContent=userStats.models;document.getElementById('psLikes').textContent=userStats.likes;document.getElementById('psDls').textContent=userStats.downloads}
  if(userUsage){document.getElementById('psUsage').textContent=userUsage.used+'/'+userUsage.limit;var pct=userUsage.limit>0?Math.round(userUsage.used/userUsage.limit*100):0;document.getElementById('usageBar').style.width=pct+'%';document.getElementById('usageLeft').textContent=userUsage.remaining+' model kaldi';document.getElementById('usageTotal').textContent=userUsage.used+' / '+userUsage.limit}
}

function saveProfile(){
  var n=document.getElementById('sName').value.trim(),p=document.getElementById('sPass').value;
  var body={};if(n)body.name=n;if(p)body.password=p;
  fetch(API+'/api/auth/update-profile',{method:'POST',headers:hdrs(),body:JSON.stringify(body)}).then(function(r){return r.json()}).then(function(){alert('Kaydedildi!');checkAuth()}).catch(function(){})
}

function upgradePlan(){fetch(API+'/api/payment/upgrade',{method:'POST',headers:ahdrs()}).then(function(r){return r.json()}).then(function(){alert('Pro plana yukseltildi!');checkAuth()}).catch(function(){})}

function swTab(t){
  var panels=['pGen','pGallery','pMyModels','pProfile'];
  var names=['gen','gallery','mymodels','profile'];
  panels.forEach(function(p,i){document.getElementById(p).className='panel'+(names[i]===t?' on':'')});
  var tabs=document.querySelectorAll('#mainTabs .tab');
  for(var i=0;i<tabs.length;i++)tabs[i].className='tab'+(names[i]===t?' on':'');
  if(t==='gallery')loadGallery();if(t==='mymodels')loadMyModels();if(t==='profile'){if(!user){openAuth('login');return}checkAuth()}
}
function swSub(t){document.getElementById('pText').className='panel'+(t==='text'?' on':'');document.getElementById('pImage').className='panel'+(t==='image'?' on':'');var st=document.querySelectorAll('#pGen>.tabs>.tab');st[0].className='tab'+(t==='text'?' on':'');st[1].className='tab'+(t==='image'?' on':'')}
function swProfTab(t,btn){document.getElementById('profSettings').style.display=t==='settings'?'block':'none';document.getElementById('profPlanSec').style.display=t==='plan'?'block':'none';document.querySelectorAll('.ptab').forEach(function(b){b.className='ptab'});btn.className='ptab on'}
function setP(t){document.getElementById('prompt').value=t}
function selS(el){document.querySelectorAll('.style-opt').forEach(function(s){s.className='style-opt'});el.className='style-opt on';style=el.getAttribute('data-s')}

var upArea=document.getElementById('upArea');
upArea.addEventListener('dragover',function(e){e.preventDefault();upArea.classList.add('drag')});upArea.addEventListener('dragleave',function(){upArea.classList.remove('drag')});
upArea.addEventListener('drop',function(e){e.preventDefault();upArea.classList.remove('drag');if(e.dataTransfer.files[0]){document.getElementById('fInp').files=e.dataTransfer.files;onFile(document.getElementById('fInp'))}});
function onFile(inp){var f=inp.files[0];if(!f)return;if(f.size>10485760){alert('Max 10MB');return}selFile=f;var rd=new FileReader();rd.onload=function(e){document.getElementById('prevImg').src=e.target.result;document.getElementById('prev').className='preview on';upArea.classList.add('has');document.getElementById('imgBtn').disabled=false};rd.readAsDataURL(f)}
function rmFile(){selFile=null;document.getElementById('fInp').value='';document.getElementById('prev').className='preview';upArea.classList.remove('has');document.getElementById('imgBtn').disabled=true}

function genText(){var p=document.getElementById('prompt').value.trim();if(!p){alert('Prompt girin!');return}showProg();disable(true);fetch(API+'/api/generate/text',{method:'POST',headers:hdrs(),body:JSON.stringify({prompt:p,style:style})}).then(function(r){if(!r.ok)return r.json().then(function(e){throw new Error(e.detail)});return r.json()}).then(function(d){startPoll(d.task_id)}).catch(function(e){showErr(e.message)})}
function genImage(){if(!selFile)return;showProg();disable(true);var fd=new FormData();fd.append('file',selFile);var h={};if(token)h['Authorization']='Bearer '+token;fetch(API+'/api/generate/image',{method:'POST',body:fd,headers:h}).then(function(r){if(!r.ok)return r.json().then(function(e){throw new Error(e.detail)});return r.json()}).then(function(d){startPoll(d.task_id)}).catch(function(e){showErr(e.message)})}
function startPoll(tid){if(poll)clearInterval(poll);poll=setInterval(function(){fetch(API+'/api/status/'+tid).then(function(r){return r.json()}).then(function(d){updProg(d.progress,d.step||'...');if(d.status==='done'){clearInterval(poll);showRes(tid);if(user)checkAuth()}else if(d.status==='failed'){clearInterval(poll);showErr(d.error||'Hata')}}).catch(function(){})},2500)}
function showProg(){hide('resSec');hide('errSec');show('progSec');updProg(0,'Baslatiliyor...')}
function updProg(p,s){document.getElementById('progBar').style.width=p+'%';document.getElementById('progPct').textContent=p+'%';document.getElementById('progStep').innerHTML='<div class="spinner"></div><span>'+s+'</span>'}
function showRes(tid){hide('progSec');show('resSec');disable(false);var vw=API+'/api/model/'+tid+'/view';document.getElementById('viewer3d').innerHTML='<model-viewer src="'+vw+'" auto-rotate camera-controls style="width:100%;height:100%;background:#070d10" loading="eager" shadow-intensity="1" environment-image="neutral" camera-orbit="45deg 55deg auto"></model-viewer>';document.getElementById('dlGrid').innerHTML='<a class="dl-btn primary" href="'+API+'/api/model/'+tid+'/glb" download>GLB<span class="dl-fmt">3D VIEWER</span></a><a class="dl-btn" href="'+API+'/api/model/'+tid+'/stl" download>STL<span class="dl-fmt">3D BASKI</span></a><a class="dl-btn" href="'+API+'/api/model/'+tid+'/obj" download>OBJ<span class="dl-fmt">MODELLEME</span></a>'}
function showErr(m){hide('progSec');show('errSec');disable(false);document.getElementById('errMsg').textContent=m}
function resetGen(){if(poll)clearInterval(poll);hide('progSec');hide('resSec');hide('errSec');disable(false);document.getElementById('viewer3d').innerHTML=''}
function show(id){document.getElementById(id).classList.add('on')}
function hide(id){document.getElementById(id).classList.remove('on')}
function disable(v){document.getElementById('txtBtn').disabled=v;document.getElementById('imgBtn').disabled=v||!selFile}

function loadGallery(){var s=document.getElementById('galSearch').value||'',sort=document.getElementById('galSort').value||'newest';document.getElementById('galGrid').innerHTML='<div class="gal-empty">Yukleniyor...</div>';fetch(API+'/api/gallery?search='+encodeURIComponent(s)+'&sort='+sort).then(function(r){return r.json()}).then(function(d){if(!d.models||!d.models.length){document.getElementById('galGrid').innerHTML='<div class="gal-empty">Henuz model yok</div>';return}var h='';d.models.forEach(function(m){var vU=m.task_id?API+'/api/model/'+m.task_id+'/view':'';var th=vU?'<model-viewer src="'+vU+'" auto-rotate camera-controls style="width:100%;height:100%;background:#070d10" loading="lazy"></model-viewer>':'';var bg=m.style?'<div class="gal-badge">'+m.style.toUpperCase()+'</div>':'';h+='<div class="gal-card" onclick="openDetail('+m.id+')"><div class="gal-thumb">'+th+bg+'</div><div class="gal-body"><div class="gal-title">'+(m.title||'Model')+'</div><div class="gal-meta"><span>'+(m.author_name||'Anonim')+'</span><span>'+(m.created_at||'').slice(0,10)+'</span></div><div class="gal-stats"><div class="gal-stat">&#9829; <span>'+m.likes+'</span></div><div class="gal-stat">&#8595; <span>'+m.downloads+'</span></div></div><div class="gal-actions">';if(m.task_id)h+='<a class="gal-btn dl" href="'+API+'/api/model/'+m.task_id+'/glb" download onclick="event.stopPropagation()">GLB</a><a class="gal-btn dl" href="'+API+'/api/model/'+m.task_id+'/stl" download onclick="event.stopPropagation()">STL</a>';h+='<button class="gal-btn" onclick="event.stopPropagation();likeModel('+m.id+',this)">&#9829;</button></div></div></div>'});document.getElementById('galGrid').innerHTML=h}).catch(function(){document.getElementById('galGrid').innerHTML='<div class="gal-empty">Hata</div>'})}

function likeModel(id,btn){if(!token){openAuth('login');return}fetch(API+'/api/gallery/'+id+'/like',{method:'POST',headers:ahdrs()}).then(function(r){return r.json()}).then(function(d){btn.className='gal-btn'+(d.liked?' liked':'');loadGallery()}).catch(function(){})}

function openDetail(id){curDetailId=id;document.getElementById('detailOverlay').classList.add('on');document.body.style.overflow='hidden';fetch(API+'/api/gallery/'+id).then(function(r){return r.json()}).then(function(m){document.getElementById('dTitle').textContent=m.title||'Model';document.getElementById('dAuthor').textContent=m.author_name||'Anonim';document.getElementById('dAvatar').textContent=(m.author_name||'A')[0].toUpperCase();document.getElementById('dLikes').textContent=m.likes||0;document.getElementById('dDls').textContent=m.downloads||0;document.getElementById('dType').textContent=(m.gen_type||'-').toUpperCase();if(m.prompt&&m.prompt!=='demo'){document.getElementById('dPromptSec').style.display='block';document.getElementById('dPrompt').textContent=m.prompt}else{document.getElementById('dPromptSec').style.display='none'}var tags='';if(m.style)tags+='<div class="detail-tag">'+m.style+'</div>';if(m.gen_type)tags+='<div class="detail-tag">'+m.gen_type+'</div>';tags+='<div class="detail-tag">GLB</div>';document.getElementById('dTags').innerHTML=tags;if(m.task_id){var vU=API+'/api/model/'+m.task_id+'/view';document.getElementById('detailViewer').innerHTML='<model-viewer src="'+vU+'" auto-rotate camera-controls style="width:100%;height:100%;background:#070d10" loading="eager" shadow-intensity="1" environment-image="neutral" camera-orbit="45deg 55deg auto"></model-viewer>';document.getElementById('dDlGrid').innerHTML='<a class="detail-dl primary" href="'+API+'/api/model/'+m.task_id+'/glb" download><span class="dl-name">GLB</span><span class="dl-desc">3D Viewer</span></a><a class="detail-dl" href="'+API+'/api/model/'+m.task_id+'/stl" download><span class="dl-name">STL</span><span class="dl-desc">3D Baski</span></a><a class="detail-dl" href="'+API+'/api/model/'+m.task_id+'/obj" download><span class="dl-name">OBJ</span><span class="dl-desc">Modelleme</span></a>'}loadSimilar(id)}).catch(function(){})}

function closeDetail(){document.getElementById('detailOverlay').classList.remove('on');document.body.style.overflow='';curDetailId=null}
function likeDetail(){if(!token){openAuth('login');return}if(!curDetailId)return;fetch(API+'/api/gallery/'+curDetailId+'/like',{method:'POST',headers:ahdrs()}).then(function(r){return r.json()}).then(function(d){var b=document.getElementById('dLikeBtn');b.className='detail-like-btn'+(d.liked?' liked':'');b.innerHTML=d.liked?'&#9829; Begenildi':'&#9829; Begen';openDetail(curDetailId)}).catch(function(){})}
function loadSimilar(id){fetch(API+'/api/gallery/'+id+'/similar').then(function(r){return r.json()}).then(function(d){if(!d.models||!d.models.length){document.getElementById('simSec').style.display='none';return}document.getElementById('simSec').style.display='block';var h='';d.models.forEach(function(m){var vU=m.task_id?API+'/api/model/'+m.task_id+'/view':'';var th=vU?'<model-viewer src="'+vU+'" auto-rotate camera-controls style="width:100%;height:100%;background:#070d10" loading="lazy"></model-viewer>':'';h+='<div class="similar-card" onclick="openDetail('+m.id+')"><div class="similar-thumb">'+th+'</div><div class="similar-body"><div class="similar-name">'+(m.title||'Model')+'</div><div class="similar-meta">'+(m.author_name||'')+' &#183; &#9829;'+m.likes+'</div></div></div>'});document.getElementById('simGrid').innerHTML=h}).catch(function(){document.getElementById('simSec').style.display='none'})}

function loadMyModels(){if(!token){document.getElementById('myGrid').innerHTML='<div class="gal-empty"><a href="#" onclick="openAuth(\'login\');return false" style="color:var(--accent)">Giris yapin</a></div>';return}document.getElementById('myGrid').innerHTML='<div class="gal-empty">Yukleniyor...</div>';fetch(API+'/api/my-models',{headers:ahdrs()}).then(function(r){return r.json()}).then(function(d){if(!d.models||!d.models.length){document.getElementById('myGrid').innerHTML='<div class="gal-empty">Henuz model yok</div>';return}var h='';d.models.forEach(function(m){var vU=m.task_id?API+'/api/model/'+m.task_id+'/view':'';var th=vU?'<model-viewer src="'+vU+'" auto-rotate camera-controls style="width:100%;height:100%;background:#070d10" loading="lazy"></model-viewer>':'';h+='<div class="gal-card" onclick="openDetail('+m.id+')"><div class="gal-thumb">'+th+'</div><div class="gal-body"><div class="gal-title">'+(m.title||'Model')+'</div><div class="gal-meta"><span>'+(m.gen_type||'')+'</span><span>'+(m.created_at||'').slice(0,10)+'</span></div><div class="gal-actions"><a class="gal-btn dl" href="'+API+'/api/model/'+m.task_id+'/glb" download onclick="event.stopPropagation()">GLB</a><a class="gal-btn dl" href="'+API+'/api/model/'+m.task_id+'/stl" download onclick="event.stopPropagation()">STL</a><button class="gal-btn" style="color:var(--red)" onclick="event.stopPropagation();deleteModel('+m.id+')">SIL</button></div></div></div>'});document.getElementById('myGrid').innerHTML=h}).catch(function(){})}

function deleteModel(id){if(!confirm('Silmek istiyor musunuz?'))return;fetch(API+'/api/my-models/'+id,{method:'DELETE',headers:ahdrs()}).then(function(){loadMyModels()}).catch(function(){})}

document.addEventListener('keydown',function(e){if(e.key==='Escape'){if(document.getElementById('detailOverlay').classList.contains('on'))closeDetail();else if(document.getElementById('authOverlay').classList.contains('on'))closeAuth()}});

checkApi();checkAuth();
</script>
</body>
</html>"""
