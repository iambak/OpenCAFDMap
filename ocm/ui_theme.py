"""OpenLauncher's dark theme: plain tkinter / ttk (no extra packages), dark surfaces with red accents."""
import ctypes
import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

C = {
    "bg": "#121214",         # window
    "side": "#0c0c0e",       # sidebar
    "card": "#1b1b1f",       # cards
    "card2": "#232328",      # inputs, hover
    "border": "#2c2c33",
    "text": "#ececf1",
    "muted": "#9b9ba7",
    "faint": "#6b6b76",
    "accent": "#e1323f",     # red
    "accent_hi": "#f2505c",
    "accent_lo": "#a8222d",
    "ok": "#38c983",
    "warn": "#f0a33a",
    "bad": "#ef4b56",
    "log": "#0e0e10",
}


def fonts(root):
    fam = "Segoe UI" if "Segoe UI" in tkfont.families(root) else "TkDefaultFont"
    mono = next((m for m in ("Cascadia Mono", "Consolas", "Courier New") if m in tkfont.families(root)), "TkFixedFont")
    return {
        "body": (fam, 10), "small": (fam, 9), "bold": (fam, 10, "bold"), "title": (fam, 18, "bold"),
        "h2": (fam, 12, "bold"), "stat": (fam, 15, "bold"), "big": (fam, 13, "bold"), "mono": (mono, 9),
        "brand": (fam, 14, "bold"),
    }


def dark_titlebar(win):
    """Windows 10/11: ask DWM for a dark title bar (no effect elsewhere)."""
    if sys.platform != "win32":
        return
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        on = ctypes.c_int(1)
        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (20; 19 on early Windows 10 builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), ctypes.sizeof(on)) == 0:
                break
    except Exception:  # noqa: BLE001 - purely cosmetic
        pass


def _checkbox_images(root):
    """16x16 checkbox images drawn pixel by pixel (no image library): off, on (red + white tick), greyed variants."""
    def box(fill, border, tick=None):
        img = tk.PhotoImage(master=root, width=20, height=16)          # 4 px transparent gap before the label
        for y in range(1, 15):
            row = []
            for x in range(1, 15):
                edge = x in (1, 14) or y in (1, 14)
                row.append(border if edge else fill)
            img.put("{" + " ".join(row) + "}", to=(1, y))
        if tick:
            pts = [(4, 8), (5, 9), (6, 10), (7, 11), (8, 10), (9, 9), (10, 8), (11, 7), (12, 6)]
            for x, y in pts:
                for dx, dy in ((0, 0), (0, -1), (1, 0)):
                    img.put(tick, to=(x + dx, y + dy))
        return img
    imgs = {
        "off": box(C["card2"], "#4a4a54"),
        "on": box(C["accent"], C["accent"], "#ffffff"),
        "off_dis": box(C["card"], "#33333a"),
        "on_dis": box("#5a2a2f", "#5a2a2f", "#9b9ba7"),
    }
    root._ocm_checkbox_imgs = imgs        # keep references
    return imgs


def apply(root):
    f = fonts(root)
    root.configure(bg=C["bg"])
    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=C["bg"], foreground=C["text"], font=f["body"], bordercolor=C["border"],
                 lightcolor=C["border"], darkcolor=C["border"], troughcolor=C["card"], focuscolor=C["accent"],
                 selectbackground=C["accent"], selectforeground="#ffffff", fieldbackground=C["card2"],
                 insertcolor=C["text"])
    # frames / labels
    for name, bg in (("TFrame", C["bg"]), ("Side.TFrame", C["side"]), ("Card.TFrame", C["card"])):
        st.configure(name, background=bg)
    for name, bg, fg, fnt in (("TLabel", C["bg"], C["text"], f["body"]),
                              ("Muted.TLabel", C["bg"], C["muted"], f["small"]),
                              ("Title.TLabel", C["bg"], C["text"], f["title"]),
                              ("Side.TLabel", C["side"], C["text"], f["brand"]),
                              ("SideMuted.TLabel", C["side"], C["faint"], f["small"]),
                              ("SideBold.TLabel", C["side"], C["muted"], f["bold"]),
                              ("SideLink.TLabel", C["side"], "#c8c8d0", f["small"]),
                              ("SideLinkHover.TLabel", C["side"], C["accent_hi"], f["small"]),
                              ("SideNote.TLabel", C["side"], C["faint"], (f["small"][0], 8)),
                              ("Card.TLabel", C["card"], C["text"], f["body"]),
                              ("CardBold.TLabel", C["card"], C["text"], f["bold"]),
                              ("CardH.TLabel", C["card"], C["text"], f["h2"]),
                              ("CardMuted.TLabel", C["card"], C["muted"], f["small"]),
                              ("Stat.TLabel", C["card"], C["text"], f["stat"]),
                              ("StatOk.TLabel", C["card"], C["ok"], f["small"]),
                              ("StatWarn.TLabel", C["card"], C["warn"], f["small"]),
                              ("StatBad.TLabel", C["card"], C["bad"], f["small"])):
        st.configure(name, background=bg, foreground=fg, font=fnt)
    # buttons
    st.configure("TButton", background=C["card2"], foreground=C["text"], borderwidth=0, focusthickness=0,
                 padding=(12, 6), relief="flat")
    st.map("TButton", background=[("disabled", C["card"]), ("pressed", C["border"]), ("active", "#2e2e35")],
           foreground=[("disabled", C["faint"])])
    for name, fnt, pad in (("Accent.TButton", f["bold"], (16, 8)), ("Hero.TButton", f["big"], (26, 14))):
        st.configure(name, background=C["accent"], foreground="#ffffff", font=fnt, padding=pad)
        st.map(name, background=[("disabled", C["accent_lo"]), ("pressed", C["accent_lo"]),
                                 ("active", C["accent_hi"])], foreground=[("disabled", "#d8b4b7")])
    st.configure("Ghost.TButton", background=C["card"], foreground=C["muted"], padding=(10, 5))
    st.map("Ghost.TButton", background=[("active", C["card2"]), ("pressed", C["border"])],
           foreground=[("active", C["text"]), ("disabled", C["faint"])])
    st.configure("Nav.TButton", background=C["side"], foreground=C["muted"], anchor="w", padding=(18, 9),
                 font=f["body"])
    st.map("Nav.TButton", background=[("active", "#17171b")], foreground=[("active", C["text"])])
    st.configure("NavOn.TButton", background="#1d1d22", foreground=C["text"], anchor="w", padding=(18, 9),
                 font=f["bold"])
    st.map("NavOn.TButton", background=[("active", "#1d1d22")])
    # inputs
    st.configure("TEntry", fieldbackground=C["card2"], foreground=C["text"], bordercolor=C["border"],
                 lightcolor=C["card2"], darkcolor=C["card2"], padding=(6, 5))
    st.map("TEntry", bordercolor=[("focus", C["accent"])], lightcolor=[("focus", C["accent"])])
    st.configure("TCombobox", fieldbackground=C["card2"], background=C["card2"], foreground=C["text"],
                 arrowcolor=C["muted"], bordercolor=C["border"], lightcolor=C["card2"], darkcolor=C["card2"],
                 padding=(6, 5))
    st.map("TCombobox", background=[("readonly", C["card2"]), ("active", C["border"])],
           arrowcolor=[("active", C["text"])], fieldbackground=[("readonly", C["card2"])], foreground=[("readonly", C["text"])],
           selectbackground=[("readonly", C["card2"])], selectforeground=[("readonly", C["text"])],
           bordercolor=[("focus", C["accent"])])
    root.option_add("*TCombobox*Listbox.background", C["card2"])
    root.option_add("*TCombobox*Listbox.foreground", C["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", C["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox.font", f["body"])
    ci = _checkbox_images(root)
    try:
        st.element_create("ocm.Checkbutton.indicator", "image", ci["off"], ("disabled", "selected", ci["on_dis"]),
                          ("selected", ci["on"]), ("disabled", ci["off_dis"]))
        st.layout("Card.TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("ocm.Checkbutton.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                ("Checkbutton.label", {"sticky": "nswe"})]})]})])
    except tk.TclError:
        pass                                   # element already defined (second window): keep the default look
    st.configure("Card.TCheckbutton", background=C["card"], foreground=C["text"], focusthickness=0)
    st.map("Card.TCheckbutton", background=[("active", C["card"])], foreground=[("disabled", C["faint"])])
    # scrollbars + progress
    for o in ("Vertical", "Horizontal"):
        st.configure(f"{o}.TScrollbar", background=C["card2"], troughcolor=C["log"], bordercolor=C["log"],
                     arrowcolor=C["muted"], lightcolor=C["card2"], darkcolor=C["card2"], gripcount=0)
        st.map(f"{o}.TScrollbar", background=[("active", C["border"])])
    st.configure("Busy.Horizontal.TProgressbar", background=C["accent"], troughcolor=C["bg"], bordercolor=C["bg"],
                 lightcolor=C["accent"], darkcolor=C["accent"], thickness=3)
    return f


def card(parent, padding=16):
    """A flat card: a 1-px border around a padded surface. Returns the inner frame."""
    outer = tk.Frame(parent, bg=C["border"], highlightthickness=0, bd=0)
    inner = ttk.Frame(outer, style="Card.TFrame", padding=padding)
    inner.pack(fill="both", expand=True, padx=1, pady=1)
    inner.outer = outer
    return inner
