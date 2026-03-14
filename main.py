from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, Response
from pydantic import BaseModel
import asyncio
import uuid
import httpx
import base64
import random
import json
import os
import io

# STL dönüştürme için trimesh
try:
    import trimesh
    import numpy
    HAS_TRIMESH = True
    print("[OK] trimesh yüklü — STL dönüştürme aktif")
except ImportError:
    HAS_TRIMESH = False
    print("[WARN] trimesh yüklü değil — STL dönüştürme devre dışı")

# ══════════════════════════════════════════════
#              UYGULAMA AYARLARI
# ══════════════════════════════════════════════
app = FastAPI(title="PrintForge Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

TRIPO_API_KEY = os.getenv("TRIPO_API_KEY", "")
MESHY_API_KEY = os.getenv("MESHY_API_KEY", "")
TRIPO_BASE = "https://api.tripo3d.ai/v2/openapi"
MESHY_BASE = "https://api.meshy.ai/openapi/v2"

# Görev deposu
tasks = {}

# Model önbelleği (indirilen GLB dosyaları bellekte tutulur)
model_cache = {}
MAX_CACHE = 30  # Max 30 model

STYLE_MAP = {
    "realistic": "realistic", "cartoon": "cartoon", "lowpoly": "low-poly",
    "sculpture": "sculpture", "mechanical": "pbr", "miniature": "sculpture",
    "geometric": "realistic",
}

DEMO_MODELS = [
    {"name": "Damaged Helmet", "glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb"},
    {"name": "Avocado", "glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/Avocado/glTF-Binary/Avocado.glb"},
    {"name": "Duck", "glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/Duck/glTF-Binary/Duck.glb"},
    {"name": "Lantern", "glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/Lantern/glTF-Binary/Lantern.glb"},
    {"name": "Water Bottle", "glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/WaterBottle/glTF-Binary/WaterBottle.glb"},
    {"name": "Suzanne", "glb": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/Suzanne/glTF-Binary/Suzanne.glb"},
]


class TextRequest(BaseModel):
    prompt: str
    style: str = "realistic"


def get_api():
    if TRIPO_API_KEY: return "tripo"
    if MESHY_API_KEY: return "meshy"
    return "demo"


# ══════════════════════════════════════════════
#         MODEL ÖNBELLEK YÖNETİMİ
# ══════════════════════════════════════════════

async def cache_model(task_id, url):
    """Model dosyasını indir ve önbelleğe al"""
    if task_id in model_cache:
        return True
    try:
        # Önbellek doluysa en eskiyi sil
        while len(model_cache) >= MAX_CACHE:
            oldest = next(iter(model_cache))
            del model_cache[oldest]
            print(f"[CACHE] {oldest} silindi (limit aşıldı)")

        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code == 200 and len(r.content) > 100:
                model_cache[task_id] = r.content
                size_mb = round(len(r.content) / 1024 / 1024, 2)
                print(f"[CACHE] {task_id} kaydedildi ({size_mb} MB)")
                return True
            else:
                print(f"[CACHE] {task_id} indirilemedi: HTTP {r.status_code}, boyut: {len(r.content)}")
    except Exception as e:
        print(f"[CACHE] {task_id} hata: {e}")
    return False


async def ensure_cached(task_id):
    """Model önbellekte yoksa indirmeyi dene"""
    if task_id in model_cache:
        return True
    if task_id in tasks and tasks[task_id].get("model_url"):
        return await cache_model(task_id, tasks[task_id]["model_url"])
    return False


# ══════════════════════════════════════════════
#              SAYFALAR
# ══════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
def serve_landing():
    for name in ["index.html", "printforge.html", "printforge (2).html"]:
        path = os.path.join(os.path.dirname(__file__), name)
        if os.path.exists(path):
            return FileResponse(path, media_type="text/html")
    return HTMLResponse('<html><body style="background:#04080a;color:#00e5ff;font-family:monospace;display:flex;align-items:center;justify-content:center;height:100vh"><a href="/app" style="color:#00e5ff;font-size:24px">PrintForge → /app</a></body></html>')


@app.get("/app", response_class=HTMLResponse)
def serve_app():
    return HTMLResponse(APP_HTML)


# ══════════════════════════════════════════════
#              API BİLGİ & DEBUG
# ══════════════════════════════════════════════

@app.get("/api/health")
async def health():
    api = get_api()
    return {
        "status": "online",
        "active_api": api,
        "api_ready": True,
        "is_demo": api == "demo",
        "stl_ready": HAS_TRIMESH,
        "cached_models": len(model_cache),
        "active_tasks": len([t for t in tasks.values() if t["status"] == "processing"]),
    }


@app.get("/api/balance")
async def balance():
    results = {}
    async with httpx.AsyncClient(timeout=15) as client:
        if TRIPO_API_KEY:
            try:
                r = await client.get(f"{TRIPO_BASE}/user/balance",
                                     headers={"Authorization": f"Bearer {TRIPO_API_KEY}"})
                results["tripo"] = r.json()
            except Exception as e:
                results["tripo"] = {"error": str(e)}
    return results if results else {"mode": "demo"}


@app.get("/api/debug/{task_id}")
async def debug_task(task_id: str):
    if task_id not in tasks:
        return {"error": "Görev bulunamadı", "all_tasks": list(tasks.keys())}
    return {
        "task_id": task_id,
        "data": tasks[task_id],
        "cached": task_id in model_cache,
        "cache_size": len(model_cache.get(task_id, b"")),
    }


@app.get("/api/debug")
async def debug_all():
    return {"total": len(tasks), "cached": len(model_cache), "tasks": {
        k: {"status": v["status"], "progress": v["progress"], "model_url": v.get("model_url", "")[:80]}
        for k, v in tasks.items()
    }}


# ══════════════════════════════════════════════
#     MODEL SUNMA — ÖNİZLEME & İNDİRME
# ══════════════════════════════════════════════

@app.get("/api/model/{task_id}/view")
async def model_view(task_id: str):
    """3D önizleme için model sun (CORS dahil)"""
    if not await ensure_cached(task_id):
        raise HTTPException(404, "Model bulunamadı veya henüz hazır değil")

    return Response(
        content=model_cache[task_id],
        media_type="model/gltf-binary",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET",
            "Cache-Control": "public, max-age=3600",
            "Content-Disposition": f'inline; filename="model_{task_id}.glb"',
        }
    )


@app.get("/api/model/{task_id}/glb")
async def download_glb(task_id: str):
    """GLB formatında indir"""
    if not await ensure_cached(task_id):
        raise HTTPException(404, "Model bulunamadı")

    return Response(
        content=model_cache[task_id],
        media_type="model/gltf-binary",
        headers={
            "Content-Disposition": f'attachment; filename="printforge_{task_id}.glb"',
            "Access-Control-Allow-Origin": "*",
        }
    )


@app.get("/api/model/{task_id}/stl")
async def download_stl(task_id: str):
    """STL formatına dönüştürüp indir"""
    if not HAS_TRIMESH:
        raise HTTPException(500, "STL dönüştürme kütüphanesi (trimesh) yüklü değil. requirements.txt'e 'trimesh' ve 'numpy' ekleyin.")

    if not await ensure_cached(task_id):
        raise HTTPException(404, "Model bulunamadı")

    try:
        glb_data = model_cache[task_id]

        # GLB → Trimesh yükle
        scene = trimesh.load(
            io.BytesIO(glb_data),
            file_type="glb",
            force="scene"
        )

        # Scene'i tek mesh'e çevir
        if isinstance(scene, trimesh.Scene):
            if len(scene.geometry) == 0:
                raise Exception("Model boş — mesh bulunamadı")
            meshes = []
            for name, geom in scene.geometry.items():
                if isinstance(geom, trimesh.Trimesh):
                    meshes.append(geom)
            if not meshes:
                raise Exception("Dönüştürülebilir mesh bulunamadı")
            combined = trimesh.util.concatenate(meshes)
        elif isinstance(scene, trimesh.Trimesh):
            combined = scene
        else:
            raise Exception(f"Beklenmeyen tip: {type(scene)}")

        # STL'e dönüştür
        stl_bytes = combined.export(file_type="stl")

        size_mb = round(len(stl_bytes) / 1024 / 1024, 2)
        print(f"[STL] {task_id} dönüştürüldü ({size_mb} MB, {len(combined.faces)} face)")

        return Response(
            content=stl_bytes,
            media_type="application/vnd.ms-pki.stl",
            headers={
                "Content-Disposition": f'attachment; filename="printforge_{task_id}.stl"',
                "Access-Control-Allow-Origin": "*",
            }
        )

    except Exception as e:
        print(f"[HATA] STL dönüştürme {task_id}: {e}")
        raise HTTPException(500, f"STL dönüştürme hatası: {str(e)}")


@app.get("/api/model/{task_id}/obj")
async def download_obj(task_id: str):
    """OBJ formatına dönüştürüp indir"""
    if not HAS_TRIMESH:
        raise HTTPException(500, "OBJ dönüştürme için trimesh gerekli")

    if not await ensure_cached(task_id):
        raise HTTPException(404, "Model bulunamadı")

    try:
        glb_data = model_cache[task_id]
        scene = trimesh.load(io.BytesIO(glb_data), file_type="glb", force="scene")

        if isinstance(scene, trimesh.Scene):
            meshes = [g for g in scene.geometry.values() if isinstance(g, trimesh.Trimesh)]
            if not meshes:
                raise Exception("Mesh bulunamadı")
            combined = trimesh.util.concatenate(meshes)
        else:
            combined = scene

        obj_bytes = combined.export(file_type="obj")

        return Response(
            content=obj_bytes,
            media_type="text/plain",
            headers={
                "Content-Disposition": f'attachment; filename="printforge_{task_id}.obj"',
                "Access-Control-Allow-Origin": "*",
            }
        )
    except Exception as e:
        raise HTTPException(500, f"OBJ dönüştürme hatası: {str(e)}")


# ══════════════════════════════════════════════
#         ÜRET & DURUM
# ══════════════════════════════════════════════

@app.post("/api/generate/text")
async def generate_text(req: TextRequest):
    api = get_api()
    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {"status": "processing", "progress": 0, "step": "Başlatılıyor...", "type": "text", "api": api}
    if api == "tripo":
        asyncio.create_task(_tripo_text(task_id, req.prompt, req.style))
    elif api == "meshy":
        asyncio.create_task(_meshy_text(task_id, req.prompt, req.style))
    else:
        asyncio.create_task(_demo_generate(task_id))
    return {"task_id": task_id}


@app.post("/api/generate/image")
async def generate_image(file: UploadFile = File(...)):
    api = get_api()
    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(400, "Max 10MB")
    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {"status": "processing", "progress": 0, "step": "Görsel hazırlanıyor...", "type": "image", "api": api}
    filename = file.filename or "image.jpg"
    if api == "tripo":
        asyncio.create_task(_tripo_image(task_id, contents, filename))
    elif api == "meshy":
        asyncio.create_task(_meshy_image(task_id, contents, filename))
    else:
        asyncio.create_task(_demo_generate(task_id))
    return {"task_id": task_id}


@app.get("/api/status/{task_id}")
async def get_status(task_id: str):
    if task_id not in tasks:
        raise HTTPException(404, "Görev bulunamadı")
    t = tasks[task_id]
    return {
        "task_id": task_id,
        "status": t["status"],
        "progress": t["progress"],
        "step": t.get("step", ""),
        "model_url": t.get("model_url", ""),
        "is_demo": t.get("api") == "demo",
        "cached": task_id in model_cache,
        "error": t.get("error", ""),
    }


# ══════════════════════════════════════════════
#     URL ÇIKARMA — Tripo3D çeşitli formatlar döner
# ══════════════════════════════════════════════

def extract_model_url(data):
    """Tripo3D output verisinden model URL'sini bul"""
    if not data:
        return ""

    # Doğrudan string URL
    if isinstance(data, str) and data.startswith("http"):
        return data

    if not isinstance(data, dict):
        return ""

    # Bilinen alanları kontrol et
    for key in ["model", "pbr_model", "base_model", "rendered_model"]:
        val = data.get(key, "")
        if isinstance(val, str) and val.startswith("http"):
            return val
        if isinstance(val, dict):
            url = val.get("url", "") or val.get("download_url", "")
            if url and url.startswith("http"):
                return url

    # Tüm değerleri tara
    for key, val in data.items():
        if isinstance(val, str) and val.startswith("http"):
            if any(ext in val.lower() for ext in [".glb", ".gltf", ".fbx", ".obj", ".stl", "model"]):
                return val
        if isinstance(val, dict):
            for sk, sv in val.items():
                if isinstance(sv, str) and sv.startswith("http"):
                    return sv

    return ""


# ══════════════════════════════════════════════
#         🟢 TRIPO3D API
# ══════════════════════════════════════════════

async def _tripo_text(task_id, prompt, style):
    try:
        headers = {"Authorization": f"Bearer {TRIPO_API_KEY}"}
        tasks[task_id]["progress"] = 10
        tasks[task_id]["step"] = "Prompt gönderiliyor..."

        async with httpx.AsyncClient(timeout=600) as client:
            r = await client.post(f"{TRIPO_BASE}/task", json={
                "type": "text_to_model",
                "prompt": f"{prompt}, {style} style",
            }, headers={**headers, "Content-Type": "application/json"})

            print(f"[TRIPO] text task yanıt: {r.status_code} - {r.text[:300]}")
            if r.status_code != 200:
                raise Exception(f"Tripo hata {r.status_code}: {r.text}")

            tripo_id = r.json().get("data", {}).get("task_id")
            if not tripo_id:
                raise Exception(f"Task ID alınamadı: {r.text}")

            tasks[task_id]["progress"] = 25
            tasks[task_id]["step"] = "3D model oluşturuluyor..."
            await _tripo_poll(client, headers, task_id, tripo_id)

    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)
        print(f"[HATA] tripo_text {task_id}: {e}")


async def _tripo_image(task_id, contents, filename):
    try:
        headers = {"Authorization": f"Bearer {TRIPO_API_KEY}"}
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext not in ("jpg", "jpeg", "png", "webp"):
            ext = "jpeg"
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"

        tasks[task_id]["progress"] = 10
        tasks[task_id]["step"] = "Görsel yükleniyor..."

        async with httpx.AsyncClient(timeout=600) as client:
            # 1. Upload
            ur = await client.post(f"{TRIPO_BASE}/upload",
                                   files={"file": (filename, contents, mime)},
                                   headers=headers)
            print(f"[TRIPO] upload yanıt: {ur.status_code} - {ur.text[:300]}")
            if ur.status_code != 200:
                raise Exception(f"Upload hata {ur.status_code}: {ur.text}")

            token = ur.json().get("data", {}).get("image_token")
            if not token:
                raise Exception(f"image_token alınamadı: {ur.text}")

            tasks[task_id]["progress"] = 25
            tasks[task_id]["step"] = "Model oluşturuluyor..."

            # 2. Task
            tr = await client.post(f"{TRIPO_BASE}/task", json={
                "type": "image_to_model",
                "file": {"type": ext if ext != "jpg" else "jpeg", "file_token": token}
            }, headers={**headers, "Content-Type": "application/json"})

            print(f"[TRIPO] task yanıt: {tr.status_code} - {tr.text[:300]}")
            if tr.status_code != 200:
                raise Exception(f"Task hata {tr.status_code}: {tr.text}")

            tripo_id = tr.json().get("data", {}).get("task_id")
            if not tripo_id:
                raise Exception(f"Task ID alınamadı: {tr.text}")

            tasks[task_id]["progress"] = 35
            await _tripo_poll(client, headers, task_id, tripo_id)

    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)
        print(f"[HATA] tripo_image {task_id}: {e}")


async def _tripo_poll(client, headers, task_id, tripo_id):
    """Tripo3D görev sonucunu bekle + model önbelleğe al"""
    for attempt in range(200):
        await asyncio.sleep(3)
        try:
            r = await client.get(f"{TRIPO_BASE}/task/{tripo_id}", headers=headers)
            full = r.json()
            d = full.get("data", {})
            st = d.get("status", "")
            pr = d.get("progress", 0)

            tasks[task_id]["progress"] = 35 + int(pr * 0.55)
            tasks[task_id]["step"] = f"Model üretiliyor... %{pr}"

            if st == "success":
                output = d.get("output", {})
                print(f"[TRIPO] BAŞARILI! Output: {json.dumps(output, indent=2, default=str)}")

                model_url = extract_model_url(output)
                print(f"[TRIPO] Bulunan URL: {model_url}")

                if not model_url:
                    print(f"[TRIPO] URL bulunamadı! Tam data: {json.dumps(d, indent=2, default=str)}")

                tasks[task_id]["status"] = "done"
                tasks[task_id]["progress"] = 95
                tasks[task_id]["step"] = "Model indiriliyor..."
                tasks[task_id]["model_url"] = model_url
                tasks[task_id]["raw_output"] = output

                # ÖNEMLİ: Modeli önbelleğe al
                if model_url:
                    cached = await cache_model(task_id, model_url)
                    if cached:
                        tasks[task_id]["progress"] = 100
                        tasks[task_id]["step"] = "Tamamlandı!"
                        print(f"[OK] {task_id} hazır ve önbellekte!")
                    else:
                        tasks[task_id]["progress"] = 100
                        tasks[task_id]["step"] = "Tamamlandı (önbellek hatası)"
                        print(f"[WARN] {task_id} URL var ama önbelleğe alınamadı")
                else:
                    tasks[task_id]["progress"] = 100
                    tasks[task_id]["step"] = "Tamamlandı (URL bulunamadı)"
                    tasks[task_id]["error"] = "Model URL parse edilemedi"

                return

            elif st in ("failed", "cancelled"):
                msg = d.get("message", "") or f"Tripo: {st}"
                raise Exception(msg)

        except Exception as e:
            if any(x in str(e) for x in ["Tripo", "failed", "cancelled"]):
                tasks[task_id]["status"] = "failed"
                tasks[task_id]["error"] = str(e)
                return
            continue

    tasks[task_id]["status"] = "failed"
    tasks[task_id]["error"] = "Zaman aşımı"


# ══════════════════════════════════════════════
#         🎭 DEMO MOD
# ══════════════════════════════════════════════

async def _demo_generate(task_id):
    try:
        for pr, st in [(8,"Prompt analiz ediliyor..."),(20,"AI yükleniyor..."),(35,"3D geometri oluşturuluyor..."),(50,"Mesh oluşturuluyor..."),(65,"Yüzeyler hesaplanıyor..."),(78,"Texture uygulanıyor..."),(88,"Optimize ediliyor..."),(95,"Dosya hazırlanıyor...")]:
            tasks[task_id]["progress"] = pr
            tasks[task_id]["step"] = st
            await asyncio.sleep(random.uniform(1.0, 2.2))

        model = random.choice(DEMO_MODELS)
        tasks[task_id]["model_url"] = model["glb"]

        # Demo modeli de önbelleğe al
        await cache_model(task_id, model["glb"])

        tasks[task_id]["status"] = "done"
        tasks[task_id]["progress"] = 100
        tasks[task_id]["step"] = f"Tamamlandı! (Demo: {model['name']})"
    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)


# ══════════════════════════════════════════════
#         🔵 MESHY API (Yedek)
# ══════════════════════════════════════════════

async def _meshy_text(task_id, prompt, style):
    try:
        headers = {"Authorization": f"Bearer {MESHY_API_KEY}", "Content-Type": "application/json"}
        tasks[task_id]["progress"] = 10
        async with httpx.AsyncClient(timeout=600) as client:
            r = await client.post(f"{MESHY_BASE}/text-to-3d", json={
                "mode": "preview", "prompt": prompt,
                "art_style": STYLE_MAP.get(style, "realistic"),
                "negative_prompt": "low quality, blurry",
            }, headers=headers)
            if r.status_code not in (200, 202):
                raise Exception(f"Meshy hata {r.status_code}: {r.text}")
            meshy_id = r.json().get("result")
            if not meshy_id: raise Exception(f"ID alınamadı: {r.text}")
            tasks[task_id]["progress"] = 20
            await _meshy_poll(client, headers, task_id, meshy_id, "text-to-3d")
    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)


async def _meshy_image(task_id, contents, filename):
    try:
        headers = {"Authorization": f"Bearer {MESHY_API_KEY}", "Content-Type": "application/json"}
        ext = filename.rsplit(".", 1)[-1].lower()
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        b64 = base64.b64encode(contents).decode()
        tasks[task_id]["progress"] = 15
        async with httpx.AsyncClient(timeout=600) as client:
            r = await client.post(f"{MESHY_BASE}/image-to-3d", json={
                "image_url": f"data:{mime};base64,{b64}", "enable_pbr": True,
            }, headers=headers)
            if r.status_code not in (200, 202):
                raise Exception(f"Meshy hata {r.status_code}: {r.text}")
            meshy_id = r.json().get("result")
            if not meshy_id: raise Exception(f"ID alınamadı: {r.text}")
            tasks[task_id]["progress"] = 25
            await _meshy_poll(client, headers, task_id, meshy_id, "image-to-3d")
    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)


async def _meshy_poll(client, headers, task_id, meshy_id, endpoint):
    for _ in range(200):
        await asyncio.sleep(3)
        try:
            r = await client.get(f"{MESHY_BASE}/{endpoint}/{meshy_id}", headers=headers)
            if r.status_code != 200: continue
            data = r.json()
            status = data.get("status", "")
            progress = data.get("progress", 0)
            tasks[task_id]["progress"] = 25 + int(progress * 0.65)
            tasks[task_id]["step"] = f"Üretiliyor... %{progress}"
            if status == "SUCCEEDED":
                urls = data.get("model_urls", {})
                glb = urls.get("glb", "")
                tasks[task_id]["model_url"] = glb
                if glb: await cache_model(task_id, glb)
                tasks[task_id]["status"] = "done"
                tasks[task_id]["progress"] = 100
                tasks[task_id]["step"] = "Tamamlandı!"
                return
            elif status == "FAILED":
                raise Exception("Meshy: üretilemedi")
        except Exception as e:
            if "üretilemedi" in str(e): raise
            continue
    raise Exception("Zaman aşımı")


# ══════════════════════════════════════════════
#     /app HTML — DÜZELTİLMİŞ VERSİYON
# ══════════════════════════════════════════════

APP_HTML = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PrintForge — 3D Model Uret</title>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Mono:wght@300;400;500&display=swap" rel="stylesheet">
<script type="module" src="https://unpkg.com/@google/model-viewer@3.5.0/dist/model-viewer.min.js"></script>
<style>
:root{--bg:#04080a;--bg2:#070d10;--border:#0e2028;--accent:#00e5ff;--accent2:#00ff9d;--text:#c8dde5;--muted:#2a4a5a;--card:#060c10;--red:#ff4466;--orange:#ffaa00}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'DM Mono',monospace;background:var(--bg);color:var(--text);min-height:100vh}
::-webkit-scrollbar{width:4px}
::-webkit-scrollbar-thumb{background:var(--muted)}
.bg-grid{position:fixed;inset:0;z-index:0;pointer-events:none;background-image:linear-gradient(rgba(0,229,255,0.02) 1px,transparent 1px),linear-gradient(90deg,rgba(0,229,255,0.02) 1px,transparent 1px);background-size:72px 72px}
.bg-orb{position:fixed;border-radius:50%;filter:blur(90px);pointer-events:none;z-index:0}
.bg-orb1{width:500px;height:500px;background:radial-gradient(circle,rgba(0,229,255,0.08),transparent 70%);top:-150px;left:-150px}
.bg-orb2{width:400px;height:400px;background:radial-gradient(circle,rgba(0,255,157,0.06),transparent 70%);bottom:-100px;right:-100px}
.nav{position:sticky;top:0;z-index:100;display:flex;justify-content:space-between;align-items:center;padding:16px 40px;background:rgba(4,8,10,0.92);backdrop-filter:blur(20px);border-bottom:1px solid rgba(0,229,255,0.07)}
.nav-logo{display:flex;align-items:center;gap:10px;text-decoration:none}
.nlm{width:24px;height:24px;border:1.5px solid var(--accent);transform:rotate(45deg);display:flex;align-items:center;justify-content:center}
.nli{width:6px;height:6px;background:var(--accent);transform:rotate(-45deg)}
.nlt{font-family:'Syne',sans-serif;font-size:15px;font-weight:800;color:var(--accent);letter-spacing:0.1em}
.nav-status{display:flex;align-items:center;gap:8px;font-size:10px;letter-spacing:0.1em}
.nav-dot{width:7px;height:7px;border-radius:50%;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
.nav-back{color:var(--muted);font-size:10px;letter-spacing:0.12em;text-decoration:none;transition:color 0.2s}
.nav-back:hover{color:var(--accent)}
.demo-banner{background:linear-gradient(90deg,rgba(255,170,0,0.08),rgba(255,170,0,0.03));border-bottom:1px solid rgba(255,170,0,0.15);padding:10px 20px;text-align:center;font-size:10px;color:var(--orange);display:none;position:relative;z-index:1}
.demo-banner a{color:var(--accent);text-decoration:underline}
.container{position:relative;z-index:1;max-width:720px;margin:0 auto;padding:40px 20px 80px}
.page-hdr{text-align:center;margin-bottom:44px}
.page-hdr h1{font-family:'Syne',sans-serif;font-size:32px;font-weight:800;margin-bottom:8px}
.page-hdr h1 span{color:var(--accent)}
.page-hdr p{font-size:12px;color:var(--muted);line-height:1.8}
.tabs{display:flex;border:1px solid var(--border);margin-bottom:32px}
.tab{flex:1;padding:14px;background:transparent;border:none;color:var(--muted);font-family:'DM Mono',monospace;font-size:11px;letter-spacing:0.12em;cursor:pointer;transition:all 0.2s;display:flex;align-items:center;justify-content:center;gap:8px}
.tab.on{background:rgba(0,229,255,0.06);color:var(--accent);border-bottom:2px solid var(--accent)}
.tab:hover:not(.on){background:rgba(0,229,255,0.02)}
.panel{display:none}.panel.on{display:block}
.card{background:var(--card);border:1px solid var(--border);padding:32px;margin-bottom:20px}
.label{font-size:9px;letter-spacing:0.18em;color:var(--muted);margin-bottom:8px;display:block}
textarea{width:100%;background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:14px;font-size:13px;font-family:'DM Mono',monospace;resize:vertical;min-height:80px;transition:border-color 0.2s}
textarea:focus{outline:none;border-color:rgba(0,229,255,0.4)}
textarea::placeholder{color:var(--muted)}
.examples{margin-top:12px;display:flex;gap:6px;flex-wrap:wrap}
.ex-btn{padding:6px 12px;border:1px solid var(--border);background:transparent;color:var(--muted);font-family:'DM Mono',monospace;font-size:9px;cursor:pointer;transition:all 0.15s}
.ex-btn:hover{border-color:var(--accent);color:var(--accent);background:rgba(0,229,255,0.04)}
.style-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:8px}
.style-opt{padding:14px 10px;border:1px solid var(--border);background:transparent;color:var(--muted);font-family:'DM Mono',monospace;font-size:10px;cursor:pointer;transition:all 0.2s;text-align:center}
.style-opt:hover{border-color:rgba(0,229,255,0.3);color:var(--text)}
.style-opt.on{border-color:var(--accent);color:var(--accent);background:rgba(0,229,255,0.04)}
.style-opt .ico{font-size:20px;display:block;margin-bottom:6px}
.upload{border:2px dashed var(--border);padding:48px 24px;text-align:center;cursor:pointer;transition:all 0.3s;position:relative;overflow:hidden}
.upload:hover,.upload.drag{border-color:var(--accent);background:rgba(0,229,255,0.03)}
.upload.has{border-color:var(--accent2);border-style:solid}
.upload input{position:absolute;inset:0;opacity:0;cursor:pointer}
.upload .ico{font-size:36px;margin-bottom:12px;color:var(--accent)}
.upload p{font-size:12px;color:var(--muted)}
.upload .hint{font-size:9px;color:var(--muted);margin-top:6px}
.preview{margin-top:16px;display:none;position:relative}
.preview.on{display:block}
.preview img{max-width:100%;max-height:220px;display:block;margin:0 auto;border:1px solid var(--border)}
.preview .rm{position:absolute;top:6px;right:6px;width:28px;height:28px;background:rgba(255,68,102,0.85);border:none;color:#fff;border-radius:50%;cursor:pointer;font-size:12px}
.gen-btn{width:100%;padding:16px;background:var(--accent);color:#04080a;border:none;font-family:'DM Mono',monospace;font-size:12px;letter-spacing:0.2em;cursor:pointer;font-weight:600;transition:all 0.2s;margin-top:20px;position:relative;overflow:hidden}
.gen-btn:hover:not(:disabled){background:var(--accent2);transform:translateY(-1px);box-shadow:0 6px 20px rgba(0,229,255,0.2)}
.gen-btn:disabled{opacity:0.4;cursor:not-allowed}
.gen-btn::after{content:'';position:absolute;inset:0;background:linear-gradient(120deg,transparent 30%,rgba(255,255,255,0.12),transparent 70%);transform:translateX(-100%);transition:transform 0.4s}
.gen-btn:hover::after{transform:translateX(100%)}
.sec{display:none;margin-bottom:24px}.sec.on{display:block}
.prog-card{background:var(--card);border:1px solid var(--border);padding:28px}
.prog-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}
.prog-title{font-family:'Syne',sans-serif;font-size:15px;font-weight:700}
.prog-pct{font-family:'Syne',sans-serif;font-size:22px;font-weight:800;color:var(--accent)}
.prog-bar-bg{width:100%;height:8px;background:var(--bg2);overflow:hidden;margin-bottom:12px}
.prog-bar{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));width:0%;transition:width 0.5s}
.prog-bar::after{content:'';position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,0.2),transparent);animation:shimmer 2s infinite}
@keyframes shimmer{0%{transform:translateX(-100%)}100%{transform:translateX(100%)}}
.prog-step{font-size:11px;color:var(--muted);display:flex;align-items:center;gap:8px}
.spinner{display:inline-block;width:12px;height:12px;border:2px solid var(--muted);border-top-color:var(--accent);border-radius:50%;animation:spin 0.8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.result-card{background:var(--card);border:1px solid var(--accent2);padding:28px;text-align:center}
.result-card .ok-icon{font-size:44px;margin-bottom:10px}
.result-card h3{font-family:'Syne',sans-serif;font-size:20px;font-weight:800;margin-bottom:6px}
.result-card>p{font-size:11px;color:var(--muted);margin-bottom:20px}
.demo-note{background:rgba(255,170,0,0.08);border:1px solid rgba(255,170,0,0.2);padding:10px 14px;font-size:10px;color:var(--orange);margin-bottom:16px;display:none;line-height:1.7}
.viewer{width:100%;height:380px;background:var(--bg2);border:1px solid var(--border);margin-bottom:20px;overflow:hidden;display:flex;align-items:center;justify-content:center;position:relative}
.viewer model-viewer{width:100%;height:100%}
.viewer .vload{color:var(--muted);font-size:11px;position:absolute}
.dl-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px}
.dl-btn{padding:14px 10px;border:1px solid var(--border);background:var(--card);color:var(--text);font-family:'DM Mono',monospace;font-size:11px;letter-spacing:0.1em;cursor:pointer;transition:all 0.2s;text-decoration:none;text-align:center;display:flex;flex-direction:column;align-items:center;gap:4px}
.dl-btn:hover{border-color:var(--accent);color:var(--accent);background:rgba(0,229,255,0.04)}
.dl-btn .dl-icon{font-size:20px}
.dl-btn .dl-fmt{font-size:8px;color:var(--muted);letter-spacing:0.15em}
.dl-btn.primary{border-color:var(--accent2);background:rgba(0,255,157,0.06)}
.dl-btn.primary:hover{background:rgba(0,255,157,0.12)}
.new-btn{width:100%;padding:13px;border:1px solid var(--border);background:transparent;color:var(--text);font-family:'DM Mono',monospace;font-size:10px;letter-spacing:0.12em;cursor:pointer;transition:all 0.2s}
.new-btn:hover{border-color:var(--accent);color:var(--accent)}
.err-card{background:rgba(255,68,102,0.06);border:1px solid rgba(255,68,102,0.2);padding:28px;text-align:center}
.err-card .err-icon{font-size:40px;margin-bottom:10px}
.err-card h3{color:var(--red);font-family:'Syne',sans-serif;font-size:16px;margin-bottom:6px}
.err-card p{font-size:11px;color:var(--muted);margin-bottom:16px;line-height:1.8}
.dbg{margin-top:12px;padding:10px;background:var(--bg2);border:1px solid var(--border);font-size:9px;color:var(--muted);text-align:left;max-height:150px;overflow-y:auto;word-break:break-all;display:none}
@media(max-width:600px){.nav{padding:14px 16px}.container{padding:24px 14px}.card{padding:24px 18px}.style-grid{grid-template-columns:repeat(2,1fr)}.viewer{height:280px}.dl-grid{grid-template-columns:1fr 1fr 1fr}}
</style>
</head>
<body>
<div class="bg-grid"></div>
<div class="bg-orb bg-orb1"></div>
<div class="bg-orb bg-orb2"></div>

<nav class="nav">
  <a href="/" class="nav-logo"><div class="nlm"><div class="nli"></div></div><span class="nlt">PRINTFORGE</span></a>
  <div class="nav-status" id="apiSt"><div class="nav-dot" style="background:var(--orange)"></div><span>Kontrol ediliyor...</span></div>
  <a href="/" class="nav-back">&#8592; ANA SAYFA</a>
</nav>
<div class="demo-banner" id="demoBanner">DEMO MOD &#8212; Ornek modeller gosterilir. Gercek AI icin <a href="https://platform.tripo3d.ai" target="_blank">Tripo3D</a> API key ekleyin.</div>

<div class="container">
  <div class="page-hdr">
    <h1>Hayalini <span>3D Modele</span> Donustur</h1>
    <p>Metin yaz veya gorsel yukle, yapay zeka saniyeler icinde 3D modelini uretsin</p>
  </div>

  <div class="tabs">
    <button class="tab on" onclick="swTab('text')">&#9000; METIN ILE URET</button>
    <button class="tab" onclick="swTab('image')">&#11042; GORSEL ILE URET</button>
  </div>

  <div class="panel on" id="pText">
    <div class="card">
      <label class="label">PROMPT</label>
      <textarea id="prompt" placeholder="Orn: a cute robot toy, a medieval castle..." rows="3"></textarea>
      <div class="examples">
        <button class="ex-btn" onclick="setP('a cute cartoon robot toy')">Robot</button>
        <button class="ex-btn" onclick="setP('a medieval stone castle')">Kale</button>
        <button class="ex-btn" onclick="setP('a futuristic sports car')">Araba</button>
        <button class="ex-btn" onclick="setP('a dragon miniature figure')">Ejderha</button>
        <button class="ex-btn" onclick="setP('a geometric modern vase')">Vazo</button>
        <button class="ex-btn" onclick="setP('an astronaut helmet')">Astronot</button>
      </div>
      <label class="label" style="margin-top:20px">STIL</label>
      <div class="style-grid">
        <button class="style-opt on" data-s="realistic" onclick="selS(this)"><span class="ico">&#128247;</span>GERCEKCI</button>
        <button class="style-opt" data-s="cartoon" onclick="selS(this)"><span class="ico">&#127912;</span>CARTOON</button>
        <button class="style-opt" data-s="lowpoly" onclick="selS(this)"><span class="ico">&#128142;</span>LOW POLY</button>
        <button class="style-opt" data-s="sculpture" onclick="selS(this)"><span class="ico">&#128511;</span>HEYKEL</button>
        <button class="style-opt" data-s="mechanical" onclick="selS(this)"><span class="ico">&#9881;</span>MEKANIK</button>
        <button class="style-opt" data-s="miniature" onclick="selS(this)"><span class="ico">&#9823;</span>MINYATUR</button>
      </div>
    </div>
    <button class="gen-btn" id="txtBtn" onclick="genText()">&#9889; 3D MODEL URET</button>
  </div>

  <div class="panel" id="pImage">
    <div class="card">
      <label class="label">GORSEL YUKLE</label>
      <div class="upload" id="upArea">
        <div class="ico">&#11042;</div>
        <p>Surukle-birak veya tikla</p>
        <div class="hint">JPG / PNG / WEBP — MAX 10MB</div>
        <input type="file" id="fInp" accept="image/*" onchange="onFile(this)">
      </div>
      <div class="preview" id="prev">
        <img id="prevImg" src="" alt="Preview">
        <button class="rm" onclick="rmFile()">X</button>
      </div>
    </div>
    <button class="gen-btn" id="imgBtn" onclick="genImage()" disabled>&#9889; 3D MODEL URET</button>
  </div>

  <div class="sec" id="progSec">
    <div class="prog-card">
      <div class="prog-top">
        <span class="prog-title">Model Uretiliyor</span>
        <span class="prog-pct" id="progPct">0%</span>
      </div>
      <div class="prog-bar-bg"><div class="prog-bar" id="progBar"></div></div>
      <div class="prog-step" id="progStep"><div class="spinner"></div><span>Baslatiliyor...</span></div>
    </div>
  </div>

  <div class="sec" id="resSec">
    <div class="result-card">
      <div class="ok-icon">&#10004;</div>
      <h3>Model Hazir!</h3>
      <p>3D modeliniz basariyla olusturuldu</p>
      <div class="demo-note" id="demoNote">Demo modeli gosteriliyor. Gercek AI icin API key ekleyin.</div>
      <div class="viewer" id="viewer3d"><span class="vload">Model yukleniyor...</span></div>
      <div class="dl-grid" id="dlGrid"></div>
      <button class="new-btn" onclick="reset()">+ YENI MODEL URET</button>
      <div class="dbg" id="dbgBox"></div>
    </div>
  </div>

  <div class="sec" id="errSec">
    <div class="err-card">
      <div class="err-icon">&#9888;</div>
      <h3>Hata Olustu</h3>
      <p id="errMsg">Bilinmeyen hata</p>
      <button class="new-btn" onclick="reset()">TEKRAR DENE</button>
    </div>
  </div>
</div>

<script>
var API=window.location.origin;
var style='realistic',selFile=null,poll=null,lastTid='';

function checkApi(){
  fetch(API+'/api/health').then(function(r){return r.json()}).then(function(d){
    var el=document.getElementById('apiSt');
    if(d.is_demo){
      el.innerHTML='<div class="nav-dot" style="background:var(--orange);animation:pulse 2s infinite"></div><span style="color:var(--orange)">DEMO MOD</span>';
      document.getElementById('demoBanner').style.display='block';
    } else {
      el.innerHTML='<div class="nav-dot" style="background:var(--accent2);animation:pulse 2s infinite"></div><span style="color:var(--accent2)">'+d.active_api.toUpperCase()+' BAGLI</span>';
    }
  }).catch(function(){
    document.getElementById('apiSt').innerHTML='<div class="nav-dot" style="background:var(--red)"></div><span style="color:var(--red)">BAGLANTI HATASI</span>';
  });
}
checkApi();

function setP(t){document.getElementById('prompt').value=t}

function swTab(t){
  var tabs=document.querySelectorAll('.tab');
  tabs[0].className='tab'+(t==='text'?' on':'');
  tabs[1].className='tab'+(t==='image'?' on':'');
  document.getElementById('pText').className='panel'+(t==='text'?' on':'');
  document.getElementById('pImage').className='panel'+(t==='image'?' on':'');
}

function selS(el){
  var opts=document.querySelectorAll('.style-opt');
  for(var i=0;i<opts.length;i++) opts[i].className='style-opt';
  el.className='style-opt on';
  style=el.getAttribute('data-s');
}

var upArea=document.getElementById('upArea');
upArea.addEventListener('dragover',function(e){e.preventDefault();upArea.classList.add('drag')});
upArea.addEventListener('dragleave',function(){upArea.classList.remove('drag')});
upArea.addEventListener('drop',function(e){e.preventDefault();upArea.classList.remove('drag');
  if(e.dataTransfer.files[0]){document.getElementById('fInp').files=e.dataTransfer.files;onFile(document.getElementById('fInp'))}});

function onFile(inp){
  var f=inp.files[0];if(!f)return;
  if(f.size>10*1024*1024){alert('Max 10MB!');return}
  selFile=f;
  var rd=new FileReader();
  rd.onload=function(e){
    document.getElementById('prevImg').src=e.target.result;
    document.getElementById('prev').className='preview on';
    upArea.classList.add('has');
    document.getElementById('imgBtn').disabled=false;
  };
  rd.readAsDataURL(f);
}
function rmFile(){
  selFile=null;document.getElementById('fInp').value='';
  document.getElementById('prev').className='preview';
  upArea.classList.remove('has');
  document.getElementById('imgBtn').disabled=true;
}

function genText(){
  var p=document.getElementById('prompt').value.trim();
  if(!p){alert('Prompt girin!');return}
  showProg();disable(true);
  fetch(API+'/api/generate/text',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:p,style:style})})
  .then(function(r){if(!r.ok) return r.json().then(function(e){throw new Error(e.detail||'Hata')});return r.json()})
  .then(function(d){lastTid=d.task_id;startPoll(d.task_id)})
  .catch(function(e){showErr(e.message)});
}

function genImage(){
  if(!selFile)return;showProg();disable(true);
  var fd=new FormData();fd.append('file',selFile);
  fetch(API+'/api/generate/image',{method:'POST',body:fd})
  .then(function(r){if(!r.ok) return r.json().then(function(e){throw new Error(e.detail||'Hata')});return r.json()})
  .then(function(d){lastTid=d.task_id;startPoll(d.task_id)})
  .catch(function(e){showErr(e.message)});
}

function startPoll(tid){
  if(poll)clearInterval(poll);
  poll=setInterval(function(){
    fetch(API+'/api/status/'+tid)
    .then(function(r){return r.json()})
    .then(function(d){
      updProg(d.progress,d.step||'Isleniyor...');
      if(d.status==='done'){clearInterval(poll);showRes(tid,d)}
      else if(d.status==='failed'){clearInterval(poll);showErr(d.error||'Uretilemedi')}
    }).catch(function(e){console.error(e)});
  },2500);
}

function showProg(){hide('resSec');hide('errSec');show('progSec');updProg(0,'Baslatiliyor...')}
function updProg(p,s){
  document.getElementById('progBar').style.width=p+'%';
  document.getElementById('progPct').textContent=p+'%';
  document.getElementById('progStep').innerHTML='<div class="spinner"></div><span>'+s+'</span>';
}

function showRes(tid,d){
  hide('progSec');show('resSec');disable(false);

  if(d.is_demo) document.getElementById('demoNote').style.display='block';
  else document.getElementById('demoNote').style.display='none';

  // 3D Viewer — backend proxy uzerinden (CORS sorunu yok)
  var viewUrl=API+'/api/model/'+tid+'/view';
  var v=document.getElementById('viewer3d');
  v.innerHTML='<model-viewer src="'+viewUrl+'" auto-rotate camera-controls interaction-prompt="none" style="width:100%;height:100%;background:#070d10" loading="eager" shadow-intensity="1" environment-image="neutral" exposure="1.1" camera-orbit="45deg 55deg auto"></model-viewer>';

  // Indirme butonlari — GLB + STL + OBJ
  var glbUrl=API+'/api/model/'+tid+'/glb';
  var stlUrl=API+'/api/model/'+tid+'/stl';
  var objUrl=API+'/api/model/'+tid+'/obj';

  var html='';
  html+='<a class="dl-btn primary" href="'+glbUrl+'" download="printforge_'+tid+'.glb">';
  html+='<span class="dl-icon">&#11015;</span>GLB<span class="dl-fmt">3D VIEWER</span></a>';
  html+='<a class="dl-btn" href="'+stlUrl+'" download="printforge_'+tid+'.stl">';
  html+='<span class="dl-icon">&#11015;</span>STL<span class="dl-fmt">3D BASKI</span></a>';
  html+='<a class="dl-btn" href="'+objUrl+'" download="printforge_'+tid+'.obj">';
  html+='<span class="dl-icon">&#11015;</span>OBJ<span class="dl-fmt">MODELLEME</span></a>';
  document.getElementById('dlGrid').innerHTML=html;
}

function showErr(m){hide('progSec');show('errSec');disable(false);document.getElementById('errMsg').textContent=m}

function reset(){
  if(poll)clearInterval(poll);
  hide('progSec');hide('resSec');hide('errSec');disable(false);
  document.getElementById('demoNote').style.display='none';
  document.getElementById('viewer3d').innerHTML='<span class="vload">Model yukleniyor...</span>';
  document.getElementById('dbgBox').style.display='none';
}

function show(id){document.getElementById(id).classList.add('on')}
function hide(id){document.getElementById(id).classList.remove('on')}
function disable(v){document.getElementById('txtBtn').disabled=v;document.getElementById('imgBtn').disabled=v||!selFile}
</script>
</body>
</html>"""
