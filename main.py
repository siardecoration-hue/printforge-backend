from fastapi import FastAPI, HTTPException, UploadFile, File, Header
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
    "mailnull.com","mailmoat.com","mailshell.com","tempmailaddress.com",
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

BLOG_POSTS = [
    {
        "id": 1, "slug": "3d-baski-nedir",
        "title": "3D Baski Nedir? Baslangic Rehberi",
        "summary": "3D baski teknolojisinin temelleri, nasil calisir ve neler yapabilirsiniz.",
        "category": "rehber", "date": "2025-01-15", "read_time": "5 dk",
        "content": "<h2>3D Baski Nedir?</h2><p>3D baski, dijital bir 3D modelden fiziksel bir nesne olusturma teknolojisidir. Katman katman malzeme eklenerek nesneler uretilir.</p><h2>Nasil Calisir?</h2><p>1. <strong>3D Model Olusturma:</strong> Bilgisayarda veya AI ile 3D model tasarlanir.</p><p>2. <strong>Dilimleme (Slicing):</strong> Model, yazici icin katmanlara ayrilir.</p><p>3. <strong>Baski:</strong> Yazici malzemeyi katman katman ekleyerek nesneyi olusturur.</p><h2>Hangi Malzemeler Kullanilir?</h2><p><strong>PLA:</strong> En yaygin, kolay kullanim, biyolojik olarak parcalanabilir.</p><p><strong>ABS:</strong> Dayanikli, isiya direncli, endustriyel kullanim.</p><p><strong>PETG:</strong> PLA ve ABS arasi, suya direncli.</p><h2>PrintForge ile 3D Model Uretme</h2><p>PrintForge sayesinde kendi 3D modellerinizi AI ile saniyeler icinde uretebilirsiniz!</p>"
    },
    {
        "id": 2, "slug": "stl-dosyasi-nedir",
        "title": "STL Dosyasi Nedir? Format Rehberi",
        "summary": "STL, OBJ ve GLB dosya formatlari arasindaki farklar ve hangisini ne zaman kullanmalisiniz.",
        "category": "rehber", "date": "2025-01-20", "read_time": "4 dk",
        "content": "<h2>3D Model Formatlari</h2><h3>STL (Stereolithography)</h3><p>3D baski icin en yaygin format. Sadece geometri bilgisi icerir. Tum slicer yazilimlariyla uyumludur.</p><h3>OBJ (Wavefront)</h3><p>Daha detayli format. Geometri + texture + malzeme bilgisi icerir.</p><h3>GLB/GLTF</h3><p>Web icin optimize format. Animasyon ve PBR malzeme destegi vardir.</p><h2>Hangisini Kullanmaliyim?</h2><p><strong>3D Baski icin:</strong> STL</p><p><strong>3D Modelleme icin:</strong> OBJ</p><p><strong>Web/Oyun icin:</strong> GLB</p>"
    },
    {
        "id": 3, "slug": "en-iyi-3d-yazicilar",
        "title": "2025 En Iyi 3D Yazicilar",
        "summary": "Baslangic seviyesinden profesyonele, butceye uygun en iyi 3D yazici onerileri.",
        "category": "liste", "date": "2025-02-01", "read_time": "6 dk",
        "content": "<h2>Baslangic Seviyesi</h2><p><strong>Creality Ender 3 V3:</strong> En populer baslangic yazicisi. Uygun fiyat, buyuk topluluk destegi.</p><p><strong>Anycubic Kobra 2:</strong> Hizli baski, otomatik yatak seviyeleme.</p><h2>Orta Seviye</h2><p><strong>Bambu Lab P1S:</strong> Yuksek hiz, coklu malzeme destegi.</p><p><strong>Prusa MK4:</strong> Guvenilir, acik kaynak, mukemmel baski kalitesi.</p><h2>Profesyonel</h2><p><strong>Bambu Lab X1 Carbon:</strong> En hizli FDM yazici, LIDAR tarama.</p><p><strong>Formlabs Form 4:</strong> Resin yazici, ultra yuksek detay.</p>"
    },
    {
        "id": 4, "slug": "ai-ile-3d-model-uretme",
        "title": "AI ile 3D Model Uretme Rehberi",
        "summary": "Yapay zeka kullanarak profesyonel 3D modeller nasil uretilir, ipuclari.",
        "category": "rehber", "date": "2025-02-10", "read_time": "5 dk",
        "content": "<h2>AI ile 3D Model Nedir?</h2><p>Yapay zeka, metin veya gorsellerden otomatik olarak 3D modeller uretebilir.</p><h2>Iyi Bir Prompt Nasil Yazilir?</h2><p><strong>Detayli olun:</strong> 'araba' yerine 'kirmizi spor araba, parlak boya, spoiler'</p><p><strong>Stil belirtin:</strong> 'low poly tavsan', 'gercekci insan figurunu'</p><h2>Gorsel ile Model Uretme</h2><p>1. Temiz arka plan kullanin</p><p>2. Nesneyi ortada ve net cekin</p><p>3. Iyi aydinlatma saglayin</p><p>4. Tek bir nesne olsun</p>"
    },
    {
        "id": 5, "slug": "3d-baski-ipuclari",
        "title": "3D Baski Icin 10 Altin Ipucu",
        "summary": "Basarili 3D baskilar icin bilmeniz gereken en onemli ipuclari.",
        "category": "ipucu", "date": "2025-02-15", "read_time": "4 dk",
        "content": "<h2>1. Yatak Sicakligini Dogru Ayarlayin</h2><p>PLA: 60C, ABS: 100C, PETG: 80C</p><h2>2. Ilk Katman Cok Onemli</h2><p>Ilk katman yapismazsa tum baski basarisiz olur.</p><h2>3. Destek Yapilarini Dogru Kullanin</h2><p>45 dereceden fazla egimli yuzeyler icin destek kullanin.</p><h2>4. Doluluk Oranini Ayarlayin</h2><p>Dekoratif: %10-15, Normal: %20-30, Guclu: %50-100</p><h2>5. Katman Yuksekligini Secin</h2><p>Hizli: 0.3mm, Normal: 0.2mm, Detayli: 0.1mm</p>"
    },
]


async def verify_email_dns(domain):
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"https://dns.google/resolve?name={domain}&type=MX")
            if r.json().get("Answer"):
                return True
            r2 = await c.get(f"https://dns.google/resolve?name={domain}&type=A")
            return bool(r2.json().get("Answer"))
    except:
        return True


async def validate_email(email):
    email = email.lower().strip()
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9._%+-]*@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return False, "Gecerli bir e-posta girin"
    local, domain = email.split("@", 1)
    if len(local) < 2: return False, "E-posta cok kisa"
    if len(domain) < 4: return False, "Gecerli bir e-posta saglayicisi kullanin"
    if domain in BLOCKED_DOMAINS: return False, "Gecici e-posta kabul edilmiyor. Gmail, Outlook veya Yahoo kullanin."
    for b in BLOCKED_DOMAINS:
        if domain.endswith("." + b): return False, "Bu e-posta saglayicisi kabul edilmiyor."
    for pat in BLOCKED_PATTERNS:
        if re.match(pat, email): return False, "Bu e-posta gecersiz."
    if domain not in ALLOWED_DOMAINS:
        if not await verify_email_dns(domain): return False, "Bu e-posta domaini bulunamadi."
    return True, "OK"


async def send_email(to, subject, html_content):
    if not RESEND_API_KEY: return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post("https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                json={"from": EMAIL_FROM, "to": [to], "subject": subject, "html": html_content})
            return r.status_code in (200, 201)
    except: return False


async def send_verification_email(email, token):
    link = f"{get_site_url()}/api/auth/verify?token={token}"
    html = f'<div style="font-family:Arial;max-width:500px;margin:0 auto;padding:30px;background:#04080a;border:1px solid #0e2028;border-radius:12px"><div style="text-align:center;margin-bottom:24px"><span style="font-size:24px;font-weight:800;color:#00e5ff">PRINTFORGE</span></div><h2 style="color:#c8dde5">Hesabinizi Dogrulayin</h2><p style="color:#2a4a5a;line-height:1.8;margin-bottom:24px">Hesabinizi aktif etmek icin butona tiklayin.</p><div style="text-align:center;margin-bottom:24px"><a href="{link}" style="display:inline-block;padding:14px 36px;background:#00e5ff;color:#04080a;text-decoration:none;font-weight:700;border-radius:8px">Hesabi Dogrula</a></div></div>'
    return await send_email(email, "PrintForge - Hesap Dogrulama", html)


async def send_reset_email(email, token):
    link = f"{get_site_url()}/app?reset={token}"
    html = f'<div style="font-family:Arial;max-width:500px;margin:0 auto;padding:30px;background:#04080a;border:1px solid #0e2028;border-radius:12px"><div style="text-align:center;margin-bottom:24px"><span style="font-size:24px;font-weight:800;color:#00e5ff">PRINTFORGE</span></div><h2 style="color:#c8dde5">Sifre Sifirlama</h2><p style="color:#2a4a5a;line-height:1.8;margin-bottom:24px">Sifrenizi sifirlamak icin butona tiklayin.</p><div style="text-align:center;margin-bottom:24px"><a href="{link}" style="display:inline-block;padding:14px 36px;background:#00e5ff;color:#04080a;text-decoration:none;font-weight:700;border-radius:8px">Sifremi Sifirla</a></div></div>'
    return await send_email(email, "PrintForge - Sifre Sifirlama", html)


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
    password: Optional[str] = None

class ForgotPasswordReq(BaseModel):
    email: str

class ResetPasswordReq(BaseModel):
    token: str
    password: str

class CommentReq(BaseModel):
    text: str

class CollectionReq(BaseModel):
    name: str
    description: str = ""
    is_public: int = 1

STYLE_MAP = {
    "realistic": "realistic", "cartoon": "cartoon", "lowpoly": "low-poly",
    "sculpture": "sculpture", "mechanical": "pbr", "miniature": "sculpture",
    "geometric": "realistic",
}

def get_api():
    if TRIPO_API_KEY: return "tripo"
    if MESHY_API_KEY: return "meshy"
    return "demo"
  # ════════ VERITABANI ════════
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            plan TEXT DEFAULT 'free',
            google_id TEXT,
            avatar_url TEXT,
            verified INTEGER DEFAULT 0,
            verify_token TEXT,
            reset_token TEXT,
            reset_expires TEXT,
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
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS collections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            is_public INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS collection_models (
            collection_id INTEGER,
            model_id INTEGER,
            added_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY(collection_id, model_id)
        );
    """)
    conn.commit()
    conn.close()


init_db()


@app.on_event("startup")
async def startup():
    init_db()
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        try:
            os.makedirs(db_dir, exist_ok=True)
        except:
            pass
    print(f"[DB] Yol: {DB_PATH}")


# ════════ AUTH HELPERS ════════
def hash_pw(pw):
    salt = secrets.token_hex(16)
    return salt, hashlib.sha256((salt + pw).encode()).hexdigest()

def verify_pw(pw, salt, h):
    return hashlib.sha256((salt + pw).encode()).hexdigest() == h

def create_token(uid, email, name, plan):
    if not HAS_JWT: return "no-jwt"
    return pyjwt.encode({"user_id": uid, "email": email, "name": name, "plan": plan, "exp": datetime.utcnow() + timedelta(days=30)}, SECRET_KEY, algorithm="HS256")

def decode_token(t):
    if not HAS_JWT: return None
    try: return pyjwt.decode(t, SECRET_KEY, algorithms=["HS256"])
    except: return None

async def get_user(authorization: Optional[str] = Header(None)):
    if not authorization: return None
    data = decode_token(authorization.replace("Bearer ", ""))
    if not data: return None
    conn = get_db()
    row = conn.execute("SELECT id,email,name,plan,avatar_url,verified,created_at FROM users WHERE id=?", (data["user_id"],)).fetchone()
    conn.close()
    if row: return {"id": row[0], "email": row[1], "name": row[2], "plan": row[3], "avatar_url": row[4], "verified": row[5], "created_at": row[6]}
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
    conn.execute("INSERT INTO usage(user_id,month,count) VALUES(?,?,1) ON CONFLICT(user_id,month) DO UPDATE SET count=count+1", (uid, month))
    conn.commit()
    conn.close()

def save_model(uid, tid, title, prompt, gtype, style, url):
    conn = get_db()
    try: conn.execute("INSERT INTO models(user_id,task_id,title,prompt,gen_type,style,model_url) VALUES(?,?,?,?,?,?,?)", (uid, tid, title, prompt, gtype, style, url))
    except: pass
    conn.commit()
    conn.close()

def get_user_stats(uid):
    conn = get_db()
    mc = conn.execute("SELECT COUNT(*) FROM models WHERE user_id=?", (uid,)).fetchone()[0]
    tl = conn.execute("SELECT COALESCE(SUM(likes),0) FROM models WHERE user_id=?", (uid,)).fetchone()[0]
    td = conn.execute("SELECT COALESCE(SUM(downloads),0) FROM models WHERE user_id=?", (uid,)).fetchone()[0]
    cc = conn.execute("SELECT COUNT(*) FROM collections WHERE user_id=?", (uid,)).fetchone()[0]
    conn.close()
    return {"models": mc, "likes": tl, "downloads": td, "collections": cc}


# ════════ SAYFALAR ════════
@app.get("/", response_class=HTMLResponse)
def serve_landing():
    for name in ["index.html", "printforge.html"]:
        path = os.path.join(os.path.dirname(__file__), name)
        if os.path.exists(path): return FileResponse(path, media_type="text/html")
    return HTMLResponse("<html><body style='background:#04080a;color:#00e5ff;display:flex;align-items:center;justify-content:center;height:100vh'><a href='/app' style='color:#00e5ff;font-size:24px'>PrintForge /app</a></body></html>")

@app.get("/app", response_class=HTMLResponse)
def serve_app():
    path = os.path.join(os.path.dirname(__file__), "app.html")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f: return HTMLResponse(f.read())
    return HTMLResponse("<html><body><h1>app.html bulunamadi</h1></body></html>")


# ════════ AUTH API ════════
@app.post("/api/auth/register")
async def register(req: RegisterReq):
    if len(req.password) < 6: raise HTTPException(400, "Sifre en az 6 karakter")
    if not req.name.strip() or len(req.name.strip()) < 2: raise HTTPException(400, "Gecerli bir isim girin")
    valid, msg = await validate_email(req.email)
    if not valid: raise HTTPException(400, msg)
    salt, h = hash_pw(req.password)
    verify_token = secrets.token_urlsafe(32)
    conn = get_db()
    try:
        conn.execute("INSERT INTO users(email,name,password_hash,salt,verify_token,verified) VALUES(?,?,?,?,?,?)",
                     (req.email.lower().strip(), req.name.strip(), h, salt, verify_token, 0 if RESEND_API_KEY else 1))
        conn.commit()
        uid = conn.execute("SELECT id FROM users WHERE email=?", (req.email.lower().strip(),)).fetchone()[0]
        conn.close()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(400, "Bu e-posta zaten kayitli")
    if RESEND_API_KEY:
        await send_verification_email(req.email.lower().strip(), verify_token)
    token = create_token(uid, req.email, req.name, "free")
    result = {"token": token, "user": {"id": uid, "name": req.name, "email": req.email, "plan": "free", "verified": 0 if RESEND_API_KEY else 1}}
    if RESEND_API_KEY:
        result["message"] = "Dogrulama maili gonderildi. E-postanizi kontrol edin."
    return result

@app.post("/api/auth/login")
async def login(req: LoginReq):
    conn = get_db()
    row = conn.execute("SELECT id,email,name,password_hash,salt,plan,verified FROM users WHERE email=?", (req.email.lower().strip(),)).fetchone()
    conn.close()
    if not row: raise HTTPException(401, "E-posta veya sifre hatali")
    if not verify_pw(req.password, row["salt"], row["password_hash"]): raise HTTPException(401, "E-posta veya sifre hatali")
    token = create_token(row["id"], row["email"], row["name"], row["plan"])
    return {"token": token, "user": {"id": row["id"], "name": row["name"], "email": row["email"], "plan": row["plan"], "verified": row["verified"]}}

@app.get("/api/auth/me")
async def get_me(authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Giris yapin")
    used = get_usage(user["id"])
    limit = PLAN_LIMITS.get(user["plan"], 5)
    stats = get_user_stats(user["id"])
    return {"user": user, "usage": {"used": used, "limit": limit, "remaining": max(0, limit - used)}, "stats": stats}

@app.get("/api/auth/verify")
async def verify_email(token: str = ""):
    if not token: raise HTTPException(400, "Gecersiz link")
    conn = get_db()
    row = conn.execute("SELECT id FROM users WHERE verify_token=?", (token,)).fetchone()
    if not row:
        conn.close()
        return HTMLResponse("<html><body style='background:#04080a;color:#ff4466;font-family:Arial;display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column'><h2>Gecersiz veya suresi dolmus link</h2><a href='/app' style='color:#00e5ff;margin-top:16px'>Uygulamaya Don</a></body></html>")
    conn.execute("UPDATE users SET verified=1, verify_token=NULL WHERE id=?", (row["id"],))
    conn.commit()
    conn.close()
    return HTMLResponse("<html><body style='background:#04080a;color:#00ff9d;font-family:Arial;display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column'><h2>Hesabiniz dogrulandi!</h2><a href='/app' style='color:#00e5ff;margin-top:16px;font-size:18px'>Uygulamaya Git</a></body></html>")

@app.post("/api/auth/resend-verification")
async def resend_verification(authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Giris yapin")
    if user.get("verified") == 1: return {"message": "Hesabiniz zaten dogrulanmis"}
    if not RESEND_API_KEY: raise HTTPException(400, "E-posta servisi yapilandirilmamis")
    new_token = secrets.token_urlsafe(32)
    conn = get_db()
    conn.execute("UPDATE users SET verify_token=? WHERE id=?", (new_token, user["id"]))
    conn.commit()
    conn.close()
    await send_verification_email(user["email"], new_token)
    return {"message": "Dogrulama maili tekrar gonderildi"}

@app.post("/api/auth/forgot-password")
async def forgot_password(req: ForgotPasswordReq):
    if not RESEND_API_KEY: raise HTTPException(400, "E-posta servisi yapilandirilmamis")
    conn = get_db()
    row = conn.execute("SELECT id,email FROM users WHERE email=?", (req.email.lower().strip(),)).fetchone()
    if not row:
        conn.close()
        return {"message": "Eger bu e-posta kayitliysa sifirlama maili gonderildi"}
    reset_token = secrets.token_urlsafe(32)
    expires = (datetime.utcnow() + timedelta(hours=1)).isoformat()
    conn.execute("UPDATE users SET reset_token=?, reset_expires=? WHERE id=?", (reset_token, expires, row["id"]))
    conn.commit()
    conn.close()
    await send_reset_email(row["email"], reset_token)
    return {"message": "Sifre sifirlama maili gonderildi"}

@app.post("/api/auth/reset-password")
async def reset_password(req: ResetPasswordReq):
    if len(req.password) < 6: raise HTTPException(400, "Sifre en az 6 karakter")
    conn = get_db()
    row = conn.execute("SELECT id,reset_expires FROM users WHERE reset_token=?", (req.token,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(400, "Gecersiz veya suresi dolmus link")
    if row["reset_expires"]:
        try:
            if datetime.utcnow() > datetime.fromisoformat(row["reset_expires"]):
                conn.close()
                raise HTTPException(400, "Sifirlama linkinin suresi dolmus")
        except: pass
    salt, h = hash_pw(req.password)
    conn.execute("UPDATE users SET password_hash=?, salt=?, reset_token=NULL, reset_expires=NULL WHERE id=?", (h, salt, row["id"]))
    conn.commit()
    conn.close()
    return {"message": "Sifreniz basariyla degistirildi"}

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
    conn.commit()
    conn.close()
    return {"success": True}

# ════════ GOOGLE LOGIN ════════
@app.get("/api/auth/google")
async def google_login():
    if not GOOGLE_CLIENT_ID: raise HTTPException(400, "Google login yapilandirilmamis")
    redirect_uri = f"{get_site_url()}/api/auth/google/callback"
    params = urlencode({"client_id": GOOGLE_CLIENT_ID, "redirect_uri": redirect_uri, "response_type": "code", "scope": "openid email profile", "access_type": "offline", "prompt": "select_account"})
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")

@app.get("/api/auth/google/callback")
async def google_callback(code: str = ""):
    if not code: raise HTTPException(400, "Google login basarisiz")
    redirect_uri = f"{get_site_url()}/api/auth/google/callback"
    async with httpx.AsyncClient() as client:
        tr = await client.post("https://oauth2.googleapis.com/token", data={"client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri})
        if tr.status_code != 200: raise HTTPException(400, "Google token alinamadi")
        at = tr.json().get("access_token")
        ur = await client.get("https://www.googleapis.com/oauth2/v2/userinfo", headers={"Authorization": f"Bearer {at}"})
        if ur.status_code != 200: raise HTTPException(400, "Google bilgi alinamadi")
        gu = ur.json()
    email, name, gid, avatar = gu.get("email", "").lower(), gu.get("name", ""), gu.get("id", ""), gu.get("picture", "")
    conn = get_db()
    ex = conn.execute("SELECT id,name,plan FROM users WHERE email=?", (email,)).fetchone()
    if ex:
        uid, name, plan = ex["id"], ex["name"], ex["plan"]
        conn.execute("UPDATE users SET google_id=?,avatar_url=?,verified=1 WHERE id=?", (gid, avatar, uid))
    else:
        salt, h = hash_pw(secrets.token_hex(16))
        conn.execute("INSERT INTO users(email,name,password_hash,salt,google_id,avatar_url,verified) VALUES(?,?,?,?,?,?,1)", (email, name, h, salt, gid, avatar))
        uid = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()[0]
        plan = "free"
    conn.commit()
    conn.close()
    jwt_token = create_token(uid, email, name, plan)
    return HTMLResponse("<html><head><script>localStorage.setItem('pf_token','" + jwt_token + "');window.location.href='/app';</script></head><body style='background:#04080a;color:#00e5ff;display:flex;align-items:center;justify-content:center;height:100vh'>Giris yapiliyor...</body></html>")

# ════════ MODEL URETIMI ════════
@app.post("/api/generate/text")
async def generate_text(req: TextRequest, authorization: Optional[str] = Header(None)):
    api = get_api()
    user = await get_user(authorization)
    if api != "demo":
        if not user: raise HTTPException(401, "Model uretmek icin giris yapin")
        used = get_usage(user["id"])
        limit = PLAN_LIMITS.get(user["plan"], 5)
        if used >= limit: raise HTTPException(403, f"Aylik limitinize ulastiniz ({limit})")
        add_usage(user["id"])
    tid = str(uuid.uuid4())[:8]
    tasks[tid] = {"status": "processing", "progress": 0, "step": "Baslatiliyor...", "type": "text", "api": api, "prompt": req.prompt, "style": req.style, "user_id": user["id"] if user else 0}
    if api == "tripo": asyncio.create_task(_tripo_text(tid, req.prompt, req.style))
    elif api == "meshy": asyncio.create_task(_meshy_text(tid, req.prompt, req.style))
    else: asyncio.create_task(_demo_generate(tid))
    return {"task_id": tid}

@app.post("/api/generate/image")
async def generate_image(file: UploadFile = File(...), authorization: Optional[str] = Header(None)):
    api = get_api()
    user = await get_user(authorization)
    if api != "demo":
        if not user: raise HTTPException(401, "Model uretmek icin giris yapin")
        used = get_usage(user["id"])
        limit = PLAN_LIMITS.get(user["plan"], 5)
        if used >= limit: raise HTTPException(403, f"Aylik limitinize ulastiniz ({limit})")
        add_usage(user["id"])
    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024: raise HTTPException(400, "Dosya cok buyuk (max 10MB)")
    tid = str(uuid.uuid4())[:8]
    fname = file.filename or "image.jpg"
    tasks[tid] = {"status": "processing", "progress": 0, "step": "Gorsel hazirlaniyor...", "type": "image", "api": api, "prompt": fname, "style": "", "user_id": user["id"] if user else 0}
    if api == "tripo": asyncio.create_task(_tripo_image(tid, contents, fname))
    elif api == "meshy": asyncio.create_task(_meshy_image(tid, contents, fname))
    else: asyncio.create_task(_demo_generate(tid))
    return {"task_id": tid}

@app.get("/api/status/{task_id}")
async def get_status(task_id: str):
    if task_id not in tasks: raise HTTPException(404, "Gorev bulunamadi")
    t = tasks[task_id]
    return {"task_id": task_id, "status": t["status"], "progress": t["progress"], "step": t.get("step", ""), "model_url": t.get("model_url", ""), "is_demo": t.get("api") == "demo", "cached": task_id in model_cache, "error": t.get("error", "")}

# ════════ MODEL SUNMA ════════
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
    return Response(content=model_cache[task_id], media_type="model/gltf-binary", headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=3600"})

@app.get("/api/model/{task_id}/glb")
async def download_glb(task_id: str):
    if not await ensure_cached(task_id): raise HTTPException(404, "Model bulunamadi")
    conn = get_db(); conn.execute("UPDATE models SET downloads=downloads+1 WHERE task_id=?", (task_id,)); conn.commit(); conn.close()
    return Response(content=model_cache[task_id], media_type="model/gltf-binary", headers={"Content-Disposition": f'attachment; filename="printforge_{task_id}.glb"'})

@app.get("/api/model/{task_id}/stl")
async def download_stl(task_id: str):
    if not HAS_TRIMESH: raise HTTPException(500, "STL yuklu degil")
    if not await ensure_cached(task_id): raise HTTPException(404, "Model bulunamadi")
    try:
        scene = trimesh.load(io.BytesIO(model_cache[task_id]), file_type="glb", force="scene")
        meshes = [g for g in scene.geometry.values() if isinstance(g, trimesh.Trimesh)] if isinstance(scene, trimesh.Scene) else [scene]
        if not meshes: raise Exception("Mesh yok")
        stl = trimesh.util.concatenate(meshes).export(file_type="stl")
        conn = get_db(); conn.execute("UPDATE models SET downloads=downloads+1 WHERE task_id=?", (task_id,)); conn.commit(); conn.close()
        return Response(content=stl, media_type="application/vnd.ms-pki.stl", headers={"Content-Disposition": f'attachment; filename="printforge_{task_id}.stl"'})
    except Exception as e: raise HTTPException(500, f"STL hatasi: {e}")

@app.get("/api/model/{task_id}/obj")
async def download_obj(task_id: str):
    if not HAS_TRIMESH: raise HTTPException(500, "OBJ yuklu degil")
    if not await ensure_cached(task_id): raise HTTPException(404, "Model bulunamadi")
    try:
        scene = trimesh.load(io.BytesIO(model_cache[task_id]), file_type="glb", force="scene")
        meshes = [g for g in scene.geometry.values() if isinstance(g, trimesh.Trimesh)] if isinstance(scene, trimesh.Scene) else [scene]
        obj = trimesh.util.concatenate(meshes).export(file_type="obj")
        return Response(content=obj, media_type="text/plain", headers={"Content-Disposition": f'attachment; filename="printforge_{task_id}.obj"'})
    except Exception as e: raise HTTPException(500, f"OBJ hatasi: {e}")
      # ════════ GALERI ════════
@app.get("/api/gallery")
async def gallery(page: int = 1, limit: int = 20, sort: str = "newest", search: str = ""):
    conn = get_db()
    offset = (page - 1) * limit
    where = "WHERE is_public=1 AND model_url != ''"
    params = []
    if search:
        where += " AND (title LIKE ? OR prompt LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    order = {"popular": "ORDER BY likes DESC", "downloads": "ORDER BY downloads DESC"}.get(sort, "ORDER BY created_at DESC")
    rows = conn.execute(f"SELECT m.*, u.name as author_name FROM models m LEFT JOIN users u ON m.user_id=u.id {where} {order} LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
    total = conn.execute(f"SELECT COUNT(*) FROM models {where}", params).fetchone()[0]
    conn.close()
    return {"models": [dict(r) for r in rows], "total": total, "page": page, "pages": max(1, (total + limit - 1) // limit)}

@app.get("/api/gallery/{model_id}")
async def model_detail(model_id: int):
    conn = get_db()
    row = conn.execute("SELECT m.*, u.name as author_name FROM models m LEFT JOIN users u ON m.user_id=u.id WHERE m.id=?", (model_id,)).fetchone()
    conn.close()
    if not row: raise HTTPException(404, "Model bulunamadi")
    return dict(row)

@app.get("/api/gallery/{model_id}/similar")
async def similar_models(model_id: int, limit: int = 6):
    conn = get_db()
    cur = conn.execute("SELECT style, gen_type FROM models WHERE id=?", (model_id,)).fetchone()
    if not cur: conn.close(); raise HTTPException(404, "Bulunamadi")
    style, gtype = cur["style"] or "", cur["gen_type"] or ""
    rows = conn.execute("SELECT m.*, u.name as author_name FROM models m LEFT JOIN users u ON m.user_id=u.id WHERE m.id!=? AND m.is_public=1 AND m.model_url!='' AND (m.style=? OR m.gen_type=?) ORDER BY m.likes DESC LIMIT ?", (model_id, style, gtype, limit)).fetchall()
    if len(rows) < limit:
        extra = conn.execute("SELECT m.*, u.name as author_name FROM models m LEFT JOIN users u ON m.user_id=u.id WHERE m.id!=? AND m.is_public=1 AND m.model_url!='' ORDER BY RANDOM() LIMIT ?", (model_id, limit - len(rows))).fetchall()
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
    if not user: raise HTTPException(401, "Giris yapin")
    conn = get_db()
    rows = conn.execute("SELECT * FROM models WHERE user_id=? ORDER BY created_at DESC", (user["id"],)).fetchall()
    conn.close()
    return {"models": [dict(r) for r in rows]}

@app.delete("/api/my-models/{model_id}")
async def delete_model(model_id: int, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Giris yapin")
    conn = get_db()
    conn.execute("DELETE FROM models WHERE id=? AND user_id=?", (model_id, user["id"]))
    conn.execute("DELETE FROM comments WHERE model_id=?", (model_id,))
    conn.execute("DELETE FROM collection_models WHERE model_id=?", (model_id,))
    conn.commit()
    conn.close()
    return {"deleted": True}


# ════════ YORUM SİSTEMİ ════════
@app.get("/api/gallery/{model_id}/comments")
async def get_comments(model_id: int):
    conn = get_db()
    rows = conn.execute(
        "SELECT c.*, u.name as author_name FROM comments c LEFT JOIN users u ON c.user_id=u.id WHERE c.model_id=? ORDER BY c.created_at DESC",
        (model_id,)
    ).fetchall()
    conn.close()
    return {"comments": [dict(r) for r in rows]}

@app.post("/api/gallery/{model_id}/comments")
async def add_comment(model_id: int, req: CommentReq, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Yorum yapmak icin giris yapin")
    text = req.text.strip()
    if not text or len(text) < 2: raise HTTPException(400, "Yorum cok kisa")
    if len(text) > 500: raise HTTPException(400, "Yorum en fazla 500 karakter olabilir")
    conn = get_db()
    conn.execute("INSERT INTO comments(model_id, user_id, text) VALUES(?,?,?)", (model_id, user["id"], text))
    conn.commit()
    conn.close()
    return {"success": True}

@app.delete("/api/comments/{comment_id}")
async def delete_comment(comment_id: int, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Giris yapin")
    conn = get_db()
    row = conn.execute("SELECT user_id FROM comments WHERE id=?", (comment_id,)).fetchone()
    if not row: raise HTTPException(404, "Yorum bulunamadi")
    if row["user_id"] != user["id"]: raise HTTPException(403, "Bu yorumu silemezsiniz")
    conn.execute("DELETE FROM comments WHERE id=?", (comment_id,))
    conn.commit()
    conn.close()
    return {"deleted": True}


# ════════ KOLEKSİYON SİSTEMİ ════════
@app.get("/api/collections")
async def get_my_collections(authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Giris yapin")
    conn = get_db()
    rows = conn.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM collection_models cm WHERE cm.collection_id=c.id) as model_count FROM collections c WHERE c.user_id=? ORDER BY c.created_at DESC",
        (user["id"],)
    ).fetchall()
    conn.close()
    return {"collections": [dict(r) for r in rows]}

@app.post("/api/collections")
async def create_collection(req: CollectionReq, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Giris yapin")
    name = req.name.strip()
    if not name or len(name) < 2: raise HTTPException(400, "Koleksiyon adi cok kisa")
    if len(name) > 50: raise HTTPException(400, "Koleksiyon adi en fazla 50 karakter")
    conn = get_db()
    conn.execute("INSERT INTO collections(user_id, name, description, is_public) VALUES(?,?,?,?)",
                 (user["id"], name, req.description.strip()[:200], req.is_public))
    conn.commit()
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return {"id": cid, "success": True}

@app.delete("/api/collections/{collection_id}")
async def delete_collection(collection_id: int, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Giris yapin")
    conn = get_db()
    row = conn.execute("SELECT user_id FROM collections WHERE id=?", (collection_id,)).fetchone()
    if not row: raise HTTPException(404, "Koleksiyon bulunamadi")
    if row["user_id"] != user["id"]: raise HTTPException(403, "Bu koleksiyonu silemezsiniz")
    conn.execute("DELETE FROM collection_models WHERE collection_id=?", (collection_id,))
    conn.execute("DELETE FROM collections WHERE id=?", (collection_id,))
    conn.commit()
    conn.close()
    return {"deleted": True}

@app.get("/api/collections/{collection_id}")
async def get_collection(collection_id: int):
    conn = get_db()
    col = conn.execute(
        "SELECT c.*, u.name as owner_name FROM collections c LEFT JOIN users u ON c.user_id=u.id WHERE c.id=?",
        (collection_id,)
    ).fetchone()
    if not col: conn.close(); raise HTTPException(404, "Koleksiyon bulunamadi")
    models = conn.execute(
        "SELECT m.*, u.name as author_name FROM collection_models cm JOIN models m ON cm.model_id=m.id LEFT JOIN users u ON m.user_id=u.id WHERE cm.collection_id=? ORDER BY cm.added_at DESC",
        (collection_id,)
    ).fetchall()
    conn.close()
    return {"collection": dict(col), "models": [dict(r) for r in models]}

@app.post("/api/collections/{collection_id}/add/{model_id}")
async def add_to_collection(collection_id: int, model_id: int, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Giris yapin")
    conn = get_db()
    col = conn.execute("SELECT user_id FROM collections WHERE id=?", (collection_id,)).fetchone()
    if not col: conn.close(); raise HTTPException(404, "Koleksiyon bulunamadi")
    if col["user_id"] != user["id"]: conn.close(); raise HTTPException(403, "Bu koleksiyona ekleyemezsiniz")
    try:
        conn.execute("INSERT INTO collection_models(collection_id, model_id) VALUES(?,?)", (collection_id, model_id))
        conn.commit()
    except: pass
    conn.close()
    return {"success": True}

@app.delete("/api/collections/{collection_id}/remove/{model_id}")
async def remove_from_collection(collection_id: int, model_id: int, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Giris yapin")
    conn = get_db()
    col = conn.execute("SELECT user_id FROM collections WHERE id=?", (collection_id,)).fetchone()
    if not col or col["user_id"] != user["id"]: conn.close(); raise HTTPException(403, "Yetkiniz yok")
    conn.execute("DELETE FROM collection_models WHERE collection_id=? AND model_id=?", (collection_id, model_id))
    conn.commit()
    conn.close()
    return {"removed": True}


# ════════ BLOG ════════
@app.get("/api/blog")
async def get_blog_posts():
    return {"posts": BLOG_POSTS}

@app.get("/api/blog/{slug}")
async def get_blog_post(slug: str):
    for post in BLOG_POSTS:
        if post["slug"] == slug:
            return post
    raise HTTPException(404, "Yazi bulunamadi")


# ════════ DIGER ════════
@app.post("/api/payment/upgrade")
async def upgrade_plan(authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user: raise HTTPException(401, "Giris yapin")
    conn = get_db()
    conn.execute("UPDATE users SET plan='pro' WHERE id=?", (user["id"],))
    conn.commit()
    conn.close()
    return {"success": True, "plan": "pro"}

@app.get("/api/health")
async def health():
    api = get_api()
    return {"status": "online", "active_api": api, "api_ready": True, "is_demo": api == "demo", "stl_ready": HAS_TRIMESH, "auth_ready": HAS_JWT, "google_ready": bool(GOOGLE_CLIENT_ID), "email_ready": bool(RESEND_API_KEY), "cached_models": len(model_cache)}

@app.get("/api/debug/{task_id}")
async def debug_task(task_id: str):
    if task_id not in tasks: return {"error": "Bulunamadi"}
    return {"task_id": task_id, "data": tasks[task_id], "cached": task_id in model_cache}


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
        tasks[tid]["progress"] = 10; tasks[tid]["step"] = "Prompt gonderiliyor..."
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.post(f"{TRIPO_BASE}/task", json={"type": "text_to_model", "prompt": f"{prompt}, {style} style"}, headers={**h, "Content-Type": "application/json"})
            if r.status_code != 200: raise Exception(f"Tripo hata {r.status_code}")
            tripo_id = r.json().get("data", {}).get("task_id")
            if not tripo_id: raise Exception("Task ID alinamadi")
            tasks[tid]["progress"] = 25; await _tripo_poll(c, h, tid, tripo_id)
    except Exception as e: tasks[tid]["status"] = "failed"; tasks[tid]["error"] = str(e)

async def _tripo_image(tid, contents, fname):
    try:
        h = {"Authorization": f"Bearer {TRIPO_API_KEY}"}
        ext = fname.rsplit(".", 1)[-1].lower()
        if ext not in ("jpg", "jpeg", "png", "webp"): ext = "jpeg"
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        tasks[tid]["progress"] = 10; tasks[tid]["step"] = "Gorsel yukleniyor..."
        async with httpx.AsyncClient(timeout=600) as c:
            ur = await c.post(f"{TRIPO_BASE}/upload", files={"file": (fname, contents, mime)}, headers=h)
            if ur.status_code != 200: raise Exception(f"Upload hata {ur.status_code}")
            token = ur.json().get("data", {}).get("image_token")
            if not token: raise Exception("Token alinamadi")
            tasks[tid]["progress"] = 25; tasks[tid]["step"] = "Model olusturuluyor..."
            tr = await c.post(f"{TRIPO_BASE}/task", json={"type": "image_to_model", "file": {"type": ext if ext != "jpg" else "jpeg", "file_token": token}}, headers={**h, "Content-Type": "application/json"})
            if tr.status_code != 200: raise Exception(f"Task hata {tr.status_code}")
            tripo_id = tr.json().get("data", {}).get("task_id")
            if not tripo_id: raise Exception("Task ID alinamadi")
            tasks[tid]["progress"] = 35; await _tripo_poll(c, h, tid, tripo_id)
    except Exception as e: tasks[tid]["status"] = "failed"; tasks[tid]["error"] = str(e)

async def _tripo_poll(client, headers, tid, tripo_id):
    for _ in range(200):
        await asyncio.sleep(3)
        try:
            r = await client.get(f"{TRIPO_BASE}/task/{tripo_id}", headers=headers)
            d = r.json().get("data", {}); st = d.get("status", ""); pr = d.get("progress", 0)
            tasks[tid]["progress"] = 35 + int(pr * 0.55); tasks[tid]["step"] = f"Model uretiliyor... %{pr}"
            if st == "success":
                url = extract_model_url(d.get("output", {}))
                tasks[tid]["model_url"] = url; tasks[tid]["progress"] = 92; tasks[tid]["step"] = "Model indiriliyor..."
                if url: await cache_model(tid, url)
                tasks[tid]["status"] = "done"; tasks[tid]["progress"] = 100; tasks[tid]["step"] = "Tamamlandi!"
                uid = tasks[tid].get("user_id", 0); prompt = tasks[tid].get("prompt", "")
                save_model(uid, tid, prompt[:50], prompt, tasks[tid].get("type", ""), tasks[tid].get("style", ""), url)
                return
            elif st in ("failed", "cancelled"): raise Exception(f"Tripo: {st}")
        except Exception as e:
            if any(x in str(e) for x in ["Tripo", "failed", "cancelled"]): tasks[tid]["status"] = "failed"; tasks[tid]["error"] = str(e); return
    tasks[tid]["status"] = "failed"; tasks[tid]["error"] = "Zaman asimi"

async def _meshy_text(tid, prompt, style):
    try:
        h = {"Authorization": f"Bearer {MESHY_API_KEY}", "Content-Type": "application/json"}
        tasks[tid]["progress"] = 10; tasks[tid]["step"] = "Prompt gonderiliyor..."
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.post(f"{MESHY_BASE}/text-to-3d", json={"mode": "preview", "prompt": prompt, "art_style": "realistic"}, headers=h)
            if r.status_code not in (200, 202): raise Exception(f"Meshy hata {r.status_code}")
            mid = r.json().get("result"); tasks[tid]["progress"] = 20
            await _meshy_poll(c, h, tid, mid, "text-to-3d")
    except Exception as e: tasks[tid]["status"] = "failed"; tasks[tid]["error"] = str(e)

async def _meshy_image(tid, contents, fname):
    try:
        h = {"Authorization": f"Bearer {MESHY_API_KEY}", "Content-Type": "application/json"}
        ext = fname.rsplit(".", 1)[-1].lower()
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        b64 = base64.b64encode(contents).decode()
        tasks[tid]["progress"] = 15; tasks[tid]["step"] = "Gorsel gonderiliyor..."
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.post(f"{MESHY_BASE}/image-to-3d", json={"image_url": f"data:{mime};base64,{b64}", "enable_pbr": True}, headers=h)
            if r.status_code not in (200, 202): raise Exception(f"Meshy hata {r.status_code}")
            mid = r.json().get("result"); tasks[tid]["progress"] = 25
            await _meshy_poll(c, h, tid, mid, "image-to-3d")
    except Exception as e: tasks[tid]["status"] = "failed"; tasks[tid]["error"] = str(e)

async def _meshy_poll(client, h, tid, mid, ep):
    for _ in range(200):
        await asyncio.sleep(3)
        try:
            r = await client.get(f"{MESHY_BASE}/{ep}/{mid}", headers=h)
            if r.status_code != 200: continue
            d = r.json(); status = d.get("status", ""); progress = d.get("progress", 0)
            tasks[tid]["progress"] = 25 + int(progress * 0.7); tasks[tid]["step"] = f"Model uretiliyor... %{progress}"
            if status == "SUCCEEDED":
                glb = d.get("model_urls", {}).get("glb", "")
                tasks[tid]["model_url"] = glb
                if glb: await cache_model(tid, glb)
                tasks[tid]["status"] = "done"; tasks[tid]["progress"] = 100; tasks[tid]["step"] = "Tamamlandi!"
                uid = tasks[tid].get("user_id", 0); prompt = tasks[tid].get("prompt", "")
                save_model(uid, tid, prompt[:50], prompt, tasks[tid].get("type", ""), "", glb); return
            elif status == "FAILED": raise Exception("Meshy: Model uretilemedi")
        except Exception as e:
            if "uretilemedi" in str(e): tasks[tid]["status"] = "failed"; tasks[tid]["error"] = str(e); return
    tasks[tid]["status"] = "failed"; tasks[tid]["error"] = "Zaman asimi"

async def _demo_generate(tid):
    try:
        for pr, st in [(8, "Analiz ediliyor..."), (22, "AI yukleniyor..."), (40, "Geometri olusturuluyor..."), (58, "Mesh uretiliyor..."), (72, "Texture uygulaniyor..."), (88, "Optimize ediliyor..."), (95, "Hazirlaniyor...")]:
            tasks[tid]["progress"] = pr; tasks[tid]["step"] = st; await asyncio.sleep(random.uniform(1.0, 2.0))
        m = random.choice(DEMO_MODELS); tasks[tid]["model_url"] = m["glb"]
        await cache_model(tid, m["glb"])
        tasks[tid]["status"] = "done"; tasks[tid]["progress"] = 100; tasks[tid]["step"] = f"Demo: {m['name']}"
        save_model(0, tid, m["name"], "demo", "demo", "", m["glb"])
    except Exception as e: tasks[tid]["status"] = "failed"; tasks[tid]["error"] = str(e)
