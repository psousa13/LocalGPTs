"""First-run setup for LocalGPT: checks every piece and installs what is missing."""
import json, os, platform, re, shutil, signal, socket, subprocess, tarfile, threading, time, urllib.request, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IS_WIN = platform.system() == "Windows"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
UA = {"User-Agent": "LocalGPTs-launcher"}

# ---- pinned versions (change here only) ----
MODELS = ["qwen3.5:4b", "qwen3.5:2b"]
HF_REPOS = ["microsoft/harrier-oss-v1-0.6b", "Qwen/Qwen3-Reranker-0.6B"]
NODE_PREFIX = "v22."
PORT = 11434
LG = ROOT / "localgpt"
OW = ROOT / "open-webui"
OWVENV = OW / (".venv" if IS_WIN else ".venv-linux")
OWPY = OWVENV / ("Scripts/python.exe" if IS_WIN else "bin/python")
OW_DIR = ROOT / "launcher" / "openwebui"
TORCH_CPU = "https://download.pytorch.org/whl/cpu"

if IS_WIN:
    BIN, NODE, PYDIR = ROOT / "bin", ROOT / "node", ROOT / "python"
    UVC, NPMC = ROOT / ".uv-cache", ROOT / ".npm-cache"
    UV, NODEEXE, NPM = BIN / "uv.exe", NODE / "node.exe", NODE / "npm.cmd"
    VENV = LG / ".venv"
    VPY = VENV / "Scripts" / "python.exe"
    PYSPEC = "cpython-3.11-windows-x86_64"
    UV_URL = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
    UV_EXT, NODE_FILE, NODE_ARCH, NODE_EXT = ".zip", "win-x64-zip", "win-x64.zip", ".zip"
else:
    BIN, NODE, PYDIR = ROOT / "bin-linux", ROOT / "node-linux", ROOT / "python-linux"
    UVC, NPMC = ROOT / ".uv-cache-linux", ROOT / ".npm-cache-linux"
    UV, NODEEXE, NPM = BIN / "uv", NODE / "bin" / "node", NODE / "bin" / "npm"
    VENV = LG / ".venv-linux"
    VPY = VENV / "bin" / "python"
    PYSPEC = "3.11"
    UV_URL = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz"
    UV_EXT, NODE_FILE, NODE_ARCH, NODE_EXT = ".tar.gz", "linux-x64", "linux-x64.tar.xz", ".tar.xz"


def make_env():
    e = os.environ.copy()
    e.update({
        "UV_PYTHON_INSTALL_DIR": str(PYDIR),
        "UV_CACHE_DIR": str(UVC),
        "npm_config_cache": str(NPMC),
        "HF_HOME": str(ROOT / "hf-cache"),
        "OLLAMA_MODELS": str(ROOT / "ollama-models"),
        "OLLAMA_HOST": "http://127.0.0.1:%d" % PORT,
        "OLLAMA_NO_CLOUD": "1",
        "PYTHONUTF8": "1",
        "PYTHONUNBUFFERED": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_DISABLE_PROGRESS_BARS": "1",
    })
    dirs = [BIN, NODEEXE.parent]
    if IS_WIN and os.environ.get("LOCALAPPDATA"):
        dirs.append(Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Ollama")
    e["PATH"] = os.pathsep.join([str(d) for d in dirs] + [e.get("PATH", "")])
    return e


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _win_kw(extra=0):
    return {"creationflags": subprocess.CREATE_NO_WINDOW | extra} if IS_WIN else {}


class Setup:
    def __init__(self, app, gpt="LocalGPT"):
        self.app = app
        self.gpt = gpt
        self.env = make_env()
        self.server = None
        self.steps = [
            ("portable uv", self.c_uv, self.i_uv),
            ("Python 3.11", self.c_python, self.i_python),
            ("Python packages (venv)", self.c_venv, self.i_venv),
            ("portable Node", self.c_node, self.i_node),
            ("UI packages (npm)", self.c_modules, self.i_modules),
            ("Ollama models", self.c_models, self.i_models),
            ("embedder and reranker", self.c_hf, self.i_hf),
        ]
        if gpt == "Open WebUI":
            self.steps = [
                ("portable uv", self.c_uv, self.i_uv),
                ("Python 3.11", self.c_python, self.i_python),
                ("Open WebUI packages (venv)", self.c_owvenv, self.i_owvenv),
                ("Open WebUI RAG-only edits", self.c_owedits, self.i_owedits),
                ("Ollama models", self.c_models, self.i_models),
            ]

    # ---------- helpers ----------
    def log(self, msg):
        self.app.logline("[setup] " + msg)

    def run(self, cmd, cwd=None, tag="setup"):
        cmd = [str(c) for c in cmd]
        self.log("$ " + " ".join(cmd))
        kw = dict(cwd=str(cwd or ROOT), env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                  text=True, encoding="utf-8", errors="replace", bufsize=1)
        if IS_WIN:
            kw["stdin"] = subprocess.DEVNULL
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            p = subprocess.Popen(cmd, **kw)
        except OSError as ex:
            self.log("cannot start: %r" % (ex,))
            return 1
        last = 0.0
        for line in p.stdout:
            line = ANSI.sub("", line.rstrip())
            if not line:
                continue
            if "%" in line and "100%" not in line and time.time() - last < 2:
                continue  # throttle progress bars
            last = time.time()
            self.app.logline("[%s] %s" % (tag, line))
        return p.wait()

    def capture(self, cmd, timeout=300):
        try:
            r = subprocess.run([str(c) for c in cmd], env=self.env, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout, **_win_kw())
            return r.returncode, r.stdout
        except Exception as ex:
            self.log("command failed: %r" % (ex,))
            return 1, ""

    def download(self, url, dest):
        dest = Path(dest)
        for attempt in range(1, 4):
            try:
                self.log("downloading " + url)
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r, open(dest, "wb") as f:
                    total = int(r.headers.get("Content-Length") or 0)
                    done = last = 0
                    while True:
                        b = r.read(1 << 20)
                        if not b:
                            break
                        f.write(b)
                        done += len(b)
                        if done - last >= 20 * (1 << 20):
                            last = done
                            self.log("  %d MB%s" % (done >> 20, (" of %d MB" % (total >> 20)) if total else ""))
                return True
            except Exception as ex:
                self.log("download failed (attempt %d/3): %r" % (attempt, ex))
                time.sleep(3)
        return False

    def extract(self, archive, dest):
        archive, dest = Path(archive), Path(dest)
        tmp = ROOT / ".setup-tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        if str(archive).endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                z.extractall(tmp)
        else:
            with tarfile.open(archive) as t:
                try:
                    t.extractall(tmp, filter="tar")
                except TypeError:
                    t.extractall(tmp)
        items = list(tmp.iterdir())
        src = items[0] if len(items) == 1 and items[0].is_dir() else tmp
        dest.mkdir(parents=True, exist_ok=True)
        for it in list(src.iterdir()):
            target = dest / it.name
            if target.exists() or target.is_symlink():
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.move(str(it), str(target))
        shutil.rmtree(tmp, ignore_errors=True)

    def ask(self, text):
        ev, res = threading.Event(), []

        def f():
            from tkinter import messagebox
            res.append(messagebox.askyesno("LocalGPTs setup", text))
            ev.set()
        self.app.root.after(0, f)
        ev.wait()
        return res[0]

    # ---------- Ollama ----------
    def ollama_exe(self):
        return shutil.which("ollama", path=self.env["PATH"])

    def ensure_server(self):
        if port_open(PORT):
            return True
        exe = self.ollama_exe()
        if not exe:
            return False
        self.log("starting a temporary Ollama server (models go to %s)" % (ROOT / "ollama-models"))
        kw = dict(cwd=str(ROOT), env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if IS_WIN:
            kw["stdin"] = subprocess.DEVNULL
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            kw["start_new_session"] = True
        self.server = subprocess.Popen([exe, "serve"], **kw)
        for _ in range(60):
            if port_open(PORT):
                return True
            if self.server.poll() is not None:
                break
            time.sleep(0.5)
        return False

    def stop_server(self):
        p, self.server = self.server, None
        if not p or p.poll() is not None:
            return
        self.log("stopping the temporary Ollama server")
        if IS_WIN:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, **_win_kw())
        else:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except Exception:
                p.terminate()

    def listed_models(self):
        exe = self.ollama_exe()
        if not exe:
            return set()
        code, out = self.capture([exe, "list"], timeout=60)
        if code != 0:
            return set()
        return {ln.split()[0] for ln in out.splitlines()[1:] if ln.strip()}

    # ---------- checks ----------
    def c_uv(self):
        return UV.exists()

    def c_python(self):
        return PYDIR.exists() and any(p.name.startswith("cpython-3.11") for p in PYDIR.iterdir())

    def c_venv(self):
        if not VPY.exists():
            return False
        self.log("testing the Python packages (takes a few seconds)...")
        code, _ = self.capture([VPY, "-c", "import torch, transformers, lancedb, docling, requests"], timeout=300)
        return code == 0

    def c_node(self):
        return NODEEXE.exists()

    def c_modules(self):
        return (LG / "node_modules" / "next").exists()

    def c_models(self):
        if not self.ensure_server():
            return False
        return set(MODELS) <= self.listed_models()

    def _hf_dir(self, repo):
        return ROOT / "hf-cache" / "hub" / ("models--" + repo.replace("/", "--")) / "snapshots"

    def c_hf(self):
        for repo in HF_REPOS:
            d = self._hf_dir(repo)
            if not (d.exists() and any(d.iterdir())):
                return False
        return True

    # ---------- installs ----------
    def i_uv(self):
        arc = ROOT / ("uv-download" + UV_EXT)
        if not self.download(UV_URL, arc):
            return False
        self.extract(arc, BIN)
        arc.unlink()
        if not IS_WIN and UV.exists():
            os.chmod(UV, 0o755)
        return UV.exists()

    def i_python(self):
        return self.run([UV, "python", "install", PYSPEC]) == 0

    def i_venv(self):
        if not VPY.exists():
            if self.run([UV, "venv", "--python", PYSPEC, VENV], cwd=LG, tag="uv") != 0:
                return False
        return self.run([UV, "pip", "install", "--python", VPY, "-r", "requirements.txt", "torchaudio==2.4.1"],
                        cwd=LG, tag="uv") == 0

    def i_node(self):
        try:
            with urllib.request.urlopen(urllib.request.Request("https://nodejs.org/dist/index.json", headers=UA), timeout=60) as r:
                idx = json.load(r)
        except Exception as ex:
            self.log("cannot read the Node release list: %r" % (ex,))
            return False
        v = next((x["version"] for x in idx if x["version"].startswith(NODE_PREFIX) and NODE_FILE in x["files"]), None)
        if not v:
            self.log("no Node %s build found" % NODE_PREFIX)
            return False
        arc = ROOT / ("node-download" + NODE_EXT)
        if not self.download("https://nodejs.org/dist/%s/node-%s-%s" % (v, v, NODE_ARCH), arc):
            return False
        self.extract(arc, NODE)
        arc.unlink()
        return NODEEXE.exists()

    def i_modules(self):
        return self.run([NPM, "install"], cwd=LG, tag="npm") == 0

    def i_models(self):
        exe = self.ollama_exe()
        have = self.listed_models()
        for m in MODELS:
            if m not in have and self.run([exe, "pull", m], tag="ollama") != 0:
                return False
        return True

    def i_hf(self):
        for repo in HF_REPOS:
            d = self._hf_dir(repo)
            if d.exists() and any(d.iterdir()):
                continue
            code = "from huggingface_hub import snapshot_download; snapshot_download(%r)" % repo
            if self.run([VPY, "-c", code], cwd=LG, tag="hf") != 0:
                return False
        return True

    # ---------- Open WebUI ----------
    def c_owvenv(self):
        if not OWPY.exists():
            return False
        self.log("checking Open WebUI packages against the pin file...")
        code, out = self.capture([OWPY, OW_DIR / "check_env.py", OW_DIR / "requirements.txt"], timeout=300)
        for ln in out.splitlines():
            if ln.strip():
                self.log("  " + ln)
        return code == 0

    def i_owvenv(self):
        if not OWPY.exists():
            if self.run([UV, "venv", "--python", PYSPEC, OWVENV], cwd=OW, tag="uv") != 0:
                return False
        return self.run([UV, "pip", "install", "--python", OWPY, "-r", OW_DIR / "requirements.txt",
                         "--extra-index-url", TORCH_CPU, "--index-strategy", "unsafe-best-match"],
                        cwd=OW, tag="uv") == 0

    def _ow_pkg(self):
        code, out = self.capture([OWPY, "-c",
            "import importlib.util,pathlib;print(pathlib.Path(importlib.util.find_spec('open_webui').origin).parent)"], timeout=120)
        return Path(out.strip()) if code == 0 and out.strip() else None

    def _sha(self, p):
        import hashlib
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()

    def c_owedits(self):
        if not OWPY.exists():
            return False
        pkg = self._ow_pkg()
        if not pkg:
            return False
        man = json.loads((OW_DIR / "overlay.json").read_text(encoding="utf-8"))
        good = 0
        for rel, h in man["files"].items():
            f = pkg / rel
            if f.exists() and self._sha(f) == h["edited"]:
                good += 1
        self.log("  your Open WebUI edits: %d of %d files match" % (good, len(man["files"])))
        return good == len(man["files"])

    def i_owedits(self):
        pkg = self._ow_pkg()
        if not pkg:
            return False
        man = json.loads((OW_DIR / "overlay.json").read_text(encoding="utf-8"))
        for rel, h in man["files"].items():
            f = pkg / rel
            cur = self._sha(f)
            if cur not in (h["stock"], h["edited"]):
                self.log("REFUSED: %s is neither the stock nor the edited version (wrong Open WebUI version?)" % rel)
                return False
        for rel in man["files"]:
            shutil.copy(OW_DIR / "overlay" / rel, pkg / rel)
            self.log("  installed " + rel)
        return True

    # ---------- main flow ----------
    def run_all(self):
        try:
            return self._run()
        except Exception as ex:
            self.log("ERROR: %r" % (ex,))
            return False
        finally:
            self.stop_server()

    def _run(self):
        if not (self.ollama_exe() or port_open(PORT)):
            self.log("Ollama is not installed. Install it from https://ollama.com/download (0.40 or newer), then press Check again.")
            self.app.say("Install Ollama first")
            return False
        if port_open(PORT):
            self.log("an Ollama server is already running; using it (its models may live outside ollama-models/)")
        todo = []
        for label, check, install in self.steps:
            self.app.say("Checking " + label + "...")
            try:
                ok = check()
            except Exception as ex:
                self.log("check failed for %s: %r" % (label, ex))
                ok = False
            self.log(("OK       " if ok else "MISSING  ") + label)
            if not ok:
                todo.append((label, check, install))
        if not todo:
            self.log("everything is in place")
            return True
        free = shutil.disk_usage(ROOT).free / 2 ** 30
        msg = ("Missing:\n- " + "\n- ".join(t[0] for t in todo) +
               "\n\nThe first full install downloads roughly " + ("10 to 15 GB" if self.gpt == "LocalGPT" else "a few GB") + " and needs internet.\n"
               "Free disk space: %.0f GB.\n\nInstall now?" % free)
        if not self.ask(msg):
            self.app.say("Setup cancelled")
            return False
        for label, check, install in todo:
            self.app.say("Installing " + label + "...")
            self.log("installing " + label)
            if not install() or not check():
                self.log("FAILED: " + label + " (see the lines above)")
                self.app.say("FAILED: " + label)
                return False
            self.log("done: " + label)
        self.log("everything is in place")
        return True
