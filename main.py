from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
import asyncio
import uuid
import httpx
import os
import io

app = FastAPI(title="PrintForge 3D Generator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== GÖREV DEPOSU ==========
tasks = {}

# ========== TRIPO3D AYARLARI ==========
TRIPO_API_KEY = os.getenv("TRIPO_API_KEY", "")
TRIPO_BASE = "https://api.tripo3d.ai/v2/openapi"

# ========== MODELLER ==========
class TextRequest(BaseModel):
    prompt: str
    style: str = "geometric"

# ========== ANA SAYFA ==========
@app.get("/")
def root():
    return {
        "status": "OK",
        "mode": "tripo3d",
        "key_set": bool(TRIPO_API_KEY),
        "endpoints": {
            "text_to_3d": "POST /generate/text",
            "image_to_3d": "POST /generate/image",
            "check_status": "GET /status/{task_id}",
            "download": "GET /download/{task_id}",
            "balance": "GET /balance",
        }
    }

# ========== HTML SAYFA ==========
@app.get("/app")
def serve_app():
    html_path = os.path.join(os.path.dirname(__file__), "printforge.html")
    if not os.path.exists(html_path):
        # Alternatif isimler dene
        for name in ["printforge (2).html", "index.html", "printforge.html"]:
            alt = os.path.join(os.path.dirname(__file__), name)
            if os.path.exists(alt):
                html_path = alt
                break
    if not os.path.exists(html_path):
        raise HTTPException(status_code=404, detail="HTML dosyası bulunamadı")
    return FileResponse(html_path, media_type="text/html")

# ========== BAKİYE KONTROL ==========
@app.get("/balance")
async def check_balance():
    """Tripo3D API bakiyenizi kontrol edin"""
    if not TRIPO_API_KEY:
        return {"error": "API key ayarlanmamış", "balance": 0}
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            res = await client.get(
                f"{TRIPO_BASE}/user/balance",
                headers={"Authorization": f"Bearer {TRIPO_API_KEY}"}
            )
            return res.json()
    except Exception as e:
        return {"error": str(e)}

# ========================================
#        GÖRSEL → 3D MODEL
# ========================================
@app.post("/generate/image")
async def generate_from_image(file: UploadFile = File(...)):
    if not TRIPO_API_KEY:
        raise HTTPException(status_code=500, detail="TRIPO_API_KEY ayarlanmamış!")
    
    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {"status": "pending", "progress": 0}
    
    contents = await file.read()
    filename = file.filename or "image.jpg"
    
    # Dosya boyutu kontrolü (max 10MB)
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Dosya çok büyük (max 10MB)")
    
    asyncio.create_task(process_image_with_tripo(task_id, contents, filename))
    return {"task_id": task_id}

async def process_image_with_tripo(task_id, contents, filename):
    """Görseli Tripo3D'ye gönderip 3D model üret"""
    try:
        tasks[task_id] = {"status": "in_progress", "progress": 5}
        headers = {"Authorization": f"Bearer {TRIPO_API_KEY}"}

        async with httpx.AsyncClient(timeout=600) as client:

            # ── ADIM 1: Görseli yükle ──
            tasks[task_id]["progress"] = 10
            tasks[task_id]["step"] = "Görsel yükleniyor..."

            ext = filename.rsplit(".", 1)[-1].lower()
            if ext not in ["jpg", "jpeg", "png", "webp"]:
                ext = "jpeg"
            mime = "image/jpeg" if ext in ["jpg", "jpeg"] else f"image/{ext}"

            upload_res = await client.post(
                f"{TRIPO_BASE}/upload",
                files={"file": (filename, contents, mime)},
                headers=headers
            )
            
            if upload_res.status_code != 200:
                raise Exception(f"Upload HTTP {upload_res.status_code}: {upload_res.text}")
            
            upload_json = upload_res.json()
            image_token = upload_json.get("data", {}).get("image_token")
            
            if not image_token:
                raise Exception(f"image_token alınamadı: {upload_json}")
            
            tasks[task_id]["progress"] = 25
            tasks[task_id]["step"] = "3D model oluşturuluyor..."

            # ── ADIM 2: image_to_model görevi oluştur ──
            task_res = await client.post(
                f"{TRIPO_BASE}/task",
                json={
                    "type": "image_to_model",
                    "file": {
                        "type": ext if ext != "jpg" else "jpeg",
                        "file_token": image_token
                    }
                },
                headers={**headers, "Content-Type": "application/json"}
            )
            
            if task_res.status_code != 200:
                raise Exception(f"Task HTTP {task_res.status_code}: {task_res.text}")
            
            task_json = task_res.json()
            tripo_task_id = task_json.get("data", {}).get("task_id")
            
            if not tripo_task_id:
                raise Exception(f"task_id alınamadı: {task_json}")
            
            tasks[task_id]["progress"] = 35
            tasks[task_id]["tripo_task_id"] = tripo_task_id

            # ── ADIM 3: Sonucu bekle ──
            await wait_for_tripo_result(client, headers, task_id, tripo_task_id)

    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)
        print(f"[HATA] Image task {task_id}: {e}")

# ========================================
#        METİN → 3D MODEL
# ========================================
@app.post("/generate/text")
async def generate_from_text(req: TextRequest):
    if not TRIPO_API_KEY:
        raise HTTPException(status_code=500, detail="TRIPO_API_KEY ayarlanmamış!")
    
    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {"status": "pending", "progress": 0}
    
    asyncio.create_task(process_text_with_tripo(task_id, req.prompt, req.style))
    return {"task_id": task_id}

async def process_text_with_tripo(task_id, prompt, style):
    """Metin prompt'unu Tripo3D'ye gönderip 3D model üret"""
    try:
        tasks[task_id] = {"status": "in_progress", "progress": 10}
        tasks[task_id]["step"] = "Prompt işleniyor..."
        headers = {"Authorization": f"Bearer {TRIPO_API_KEY}"}

        # Stil bilgisini prompt'a ekle
        full_prompt = f"{prompt}, {style} style" if style else prompt

        async with httpx.AsyncClient(timeout=600) as client:

            # ── ADIM 1: text_to_model görevi oluştur ──
            tasks[task_id]["progress"] = 20
            tasks[task_id]["step"] = "3D model oluşturuluyor..."

            task_res = await client.post(
                f"{TRIPO_BASE}/task",
                json={
                    "type": "text_to_model",
                    "prompt": full_prompt
                },
                headers={**headers, "Content-Type": "application/json"}
            )
            
            if task_res.status_code != 200:
                raise Exception(f"Task HTTP {task_res.status_code}: {task_res.text}")
            
            task_json = task_res.json()
            tripo_task_id = task_json.get("data", {}).get("task_id")
            
            if not tripo_task_id:
                raise Exception(f"task_id alınamadı: {task_json}")
            
            tasks[task_id]["progress"] = 30
            tasks[task_id]["tripo_task_id"] = tripo_task_id

            # ── ADIM 2: Sonucu bekle ──
            await wait_for_tripo_result(client, headers, task_id, tripo_task_id)

    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)
        print(f"[HATA] Text task {task_id}: {e}")

# ========================================
#        ORTAK: TRIPO SONUÇ BEKLEYİCİ
# ========================================
async def wait_for_tripo_result(client, headers, task_id, tripo_task_id):
    """Tripo3D görevinin tamamlanmasını bekle"""
    max_attempts = 120  # Max 6 dakika (120 x 3sn)
    attempt = 0
    
    while attempt < max_attempts:
        await asyncio.sleep(3)
        attempt += 1
        
        try:
            status_res = await client.get(
                f"{TRIPO_BASE}/task/{tripo_task_id}",
                headers=headers
            )
            status_json = status_res.json()
            tripo_data = status_json.get("data", {})
            tripo_status = tripo_data.get("status", "unknown")
            tripo_progress = tripo_data.get("progress", 0)
            
            # Progress güncelle (35-95 arası map'le)
            mapped_progress = 35 + int(tripo_progress * 0.6)
            tasks[task_id]["progress"] = min(mapped_progress, 95)
            tasks[task_id]["step"] = f"Model üretiliyor... %{tripo_progress}"

            if tripo_status == "success":
                output = tripo_data.get("output", {})
                model_url = output.get("model", "")
                
                # Farklı format URL'leri
                pbr_model = output.get("pbr_model", "")
                rendered_image = output.get("rendered_image", "")

                tasks[task_id]["status"] = "succeeded"
                tasks[task_id]["progress"] = 100
                tasks[task_id]["step"] = "Tamamlandı!"
                tasks[task_id]["download_url"] = model_url
                tasks[task_id]["output"] = {
                    "model": model_url,
                    "pbr_model": pbr_model,
                    "rendered_image": rendered_image,
                }
                print(f"[OK] Task {task_id} başarılı! Model: {model_url}")
                return
                
            elif tripo_status in ["failed", "cancelled", "unknown"]:
                error_msg = tripo_data.get("message", f"Tripo durumu: {tripo_status}")
                tasks[task_id]["status"] = "failed"
                tasks[task_id]["error"] = error_msg
                print(f"[FAIL] Task {task_id}: {error_msg}")
                return
                
        except Exception as e:
            print(f"[WARN] Status check hatası: {e}")
            continue
    
    # Zaman aşımı
    tasks[task_id]["status"] = "failed"
    tasks[task_id]["error"] = "Zaman aşımı - model üretimi çok uzun sürdü"

# ========================================
#        DURUM SORGULAMA
# ========================================
@app.get("/status/{task_id}")
async def get_status(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Görev bulunamadı")
    
    t = tasks[task_id]
    return {
        "task_id": task_id,
        "status": t["status"],
        "progress": t["progress"],
        "step": t.get("step", ""),
        "download_url": t.get("download_url", ""),
        "output": t.get("output", {}),
        "stats": t.get("stats", {}),
        "error": t.get("error", ""),
    }

# ========================================
#        MODEL İNDİRME (PROXY)
# ========================================
@app.get("/download/{task_id}")
async def download_model(task_id: str):
    """Üretilen modeli indir (Tripo URL'sini proxy'le)"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Görev bulunamadı")
    
    t = tasks[task_id]
    if t["status"] != "succeeded":
        raise HTTPException(status_code=400, detail="Model henüz hazır değil")
    
    model_url = t.get("download_url", "")
    if not model_url:
        raise HTTPException(status_code=404, detail="Model URL'si bulunamadı")
    
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            res = await client.get(model_url)
            if res.status_code != 200:
                raise HTTPException(status_code=502, detail="Model indirilemedi")
            
            # Content type belirle
            content_type = res.headers.get("content-type", "application/octet-stream")
            
            # Dosya uzantısı belirle
            if "glb" in model_url.lower():
                filename = f"model_{task_id}.glb"
                content_type = "model/gltf-binary"
            elif "fbx" in model_url.lower():
                filename = f"model_{task_id}.fbx"
            elif "obj" in model_url.lower():
                filename = f"model_{task_id}.obj"
            else:
                filename = f"model_{task_id}.glb"
            
            return StreamingResponse(
                io.BytesIO(res.content),
                media_type=content_type,
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"'
                }
            )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"İndirme hatası: {str(e)}")

# ========================================
#        TÜM GÖREVLERİ LİSTELE
# ========================================
@app.get("/tasks")
async def list_tasks():
    """Tüm görevleri listele"""
    return {
        "total": len(tasks),
        "tasks": {
            tid: {
                "status": t["status"],
                "progress": t["progress"],
                "step": t.get("step", ""),
            }
            for tid, t in tasks.items()
        }
    }

# ========================================
#        SAĞLIK KONTROLÜ
# ========================================
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "api_key_set": bool(TRIPO_API_KEY),
        "active_tasks": len([t for t in tasks.values() if t["status"] == "in_progress"]),
        "total_tasks": len(tasks),
    }
