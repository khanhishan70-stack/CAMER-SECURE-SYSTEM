from __future__ import annotations

import json
import hashlib
import sys
import tkinter as tk
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DATA_DIR

PASSWORD_FILE = DATA_DIR / "password.hash"
FONT = ("Segoe UI", 16)
TITLE_FONT = ("Segoe UI", 28, "bold")


def _verify(pwd: str) -> bool:
    if not PASSWORD_FILE.is_file():
        return False
    data = json.loads(PASSWORD_FILE.read_text(encoding="utf-8"))
    salt = bytes.fromhex(data["salt"])
    stored = bytes.fromhex(data["hash"])
    h = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), salt, 100_000)
    return h == stored


def run_lock_screen() -> None:
    root = tk.Tk()
    root.title("SecureCam — Locked")

    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.focus_force()
    root.grab_set()

    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{sw}x{sh}+0+0")
    root.configure(bg="#0a0a0f")

    container = tk.Frame(root, bg="#0a0a0f")
    container.place(relx=0.5, rely=0.5, anchor="center")

    lock_icon = tk.Label(container, text="LOCKED", font=("Segoe UI", 20, "bold"), bg="#0a0a0f", fg="#6c63ff")
    lock_icon.pack(pady=(0, 10))

    title = tk.Label(
        container,
        text="SecureCam Locked",
        font=TITLE_FONT,
        bg="#0a0a0f",
        fg="#ffffff",
    )
    title.pack(pady=(0, 6))

    sub = tk.Label(
        container,
        text="Unknown person detected · Enter password to unlock",
        font=("Segoe UI", 12),
        bg="#0a0a0f",
        fg="#888899",
    )
    sub.pack(pady=(0, 30))

    pwd_var = tk.StringVar()
    entry = tk.Entry(
        container,
        textvariable=pwd_var,
        font=FONT,
        show="●",
        bg="#1a1a2e",
        fg="#ffffff",
        insertbackground="#ffffff",
        relief="flat",
        width=30,
        bd=0,
        highlightthickness=2,
        highlightbackground="#3a3a5e",
        highlightcolor="#6c63ff",
    )
    entry.pack(pady=(0, 16), ipady=8, ipadx=6)

    error_lbl = tk.Label(container, text="", font=("Segoe UI", 11), bg="#0a0a0f", fg="#ff4444")
    error_lbl.pack(pady=(0, 10))

    def unlock() -> None:
        pwd = pwd_var.get()
        if _verify(pwd):
            root.grab_release()
            root.destroy()
        else:
            error_lbl.configure(text="Incorrect password")
            pwd_var.set("")
            entry.focus_set()

    def on_key(ev):
        if ev.keysym == "Return":
            unlock()

    entry.bind("<Key>", on_key)
    entry.focus_set()

    root.mainloop()


def show_warning_popup(side: str) -> bool:
    root = tk.Tk()
    root.title("SecureCam — Warning")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.focus_force()
    root.grab_set()
    root.configure(bg="#0d0d1a")

    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()

    popup_w = 540
    popup_h = 340
    x = (sw - popup_w) // 2
    y = (sh - popup_h) // 2

    root.geometry(f"1x1+{x + popup_w // 2}+{y + popup_h // 2}")
    root.attributes("-alpha", 0)

    result = [False]
    anim_active = [True]

    main_bg = "#0d0d1a"
    neon_cyan = "#00f5d4"
    neon_purple = "#b388ff"
    accent_red = "#ff4757"
    accent_yellow = "#ffd93d"

    # Outer glow frame
    outer = tk.Frame(root, bg=neon_purple, highlightthickness=0)
    outer.pack(fill="both", expand=True, padx=0, pady=0)
    inner = tk.Frame(outer, bg=main_bg, highlightthickness=0)
    inner.pack(fill="both", expand=True, padx=2, pady=2)

    # Icon with glow
    icon_frame = tk.Frame(inner, bg=main_bg)
    icon_frame.pack(pady=(35, 0))

    glow_lbl = tk.Label(
        icon_frame,
        text="⚠",
        font=("Segoe UI", 64),
        bg=main_bg,
        fg=accent_red,
    )
    glow_lbl.pack()

    # Title
    side_label = "left" if side == "left" else "right"
    title_lbl = tk.Label(
        inner,
        text=f"Someone is watching your screen",
        font=("Segoe UI", 18, "bold"),
        bg=main_bg,
        fg="#ffffff",
    )
    title_lbl.pack(pady=(12, 0))

    side_lbl = tk.Label(
        inner,
        text=f"from {side_label} side!",
        font=("Segoe UI", 18, "bold"),
        bg=main_bg,
        fg=accent_yellow,
    )
    side_lbl.pack(pady=(0, 4))

    # Subtitle
    sub_lbl = tk.Label(
        inner,
        text="Would you like to lock the screen?",
        font=("Segoe UI", 13),
        bg=main_bg,
        fg="#9a9abf",
    )
    sub_lbl.pack(pady=(0, 22))

    # Buttons
    btn_frame = tk.Frame(inner, bg=main_bg)
    btn_frame.pack()

    def on_yes():
        result[0] = True
        anim_active[0] = False
        root.grab_release()
        root.destroy()

    def on_no():
        result[0] = False
        anim_active[0] = False
        root.grab_release()
        root.destroy()

    def on_key(ev):
        if ev.keysym == "Return":
            on_yes()
        elif ev.keysym == "Escape":
            on_no()

    yes_btn = tk.Button(
        btn_frame,
        text="YES, Lock Screen",
        font=("Segoe UI", 12, "bold"),
        bg=neon_cyan,
        fg="#0d0d1a",
        padx=26,
        pady=10,
        relief="flat",
        cursor="hand2",
        command=on_yes,
        activebackground="#00c9b0",
        activeforeground="#0d0d1a",
        bd=0,
    )
    yes_btn.pack(side="left", padx=(0, 14))

    no_btn = tk.Button(
        btn_frame,
        text="NO, Dismiss",
        font=("Segoe UI", 12, "bold"),
        bg="#2a2a4a",
        fg="#c0c0e0",
        padx=26,
        pady=10,
        relief="flat",
        cursor="hand2",
        command=on_no,
        activebackground="#3a3a5e",
        activeforeground="#ffffff",
        bd=0,
    )
    no_btn.pack(side="left")

    root.bind("<Key>", on_key)
    root.protocol("WM_DELETE_WINDOW", on_no)

    def animate(step=0):
        if not anim_active[0]:
            return
        total_steps = 12
        if step <= total_steps:
            progress = step / total_steps
            ease = 1 - (1 - progress) ** 3
            w = int(40 + (popup_w - 40) * ease)
            h = int(40 + (popup_h - 40) * ease)
            cx = x + (popup_w - w) // 2
            cy = y + (popup_h - h) // 2
            root.geometry(f"{w}x{h}+{cx}+{cy}")
            root.attributes("-alpha", min(1.0, ease * 1.2))
            root.after(20, animate, step + 1)
        else:
            root.geometry(f"{popup_w}x{popup_h}+{x}+{y}")
            root.attributes("-alpha", 1)

    root.after(10, animate)
    root.mainloop()
    return result[0]


def run_unlock_click_screen() -> None:
    """Fullscreen fallback lock used by the "lock screen" voice command when
    the real Windows lock is unavailable and no unlock password is configured.

    Unlike the password-protected lock screen (which guards against strangers),
    this one is raised by the owner on purpose, so a single click unlocks.
    """
    root = tk.Tk()
    root.title("SecureCam — Locked")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.focus_force()
    root.grab_set()

    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{sw}x{sh}+0+0")
    root.configure(bg="#0a0a0f")

    container = tk.Frame(root, bg="#0a0a0f")
    container.place(relx=0.5, rely=0.5, anchor="center")

    tk.Label(container, text="LOCKED", font=("Segoe UI", 20, "bold"), bg="#0a0a0f", fg="#6c63ff").pack(pady=(0, 10))
    tk.Label(container, text="SecureCam Locked", font=TITLE_FONT, bg="#0a0a0f", fg="#ffffff").pack(pady=(0, 6))
    tk.Label(
        container,
        text="\"Lock screen\" heard · PC locked",
        font=("Segoe UI", 12),
        bg="#0a0a0f",
        fg="#888899",
    ).pack(pady=(0, 30))

    def unlock() -> None:
        root.grab_release()
        root.destroy()

    btn = tk.Button(
        container,
        text="UNLOCK",
        font=("Segoe UI", 14, "bold"),
        bg="#6c63ff",
        fg="#ffffff",
        padx=34,
        pady=10,
        relief="flat",
        cursor="hand2",
        command=unlock,
        activebackground="#5a52e0",
        bd=0,
    )
    btn.pack()
    root.bind("<Return>", lambda ev: unlock())
    root.bind("<Escape>", lambda ev: unlock())
    btn.focus_set()

    root.mainloop()


def lock_windows_session() -> bool:
    """Lock the actual Windows session - equivalent to pressing Win+L.

    Returns True when the OS accepted the request. On non-Windows platforms or
    when the call fails it returns False so the caller can fall back to the
    in-app password lock screen.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.user32.LockWorkStation())
    except Exception:
        return False


if __name__ == "__main__":
    run_lock_screen()
