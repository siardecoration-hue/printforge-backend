from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, Response, RedirectResponse
from pydantic import BaseModel
import asyncio, uuid, httpx, base64, random, json, os, io, re
import hashlib, secrets, sqlite3, hmac, time
from datetime import datetime, timedelta
from typing import Optional, List
from urllib.parse import urlencode, urlparse

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

app = FastAPI(title="PrintForge", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

TRIPO_API_KEY = os.getenv("TRIPO_API_KEY", "")
MESHY_API_KEY = os.getenv("MESHY_API_KEY", "")
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))
DB_PATH = os.getenv("DB_PATH", "printforge.db")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "onboarding@resend.dev")
TRIPO_BASE = "https://api.tripo3d.ai/v2/openapi"
MESHY_BASE = "https://api.meshy.ai/openapi/v2"

def get_site_url():
    d = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
    return f"https://{d}" if d else "http://localhost:8000"

tasks = {}
model_cache = {}
MAX_CACHE = 50
PLAN_LIMITS = {"free": 5, "pro": 100, "business": 999999}
MODEL_DIR = os.getenv("MODEL_DIR", "models_store")
os.makedirs(MODEL_DIR, exist_ok=True)

def model_file_path(task_id: str) -> str:
    return os.path.join(MODEL_DIR, f"{task_id}.glb")

async def download_glb_bytes(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        r = await c.get(url, headers={"Accept": "model/gltf-binary,application/octet-stream,*/*"})
        if r.status_code != 200 or len(r.content) < 100:
            raise Exception(f"GLB indirilemedi: {r.status_code}, size={len(r.content)}")
        data = r.content
        if len(data) < 4 or data[:4] != b"glTF":
            raise Exception("Gelen dosya gecerli GLB degil")
        return data

async def persist_model_glb(task_id: str, url: str):
    data = await download_glb_bytes(url)
    with open(model_file_path(task_id), "wb") as f:
        f.write(data)
    model_cache[task_id] = data
DEMO_MODELS = [
    {"name": "Damaged Helmet", "glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb"},
    {"name": "Avocado", "glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/Avocado/glTF-Binary/Avocado.glb"},
    {"name": "Duck", "glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/Duck/glTF-Binary/Duck.glb"},
    {"name": "Lantern", "glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/Lantern/glTF-Binary/Lantern.glb"},
    {"name": "Water Bottle", "glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/WaterBottle/glTF-Binary/WaterBottle.glb"},
]
async def cache_model(tid, url):
    try:
        await persist_model_glb(tid, url)
        return True
    except Exception as e:
        print(f"[CACHE] basarisiz: {e}")
        return False
# ════════ RATE LIMITING ════════
rate_limits = {}
login_attempts = {}

def check_rate_limit(ip, action="general", max_req=60, window=60):
    key = f"{ip}:{action}"
    now = time.time()
    if key not in rate_limits:
        rate_limits[key] = []
    rate_limits[key] = [t for t in rate_limits[key] if now - t < window]
    if len(rate_limits[key]) >= max_req:
        return False
    rate_limits[key].append(now)
    return True

def check_login_attempt(ip):
    now = time.time()
    if ip not in login_attempts:
        login_attempts[ip] = []
    login_attempts[ip] = [t for t in login_attempts[ip] if now - t < 900]
    return len(login_attempts[ip]) < 5

def record_login_fail(ip):
    if ip not in login_attempts:
        login_attempts[ip] = []
    login_attempts[ip].append(time.time())

def get_client_ip(request: Request):
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() if forwarded else request.client.host

# ════════ GUVENLIK ════════
def hash_pw(pw):
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac('sha512', pw.encode(), salt.encode(), 310000).hex()
    return salt, h

def verify_pw(pw, salt, h):
    computed = hashlib.pbkdf2_hmac('sha512', pw.encode(), salt.encode(), 310000).hex()
    return hmac.compare_digest(computed, h)

def create_token(uid, email, name, plan):
    if not HAS_JWT:
        return "no-jwt"
    return pyjwt.encode(
        {"user_id": uid, "email": email, "name": name, "plan": plan,
         "exp": datetime.utcnow() + timedelta(days=7)},
        SECRET_KEY, algorithm="HS256"
    )

def decode_token(t):
    if not HAS_JWT:
        return None
    try:
        return pyjwt.decode(t, SECRET_KEY, algorithms=["HS256"])
    except:
        return None

def sanitize(text):
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', '', str(text))
    text = text.replace('&', '&amp;').replace('"', '&quot;')
    return text.strip()[:500]

def validate_password(pw):
    if len(pw) < 8:
        return False, "Sifre en az 8 karakter olmali"
    if not re.search(r'[a-zA-Z]', pw):
        return False, "Sifre en az 1 harf icermeli"
    if not re.search(r'[0-9]', pw):
        return False, "Sifre en az 1 rakam icermeli"
    return True, "OK"

BLOCKED_DOMAINS = set([
    "tempmail.com","throwaway.email","guerrillamail.com","mailinator.com",
    "yopmail.com","sharklasers.com","guerrillamailblock.com","grr.la",
    "dispostable.com","trashmail.com","10minutemail.com","temp-mail.org",
])

async def validate_email(email):
    email = email.lower().strip()
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9._%+-]*@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return False, "Gecerli bir e-posta girin"
    local, domain = email.split("@", 1)
    if len(local) < 2:
        return False, "E-posta cok kisa"
    if domain in BLOCKED_DOMAINS:
        return False, "Gecici e-posta kabul edilmiyor"
    return True, "OK"

def generate_username(name, uid):
    base = re.sub(r'[^a-z0-9]', '', name.lower().replace(' ', ''))[:15]
    if not base:
        base = "user"
    return f"{base}{uid}"


# ════════ E-POSTA ════════
async def send_email(to, subject, html_content):
    if not RESEND_API_KEY:
        print(f"[MAIL] API key yok: {to}")
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post("https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                json={"from": EMAIL_FROM, "to": [to], "subject": subject, "html": html_content})
            return r.status_code in (200, 201)
    except:
        return False

async def send_verification_email(email, token):
    link = f"{get_site_url()}/api/auth/verify?token={token}"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;padding:30px;background:#04080a;border:1px solid #0e2028;border-radius:12px">
        <div style="text-align:center;margin-bottom:24px">
            <span style="font-size:24px;font-weight:800;color:#00e5ff;letter-spacing:0.1em">PRINTFORGE</span>
        </div>
        <h2 style="color:#c8dde5;font-size:18px;margin-bottom:12px">Hesabinizi Dogrulayin</h2>
        <p style="color:#2a4a5a;font-size:14px;line-height:1.8;margin-bottom:24px">
            PrintForge'a hosgeldiniz! Hesabinizi aktif etmek icin asagidaki butona tiklayin.
        </p>
        <div style="text-align:center;margin-bottom:24px">
            <a href="{link}" style="display:inline-block;padding:14px 36px;background:#00e5ff;color:#04080a;text-decoration:none;font-weight:700;font-size:14px;border-radius:8px">
                Hesabi Dogrula
            </a>
        </div>
        <p style="color:#2a4a5a;font-size:11px">Bu link 24 saat gecerlidir.</p>
    </div>"""
    return await send_email(email, "PrintForge - Hesap Dogrulama", html)

async def send_reset_email(email, token):
    link = f"{get_site_url()}/app?reset={token}"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;padding:30px;background:#04080a;border:1px solid #0e2028;border-radius:12px">
        <div style="text-align:center;margin-bottom:24px">
            <span style="font-size:24px;font-weight:800;color:#00e5ff;letter-spacing:0.1em">PRINTFORGE</span>
        </div>
        <h2 style="color:#c8dde5;font-size:18px;margin-bottom:12px">Sifre Sifirlama</h2>
        <p style="color:#2a4a5a;font-size:14px;line-height:1.8;margin-bottom:24px">
            Sifrenizi sifirlamak icin asagidaki butona tiklayin.
        </p>
        <div style="text-align:center;margin-bottom:24px">
            <a href="{link}" style="display:inline-block;padding:14px 36px;background:#00e5ff;color:#04080a;text-decoration:none;font-weight:700;font-size:14px;border-radius:8px">
                Sifremi Sifirla
            </a>
        </div>
        <p style="color:#2a4a5a;font-size:11px">Bu link 1 saat gecerlidir.</p>
    </div>"""
    return await send_email(email, "PrintForge - Sifre Sifirlama", html)

async def send_welcome_email(email, name):
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;padding:30px;background:#04080a;border:1px solid #0e2028;border-radius:12px">
        <div style="text-align:center;margin-bottom:24px">
            <span style="font-size:24px;font-weight:800;color:#00e5ff;letter-spacing:0.1em">PRINTFORGE</span>
        </div>
        <h2 style="color:#c8dde5;font-size:18px;margin-bottom:12px">Hosgeldin {name}! 🎉</h2>
        <p style="color:#2a4a5a;font-size:14px;line-height:1.8;margin-bottom:24px">
            PrintForge'a katildigin icin tesekkurler! Artik AI ile 3D model uretebilirsin.
        </p>
        <ul style="color:#c8dde5;font-size:13px;line-height:2;margin-bottom:24px">
            <li>✅ Ayda 5 ucretsiz model uret</li>
            <li>✅ GLB, STL, OBJ formatinda indir</li>
            <li>✅ Topluluk galerisin kesfet</li>
            <li>✅ Koleksiyonlar olustur</li>
        </ul>
        <div style="text-align:center">
            <a href="{get_site_url()}/app" style="display:inline-block;padding:14px 36px;background:#00e5ff;color:#04080a;text-decoration:none;font-weight:700;font-size:14px;border-radius:8px">
                Hemen Basla →
            </a>
        </div>
    </div>"""
    return await send_email(email, f"PrintForge'a Hosgeldin, {name}!", html)

async def send_follow_email(follower_name, target_email, target_name):
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;padding:30px;background:#04080a;border:1px solid #0e2028;border-radius:12px">
        <div style="text-align:center;margin-bottom:24px">
            <span style="font-size:24px;font-weight:800;color:#00e5ff;letter-spacing:0.1em">PRINTFORGE</span>
        </div>
        <h2 style="color:#c8dde5;font-size:18px;margin-bottom:12px">Yeni Takipci! 🎉</h2>
        <p style="color:#2a4a5a;font-size:14px;line-height:1.8">
            <strong style="color:#00e5ff">{follower_name}</strong> seni takip etmeye basladi!
        </p>
    </div>"""
    return await send_email(target_email, f"{follower_name} seni takip ediyor!", html)


# ════════ MODELS ════════
class TextRequest(BaseModel):
    prompt: str
    style: str = "realistic"
    negative_prompt: str = ""
    tags: str = ""

class RegisterReq(BaseModel):
    name: str
    email: str
    password: str

class LoginReq(BaseModel):
    email: str
    password: str

class UpdateProfileReq(BaseModel):
    name: Optional[str] = None
    password: Optional[str] = None
    bio: Optional[str] = None
    website: Optional[str] = None

class ForgotPasswordReq(BaseModel):
    email: str

class ResetPasswordReq(BaseModel):
    token: str
    password: str

class CollectionReq(BaseModel):
    name: str
    description: str = ""
    is_public: int = 1

class CollectionItemReq(BaseModel):
    model_id: int

class BlogPostReq(BaseModel):
    title: str
    slug: str
    excerpt: str = ""
    content: str
    cover_image: str = ""
    tags: str = ""

class TagModelReq(BaseModel):
    tags: str  # comma separated


STYLE_MAP = {
    "realistic": "realistic", "cartoon": "cartoon", "lowpoly": "low-poly",
    "sculpture": "sculpture", "mechanical": "pbr", "miniature": "sculpture",
    "geometric": "realistic",
}

CATEGORIES = [
    "karakter", "arac", "mimari", "dekor", "mobilya", "hayvan",
    "yiyecek", "aksesuar", "silah", "bitki", "elektronik", "oyuncak",
    "spor", "muzik", "diger"
]

def get_api():
    if TRIPO_API_KEY:
        return "tripo"
    if MESHY_API_KEY:
        return "meshy"
    return "demo"


# ════════ VERITABANI ════════
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            username TEXT UNIQUE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            plan TEXT DEFAULT 'free',
            bio TEXT DEFAULT '',
            website TEXT DEFAULT '',
            google_id TEXT,
            avatar_url TEXT,
            verified INTEGER DEFAULT 0,
            verify_token TEXT,
            reset_token TEXT,
            reset_expires TEXT,
            email_notifications INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER DEFAULT 0,
            task_id TEXT UNIQUE,
            title TEXT,
            prompt TEXT,
            negative_prompt TEXT DEFAULT '',
            gen_type TEXT,
            style TEXT,
            category TEXT DEFAULT '',
            model_url TEXT,
            is_public INTEGER DEFAULT 1,
            likes INTEGER DEFAULT 0,
            downloads INTEGER DEFAULT 0,
            views INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS model_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id INTEGER,
            tag TEXT,
            UNIQUE(model_id, tag)
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
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY(user_id, model_id)
        );
        CREATE TABLE IF NOT EXISTS follows (
            follower_id INTEGER,
            following_id INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY(follower_id, following_id)
        );
        CREATE TABLE IF NOT EXISTS collections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            is_public INTEGER DEFAULT 1,
            model_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS collection_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection_id INTEGER NOT NULL,
            model_id INTEGER NOT NULL,
            added_at TEXT DEFAULT (datetime('now')),
            UNIQUE(collection_id, model_id)
        );
        CREATE TABLE IF NOT EXISTS blog_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_id INTEGER DEFAULT 0,
            title TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            excerpt TEXT DEFAULT '',
            content TEXT NOT NULL,
            cover_image TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            views INTEGER DEFAULT 0,
            is_published INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS security_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            ip TEXT,
            detail TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    # Add columns if missing (migration)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN username TEXT UNIQUE")
    except: pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN bio TEXT DEFAULT ''")
    except: pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN website TEXT DEFAULT ''")
    except: pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN email_notifications INTEGER DEFAULT 1")
    except: pass
    try:
        conn.execute("ALTER TABLE models ADD COLUMN negative_prompt TEXT DEFAULT ''")
    except: pass
    try:
        conn.execute("ALTER TABLE models ADD COLUMN category TEXT DEFAULT ''")
    except: pass
    try:
        conn.execute("ALTER TABLE models ADD COLUMN views INTEGER DEFAULT 0")
    except: pass

    # Insert sample blog posts
    try:
        cnt = conn.execute("SELECT COUNT(*) FROM blog_posts").fetchone()[0]
        if cnt == 0:
            conn.executescript("""
                INSERT INTO blog_posts (title, slug, excerpt, content, cover_image, tags) VALUES
                ('PrintForge ile Ilk 3D Modelinizi Uretin', 'ilk-3d-model', 'AI destekli 3D model uretiminin temelleri', 'PrintForge, yapay zeka teknolojisini kullanarak metin veya gorsellerden 3D model uretmenizi saglar. Bu rehberde adim adim ilk modelinizi nasil olusturacaginizi ogreneceksiniz.\n\n## Baslarken\n\nPrintForge''a kaydolduktan sonra ucretsiz planla ayda 5 model uretebilirsiniz. Hemen /app sayfasindan baslayabilirsiniz.\n\n## Metin ile Model Uretme\n\nSol panelde "Metin" sekmesini secin, modelinizi tanimlayan bir prompt yazin. Ornegin: "Detayli bir ortacag kalesi, kuleler ve asma kopru ile"\n\n## Gorsel ile Model Uretme\n\n"Gorsel" sekmesine gecin ve bir fotograf yukleyin. AI, gorseldeki nesneyi analiz ederek 3D modele donusturecektir.\n\n## Indirme\n\nModeliniz hazir oldugunda GLB, STL veya OBJ formatinda indirebilirsiniz.', '', 'rehber,baslangic,3d-model'),
                ('3D Baski Icin Model Hazirlama Rehberi', '3d-baski-rehberi', '3D yazicida baskilar icin model hazirlama ipuclari', 'PrintForge ile urettiginiz modelleri 3D yazicida basmak icin bazi onemli adimlar vardir.\n\n## STL Formatinda Indirin\n\nCogu 3D yazici dilimleyici (slicer) yazilimi STL formatini destekler. Modelinizi indirirken STL secenegini tercih edin.\n\n## Slicer Ayarlari\n\nCura, PrusaSlicer veya Bambu Studio gibi bir dilimleyici kullanin. Katman yuksekligi, dolgu orani ve destek yapilarini ayarlayin.\n\n## Malzeme Secimi\n\nPLA: Baslangic icin ideal, kolay basilir\nPETG: Daha dayanikli, sicakliga direncli\nABS: Profesyonel kullanim, post-processing uygun', '', '3d-baski,slicer,rehber'),
                ('Prompt Muhendisligi: Daha Iyi 3D Modeller', 'prompt-muhendisligi', 'Etkili prompt yazarak daha kaliteli modeller uretin', 'AI ile 3D model uretirken yazdiginiz prompt (aciklama) sonucun kalitesini dogrudan etkiler.\n\n## Iyi Prompt Nasil Yazilir?\n\n1. **Detayli olun**: "Araba" yerine "Kirmizi spor araba, karbon fiber detaylar, parlak boya"\n2. **Stil belirtin**: "Low-poly", "Gercekci", "Cartoon" gibi stiller ekleyin\n3. **Boyut referansi verin**: "20cm yuksekliginde masa ustu figur"\n4. **Malzeme belirtin**: "Metalik gorunumlu", "Ahsap dokulu"\n\n## Kotu Prompt Ornekleri\n- "bir sey yap" (cok belirsiz)\n- "gzl arba" (yazim hatalari)\n\n## Iyi Prompt Ornekleri\n- "Detayli ortacag savascisi, zirhli, kilicli, gercekci stil, 15cm figur"\n- "Minimalist geometric vazo, dalgali yuzey, beyaz seramik gorunum"', '', 'prompt,ipucu,kalite')
            """)
    except: pass

    conn.commit()
    conn.close()

init_db()

@app.on_event("startup")
async def startup():
    init_db()
    print(f"[DB] Yol: {DB_PATH}")
    print(f"[MAIL] Resend: {'ON' if RESEND_API_KEY else 'OFF'}")
    print(f"[GOOGLE] OAuth: {'ON' if GOOGLE_CLIENT_ID else 'OFF'}")


# ════════ GUVENLIK MIDDLEWARE ════════
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    ip = get_client_ip(request)
    if not check_rate_limit(ip, "general", 120, 60):
        return Response(content='{"detail":"Cok fazla istek"}', status_code=429, media_type="application/json")
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# ════════ AUTH HELPERS ════════
async def get_user(authorization: Optional[str] = Header(None)):
    if not authorization:
        return None
    data = decode_token(authorization.replace("Bearer ", ""))
    if not data:
        return None
    conn = get_db()
    row = conn.execute(
        "SELECT id,email,name,username,plan,bio,website,avatar_url,verified,email_notifications,created_at FROM users WHERE id=?",
        (data["user_id"],)
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def get_usage(uid):
    month = datetime.now().strftime("%Y-%m")
    conn = get_db()
    row = conn.execute("SELECT count FROM usage WHERE user_id=? AND month=?", (uid, month)).fetchone()
    conn.close()
    return row[0] if row else 0

def add_usage(uid):
    month = datetime.now().strftime("%Y-%m")
    conn = get_db()
    conn.execute(
        "INSERT INTO usage(user_id,month,count) VALUES(?,?,1) "
        "ON CONFLICT(user_id,month) DO UPDATE SET count=count+1", (uid, month))
    conn.commit()
    conn.close()

def save_model(uid, tid, title, prompt, gtype, style, url, neg_prompt="", tags="", category=""):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO models(user_id,task_id,title,prompt,negative_prompt,gen_type,style,category,model_url) VALUES(?,?,?,?,?,?,?,?,?)",
            (uid, tid, title, prompt, neg_prompt, gtype, style, category, url))
        if tags:
            mid = conn.execute("SELECT id FROM models WHERE task_id=?", (tid,)).fetchone()
            if mid:
                for tag in [t.strip().lower() for t in tags.split(",") if t.strip()]:
                    try:
                        conn.execute("INSERT INTO model_tags(model_id,tag) VALUES(?,?)", (mid[0], tag))
                    except: pass
    except: pass
    conn.commit()
    conn.close()

def get_user_stats(uid):
    conn = get_db()
    mc = conn.execute("SELECT COUNT(*) FROM models WHERE user_id=?", (uid,)).fetchone()[0]
    tl = conn.execute("SELECT COALESCE(SUM(likes),0) FROM models WHERE user_id=?", (uid,)).fetchone()[0]
    td = conn.execute("SELECT COALESCE(SUM(downloads),0) FROM models WHERE user_id=?", (uid,)).fetchone()[0]
    followers = conn.execute("SELECT COUNT(*) FROM follows WHERE following_id=?", (uid,)).fetchone()[0]
    following = conn.execute("SELECT COUNT(*) FROM follows WHERE follower_id=?", (uid,)).fetchone()[0]
    collections = conn.execute("SELECT COUNT(*) FROM collections WHERE user_id=?", (uid,)).fetchone()[0]
    conn.close()
    return {"models": mc, "likes": tl, "downloads": td, "followers": followers, "following": following, "collections": collections}

def log_security(uid, action, ip, detail=""):
    try:
        conn = get_db()
        conn.execute("INSERT INTO security_log(user_id,action,ip,detail) VALUES(?,?,?,?)", (uid, action, ip, detail))
        conn.commit()
        conn.close()
    except: pass


# ════════ SAYFALAR ════════
@app.get("/", response_class=HTMLResponse)
def serve_landing():
    for name in ["index.html", "printforge.html"]:
        path = os.path.join(os.path.dirname(__file__), name)
        if os.path.exists(path):
            return FileResponse(path, media_type="text/html")
    return HTMLResponse("<html><body style='background:#04080a;color:#00e5ff;display:flex;align-items:center;justify-content:center;height:100vh'><a href='/app' style='color:#00e5ff;font-size:24px'>PrintForge /app</a></body></html>")

@app.get("/app", response_class=HTMLResponse)
def serve_app():
    path = os.path.join(os.path.dirname(__file__), "app.html")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<html><body><h1>app.html bulunamadi</h1></body></html>")

@app.get("/blog", response_class=HTMLResponse)
@app.get("/blog/{slug}", response_class=HTMLResponse)
def serve_blog(slug: str = ""):
    path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(path):
        return FileResponse(path, media_type="text/html")
    return HTMLResponse("<html><body><h1>Blog</h1></body></html>")

@app.get("/u/{username}", response_class=HTMLResponse)
def serve_profile(username: str):
    path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(path):
        return FileResponse(path, media_type="text/html")
    return HTMLResponse("<html><body><h1>Profil</h1></body></html>")


# ════════ AUTH API ════════
@app.post("/api/auth/register")
async def register(req: RegisterReq, request: Request):
    ip = get_client_ip(request)
    if not check_rate_limit(ip, "register", 3, 3600):
        raise HTTPException(429, "Cok fazla kayit denemesi. 1 saat sonra tekrar deneyin.")
    valid, msg = validate_password(req.password)
    if not valid:
        raise HTTPException(400, msg)
    if not req.name.strip() or len(req.name.strip()) < 2:
        raise HTTPException(400, "Gecerli bir isim girin")
    valid, msg = await validate_email(req.email)
    if not valid:
        raise HTTPException(400, msg)

    salt, h = hash_pw(req.password)
    verify_token = secrets.token_urlsafe(32)
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users(email,name,password_hash,salt,verify_token,verified) VALUES(?,?,?,?,?,?)",
            (req.email.lower().strip(), sanitize(req.name.strip()), h, salt, verify_token,
             0 if RESEND_API_KEY else 1))
        conn.commit()
        uid = conn.execute("SELECT id FROM users WHERE email=?", (req.email.lower().strip(),)).fetchone()[0]
        username = generate_username(req.name, uid)
        conn.execute("UPDATE users SET username=? WHERE id=?", (username, uid))
        conn.commit()
        conn.close()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(400, "Bu e-posta zaten kayitli")

    log_security(uid, "register", ip)

    if RESEND_API_KEY:
        await send_verification_email(req.email.lower().strip(), verify_token)
        await send_welcome_email(req.email.lower().strip(), req.name.strip())

    token = create_token(uid, req.email, req.name, "free")
    result = {
        "token": token,
        "user": {"id": uid, "name": req.name, "email": req.email, "username": username, "plan": "free",
                 "verified": 0 if RESEND_API_KEY else 1}
    }
    if RESEND_API_KEY:
        result["message"] = "Dogrulama maili gonderildi. E-postanizi kontrol edin."
    return result

@app.post("/api/auth/login")
async def login(req: LoginReq, request: Request):
    ip = get_client_ip(request)
    if not check_login_attempt(ip):
        raise HTTPException(429, "Cok fazla basarisiz deneme. 15 dakika sonra tekrar deneyin.")
    if not check_rate_limit(ip, "login", 10, 60):
        raise HTTPException(429, "Cok fazla istek")

    conn = get_db()
    row = conn.execute(
        "SELECT id,email,name,username,password_hash,salt,plan,verified FROM users WHERE email=?",
        (req.email.lower().strip(),)).fetchone()
    conn.close()
    if not row:
        record_login_fail(ip)
        log_security(0, "login_fail", ip, f"email:{req.email}")
        raise HTTPException(401, "E-posta veya sifre hatali")
    if not verify_pw(req.password, row["salt"], row["password_hash"]):
        record_login_fail(ip)
        log_security(row["id"], "login_fail", ip)
        raise HTTPException(401, "E-posta veya sifre hatali")

    log_security(row["id"], "login_ok", ip)
    token = create_token(row["id"], row["email"], row["name"], row["plan"])
    return {
        "token": token,
        "user": {"id": row["id"], "name": row["name"], "email": row["email"],
                 "username": row["username"], "plan": row["plan"], "verified": row["verified"]}
    }

@app.get("/api/auth/me")
async def get_me(authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giris yapin")
    used = get_usage(user["id"])
    limit = PLAN_LIMITS.get(user["plan"], 5)
    stats = get_user_stats(user["id"])
    return {
        "user": user,
        "usage": {"used": used, "limit": limit, "remaining": max(0, limit - used)},
        "stats": stats
    }

@app.get("/api/auth/verify")
async def verify_email_endpoint(token: str = ""):
    if not token:
        raise HTTPException(400, "Gecersiz link")
    conn = get_db()
    row = conn.execute("SELECT id FROM users WHERE verify_token=?", (token,)).fetchone()
    if not row:
        conn.close()
        return HTMLResponse("<html><body style='background:#04080a;color:#ff4466;font-family:Arial;display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column'><h2>Gecersiz veya suresi dolmus link</h2><a href='/app' style='color:#00e5ff;margin-top:16px'>Uygulamaya Don</a></body></html>")
    conn.execute("UPDATE users SET verified=1, verify_token=NULL WHERE id=?", (row["id"],))
    conn.commit()
    conn.close()
    return HTMLResponse("<html><body style='background:#04080a;color:#00ff9d;font-family:Arial;display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column'><h2>Hesabiniz dogrulandi! ✓</h2><p style='color:#c8dde5;margin-top:8px'>Artik PrintForge'u kullanabilirsiniz.</p><a href='/app' style='color:#00e5ff;margin-top:16px;font-size:18px'>Uygulamaya Git →</a></body></html>")

@app.post("/api/auth/resend-verification")
async def resend_verification(authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giris yapin")
    if user.get("verified") == 1:
        return {"message": "Hesabiniz zaten dogrulanmis"}
    if not RESEND_API_KEY:
        raise HTTPException(400, "E-posta servisi yapilandirilmamis")
    new_token = secrets.token_urlsafe(32)
    conn = get_db()
    conn.execute("UPDATE users SET verify_token=? WHERE id=?", (new_token, user["id"]))
    conn.commit()
    conn.close()
    await send_verification_email(user["email"], new_token)
    return {"message": "Dogrulama maili tekrar gonderildi"}

@app.post("/api/auth/forgot-password")
async def forgot_password(req: ForgotPasswordReq, request: Request):
    ip = get_client_ip(request)
    if not check_rate_limit(ip, "forgot", 3, 3600):
        raise HTTPException(429, "Cok fazla istek")
    if not RESEND_API_KEY:
        raise HTTPException(400, "E-posta servisi yapilandirilmamis")
    conn = get_db()
    row = conn.execute("SELECT id,email FROM users WHERE email=?", (req.email.lower().strip(),)).fetchone()
    if not row:
        return {"message": "Eger bu e-posta kayitliysa sifirlama maili gonderildi"}
    reset_token = secrets.token_urlsafe(32)
    expires = (datetime.utcnow() + timedelta(hours=1)).isoformat()
    conn.execute("UPDATE users SET reset_token=?, reset_expires=? WHERE id=?", (reset_token, expires, row["id"]))
    conn.commit()
    conn.close()
    await send_reset_email(row["email"], reset_token)
    log_security(row["id"], "password_reset_request", ip)
    return {"message": "Sifre sifirlama maili gonderildi"}

@app.post("/api/auth/reset-password")
async def reset_password(req: ResetPasswordReq):
    valid, msg = validate_password(req.password)
    if not valid:
        raise HTTPException(400, msg)
    conn = get_db()
    row = conn.execute("SELECT id,reset_expires FROM users WHERE reset_token=?", (req.token,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(400, "Gecersiz veya suresi dolmus link")
    if row["reset_expires"]:
        try:
            if datetime.utcnow() > datetime.fromisoformat(row["reset_expires"]):
                conn.close()
                raise HTTPException(400, "Link suresi dolmus")
        except: pass
    salt, h = hash_pw(req.password)
    conn.execute("UPDATE users SET password_hash=?, salt=?, reset_token=NULL, reset_expires=NULL WHERE id=?", (h, salt, row["id"]))
    conn.commit()
    conn.close()
    return {"message": "Sifreniz basariyla degistirildi"}

@app.post("/api/auth/update-profile")
async def update_profile(req: UpdateProfileReq, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giris yapin")
    conn = get_db()
    if req.name and len(req.name.strip()) >= 2:
        conn.execute("UPDATE users SET name=? WHERE id=?", (sanitize(req.name.strip()), user["id"]))
    if req.password:
        valid, msg = validate_password(req.password)
        if not valid:
            conn.close()
            raise HTTPException(400, msg)
        salt, h = hash_pw(req.password)
        conn.execute("UPDATE users SET password_hash=?, salt=? WHERE id=?", (h, salt, user["id"]))
    if req.bio is not None:
        conn.execute("UPDATE users SET bio=? WHERE id=?", (sanitize(req.bio)[:300], user["id"]))
    if req.website is not None:
        conn.execute("UPDATE users SET website=? WHERE id=?", (sanitize(req.website)[:200], user["id"]))
    conn.commit()
    conn.close()
    return {"success": True}


# ════════ GOOGLE LOGIN ════════
@app.get("/api/auth/google")
async def google_login():
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(400, "Google login yapilandirilmamis")
    redirect_uri = f"{get_site_url()}/api/auth/google/callback"
    params = urlencode({
        "client_id": GOOGLE_CLIENT_ID, "redirect_uri": redirect_uri,
        "response_type": "code", "scope": "openid email profile",
        "access_type": "offline", "prompt": "select_account",
    })
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")

@app.get("/api/auth/google/callback")
async def google_callback(code: str = ""):
    if not code:
        raise HTTPException(400, "Google login basarisiz")
    redirect_uri = f"{get_site_url()}/api/auth/google/callback"
    async with httpx.AsyncClient() as client:
        tr = await client.post("https://oauth2.googleapis.com/token", data={
            "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
            "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri})
        if tr.status_code != 200:
            raise HTTPException(400, "Google token alinamadi")
        at = tr.json().get("access_token")
        ur = await client.get("https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {at}"})
        if ur.status_code != 200:
            raise HTTPException(400, "Google bilgi alinamadi")
        gu = ur.json()
    email = gu.get("email", "").lower()
    name = gu.get("name", email.split("@")[0])
    gid = gu.get("id", "")
    avatar = gu.get("picture", "")
    conn = get_db()
    ex = conn.execute("SELECT id,name,plan FROM users WHERE email=?", (email,)).fetchone()
    if ex:
        uid, name, plan = ex["id"], ex["name"], ex["plan"]
        conn.execute("UPDATE users SET google_id=?,avatar_url=?,verified=1 WHERE id=?", (gid, avatar, uid))
    else:
        salt, h = hash_pw(secrets.token_hex(16))
        conn.execute("INSERT INTO users(email,name,password_hash,salt,google_id,avatar_url,verified) VALUES(?,?,?,?,?,?,1)",
            (email, name, h, salt, gid, avatar))
        uid = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()[0]
        username = generate_username(name, uid)
        conn.execute("UPDATE users SET username=? WHERE id=?", (username, uid))
        plan = "free"
    conn.commit()
    conn.close()
    jwt_token = create_token(uid, email, name, plan)
    return HTMLResponse(f"<html><head><script>localStorage.setItem('pf_token','{jwt_token}');window.location.href='/app';</script></head><body style='background:#04080a;color:#00e5ff;display:flex;align-items:center;justify-content:center;height:100vh'>Giris yapiliyor...</body></html>")


# ════════ PUBLIC PROFILE ════════
@app.get("/api/users/{username}")
async def get_public_profile(username: str):
    conn = get_db()
    user = conn.execute(
        "SELECT id,name,username,bio,website,avatar_url,plan,created_at FROM users WHERE username=?",
        (username,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(404, "Kullanici bulunamadi")
    uid = user["id"]
    models = conn.execute(
        "SELECT id,task_id,title,prompt,style,category,likes,downloads,views,created_at FROM models WHERE user_id=? AND is_public=1 ORDER BY created_at DESC LIMIT 50",
        (uid,)).fetchall()
    followers = conn.execute("SELECT COUNT(*) FROM follows WHERE following_id=?", (uid,)).fetchone()[0]
    following = conn.execute("SELECT COUNT(*) FROM follows WHERE follower_id=?", (uid,)).fetchone()[0]
    model_count = conn.execute("SELECT COUNT(*) FROM models WHERE user_id=? AND is_public=1", (uid,)).fetchone()[0]
    total_likes = conn.execute("SELECT COALESCE(SUM(likes),0) FROM models WHERE user_id=?", (uid,)).fetchone()[0]
    collections = conn.execute("SELECT id,name,description,model_count,created_at FROM collections WHERE user_id=? AND is_public=1 ORDER BY created_at DESC", (uid,)).fetchall()
    conn.close()
    return {
        "user": dict(user),
        "models": [dict(m) for m in models],
        "collections": [dict(c) for c in collections],
        "stats": {"models": model_count, "followers": followers, "following": following, "total_likes": total_likes}
    }


# ════════ FOLLOW SYSTEM ════════
@app.post("/api/users/{username}/follow")
async def toggle_follow(username: str, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giris yapin")
    conn = get_db()
    target = conn.execute("SELECT id,email,name,email_notifications FROM users WHERE username=?", (username,)).fetchone()
    if not target:
        conn.close()
        raise HTTPException(404, "Kullanici bulunamadi")
    if target["id"] == user["id"]:
        conn.close()
        raise HTTPException(400, "Kendinizi takip edemezsiniz")
    ex = conn.execute("SELECT 1 FROM follows WHERE follower_id=? AND following_id=?", (user["id"], target["id"])).fetchone()
    if ex:
        conn.execute("DELETE FROM follows WHERE follower_id=? AND following_id=?", (user["id"], target["id"]))
        followed = False
    else:
        conn.execute("INSERT INTO follows(follower_id,following_id) VALUES(?,?)", (user["id"], target["id"]))
        followed = True
        if target["email_notifications"] and RESEND_API_KEY:
            asyncio.create_task(send_follow_email(user["name"], target["email"], target["name"]))
    conn.commit()
    conn.close()
    return {"followed": followed}

@app.get("/api/users/{username}/followers")
async def get_followers(username: str):
    conn = get_db()
    target = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if not target:
        conn.close()
        raise HTTPException(404, "Kullanici bulunamadi")
    rows = conn.execute(
        "SELECT u.name,u.username,u.avatar_url FROM follows f JOIN users u ON f.follower_id=u.id WHERE f.following_id=? ORDER BY f.created_at DESC LIMIT 100",
        (target["id"],)).fetchall()
    conn.close()
    return {"followers": [dict(r) for r in rows]}

@app.get("/api/users/{username}/following")
async def get_following(username: str):
    conn = get_db()
    target = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if not target:
        conn.close()
        raise HTTPException(404, "Kullanici bulunamadi")
    rows = conn.execute(
        "SELECT u.name,u.username,u.avatar_url FROM follows f JOIN users u ON f.following_id=u.id WHERE f.follower_id=? ORDER BY f.created_at DESC LIMIT 100",
        (target["id"],)).fetchall()
    conn.close()
    return {"following": [dict(r) for r in rows]}


# ════════ COLLECTIONS ════════
@app.get("/api/collections")
async def get_my_collections(authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giris yapin")
    conn = get_db()
    rows = conn.execute("SELECT * FROM collections WHERE user_id=? ORDER BY created_at DESC", (user["id"],)).fetchall()
    conn.close()
    return {"collections": [dict(r) for r in rows]}

@app.post("/api/collections")
async def create_collection(req: CollectionReq, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giris yapin")
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM collections WHERE user_id=?", (user["id"],)).fetchone()[0]
    if count >= 50:
        conn.close()
        raise HTTPException(400, "Maksimum 50 koleksiyon olusturabilirsiniz")
    conn.execute("INSERT INTO collections(user_id,name,description,is_public) VALUES(?,?,?,?)",
        (user["id"], sanitize(req.name)[:100], sanitize(req.description)[:500], req.is_public))
    conn.commit()
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return {"id": cid, "message": "Koleksiyon olusturuldu"}

@app.get("/api/collections/{collection_id}")
async def get_collection(collection_id: int):
    conn = get_db()
    col = conn.execute("SELECT c.*, u.name as owner_name, u.username as owner_username FROM collections c JOIN users u ON c.user_id=u.id WHERE c.id=?", (collection_id,)).fetchone()
    if not col:
        conn.close()
        raise HTTPException(404, "Koleksiyon bulunamadi")
    items = conn.execute(
        "SELECT m.* FROM collection_items ci JOIN models m ON ci.model_id=m.id WHERE ci.collection_id=? ORDER BY ci.added_at DESC",
        (collection_id,)).fetchall()
    conn.close()
    return {"collection": dict(col), "models": [dict(i) for i in items]}

@app.post("/api/collections/{collection_id}/items")
async def add_to_collection(collection_id: int, req: CollectionItemReq, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giris yapin")
    conn = get_db()
    col = conn.execute("SELECT user_id FROM collections WHERE id=?", (collection_id,)).fetchone()
    if not col or col["user_id"] != user["id"]:
        conn.close()
        raise HTTPException(403, "Bu koleksiyon size ait degil")
    try:
        conn.execute("INSERT INTO collection_items(collection_id,model_id) VALUES(?,?)", (collection_id, req.model_id))
        conn.execute("UPDATE collections SET model_count=model_count+1 WHERE id=?", (collection_id,))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(400, "Model zaten koleksiyonda")
    conn.close()
    return {"message": "Koleksiyona eklendi"}

@app.delete("/api/collections/{collection_id}/items/{model_id}")
async def remove_from_collection(collection_id: int, model_id: int, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giris yapin")
    conn = get_db()
    col = conn.execute("SELECT user_id FROM collections WHERE id=?", (collection_id,)).fetchone()
    if not col or col["user_id"] != user["id"]:
        conn.close()
        raise HTTPException(403, "Bu koleksiyon size ait degil")
    conn.execute("DELETE FROM collection_items WHERE collection_id=? AND model_id=?", (collection_id, model_id))
    conn.execute("UPDATE collections SET model_count=MAX(0,model_count-1) WHERE id=?", (collection_id,))
    conn.commit()
    conn.close()
    return {"message": "Koleksiyondan cikarildi"}

@app.delete("/api/collections/{collection_id}")
async def delete_collection(collection_id: int, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giris yapin")
    conn = get_db()
    col = conn.execute("SELECT user_id FROM collections WHERE id=?", (collection_id,)).fetchone()
    if not col or col["user_id"] != user["id"]:
        conn.close()
        raise HTTPException(403, "Bu koleksiyon size ait degil")
    conn.execute("DELETE FROM collection_items WHERE collection_id=?", (collection_id,))
    conn.execute("DELETE FROM collections WHERE id=?", (collection_id,))
    conn.commit()
    conn.close()
    return {"deleted": True}


# ════════ TAGS / CATEGORIES ════════
@app.get("/api/tags")
async def get_tags():
    conn = get_db()
    rows = conn.execute("SELECT tag, COUNT(*) as count FROM model_tags GROUP BY tag ORDER BY count DESC LIMIT 50").fetchall()
    conn.close()
    return {"tags": [{"tag": r["tag"], "count": r["count"]} for r in rows]}

@app.get("/api/tags/{tag}")
async def get_models_by_tag(tag: str, page: int = 1, limit: int = 20):
    conn = get_db()
    offset = (page - 1) * limit
    rows = conn.execute(
        "SELECT m.*, u.name as author_name, u.username as author_username FROM model_tags mt JOIN models m ON mt.model_id=m.id LEFT JOIN users u ON m.user_id=u.id WHERE mt.tag=? AND m.is_public=1 ORDER BY m.created_at DESC LIMIT ? OFFSET ?",
        (tag.lower(), limit, offset)).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM model_tags mt JOIN models m ON mt.model_id=m.id WHERE mt.tag=? AND m.is_public=1", (tag.lower(),)).fetchone()[0]
    conn.close()
    return {"models": [dict(r) for r in rows], "total": total, "tag": tag}

@app.post("/api/models/{model_id}/tags")
async def update_model_tags(model_id: int, req: TagModelReq, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giris yapin")
    conn = get_db()
    model = conn.execute("SELECT user_id FROM models WHERE id=?", (model_id,)).fetchone()
    if not model or model["user_id"] != user["id"]:
        conn.close()
        raise HTTPException(403, "Bu model size ait degil")
    conn.execute("DELETE FROM model_tags WHERE model_id=?", (model_id,))
    tags = [t.strip().lower() for t in req.tags.split(",") if t.strip()][:10]
    for tag in tags:
        try:
            conn.execute("INSERT INTO model_tags(model_id,tag) VALUES(?,?)", (model_id, sanitize(tag)[:30]))
        except: pass
    conn.commit()
    conn.close()
    return {"tags": tags}

@app.get("/api/categories")
async def get_categories():
    return {"categories": CATEGORIES}


# ════════ BLOG API ════════
@app.get("/api/blog")
async def get_blog_posts(page: int = 1, limit: int = 10, tag: str = ""):
    conn = get_db()
    offset = (page - 1) * limit
    where = "WHERE is_published=1"
    params = []
    if tag:
        where += " AND tags LIKE ?"
        params.append(f"%{tag}%")
    rows = conn.execute(
        f"SELECT id,title,slug,excerpt,cover_image,tags,views,created_at FROM blog_posts {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset]).fetchall()
    total = conn.execute(f"SELECT COUNT(*) FROM blog_posts {where}", params).fetchone()[0]
    conn.close()
    return {"posts": [dict(r) for r in rows], "total": total, "page": page, "pages": max(1, (total + limit - 1) // limit)}

@app.get("/api/blog/{slug}")
async def get_blog_post(slug: str):
    conn = get_db()
    row = conn.execute("SELECT bp.*, u.name as author_name FROM blog_posts bp LEFT JOIN users u ON bp.author_id=u.id WHERE bp.slug=? AND bp.is_published=1", (slug,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Yazi bulunamadi")
    conn.execute("UPDATE blog_posts SET views=views+1 WHERE slug=?", (slug,))
    conn.commit()
    related = conn.execute("SELECT id,title,slug,excerpt,cover_image FROM blog_posts WHERE slug!=? AND is_published=1 ORDER BY created_at DESC LIMIT 3", (slug,)).fetchall()
    conn.close()
    return {"post": dict(row), "related": [dict(r) for r in related]}


# ════════ MODEL URETIMI ════════
@app.post("/api/generate/text")
async def generate_text(req: TextRequest, authorization: Optional[str] = Header(None), request: Request = None):
    api = get_api()
    user = await get_user(authorization)
    if api != "demo":
        if not user:
            raise HTTPException(401, "Model uretmek icin giris yapin")
        used = get_usage(user["id"])
        limit = PLAN_LIMITS.get(user["plan"], 5)
        if used >= limit:
            raise HTTPException(403, f"Aylik limitinize ulastiniz ({limit}). Planizi yukseltin.")
        add_usage(user["id"])
    tid = str(uuid.uuid4())[:8]
    tasks[tid] = {
        "status": "processing", "progress": 0, "step": "Baslatiliyor...",
        "type": "text", "api": api, "prompt": req.prompt, "style": req.style,
        "negative_prompt": req.negative_prompt, "tags": req.tags,
        "user_id": user["id"] if user else 0
    }
    if api == "tripo":
        asyncio.create_task(_tripo_text(tid, req.prompt, req.style))
    elif api == "meshy":
        asyncio.create_task(_meshy_text(tid, req.prompt, req.style))
    else:
        asyncio.create_task(_demo_generate(tid))
    return {"task_id": tid}

@app.post("/api/generate/image")
async def generate_image(file: UploadFile = File(...), authorization: Optional[str] = Header(None)):
    api = get_api()
    user = await get_user(authorization)
    if api != "demo":
        if not user:
            raise HTTPException(401, "Model uretmek icin giris yapin")
        used = get_usage(user["id"])
        limit = PLAN_LIMITS.get(user["plan"], 5)
        if used >= limit:
            raise HTTPException(403, f"Aylik limitinize ulastiniz ({limit}). Planizi yukseltin.")
        add_usage(user["id"])
    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(400, "Dosya cok buyuk (max 10MB)")
    # Magic bytes check
    if contents[:3] not in (b'\xff\xd8\xff', b'\x89PN', b'RIF'):
        raise HTTPException(400, "Gecersiz dosya formati")
    tid = str(uuid.uuid4())[:8]
    fname = file.filename or "image.jpg"
    tasks[tid] = {
        "status": "processing", "progress": 0, "step": "Gorsel hazirlaniyor...",
        "type": "image", "api": api, "prompt": fname, "style": "",
        "user_id": user["id"] if user else 0
    }
    if api == "tripo":
        asyncio.create_task(_tripo_image(tid, contents, fname))
    elif api == "meshy":
        asyncio.create_task(_meshy_image(tid, contents, fname))
    else:
        asyncio.create_task(_demo_generate(tid))
    return {"task_id": tid}

@app.get("/api/status/{task_id}")
async def get_status(task_id: str):
    if task_id not in tasks:
        raise HTTPException(404, "Gorev bulunamadi")
    t = tasks[task_id]
    return {
        "task_id": task_id, "status": t["status"], "progress": t["progress"],
        "step": t.get("step", ""), "model_url": t.get("model_url", ""),
        "is_demo": t.get("api") == "demo", "error": t.get("error", ""),
    }


# ════════ MODEL SUNMA ════════
@app.get("/api/model/{task_id}/view")
async def model_view(task_id: str):
    fp = model_file_path(task_id)

    # 1) Diskte varsa direkt ver
    if os.path.exists(fp):
        return FileResponse(
            fp,
            media_type="model/gltf-binary",
            headers={"Cache-Control": "public, max-age=3600"}
        )

    # 2) RAM cache'te varsa ver
    if task_id in model_cache and len(model_cache[task_id]) >= 4 and model_cache[task_id][:4] == b"glTF":
        return Response(
            content=model_cache[task_id],
            media_type="model/gltf-binary",
            headers={"Cache-Control": "public, max-age=3600"}
        )

    # 3) URL'den indirip diske yaz, sonra ver
    url = get_model_url(task_id)
    if not url:
        raise HTTPException(404, "Model bulunamadi")

    try:
        await persist_model_glb(task_id, url)
    except Exception as e:
        raise HTTPException(502, f"Model indirilemedi: {e}")

    if os.path.exists(fp):
        return FileResponse(
            fp,
            media_type="model/gltf-binary",
            headers={"Cache-Control": "public, max-age=3600"}
        )

    raise HTTPException(500, "Model kaydedilemedi")
# ════════ GALERI ════════
@app.get("/api/gallery")
async def gallery(page: int = 1, limit: int = 20, sort: str = "newest", search: str = "", category: str = "", tag: str = ""):
    conn = get_db()
    offset = (page - 1) * limit
    where = "WHERE m.is_public=1 AND m.model_url != ''"
    params = []
    if search:
        where += " AND (m.title LIKE ? OR m.prompt LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    if category:
        where += " AND m.category=?"
        params.append(category)
    if tag:
        where += " AND m.id IN (SELECT model_id FROM model_tags WHERE tag=?)"
        params.append(tag.lower())
    order = {"popular": "ORDER BY m.likes DESC", "downloads": "ORDER BY m.downloads DESC", "views": "ORDER BY m.views DESC"}.get(sort, "ORDER BY m.created_at DESC")
    rows = conn.execute(
        f"SELECT m.*, u.name as author_name, u.username as author_username FROM models m LEFT JOIN users u ON m.user_id=u.id {where} {order} LIMIT ? OFFSET ?",
        params + [limit, offset]).fetchall()
    total = conn.execute(f"SELECT COUNT(*) FROM models m {where}", params).fetchone()[0]
    conn.close()
    return {"models": [dict(r) for r in rows], "total": total, "page": page, "pages": max(1, (total + limit - 1) // limit)}

@app.get("/api/gallery/{model_id}")
async def model_detail(model_id: int):
    conn = get_db()
    row = conn.execute("SELECT m.*, u.name as author_name, u.username as author_username FROM models m LEFT JOIN users u ON m.user_id=u.id WHERE m.id=?", (model_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Model bulunamadi")
    conn.execute("UPDATE models SET views=views+1 WHERE id=?", (model_id,))
    conn.commit()
    tags = conn.execute("SELECT tag FROM model_tags WHERE model_id=?", (model_id,)).fetchall()
    conn.close()
    result = dict(row)
    result["tags"] = [t["tag"] for t in tags]
    return result

@app.post("/api/gallery/{model_id}/like")
async def toggle_like(model_id: int, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giris yapin")
    conn = get_db()
    ex = conn.execute("SELECT 1 FROM user_likes WHERE user_id=? AND model_id=?", (user["id"], model_id)).fetchone()
    if ex:
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

@app.get("/api/my-models")
async def my_models(authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giris yapin")
    conn = get_db()
    rows = conn.execute("SELECT * FROM models WHERE user_id=? ORDER BY created_at DESC", (user["id"],)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        tags = conn.execute("SELECT tag FROM model_tags WHERE model_id=?", (r["id"],)).fetchall()
        d["tags"] = [t["tag"] for t in tags]
        result.append(d)
    conn.close()
    return {"models": result}

@app.delete("/api/my-models/{model_id}")
async def delete_model(model_id: int, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giris yapin")
    conn = get_db()
    conn.execute("DELETE FROM model_tags WHERE model_id=?", (model_id,))
    conn.execute("DELETE FROM collection_items WHERE model_id=?", (model_id,))
    conn.execute("DELETE FROM user_likes WHERE model_id=?", (model_id,))
    conn.execute("DELETE FROM models WHERE id=? AND user_id=?", (model_id, user["id"]))
    conn.commit()
    conn.close()
    return {"deleted": True}

@app.post("/api/payment/upgrade")
async def upgrade_plan(authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giris yapin")
    conn = get_db()
    conn.execute("UPDATE users SET plan='pro' WHERE id=?", (user["id"],))
    conn.commit()
    conn.close()
    return {"success": True, "plan": "pro"}


# ════════ PRIVACY ════════
@app.post("/api/privacy/export-data")
async def export_data(authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giris yapin")
    conn = get_db()
    models = [dict(r) for r in conn.execute("SELECT * FROM models WHERE user_id=?", (user["id"],)).fetchall()]
    collections = [dict(r) for r in conn.execute("SELECT * FROM collections WHERE user_id=?", (user["id"],)).fetchall()]
    likes = [dict(r) for r in conn.execute("SELECT * FROM user_likes WHERE user_id=?", (user["id"],)).fetchall()]
    conn.close()
    return {"user": user, "models": models, "collections": collections, "likes": likes, "exported_at": datetime.utcnow().isoformat()}

@app.post("/api/privacy/delete-account")
async def delete_account(authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giris yapin")
    conn = get_db()
    uid = user["id"]
    conn.execute("DELETE FROM model_tags WHERE model_id IN (SELECT id FROM models WHERE user_id=?)", (uid,))
    conn.execute("DELETE FROM collection_items WHERE collection_id IN (SELECT id FROM collections WHERE user_id=?)", (uid,))
    conn.execute("DELETE FROM collections WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM follows WHERE follower_id=? OR following_id=?", (uid, uid))
    conn.execute("DELETE FROM user_likes WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM usage WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM security_log WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM models WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit()
    conn.close()
    return {"deleted": True, "message": "Hesabiniz ve tum verileriniz kalici olarak silindi"}

@app.get("/api/health")
async def health():
    api = get_api()
    return {
        "status": "online", "active_api": api, "api_ready": True,
        "is_demo": api == "demo", "stl_ready": HAS_TRIMESH,
        "auth_ready": HAS_JWT, "google_ready": bool(GOOGLE_CLIENT_ID),
        "email_ready": bool(RESEND_API_KEY), "cached_models": len(model_cache),
    }


# ════════ URL CIKARMA ════════
def extract_model_url(data):
    if not data: return ""
    if isinstance(data, str) and data.startswith("http"): return data
    if not isinstance(data, dict): return ""
    for key in ["model", "pbr_model", "base_model"]:
        val = data.get(key, "")
        if isinstance(val, str) and val.startswith("http"): return val
        if isinstance(val, dict):
            url = val.get("url", "") or val.get("download_url", "")
            if url and url.startswith("http"): return url
    for k, v in data.items():
        if isinstance(v, str) and v.startswith("http"):
            if any(x in v.lower() for x in [".glb", ".gltf", "model"]): return v
    return ""


# ════════ TRIPO3D ════════
async def _tripo_text(tid, prompt, style):
    try:
        h = {"Authorization": f"Bearer {TRIPO_API_KEY}"}
        tasks[tid]["progress"] = 10
        tasks[tid]["step"] = "Prompt gonderiliyor..."
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.post(f"{TRIPO_BASE}/task",
                json={"type": "text_to_model", "prompt": f"{prompt}, {style} style"},
                headers={**h, "Content-Type": "application/json"})
            if r.status_code != 200: raise Exception(f"Tripo hata {r.status_code}")
            tripo_id = r.json().get("data", {}).get("task_id")
            if not tripo_id: raise Exception("Task ID alinamadi")
            tasks[tid]["progress"] = 25
            await _tripo_poll(c, h, tid, tripo_id)
    except Exception as e:
        tasks[tid]["status"] = "failed"
        tasks[tid]["error"] = str(e)

async def _tripo_image(tid, contents, fname):
    try:
        h = {"Authorization": f"Bearer {TRIPO_API_KEY}"}
        ext = fname.rsplit(".", 1)[-1].lower()
        if ext not in ("jpg", "jpeg", "png", "webp"): ext = "jpeg"
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        tasks[tid]["progress"] = 10
        tasks[tid]["step"] = "Gorsel yukleniyor..."
        async with httpx.AsyncClient(timeout=600) as c:
            ur = await c.post(f"{TRIPO_BASE}/upload", files={"file": (fname, contents, mime)}, headers=h)
            if ur.status_code != 200: raise Exception(f"Upload hata {ur.status_code}")
            token = ur.json().get("data", {}).get("image_token")
            if not token: raise Exception("Token alinamadi")
            tasks[tid]["progress"] = 25
            tasks[tid]["step"] = "Model olusturuluyor..."
            tr = await c.post(f"{TRIPO_BASE}/task",
                json={"type": "image_to_model", "file": {"type": ext if ext != "jpg" else "jpeg", "file_token": token}},
                headers={**h, "Content-Type": "application/json"})
            if tr.status_code != 200: raise Exception(f"Task hata {tr.status_code}")
            tripo_id = tr.json().get("data", {}).get("task_id")
            if not tripo_id: raise Exception("Task ID alinamadi")
            tasks[tid]["progress"] = 35
            await _tripo_poll(c, h, tid, tripo_id)
    except Exception as e:
        tasks[tid]["status"] = "failed"
        tasks[tid]["error"] = str(e)

async def _tripo_poll(client, headers, tid, tripo_id):
    for _ in range(200):
        await asyncio.sleep(3)
        try:
            r = await client.get(f"{TRIPO_BASE}/task/{tripo_id}", headers=headers)
            d = r.json().get("data", {})
            st = d.get("status", "")
            pr = d.get("progress", 0)

            tasks[tid]["progress"] = 35 + int(pr * 0.55)
            tasks[tid]["step"] = f"Model uretiliyor... %{pr}"

            if st == "success":
                url = extract_model_url(d.get("output", {}))
                tasks[tid]["model_url"] = url
                tasks[tid]["progress"] = 92
                tasks[tid]["step"] = "Model indiriliyor..."

                if url:
                    await persist_model_glb(tid, url)

                tasks[tid]["status"] = "done"
                tasks[tid]["progress"] = 100
                tasks[tid]["step"] = "Tamamlandi!"

                uid = tasks[tid].get("user_id", 0)
                prompt = tasks[tid].get("prompt", "")
                save_model(
                    uid, tid, prompt[:50], prompt,
                    tasks[tid].get("type", ""),
                    tasks[tid].get("style", ""),
                    url,
                    tasks[tid].get("negative_prompt", ""),
                    tasks[tid].get("tags", "")
                )
                return

            if st in ("failed", "cancelled"):
                raise Exception(f"Tripo: {st}")

        except Exception as e:
            if any(x in str(e) for x in ["Tripo", "failed", "cancelled"]):
                tasks[tid]["status"] = "failed"
                tasks[tid]["error"] = str(e)
                return

    tasks[tid]["status"] = "failed"
    tasks[tid]["error"] = "Zaman asimi"


# ════════ MESHY ════════
async def _meshy_text(tid, prompt, style):
    try:
        h = {"Authorization": f"Bearer {MESHY_API_KEY}", "Content-Type": "application/json"}
        tasks[tid]["progress"] = 10
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.post(f"{MESHY_BASE}/text-to-3d",
                json={"mode": "preview", "prompt": prompt, "art_style": "realistic"}, headers=h)
            if r.status_code not in (200, 202): raise Exception(f"Meshy hata {r.status_code}")
            mid = r.json().get("result")
            tasks[tid]["progress"] = 20
            await _meshy_poll(c, h, tid, mid, "text-to-3d")
    except Exception as e:
        tasks[tid]["status"] = "failed"
        tasks[tid]["error"] = str(e)

async def _meshy_image(tid, contents, fname):
    try:
        h = {"Authorization": f"Bearer {MESHY_API_KEY}", "Content-Type": "application/json"}
        ext = fname.rsplit(".", 1)[-1].lower()
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        b64 = base64.b64encode(contents).decode()
        tasks[tid]["progress"] = 15
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.post(f"{MESHY_BASE}/image-to-3d",
                json={"image_url": f"data:{mime};base64,{b64}", "enable_pbr": True}, headers=h)
            if r.status_code not in (200, 202): raise Exception(f"Meshy hata {r.status_code}")
            mid = r.json().get("result")
            tasks[tid]["progress"] = 25
            await _meshy_poll(c, h, tid, mid, "image-to-3d")
    except Exception as e:
        tasks[tid]["status"] = "failed"
        tasks[tid]["error"] = str(e)

async def _meshy_poll(client, h, tid, mid, ep):
    for _ in range(200):
        await asyncio.sleep(3)
        try:
            r = await client.get(f"{MESHY_BASE}/{ep}/{mid}", headers=h)
            if r.status_code != 200:
                continue

            d = r.json()
            status = d.get("status", "")
            progress = d.get("progress", 0)

            tasks[tid]["progress"] = 25 + int(progress * 0.7)
            tasks[tid]["step"] = f"Model uretiliyor... %{progress}"

            if status == "SUCCEEDED":
                glb = d.get("model_urls", {}).get("glb", "")
                tasks[tid]["model_url"] = glb

                if glb:
                    await persist_model_glb(tid, glb)

                tasks[tid]["status"] = "done"
                tasks[tid]["progress"] = 100
                tasks[tid]["step"] = "Tamamlandi!"

                uid = tasks[tid].get("user_id", 0)
                prompt = tasks[tid].get("prompt", "")
                save_model(
                    uid, tid, prompt[:50], prompt,
                    tasks[tid].get("type", ""),
                    "",
                    glb,
                    tasks[tid].get("negative_prompt", ""),
                    tasks[tid].get("tags", "")
                )
                return

            if status == "FAILED":
                raise Exception("Meshy: Model uretilemedi")

        except Exception as e:
            if "uretilemedi" in str(e):
                tasks[tid]["status"] = "failed"
                tasks[tid]["error"] = str(e)
                return

    tasks[tid]["status"] = "failed"
    tasks[tid]["error"] = "Zaman asimi"


# ════════ DEMO ════════
async def _demo_generate(tid):
    try:
        steps = [(8,"Analiz ediliyor..."),(22,"AI yukleniyor..."),(40,"Geometri olusturuluyor..."),
                 (58,"Mesh uretiliyor..."),(72,"Texture uygulanıyor..."),(88,"Optimize ediliyor..."),(95,"Hazirlaniyor...")]
        for pr, st in steps:
            tasks[tid]["progress"] = pr
            tasks[tid]["step"] = st
            await asyncio.sleep(random.uniform(1.0, 2.0))
        m = random.choice(DEMO_MODELS)
        tasks[tid]["model_url"] = m["glb"]
        await cache_model(tid, m["glb"])
        tasks[tid]["status"] = "done"
        tasks[tid]["progress"] = 100
        tasks[tid]["step"] = f"Demo: {m['name']}"
        save_model(0, tid, m["name"], "demo", "demo", "", m["glb"])
    except Exception as e:
        tasks[tid]["status"] = "failed"
        tasks[tid]["error"] = str(e)
