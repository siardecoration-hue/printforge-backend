from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import random
import uuid
import httpx
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

tasks = {}

COLAB_URL = os.getenv("COLAB_URL", "https://hanna-keratoid-indistinguishably.ngrok-free.app")
NGROK_HEADER = {"ngrok-skip-browser-warning": "true"}

class TextRequest(BaseModel):
    prompt: str
    style: str = "geometric"

@app.get("/")
def root():
    return {"status": "OK", "mode": "triposr", "colab": COLAB_URL}

@app.post("/generate/image")
async def generate_from_image(file: UploadFile = File(...)):
    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {"status": "pending", "progress": 0}
    contents = await file.read()
    asyncio.create_task(forward_to_colab(task_id, contents, file.filename))
    return {"task_id": task_id}

async def forward_to_colab(task_id, contents, filename):
    try:
        tasks[task_id]["status"] = "in_progress"
        tasks[task_id]["progress"] = 10
        async with httpx.AsyncClient(timeout=300) as client:
            files = {"file": (filename, contents, "image/jpeg")}
            res = await client.post(
                f"{COLAB_URL}/generate/image",
                files=files,
                headers=NGROK_HEADER
            )
            data = res.json()
            colab_task_id = data["task_id"]
            tasks[task_id]["progress"] = 30

            while True:
                await asyncio.sleep(3)
                status_res = await client.get(
                    f"{COLAB_URL}/status/{colab_task_id}",
                    headers=NGROK_HEADER
                )
                status_data = status_res.json()
                progress = status_data.get("progress", 0)
                tasks[task_id]["progress"] = 30 + int(progress * 0.6)

                if status_data["status"] == "succeeded":
                    tasks[task_id]["status"] = "succeeded"
                    tasks[task_id]["progress"] = 100
                    tasks[task_id]["download_url"] = f"{COLAB_URL}/download/{colab_task_id}"
                    tasks[task_id]["stats"] = {
                        "vertices": random.randint(8000, 45000),
                        "faces": random.randint(16000, 90000),
                        "size_mb": round(random.uniform(0.8, 4.5), 1),
                        "print_time": f"{random.randint(0,3)}h {random.randint(10,59)}m",
                        "support_needed": random.choice([True, False]),
                        "infill": f"{random.randint(15, 40)}%",
                    }
                    break
                elif status_data["status"] == "failed":
                    tasks[task_id]["status"] = "failed"
                    tasks[task_id]["error"] = status_data.get("error", "Bilinmeyen hata")
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
