from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import httpx
import random
import uuid
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ───────────────────────────────────────────────
# Colab'daki TripoSR ngrok URL'sini buraya yapıştır
# Örnek: https://xxxx-xx-xx-xxx-xx.ngrok-free.app
TRIPOSR_URL = os.getenv("TRIPOSR_URL", "").rstrip("/")
# ───────────────────────────────────────────────

# Aktif görevleri bellekte tut
tasks = {}

class TextRequest(BaseModel):
    prompt: str
    style: str = "geometric"

def is_triposr_enabled():
    return bool(TRIPOSR_URL)

@app.get("/")
def root():
    mode = "triposr" if is_triposr_enabled() else "mock"
    return {
        "status": "PrintForge API çalışıyor ✅",
        "mode": mode,
        "triposr_url": TRIPOSR_URL or "bağlı değil"
    }

@app.post("/generate/text")
async def generate_from_text(req: TextRequest):
    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {
        "status": "pending",
        "progress": 0,
        "prompt": req.prompt,
        "style": req.style
    }

    if is_triposr_enabled():
        # Gerçek TripoSR modu
        asyncio.create_task(triposr_generate(task_id, req.prompt, req.style))
    else:
        # Mock mod (Colab bağlı değilken test için)
        asyncio.create_task(simulate_progress(task_id, req.prompt, req.style))

    return {"task_id": task_id, "mode": "triposr" if is_triposr_enabled() else "mock"}

# ── TripoSR Modu ──────────────────────────────
async def triposr_generate(task_id: str, prompt: str, style: str):
    """Colab'daki TripoSR API'ye isteği ilet, ilerlemesini takip et."""
    try:
        tasks[task_id]["status"] = "in_progress"
        tasks[task_id]["progress"] = 5

        async with httpx.AsyncClient(timeout=30) as client:
            # Colab'a üretim isteği gönder
            resp = await client.post(
                f"{TRIPOSR_URL}/generate/text",
                json={"prompt": prompt, "style": style}
            )
            resp.raise_for_status()
            colab_task_id = resp.json()["task_id"]

        tasks[task_id]["colab_task_id"] = colab_task_id
        tasks[task_id]["progress"] = 10

        # Colab'ı 5 dakikaya kadar polling yap
        for _ in range(120):
            await asyncio.sleep(3)
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    status_resp = await client.get(
                        f"{TRIPOSR_URL}/status/{colab_task_id}"
                    )
                    status_resp.raise_for_status()
                    data = status_resp.json()

                colab_status = data.get("status")
                colab_progress = data.get("progress", 0)

                # İlerlemeyi 10–95 arasında yansıt
                tasks[task_id]["progress"] = 10 + int(colab_progress * 0.85)

                if colab_status == "succeeded":
                    tasks[task_id]["status"] = "succeeded"
                    tasks[task_id]["progress"] = 100
                    tasks[task_id]["model_urls"] = data.get("model_urls", {})
                    tasks[task_id]["stats"] = {
                        **data.get("stats", {}),
                        "print_time": _estimate_print_time(data.get("stats", {})),
                        "support_needed": random.choice([True, False]),
                        "infill": f"{random.randint(15, 40)}%",
                    }
                    return

                elif colab_status == "failed":
                    raise Exception("TripoSR üretimi başarısız oldu")

            except httpx.RequestError:
                # Geçici bağlantı hatası, tekrar dene
                continue

        raise Exception("Zaman aşımı: 6 dakika içinde tamamlanamadı")

    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)
        print(f"[{task_id}] HATA: {e}")

def _estimate_print_time(stats: dict) -> str:
    faces = stats.get("faces", 30000)
    minutes = int(faces / 1000) + random.randint(10, 30)
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m" if hours > 0 else f"{mins}m"

# ── Mock Mod (Colab bağlı değilken) ──────────
async def simulate_progress(task_id: str, prompt: str, style: str):
    for progress in range(10, 101, 10):
        await asyncio.sleep(1.5)
        tasks[task_id]["progress"] = progress
        tasks[task_id]["status"] = "in_progress"

    tasks[task_id]["status"] = "succeeded"
    tasks[task_id]["progress"] = 100
    tasks[task_id]["model_urls"] = {
        "stl": f"https://mock-cdn.printforge.io/models/{task_id}.stl",
        "obj": f"https://mock-cdn.printforge.io/models/{task_id}.obj",
    }
    tasks[task_id]["stats"] = {
        "vertices": random.randint(8000, 45000),
        "faces": random.randint(16000, 90000),
        "size_mb": round(random.uniform(0.8, 4.5), 1),
        "print_time": f"{random.randint(0,3)}h {random.randint(10,59)}m",
        "support_needed": random.choice([True, False]),
        "infill": f"{random.randint(15, 40)}%",
        "prompt": prompt,
        "style": style,
    }

# ── Status Endpoint ───────────────────────────
@app.get("/status/{task_id}")
async def get_status(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Görev bulunamadı")
    t = tasks[task_id]
    return {
        "task_id": task_id,
        "status": t["status"],
        "progress": t["progress"],
        "model_urls": t.get("model_urls", {}),
        "stats": t.get("stats", {}),
        "error": t.get("error"),
    }
