#!/usr/bin/env python3
"""OpenLauncher (Open CAFD Map): set your paths, build the definitions, start E-Sys with labels, or open
the BimmerCode Backup Editor. Start it with OpenLauncher.bat (or: pythonw openlauncher.py)."""
import glob
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "ocm"))
import config  # noqa: E402
import esys  # noqa: E402
import pipeline  # noqa: E402
import updater  # noqa: E402
import appicon  # noqa: E402
import ui_theme as ui  # noqa: E402

FIELDS = [  # key, label, kind, help
    ("esys_dir", "E-Sys folder", "dir", "contains E-Sys.bat, jre\\ and lib\\"),
    ("psdzdata_dir", "psdzdata folder", "dir", "empty = the one E-Sys is set up with"),
    ("series", "Main series", "text", "G020 = G20/G21/G22/G23/G26/G28/G8x"),
    ("context", "Reference vehicle", "context", "shortens value labels to what matters for this vehicle class"),
    ("workspace", "Workspace", "dir", "DB, decrypted cache, definitions, logs (never committed)"),
    ("bimmercode_backups_dir", "BimmerCode backups", "dir", "optional: folder with your .cod backups"),
    ("cheat_sheets_dir", "Cheat sheets", "dir", "optional: FDL cheat-sheet XMLs (imported locally only)"),
    ("ista_db", "ISTA DiagDocDb", "file", "optional: decrypted DiagDocDb .sqlite (option-code titles)"),
]


def app_version():
    """'v1.0.0' for a release install (VERSION is added by installer/build_release.py), '' otherwise."""
    try:
        return "v" + open(os.path.join(ROOT, "VERSION"), encoding="utf-8").read().strip()
    except OSError:
        return ""


def pythonw():
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return w if os.path.exists(w) else exe


class OpenLauncher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"OpenLauncher {app_version()} - Open CAFD Map".replace("  ", " "))
        appicon.apply(self)
        self.geometry("1120x780")
        self.minsize(980, 680)
        self.f = ui.apply(self)
        self.cfg = config.load()
        self.vars = {}
        self.busy = False
        self.q = queue.Queue()
        self._build()
        ui.dark_titlebar(self)
        self.refresh_status()
        self.show_page("home")
        self.after(100, self._drain)
        self.after(800, lambda: self.check_updates(silent=True))
        self.after(1200, lambda: self.check_launcher(silent=True))

    # ---------------------------------------------------------------- layout
    def _build(self):
        C = ui.C
        # sidebar ---------------------------------------------------------------
        side = ttk.Frame(self, style="Side.TFrame", width=236)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        brand = ttk.Frame(side, style="Side.TFrame", padding=(18, 22, 18, 18))
        brand.pack(fill="x")
        try:
            self._logo = tk.PhotoImage(file=appicon.PNG).subsample(7)
            tk.Label(brand, image=self._logo, bg=C["side"], bd=0).pack(side="left", padx=(0, 10))
        except tk.TclError:
            pass
        bt = ttk.Frame(brand, style="Side.TFrame")
        bt.pack(side="left")
        ttk.Label(bt, text="OpenLauncher", style="Side.TLabel").pack(anchor="w")
        ttk.Label(bt, text=(app_version() or "source checkout"), style="SideMuted.TLabel").pack(anchor="w")
        self.nav = {}
        for key, text in (("home", "⌂   Home"), ("settings", "⚙   Settings"), ("activity", "≡   Activity")):
            b = ttk.Button(side, text=text, style="Nav.TButton", command=lambda k=key: self.show_page(k))
            b.pack(fill="x", padx=10, pady=1)
            self.nav[key] = b
        foot = ttk.Frame(side, style="Side.TFrame", padding=(18, 14))
        foot.pack(side="bottom", fill="x")
        ttk.Label(foot, text="Open CAFD Map\nCoding names for E-Sys\nFree and open source",
                  style="SideMuted.TLabel", justify="left").pack(anchor="w")
        tk.Frame(self, bg=C["border"], width=1).pack(side="left", fill="y")

        # main area ---------------------------------------------------------------
        main = ttk.Frame(self, padding=(28, 20, 28, 16))
        main.pack(side="left", fill="both", expand=True)
        head = ttk.Frame(main)
        head.pack(fill="x")
        self.page_title = tk.StringVar()
        ttk.Label(head, textvariable=self.page_title, style="Title.TLabel").pack(side="left")
        self.page_sub = tk.StringVar()
        ttk.Label(head, textvariable=self.page_sub, style="Muted.TLabel").pack(side="left", padx=14, pady=(8, 0))
        slot = tk.Frame(main, bg=ui.C["bg"], height=3)     # fixed-height slot: the bar shows only while busy
        slot.pack(fill="x", pady=(10, 12))
        slot.pack_propagate(False)
        self.progress = ttk.Progressbar(slot, mode="indeterminate", style="Busy.Horizontal.TProgressbar")
        self._progress_on = False
        self.pages = {k: ttk.Frame(main) for k in ("home", "settings", "activity")}
        self._build_activity(self.pages["activity"])     # first: the other pages write to the log
        self._build_settings(self.pages["settings"])
        self._build_home(self.pages["home"])

    def show_page(self, key):
        for k, p in self.pages.items():
            p.pack_forget()
            self.nav[k].configure(style="NavOn.TButton" if k == key else "Nav.TButton")
        self.pages[key].pack(fill="both", expand=True)
        self.page_title.set({"home": "Home", "settings": "Settings", "activity": "Activity"}[key])
        self.page_sub.set({"home": "Start E-Sys, build definitions, stay up to date",
                           "settings": "Paths and options, saved to config.json",
                           "activity": "Everything OpenLauncher did this session"}[key])

    def _build_home(self, page):
        # hero: start E-Sys -------------------------------------------------------
        hero = ui.card(page, padding=(22, 18))
        hero.outer.pack(fill="x")
        left = ttk.Frame(hero, style="Card.TFrame")
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text="Start coding", style="CardH.TLabel").pack(anchor="w")
        ttk.Label(left, text="Opens E-Sys with BMW names and value labels in the CAF and FDL views. "
                             "No build needed.", style="CardMuted.TLabel").pack(anchor="w", pady=(2, 12))
        row = ttk.Frame(left, style="Card.TFrame")
        row.pack(anchor="w")
        self.btn_esys = ttk.Button(row, text="▶  Start E-Sys", style="Hero.TButton", command=self.start_esys)
        self.btn_esys.pack(side="left")
        ttk.Button(row, text="BimmerCode Backup Editor", command=self.start_bc).pack(side="left", padx=12)
        self.esys_info = tk.StringVar()
        ttk.Label(hero, textvariable=self.esys_info, style="CardMuted.TLabel", justify="right").pack(side="right",
                                                                                                   anchor="n")

        # status cards ------------------------------------------------------------
        stats = ttk.Frame(page)
        stats.pack(fill="x", pady=14)
        self.stat = {}
        for i, (key, title) in enumerate((("esys", "E-SYS"), ("psdz", "PSDZDATA"), ("defs", "DEFINITIONS"),
                                          ("db", "DATABASE"))):
            c = ui.card(stats, padding=(16, 12))
            c.outer.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 7, 0 if i == 3 else 7))
            stats.columnconfigure(i, weight=1, uniform="stat")
            ttk.Label(c, text=title, style="CardMuted.TLabel").pack(anchor="w")
            v, d = tk.StringVar(), tk.StringVar()
            ttk.Label(c, textvariable=v, style="Stat.TLabel").pack(anchor="w", pady=(2, 0))
            dl = ttk.Label(c, textvariable=d, style="StatOk.TLabel")
            dl.pack(anchor="w")
            self.stat[key] = (v, d, dl)

        # definitions + updates ---------------------------------------------------
        cols = ttk.Frame(page)
        cols.pack(fill="x")
        cols.columnconfigure(0, weight=1, uniform="col")
        cols.columnconfigure(1, weight=1, uniform="col")
        d = ui.card(cols)
        d.outer.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        ttk.Label(d, text="Definitions", style="CardH.TLabel").pack(anchor="w", pady=(0, 8))
        self.btns = []
        for text, step, tip in (("Build definitions", "build", "For the Backup Editor"),
                                ("Apply names", "names", "After editing names"),
                                ("Check contributions", "check", "Before you contribute")):
            r = ttk.Frame(d, style="Card.TFrame")
            r.pack(fill="x", pady=3)
            w = ttk.Button(r, text=text, width=20, command=lambda s=step: self.run_step(s))
            w.pack(side="left")
            ttk.Label(r, text=tip, style="CardMuted.TLabel").pack(side="left", padx=10)
            self.btns.append(w)

        u = ui.card(cols)
        u.outer.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        ttk.Label(u, text="Updates", style="CardH.TLabel").pack(anchor="w", pady=(0, 8))
        # each button has three states: "Check for updates" -> "Update now" | "... up to date"
        ttk.Label(u, text="DEFINITIONS (NAMES)", style="CardMuted.TLabel").pack(anchor="w")
        ur = ttk.Frame(u, style="Card.TFrame")
        ur.pack(fill="x", pady=(2, 10))
        self.btn_update = ttk.Button(ur, text="Check for updates", width=22, command=self.update_clicked)
        self.btn_update.pack(side="left")
        self.update_state = "check"
        self.update_label = tk.StringVar()
        ttk.Label(ur, textvariable=self.update_label, style="CardMuted.TLabel", wraplength=230,
                  justify="left").pack(side="left", padx=10)
        self.show_versions(None)
        ttk.Label(u, text="LAUNCHER", style="CardMuted.TLabel").pack(anchor="w")
        lr = ttk.Frame(u, style="Card.TFrame")
        lr.pack(fill="x", pady=(2, 0))
        self.btn_launcher = ttk.Button(lr, text="Check for launcher update", width=22, command=self.launcher_clicked)
        self.btn_launcher.pack(side="left")
        # git clones only: move to origin/main even with local edits/commits (they are saved first)
        self.btn_force = ttk.Button(lr, text="Force update", style="Ghost.TButton", command=self.force_launcher_update)
        if updater.is_git_clone():
            self.btn_force.pack(side="left", padx=(6, 0))
        self.launcher_state = "check"
        self.launcher_label = tk.StringVar()
        ttk.Label(u, textvariable=self.launcher_label, style="CardMuted.TLabel", wraplength=380,
                  justify="left").pack(anchor="w", pady=(6, 0))
        self.set_launcher_state("check")

        # folders + latest activity -----------------------------------------------
        fo = ui.card(page, padding=(16, 10))
        fo.outer.pack(fill="x", pady=(14, 0))
        for text, cmd in (("Workspace", lambda: self.open_dir(config.paths().workspace)),
                          ("Names", lambda: self.open_dir(config.NAMES)),
                          ("Logs", lambda: self.open_dir(config.paths().logs)),
                          ("Desktop shortcut", self.make_shortcut)):
            ttk.Button(fo, text=text, style="Ghost.TButton", command=cmd).pack(side="left", padx=(0, 6))
        self.last_line = tk.StringVar(value="Ready.")
        last = ttk.Label(fo, textvariable=self.last_line, style="CardMuted.TLabel", cursor="hand2")
        last.pack(side="right")
        last.bind("<Button-1>", lambda e: self.show_page("activity"))

    def _build_settings(self, page):
        s = ui.card(page, padding=(22, 18))
        s.outer.pack(fill="x")
        ttk.Label(s, text="Paths", style="CardH.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        contexts = ["none"] + sorted(os.path.basename(p) for p in glob.glob(os.path.join(config.DATA, "contexts", "*.xml")))
        for i, (key, label, kind, hlp) in enumerate(FIELDS):
            r = 1 + i * 2
            ttk.Label(s, text=label, style="CardBold.TLabel").grid(row=r, column=0, sticky="w", padx=(0, 18))
            v = self.vars[key] = tk.StringVar(value=str(self.cfg.get(key) or ""))
            if kind == "context":
                w = ttk.Combobox(s, textvariable=v, values=contexts, state="readonly")
            else:
                w = ttk.Entry(s, textvariable=v)
            w.grid(row=r, column=1, sticky="we")
            if kind in ("dir", "file"):
                ttk.Button(s, text="Browse", style="Ghost.TButton",
                           command=lambda k=key, t=kind: self.browse(k, t)).grid(row=r, column=2, padx=(8, 0))
            ttk.Label(s, text=hlp[:1].upper() + hlp[1:], style="CardMuted.TLabel").grid(row=r + 1, column=1,
                                                                                     sticky="w", pady=(1, 8))
        s.columnconfigure(1, weight=1)

        o = ui.card(page, padding=(22, 16))
        o.outer.pack(fill="x", pady=(14, 0))
        ttk.Button(o, text="Save settings", style="Accent.TButton", command=self.save).pack(side="right", anchor="s")
        ttk.Label(o, text="Options", style="CardH.TLabel").pack(anchor="w", pady=(0, 8))
        self.all_versions = tk.BooleanVar(value=bool(self.cfg.get("export_all_versions")))
        ttk.Checkbutton(o, text="Export every CAFD version (bigger; otherwise the newest 3 plus the versions in your "
                                "BimmerCode backups)", variable=self.all_versions,
                        style="Card.TCheckbutton").pack(anchor="w", pady=2)
        self.try_untested = tk.BooleanVar(value=bool(self.cfg.get("try_untested_esys")))
        ttk.Checkbutton(o, text="Try anyway: allow Cheat Sheet Apply on E-Sys versions OpenLauncher wasn't tested "
                                "with (tested: 25.x)", variable=self.try_untested,
                        style="Card.TCheckbutton").pack(anchor="w", pady=2)

    def _build_activity(self, page):
        bar = ttk.Frame(page)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text="Open logs folder", style="Ghost.TButton",
                   command=lambda: self.open_dir(config.paths().logs)).pack(side="right")
        ttk.Button(bar, text="Clear", style="Ghost.TButton",
                   command=lambda: self.log.delete("1.0", "end")).pack(side="right", padx=6)
        box = tk.Frame(page, bg=ui.C["border"])
        box.pack(fill="both", expand=True)
        inner = tk.Frame(box, bg=ui.C["log"])
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.log = tk.Text(inner, wrap="none", bg=ui.C["log"], fg="#d6d6dd", insertbackground=ui.C["text"],
                           selectbackground=ui.C["accent"], relief="flat", bd=0, padx=12, pady=10,
                           font=self.f["mono"])
        self.log.tag_configure("err", foreground=ui.C["bad"])
        self.log.tag_configure("head", foreground=ui.C["accent_hi"])
        self.log.tag_configure("ok", foreground=ui.C["ok"])
        vs = ttk.Scrollbar(inner, orient="vertical", command=self.log.yview)
        hs = ttk.Scrollbar(inner, orient="horizontal", command=self.log.xview)
        self.log.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        inner.rowconfigure(0, weight=1)
        inner.columnconfigure(0, weight=1)

    # ---------------------------------------------------------------- settings
    def browse(self, key, kind):
        cur = self.vars[key].get()
        if kind == "dir":
            p = filedialog.askdirectory(initialdir=cur or None)
        else:
            p = filedialog.askopenfilename(initialdir=os.path.dirname(cur) if cur else None,
                                           filetypes=[("SQLite", "*.sqlite *.db"), ("All", "*.*")])
        if p:
            self.vars[key].set(os.path.normpath(p))

    def save(self, quiet=False):
        for key, *_ in FIELDS:
            self.cfg[key] = self.vars[key].get().strip()
        self.cfg["export_all_versions"] = self.all_versions.get()
        self.cfg["try_untested_esys"] = self.try_untested.get()
        config.save(self.cfg)
        self.cfg = config.load()
        self.vars["workspace"].set(self.cfg["workspace"])
        self.refresh_status()
        if not quiet:
            self.write("Settings saved to config.json")

    def _set_stat(self, key, value, detail, level="ok"):
        v, d, dl = self.stat[key]
        v.set(value)
        d.set(detail)
        dl.configure(style={"ok": "StatOk.TLabel", "warn": "StatWarn.TLabel", "bad": "StatBad.TLabel"}[level])

    def refresh_status(self):
        P = config.Paths(self.cfg)
        d = self.cfg.get("esys_dir") or ""
        esys_ok = os.path.exists(os.path.join(d, "lib", "esysGui.jar"))
        psdz_ok = bool(P.psdzdata) and os.path.isdir(os.path.join(P.psdzdata, "swe", "cafd"))
        n_cache = len(glob.glob(os.path.join(P.cache, "cafd_*.xml.gz")))
        n_defs = len(glob.glob(os.path.join(P.generated, "CAFD_*.xml")))
        ver = esys.esys_version(d) if esys_ok else ""
        self._set_stat("esys", ver or ("Found" if esys_ok else "Not found"),
                       os.path.basename(d.rstrip("\\/")) if esys_ok else "Set the E-Sys folder in Settings",
                       "ok" if esys_ok else "bad")
        self._set_stat("psdz", "Ready" if psdz_ok else "Not found",
                       (P.psdzdata or "-") if psdz_ok else "Check the psdzdata folder", "ok" if psdz_ok else "bad")
        self._set_stat("defs", f"{n_defs:,}", f"{self.cfg.get('series', '')}  ·  {n_cache:,} decrypted"
                       if n_cache else f"{self.cfg.get('series', '')}  ·  built on demand", "ok")
        has_db = os.path.exists(P.db)
        self._set_stat("db", "Built" if has_db else "Not built",
                       "Backup Editor ready" if has_db else "Only needed for the Backup Editor", "ok" if has_db else "warn")
        self.esys_info.set(f"E-Sys {ver}\nmain series {self.cfg.get('series', '')}" if esys_ok else "")

    # ---------------------------------------------------------------- definition updates
    def show_versions(self, available):
        cur = updater.installed().get("version", "0")
        self.update_label.set(f"Installed {cur}" + (f"\nAvailable {available}" if available and available != cur else ""))

    def set_update_state(self, state, available=None):
        self.update_state = state
        self.btn_update.configure(text={"check": "Check for updates", "available": "Update now",
                                        "current": "Definitions up to date", "busy": "Working..."}[state])
        self.btn_update.state(["disabled"] if state == "busy" else ["!disabled"])
        self.show_versions(available)

    def update_clicked(self):
        if self.update_state == "available":
            self.do_update()
        else:  # "check" and "current" both (re)check
            self.check_updates()

    def check_updates(self, silent=False):
        if self.busy:
            return
        self.save(quiet=True)
        self.set_update_state("busy")

        def work():
            try:
                cur, new, avail = updater.check(self.cfg)
                self.q.put(lambda: self.set_update_state("available" if avail else "current", new))
                if not silent:
                    self.q.put(f"Definitions: installed {cur}, available {new}" + (" - update available" if avail else ""))
            except updater.UpdateError as e:
                self.q.put(lambda: self.set_update_state("check"))
                if not silent:
                    self.q.put(f"Update check: {e}")
        threading.Thread(target=work, daemon=True).start()

    def do_update(self):
        if self.busy:
            return
        changed = updater.local_changes()
        if changed and not messagebox.askyesno(
                "Update definitions", f"You changed or added {len(changed)} file(s) in names/definitions/data "
                                      f"(e.g. {changed[0]}).\nThey will be backed up to workspace\\backups and "
                                      "replaced by the published versions. Continue?"):
            return
        self.busy = True
        self.set_update_state("busy")
        for w in self.btns + [self.btn_esys]:
            w.state(["disabled"])

        def work():
            try:
                if updater.update(self.cfg, self.q.put) and os.path.exists(config.paths().db):
                    self.q.put("== Applying the new names to your definitions")
                    pipeline.build(self.q.put, full=False)
                self.q.put(lambda: self.set_update_state("current", updater.installed().get("version")))
            except (updater.UpdateError, pipeline.StepFailed) as e:
                self.q.put(f"UPDATE FAILED: {e}")
                self.q.put(lambda: self.set_update_state("check"))
            except Exception as e:  # noqa: BLE001
                self.q.put(f"UPDATE FAILED: {type(e).__name__}: {e}")
                self.q.put(lambda: self.set_update_state("check"))
            self.q.put(None)
        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------------- launcher updates
    def set_launcher_state(self, state, available=None, git=None):
        why = updater.launcher_blocker(self.cfg)
        if why:
            state = "blocked"
        self.launcher_state = state
        self.btn_launcher.configure(text={"check": "Check for launcher update", "available": "Update launcher",
                                          "current": "Launcher up to date", "busy": "Working...",
                                          "blocked": "Launcher updates off"}[state])
        self.btn_launcher.state(["disabled"] if state in ("busy", "blocked") else ["!disabled"])
        self.btn_force.state(["disabled"] if state in ("busy", "blocked") else ["!disabled"])
        if updater.is_git_clone():
            g = git or {}
            text = "Installed launcher: git clone" + (f", {g['branch']} @ {g['head']}" if g else "")
            if g:
                text += f"     origin/main @ {g['latest']}" + (f" ({g['behind']} new commit(s))" if g["behind"] else "")
                local = [f"{g['dirty']} edited file(s)"] * bool(g["dirty"]) + [f"{g['ahead']} local commit(s)"] * bool(g["ahead"])
                if local:
                    text += f"     local: {', '.join(local)}"
        else:
            cur = updater.launcher_installed()
            text = (f"Installed launcher: {('v' + cur) if cur else 'source checkout'}"
                    + (f"     Available: v{available}" if available else ""))
        self.launcher_label.set(text + (f"     ({why})" if why else ""))

    def launcher_clicked(self):
        if self.launcher_state == "available":
            self.do_launcher_update()
        else:
            self.check_launcher()

    def check_launcher(self, silent=False):
        if updater.launcher_blocker(self.cfg):
            return
        self.set_launcher_state("busy")

        def work():
            try:
                if updater.is_git_clone():
                    g = updater.git_state()
                    avail = g["behind"] > 0
                    self.q.put(lambda: self.set_launcher_state("available" if avail else "current", git=g))
                    if not silent or avail:
                        self.q.put(f"Launcher (git clone): {g['branch']} @ {g['head']}, origin/main @ {g['latest']}"
                                   + (f" - {g['behind']} new commit(s) (Updates > Update launcher)" if avail else ""))
                    return
                cur, new, avail = updater.launcher_check()
                self.q.put(lambda: self.set_launcher_state("available" if avail else "current", new))
                if not silent or avail:
                    self.q.put(f"Launcher: installed v{cur}, latest release v{new}"
                               + (" - update available (Updates > Update launcher)" if avail else ""))
            except (updater.UpdateError, OSError, subprocess.SubprocessError) as e:
                self.q.put(lambda: self.set_launcher_state("check"))
                if not silent:
                    self.q.put(f"Launcher update check: {e}")
        threading.Thread(target=work, daemon=True).start()

    def _launcher_update_allowed(self, title):
        if self.busy:
            messagebox.showinfo(title, "Wait for the current task to finish first.")
            return False
        if esys_running():
            messagebox.showwarning(title, "Close E-Sys first: it runs Open CAFD Map's files from this "
                                          "install, and the update replaces them.")
            return False
        return True

    def _git_update(self, force):
        """Pull (or force-move) the clone in the background, then restart OpenLauncher on the new code."""
        self.busy = True
        self.set_launcher_state("busy")

        def work():
            try:
                if updater.git_update(force, self.q.put):
                    self.q.put("Restarting OpenLauncher on the updated code...")
                    self.q.put(self.restart)
                    return
                self.q.put(lambda: self.set_launcher_state("current"))
            except (updater.UpdateError, OSError, subprocess.SubprocessError) as e:
                self.q.put(f"LAUNCHER UPDATE FAILED: {e}")
                if not force:
                    self.q.put("If the clone has local edits or commits, Force update saves them and updates anyway.")
                self.q.put(lambda: self.set_launcher_state("check"))
            self.q.put(None)
        threading.Thread(target=work, daemon=True).start()

    def restart(self):
        subprocess.Popen([pythonw(), os.path.join(ROOT, "openlauncher.py")], cwd=ROOT)
        self.after(200, self.destroy)

    def force_launcher_update(self):
        if not self._launcher_update_allowed("Force update"):
            return
        if not messagebox.askyesno(
                "Force update", f"This moves the git clone in\n{ROOT}\nto origin/main, even if you changed files "
                                "or made commits. Nothing is lost:\n\n"
                                "  - uncommitted changes are saved to a git stash (git stash list)\n"
                                "  - local commits are saved to a branch named openlauncher-backup-<time>\n\n"
                                "Your workspace and config.json are not touched. Force update now?"):
            return
        self._git_update(force=True)

    def do_launcher_update(self):
        if not self._launcher_update_allowed("Update launcher"):
            return
        if updater.is_git_clone():
            self._git_update(force=False)
            return
        changed = updater.local_changes()
        note = (f"\n\nYou changed or added {len(changed)} file(s) in names/definitions/data (e.g. {changed[0]}); "
                "setup replaces those folders, so they are backed up to workspace\\backups first." if changed else "")
        if not messagebox.askyesno(
                "Update launcher", f"OpenLauncher closes and the latest release is installed into\n{ROOT}\n"
                                   "Your workspace and settings are kept. OpenLauncher starts again when it's done."
                                   + note + "\n\nUpdate now?"):
            return
        try:
            if changed:
                bdir = updater.backup_local_changes(changed, "launcher-update")
                self.write(f"Backed up {len(changed)} edited file(s) to {bdir}")
            updater.launcher_update(self.cfg, self.write)
        except (updater.UpdateError, OSError) as e:
            messagebox.showerror("Update launcher", str(e))
            return
        self.after(300, self.destroy)

    # ---------------------------------------------------------------- actions
    def write(self, line):
        up = line.lstrip().upper()
        tag = ("err" if up.startswith(("FAILED", "UPDATE FAILED", "LAUNCHER UPDATE FAILED", "ERROR")) else
               "head" if line.startswith("==") else
               "ok" if up.startswith(("DONE", "E-SYS STARTED", "SETTINGS SAVED", "SHORTCUT CREATED")) else None)
        self.log.insert("end", line + "\n", (tag,) if tag else ())
        self.log.see("end")
        if line.strip():
            self.last_line.set(line.strip()[:110])

    def _drain(self):
        if self.busy != self._progress_on:        # thin red bar under the title while something runs
            self._progress_on = self.busy
            if self.busy:
                self.progress.pack(fill="both", expand=True)
                self.progress.start(12)
            else:
                self.progress.stop()
                self.progress.pack_forget()
        try:
            while True:
                item = self.q.get_nowait()
                if callable(item):           # UI change requested by a worker thread
                    item()
                elif item is None:
                    self.busy = False
                    for w in self.btns + [self.btn_esys]:
                        w.state(["!disabled"])
                    self.refresh_status()
                else:
                    self.write(item)
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def run_step(self, step):
        if self.busy:
            return
        self.save(quiet=True)
        if step == "build" and not esys_ready(self.cfg):
            messagebox.showerror("E-Sys", "Set the E-Sys folder first (it is used to decrypt your psdzdata).")
            return
        self.busy = True
        for w in self.btns + [self.btn_esys]:
            w.state(["disabled"])

        def work():
            try:
                if step == "build":
                    pipeline.build(self.q.put, full=True)
                elif step == "names":
                    pipeline.build(self.q.put, full=False)
                else:
                    pipeline.check(self.q.put)
            except pipeline.StepFailed as e:
                self.q.put(f"FAILED: {e}")
            except SystemExit as e:
                self.q.put(f"FAILED: {e}")
            except Exception as e:  # noqa: BLE001
                self.q.put(f"FAILED: {type(e).__name__}: {e}")
            self.q.put(None)
        threading.Thread(target=work, daemon=True).start()

    def start_esys(self):
        self.save(quiet=True)
        if esys_running() and not messagebox.askyesno(
                "E-Sys is already running", "An E-Sys window is already open. Two E-Sys instances can conflict "
                                            "(PsdZ). Start another one anyway?"):
            return
        try:
            esys.check_esys(self.cfg)
        except SystemExit as e:
            messagebox.showerror("Start E-Sys", str(e))
            return
        self.btn_esys.state(["disabled"])
        cfg = dict(self.cfg)

        def work():
            try:
                esys.prepare(cfg, self.q.put)
                d = cfg["esys_dir"]
                spec = esys.launch_spec(d)
                self.q.put(f"E-Sys {esys.esys_version(d) or '(unknown version)'}, Java {esys.java_major(d) or '?'}; "
                           f"launch recipe: {spec['source']}")
                pr = esys.launch(cfg)
                self.q.put(f"E-Sys started with Open CAFD Map (pid {pr.pid}). Labels apply when you open a CAFD; "
                           f"one without a definition yet is labelled within seconds. "
                           f"Logs: {config.Paths(cfg).logs} (esys.log, agent.log)")
                self.q.put(lambda: self.btn_esys.state(["!disabled"]))
                # E-Sys that dies while starting: say so here, with the reason from esys.log. Exit code 0 is a
                # normal close (the user closed E-Sys); a failed start exits non-zero (bootstrap.js uses 3).
                for _ in range(90):
                    if pr.poll() is not None:
                        if pr.returncode == 0:
                            self.q.put("E-Sys closed.")
                            break
                        self.q.put(f"E-Sys stopped during startup (exit {pr.returncode}). Last lines of esys.log:")
                        for line in esys.log_tail(cfg):
                            self.q.put("    " + line)
                        break
                    time.sleep(1)
            except SystemExit as e:
                self.q.put(f"FAILED: {e}")
            except Exception as e:  # noqa: BLE001
                self.q.put(f"FAILED: {type(e).__name__}: {e}")
            self.q.put(lambda: self.btn_esys.state(["!disabled"]))
        threading.Thread(target=work, daemon=True).start()

    def start_bc(self):
        self.save(quiet=True)
        if not os.path.exists(config.paths().db):
            messagebox.showerror("BimmerCode Backup Editor", "Build the definitions first.")
            return
        subprocess.Popen([pythonw(), os.path.join(ROOT, "ocm", "bc_gui.py")], cwd=ROOT)
        self.write("BimmerCode Backup Editor started.")

    def make_shortcut(self):
        """OpenLauncher.lnk on the desktop, with the OpenLauncher icon (a .bat can't have one)."""
        ps = ("$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell)"
              ".CreateShortcut((Join-Path $d 'OpenLauncher.lnk')); $s.TargetPath=$env:OCM_T; "
              "$s.Arguments='\"'+$env:OCM_A+'\"'; $s.WorkingDirectory=$env:OCM_W; $s.IconLocation=$env:OCM_I; "
              "$s.Description='OpenLauncher - Open CAFD Map'; $s.Save(); Write-Output (Join-Path $d 'OpenLauncher.lnk')")
        env = dict(os.environ, OCM_T=pythonw(), OCM_A=os.path.join(ROOT, "openlauncher.py"), OCM_W=ROOT,
                   OCM_I=appicon.ICO)
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env,
                           capture_output=True, text=True, creationflags=esys.NO_WINDOW)
        if r.returncode == 0:
            self.write(f"Shortcut created: {r.stdout.strip()}")
        else:
            messagebox.showerror("Desktop shortcut", r.stderr.strip() or "could not create the shortcut")

    def open_dir(self, path):
        os.makedirs(path, exist_ok=True)
        os.startfile(path)


def esys_ready(cfg):
    return os.path.exists(os.path.join(cfg.get("esys_dir") or "", "jre", "bin", "java.exe"))


def esys_running():
    try:
        out = subprocess.run(["tasklist", "/V", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq javaw.exe"],
                             capture_output=True, text=True, creationflags=esys.NO_WINDOW, timeout=15).stdout
        return "E-Sys" in out
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    appicon.set_app_id()
    OpenLauncher().mainloop()
