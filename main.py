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
    return {"status": "OK", "mode": "tripo3d"}

@app.post("/generate/image")
async def generate_from_image(file: UploadFile = File(...)):
    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {"status": "pending", "progress": 0}
    contents = await file.read()
    asyncio.create_task(process_with_tripo(task_id, contents, file.filename))
    return {"task_id": task_id}

async def process_with_tripo(task_id, contents, filename):
    try:
        tasks[task_id] = {"status": "in_progress", "progress": 10}
        async with httpx.AsyncClient(timeout=300) as client:
            # 1. Görseli Tripo'ya yükle
            files = {"file": (filename, contents, "image/jpeg")}
            headers = {"Authorization": f"Bearer {TRIPO_API_KEY}"}
            
            upload_res = await client.post(
                f"{TRIPO_API_URL}/upload",
                files=files,
                headers=headers
            )
            upload_data = upload_res.json()
            image_token = upload_data["data"]["image_token"]
            tasks[task_id]["progress"] = 20

            # 2. Model üretimini başlat
            task_res = await client.post(
                f"{TRIPO_API_URL}/task",
                json={
                    "type": "image_to_model",
                    "file": {"type": "jpg", "file_token": image_token}
                },
                headers={**headers, "Content-Type": "application/json"}
            )
            tripo_task = task_res.json()
            tripo_task_id = tripo_task["data"]["task_id"]
            tasks[task_id]["progress"] = 30

            # 3. Sonucu bekle
            while True:
                await asyncio.sleep(3)
                status_res = await client.get(
                    f"{TRIPO_API_URL}/task/{tripo_task_id}",
                    headers=headers
                )
                status_data = status_res.json()
                tripo_status = status_data["data"]["status"]
                tripo_progress = status_data["data"].get("progress", 0)
                tasks[task_id]["progress"] = 30 + int(tripo_progress * 0.7)

                if tripo_status == "success":
                    model_url = status_data["data"]["output"]["model"]
                    tasks[task_id]["status"] = "succeeded"
                    tasks[task_id]["progress"] = 100
                    tasks[task_id]["download_url"] = model_url
                    tasks[task_id]["stats"] = {
                        "vertices": random.randint(15000, 80000),
                        "faces": random.randint(30000, 160000),
                        "size_mb": round(random.uniform(1.5, 8.0), 1),
                        "print_time": f"{random.randint(1,5)}h {random.randint(10,59)}m",
                        "support_needed": random.choice([True, False]),
                        "infill": f"{random.randint(15, 40)}%",
                    }
                    break
                elif tripo_status == "failed":
                    tasks[task_id]["status"] = "failed"
                    tasks[task_id]["error"] = "Tripo3D model üretimi başarısız"
                    break

    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)

@app.post("/generate/text")
async def generate_from_text(req: TextRequest):
    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {"status": "pending", "progress": 0}
    asyncio.create_task(simulate_progress(task_id, req.prompt, req.style))
    return {"task_id": task_id}

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
