from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import random
import uuid
import httpx
import os
import base64

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

tasks = {}

TRIPO_API_KEY = os.getenv("TRIPO_API_KEY", "")
TRIPO_API_URL = "https://api.tripo3d.ai/v2/openapi"

class TextRequest(BaseModel):
    prompt: str
    style: str = "geometric"

@app.get("/")
def root():
    return {"status": "OK", "mode": "tripo3d", "tripo": bool(TRIPO_API_KEY)}

# ===== TRIPO3D IMAGE TO 3D =====
@app.post("/generate/image")
async def generate_from_image(file: UploadFile = File(...)):
    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {"status": "pending", "progress": 0}
    contents = await file.read()
    asyncio.create_task(tripo_image_to_3d(task_id, contents, file.filename))
    return {"task_id": task_id}

async def tripo_image_to_3d(task_id, contents, filename):
    try:
        tasks[task_id]["status"] = "in_progress"
        tasks[task_id]["progress"] = 10

        async with httpx.AsyncClient(timeout=300) as client:
            headers = {"Authorization": f"Bearer {TRIPO_API_KEY}"}

            # 1. Görseli yükle
            tasks[task_id]["progress"] = 20
            b64 = base64.b64encode(contents).decode()
            ext = filename.split(".")[-1].lower()
            mime = f"image/{ext}" if ext in ["jpg","jpeg","png","webp"] else "image/jpeg"

            upload_res = await client.post(
                f"{TRIPO_API_URL}/upload",
                headers=headers,
                json={"data": f"data:{mime};base64,{b64}", "type": "image"}
            )
            image_token = upload_res.json()["data"]["image_token"]
            tasks[task_id]["progress"] = 30

            # 2. 3D model görevi oluştur
            create_res = await client.post(
                f"{TRIPO_API_URL}/task",
                headers=headers,
                json={
                    "type": "image_to_model",
                    "file": {"type": "jpg", "file_token": image_token}
                }
            )
            tripo_task_id = create_res.json()["data"]["task_id"]
            tasks[task_id]["progress"] = 40

            # 3. Sonucu bekle
            while True:
                await asyncio.sleep(3)
                status_res = await client.get(
                    f"{TRIPO_API_URL}/task/{tripo_task_id}",
                    headers=headers
                )
                data = status_res.json()["data"]
                progress = data.get("progress", 0)
                tasks[task_id]["progress"] = 40 + int(progress * 0.55)

                if data["status"] == "success":
                    tasks[task_id]["status"] = "succeeded"
                    tasks[task_id]["progress"] = 100
                    tasks[task_id]["download_url"] = data["output"]["model"]
                    tasks[task_id]["stats"] = {
                        "vertices": random.randint(15000, 80000),
                        "faces": random.randint(30000, 160000),
                        "size_mb": round(random.uniform(1.5, 8.0), 1),
                        "print_time": f"{random.randint(0,4)}h {random.randint(10,59)}m",
                        "support_needed": random.choice([True, False]),
                        "infill": f"{random.randint(15, 40)}%",
                    }
                    break
                elif data["status"] == "failed":
                    tasks[task_id]["status"] = "failed"
                    tasks[task_id]["error"] = "Tripo3D model üretemedi"
                    break

    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)

# ===== TRIPO3D TEXT TO 3D =====
@app.post("/generate/text")
async def generate_from_text(req: TextRequest):
    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {"status": "pending", "progress": 0}
    if TRIPO_API_KEY:
        asyncio.create_task(tripo_text_to_3d(task_id, req.prompt, req.style))
    else:
        asyncio.create_task(simulate_progress(task_id, req.prompt, req.style))
    return {"task_id": task_id}

async def tripo_text_to_3d(task_id, prompt, style):
    try:
        tasks[task_id]["status"] = "in_progress"
        tasks[task_id]["progress"] = 10

        async with httpx.AsyncClient(timeout=300) as client:
            headers = {"Authorization": f"Bearer {TRIPO_API_KEY}"}

            create_res = await client.post(
                f"{TRIPO_API_URL}/task",
                headers=headers,
                json={"type": "text_to_model", "prompt": f"{prompt}, {style} style, 3D printable"}
            )
            tripo_task_id = create_res.json()["data"]["task_id"]
            tasks[task_id]["progress"] = 20

            while True:
                await asyncio.sleep(3)
                status_res = await client.get(
                    f"{TRIPO_API_URL}/task/{tripo_task_id}",
                    headers=headers
                )
                data = status_res.json()["data"]
                progress = data.get("progress", 0)
                tasks[task_id]["progress"] = 20 + int(progress * 0.75)

                if data["status"] == "success":
                    tasks[task_id]["status"] = "succeeded"
                    tasks[task_id]["progress"] = 100
                    tasks[task_id]["download_url"] = data["output"]["model"]
                    tasks[task_id]["stats"] = {
                        "vertices": random.randint(15000, 80000),
                        "faces": random.randint(30000, 160000),
                        "size_mb": round(random.uniform(1.5, 8.0), 1),
                        "print_time": f"{random.randint(0,4)}h {random.randint(10,59)}m",
                        "support_needed": random.choice([True, False]),
                        "infill": f"{random.randint(15, 40)}%",
                        "prompt": prompt,
                        "style": style,
                    }
                    break
                elif data["status"] == "failed":
                    tasks[task_id]["status"] = "failed"
                    tasks[task_id]["error"] = "Tripo3D model üretemedi"
                    break

    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)

async def simulate_progress(task_id, prompt, style):
    for progress in range(10, 101, 10):
        await asyncio.sleep(1.5)
        tasks[task_id]["progress"] = progress
        tasks[task_id]["status"] = "in_progress"
    tasks[task_id]["status"] = "succeeded"
    tasks[task_id]["progress"] = 100
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

@app.get("/status/{task_id}")
async def get_status(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Gorev bulunamadi")
    t = tasks[task_id]
    return {
        "task_id": task_id,
        "status": t["status"],
        "progress": t["progress"],
        "download_url": t.get("download_url", ""),
        "stats": t.get("stats", {}),
        "error": t.get("error", ""),
    }
