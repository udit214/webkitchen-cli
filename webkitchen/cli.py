import typer
import json
import hashlib
from pathlib import Path
import shutil
import difflib
import smtplib
from email.message import EmailMessage
import uuid
import requests
import os

app = typer.Typer(help="WebKitchen - Collaborative Dev Tool")

WK_DIR = ".wk"
CONFIG_PATH = Path("webkitchen/.wk/config.json")
HASH_PATH = Path("webkitchen/.wk/hashes.json")
WK_DIR = Path("webkitchen/.wk")
CONFIG_PATH = WK_DIR / "config.json"
HASH_PATH = WK_DIR / "hashes.json"
SNAPSHOT_DIR = WK_DIR / "snapshots"
UPDATE_DIR = WK_DIR / "updates"
STAGED_PATH = WK_DIR / "staged.json"
LOCK_PATH = WK_DIR / "locks.json"
SESSION_PATH = WK_DIR / "session.json"
SERVER_ROOT = Path.home() / "WebKitchenServer" / "projects"
SERVER_URL = "webkitchen-cli.railway.internal"




def get_hash(path: Path):
    hasher = hashlib.md5()

    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hasher.update(chunk)

    return hasher.hexdigest()


def get_all_files():

    workspace = Path("workspace")

    if not workspace.exists():
        return []

    return [p for p in workspace.rglob("*") if p.is_file()]


def generate_hash_map():
    files = get_all_files()

    data = {}

    for f in files:
        data[str(f)] = get_hash(f)

    return data


@app.command("startproject")
def startproject(name: str):

    project_path = Path(name)
    project_code = "WK-" + uuid.uuid4().hex[:6].upper()

    if project_path.exists():
        typer.echo("Project folder already exists.")
        return

    # -------------------------
    # 🔐 ADMIN SETUP
    # -------------------------
    username = typer.prompt("Admin Username")

    password = typer.prompt("Password", hide_input=True)
    confirm = typer.prompt("Confirm Password", hide_input=True)

    if password != confirm:
        typer.secho("❌ Passwords do not match.", fg=typer.colors.RED)
        return

    # -------------------------
    # Create local structure
    # -------------------------
    project_path.mkdir()
    (project_path / "workspace").mkdir()

    wk = project_path / "webkitchen" / ".wk"
    wk.mkdir(parents=True)

    (wk / "snapshots").mkdir()
    (wk / "updates").mkdir()

    # -------------------------
    # Create project on server
    # -------------------------
    try:
        response = requests.post(
            f"{SERVER_URL}/project/create",
            params={"project_code": project_code},
            timeout=10
        )

        if response.status_code != 200:
            typer.secho(f"❌ Server error ({response.status_code})", fg=typer.colors.RED)
            typer.echo(response.text)
            return

        data = response.json()

        if data.get("status") not in ["created", "exists"]:
            typer.secho("❌ Server failed to create project.", fg=typer.colors.RED)
            typer.echo(data)
            return

    except Exception as e:
        typer.secho("❌ Cannot connect to WebKitchen server.", fg=typer.colors.RED)
        typer.echo(str(e))
        return

    # -------------------------
    # 🔐 REGISTER ADMIN
    # -------------------------
    try:
        r = requests.post(
            f"{SERVER_URL}/auth/register",
            json={
                "project_code": project_code,
                "username": username,
                "password": password
            },
            timeout=10
        )

        if r.status_code != 200:
            typer.secho("❌ Failed to register admin.", fg=typer.colors.RED)
            typer.echo(r.text)
            return

        data = r.json()

        if data.get("status") != "registered":
            typer.secho("❌ Admin registration failed.", fg=typer.colors.RED)
            typer.echo(data)
            return

    except Exception as e:
        typer.secho("❌ Error during admin registration.", fg=typer.colors.RED)
        typer.echo(str(e))
        return

    # -------------------------
    # 🔐 LOGIN ADMIN
    # -------------------------
    token = None
    try:
        r = requests.post(
            f"{SERVER_URL}/auth/login",
            json={
                "project_code": project_code,
                "username": username,
                "password": password
            },
            timeout=10
        )

        if r.status_code == 200:
            data = r.json()
            token = data.get("access_token")

        if not token:
            typer.secho("❌ Failed to login admin.", fg=typer.colors.RED)
            typer.echo(data)
            return

    except Exception as e:
        typer.secho("❌ Error during admin login.", fg=typer.colors.RED)
        typer.echo(str(e))
        return

    # -------------------------
    # Save config
    # -------------------------
    config = {
        "project_name": name,
        "project_code": project_code,
        "server_url": SERVER_URL,
        "owner": username,
        "current_update": None,
        "updates": [],
        "snapshot": 0,
        "token": token,   # 🔥 IMPORTANT
        "collaborators": []
    }

    with open(wk / "config.json", "w") as f:
        json.dump(config, f, indent=4)

    # Hash tracking
    with open(wk / "hashes.json", "w") as f:
        json.dump({}, f)

    # Staging
    with open(wk / "staged.json", "w") as f:
        json.dump({}, f)

    # Locks
    with open(wk / "locks.json", "w") as f:
        json.dump({}, f)

    typer.secho("🍳 WebKitchen project created.", fg=typer.colors.GREEN)
    typer.echo(f"Project Code: {project_code}")
    typer.echo(f"Admin: {username}")
    typer.echo(f"Server: {SERVER_URL}")

@app.command("startupdate")
def startupdate(name: str):
    stage_changes()
    if not CONFIG_PATH.exists():
        typer.echo("Not a WebKitchen project.")
        return

    with open(CONFIG_PATH) as f:
        config = json.load(f)

    updates = config.get("updates", [])

    if name in updates:
        typer.echo("Update already exists.")
        return

    updates.append(name)

    config["updates"] = updates
    config["current_update"] = name

    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=4)

    UPDATE_DIR.mkdir(parents=True, exist_ok=True)

    update_file = UPDATE_DIR / f"{name}.json"

    data = {
        "name": name,
        "files": [],
        "status": "in-progress"
    }

    with open(update_file, "w") as f:
        json.dump(data, f, indent=4)

    typer.secho(f"🚀 Update '{name}' created and activated.", fg=typer.colors.GREEN)

def stage_changes():

    if not CONFIG_PATH.exists():
        return

    if not STAGED_PATH.exists():
        with open(STAGED_PATH, "w") as f:
            json.dump({}, f)

    with open(CONFIG_PATH) as f:
        config = json.load(f)

    current = config.get("current_update")

    if not current:
        return

    with open(STAGED_PATH) as f:
        staged = json.load(f)

    if HASH_PATH.exists():
        with open(HASH_PATH) as f:
            old_hashes = json.load(f)
    else:
        old_hashes = {}

    current_hashes = generate_hash_map()

    for path, new_hash in current_hashes.items():

        old_hash = old_hashes.get(path)

        # New file
        if path not in old_hashes:

            staged[path] = current

        # Modified file
        elif old_hash != new_hash:

            staged[path] = current

        else:
            continue

        update_file = UPDATE_DIR / f"{current}.json"

        if update_file.exists():

            with open(update_file) as f:
                data = json.load(f)

            if path not in data["files"]:
                data["files"].append(path)

            with open(update_file, "w") as f:
                json.dump(data, f, indent=4)

    with open(STAGED_PATH, "w") as f:
        json.dump(staged, f, indent=4)

@app.command("status")
def status():
    stage_changes()
    """
    wk status
    """

    if not CONFIG_PATH.exists():
        typer.echo("Not a WebKitchen project.")
        return

    with open(CONFIG_PATH) as f:
        config = json.load(f)

    with open(HASH_PATH) as f:
        old_hashes = json.load(f)

    typer.echo(f"\nProject: {config['project_name']}")
    typer.echo(f"Current Update: {config['current_update']}")
    typer.echo(f"Snapshot: {config['snapshot']}")

    current_hashes = generate_hash_map()

    added = []
    modified = []
    deleted = []

    for path, h in current_hashes.items():

        if path not in old_hashes:
            added.append(path)

        elif old_hashes[path] != h:
            modified.append(path)

    for path in old_hashes:

        if path not in current_hashes:
            deleted.append(path)

    typer.echo("\nChanges:")

    for f in added:
        typer.secho(f" (+) {f}", fg=typer.colors.GREEN)

    for f in modified:
        typer.secho(f" (m) {f}", fg=typer.colors.YELLOW)

    for f in deleted:
        typer.secho(f" (d) {f}", fg=typer.colors.RED)
    current = config.get("current_update")

    if current:

        update_file = UPDATE_DIR / f"{current}.json"

        if update_file.exists():

            with open(update_file) as f:
                update_data = json.load(f)

            for f in added + modified:
                if f not in update_data["files"]:
                    update_data["files"].append(f)

            with open(update_file, "w") as f:
                json.dump(update_data, f, indent=4)


@app.command("workon")
def workon(name: str):
    stage_changes()

    if not CONFIG_PATH.exists():
        typer.echo("Not a WebKitchen project.")
        return

    with open(CONFIG_PATH) as f:
        config = json.load(f)

    updates = config.get("updates", [])

    if name not in updates:
        typer.echo("Update does not exist.")
        return

    config["current_update"] = name

    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=4)

    typer.secho(f"🔧 Now working on '{name}'", fg=typer.colors.GREEN)

@app.command("cu")
def current_update():
    stage_changes()
    if not CONFIG_PATH.exists():
        typer.echo("Not a WebKitchen project.")
        return

    with open(CONFIG_PATH) as f:
        config = json.load(f)

    typer.echo(f"Current update: {config['current_update']}")

def auto_record_changes():
    """
    Detect changed files and attach them ONLY to the current update.
    Prevent files from being attached to multiple updates.
    """

    if not CONFIG_PATH.exists():
        return

    with open(CONFIG_PATH) as f:
        config = json.load(f)

    current = config.get("current_update")

    if not current:
        return

    update_file = UPDATE_DIR / f"{current}.json"

    if not update_file.exists():
        return

    # collect files already owned by other updates
    taken_files = set()

    for f in UPDATE_DIR.glob("*.json"):
        with open(f) as u:
            data = json.load(u)
            for file in data.get("files", []):
                taken_files.add(file)

    with open(update_file) as f:
        update_data = json.load(f)

    current_hashes = generate_hash_map()

    for path in current_hashes:

        # skip files already attached to another update
        if path in taken_files:
            continue

        if path not in update_data["files"]:
            update_data["files"].append(path)

    with open(update_file, "w") as f:
        json.dump(update_data, f, indent=4)

@app.command("updates")
def updates():

    if not CONFIG_PATH.exists():
        typer.echo("Not a WebKitchen project.")
        return

    with open(CONFIG_PATH) as f:
        config = json.load(f)

    server_url = config["server_url"]
    project_code = config["project_code"]

    r = requests.get(f"{server_url}/updates/{project_code}")
    updates = r.json()

    if not updates:
        typer.echo("No updates available.")
        return

    typer.echo("\nAvailable updates:\n")

    for u in updates:
        typer.echo(f" - {u['name']} (snapshot {u.get('snapshot')})")

@app.command("publish")
def publish(update: str = typer.Argument(None)):
    """
    Publish updates.

    wk publish           -> publish current update
    wk publish update1   -> publish specific update
    wk publish all       -> publish all updates
    """

    stage_changes()

    if not CONFIG_PATH.exists():
        typer.echo("Not a WebKitchen project.")
        return

    with open(CONFIG_PATH) as f:
        config = json.load(f)

    server_url = config["server_url"]
    project_code = config["project_code"]

    updates = config.get("updates", [])

    # ---------------------------
    # Determine target updates
    # ---------------------------

    if update == "all":
        target_updates = updates

    elif update:
        if update not in updates:
            typer.echo(f"Update '{update}' does not exist.")
            return
        target_updates = [update]

    else:
        current = config.get("current_update")
        if not current:
            typer.echo("No active update. Use 'wk workon <update>'")
            return
        target_updates = [current]

    with open(HASH_PATH) as f:
        old_hashes = json.load(f)

    publish_queue = []

    for u in target_updates:

        update_file = UPDATE_DIR / f"{u}.json"

        if not update_file.exists():
            typer.echo(f"Update metadata missing for {u}")
            continue

        with open(update_file) as f:
            update_data = json.load(f)

        files = update_data.get("files", [])

        if not files:
            typer.echo(f"No files recorded for {u}")
            continue

        publish_queue.append((u, files, update_file, update_data))

    if not publish_queue:
        typer.echo("Nothing to publish.")
        return
    # ---------------------------
# Fetch server hashes
# ---------------------------

    try:
        r = requests.get(f"{server_url}/project/hashes/{project_code}")

        if r.status_code != 200:
            typer.echo("Failed to fetch server hashes.")
            raise typer.Exit()

        server_hashes = r.json()

    except Exception:
        typer.echo("Cannot connect to WebKitchen server.")
        typer.echo("Make sure server.py is running.")
        raise typer.Exit()

   # ---------------------------
# Conflict detection
# ---------------------------

    conflict_files = []

    for _, files, _, _ in publish_queue:

        for file in files:

            src = Path(file)

            if not src.exists():
                continue

            relative = str(src.relative_to("workspace"))

            local_hash = get_hash(src)
            server_hash = server_hashes.get(relative)

            # local stored base version
            base_hash = old_hashes.get(str(src))

            # conflict only if server changed since last publish/pull
            if server_hash and base_hash and server_hash != base_hash:

                # avoid false conflicts if local matches server
                if local_hash != server_hash:
                    conflict_files.append(relative)

    # ---------------------------
    # Snapshot creation
    # ---------------------------

    snapshot_id = config["snapshot"] + 1
    snapshot_path = SNAPSHOT_DIR / f"snapshot_{snapshot_id}.zip"

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    import zipfile

    with zipfile.ZipFile(snapshot_path, "w") as zipf:
        for file in get_all_files():
            zipf.write(file, file.relative_to("workspace"))

        # ---------------------------
        # ---------------------------
    # Upload files to server
    # ---------------------------
    server_hash_updates = {}

    # 🔥 LOAD TOKEN FROM CONFIG
    with open(CONFIG_PATH) as f:
        config = json.load(f)

    token = config.get("token")

    if not token:
        typer.secho("❌ Not authenticated. Run 'wk joinproject' again.", fg=typer.colors.RED)
        raise typer.Exit()

    headers = {
        "Authorization": f"Bearer {token}"
    }

    for u, files, update_file, update_data in publish_queue:

        for file in files:

            src = Path(file)

            if not src.exists():
                continue

            relative = src.relative_to("workspace")

            try:

                with open(src, "rb") as f:

                    r = requests.post(
                        f"{server_url}/project/upload",
                        headers=headers,   # ✅ FIX: add token
                        data={
                            "project_code": project_code,
                            "relative_path": str(relative)
                        },
                        files={"file": f}
                    )

                if r.status_code == 401:
                    typer.secho("❌ Unauthorized. Please login again (wk joinproject).", fg=typer.colors.RED)
                    raise typer.Exit()

                if r.status_code != 200:
                    typer.echo(f"❌ Failed to upload {relative}")
                    typer.echo(r.text)
                    continue

                response = r.json()

                # server authoritative hash
                server_hash_updates[str(src)] = response.get("hash")

                typer.echo(f"⬆ Uploaded: {relative}")

            except Exception as e:

                typer.echo(f"❌ Server upload failed for {relative}")
                typer.echo(str(e))
                raise typer.Exit()

        # ---------------------------
        # 🔥 SEND UPDATE METADATA TO SERVER (VERY IMPORTANT)
        # ---------------------------

        try:

            r = requests.post(
                f"{server_url}/update/publish",
                json={
                    "project_code": project_code,
                    "update": update_data
                }
            )

            if r.status_code != 200:
                typer.echo(f"❌ Failed to register update '{u}' on server")

        except Exception as e:

            typer.echo(f"❌ Failed to send update metadata for '{u}'")
            typer.echo(str(e))
            raise typer.Exit()

        # update local metadata
        update_data["status"] = "published"
        update_data["snapshot"] = snapshot_id

        with open(update_file, "w") as f:
            json.dump(update_data, f, indent=4)

        typer.secho(f"📦 Update '{u}' published.", fg=typer.colors.GREEN)


    # ---------------------------
    # Sync local hashes with server
    # ---------------------------

    if HASH_PATH.exists():

        with open(HASH_PATH) as f:
            local_hashes = json.load(f)

    else:
        local_hashes = {}

    for path, h in server_hash_updates.items():
        local_hashes[path] = h

    with open(HASH_PATH, "w") as f:
        json.dump(local_hashes, f, indent=4)


    # ---------------------------
    # Update config snapshot
    # ---------------------------

    config["snapshot"] = snapshot_id

    if update != "all":
        config["current_update"] = None

    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=4)

    typer.echo(f"Snapshot created: snapshot_{snapshot_id}")

@app.command("graph")
def graph():
    """
    Show update timeline.
    """

    if not CONFIG_PATH.exists():
        typer.echo("Not a WebKitchen project.")
        return

    if not UPDATE_DIR.exists():
        typer.echo("No updates found.")
        return

    typer.echo("\nWebKitchen Update Graph\n")

    updates = []

    for file in UPDATE_DIR.glob("*.json"):

        with open(file) as f:
            data = json.load(f)

        updates.append(data)

    # sort by snapshot if available
    updates.sort(key=lambda x: x.get("snapshot", 999999))

    for u in updates:

        name = u.get("name")
        snapshot = u.get("snapshot")
        status = u.get("status")

        if snapshot:
            typer.secho(f"snapshot_{snapshot}  ── {name}", fg=typer.colors.CYAN)

        else:
            typer.secho(f"pending      ── {name}", fg=typer.colors.YELLOW)
@app.command("diff")
def diff(update: str):
    """
    Show file differences for an update.
    """

    if not CONFIG_PATH.exists():
        typer.echo("Not a WebKitchen project.")
        return

    update_file = UPDATE_DIR / f"{update}.json"

    if not update_file.exists():
        typer.echo(f"Update '{update}' does not exist.")
        return

    with open(update_file) as f:
        data = json.load(f)

    files = data.get("files", [])

    if not files:
        typer.echo("No files recorded for this update.")
        return

    typer.echo(f"\nDiff for {update}\n")

    for file in files:

        workspace_path = Path(file)

        if not workspace_path.exists():
            typer.echo(f"Missing file: {file}")
            continue

        relative = workspace_path.relative_to("workspace")
        main_path = Path("main") / relative

        typer.secho(f"\nFile: {relative}", fg=typer.colors.CYAN)

        # New file
        if not main_path.exists():
            typer.secho(f"New file: {workspace_path}", fg=typer.colors.GREEN)
            continue

        with open(main_path) as f1:
            main_lines = f1.readlines()

        with open(workspace_path) as f2:
            workspace_lines = f2.readlines()

        diff_lines = difflib.unified_diff(
            main_lines,
            workspace_lines,
            fromfile=str(main_path),
            tofile=str(workspace_path),
        )

        for line in diff_lines:

            if line.startswith("+") and not line.startswith("+++"):
                typer.secho(line.rstrip(), fg=typer.colors.GREEN)

            elif line.startswith("-") and not line.startswith("---"):
                typer.secho(line.rstrip(), fg=typer.colors.RED)

            else:
                typer.echo(line.rstrip())

@app.command("revert")
def revert(snapshot: str):
    """
    Revert project to a previous snapshot.

    Example:
    wk revert snapshot_3
    """

    if not CONFIG_PATH.exists():
        typer.echo("Not a WebKitchen project.")
        return

    snapshot_path = SNAPSHOT_DIR / f"{snapshot}.zip"

    if not snapshot_path.exists():
        typer.echo(f"Snapshot '{snapshot}' does not exist.")
        return

    main = Path("main")

    # Clear main folder
    if main.exists():
        for item in main.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    else:
        main.mkdir()

    import zipfile

    with zipfile.ZipFile(snapshot_path, "r") as zipf:
        zipf.extractall(main)

    # Rebuild hashes.json
    hashes = {}

    for file in main.rglob("*"):
        if file.is_file():
            hashes[str(Path("workspace") / file.relative_to(main))] = get_hash(file)

    with open(HASH_PATH, "w") as f:
        json.dump(hashes, f, indent=4)

    # reset active update
    with open(CONFIG_PATH) as f:
        config = json.load(f)

    config["current_update"] = None

    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=4)

    typer.secho(f"Project reverted to {snapshot}", fg=typer.colors.GREEN)

@app.command("updateinfo")
def updateinfo(update: str):
    stage_changes()
    """
    Show detailed information about an update.

    Example:
    wk updateinfo update1
    """

    if not CONFIG_PATH.exists():
        typer.echo("Not a WebKitchen project.")
        return

    update_file = UPDATE_DIR / f"{update}.json"

    if not update_file.exists():
        typer.echo(f"Update '{update}' does not exist.")
        return

    with open(update_file) as f:
        data = json.load(f)

    name = data.get("name")
    status = data.get("status", "unknown")
    snapshot = data.get("snapshot")
    files = data.get("files", [])

    typer.secho(f"\nUpdate: {name}", fg=typer.colors.CYAN)
    typer.echo(f"Status: {status}")

    if snapshot:
        typer.echo(f"Snapshot: snapshot_{snapshot}")
    else:
        typer.echo("Snapshot: pending")

    typer.echo(f"\nFiles ({len(files)}):\n")

    if not files:
        typer.echo(" No files recorded.")
        return

    for fpath in files:
        typer.echo(f" - {fpath}")

@app.command("deleteupdate")
def deleteupdate(update: str):
    """
    Delete an update from the project.

    Example:
    wk deleteupdate update2
    """

    if not CONFIG_PATH.exists():
        typer.echo("Not a WebKitchen project.")
        return

    update_file = UPDATE_DIR / f"{update}.json"

    if not update_file.exists():
        typer.echo(f"Update '{update}' does not exist.")
        return

    # Load config
    with open(CONFIG_PATH) as f:
        config = json.load(f)

    updates = config.get("updates", [])

    if update not in updates:
        typer.echo(f"Update '{update}' not found in config.")
        return

    # Load update metadata
    with open(update_file) as f:
        update_data = json.load(f)

    files = update_data.get("files", [])

    # Remove staged ownership
    if STAGED_PATH.exists():

        with open(STAGED_PATH) as f:
            staged = json.load(f)

        for fpath in files:
            if fpath in staged:
                del staged[fpath]

        with open(STAGED_PATH, "w") as f:
            json.dump(staged, f, indent=4)

    # Remove update metadata file
    update_file.unlink()

    # Remove update from config
    updates.remove(update)
    config["updates"] = updates

    # Reset active update if needed
    if config.get("current_update") == update:
        config["current_update"] = None

    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=4)

    typer.secho(f"🗑 Update '{update}' deleted.", fg=typer.colors.RED)

import smtplib
from email.message import EmailMessage


def send_invite_email(email, username, password, project_code, project_name):

    msg = EmailMessage()
    msg["Subject"] = f"Invitation to collaborate on {project_name}"
    msg["From"] = "WebKitchen <yourgmail@gmail.com>"
    msg["To"] = email

    # Plain text fallback
    msg.set_content(f"""
You have been invited to collaborate on a WebKitchen project.

Project: {project_name}

Project Code: {project_code}
Username: {username}
Password: {password}

To join the project run:

wk joinproject

Then enter your credentials.

WebKitchen CLI
""")

    # HTML invitation
    html_content = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
body {{
    font-family: Arial, sans-serif;
    background-color: #f6f8fb;
    margin: 0;
    padding: 0;
}}

.container {{
    max-width: 600px;
    margin: 40px auto;
    background: white;
    border-radius: 10px;
    box-shadow: 0 4px 14px rgba(0,0,0,0.08);
    overflow: hidden;
}}

.header {{
    background: #0f172a;
    color: white;
    padding: 24px;
    text-align: center;
    font-size: 22px;
    font-weight: bold;
}}

.content {{
    padding: 30px;
    color: #333;
    line-height: 1.6;
}}

.credentials {{
    background: #f1f5f9;
    padding: 16px;
    border-radius: 6px;
    margin: 20px 0;
    font-family: monospace;
}}

.button {{
    display: inline-block;
    padding: 12px 20px;
    margin-top: 15px;
    background: #2563eb;
    color: white;
    text-decoration: none;
    border-radius: 6px;
}}

.footer {{
    text-align: center;
    font-size: 12px;
    color: #888;
    padding: 20px;
}}
</style>
</head>

<body>

<div class="container">

<div class="header">
🍳 WebKitchen Collaboration Invite
</div>

<div class="content">

<p>Hello <b>{username}</b>,</p>

<p>You have been invited to collaborate on the project:</p>

<p><b>{project_name}</b></p>

<p>Use the credentials below to join:</p>

<div class="credentials">
Project Code: <b>{project_code}</b><br>
Username: <b>{username}</b><br>
Password: <b>{password}</b>
</div>

<p>Install WebKitchen CLI and run:</p>

<div class="credentials">
wk joinproject
</div>

<p>Enter the project code and your credentials to access the project.</p>

<a class="button" href="https://pypi.org/">Install WebKitchen</a>

</div>

<div class="footer">
WebKitchen • Collaborative Development Platform
</div>

</div>

</body>
</html>
"""

    msg.add_alternative(html_content, subtype="html")

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.starttls()
        smtp.login("uditkhare214@gmail.com", "spds nxuh mkdh bdlj")
        smtp.send_message(msg)

@app.command("addcollab")
def addcollab():

    if not CONFIG_PATH.exists():
        typer.echo("Not a WebKitchen project.")
        return

    username = typer.prompt("Username")
    email = typer.prompt("Email")

    import secrets

    # 🔥 FORCE SAFE PASSWORD (no chance of >72 bytes)
    raw_password = secrets.token_hex(4)
    password = str(raw_password)[:32]

    print("SENDING PASSWORD:", password)
    print("LENGTH:", len(password))

    # -----------------------------
    # Load config
    # -----------------------------
    with open(CONFIG_PATH) as f:
        config = json.load(f)

    # Ensure project_code exists
    import uuid
    if "project_code" not in config:
        config["project_code"] = "WK-" + uuid.uuid4().hex[:6].upper()

    # -----------------------------
    # Save locally
    # -----------------------------
    collabs = config.get("collaborators", [])

    collabs.append({
        "username": username,
        "email": email,
        "password": password   # keep raw locally
    })

    config["collaborators"] = collabs

    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=4)

    # -----------------------------
    # ✅ REGISTER USER ON SERVER
    # -----------------------------
    try:
        payload = {
            "project_code": config["project_code"],
            "username": username,
            "password": password   # 🔥 RAW PASSWORD ONLY
        }

        print("PAYLOAD SENT:", payload)

        r = requests.post(
            f"{SERVER_URL}/auth/register",
            json=payload,
            timeout=10
        )

        print("SERVER RESPONSE:", r.status_code, r.text)

        if r.status_code != 200:
            typer.secho("❌ Failed to register user on server", fg=typer.colors.RED)
            return

        data = r.json()

        if data.get("status") != "registered":
            typer.secho("❌ Server rejected user registration", fg=typer.colors.RED)
            typer.echo(data)
            return

    except Exception as e:
        typer.secho("❌ Cannot connect to server", fg=typer.colors.RED)
        typer.echo(str(e))
        return

    # -----------------------------
    # Send email invite
    # -----------------------------
    send_invite_email(
        email,
        username,
        password,
        config["project_code"],
        config["project_name"]
    )

    typer.secho(f"✅ Invitation sent to {email}", fg=typer.colors.GREEN)


@app.command("joinproject")
def joinproject():

    project_code = typer.prompt("Project Code")
    username = typer.prompt("Username")
    password = typer.prompt("Password", hide_input=True)

    server_url = SERVER_URL

    try:
        # 🔐 Login to server (get JWT token)
        r = requests.post(
            f"{server_url}/auth/login",
            json={
                "project_code": project_code,
                "username": username,
                "password": password
            },
            timeout=10
        )

        if r.status_code != 200:
            typer.secho("❌ Authentication failed.", fg=typer.colors.RED)
            typer.echo(r.text)
            return

        try:
            data = r.json()
        except Exception:
            typer.secho("❌ Invalid response from server.", fg=typer.colors.RED)
            typer.echo(r.text)
            return

        token = data.get("access_token")

        if not token:
            typer.secho("❌ Login failed. No token received.", fg=typer.colors.RED)
            typer.echo(data)
            return

    except Exception as e:
        typer.secho("❌ Cannot connect to WebKitchen server.", fg=typer.colors.RED)
        typer.echo(str(e))
        return

    # ✅ FIX: define project_name BEFORE using it
    project_name = project_code

    project_path = Path(project_name)

    if project_path.exists():
        typer.echo("Project already exists locally.")
        return

    # -----------------------------
    # Create project structure
    # -----------------------------
    project_path.mkdir()
    (project_path / "workspace").mkdir()

    wk = project_path / "webkitchen" / ".wk"
    wk.mkdir(parents=True)

    (wk / "snapshots").mkdir()
    (wk / "updates").mkdir()

    # -----------------------------
    # ✅ FINAL CONFIG (WITH TOKEN)
    # -----------------------------
    config = {
        "project_name": project_name,
        "project_code": project_code,
        "server_url": server_url,
        "current_update": None,
        "updates": [],
        "snapshot": 0,
        "token": token   # ✅ IMPORTANT FIX
    }

    with open(wk / "config.json", "w") as f:
        json.dump(config, f, indent=4)

    with open(wk / "hashes.json", "w") as f:
        json.dump({}, f)

    with open(wk / "staged.json", "w") as f:
        json.dump({}, f)

    with open(wk / "locks.json", "w") as f:
        json.dump({}, f)

    typer.secho(f"\nWelcome {username}!", fg=typer.colors.GREEN)
    typer.echo("Downloading project from server...")

    os.chdir(project_path)

    pull()

    typer.secho("Project joined successfully.", fg=typer.colors.GREEN)

@app.command("projectcode")
def projectcode():

    with open(CONFIG_PATH) as f:
        config = json.load(f)

    typer.echo(f"Project Code: {config.get('project_code')}")

@app.command("install")
def pull(update: str = typer.Argument(None)):

    if not CONFIG_PATH.exists():
        typer.echo("Not a WebKitchen project.")
        return

    with open(CONFIG_PATH) as f:
        config = json.load(f)

    server_url = config["server_url"]
    project_code = config["project_code"]

    workspace = Path("workspace")

    # -----------------------------
    # Pull entire project
    # -----------------------------
    if update is None:

        typer.echo("Pulling latest project from server...")

        try:

            r = requests.get(
                f"{server_url}/project/download/{project_code}",
                stream=True,
                timeout=10
            )

        except Exception:
            typer.secho("❌ Cannot connect to server.", fg=typer.colors.RED)
            return

        if r.status_code != 200:
            typer.secho(f"❌ Failed to download project (status {r.status_code})", fg=typer.colors.RED)
            return

        content_type = r.headers.get("content-type", "")

        # 🚨 important: ensure it's actually a zip
        if "zip" not in content_type:
            typer.secho("❌ Server did not return a zip file.", fg=typer.colors.RED)
            typer.echo("Server response:")
            try:
                typer.echo(r.json())
            except Exception:
                typer.echo(r.text)
            return

        zip_path = Path("project_pull.zip")

        # download file
        with open(zip_path, "wb") as f:
            for chunk in r.iter_content(1024):
                if chunk:
                    f.write(chunk)

        import zipfile

        # extract safely
        try:
            with zipfile.ZipFile(zip_path, "r") as zipf:

                # clear workspace ONLY after zip is valid
                if workspace.exists():
                    for item in workspace.iterdir():
                        if item.is_dir():
                            shutil.rmtree(item)
                        else:
                            item.unlink()

                zipf.extractall(workspace)

        except zipfile.BadZipFile:
            typer.secho("❌ Downloaded file is not a valid zip.", fg=typer.colors.RED)
            zip_path.unlink(missing_ok=True)
            return

        zip_path.unlink()

        # ---------------------------
        # Fetch server hashes
        # ---------------------------
        try:
            r = requests.get(f"{server_url}/project/hashes/{project_code}", timeout=5)

            if r.status_code != 200:
                typer.secho("❌ Failed to fetch server hashes.", fg=typer.colors.RED)
                return

            server_hashes = r.json()

        except Exception:
            typer.secho("❌ Failed to fetch server hashes.", fg=typer.colors.RED)
            return

        with open(HASH_PATH, "w") as f:
            json.dump(server_hashes, f, indent=4)

        typer.secho("✅ Workspace updated from server.", fg=typer.colors.GREEN)
        return

    # -----------------------------
    # Pull specific update
    # -----------------------------
    typer.echo(f"Pulling update '{update}'...")

    try:

        r = requests.get(f"{server_url}/updates/{project_code}")

        if r.status_code != 200:
            typer.echo("Failed to fetch updates.")
            return

        updates = r.json()

    except Exception:
        typer.echo("Cannot connect to server.")
        return

    target = None

    for u in updates:
        if u["name"] == update:
            target = u
            break

    if not target:
        typer.echo("Update not found on server.")
        return

    files = target.get("files", [])

    if not files:
        typer.echo("Update has no files.")
        return

    for file in files:

        relative = Path(file).relative_to("workspace")
        dest = workspace / relative

        dest.parent.mkdir(parents=True, exist_ok=True)

        try:

            r = requests.get(
                f"{server_url}/project/file/{project_code}",
                params={"path": str(relative)},
                stream=True
            )

            if r.status_code != 200:
                typer.echo(f"Failed to download {relative}")
                continue

            with open(dest, "wb") as f:
                for chunk in r.iter_content(1024):
                    f.write(chunk)

        except Exception:
            typer.echo(f"Error downloading {relative}")

    typer.secho(f"Update '{update}' installed.", fg=typer.colors.GREEN)

def login_and_get_token(server_url, project_code, username, password):

    r = requests.post(
        f"{server_url}/auth/login",
        json={
            "username": username,
            "password": password,
            "project_code": project_code
        }
    )

    if r.status_code != 200:
        return None

    data = r.json()

    return data.get("access_token")

if __name__ == "__main__":
    app()