from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, Response, RedirectResponse
from pydantic import BaseModel
import asyncio, uuid, httpx, base64, random, json, os, io, re
import hashlib, secrets, sqlite3, time, struct
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode
from collections import defaultdict
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
# ════════ GÜVENLIK AYARLARI ════════
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
# Güvenlik sabitleri
JWT_EXPIRY_HOURS = int(os.getenv("JWT_EXPIRY_HOURS", "24"))
JWT_REFRESH_DAYS = int(os.getenv("JWT_REFRESH_DAYS", "7"))
MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_MINUTES = 15
MAX_REGISTER_PER_HOUR = 3
MAX_REQUESTS_PER_MINUTE = 60
MAX_GENERATE_PER_MINUTE = 5
PASSWORD_MIN_LENGTH = 8
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_FILE_TYPES = {"image/jpeg", "image/png", "image/webp"}
SESSION_INACTIVE_HOURS = 12
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=True,
    max_age=3600,
)
def get_site_url():
    d = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
    return f"https://{d}" if d else "http://localhost:8000"
# ════════ GÜVENLİK MIDDLEWARE ════════
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Güvenlik başlıkları ve rate limiting"""
    client_ip = get_client_ip(request)
    path = request.url.path
    # Rate limiting kontrolü
    if not check_rate_limit(client_ip, path):
        log_security("RATE_LIMIT", client_ip, f"Path: {path}")
        return Response(
            content=json.dumps({"detail": "Çok fazla istek. Lütfen bekleyin."}),
            status_code=429,
            media_type="application/json",
            headers={"Retry-After": "60"}
        )
    response = await call_next(request)
    # Güvenlik başlıkları
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://ajax.googleapis.com https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: blob: https:; "
        "connect-src 'self' https://api.tripo3d.ai https://api.meshy.ai https://raw.githubusercontent.com; "
        "frame-src 'none'; "
        "object-src 'none'; "
        "base-uri 'self'"
    )
    return response
# ════════ RATE LIMITING ════════
rate_limits = defaultdict(list)  # ip -> [timestamp, ...]
login_attempts = defaultdict(list)  # ip -> [(timestamp, success), ...]
register_attempts = defaultdict(list)  # ip -> [timestamp, ...]
generate_attempts = defaultdict(list)  # ip -> [timestamp, ...]
account_lockouts = {}  # email -> lockout_until_timestamp
blocked_ips = {}  # ip -> unblock_timestamp
def get_client_ip(request: Request) -> str:
    """Gerçek IP adresini al (proxy arkasında bile)"""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip", "")
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"
def check_rate_limit(ip: str, path: str) -> bool:
    """Genel rate limiting"""
    # Engelli IP kontrolü
    if ip in blocked_ips:
        if time.time() < blocked_ips[ip]:
            return False
        del blocked_ips[ip]
    now = time.time()
    # Eski kayıtları temizle (1 dk öncesi)
    rate_limits[ip] = [t for t in rate_limits[ip] if now - t < 60]
    rate_limits[ip].append(now)
    if len(rate_limits[ip]) > MAX_REQUESTS_PER_MINUTE:
        log_security("RATE_EXCEEDED", ip, f"Requests: {len(rate_limits[ip])}/min")
        return False
    return True
def check_login_rate(ip: str, email: str) -> tuple:
    """Login rate limiting ve brute force koruması"""
    now = time.time()
    # Hesap kilitli mi?
    if email in account_lockouts:
        if now < account_lockouts[email]:
            remaining = int((account_lockouts[email] - now) / 60) + 1
            return False, f"Hesap geçici olarak kilitlendi. {remaining} dk sonra tekrar deneyin."
        del account_lockouts[email]
    # IP bazlı kontrol
    login_attempts[ip] = [(t, s) for t, s in login_attempts[ip] if now - t < 900]
    failed = sum(1 for t, s in login_attempts[ip] if not s)
    if failed >= MAX_LOGIN_ATTEMPTS:
        account_lockouts[email] = now + (LOGIN_LOCKOUT_MINUTES * 60)
        log_security("ACCOUNT_LOCKED", ip, f"Email: {email}, Failed: {failed}")
        return False, f"Çok fazla başarısız deneme. {LOGIN_LOCKOUT_MINUTES} dk sonra tekrar deneyin."
    return True, "OK"
def record_login_attempt(ip: str, email: str, success: bool):
    """Login denemesini kaydet"""
    login_attempts[ip].append((time.time(), success))
    if not success:
        log_security("LOGIN_FAILED", ip, f"Email: {email}")
    else:
        # Başarılı girişte sayacı sıfırla
        login_attempts[ip] = [(time.time(), True)]
        if email in account_lockouts:
            del account_lockouts[email]
def check_register_rate(ip: str) -> bool:
    """Kayıt rate limiting"""
    now = time.time()
    register_attempts[ip] = [t for t in register_attempts[ip] if now - t < 3600]
    if len(register_attempts[ip]) >= MAX_REGISTER_PER_HOUR:
        log_security("REGISTER_RATE", ip, f"Attempts: {len(register_attempts[ip])}/hr")
        return False
    register_attempts[ip].append(now)
    return True
def check_generate_rate(ip: str) -> bool:
    """Model üretim rate limiting"""
    now = time.time()
    generate_attempts[ip] = [t for t in generate_attempts[ip] if now - t < 60]
    if len(generate_attempts[ip]) >= MAX_GENERATE_PER_MINUTE:
        return False
    generate_attempts[ip].append(now)
    return True
# ════════ GÜVENLİK LOGLAMA ════════
def log_security(event_type: str, ip: str, details: str = ""):
    """Güvenlik olaylarını logla ve veritabanına kaydet"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[SECURITY] [{timestamp}] [{event_type}] IP:{ip} {details}")
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO security_logs(event_type, ip_address, details, created_at) VALUES(?,?,?,?)",
            (event_type, ip, details, timestamp)
        )
        conn.commit()
        conn.close()
    except:
        pass
# ════════ GÜVENLİ ŞİFRE HASHLEME (PBKDF2) ════════
def hash_pw(pw: str) -> tuple:
    """PBKDF2-SHA512 ile güvenli şifre hashleme"""
    salt = secrets.token_hex(32)
    iterations = 310000  # OWASP önerisi
    h = hashlib.pbkdf2_hmac(
        'sha512',
        pw.encode('utf-8'),
        salt.encode('utf-8'),
        iterations
    ).hex()
    return salt, h
def verify_pw(pw: str, salt: str, h: str) -> bool:
    """PBKDF2-SHA512 ile şifre doğrulama"""
    iterations = 310000
    computed = hashlib.pbkdf2_hmac(
        'sha512',
        pw.encode('utf-8'),
        salt.encode('utf-8'),
        iterations
    ).hex()
    # Timing attack koruması - sabit zamanlı karşılaştırma
    return secrets.compare_digest(computed, h)
def validate_password_strength(password: str) -> tuple:
    """Şifre gücü kontrolü"""
    if len(password) < PASSWORD_MIN_LENGTH:
        return False, f"Şifre en az {PASSWORD_MIN_LENGTH} karakter olmalı"
    if not re.search(r'[a-zA-Z]', password):
        return False, "Şifre en az bir harf içermeli"
    if not re.search(r'[0-9]', password):
        return False, "Şifre en az bir rakam içermeli"
    # Yaygın zayıf şifreler
    weak_passwords = [
        "12345678", "password", "123456789", "qwerty123",
        "abc12345", "password1", "11111111", "iloveyou",
        "admin123", "letmein1", "welcome1", "monkey12",
    ]
    if password.lower() in weak_passwords:
        return False, "Bu şifre çok yaygın, daha güçlü bir şifre seçin"
    return True, "OK"
# ════════ GİRİŞ TEMİZLEME & DOĞRULAMA ════════
def sanitize_input(text: str, max_length: int = 500) -> str:
    """Girişi temizle - XSS ve injection koruması"""
    if not text:
        return ""
    text = text[:max_length]
    # HTML etiketlerini temizle
    text = re.sub(r'<[^>]+>', '', text)
    # Tehlikeli karakterleri kaldır
    text = text.replace('\x00', '')
    # Script injection önleme
    text = re.sub(r'(?i)(javascript|on\w+\s*=|eval\s*\(|alert\s*\()', '', text)
    return text.strip()
def sanitize_name(name: str) -> str:
    """İsim temizleme"""
    name = sanitize_input(name, 100)
    name = re.sub(r'[^\w\s\-\.]', '', name, flags=re.UNICODE)
    return name.strip()
def sanitize_email(email: str) -> str:
    """E-posta temizleme"""
    return email.lower().strip()[:254]
def sanitize_prompt(prompt: str) -> str:
    """Prompt temizleme"""
    prompt = sanitize_input(prompt, 500)
    # SQL injection kalıplarını temizle
    prompt = re.sub(r'(?i)(DROP\s+TABLE|DELETE\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|UNION\s+SELECT)', '', prompt)
    return prompt.strip()
# ════════ DOSYA DOĞRULAMA ════════
IMAGE_SIGNATURES = {
    b'\xff\xd8\xff': 'image/jpeg',
    b'\x89\x50\x4e\x47': 'image/png',
    b'\x52\x49\x46\x46': 'image/webp',  # RIFF header for WebP
}
def validate_file(contents: bytes, filename: str) -> tuple:
    """Dosya içeriğini doğrula - magic bytes kontrolü"""
    if len(contents) > MAX_UPLOAD_SIZE:
        return False, "Dosya çok büyük (max 10MB)"
    if len(contents) < 8:
        return False, "Geçersiz dosya"
    # Magic bytes kontrolü
    detected_type = None
    for signature, mime_type in IMAGE_SIGNATURES.items():
        if contents[:len(signature)] == signature:
            detected_type = mime_type
            break
    if not detected_type:
        return False, "Desteklenmeyen dosya formatı. Sadece JPG, PNG ve WEBP kabul edilir."
    # Uzantı kontrolü
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    allowed_exts = {"jpg", "jpeg", "png", "webp"}
    if ext not in allowed_exts:
        return False, f"Geçersiz dosya uzantısı: .{ext}"
    # İçerik boyutu kontrolü (min 1KB - muhtemelen gerçek bir görsel)
    if len(contents) < 1024:
        return False, "Dosya çok küçük, geçerli bir görsel değil"
    # Embedded script kontrolü
    content_start = contents[:4096].decode('latin-1', errors='ignore').lower()
    dangerous = ['<script', 'javascript:', 'onerror=', 'onload=', '<?php', '<%']
    for d in dangerous:
        if d in content_start:
            log_security("MALICIOUS_FILE", "unknown", f"Dangerous content: {d}")
            return False, "Dosyada şüpheli içerik tespit edildi"
    return True, detected_type
# ════════ VERİTABANI ════════
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
            if r.json().get("Answer"):
                return True
            r2 = await c.get(f"https://dns.google/resolve?name={domain}&type=A")
            return bool(r2.json().get("Answer"))
    except:
        return True
async def validate_email(email):
    email = sanitize_email(email)
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9._%+-]*@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return False, "Geçerli bir e-posta girin (örnek: isim@gmail.com)"
    local, domain = email.split("@", 1)
    if len(local) < 2:
        return False, "E-posta çok kısa"
    if len(domain) < 4:
        return False, "Geçerli bir e-posta sağlayıcısı kullanın"
    if domain in BLOCKED_DOMAINS:
        return False, "Geçici e-posta kabul edilmiyor. Gmail, Outlook veya Yahoo kullanın."
    for b in BLOCKED_DOMAINS:
        if domain.endswith("." + b):
            return False, "Bu e-posta sağlayıcısı kabul edilmiyor."
    for pat in BLOCKED_PATTERNS:
        if re.match(pat, email):
            return False, "Bu e-posta geçersiz. Gerçek e-posta adresinizi kullanın."
    if local.replace(".", "").replace("-", "").replace("_", "").isdigit():
        return False, "Gerçek bir e-posta kullanın"
    for ch in set(local):
        if ch * 4 in local:
            return False, "Geçerli bir e-posta girin"
    if domain not in ALLOWED_DOMAINS:
        if not await verify_email_dns(domain):
            return False, "Bu e-posta domaini bulunamadı."
    return True, "OK"
# ════════ E-POSTA GÖNDERME ════════
async def send_email(to, subject, html_content):
    if not RESEND_API_KEY:
        print(f"[MAIL] API key yok, mail gönderilemedi: {to}")
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "from": EMAIL_FROM,
                    "to": [to],
                    "subject": subject,
                    "html": html_content
                }
            )
            if r.status_code in (200, 201):
                print(f"[MAIL] Gönderildi: {to}")
                return True
            else:
                print(f"[MAIL] Hata: {r.status_code} - {r.text}")
                return False
    except Exception as e:
        print(f"[MAIL] Exception: {e}")
        return False
async def send_verification_email(email, token):
    link = f"{get_site_url()}/api/auth/verify?token={token}"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;padding:30px;background:#04080a;border:1px solid #0e2028;border-radius:12px">
        <div style="text-align:center;margin-bottom:24px">
            <span style="font-size:24px;font-weight:800;color:#00e5ff;letter-spacing:0.1em">PRINTFORGE</span>
        </div>
        <h2 style="color:#c8dde5;font-size:18px;margin-bottom:12px">Hesabınızı Doğrulayın</h2>
        <p style="color:#2a4a5a;font-size:14px;line-height:1.8;margin-bottom:24px">
            PrintForge'a hoş geldiniz! Hesabınızı aktif etmek için aşağıdaki butona tıklayın.
        </p>
        <div style="text-align:center;margin-bottom:24px">
            <a href="{link}" style="display:inline-block;padding:14px 36px;background:#00e5ff;color:#04080a;text-decoration:none;font-weight:700;font-size:14px;border-radius:8px">
                Hesabı Doğrula
            </a>
        </div>
        <p style="color:#2a4a5a;font-size:11px;line-height:1.6">
            Bu link 24 saat geçerlidir. Eğer siz kayıt olmadıysanız bu maili görmezden gelin.
        </p>
        <hr style="border:none;border-top:1px solid #0e2028;margin:20px 0">
        <p style="color:#2a4a5a;font-size:10px;text-align:center">PrintForge - AI ile 3D Model Üretici</p>
    </div>
    """
    return await send_email(email, "PrintForge - Hesap Doğrulama", html)
async def send_reset_email(email, token):
    link = f"{get_site_url()}/app?reset={token}"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;padding:30px;background:#04080a;border:1px solid #0e2028;border-radius:12px">
        <div style="text-align:center;margin-bottom:24px">
            <span style="font-size:24px;font-weight:800;color:#00e5ff;letter-spacing:0.1em">PRINTFORGE</span>
        </div>
        <h2 style="color:#c8dde5;font-size:18px;margin-bottom:12px">Şifre Sıfırlama</h2>
        <p style="color:#2a4a5a;font-size:14px;line-height:1.8;margin-bottom:24px">
            Şifrenizi sıfırlamak için aşağıdaki butona tıklayın.
        </p>
        <div style="text-align:center;margin-bottom:24px">
            <a href="{link}" style="display:inline-block;padding:14px 36px;background:#00e5ff;color:#04080a;text-decoration:none;font-weight:700;font-size:14px;border-radius:8px">
                Şifremi Sıfırla
            </a>
        </div>
        <p style="color:#2a4a5a;font-size:11px;line-height:1.6">
            Bu link 1 saat geçerlidir. Eğer siz talep etmediyseniz bu maili görmezden gelin.
        </p>
        <hr style="border:none;border-top:1px solid #0e2028;margin:20px 0">
        <p style="color:#2a4a5a;font-size:10px;text-align:center">PrintForge - AI ile 3D Model Üretici</p>
    </div>
    """
    return await send_email(email, "PrintForge - Şifre Sıfırlama", html)
async def send_security_alert(email, event_type, ip):
    """Şüpheli aktivite bildirim maili"""
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;padding:30px;background:#04080a;border:1px solid #0e2028;border-radius:12px">
        <div style="text-align:center;margin-bottom:24px">
            <span style="font-size:24px;font-weight:800;color:#00e5ff;letter-spacing:0.1em">PRINTFORGE</span>
        </div>
        <h2 style="color:#ff4466;font-size:18px;margin-bottom:12px">⚠️ Güvenlik Uyarısı</h2>
        <p style="color:#c8dde5;font-size:14px;line-height:1.8;margin-bottom:16px">
            Hesabınızda şüpheli bir aktivite tespit edildi:
        </p>
        <div style="background:#0a1318;border:1px solid #162a36;padding:16px;margin-bottom:20px">
            <p style="color:#ff9800;font-size:12px;margin:0">Olay: {event_type}</p>
            <p style="color:#2a4a5a;font-size:11px;margin:4px 0 0">IP: {ip}</p>
            <p style="color:#2a4a5a;font-size:11px;margin:4px 0 0">Tarih: {datetime.now().strftime('%d.%m.%Y %H:%M')}</p>
        </div>
        <p style="color:#2a4a5a;font-size:12px;line-height:1.8">
            Eğer bu siz değilseniz, hemen şifrenizi değiştirin.
        </p>
    </div>
    """
    return await send_email(email, "PrintForge - Güvenlik Uyarısı", html)
# ════════ REQUEST MODELLER ════════
class TextRequest(BaseModel):
    prompt: str
    style: str = "realistic"
    negative_prompt: str = ""
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
class DeleteAccountReq(BaseModel):
    password: str
class ExportDataReq(BaseModel):
    pass
STYLE_MAP = {
    "realistic": "realistic", "cartoon": "cartoon", "lowpoly": "low-poly",
    "sculpture": "sculpture", "mechanical": "pbr", "miniature": "sculpture",
    "geometric": "realistic",
}
def get_api():
    if TRIPO_API_KEY:
        return "tripo"
    if MESHY_API_KEY:
        return "meshy"
    return "demo"
# ════════ VERİTABANI ════════
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
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            plan TEXT DEFAULT 'free',
            google_id TEXT,
            avatar_url TEXT,
            verified INTEGER DEFAULT 0,
            verify_token TEXT,
            reset_token TEXT,
            reset_expires TEXT,
            last_login TEXT,
            last_ip TEXT,
            login_count INTEGER DEFAULT 0,
            failed_attempts INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
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
        CREATE TABLE IF NOT EXISTS security_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT,
            ip_address TEXT,
            details TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS active_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            token_hash TEXT UNIQUE,
            ip_address TEXT,
            user_agent TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            last_active TEXT DEFAULT (datetime('now')),
            expires_at TEXT
        );
        CREATE TABLE IF NOT EXISTS data_exports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            status TEXT DEFAULT 'pending',
            file_path TEXT,
            requested_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT
        );
    """)
    # İndeksler
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_security_logs_ip ON security_logs(ip_address)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_security_logs_type ON security_logs(event_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON active_sessions(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_models_user ON models(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_models_public ON models(is_public, model_url)")
    except:
        pass
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
    print(f"[MAIL] Resend: {'ON' if RESEND_API_KEY else 'OFF'}")
    print(f"[GOOGLE] OAuth: {'ON' if GOOGLE_CLIENT_ID else 'OFF'}")
    print(f"[SECURITY] Rate Limit: {MAX_REQUESTS_PER_MINUTE}/min")
    print(f"[SECURITY] Login Lockout: {MAX_LOGIN_ATTEMPTS} attempts / {LOGIN_LOCKOUT_MINUTES} min")
    print(f"[SECURITY] JWT Expiry: {JWT_EXPIRY_HOURS}h")
    print(f"[SECURITY] Password Min: {PASSWORD_MIN_LENGTH} chars")
# ════════ TOKEN SİSTEMİ (GÜVENLİ JWT) ════════
def create_token(uid, email, name, plan, ip="unknown"):
    if not HAS_JWT:
        return "no-jwt"
    jti = secrets.token_hex(16)  # Benzersiz token ID
    payload = {
        "user_id": uid,
        "email": email,
        "name": name,
        "plan": plan,
        "jti": jti,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    token = pyjwt.encode(payload, SECRET_KEY, algorithm="HS256")
    # Oturumu kaydet
    try:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expires = (datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS)).isoformat()
        conn = get_db()
        conn.execute(
            "INSERT INTO active_sessions(user_id, token_hash, ip_address, expires_at) VALUES(?,?,?,?)",
            (uid, token_hash, ip, expires)
        )
        conn.commit()
        conn.close()
    except:
        pass
    return token
def decode_token(t):
    if not HAS_JWT:
        return None
    try:
        payload = pyjwt.decode(t, SECRET_KEY, algorithms=["HS256"])
        # Oturum geçerlilik kontrolü
        token_hash = hashlib.sha256(t.encode()).hexdigest()
        conn = get_db()
        session = conn.execute(
            "SELECT id FROM active_sessions WHERE token_hash=? AND expires_at > datetime('now')",
            (token_hash,)
        ).fetchone()
        if session:
            # Son aktivite güncelle
            conn.execute(
                "UPDATE active_sessions SET last_active=datetime('now') WHERE token_hash=?",
                (token_hash,)
            )
            conn.commit()
        conn.close()
        if not session:
            return None  # Oturum sonlandırılmış veya süresi dolmuş
        return payload
    except pyjwt.ExpiredSignatureError:
        return None
    except:
        return None
def invalidate_token(token: str):
    """Tek bir oturumu sonlandır"""
    try:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        conn = get_db()
        conn.execute("DELETE FROM active_sessions WHERE token_hash=?", (token_hash,))
        conn.commit()
        conn.close()
    except:
        pass
def invalidate_all_sessions(user_id: int):
    """Kullanıcının tüm oturumlarını sonlandır"""
    try:
        conn = get_db()
        conn.execute("DELETE FROM active_sessions WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()
    except:
        pass
async def get_user(authorization: Optional[str] = Header(None)):
    if not authorization:
        return None
    token = authorization.replace("Bearer ", "")
    data = decode_token(token)
    if not data:
        return None
    conn = get_db()
    row = conn.execute(
        "SELECT id,email,name,plan,avatar_url,verified,created_at FROM users WHERE id=?",
        (data["user_id"],)
    ).fetchone()
    conn.close()
    if row:
        return {"id": row[0], "email": row[1], "name": row[2], "plan": row[3],
                "avatar_url": row[4], "verified": row[5], "created_at": row[6]}
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
        "ON CONFLICT(user_id,month) DO UPDATE SET count=count+1",
        (uid, month)
    )
    conn.commit()
    conn.close()
def save_model(uid, tid, title, prompt, gtype, style, url):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO models(user_id,task_id,title,prompt,gen_type,style,model_url) VALUES(?,?,?,?,?,?,?)",
            (uid, tid, sanitize_input(title, 100), sanitize_prompt(prompt), gtype, style, url)
        )
    except:
        pass
    conn.commit()
    conn.close()
def get_user_stats(uid):
    conn = get_db()
    mc = conn.execute("SELECT COUNT(*) FROM models WHERE user_id=?", (uid,)).fetchone()[0]
    tl = conn.execute("SELECT COALESCE(SUM(likes),0) FROM models WHERE user_id=?", (uid,)).fetchone()[0]
    td = conn.execute("SELECT COALESCE(SUM(downloads),0) FROM models WHERE user_id=?", (uid,)).fetchone()[0]
    conn.close()
    return {"models": mc, "likes": tl, "downloads": td}
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
    return HTMLResponse("<html><body><h1>app.html bulunamadı</h1></body></html>")
# ════════ AUTH API (GÜVENLİ) ════════
@app.post("/api/auth/register")
async def register(req: RegisterReq, request: Request):
    ip = get_client_ip(request)
    # Rate limiting
    if not check_register_rate(ip):
        log_security("REGISTER_RATE_LIMIT", ip, f"Email: {req.email}")
        raise HTTPException(429, "Çok fazla kayıt denemesi. 1 saat sonra tekrar deneyin.")
    # Şifre gücü kontrolü
    pw_ok, pw_msg = validate_password_strength(req.password)
    if not pw_ok:
        raise HTTPException(400, pw_msg)
    # İsim temizleme ve kontrol
    name = sanitize_name(req.name)
    if not name or len(name) < 2:
        raise HTTPException(400, "Geçerli bir isim girin (en az 2 karakter)")
    if len(name) > 50:
        raise HTTPException(400, "İsim çok uzun (max 50 karakter)")
    # E-posta doğrulama
    valid, msg = await validate_email(req.email)
    if not valid:
        raise HTTPException(400, msg)
    email = sanitize_email(req.email)
    salt, h = hash_pw(req.password)
    verify_token = secrets.token_urlsafe(32)
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users(email,name,password_hash,salt,verify_token,verified) VALUES(?,?,?,?,?,?)",
            (email, name, h, salt, verify_token, 0 if RESEND_API_KEY else 1)
        )
        conn.commit()
        uid = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()[0]
        conn.close()
    except sqlite3.IntegrityError:
        conn.close()
        log_security("REGISTER_DUPLICATE", ip, f"Email: {email}")
        raise HTTPException(400, "Bu e-posta zaten kayıtlı")
    log_security("REGISTER_SUCCESS", ip, f"Email: {email}, UID: {uid}")
    if RESEND_API_KEY:
        await send_verification_email(email, verify_token)
    token = create_token(uid, email, name, "free", ip)
    result = {
        "token": token,
        "user": {"id": uid, "name": name, "email": email, "plan": "free",
                 "verified": 0 if RESEND_API_KEY else 1}
    }
    if RESEND_API_KEY:
        result["message"] = "Doğrulama maili gönderildi. Lütfen e-postanızı kontrol edin."
    return result
@app.post("/api/auth/login")
async def login(req: LoginReq, request: Request):
    ip = get_client_ip(request)
    email = sanitize_email(req.email)
    # Brute force kontrolü
    rate_ok, rate_msg = check_login_rate(ip, email)
    if not rate_ok:
        raise HTTPException(429, rate_msg)
    conn = get_db()
    row = conn.execute(
        "SELECT id,email,name,password_hash,salt,plan,verified FROM users WHERE email=?",
        (email,)
    ).fetchone()
    if not row:
        conn.close()
        record_login_attempt(ip, email, False)
        raise HTTPException(401, "E-posta veya şifre hatalı")
    if not verify_pw(req.password, row["salt"], row["password_hash"]):
        # Başarısız deneme sayısını artır
        conn.execute(
            "UPDATE users SET failed_attempts=failed_attempts+1 WHERE id=?",
            (row["id"],)
        )
        conn.commit()
        conn.close()
        record_login_attempt(ip, email, False)
        # Çok fazla deneme → güvenlik maili
        failed = sum(1 for t, s in login_attempts[ip] if not s)
        if failed >= 3 and RESEND_API_KEY:
            asyncio.create_task(send_security_alert(email, "Başarısız giriş denemeleri", ip))
        raise HTTPException(401, "E-posta veya şifre hatalı")
    # Başarılı giriş
    conn.execute(
        "UPDATE users SET last_login=datetime('now'), last_ip=?, failed_attempts=0, login_count=login_count+1 WHERE id=?",
        (ip, row["id"])
    )
    conn.commit()
    conn.close()
    record_login_attempt(ip, email, True)
    log_security("LOGIN_SUCCESS", ip, f"Email: {email}")
    token = create_token(row["id"], row["email"], row["name"], row["plan"], ip)
    return {
        "token": token,
        "user": {"id": row["id"], "name": row["name"], "email": row["email"],
                 "plan": row["plan"], "verified": row["verified"]}
    }
@app.post("/api/auth/logout")
async def logout(authorization: Optional[str] = Header(None)):
    """Oturumu sonlandır"""
    if authorization:
        token = authorization.replace("Bearer ", "")
        invalidate_token(token)
    return {"success": True}
@app.post("/api/auth/logout-all")
async def logout_all(authorization: Optional[str] = Header(None)):
    """Tüm oturumları sonlandır"""
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giriş yapın")
    invalidate_all_sessions(user["id"])
    log_security("LOGOUT_ALL", "api", f"UID: {user['id']}")
    return {"success": True, "message": "Tüm oturumlar sonlandırıldı"}
@app.get("/api/auth/me")
async def get_me(authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giriş yapın")
    used = get_usage(user["id"])
    limit = PLAN_LIMITS.get(user["plan"], 5)
    stats = get_user_stats(user["id"])
    return {
        "user": user,
        "usage": {"used": used, "limit": limit, "remaining": max(0, limit - used)},
        "stats": stats
    }
@app.get("/api/auth/sessions")
async def get_sessions(authorization: Optional[str] = Header(None)):
    """Aktif oturumları listele"""
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giriş yapın")
    conn = get_db()
    rows = conn.execute(
        "SELECT id, ip_address, created_at, last_active FROM active_sessions "
        "WHERE user_id=? AND expires_at > datetime('now') ORDER BY last_active DESC",
        (user["id"],)
    ).fetchall()
    conn.close()
    return {"sessions": [dict(r) for r in rows]}
@app.get("/api/auth/verify")
async def verify_email_endpoint(token: str = ""):
    if not token:
        raise HTTPException(400, "Geçersiz link")
    conn = get_db()
    row = conn.execute("SELECT id,email FROM users WHERE verify_token=?", (token,)).fetchone()
    if not row:
        conn.close()
        return HTMLResponse("""
        <html><body style="background:#04080a;color:#ff4466;font-family:Arial;display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column">
        <h2>Geçersiz veya süresi dolmuş link</h2>
        <a href="/app" style="color:#00e5ff;margin-top:16px">Uygulamaya Dön</a>
        </body></html>
        """)
    conn.execute("UPDATE users SET verified=1, verify_token=NULL WHERE id=?", (row["id"],))
    conn.commit()
    conn.close()
    log_security("EMAIL_VERIFIED", "web", f"Email: {row['email']}")
    return HTMLResponse("""
    <html><body style="background:#04080a;color:#00ff9d;font-family:Arial;display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column">
    <h2>Hesabınız doğrulandı!</h2>
    <p style="color:#c8dde5;margin-top:8px">Artık PrintForge'u kullanabilirsiniz.</p>
    <a href="/app" style="color:#00e5ff;margin-top:16px;font-size:18px">Uygulamaya Git</a>
    </body></html>
    """)
@app.post("/api/auth/resend-verification")
async def resend_verification(authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giriş yapın")
    if user.get("verified") == 1:
        return {"message": "Hesabınız zaten doğrulanmış"}
    if not RESEND_API_KEY:
        raise HTTPException(400, "E-posta servisi yapılandırılmamış")
    new_token = secrets.token_urlsafe(32)
    conn = get_db()
    conn.execute("UPDATE users SET verify_token=? WHERE id=?", (new_token, user["id"]))
    conn.commit()
    conn.close()
    await send_verification_email(user["email"], new_token)
    return {"message": "Doğrulama maili tekrar gönderildi"}
@app.post("/api/auth/forgot-password")
async def forgot_password(req: ForgotPasswordReq, request: Request):
    ip = get_client_ip(request)
    if not RESEND_API_KEY:
        raise HTTPException(400, "E-posta servisi yapılandırılmamış")
    email = sanitize_email(req.email)
    conn = get_db()
    row = conn.execute("SELECT id,email FROM users WHERE email=?", (email,)).fetchone()
    if not row:
        conn.close()
        return {"message": "Eğer bu e-posta kayıtlıysa sıfırlama maili gönderildi"}
    reset_token = secrets.token_urlsafe(32)
    expires = (datetime.utcnow() + timedelta(hours=1)).isoformat()
    conn.execute("UPDATE users SET reset_token=?, reset_expires=? WHERE id=?", (reset_token, expires, row["id"]))
    conn.commit()
    conn.close()
    log_security("PASSWORD_RESET_REQUEST", ip, f"Email: {email}")
    await send_reset_email(row["email"], reset_token)
    return {"message": "Şifre sıfırlama maili gönderildi. E-postanızı kontrol edin."}
@app.post("/api/auth/reset-password")
async def reset_password(req: ResetPasswordReq, request: Request):
    ip = get_client_ip(request)
    pw_ok, pw_msg = validate_password_strength(req.password)
    if not pw_ok:
        raise HTTPException(400, pw_msg)
    conn = get_db()
    row = conn.execute("SELECT id,email,reset_expires FROM users WHERE reset_token=?", (req.token,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(400, "Geçersiz veya süresi dolmuş link")
    if row["reset_expires"]:
        try:
            expires = datetime.fromisoformat(row["reset_expires"])
            if datetime.utcnow() > expires:
                conn.close()
                raise HTTPException(400, "Sıfırlama linkinin süresi dolmuş. Yeni link talep edin.")
        except:
            pass
    salt, h = hash_pw(req.password)
    conn.execute(
        "UPDATE users SET password_hash=?, salt=?, reset_token=NULL, reset_expires=NULL, updated_at=datetime('now') WHERE id=?",
        (h, salt, row["id"])
    )
    conn.commit()
    conn.close()
    # Tüm eski oturumları sonlandır
    invalidate_all_sessions(row["id"])
    log_security("PASSWORD_RESET_SUCCESS", ip, f"Email: {row['email']}")
    return {"message": "Şifreniz başarıyla değiştirildi. Giriş yapabilirsiniz."}
@app.post("/api/auth/update-profile")
async def update_profile(req: UpdateProfileReq, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giriş yapın")
    conn = get_db()
    if req.name:
        name = sanitize_name(req.name)
        if len(name) >= 2:
            conn.execute("UPDATE users SET name=?, updated_at=datetime('now') WHERE id=?", (name, user["id"]))
    if req.password:
        pw_ok, pw_msg = validate_password_strength(req.password)
        if not pw_ok:
            conn.close()
            raise HTTPException(400, pw_msg)
        salt, h = hash_pw(req.password)
        conn.execute(
            "UPDATE users SET password_hash=?, salt=?, updated_at=datetime('now') WHERE id=?",
            (h, salt, user["id"])
        )
    conn.commit()
    conn.close()
    return {"success": True}
# ════════ KVKK / VERİ KORUMA ════════
@app.post("/api/privacy/export-data")
async def export_user_data(authorization: Optional[str] = Header(None)):
    """KVKK - Kullanıcının tüm verilerini dışa aktar"""
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giriş yapın")
    conn = get_db()
    # Kullanıcı bilgileri
    user_data = dict(conn.execute(
        "SELECT id,email,name,plan,verified,created_at FROM users WHERE id=?",
        (user["id"],)
    ).fetchone())
    # Modelleri
    models = [dict(r) for r in conn.execute(
        "SELECT id,task_id,title,prompt,gen_type,style,likes,downloads,created_at FROM models WHERE user_id=?",
        (user["id"],)
    ).fetchall()]
    # Kullanım
    usage = [dict(r) for r in conn.execute(
        "SELECT month,count FROM usage WHERE user_id=?",
        (user["id"],)
    ).fetchall()]
    # Beğeniler
    likes = [dict(r) for r in conn.execute(
        "SELECT model_id FROM user_likes WHERE user_id=?",
        (user["id"],)
    ).fetchall()]
    conn.close()
    export = {
        "export_date": datetime.now().isoformat(),
        "user": user_data,
        "models": models,
        "usage": usage,
        "likes": likes,
        "info": "Bu dosya KVKK kapsamında kişisel verilerinizi içerir."
    }
    log_security("DATA_EXPORT", "api", f"UID: {user['id']}")
    return Response(
        content=json.dumps(export, indent=2, default=str, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="printforge_data_{user["id"]}.json"'}
    )
@app.post("/api/privacy/delete-account")
async def delete_account(req: DeleteAccountReq, authorization: Optional[str] = Header(None)):
    """KVKK - Hesabı ve tüm verileri sil"""
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giriş yapın")
    # Şifre doğrulama
    conn = get_db()
    row = conn.execute(
        "SELECT password_hash, salt FROM users WHERE id=?",
        (user["id"],)
    ).fetchone()
    if not row or not verify_pw(req.password, row["salt"], row["password_hash"]):
        conn.close()
        raise HTTPException(403, "Şifre hatalı")
    # Tüm verileri sil
    conn.execute("DELETE FROM user_likes WHERE user_id=?", (user["id"],))
    conn.execute("DELETE FROM models WHERE user_id=?", (user["id"],))
    conn.execute("DELETE FROM usage WHERE user_id=?", (user["id"],))
    conn.execute("DELETE FROM active_sessions WHERE user_id=?", (user["id"],))
    conn.execute("DELETE FROM users WHERE id=?", (user["id"],))
    conn.commit()
    conn.close()
    log_security("ACCOUNT_DELETED", "api", f"UID: {user['id']}, Email: {user['email']}")
    return {"success": True, "message": "Hesabınız ve tüm verileriniz kalıcı olarak silindi."}
@app.get("/api/privacy/policy")
async def privacy_policy():
    """Gizlilik politikası özeti"""
    return {
        "platform": "PrintForge",
        "data_collected": [
            "E-posta adresi ve isim (kayıt için)",
            "Oluşturulan 3D modeller ve promptlar",
            "Kullanım istatistikleri",
            "IP adresi (güvenlik için)"
        ],
        "data_retention": "Hesap silinene kadar",
        "data_sharing": "Üçüncü taraflarla paylaşılmaz",
        "user_rights": [
            "Verilerinizi dışa aktarabilirsiniz (GET /api/privacy/export-data)",
            "Hesabınızı ve tüm verilerinizi silebilirsiniz (POST /api/privacy/delete-account)",
            "E-posta tercihlerinizi değiştirebilirsiniz"
        ],
        "security_measures": [
            "PBKDF2-SHA512 ile şifre hashleme (310.000 iterasyon)",
            "JWT tabanlı oturum yönetimi",
            "Rate limiting ve brute force koruması",
            "HTTPS zorunluluğu",
            "Güvenlik olayı loglama"
        ],
        "contact": "Veri sorumlusu ile iletişim: destek@printforge.app"
    }
# ════════ GOOGLE LOGIN ════════
@app.get("/api/auth/google")
async def google_login():
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(400, "Google login yapılandırılmamış")
    redirect_uri = f"{get_site_url()}/api/auth/google/callback"
    state = secrets.token_urlsafe(32)
    params = urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
        "state": state,
    })
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")
@app.get("/api/auth/google/callback")
async def google_callback(code: str = "", request: Request = None):
    if not code:
        raise HTTPException(400, "Google login başarısız")
    ip = get_client_ip(request) if request else "unknown"
    redirect_uri = f"{get_site_url()}/api/auth/google/callback"
    async with httpx.AsyncClient() as client:
        tr = await client.post("https://oauth2.googleapis.com/token", data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        })
        if tr.status_code != 200:
            raise HTTPException(400, "Google token alınamadı")
        at = tr.json().get("access_token")
        ur = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {at}"}
        )
        if ur.status_code != 200:
            raise HTTPException(400, "Google bilgi alınamadı")
        gu = ur.json()
    email = sanitize_email(gu.get("email", ""))
    name = sanitize_name(gu.get("name", email.split("@")[0]))
    gid = gu.get("id", "")
    avatar = gu.get("picture", "")
    conn = get_db()
    ex = conn.execute("SELECT id,name,plan FROM users WHERE email=?", (email,)).fetchone()
    if ex:
        uid, name, plan = ex["id"], ex["name"], ex["plan"]
        conn.execute(
            "UPDATE users SET google_id=?,avatar_url=?,verified=1,last_login=datetime('now'),last_ip=? WHERE id=?",
            (gid, avatar, ip, uid)
        )
    else:
        salt, h = hash_pw(secrets.token_hex(16))
        conn.execute(
            "INSERT INTO users(email,name,password_hash,salt,google_id,avatar_url,verified,last_ip) VALUES(?,?,?,?,?,?,1,?)",
            (email, name, h, salt, gid, avatar, ip)
        )
        uid = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()[0]
        plan = "free"
    conn.commit()
    conn.close()
    log_security("GOOGLE_LOGIN", ip, f"Email: {email}")
    jwt_token = create_token(uid, email, name, plan, ip)
    return HTMLResponse(
        "<html><head><script>"
        "localStorage.setItem('pf_token','" + jwt_token + "');"
        "window.location.href='/app';"
        "</script></head><body style='background:#04080a;color:#00e5ff;"
        "display:flex;align-items:center;justify-content:center;height:100vh'>"
        "Giriş yapılıyor...</body></html>"
    )
# ════════ MODEL ÜRETİMİ (GÜVENLİ) ════════
@app.post("/api/generate/text")
async def generate_text(req: TextRequest, request: Request, authorization: Optional[str] = Header(None)):
    api = get_api()
    user = await get_user(authorization)
    ip = get_client_ip(request)
    if api != "demo":
        if not user:
            raise HTTPException(401, "Model üretmek için giriş yapın")
        if not check_generate_rate(ip):
            raise HTTPException(429, "Çok sık model üretiyorsunuz. Lütfen bekleyin.")
        used = get_usage(user["id"])
        limit = PLAN_LIMITS.get(user["plan"], 5)
        if used >= limit:
            raise HTTPException(403, f"Aylık limitinize ulaştınız ({limit} model). Planınızı yükseltin.")
        add_usage(user["id"])
    # Prompt temizleme
    prompt = sanitize_prompt(req.prompt)
    if not prompt or len(prompt) < 3:
        raise HTTPException(400, "Prompt çok kısa (en az 3 karakter)")
    style = req.style if req.style in STYLE_MAP else "realistic"
    tid = str(uuid.uuid4())[:8]
    tasks[tid] = {
        "status": "processing", "progress": 0, "step": "Başlatılıyor...",
        "type": "text", "api": api, "prompt": prompt, "style": style,
        "user_id": user["id"] if user else 0
    }
    if api == "tripo":
        asyncio.create_task(_tripo_text(tid, prompt, style))
    elif api == "meshy":
        asyncio.create_task(_meshy_text(tid, prompt, style))
    else:
        asyncio.create_task(_demo_generate(tid))
    log_security("GENERATE_TEXT", ip, f"UID: {user['id'] if user else 0}, Prompt: {prompt[:50]}")
    return {"task_id": tid}
@app.post("/api/generate/image")
async def generate_image(request: Request, file: UploadFile = File(...), authorization: Optional[str] = Header(None)):
    api = get_api()
    user = await get_user(authorization)
    ip = get_client_ip(request)
    if api != "demo":
        if not user:
            raise HTTPException(401, "Model üretmek için giriş yapın")
        if not check_generate_rate(ip):
            raise HTTPException(429, "Çok sık model üretiyorsunuz. Lütfen bekleyin.")
        used = get_usage(user["id"])
        limit = PLAN_LIMITS.get(user["plan"], 5)
        if used >= limit:
            raise HTTPException(403, f"Aylık limitinize ulaştınız ({limit} model). Planınızı yükseltin.")
        add_usage(user["id"])
    contents = await file.read()
    fname = sanitize_input(file.filename or "image.jpg", 100)
    # Güvenli dosya doğrulama
    valid, result = validate_file(contents, fname)
    if not valid:
        raise HTTPException(400, result)
    tid = str(uuid.uuid4())[:8]
    tasks[tid] = {
        "status": "processing", "progress": 0, "step": "Görsel hazırlanıyor...",
        "type": "image", "api": api, "prompt": fname, "style": "",
        "user_id": user["id"] if user else 0
    }
    if api == "tripo":
        asyncio.create_task(_tripo_image(tid, contents, fname))
    elif api == "meshy":
        asyncio.create_task(_meshy_image(tid, contents, fname))
    else:
        asyncio.create_task(_demo_generate(tid))
    log_security("GENERATE_IMAGE", ip, f"UID: {user['id'] if user else 0}, File: {fname}")
    return {"task_id": tid}
@app.get("/api/status/{task_id}")
async def get_status(task_id: str):
    task_id = sanitize_input(task_id, 20)
    if task_id not in tasks:
        raise HTTPException(404, "Görev bulunamadı")
    t = tasks[task_id]
    return {
        "task_id": task_id, "status": t["status"], "progress": t["progress"],
        "step": t.get("step", ""), "model_url": t.get("model_url", ""),
        "is_demo": t.get("api") == "demo", "cached": task_id in model_cache,
        "error": t.get("error", ""),
    }
# ════════ MODEL SUNMA ════════
async def cache_model(tid, url):
    if tid in model_cache:
        return True
    while len(model_cache) >= MAX_CACHE:
        del model_cache[next(iter(model_cache))]
    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
            r = await c.get(url)
            if r.status_code == 200 and len(r.content) > 100:
                model_cache[tid] = r.content
                return True
    except:
        pass
    return False
async def ensure_cached(tid):
    if tid in model_cache:
        return True
    if tid in tasks and tasks[tid].get("model_url"):
        return await cache_model(tid, tasks[tid]["model_url"])
    return False
@app.get("/api/model/{task_id}/view")
async def model_view(task_id: str):
    task_id = sanitize_input(task_id, 20)
    if not await ensure_cached(task_id):
        raise HTTPException(404, "Model bulunamadı")
    return Response(
        content=model_cache[task_id],
        media_type="model/gltf-binary",
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=3600"}
    )
@app.get("/api/model/{task_id}/glb")
async def download_glb(task_id: str):
    task_id = sanitize_input(task_id, 20)
    if not await ensure_cached(task_id):
        raise HTTPException(404, "Model bulunamadı")
    conn = get_db()
    conn.execute("UPDATE models SET downloads=downloads+1 WHERE task_id=?", (task_id,))
    conn.commit()
    conn.close()
    return Response(
        content=model_cache[task_id],
        media_type="model/gltf-binary",
        headers={"Content-Disposition": f'attachment; filename="printforge_{task_id}.glb"'}
    )
@app.get("/api/model/{task_id}/stl")
async def download_stl(task_id: str):
    task_id = sanitize_input(task_id, 20)
    if not HAS_TRIMESH:
        raise HTTPException(500, "STL dönüştürme yüklü değil")
    if not await ensure_cached(task_id):
        raise HTTPException(404, "Model bulunamadı")
    try:
        scene = trimesh.load(io.BytesIO(model_cache[task_id]), file_type="glb", force="scene")
        if isinstance(scene, trimesh.Scene):
            meshes = [g for g in scene.geometry.values() if isinstance(g, trimesh.Trimesh)]
        else:
            meshes = [scene]
        if not meshes:
            raise Exception("Mesh bulunamadı")
        stl = trimesh.util.concatenate(meshes).export(file_type="stl")
        conn = get_db()
        conn.execute("UPDATE models SET downloads=downloads+1 WHERE task_id=?", (task_id,))
        conn.commit()
        conn.close()
        return Response(
            content=stl,
            media_type="application/vnd.ms-pki.stl",
            headers={"Content-Disposition": f'attachment; filename="printforge_{task_id}.stl"'}
        )
    except Exception as e:
        raise HTTPException(500, f"STL hatası: {e}")
@app.get("/api/model/{task_id}/obj")
async def download_obj(task_id: str):
    task_id = sanitize_input(task_id, 20)
    if not HAS_TRIMESH:
        raise HTTPException(500, "OBJ dönüştürme yüklü değil")
    if not await ensure_cached(task_id):
        raise HTTPException(404, "Model bulunamadı")
    try:
        scene = trimesh.load(io.BytesIO(model_cache[task_id]), file_type="glb", force="scene")
        if isinstance(scene, trimesh.Scene):
            meshes = [g for g in scene.geometry.values() if isinstance(g, trimesh.Trimesh)]
        else:
            meshes = [scene]
        obj = trimesh.util.concatenate(meshes).export(file_type="obj")
        return Response(
            content=obj,
            media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="printforge_{task_id}.obj"'}
        )
    except Exception as e:
        raise HTTPException(500, f"OBJ hatası: {e}")
# ════════ GALERİ ════════
@app.get("/api/gallery")
async def gallery(page: int = 1, limit: int = 20, sort: str = "newest", search: str = ""):
    limit = min(limit, 50)
    page = max(1, page)
    search = sanitize_input(search, 100)
    conn = get_db()
    offset = (page - 1) * limit
    where = "WHERE is_public=1 AND model_url != ''"
    params = []
    if search:
        where += " AND (title LIKE ? OR prompt LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    order = {"popular": "ORDER BY likes DESC", "downloads": "ORDER BY downloads DESC"}.get(sort, "ORDER BY created_at DESC")
    rows = conn.execute(
        f"SELECT m.*, u.name as author_name FROM models m LEFT JOIN users u ON m.user_id=u.id {where} {order} LIMIT ? OFFSET ?",
        params + [limit, offset]
    ).fetchall()
    total = conn.execute(f"SELECT COUNT(*) FROM models {where}", params).fetchone()[0]
    conn.close()
    return {"models": [dict(r) for r in rows], "total": total, "page": page, "pages": max(1, (total + limit - 1) // limit)}
@app.get("/api/gallery/{model_id}")
async def model_detail(model_id: int):
    conn = get_db()
    row = conn.execute(
        "SELECT m.*, u.name as author_name FROM models m LEFT JOIN users u ON m.user_id=u.id WHERE m.id=?",
        (model_id,)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Model bulunamadı")
    return dict(row)
@app.get("/api/gallery/{model_id}/similar")
async def similar_models(model_id: int, limit: int = 6):
    limit = min(limit, 20)
    conn = get_db()
    cur = conn.execute("SELECT style, gen_type FROM models WHERE id=?", (model_id,)).fetchone()
    if not cur:
        conn.close()
        raise HTTPException(404, "Bulunamadı")
    style = cur["style"] or ""
    gtype = cur["gen_type"] or ""
    rows = conn.execute(
        "SELECT m.*, u.name as author_name FROM models m LEFT JOIN users u ON m.user_id=u.id "
        "WHERE m.id!=? AND m.is_public=1 AND m.model_url!='' AND (m.style=? OR m.gen_type=?) "
        "ORDER BY m.likes DESC LIMIT ?",
        (model_id, style, gtype, limit)
    ).fetchall()
    if len(rows) < limit:
        extra = conn.execute(
            "SELECT m.*, u.name as author_name FROM models m LEFT JOIN users u ON m.user_id=u.id "
            "WHERE m.id!=? AND m.is_public=1 AND m.model_url!='' ORDER BY RANDOM() LIMIT ?",
            (model_id, limit - len(rows))
        ).fetchall()
        rows = list(rows) + list(extra)
    conn.close()
    return {"models": [dict(r) for r in rows]}
@app.post("/api/gallery/{model_id}/like")
async def toggle_like(model_id: int, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giriş yapın")
    conn = get_db()
    ex = conn.execute("SELECT 1 FROM user_likes WHERE user_id=? AND model_id=?", (user["id"], model_id)).fetchone()
    if ex:
        conn.execute("DELETE FROM user_likes WHERE user_id=? AND model_id=?", (user["id"], model_id))
        conn.execute("UPDATE models SET likes=MAX(0,likes-1) WHERE id=?", (model_id,))
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
        raise HTTPException(401, "Giriş yapın")
    conn = get_db()
    rows = conn.execute("SELECT * FROM models WHERE user_id=? ORDER BY created_at DESC", (user["id"],)).fetchall()
    conn.close()
    return {"models": [dict(r) for r in rows]}
@app.delete("/api/my-models/{model_id}")
async def delete_model(model_id: int, authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giriş yapın")
    conn = get_db()
    conn.execute("DELETE FROM models WHERE id=? AND user_id=?", (model_id, user["id"]))
    conn.commit()
    conn.close()
    return {"deleted": True}
@app.post("/api/payment/upgrade")
async def upgrade_plan(authorization: Optional[str] = Header(None)):
    user = await get_user(authorization)
    if not user:
        raise HTTPException(401, "Giriş yapın")
    conn = get_db()
    conn.execute("UPDATE users SET plan='pro', updated_at=datetime('now') WHERE id=?", (user["id"],))
    conn.commit()
    conn.close()
    return {"success": True, "plan": "pro"}
@app.get("/api/health")
async def health():
    api = get_api()
    return {
        "status": "online",
        "active_api": api,
        "api_ready": True,
        "is_demo": api == "demo",
        "stl_ready": HAS_TRIMESH,
        "auth_ready": HAS_JWT,
        "google_ready": bool(GOOGLE_CLIENT_ID),
        "email_ready": bool(RESEND_API_KEY),
        "cached_models": len(model_cache),
        "security": {
            "rate_limiting": True,
            "pbkdf2_hashing": True,
            "session_management": True,
            "security_headers": True,
            "input_sanitization": True,
            "file_validation": True,
        }
    }
# ════════ GÜVENLİK ADMIN ════════
@app.get("/api/security/logs")
async def security_logs(authorization: Optional[str] = Header(None), limit: int = 50):
    """Güvenlik loglarını görüntüle (sadece admin)"""
    user = await get_user(authorization)
    if not user or user["plan"] != "business":
        raise HTTPException(403, "Yetkiniz yok")
    limit = min(limit, 200)
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM security_logs ORDER BY created_at DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return {"logs": [dict(r) for r in rows]}
@app.get("/api/security/stats")
async def security_stats(authorization: Optional[str] = Header(None)):
    """Güvenlik istatistikleri"""
    user = await get_user(authorization)
    if not user or user["plan"] != "business":
        raise HTTPException(403, "Yetkiniz yok")
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM security_logs").fetchone()[0]
    failed_logins = conn.execute(
        "SELECT COUNT(*) FROM security_logs WHERE event_type='LOGIN_FAILED' AND created_at > datetime('now','-24 hours')"
    ).fetchone()[0]
    registrations = conn.execute(
        "SELECT COUNT(*) FROM security_logs WHERE event_type='REGISTER_SUCCESS' AND created_at > datetime('now','-24 hours')"
    ).fetchone()[0]
    active = conn.execute(
        "SELECT COUNT(*) FROM active_sessions WHERE expires_at > datetime('now')"
    ).fetchone()[0]
    conn.close()
    return {
        "total_events": total,
        "failed_logins_24h": failed_logins,
        "new_registrations_24h": registrations,
        "active_sessions": active,
        "blocked_ips": len(blocked_ips),
        "locked_accounts": len(account_lockouts),
    }
# ════════ URL ÇIKARMA ════════
def extract_model_url(data):
    if not data:
        return ""
    if isinstance(data, str) and data.startswith("http"):
        return data
    if not isinstance(data, dict):
        return ""
    for key in ["model", "pbr_model", "base_model"]:
        val = data.get(key, "")
        if isinstance(val, str) and val.startswith("http"):
            return val
        if isinstance(val, dict):
            url = val.get("url", "") or val.get("download_url", "")
            if url and url.startswith("http"):
                return url
    for k, v in data.items():
        if isinstance(v, str) and v.startswith("http"):
            if any(x in v.lower() for x in [".glb", ".gltf", "model"]):
                return v
    return ""
# ════════ TRIPO3D ════════
async def _tripo_text(tid, prompt, style):
    try:
        h = {"Authorization": f"Bearer {TRIPO_API_KEY}"}
        tasks[tid]["progress"] = 10
        tasks[tid]["step"] = "Prompt gönderiliyor..."
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.post(
                f"{TRIPO_BASE}/task",
                json={"type": "text_to_model", "prompt": f"{prompt}, {style} style"},
                headers={**h, "Content-Type": "application/json"}
            )
            if r.status_code != 200:
                raise Exception(f"Tripo hata {r.status_code}")
            tripo_id = r.json().get("data", {}).get("task_id")
            if not tripo_id:
                raise Exception("Task ID alınamadı")
            tasks[tid]["progress"] = 25
            await _tripo_poll(c, h, tid, tripo_id)
    except Exception as e:
        tasks[tid]["status"] = "failed"
        tasks[tid]["error"] = str(e)
async def _tripo_image(tid, contents, fname):
    try:
        h = {"Authorization": f"Bearer {TRIPO_API_KEY}"}
        ext = fname.rsplit(".", 1)[-1].lower()
        if ext not in ("jpg", "jpeg", "png", "webp"):
            ext = "jpeg"
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        tasks[tid]["progress"] = 10
        tasks[tid]["step"] = "Görsel yükleniyor..."
        async with httpx.AsyncClient(timeout=600) as c:
            ur = await c.post(f"{TRIPO_BASE}/upload", files={"file": (fname, contents, mime)}, headers=h)
            if ur.status_code != 200:
                raise Exception(f"Upload hata {ur.status_code}")
            token = ur.json().get("data", {}).get("image_token")
            if not token:
                raise Exception("Token alınamadı")
            tasks[tid]["progress"] = 25
            tasks[tid]["step"] = "Model oluşturuluyor..."
            tr = await c.post(
                f"{TRIPO_BASE}/task",
                json={"type": "image_to_model", "file": {"type": ext if ext != "jpg" else "jpeg", "file_token": token}},
                headers={**h, "Content-Type": "application/json"}
            )
            if tr.status_code != 200:
                raise Exception(f"Task hata {tr.status_code}")
            tripo_id = tr.json().get("data", {}).get("task_id")
            if not tripo_id:
                raise Exception("Task ID alınamadı")
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
            tasks[tid]["step"] = f"Model üretiliyor... %{pr}"
            if st == "success":
                url = extract_model_url(d.get("output", {}))
                tasks[tid]["model_url"] = url
                tasks[tid]["progress"] = 92
                tasks[tid]["step"] = "Model indiriliyor..."
                if url:
                    await cache_model(tid, url)
                tasks[tid]["status"] = "done"
                tasks[tid]["progress"] = 100
                tasks[tid]["step"] = "Tamamlandı!"
                uid = tasks[tid].get("user_id", 0)
                prompt = tasks[tid].get("prompt", "")
                save_model(uid, tid, prompt[:50], prompt, tasks[tid].get("type", ""), tasks[tid].get("style", ""), url)
                return
            elif st in ("failed", "cancelled"):
                raise Exception(f"Tripo: {st}")
        except Exception as e:
            if any(x in str(e) for x in ["Tripo", "failed", "cancelled"]):
                tasks[tid]["status"] = "failed"
                tasks[tid]["error"] = str(e)
                return
    tasks[tid]["status"] = "failed"
    tasks[tid]["error"] = "Zaman aşımı"
# ════════ MESHY ════════
async def _meshy_text(tid, prompt, style):
    try:
        h = {"Authorization": f"Bearer {MESHY_API_KEY}", "Content-Type": "application/json"}
        tasks[tid]["progress"] = 10
        tasks[tid]["step"] = "Prompt gönderiliyor..."
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.post(
                f"{MESHY_BASE}/text-to-3d",
                json={"mode": "preview", "prompt": prompt, "art_style": "realistic"},
                headers=h
            )
            if r.status_code not in (200, 202):
                raise Exception(f"Meshy hata {r.status_code}")
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
        tasks[tid]["step"] = "Görsel gönderiliyor..."
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.post(
                f"{MESHY_BASE}/image-to-3d",
                json={"image_url": f"data:{mime};base64,{b64}", "enable_pbr": True},
                headers=h
            )
            if r.status_code not in (200, 202):
                raise Exception(f"Meshy hata {r.status_code}")
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
            tasks[tid]["step"] = f"Model üretiliyor... %{progress}"
            if status == "SUCCEEDED":
                glb = d.get("model_urls", {}).get("glb", "")
                tasks[tid]["model_url"] = glb
                if glb:
                    await cache_model(tid, glb)
                tasks[tid]["status"] = "done"
                tasks[tid]["progress"] = 100
                tasks[tid]["step"] = "Tamamlandı!"
                uid = tasks[tid].get("user_id", 0)
                prompt = tasks[tid].get("prompt", "")
                save_model(uid, tid, prompt[:50], prompt, tasks[tid].get("type", ""), "", glb)
                return
            elif status == "FAILED":
                raise Exception("Meshy: Model üretilemedi")
        except Exception as e:
            if "üretilemedi" in str(e):
                tasks[tid]["status"] = "failed"
                tasks[tid]["error"] = str(e)
                return
    tasks[tid]["status"] = "failed"
    tasks[tid]["error"] = "Zaman aşımı"
# ════════ DEMO ════════
async def _demo_generate(tid):
    try:
        steps = [
            (8, "Analiz ediliyor..."),
            (22, "AI yükleniyor..."),
            (40, "Geometri oluşturuluyor..."),
            (58, "Mesh üretiliyor..."),
            (72, "Texture uygulanıyor..."),
            (88, "Optimize ediliyor..."),
            (95, "Hazırlanıyor..."),
        ]
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
# ════════ PERIYODIK TEMIZLIK ════════
@app.on_event("startup")
async def cleanup_scheduler():
    """Eski verileri düzenli temizle"""
    async def periodic_cleanup():
        while True:
            await asyncio.sleep(3600)  # Her saat
            try:
                conn = get_db()
                # Süresi dolmuş oturumları temizle
                conn.execute("DELETE FROM active_sessions WHERE expires_at < datetime('now')")
                # 30 günden eski güvenlik loglarını temizle
                conn.execute("DELETE FROM security_logs WHERE created_at < datetime('now','-30 days')")
                conn.commit()
                conn.close()
                # Bellek temizliği
                now = time.time()
                for key in list(rate_limits.keys()):
                    rate_limits[key] = [t for t in rate_limits[key] if now - t < 120]
                    if not rate_limits[key]:
                        del rate_limits[key]
                print(f"[CLEANUP] {datetime.now().strftime('%H:%M')} - Sessions/logs temizlendi")
            except Exception as e:
                print(f"[CLEANUP] Hata: {e}")
    asyncio.create_task(periodic_cleanup())
