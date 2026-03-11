from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from gradio_client import Client
import trimesh
import asyncio
import uuid
import os

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

tasks = {}
TRELLIS_CLIENT = Client("JeffreyXiang/TRELLIS")

@app.get("/")
def root():
    return {"status": "OK", "mode": "trellis"}

@app.post("/generate/image")
async def generate_from_image(file: UploadFile = File(...)):
    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {"status": "pending", "progress": 0}
    image_path = f"/tmp/{task_id}_{file.filename}"
    with open(image_path, "wb") as f:
        f.write(await file.read())
    asyncio.create_task(process_image(task_id, image_path))
    return {"task_id": task_id}

async def process_image(task_id, image_path):
    try:
        tasks[task_id]["status"] = "in_progress"
        tasks[task_id]["progress"] = 10

        # Adım 1: Görseli işle
        preprocessed = await asyncio.to_thread(
            TRELLIS_CLIENT.predict,
            image_path,
            api_name="/preprocess_image"
        )
        tasks[task_id]["progress"] = 30

        # Adım 2: 3D oluştur
        await asyncio.to_thread(
            TRELLIS_CLIENT.predict,
            preprocessed,
            [],
            0,
            7.5,
            12,
            3.0,
            12,
            "stochastic",
            api_name="/image_to_3d"
        )
        tasks[task_id]["progress"] = 70

        # Adım 3: GLB dosyasını al
        result = await asyncio.to_thread(
            TRELLIS_CLIENT.predict,
            0.95,
            1024,
            api_name="/extract_glb"
        )
        tasks[task_id]["progress"] = 90

        # GLB'yi STL'e çevir
        glb_path = result[1] if isinstance(result, tuple) else result
        stl_path = f"/tmp/{task_id}.stl"
        mesh = trimesh.load(glb_path)
        mesh.export(stl_path)

        tasks[task_id]["status"] = "succeeded"
        tasks[task_id]["progress"] = 100
        tasks[task_id]["stl_path"] = stl_path

    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)

@app.get("/status/{task_id}")
async def get_status(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Görev bulunamadı")
    t = tasks[task_id]
    return {
        "task_id": task_id,
        "status": t["status"],
        "progress": t["progress"],
        "error": t.get("error", None)
    }

@app.get("/download/{task_id}")
async def download_stl(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Görev bulunamadı")
    stl_path = tasks[task_id].get("stl_path")
    if not stl_path or not os.path.exists(stl_path):
        raise HTTPException(status_code=404, detail="Dosya hazır değil")
    return FileResponse(stl_path, filename=f"{task_id}.stl", media_type="application/octet-stream")
