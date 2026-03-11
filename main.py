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
        tasks[task_id]["progress"] = 20

        result = await asyncio.to_thread(
            TRELLIS_CLIENT.predict,
            image_path,
            api_name="/image_to_3d"
        )
        
        tasks[task_id]["progress"] = 70

        glb_path = result[0] if isinstance(result, list) else result
        stl_path = f"/tmp/{task_id}.stl"
        mesh = trimesh.load(glb_path)
        mesh.export(stl_path)

        tasks[task_id]["status"] = "succeeded"
        tasks[task_id]["progress"] = 100
        tasks[task_id]["stl_path"] = stl_path

    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_
