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
