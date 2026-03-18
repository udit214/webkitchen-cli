from fastapi import FastAPI, UploadFile, File, Form
from pathlib import Path
import shutil
import json
import hashlib
from fastapi.responses import FileResponse
import zipfile
from jose import JWTError, jwt
from datetime import datetime, timedelta
from passlib.context import CryptContext
from fastapi import Header, HTTPException
from fastapi import Depends, Header, HTTPException
from webkitchen.wk_server.auth import (
    create_access_token,
    verify_token,
    verify_password,
    hash_password
)

app = FastAPI()

SECRET_KEY = "asdfajgfnoieghklmdvs0934"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
SERVER_ROOT = Path("projects")
SERVER_ROOT.mkdir(exist_ok=True)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_current_user(authorization: str = Header(None)):

    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")

    token = authorization.replace("Bearer ", "")
    payload = verify_token(token)

    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")

    return payload





def get_hash(path: Path):
    hasher = hashlib.md5()

    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hasher.update(chunk)

    return hasher.hexdigest()


@app.post("/project/upload")
async def upload_file(
    project_code: str = Form(...),
    relative_path: str = Form(...),
    file: UploadFile = File(...),
    user=Depends(get_current_user)
):

    project = SERVER_ROOT / project_code

    if not project.exists():
        return {"error": "project_not_found"}

    main = project / "main"
    hashes_file = project / "hashes.json"

    dest = main / relative_path
    dest.parent.mkdir(parents=True, exist_ok=True)

    with open(dest, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # ------------------------
    # Update server hashes
    # ------------------------

    new_hash = get_hash(dest)

    if hashes_file.exists():

        with open(hashes_file) as f:
            hashes = json.load(f)

    else:
        hashes = {}

    hashes[str(relative_path)] = new_hash

    with open(hashes_file, "w") as f:
        json.dump(hashes, f, indent=4)

    return {
        "status": "uploaded",
        "path": relative_path,
        "hash": new_hash
    }

@app.get("/project/hashes/{project_code}")
def get_hashes(project_code: str):

    project = SERVER_ROOT / project_code
    hashes_file = project / "hashes.json"

    if not hashes_file.exists():
        return {}

    with open(hashes_file) as f:
        hashes = json.load(f)

    return hashes

@app.get("/project/hashes/{project_code}")
def get_project_hashes(project_code: str):

    project = SERVER_ROOT / project_code
    hashes_file = project / "hashes.json"

    if not project.exists():
        return {"error": "project_not_found"}

    if not hashes_file.exists():
        return {}

    with open(hashes_file) as f:
        hashes = json.load(f)

    return hashes

@app.post("/project/create")
def create_project(project_code: str):

    project = SERVER_ROOT / project_code

    if project.exists():
        return {"status": "exists"}

    # create project structure
    (project / "main").mkdir(parents=True)
    (project / "updates").mkdir()

    # create collaborators file
    with open(project / "collaborators.json", "w") as f:
        json.dump([], f, indent=4)

    # initialize server hashes
    with open(project / "hashes.json", "w") as f:
        json.dump({}, f, indent=4)

    return {"status": "created"}

@app.post("/project/join")
def join_project(data: dict):

    project_code = data["project_code"]
    username = data["username"]
    password = data["password"]

    project = SERVER_ROOT / project_code
    collab_file = project / "collaborators.json"

    if not collab_file.exists():
        return {"error": "project_not_found"}

    with open(collab_file) as f:
        collabs = json.load(f)

    for c in collabs:
        if c["username"] == username and c["password"] == password:
            return {"status": "ok"}

    return {"error": "auth_failed"}

@app.get("/project/download/{project_code}")
def download_project(project_code: str):

    project = SERVER_ROOT / project_code
    main = project / "main"

    if not main.exists():
        return {"error": "project_not_found"}

    zip_path = project / "project.zip"

    with zipfile.ZipFile(zip_path, "w") as zipf:
        for file in main.rglob("*"):
            if file.is_file():
                zipf.write(file, file.relative_to(main))

    return FileResponse(zip_path, filename="project.zip")


@app.get("/updates/{project_code}")
def list_updates(project_code: str):

    project = SERVER_ROOT / project_code
    updates_dir = project / "updates"

    if not updates_dir.exists():
        return []

    updates = []

    for file in updates_dir.glob("*.json"):
        with open(file) as f:
            updates.append(json.load(f))

    return updates

@app.get("/project/file/{project_code}")
def get_file(project_code: str, path: str):

    project = SERVER_ROOT / project_code
    file_path = project / "main" / path

    if not file_path.exists():
        return {"error": "file_not_found"}

    return FileResponse(file_path)

@app.post("/project/upload")
async def upload_file(
    project_code: str = Form(...),
    relative_path: str = Form(...),
    file: UploadFile = File(...),
    user=Depends(get_current_user)
):

    project = SERVER_ROOT / project_code
    main = project / "main"

    dest = main / relative_path
    dest.parent.mkdir(parents=True, exist_ok=True)

    with open(dest, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # update hashes
    hashes_file = project / "hashes.json"

    if hashes_file.exists():
        with open(hashes_file) as f:
            hashes = json.load(f)
    else:
        hashes = {}

    hashes[str(relative_path)] = get_hash(dest)

    with open(hashes_file, "w") as f:
        json.dump(hashes, f, indent=4)

    return {"status": "uploaded"}

@app.post("/update/publish")
def publish_update(data: dict):

    project_code = data["project_code"]
    update = data["update"]

    project = SERVER_ROOT / project_code
    updates_dir = project / "updates"

    updates_dir.mkdir(exist_ok=True)

    update_file = updates_dir / f"{update['name']}.json"

    with open(update_file, "w") as f:
        json.dump(update, f, indent=4)

    return {"status": "stored"}



@app.post("/auth/login")
def login(data: dict):

    username = data.get("username")
    password = data.get("password")
    project_code = data.get("project_code")

    project = SERVER_ROOT / project_code
    collab_file = project / "collaborators.json"

    if not collab_file.exists():
        return {"error": "project_not_found"}

    with open(collab_file) as f:
        collabs = json.load(f)

    for c in collabs:
        if c["username"] == username and verify_password(password, c["password"]):

            token = create_access_token({
                "username": username,
                "project_code": project_code
            })

            return {"access_token": token}

    return {"error": "invalid_credentials"}

@app.post("/auth/register")
def register(data: dict):

    try:
        username = str(data.get("username"))
        password = str(data.get("password"))
        project_code = str(data.get("project_code"))

        print("PASSWORD:", password)
        print("LENGTH:", len(password))

        if len(password) > 72:
            password = password[:72]

        project = SERVER_ROOT / project_code

        if not project.exists():
            return {"error": "project_not_found"}

        collab_file = project / "collaborators.json"

        if not collab_file.exists():
            with open(collab_file, "w") as f:
                json.dump([], f)

        with open(collab_file) as f:
            collabs = json.load(f)

        for c in collabs:
            if c["username"] == username:
                return {"error": "user_exists"}

        hashed = hash_password(password)

        collabs.append({
            "username": username,
            "password": hashed
        })

        with open(collab_file, "w") as f:
            json.dump(collabs, f, indent=4)

        return {"status": "registered"}

    except Exception as e:
        print("🔥 REGISTER ERROR:", str(e))
        return {"error": "server_crash"}