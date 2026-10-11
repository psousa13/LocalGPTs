import json, os, platform, re, shutil, signal, socket, subprocess, sys, threading, time, urllib.request, webbrowser
import tkinter as tk
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LOGS = HERE / "logs"
LOGS.mkdir(exist_ok=True)
GPTS = json.loads((HERE / "gpts.json").read_text(encoding="utf-8"))
IS_WIN = platform.system() == "Windows"
OS = "windows" if IS_WIN else "linux"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def read_env_ps1():
    out, skipped = {}, []
    f = ROOT / "env.ps1"
    if not f.exists():
        return out, []
    data = f.read_bytes()
    text = data.decode("utf-16") if (data[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in data) else data.decode("utf-8-sig", errors="replace")
    for line in text.splitlines():
        m = re.match(r'^\s*\$env:([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(["\'])(.*)\2\s*$', line)
        if not m:
            continue
        k, v = m.group(1), m.group(3).replace("$PSScriptRoot", str(ROOT))
        if k.upper() == "PATH":
            continue
        if "$" in v:
            skipped.append(k)
            continue
        out[k] = v
    return out, skipped


def base_env(cfg):
    e = os.environ.copy()
    e.update({
        "UV_PYTHON_INSTALL_DIR": str(ROOT / ("python" if IS_WIN else "python-linux")),
        "HF_HOME": str(ROOT / "hf-cache"),
        "OLLAMA_MODELS": str(ROOT / "ollama-models"),
        "OLLAMA_HOST": "http://127.0.0.1:11434",
        "OLLAMA_NO_CLOUD": "1",
        "GENERATION_MODEL": "qwen3.5:4b",
        "ENRICHMENT_MODEL": "qwen3.5:2b",
        "RERANKER_MODEL": "Qwen/Qwen3-Reranker-0.6B",
        "PYTHONUTF8": "1",
        "PYTHONUNBUFFERED": "1",
        "NEXT_TELEMETRY_DISABLED": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
    })
    skipped = []
    if IS_WIN:
        vals, skipped = read_env_ps1()
        e.update(vals)
    e.update(cfg.get("env", {}))
    dirs = [ROOT / d for d in cfg.get("path", [])]
    if cfg.get("venv_bin"):
        dirs.insert(0, ROOT / cfg["venv_bin"])
    if IS_WIN and os.environ.get("LOCALAPPDATA"):
        dirs.append(Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Ollama")
    e["PATH"] = os.pathsep.join([str(d) for d in dirs] + [e.get("PATH", "")])
    return e, skipped


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def spawn(cmd, cwd, env):
    kw = dict(cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
              text=True, encoding="utf-8", errors="replace", bufsize=1)
    if IS_WIN:
        kw["stdin"] = subprocess.DEVNULL
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
    return subprocess.Popen(cmd, **kw)


def kill_tree(p):
    if p.poll() is not None:
        return
    if IS_WIN:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except Exception:
            p.terminate()


class App:
    def __init__(self, root):
        self.root = root
        self.procs = []
        self.busy = False
        self.checking = False
        self.open_url = None
        root.title("LocalGPTs Launcher")
        root.geometry("620x560")
        tk.Label(root, text="Choose a GPT", font=("Sans", 14, "bold")).pack(pady=8)
        self.listbox = tk.Listbox(root, height=4, font=("Sans", 12), activestyle="none", exportselection=False)
        for g in GPTS:
            self.listbox.insert(tk.END, g["name"])
        self.listbox.selection_set(0)
        self.listbox.pack(fill="x", padx=20)
        self.desc = tk.Label(root, text="", wraplength=560, fg="gray")
        self.desc.pack(pady=4)
        self.listbox.bind("<<ListboxSelect>>", self.on_select)
        self.on_select()
        row = tk.Frame(root)
        row.pack(pady=6)
        self.btn_check = tk.Button(row, text="Check", width=12, command=self.check)
        self.btn_check.pack(side="left", padx=6)
        self.btn_launch = tk.Button(row, text="Launch", width=12, command=self.launch)
        self.btn_launch.pack(side="left", padx=6)
        self.btn_stop = tk.Button(row, text="Stop", width=12, command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        self.btn_open = tk.Button(row, text="Open", width=12, command=self.open_browser, state="disabled")
        self.btn_open.pack(side="left", padx=6)
        self.status = tk.Label(root, text="Ready", anchor="w")
        self.status.pack(fill="x", padx=20, pady=2)
        self.log = tk.Text(root, height=16, font=("Mono", 9), state="disabled")
        self.log.pack(fill="both", expand=True, padx=20, pady=6)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    def selected(self):
        sel = self.listbox.curselection()
        return GPTS[sel[0]] if sel else None

    def on_select(self, _e=None):
        g = self.selected()
        if g:
            self.desc.config(text=g["description"])

    def say(self, msg):
        self.root.after(0, lambda: self.status.config(text=msg))

    def logline(self, msg):
        def f():
            self.log.config(state="normal")
            self.log.insert(tk.END, msg + "\n")
            if int(self.log.index("end-1c").split(".")[0]) > 3000:
                self.log.delete("1.0", "1000.0")
            self.log.see(tk.END)
            self.log.config(state="disabled")
        self.root.after(0, f)

    def set_buttons(self, launch, stop, open_="disabled"):
        def f():
            self.btn_launch.config(state=launch)
            self.btn_stop.config(state=stop)
            self.btn_open.config(state=open_)
            self.btn_check.config(state="disabled" if (self.busy or self.checking) else "normal")
        self.root.after(0, f)

    def open_browser(self):
        if self.open_url:
            webbrowser.open(self.open_url)

    # ---------- Check ----------
    def check(self):
        if self.busy or self.checking:
            return
        self.checking = True
        self.set_buttons("disabled", "disabled")
        threading.Thread(target=self.run_check, daemon=True).start()

    def run_check(self):
        ok = False
        try:
            sys.path.insert(0, str(HERE))
            from setup_steps import Setup
            ok = Setup(self).run_all()
        except Exception as ex:
            self.logline("[setup] ERROR: " + repr(ex))
        self.checking = False
        self.set_buttons("normal", "disabled")
        if ok:
            self.say("Everything is in place. Press Launch.")

    # ---------- Launch ----------
    def launch(self):
        g = self.selected()
        if not g or self.busy or self.checking:
            return
        cfg = g.get(OS)
        if not cfg:
            self.say("No " + OS + " entry for " + g["name"])
            return
        if not (ROOT / cfg.get("cwd", ".")).exists():
            self.say("Not installed yet. Press Check first.")
            return
        self.busy = True
        self.set_buttons("disabled", "normal")
        threading.Thread(target=self.run_gpt, args=(g, cfg), daemon=True).start()

    def pump(self, name, p, path):
        with open(path, "w", encoding="utf-8") as f:
            for line in p.stdout:
                line = ANSI.sub("", line.rstrip())
                f.write(line + "\n")
                f.flush()
                self.logline("[" + name + "] " + line)

    def run_gpt(self, g, cfg):
        env, skipped = base_env(cfg)
        if skipped:
            self.logline("[launcher] env.ps1 values not read (they contain $): " + ", ".join(skipped))
        cwd = ROOT / cfg.get("cwd", ".")
        timeout = cfg.get("timeout", g.get("timeout", 300))
        try:
            for svc in cfg["services"]:
                if svc.get("wait_port") and port_open(svc["wait_port"]):
                    if svc["name"] == "ollama":
                        self.logline("[launcher] Ollama is already running on port " + str(svc["wait_port"]) + "; using it.")
                        continue
                    self.logline("[launcher] " + svc["name"] + ": port " + str(svc["wait_port"]) +
                                 " is already in use. Stop the other instance first.")
                    self.say("Port busy")
                    self.stop_all()
                    return
                self.say("Starting " + svc["name"] + "...")
                senv = dict(env)
                senv.update(svc.get("env", {}))
                cmd = list(svc["cmd"])
                if IS_WIN:
                    exe = shutil.which(cmd[0], path=senv["PATH"])
                    if not exe:
                        self.fail("'" + cmd[0] + "' was not found in PATH")
                        return
                    cmd[0] = exe
                elif svc.get("mem") and shutil.which("systemd-run"):
                    envargs = ["env"] + [k + "=" + v for k, v in svc.get("env", {}).items()]
                    cmd = ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=" + svc["mem"]] + envargs + cmd
                p = spawn(cmd, cwd, senv)
                self.procs.append((svc["name"], p))
                threading.Thread(target=self.pump, args=(svc["name"], p, LOGS / (svc["name"] + ".log")), daemon=True).start()
                if svc.get("wait_port"):
                    for _ in range(30):
                        if port_open(svc["wait_port"]):
                            break
                        if p.poll() is not None:
                            self.fail(svc["name"] + " exited with code " + str(p.returncode))
                            return
                        time.sleep(1)
                    else:
                        self.fail(svc["name"] + " did not open port " + str(svc["wait_port"]))
                        return
            self.say("Waiting for " + g["name"] + " to be ready...")
            start = time.time()
            while time.time() - start < timeout:
                for name, p in self.procs:
                    if p.poll() is not None:
                        self.fail(name + " stopped with code " + str(p.returncode))
                        return
                try:
                    with urllib.request.urlopen(g["health_url"], timeout=3) as r:
                        if r.status == 200 and b"ok" in r.read():
                            self.open_url = g["open_url"]
                            self.say("Ready. Opened " + g["open_url"])
                            webbrowser.open(g["open_url"])
                            self.set_buttons("disabled", "normal", "normal")
                            return
                except Exception:
                    pass
                time.sleep(2)
            self.fail("Health check timed out after " + str(timeout) + " s")
        except Exception as ex:
            self.fail("Launcher error: " + repr(ex))

    def fail(self, msg):
        self.say("FAILED: " + msg)
        self.logline("[launcher] FAILED: " + msg + " (full logs in launcher/logs/)")
        self.stop_all()

    def stop_all(self):
        for name, p in reversed(self.procs):
            kill_tree(p)
        self.procs = []
        self.busy = False
        self.open_url = None
        self.set_buttons("normal", "disabled", "disabled")

    def stop(self):
        self.say("Stopping...")
        threading.Thread(target=lambda: (self.stop_all(), self.say("Stopped")), daemon=True).start()

    def on_close(self):
        self.stop_all()
        self.root.destroy()


if __name__ == "__main__":
    r = tk.Tk()
    App(r)
    r.mainloop()
