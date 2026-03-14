from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from pydantic import BaseModel
import asyncio
import uuid
import httpx
import base64
import os
import io

# ══════════════════════════════════════
#         UYGULAMA AYARLARI
# ══════════════════════════════════════
app = FastAPI(title="PrintForge Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Anahtarları (Railway'de Environment Variable olarak ayarlanacak)
MESHY_API_KEY = os.getenv("MESHY_API_KEY", "")
TRIPO_API_KEY = os.getenv("TRIPO_API_KEY", "")

MESHY_BASE = "https://api.meshy.ai/openapi/v2"
TRIPO_BASE = "https://api.tripo3d.ai/v2/openapi"

# Görev deposu (bellekte tutuluyor)
tasks = {}

# Stil eşleştirme (Meshy API için)
STYLE_MAP = {
    "realistic": "realistic",
    "cartoon": "cartoon",
    "lowpoly": "low-poly",
    "sculpture": "sculpture",
    "mechanical": "pbr",
    "miniature": "sculpture",
    "geometric": "realistic",
}

class TextRequest(BaseModel):
    prompt: str
    style: str = "realistic"


# ══════════════════════════════════════
#         SAYFA SERVİSLERİ
# ══════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
def serve_landing():
    """Ana sayfa - Landing Page"""
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(html_path):
        return FileResponse(html_path, media_type="text/html")
    return HTMLResponse("<h1>PrintForge</h1><p><a href='/app'>Uygulamaya Git</a></p>")


@app.get("/app", response_class=HTMLResponse)
def serve_app():
    """3D Model Üretme Uygulaması"""
    return HTMLResponse(APP_HTML)


# ══════════════════════════════════════
#         SAĞLIK & BİLGİ
# ══════════════════════════════════════

@app.get("/api/health")
async def health():
    api = "meshy" if MESHY_API_KEY else ("tripo" if TRIPO_API_KEY else "none")
    return {
        "status": "online",
        "active_api": api,
        "api_ready": api != "none",
        "active_tasks": len([t for t in tasks.values() if t["status"] == "processing"]),
    }


@app.get("/api/balance")
async def balance():
    """API bakiyesini kontrol et"""
    results = {}
    async with httpx.AsyncClient(timeout=15) as client:
        if MESHY_API_KEY:
            try:
                r = await client.get(f"{MESHY_BASE}/me",
                    headers={"Authorization": f"Bearer {MESHY_API_KEY}"})
                results["meshy"] = r.json()
            except:
                results["meshy"] = {"error": "Bağlantı hatası"}

        if TRIPO_API_KEY:
            try:
                r = await client.get(f"{TRIPO_BASE}/user/balance",
                    headers={"Authorization": f"Bearer {TRIPO_API_KEY}"})
                results["tripo"] = r.json()
            except:
                results["tripo"] = {"error": "Bağlantı hatası"}

    return results if results else {"error": "API key ayarlanmamış"}


# ══════════════════════════════════════
#         3D MODEL ÜRETİMİ
# ══════════════════════════════════════

def get_api():
    """Hangi API kullanılacak"""
    if MESHY_API_KEY:
        return "meshy"
    if TRIPO_API_KEY:
        return "tripo"
    return None


@app.post("/api/generate/text")
async def generate_text(req: TextRequest):
    """Metin prompt'undan 3D model üret"""
    api = get_api()
    if not api:
        raise HTTPException(400, "API key ayarlanmamış! Railway'de MESHY_API_KEY ekleyin.")

    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {
        "status": "processing",
        "progress": 0,
        "step": "Başlatılıyor...",
        "type": "text",
        "api": api,
    }

    if api == "meshy":
        asyncio.create_task(_meshy_text(task_id, req.prompt, req.style))
    else:
        asyncio.create_task(_tripo_text(task_id, req.prompt, req.style))

    return {"task_id": task_id}


@app.post("/api/generate/image")
async def generate_image(file: UploadFile = File(...)):
    """Görselden 3D model üret"""
    api = get_api()
    if not api:
        raise HTTPException(400, "API key ayarlanmamış! Railway'de MESHY_API_KEY ekleyin.")

    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(400, "Dosya çok büyük (max 10MB)")

    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {
        "status": "processing",
        "progress": 0,
        "step": "Görsel hazırlanıyor...",
        "type": "image",
        "api": api,
    }

    filename = file.filename or "image.jpg"

    if api == "meshy":
        asyncio.create_task(_meshy_image(task_id, contents, filename))
    else:
        asyncio.create_task(_tripo_image(task_id, contents, filename))

    return {"task_id": task_id}


@app.get("/api/status/{task_id}")
async def get_status(task_id: str):
    """Görev durumunu sorgula"""
    if task_id not in tasks:
        raise HTTPException(404, "Görev bulunamadı")
    t = tasks[task_id]
    return {
        "task_id": task_id,
        "status": t["status"],
        "progress": t["progress"],
        "step": t.get("step", ""),
        "model_url": t.get("model_url", ""),
        "download_urls": t.get("download_urls", {}),
        "thumbnail": t.get("thumbnail", ""),
        "error": t.get("error", ""),
    }


@app.get("/api/download/{task_id}")
async def download_model(task_id: str):
    """Modeli indir (proxy)"""
    if task_id not in tasks:
        raise HTTPException(404, "Görev bulunamadı")
    t = tasks[task_id]
    if t["status"] != "done":
        raise HTTPException(400, "Model henüz hazır değil")

    url = t.get("model_url", "")
    if not url:
        raise HTTPException(404, "Model URL bulunamadı")

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.get(url)
        if r.status_code != 200:
            raise HTTPException(502, "Model indirilemedi")
        return StreamingResponse(
            io.BytesIO(r.content),
            media_type="model/gltf-binary",
            headers={"Content-Disposition": f'attachment; filename="printforge_{task_id}.glb"'}
        )


# ══════════════════════════════════════
#         MESHY API
# ══════════════════════════════════════

async def _meshy_text(task_id, prompt, style):
    """Meshy ile metin → 3D"""
    try:
        art = STYLE_MAP.get(style, "realistic")
        headers = {
            "Authorization": f"Bearer {MESHY_API_KEY}",
            "Content-Type": "application/json",
        }

        tasks[task_id]["progress"] = 10
        tasks[task_id]["step"] = "Prompt Meshy'ye gönderiliyor..."

        async with httpx.AsyncClient(timeout=600) as client:
            # Görev oluştur
            r = await client.post(f"{MESHY_BASE}/text-to-3d", json={
                "mode": "preview",
                "prompt": prompt,
                "art_style": art,
                "negative_prompt": "low quality, blurry, distorted, ugly",
            }, headers=headers)

            if r.status_code not in (200, 202):
                raise Exception(f"Meshy hata: {r.status_code} - {r.text}")

            meshy_id = r.json().get("result")
            if not meshy_id:
                raise Exception(f"Task ID alınamadı: {r.text}")

            tasks[task_id]["progress"] = 20
            tasks[task_id]["step"] = "3D model üretiliyor..."

            # Sonucu bekle
            await _meshy_poll(client, headers, task_id, meshy_id, "text-to-3d")

    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)
        print(f"[HATA] meshy_text {task_id}: {e}")


async def _meshy_image(task_id, contents, filename):
    """Meshy ile görsel → 3D"""
    try:
        headers = {
            "Authorization": f"Bearer {MESHY_API_KEY}",
            "Content-Type": "application/json",
        }

        tasks[task_id]["progress"] = 10
        tasks[task_id]["step"] = "Görsel hazırlanıyor..."

        # Base64'e çevir
        ext = filename.rsplit(".", 1)[-1].lower()
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        b64 = base64.b64encode(contents).decode()
        data_url = f"data:{mime};base64,{b64}"

        tasks[task_id]["progress"] = 15
        tasks[task_id]["step"] = "Görsel Meshy'ye gönderiliyor..."

        async with httpx.AsyncClient(timeout=600) as client:
            r = await client.post(f"{MESHY_BASE}/image-to-3d", json={
                "image_url": data_url,
                "enable_pbr": True,
            }, headers=headers)

            if r.status_code not in (200, 202):
                raise Exception(f"Meshy hata: {r.status_code} - {r.text}")

            meshy_id = r.json().get("result")
            if not meshy_id:
                raise Exception(f"Task ID alınamadı: {r.text}")

            tasks[task_id]["progress"] = 25
            tasks[task_id]["step"] = "3D model oluşturuluyor..."

            await _meshy_poll(client, headers, task_id, meshy_id, "image-to-3d")

    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)
        print(f"[HATA] meshy_image {task_id}: {e}")


async def _meshy_poll(client, headers, task_id, meshy_id, endpoint):
    """Meshy görevini takip et"""
    for i in range(200):  # max ~10 dakika
        await asyncio.sleep(3)

        try:
            r = await client.get(
                f"{MESHY_BASE}/{endpoint}/{meshy_id}",
                headers=headers,
            )
            if r.status_code != 200:
                continue

            data = r.json()
            status = data.get("status", "")
            progress = data.get("progress", 0)

            tasks[task_id]["progress"] = 25 + int(progress * 0.7)
            tasks[task_id]["step"] = f"Model üretiliyor... %{progress}"

            if status == "SUCCEEDED":
                urls = data.get("model_urls", {})
                glb = urls.get("glb", "")
                tasks[task_id]["status"] = "done"
                tasks[task_id]["progress"] = 100
                tasks[task_id]["step"] = "Tamamlandı!"
                tasks[task_id]["model_url"] = glb
                tasks[task_id]["download_urls"] = urls
                tasks[task_id]["thumbnail"] = data.get("thumbnail_url", "")
                print(f"[OK] {task_id} tamamlandı!")
                return

            elif status == "FAILED":
                raise Exception(data.get("task_error", {}).get("message", "Model üretilemedi"))

        except Exception as e:
            if "üretilemedi" in str(e) or "FAILED" in str(e):
                raise
            print(f"[WARN] poll: {e}")
            continue

    raise Exception("Zaman aşımı - model çok uzun sürdü")


# ══════════════════════════════════════
#         TRIPO API (YEDEK)
# ══════════════════════════════════════

async def _tripo_text(task_id, prompt, style):
    """Tripo3D ile metin → 3D"""
    try:
        headers = {"Authorization": f"Bearer {TRIPO_API_KEY}"}
        tasks[task_id]["progress"] = 10
        tasks[task_id]["step"] = "Prompt gönderiliyor..."

        async with httpx.AsyncClient(timeout=600) as client:
            r = await client.post(f"{TRIPO_BASE}/task", json={
                "type": "text_to_model",
                "prompt": f"{prompt}, {style} style",
            }, headers={**headers, "Content-Type": "application/json"})

            if r.status_code != 200:
                raise Exception(f"Tripo hata: {r.status_code} - {r.text}")

            tripo_id = r.json().get("data", {}).get("task_id")
            if not tripo_id:
                raise Exception(f"Task ID alınamadı: {r.text}")

            tasks[task_id]["progress"] = 25
            await _tripo_poll(client, headers, task_id, tripo_id)

    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)


async def _tripo_image(task_id, contents, filename):
    """Tripo3D ile görsel → 3D"""
    try:
        headers = {"Authorization": f"Bearer {TRIPO_API_KEY}"}
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext not in ("jpg", "jpeg", "png", "webp"):
            ext = "jpeg"
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"

        tasks[task_id]["progress"] = 10
        tasks[task_id]["step"] = "Görsel yükleniyor..."

        async with httpx.AsyncClient(timeout=600) as client:
            ur = await client.post(f"{TRIPO_BASE}/upload",
                files={"file": (filename, contents, mime)}, headers=headers)
            token = ur.json().get("data", {}).get("image_token")
            if not token:
                raise Exception(f"Upload başarısız: {ur.text}")

            tasks[task_id]["progress"] = 20
            tasks[task_id]["step"] = "Model oluşturuluyor..."

            tr = await client.post(f"{TRIPO_BASE}/task", json={
                "type": "image_to_model",
                "file": {"type": ext if ext != "jpg" else "jpeg", "file_token": token}
            }, headers={**headers, "Content-Type": "application/json"})

            tripo_id = tr.json().get("data", {}).get("task_id")
            if not tripo_id:
                raise Exception(f"Task oluşturulamadı: {tr.text}")

            tasks[task_id]["progress"] = 30
            await _tripo_poll(client, headers, task_id, tripo_id)

    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)


async def _tripo_poll(client, headers, task_id, tripo_id):
    """Tripo görevini takip et"""
    for _ in range(200):
        await asyncio.sleep(3)
        try:
            r = await client.get(f"{TRIPO_BASE}/task/{tripo_id}", headers=headers)
            d = r.json().get("data", {})
            st = d.get("status", "")
            pr = d.get("progress", 0)
            tasks[task_id]["progress"] = 30 + int(pr * 0.65)
            tasks[task_id]["step"] = f"Model üretiliyor... %{pr}"

            if st == "success":
                url = d.get("output", {}).get("model", "")
                tasks[task_id]["status"] = "done"
                tasks[task_id]["progress"] = 100
                tasks[task_id]["step"] = "Tamamlandı!"
                tasks[task_id]["model_url"] = url
                tasks[task_id]["download_urls"] = {"glb": url}
                return
            elif st in ("failed", "cancelled"):
                raise Exception(f"Tripo: {st}")
        except Exception as e:
            if "Tripo" in str(e):
                raise
            continue

    raise Exception("Zaman aşımı")


# ══════════════════════════════════════
#     /app SAYFASI (GÖMÜLÜ HTML)
# ══════════════════════════════════════

APP_HTML = """
<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PrintForge — 3D Model Üret</title>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Mono:wght@300;400;500&display=swap" rel="stylesheet">
<script type="module" src="https://unpkg.com/@google/model-viewer@3.5.0/dist/model-viewer.min.js"></script>
<style>
:root{--bg:#04080a;--bg2:#070d10;--border:#0e2028;--accent:#00e5ff;--accent2:#00ff9d;--text:#c8dde5;--muted:#2a4a5a;--card:#060c10;--red:#ff4466}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'DM Mono',monospace;background:var(--bg);color:var(--text);min-height:100vh}
::-webkit-scrollbar{width:4px}
::-webkit-scrollbar-thumb{background:var(--muted)}

/* BG */
.bg-grid{position:fixed;inset:0;z-index:0;pointer-events:none;background-image:linear-gradient(rgba(0,229,255,0.02) 1px,transparent 1px),linear-gradient(90deg,rgba(0,229,255,0.02) 1px,transparent 1px);background-size:72px 72px}
.bg-orb{position:fixed;border-radius:50%;filter:blur(90px);pointer-events:none;z-index:0}
.bg-orb1{width:500px;height:500px;background:radial-gradient(circle,rgba(0,229,255,0.08),transparent 70%);top:-150px;left:-150px}
.bg-orb2{width:400px;height:400px;background:radial-gradient(circle,rgba(0,255,157,0.06),transparent 70%);bottom:-100px;right:-100px}

/* NAV */
.nav{position:sticky;top:0;z-index:100;display:flex;justify-content:space-between;align-items:center;padding:16px 40px;background:rgba(4,8,10,0.9);backdrop-filter:blur(20px);border-bottom:1px solid rgba(0,229,255,0.07)}
.nav-logo{display:flex;align-items:center;gap:10px;text-decoration:none}
.nlm{width:24px;height:24px;border:1.5px solid var(--accent);transform:rotate(45deg);display:flex;align-items:center;justify-content:center}
.nli{width:6px;height:6px;background:var(--accent);transform:rotate(-45deg)}
.nlt{font-family:'Syne',sans-serif;font-size:15px;font-weight:800;color:var(--accent);letter-spacing:0.1em}
.nav-status{display:flex;align-items:center;gap:8px;font-size:10px;letter-spacing:0.1em}
.nav-dot{width:7px;height:7px;border-radius:50%;animation:pulse 2s infinite}
.nav-dot.ok{background:var(--accent2)}
.nav-dot.no{background:var(--red)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
.nav-back{color:var(--muted);font-size:10px;letter-spacing:0.12em;text-decoration:none;transition:color 0.2s}
.nav-back:hover{color:var(--accent)}

/* CONTAINER */
.container{position:relative;z-index:1;max-width:720px;margin:0 auto;padding:40px 20px 80px}

/* HEADER */
.page-hdr{text-align:center;margin-bottom:44px}
.page-hdr h1{font-family:'Syne',sans-serif;font-size:32px;font-weight:800;margin-bottom:8px}
.page-hdr h1 span{color:var(--accent)}
.page-hdr p{font-size:12px;color:var(--muted);line-height:1.8}

/* TABS */
.tabs{display:flex;border:1px solid var(--border);margin-bottom:32px}
.tab{flex:1;padding:14px;background:transparent;border:none;color:var(--muted);font-family:'DM Mono',monospace;font-size:11px;letter-spacing:0.12em;cursor:pointer;transition:all 0.2s;display:flex;align-items:center;justify-content:center;gap:8px}
.tab.on{background:rgba(0,229,255,0.06);color:var(--accent);border-bottom:2px solid var(--accent)}
.tab:hover:not(.on){background:rgba(0,229,255,0.02)}

/* PANELS */
.panel{display:none}
.panel.on{display:block}

/* CARD */
.card{background:var(--card);border:1px solid var(--border);padding:32px;margin-bottom:20px}

/* INPUT */
.label{font-size:9px;letter-spacing:0.18em;color:var(--muted);margin-bottom:8px;display:block}
textarea{width:100%;background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:14px;font-size:13px;font-family:'DM Mono',monospace;resize:vertical;min-height:80px;transition:border-color 0.2s}
textarea:focus{outline:none;border-color:rgba(0,229,255,0.4)}
textarea::placeholder{color:var(--muted)}

/* STYLES */
.style-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:8px}
.style-opt{padding:14px 10px;border:1px solid var(--border);background:transparent;color:var(--muted);font-family:'DM Mono',monospace;font-size:10px;letter-spacing:0.1em;cursor:pointer;transition:all 0.2s;text-align:center}
.style-opt:hover{border-color:rgba(0,229,255,0.3);color:var(--text)}
.style-opt.on{border-color:var(--accent);color:var(--accent);background:rgba(0,229,255,0.04)}
.style-opt .ico{font-size:20px;display:block;margin-bottom:6px}

/* UPLOAD */
.upload{border:2px dashed var(--border);padding:48px 24px;text-align:center;cursor:pointer;transition:all 0.3s;position:relative;overflow:hidden}
.upload:hover,.upload.drag{border-color:var(--accent);background:rgba(0,229,255,0.03)}
.upload.has{border-color:var(--accent2);border-style:solid}
.upload input{position:absolute;inset:0;opacity:0;cursor:pointer}
.upload .ico{font-size:36px;margin-bottom:12px;color:var(--accent)}
.upload p{font-size:12px;color:var(--muted)}
.upload .hint{font-size:9px;color:var(--muted);margin-top:6px;letter-spacing:0.1em}
.preview{margin-top:16px;display:none;position:relative}
.preview.on{display:block}
.preview img{max-width:100%;max-height:220px;display:block;margin:0 auto;border:1px solid var(--border)}
.preview .rm{position:absolute;top:6px;right:6px;width:28px;height:28px;background:rgba(255,68,102,0.85);border:none;color:#fff;border-radius:50%;cursor:pointer;font-size:12px}

/* BUTTON */
.gen-btn{width:100%;padding:16px;background:var(--accent);color:#04080a;border:none;font-family:'DM Mono',monospace;font-size:12px;letter-spacing:0.2em;cursor:pointer;font-weight:600;transition:all 0.2s;margin-top:20px;position:relative;overflow:hidden}
.gen-btn:hover:not(:disabled){background:var(--accent2);transform:translateY(-1px);box-shadow:0 6px 20px rgba(0,229,255,0.2)}
.gen-btn:disabled{opacity:0.4;cursor:not-allowed}
.gen-btn::after{content:'';position:absolute;inset:0;background:linear-gradient(120deg,transparent 30%,rgba(255,255,255,0.12),transparent 70%);transform:translateX(-100%);transition:transform 0.4s}
.gen-btn:hover::after{transform:translateX(100%)}

/* PROGRESS */
.progress-sec{display:none;margin-bottom:24px}
.progress-sec.on{display:block}
.prog-card{background:var(--card);border:1px solid var(--border);padding:28px}
.prog-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}
.prog-title{font-family:'Syne',sans-serif;font-size:15px;font-weight:700}
.prog-pct{font-family:'Syne',sans-serif;font-size:22px;font-weight:800;color:var(--accent)}
.prog-bar-bg{width:100%;height:8px;background:var(--bg2);overflow:hidden;margin-bottom:12px}
.prog-bar{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));width:0%;transition:width 0.5s;position:relative}
.prog-bar::after{content:'';position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,0.2),transparent);animation:shimmer 2s infinite}
@keyframes shimmer{0%{transform:translateX(-100%)}100%{transform:translateX(100%)}}
.prog-step{font-size:11px;color:var(--muted);display:flex;align-items:center;gap:8px}
.spinner{display:inline-block;width:12px;height:12px;border:2px solid var(--muted);border-top-color:var(--accent);border-radius:50%;animation:spin 0.8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

/* RESULT */
.result-sec{display:none;margin-bottom:24px}
.result-sec.on{display:block}
.result-card{background:var(--card);border:1px solid var(--accent2);padding:28px;text-align:center}
.result-card .ok-icon{font-size:44px;margin-bottom:10px}
.result-card h3{font-family:'Syne',sans-serif;font-size:20px;font-weight:800;margin-bottom:6px}
.result-card>p{font-size:11px;color:var(--muted);margin-bottom:20px}
.viewer{width:100%;height:360px;background:var(--bg2);border:1px solid var(--border);margin-bottom:20px;overflow:hidden}
.viewer model-viewer{width:100%;height:100%}
.result-btns{display:flex;gap:10px;flex-wrap:wrap}
.rbtn{flex:1;min-width:140px;padding:13px;border:1px solid var(--border);background:transparent;color:var(--text);font-family:'DM Mono',monospace;font-size:10px;letter-spacing:0.12em;cursor:pointer;transition:all 0.2s;text-decoration:none;text-align:center;display:flex;align-items:center;justify-content:center;gap:6px}
.rbtn:hover{border-color:var(--accent);color:var(--accent)}
.rbtn.main{background:var(--accent2);color:#04080a;border-color:var(--accent2)}
.rbtn.main:hover{background:var(--accent);border-color:var(--accent)}

/* ERROR */
.error-sec{display:none;margin-bottom:24px}
.error-sec.on{display:block}
.err-card{background:rgba(255,68,102,0.06);border:1px solid rgba(255,68,102,0.2);padding:28px;text-align:center}
.err-card .err-icon{font-size:40px;margin-bottom:10px}
.err-card h3{color:var(--red);font-family:'Syne',sans-serif;font-size:16px;margin-bottom:6px}
.err-card p{font-size:11px;color:var(--muted);margin-bottom:16px;line-height:1.8}
.err-card .rbtn{display:inline-flex}

/* RESPONSIVE */
@media(max-width:600px){
  .nav{padding:14px 16px}
  .container{padding:24px 14px}
  .card{padding:24px 18px}
  .style-grid{grid-template-columns:repeat(2,1fr)}
  .viewer{height:260px}
}
</style>
</head>
<body>
<div class="bg-grid"></div>
<div class="bg-orb bg-orb1"></div>
<div class="bg-orb bg-orb2"></div>

<nav class="nav">
  <a href="/" class="nav-logo"><div class="nlm"><div class="nli"></div></div><span class="nlt">PRINTFORGE</span></a>
  <div class="nav-status" id="apiSt"><div class="nav-dot no"></div><span>Kontrol ediliyor...</span></div>
  <a href="/" class="nav-back">← ANA SAYFA</a>
</nav>

<div class="container">
  <div class="page-hdr">
    <h1>Hayalini <span>3D Modele</span> Dönüştür</h1>
    <p>Metin yaz veya görsel yükle — yapay zeka saniyeler içinde 3D modelini üretsin</p>
  </div>

  <!-- TABS -->
  <div class="tabs">
    <button class="tab on" onclick="swTab('text')">⌨ METİN İLE ÜRET</button>
    <button class="tab" onclick="swTab('image')">⬡ GÖRSEL İLE ÜRET</button>
  </div>

  <!-- TEXT PANEL -->
  <div class="panel on" id="pText">
    <div class="card">
      <label class="label">PROMPT</label>
      <textarea id="prompt" placeholder="Örn: a cute robot toy, a medieval castle, a sports car..." rows="3"></textarea>

      <label class="label" style="margin-top:20px">STİL</label>
      <div class="style-grid" id="styles">
        <button class="style-opt on" data-s="realistic" onclick="selS(this)"><span class="ico">📷</span>GERÇEKÇİ</button>
        <button class="style-opt" data-s="cartoon" onclick="selS(this)"><span class="ico">🎨</span>CARTOON</button>
        <button class="style-opt" data-s="lowpoly" onclick="selS(this)"><span class="ico">💎</span>LOW POLY</button>
        <button class="style-opt" data-s="sculpture" onclick="selS(this)"><span class="ico">🗿</span>HEYKEL</button>
        <button class="style-opt" data-s="mechanical" onclick="selS(this)"><span class="ico">⚙️</span>MEKANİK</button>
        <button class="style-opt" data-s="miniature" onclick="selS(this)"><span class="ico">♟️</span>MİNYATÜR</button>
      </div>
    </div>
    <button class="gen-btn" id="txtBtn" onclick="genText()">⚡ 3D MODEL ÜRET</button>
  </div>

  <!-- IMAGE PANEL -->
  <div class="panel" id="pImage">
    <div class="card">
      <label class="label">GÖRSEL YÜKLE</label>
      <div class="upload" id="upArea">
        <div class="ico">⬡</div>
        <p>Sürükle-bırak veya tıkla</p>
        <div class="hint">JPG · PNG · WEBP — MAX 10MB</div>
        <input type="file" id="fInp" accept="image/*" onchange="onFile(this)">
      </div>
      <div class="preview" id="prev">
        <img id="prevImg" src="">
        <button class="rm" onclick="rmFile()">✕</button>
      </div>
    </div>
    <button class="gen-btn" id="imgBtn" onclick="genImage()" disabled>⚡ 3D MODEL ÜRET</button>
  </div>

  <!-- PROGRESS -->
  <div class="progress-sec" id="progSec">
    <div class="prog-card">
      <div class="prog-top">
        <span class="prog-title">🔄 Model Üretiliyor</span>
        <span class="prog-pct" id="progPct">0%</span>
      </div>
      <div class="prog-bar-bg"><div class="prog-bar" id="progBar"></div></div>
      <div class="prog-step" id="progStep"><div class="spinner"></div><span>Başlatılıyor...</span></div>
    </div>
  </div>

  <!-- RESULT -->
  <div class="result-sec" id="resSec">
    <div class="result-card">
      <div class="ok-icon">✅</div>
      <h3>Model Hazır!</h3>
      <p>3D modeliniz başarıyla oluşturuldu</p>
      <div class="viewer" id="viewer3d"></div>
      <div class="result-btns">
        <a class="rbtn main" id="dlBtn" href="#" target="_blank">↓ GLB İNDİR</a>
        <a class="rbtn" id="dlFbx" href="#" target="_blank" style="display:none">↓ FBX</a>
        <a class="rbtn" id="dlObj" href="#" target="_blank" style="display:none">↓ OBJ</a>
        <button class="rbtn" onclick="reset()">+ YENİ MODEL</button>
      </div>
    </div>
  </div>

  <!-- ERROR -->
  <div class="error-sec" id="errSec">
    <div class="err-card">
      <div class="err-icon">⚠️</div>
      <h3>Bir Hata Oluştu</h3>
      <p id="errMsg">Bilinmeyen hata</p>
      <button class="rbtn" onclick="reset()">↻ TEKRAR DENE</button>
    </div>
  </div>
</div>

<script>
const API = window.location.origin;
let style = 'realistic';
let selFile = null;
let poll = null;

// API DURUM
async function checkApi(){
  try{
    const r = await fetch(API+'/api/health');
    const d = await r.json();
    const el = document.getElementById('apiSt');
    if(d.api_ready){
      el.innerHTML='<div class="nav-dot ok"></div><span style="color:var(--accent2)">'+d.active_api.toUpperCase()+' BAĞLI</span>';
    } else {
      el.innerHTML='<div class="nav-dot no"></div><span style="color:var(--red)">API KEY EKSİK</span>';
    }
  }catch(e){console.error(e)}
}
checkApi();

// TABS
function swTab(t){
  document.querySelectorAll('.tab').forEach((b,i)=>{
    b.classList.toggle('on', (t==='text'&&i===0)||(t==='image'&&i===1));
  });
  document.getElementById('pText').classList.toggle('on', t==='text');
  document.getElementById('pImage').classList.toggle('on', t==='image');
}

// STYLE
function selS(el){
  document.querySelectorAll('.style-opt').forEach(s=>s.classList.remove('on'));
  el.classList.add('on');
  style = el.dataset.s;
}

// FILE
const upArea = document.getElementById('upArea');
upArea.addEventListener('dragover',e=>{e.preventDefault();upArea.classList.add('drag')});
upArea.addEventListener('dragleave',()=>upArea.classList.remove('drag'));
upArea.addEventListener('drop',e=>{
  e.preventDefault();upArea.classList.remove('drag');
  if(e.dataTransfer.files[0]){document.getElementById('fInp').files=e.dataTransfer.files;onFile(document.getElementById('fInp'))}
});

function onFile(inp){
  const f=inp.files[0]; if(!f)return;
  if(f.size>10*1024*1024){alert('Dosya çok büyük! Max 10MB');return}
  selFile=f;
  const rd=new FileReader();
  rd.onload=e=>{
    document.getElementById('prevImg').src=e.target.result;
    document.getElementById('prev').classList.add('on');
    upArea.classList.add('has');
    document.getElementById('imgBtn').disabled=false;
  };
  rd.readAsDataURL(f);
}
function rmFile(){
  selFile=null;
  document.getElementById('fInp').value='';
  document.getElementById('prev').classList.remove('on');
  upArea.classList.remove('has');
  document.getElementById('imgBtn').disabled=true;
}

// GENERATE TEXT
async function genText(){
  const p=document.getElementById('prompt').value.trim();
  if(!p){alert('Lütfen bir prompt girin!');return}
  showProg(); disable(true);
  try{
    const r=await fetch(API+'/api/generate/text',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:p,style:style})});
    if(!r.ok){const e=await r.json();throw new Error(e.detail||'İstek başarısız')}
    const d=await r.json();
    startPoll(d.task_id);
  }catch(e){showErr(e.message)}
}

// GENERATE IMAGE
async function genImage(){
  if(!selFile){alert('Lütfen bir görsel yükleyin!');return}
  showProg(); disable(true);
  try{
    const fd=new FormData(); fd.append('file',selFile);
    const r=await fetch(API+'/api/generate/image',{method:'POST',body:fd});
    if(!r.ok){const e=await r.json();throw new Error(e.detail||'İstek başarısız')}
    const d=await r.json();
    startPoll(d.task_id);
  }catch(e){showErr(e.message)}
}

// POLLING
function startPoll(tid){
  if(poll)clearInterval(poll);
  poll=setInterval(async()=>{
    try{
      const r=await fetch(API+'/api/status/'+tid);
      const d=await r.json();
      updProg(d.progress, d.step||'İşleniyor...');
      if(d.status==='done'){clearInterval(poll);showRes(tid,d)}
      else if(d.status==='failed'){clearInterval(poll);showErr(d.error||'Model üretilemedi')}
    }catch(e){console.error(e)}
  },2500);
}

// UI
function showProg(){
  hide('resSec');hide('errSec');show('progSec');
  updProg(0,'Başlatılıyor...');
}
function updProg(pct,step){
  document.getElementById('progBar').style.width=pct+'%';
  document.getElementById('progPct').textContent=pct+'%';
  document.getElementById('progStep').innerHTML='<div class="spinner"></div><span>'+step+'</span>';
}
function showRes(tid,d){
  hide('progSec');show('resSec');disable(false);
  const glb=d.model_url||'';
  const urls=d.download_urls||{};

  // Download buttons
  document.getElementById('dlBtn').href=glb||API+'/api/download/'+tid;

  if(urls.fbx){document.getElementById('dlFbx').href=urls.fbx;document.getElementById('dlFbx').style.display='flex'}
  if(urls.obj){document.getElementById('dlObj').href=urls.obj;document.getElementById('dlObj').style.display='flex'}

  // 3D viewer
  if(glb){
    document.getElementById('viewer3d').innerHTML=
      '<model-viewer src="'+glb+'" auto-rotate camera-controls interaction-prompt="none" '+
      'style="width:100%;height:100%;background:#070d10" loading="eager" '+
      'shadow-intensity="1" environment-image="neutral" exposure="1.1"></model-viewer>';
  }
}
function showErr(msg){
  hide('progSec');show('errSec');disable(false);
  document.getElementById('errMsg').textContent=msg;
}
function reset(){
  if(poll)clearInterval(poll);
  hide('progSec');hide('resSec');hide('errSec');
  disable(false);
  document.getElementById('dlFbx').style.display='none';
  document.getElementById('dlObj').style.display='none';
}
function show(id){document.getElementById(id).classList.add('on')}
function hide(id){document.getElementById(id).classList.remove('on')}
function disable(v){
  document.getElementById('txtBtn').disabled=v;
  document.getElementById('imgBtn').disabled=v||!selFile;
}
</script>
</body>
</html>
"""
