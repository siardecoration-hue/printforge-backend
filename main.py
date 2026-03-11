from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import random
import uuid

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

tasks = {}

class TextRequest(BaseModel):
    prompt: str
    style: str = "geometric"

@app.get("/")
def root():
    return {"status": "PrintForge API çalışıyor ✅", "mode": "mock"}

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
