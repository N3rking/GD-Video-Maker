"""GD Video Maker: pick an MP4, choose settings, and it gets fitted with real GD objects and injected into a level.

Run with no arguments for the window. The window runs the heavy work by starting this same program again with
--fit or --build, so the fitting code works exactly as it does from the command line.
"""
import json
import multiprocessing
import os
import subprocess
import sys
import threading
import time

APP = "GD Video Maker"
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "GDVideoMaker")
OUTPUTS = os.path.join(os.path.expanduser("~"), "Documents", APP)
STEAM_RES = r"C:\Program Files (x86)\Steam\steamapps\common\Geometry Dash\Resources"
SAVE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "GeometryDash", "CCLocalLevels.dat")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

CPUS = os.cpu_count() or 2
# key, label, slider min, slider max, Advanced max, step, default, help
SETTINGS = [
    ("first", "Objects per redraw", 100, 5000, 50000, 100, 3000, "Objects used when a frame is drawn fresh"),
    ("per_frame", "New objects per frame", 10, 1000, 10000, 10, 300, "Objects added to show changes between redraws"),
    ("target_rmse", "Target error", 1, 20, 50, 0.5, 4, "Stop adding objects once this accurate. Lower is sharper"),
    ("detail", "Edge detail boost", 0, 10, 50, 0.5, 4, "How much more text and outlines count than flat areas"),
    ("fps", "FPS", 5, 60, 240, 1, 30, "Frames per second in the level"),
    ("size", "Fit resolution", 96, 576, 1152, 48, 288, "Pixels the fitter works at. Higher is finer but slower"),
    ("max_showing", "Max objects showing", 1000, 15000, 100000, 500, 6000, "Redraw fresh past this many, to limit lag"),
    ("tries", "Search tries", 20, 500, 5000, 10, 120, "Random objects tested per placement"),
    ("climbs", "Refine steps", 0, 60, 500, 1, 15, "Nudges used to perfect each placement"),
    ("workers", "CPU workers", 1, CPUS, CPUS * 4, 1, max(1, CPUS - 2), "Processes working at once"),
]
INTEGER = {"first", "per_frame", "fps", "size", "max_showing", "tries", "climbs", "workers"}
# first, per-frame, target error, detail
PRESETS = {
    "Fast (less lag, softer)": (1000, 150, 10, 0),
    "Balanced": (2000, 200, 6, 4),
    "Sharp (readable text)": (3000, 300, 4, 4),
}

os.environ["PATH"] = HERE + os.pathsep + os.environ.get("PATH", "")  # bundled ffmpeg.exe


def self_command(*args):
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, os.path.abspath(__file__), *args]


def child_mode(argv):
    """--fit/--build: runs one step with its output going to a log file the window reads."""
    mode, log, rest = argv[0], argv[1], argv[2:]
    sys.stdout = sys.stderr = open(log, "w", buffering=1, encoding="utf8")
    if os.name == "nt":  # this program has no console, so keep ffmpeg from opening one per call
        original = subprocess.Popen.__init__

        def hidden(self, *a, **k):
            k.setdefault("creationflags", NO_WINDOW)
            original(self, *a, **k)
        subprocess.Popen.__init__ = hidden
    if mode == "--fit":
        os.environ["GD_FIT_SIZE"] = rest[rest.index("--size") + 1] if "--size" in rest else "288"
        os.environ["GD_LIBRARY"] = os.path.join(DATA, "library.npz")
        os.environ["GD_PROGRESS_DIR"] = os.path.join(DATA, "progress")
        sys.argv = ["fit_sharp", *rest]
        import fit_sharp
        fit_sharp.main()
    else:
        sys.argv = ["build_level", *rest]
        import build_level
        build_level.main()


def ensure_library(res, log):
    """Builds the verified object library from the player's own GD art the first time."""
    path = os.path.join(DATA, "library.npz")
    marker = os.path.join(DATA, "library_source.txt")
    if os.path.exists(path) and os.path.exists(marker) and open(marker).read() == res:
        return
    if not os.path.exists(os.path.join(res, "GJ_GameSheet-uhd.plist")):
        raise RuntimeError(f"Couldn't find GD's sprite sheets in:\n{res}\nPick your Geometry Dash 'Resources' folder.")
    log("First run: reading object art from your GD install...")
    import build_library
    verified = json.load(open(os.path.join(HERE, "verified_objects.json")))
    os.makedirs(DATA, exist_ok=True)
    n = build_library.library_from_names(verified["names"], verified["ids"], res, path)
    open(marker, "w").write(res)
    log(f"Object library ready: {n} verified still objects.")


# Measured on a 16-thread PC at fit resolution 288: CPU-seconds per video frame for each preset, and its speed test time.
COST = {"Fast (less lag, softer)": 15.5, "Balanced": 30, "Sharp (readable text)": 59}
BASELINE_BENCH = 0.127


def bench():
    """A short test of the same kind of array math the fitter does, to compare this PC with the measured one."""
    import numpy as np
    rng = np.random.default_rng(0)
    a, b = rng.random((288, 288, 3), np.float32), rng.random((288, 288, 3), np.float32)
    best = 1e9
    for _ in range(3):
        t = time.perf_counter()
        for _ in range(4000):
            x = a[40:140, 60:160] - b[40:140, 60:160]
            (x * x).sum()
            a[40:140, 60:160] = a[40:140, 60:160] * 0.7 + b[40:140, 60:160] * 0.3
        best = min(best, time.perf_counter() - t)
    return best


def ram_gb():
    import ctypes

    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in
                                                                             ("total", "free", "pt", "pf", "vt", "vf", "ve")]
    s = Status()
    s.length = ctypes.sizeof(Status)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
    return s.total / 2 ** 30, s.free / 2 ** 30


def video_seconds(path):
    r = subprocess.run(["ffmpeg", "-i", path], capture_output=True, text=True, creationflags=NO_WINDOW)
    for part in r.stderr.split("Duration: ")[1:2]:
        h, m, sec = part.split(",")[0].split(":")
        return int(h) * 3600 + int(m) * 60 + float(sec)
    return None


def recommend(video, fps, minutes_ok=60):
    """Picks the sharpest preset that finishes in about an hour on this PC, plus workers and resolution that fit its RAM."""
    threads = os.cpu_count() or 2
    total, free = ram_gb()
    speed = BASELINE_BENCH / bench()  # per-core speed compared with the measured PC
    seconds = (video_seconds(video) if video and os.path.exists(video) else None) or 30
    frames = seconds * fps
    workers = max(1, threads - 2)  # leave room for the PC itself
    # The fitter holds every frame in memory (about 1 MB each at 288, three copies) plus about 150 MB per worker.
    size = 288
    while size > 144 and frames * (size / 288) ** 2 * 3 / 1024 + workers * 0.15 > free * 0.8:
        size -= 48
    workers = max(1, min(workers, int(free * 0.8 / 0.15)))
    est = {p: frames * c * (size / 288) ** 2 / (workers * speed) / 60 for p, c in COST.items()}
    pick = next((p for p in ("Sharp (readable text)", "Balanced") if est[p] <= minutes_ok), "Fast (less lag, softer)")
    report = (f"Your PC: {threads} CPU threads, {total:.0f} GB RAM ({free:.0f} GB free), speed {speed:.2f}x the tested PC.\n"
              f"Video: {seconds:.0f} s at {fps:g} fps = {frames:.0f} frames" + ("" if video else " (no video picked, so 30 s assumed)") + ".\n\n"
              "Estimated fitting time:\n" + "\n".join(f"   {p}: about {m:.0f} min" for p, m in est.items()) +
              f"\n\nRecommended: {pick}, {workers} workers, fit resolution {size}.")
    return dict(preset=pick, workers=workers, size=size), report


def process_tree(pid):
    """pid plus every process it started, so pausing also reaches the fitter's worker processes."""
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                    ("module", wintypes.DWORD), ("threads", wintypes.DWORD), ("parent", wintypes.DWORD), ("priority", ctypes.c_long),
                    ("flags", wintypes.DWORD), ("exe", ctypes.c_wchar * 260)]
    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k.CreateToolhelp32Snapshot(2, 0)  # all processes
    e = Entry()
    e.size = ctypes.sizeof(Entry)
    children = {}
    ok = k.Process32FirstW(wintypes.HANDLE(snap), ctypes.byref(e))
    while ok:
        children.setdefault(e.parent, []).append(e.pid)
        ok = k.Process32NextW(wintypes.HANDLE(snap), ctypes.byref(e))
    k.CloseHandle(wintypes.HANDLE(snap))
    found, todo = [], [pid]
    while todo:
        p = todo.pop()
        if p not in found:
            found.append(p)
            todo += children.get(p, [])
    return found


def set_paused(pid, pause):
    """Freezes or unfreezes a process tree in place; nothing is lost while paused."""
    import ctypes
    from ctypes import wintypes
    k, nt = ctypes.windll.kernel32, ctypes.windll.ntdll
    k.OpenProcess.restype = wintypes.HANDLE
    for p in process_tree(pid):
        h = k.OpenProcess(0x0800, False, p)  # PROCESS_SUSPEND_RESUME
        if h:
            (nt.NtSuspendProcess if pause else nt.NtResumeProcess)(wintypes.HANDLE(h))
            k.CloseHandle(wintypes.HANDLE(h))


def gd_running():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq GeometryDash.exe"], capture_output=True, text=True, creationflags=NO_WINDOW).stdout
    return "GeometryDash.exe" in out


def open_gd(res):
    """Starts GD through Steam, or its exe next to the Resources folder if Steam's link doesn't work."""
    try:
        os.startfile("steam://rungameid/322170")
    except OSError:
        exe = os.path.join(os.path.dirname(res.rstrip("\\/")), "GeometryDash.exe")
        if not os.path.exists(exe):
            raise RuntimeError("Couldn't start Geometry Dash. Is Steam installed?") from None
        subprocess.Popen([exe], cwd=os.path.dirname(exe))


def ffmpeg(*args):
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True, text=True, creationflags=NO_WINDOW)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or "ffmpeg failed")


BG, PANEL, FIELD, BORDER = "#262624", "#30302e", "#1f1e1d", "#45443f"
TEXT, MUTED, ACCENT, ACCENT_HOVER, ERROR = "#ece9e1", "#a8a59b", "#d97757", "#c4643f", "#ff6b6b"


def dark_theme(root, ttk):
    """Claude-style dark colors. Explicit colors and fonts also fix Windows' default theme drawing ttk text invisibly."""
    font = ("Segoe UI", 10)
    root.configure(bg=BG)
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=BG, foreground=TEXT, fieldbackground=FIELD, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                troughcolor=FIELD, insertcolor=TEXT, selectbackground=ACCENT, selectforeground=TEXT, font=font)
    s.configure("TEntry", padding=5)
    s.configure("TCombobox", padding=5, arrowcolor=TEXT, background=PANEL)
    s.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)], background=[("active", PANEL)])
    s.configure("TButton", background=PANEL, padding=(12, 6), relief="flat")
    s.map("TButton", background=[("disabled", BG), ("active", "#3a3936")], foreground=[("disabled", MUTED)])
    s.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", bordercolor=ACCENT, font=("Segoe UI Semibold", 10))
    s.map("Accent.TButton", background=[("disabled", PANEL), ("active", ACCENT_HOVER)])
    s.configure("TCheckbutton", indicatorbackground=FIELD, indicatorforeground=ACCENT, focuscolor=BG)
    s.map("TCheckbutton", background=[("active", BG)], indicatorbackground=[("selected", FIELD)])
    s.configure("TLabelframe", background=BG, bordercolor=BORDER)
    s.configure("TLabelframe.Label", background=BG, foreground=MUTED)
    s.configure("Muted.TLabel", foreground=MUTED)
    s.configure("Title.TLabel", font=("Segoe UI Semibold", 15))
    s.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=FIELD, bordercolor=BORDER, thickness=8)
    for k, val in (("background", FIELD), ("foreground", TEXT), ("selectBackground", ACCENT), ("selectForeground", "#ffffff")):
        root.option_add(f"*TCombobox*Listbox.{k}", val)
    try:  # dark title bar on Windows 10/11
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(1)), 4)
    except Exception:
        pass


def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title(APP)
    root.minsize(1200, 720)
    root.update_idletasks()
    dark_theme(root, ttk)
    try:
        root.iconbitmap(os.path.join(HERE, "icon.ico"))
    except tk.TclError:
        pass
    s = ttk.Style(root)
    s.configure("Horizontal.TScale", background=ACCENT, troughcolor=FIELD, bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT)
    s.map("Horizontal.TScale", background=[("active", ACCENT_HOVER)])
    s.configure("Value.TLabel", foreground=ACCENT, font=("Segoe UI Semibold", 10))
    s.configure("Card.TFrame", background=PANEL)
    s.configure("Card.TLabel", background=PANEL)
    s.configure("CardMuted.TLabel", background=PANEL, foreground=MUTED)
    s.configure("Big.TLabel", background=PANEL, font=("Segoe UI Semibold", 13))

    left = ttk.Frame(root, padding=16)
    left.pack(side="left", fill="both", expand=True)
    left.columnconfigure(1, weight=1)
    right = ttk.Frame(root, padding=(0, 16, 16, 16))
    right.pack(side="left", fill="both")

    ttk.Label(left, text=APP, style="Title.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
    ttk.Label(left, text="Turn a video into Geometry Dash objects, then inject it into a level.", style="Muted.TLabel").grid(
        row=1, column=0, columnspan=3, sticky="w", pady=(0, 10))
    v = {k: tk.StringVar(value=d) for k, d in dict(video="", level="", preset="Sharp (readable text)", start="", length="",
                                                   res=STEAM_RES, save=SAVE).items()}
    for key, *_rest, default, _tip in SETTINGS:
        v[key] = tk.StringVar(value=str(default))
    mp3_too = tk.BooleanVar(value=True)
    inject = tk.BooleanVar(value=True)
    advanced = tk.BooleanVar(value=False)
    state = {"proc": None, "cancel": False, "running": False, "t0": 0, "total": 0, "first_err": {}, "paused_at": 0}
    row = [2]

    def line(label, widget, button=None):
        ttk.Label(left, text=label).grid(row=row[0], column=0, sticky="w", pady=2, padx=(0, 8))
        widget.grid(row=row[0], column=1, sticky="ew", pady=2)
        if button:
            button.grid(row=row[0], column=2, padx=(6, 0), pady=2, sticky="ew")
        row[0] += 1

    def pick_video():
        p = filedialog.askopenfilename(filetypes=[("Videos", "*.mp4 *.mov *.mkv *.webm *.avi"), ("All files", "*.*")])
        if p:
            v["video"].set(p)
            if not v["level"].get():
                v["level"].set(os.path.splitext(os.path.basename(p))[0][:30])

    line("Video", ttk.Entry(left, textvariable=v["video"]), ttk.Button(left, text="Browse...", command=pick_video))
    line("Level name", ttk.Entry(left, textvariable=v["level"]))

    def apply_preset(_e=None):
        for key, value in zip(("first", "per_frame", "target_rmse", "detail"), PRESETS[v["preset"].get()]):
            v[key].set(str(value))

    preset = ttk.Combobox(left, textvariable=v["preset"], values=list(PRESETS), state="readonly")
    preset.bind("<<ComboboxSelected>>", apply_preset)
    line("Quality preset", preset, ttk.Button(left, text="Recommended settings", command=lambda: suggest()))

    box = ttk.LabelFrame(left, text="Settings", padding=(10, 6))
    box.grid(row=row[0], column=0, columnspan=3, sticky="ew", pady=6)
    box.columnconfigure(1, weight=1)
    row[0] += 1
    entries, tips = [], []

    def fmt(key, x):
        return str(int(round(x))) if key in INTEGER else f"{x:g}"

    def slider(r, key, label, lo, hi, step, tip):
        """A slider tied to the setting's text. A typed value past the slider's end is kept; the slider just sits at its end."""
        ttk.Label(box, text=label).grid(row=r, column=0, sticky="w", padx=(0, 8))
        pos = tk.DoubleVar(value=float(v[key].get()))
        busy = [False]

        def from_slider(*_):
            if not busy[0]:
                busy[0] = True
                v[key].set(fmt(key, round(pos.get() / step) * step))
                busy[0] = False

        def from_text(*_):
            if busy[0]:
                return
            busy[0] = True
            try:
                pos.set(min(hi, max(lo, float(v[key].get()))))
            except ValueError:
                pass
            busy[0] = False
        pos.trace_add("write", from_slider)
        v[key].trace_add("write", from_text)
        scale = ttk.Scale(box, from_=lo, to=hi, variable=pos)
        scale.grid(row=r, column=1, sticky="ew", pady=3)
        ttk.Label(box, textvariable=v[key], style="Value.TLabel", width=7, anchor="e").grid(row=r, column=2, sticky="e", padx=(8, 0))
        entry = ttk.Entry(box, textvariable=v[key], width=8)
        entry.grid(row=r, column=3, padx=(8, 0))
        entry.grid_remove()
        entries.append(entry)
        tips.append((scale, tip))

    for r, (key, label, lo, hi, _adv, step, _d, tip) in enumerate(SETTINGS):
        slider(r, key, label, lo, hi, step, tip)
    hint = ttk.Label(box, text="Point at a slider to see what it does.", style="Muted.TLabel")
    hint.grid(row=len(SETTINGS), column=0, columnspan=4, sticky="w", pady=(4, 0))
    for scale, tip in tips:
        scale.bind("<Enter>", lambda _e, t=tip: hint.configure(text=t))

    def toggle_advanced():
        for e in entries:
            e.grid() if advanced.get() else e.grid_remove()
        hint.configure(text="Advanced: type values up to much higher limits. The slider stops at its end." if advanced.get()
                       else "Point at a slider to see what it does.")

    ttk.Checkbutton(left, text="Advanced: type exact values with higher limits", variable=advanced, command=toggle_advanced).grid(
        row=row[0], column=0, columnspan=3, sticky="w")
    row[0] += 1
    trim = ttk.Frame(left)
    ttk.Label(trim, text="Start at").pack(side="left")
    ttk.Entry(trim, textvariable=v["start"], width=7).pack(side="left", padx=(6, 12))
    ttk.Label(trim, text="Length").pack(side="left")
    ttk.Entry(trim, textvariable=v["length"], width=7).pack(side="left", padx=6)
    ttk.Label(trim, text="seconds, blank = whole video", style="Muted.TLabel").pack(side="left")
    line("Trim", trim)
    line("GD Resources", ttk.Entry(left, textvariable=v["res"]),
         ttk.Button(left, text="Browse...", command=lambda: v["res"].set(filedialog.askdirectory() or v["res"].get())))
    line("GD save file", ttk.Entry(left, textvariable=v["save"]))
    ttk.Checkbutton(left, text="Inject into Geometry Dash when done (waits for GD to be closed)", variable=inject).grid(
        row=row[0], column=0, columnspan=3, sticky="w")
    row[0] += 1
    ttk.Checkbutton(left, text="Also save the video's audio as MP3", variable=mp3_too).grid(row=row[0], column=0, columnspan=3, sticky="w")
    row[0] += 1
    buttons = ttk.Frame(left)
    buttons.grid(row=row[0], column=0, columnspan=3, sticky="ew", pady=(10, 0))

    # Right side: what the fitter is doing right now.
    card = ttk.Frame(right, style="Card.TFrame", padding=12)
    card.pack(fill="both", expand=True)
    ttk.Label(card, text="Live preview", style="Big.TLabel").pack(anchor="w")
    ttk.Label(card, text="Left: the video frame.  Right: the GD objects placed so far.", style="CardMuted.TLabel").pack(anchor="w", pady=(0, 8))
    picture = tk.Label(card, bg=FIELD, width=80, height=18, bd=0)  # characters until the first picture arrives
    picture.pack()
    frame_text = ttk.Label(card, text="Frame -", style="Card.TLabel")
    frame_text.pack(anchor="w", pady=(10, 2))
    frame_bar = ttk.Progressbar(card, maximum=100)
    frame_bar.pack(fill="x")
    ttk.Label(card, text="Whole video", style="Card.TLabel").pack(anchor="w", pady=(12, 2))
    total_bar = ttk.Progressbar(card, maximum=100)
    total_bar.pack(fill="x")
    status = ttk.Label(card, text="Pick a video to start.", style="Card.TLabel")
    status.pack(anchor="w", pady=(6, 0))
    eta = ttk.Label(card, text="", style="CardMuted.TLabel")
    eta.pack(anchor="w")
    ttk.Button(card, text="Open Geometry Dash", command=lambda: launch_gd(), style="Accent.TButton").pack(anchor="w", pady=(10, 0))
    logbox = tk.Text(card, height=7, width=70, wrap="word", state="disabled", bg=FIELD, fg=TEXT, insertbackground=TEXT, relief="flat",
                     highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER, font=("Consolas", 9), padx=8, pady=6)
    logbox.pack(fill="both", expand=True, pady=(10, 0))
    photo = [None]
    progress_dir = os.path.join(DATA, "progress")

    logbox.tag_configure("error", foreground=ERROR)

    def log(text, error=False):
        def add():
            logbox.configure(state="normal")
            logbox.insert("end", text + "\n", ("error",) if error else ())
            logbox.see("end")
            logbox.configure(state="disabled")
        root.after(0, add)

    def set_status(text, pct=None, error=False):
        root.after(0, lambda: (status.configure(text=text, foreground=ERROR if error else TEXT),
                               pct is not None and total_bar.configure(value=pct)))

    def clock(sec):
        sec = int(sec)
        return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"

    def poll():
        """Reads the fitter's progress files. The earliest unfinished frame is shown, so the preview doesn't jump between scenes."""
        if state["paused_at"]:
            eta.configure(text=f"Paused   ·   elapsed {clock(state['paused_at'] - state['t0'])}" if state["t0"] else "Paused")
        elif state["running"] and state["total"] and os.path.isdir(progress_dir):
            infos = {}
            for name in os.listdir(progress_dir):
                if name.endswith(".json"):
                    try:
                        with open(os.path.join(progress_dir, name)) as f:
                            infos[name[:-5]] = json.load(f)
                    except (OSError, ValueError):
                        pass
            done = sum(i["done"] for i in infos.values())
            # A chunk's file names the frame it's on; its "done" count says how many of its frames are finished.
            active = [(i["frame"], k) for k, i in infos.items() if i["done"] <= i["frame"] - int(k)]
            if active:
                frame, key = min(active)
                info = infos[key]
                first = state["first_err"].setdefault((key, frame), max(info["err"], info["goal"] + 0.1))
                by_error = (first - info["err"]) / max(1e-6, first - info["goal"])
                frame_bar.configure(value=100 * min(1, max(info["placed"] / max(1, info["limit"]), by_error)))
                frame_text.configure(text=f"Frame {frame + 1} of {state['total']}   ·   {info['placed']:,} of up to {info['limit']:,} objects   ·   "
                                          f"error {info['err']:.1f} → goal {info['goal']:.1f}   ·   {len(active)} frames in progress")
                try:
                    img = tk.PhotoImage(file=os.path.join(progress_dir, key + ".ppm"))
                    shrink = -(-img.width() // 600)
                    photo[0] = img.subsample(shrink) if shrink > 1 else img
                    picture.configure(image=photo[0], width=photo[0].width(), height=photo[0].height())
                except (tk.TclError, OSError):
                    pass
            pct = 100 * done / state["total"]
            total_bar.configure(value=pct)
            spent = time.time() - state["t0"]
            left_time = spent / done * (state["total"] - done) if done else None
            status.configure(text=f"Fitting: {done} of {state['total']} frames done ({pct:.0f}%)")
            eta.configure(text=f"Elapsed {clock(spent)}   ·   " + (f"about {clock(left_time)} left" if left_time is not None else "estimating time left..."))
        root.after(500, poll)

    def out_dir():
        name = "".join(c if c.isalnum() or c in " -_" else "_" for c in v["level"].get().strip()) or "video"
        d = os.path.join(OUTPUTS, name)
        os.makedirs(d, exist_ok=True)
        return d

    def save_mp3(dest=None):
        src = v["video"].get()
        if not os.path.exists(src):
            messagebox.showerror(APP, "Pick a video first.")
            return
        dest = dest or filedialog.asksaveasfilename(defaultextension=".mp3", initialfile=os.path.splitext(os.path.basename(src))[0] + ".mp3",
                                                    filetypes=[("MP3", "*.mp3")])
        if not dest:
            return
        try:
            ffmpeg("-i", src, "-vn", "-c:a", "libmp3lame", "-q:a", "2", dest)
            log(f"MP3 saved: {dest}")
        except RuntimeError as e:
            messagebox.showerror(APP, f"Couldn't extract audio (does the video have sound?)\n\n{e}")

    def run_step(mode, args):
        """Runs --fit or --build in a child process and copies its log into the window."""
        logfile = os.path.join(DATA, f"{mode.strip('-')}.log")
        os.makedirs(DATA, exist_ok=True)
        open(logfile, "w").close()
        proc = subprocess.Popen(self_command(mode, logfile, *args), creationflags=NO_WINDOW)
        state["proc"] = proc
        seen, failing = 0, False
        while True:
            finished = proc.poll() is not None
            with open(logfile, encoding="utf8", errors="replace") as f:
                lines = f.read().splitlines()
            for text in lines[seen:]:
                failing = failing or text.startswith(("Traceback", "ERROR"))
                if not text.startswith("chunk "):
                    log(text, error=failing)
                if text.startswith("fitting "):
                    state["total"], state["t0"] = int(text.split()[1]), time.time()
            seen = len(lines)
            if finished:
                break
            time.sleep(0.5)
        state["proc"] = None
        if state["cancel"]:
            raise RuntimeError("Cancelled.")
        if proc.returncode:
            if lines and not failing:  # a short reason, like "Close Geometry Dash first", already went in the log
                log(lines[-1], error=True)
            raise RuntimeError(lines[-1] if lines else f"{mode} failed")

    def number(key):
        spec = next(sp for sp in SETTINGS if sp[0] == key)
        try:
            x = float(v[key].get())
        except ValueError:
            raise RuntimeError(f"{spec[1]}: '{v[key].get()}' isn't a number.") from None
        low = 0 if key in ("detail", "climbs") else 0.1 if key == "target_rmse" else 1
        if not low <= x <= spec[4]:
            raise RuntimeError(f"{spec[1]} must be between {low:g} and {spec[4]:g}.")
        return int(round(x)) if key in INTEGER else x

    def work():
        try:
            src, level = v["video"].get(), v["level"].get().strip()
            if not os.path.exists(src):
                raise RuntimeError("Pick a video first.")
            if not level:
                raise RuntimeError("Give the level a name.")
            args = []
            for key, *_r in SETTINGS:
                args += ["--" + key.replace("_", "-"), str(number(key))]
            ensure_library(v["res"].get(), log)
            d = out_dir()
            video = src
            if v["start"].get().strip() or v["length"].get().strip():
                video = os.path.join(d, "trimmed.mp4")
                try:
                    cut = (["-ss", str(float(v["start"].get()))] if v["start"].get().strip() else []) + \
                          (["-t", str(float(v["length"].get()))] if v["length"].get().strip() else [])
                except ValueError:
                    raise RuntimeError("Trim start and length must be numbers of seconds.") from None
                set_status("Trimming the video...")
                ffmpeg(*cut, "-i", src, "-an", video)
            if mp3_too.get():
                save_mp3(os.path.join(d, os.path.splitext(os.path.basename(src))[0] + ".mp3"))
            out = os.path.join(d, "fit")
            os.makedirs(progress_dir, exist_ok=True)
            for name in os.listdir(progress_dir):
                os.remove(os.path.join(progress_dir, name))
            state["first_err"].clear()
            set_status("Reading the video...", 0)
            state["running"] = True
            root.after(0, lambda: pause_btn.configure(state="normal"))
            run_step("--fit", [video, "--out", out, *args])
            state["running"] = False
            root.after(0, lambda: pause_btn.configure(state="disabled"))
            root.after(600, lambda: (total_bar.configure(value=100), frame_bar.configure(value=100)))
            took = time.time() - state["t0"]
            root.after(700, lambda: eta.configure(text=f"Fitting took {clock(took)}"))
            log(f"Preview video: {out}_preview.mp4 (original | GD objects | pixel grid)")
            if inject.get():
                # GD rewrites its save when it closes, so wait for it instead of failing after a long fit.
                if gd_running():
                    log("Waiting for Geometry Dash to close before injecting...")
                while gd_running():
                    set_status("Close Geometry Dash to inject the level.", 100, error=True)
                    if state["cancel"]:
                        raise RuntimeError("Cancelled.")
                    time.sleep(2)
                set_status("Injecting into Geometry Dash...", 100)
                run_step("--build", [out + ".json", "--level", level, "--save", v["save"].get()])
                set_status(f'Done. Open "{level}" in Geometry Dash.', 100)
            else:
                set_status("Done. Fitted, not injected.", 100)
        except Exception as e:  # shown to the user rather than lost in a hidden console
            state["running"] = False
            cancelled = str(e) == "Cancelled."
            set_status("Cancelled." if cancelled else f"Stopped: {e}", error=True)
            log(f"ERROR: {e}", error=True)
            if not cancelled:
                root.after(0, lambda: messagebox.showerror(APP, str(e)))
        finally:
            state["cancel"] = False
            state["paused_at"] = 0
            root.after(0, lambda: (start_btn.configure(state="normal"), pause_btn.configure(state="disabled", text="Pause")))

    def start():
        start_btn.configure(state="disabled")
        state["total"], state["t0"], state["paused_at"] = 0, 0, 0
        threading.Thread(target=work, daemon=True).start()

    def toggle_pause():
        proc = state["proc"]
        if not proc or not state["running"]:
            return
        if state["paused_at"]:
            set_paused(proc.pid, False)
            state["t0"] += time.time() - state["paused_at"]  # paused time doesn't count toward elapsed or the estimate
            state["paused_at"] = 0
            pause_btn.configure(text="Pause")
            log("Resumed.")
        else:
            set_paused(proc.pid, True)
            state["paused_at"] = time.time()
            pause_btn.configure(text="Resume")
            status.configure(text="Paused. Press Resume to continue.", foreground=TEXT)
            log("Paused.")

    def cancel():
        state["cancel"] = True  # also stops waiting for GD to close
        if state["proc"]:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(state["proc"].pid)], capture_output=True, creationflags=NO_WINDOW)

    def launch_gd():
        try:
            open_gd(v["res"].get())
            log("Opening Geometry Dash...")
        except RuntimeError as e:
            log(f"ERROR: {e}", error=True)
            messagebox.showerror(APP, str(e))

    def open_path(p):
        if os.path.exists(p):
            os.startfile(p)
        else:
            messagebox.showinfo(APP, "Nothing there yet.")

    def suggest():
        set_status("Checking your PC...")

        def check():
            try:
                fps = float(v["fps"].get())
            except ValueError:
                fps = 30.0
            try:
                pick, report = recommend(v["video"].get(), fps)
            except Exception as e:
                root.after(0, lambda: messagebox.showerror(APP, f"Couldn't check this PC: {e}"))
                return

            def apply():
                set_status("Ready." if v["video"].get() else "Pick a video to start.")
                if messagebox.askyesno(APP, report + "\n\nUse these settings?"):
                    v["preset"].set(pick["preset"])
                    apply_preset()
                    v["workers"].set(str(pick["workers"]))
                    v["size"].set(str(pick["size"]))
                    log("Applied recommended settings.")
            root.after(0, apply)
        threading.Thread(target=check, daemon=True).start()

    start_btn = ttk.Button(buttons, text="Generate", command=start, style="Accent.TButton")
    start_btn.pack(side="left")
    pause_btn = ttk.Button(buttons, text="Pause", command=toggle_pause, state="disabled", width=8)
    pause_btn.pack(side="left", padx=(6, 0))
    ttk.Button(buttons, text="Cancel", command=cancel).pack(side="left", padx=6)
    ttk.Button(buttons, text="Extract MP3...", command=save_mp3).pack(side="left")
    ttk.Button(buttons, text="Open preview", command=lambda: open_path(os.path.join(out_dir(), "fit_preview.mp4"))).pack(side="left", padx=6)
    ttk.Button(buttons, text="Output folder", command=lambda: open_path(out_dir())).pack(side="left")
    ttk.Button(buttons, text="Credits", command=lambda: messagebox.showinfo(
        f"{APP} credits", open(os.path.join(HERE, "CREDITS.txt"), encoding="utf8").read())).pack(side="left", padx=(6, 0))
    root.protocol("WM_DELETE_WINDOW", lambda: (cancel(), root.destroy()))
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):  # a video dropped onto the .exe
        v["video"].set(sys.argv[1])
        v["level"].set(os.path.splitext(os.path.basename(sys.argv[1]))[0][:30])
    poll()
    root.mainloop()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 2 and sys.argv[1] in ("--fit", "--build"):
        child_mode(sys.argv[1:])
    else:
        run_gui()
