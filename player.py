#!/usr/bin/env python3
"""
Leitor Multimédia Multi-Monitor / Multi-Monitor Media Player  (v5.36.49)
------------------------------------------------------------------
- Control interface on one screen (Tkinter, very light); two independent fullscreen
  outputs (Output 1 / Output 2) on other screens, each one a libmpv window.
- Each output has its own screen picker, on/off button, monitor, progress bar,
  times (current / total / remaining) and play / pause / stop buttons.
- A screen chosen for one output is disabled for the other.
- Each playlist item chooses its output; two items can play at the same time
  (one per output). New: "Reproduzir em Simultâneo" column to link any item with
  different output. Also "with next".
- Languages: Português / English (switch live, remembered between sessions).
- Per-item options: output, next (default None), repeat, start the next one X ms
  before the end, freeze first / last frame, in/out marks, aspect, with next, simultaneous.
- Save / open shows (.mmp) with all per-item settings and the settings of every output
  (name, resolution, FPS, volume). Old .json playlists can still be opened. Welcome screen on start.
- Outputs: the app starts with none; «Adicionar Output» creates as many as needed, each one has an ✕ to
  remove it and the output area has a scroll bar.
- New columns: «Agendar horário» (time of day at which the item starts by itself) and «Duração»
  (seconds an image stays on screen).
- Selected-item preview (bottom left) with timeline, transport and in/out markers.
- Right-click menu on an item: Duplicate, Remove, Output, Aspect.
- UI adjusted for lower resolution screens.
"""
import datetime
import json
import locale
import math
import atexit
import base64
import collections
import contextlib
import ctypes
import faulthandler
import os
import queue
import random
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request

# --- Platform preparation (before importing mpv) ------------------------------------
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # correct screen coordinates
    except Exception:
        pass
    _here = os.path.dirname(os.path.abspath(__file__))
    os.environ["PATH"] = _here + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(_here)  # looks for mpv-2.dll / libmpv-2.dll next to the script

locale.setlocale(locale.LC_NUMERIC, "C")  # required by libmpv

# --- Dependencies: check at start, offer to install what is missing (pip) ------------------------------------------
# (import name, pip name, required?, what it is for)
_DEPS = [
    ("mpv", "python-mpv", True, "video / audio engine (also needs mpv-2.dll next to this file)"),
    ("screeninfo", "screeninfo", True, "screen list"),
    ("numpy", "numpy", False, "NDI output"),
    ("NDIlib", "ndi-python", False, "NDI input / output"),
    ("zeroconf", "zeroconf", False, "finding NDI sources on the network"),
    ("tkinterdnd2", "tkinterdnd2", False, "drag and drop on the list"),
    ("psutil", "psutil", False, "diagnostics"),
    ("yt_dlp", "yt-dlp", False, "YouTube"),
    ("PIL", "pillow", False, "YouTube thumbnails"),
    ("certifi", "certifi", False, "secure (HTTPS) connections"),
    ("sounddevice", "sounddevice", False, "audio routing to ASIO / DirectSound / MME devices"),
]


def _ensure_dependencies():
    """Missing libraries: ask once, install them with pip (this same Python), carry on. A library the user refuses is
    remembered (player_logs/deps_skipped.txt) so the question is not repeated on every start. PLAYER_AUTOINSTALL=yes
    installs without asking, PLAYER_AUTOINSTALL=no never asks."""
    import importlib
    import importlib.util
    mode = os.environ.get("PLAYER_AUTOINSTALL", "").lower()
    if mode == "no":
        return
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "player_logs")
    skip_file = os.path.join(folder, "deps_skipped.txt")
    try:
        with open(skip_file, encoding="utf-8") as fh:
            skipped = {ln.strip() for ln in fh if ln.strip()}
    except OSError:
        skipped = set()

    def present(name):
        if name in sys.modules:
            return True
        try:
            return importlib.util.find_spec(name) is not None
        except Exception:
            return False
    missing = [d for d in _DEPS if not present(d[0]) and (d[2] or d[1] not in skipped)]
    if not missing:
        return
    lines = "\n".join(f"  • {d[1]}  —  {d[3]}" + ("" if d[2] else "  (optional)") for d in missing)
    need = any(d[2] for d in missing)
    root = None
    ok = mode == "yes"
    if not ok:
        try:
            import tkinter as _tk
            from tkinter import messagebox as _mb
            root = _tk.Tk()
            root.withdraw()
            ok = _mb.askyesno(
                "Dependências em falta / Missing libraries",
                f"Faltam estas bibliotecas Python / These Python libraries are missing:\n\n{lines}\n\n"
                f"Instalar agora com pip? / Install them now with pip?\n(Python {sys.version.split()[0]})", parent=root)
        except Exception:
            ok = True                               # no window possible (no display): install, print what happens
    if not ok:
        try:
            os.makedirs(folder, exist_ok=True)
            with open(skip_file, "a", encoding="utf-8") as fh:
                fh.write("".join(d[1] + "\n" for d in missing if not d[2]))
        except OSError:
            pass
        if need:
            sys.exit("Missing required libraries: " + ", ".join(d[1] for d in missing if d[2]))
        return
    box = None
    if root is not None:
        try:
            import tkinter as _tk
            box = _tk.Toplevel(root)
            box.title("A instalar… / Installing…")
            box.geometry("420x90")
            box.attributes("-topmost", True)
            lab = _tk.Label(box, text="", justify="left", padx=14, pady=14)
            lab.pack(fill="both", expand=True)
            root.update()
        except Exception:
            box = None
    flags = 0x08000000 if sys.platform == "win32" else 0
    failed = []
    base = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input"]
    try:                                            # pip itself may be missing
        if subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True, creationflags=flags).returncode != 0:
            subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"], capture_output=True, creationflags=flags)
    except Exception:
        pass
    for imp, pipname, req, _why in missing:
        if box is not None:
            try:
                lab.config(text=f"pip install {pipname} …")
                root.update()
            except Exception:
                pass
        err = ""
        for extra in ([], ["--user"]):              # a system-wide Python without write access: per-user install
            try:
                r = subprocess.run(base + extra + [pipname], capture_output=True, text=True, timeout=600, creationflags=flags)
                err = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
                err = err[0]
                if r.returncode == 0:
                    err = ""
                    break
            except Exception as exc:
                err = str(exc)
        importlib.invalidate_caches()
        if err or not present(imp):
            failed.append((pipname, req, err or "installed but not importable"))
    if box is not None:
        try:
            box.destroy()
        except Exception:
            pass
    if failed:
        text = "\n".join(f"  • {n}: {e[:140]}" for n, _r, e in failed)
        try:
            os.makedirs(folder, exist_ok=True)
            with open(skip_file, "a", encoding="utf-8") as fh:        # do not insist on optional ones that cannot be installed
                fh.write("".join(n + "\n" for n, r, _e in failed if not r))
        except OSError:
            pass
        if root is not None:
            try:
                from tkinter import messagebox as _mb
                _mb.showwarning("Instalação incompleta / Install incomplete", f"Não foi possível instalar / Could not install:\n\n{text}", parent=root)
            except Exception:
                pass
        else:
            print("Could not install:\n" + text)
        if any(r for _n, r, _e in failed):
            if root is not None:
                root.destroy()
            sys.exit(1)
    if root is not None:
        try:
            root.destroy()
        except Exception:
            pass


if os.environ.get("PLAYER_AUTOINSTALL", "").lower() != "no":
    try:
        _ensure_dependencies()
    except SystemExit:
        raise
    except Exception as _exc:                       # never block the start because of this helper
        print(f"(dependency check skipped: {_exc})")

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
import tkinter.font as tkfont

try:
    import mpv
    from screeninfo import get_monitors
except (ImportError, OSError) as exc:  # missing library
    _r = tk.Tk()
    _r.withdraw()
    messagebox.showerror(
        "Dependência em falta / Missing dependency",
        f"{exc}\n\nInstale / Install: pip install python-mpv screeninfo\n"
        "+ libmpv (mpv-2.dll / brew install mpv)",
    )
    sys.exit(1)

SHUTDOWN = getattr(mpv, "ShutdownError", SystemError)


# ---------------------------------------------------------------- diagnostics (player_logs/ next to this file)
_LOG = {"path": None, "crash": None}


def breadcrumb(msg):
    """Written to player_logs/last_calls.log BEFORE the call it describes (open / append / close): it survives a native crash."""
    path = _LOG.get("path")
    if not path:
        return
    try:
        crumbs = os.path.join(os.path.dirname(path), "last_calls.log")
        if os.path.exists(crumbs) and os.path.getsize(crumbs) > 300_000:
            os.replace(crumbs, crumbs + ".1")
        with open(crumbs, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.datetime.now():%H:%M:%S.%f}"[:-3] + f" [{threading.current_thread().name}] {msg}\n")
    except Exception:
        pass


def diag_log(msg):
    """Append a timestamped line to player_logs/player.log (rotates at ~1.5 MB). Never raises."""
    path = _LOG["path"]
    if not path:
        return
    try:
        if os.path.exists(path) and os.path.getsize(path) > 1_500_000:
            os.replace(path, path + ".1")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except Exception:
        pass


def native_monitors():
    """Windows: the monitors through EnumDisplayMonitors + GetMonitorInfoW only (no device contexts are created). screeninfo,
    polled every 2 s, is the first suspect of the GDI 'DC' objects that climbed ~1 per second until Windows' 10 000 limit."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes
    from types import SimpleNamespace

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]

    u32 = ctypes.windll.user32
    CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    u32.EnumDisplayMonitors.argtypes = [wintypes.HANDLE, ctypes.c_void_p, CB, wintypes.LPARAM]
    u32.EnumDisplayMonitors.restype = wintypes.BOOL
    u32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    u32.GetMonitorInfoW.restype = wintypes.BOOL
    found = []

    def cb(hmon, _hdc, _rc, _lp):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if u32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            found.append(SimpleNamespace(x=r.left, y=r.top, width=r.right - r.left, height=r.bottom - r.top,
                                         name=mi.szDevice, is_primary=bool(mi.dwFlags & 1), width_mm=None, height_mm=None))
        return True

    fn = CB(cb)
    u32.EnumDisplayMonitors(None, None, fn, 0)
    return found


def gdi_count():
    """Number of GDI objects of this process (Windows), -1 elsewhere."""
    if sys.platform != "win32":
        return -1
    try:
        import ctypes
        from ctypes import wintypes
        k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        u32.GetGuiResources.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        u32.GetGuiResources.restype = wintypes.DWORD
        return int(u32.GetGuiResources(k32.GetCurrentProcess(), 0))
    except Exception:
        return -1


def gdi_by_type():
    """Windows: this process' GDI objects counted by type (read from the shared GDI handle table) -> 'BITMAP:3000,DC:12,...'.
    A steady climb of one type tells what is leaking. Every read goes through ReadProcessMemory, so a wrong layout cannot crash."""
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        k32, nt = ctypes.windll.kernel32, ctypes.windll.ntdll
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        nt.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p]
        nt.NtQueryInformationProcess.restype = ctypes.c_long
        h = k32.GetCurrentProcess()
        pbi = (ctypes.c_size_t * 6)()
        if nt.NtQueryInformationProcess(h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), None) != 0 or ctypes.sizeof(ctypes.c_void_p) != 8:
            return ""
        done = ctypes.c_size_t()
        tab = ctypes.c_size_t()
        if not k32.ReadProcessMemory(h, pbi[1] + 0xF8, ctypes.byref(tab), 8, ctypes.byref(done)) or not tab.value:
            return ""
        n = 0x10000
        buf = (ctypes.c_ubyte * (n * 24))()
        if not k32.ReadProcessMemory(h, tab.value, buf, n * 24, ctypes.byref(done)):
            return ""
        raw, pid, counts = bytes(buf), os.getpid(), {}
        names = {1: "DC", 4: "REGION", 5: "BITMAP", 8: "PALETTE", 10: "FONT", 16: "BRUSH", 48: "PEN", 80: "EXTPEN"}
        for i in range(n):
            o = i * 24
            if int.from_bytes(raw[o + 8:o + 10], "little") == pid and raw[o:o + 8] != b"\0" * 8:
                t = int.from_bytes(raw[o + 14:o + 16], "little") & 0x7F
                nm = names.get(t, f"type{t}")
                counts[nm] = counts.get(nm, 0) + 1
        return ",".join(f"{k}:{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
    except Exception:
        return ""


def process_stats():
    """Memory, handle, GDI and USER object counts of this process (Windows) - a steady climb in any of them is
    what kills a long-running Tk app after hours. Optional psutil adds child processes and thread count."""
    stats = {}
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                            ("PrivateUsage", ctypes.c_size_t)]
            k32, u32, psapi = ctypes.windll.kernel32, ctypes.windll.user32, ctypes.WinDLL("psapi")
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            u32.GetGuiResources.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            u32.GetGuiResources.restype = wintypes.DWORD
            k32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]   # without it the pseudo handle (-1) overflowed and handles / GDI / USER never reached the log
            h = k32.GetCurrentProcess()
            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            if psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                stats["private_MB"] = round(pmc.PrivateUsage / 1048576, 1)
                stats["workingset_MB"] = round(pmc.WorkingSetSize / 1048576, 1)
            cnt = wintypes.DWORD()
            if k32.GetProcessHandleCount(h, ctypes.byref(cnt)):
                stats["handles"] = cnt.value
            stats["gdi_objects"] = u32.GetGuiResources(h, 0)
            stats["user_objects"] = u32.GetGuiResources(h, 1)
            if stats["gdi_objects"] > 700:
                stats["gdi_types"] = gdi_by_type()
        except Exception:
            pass
    else:
        try:
            import resource
            stats["maxrss_MB"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
        except Exception:
            pass
    try:
        import psutil
        proc = psutil.Process()
        stats["children"] = len(proc.children(recursive=True))
        stats["os_threads"] = proc.num_threads()
    except Exception:
        pass
    return stats


def _install_native_crash_reporter(folder):
    """Windows: when the process dies from an unhandled native exception (access violation in libmpv / Tk / a driver), write
    the exception code, the module + offset where it happened, the thread, and a small minidump. Unlike faulthandler it is
    called only for exceptions nobody handled, so the harmless ones raised inside libmpv's LuaJIT scripts never trigger it."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetModuleHandleExW.argtypes = [wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.HMODULE)]
        k32.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        report = os.path.join(folder, "native_crash.log")

        class REC(ctypes.Structure):
            _fields_ = [("code", wintypes.DWORD), ("flags", wintypes.DWORD), ("nested", ctypes.c_void_p),
                        ("address", ctypes.c_void_p), ("nparams", wintypes.DWORD), ("info", ctypes.c_size_t * 15)]

        class PTRS(ctypes.Structure):
            _fields_ = [("record", ctypes.POINTER(REC)), ("context", ctypes.c_void_p)]

        class MDEI(ctypes.Structure):
            _pack_ = 4
            _fields_ = [("thread", wintypes.DWORD), ("ptrs", ctypes.c_void_p), ("client", wintypes.BOOL)]

        def where(addr):
            mod = wintypes.HMODULE()
            if addr and k32.GetModuleHandleExW(0x6, addr, ctypes.byref(mod)):      # FROM_ADDRESS | UNCHANGED_REFCOUNT
                buf = ctypes.create_unicode_buffer(520)
                k32.GetModuleFileNameW(mod, buf, 520)
                return f"{buf.value} +0x{(addr - (mod.value or 0)):x}"
            return "?"

        @ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)
        def handler(ptr):
            try:
                p = ctypes.cast(ptr, ctypes.POINTER(PTRS)).contents
                r = p.record.contents
                tid = k32.GetCurrentThreadId()
                names = {t.native_id: t.name for t in list(threading._active.values())} if hasattr(threading, "_active") else {}
                lines = [f"=== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} pid {os.getpid()} NATIVE CRASH code=0x{r.code:08x} ===",
                         f"address: {where(r.address)}", f"thread: {tid} ({names.get(tid, '?')})"]
                if r.code == 0xC0000005 and r.nparams >= 2:
                    lines.append(f"access violation: {'write' if r.info[0] == 1 else 'DEP' if r.info[0] == 8 else 'read'} at 0x{r.info[1]:x}")
                with open(report, "a", encoding="utf-8") as fh:
                    fh.write("\n".join(lines) + "\n")
                try:
                    dump = os.path.join(folder, f"crash_{datetime.datetime.now():%Y%m%d_%H%M%S}.dmp")
                    fh_ = k32.CreateFileW(dump, 0x40000000, 0, None, 2, 0x80, None)
                    if fh_ and fh_ != wintypes.HANDLE(-1).value:
                        info = MDEI(tid, ptr, False)
                        mdump = ctypes.WinDLL("dbghelp").MiniDumpWriteDump
                        mdump.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
                        mdump(k32.GetCurrentProcess(), os.getpid(), fh_, 0x1000, ctypes.byref(info), None, None)
                        k32.CloseHandle(fh_)
                except Exception:
                    pass
            except Exception:
                pass
            return 0                                                                # EXCEPTION_CONTINUE_SEARCH: normal termination follows

        k32.SetUnhandledExceptionFilter(handler)
        _LOG["native_handler"] = handler                                            # keep the callback alive
    except Exception:
        pass


def setup_diagnostics(base_dir=None):
    """Crash log (native crashes: faulthandler), error log, signal / console-close log. Call once at start."""
    folder = os.path.join(base_dir or os.path.dirname(os.path.abspath(__file__)), "player_logs")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return None
    _LOG["path"] = os.path.join(folder, "player.log")
    try:
        _LOG["crash"] = open(os.path.join(folder, "crash.log"), "a", buffering=1, encoding="utf-8")
        _LOG["crash"].write(f"\n=== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} pid {os.getpid()} ===\n")
        # faulthandler's Windows handler runs on EVERY exception, also the harmless ones libmpv's LuaJIT scripts raise
        # (code 0xe24c4a02), and walks the Python stacks of all threads while they run: that dump itself crashed with an
        # access violation. Off by default; PLAYER_FAULTHANDLER=1 turns it back on for a real native crash hunt.
        if os.environ.get("PLAYER_FAULTHANDLER") == "1" or sys.platform != "win32":
            faulthandler.enable(file=_LOG["crash"], all_threads=True)   # segfault / access violation: all thread stacks
        else:
            _LOG["crash"].write("(faulthandler off: PLAYER_FAULTHANDLER=1 enables it)\n")
    except Exception:
        pass
    _install_native_crash_reporter(folder)
    diag_log(f"=== start v5.36.49 pid={os.getpid()} python={sys.version.split()[0]} platform={sys.platform} ===")
    if sys.platform == "win32" and sys.version_info >= (3, 13):
        diag_log("NOTE: Python 3.13+ uses Tcl/Tk 9; a native crash in tcl9tk90.dll was reported with it. Python 3.11 / 3.12 (Tk 8.6) is the safer choice.")
    # run state: tells the next start whether this run ended cleanly, and when it was last alive
    state = os.path.join(folder, "run.state")
    _LOG["state"] = state
    try:
        if os.path.exists(state):
            with open(state, encoding="utf-8") as fh:
                prev = fh.read().strip()
            if prev.startswith("running"):
                diag_log(f"the previous run did NOT end cleanly (crash or killed): {prev}")
    except Exception:
        pass

    def _beat():
        while True:
            try:
                with open(state, "w", encoding="utf-8") as fh:
                    fh.write(f"running pid={os.getpid()} last_alive={datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
            except Exception:
                pass
            time.sleep(15)
    threading.Thread(target=_beat, daemon=True, name="heartbeat").start()

    def excepthook(kind, value, tb):
        diag_log("UNCAUGHT EXCEPTION\n" + "".join(traceback.format_exception(kind, value, tb)))
        sys.__excepthook__(kind, value, tb)
    sys.excepthook = excepthook
    prev_thread_hook = threading.excepthook

    def thread_hook(args):
        diag_log(f"THREAD EXCEPTION in {getattr(args.thread, 'name', '?')}\n"
                 + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
        prev_thread_hook(args)
    threading.excepthook = thread_hook
    atexit.register(lambda: diag_log("atexit: process exiting normally"))
    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            prev = signal.getsignal(sig)

            def handler(num, frame, prev=prev, name=name):
                diag_log(f"signal {name} received")
                if callable(prev):
                    prev(num, frame)
            signal.signal(sig, handler)
        except Exception:
            pass
    if sys.platform == "win32":                                          # console closed / logoff / shutdown
        try:
            import ctypes
            from ctypes import wintypes
            names = {0: "CTRL_C", 1: "CTRL_BREAK", 2: "CTRL_CLOSE (console window closed)", 5: "LOGOFF", 6: "SHUTDOWN"}
            handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

            def console_handler(event):
                diag_log(f"console control event: {names.get(event, event)}")
                return False
            _LOG["console_handler"] = handler_type(console_handler)       # keep a reference
            ctypes.windll.kernel32.SetConsoleCtrlHandler(_LOG["console_handler"], True)
        except Exception:
            pass
    return folder


# monitor (small embedded preview): light decoding, never blocks on late frames
PV_OPTS = dict(profile="fast", hwdec="auto-safe", aid="no", ao="null", keep_open=True, idle=True, force_window=True,
               osc=False, osd_level=0, input_default_bindings=False, input_vo_keyboard=False,
               vd_lavc_skiploopfilter="all", image_display_duration="inf")
NDI_PV_ID = "ndipv"        # id of the temporary receiver behind the NDI import window's preview
UDP_PORTS = 8             # per live input: 0 output, 1 monitor, 2 preview, 3 NDI feed, 4-7 Multiview tiles (one port each: a port has one reader)
UDP_Q_CAP = "fifo_size=1500&overrun_nonfatal=1"     # a camera / screen feed: ~0.5 s of buffer at most (a late window drops old data, it does not fall behind)
UDP_Q = "fifo_size=8000&overrun_nonfatal=1"       # fifo_size counts 188-byte packets (8000 = 1.5 MB); 5000000 meant ~940 MB per input
def udp_q(item):
    return UDP_Q_CAP if ((item or {}).get("is_capture") or (item or {}).get("is_ndi")) else UDP_Q


PV_FAST = dict(vd_lavc_fast=True, framedrop="decoder+vo")
YT_H264_MAX = 1080      # up to this height YouTube is played as H.264 (hardware decoders); VP9 / AV1 only above it or when there is no H.264 stream
YT_DEFAULT_FORMAT = "bestvideo[height<=1080][vcodec^=avc1]+bestaudio/best[height<=1080][vcodec^=avc1]/bestvideo[vcodec!^=av01]+bestaudio/best[vcodec!^=av01]/bestvideo+bestaudio/best"   # AV1 only as a last resort: most PCs decode it in software
YT_VIDEO_HEIGHTS = [0, 2160, 1440, 1080, 720, 480, 360, 240]       # 0 = best available
YT_AUDIO_ABR = [0, 160, 128, 64]                                    # 0 = best available (kbps upper limit)


def yt_audio_format(abr=0):
    """Audio-only format string (the best audio, or the best one up to abr kbps)."""
    return f"bestaudio[abr<={int(abr)}]/bestaudio/best" if abr else "bestaudio/best"


def yt_build_format(height=0, abr=0):
    """mpv/yt-dlp format string for the chosen video height and audio bitrate limit ('best' = defaults)."""
    if not height and not abr:
        return "best"
    hf = f"[height<={int(height)}]" if height else ""
    aud = f"(bestaudio[abr<={int(abr)}]/bestaudio)" if abr else "bestaudio"
    h264 = ""
    if not height or int(height) <= YT_H264_MAX:             # H.264 first (hardware decoding); the other codecs are the fallback
        h = f"[height<={int(height) if height else YT_H264_MAX}]"
        h264 = f"bestvideo{h}[vcodec^=avc1]+{aud}/best{h}[vcodec^=avc1]/"
    return (h264 + f"bestvideo{hf}[vcodec!^=av01]+{aud}/best{hf}[vcodec!^=av01]/"
            f"bestvideo{hf}+{aud}/best{hf}/best")


def yt_prefer_h264(fmt):
    """A saved format string (made before H.264 was preferred) -> the same one with the H.264 alternative in front."""
    fmt = str(fmt or "")
    if not fmt.startswith("bestvideo") or "avc1" in fmt:
        return fmt
    m = re.search(r"height<=(\d+)", fmt)
    height = int(m.group(1)) if m else 0
    if height > YT_H264_MAX:
        return fmt
    h = f"[height<={height or YT_H264_MAX}]"
    return f"bestvideo{h}[vcodec^=avc1]+bestaudio/best{h}[vcodec^=avc1]/" + fmt


PV_YTDL_FORMAT = "bestvideo[height<=480][vcodec^=avc1]/best[height<=480][vcodec^=avc1]/bestvideo[height<=480][vcodec!^=av01]/best[height<=480][vcodec!^=av01]/bestvideo[height<=480]/best[height<=480]/best"       # the monitor is tiny: a light stream is enough


def new_pv(wid):
    """mpv for the monitor; falls back to the basic options if this mpv build rejects the speed-ups."""
    try:
        return new_mpv(wid=wid, vo="gpu", **PV_OPTS, **PV_FAST)
    except Exception:
        return new_mpv(wid=wid, vo="gpu", **PV_OPTS)


YT_BROWSERS = ["firefox", "chrome", "edge", "brave", "opera", "vivaldi", "chromium"]
YT_COOKIES = {"mode": "none", "browser": "firefox", "file": ""}      # set by the App from the saved settings


def yt_cookie_settings(raw):
    """Clean {mode, browser, file} from a saved setting."""
    out = {"mode": "none", "browser": "firefox", "file": ""}
    if isinstance(raw, dict):
        if raw.get("mode") in ("none", "browser", "file"):
            out["mode"] = raw["mode"]
        if raw.get("browser") in YT_BROWSERS:
            out["browser"] = raw["browser"]
        if isinstance(raw.get("file"), str):
            out["file"] = raw["file"][:1000]
    if out["mode"] == "file" and not out["file"]:
        out["mode"] = "none"
    return out


YT_ANON = {"on": False}      # cookies set aside: YouTube gave the signed-in account DRM-only formats (yt-dlp issue 12563)


def yt_cookie_opts():
    """(yt_dlp python options, yt-dlp command-line arguments, mpv ytdl-raw-options) for the chosen cookies."""
    c = YT_COOKIES
    if YT_ANON["on"]:
        return ({}, [], "")
    if c["mode"] == "browser":
        return ({"cookiesfrombrowser": (c["browser"],)}, ["--cookies-from-browser", c["browser"]],
                f"cookies-from-browser={c['browser']}")
    if c["mode"] == "file" and c["file"]:
        return ({"cookiefile": c["file"]}, ["--cookies", c["file"]], "cookies=%" + str(len(c["file"])) + "%" + c["file"])
    return ({}, [], "")


YT_DIRECT_PLAY = True       # YouTube items are opened from stream addresses resolved by yt-dlp.exe (not mpv's ytdl hook)
YT_COOKIE_FILE = os.path.join(os.path.expanduser("~"), ".leitor_youtube_cookies.txt")


def yt_find_browser():
    """Path of an installed Edge / Chrome / Brave (Chromium based) to sign in with, or None."""
    cands = []
    if sys.platform == "win32":
        for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                          os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                          os.path.join(base, "BraveSoftware", "Brave-Browser", "Application", "brave.exe")]
    elif sys.platform == "darwin":
        cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    else:
        for n in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"):
            p = shutil.which(n)
            if p:
                cands.append(p)
    env = os.environ.get("LEITOR_YT_BROWSER")
    if env:
        cands.insert(0, env)
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def _ws_connect(url, timeout=10):
    """Minimal RFC 6455 client socket (enough for the DevTools protocol)."""
    u = urllib.parse.urlparse(url)
    sk = socket.create_connection((u.hostname, u.port), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    sk.sendall((f"GET {u.path or '/'} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        d = sk.recv(4096)
        if not d:
            raise OSError("websocket handshake failed")
        buf += d
    if b" 101 " not in buf.split(b"\r\n", 1)[0]:
        raise OSError("websocket refused: " + buf.split(b"\r\n", 1)[0].decode("latin1"))
    return sk, buf.split(b"\r\n\r\n", 1)[1]


def _ws_send(sk, text):
    data = text.encode("utf-8")
    n = len(data)
    hdr = bytearray([0x81])
    if n < 126:
        hdr.append(0x80 | n)
    elif n < 65536:
        hdr += bytes([0x80 | 126]) + n.to_bytes(2, "big")
    else:
        hdr += bytes([0x80 | 127]) + n.to_bytes(8, "big")
    mask = os.urandom(4)
    hdr += mask
    sk.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))


def _ws_recv(sk, pre=b""):
    """Next complete text message (handles fragments, ping and close frames)."""
    buf = bytearray(pre)

    def need(n):
        while len(buf) < n:
            d = sk.recv(65536)
            if not d:
                raise OSError("websocket closed")
            buf.extend(d)

    msg = b""
    while True:
        need(2)
        op = buf[0] & 0x0F
        fin = buf[0] & 0x80
        ln = buf[1] & 0x7F
        pos = 2
        if ln == 126:
            need(4); ln = int.from_bytes(buf[2:4], "big"); pos = 4
        elif ln == 127:
            need(10); ln = int.from_bytes(buf[2:10], "big"); pos = 10
        masked = buf[1] & 0x80
        if masked:
            need(pos + 4); mk = bytes(buf[pos:pos + 4]); pos += 4
        need(pos + ln)
        payload = bytes(buf[pos:pos + ln])
        if masked:
            payload = bytes(b ^ mk[i % 4] for i, b in enumerate(payload))
        del buf[:pos + ln]
        if op == 8:
            raise OSError("websocket closed")
        if op == 9:                                            # ping -> pong
            sk.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
            continue
        if op in (1, 0, 2):
            msg += payload
            if fin:
                return msg.decode("utf-8", "replace")


def cdp_cookies(port, timeout=15):
    """All cookies of a Chromium started with --remote-debugging-port (browser-level Storage.getCookies)."""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as r:
        info = json.loads(r.read().decode("utf-8", "replace"))
    sk, rest = _ws_connect(info["webSocketDebuggerUrl"], timeout)
    try:
        sk.settimeout(timeout)
        _ws_send(sk, json.dumps({"id": 1, "method": "Storage.getCookies"}))
        while True:
            m = json.loads(_ws_recv(sk, rest))
            rest = b""
            if m.get("id") == 1:
                if "error" in m:
                    raise OSError(str(m["error"]))
                return m.get("result", {}).get("cookies", [])
    finally:
        try:
            sk.close()
        except OSError:
            pass


def cookies_to_netscape(cookies, only=("youtube.com", "google.com", "googlevideo.com", "ytimg.com")):
    """Netscape cookies.txt text (what yt-dlp reads) for the Google/YouTube cookies."""
    lines = ["# Netscape HTTP Cookie File", "# written by Leitor Multimedia (YouTube sign-in)", ""]
    far = int(time.time()) + 365 * 24 * 3600
    n = 0
    for c in cookies:
        dom = c.get("domain", "")
        bare = dom.lstrip(".")
        if not any(bare == o or bare.endswith("." + o) for o in only):
            continue
        exp = c.get("expires", -1)
        exp = int(exp) if isinstance(exp, (int, float)) and exp > 0 else far
        flag = "TRUE" if dom.startswith(".") else "FALSE"
        val = str(c.get("value", "")).replace("\t", " ").replace("\n", "").replace("\r", "")
        lines.append("\t".join([("#HttpOnly_" if c.get("httpOnly") else "") + dom, flag, c.get("path", "/") or "/",
                                "TRUE" if c.get("secure") else "FALSE", str(exp), str(c.get("name", "")), val]))
        n += 1
    return "\n".join(lines) + "\n", n


def yt_has_consent(cookies):
    """True once the YouTube/Google cookie-consent choice ('Accept all') was stored by the page."""
    names = {c.get("name") for c in cookies if "google" in c.get("domain", "") or "youtube" in c.get("domain", "")}
    return bool(names & {"SOCS", "CONSENT"})


YT_ACCOUNT_JS = """(async()=>{try{const r=await fetch('/getAccountSwitcherEndpoint',{credentials:'include'});return (await r.text()).slice(0,600000);}catch(e){return '';}})()"""


def yt_has_login(cookies):
    names = {c.get("name") for c in cookies if "google" in c.get("domain", "") or "youtube" in c.get("domain", "")}
    return bool(names & {"SID", "__Secure-1PSID", "__Secure-3PSID", "SAPISID", "LOGIN_INFO"})


ACCOUNTS_DIR = os.path.join(os.path.expanduser("~"), ".leitor_accounts")
SP_SESSION_FILE = os.path.join(ACCOUNTS_DIR, "spotify_session.json")       # the sp_dc cookie of the Spotify sign-in
SP_LOGIN_URL = "https://accounts.spotify.com/login?continue=https%3A%2F%2Fopen.spotify.com%2F"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def sp_has_login(cookies):
    return any(c.get("name") == "sp_dc" and c.get("value") and "spotify" in c.get("domain", "") for c in cookies)


def cdp_eval(port, expression, url_part="", timeout=15):
    """Runs a JavaScript expression (promises are awaited) in a page of the private browser whose address contains
    `url_part` and returns its value. The page's own cookies and origin apply, as for the user."""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=timeout) as r:
        tabs = json.loads(r.read().decode("utf-8", "replace"))
    tabs = [t for t in tabs if t.get("type") == "page" and t.get("webSocketDebuggerUrl") and url_part in (t.get("url") or "")]
    if not tabs:
        raise OSError("no page for " + url_part)
    sk, rest = _ws_connect(tabs[0]["webSocketDebuggerUrl"], timeout)
    try:
        sk.settimeout(timeout)
        _ws_send(sk, json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": expression, "awaitPromise": True, "returnByValue": True}}))
        while True:
            m = json.loads(_ws_recv(sk, rest))
            rest = b""
            if m.get("id") == 1:
                if "error" in m or m.get("result", {}).get("exceptionDetails"):
                    raise OSError(str(m.get("error") or m["result"]["exceptionDetails"])[:200])
                return m.get("result", {}).get("result", {}).get("value")
    finally:
        try:
            sk.close()
        except OSError:
            pass


def _find_all(obj, key, out, depth=0):
    if depth > 40:
        return
    if isinstance(obj, dict):
        if key in obj:
            out.append(obj)
        for v in obj.values():
            _find_all(v, key, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _find_all(v, key, out, depth + 1)


def _text_of(x):
    if isinstance(x, dict):
        if "simpleText" in x:
            return str(x["simpleText"])
        if "runs" in x:
            return "".join(str(r.get("text", "")) for r in x["runs"] if isinstance(r, dict))
    return str(x) if isinstance(x, str) else ""


def yt_parse_account(text):
    """{'name', 'handle', 'photo'} of the signed-in YouTube account from the account-switcher JSON (the selected
    account, or the first one). None when the text holds no account."""
    t = (text or "").lstrip()
    if t.startswith(")]}'"):
        t = t[4:]
    try:
        data = json.loads(t)
    except ValueError:
        return None
    found = []
    _find_all(data, "accountName", found)
    if not found:
        return None
    pick = next((a for a in found if a.get("isSelected")), found[0])
    photos = (pick.get("accountPhoto") or {}).get("thumbnails") or []
    photo = photos[-1].get("url") if photos else None
    if photo and photo.startswith("//"):
        photo = "https:" + photo
    name = clean_text(_text_of(pick.get("accountName"))).strip()
    handle = clean_text(_text_of(pick.get("channelHandle") or pick.get("accountByline"))).strip()
    if not name:
        return None
    return {"name": name, "handle": handle, "photo": photo}


def avatar_png(raw, size=64, ffmpeg=None):
    """Round avatar PNG bytes from an image of any kind (Pillow; without it the plain converted picture)."""
    try:
        from PIL import Image, ImageDraw
        import io
        im = Image.open(io.BytesIO(raw)).convert("RGBA")
        w, h = im.size
        m = min(w, h)
        im = im.crop(((w - m) // 2, (h - m) // 2, (w - m) // 2 + m, (h - m) // 2 + m)).resize((size, size), Image.LANCZOS)
        mask = Image.new("L", (size * 4, size * 4), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
        im.putalpha(mask.resize((size, size), Image.LANCZOS))
        out = io.BytesIO()
        im.save(out, "PNG")
        return out.getvalue()
    except ImportError:
        return _thumb_to_png(raw, ffmpeg)
    except Exception:
        return None


def download_bytes(url, timeout=10):
    with urlopen_public(urllib.request.Request(url, headers={"User-Agent": BROWSER_UA}), timeout=timeout) as r:
        return r.read()


SPOTIFY_AUTH = {"token": "", "exp": 0.0}


def sp_save_session(sp_dc):
    os.makedirs(ACCOUNTS_DIR, exist_ok=True)
    with open(SP_SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump({"sp_dc": sp_dc, "ts": time.time()}, f)
    try:
        os.chmod(SP_SESSION_FILE, 0o600)
    except OSError:
        pass
    SPOTIFY_AUTH.update(token="", exp=0.0)


def sp_load_session():
    try:
        with open(SP_SESSION_FILE, encoding="utf-8") as f:
            return str(json.load(f).get("sp_dc") or "")
    except (OSError, ValueError):
        return ""


def sp_clear_session():
    SPOTIFY_AUTH.update(token="", exp=0.0)
    try:
        os.remove(SP_SESSION_FILE)
    except OSError:
        pass


def sp_parse_session_html(html):
    """(access token, expiry in seconds since epoch) from the 'session' script of open.spotify.com, or (None, 0)."""
    m = re.search(r'<script[^>]*id="session"[^>]*>(.*?)</script>', html or "", re.S)
    if not m:
        return None, 0.0
    try:
        d = json.loads(m.group(1))
    except ValueError:
        return None, 0.0
    if d.get("isAnonymous") or not d.get("accessToken"):
        return None, 0.0
    return d["accessToken"], float(d.get("accessTokenExpirationTimestampMs") or 0) / 1000.0


def spotify_token(force=False):
    """Access token of the signed-in Spotify account (the web player's own token, from the saved sign-in) or None."""
    if not force and SPOTIFY_AUTH["token"] and SPOTIFY_AUTH["exp"] - time.time() > 120:
        return SPOTIFY_AUTH["token"]
    dc = sp_load_session()
    if not dc:
        return None
    try:
        req = urllib.request.Request("https://open.spotify.com/", headers={"User-Agent": BROWSER_UA, "Cookie": "sp_dc=" + dc,
                                                                           "Accept-Language": "en"})
        with urllib.request.urlopen(req, timeout=15, context=_ssl_context()) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception as exc:
        diag_log(f"spotify: token request failed ({exc})")
        return None
    tok, exp = sp_parse_session_html(html)
    if not tok:
        diag_log("spotify: the saved sign-in gave no token (expired?)")
        return None
    SPOTIFY_AUTH.update(token=tok, exp=exp or time.time() + 1800)
    return tok


def sp_api(token, path, timeout=15):
    url = path if path.startswith("http") else "https://api.spotify.com/v1/" + path.lstrip("/")
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token, "User-Agent": BROWSER_UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


SP_ACCOUNT_JS = """(async()=>{for(let i=0;i<20;i++){const b=document.querySelector('[data-testid="user-widget-link"]');
if(b){const im=b.querySelector('img');return JSON.stringify({name:(b.getAttribute('aria-label')||b.getAttribute('title')||b.textContent||'').trim(),
photo:im?(im.currentSrc||im.src):''});}await new Promise(r=>setTimeout(r,500));}return '';})()"""


def sp_parse_dom(value):
    """{'name', 'handle', 'photo', 'product'} from the user widget of the Spotify web player (JSON text) or None."""
    try:
        d = json.loads(value or "")
    except ValueError:
        return None
    name = clean_text(str(d.get("name") or "")).strip()
    if not name:
        return None
    return {"name": name, "handle": "", "photo": d.get("photo") or None, "product": ""}


def sp_account_info(token):
    """{'name', 'handle', 'photo', 'product'} of the account behind the token."""
    me = sp_api(token, "me")
    imgs = me.get("images") or []
    photo = max(imgs, key=lambda i: i.get("width") or 0).get("url") if imgs else None
    prod = str(me.get("product") or "")
    return {"name": clean_text(me.get("display_name") or me.get("id") or "").strip(), "handle": str(me.get("id") or ""),
            "photo": photo, "product": prod}


def _sp_track(t):
    if not isinstance(t, dict) or not t.get("name") or t.get("is_local"):
        return None
    ar = ", ".join(str(a.get("name")) for a in (t.get("artists") or []) if isinstance(a, dict) and a.get("name"))
    return {"title": clean_text(str(t["name"])).strip(), "artist": clean_text(ar).strip(), "dur": float(t.get("duration_ms") or 0) / 1000.0}


def _sp_pages(token, first, getter, cap=3000):
    out, url = [], first
    while url and len(out) < cap:
        d = sp_api(token, url)
        for it in d.get("items") or []:
            tr = getter(it)
            if tr:
                out.append(tr)
        url = d.get("next")
    return out


def fetch_spotify_api(kind, sid, token):
    """Same result as fetch_spotify, from the account: private playlists, 'Liked songs' and playlists of any length."""
    if kind == "liked":
        return {"kind": kind, "title": "Liked Songs", "cover": None,
                "tracks": _sp_pages(token, "me/tracks?limit=50", lambda it: _sp_track(it.get("track")))}
    if kind == "track":
        t = _sp_track(sp_api(token, "tracks/" + sid))
        if not t:
            raise ValueError("track not found")
        return {"kind": kind, "title": t["title"], "cover": None, "tracks": [t]}
    if kind == "album":
        a = sp_api(token, "albums/" + sid)
        imgs = a.get("images") or []
        tracks = [x for x in (_sp_track(t) for t in (a.get("tracks") or {}).get("items") or []) if x]
        nxt = (a.get("tracks") or {}).get("next")
        if nxt:
            tracks += _sp_pages(token, nxt, _sp_track)
        return {"kind": kind, "title": clean_text(a.get("name") or "").strip(), "cover": imgs[0].get("url") if imgs else None, "tracks": tracks}
    if kind == "playlist":
        p = sp_api(token, f"playlists/{sid}?fields=name,images")
        imgs = p.get("images") or []
        get = lambda it: _sp_track(it.get("track") or it.get("item"))        # field name changed in newer API versions
        try:
            tracks = _sp_pages(token, f"playlists/{sid}/tracks?limit=100", get)
        except urllib.error.HTTPError:
            tracks = _sp_pages(token, f"playlists/{sid}/items?limit=100", get)
        return {"kind": kind, "title": clean_text(p.get("name") or "").strip(), "cover": imgs[0].get("url") if imgs else None, "tracks": tracks}
    raise ValueError("unsupported")


def sp_my_playlists(token):
    """[(id, name, number of tracks)] of the signed-in account."""
    out, url = [], "me/playlists?limit=50"
    while url and len(out) < 500:
        d = sp_api(token, url)
        for p in d.get("items") or []:
            if isinstance(p, dict) and p.get("id"):
                n = (p.get("tracks") or p.get("items") or {}).get("total", 0)
                out.append((p["id"], clean_text(p.get("name") or "").strip(), n))
        url = d.get("next")
    return out


class YtLoginBrowser:
    """A temporary, private browser window (own profile) where the user signs in to YouTube."""
    URL = "https://accounts.google.com/ServiceLogin?service=youtube&continue=https%3A%2F%2Fwww.youtube.com%2F"

    def __init__(self, exe=None, url=None):
        self.exe = exe or yt_find_browser()
        if not self.exe:
            raise FileNotFoundError("browser")
        self.port = free_tcp_port()
        self.dir = tempfile.mkdtemp(prefix="leitor_yt_")
        args = [self.exe, f"--remote-debugging-port={self.port}", "--remote-allow-origins=*",
                f"--user-data-dir={self.dir}", "--no-first-run", "--no-default-browser-check",
                "--disable-sync", "--window-size=1000,780"]
        if os.environ.get("LEITOR_YT_BROWSER_ARGS"):
            args += os.environ["LEITOR_YT_BROWSER_ARGS"].split()
        args.append(url or self.URL)
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def alive(self):
        return self.proc.poll() is None

    def fetch(self):
        return cdp_cookies(self.port)

    def close(self):
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        except OSError:
            pass
        time.sleep(0.3)
        shutil.rmtree(self.dir, ignore_errors=True)


_YT_CACHE = {}


def yt_dlp_version():
    if "ver" in _YT_CACHE:
        return _YT_CACHE["ver"]
    _YT_CACHE["ver"] = ""
    try:
        import yt_dlp.version as v
        _YT_CACHE["ver"] = str(v.__version__)
    except Exception:
        pass
    return _YT_CACHE["ver"]


def yt_js_runtimes():
    """JavaScript runtimes yt-dlp can use to solve YouTube's challenges (needed by recent YouTube)."""
    if "rt" not in _YT_CACHE:
        _YT_CACHE["rt"] = {n: {} for n in ("deno", "node", "bun") if shutil.which(n)}
    return dict(_YT_CACHE["rt"])


def yt_extra_opts():
    """(yt_dlp python options, mpv ytdl-raw-options fragment) for YouTube's JS challenge; empty on old yt-dlp."""
    ver = yt_dlp_version()
    if not ver or ver < "2025.11.01":
        return {}, ""
    rt = yt_js_runtimes()
    opts = {"remote_components": ["ejs:github"]}
    frag = "remote-components=ejs:github"
    if rt:
        opts["js_runtimes"] = rt
        frag += ",js-runtimes=" + rt_first(rt)
    return opts, frag


def rt_first(rt):
    return next(iter(rt))


def yt_is_formats_error(text):
    t = (text or "").lower()
    return "no video formats found" in t or "requested format is not available" in t or "only images are available" in t


def yt_update_ytdlp():
    """pip install -U yt-dlp (with its default extras). (ok, last output line)"""
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-U", "yt-dlp[default]"], capture_output=True,
                           text=True, timeout=300, encoding="utf-8", errors="replace")
        _YT_CACHE.clear()
        out = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        return r.returncode == 0, (out[-1] if out else "")
    except Exception as exc:
        return False, str(exc)


def yt_is_dpapi_error(text):
    t = (text or "").lower()
    return "dpapi" in t or "failed to decrypt" in t or "could not copy" in t and "cookie" in t


def yt_is_drm_error(text):
    return "drm protected" in (text or "").lower()


def yt_is_bot_error(text):
    t = (text or "").lower()
    return "not a bot" in t or "sign in to confirm" in t or "--cookies" in t


def yt_dlp_exe():
    """yt-dlp executable that belongs to THIS Python (the one pip updates), so mpv's YouTube hook uses it too."""
    if "exe" not in _YT_CACHE:
        _YT_CACHE["exe"] = _yt_dlp_exe_find()
    return _YT_CACHE["exe"]


def _yt_dlp_exe_find():
    names = ("yt-dlp.exe", "yt-dlp") if sys.platform == "win32" else ("yt-dlp",)
    dirs = [os.path.join(os.path.dirname(sys.executable), "Scripts"), os.path.dirname(sys.executable)]
    try:
        import site
        dirs.append(os.path.join(site.getuserbase(), "Scripts"))
        dirs.append(os.path.join(site.getuserbase(), "bin"))
        dirs.append(os.path.join(site.getuserbase(), "Python%d%d" % sys.version_info[:2], "Scripts"))
    except Exception:
        pass
    for d in dirs:
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    return shutil.which("yt-dlp")


_MPV_LOGS = {"n": 0}
_MPV_LOGQ = collections.deque(maxlen=400)


def _mpv_log(level, prefix, text):
    """mpv log callback (runs on mpv's own event thread): only queues the line, no file I/O here."""
    try:
        if _MPV_LOGS["n"] < 300 and (prefix in ("ytdl_hook", "ffmpeg", "stream", "demux", "cplayer", "file") or level in ("error", "fatal")):
            _MPV_LOGS["n"] += 1
            _MPV_LOGQ.append(f"mpv[{level}/{prefix}] {str(text).strip()[:400]}")
    except Exception:
        pass


def flush_mpv_log():
    """Writes the queued mpv messages to player.log (called from the Tk main thread)."""
    try:
        while _MPV_LOGQ:
            diag_log(_MPV_LOGQ.popleft())
    except Exception:
        pass


_BC_PATCHED = {"done": False}


def _patch_mpv_breadcrumbs():
    """Wraps mpv.MPV.command / property setters so the last call before a crash is on disk (player_logs/last_calls.log)."""
    if _BC_PATCHED["done"]:
        return
    _BC_PATCHED["done"] = True
    cls = getattr(mpv, "MPV", None)
    if cls is None:
        return
    orig_cmd = getattr(cls, "command", None)
    if orig_cmd is not None:
        def command(self, name, *args, **kw):
            if name != "screenshot-raw":
                breadcrumb(f"mpv#{id(self) % 10000} command {name} {str(args)[:150]}")
            return orig_cmd(self, name, *args, **kw)
        try:
            cls.command = command
        except Exception:
            pass
    orig_set = getattr(cls, "_set_property", None)
    if orig_set is not None:
        def _set_property(self, name, value):
            breadcrumb(f"mpv#{id(self) % 10000} set {name} = {str(value)[:100]}")
            return orig_set(self, name, value)
        try:
            cls._set_property = _set_property
        except Exception:
            pass


def new_mpv(**kw):
    """mpv instance without the playback progress bar mpv otherwise draws on the Windows taskbar icon."""
    raw = yt_cookie_opts()[2]
    ex = yt_extra_opts()[1]
    if ex:
        raw = (raw + "," if raw else "") + ex
    if raw and "ytdl_raw_options" not in kw:
        kw["ytdl_raw_options"] = raw
    if "ytdl_format" not in kw:
        kw["ytdl_format"] = YT_DEFAULT_FORMAT
    if "script_opts" not in kw:
        exe = yt_dlp_exe()
        if exe:
            kw["script_opts"] = "ytdl_hook-ytdl_path=%" + str(len(exe)) + "%" + exe
    _patch_mpv_breadcrumbs()
    breadcrumb(f"new_mpv {sorted(k for k in kw)}")
    extra = []
    opt = {}
    # no mpv log callback and no log file: the file ignores msg-level (verbose, 14 MB) and the callback ran on mpv's threads
    # screenshot_sw is only set for the OUTPUT mpv (see the output start): the preview / monitor / audio instances stay plain
    extra.append(dict(opt))
    if "screenshot_sw" in opt:
        opt.pop("screenshot_sw")
        extra.append(dict(opt))
    extra.append({})
    last = None
    for ex_kw in extra:
        for base in ((dict(taskbar_progress=False),) if sys.platform == "win32" else ()) + ({},):
            try:
                return mpv.MPV(**base, **ex_kw, **kw)
            except SHUTDOWN:
                raise
            except Exception as exc:
                last = exc
    if "screenshot_sw" in kw:                       # this mpv build does not know the option: plain instance
        kw = {k: v for k, v in kw.items() if k != "screenshot_sw"}
        return mpv.MPV(**kw)
    raise last


# ---------------------------------------------------------------- NDI (optional: pip install ndi-python numpy mss)
try:
    import zeroconf as zc_mod            # optional: second, independent way to discover NDI sources (pip install zeroconf)
except Exception:
    zc_mod = None
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES, DND_TEXT      # optional: drop files / folders / links on the list
except Exception:
    TkinterDnD = DND_FILES = DND_TEXT = None
NDI_IMPORT_ERRORS = []          # why an optional library could not be imported (shown in the error dialogs)
try:
    import NDIlib as ndi
except Exception as exc:
    ndi = None
    NDI_IMPORT_ERRORS.append(f"NDIlib: {type(exc).__name__}: {exc}")
try:
    import numpy as np
except Exception as exc:
    np = None
    NDI_IMPORT_ERRORS.append(f"numpy: {type(exc).__name__}: {exc}")


def yt_info_is_live(info):
    """True for a YouTube live (or one that has not started yet), from yt-dlp's information. Anything else - a video, a finished live - has a length."""
    info = info or {}
    return info.get("is_live") is True or info.get("live_status") in ("is_live", "is_upcoming")


def is_ytlive(it):
    """YouTube live stream: handled like a live input - one ffmpeg copies it, as it is, to local UDP and every window (fullscreen,
    monitor, preview, tiles, NDI) reads that copy, so a window that opens later does not reload the stream. Only items that YouTube
    itself reported as live (`yt_live`): a video whose length is not known yet (a playlist entry) is still a video."""
    return bool(it) and bool(it.get("is_youtube")) and not it.get("yt_audio") and it.get("yt_live") is True


def is_live(it):
    """Items fed by a live input (RTMP / NDI / capture / YouTube live): no file, no seeking, shown through a UDP feed."""
    return bool(it) and bool(it.get("is_rtmp") or it.get("is_ndi") or it.get("is_countdown") or it.get("is_slideshow") or it.get("is_capture")
                             or is_ytlive(it))


def fps_fraction(fps):
    """29.97 -> (30000, 1001), 25 -> (25000, 1000) ..."""
    for n, d in ((24000, 1001), (30000, 1001), (60000, 1001)):
        if abs(fps - n / d) < 0.01:
            return n, d
    return int(round(fps * 1000)), 1000


def frame_bytes(arr):
    """Raw bytes of a (contiguous) frame buffer: copies, so the SDK can free the frame afterwards."""
    try:
        view = memoryview(arr)
        if view.c_contiguous:
            return view.tobytes()
    except TypeError:
        pass
    return np.ascontiguousarray(arr).tobytes()


def audio_bytes(frame):
    """NDI planar float audio (channels, samples) -> interleaved stereo f32le + sample rate."""
    data = frame.data
    stereo = data[:2] if data.shape[0] >= 2 else np.vstack([data[0], data[0]])
    return np.ascontiguousarray(stereo.T, dtype="<f4").tobytes(), int(frame.sample_rate)


def free_tcp_port():
    sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
    sk.close()
    return port


def make_source(name, url=None):
    """ndi.Source for a name, an 'ip[:port]' address, or a name + address (as announced over mDNS)."""
    src = ndi.Source()
    src.ndi_name = name
    if not url and re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}(:\d+)?", name or ""):
        url = name if ":" in name else name + ":5961"
    if url:
        src.url_address = url
    return src


class NdiMdns:
    """NDI sources announce themselves as mDNS services (_ndi._tcp.local.). Browsing for them directly finds the
    same sources even when the NDI finder inside ndi-python sees nothing. Optional (pip install zeroconf)."""
    TYPE = "_ndi._tcp.local."

    def __init__(self):
        self._zc = None
        self._names = set()
        self._info = {}
        self._lock = threading.Lock()
        self.error = ""

    @property
    def available(self):
        return zc_mod is not None

    def start(self):
        if zc_mod is None or self._zc is not None:
            return
        try:
            self._zc = zc_mod.Zeroconf()
            self._browser = zc_mod.ServiceBrowser(self._zc, self.TYPE, handlers=[self._on_change])
        except Exception as exc:
            self._zc = None
            self.error = f"{type(exc).__name__}: {exc}"

    def _on_change(self, zeroconf, service_type, name, state_change):
        with self._lock:
            if "Removed" in str(state_change):
                self._names.discard(name)
                self._info.pop(name, None)
            else:
                self._names.add(name)

    def sources(self):
        """[(ndi_name, 'ip:port' or '')] currently announced on the network."""
        self.start()
        if self._zc is None:
            return []
        with self._lock:
            names = sorted(self._names)
        found = []
        for name in names:
            info = self._info.get(name)
            if info is None:
                try:
                    svc = self._zc.get_service_info(self.TYPE, name, timeout=700)
                except Exception:
                    svc = None
                if svc is not None and svc.port:
                    addrs = [a for a in svc.parsed_addresses() if ":" not in a] or list(svc.parsed_addresses())
                    if addrs:
                        info = (addrs[0], svc.port)
                        self._info[name] = info
            ndi_name = name[:-len("." + self.TYPE)] if name.endswith("." + self.TYPE) else name
            found.append((ndi_name, f"{info[0]}:{info[1]}" if info else ""))
        return found


class NdiRuntime:
    """Lazy NDI runtime plus one finder shared by the whole app."""

    def __init__(self):
        self._ok = None
        self._find = None
        self._lock = threading.Lock()          # runtime init / shutdown
        self._flock = threading.Lock()         # finder (held while waiting for sources, never by the UI thread)
        self._last_names = None
        self.mdns = NdiMdns()
        self.last_finder = 0                   # sources the NDI finder itself reported on the last poll

    @property
    def available(self):
        return ndi is not None and np is not None

    def init(self):
        with self._lock:
            if self._ok is None:
                try:
                    self._ok = bool(self.available and ndi.initialize())
                except Exception:
                    self._ok = False
                if self._ok:
                    try:
                        print("[NDI] runtime", ndi.version(), file=sys.stderr)
                    except Exception:
                        pass
            return self._ok

    def sources(self, wait_ms=0):
        """[(ndi_name, Source)] visible on the network (this PC included). Polls up to wait_ms, returns as
        soon as something shows up (mDNS discovery can take a few seconds after the finder is created)."""
        if not self.init():
            return []
        with self._flock:
            if self._find is None:
                settings = ndi.FindCreate()
                try:
                    settings.show_local_sources = True       # local sources (e.g. NDI Screen Capture on this PC)
                except Exception:
                    pass
                try:
                    self._find = ndi.find_create_v2(settings)
                except Exception:
                    self._find = ndi.find_create_v2()
                if not self._find:
                    raise RuntimeError("find_create_v2 returned no finder")
            deadline = time.time() + wait_ms / 1000.0
            while True:
                left = deadline - time.time()
                # the NDI finder only refreshes its list inside find_wait_for_sources: call it on EVERY poll,
                # also the quick background ones (otherwise sources that appear later are never seen)
                ndi.find_wait_for_sources(self._find, int(max(0.05, min(left, 0.5)) * 1000))
                found = [(src.ndi_name, src) for src in (ndi.find_get_current_sources(self._find) or [])]
                self.last_finder = len(found)
                have = {n for n, _s in found}
                for name, url in self.mdns.sources():           # sources only seen over mDNS are added (with their address)
                    if name not in have:
                        found.append((name, make_source(name, url or None)))
                names = sorted(n for n, _s in found)
                if names != self._last_names:
                    self._last_names = names
                    print("[NDI] fontes:", names or "(nenhuma)", f"(finder: {self.last_finder})", file=sys.stderr)
                if found or left <= 0:
                    return found

    def diagnostics(self, tr=None):
        """One line for the import window: runtime, what the finder sees, mDNS state."""
        tr = tr or (lambda k, **kw: STR["en"][k].format(**kw))
        try:
            ver = ndi.version()
        except Exception:
            ver = "?"
        if not self.mdns.available:
            mdns = tr("ndi_diag_unavail")
        elif self.mdns.error:
            mdns = tr("ndi_diag_err", err=self.mdns.error[:40])
        else:
            mdns = tr("ndi_diag_count", n=len(self.mdns._names))
        return tr("ndi_diag_line", ver=ver, finder=self.last_finder, mdns=mdns)

    def warm_up(self):
        """Create the finder early (background) so sources are already known when the menu is opened."""
        def work():
            try:
                self.sources(wait_ms=1000)
            except Exception:
                traceback.print_exc()
        threading.Thread(target=work, daemon=True).start()

    def find(self, name):
        for found, src in self.sources():
            if found == name:
                return src
        return None

    def shutdown(self):
        with self._lock, self._flock:
            try:
                if self._find is not None:
                    ndi.find_destroy(self._find)
                if self._ok:
                    ndi.destroy()
            except Exception:
                pass
            self._find, self._ok = None, None


NDI = NdiRuntime()


class NdiHub:
    """ffmpeg that turns raw NDI video (stdin) + audio (local TCP) into MPEG-TS on UDP.

    One encode, one copy per consumer (output window, its monitor, the preview) through the tee muxer.
    """

    def __init__(self, ffmpeg, size, fps, rate, udp_ports, pix_fmt="bgra", silent=False):
        w, h = size
        self.silent = bool(silent)          # source without audio: ffmpeg makes the silence itself (no audio pipe that can stall)
        n, d = fps
        self.size, self.rate = size, rate
        fr = f"{n}/{d}"
        tee = "|".join(f"[f=mpegts:flush_packets=1]udp://127.0.0.1:{p}?pkt_size=1316" for p in udp_ports)
        aport = 0 if self.silent else free_tcp_port()
        ain = (["-f", "lavfi", "-i", f"anullsrc=r={rate}:cl=stereo"] if self.silent else
               ["-fflags", "nobuffer", "-probesize", "32", "-analyzeduration", "0", "-use_wallclock_as_timestamps", "1",
                "-f", "f32le", "-ar", str(rate), "-ac", "2", "-i", f"tcp://127.0.0.1:{aport}?listen=1"])
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error",
               "-fflags", "nobuffer", "-probesize", "32", "-analyzeduration", "0",
               "-use_wallclock_as_timestamps", "1",
               "-f", "rawvideo", "-pix_fmt", pix_fmt, "-video_size", f"{w}x{h}", "-framerate", fr, "-i", "pipe:0",
               *ain,
               "-map", "0:v", "-map", "1:a", "-muxdelay", "0", "-muxpreload", "0",
               "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-pix_fmt", "yuv420p",
               "-g", str(max(1, int(round(n / d / 2)))), "-bf", "0", "-r", fr,   # keyframe every ~0.5 s: quick join
               "-c:a", "aac", "-b:a", "160k", "-f", "tee", tee]
        flags = 0x08000000 if sys.platform == "win32" else 0         # CREATE_NO_WINDOW
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE, bufsize=0, creationflags=flags)
        self.asock = None
        self._closing = False
        self._err = collections.deque(maxlen=6)
        self.frames = 0                     # video frames delivered to ffmpeg
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        # ffmpeg only opens (listens on) the audio input after it has probed the video one, so the audio
        # side is connected in the background while video frames are already being written.
        if not self.silent:
            threading.Thread(target=self._connect_audio, args=(aport,), daemon=True).start()

    def _drain_stderr(self):
        """Keep ffmpeg's last messages (shown to the user when the feed fails) and echo them to the console."""
        buf = b""
        try:
            while True:
                chunk = self.proc.stderr.read(2048)
                if not chunk:
                    break
                buf += chunk
                *lines, buf = re.split(rb"[\r\n]+", buf)
                for raw in lines:
                    line = raw.decode("utf-8", "replace").strip()
                    if line:
                        self._err.append(line)
                        print("[ffmpeg NDI]", line, file=sys.stderr)
        except Exception:
            pass

    def error_text(self):
        return " | ".join(list(self._err)[-2:])[:160]

    def _connect_audio(self, aport):
        deadline = time.time() + 8
        while not self._closing and time.time() < deadline and self.proc.poll() is None:
            try:
                sk = socket.create_connection(("127.0.0.1", aport), timeout=0.5)
                sk.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                # ffmpeg probes this input before it starts transcoding: give it data right away so the
                # video writer (which blocks until ffmpeg reads) cannot deadlock against it
                sk.sendall(bytes(4 * 2 * int(self.rate * 0.1)))
                sk.settimeout(3.0)
                self.asock = sk
                return
            except OSError:
                time.sleep(0.05)

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def write_video(self, buf):
        t = time.perf_counter()
        self.proc.stdin.write(buf)
        self.frames += 1
        self.max_write = max(getattr(self, "max_write", 0.0), time.perf_counter() - t)

    def write_audio(self, buf):
        if self.asock is not None:                  # before the audio side is connected: dropped
            self.asock.sendall(buf)

    def close(self):
        self._closing = True
        for closer in (lambda: self.proc.stdin.close(), lambda: self.asock.close(), lambda: self.proc.kill()):
            try:
                closer()
            except Exception:
                pass
        try:
            self.proc.wait(timeout=2)
        except Exception:
            pass


class NdiReceiver:
    """Receives one NDI source and exposes it like an RTMP input (state + 3 UDP copies for mpv)."""

    def __init__(self, ffmpeg, source_name, low_bw, udp_ports, on_change):
        self.ffmpeg, self.source_name, self.low_bw = ffmpeg, source_name, bool(low_bw)
        self.udp_ports, self.on_change = list(udp_ports), on_change
        self.state, self.error, self.rejected = "waiting", "", ""   # waiting | live | error
        self.gen = 0                                  # grows each time the signal (re)appears
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _set(self, state, error=""):
        if (state, error) != (self.state, self.error):
            if state == "live" and self.state != "live":
                self.gen = getattr(self, "gen", 0) + 1
            self.state, self.error = state, error
            diag_log(f"ndi in '{self.source_name}': {state} {error}".rstrip())
            try:
                self.on_change(self)
            except Exception:
                pass

    def _run(self):
        if not NDI.init():
            self._set("error", "NDI runtime")
            return
        fails = 0
        while not self._stop.is_set():
            started = time.time()
            try:
                self._session()
                fails = 0
            except Exception as exc:
                if time.time() - started > 5:
                    fails = 0
                fails += 1
                diag_log(f"ndi in '{self.source_name}': sessão terminou ({type(exc).__name__}: {str(exc)[:160]})")
                self._set("error" if fails >= 3 else "waiting", str(exc)[:100] if fails >= 3 else "")
                self._stop.wait(min(10, 0.5 * fails))
        self._set("waiting")

    def _session(self):
        src = NDI.find(self.source_name)
        if src is None:
            src = make_source(self.source_name)
        settings = ndi.RecvCreateV3()
        settings.color_format = ndi.RECV_COLOR_FORMAT_BGRX_BGRA
        settings.bandwidth = ndi.RECV_BANDWIDTH_LOWEST if self.low_bw else ndi.RECV_BANDWIDTH_HIGHEST
        recv = ndi.recv_create_v3(settings)
        if not recv:
            raise RuntimeError("recv_create")
        ndi.recv_connect(recv, src)
        hub, rate, last_video, last_audio = None, 48000, 0.0, 0.0
        n_vid = n_aud = 0
        got_audio, t_sess = False, time.time()
        sil_t0, sil_n = None, 0
        t_log = time.time()
        diag_log(f"ndi in '{self.source_name}': ligado (low_bw={self.low_bw}, udp {self.udp_ports[:3]})")
        try:
            while not self._stop.is_set():
                kind, vid, aud, meta = ndi.recv_capture_v2(recv, 100)
                now = time.time()
                if now - t_log >= 5:
                    t_log = now
                    diag_log(f"ndi in '{self.source_name}': 5 s -> video {n_vid} audio {n_aud} | hub frames {getattr(hub, 'frames', '-')} "
                             f"ffmpeg vivo {hub.alive() if hub is not None else '-'} | escrita max {getattr(hub, 'max_write', 0) * 1000:.0f} ms | estado {self.state}")
                    if hub is not None:
                        hub.max_write = 0.0
                    n_vid = n_aud = 0
                if kind == ndi.FRAME_TYPE_VIDEO:
                    n_vid += 1
                elif kind == ndi.FRAME_TYPE_AUDIO:
                    n_aud += 1
                if kind == ndi.FRAME_TYPE_VIDEO:
                    try:
                        size = (int(vid.xres), int(vid.yres))
                        fps = (int(vid.frame_rate_N) or 30000, int(vid.frame_rate_D) or 1001)
                        buf = frame_bytes(vid.data)
                    finally:
                        ndi.recv_free_video_v2(recv, vid)
                    if hub is not None and hub.size != size:
                        hub.close()
                        hub = None
                    try:
                        if hub is not None and not hub.alive():
                            raise RuntimeError(hub.error_text() or "ffmpeg exited")
                        if hub is None and not got_audio and now - t_sess < 1.0:
                            continue                                  # first second: wait to see whether the source has audio at all
                        if hub is None:
                            hub = NdiHub(self.ffmpeg, size, fps, rate, self.udp_ports, silent=not got_audio)
                            diag_log(f"ndi in '{self.source_name}': ffmpeg {size[0]}x{size[1]} {fps[0]}/{fps[1]} fps, "
                                     f"{'sem áudio (silêncio do ffmpeg)' if hub.silent else 'com áudio'}")
                        hub.write_video(buf)
                    except (OSError, ValueError) as exc:             # ffmpeg died: say what it complained about
                        time.sleep(0.2)
                        raise RuntimeError((hub.error_text() if hub is not None else "") or str(exc))
                    last_video = now
                    if hub.frames >= 3 and hub.alive():             # healthy feed, not just a first write into the pipe
                        self._set("live")
                elif kind == ndi.FRAME_TYPE_AUDIO:
                    try:
                        data, arate = audio_bytes(aud)
                    finally:
                        ndi.recv_free_audio_v2(recv, aud)
                    last_audio = now
                    sil_t0 = None
                    if not got_audio:
                        got_audio = True
                        if hub is not None and hub.silent:            # audio showed up late: rebuild with the real audio input
                            hub.close()
                            hub = None
                    if arate != rate:                                # new sample rate: rebuild the hub
                        rate = arate
                        if hub is not None:
                            hub.close()
                            hub = None
                    elif hub is not None and not hub.silent:
                        try:
                            hub.write_audio(data)
                        except (OSError, ValueError) as exc:
                            time.sleep(0.2)
                            raise RuntimeError(hub.error_text() or str(exc))
                elif kind == ndi.FRAME_TYPE_METADATA:
                    ndi.recv_free_metadata(recv, meta)
                elif kind == ndi.FRAME_TYPE_ERROR:
                    raise RuntimeError("NDI receive error")
                if hub is not None and not hub.silent and now - last_audio > 0.25:   # source without audio: silence by the CLOCK (not per video frame:
                    if sil_t0 is None:                                 # a per-frame amount over-fills the audio pipe and builds up delay)
                        sil_t0, sil_n = now, 0
                    want = min(rate, int((now - sil_t0) * rate) - sil_n)
                    if want >= rate // 50:
                        try:
                            hub.write_audio(bytes(4 * 2 * want))
                            sil_n += want
                        except (OSError, ValueError) as exc:
                            time.sleep(0.2)
                            raise RuntimeError(hub.error_text() or str(exc))
                else:
                    sil_t0 = None
                if self.state == "live" and now - last_video > 2.0:   # source went away
                    self._set("waiting")
                    if hub is not None:
                        hub.close()
                        hub = None
        finally:
            if hub is not None:
                hub.close()
            ndi.recv_destroy(recv)


MPV_GRAB_LOCK = threading.RLock()      # the NDI sender talks to an output's mpv from its own thread: never while that mpv is destroyed


def mpv_terminate(player):
    """terminate() that cannot overlap a screenshot taken by the NDI sender thread."""
    with MPV_GRAB_LOCK:
        breadcrumb(f"mpv#{id(player) % 10000} terminate")
        player.terminate()


class _NoRawShot(Exception):
    """python-mpv cannot return a raw screenshot (too old)."""


class _FfFeed:
    """ffmpeg decoding the playing file straight to raw UYVY (NDI's native 4:2:2, no alpha) at the output's
    resolution / frame rate. Much cheaper than mpv's screenshot-raw and delivers every frame of the file."""

    def __init__(self, sender):
        self.s = sender
        self.proc = None
        self.thread = None
        self.key = None
        self.start_pos = 0.0
        self.t_start = 0.0
        self.frames = 0
        self.fps = 25.0
        self.got = False
        self.latency = 0.3
        self.eof = False
        self.dims = None
        self.err = collections.deque(maxlen=4)
        self.accept_t = 8.0
        self.noseek = False
        self.paced = False
        self.srv = None
        self.udp = False
        self.jb = collections.deque()          # jitter buffer of a live (RTMP / NDI) feed
        self._jl = threading.Lock()

    def running(self):
        return self.proc is not None and self.thread is not None and self.thread.is_alive()

    def pos(self):
        return self.start_pos + self.frames / max(1.0, self.fps)

    @staticmethod
    def probe(spec):
        """(w, h) of the picture after square-pixel correction, plus the stream's frame rate."""
        exe = spec.get("ffprobe")
        if not exe:
            return None
        try:
            ua = ["-user_agent", spec["ua"]] if spec.get("ua") else []
            res = subprocess.run([exe, "-v", "error"] + ua + ["-select_streams", "v:0", "-show_entries",
                                  "stream=width,height,sample_aspect_ratio,avg_frame_rate", "-of", "csv=p=0:s=,", spec.get("src") or spec["path"]],
                                 capture_output=True, text=True, timeout=10,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            parts = res.stdout.strip().splitlines()[0].split(",")
            w, h = int(parts[0]), int(parts[1])
            sar = 1.0
            if len(parts) > 2 and ":" in parts[2]:
                a, b = parts[2].split(":", 1)
                if float(b) > 0 and float(a) > 0:
                    sar = float(a) / float(b)
            fps = 0.0
            if len(parts) > 3 and "/" in parts[3]:
                a, b = parts[3].split("/", 1)
                fps = float(a) / float(b) if float(b) else 0.0
            return max(2, int(w * sar / 2) * 2), h - (h % 2), fps
        except Exception:
            return None

    def start(self, spec, pos):
        self.stop()
        canvas = spec.get("canvas")
        vf = spec.get("vf") or ""
        if canvas:
            w, h = canvas
            fps = spec.get("fps") or self.s.fps_value
        else:
            pr = self.probe(spec)
            if not pr:
                self.s.ff_bad.add(spec["path"])
                return False
            w, h, pfps = pr
            vf = "scale=trunc(iw*sar/2)*2:trunc(ih/2)*2,setsar=1"
            dv = int(spec.get("div") or 1)
            if dv > 1:                                        # «Original» resolution at 1/2 or 1/4 quality
                w, h = max(16, (w // dv) // 2 * 2), max(16, (h // dv) // 2 * 2)
                vf += f",scale={w}:{h}:flags=bilinear,setsar=1"
            if spec.get("fps"):
                vf += f",fps={spec['fps']:g}:round=near"
            fps = spec.get("fps") or pfps or self.s.fps_value
        self.fps, self.dims = float(fps), (w, h)
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
        except Exception as exc:
            self.s.ff_bad.add(spec["path"])
            diag_log(f"ndi {self.s.name}: no local socket for ffmpeg ({exc})")
            return False
        udp = bool(spec.get("udp"))
        var = int(spec.get("variant", 0)) if (spec.get("src") and not udp) else 0     # network streams: three ways of opening, tried in turn
        self.noseek = var >= 2
        seek = [] if (spec.get("live") or spec.get("image") or var >= 2) else ["-ss", f"{max(0.0, pos):.3f}"]
        loop = ["-loop", "1", "-framerate", f"{(spec.get('fps') or self.s.fps_value):g}"] if spec.get("image") else []
        net = bool(spec.get("src")) and not udp
        self.accept_t = 10.0 if net else 8.0                  # a network stream needs time to open, seek and decode its first frame
        netopt = ["-probesize", "3000000", "-analyzeduration", "1000000", "-reconnect", "1", "-reconnect_streamed", "1",
                  "-reconnect_delay_max", "2"] if (net and var == 0) else []
        ua = spec.get("ua") or ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
                                if net else None)
        # a network video is paced by our own reader instead of -re: with -re, ffmpeg skips to the seek point at playing speed
        # (25 s into the video = 25 s of black) when the server does not allow real seeking
        self.paced = bool(net and not spec.get("live") and not spec.get("image"))
        pipe = bool(var == 3 and spec.get("ytcmd"))
        if pipe:
            self.accept_t = 40.0                              # yt-dlp has to resolve and start downloading first
            ua, netopt = None, ["-probesize", "3000000", "-analyzeduration", "1000000"]
        lowlat = ["-fflags", "nobuffer", "-flags", "low_delay", "-probesize", "1000000", "-analyzeduration", "500000"] if udp else []
        cmd = [spec["ffmpeg"], "-v", "verbose" if net else "error", "-nostdin"] + ([] if (self.paced or udp) else ["-re"]) + (["-user_agent", ua] if ua else []) + \
              seek + loop + netopt + lowlat + ["-i", spec.get("src") or spec["path"],
               "-an", "-sn", "-vf", (vf + "," if vf else "") + "format=uyvy422", "-f", "rawvideo", "-pix_fmt", "uyvy422",
               f"tcp://127.0.0.1:{port}"]
        self.yt = None
        try:
            if pipe:
                i = cmd.index("-i")
                cmd[i + 1] = "pipe:0"
                self.yt = subprocess.Popen(spec["ytcmd"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                         stdin=self.yt.stdout if self.yt else subprocess.DEVNULL,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if self.yt:
                self.yt.stdout.close()
        except Exception as exc:
            srv.close()
            self.s.ff_bad.add(spec["path"])
            diag_log(f"ndi {self.s.name}: ffmpeg could not start ({exc})")
            return False
        self.srv = srv
        self.err = collections.deque(maxlen=8)
        threading.Thread(target=self._drain_err, args=(self.proc, self.err), daemon=True).start()
        self.start_pos, self.t_start, self.frames, self.got, self.eof = (0.0 if self.noseek else pos), time.time(), 0, False, False
        self.key = spec["key"]
        proc = self.proc
        self.udp = udp
        with self._jl:
            self.jb.clear()
        self.thread = threading.Thread(target=self._read, args=(proc, srv, w, h), daemon=True)
        self.thread.start()
        if udp:
            threading.Thread(target=self._pace, args=(proc,), daemon=True).start()
        return True

    def _pace(self, proc):
        """Live feeds arrive in bursts (network jitter): the frames wait here and are handed to the NDI sender
        one at a time, only when its queue needs one, so the output runs at an even rhythm (~0.25 s of delay)."""
        s = self.s
        ready = False
        while not s._stop.is_set() and proc is self.proc:
            time.sleep(0.004)
            with self._jl:
                n = len(self.jb)
            if not ready:
                if n < 6:
                    continue
                ready = True
            if n == 0:
                ready = False                                  # ran dry: build the buffer up again
                continue
            if len(s._queue) < 2:
                with self._jl:
                    if not self.jb:
                        continue
                    frame = self.jb.popleft()
                self.frames += 1
                with s._lock:
                    s._queue.append((frame, True))
                    s._slot = (frame, True)
                s.sending = True

    @staticmethod
    def _drain_err(proc, keep):
        try:
            for line in proc.stderr:
                keep.append(line.decode("utf-8", "replace").strip()[:200])
        except Exception:
            pass

    def _read(self, proc, srv, w, h):
        n = w * h * 2
        s = self.s
        conn = None
        try:
            t_acc = time.time() + self.accept_t
            srv.settimeout(0.25)
            while True:
                try:
                    conn, _ = srv.accept()
                    break
                except socket.timeout:
                    if proc.poll() is not None or proc is not self.proc or s._stop.is_set() or time.time() > t_acc:
                        raise                                  # ffmpeg died (403 ...) or never came: do not wait the full time
            srv.close()
            conn.settimeout(5.0)
            try:
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 23)
            except Exception:
                pass
            while not s._stop.is_set():
                buf = bytearray(n)
                view = memoryview(buf)
                got = 0
                while got < n:
                    k = conn.recv_into(view[got:], n - got)
                    if not k:
                        self.eof = True
                        return
                    got += k
                if proc is not self.proc:
                    return
                frame = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 2)
                if not self.got:
                    self.got = True
                    self.latency = min(8.0 if self.paced else 1.5, max(0.1, time.time() - self.t_start))
                    if self.paced:
                        # the picture must meet mpv's clock: wait if ffmpeg landed ahead of it
                        sp = s.spec or {}
                        try:
                            now_mpv = sp["pos"] + (0.0 if sp.get("paused") else time.time() - sp["t"])
                            lead = self.start_pos - now_mpv
                        except Exception:
                            lead = 0.0
                        if 0.02 < lead < 6.0 and not s._stop.wait(lead):
                            pass
                        self._t_first = time.time()
                if self.paced and self.noseek:
                    # opened from the start (the server does not seek): decode at full speed up to the player's position, then follow its clock
                    sp = s.spec or {}
                    try:
                        now_mpv = sp["pos"] + (0.0 if sp.get("paused") else time.time() - sp["t"])
                    except Exception:
                        now_mpv = None
                    if now_mpv is not None:
                        ftime = self.frames / self.fps
                        if ftime < now_mpv - 0.15:
                            self.frames += 1                              # still behind: this picture is skipped
                            continue
                        wait = ftime - now_mpv
                        if 0.001 < wait < 2.0:
                            time.sleep(wait)
                elif self.paced and self.frames:
                    due = self._t_first + self.frames / self.fps          # real-time cadence from the first picture
                    wait = due - time.time()
                    if 0.001 < wait < 2.0:
                        time.sleep(wait)
                if self.udp:
                    with self._jl:
                        self.jb.append(frame)
                        if len(self.jb) > 20:                  # far behind: drop the oldest, keep the delay bounded
                            for _ in range(len(self.jb) - 8):
                                self.jb.popleft()
                    continue
                self.frames += 1
                with s._lock:
                    if len(s._queue) == s._queue.maxlen:
                        s.stats["dropped"] += 1
                    s._queue.append((frame, True))
                    s._slot = (frame, True)
                s.sending = True
        except Exception:
            self.eof = True
        finally:
            for sk in (conn, srv):
                try:
                    if sk is not None:
                        sk.close()
                except Exception:
                    pass
            if not self.got and proc is self.proc:
                try:
                    diag_log(f"ndi {s.name}: ffmpeg gave no picture after {time.time() - self.t_start:.0f} s "
                             f"(exit={proc.poll()}): {' | '.join(self.err) or 'no error output'}")
                except Exception:
                    pass

    def stop(self):
        proc, self.proc = self.proc, None
        yt, self.yt = getattr(self, "yt", None), None
        if yt is not None:
            try:
                yt.kill()
            except Exception:
                pass
        srv, self.srv = getattr(self, "srv", None), None
        for sk in (srv,):
            try:
                if sk is not None:
                    sk.close()
            except Exception:
                pass
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        th, self.thread = self.thread, None
        if th is not None and th is not threading.current_thread():
            th.join(timeout=1.0)
        self.key = None


class NdiSender:
    """Sends what an output is playing as an NDI source named after the output.

    Local video files: an ffmpeg process decodes the same file to UYVY at the output's resolution / fps (every
    frame, fluid), kept in sync with the output's mpv. Anything else (or if ffmpeg fails) falls back to the frames
    of the output's own mpv (screenshot-raw "video": after the filter chain, without OSD) - NOT a screen capture.

    The frames come from the output's own mpv (screenshot-raw "video": the frame after the filter chain,
    i.e. at the output's chosen resolution / fps, without OSD) - NOT from a screen capture. While nothing
    is loaded the source sends black at the output's resolution.
    """

    def __init__(self, name, player_fn, size_fn, fps_fn):
        self.name, self.player_fn, self.size_fn, self.fps_fn = name, player_fn, size_fn, fps_fn
        self.error, self.sending = "", False
        self._stop = threading.Event()
        self._thread = None
        self._black = None
        self._last = None            # (key, frame, time) of the last real grab
        self._slot = None            # (frame, real) latest real picture
        self._queue = collections.deque(maxlen=3)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._obs_player = None
        self._prev = None
        self.hold_fn = lambda: False
        self.spec_fn = None           # main-thread callable -> dict describing the playing local file (or None)
        self.spec = None
        self.ff_bad = set()           # files ffmpeg could not deliver: they use the mpv grab instead
        self.ff_var = {}              # network stream -> which way of opening it is being tried (0, 1, 2)
        self._ff = None
        self.mode = "mpv"
        self.stats = dict(grabs=0, grab_ms=0.0, grab_max=0.0, dropped=0, sent_new=0, repeats=0)
        self.fps_value, self.size_value = 30.0, None      # Tk values, refreshed by the MAIN thread only (Tk is not thread safe)
        self.refresh()

    def refresh(self):
        """Reads the output's fps / canvas from the Tk variables. MAIN THREAD ONLY: the sender threads never touch Tk."""
        fps = None
        try:
            fps = self.fps_fn()
        except Exception:
            pass
        if not fps:                                    # "Original": the frame rate of the file being played (23.976, 25 ...)
            player = self.player_fn()
            for prop in ("container_fps", "estimated_vf_fps"):
                try:
                    v = float(getattr(player, prop) or 0) if player is not None else 0.0
                except Exception:
                    v = 0.0
                if 1.0 <= v <= 240.0:
                    fps = v
                    break
        if fps:
            self.fps_value = max(1.0, min(120.0, float(fps)))
        elif self.fps_value <= 0:
            self.fps_value = 30.0
        try:
            self.size_value = self.size_fn() or None
        except Exception:
            pass
        if self.spec_fn is not None:
            try:
                self.spec = self.spec_fn()
            except SHUTDOWN:
                self.spec = None
            except Exception:
                self.spec = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._ff is not None:
            self._ff.stop()
        if self._thread is not None:
            self._thread.join(timeout=1.5)

    _uyvy_ok = None                      # None = not tried yet, True / False after the first attempt

    def _raw(self, player):
        """mpv screenshot-raw as a dict {w, h, stride, format, data}. uyvy422 (NDI's native 4:2:2, half the bytes of
        BGRX) is tried first; mpv builds that only know bgr0/bgra/rgba make it fail once and bgr0 is used from then on."""
        fmts = ["bgr0"]            # this mpv rejects uyvy422 ("Invalid value for option screenshot-raw") and then returned no picture
        for call in ("node_command", "command"):
            fn = getattr(player, call, None)
            if fn is None:
                continue
            last = None
            for f in fmts:
                try:
                    res = fn("screenshot-raw", "video", f)     # opaque formats: the NDI output carries no transparency
                except Exception as exc:
                    last = exc
                    continue
                if isinstance(res, dict):
                    if f == "uyvy422" and self._uyvy_ok is None:
                        self._uyvy_ok = True
                        diag_log(f"ndi {self.name}: capture format uyvy422")
                    elif f == "bgr0" and self._uyvy_ok is None and "uyvy422" in fmts:
                        self._uyvy_ok = False
                        diag_log(f"ndi {self.name}: uyvy422 not supported by this mpv's screenshot-raw -> capture format bgr0")
                    return res
            if last is not None:
                raise last
        raise _NoRawShot("python-mpv has no node_command (pip install -U python-mpv)")

    def _grab(self):
        """(frame, is_real): BGRX array (h, w, 4) of the output's current picture, or black."""
        with MPV_GRAB_LOCK:
            return self._grab_locked()

    def _grab_locked(self):
        player = self.player_fn()
        if player is not None:
            try:
                key = (player.path, player.time_pos)
            except Exception:
                key = None
            last = self._last
            now = time.time()
            if key is not None and last is not None and last[0] == key and now - last[2] < 1.0:
                return last[1], True                   # same picture (paused / still image): reuse, no re-grab
            try:
                res = self._raw(player)
                if res.get("format") == "uyvy422":
                    w, h, stride = int(res["w"]), int(res["h"]), int(res["stride"])
                    arr = np.frombuffer(res["data"], dtype=np.uint8).reshape(h, stride)
                    if stride == w * 2:
                        frame = arr.reshape(h, w, 2)
                    else:
                        frame = np.ascontiguousarray(arr[:, :w * 2].reshape(h, w, 2))
                    self._last = (key, frame, now)
                    return frame, True
                if res.get("format") == "bgr0":
                    w, h, stride = int(res["w"]), int(res["h"]), int(res["stride"])
                    arr = np.frombuffer(res["data"], dtype=np.uint8).reshape(h, stride)
                    if stride == w * 4:
                        frame = arr.reshape(h, w, 4)       # no copy: the buffer is ours (a new bytes object per grab)
                    else:
                        frame = np.ascontiguousarray(arr[:, :w * 4].reshape(h, w, 4))
                    self._last = (key, frame, now)
                    return frame, True
            except _NoRawShot as exc:
                self.error = str(exc)                  # unusable python-mpv: reported to the user
                raise
            except Exception:
                pass                                   # nothing loaded (idle / between items)
        self._last = None
        return self._grab_black()

    def _fps(self):
        return self.fps_value

    def _observe(self):
        """Wakes the grabber on every new frame of the output's mpv (instead of a timer that drifts against the frames)."""
        return                                           # polled instead: mpv's observer callbacks crashed in python-mpv (node_cast_value)
        player = self.player_fn()
        if player is None or player is self._obs_player:
            return
        self.detach()
        self._obs_player = player
        try:
            self._obs_cb = lambda _n, _v: self._wake.set()
            player.observe_property("estimated-frame-number", self._obs_cb)
        except Exception:
            self._obs_cb = None

    def detach(self):
        """Stops observing the current mpv (before it is terminated)."""
        with MPV_GRAB_LOCK:
            p, cb = self._obs_player, getattr(self, "_obs_cb", None)
            self._obs_player = None
            self._obs_cb = None
            if p is not None and cb is not None:
                try:
                    p.unobserve_property("estimated-frame-number", cb)
                except Exception:
                    pass

    def _ff_failed(self, spec, ff, why):
        """ffmpeg gave nothing: a network stream gets two more ways of being opened before the mpv capture takes over."""
        path = spec["path"]
        var = self.ff_var.get(path, 0 if spec.get("live") else 2)
        tail = " | ".join(list(ff.err)[-6:])
        ff.stop()
        order = {0: 1, 1: 2} if spec.get("live") else ({2: 3} if spec.get("ytcmd") else {})        # a video: the no-seek way first (it works on any server)
        if spec.get("src") and not spec.get("udp") and var in order:
            self.ff_var[path] = order[var]
            diag_log(f"ndi {self.name}: ffmpeg {why} (way {var}) -> trying way {order[var]}. last output: {tail[:600]}")
            return True
        self.ff_bad.add(path)
        diag_log(f"ndi {self.name}: ffmpeg {why} on {path} -> mpv capture. last output: {tail[:600]}")
        return False

    def _ff_step(self):
        """ffmpeg mode. True when the picture is coming from ffmpeg (nothing to grab from mpv)."""
        spec = self.spec
        ff = self._ff
        if spec is None or not spec.get("ffmpeg") or spec["path"] in self.ff_bad:
            if ff is not None and ff.running():
                ff.stop()
            self.mode = "mpv"
            return False
        if ff is None:
            ff = self._ff = _FfFeed(self)
        self.mode = "ffmpeg"
        now = time.time()
        if spec.get("pending"):
            return True
        if spec.get("live") or spec.get("image"):                  # no seeking, no drift: just keep one decoder running
            if ff.running() and ff.key == spec["key"]:
                if not ff.got and now - ff.t_start > 10.0:
                    self.ff_bad.add(spec["path"])
                    ff.stop()
                    diag_log(f"ndi {self.name}: ffmpeg gave no picture ({ff.err and ' | '.join(ff.err)}) -> mpv capture")
                    return False
                return True
            if (not ff.running()) and ff.key == spec["key"] and ff.eof and ff.got and spec.get("image"):
                return True                                        # a picture: the last frame stays
            if now - ff.t_start > 1.0 or ff.key is None or ff.key != spec["key"]:
                ff.start(spec, 0.0)
            return True
        if spec.get("src") and not spec.get("udp"):
            spec = dict(spec, variant=self.ff_var.get(spec["path"], 0 if spec.get("live") else 2))
        want = spec["pos"] + (now - spec["t"] if not spec["paused"] else 0.0)
        if spec["paused"]:
            if ff.running():
                ff.stop()                                          # paused: the last picture just stays
            return True
        if ff.running():
            if ff.key != spec["key"] or (ff.got and not ff.noseek and now - ff.t_start > 2.0 and abs(ff.pos() - want) > 0.6):
                if now - ff.t_start > 0.8:
                    ff.start(spec, want + ff.latency)              # file / format changed or seek / loop: follow mpv
            elif not ff.got and now - ff.t_start > ((30.0 if spec.get("variant") == 3 else 12.0) if spec.get("src") else 6.0):
                return self._ff_failed(spec, ff, "no picture")
            return True
        if ff.eof and ff.got and ff.key == spec["key"] and abs(want - ff.pos()) < 1.5:
            return True                                            # end of the file: last picture stays until mpv moves on
        if ff.eof and not ff.got and ff.key == spec["key"] and ff.proc is not None:
            return self._ff_failed(spec, ff, "failed")
        if now - ff.t_start > 0.8 or ff.key is None:
            ff.start(spec, want + ff.latency)
        return True

    def _produce(self):
        """Grabs the output's picture once per displayed frame and queues it (in order, max 3 deep). The sender
        below sends them at the exact output fps. A grab slower than the frame period is the only way a frame
        can be lost; the statistics in player.log show it."""
        while not self._stop.is_set():
            with MPV_GRAB_LOCK:
                self._observe()
            period = 1.0 / self._fps()
            self._wake.wait(timeout=max(0.01, period / 2))         # polled twice per frame period
            self._wake.clear()
            if self._stop.is_set():
                return
            if self._ff_step():
                continue
            try:
                if self.hold_fn():                                 # the output is being (re)started / reconfigured: no grabbing now
                    continue
            except Exception:
                pass
            t0 = time.perf_counter()
            try:
                frame, real = self._grab()
            except _NoRawShot:
                self._stop.set()
                return
            except Exception:
                frame, real = None, False
            spent = time.perf_counter() - t0
            st = self.stats
            st["grabs"] += 1
            st["grab_ms"] += spent * 1000
            st["grab_max"] = max(st["grab_max"], spent * 1000)
            if frame is None:
                continue
            if frame is self._prev and real:
                continue                                           # the very same picture again (paused / still image)
            self._prev = frame
            with self._lock:
                if len(self._queue) == self._queue.maxlen:
                    st["dropped"] += 1                             # the sender is behind: the oldest picture is lost
                self._queue.append((frame, real))
                if real:
                    self._slot = (frame, real)

    def _run(self):
        sender = None
        timer_res = False
        try:
            if not NDI.init():
                raise RuntimeError("NDI runtime")
            if sys.platform == "win32":
                try:
                    ctypes.windll.winmm.timeBeginPeriod(1)       # 1 ms timers
                    timer_res = True
                except Exception:
                    pass
            settings = ndi.SendCreate()
            settings.ndi_name = self.name
            settings.clock_video = True                          # the NDI SDK paces the frames at the exact frame rate
            sender = ndi.send_create(settings)
            if not sender:
                raise RuntimeError("send_create")
            prod = threading.Thread(target=self._produce, daemon=True, name="ndi-grab")
            prod.start()
            send = getattr(ndi, "send_send_video_async_v2", None) or ndi.send_send_video_v2
            hold = collections.deque(maxlen=4)                   # async sends read the buffer after the call returns
            cur, last_log = None, time.time()
            while not self._stop.is_set():
                with self._lock:
                    item = self._queue.popleft() if self._queue else None
                if item is not None:
                    cur = item
                    self.stats["sent_new"] += 1
                elif cur is None or (not cur[1] and self._slot is None):
                    cur = self._grab_black()                      # nothing playing: black at the output resolution
                else:
                    self.stats["repeats"] += 1                    # no new picture yet: the last one again (steady cadence)
                frame, real = cur
                video = ndi.VideoFrameV2()
                video.data = frame
                video.FourCC = ndi.FOURCC_VIDEO_TYPE_UYVY if frame.shape[2] == 2 else ndi.FOURCC_VIDEO_TYPE_BGRX
                fps = self._fps()
                video.frame_rate_N, video.frame_rate_D = fps_fraction(fps)
                video.picture_aspect_ratio = frame.shape[1] / frame.shape[0]
                hold.append((frame, video))
                send(sender, video)
                self.sending = real
                now = time.time()
                if now - last_log >= 10:
                    st, self.stats = self.stats, dict(grabs=0, grab_ms=0.0, grab_max=0.0, dropped=0, sent_new=0, repeats=0)
                    last_log = now
                    if not self.sending:
                        diag_log(f"ndi {self.name}: a enviar PRETO (nada a reproduzir no output ou sem imagem do mpv) | modo {self.mode} | "
                                 f"grabs {st['grabs']} novos {st['sent_new']} | erro: {self.error or '-'}")
                    if (st["grabs"] or st["sent_new"]) and self.sending:
                        g = (f"grabs {st['grabs']} avg {st['grab_ms'] / st['grabs']:.1f} ms max {st['grab_max']:.0f} ms | "
                             if st["grabs"] else "")
                        diag_log(f"ndi {self.name}: {self.mode} {fps:g} fps | {g}new {st['sent_new']} repeated {st['repeats']} "
                                 f"lost {st['dropped']} (10 s)")
        except Exception as exc:
            self.error = self.error or str(exc)[:120]
        finally:
            self.sending = False
            self._stop.set()
            self._wake.set()
            if sender:
                try:
                    if getattr(ndi, "send_send_video_async_v2", None):
                        ndi.send_send_video_async_v2(sender, None)     # flush: no async frame left in flight
                except Exception:
                    pass
                try:
                    ndi.send_destroy(sender)
                except Exception:
                    pass
            if timer_res:
                try:
                    ctypes.windll.winmm.timeEndPeriod(1)
                except Exception:
                    pass

    def _grab_black(self):
        w, h = self.size_value or (1920, 1080)
        if self._black is None or self._black.shape[:2] != (h, w):
            self._black = np.zeros((h, w, 4), dtype=np.uint8)
            self._black[:, :, 3] = 255
        return self._black, False


RTMP_BASE_PORT = 1935


def sanitize_key(text):
    """RTMP key / input name: letters, digits, '_', '-', '.' only."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or "").strip()).strip("_")[:40]


def free_udp_ports(n):
    socks, ports = [], []
    try:
        for _ in range(n):
            sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sk.bind(("127.0.0.1", 0))
            socks.append(sk)
            ports.append(sk.getsockname()[1])
    finally:
        for sk in socks:
            sk.close()
    return ports


class RtmpListener:
    """ffmpeg in listen mode: RTMP in (rtmp://0.0.0.0:<port>/live/<key>) -> MPEG-TS over local UDP.

    No re-encode (-c copy). One UDP copy per consumer (output window + its monitor). ffmpeg exits when the
    publisher disconnects, so the loop re-launches it and the input is ready again (state "waiting").
    """

    def __init__(self, ffmpeg, key, port, udp_ports, on_change):
        self.ffmpeg, self.key, self.port, self.udp_ports = ffmpeg, key, port, list(udp_ports)
        self.on_change = on_change
        self.state, self.error = "waiting", ""        # waiting | live | error
        self.gen = 0
        self.rejected = ""                            # last publisher refused for using a different key
        self._proc = None
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        p = self._proc
        if p is not None:
            try:
                p.kill()
            except OSError:
                pass

    def _set(self, state, error="", rejected=None):
        rejected = self.rejected if rejected is None else rejected
        if (state, error, rejected) != (self.state, self.error, self.rejected):
            if state == "live" and self.state != "live":
                self.gen += 1
            self.state, self.error, self.rejected = state, error, rejected
            try:
                self.on_change(self)
            except Exception:
                pass

    def _cmd(self):
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "info", "-nostdin",
               "-fflags", "nobuffer", "-flags", "low_delay",
               "-analyzeduration", "1000000", "-probesize", "1000000",
               "-listen", "1", "-f", "flv", "-i", f"rtmp://0.0.0.0:{self.port}/live/{self.key}"]
        for u in self.udp_ports:
            cmd += ["-map", "0", "-c", "copy", "-f", "mpegts", "-flush_packets", "1",
                    f"udp://127.0.0.1:{u}?pkt_size=1316"]
        return cmd

    def _run(self):
        fails = 0
        flags = 0x08000000 if sys.platform == "win32" else 0        # CREATE_NO_WINDOW
        while not self._stop.is_set():
            started, was_live, refused = time.time(), False, False
            tail = collections.deque(maxlen=6)
            try:
                self._proc = subprocess.Popen(self._cmd(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.PIPE, bufsize=0, creationflags=flags)
            except OSError as exc:
                self._set("error", str(exc))
                if self._stop.wait(3):
                    break
                continue
            proc, buf = self._proc, b""
            while True:
                chunk = proc.stderr.read(4096)          # unbuffered: returns what is available
                if not chunk:
                    break
                buf += chunk
                *lines, buf = re.split(rb"[\r\n]+", buf)
                for raw in lines:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    tail.append(line)
                    # ffmpeg only WARNS when the publisher uses another key, it still accepts the stream:
                    # the key is enforced here by cutting that connection at once (before any data flows)
                    m = re.search(r"Unexpected stream (\S+), expecting", line)
                    if m and not refused:
                        refused = True
                        self._set("waiting", "", m.group(1)[:60])
                        try:
                            proc.kill()
                        except OSError:
                            pass
                    elif not refused and not was_live and (line.startswith("Input #0") or line.startswith("frame=")):
                        was_live = True
                        self._set("live", "", "")
            rc = proc.wait()
            self._proc = None
            if self._stop.is_set():
                break
            if refused or was_live or rc == 0 or time.time() - started > 3:
                fails = 0
                self._set("waiting")
                self._stop.wait(0.2)
            else:                                        # could not even start (port busy, bad ffmpeg...)
                fails += 1
                msg = next((l for l in reversed(tail) if "rror" in l or "in use" in l or "ailed" in l),
                           tail[-1] if tail else "ffmpeg")
                if fails >= 3:                           # first retries are silent (port still being released)
                    self._set("error", msg[:120])
                self._stop.wait(0.6 if fails < 3 else min(10, fails))
        self._proc = None


# ---- Capture inputs: cameras / capture cards / microphones (DirectShow), screen and window (GDI) through ffmpeg ------------
CAP_KINDS = ("video", "audio", "screen", "window", "lavfi")      # lavfi: a synthetic source, used by the tests only
_NOWIN = 0x08000000 if sys.platform == "win32" else 0            # CREATE_NO_WINDOW
_ABOVE_NORMAL = 0x00008000 if sys.platform == "win32" else 0     # ABOVE_NORMAL_PRIORITY_CLASS


def dshow_parse_devices(text):
    """`ffmpeg -list_devices true -f dshow -i dummy` (stderr) -> [(name, 'video'|'audio')], in order, without duplicates."""
    found, seen = [], set()
    for line in str(text).splitlines():
        m = re.search(r'"(.+?)"\s+\((video|audio)(?:,[^)]*)?\)\s*$', line.strip())
        if m and (m.group(1), m.group(2)) not in seen:
            seen.add((m.group(1), m.group(2)))
            found.append((m.group(1), m.group(2)))
    return found


def dshow_parse_video_modes(text):
    """`-list_options true -f dshow -i video=NAME` -> [(size 'WxH', max_fps)], biggest first, each size once."""
    best = {}
    for m in re.finditer(r"min s=(\d+)x(\d+) fps=([\d.]+) max s=(\d+)x(\d+) fps=([\d.]+)", str(text)):
        size, fps = f"{m.group(4)}x{m.group(5)}", float(m.group(6))
        best[size] = max(best.get(size, 0.0), fps)
    return sorted(best.items(), key=lambda kv: -int(kv[0].split("x")[0]) * int(kv[0].split("x")[1]))


def dshow_parse_modes(text):
    """`-list_options true -f dshow -i video=NAME` -> the modes the camera really offers:
    [{'size': 'WxH', 'fmt': 'p:yuyv422' | 'c:mjpeg' | '', 'fmin': 5.0, 'fmax': 30.0}], biggest size first, no duplicates."""
    out, seen = [], set()
    for m in re.finditer(r"(?:(pixel_format|vcodec)=(\S+)\s+)?min s=(\d+)x(\d+) fps=([\d.]+) max s=(\d+)x(\d+) fps=([\d.]+)", str(text)):
        fmt = ("p:" if m.group(1) == "pixel_format" else "c:") + m.group(2) if m.group(1) else ""
        size, fmin, fmax = f"{m.group(6)}x{m.group(7)}", float(m.group(5)), float(m.group(8))
        if (size, fmt, fmin, fmax) in seen:
            continue
        seen.add((size, fmt, fmin, fmax))
        out.append({"size": size, "fmt": fmt, "fmin": fmin, "fmax": fmax})
    out.sort(key=lambda d: (-int(d["size"].split("x")[0]) * int(d["size"].split("x")[1]), -d["fmax"], d["fmt"]))
    return out


def dshow_mode_label(m):
    fmt = m["fmt"].split(":", 1)[1] if m["fmt"] else ""
    rng = f"{m['fmax']:g}" if m["fmin"] == m["fmax"] else f"{m['fmin']:g}–{m['fmax']:g}"
    return "  ·  ".join(x for x in (m["size"], fmt, rng + " fps") if x)


def dshow_mode_fps(m):
    """Frame rates worth offering for a mode (all inside the range the camera reports)."""
    std = [60, 50, 30, 29.97, 25, 24, 23.976, 15, 10, 5]
    vals = [v for v in std if m["fmin"] - 0.01 <= v <= m["fmax"] + 0.01]
    if m["fmax"] not in vals:
        vals.insert(0, m["fmax"])
    return sorted(set(vals), reverse=True)


def dshow_parse_audio_modes(text):
    """`-list_options true -f dshow -i audio=NAME` -> summary {'ch': (min, max), 'bits': (min, max), 'rate': (min, max)} or None."""
    rows = re.findall(r"min ch=\s*(\d+) bits=\s*(\d+) rate=\s*(\d+) max ch=\s*(\d+) bits=\s*(\d+) rate=\s*(\d+)", str(text))
    if not rows:
        return None
    cols = list(zip(*[tuple(int(v) for v in r) for r in rows]))
    return {"ch": (min(cols[0]), max(cols[3])), "bits": (min(cols[1]), max(cols[4])), "rate": (min(cols[2]), max(cols[5]))}


def _ff_stderr(args, timeout=12):
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=_NOWIN, stdin=subprocess.DEVNULL)
        return (r.stderr or b"").decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return ""


def dshow_devices(ffmpeg):
    return dshow_parse_devices(_ff_stderr([ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]))


def dshow_options(ffmpeg, kind, name):
    return _ff_stderr([ffmpeg, "-hide_banner", "-list_options", "true", "-f", "dshow", "-i", f"{kind}={name}"])


def list_windows():
    """Titles of the visible top-level windows (Windows only): [(hwnd, title)]."""
    if sys.platform != "win32":
        return []
    out = []
    try:
        user32 = ctypes.windll.user32
        proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def cb(hwnd, _lp):
            if user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                if n > 0:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, buf, n + 1)
                    if buf.value.strip():
                        out.append((int(hwnd), buf.value))
            return True
        user32.EnumWindows(proto(cb), 0)
    except Exception:
        return []
    return out


def cap_clean(raw):
    """A capture description read from a show file or a dialog: only known keys, safe types."""
    if not isinstance(raw, dict) or raw.get("kind") not in CAP_KINDS:
        return None
    c = {"kind": raw["kind"]}
    for key, limit in (("device", 300), ("title", 300), ("size", 20), ("fps", 8)):
        v = raw.get(key)
        c[key] = str(v)[:limit] if isinstance(v, (str, int, float)) and not isinstance(v, bool) else ""
    for key in ("x", "y", "w", "h"):
        v = raw.get(key)
        c[key] = int(v) if isinstance(v, int) and not isinstance(v, bool) and -32768 <= v <= 32768 else None
    vf = raw.get("vfmt")
    c["vfmt"] = vf if isinstance(vf, str) and re.fullmatch(r"[pc]:[a-z0-9_]{1,24}", vf) else ""      # camera format: p:yuyv422 / c:mjpeg
    c["mouse"] = raw.get("mouse") is not False                       # the mouse pointer is shown unless switched off
    return c


def cap_fps_norm(text):
    """What the user typed in an FPS box -> '' (auto), '29.97', ... or None when it is not a usable number (1-240)."""
    v = str(text or "").strip().replace(",", ".")
    if not v or v.lower() in ("auto", "automático", "automatico"):
        return ""
    try:
        f = float(v)
    except ValueError:
        return None
    if not (1 <= f <= 240) or f != f:
        return None
    return f"{f:.3f}".rstrip("0").rstrip(".")


def cap_label(cap):
    k = cap.get("kind")
    if k == "window":
        return cap.get("title") or "window"
    if k == "screen":
        return cap.get("device") or "screen"
    return cap.get("device") or k


def cap_input_args(cap):
    """ffmpeg input options for a capture; returns (args, has_video, has_audio)."""
    k = cap["kind"]
    fps = str(cap.get("fps") or "")
    if k == "video":
        a = ["-f", "dshow", "-rtbufsize", "8M", "-probesize", "131072", "-analyzeduration", "0"]
        vf = cap.get("vfmt") or ""
        if vf:
            a += ["-pixel_format" if vf[0] == "p" else "-vcodec", vf[2:]]
        if cap.get("size"):
            a += ["-video_size", cap["size"]]
        if fps:
            a += ["-framerate", fps]
        return a + ["-i", "video=" + cap["device"]], True, False
    if k == "audio":
        return ["-f", "dshow", "-audio_buffer_size", "20", "-probesize", "32768", "-analyzeduration", "0",
                "-i", "audio=" + cap["device"]], False, True
    if k in ("screen", "window"):
        a = ["-f", "gdigrab", "-probesize", "32768", "-analyzeduration", "0", "-framerate", fps or "30",
             "-draw_mouse", "0" if cap.get("mouse") is False else "1"]
        if k == "screen":
            if cap.get("w") and cap.get("h"):
                a += ["-offset_x", str(cap.get("x") or 0), "-offset_y", str(cap.get("y") or 0),
                      "-video_size", f"{cap['w']}x{cap['h']}"]
            return a + ["-i", "desktop"], True, False
        return a + ["-i", "title=" + cap["title"]], True, False
    audio = cap.get("size") == "audio"                                         # lavfi (tests): size "audio" = sound only
    return ["-f", "lavfi", "-i", cap["device"]], not audio, audio


# ---- Output Composto: slices (crop / rotation / mirror / corner pin / edge blend) as an mpv filter graph -------------------
CO_PRESETS = ((1920, 1080), (3840, 2160), (2560, 1440), (1280, 720), (3840, 1080), (1920, 2160), (5760, 1080))
CO_MAX = 16384
CO_ROT = {90: "transpose=1", 180: "hflip,vflip", 270: "transpose=2"}


def co_arrange(n, mode, w=1920, h=1080):
    """Several outputs side by side as one canvas -> (canvas_w, canvas_h, [(x, y, w, h) as fractions of the canvas])."""
    n = max(1, int(n))
    if mode == "v":
        cols, rows = 1, n
    elif mode == "h":
        cols, rows = n, 1
    else:
        cols = int(math.ceil(math.sqrt(n)))
        rows = int(math.ceil(n / cols))
    cells = []
    for i in range(n):
        r, c = divmod(i, cols)
        cells.append((c / cols, r / rows, 1.0 / cols, 1.0 / rows))
    return cols * w, rows * h, cells


def co_needs_vf(t):
    """True when a slice needs an mpv filter graph at all (otherwise the plain, letterboxed picture is shown)."""
    return bool(any(t.get("crop") or (0, 0, 0, 0)) or t.get("rot") or t.get("fx") or t.get("fy") or t.get("pin")
                or any(t.get("blend") or (0, 0, 0, 0)) or t.get("aspect") in ("stretch", "fill"))


def co_slice_graph(t, bw, bh):
    """lavfi graph (one unconnected input, one output) for slice `t` drawn in a tile of bw x bh pixels. "" = nothing to do."""
    if not co_needs_vf(t):
        return ""
    bw, bh = max(16, int(bw) // 2 * 2), max(16, int(bh) // 2 * 2)
    l, tp, r, b = [min(0.9, max(0.0, float(v))) for v in (t.get("crop") or (0, 0, 0, 0))]
    if l + r > 0.9:
        r = 0.9 - l
    if tp + b > 0.9:
        b = 0.9 - tp
    chain = []
    if l or tp or r or b:
        chain.append(f"crop=iw*{1 - l - r:.5f}:ih*{1 - tp - b:.5f}:iw*{l:.5f}:ih*{tp:.5f}")
    if t.get("rot") in CO_ROT:
        chain.append(CO_ROT[t["rot"]])
    if t.get("fx"):
        chain.append("hflip")
    if t.get("fy"):
        chain.append("vflip")
    asp = t.get("aspect") or "fit"
    if asp == "stretch":
        chain.append(f"scale={bw}:{bh}")
    elif asp == "fill":
        chain.append(f"scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh}")
    else:
        chain.append(f"scale={bw}:{bh}:force_original_aspect_ratio=decrease,pad={bw}:{bh}:(ow-iw)/2:(oh-ih)/2:black")
    chain.append("setsar=1")
    pin = t.get("pin")
    bl = [min(0.5, max(0.0, float(v))) for v in (t.get("blend") or (0, 0, 0, 0))]
    persp = ""
    if pin:
        p = [float(v) for v in pin]
        xs = [p[0] * bw, p[2] * bw, p[4] * bw, p[6] * bw]
        ys = [p[1] * bh, p[3] * bh, p[5] * bh, p[7] * bh]
        persp = "perspective=" + ":".join(f"x{k}={xs[k]:.2f}:y{k}={ys[k]:.2f}" for k in range(4)) + ":sense=destination:interpolation=linear"
    if not pin and not any(bl):
        return ",".join(chain)
    chain.append("format=rgba")
    if persp:
        chain.append(persp)

    def ramp(coord, size):
        return "1" if size < 1 else f"(1-cos(PI*clip({coord}/{size:.2f},0,1)))/2"
    expr = "*".join([ramp("X", bl[0] * bw), ramp("(W-1-X)", bl[2] * bw), ramp("Y", bl[1] * bh), ramp("(H-1-Y)", bl[3] * bh)])
    if pin:
        expr += "*gt(X,0)*lt(X,W-1)*gt(Y,0)*lt(Y,H-1)"
    mask = f"color=c=white:s={bw}x{bh}:r=1:d=1,format=gray,geq=lum='255*{expr}'"
    if persp:
        mask += "," + persp
    return f"{','.join(chain)}[p];{mask}[m];[p][m]alphamerge"


def co_vf_spec(graph):
    """mpv `vf set` value for a lavfi graph ('' clears the filters)."""
    return f"lavfi=graph=%{len(graph.encode('utf-8'))}%{graph}" if graph else ""


# ---- Audio routing (Dante Controller style): source channels x channels of the output's audio device ---------------------
PA_MAX_CH = 64
AR_COUNTS = (2, 4, 6, 8, 12, 16, 24, 32)               # channels of the device
AR_SRC_LAYOUTS = {2: ("stereo", ("L", "R")), 4: ("quad", ("FL", "FR", "BL", "BR")),
                  6: ("5.1", ("FL", "FR", "FC", "LFE", "BL", "BR")), 8: ("7.1", ("FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR"))}
AR_API_NAMES = {"auto": "Default", "wasapi": "WASAPI", "dsound": "DirectSound", "winmm": "MME (WinMM)", "asio": "ASIO",
                "wdmks": "WDM-KS", "coreaudio": "CoreAudio", "pulse": "PulseAudio", "pipewire": "PipeWire", "alsa": "ALSA",
                "jack": "JACK", "oss": "OSS", "sdl": "SDL", "openal": "OpenAL", "sndio": "sndio"}
AR_WIN_APIS = ("winmm", "dsound", "wasapi", "wdmks", "asio")      # the systems a Windows user expects to see


def ar_default(n_src=2, n_dst=2):
    return {"dev": "auto", "dev_label": "", "n_src": n_src, "n_dst": n_dst, "map": [[k, k] for k in range(min(n_src, n_dst))]}


def ar_clean(raw):
    """Routing read from a show file: only valid values."""
    ar = ar_default()
    if not isinstance(raw, dict):
        return ar
    if isinstance(raw.get("dev"), str) and raw["dev"].strip():
        ar["dev"] = raw["dev"][:300]
    if isinstance(raw.get("dev_label"), str):
        ar["dev_label"] = raw["dev_label"][:200]
    ns, nd = raw.get("n_src"), raw.get("n_dst")
    ar["n_src"] = ns if ns in AR_SRC_LAYOUTS else 2
    ar["n_dst"] = nd if (nd in AR_COUNTS or (ar["dev"].startswith("pa|") and isinstance(nd, int) and 1 <= nd <= PA_MAX_CH)) else 2
    m = raw.get("map")
    if isinstance(m, list):
        pairs = []
        for p in m:
            if isinstance(p, list) and len(p) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in p) \
                    and 0 <= p[0] < ar["n_dst"] and 0 <= p[1] < ar["n_src"] and p not in pairs:
                pairs.append(p)
        ar["map"] = pairs
    else:
        ar["map"] = [[k, k] for k in range(min(ar["n_src"], ar["n_dst"]))]
    return ar


def ar_is_default(ar):
    return (ar["dev"] == "auto" and ar["n_src"] == 2 and ar["n_dst"] == 2
            and sorted(map(tuple, ar["map"])) == [(0, 0), (1, 1)])


def ar_dst_layout(n):
    return AR_SRC_LAYOUTS[n][0] if n in AR_SRC_LAYOUTS else f"{n}c"


def ar_graph(ar):
    """lavfi graph: the source (forced to a known layout) -> one channel per device channel, mixed as the matrix says."""
    ns, nd = ar["n_src"], ar["n_dst"]
    parts = []
    for d in range(nd):
        srcs = sorted(s for (dd, s) in map(tuple, ar["map"]) if dd == d and s < ns)
        if not srcs:
            parts.append(f"c{d}=0*c0")
        elif len(srcs) == 1:
            parts.append(f"c{d}=c{srcs[0]}")
        else:
            parts.append(f"c{d}<" + "+".join(f"c{s}" for s in srcs))        # '<': the sum is renormalised (no clipping)
    return f"aformat=channel_layouts={AR_SRC_LAYOUTS[ns][0]},pan={ar_dst_layout(nd)}|" + "|".join(parts)


def ar_af_spec(ar):
    if ar_is_default(ar):
        return ""
    g = ar_graph(ar)
    return f"lavfi=graph=%{len(g.encode('utf-8'))}%{g}"


def ar_group_devices(devs):
    """[(name, description)] from mpv -> [(api_key, api_label, [(name, description)])] in a stable order."""
    groups, labels = {}, {}
    for name, desc in devs:
        if name.startswith("pa|"):                          # PortAudio: "pa|<host api>|<device>"
            api = "pa|" + name.split("|")[1]
            labels[api] = f"{name.split('|')[1]} (PortAudio)"
        else:
            api = name.split("/", 1)[0] if "/" in name else name
        groups.setdefault(api, []).append((name, desc))
    mpv_first = ["auto"] + [a for a in AR_WIN_APIS if a in groups] + sorted(a for a in groups if a not in AR_WIN_APIS and a != "auto" and not a.startswith("pa|"))
    pa_apis = sorted((a for a in groups if a.startswith("pa|")), key=lambda a: (("ASIO" in a) * -1, a))
    return [(a, labels.get(a) or AR_API_NAMES.get(a, a), groups[a]) for a in mpv_first + pa_apis if a in groups]


# ---- PortAudio engine (ASIO, DirectSound, MME, WDM-KS, WASAPI): ffmpeg decodes the output's audio, sounddevice plays it ---------
try:
    import sounddevice as sd_mod          # optional: pip install sounddevice (its Windows wheel carries PortAudio with ASIO)
except Exception:
    sd_mod = None


def pa_dev_name(api, name):
    return f"pa|{api}|{name}"


def pa_devices():
    """Output devices of PortAudio: [(host_api, name, max_channels, default_samplerate)]. [] without sounddevice."""
    if sd_mod is None:
        return []
    try:
        apis = [a["name"] for a in sd_mod.query_hostapis()]
        res = []
        for d in sd_mod.query_devices():
            if d.get("max_output_channels", 0) > 0:
                res.append((apis[d["hostapi"]], d["name"], int(d["max_output_channels"]), int(d.get("default_samplerate") or 48000)))
        return res
    except Exception as exc:
        diag_log(f"portaudio: device list failed ({exc})")
        return []


def pa_find(dev):
    """-> (index, max_channels, samplerate) of a stored "pa|api|name" device, or None."""
    if sd_mod is None or not dev.startswith("pa|"):
        return None
    try:
        _p, api, name = dev.split("|", 2)
        apis = [a["name"] for a in sd_mod.query_hostapis()]
        for i, d in enumerate(sd_mod.query_devices()):
            if d.get("max_output_channels", 0) > 0 and d["name"] == name and apis[d["hostapi"]] == api:
                return i, int(d["max_output_channels"]), int(d.get("default_samplerate") or 48000)
    except Exception as exc:
        diag_log(f"portaudio: find device failed ({exc})")
    return None


def pa_matrix(ar):
    """ns x nd gains: several source channels into one device channel are averaged (as the mpv routing does)."""
    ns, nd = ar["n_src"], ar["n_dst"]
    m = [[0.0] * nd for _ in range(ns)]
    for d in range(nd):
        srcs = sorted({s for (dd, s) in map(tuple, ar["map"]) if dd == d and s < ns})
        for s in srcs:
            m[s][d] = 1.0 / len(srcs)
    return m


def pa_ffmpeg_cmd(ff, src, pos, ns, sr, live=False):
    cmd = [ff, "-v", "error", "-nostdin"]
    if src.startswith(("http://", "https://")):
        cmd += ["-user_agent", BROWSER_UA, "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "2"]
    if pos > 0.05 and not live:
        cmd += ["-ss", f"{pos:.3f}"]
    return cmd + ["-i", src, "-vn", "-sn", "-dn", "-f", "f32le", "-ac", str(ns), "-ar", str(sr), "pipe:1"]


class PaRouter:
    """Plays what an output plays through a PortAudio device with a channel matrix. mpv stays the clock (it keeps the video):
    this ffmpeg is started at mpv's position and restarted when the two drift apart."""

    TOL = 0.3                   # seconds of drift tolerated before the audio is restarted at mpv's position

    def __init__(self, ffmpeg, dev_index, sr, ar, name="", stream_factory=None):
        import numpy as np
        self.np = np
        self.ff, self.dev, self.sr, self.name = ffmpeg, dev_index, sr, name
        self.ns, self.nd = ar["n_src"], ar["n_dst"]
        self.fb = self.ns * 4
        self.M = np.array(pa_matrix(ar), dtype=np.float32).reshape(self.ns, self.nd)
        self.lock = threading.Lock()
        self.src, self.live, self.pos, self.t_upd, self.paused, self.gain = None, False, 0.0, time.time(), False, 1.0
        self.buf = collections.deque()
        self.buf_bytes = 0
        self.proc, self.gen, self.cur_src = None, 0, None
        self.start_pos, self.frames, self.t_start = 0.0, 0, 0.0
        self.startup, self.got_first = 0.35, False
        self.latency = 0.0
        self.err, self.restarts = "", 0
        self.stop_flag = False
        self.stream = None
        factory = stream_factory or self._real_stream
        self.stream = factory(self._cb)
        try:
            self.latency = float(getattr(self.stream, "latency", 0) or 0)
        except Exception:
            self.latency = 0.0
        self.stream.start()
        self.th = threading.Thread(target=self._loop, daemon=True)
        self.th.start()
        _PA_ROUTERS.append(self)

    def _real_stream(self, cb):
        return sd_mod.RawOutputStream(samplerate=self.sr, device=self.dev, channels=self.nd, dtype="float32",
                                      latency="low", callback=cb)

    # -- fed by the app (main thread, ~4x per second)
    def update(self, src, pos, paused, gain, live=False):
        with self.lock:
            self.src, self.live, self.pos, self.t_upd, self.paused, self.gain = src, live, pos, time.time(), paused, gain

    # -- audio callback (PortAudio's thread)
    def _cb(self, outdata, frames, _t, _status):
        need = frames * self.fb
        chunks, got = [], 0
        with self.lock:
            if self.src is not None and not self.paused:
                while got < need and self.buf:
                    c = self.buf[0]
                    take = need - got
                    if len(c) <= take:
                        chunks.append(c)
                        got += len(c)
                        self.buf.popleft()
                    else:
                        chunks.append(c[:take])
                        self.buf[0] = c[take:]
                        got += take
                    self.buf_bytes -= chunks[-1].__len__()
                self.frames += got // self.fb
            gain = self.gain
        if got == 0:
            outdata[:] = bytes(len(outdata))
            return
        raw = b"".join(chunks) + (bytes(need - got) if got < need else b"")
        np = self.np
        y = np.frombuffer(raw, dtype=np.float32).reshape(frames, self.ns) @ self.M
        y *= gain
        np.clip(y, -1.0, 1.0, out=y)
        outdata[:] = y.astype(np.float32).tobytes()

    # -- ffmpeg
    def _kill(self):
        p, self.proc = self.proc, None
        self.gen += 1
        if p is not None:
            try:
                p.kill()
            except Exception:
                pass
            for f in (p.stdout,):
                try:
                    f.close()
                except Exception:
                    pass
        with self.lock:
            self.buf.clear()
            self.buf_bytes = 0

    def _start(self, target, live):
        self._kill()
        self.restarts += 1
        pos = max(0.0, target + self.latency + self.startup)
        cmd = pa_ffmpeg_cmd(self.ff, self.src, pos, self.ns, self.sr, live)
        flags = 0x08000000 if sys.platform == "win32" else 0
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, creationflags=flags)
        except Exception as exc:
            self.err = str(exc)
            self.proc = None
            return
        self.cur_src, self.start_pos, self.frames, self.t_start, self.got_first = self.src, pos, 0, time.time(), False
        threading.Thread(target=self._read, args=(self.proc, self.gen), daemon=True).start()

    def _read(self, proc, gen):
        cap = int(self.sr * self.fb * 0.8)             # ~0.8 s of audio is buffered, ffmpeg waits on the pipe for the rest
        carry = b""
        try:
            while gen == self.gen:
                if self.buf_bytes > cap:
                    time.sleep(0.01)
                    continue
                data = proc.stdout.read1(self.fb * 2048)
                if not data:
                    break
                if not self.got_first:
                    self.got_first = True
                    self.startup = min(1.5, max(0.1, 0.5 * self.startup + 0.5 * (time.time() - self.t_start)))
                data = carry + data
                n = len(data) - len(data) % self.fb
                carry = data[n:]
                if n and gen == self.gen:
                    with self.lock:
                        self.buf.append(data[:n])
                        self.buf_bytes += n
        except Exception:
            pass

    def _loop(self):
        while not self.stop_flag:
            time.sleep(0.05)
            try:
                self._steer()
            except Exception as exc:
                self.err = str(exc)

    def _steer(self):
        with self.lock:
            src, live, pos, tu, paused = self.src, self.live, self.pos, self.t_upd, self.paused
        if src is None:
            if self.proc is not None:
                self._kill()
            self.cur_src = None
            return
        target = pos + (0.0 if paused else time.time() - tu)
        if src != self.cur_src:
            self._start(target, live)
            return
        if paused or live:
            if self.proc is not None and self.proc.poll() is not None and not self.buf_bytes and live and time.time() - self.t_start > 2:
                self._start(target, live)                  # a live stream that ended: reconnect
            return
        played = self.start_pos + self.frames / self.sr - self.latency
        if time.time() - self.t_start > 1.5 and abs(played - target) > self.TOL:
            if time.time() - self.t_start > 2.0:
                self._start(target, live)

    def close(self):
        self.stop_flag = True
        self._kill()
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass
        if self in _PA_ROUTERS:
            _PA_ROUTERS.remove(self)


_PA_ROUTERS = []
atexit.register(lambda: [r.close() for r in list(_PA_ROUTERS)])


# ---- Main / Backup synchronisation between two computers (LAN) --------------------------------------------------------------
import hashlib
import hmac
import http.server
import socketserver
import struct

SYNC_PORT = 47800                  # control channel (TCP); the file server of the Main uses SYNC_PORT + 1
SYNC_MAX_MSG = 256 * 1024 * 1024
SYNC_VOLATILE = ("col_widths", "settings")        # changes that must not reload the Backup's list


def sync_pack(obj):
    b = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return struct.pack(">I", len(b)) + b


def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(65536, n - len(buf)))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def sync_recv(sock):
    """One message or None (connection closed / garbage)."""
    head = _recv_exact(sock, 4)
    if head is None:
        return None
    n = struct.unpack(">I", head)[0]
    if n > SYNC_MAX_MSG:
        return None
    body = _recv_exact(sock, n)
    if body is None:
        return None
    try:
        obj = json.loads(body.decode("utf-8"))
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def sync_proof(key, nonce):
    return hmac.new(key.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256).hexdigest()


def sync_struct_hash(data):
    """Hash of everything in a show that needs the Backup's list to be rebuilt (volumes and layout excluded)."""
    d = {k: v for k, v in data.items() if k not in SYNC_VOLATILE}
    d["outputs"] = [{k: v for k, v in o.items() if k not in ("volume", "screen", "audio")} for o in data.get("outputs", []) if isinstance(o, dict)]
    if isinstance(d.get("multiview"), dict):
        d["multiview"] = {k: v for k, v in d["multiview"].items() if k != "geo"}
    if isinstance(d.get("composite"), dict):
        d["composite"] = {k: v for k, v in d["composite"].items() if k != "screen"}
    return hashlib.md5(json.dumps(d, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def sync_scan_files(data, cache=None):
    """Absolute paths of existing files named anywhere in a show -> {path: size}."""
    found = {}
    cache = cache if cache is not None else {}

    def walk(v):
        if isinstance(v, str):
            if len(v) > 3 and (os.path.isabs(v) or (len(v) > 2 and v[1] == ":")) and "://" not in v:
                if v not in cache:
                    try:
                        cache[v] = os.path.getsize(v) if os.path.isfile(v) else None
                    except OSError:
                        cache[v] = None
                if cache[v] is not None:
                    found[v] = cache[v]
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(data)
    return found


def sync_rewrite(data, mapping):
    """The show with every string that is a key of `mapping` replaced."""
    if isinstance(data, str):
        return mapping.get(data, data)
    if isinstance(data, dict):
        return {k: sync_rewrite(v, mapping) for k, v in data.items()}
    if isinstance(data, list):
        return [sync_rewrite(v, mapping) for v in data]
    return data


def sync_local_path(folder, path):
    h = hashlib.sha1(path.encode("utf-8")).hexdigest()[:10]
    return os.path.join(folder, h, os.path.basename(path))           # the file keeps exactly the Main's name


class _SyncFileHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        key = self.server.sync_key
        if u.path != "/f" or q.get("k", [""])[0] != sync_proof(key, "files"):
            self.send_error(403)
            return
        p = q.get("p", [""])[0]
        if p not in self.server.allowed():
            self.send_error(404)
            return
        try:
            size = os.path.getsize(p)
            start, end = 0, size - 1
            rng = self.headers.get("Range")
            m = re.match(r"bytes=(\d+)-(\d*)", rng or "")
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            if start >= size:
                self.send_error(416)
                return
            end = min(end, size - 1)
            self.send_response(206 if m else 200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if m:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with open(p, "rb") as fh:
                fh.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = fh.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (OSError, ConnectionError):
            pass


class _ThreadingHTTP(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class SyncServer:
    """Main: accepts the Backup(s), sends them the show ("state") and the playback ("play") messages and serves the media files."""

    def __init__(self, key, port, allowed):
        self.key, self.port = key, port
        self.lock = threading.Lock()
        self.clients = []
        self.prog = {}                                   # connection -> (bytes done, bytes total) of the Backup's file copy
        self.last_state = None
        self.stop_flag = False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", port))
        self.sock.listen(4)
        self.sock.settimeout(0.5)
        self.http = _ThreadingHTTP(("0.0.0.0", port + 1), _SyncFileHandler)
        self.http.sync_key, self.http.allowed = key, allowed
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self.stop_flag:
            try:
                conn, addr = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            if self.stop_flag:
                conn.close()
                return
            threading.Thread(target=self._serve, args=(conn, addr), daemon=True).start()

    def _serve(self, conn, addr):
        try:
            conn.settimeout(5)
            nonce = os.urandom(12).hex()
            conn.sendall(sync_pack({"t": "hello", "nonce": nonce, "v": 1}))
            msg = sync_recv(conn)
            if not msg or msg.get("t") != "auth" or not hmac.compare_digest(str(msg.get("proof", "")), sync_proof(self.key, nonce)):
                conn.sendall(sync_pack({"t": "denied"}))
                conn.close()
                diag_log(f"sync: {addr[0]} refused (wrong key)")
                return
            conn.sendall(sync_pack({"t": "ok"}))
            with self.lock:
                self.clients.append(conn)
                last = self.last_state
            if last is not None:
                conn.sendall(last)
            diag_log(f"sync: Backup {addr[0]} connected")
            conn.settimeout(None)
            while not self.stop_flag:
                m = sync_recv(conn)
                if m is None:
                    break
                if m.get("t") == "prog":
                    with self.lock:
                        self.prog[conn] = (int(m.get("done", 0)), int(m.get("total", 0)))
        except OSError:
            pass
        finally:
            with self.lock:
                self.prog.pop(conn, None)
                if conn in self.clients:
                    self.clients.remove(conn)
            try:
                conn.close()
            except OSError:
                pass
            diag_log(f"sync: Backup {addr[0]} disconnected")

    def broadcast(self, obj, remember=False):
        data = sync_pack(obj)
        with self.lock:
            if remember:
                self.last_state = data
            clients = list(self.clients)
        for c in clients:
            try:
                c.settimeout(3)
                c.sendall(data)
                c.settimeout(None)
            except OSError:
                with self.lock:
                    if c in self.clients:
                        self.clients.remove(c)
                try:
                    c.shutdown(socket.SHUT_RDWR)
                    c.close()
                except OSError:
                    pass

    def count(self):
        with self.lock:
            return len(self.clients)

    def progress(self):
        """(done, total) bytes of all the Backups that are copying files now."""
        with self.lock:
            vals = [v for v in self.prog.values() if v[1] > 0]
        return sum(v[0] for v in vals), sum(v[1] for v in vals)

    def close(self):
        self.stop_flag = True
        time.sleep(0.6)                                   # the accept loop sees the flag and leaves
        try:
            self.sock.close()
        except OSError:
            pass
        try:
            self.http.shutdown()
            self.http.server_close()
        except Exception:
            pass
        with self.lock:
            for c in self.clients:
                try:
                    c.shutdown(socket.SHUT_RDWR)
                    c.close()
                except OSError:
                    pass
            self.clients = []


class SyncClient:
    """Backup: keeps a connection to the Main (reconnecting) and hands every message to `on_msg`."""

    def __init__(self, host, port, key, on_msg):
        self.host, self.port, self.key, self.on_msg = host, port, key, on_msg
        self.connected, self.ever, self.denied = False, False, False
        self.last_rx = time.time()
        self.stop_flag = False
        self.err = ""
        self.sock = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self.stop_flag:
            try:
                s = socket.create_connection((self.host, self.port), timeout=4)
                self.sock = s
                s.settimeout(5)
                hello = sync_recv(s)
                if not hello or hello.get("t") != "hello":
                    raise OSError("not a Main")
                s.sendall(sync_pack({"t": "auth", "proof": sync_proof(self.key, hello["nonce"])}))
                ok = sync_recv(s)
                if not ok or ok.get("t") != "ok":
                    self.denied = True
                    raise OSError("wrong key")
                self.denied = False
                self.connected, self.ever, self.err = True, True, ""
                self.last_rx = time.time()
                s.settimeout(None)
                while not self.stop_flag:
                    msg = sync_recv(s)
                    if msg is None:
                        break
                    self.last_rx = time.time()
                    self.on_msg(msg)
            except OSError as exc:
                self.err = str(exc)
            finally:
                self.connected = False
                try:
                    if self.sock:
                        self.sock.close()
                except OSError:
                    pass
            if not self.stop_flag:
                time.sleep(1.0)

    def send(self, obj):
        try:
            if self.sock is not None and self.connected:
                self.sock.sendall(sync_pack(obj))
        except OSError:
            pass

    def close(self):
        self.stop_flag = True
        try:
            if self.sock:
                self.sock.shutdown(socket.SHUT_RDWR)
                self.sock.close()
        except OSError:
            pass


def sync_download(host, port, key, path, dest, progress=None, stop=None):
    """Copies `path` of the Main to `dest` (resumable). True when complete."""
    part = dest + ".part"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    have = os.path.getsize(part) if os.path.exists(part) else 0
    url = f"http://{host}:{port + 1}/f?k={sync_proof(key, 'files')}&p={urllib.parse.quote(path, safe='')}"
    req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            mode = "ab" if r.status == 206 else "wb"
            with open(part, mode) as fh:
                while True:
                    if stop is not None and stop():
                        return False
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
                    if progress:
                        progress(len(chunk))
        os.replace(part, dest)
        return True
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and os.path.exists(part):        # already complete
            os.replace(part, dest)
            return True
        return False
    except (OSError, urllib.error.URLError):
        return False


MV_QUALITIES = ("auto", "720", "480", "360", "240")


TILE_COPY_NETWORK = False        # the UDP copy of a network video (YouTube) was slower than opening it directly


YT_PREFETCH_LOCK = threading.Lock()


class TileFeed:
    """What an output is playing, copied once for ALL its Multiview tiles: one ffmpeg reads the same source at the output's
    position, scales it down (tile quality) and sends MPEG-TS (tee) to a local UDP port per tile. The tiles only display
    this copy, so they cannot drift away from the output and never open the file / YouTube address a second time."""

    PORTS = 5                           # 4 Multiview tiles + the output's monitor window
    TILE_SLOTS = 4
    MON_PORT = 4

    def __init__(self, ffmpeg, ports):
        self.ffmpeg, self.ports = ffmpeg, list(ports)
        self.proc, self.key = None, None
        self.gen = 0                    # grows at every (re)start: the tiles re-open their UDP stream
        self.t_start, self.start_pos, self.out_t = 0.0, 0.0, 0.0
        self.got, self.fails, self.need = False, 0, False
        self.live, self.owner = False, None
        self.starts = collections.deque(maxlen=6)          # times of the latest (re)starts: a copy that keeps restarting is not following anything
        self.err = collections.deque(maxlen=6)

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def pos(self):
        """Content time (s) of the newest picture ffmpeg has sent."""
        return self.start_pos + self.out_t

    def stop(self):
        p, self.proc = self.proc, None
        if p is not None:
            try:
                p.kill()
            except OSError:
                pass

    def command(self, spec, pos, height):
        image, live, net = bool(spec.get("image")), bool(spec.get("live")), bool(spec.get("src"))
        ua = spec.get("ua") or ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36" if net else None)
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "verbose" if net else "error", "-nostdin", "-progress", "pipe:1", "-nostats", "-stats_period", "0.5", "-re"]
        if ua:
            cmd += ["-user_agent", ua]
        if net:
            cmd += ["-probesize", "3000000", "-analyzeduration", "1000000", "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "2"]
        if not (live or image):
            cmd += ["-ss", f"{max(0.0, pos):.3f}"]
        if image:
            cmd += ["-loop", "1", "-framerate", "5"]
        cmd += ["-i", spec.get("src") or spec["path"], "-map", "0:v:0", "-an", "-sn",
                "-vf", f"scale='trunc(iw*sar/2)*2':ih,setsar=1,scale=-2:'min({int(height)},ih)':flags=bilinear",
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-pix_fmt", "yuv420p",
                "-bf", "0", "-g", "30", "-force_key_frames", "expr:gte(t,n_forced*0.5)", "-crf", "26",
                "-muxdelay", "0", "-muxpreload", "0", "-max_delay", "0"]
        tee = "|".join(f"[f=mpegts:flush_packets=1]udp://127.0.0.1:{p}?pkt_size=1316" for p in self.ports)
        return cmd + ["-f", "tee", tee]

    def start(self, spec, pos, height):
        self.stop()
        cmd = self.command(spec, pos, height)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)   # the copy for the tiles never competes with the playback
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, creationflags=flags)
        self.gen += 1
        self.starts.append(time.time())
        self.live = bool(spec.get("live"))
        self.key, self.start_pos, self.t_start, self.out_t, self.got, self.need = spec_key(spec, height), (0.0 if (spec.get("live") or spec.get("image")) else max(0.0, pos)), time.time(), 0.0, False, False
        self.err = collections.deque(maxlen=6)
        diag_log(f"tile / monitor copy #{self.gen}: {'live' if spec.get('live') else 'image' if spec.get('image') else 'video'} "
                 f"{'network' if spec.get('src') else 'file'} from {max(0.0, pos):.1f}s at {int(height)}p: {str(spec.get('src') or spec.get('path'))[:80]}")
        threading.Thread(target=self._read, args=(self.proc,), daemon=True, name="tilefeed").start()
        threading.Thread(target=self._drain, args=(self.proc, self.err), daemon=True).start()

    def _read(self, proc):
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("out_time_us="):
                    try:
                        v = int(line.split("=", 1)[1])
                    except ValueError:
                        continue
                    if proc is self.proc:
                        self.out_t = max(0.0, v / 1e6)
                        self.got = self.got or v > 0
                elif line.startswith("frame=") and proc is self.proc:
                    try:
                        self.got = self.got or int(line.split("=", 1)[1]) > 0
                    except ValueError:
                        pass
        except Exception:
            pass

    @staticmethod
    def _drain(proc, keep):
        try:
            for line in proc.stderr:
                keep.append(line.decode("utf-8", "replace").strip()[:200])
        except Exception:
            pass


def spec_key(spec, height):
    """What makes a running tile feed obsolete: another file / address / size (not the position: that is followed separately)."""
    return (spec.get("path"), spec.get("src"), int(height), bool(spec.get("image")))


def cap_command(ffmpeg, cap, udp_ports):
    """One capture process, one encode, MPEG-TS copied (tee) to every consumer's local UDP port."""
    args, has_v, has_a = cap_input_args(cap)
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "info", "-nostdin", "-fflags", "nobuffer", "-flags", "low_delay"] + args
    if has_v:
        cmd += ["-map", "0:v:0", "-an", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-pix_fmt", "yuv420p",
                "-bf", "0", "-g", "30", "-force_key_frames", "expr:gte(t,n_forced*0.5)", "-crf", "22"]      # a key frame every 0.5 s: a window that joins late waits half a second at most
    else:
        cmd += ["-map", "0:a:0", "-vn", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]
    cmd += ["-muxdelay", "0", "-muxpreload", "0", "-max_delay", "0"]                # nothing is held back by the muxer
    tee = "|".join(f"[f=mpegts:flush_packets=1]udp://127.0.0.1:{p}?pkt_size=1316" for p in udp_ports)
    return cmd + ["-f", "tee", tee]


def yt_live_command(ffmpeg, urls, udp_ports):
    """One ffmpeg, no re-encode: the live's HLS address(es) -> MPEG-TS copied (tee) to every consumer's local UDP port."""
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-progress", "pipe:1", "-nostats", "-stats_period", "1"]
    for u in urls[:2]:
        cmd += ["-user_agent", ua, "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
        if "m3u8" in u or "hls_" in u:
            cmd += ["-live_start_index", "-2"]                     # ~4 s behind the live edge: nothing to catch up, no burst into UDP
        cmd += ["-re", "-fflags", "+genpts", "-i", u]
    cmd += (["-map", "0:v:0", "-map", "1:a:0"] if len(urls) > 1 else ["-map", "0:v:0?", "-map", "0:a:0?"])
    cmd += ["-c", "copy", "-muxdelay", "0", "-muxpreload", "0", "-max_delay", "0"]
    tee = "|".join(f"[f=mpegts:flush_packets=1]udp://127.0.0.1:{p}?pkt_size=1316" for p in udp_ports)
    return cmd + ["-f", "tee", tee]


class YtLiveSource:
    """A YouTube live stream as a live input (same interface as RtmpListener / CaptureSource: state, udp_ports, gen)."""

    def __init__(self, ffmpeg, resolve, udp_ports, on_change):
        self.ffmpeg, self.resolve, self.udp_ports, self.on_change = ffmpeg, resolve, list(udp_ports), on_change
        self.state, self.error, self.rejected, self.gen = "waiting", "", "", 0
        self._proc, self._stop, self._thread = None, threading.Event(), None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="ytlive")
        self._thread.start()

    def stop(self):
        self._stop.set()
        p = self._proc
        if p is not None:
            try:
                p.kill()
            except OSError:
                pass

    def _set(self, state, error=""):
        if (state, error) != (self.state, self.error):
            if state == "live" and self.state != "live":
                self.gen += 1
            self.state, self.error = state, error
            try:
                self.on_change(self)
            except Exception:
                pass

    def _run(self):
        fails = 0
        while not self._stop.is_set():
            started, was_live, tail = time.time(), False, collections.deque(maxlen=4)
            try:
                urls = self.resolve()
            except Exception as exc:
                urls, tail = [], collections.deque([str(exc)[:160]])
            if self._stop.is_set():
                return
            if not urls:
                self._set("error", "yt-dlp: no address (live not started, private or blocked)")
                self._stop.wait(min(30, 8 + 4 * fails))
                fails += 1
                continue
            self._set("waiting")
            try:
                proc = subprocess.Popen(yt_live_command(self.ffmpeg, urls, self.udp_ports), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError as exc:
                self._set("error", str(exc)[:160])
                self._stop.wait(15)
                continue
            self._proc = proc

            def drain(pr=proc):
                try:
                    for line in pr.stderr:
                        tail.append(line.decode("utf-8", "replace").strip()[:200])
                except Exception:
                    pass
            threading.Thread(target=drain, daemon=True).start()
            try:
                for raw in proc.stdout:
                    if self._stop.is_set():
                        break
                    line = raw.decode("utf-8", "replace").strip()
                    if not was_live and (line.startswith("out_time_us=") or line.startswith("frame=")):
                        try:
                            if int(line.split("=", 1)[1]) > 0:
                                was_live = True
                                self._set("live")
                        except ValueError:
                            pass
            except Exception:
                pass
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass
            self._proc = None
            if self._stop.is_set():
                return
            if was_live and time.time() - started > 20:
                fails = 0
                self._set("waiting")                      # the stream hiccuped / the address expired: resolve and connect again
                self._stop.wait(1.0)
            else:
                fails += 1
                self._set("error" if not was_live else "waiting", (" | ".join(tail) or "ffmpeg ended")[:160] if not was_live else "")
                self._stop.wait(min(30, 4 + 3 * fails))


class CaptureSource:
    """Same interface as RtmpListener / NdiReceiver (state waiting|live|error, udp_ports, gen), fed by a capture device."""

    def __init__(self, ffmpeg, cap, udp_ports, on_change):
        self.ffmpeg, self.cap, self.udp_ports, self.on_change = ffmpeg, cap, list(udp_ports), on_change
        self.state, self.error, self.rejected, self.gen = "waiting", "", "", 0
        self.progress = ""
        self._proc, self._stop, self._thread = None, threading.Event(), None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="capture")
        self._thread.start()

    def stop(self):
        self._stop.set()
        p = self._proc
        if p is not None:
            try:
                p.kill()
            except OSError:
                pass

    def _set(self, state, error=""):
        if (state, error) != (self.state, self.error):
            if state == "live" and self.state != "live":
                self.gen += 1
            self.state, self.error = state, error
            try:
                self.on_change(self)
            except Exception:
                pass

    def _run(self):
        fails = 0
        while not self._stop.is_set():
            started, was_live = time.time(), False
            tail = collections.deque(maxlen=8)
            try:
                self._proc = subprocess.Popen(cap_command(self.ffmpeg, self.cap, self.udp_ports), stdin=subprocess.DEVNULL,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, bufsize=0,
                                              creationflags=_NOWIN | _ABOVE_NORMAL)       # the encoder must not be starved by the players
            except OSError as exc:
                self._set("error", str(exc)[:120])
                self._stop.wait(5)
                continue
            proc, buf = self._proc, b""
            while True:
                chunk = proc.stderr.read(4096)
                if not chunk:
                    break
                buf += chunk
                *lines, buf = re.split(rb"[\r\n]+", buf)
                for raw in lines:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    tail.append(line)
                    if line.startswith("frame=") or "speed=" in line:
                        self.progress = line[:140]
                    if not was_live and (line.startswith("frame=") or line.startswith("size=") or "time=" in line):
                        was_live = True
                        fails = 0
                        self._set("live")
            rc = proc.wait()
            self._proc = None
            if self._stop.is_set():
                break
            if was_live:                                 # device unplugged / window closed: wait and try again
                self._set("waiting")
                self._stop.wait(1.5)
            else:
                fails += 1
                msg = next((l for l in reversed(tail) if re.search(r"rror|ould not|not found|nable|ailed|denied", l)),
                           tail[-1] if tail else f"ffmpeg exit {rc}")
                if fails >= 2:
                    self._set("error", msg[:140])
                self._stop.wait(min(8.0, 1.0 + fails))
        self._proc = None

    def join(self, timeout=2.0):
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout)


class CapPreview:
    """The preview inside a capture dialog: one throw-away capture process (one UDP port) shown in an mpv embedded in the dialog."""

    def __init__(self, app, frame, kind, on_status):
        self.app, self.frame, self.kind, self.on_status = app, frame, kind, on_status
        self.src, self.player, self.cap, self.port = None, None, None, 0
        self.dead, self.hold, self.failed, self._tok = False, False, False, 0

    def _stop_src(self):
        self._tok += 1
        src, self.src = self.src, None
        if src is not None:
            src.stop()
        return src

    def _idle(self):
        if self.player is not None:
            try:
                self.player.command("stop")
            except Exception:
                pass

    def show(self, cap):
        """(Re)start the preview for this capture; None = nothing to show."""
        self.cap = cap
        if self.dead or self.hold:
            return
        self._stop_src()
        self._idle()
        if cap is None:
            return
        ff = self.app._ffmpeg_path()
        if not ff:
            return
        self.port = free_udp_ports(1)[0]
        tok = self._tok
        self.on_status(self.app.t("cap_prev_wait"))
        self.src = CaptureSource(ff, cap, [self.port], lambda s, tok=tok: self.app._uiq.put(lambda: self._changed(s, tok)))
        self.src.start()

    def pause(self):
        """Release the device (a driver dialog needs it)."""
        self.hold = True
        src = self._stop_src()
        self._idle()
        if src is not None:
            src.join(3.0)

    def resume(self):
        self.hold = False
        self.show(self.cap)

    def stop(self):
        self.dead = True
        self._stop_src()
        p, self.player = self.player, None
        if p is not None:
            try:
                mpv_terminate(p)
            except Exception:
                pass

    def _changed(self, src, tok):
        if self.dead or tok != self._tok or src is not self.src:
            return
        if src.state == "live":
            self._play()
        elif src.state == "error":
            self._idle()
            self.on_status(self.app.t("cap_prev_err", err=src.error))
        else:
            self.on_status(self.app.t("cap_prev_wait"))

    def _make_player(self):
        wid = self.frame.winfo_id()
        if self.kind == "audio":                                      # no picture: a level meter drawn from the sound (muted)
            opts = dict(PV_OPTS, aid="auto", ao="null")
            return new_mpv(wid=wid, vo="gpu", lavfi_complex="[aid1]showvolume=w=600:h=28:t=0:v=0:f=0.5:b=2[vo]", **opts)
        return new_pv(wid)

    def _play(self):
        try:
            if self.player is None:
                if self.failed:
                    return
                self.frame.update_idletasks()
                self.player = self._make_player()
            self.app._rtmp_props(self.player, True, self.kind != "audio")
            self.player.play(f"udp://127.0.0.1:{self.port}?{UDP_Q_CAP}")
            self.on_status(self.app.t("cap_prev_live"))
        except SHUTDOWN:
            raise
        except Exception:
            self.failed, self.player = True, None
            self.on_status(self.app.t("cap_prev_live") + " " + self.app.t("cap_prev_nolib"))



APP_ID = "leitor-multimonitor"
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".leitor_multimonitor.json")
PL_EXT = ".mmp"                # extension of the saved shows

MEDIA_EXT = {
    ".mp4", ".mov", ".mkv", ".avi", ".wmv", ".webm", ".m4v", ".mpg", ".mpeg", ".mxf", ".ts",
    ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg",
    ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp",
}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# --- Temas (Escuro / Claro) ---
THEMES = {
    "dark": {
        "BG": "#14161b", "PANEL": "#1d2027", "FG": "#e8eaed", "MUTED": "#8b919c",
        "ACCENT": "#4c8dff", "GREEN": "#3ecf8e", "RED": "#e5534b",
        "ORANGE": "#f0a030", "ACCENT_DIM": "#2f5fb0",
        "ROW_PLAYING": "#d63a3a", "ROW_SELECTED": "#3a78e0",
        "TREE_BG": "#1d2027", "TREE_HEAD": "#262a33",
        "ENTRY_BG": "#1d2027", "PICKER_BG": "#262a33", "PICKER_ACTIVE": "#2f3440",
        "LINE": "#2b303a", "SCROLL_BG": "#1d2027", "SCROLL_THUMB": "#3a3f4b"
    },
    "light": {
        "BG": "#f0f2f5", "PANEL": "#ffffff", "FG": "#1a1d20", "MUTED": "#656d78",
        "ACCENT": "#0066cc", "GREEN": "#107c10", "RED": "#d83b01",
        "ORANGE": "#8a6100", "ACCENT_DIM": "#cce0ff",
        "ROW_PLAYING": "#fad1d1", "ROW_SELECTED": "#cce0ff",
        "TREE_BG": "#ffffff", "TREE_HEAD": "#e1e4e8",
        "ENTRY_BG": "#ffffff", "PICKER_BG": "#e1e4e8", "PICKER_ACTIVE": "#d0d5dd",
        "LINE": "#d1d5db", "SCROLL_BG": "#f0f2f5", "SCROLL_THUMB": "#c0c6d0"
    }
}

TICK_MS = 30            
UI_EVERY = 5            
PREVIEW_EVERY = 5       
MONITOR_POLL_MS = 2000  
SCHED_EVERY = 7         
OUT_PANEL_W = 440       
VIEW_W, VIEW_H = 200, 112      
PV_W, PV_H = VIEW_W, VIEW_H    
IP_W, IP_H = VIEW_W, VIEW_H    
INFO_H = 176            # fixed height of the selected item's information area (preview row)
MIN_GAP = 0.04          

COLUMNS = [("pos", 44), ("nome", 180), ("out", 70), ("next", 140), ("rep", 70), ("mute", 60),
           ("fade_in", 140), ("fade_out", 140),
           ("f1", 80), ("fl", 90), ("sim", 150), ("sched", 140), ("dow", 150), ("dur", 110), ("trecho", 160)]

ASPECTS = ["fit", "fill", "stretch", "orig", "a169", "a43"]
FADE_COLS = ("fade_in", "fade_out")
COL_MIN_W = 30
BULK_FLAG_COLS = ("fade_in", "fade_out", "f1", "fl")
PLAY_OPENS_FULLSCREEN = False   # False: Play never opens a fullscreen (only "Turn On" does); True: old behaviour
FADE_VIDEO = True       # fades also dim the picture, not only the audio
OUT_HWDEC = "auto-copy"   # fixed for the output's lifetime: changing hwdec re-inits the decoder (stalls streams)
RES_OPTIONS = ["Original", "1920x1080", "1280x720", "3840x2160", "720x576", "1024x768"]
FPS_OPTIONS = ["Original", "24", "25", "30", "50", "60"]
LANGS = [("pt", "Português"), ("en", "English")]

STR = {
    "pt": {
        "app_title": "Leitor Multi-Monitor",
        "welcome_title": "Bem-vindo ao Leitor Multi-Monitor",
        "welcome_new": "Novo Show",
        "welcome_load": "Carregar Show",
        "welcome_last": "Abrir Último Show",
        "out_window": "Output {n}",
        "out_label": "Output {n}",
        "identify": "Identificar Ecrãs",
        "add_output": "Adicionar Output",
        "out_on": "Ligar",
        "out_off": "Desligar",
        "pl_open": "Abrir…",
        "pl_save": "Guardar",
        "pl_save_as": "Guardar como…",
        "screen_label": "Ecrã {n} — {w}×{h}{tag}",
        "screen_short": "Ecrã {n} — {w}×{h}",
        "screen_n": "Ecrã {n}",
        "screen_none": "Nenhum",
        "tag_primary": " · principal",
        "pick_taken": " — em uso em {o}", "pick_taken_mv": " — em uso no Multiview",
        "btn_add_files": "＋ Ficheiros",
        "btn_add_folder": "＋ Pasta",
        "btn_youtube": "YouTube",
        "btn_logins": "Logins", "lg_title": "Logins — contas YouTube e Spotify",
        "lg_help": "Inicie sessão nas suas contas. O nome e a imagem da conta ficam guardados e a sessão é usada nas importações: o YouTube passa a usar a sua conta (vídeos com restrição de idade, listas privadas, sem o aviso «não é um robô») e o Spotify lê as suas playlists privadas, as Músicas gostadas e listas completas.",
        "lg_login": "Iniciar sessão…", "lg_relogin": "Trocar de conta…", "lg_logout": "Terminar sessão", "lg_none": "Sem sessão iniciada",
        "lg_signed_in": "Sessão iniciada", "lg_premium": "Premium", "lg_free": "Gratuita", "lg_unknown_name": "(conta ligada)",
        "lg_expired": "A sessão do Spotify expirou — inicie sessão outra vez nos Logins.",
        "lg_ok_youtube": "Sessão do YouTube guardada. Os vídeos passam a ser abertos com a sua conta.",
        "lg_ok_spotify": "Sessão do Spotify guardada. As importações passam a usar a sua conta.",
        "lg_out_ask": "Terminar a sessão e apagar os dados guardados desta conta neste computador?",
        "lg_note": "A sessão é guardada só neste computador (pasta do utilizador). O áudio do Spotify continua protegido (DRM), mesmo com Premium: as músicas continuam a tocar a partir do YouTube. A sua palavra-passe nunca é lida nem guardada.",
        "lg_using": "Conta: {name}", "lg_none_hint": "Sem conta (menu Logins, junto ao «Criado por»).",
        "lg_my_playlists": "As minhas playlists…", "lg_liked": "Músicas gostadas", "lg_via": "  ·  lido da sua conta",
        "sp_login_help": "Abriu-se uma janela do browser (perfil temporário, separado do seu). Inicie sessão no Spotify. Quando a sessão for detetada, é guardada e a janela fecha sozinha. A palavra-passe não é lida nem guardada.",
        "pl_saved_ok": "A preset «{name}» foi gravada com sucesso.\n\n{path}",
        "btn_spotify": "Spotify", "dlg_spotify": "Importar do Spotify",
        "ask_spotify_url": "Cola o link do Spotify (música, álbum ou playlist):",
        "sp_hint": "O áudio do Spotify é protegido: a app lê os nomes das músicas e toca-as a partir do YouTube (mesmo título, artista e duração).",
        "sp_not_link": "Isto não parece um link do Spotify (open.spotify.com/track/…, /album/… ou /playlist/…).",
        "sp_reading": "A ler o Spotify…", "sp_found": "{title} — {n} música(s). Escolhe as que queres (todas selecionadas).",
        "sp_err": "Não foi possível ler este link: {err}", "sp_import": "Importar", "sp_video": "Importar com vídeo (videoclip) em vez de só áudio",
        "st_sp_searching": "Spotify: a procurar no YouTube… {k}/{n}", "st_sp_done": "Spotify: {n} música(s) importada(s) de «{title}»",
        "st_sp_done_missing": "Spotify: {n} importada(s), {m} sem correspondência no YouTube (ver player.log)",
        "btn_audio_routing": "Encaminhamento de áudio",
        "btn_play": "Reproduzir",
        "btn_stop": "Parar",
        "btn_remove": "Remover",
        "btn_clear": "Limpar lista",
        "btn_up": "▲ Subir",
        "btn_down": "▼ Descer",
        "col_pos": "#",
        "col_nome": "Ficheiro",
        "col_out": "Output",
        "col_next": "Próximo",
        "col_rep": "Repetir",
        "col_fade_in": "Fade In",
        "col_fade_out": "Fade Out",
        "col_mute": "Silenciar",
        "col_ms": "Antes do fim (ms)",
        "col_f1": "Congelar 1º",
        "col_fl": "Congelar últ.",
        "col_wn": "Com seguinte",
        "col_sim": "Reproduzir em Simultâneo",
        "col_sched": "Agendar horário",
        "col_dow": "Dia da semana",
        "dow_title": "Dias da semana", "dow_all": "Todos os dias", "dow_hint": "O item só arranca nos dias marcados (sem nenhum marcado = todos os dias).",
        "dow_names": "Seg Ter Qua Qui Sex Sáb Dom", "btn_ok": "OK",
        "mi_video": "Ficheiros de vídeo…", "mi_audio": "Ficheiros de áudio…", "mi_image": "Ficheiros de imagem…",
        "ft_video": "Vídeo", "ft_audio": "Áudio", "ft_imgs": "Imagem",
        "dlg_add_video": "Adicionar vídeos", "dlg_add_audio": "Adicionar áudios", "dlg_add_image": "Adicionar imagens",
        "col_dur": "Duração",
        "col_trecho": "Trecho (início → fim)",
        "nx_none": "Nenhum",
        "nx_prev": "Anterior",
        "nx_next": "Seguinte",
        "nx_menu_none": "Nenhum (terminar)",
        "ctx_duplicate": "Duplicar",
        "ctx_remove": "Remover",
        "ctx_rename": "Renomear",
        "ctx_play": "Reproduzir",
        "ctx_output": "Output",
        "ctx_aspect": "Aspecto",
        "asp_fit": "Ajustar (manter proporção)",
        "asp_fill": "Preencher (cortar o excesso)",
        "asp_stretch": "Esticar (deformar)",
        "asp_orig": "Tamanho original (1:1)",
        "asp_a169": "Forçar 16:9",
        "asp_a43": "Forçar 4:3",
        "aspect_line": "Aspecto: {mode}",
        "hint": "Vermelho = em reprodução · Azul = selecionado. Clique com Ctrl/Shift ou use Ctrl+A para selecionar vários itens. Arraste as linhas para reordenar e os limites dos cabeçalhos para redimensionar as colunas. Botão direito nos cabeçalhos Fade/Congelar/Próximo aplica a todos ou aos selecionados.",
        "opt_shuffle": "Shuffle",
        "opt_blackout": "Ecrã preto (B)",
        "opt_mute": "Silenciar (M)",
        "opt_volume": "Volume",
        "opt_theme_dark": "Tema Escuro",
        "opt_theme_light": "Tema Claro",
        "mon_title": "Monitor de saída",
        "mon_enabled": "Ativar monitor",
        "mon_caption": "atual / total   ·   restante",
        "res_label": "Resolução",
        "fps_label": "FPS",
        "opt_original": "Original",
        "ip_header": "ITEM SELECIONADO",
        "ip_placeholder": "Selecione um item da lista",
        "ip_unavailable": "Pré-visualização indisponível",
        "btn_mark_in": "[ Início (I)",
        "btn_mark_out": "Fim ] (O)",
        "btn_marks_clear": "Limpar",
        "info_select": "Selecione um item da lista.",
        "info_loading": "A carregar…",
        "info_load_err": "Erro ao carregar: {err}",
        "inf_video": "Vídeo",
        "inf_image": "Imagem",
        "inf_no_video": "Sem vídeo",
        "inf_audio": "Áudio",
        "inf_no_audio": "Sem áudio",
        "inf_channels": "{n} canais",
        "inf_container": "Contentor",
        "inf_duration": "Duração total",
        "tr_whole": "Trecho: item completo (sem marcas)",
        "tr_range": "Trecho: {a} → {b}{extra}",
        "tr_len": "  ·  duração {d}",
        "tr_end": "fim",
        "st_ready": "Pronto. Adicione ficheiros e outputs, escolha os ecrãs e carregue em ▶.",
        "st_ready_short": "Pronto.",
        "st_overlap": "Atenção: o Output {o} está no mesmo ecrã que esta interface.",
        "st_screens_updated": "Lista de ecrãs atualizada.",
        "st_screen_gone": "O ecrã do Output {o} deixou de estar disponível — a usar o Ecrã {n}.",
        "st_screen_gone_none": "O ecrã do Output {o} deixou de estar disponível e não há outro livre.",
        "st_no_screen": "Escolha um ecrã para o Output {n} antes de abrir o fullscreen.",
        "st_out_nowindow": "Output {n}: a reproduzir sem janela. Use «{btn}» para abrir o fullscreen.",
        "st_output_lost": "O Output {n} foi fechado.",
        "st_mirror_fail": "Não foi possível criar o monitor do Output {n}: {err}",
        "st_ip_fail": "Não foi possível criar a pré-visualização do item: {err}",
        "st_playing": "A tocar {i}/{n} (Output {o}): {name}",
        "st_error": "Erro: {err}",
        "st_paused_first": "Em pausa na primeira frame — carregue em ▶ para iniciar.",
        "st_wn_same_output": "«Com seguinte»: o item seguinte usa o mesmo output — não foi iniciado.",
        "st_marks_select": "Selecione um vídeo/áudio para definir marcas.",
        "st_in_before_out": "O início tem de ficar antes do fim.",
        "st_out_after_in": "O fim tem de ficar depois do início.",
        "st_in_set": "Início definido em {t}.",
        "st_out_set": "Fim definido em {t}.",
        "st_end_of_file": "fim do ficheiro",
        "st_marks_cleared": "Marcas removidas.",
        "st_marks_saved": "Marcas guardadas — aplicam-se na próxima vez que o item arrancar.",
        "st_relinked": "{n} item(ns) apontavam para o removido e passaram a «Seguinte».",
        "st_duplicated": "{n} item(ns) duplicado(s).",
        "st_aspect_set": "Aspecto «{mode}» aplicado a {n} item(ns).",
        "st_output_set": "{n} item(ns) enviado(s) para o Output {o}.",
        "st_shuffle_on": "Reprodução aleatória ativada.",
        "st_shuffle_off": "Reprodução aleatória desativada.",
        "st_file_missing": "Ficheiro não encontrado: {name}",
        "st_out_missing": "O Output {n} não existe — adicione-o ou escolha outro output para este item.",
        "st_out_removed": "{name} removido.",
        "st_sched_invalid": "Hora inválida. Use HH:MM:SS.",
        "st_dur_invalid": "Duração inválida. Use H:MM:SS.",
        "outs_empty": "Sem outputs.\nCarregue em «Adicionar Output».",
        "outs_none": "Nenhum output — adicione um",
        "dlg_remove_out": "Remover output",
        "ask_remove_out": "«{name}» está ligado. Remover mesmo assim?",
        "dlg_out_err": "Erro ao iniciar o Output {n}",
        "dlg_add_files": "Adicionar ficheiros",
        "dlg_add_folder": "Adicionar pasta",
        "dlg_youtube": "Adicionar vídeo do YouTube",
        "btn_rtmp": "RTMP Input",
        "dlg_rtmp": "Adicionar RTMP Input",
        "ask_rtmp_key": "Nome do input (é também a chave RTMP):",
        "ask_rtmp_rename": "Nova chave (nome do input):",
        "rtmp_title": "RTMP INPUT",
        "rtmp_address": "Endereço",
        "rtmp_key": "Chave",
        "rtmp_waiting": "A aguardar sinal…",
        "rtmp_bad_key": "Chave recebida inválida: {rk}",
        "rtmp_err": "Erro: {err}",
        "rtmp_state_live": "COM SINAL",
        "rtmp_no_ffmpeg": "ffmpeg não encontrado. Coloque o ffmpeg junto ao programa ou no PATH.",
        "st_rtmp_added": "RTMP Input criado — endereço: {addr}  ·  chave: {rk}",
        "st_rtmp_locked": "A chave não pode ser alterada enquanto o input está a receber sinal.",
        "btn_ndi": "NDI Input",
        "btn_ndi_out": "Saída NDI",
        "ndi_quality": "Qualidade NDI", "btn_lock_kb": "🔒 Bloquear teclado", "btn_lock_kb_on": "🔓 Teclado bloqueado",
        "quality_label": "Qualidade", "big_title": "Janela maior", "btn_multiview": "Multiview", "btn_composite": "Output Composto", "btn_sync": "Sync", "sy_title": "Sincronização Main / Backup", "sy_role": "Este computador é:", "sy_host": "IP do Main (só no Backup):", "sy_port": "Porta:", "sy_key": "Chave (igual nos dois):", "sy_fail": "Backup assume após (s):", "sy_start": "Iniciar", "sy_stop": "Parar", "sy_promote": "Promover a Main", "sy_hint": "Os dois computadores têm de estar na mesma rede e ter a mesma chave. Abre as portas TCP da porta escolhida e da seguinte na firewall do Main. A ligação não é cifrada (uso em rede local). No Backup, as ações feitas à mão na reprodução são desfeitas pelo Main; os ecrãs, os dispositivos de áudio e o Multiview/Composto ficam os do próprio Backup.", "sy_st_main": "Main ativo na porta {port}. Backups ligados: {n}", "sy_st_denied": "O Main recusou a ligação: chave errada", "sy_st_conn": "Ligado ao Main {host}", "sy_st_wait": "A ligar ao Main {host}… {err}", "sy_st_applied": "Último show recebido às {t} (vezes: {n})", "sy_st_files": "A copiar ficheiros do Main: {done} / {total} MB ({left} em falta)", "ndi_preview": "Pré-visualização", "ndi_preview_pick": "Clica numa fonte da lista para a ver aqui", "ndi_preview_wait": "A ligar à fonte NDI…", "ndi_preview_err": "Não foi possível receber esta fonte", "ndi_ctx_inputs": "NDI Inputs", "ndi_ctx_none": "(sem fontes NDI na rede)", "ndi_ctx_refresh": "Procurar de novo", "ndi_ctx_window": "Janela de importação…", "sy_st_off": "Sincronização desligada", "sy_st_bk_files": "O Backup está a copiar ficheiros: {done} / {total} MB", "sy_st_sent": "Show enviado (versão {n}, {f} ficheiros)", "sy_st_promoted": "Este computador assumiu como Main", "sy_err_listen": "Sync: não foi possível abrir a porta: {err}", "sy_err_host": "Sync: indica o IP do Main", "sy_promoted": "Backup promovido a Main", "sy_promoted_msg": "O Main deixou de responder. Este computador assumiu como Main e continua a reprodução do ponto em que estava.",  "ar_devices": "Dispositivos de áudio (por sistema)", "ar_pa_missing": "Routing PortAudio: dispositivo ou sounddevice/ffmpeg indisponível (o mpv toca normalmente)", "ar_pa_failed": "Routing PortAudio falhou: {err}", "ar_pa_live": "Motor PortAudio ativo: {n} canais a {r} Hz, latência do dispositivo ~{ms} ms", "ar_pa_idle": "Motor PortAudio: carrega em Aplicar para abrir o dispositivo", "ar_asio_nosd": "precisa de «pip install sounddevice»", "ar_asio_nodrv": "nenhum driver ASIO instalado",  "ar_src_ch": "Canais da fonte:", "ar_dst_ch": "Canais do dispositivo:", "ar_corner": "Dispositivo ↓  /  Fonte →", "ar_ch": "Canal", "ar_default_dev": "Dispositivo por defeito do sistema", "ar_current": "Dispositivo: {dev}", "ar_live": "A tocar: o dispositivo abriu com {n} canais a {r} Hz", "ar_idle": "(o output não está a tocar: o dispositivo ainda não foi aberto)", "ar_na": "indisponível neste libmpv", "ar_identity": "1:1", "ar_spread": "Repetir", "ar_mono_all": "Mono → todos", "ar_clear": "Limpar", "ar_refresh": "Atualizar lista", "ar_hint": "Clica numa célula para ligar um canal da fonte (coluna) a um canal do dispositivo (linha); vários canais no mesmo destino são misturados. A saída NDI não é afetada.",  "co_title": "Output Composto", "co_res": "Resolução", "co_apply": "Aplicar", "co_unite_lbl": "Canvas único:", "co_unite": "Unir outputs", "co_arr_h": "Horizontal", "co_arr_v": "Vertical", "co_arr_g": "Grelha", "co_hint": "Arrasta outputs/inputs para o canvas · pontos azuis = corner pin · canto laranja redimensiona · botão direito remove", "co_crop": "Recorte %", "co_rot": "Rotação", "co_flip_h": "Espelho horizontal", "co_flip_v": "Espelho vertical", "co_fit": "Ajuste da imagem", "co_fit_fit": "Encaixar", "co_fit_stretch": "Esticar", "co_fit_fill": "Preencher", "co_blend": "Edge blend %", "co_l": "Esq", "co_t": "Topo", "co_r": "Dir", "co_b": "Base", "co_pin_reset": "Repor corner pin", "co_remove": "Remover slice", "co_none_sel": "(nenhum slice selecionado)", "st_co_on": "Output Composto ligado", "st_co_no_screen": "Output Composto: escolhe primeiro um ecrã", "co_unite_none": "Não há outputs para unir", "mv_title": "Multiview Output", "mv_screen": "Ecrã", "mv_clear": "Limpar tiles", "yt_press_play_short": "Carrega em ▶ no preview para abrir o vídeo antes de marcar IN/OUT", "yt_press_play": "Carrega em ▶ para abrir o vídeo (barra de tempo e marcadores)", "ytl_title": "Live do YouTube", "ytl_waiting": "A ligar à live…", "mv_quality": "Qualidade dos tiles", "mv_q_auto": "Auto", "mv_hint": "Arrasta outputs/inputs para o ecrã · arrasta o canto laranja para redimensionar · botão direito remove", "mv_outputs": "Outputs", "mv_inputs": "Inputs (lista)", "mv_remove": "Remover tile", "st_mv_taken": "Aviso: este ecrã está a ser usado por {o}", "st_mv_no_screen": "Multiview: escolhe primeiro um ecrã", "st_mv_on": "Multiview ligado",
        "ss_autostart": "Iniciar o slideshow automaticamente ao Play", "ss_apply_close": "Aplicar e fechar", "ctx_ss_start": "Iniciar slideshow", "st_mv_same": "Aviso: este é o ecrã da interface; o Multiview fica por trás da app (duplo clique ou Esc no Multiview desliga-o)",
        "mv_copy_size": "Copiar tamanho", "mv_paste_size": "Colar tamanho", "mv_copy_pos": "Copiar posição", "mv_paste_pos": "Colar posição",
        "btn_capture": "Captura", "cap_kind_video": "Dispositivo de vídeo", "cap_kind_audio": "Dispositivo de áudio", "cap_kind_screen": "Captura de ecrã", "cap_kind_window": "Captura de janela", "cap_kind_lavfi": "Fonte de teste", "cap_mi_video": "Dispositivo de vídeo (câmara / placa)…", "cap_mi_audio": "Dispositivo de áudio…", "cap_mi_screen": "Captura de ecrã…", "cap_mi_window": "Captura de janela…", "cap_title": "CAPTURA", "cap_waiting": "A iniciar a captura…", "cap_loading": "A procurar dispositivos…", "cap_none": "Nenhum dispositivo encontrado (a lista usa o ffmpeg e o DirectShow do Windows).", "cap_device": "Dispositivo", "cap_mode": "Modo", "cap_auto": "Automático", "cap_add": "Adicionar", "cap_refresh": "Atualizar", "cap_col_dev": "Dispositivo", "cap_col_ch": "Canais", "cap_col_bits": "Bits", "cap_col_rate": "Frequência", "cap_hint_audio": "Para capturar o som do sistema escolhe «Stereo Mix» ou «virtual-audio-capturer», se existir (Windows → Som → Gravação → mostrar dispositivos desativados).", "cap_screen_all": "Todos os ecrãs", "cap_windows_only": "A captura de ecrã e de janela só funciona no Windows.", "st_cap_added": "Captura «{name}» adicionada",
        "cap_apply": "Aplicar", "cap_fps_range": "Este modo só aceita entre {lo} e {hi} fps.", "cap_mouse": "Mostrar o rato", "cap_dev_opts": "Opções do dispositivo…", "cap_preview": "Pré-visualização",
        "cap_prev_wait": "A iniciar a pré-visualização…", "cap_prev_live": "Fonte ativa.", "cap_prev_err": "Erro: {err}", "cap_prev_nolib": "Pré-visualização indisponível (libmpv).",
        "cap_fps_bad": "FPS inválido (use um número entre 1 e 240, ex.: 25 ou 29.97).", "cap_dev_opts_busy": "A abrir as opções do dispositivo…",
        "cap_dev_opts_fail": "Não foi possível abrir as opções: {err}", "st_cap_applied": "Captura atualizada: {name}", "st_ndi_applied": "Fonte NDI atualizada: {name}",
        "rtmp_dlg_key": "Nome do input (chave RTMP):", "rtmp_dlg_url": "Endereço para o emissor (OBS, etc.):", "rtmp_dlg_hint": "No emissor use um keyframe de 1 s e o perfil de baixa latência (ex.: tune zerolatency, sem B-frames).",
        "st_rtmp_applied": "Chave RTMP atualizada: {name}", "ndi_edit_hint": "Escolha a nova fonte NDI:", "btn_apply_close": "Aplicar e fechar",
        "btn_group": "＋ Grupo", "ctx_group_add": "Adicionar ao grupo", "ctx_group_none": "Sem grupo", "ctx_group_ungroup": "Desfazer grupo (manter itens)", "ctx_group_toggle": "Expandir / recolher", "ask_group_name": "Nome do grupo", "grp_default": "Grupo", "st_group_made": "Grupo «{name}» criado ({n} itens)",
        "ndi_low_bw": "Baixa largura de banda",
        "ndi_refresh": "Atualizar lista",
        "ndi_none": "(nenhuma fonte NDI encontrada)",
        "ndi_title": "NDI INPUT",
        "ndi_source": "Fonte",
        "ndi_waiting": "A aguardar sinal NDI…",
        "ndi_missing_lib": "NDI indisponível. Instale: pip install ndi-python numpy",
        "ndi_out_missing": "NDI output requer: pip install ndi-python numpy",
        "st_ndi_out_err": "NDI output '{name}' parou: {err}",
        "st_ndi_search": "A procurar fontes NDI…",
        "st_ndi_added": "NDI Input criado — fonte: {src}",
        "st_ndi_out_on": "NDI output ligado: {name} (envia enquanto o output estiver ativo)",
        "st_ndi_out_off": "NDI output desligado: {name}",
        "btn_import": "Importar ▾",
        "ndi_win_hint": "Fontes NDI disponíveis na rede:",
        "ndi_win_searching": "A procurar fontes NDI…",
        "ndi_win_found": "{n} fonte(s) encontrada(s).",
        "ndi_win_none": "Nenhuma fonte NDI encontrada.",
        "ndi_win_none_hint": "Nenhuma fonte NDI encontrada. Se a app NDI já estiver aberta: instale «pip install zeroconf» (deteção alternativa), confirme que o PC e a fonte estão na mesma rede, ou use «Fonte manual…».",
        "ndi_win_pick": "Selecione uma ou mais fontes na lista.",
        "ndi_win_imported": "{n} fonte(s) importada(s).",
        "ndi_btn_import": "Importar",
        "ndi_btn_close": "Fechar",
        "ndi_manual": "Fonte manual…",
        "ask_ndi_manual": "Nome completo da fonte NDI (ex.: PC (Screen Capture)) ou o endereço IP (ex.: 192.168.1.10:5961):",
        "ndi_err_item": "Erro NDI: {err}",
        "ask_youtube_url": "Cole o URL do vídeo do YouTube:",
        "yt_loading": "A obter título e miniatura…",
        "yt_no_title": "(título indisponível)",
        "yt_btn_import": "Importar",
        "yt_btn_import_n": "Importar ({n})",
        "yt_btn_cancel": "Cancelar",
        "yt_opt_video": "Importar só este vídeo",
        "yt_opt_playlist": "Importar a playlist completa",
        "yt_pl_loading": "A obter a playlist…",
        "yt_pl_title": "Playlist: {title}",
        "yt_pl_count": "{n} vídeos serão importados como itens separados.",
        "yt_pl_err": "Não foi possível ler a playlist: {err}",
        "yt_pl_empty": "a playlist está vazia ou é privada",
        "st_youtube_playlist": "Playlist «{title}»: {n} vídeos importados (os detalhes completam-se em segundo plano).",
        "st_dropped": "{n} ficheiro(s) adicionado(s) por arrastar e largar ({skipped} ignorado(s)).",
        "st_drop_none": "Nada para importar: só ficheiros de media, pastas com media ou links.",
        "st_dnd_missing": "Para arrastar ficheiros para a lista: pip install tkinterdnd2",
        "st_replay_loop": "Output {n}: o stream terminou ou falhou 4 vezes seguidas — reprodução parada.",
        "dlg_open_pl": "Abrir playlist",
        "dlg_save_pl": "Guardar playlist",
        "ft_media": "Multimédia",
        "ft_all": "Todos",
        "ft_playlist": "Show (*.mmp)",
        "ft_playlist_old": "Playlist antiga (*.json)",
        "ft_playlist_any": "Show (*.mmp, *.json)",
        "ask_replace": "Substituir a lista atual pela playlist selecionada?",
        "st_pl_saved": "Playlist guardada: {name}",
        "st_pl_opened": "Playlist aberta: {name} ({n} itens).",
        "st_pl_missing": "Playlist aberta: {name} ({n} itens) — {m} ficheiro(s) não encontrado(s).",
        "st_pl_open_err": "Não foi possível abrir a playlist: {err}",
        "st_pl_save_err": "Não foi possível guardar a playlist: {err}",
        "pl_invalid": "ficheiro de playlist inválido",
        "st_youtube_added": "Vídeo do YouTube adicionado: {title}",
        "st_youtube_err": "Erro ao processar YouTube: {err}",
        "st_youtube_no_title": "Não foi possível obter o título. A usar o URL.",
        "st_youtube_importing": "Importando vídeo do YouTube…",
        "st_youtube_ready": "YouTube pronto: {title}",
        "ctx_quality": "Qualidade",
        "quality_best": "Melhor qualidade",
        "shuf_win_title": "Definições de Shuffle",
        "shuf_head": "Configuração de Shuffle",
        "shuf_off": "Desligado",
        "shuf_all": "Shuffle em tudo",
        "shuf_sel": "Shuffle nos selecionados",
        "shuf_out": "Shuffle por Output",
        "shuf_output_lbl": "Output:",
        "btn_apply": "Aplicar",
        "btn_close": "Fechar",
        "ask_new_name": "Novo nome:",
        "route_win_title": "Encaminhamento de áudio — {name}",
        "route_head": "Encaminhamento de áudio para {name}",
        "route_src_dst": "Fonte \\ Destino",
        "credit": "Criado por: Tiago Ventura",
        "dur_video": "Vídeo",
        "out_vol_lbl": "Vol",
        "out_mute_lbl": "Silenciar",
        "ndi_diag_unavail": "indisponível (pip install zeroconf)",
        "ndi_diag_err": "erro {err}",
        "ndi_diag_count": "{n} anunciada(s)",
        "ndi_diag_line": "NDI {ver} · finder: {finder} · mDNS: {mdns}",
        "ndi_init_false": "ndi.initialize() devolveu False (CPU/runtime NDI não suportado?)",
        "ctx_scope_all": "Todos",
        "ctx_scope_sel": "Selecionados",
        "nx_first": "Primeiro",
        "nx_last": "Último",
        "nx_hd_none_all": "Nenhum – todos",
        "nx_hd_none_sel": "Nenhum – selecionados",
        "nx_hd_prev_all": "Anterior – todos",
        "nx_hd_prev_sel": "Anterior – selecionados",
        "nx_hd_next_all": "Seguinte – todos",
        "nx_hd_next_sel": "Seguinte – selecionados",
        "nx_hd_first_all": "Primeiro – todos",
        "nx_hd_first_sel": "Primeiro – selecionados",
        "nx_hd_last_all": "Último – todos",
        "nx_hd_last_sel": "Último – selecionados",
        "dlg_remove_items": "Remover itens",
        "ask_remove_one": "Remover «{name}» da lista?",
        "ask_remove_many": "Remover {n} itens da lista?\n\n{names}",
        "dlg_clear_list": "Limpar lista",
        "ask_clear_all": "Remover todos os {n} itens da lista?",
        "st_selected": "{n} item(ns) selecionado(s).",
        "st_hdr_applied": "Aplicado a {n} item(ns).",
        "btn_slideshow": "Slideshow",
        "btn_countdown": "Contagem decrescente",
        "ctx_options": "Opções…",
        "ctx_cd_panel": "Painel da contagem…",
        "ft_images": "Imagens",
        "ft_videos": "Vídeos",
        "ft_audio_files": "Áudio",
        "ss_win_title": "Slideshow — {name}",
        "ss_sec_media": "Imagens e vídeos",
        "ss_sec_audio": "Áudio",
        "ss_sec_opts": "Opções",
        "ss_add_media": "Adicionar ficheiros…",
        "ss_add_folder": "Adicionar pasta…",
        "ss_add_audio": "Adicionar áudio…",
        "ss_remove": "Remover",
        "ss_clear": "Limpar",
        "ss_shuffle": "Shuffle",
        "ss_img_dur": "Duração da imagem (s)",
        "ss_trans_time": "Tempo de transição (s)",
        "ss_trans_type": "Tipo de transição",
        "ss_fit_total": "Ajustar ao tempo total (s, 0 = desligado)",
        "ss_fit_audio": "Ajustar à duração do áudio",
        "ss_loop_audio": "Repetir o áudio em ciclo",
        "ss_tr_fade": "Fundido",
        "ss_tr_dissolve": "Dissolver",
        "ss_tr_wipeleft": "Cortina ←",
        "ss_tr_wiperight": "Cortina →",
        "ss_tr_slideleft": "Deslizar ←",
        "ss_tr_slideright": "Deslizar →",
        "ss_tr_circleopen": "Círculo",
        "ss_tr_none": "Corte seco",
        "ss_summary": "{n} ficheiro(s) · duração por imagem: {img} s · total: {total}",
        "ss_audio_total": "Áudio: {total}",
        "ss_btn_apply": "Aplicar",
        "ss_no_media": "Adicione pelo menos uma imagem ou vídeo.",
        "ss_info": "{name}\nSlideshow · {n} ficheiro(s)\n{state}",
        "cd_title": "Contagem decrescente — {name}",
        "cd_minutes": "Minutos",
        "cd_seconds": "Segundos",
        "cd_set": "Definir",
        "cd_start": "Iniciar",
        "cd_pause": "Pausar",
        "cd_reset": "Repor",
        "cd_mode_down": "Decrescente",
        "cd_mode_up": "Crescente",
        "cd_warn": "Aviso nos últimos (s)",
        "cd_end_text": "Texto no fim",
        "cd_ring": "Mostrar o anel",
        "cd_info": "{name}\nContagem · {total}\n{state}",
        "cd_state_running": "a correr",
        "cd_state_paused": "em pausa",
        "cd_state_done": "terminada",
        "cd_negative": "Permitir contagem negativa",
        "cd_auto_start": "Iniciar a contagem ao dar Play",
        "cd_sec_bg": "Fundo",
        "cd_bg_color": "Cor…",
        "cd_bg_image": "Imagem / vídeo…",
        "cd_bg_clear": "Sem imagem",
        "cd_sec_audio": "Áudio em zero",
        "cd_audio_pick": "Escolher…",
        "cd_audio_clear": "Remover",
        "cd_audio_none": "(nenhum)",
        "cd_sec_layout": "Posição e tamanho (arraste no quadro; roda do rato = tamanho)",
        "cd_scale": "Tamanho (%)",
        "cd_layout_reset": "Repor posição",
        "cd_audio_test": "Testar",
        "cd_audio_stop": "Parar",
        "cd_pos_x": "X (px, 0 = centro)",
        "cd_pos_y": "Y (px, 0 = centro, + para baixo)",
        "cd_center": "Centrar",

        "ss_loop": "Repetir a lista em ciclo",
        "ss_title": "SLIDESHOW",
        "ss_state_prep": "a preparar…",
        "ss_state_live": "a reproduzir",
        "ss_state_idle": "parado — começa quando é reproduzido",
        "ss_applied": "Aplicado. A reiniciar o slideshow…",
        "cd_show_name": "Mostrar o nome da contagem",
        "cd_sec_show": "Relógio",
        "cd_show_hour": "Hora",
        "cd_show_date": "Data",
        "cd_show_dow": "Dia da semana",
        "cd_sec_bar": "Barra de progresso",
        "cd_bar_none": "Nenhuma",
        "cd_bar_top": "Topo",
        "cd_bar_bottom": "Fundo",
        "cd_bar_left": "Esquerda",
        "cd_bar_right": "Direita",
        "cd_transparent": "Fundo transparente",
        "cd_transparent_hint": "Sem fundo (cor/imagem/vídeo): só o anel e os números são desenhados. Transparência real sobre outras janelas depende do Windows/mpv e não foi testada.",
        "cd_days": "segunda,terça,quarta,quinta,sexta,sábado,domingo",
        "cd_sec_text": "Texto e contador",
        "cd_font": "Tipo de letra",
        "cd_counter_size": "Tamanho do contador",
        "cd_text_size": "Tamanho do texto",
        "cd_counter_color": "Cor do contador",
        "cd_text_color": "Cor do texto",
        "cd_zero_red": "Contador fica vermelho a zero",
        "cd_bg_opacity": "Opacidade do fundo",
        "cd_audio_run": "Tocar áudio enquanto a contagem corre (repete)",
        "cd_counter_font": "Letra do contador",
        "cd_text_font": "Letra do texto",
        "cd_main": "No lugar do contador:",
        "cd_main_counter": "Contador",
        "cd_date_fmt": "Formato da data",
        "cd_date_full": "Por extenso",
        "cd_months": "janeiro,fevereiro,março,abril,maio,junho,julho,agosto,setembro,outubro,novembro,dezembro",
        "cd_full_date": "{dow}, {d} de {month} de {y}",
        "ss_res": "Resolução das transições",
        "yt_cookies_menu": "YouTube — cookies…",
        "yt_cookies_title": "Cookies do YouTube",
        "yt_bot_ask": "O YouTube pediu para confirmar que não é um robô neste computador.\n\nQuer escolher de onde vir os cookies (browser ou ficheiro cookies.txt) e tentar de novo?",
        "yt_cookies_help": "Alguns computadores/redes recebem o aviso «Sign in to confirm you’re not a bot». Dar ao yt-dlp os cookies de uma sessão YouTube já iniciada resolve. Vale para a importação e para a reprodução.",
        "yt_cookies_none": "Sem cookies",
        "yt_cookies_browser": "Do browser:",
        "yt_cookies_file": "Ficheiro cookies.txt:",
        "yt_cookies_save": "Guardar",
        "yt_cookies_note": "Dica: o Firefox é o que melhor funciona; no Chrome/Edge o Windows costuma bloquear a leitura (feche o browser ou use um ficheiro cookies.txt exportado com a extensão «Get cookies.txt LOCALLY»). Mantenha o yt-dlp atualizado: pip install -U yt-dlp",
        "yt_dpapi_msg": "O Windows não deixa o yt-dlp ler os cookies do Chrome/Edge (erro DPAPI — o Chrome/Edge atuais cifram os cookies de forma que programas externos não conseguem abrir).\n\nDuas soluções:\n1) Iniciar sessão no YouTube no Firefox e escolher «Firefox» nos cookies; ou\n2) Exportar um ficheiro cookies.txt (extensão «Get cookies.txt LOCALLY» no Chrome/Edge, com o YouTube aberto) e escolher «Ficheiro cookies.txt».\n\nVai abrir-se a janela dos cookies.",
        "yt_cookies_btn": "Cookies…",
        "yt_cookies_st_none": "sem cookies",
        "yt_cookies_st_browser": "cookies: {b}",
        "yt_cookies_st_file": "cookies: {f}",
        "yt_login_btn": "Iniciar sessão numa janela…",
        "yt_login_title": "Iniciar sessão no YouTube",
        "yt_login_help": "Abriu-se uma janela do browser (perfil temporário, separado do seu). Inicie sessão na conta Google/YouTube e aceite os avisos. Quando a sessão for detetada, os cookies são guardados e a janela fecha sozinha. Nada é guardado além dos cookies do Google/YouTube.",
        "yt_login_wait": "À espera do início de sessão…",
        "yt_login_saving": "Sessão detetada, a guardar…",
        "yt_login_now": "Já iniciei sessão",
        "yt_login_nobrowser": "Não foi encontrado o Edge, Chrome ou Brave neste computador. Use a opção do ficheiro cookies.txt.",
        "yt_login_nologin": "Não foi detetada nenhuma sessão iniciada. Inicie sessão na janela e tente de novo.",
        "yt_login_fail": "Falha ao obter os cookies: {err}",
        "yt_login_ok": "Sessão guardada ({n} cookies). Já pode importar vídeos do YouTube.",
        "yt_fmt_ask": "O yt-dlp não encontrou formatos de vídeo. Quase sempre é porque o yt-dlp está desatualizado (o YouTube muda com frequência).{rt}\n\nQuer atualizar o yt-dlp agora? (demora alguns segundos; depois feche e abra a app)",
        "yt_fmt_rt": "\n\nNota: não foi encontrado o Deno nem o Node.js neste computador; o YouTube atual precisa de um deles. Instale o Deno (winget install DenoLand.Deno) ou o Node.js.",
        "yt_upd_ok": "yt-dlp atualizado. Feche e abra a app e importe o vídeo outra vez.",
        "yt_upd_fail": "Não foi possível atualizar o yt-dlp: {err}\n\nExecute manualmente: pip install -U yt-dlp[default]",
        "yt_consent_help": "Abriu-se o YouTube numa janela do browser (perfil temporário). Clique em «Aceitar tudo». Os cookies são guardados e a janela fecha logo a seguir; ficam disponíveis para todos os vídeos seguintes.",
        "yt_consent_wait": "À espera de «Aceitar tudo»…",
        "yt_cookies_adv": "Opções…",
        "yt_ctx_cut": "Cortar",
        "yt_ctx_copy": "Copiar",
        "yt_ctx_paste": "Colar",
        "yt_ctx_all": "Selecionar tudo",
        "yt_tab_video": "Vídeo",
        "yt_tab_audio": "Apenas Áudio",
        "yt_q_video": "Qualidade do vídeo:",
        "yt_q_audio": "Qualidade do áudio:",
        "yt_q_best": "Melhor disponível",
        "yt_q_abr": "até {n} kbps",
        "yt_hw": "Hardware Decoder",
        "yt_hw_hint": "Desligado = descodificação por software (mais estável, usa mais CPU).",
    },
    "en": {
        "app_title": "Multi-Monitor Player",
        "welcome_title": "Welcome to Multi-Monitor Player",
        "welcome_new": "New Show",
        "welcome_load": "Load Show",
        "welcome_last": "Open Last Show",
        "out_window": "Output {n}",
        "out_label": "Output {n}",
        "identify": "Identify Screens",
        "add_output": "Add Output",
        "out_on": "Turn on",
        "out_off": "Turn off",
        "pl_open": "Open…",
        "pl_save": "Save",
        "pl_save_as": "Save as…",
        "screen_label": "Screen {n} — {w}×{h}{tag}",
        "screen_short": "Screen {n} — {w}×{h}",
        "screen_n": "Screen {n}",
        "screen_none": "None",
        "tag_primary": " · primary",
        "pick_taken": " — in use by {o}", "pick_taken_mv": " — used by the Multiview",
        "btn_add_files": "＋ Files",
        "btn_add_folder": "＋ Folder",
        "btn_youtube": "YouTube",
        "btn_logins": "Logins", "lg_title": "Logins — YouTube and Spotify accounts",
        "lg_help": "Sign in to your accounts. The account name and picture are kept and the session is used by the imports: YouTube then uses your account (age-restricted videos, private lists, no «not a robot» warning) and Spotify reads your private playlists, Liked Songs and full lists.",
        "lg_login": "Sign in…", "lg_relogin": "Switch account…", "lg_logout": "Sign out", "lg_none": "Not signed in",
        "lg_signed_in": "Signed in", "lg_premium": "Premium", "lg_free": "Free", "lg_unknown_name": "(connected account)",
        "lg_expired": "The Spotify session expired — sign in again in Logins.",
        "lg_ok_youtube": "YouTube sign-in saved. Videos are now opened with your account.",
        "lg_ok_spotify": "Spotify sign-in saved. Imports now use your account.",
        "lg_out_ask": "Sign out and delete the stored data of this account on this computer?",
        "lg_note": "The session is kept on this computer only (user folder). Spotify's audio stays protected (DRM), even with Premium: songs still play from YouTube. Your password is never read or stored.",
        "lg_using": "Account: {name}", "lg_none_hint": "No account (Logins button, next to «Created by»).",
        "lg_my_playlists": "My playlists…", "lg_liked": "Liked Songs", "lg_via": "  ·  read from your account",
        "sp_login_help": "A browser window opened (temporary profile, separate from yours). Sign in to Spotify. When the sign-in is detected it is saved and the window closes by itself. The password is not read or stored.",
        "pl_saved_ok": "Preset “{name}” was saved successfully.\n\n{path}",
        "btn_spotify": "Spotify", "dlg_spotify": "Import from Spotify",
        "ask_spotify_url": "Paste the Spotify link (track, album or playlist):",
        "sp_hint": "Spotify's audio is protected: the app reads the song names and plays them from YouTube (same title, artist and length).",
        "sp_not_link": "This does not look like a Spotify link (open.spotify.com/track/…, /album/… or /playlist/…).",
        "sp_reading": "Reading Spotify…", "sp_found": "{title} — {n} song(s). Pick the ones you want (all selected).",
        "sp_err": "Could not read this link: {err}", "sp_import": "Import", "sp_video": "Import with video (music video) instead of audio only",
        "st_sp_searching": "Spotify: searching YouTube… {k}/{n}", "st_sp_done": "Spotify: {n} song(s) imported from “{title}”",
        "st_sp_done_missing": "Spotify: {n} imported, {m} not found on YouTube (see player.log)",
        "btn_audio_routing": "Audio routing",
        "btn_play": "Play",
        "btn_stop": "Stop",
        "btn_remove": "Remove",
        "btn_clear": "Clear list",
        "btn_up": "▲ Move up",
        "btn_down": "▼ Move down",
        "col_pos": "#",
        "col_nome": "File",
        "col_out": "Output",
        "col_next": "Next",
        "col_rep": "Repeat",
        "col_fade_in": "Fade In",
        "col_fade_out": "Fade Out",
        "col_mute": "Mute",
        "col_ms": "Before end (ms)",
        "col_f1": "Freeze first",
        "col_fl": "Freeze last",
        "col_wn": "With next",
        "col_sim": "Play Simultaneously",
        "col_sched": "Schedule time",
        "col_dow": "Day of week",
        "dow_title": "Days of the week", "dow_all": "Every day", "dow_hint": "The item only starts on the ticked days (none ticked = every day).",
        "dow_names": "Mon Tue Wed Thu Fri Sat Sun", "btn_ok": "OK",
        "mi_video": "Video files…", "mi_audio": "Audio files…", "mi_image": "Image files…",
        "ft_video": "Video", "ft_audio": "Audio", "ft_imgs": "Image",
        "dlg_add_video": "Add videos", "dlg_add_audio": "Add audio files", "dlg_add_image": "Add images",
        "col_dur": "Duration",
        "col_trecho": "Range (in → out)",
        "nx_none": "None",
        "nx_prev": "Previous",
        "nx_next": "Next",
        "nx_menu_none": "None (end)",
        "ctx_duplicate": "Duplicate",
        "ctx_remove": "Remove",
        "ctx_rename": "Rename",
        "ctx_play": "Play",
        "ctx_output": "Output",
        "ctx_aspect": "Aspect",
        "asp_fit": "Fit (keep aspect ratio)",
        "asp_fill": "Fill (crop the overflow)",
        "asp_stretch": "Stretch (distort)",
        "asp_orig": "Original size (1:1)",
        "asp_a169": "Force 16:9",
        "asp_a43": "Force 4:3",
        "aspect_line": "Aspect: {mode}",
        "hint": "Red = playing · Blue = selected. Ctrl/Shift+click or Ctrl+A selects several items. Drag rows to reorder and header borders to resize columns. Right-click the Fade/Freeze/Next headers to apply to all or to the selected items.",
        "opt_shuffle": "Shuffle",
        "opt_blackout": "Black screen (B)",
        "opt_mute": "Mute (M)",
        "opt_volume": "Volume",
        "opt_theme_dark": "Dark Theme",
        "opt_theme_light": "Light Theme",
        "mon_title": "Output monitor",
        "mon_enabled": "Enable monitor",
        "mon_caption": "current / total   ·   remaining",
        "res_label": "Resolution",
        "fps_label": "FPS",
        "opt_original": "Original",
        "ip_header": "SELECTED ITEM",
        "ip_placeholder": "Select an item from the list",
        "ip_unavailable": "Preview unavailable",
        "btn_mark_in": "[ In (I)",
        "btn_mark_out": "Out ] (O)",
        "btn_marks_clear": "Clear",
        "info_select": "Select an item from the list.",
        "info_loading": "Loading…",
        "info_load_err": "Error loading: {err}",
        "inf_video": "Video",
        "inf_image": "Image",
        "inf_no_video": "No video",
        "inf_audio": "Audio",
        "inf_no_audio": "No audio",
        "inf_channels": "{n} channels",
        "inf_container": "Container",
        "inf_duration": "Total duration",
        "tr_whole": "Range: whole item (no marks)",
        "tr_range": "Range: {a} → {b}{extra}",
        "tr_len": "  ·  length {d}",
        "tr_end": "end",
        "st_ready": "Ready. Add files and outputs, choose the screens and press ▶.",
        "st_ready_short": "Ready.",
        "st_overlap": "Warning: Output {o} is on the same screen as this interface.",
        "st_screens_updated": "Screen list updated.",
        "st_screen_gone": "The screen of Output {o} is no longer available — using Screen {n}.",
        "st_screen_gone_none": "The screen of Output {o} is no longer available and no other is free.",
        "st_no_screen": "Choose a screen for Output {n} before opening the fullscreen.",
        "st_out_nowindow": "Output {n}: playing without a window. Use «{btn}» to open the fullscreen.",
        "st_output_lost": "Output {n} was closed.",
        "st_mirror_fail": "Could not create the monitor of Output {n}: {err}",
        "st_ip_fail": "Could not create the item preview: {err}",
        "st_playing": "Playing {i}/{n} (Output {o}): {name}",
        "st_error": "Error: {err}",
        "st_paused_first": "Paused on the first frame — press ▶ to start.",
        "st_wn_same_output": "«With next»: the next item uses the same output — it was not started.",
        "st_marks_select": "Select a video/audio item to set marks.",
        "st_in_before_out": "The in point must be before the out point.",
        "st_out_after_in": "The out point must be after the in point.",
        "st_in_set": "In point set at {t}.",
        "st_out_set": "Out point set at {t}.",
        "st_end_of_file": "end of file",
        "st_marks_cleared": "Marks cleared.",
        "st_marks_saved": "Marks saved — they apply the next time the item starts.",
        "st_relinked": "{n} item(s) pointed to the removed one and now use «Next».",
        "st_duplicated": "{n} item(s) duplicated.",
        "st_aspect_set": "Aspect «{mode}» applied to {n} item(s).",
        "st_output_set": "{n} item(s) sent to Output {o}.",
        "st_shuffle_on": "Shuffle on.",
        "st_shuffle_off": "Shuffle off.",
        "st_file_missing": "File not found: {name}",
        "st_out_missing": "Output {n} does not exist — add it or choose another output for this item.",
        "st_out_removed": "{name} removed.",
        "st_sched_invalid": "Invalid time. Use HH:MM:SS.",
        "st_dur_invalid": "Invalid duration. Use H:MM:SS.",
        "outs_empty": "No outputs.\nPress «Add Output».",
        "outs_none": "No outputs — add one",
        "dlg_remove_out": "Remove output",
        "ask_remove_out": "«{name}» is on. Remove it anyway?",
        "dlg_out_err": "Error starting Output {n}",
        "dlg_add_files": "Add files",
        "dlg_add_folder": "Add folder",
        "dlg_youtube": "Add YouTube video",
        "btn_rtmp": "RTMP Input",
        "dlg_rtmp": "Add RTMP Input",
        "ask_rtmp_key": "Input name (it is also the RTMP key):",
        "ask_rtmp_rename": "New key (input name):",
        "rtmp_title": "RTMP INPUT",
        "rtmp_address": "Address",
        "rtmp_key": "Key",
        "rtmp_waiting": "Waiting for signal…",
        "rtmp_bad_key": "Invalid key received: {rk}",
        "rtmp_err": "Error: {err}",
        "rtmp_state_live": "SIGNAL OK",
        "rtmp_no_ffmpeg": "ffmpeg not found. Put ffmpeg next to the program or on the PATH.",
        "st_rtmp_added": "RTMP Input created — address: {addr}  ·  key: {rk}",
        "st_rtmp_locked": "The key cannot be changed while the input is receiving signal.",
        "btn_ndi": "NDI Input",
        "btn_ndi_out": "NDI output",
        "ndi_quality": "NDI quality", "btn_lock_kb": "🔒 Lock keyboard", "btn_lock_kb_on": "🔓 Keyboard locked",
        "quality_label": "Quality", "big_title": "Bigger window", "btn_multiview": "Multiview", "btn_composite": "Composite Output", "btn_sync": "Sync", "sy_title": "Main / Backup synchronisation", "sy_role": "This computer is:", "sy_host": "Main IP (Backup only):", "sy_port": "Port:", "sy_key": "Key (same on both):", "sy_fail": "Backup takes over after (s):", "sy_start": "Start", "sy_stop": "Stop", "sy_promote": "Promote to Main", "sy_hint": "Both computers must be on the same network and use the same key. Allow the chosen TCP port and the next one in the Main's firewall. The link is not encrypted (local network use). On the Backup, playback actions made by hand are undone by the Main; its screens, audio devices and Multiview / Composite stay the Backup's own.", "sy_st_main": "Main active on port {port}. Backups connected: {n}", "sy_st_denied": "The Main refused the connection: wrong key", "sy_st_conn": "Connected to Main {host}", "sy_st_wait": "Connecting to Main {host}… {err}", "sy_st_applied": "Last show received at {t} (times: {n})", "sy_st_files": "Copying files from the Main: {done} / {total} MB ({left} left)", "ndi_preview": "Preview", "ndi_preview_pick": "Click a source in the list to see it here", "ndi_preview_wait": "Connecting to the NDI source…", "ndi_preview_err": "This source could not be received", "ndi_ctx_inputs": "NDI Inputs", "ndi_ctx_none": "(no NDI sources on the network)", "ndi_ctx_refresh": "Search again", "ndi_ctx_window": "Import window…", "sy_st_off": "Synchronisation is off", "sy_st_bk_files": "The Backup is copying files: {done} / {total} MB", "sy_st_sent": "Show sent (version {n}, {f} files)", "sy_st_promoted": "This computer took over as Main", "sy_err_listen": "Sync: could not open the port: {err}", "sy_err_host": "Sync: enter the Main's IP", "sy_promoted": "Backup promoted to Main", "sy_promoted_msg": "The Main stopped answering. This computer took over as Main and keeps playing from where it was.",  "ar_devices": "Audio devices (by system)", "ar_pa_missing": "PortAudio routing: device or sounddevice/ffmpeg unavailable (mpv plays normally)", "ar_pa_failed": "PortAudio routing failed: {err}", "ar_pa_live": "PortAudio engine on: {n} channels at {r} Hz, device latency ~{ms} ms", "ar_pa_idle": "PortAudio engine: press Apply to open the device", "ar_asio_nosd": "needs «pip install sounddevice»", "ar_asio_nodrv": "no ASIO driver installed",  "ar_src_ch": "Source channels:", "ar_dst_ch": "Device channels:", "ar_corner": "Device ↓  /  Source →", "ar_ch": "Channel", "ar_default_dev": "System default device", "ar_current": "Device: {dev}", "ar_live": "Playing: the device opened with {n} channels at {r} Hz", "ar_idle": "(the output is not playing: the device has not been opened yet)", "ar_na": "not available in this libmpv", "ar_identity": "1:1", "ar_spread": "Repeat", "ar_mono_all": "Mono → all", "ar_clear": "Clear", "ar_refresh": "Refresh list", "ar_hint": "Click a cell to connect a source channel (column) to a device channel (row); several channels into one destination are mixed. NDI output is not affected.",  "co_title": "Composite Output", "co_res": "Resolution", "co_apply": "Apply", "co_unite_lbl": "Single canvas:", "co_unite": "Merge outputs", "co_arr_h": "Horizontal", "co_arr_v": "Vertical", "co_arr_g": "Grid", "co_hint": "Drag outputs/inputs onto the canvas · blue dots = corner pin · orange corner resizes · right click removes", "co_crop": "Crop %", "co_rot": "Rotation", "co_flip_h": "Mirror horizontal", "co_flip_v": "Mirror vertical", "co_fit": "Picture fit", "co_fit_fit": "Fit", "co_fit_stretch": "Stretch", "co_fit_fill": "Fill", "co_blend": "Edge blend %", "co_l": "Left", "co_t": "Top", "co_r": "Right", "co_b": "Bottom", "co_pin_reset": "Reset corner pin", "co_remove": "Remove slice", "co_none_sel": "(no slice selected)", "st_co_on": "Composite output on", "st_co_no_screen": "Composite output: pick a screen first", "co_unite_none": "No outputs to merge", "mv_title": "Multiview Output", "mv_screen": "Screen", "mv_clear": "Clear tiles", "yt_press_play_short": "Press ▶ in the preview to open the video before setting IN / OUT", "yt_press_play": "Press ▶ to open the video (time bar and markers)", "ytl_title": "YouTube live", "ytl_waiting": "Connecting to the live stream…", "mv_quality": "Tile quality", "mv_q_auto": "Auto", "mv_hint": "Drag outputs/inputs onto the screen · drag the orange corner to resize · right click removes", "mv_outputs": "Outputs", "mv_inputs": "Inputs (list)", "mv_remove": "Remove tile", "st_mv_taken": "Warning: this screen is used by {o}", "st_mv_no_screen": "Multiview: pick a screen first", "st_mv_on": "Multiview on",
        "ss_autostart": "Auto-start Slideshow when Play", "ss_apply_close": "Apply and Close", "ctx_ss_start": "Start slideshow", "st_mv_same": "Warning: this is the app's own screen; the Multiview sits behind the app (double-click or Esc on the Multiview turns it off)",
        "mv_copy_size": "Copy size", "mv_paste_size": "Paste size", "mv_copy_pos": "Copy position", "mv_paste_pos": "Paste position",
        "btn_capture": "Capture", "cap_kind_video": "Video device", "cap_kind_audio": "Audio device", "cap_kind_screen": "Screen capture", "cap_kind_window": "Window capture", "cap_kind_lavfi": "Test source", "cap_mi_video": "Video device (camera / card)…", "cap_mi_audio": "Audio device…", "cap_mi_screen": "Screen capture…", "cap_mi_window": "Window capture…", "cap_title": "CAPTURE", "cap_waiting": "Starting the capture…", "cap_loading": "Looking for devices…", "cap_none": "No device found (the list uses ffmpeg and Windows DirectShow).", "cap_device": "Device", "cap_mode": "Mode", "cap_auto": "Automatic", "cap_add": "Add", "cap_refresh": "Refresh", "cap_col_dev": "Device", "cap_col_ch": "Channels", "cap_col_bits": "Bits", "cap_col_rate": "Sample rate", "cap_hint_audio": "To capture the system sound pick «Stereo Mix» or «virtual-audio-capturer» if it exists (Windows → Sound → Recording → show disabled devices).", "cap_screen_all": "All screens", "cap_windows_only": "Screen and window capture only work on Windows.", "st_cap_added": "Capture «{name}» added",
        "cap_apply": "Apply", "cap_fps_range": "This mode only accepts {lo} to {hi} fps.", "cap_mouse": "Show mouse", "cap_dev_opts": "Device options…", "cap_preview": "Preview",
        "cap_prev_wait": "Starting the preview…", "cap_prev_live": "Source is live.", "cap_prev_err": "Error: {err}", "cap_prev_nolib": "Preview unavailable (libmpv).",
        "cap_fps_bad": "Invalid FPS (use a number between 1 and 240, e.g. 25 or 29.97).", "cap_dev_opts_busy": "Opening the device options…",
        "cap_dev_opts_fail": "Could not open the options: {err}", "st_cap_applied": "Capture updated: {name}", "st_ndi_applied": "NDI source updated: {name}",
        "rtmp_dlg_key": "Input name (RTMP key):", "rtmp_dlg_url": "Address for the sender (OBS, etc.):", "rtmp_dlg_hint": "In the sender use a 1 s keyframe interval and a low-latency profile (e.g. tune zerolatency, no B-frames).",
        "st_rtmp_applied": "RTMP key updated: {name}", "ndi_edit_hint": "Choose the new NDI source:", "btn_apply_close": "Apply and close",
        "btn_group": "＋ Group", "ctx_group_add": "Add to group", "ctx_group_none": "No group", "ctx_group_ungroup": "Ungroup (keep items)", "ctx_group_toggle": "Expand / collapse", "ask_group_name": "Group name", "grp_default": "Group", "st_group_made": "Group «{name}» created ({n} items)",
        "ndi_low_bw": "Low Bandwidth",
        "ndi_refresh": "Refresh list",
        "ndi_none": "(no NDI sources found)",
        "ndi_title": "NDI INPUT",
        "ndi_source": "Source",
        "ndi_waiting": "Waiting for NDI signal…",
        "ndi_missing_lib": "NDI unavailable. Install: pip install ndi-python numpy",
        "ndi_out_missing": "NDI output requires: pip install ndi-python numpy",
        "st_ndi_out_err": "NDI output '{name}' stopped: {err}",
        "st_ndi_search": "Looking for NDI sources…",
        "st_ndi_added": "NDI Input created — source: {src}",
        "st_ndi_out_on": "NDI output on: {name} (sends while the output is active)",
        "st_ndi_out_off": "NDI output off: {name}",
        "btn_import": "Import ▾",
        "ndi_win_hint": "NDI sources available on the network:",
        "ndi_win_searching": "Looking for NDI sources…",
        "ndi_win_found": "{n} source(s) found.",
        "ndi_win_none": "No NDI sources found.",
        "ndi_win_none_hint": "No NDI sources found. If the NDI app is already open: install «pip install zeroconf» (alternative discovery), check that this PC and the source are on the same network, or use «Manual source…».",
        "ndi_win_pick": "Select one or more sources in the list.",
        "ndi_win_imported": "{n} source(s) imported.",
        "ndi_btn_import": "Import",
        "ndi_btn_close": "Close",
        "ndi_manual": "Manual source…",
        "ask_ndi_manual": "Full NDI source name (e.g. PC (Screen Capture)) or the IP address (e.g. 192.168.1.10:5961):",
        "ndi_err_item": "NDI error: {err}",
        "ask_youtube_url": "Paste the YouTube video URL:",
        "yt_loading": "Fetching title and thumbnail…",
        "yt_no_title": "(title unavailable)",
        "yt_btn_import": "Import",
        "yt_btn_import_n": "Import ({n})",
        "yt_btn_cancel": "Cancel",
        "yt_opt_video": "Import only this video",
        "yt_opt_playlist": "Import the entire playlist",
        "yt_pl_loading": "Loading playlist…",
        "yt_pl_title": "Playlist: {title}",
        "yt_pl_count": "{n} videos will be imported as separate items.",
        "yt_pl_err": "Could not read the playlist: {err}",
        "yt_pl_empty": "the playlist is empty or private",
        "st_youtube_playlist": "Playlist «{title}»: {n} videos imported (details fill in the background).",
        "st_dropped": "{n} file(s) added by drag and drop ({skipped} ignored).",
        "st_drop_none": "Nothing to import: only media files, folders with media, or links.",
        "st_dnd_missing": "To drag files onto the list: pip install tkinterdnd2",
        "st_replay_loop": "Output {n}: the stream ended or failed 4 times in a row — playback stopped.",
        "dlg_open_pl": "Open playlist",
        "dlg_save_pl": "Save playlist",
        "ft_media": "Media",
        "ft_all": "All files",
        "ft_playlist": "Show (*.mmp)",
        "ft_playlist_old": "Old playlist (*.json)",
        "ft_playlist_any": "Show (*.mmp, *.json)",
        "ask_replace": "Replace the current list with the selected playlist?",
        "st_pl_saved": "Playlist saved: {name}",
        "st_pl_opened": "Playlist opened: {name} ({n} items).",
        "st_pl_missing": "Playlist opened: {name} ({n} items) — {m} file(s) not found.",
        "st_pl_open_err": "Could not open the playlist: {err}",
        "st_pl_save_err": "Could not save the playlist: {err}",
        "pl_invalid": "invalid playlist file",
        "st_youtube_added": "YouTube video added: {title}",
        "st_youtube_err": "Error processing YouTube: {err}",
        "st_youtube_no_title": "Could not get title. Using URL.",
        "st_youtube_importing": "Importing YouTube video…",
        "st_youtube_ready": "YouTube ready: {title}",
        "ctx_quality": "Quality",
        "quality_best": "Best quality",
        "shuf_win_title": "Shuffle Settings",
        "shuf_head": "Shuffle Settings",
        "shuf_off": "Off",
        "shuf_all": "Shuffle All",
        "shuf_sel": "Shuffle Selected",
        "shuf_out": "Shuffle by Output",
        "shuf_output_lbl": "Output:",
        "btn_apply": "Apply",
        "btn_close": "Close",
        "ask_new_name": "New name:",
        "route_win_title": "Audio routing — {name}",
        "route_head": "Audio routing for {name}",
        "route_src_dst": "Source \\ Destination",
        "credit": "Created by: Tiago Ventura",
        "dur_video": "Video",
        "out_vol_lbl": "Vol",
        "out_mute_lbl": "Mute",
        "ndi_diag_unavail": "unavailable (pip install zeroconf)",
        "ndi_diag_err": "error {err}",
        "ndi_diag_count": "{n} announced",
        "ndi_diag_line": "NDI {ver} · finder: {finder} · mDNS: {mdns}",
        "ndi_init_false": "ndi.initialize() returned False (CPU/NDI runtime not supported?)",
        "ctx_scope_all": "All",
        "ctx_scope_sel": "Selected",
        "nx_first": "First",
        "nx_last": "Last",
        "nx_hd_none_all": "None All",
        "nx_hd_none_sel": "None Selected",
        "nx_hd_prev_all": "Previous All",
        "nx_hd_prev_sel": "Previous Selected",
        "nx_hd_next_all": "Next All",
        "nx_hd_next_sel": "Next Selected",
        "nx_hd_first_all": "First All",
        "nx_hd_first_sel": "First Selected",
        "nx_hd_last_all": "Last All",
        "nx_hd_last_sel": "Last Selected",
        "dlg_remove_items": "Remove items",
        "ask_remove_one": "Remove “{name}” from the list?",
        "ask_remove_many": "Remove {n} items from the list?\n\n{names}",
        "dlg_clear_list": "Clear list",
        "ask_clear_all": "Remove all {n} items from the list?",
        "st_selected": "{n} item(s) selected.",
        "st_hdr_applied": "Applied to {n} item(s).",
        "btn_slideshow": "Slideshow",
        "btn_countdown": "Countdown",
        "ctx_options": "Options…",
        "ctx_cd_panel": "Countdown panel…",
        "ft_images": "Images",
        "ft_videos": "Videos",
        "ft_audio_files": "Audio",
        "ss_win_title": "Slideshow — {name}",
        "ss_sec_media": "Images and videos",
        "ss_sec_audio": "Audio",
        "ss_sec_opts": "Options",
        "ss_add_media": "Add files…",
        "ss_add_folder": "Add folder…",
        "ss_add_audio": "Add audio…",
        "ss_remove": "Remove",
        "ss_clear": "Clear",
        "ss_shuffle": "Shuffle",
        "ss_img_dur": "Image duration (s)",
        "ss_trans_time": "Transition time (s)",
        "ss_trans_type": "Transition type",
        "ss_fit_total": "Total time fit (s, 0 = off)",
        "ss_fit_audio": "Audio time fit",
        "ss_loop_audio": "Loop audio",
        "ss_tr_fade": "Fade",
        "ss_tr_dissolve": "Dissolve",
        "ss_tr_wipeleft": "Wipe left",
        "ss_tr_wiperight": "Wipe right",
        "ss_tr_slideleft": "Slide left",
        "ss_tr_slideright": "Slide right",
        "ss_tr_circleopen": "Circle open",
        "ss_tr_none": "Cut",
        "ss_summary": "{n} file(s) · image duration: {img} s · total: {total}",
        "ss_audio_total": "Audio: {total}",
        "ss_btn_apply": "Apply",
        "ss_no_media": "Add at least one image or video.",
        "ss_info": "{name}\nSlideshow · {n} file(s)\n{state}",
        "cd_title": "Countdown — {name}",
        "cd_minutes": "Minutes",
        "cd_seconds": "Seconds",
        "cd_set": "Set",
        "cd_start": "Start",
        "cd_pause": "Pause",
        "cd_reset": "Reset",
        "cd_mode_down": "Count down",
        "cd_mode_up": "Count up",
        "cd_warn": "Warning in the last (s)",
        "cd_end_text": "Text at the end",
        "cd_ring": "Show the ring",
        "cd_info": "{name}\nCountdown · {total}\n{state}",
        "cd_state_running": "running",
        "cd_state_paused": "paused",
        "cd_state_done": "finished",
        "cd_negative": "Allow negative counting",
        "cd_auto_start": "Start counting on Play",
        "cd_sec_bg": "Background",
        "cd_bg_color": "Color…",
        "cd_bg_image": "Image / video…",
        "cd_bg_clear": "No image",
        "cd_sec_audio": "Audio at zero",
        "cd_audio_pick": "Choose…",
        "cd_audio_clear": "Remove",
        "cd_audio_none": "(none)",
        "cd_sec_layout": "Position and size (drag in the frame; mouse wheel = size)",
        "cd_scale": "Size (%)",
        "cd_layout_reset": "Reset layout",
        "cd_audio_test": "Test",
        "cd_audio_stop": "Stop",
        "cd_pos_x": "X (px, 0 = center)",
        "cd_pos_y": "Y (px, 0 = center, + down)",
        "cd_center": "Center",

        "ss_loop": "Loop the list",
        "ss_title": "SLIDESHOW",
        "ss_state_prep": "preparing…",
        "ss_state_live": "playing",
        "ss_state_idle": "idle — starts when played",
        "ss_applied": "Applied. Restarting the slideshow…",
        "cd_show_name": "Show the countdown name",
        "cd_sec_show": "Clock",
        "cd_show_hour": "Hour",
        "cd_show_date": "Date",
        "cd_show_dow": "Day of week",
        "cd_sec_bar": "Progress bar",
        "cd_bar_none": "None",
        "cd_bar_top": "Top",
        "cd_bar_bottom": "Bottom",
        "cd_bar_left": "Left",
        "cd_bar_right": "Right",
        "cd_transparent": "Transparent background",
        "cd_transparent_hint": "No background (colour/image/video): only the ring and digits are drawn. True see-through over other windows depends on Windows/mpv and is untested.",
        "cd_days": "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday",
        "cd_sec_text": "Text and counter",
        "cd_font": "Font",
        "cd_counter_size": "Counter size",
        "cd_text_size": "Text size",
        "cd_counter_color": "Counter colour",
        "cd_text_color": "Text colour",
        "cd_zero_red": "Counter turns red at zero",
        "cd_bg_opacity": "Background opacity",
        "cd_audio_run": "Play audio while the counter runs (loops)",
        "cd_counter_font": "Counter font",
        "cd_text_font": "Text font",
        "cd_main": "In the counter position:",
        "cd_main_counter": "Counter",
        "cd_date_fmt": "Date format",
        "cd_date_full": "Full text",
        "cd_months": "January,February,March,April,May,June,July,August,September,October,November,December",
        "cd_full_date": "{dow}, {month} {d}, {y}",
        "ss_res": "Transition resolution",
        "yt_cookies_menu": "YouTube cookies…",
        "yt_cookies_title": "YouTube cookies",
        "yt_bot_ask": "YouTube asked to confirm you are not a bot on this computer.\n\nChoose where the cookies come from (a browser or a cookies.txt file) and try again?",
        "yt_cookies_help": "Some computers/networks get «Sign in to confirm you’re not a bot». Giving yt-dlp the cookies of a YouTube session that is already signed in fixes it. It is used for the import and for playback.",
        "yt_cookies_none": "No cookies",
        "yt_cookies_browser": "From browser:",
        "yt_cookies_file": "cookies.txt file:",
        "yt_cookies_save": "Save",
        "yt_cookies_note": "Tip: Firefox works best; Chrome/Edge are often locked by Windows (close the browser, or use a cookies.txt exported with the «Get cookies.txt LOCALLY» extension). Keep yt-dlp up to date: pip install -U yt-dlp",
        "yt_dpapi_msg": "Windows does not let yt-dlp read Chrome/Edge cookies (DPAPI error — current Chrome/Edge encrypt cookies in a way outside programs cannot open).\n\nTwo fixes:\n1) Sign in to YouTube in Firefox and choose «Firefox» in the cookies window; or\n2) Export a cookies.txt file (the «Get cookies.txt LOCALLY» extension in Chrome/Edge, with YouTube open) and choose «cookies.txt file».\n\nThe cookies window will open now.",
        "yt_cookies_btn": "Cookies…",
        "yt_cookies_st_none": "no cookies",
        "yt_cookies_st_browser": "cookies: {b}",
        "yt_cookies_st_file": "cookies: {f}",
        "yt_login_btn": "Sign in with a window…",
        "yt_login_title": "Sign in to YouTube",
        "yt_login_help": "A browser window opened (temporary profile, separate from yours). Sign in to your Google/YouTube account and accept any prompts. When the sign-in is detected the cookies are saved and the window closes by itself. Only Google/YouTube cookies are kept.",
        "yt_login_wait": "Waiting for sign-in…",
        "yt_login_saving": "Signed in, saving…",
        "yt_login_now": "I'm signed in",
        "yt_login_nobrowser": "Edge, Chrome or Brave was not found on this computer. Use the cookies.txt file option.",
        "yt_login_nologin": "No signed-in session was detected. Sign in in the window and try again.",
        "yt_login_fail": "Could not read the cookies: {err}",
        "yt_login_ok": "Sign-in saved ({n} cookies). You can import YouTube videos now.",
        "yt_fmt_ask": "yt-dlp found no video formats. This is almost always an outdated yt-dlp (YouTube changes often).{rt}\n\nUpdate yt-dlp now? (takes a few seconds; then close and reopen the app)",
        "yt_fmt_rt": "\n\nNote: neither Deno nor Node.js was found on this computer; current YouTube needs one of them. Install Deno (winget install DenoLand.Deno) or Node.js.",
        "yt_upd_ok": "yt-dlp updated. Close and reopen the app, then import the video again.",
        "yt_upd_fail": "Could not update yt-dlp: {err}\n\nRun manually: pip install -U yt-dlp[default]",
        "yt_consent_help": "YouTube opened in a browser window (temporary profile). Click “Accept all”. The cookies are saved and the window closes right after; they are then used for every later video.",
        "yt_consent_wait": "Waiting for “Accept all”…",
        "yt_cookies_adv": "Options…",
        "yt_ctx_cut": "Cut",
        "yt_ctx_copy": "Copy",
        "yt_ctx_paste": "Paste",
        "yt_ctx_all": "Select all",
        "yt_tab_video": "Video",
        "yt_tab_audio": "Audio only",
        "yt_q_video": "Video quality:",
        "yt_q_audio": "Audio quality:",
        "yt_q_best": "Best available",
        "yt_q_abr": "up to {n} kbps",
        "yt_hw": "Hardware Decoder",
        "yt_hw_hint": "Off = software decoding (more stable, uses more CPU).",
    },
}

def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}

def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass

def fmt(seconds):
    seconds = int(max(0, seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def fmt_ms(seconds):
    ms = int(round(max(0.0, seconds or 0.0) * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h}:{m:02d}:{s:02d}.{ms:03d}" if h else f"{m}:{s:02d}.{ms:03d}"

def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024

def num(value, default=0.0, lo=None, hi=None):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    if x != x:
        return default
    if lo is not None:
        x = max(lo, x)
    if hi is not None:
        x = min(hi, x)
    return x

def geo(mo):
    return (mo.x, mo.y, mo.width, mo.height)

def parse_canvas(text):
    """'1920x1080' -> (1920, 1080); 'Original' / invalid -> None."""
    t = (text or "").lower().replace("×", "x").replace(" ", "")
    if "x" not in t:
        return None
    try:
        w, h = (int(p) for p in t.split("x", 1))
    except ValueError:
        return None
    return (w, h) if w > 0 and h > 0 else None

def parse_fps(text):
    """'25' / '29,97' -> float; 'Original' / invalid -> None."""
    try:
        v = float((text or "").strip().replace(",", "."))
    except ValueError:
        return None
    return v if 0 < v <= 240 else None

def build_vf(canvas, mode, fps, div=1):
    """mpv --vf string for an output.

    The chosen resolution is a CANVAS (square pixels, W:H aspect). The item's aspect mode decides how
    the picture is placed on it (fit / fill / stretch / original size / forced 16:9 or 4:3), so the
    canvas is always delivered at exactly W:H and the fullscreen window only has to show it
    undistorted. No canvas (Original resolution) -> only the optional FPS filter.
    """
    f = []
    if canvas:
        w, h = canvas
        if mode in ("a169", "a43"):
            # exact 16:9 / 4:3 frame (multiples of 32x18 / 8x6, rounded up so nothing is lost)
            n = "trunc((ih+17)/18)" if mode == "a169" else "trunc((ih+5)/6)"
            wk, hk = (32, 18) if mode == "a169" else (8, 6)
            f += [f"scale=w={n}*{wk}:h={n}*{hk}", "setsar=1"]             # force the display aspect
        else:
            f += ["scale=w=trunc(iw*sar/2)*2:h=ih", "setsar=1"]            # non-square pixels -> square
        if mode == "stretch":
            f.append(f"scale={w}:{h}:flags=lanczos")
        elif mode == "fill":
            f += [f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos", f"crop={w}:{h}"]
        elif mode == "orig":
            # min(a,b) written as (a+b-abs(a-b))/2 so no comma has to be escaped
            f += [f"crop=w=(iw+{w}-abs(iw-{w}))/2:h=(ih+{h}-abs(ih-{h}))/2",
                  f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"]
        else:                                                              # fit, a169, a43
            f += [f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos",
                  f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"]
        f.append("setsar=1")
    elif div > 1:                                                          # «Original» resolution at 1/2, 1/4 quality
        f += [f"scale=w=trunc(iw*sar/{div}/2)*2:h=trunc(ih/{div}/2)*2", "setsar=1"]
    if fps:
        f.append(f"fps={fps:g}:round=near")
    return f"lavfi=[{','.join(f)}]" if f else ""

def clean_text(text):
    """Strip control characters and characters Tk cannot draw (outside the BMP, e.g. emoji); collapse whitespace."""
    out = "".join((" " if (ch < " " or ch == "\x7f") else ch) for ch in str(text) if ord(ch) <= 0xFFFF)
    return " ".join(out.split())


_YT_ID = re.compile(r"(?:youtu\.be/|youtube\.com/(?:watch\?(?:[^#]*&)?v=|shorts/|live/|embed/)|youtube-nocookie\.com/embed/)"
                    r"([A-Za-z0-9_-]{11})")


def youtube_video_id(url):
    m = _YT_ID.search(url or "")
    return m.group(1) if m else None


_YT_LIST = re.compile(r"[?&]list=([A-Za-z0-9_-]+)")


def youtube_playlist_id(url):
    url = url or ""
    m = _YT_LIST.search(url)
    return m.group(1) if (m and ("youtube.com" in url or "youtu.be" in url)) else None


def youtube_watch_url(vid):
    return f"https://www.youtube.com/watch?v={vid}"


def youtube_playlist_url(pid):
    return f"https://www.youtube.com/playlist?list={pid}"


def fetch_youtube_playlist(url):
    """{'title', 'entries': [{'id', 'title', 'url', 'duration'}]} for the playlist a link belongs to.
    Flat listing (titles only): fast even for long playlists. Private / deleted videos are left out."""
    pid = youtube_playlist_id(url)
    if not pid:
        raise ValueError("not a playlist link")
    purl = youtube_playlist_url(pid)
    try:
        import yt_dlp
    except ImportError:
        yt_dlp = None
    if yt_dlp is not None:
        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": "in_playlist"}
        opts.update(yt_cookie_opts()[0])
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(purl, download=False)
    else:
        flags = 0x08000000 if sys.platform == "win32" else 0
        res = subprocess.run(["yt-dlp", "--flat-playlist", "-J", "--no-warnings"] + yt_cookie_opts()[1] + [purl], capture_output=True,
                             text=True, timeout=90, encoding="utf-8", errors="replace", creationflags=flags)
        if res.returncode != 0 or not res.stdout.strip():
            raise RuntimeError(((res.stderr or "").strip().splitlines() or ["yt-dlp"])[-1][:120])
        info = json.loads(res.stdout)
    entries = []
    for e in (info or {}).get("entries") or []:
        if not e:
            continue
        vid = e.get("id") or youtube_video_id(e.get("url") or "")
        title = clean_text(e.get("title") or "")
        if not vid or title.lower() in ("[private video]", "[deleted video]"):
            continue
        entries.append({"id": vid, "title": title or vid, "url": youtube_watch_url(vid),
                        "duration": float(e.get("duration") or 0)})
    return {"title": clean_text((info or {}).get("title") or "") or pid, "entries": entries}


class _Ev:
    """Minimal event stand-in (forwarding mouse events from child widgets to the list)."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _thumb_to_png(raw, ffmpeg=None):
    """JPEG/WebP bytes -> PNG bytes (Tk cannot read JPEG): Pillow if installed, otherwise ffmpeg."""
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        im.thumbnail((320, 180))
        out = io.BytesIO()
        im.save(out, "PNG")
        return out.getvalue()
    except ImportError:
        pass
    except Exception:
        return None
    if not ffmpeg:
        return None
    try:
        flags = 0x08000000 if sys.platform == "win32" else 0
        res = subprocess.run([ffmpeg, "-v", "error", "-i", "pipe:0", "-vf", "scale=320:-2", "-frames:v", "1",
                              "-f", "image2pipe", "-c:v", "png", "pipe:1"],
                             input=raw, capture_output=True, timeout=15, creationflags=flags)
        return res.stdout if res.returncode == 0 and res.stdout else None
    except Exception:
        return None


def fetch_youtube_preview(url, ffmpeg=None):
    """(title, png_bytes) of a YouTube link, no API key: oEmbed for the title, i.ytimg.com for the thumbnail.
    Either may be None."""
    vid = youtube_video_id(url)
    if not vid:
        return None, None
    headers = {"User-Agent": "Mozilla/5.0"}
    title, thumb_urls = None, []
    try:
        api = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(url, safe="")
        with urllib.request.urlopen(urllib.request.Request(api, headers=headers), timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        title = clean_text(data.get("title") or "") or None
        if data.get("thumbnail_url"):
            thumb_urls.append(data["thumbnail_url"])
    except Exception:
        pass
    thumb_urls = [f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"] + thumb_urls          # 16:9 first
    png = None
    for tu in thumb_urls:
        try:
            with urllib.request.urlopen(urllib.request.Request(tu, headers=headers), timeout=8) as resp:
                png = _thumb_to_png(resp.read(), ffmpeg)
            if png:
                break
        except Exception:
            continue
    return title, png


def _ssl_context(verify=True):
    """HTTPS context: the system certificates plus certifi's list (Python on Windows often lacks a root / intermediate
    certificate: «unable to get local issuer certificate»). verify=False only for public, credential-free requests."""
    if not verify:
        return ssl._create_unverified_context()
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:
        pass
    return ctx


def install_https_opener():
    try:
        urllib.request.install_opener(urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ssl_context())))
    except Exception:
        pass


install_https_opener()


def urlopen_public(req, timeout=15):
    """urlopen for public pages that carry no credentials: when the certificate cannot be verified on this computer it
    retries without verification (the data is only song names / pictures)."""
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as exc:
        if not isinstance(getattr(exc, "reason", exc), ssl.SSLError):
            raise
        diag_log(f"https: certificate check failed ({exc.reason}); public page opened without verification")
        return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context(False))


def source_icon_png(kind, size=20):
    """RGBA PNG bytes of a small YouTube / Spotify badge (drawn here: no files, no Pillow needed)."""
    import zlib
    import struct
    ss = 4
    n = size * ss
    rows = []
    c = n / 2.0
    for py in range(size):
        row = bytearray([0])
        for px in range(size):
            r = g = b = a = 0
            for sy in range(ss):
                for sx in range(ss):
                    x = (px * ss + sx + 0.5) / ss
                    y = (py * ss + sy + 0.5) / ss
                    col = None
                    if kind == "youtube":
                        w, h, rad = size * 0.96, size * 0.68, size * 0.2
                        dx, dy = abs(x - size / 2.0), abs(y - size / 2.0)
                        if dx <= w / 2 and dy <= h / 2:
                            ex, ey = dx - (w / 2 - rad), dy - (h / 2 - rad)
                            if ex <= 0 or ey <= 0 or ex * ex + ey * ey <= rad * rad:
                                col = (255, 0, 0)
                                tx = x - size * 0.40                                     # white play triangle
                                th = size * 0.16
                                if 0 <= tx <= size * 0.26 and abs(y - size / 2.0) <= th * (1 - tx / (size * 0.26)):
                                    col = (255, 255, 255)
                    elif kind in ("video", "image", "audio"):
                        u, v = x / size, y / size
                        if kind == "video":                                              # film strip
                            if 0.05 <= u <= 0.95 and 0.18 <= v <= 0.82:
                                col = (66, 133, 244)
                                if (0.05 <= u <= 0.2 or 0.8 <= u <= 0.95) and int(v * 10 + 0.5) % 2 == 0 and 0.22 <= v <= 0.78:
                                    col = (255, 255, 255)
                                elif 0.27 <= u <= 0.73 and 0.26 <= v <= 0.74:
                                    col = (190, 215, 255)
                        elif kind == "image":                                            # picture: sun and mountains
                            if 0.06 <= u <= 0.94 and 0.14 <= v <= 0.86:
                                col = (46, 160, 120)
                                if (u - 0.72) ** 2 + (v - 0.36) ** 2 <= 0.09 ** 2:
                                    col = (255, 255, 255)
                                for (ax, ay), (bx, by), (cx, cy) in ((((0.14, 0.80), (0.38, 0.44), (0.62, 0.80))), (((0.50, 0.80), (0.70, 0.55), (0.88, 0.80)))):
                                    def side(p1, p2):
                                        return (u - p2[0]) * (p1[1] - p2[1]) - (p1[0] - p2[0]) * (v - p2[1])
                                    s1, s2, s3 = side((ax, ay), (bx, by)), side((bx, by), (cx, cy)), side((cx, cy), (ax, ay))
                                    if not ((s1 < 0 or s2 < 0 or s3 < 0) and (s1 > 0 or s2 > 0 or s3 > 0)):
                                        col = (255, 255, 255)
                        else:                                                            # music note on an orange disc
                            if (u - 0.5) ** 2 + (v - 0.5) ** 2 <= 0.5 ** 2:
                                col = (240, 140, 40)
                                if ((u - 0.40) / 0.13) ** 2 + ((v - 0.70) / 0.10) ** 2 <= 1 or (0.50 <= u <= 0.56 and 0.24 <= v <= 0.70) \
                                        or (0.50 <= u <= 0.76 and 0.24 + (u - 0.50) * 0.55 <= v <= 0.38 + (u - 0.50) * 0.55):
                                    col = (255, 255, 255)
                    else:
                        if (x - size / 2.0) ** 2 + (y - size / 2.0) ** 2 <= (size / 2.0) ** 2:
                            col = (29, 185, 84)
                            for rr, hw in ((size * 0.74, size * 0.34), (size * 0.58, size * 0.29), (size * 0.42, size * 0.24)):
                                d = ((x - size / 2.0) ** 2 + (y - size * 1.12) ** 2) ** 0.5
                                if abs(d - rr) <= size * 0.042 and abs(x - size / 2.0) <= hw:
                                    col = (0, 0, 0)
                    if col:
                        r, g, b, a = r + col[0], g + col[1], b + col[2], a + 255
            k = ss * ss
            if a:
                cov = a // k
                cnt = a // 255
                row += bytes([r // cnt, g // cnt, b // cnt, cov])
            else:
                row += bytes([0, 0, 0, 0])
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


AUDIO_COVER = True          # audio-only YouTube items (Spotify imports) show the video's thumbnail as a cover picture


def yt_pick_audio(info, worst=False):
    """The audio-only stream of a yt-dlp info dict that 'bestaudio' (or 'worstaudio') would take, or None."""
    best = None
    for f in (info or {}).get("formats") or []:
        u = str(f.get("url") or "")
        if not u.startswith("http") or f.get("vcodec") not in (None, "none") or f.get("acodec") in (None, "none"):
            continue
        if f.get("has_drm") or str(f.get("protocol") or "https") not in ("https", "http"):
            continue
        q = float(f.get("abr") or f.get("tbr") or 0)
        if best is None or (q < best[0] if worst else q > best[0]):
            best = (q, f)
    return best[1] if best else None


SPOTIFY_RE = re.compile(r"(?:open\.spotify\.com/(?:intl-[a-zA-Z-]+/)?(?:embed/)?|spotify:)(track|album|playlist|episode|show)[/:]([A-Za-z0-9]{22})")
SPOTIFY_SKIP_WORDS = ("live", "cover", "karaoke", "remix", "sped up", "slowed", "8d", "instrumental", "reaction", "lyrics video",
                      "nightcore", "acoustic", "piano version", "tutorial")


def spotify_parse_link(text):
    """('track' | 'album' | 'playlist' | ..., id) of a Spotify link / URI, or None."""
    if re.search(r"spotify:(?:user:[^:]+:)?collection:tracks|open\.spotify\.com/collection/tracks", text or ""):
        return ("liked", "me")
    m = SPOTIFY_RE.search(text or "")
    return (m.group(1), m.group(2)) if m else None


def _spotify_find(obj, key, depth=0):
    """First value of `key` anywhere inside a JSON structure."""
    if depth > 12:
        return None
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _spotify_find(v, key, depth + 1)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _spotify_find(v, key, depth + 1)
            if r is not None:
                return r
    return None


def spotify_parse_embed(html):
    """{'title', 'cover', 'tracks': [{'title', 'artist', 'dur'}]} from the public embed page of a Spotify track / album /
    playlist (the JSON in its __NEXT_DATA__ script; no account or API key). Only the names are read: Spotify's own audio is
    protected, the songs are played from YouTube."""
    m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html or "", re.S)
    if not m:
        return None
    data = json.loads(m.group(1))
    ent = None
    try:
        ent = data["props"]["pageProps"]["state"]["data"]["entity"]
    except (KeyError, TypeError):
        ent = _spotify_find(data, "entity")
    if not isinstance(ent, dict):
        return None
    title = clean_text(str(ent.get("name") or ent.get("title") or "")).strip()
    cover = None
    try:
        srcs = (ent.get("coverArt") or {}).get("sources") or (ent.get("visualIdentity") or {}).get("image") or []
        if srcs:
            cover = max(srcs, key=lambda x: x.get("width") or x.get("maxWidth") or 0).get("url")
    except Exception:
        cover = None
    tracks = []
    tl = ent.get("trackList")
    if isinstance(tl, list) and tl:
        for t in tl:
            if not isinstance(t, dict):
                continue
            tt = clean_text(str(t.get("title") or t.get("name") or "")).strip()
            ar = clean_text(str(t.get("subtitle") or "")).replace("\u00a0", " ").strip()
            if tt:
                tracks.append({"title": tt, "artist": ar, "dur": float(t.get("duration") or 0) / 1000.0})
    else:
        artists = ent.get("artists") or []
        ar = ", ".join(str(a.get("name")) for a in artists if isinstance(a, dict) and a.get("name")) or \
            clean_text(str(ent.get("subtitle") or "")).strip()
        dur = ent.get("duration")
        if isinstance(dur, dict):
            dur = dur.get("totalMilliseconds")
        if title:
            tracks.append({"title": title, "artist": ar, "dur": float(dur or 0) / 1000.0})
    return {"title": title, "cover": cover, "tracks": tracks}


def fetch_spotify(link, use_account=True):
    """Reads a Spotify link -> {'kind', 'title', 'cover', 'tracks'}; raises with a readable message when it cannot.
    With a signed-in Spotify account (Logins) the account is used first: private playlists, 'Liked songs', full lists."""
    pr = spotify_parse_link(link)
    if not pr:
        raise ValueError("not a Spotify link")
    kind, sid = pr
    if kind in ("episode", "show"):
        raise ValueError("podcasts are not supported")
    tok = spotify_token() if use_account else None
    if tok:
        try:
            res = fetch_spotify_api(kind, sid, tok)
            if res["tracks"]:
                res["via"] = "account"
                return res
        except Exception as exc:
            diag_log(f"spotify: account read failed for {kind} {sid} ({exc}); using the public page")
    if kind == "liked":
        raise ValueError("sign in to Spotify (Logins) to import your liked songs")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
               "Accept-Language": "en"}
    url = f"https://open.spotify.com/embed/{kind}/{sid}"
    with urlopen_public(urllib.request.Request(url, headers=headers), timeout=15) as resp:
        html = resp.read().decode("utf-8", "replace")
    res = spotify_parse_embed(html)
    if not res or not res["tracks"]:
        # last resort: oEmbed gives at least the name of a single track
        api = "https://open.spotify.com/oembed?url=" + urllib.parse.quote(f"https://open.spotify.com/{kind}/{sid}", safe="")
        with urlopen_public(urllib.request.Request(api, headers=headers), timeout=10) as resp:
            o = json.loads(resp.read().decode("utf-8", "replace"))
        t = clean_text(o.get("title") or "").strip()
        if not t:
            raise ValueError("Spotify returned no track names")
        res = {"title": t, "cover": o.get("thumbnail_url"), "tracks": [{"title": t, "artist": "", "dur": 0.0}] if kind == "track" else []}
        if not res["tracks"]:
            raise ValueError("Spotify returned no track list")
    res["kind"] = kind
    return res


def spotify_pick(entries, track):
    """Best YouTube search result for a Spotify track: closest duration, no live / cover / remix versions unless the
    Spotify title has them, official 'Topic' channels preferred. -> entry or None."""
    want = (track.get("title") or "").lower()
    dur = float(track.get("dur") or 0)
    best, best_score = None, None
    for rank, e in enumerate(entries or []):
        if not e or not e.get("id"):
            continue
        t = (e.get("title") or "").lower()
        score = rank * 2.0
        ed = float(e.get("duration") or 0)
        score += min(abs(ed - dur), 120.0) if (dur and ed) else 20.0
        for w in SPOTIFY_SKIP_WORDS:
            if w in t and w not in want:
                score += 40.0
        ch = (e.get("channel") or e.get("uploader") or "").lower()
        if ch.endswith(" - topic"):
            score -= 8.0
        if best_score is None or score < best_score:
            best, best_score = e, score
    return best


def is_image(path):
    return os.path.splitext(str(path))[1].lower() in IMAGE_EXT

def parse_clock(text):
    parts = [p for p in (text or "").strip().replace("h", ":").replace("H", ":").split(":") if p.strip()]
    if not 1 <= len(parts) <= 3:
        return None
    try:
        vals = [int(p) for p in parts]
    except ValueError:
        return None
    h, m, sec = vals[0], (vals[1] if len(vals) > 1 else 0), (vals[2] if len(vals) > 2 else 0)
    if not (0 <= h < 24 and 0 <= m < 60 and 0 <= sec < 60):
        return None
    return h * 3600 + m * 60 + sec

def fmt_clock(secs):
    h, rem = divmod(int(secs), 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"

def parse_duration(text):
    t = (text or "").strip().lower().replace(",", ".")
    if t.endswith("s"):
        t = t[:-1].strip()
    if not t:
        return 0.0
    try:
        if ":" in t:
            total = 0.0
            for part in t.split(":"):
                total = total * 60 + float(part)
        else:
            total = float(t)
    except ValueError:
        return None
    if total != total or total < 0:
        return None
    return min(total, 86400.0)

def fmt_duration(secs):
    if not secs or secs <= 0:
        return "0:00:00"
    h, rem = divmod(int(secs), 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}"

_KEEP = object()

def cfg_if(widget, **kw):
    """widget.configure(...) only for the options whose value really changes (each configure redraws the widget)."""
    ch = {}
    for k, v in kw.items():
        try:
            cur = str(widget.cget(k))
        except Exception:
            cur = None
        if cur != str(v):
            ch[k] = v
    if ch:
        widget.configure(**ch)


class Timeline(tk.Canvas):
    PAD = 12
    def __init__(self, parent, on_seek, on_marker, app, show_markers=True, windowed=False):
        self.app = app
        self.show_markers = show_markers
        self.windowed = windowed        # True: the bar spans IN..OUT only (outputs), not the whole file
        theme = THEMES[self.app.theme_name]
        super().__init__(parent, height=48, bg=theme["PANEL"], highlightthickness=0, cursor="hand2")
        self.on_seek, self.on_marker = on_seek, on_marker
        self.dur, self.pos, self.t_in, self.t_out = 0.0, 0.0, 0.0, None
        self._drag = None
        self._last = 0.0
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", self._release)

    def update_theme(self):
        theme = THEMES[self.app.theme_name]
        self.configure(bg=theme["PANEL"])
        self.redraw()

    def set(self, dur=None, pos=None, t_in=None, t_out=_KEEP):
        if dur is not None:
            self.dur = dur
        if pos is not None and self._drag != "head":
            self.pos = pos
        if t_in is not None:
            self.t_in = t_in
        if t_out is not _KEEP:
            self.t_out = t_out
        key = (round(self.dur, 2), int(self._x(self.pos)), self.t_in, self.t_out, self._width(), self.app.theme_name)
        if key == self.__dict__.get("_rkey"):
            return                                      # same picture: no redraw
        self.redraw()
        self._rkey = key

    def _width(self):
        return max(self.winfo_width(), 2 * self.PAD + 4)

    def _range(self):
        """(start, end) in seconds shown by the bar."""
        if self.windowed:
            lo = max(0.0, self.t_in)
            hi = self.t_out if self.t_out else self.dur
            if hi - lo > 0.05:
                return lo, hi
        return 0.0, self.dur

    def _x(self, t):
        lo, hi = self._range()
        frac = ((t - lo) / (hi - lo)) if hi > lo else 0
        return self.PAD + (self._width() - 2 * self.PAD) * max(0.0, min(1.0, frac))

    def _t(self, x):
        lo, hi = self._range()
        frac = (x - self.PAD) / (self._width() - 2 * self.PAD)
        return lo + max(0.0, min(1.0, frac)) * (hi - lo)

    def redraw(self):
        self._rkey = None
        self.delete("all")
        w, h, ty = self._width(), 48, 26
        theme = THEMES[self.app.theme_name]
        self.create_rectangle(self.PAD, ty - 3, w - self.PAD, ty + 3, fill=theme["LINE"], outline="")
        if self.dur <= 0:
            return
        if self.show_markers:
            xi = self._x(self.t_in)
            xo = self._x(self.t_out if self.t_out else self.dur)
            self.create_rectangle(xi, ty - 3, xo, ty + 3, fill=theme["ACCENT_DIM"], outline="")
            self.create_line(xi + 9, 20, xi, 20, xi, h - 3, xi + 9, h - 3, fill=theme["GREEN"], width=2)
            self.create_line(xo - 9, 20, xo, 20, xo, h - 3, xo - 9, h - 3, fill=theme["ORANGE"], width=2)
        xp = self._x(self.pos)
        self.create_line(xp, 4, xp, h - 4, fill=theme["FG"], width=2)
        self.create_oval(xp - 5, 3, xp + 5, 13, fill=theme["FG"], outline="")

    def _press(self, e):
        if self.dur <= 0:
            return
        self._drag = "head"
        if self.show_markers and e.y >= 16:
            xi = self._x(self.t_in)
            xo = self._x(self.t_out if self.t_out else self.dur)
            di, do = abs(e.x - xi), abs(e.x - xo)
            if min(di, do) <= 9:
                self._drag = "in" if di <= do else "out"
        self._move(e, force=True)

    def _move(self, e, force=False):
        if not self._drag or self.dur <= 0:
            return
        t = self._t(e.x)
        if self._drag == "head":
            self.pos = t
        elif self._drag == "in":
            hi = (self.t_out if self.t_out else self.dur) - MIN_GAP
            t = max(0.0, min(t, hi))
            self.t_in = t if t > 0.02 else 0.0
        else:
            t = max(self.t_in + MIN_GAP, min(t, self.dur))
            self.t_out = None if t >= self.dur - 0.02 else t
        self.redraw()
        now = time.time()
        if force or now - self._last > 0.06:
            self._last = now
            if self._drag == "head":
                self.on_seek(self.pos, False)
            elif self._drag == "in":
                self.on_marker("in", self.t_in, False)
            else:
                self.on_marker("out", self.t_out, False)

    def _release(self, _e):
        d, self._drag = self._drag, None
        if d == "head":
            self.on_seek(self.pos, True)
        elif d == "in":
            self.on_marker("in", self.t_in, True)
        elif d == "out":
            self.on_marker("out", self.t_out, True)

class Output:
    def __init__(self, num, name=None):
        self.num = num
        self.name = name or f"Output {num}"
        self.m = None
        self.pv = None
        self.index = -1
        self.sel_geo = None
        self.cur_in, self.cur_out, self.cur_marked = 0.0, None, False
        self.dragging = False
        self.eof_handled = False
        self.fired = False
        self.ignore_until = 0.0
        self.pick_var = self.pick_btn = self.pick_menu = None
        self.btn = self.dot = None
        self.frame = self.label = None
        self.timeline = None
        self.time_lbl = self.rem_lbl = self.play_btn = None
        self.preview_on = None
        self.name_var = None
        self.col = None
        self.panel = None
        self.vol = tk.IntVar(value=80)
        self._sig = {}              # last format applied per player ("m" / "pv")
        self._windowed = False      # user left fullscreen on purpose (key F)
        self._fade_f = 1.0          # current fade factor (1 = no fade)
        self._headless = False      # running without a window (Screen = None) because NDI output is on
        self._pv_reload_t = 0.0     # last time the monitor's stream was (re)loaded
        self._replay_t = []         # times the output replayed the same item after it ended (loop guard)
        self._reset_start = False   # `start` was set to a resume position: restore the IN mark after loading
        self.hframe = None          # 2x2 Tk frame a window-less (headless) output renders into
        self._pv_seek_t = 0.0       # last time the monitor was sought while dragging the progress bar
        self._sync_hold = 0.0       # no drift correction of the monitor before this time
        self.ndi_sender = None      # NdiSender while "NDI output" is on
        self.ndi_btn = None         # its checkbox (next to "Enable monitor")
        self.ndi_var = None
        self.ar = ar_default()      # audio routing: device + matrix source channels x device channels

# ---- Slideshow: mpv itself plays the slides (like the countdown draws itself): no stream, no encoder ----------------------
AUDIO_EXT = {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".opus", ".wma"}
VIDEO_EXT = MEDIA_EXT - IMAGE_EXT - AUDIO_EXT - {".gif"}
SS_TRANSITIONS = ["fade", "dissolve", "wipeleft", "wiperight", "slideleft", "slideright", "circleopen", "none"]
SS_RESOLUTIONS = ["1280x720", "1920x1080"]          # kept only so older shows load


def ss_default_cfg():
    return {"media": [], "audio": [], "shuffle": False, "img_dur": 5.0, "trans_time": 1.0, "trans_type": "fade",
            "fit_total": 0.0, "fit_audio": False, "loop_audio": True, "res": "1280x720", "loop": True, "autostart": True}


def ss_clean_cfg(raw):
    """A config read from a show file or a window: only known keys, safe types and ranges."""
    cfg = ss_default_cfg()
    if not isinstance(raw, dict):
        return cfg
    for key in ("media", "audio"):
        if isinstance(raw.get(key), list):
            cfg[key] = [p for p in raw[key] if isinstance(p, str) and p][:2000]
    for key in ("shuffle", "fit_audio", "loop_audio", "loop", "autostart"):
        if isinstance(raw.get(key), bool):
            cfg[key] = raw[key]

    def num(key, lo, hi):
        v = raw.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi:
            cfg[key] = float(v)
    num("img_dur", 0.5, 3600)
    num("trans_time", 0, 30)
    num("fit_total", 0, 86400)
    if raw.get("trans_type") in SS_TRANSITIONS:
        cfg["trans_type"] = raw["trans_type"]
    if raw.get("res") in SS_RESOLUTIONS:
        cfg["res"] = raw["res"]
    return cfg


def ffprobe_path(ffmpeg):
    if not ffmpeg:
        return shutil.which("ffprobe") or ""
    exe = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
    local = os.path.join(os.path.dirname(ffmpeg), exe)
    return local if os.path.isfile(local) else (shutil.which("ffprobe") or "")


def probe_duration(ffprobe, path, ffmpeg=None):
    """Duration in seconds of a media file (0.0 when unknown). ffprobe (container, then first video stream),
    and when that gives nothing the 'Duration:' line ffmpeg prints."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def num(text):
        try:
            return max(0.0, float((text or "").strip().splitlines()[0]))
        except (ValueError, IndexError):
            return 0.0
    if ffprobe:
        for entries, extra in (("format=duration", []), ("stream=duration", ["-select_streams", "v:0"])):
            try:
                res = subprocess.run([ffprobe, "-v", "error"] + extra + ["-show_entries", entries, "-of", "csv=p=0", path],
                                     capture_output=True, text=True, timeout=30, creationflags=flags)
                d = num(res.stdout)
                if d > 0:
                    return d
            except Exception:
                pass
    if ffmpeg:
        try:
            res = subprocess.run([ffmpeg, "-hide_banner", "-i", path], capture_output=True, timeout=30, creationflags=flags)
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", (res.stderr or b"").decode("utf-8", "replace"))
            if m:
                return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        except Exception:
            pass
    return 0.0


def ss_image_ok(ffprobe, path):
    """False for a file ffmpeg cannot decode as a picture (a broken image would make a looped input spin forever)."""
    if not ffprobe:
        return True
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                              "-of", "csv=p=0", path], capture_output=True, text=True, timeout=30, creationflags=flags)
        parts = (res.stdout or "").strip().split(",")
        return res.returncode == 0 and len(parts) >= 2 and parts[0].isdigit() and int(parts[0]) > 0
    except Exception:
        return True


def ss_plan(cfg, durations, audio_total=0.0, order=None):
    """Durations of every clip, the transition length and the total length.
    «Total time fit» / «Audio time fit» overrule the image duration: they compute it."""
    media = list(order if order is not None else cfg["media"])
    kinds = ["image" if is_image(p) else "video" for p in media]
    t = 0.0 if cfg["trans_type"] == "none" else max(0.0, float(cfg["trans_time"]))
    n = len(media)
    vid_sum = sum(durations.get(p, 0.0) for p, k in zip(media, kinds) if k == "video")
    n_img = kinds.count("image")
    img = float(cfg["img_dur"])
    target = audio_total if (cfg["fit_audio"] and audio_total > 0) else (cfg["fit_total"] if cfg["fit_total"] > 0 else 0.0)
    if target > 0 and n_img:
        img = (target - vid_sum + max(0, n - 1) * t) / n_img
    img = max(img, t + 0.2, 0.5)
    clips = [img if k == "image" else durations.get(p, 0.0) for p, k in zip(media, kinds)]
    if t > 0:
        t = min(t, max(0.0, min(clips) / 2 - 0.05)) if clips else 0.0     # a transition cannot be longer than half a clip
    total = sum(clips) - max(0, n - 1) * t
    return {"media": media, "kinds": kinds, "clips": clips, "trans": t, "img_dur": img, "total": total}


def ss_mix_audio(ffmpeg, files, out_wav, cancel=None):
    """All the audio files, one after the other, as one WAV (so the video pass has a single audio input)."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats", "-y"]
    for p in files:
        cmd += ["-i", p]
    parts = "".join(f"[{k}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{k}];" for k in range(len(files)))
    parts += "".join(f"[a{k}]" for k in range(len(files))) + f"concat=n={len(files)}:v=0:a=1[ac]"
    cmd += ["-filter_complex", parts, "-map", "[ac]", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", out_wav]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if res.returncode != 0 or not os.path.isfile(out_wav):
        raise RuntimeError("audio: " + " | ".join((res.stderr or "").splitlines()[-4:])[-300:])


SS_THUMB = (96, 54)


def ss_thumb(ffmpeg, path, cache_dir, size=None):
    """Small PNG of the first frame of an image / video (cached). None when it cannot be made."""
    import hashlib
    try:
        st = os.stat(path)
        size = size or SS_THUMB
        key = hashlib.sha1(f"{path}|{st.st_mtime_ns}|{st.st_size}|{size}".encode()).hexdigest()[:20]
        folder = os.path.join(cache_dir, "thumbs")
        os.makedirs(folder, exist_ok=True)
        out = os.path.join(folder, key + ".png")
        if os.path.isfile(out) and os.path.getsize(out) > 100:
            return out
        w, h = size
        vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"
        flags = 0x08000000 if sys.platform == "win32" else 0
        subprocess.run([ffmpeg, "-v", "error", "-y", "-i", path, "-frames:v", "1", "-vf", vf, "-f", "image2", out],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25, creationflags=flags)
        return out if os.path.isfile(out) and os.path.getsize(out) > 100 else None
    except Exception:
        return None


class SsListView:
    """List of files: [thumbnail] position number, file name. Drag a row onto another one to move it."""

    def __init__(self, parent, thumbs, on_move=None):
        self.box = ttk.Frame(parent)
        self.images, self.paths, self.on_move, self._src = {}, [], on_move, None
        if thumbs:
            ttk.Style().configure("Thumb.Treeview", rowheight=SS_THUMB[1] + 8)
        self.tv = ttk.Treeview(self.box, columns=("n", "name"), show="tree", selectmode="extended", height=8,
                               style="Thumb.Treeview" if thumbs else "Treeview")
        self.tv.column("#0", width=(SS_THUMB[0] + 14) if thumbs else 2, stretch=False)
        self.tv.column("n", width=40, anchor="e", stretch=False)
        self.tv.column("name", width=200, anchor="w", stretch=True)
        sb = ttk.Scrollbar(self.box, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tv.pack(side="left", fill="both", expand=True)
        self.tv.bind("<ButtonPress-1>", self._press, add="+")
        self.tv.bind("<ButtonRelease-1>", self._release, add="+")

    def set_rows(self, paths):
        self.paths = list(paths)
        self.tv.delete(*self.tv.get_children())
        for i, p in enumerate(self.paths):
            kw = {"image": self.images[p]} if p in self.images else {}
            self.tv.insert("", "end", iid=f"r{i}", values=(f"{i + 1}.", os.path.basename(p)), **kw)

    def set_image(self, path, photo):
        self.images[path] = photo
        for i, p in enumerate(self.paths):
            if p == path:
                self.tv.item(f"r{i}", image=photo)

    def curselection(self):
        return sorted(int(i[1:]) for i in self.tv.selection())

    def selection_set(self, k):
        self.tv.selection_add(f"r{k}")

    def _press(self, e):
        self._src = self.tv.identify_row(e.y) or None

    def _release(self, e):
        src, self._src = self._src, None
        dst = self.tv.identify_row(e.y)
        if self.on_move and src and dst and src != dst:
            self.on_move(int(src[1:]), int(dst[1:]))


def ss_transition_key(a, b, ttype, t, res):
    import hashlib

    def sig(s):
        try:
            st = os.stat(s["path"])
            return f"{s['path']}|{st.st_mtime_ns}|{st.st_size}|{s['kind']}|{s['clip']:.2f}"
        except OSError:
            return s["path"]
    return hashlib.sha1(f"{sig(a)}>{sig(b)}|{ttype}|{t:.3f}|{res}|v1".encode()).hexdigest()[:24]


def ss_render_transition(ffmpeg, a, b, ttype, t, res, out):
    """One short clip (about a second): the end of slide a cross-faded into the start of slide b. True when made."""
    w, h = (int(v) for v in res.split("x"))

    def inp(s, tail):
        if s["kind"] == "image":
            return ["-loop", "1", "-framerate", "25", "-t", f"{t:.3f}", "-i", s["path"]]
        return ["-ss", f"{max(0.0, s['clip'] - t) if tail else 0.0:.3f}", "-t", f"{t:.3f}", "-i", s["path"]]
    norm = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
            f"setsar=1,fps=25,format=yuv420p")
    fc = f"[0:v]{norm}[a];[1:v]{norm}[b];[a][b]xfade=transition={ttype}:duration={t:.3f}:offset=0[v]"
    part = out + ".part.mp4"
    cmd = ([ffmpeg, "-y", "-v", "error", "-threads", "2"] + inp(a, True) + inp(b, False) +
           ["-filter_complex", fc, "-map", "[v]", "-an", "-t", f"{t:.3f}", "-c:v", "libx264", "-preset", "ultrafast",
            "-crf", "18", "-pix_fmt", "yuv420p", part])
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        res_ = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=90,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if res_.returncode != 0 or not os.path.isfile(part) or os.path.getsize(part) < 1000:
            diag_log(f"slideshow transition clip failed: {(res_.stderr or b'').decode('utf-8', 'replace')[-200:]}")
            return False
        os.replace(part, out)
        return True
    except Exception as exc:
        diag_log(f"slideshow transition clip failed: {exc}")
        return False
    finally:
        try:
            if os.path.isfile(part):
                os.remove(part)
        except OSError:
            pass


class SlideshowRunner:
    """Timeline of a slideshow. It plays nothing itself: the App asks `advance()` which slide (and which
    transition) is due and shows it on every output / monitor / preview with mpv.

    Cross-fades: a background thread makes the short transition clip (xfade) of the next slides while the
    current one is on screen, so nothing is rendered up front. When a clip is not ready in time the
    transition falls back to a dip through black.
    Looks like a listener (state, error, start/stop/go) so it lives next to the other live inputs."""
    LOOKAHEAD = 3
    LEAD = 0.12                                   # the clip is given a moment to finish before the next slide replaces it

    def __init__(self, ffmpeg, cfg, udp_ports, on_change, cache_dir):
        self.ffmpeg, self.cfg, self.on_change, self.cache_dir = ffmpeg, ss_clean_cfg(cfg), on_change, cache_dir
        self.udp_ports, self.state, self.error, self.rejected = [], "waiting", "", ""
        self.index, self.total, self.trans, self.serial = 0, 0, 0.0, 0
        self.total_time, self._pass_acc, self._cycle = 0.0, 0.0, 1.0        # length of one pass (the estimate shown in the options window)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._seq = self._cur = None
        self._ahead = collections.deque()
        self._clips = {}                          # transition key -> clip path, or None when it could not be made
        self._clip_in = None                      # clip that brought us to the current slide
        self._want_go, self._t_slide, self._via_t = False, None, False

    # -- interface shared with RtmpListener / NdiReceiver
    def start(self):
        threading.Thread(target=self._prepare, daemon=True, name="slideshow").start()

    def stop(self):
        self._stop.set()

    def go(self):
        self._want_go = True

    def _set(self, state, error=""):
        if (state, error) != (self.state, self.error):
            if state == "live" and self.state != "live":
                self.gen = getattr(self, "gen", 0) + 1
            self.state, self.error = state, error
            try:
                self.on_change(self)
            except Exception:
                pass

    def _prepare(self):
        try:
            cfg, probe = self.cfg, ffprobe_path(self.ffmpeg)
            media = [p for p in cfg["media"] if os.path.isfile(p)]
            durs = {p: probe_duration(probe, p, self.ffmpeg) for p in set(media) if not is_image(p)}
            self.media = [p for p in media if is_image(p) or durs.get(p, 0) > 0.2]
            for p in media:
                if p not in self.media:
                    diag_log(f"slideshow: cannot read the length of {p}, skipped")
            if not self.media:
                raise RuntimeError("no readable media: " + (os.path.basename(media[0]) if media else "-"))
            self.total = len(self.media)
            audio_total = 0.0
            if cfg["fit_audio"]:
                audio_total = sum(probe_duration(probe, p, self.ffmpeg) for p in cfg["audio"] if os.path.isfile(p))
            self._seq = self._sequence(durs, audio_total)
            self._cur = self._take()
            if self._cur is None:
                raise RuntimeError("no readable media")
            if not (self.total == 1 and self._cur["kind"] == "image"):       # one picture just stays
                self._refill()
        except Exception as exc:
            if not self._stop.is_set():
                diag_log(f"slideshow failed: {exc}")
                self._set("error", str(exc)[:160])
            return
        self._set("live")
        threading.Thread(target=self._render_loop, daemon=True, name="slideshow-clips").start()

    def _sequence(self, durs, audio_total):
        """Endless (or one pass, without loop) stream of slides: path, kind, clip length, seconds before the next one."""
        cfg = self.cfg
        while not self._stop.is_set():
            order = list(self.media)
            if cfg["shuffle"]:
                random.shuffle(order)
            plan = ss_plan(cfg, durs, audio_total, order)
            self.trans = plan["trans"] if cfg["trans_type"] != "none" else 0.0
            self.total_time = plan["total"]
            self._cycle = sum(c if (k == len(plan["clips"]) - 1 and not cfg["loop"]) else max(0.3, c - self.trans)
                              for k, c in enumerate(plan["clips"])) or plan["total"]
            last = len(plan["media"]) - 1
            for k, (p, kind, clip) in enumerate(zip(plan["media"], plan["kinds"], plan["clips"])):
                end_of_show = (k == last and not cfg["loop"])
                yield {"path": p, "kind": kind, "clip": clip, "first": k == 0,
                       "period": clip if end_of_show else max(0.3, clip - self.trans)}
            if not cfg["loop"]:
                return

    def _take(self):
        return next(self._seq, None)

    def _refill(self):
        with self._lock:
            while len(self._ahead) < self.LOOKAHEAD:
                s = self._take()
                if s is None:
                    break
                self._ahead.append(s)

    # -- transition clips
    def _clip_path(self, a, b):
        if self.trans <= 0 or self.cfg["trans_type"] == "none":
            return None
        p = self._clips.get(ss_transition_key(a, b, self.cfg["trans_type"], self.trans, self.cfg["res"]))
        return p if p and os.path.isfile(p) else None

    def _render_loop(self):
        folder = os.path.join(self.cache_dir, "transitions")
        try:                                       # keep the cache small: newest 300 clips
            files = sorted((os.path.join(folder, n) for n in os.listdir(folder)), key=os.path.getmtime)
            for old in files[:-300]:
                os.remove(old)
        except OSError:
            pass
        ttype, res = self.cfg["trans_type"], self.cfg["res"]
        while not self._stop.is_set():
            job = None
            if self.trans > 0 and ttype != "none":
                with self._lock:
                    seq = [self._cur] + list(self._ahead)
                for a, b in zip(seq, seq[1:]):
                    key = ss_transition_key(a, b, ttype, self.trans, res)
                    if key not in self._clips:
                        job = (key, a, b)
                        break
            if job is None:
                self._stop.wait(0.25)
                continue
            key, a, b = job
            out = os.path.join(folder, key + ".mp4")
            ok = (os.path.isfile(out) and os.path.getsize(out) > 1000) or \
                ss_render_transition(self.ffmpeg, a, b, ttype, self.trans, res, out)
            self._clips[key] = out if ok else None

    def position(self, now):
        """(seconds into the current pass, length of a pass) - what the output's progress bar shows."""
        if self._cur is None or self._t_slide is None:
            return 0.0, self.total_time
        pos = self._pass_acc + (now - self._t_slide)
        return max(0.0, min(self.total_time, pos * self.total_time / max(0.1, self._cycle))), self.total_time

    # -- timeline (UI thread only)
    def advance(self, now):
        """The picture due now: {path, kind, token, serial, solo, ovl, start}. ovl = None | ("out"/"in", 0..1) is the
        dip through black used when a transition clip is not ready."""
        if self._cur is None:
            return None
        if self._t_slide is None and self._want_go:
            self._t_slide = now
        if self._t_slide is not None:
            moved = 0
            while self._ahead and now - self._t_slide >= self._cur["period"]:
                self._t_slide += self._cur["period"]
                prev = self._cur
                with self._lock:
                    self._cur = self._ahead.popleft()
                self._pass_acc = 0.0 if self._cur.get("first") else self._pass_acc + prev["period"]
                self._clip_in = self._clip_path(prev, self._cur)
                self._refill()
                self.index, self.serial, self._via_t = (self.index + 1) % max(1, self.total), self.serial + 1, self.trans > 0
                moved += 1
                if moved > 3:                                  # the app was frozen: do not replay the missed slides
                    self._t_slide = now
                    break
        cur, nxt = self._cur, (self._ahead[0] if self._ahead else None)
        e = (now - self._t_slide) if self._t_slide is not None else 0.0
        base = {"serial": self.serial, "solo": self.total == 1, "ovl": None, "start": 0.0}
        if self._t_slide is not None and self._clip_in and e < self.trans + self.LEAD:
            return dict(base, path=self._clip_in, kind="video", token=(self.serial, "clip"), solo=False)
        if self._clip_in and cur["kind"] == "video":
            base["start"] = self.trans + self.LEAD
        if self._t_slide is not None and self.trans > 0:
            half, per = self.trans / 2, cur["period"]
            if nxt is not None and e > per - half and not self._clip_path(cur, nxt):
                base["ovl"] = ("out", min(1.0, (e - (per - half)) / half))
            elif self._via_t and not self._clip_in and e < half:
                base["ovl"] = ("in", max(0.0, e / half))
        return dict(base, path=cur["path"], kind=cur["kind"], token=(self.serial, "slide"))


# ---- Countdown input: drawn by mpv itself (ASS overlay on a black picture / a background image) -------
CD_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
CD_BARS = ("none", "top", "bottom", "left", "right")
CD_MAINS = ("counter", "hour", "date", "dow")
CD_DATE_FMTS = ("ymd", "mdy", "dmy", "full")
CD_MONTHS_EN = "January,February,March,April,May,June,July,August,September,October,November,December"
CD_DAYS_EN = "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday"


def cd_default():
    return {"total": 300.0, "mode": "down", "running": False, "acc": 0.0, "t0": 0.0, "warn": 30.0,
            "end_text": "", "ring": True, "negative": False, "auto_start": True, "bg_color": "#000000",
            "bg_image": "", "scale": 100.0, "dx": 0.0, "dy": 0.0, "audio": "",
            "show_name": True, "show_hour": False, "hour12": False, "show_date": False, "show_dow": False,
            "bar": "none", "transparent": False, "counter_font": "", "text_font": "", "main": "counter", "date_fmt": "ymd", "counter_size": 150.0, "text_size": 34.0,
            "counter_color": "#FFFFFF", "text_color": "#B4B4B4", "zero_red": True, "bg_opacity": 100.0, "audio_run": ""}


def cd_clean(raw):
    cd = cd_default()
    if not isinstance(raw, dict):
        return cd

    def num(key, lo, hi):
        v = raw.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi:
            cd[key] = float(v)
    num("total", 1, 359999)
    num("warn", 0, 3600)
    num("scale", 20, 300)
    num("counter_size", 20, 400)
    num("text_size", 10, 150)
    num("bg_opacity", 0, 100)
    num("dx", -640, 640)
    num("dy", -360, 360)
    if "dx" not in raw and isinstance(raw.get("x"), (int, float)) and not isinstance(raw.get("x"), bool):
        cd["dx"] = max(-640.0, min(640.0, (float(raw["x"]) - 50.0) * 12.8))        # shows saved by v5.18.0 (percent)
    if "dy" not in raw and isinstance(raw.get("y"), (int, float)) and not isinstance(raw.get("y"), bool):
        cd["dy"] = max(-360.0, min(360.0, (float(raw["y"]) - 50.0) * 7.2))
    if raw.get("mode") in ("down", "up"):
        cd["mode"] = raw["mode"]
    if isinstance(raw.get("end_text"), str):
        cd["end_text"] = raw["end_text"][:80]
    if raw.get("bar") in CD_BARS:
        cd["bar"] = raw["bar"]
    for key in ("ring", "negative", "auto_start", "show_name", "show_hour", "hour12", "show_date", "show_dow", "transparent", "zero_red"):
        if isinstance(raw.get(key), bool):
            cd[key] = raw[key]
    if isinstance(raw.get("bg_color"), str) and CD_HEX.match(raw["bg_color"]):
        cd["bg_color"] = raw["bg_color"]
    for key in ("bg_image", "audio", "audio_run"):
        if isinstance(raw.get(key), str):
            cd[key] = raw[key][:1000]
    for key in ("counter_font", "text_font"):
        v = raw.get(key, raw.get("font"))                 # "font" = one font for both (v5.19.1 shows)
        if isinstance(v, str):
            cd[key] = re.sub(r"[{}\\,;]", "", v)[:60]
    if raw.get("main") in CD_MAINS:
        cd["main"] = raw["main"]
    if raw.get("date_fmt") in CD_DATE_FMTS:
        cd["date_fmt"] = raw["date_fmt"]
    for key in ("counter_color", "text_color"):
        if isinstance(raw.get(key), str) and CD_HEX.match(raw[key]):
            cd[key] = raw[key]
    return cd


def cd_elapsed(cd, now):
    return cd["acc"] + ((now - cd["t0"]) if cd["running"] else 0.0)


def cd_value(cd, now):
    """(seconds to show - negative once past zero when allowed -, reached zero / the target?)"""
    el = cd_elapsed(cd, now)
    if cd["mode"] == "down":
        left = cd["total"] - el
        if not cd.get("negative"):
            left = max(0.0, left)
        return left, left <= 0.0
    return el, el >= cd["total"]


def cd_fmt(secs, total):
    if secs < 0:
        s, sign = int(-secs), "-"
        sign = sign if s > 0 else ""
    else:
        s, sign = (int(math.ceil(secs - 1e-9)) if secs > 0 else 0), ""
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return sign + (f"{h}:{m:02d}:{sec:02d}" if total >= 3600 else f"{m:02d}:{sec:02d}")


def _cd_sector(cx, cy, r_out, r_in, a0, a1):
    """ASS drawing of a ring sector; angles in degrees, clockwise from 12 o'clock."""
    steps = max(2, int(abs(a1 - a0) / 4) + 1)
    pts = []
    for i in range(steps + 1):
        a = math.radians(-90 + a0 + (a1 - a0) * i / steps)
        pts.append((cx + r_out * math.cos(a), cy + r_out * math.sin(a)))
    for i in range(steps, -1, -1):
        a = math.radians(-90 + a0 + (a1 - a0) * i / steps)
        pts.append((cx + r_in * math.cos(a), cy + r_in * math.sin(a)))
    return f"m {pts[0][0]:.0f} {pts[0][1]:.0f} l " + " ".join(f"{x:.0f} {y:.0f}" for x, y in pts[1:])


def _bgr(hex_rgb):
    h = hex_rgb.lstrip("#")
    return (h[4:6] + h[2:4] + h[0:2]).upper()


def cd_wall_text(cd, wall, what, days=None, months=None, full=None):
    """Text of the wall clock: what = hour | date | dow."""
    dnames = (days or CD_DAYS_EN).split(",")
    mnames = (months or CD_MONTHS_EN).split(",")
    if what == "dow":
        return dnames[wall.weekday()] if len(dnames) == 7 else ""
    if what == "hour":
        return wall.strftime("%I:%M:%S %p").lstrip("0") if cd.get("hour12") else wall.strftime("%H:%M:%S")
    fmt = cd.get("date_fmt", "ymd")
    if fmt == "full" and len(dnames) == 7 and len(mnames) == 12:
        return (full or "{dow}, {month} {d}, {y}").format(dow=dnames[wall.weekday()], month=mnames[wall.month - 1],
                                                          d=wall.day, y=wall.year)
    return wall.strftime({"mdy": "%m/%d/%Y", "dmy": "%d/%m/%Y"}.get(fmt, "%Y/%m/%d"))


def cd_extras(cd, wall, days=None, months=None, full=None):
    """Small wall-clock line under the ring (day of week, date, hour) - whatever is not already the main display."""
    parts = []
    for flag, what in (("show_dow", "dow"), ("show_date", "date"), ("show_hour", "hour")):
        if cd.get(flag) and cd.get("main", "counter") != what:
            parts.append(cd_wall_text(cd, wall, what, days, months, full))
    return "   ·   ".join(p for p in parts if p)


def cd_ass(cd, title, now, end_default="", blink=None, wall=None, days=None, months=None, full=None):
    """ASS events (1280x720): background colour, iris ring, big digits, title and end message.
    Position (x, y in % of the picture) and size (scale in %) come from the countdown settings."""
    left, done = cd_value(cd, now)
    total = cd["total"]
    show = cd_fmt(left, total)
    green, amber, red, track, white, dim = "78C850", "00BEFF", "2828E6", "3C3C3C", "FFFFFF", "B4B4B4"
    overtime = left < 0
    zero = cd["mode"] == "down" and done
    warn = cd["mode"] == "down" and not done and 0 < cd["warn"] >= left
    color = red if zero else (amber if warn else green)
    if zero and not overtime:
        if blink is None:
            blink = int(now * 2) % 2 == 0
        if not blink:
            color = "5A1414"
    frac = 1.0 if overtime else ((left / total) if cd["mode"] == "down" else min(1.0, left / total))
    k = cd.get("scale", 100.0) / 100.0
    cx, cy = 640 + cd.get("dx", 0.0), 360 + cd.get("dy", 0.0)
    ev = []
    if cd.get("bg_color", "#000000").lower() != "#000000" and not cd.get("bg_image") and not cd.get("transparent"):
        ev.append(r"{\an7\pos(0,0)\bord0\shad0\p1\1c&H%s&}m 0 0 l 1280 0 1280 720 0 720{\p0}" % _bgr(cd["bg_color"]))
    op = cd.get("bg_opacity", 100.0)
    if op < 99.5 and not cd.get("transparent") and (cd.get("bg_image") or cd.get("bg_color", "#000000").lower() != "#000000"):
        ev.append(r"{\an7\pos(0,0)\bord0\shad0\p1\1c&H000000&\1a&H%02X&}m 0 0 l 1280 0 1280 720 0 720{\p0}" % int(round(op / 100.0 * 255)))
    cfnt = (r"\fn" + cd["counter_font"]) if cd.get("counter_font") else ""
    tfnt = (r"\fn" + cd["text_font"]) if cd.get("text_font") else ""
    tcol, ccol = _bgr(cd.get("text_color", "#B4B4B4")), _bgr(cd.get("counter_color", "#FFFFFF"))
    tsize = cd.get("text_size", 34.0) * k
    bar = cd.get("bar", "none")
    if bar != "none":
        th, f = 22, max(0.0, min(1.0, frac))
        rect = lambda x0, y0, x1, y1: "m %d %d l %d %d %d %d %d %d" % (x0, y0, x1, y0, x1, y1, x0, y1)
        if bar in ("top", "bottom"):
            y0 = 0 if bar == "top" else 720 - th
            track_s, fill_s = rect(0, y0, 1280, y0 + th), rect(0, y0, int(1280 * f), y0 + th)
        else:
            x0 = 0 if bar == "left" else 1280 - th
            track_s, fill_s = rect(x0, 0, x0 + th, 720), rect(x0, 720 - int(720 * f), x0 + th, 720)
        ev.append(r"{\an7\pos(0,0)\bord0\shad0\p1\1c&H%s&}%s{\p0}" % (track, track_s))
        if f > 0.002:
            ev.append(r"{\an7\pos(0,0)\bord0\shad0\p1\1c&H%s&}%s{\p0}" % (color, fill_s))
    if cd["ring"]:
        shape = lambda a0, a1: _cd_sector(cx, cy, 250 * k, 212 * k, a0, a1)
        base = r"{\an7\pos(0,0)\bord0\shad0\p1\1c&H%s&}" % track
        ev.append(base + shape(0, 180) + r"{\p0}")
        ev.append(base + shape(180, 360) + r"{\p0}")
        sweep = 360.0 * max(0.0, min(1.0, frac))
        if sweep >= 1:
            sweep = min(sweep, 359.9)
            pre = r"{\an7\pos(0,0)\bord0\shad0\p1\1c&H%s&}" % color
            if sweep > 180:
                ev.append(pre + shape(0, 180) + r"{\p0}")
                ev.append(pre + shape(180, sweep) + r"{\p0}")
            else:
                ev.append(pre + shape(0, sweep) + r"{\p0}")
    main = cd.get("main", "counter")
    if main != "counter" and wall is not None:
        show = cd_wall_text(cd, wall, main, days, months, full)
        shrink = 1.0 if len(show) <= 5 else max(0.18, min(1.0, 5.6 / len(show)))
        zero_now = False
    else:
        shrink = 1.0 if len(show) <= 5 else 0.73
        zero_now = zero
    size = cd.get("counter_size", 150.0) * shrink * k
    ev.append(r"{\an5\pos(%d,%d)%s\fs%d\b1\bord0\shad0\1c&H%s&}%s" % (
        cx, cy, cfnt, size, color if (zero_now and cd.get("zero_red", True)) else ccol, re.sub(r"[{}\\]", "", show)))
    extra = cd_extras(cd, wall, days, months, full) if wall is not None else ""
    if extra:
        ev.append(r"{\an5\pos(%d,%d)%s\fs%d\b0\bord0\shad0\1c&H%s&}%s" % (cx, cy + 290 * k, tfnt, tsize * 0.94, tcol, extra))
    if title and cd.get("show_name", True):
        ev.append(r"{\an5\pos(%d,%d)%s\fs%d\b0\bord0\shad0\1c&H%s&}%s" % (cx, cy - 290 * k, tfnt, tsize, tcol, re.sub(r"[{}\\]", "", title)))
    msg = (cd.get("end_text") or end_default) if zero else ""
    if msg:
        ev.append(r"{\an5\pos(%d,%d)%s\fs%d\b1\bord0\shad0\1c&H%s&}%s" % (
            cx, cy + (340 if extra else 320) * k, tfnt, tsize * 1.6, color if cd.get("zero_red", True) else tcol, re.sub(r"[{}\\]", "", msg)))
    return "\n".join(ev)



class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        YT_COOKIES.update(yt_cookie_settings(self.cfg.get("yt_cookies")))
        YT_ANON["on"] = bool(self.cfg.get("yt_anon"))
        self.lang = self.cfg.get("lang") if self.cfg.get("lang") in STR else "pt"
        self.theme_name = self.cfg.get("theme", "dark")
        self._i18n = []
        self._status_last = None
        self.playlist_path = None

        self.outs = []
        self.ip = None
        self.ip_item = None
        self.ip_dur = 0.0
        self.ip_playing = False
        self.ip_ss_on = False             # slideshow in the preview: shown only after Play
        self._ip_idle_key = None
        self.ip_info_ready = False
        self.ip_from = 0.0
        self.items = []
        self._next_id = 0
        self.sel = []
        self.anchor = None
        self.played = set()
        self.history = []
        self._n = 0
        self._edit = None
        self._press = None
        self._fade_w = {}                  # (item id, column) -> checkbox + ms widgets placed over the cell
        self._fade_sync_pending = False
        self._rtmp = {}                    # item id -> RtmpListener (ffmpeg in listen mode)
        self._rtmp_host = ("", 0.0)
        self._ffmpeg = None
        self._yt_play_cache = {}            # (url, format) -> stream addresses handed to mpv
        self._yt_direct = {}                # (url, format) -> {"t", "state", "src", "ua"}: stream address for the NDI ffmpeg feed
        self._ndi_win = None
        self._cd_audio = None
        self._cd_runp = None
        self._cd_run_id = None
        self._ss_shown = {}               # id(player) -> (player, serial, overlay signature) of the slideshow slide it shows
        self._ss_aud = {}                 # item id -> mpv playing the slideshow audio
        self._uiq = queue.Queue()          # callables posted by worker threads, run by _tick on the Tk thread
        self._closing = False
        self.root.after(1500, self._ndi_warm_up)
        self.root.after(400, self._setup_dnd)
        self.root.report_callback_exception = self._on_tk_error
        self.root.after(60_000, self._health_log)

        self.monitors = self._get_monitors()
        self._mon_sig = self._sig(self.monitors)

        self._style()
        self._build()
        self._bind_keys()
        self._update_title()
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.after(100, self._show_welcome)
        root.after(TICK_MS, self._tick)
        root.after(MONITOR_POLL_MS, self._monitor_loop)

    def _show_welcome(self):
        w = tk.Toplevel(self.root)
        w.title(self.t("welcome_title"))
        w.geometry("420x280")
        w.resizable(False, False)
        theme = THEMES[self.theme_name]
        w.configure(bg=theme["BG"])
        w.transient(self.root)
        w.attributes("-topmost", True)
        w.update_idletasks()
        x = (w.winfo_screenwidth() - 420) // 2
        y = (w.winfo_screenheight() - 280) // 2
        w.geometry(f"+{x}+{y}")

        ttk.Label(w, text=self.t("welcome_title"), style="Header.TLabel", font=("Segoe UI", 14, "bold")).pack(pady=(30, 20))

        def do_new():
            w.destroy()
            self.root.lift()
            self.root.focus_force()

        def do_load():
            w.destroy()
            self.root.lift()
            self.root.focus_force()
            self.open_playlist()

        def do_last():
            last = self.cfg.get("last_playlist")
            w.destroy()
            self.root.lift()
            self.root.focus_force()
            if last and os.path.exists(last):
                self.load_playlist(last)
            else:
                self.status_t("st_pl_open_err", THEMES[self.theme_name]["RED"], err="no last show")

        btn_frame = ttk.Frame(w)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text=self.t("welcome_new"), style="Accent.TButton", command=do_new, width=22).pack(pady=6)
        ttk.Button(btn_frame, text=self.t("welcome_load"), style="TButton", command=do_load, width=22).pack(pady=6)
        last_btn = ttk.Button(btn_frame, text=self.t("welcome_last"), style="TButton", command=do_last, width=22)
        last_btn.pack(pady=6)
        if not self.cfg.get("last_playlist") or not os.path.exists(self.cfg.get("last_playlist", "")):
            last_btn.configure(state="disabled")

        w.protocol("WM_DELETE_WINDOW", do_new)
        w.grab_set()
        w.focus_force()

    def t(self, key, **kw):
        text = STR.get(self.lang, STR["pt"]).get(key) or STR["pt"].get(key, key)
        return text.format(**kw) if kw else text

    def _tx(self, widget, key, option="text"):
        self._i18n.append((widget, option, key))
        widget.configure(**{option: self.t(key)})
        return widget

    def status_t(self, key, color=None, **kw):
        self._status_last = (key, color, kw)
        if hasattr(self, "status_lbl") and self.status_lbl is not None:
            theme = THEMES[self.theme_name]
            self.status_lbl.configure(text=self.t(key, **kw), foreground=color or theme["MUTED"])

    def set_language(self, code):
        if code not in STR or code == self.lang:
            return
        self.lang = code
        self.cfg["lang"] = code
        save_config(self.cfg)
        for widget, option, key in self._i18n:
            widget.configure(**{option: self.t(key)})
        for cid, _w in COLUMNS:
            self.tree.heading(cid, text=self.t("col_" + cid))
        for out in self.outs:
            self._set_output_ui(out)
        self._style_lock_btn()
        self._update_pickers()
        self._update_title()
        self._refresh_info()
        self._refresh_all()
        if self._status_last:
            key, color, kw = self._status_last
            self.status_t(key, color, **kw)

    def on_lang_change(self, _e=None):
        self.root.focus_set()
        self.set_language(LANGS[self.lang_combo.current()][0])

    def toggle_kb_lock(self):
        """Lock Keyboard: the shortcuts (space, arrows, Delete, S, F…) stop acting, so a stray key cannot hit a live show.
        The button turns red while locked; click it again to unlock."""
        self.kb_locked = not getattr(self, "kb_locked", False)
        self._style_lock_btn()
        if self.kb_locked:
            self._kb_lock_tick()

    def _kb_lock_tags(self, w):
        """A bind tag in front of every widget swallows all key events while locked (typing, Tab, space on a button…)."""
        try:
            tags = w.bindtags()
            if "KbLock" not in tags:
                w.bindtags(("KbLock",) + tuple(tags))
            for c in w.winfo_children():
                self._kb_lock_tags(c)
        except tk.TclError:
            pass

    def _kb_lock_tick(self):
        """While locked, windows opened meanwhile (dialogs, bigger window…) get the tag too."""
        if not getattr(self, "kb_locked", False) or self._closing:
            return
        if not getattr(self, "_kb_class_bound", False):
            for seq in ("<KeyPress>", "<KeyRelease>"):
                self.root.bind_class("KbLock", seq, lambda e: "break" if getattr(self, "kb_locked", False) else None)
            self._kb_class_bound = True
        self._kb_lock_tags(self.root)
        self.root.after(700, self._kb_lock_tick)

    def _style_lock_btn(self):
        btn = getattr(self, "lock_btn", None)
        if btn is not None:
            on = getattr(self, "kb_locked", False)
            btn.configure(text=self.t("btn_lock_kb_on" if on else "btn_lock_kb"), style="Red.TButton" if on else "TButton")

    def toggle_theme(self):
        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        self.cfg["theme"] = self.theme_name
        save_config(self.cfg)
        self._style()
        self.theme_btn.configure(text=self.t("opt_theme_light" if self.theme_name == "light" else "opt_theme_dark"))
        if hasattr(self, "timeline"):
            self.timeline.update_theme()
        for out in self.outs:
            if hasattr(out, "panel") and out.panel:
                out.panel.configure(style="Panel.TFrame")
            if hasattr(out, "timeline") and out.timeline:
                out.timeline.update_theme()
            if hasattr(out, "frame") and out.frame:
                out.frame.configure(bg="black")
        if hasattr(self, "out_panel") and self.out_panel:
            self.out_panel.configure(style="Panel.TFrame")
        if hasattr(self, "out_canvas") and self.out_canvas:
            self.out_canvas.configure(bg=THEMES[self.theme_name]["PANEL"])
        self._destroy_fade_widgets()
        self._refresh_all()
        self.status_t("st_ready_short")

    def _update_title(self):
        name = os.path.basename(self.playlist_path) if self.playlist_path else None
        role = getattr(self, "sync", {}).get("role", "off")
        tag = {"main": "  [MAIN]", "backup": "  [BACKUP]"}.get(role, "")
        self.root.title(self.t("app_title") + (f" — {name}" if name else "") + tag)

    @staticmethod
    def _get_monitors():
        if sys.platform == "win32":
            try:
                mons = native_monitors()
                if mons:
                    return mons
            except Exception:
                pass
        try:
            return list(get_monitors())
        except Exception:
            return []

    @staticmethod
    def _sig(mons):
        return tuple((m.x, m.y, m.width, m.height, bool(getattr(m, "is_primary", False)))
                     for m in mons)

    def _others(self, out):
        return [o for o in self.outs if o is not out]

    def _mv_geo(self):
        mv = getattr(self, "mv", None)
        return tuple(mv["geo"]) if mv and mv.get("geo") else None

    def _taken(self, out):
        t = {o.sel_geo for o in self._others(out) if o.sel_geo is not None}
        if self._mv_geo() is not None:
            t.add(self._mv_geo())                           # the Multiview's screen is not available to the outputs
        if self._co_screen() is not None:
            t.add(self._co_screen())                        # nor the composite output's
        return t

    def _monitor_label(self, i, short=False):
        mo = self.monitors[i]
        if short:
            return self.t("screen_short", n=i + 1, w=mo.width, h=mo.height)
        tag = self.t("tag_primary") if getattr(mo, "is_primary", False) else ""
        return self.t("screen_label", n=i + 1, w=mo.width, h=mo.height, tag=tag)

    def _screen_index(self, out):
        if out.sel_geo is None:
            return None
        return next((i for i, mo in enumerate(self.monitors) if geo(mo) == out.sel_geo), None)

    def _free_screen_for(self, out):
        taken = self._taken(out)
        free = [i for i, mo in enumerate(self.monitors) if geo(mo) not in taken]
        secondary = [i for i in free if not getattr(self.monitors[i], "is_primary", False)]
        if secondary:
            return secondary[0]
        if not taken and free:
            return free[0]
        return None

    def _ui_monitor(self):
        cx = self.root.winfo_x() + self.root.winfo_width() // 2
        cy = self.root.winfo_y() + self.root.winfo_height() // 2
        for i, mo in enumerate(self.monitors):
            if mo.x <= cx < mo.x + mo.width and mo.y <= cy < mo.y + mo.height:
                return i
        return None

    def _picker_text(self, out):
        if out.sel_geo is None:
            return self.t("screen_none")
        i = self._screen_index(out)
        return self._monitor_label(i, short=True) if (i is not None and i < len(self.monitors)) else "—"

    def _update_pickers(self):
        for out in self.outs:
            out.pick_var.set(self._picker_text(out))

    def _fill_pick_menu(self, out):
        self.refresh_monitors()
        menu = out.pick_menu
        menu.delete(0, "end")
        mark = "✓  " if out.sel_geo is None else "     "
        menu.add_command(label=mark + self.t("screen_none"),
                         command=lambda: self._choose_screen(out, None))
        if not self.monitors:
            return
        menu.add_separator()
        for i, mo in enumerate(self.monitors):
            owner = next((o for o in self._others(out) if o.sel_geo is not None and o.sel_geo == geo(mo)), None)
            mvtaken = owner is None and geo(mo) in (self._mv_geo(), self._co_screen())
            label = self._monitor_label(i) + (self.t("pick_taken", o=owner.name) if owner else (self.t("pick_taken_mv") if mvtaken else ""))
            mark = "✓  " if geo(mo) == out.sel_geo else "     "
            menu.add_command(label=mark + label, state="disabled" if (owner or mvtaken) else "normal",
                             command=lambda k=i: self._choose_screen(out, k))

    def _hide_output(self, out):
        """Take the fullscreen window away WITHOUT touching playback (same as "Turn Off")."""
        if not out.m or out._headless or getattr(out, "_hidden", False):
            return
        try:
            out.m.fullscreen = False
            out.m.geometry = "1x1+10000+10000"
        except Exception:
            pass
        out._hidden = True
        self._set_output_ui(out)

    def _drop_screen(self, out):
        """The output lost its screen (Screen = None / monitor unplugged): the window goes away but the output
        keeps playing - same video, sound and progress bar, no restart (the fullscreen is optional)."""
        self._hide_output(out)

    def _choose_screen(self, out, idx):
        if idx is None:
            out.sel_geo = None
            self._update_pickers()
            if out.m:
                self._drop_screen(out)
            return
        if not (0 <= idx < len(self.monitors)):
            return
        if geo(self.monitors[idx]) in self._taken(out):
            return
        out.sel_geo = geo(self.monitors[idx])
        self._update_pickers()
        if out.m and (out._headless or getattr(out, "_hidden", False)):
            pass                                         # stays without a window until "Turn On"
        elif out.m:
            self._move_output(out, idx)
        self._check_overlap()

    def refresh_monitors(self):
        mons = self._get_monitors()
        if not mons:
            return
        sig = self._sig(mons)
        if sig == self._mon_sig:
            return
        old = {o.num: self._screen_index(o) for o in self.outs}
        self.monitors, self._mon_sig = mons, sig
        gone = None
        for out in self.outs:
            if out.sel_geo is None:
                continue
            new_idx = self._screen_index(out)
            if new_idx is None:
                free = self._free_screen_for(out)
                out.sel_geo = geo(mons[free]) if free is not None else None
                if out.m:
                    if free is not None:
                        self._move_output(out, free)
                    else:
                        self._drop_screen(out)
                gone = ("st_screen_gone", {"o": out.num, "n": free + 1}) if free is not None \
                    else ("st_screen_gone_none", {"o": out.num})
            elif out.m and new_idx != old[out.num]:
                self._move_output(out, new_idx)
        self._update_pickers()
        if gone:
            self.status_t(gone[0], THEMES[self.theme_name]["RED"], **gone[1])
        else:
            self.status_t("st_screens_updated")
            self._check_overlap(keep_message=True)

    def _monitor_loop(self):
        self.refresh_monitors()
        self.root.after(MONITOR_POLL_MS, self._monitor_loop)

    def identify(self):
        self.refresh_monitors()
        theme = THEMES[self.theme_name]
        for i, mo in enumerate(self.monitors):
            size = 220
            w = tk.Toplevel(self.root)
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            w.configure(bg=theme["ACCENT"])
            w.geometry(f"{size}x{size}+{mo.x + (mo.width - size) // 2}+{mo.y + (mo.height - size) // 2}")
            tk.Label(w, text=str(i + 1), font=("Segoe UI", 96, "bold"), bg=theme["ACCENT"], fg="white").pack(
                expand=True, fill="both")
            w.after(1500, w.destroy)

    def _move_output(self, out, idx):
        def move():
            try:
                if out.m is None or out._headless:
                    return
                out.m.fullscreen = False
                out.m.fs_screen = idx
                out.m.fullscreen = True
            except Exception:
                pass
        self.root.after(120, move)

    def _check_overlap(self, keep_message=False):
        ui = self._ui_monitor()
        theme = THEMES[self.theme_name]
        if len(self.monitors) > 1 and ui is not None:
            for out in self.outs:
                if out.sel_geo is not None and self._screen_index(out) == ui:
                    self.status_t("st_overlap", theme["RED"], o=out.num)
                    return
        if not keep_message:
            self.status_t("st_ready_short")

    def _prop(self, out, name, default=None):
        try:
            v = getattr(out.m, name)
        except SHUTDOWN:
            raise
        except Exception:
            return default
        return default if v is None else v

    @staticmethod
    def _setp(player, name, value):
        try:
            with MPV_GRAB_LOCK:                          # never while the NDI sender is taking a picture from this mpv
                setattr(player, name, value)
        except SHUTDOWN:
            raise
        except Exception:
            pass

    def _apply_aspect(self, player, mode):
        if not player:
            return
        try:
            self._setp(player, "keepaspect", mode != "stretch")
            self._setp(player, "panscan", 1.0 if mode == "fill" else 0.0)
            self._setp(player, "video_unscaled", "yes" if mode == "orig" else "no")
            self._setp(player, "video_aspect_override", {"a169": "16:9", "a43": "4:3"}.get(mode, "no"))
        except SHUTDOWN:
            pass

    def _out_of(self, item):
        n = item.get("output")
        return next((o for o in self.outs if o.num == n), None)

    @staticmethod
    def _img_dur(item):
        d = item.get("dur", 0.0) or 0.0
        return f"{d:g}" if (d > 0 and is_image(item["path"])) else "inf"

    def _cur_item(self, out):
        return self.items[out.index] if 0 <= out.index < len(self.items) else None

    def _snap(self):
        return [self._cur_item(o) for o in self.outs]

    def _restore(self, snap):
        for o, cur in zip(self.outs, snap):
            o.index = next((k for k, it in enumerate(self.items) if it is cur), -1) if cur is not None else -1

    def _is_playing(self, it):
        return any(self._cur_item(o) is it for o in self.outs)

    def _drop_hframe(self, out):
        if out.hframe is not None:
            try:
                out.hframe.destroy()
            except Exception:
                pass
            out.hframe = None

    def start_output(self, out, headless=None):
        """headless=True: play without any window (NDI / audio / monitor only); False: always with its fullscreen
        window (needs a screen); None: a window only when a screen is selected."""
        if out.m:
            return True
        out._ndi_hold_until = time.time() + 2.0              # the NDI sender leaves a new / changing mpv alone for a moment
        idx = self._screen_index(out)
        theme = THEMES[self.theme_name]
        if headless is None:
            headless = idx is None
        if idx is None and not headless:
            self.status_t("st_no_screen", theme["RED"], n=out.num)
            return False
        try:
            opts = dict(hwdec=OUT_HWDEC, keep_open=True, idle=True, osc=False, osd_level=0,
                        input_default_bindings=False, input_vo_keyboard=False, audio_display=False,
                        image_display_duration="inf", volume=out.vol.get() if hasattr(out, "vol") else 80,
                        screenshot_sw="yes")         # screenshot-raw (NDI fallback capture) reads the decoded frame
            if headless:
                # NDI-only output: embedded in a 2x2 px Tk frame, so mpv never creates a window of its own
                # (no fullscreen, no taskbar button) yet renders normally for the NDI frames
                try:
                    out.hframe = tk.Frame(self.root, width=2, height=2, bg="black", bd=0, highlightthickness=0)
                    out.hframe.place(x=0, y=0)
                    self.root.update_idletasks()
                    out.m = new_mpv(vo="gpu", wid=str(out.hframe.winfo_id()), force_window=True, **opts)
                except Exception:
                    self._drop_hframe(out)
                    out.m = new_mpv(vo="null", force_window=False, **opts)
            else:
                opts.update(vo="gpu", force_window=True, fullscreen=True, fs_screen=idx, cursor_autohide=300,
                            title=f"{out.name} (NDI: {out.name})")
                out.m = new_mpv(**opts)
            out._headless = headless
            out._sig = {}
            out._windowed = False
            try:
                @out.m.event_callback("file-loaded", "video-reconfig")
                def _video_event(_event, o=out):
                    try:
                        self._uiq.put(lambda: self._on_video_event(o))
                    except Exception:
                        pass
            except Exception:
                pass
            if self.mute.get():
                out.m.mute = True
            if self.blackout.get():
                out.m.brightness = -100
            self._apply_output_format(out)
            if not ar_is_default(getattr(out, "ar", None) or ar_default()):
                self._audio_apply(out)
        except Exception as exc:
            out.m = None
            messagebox.showerror(self.t("dlg_out_err", n=out.num), str(exc))
            return False
        self._set_output_ui(out)
        self._check_overlap()
        if out.index >= 0:
            item = self._cur_item(out)
            if item:
                try:
                    saved = float(getattr(out, "_saved_pos", 0) or 0)
                    resume = saved > max(0.5, item["in"]) and not is_live(item)
                    begin = max(item["in"], saved) if resume else item["in"]
                    out._load_start = None
                    if resume and item.get("is_youtube") and YT_DIRECT_PLAY:
                        # resume point goes in the loadfile options (file-local): no global `start` to put back later
                        # (that late property write inside a loading HLS stream crashed libmpv)
                        out._load_start = begin
                        begin = item["in"]
                        resume = False
                    self._setp(out.m, "start", f"{begin:.3f}" if begin > 0 else "none")
                    out._reset_start = resume                      # back to the IN mark once the file is loaded
                    self._setp(out.m, "end", f"{item['out']:.3f}" if item["out"] else "none")
                    self._setp(out.m, "image_display_duration", self._img_dur(item))

                    self._apply_output_format(out, item)
                    
                    self._out_hw(out.m, item)
                    if item.get("is_youtube"):
                        fmt = item.get("ytdl_format")
                        if fmt and fmt not in ("best", ""):
                            self._setp(out.m, "ytdl_format", fmt)
                        else:
                            self._setp(out.m, "ytdl_format", YT_DEFAULT_FORMAT)
                        self._setp(out.m, "cache", "yes")
                        self._setp(out.m, "cache_secs", 30)
                        self._setp(out.m, "demuxer_max_bytes", 150000000)
                        self._setp(out.m, "demuxer_max_back_bytes", 30000000)       # live streams run for hours
                        self._setp(out.m, "demuxer_readahead_secs", 30)
                        
                    self._open_item(out, item)
                except Exception:
                    pass
        if out.preview_on.get():
            self.start_preview(out)
        return True

    def _on_format_change(self, out):
        try:
            self._apply_output_format(out)
        except SHUTDOWN:
            self._output_lost(out)
            return
        self._update_output_frame_aspect(out)

    def _update_output_frame_aspect(self, out):
        if not hasattr(out, "frame") or not hasattr(out, "res_var"):
            return
        canvas = parse_canvas(out.res_var.get())
        max_w, max_h = PV_W, PV_H
        if not canvas:
            w, h = max_w, max_h
        else:
            aspect = canvas[0] / canvas[1]
            if aspect > (max_w / max_h):
                w, h = max_w, int(max_w / aspect)
            else:
                w, h = int(max_h * aspect), max_h
            w, h = max(80, w), max(45, h)
        out.frame.configure(width=w, height=h)

    NDI_QUALITIES = ("1", "1/2", "1/4")

    def _q_div(self, out):
        q = getattr(out, "q_var", None)
        return {"1/2": 2, "1/4": 4}.get(q.get() if q is not None else "1", 1)

    def _ndi_canvas(self, out):
        """Canvas the output renders at (window, fullscreen and NDI): the chosen resolution divided by the quality factor
        (1, 1/2, 1/4). With «Original» resolution there is no canvas: the divisor is applied to the source size instead."""
        c = self._canvas(out)
        div = self._q_div(out)
        if not c or div == 1:
            return c
        return (max(16, (c[0] // div) // 2 * 2), max(16, (c[1] // div) // 2 * 2))

    def _on_ndi_quality(self, out):
        """Quality 1 / 1/2 / 1/4 changed: fullscreen + window (m), monitor (pv) and NDI all follow."""
        try:
            self._apply_output_format(out)
        except SHUTDOWN:
            self._output_lost(out)
            return
        except Exception as exc:
            diag_log(f"quality apply failed: {exc}")
        if out.ndi_sender is not None:
            try:
                out.ndi_sender.refresh()
            except Exception as exc:
                diag_log(f"ndi quality refresh failed: {exc}")

    def _canvas(self, out):
        var = getattr(out, "res_var", None)
        return parse_canvas(var.get()) if var is not None else None

    def _apply_output_format(self, out, item=None):
        """Apply resolution / FPS / aspect to the output window and to its monitor.

        Resolution = canvas the content is composed on, FPS = output frame rate, and the window
        shows the canvas with its own aspect always preserved (letter/pillar-boxed on the screen).
        Only re-applied when something changed, so mpv does not re-init the video chain needlessly.
        """
        if item is None:
            item = self._cur_item(out)
        mode = item["aspect"] if item else "fit"
        canvas = self._ndi_canvas(out)                    # resolution / quality
        div = self._q_div(out)
        fvar = getattr(out, "fps_var", None)
        vf = build_vf(canvas, mode, parse_fps(fvar.get()) if fvar is not None else None, div if not self._canvas(out) else 1)
        sig = (vf, bool(canvas), mode)
        for role, player in (("m", out.m), ("pv", out.pv)):
            if not player:
                continue
            if role == "pv":
                # The monitor is a second, small decoder of the same stream: it never gets a filter chain
                # (re-initialising it stalls the thumbnail). Its frame already has the canvas aspect, so
                # plain window-level aspect handling shows the same picture, instantly.
                pmode = "fit" if (canvas and mode == "orig") else mode
                qkey = (id(player), div)
                if out._sig.get("pvq", (None, 1)) != qkey and (div > 1 or out._sig.get("pvq", (None, 1))[1] > 1):
                    try:                              # the small monitor decodes at the same reduced quality (one re-init, only when changed)
                        player.vf = "" if div == 1 else f"lavfi=[scale=trunc(iw/{div}/2)*2:trunc(ih/{div}/2)*2]"
                    except SHUTDOWN:
                        raise
                    except Exception:
                        pass
                    out._sig["pvq"] = qkey
                key = (id(player), ("pv", pmode))
                if out._sig.get("pv") != key:
                    self._apply_aspect(player, pmode)
                    out._sig["pv"] = key
                continue
            key = (id(player), sig)
            if out._sig.get("m") == key:
                continue
            try:
                # hwdec is NOT touched here: it is fixed at OUT_HWDEC when the output is created
                try:
                    out._ndi_hold_until = max(getattr(out, "_ndi_hold_until", 0.0), time.time() + 0.4)
                    with MPV_GRAB_LOCK:                      # never while the NDI sender is taking a picture
                        player.vf = vf
                except SHUTDOWN:
                    raise
                except Exception:
                    # filter graph refused: fall back to plain window-level aspect handling
                    player.vf = ""
                    canvas_ok = False
                else:
                    canvas_ok = bool(canvas)
                # with a canvas the item's aspect is already baked into the frame
                self._apply_aspect(player, "fit" if canvas_ok else mode)
                out._sig["m"] = key
            except SHUTDOWN:
                raise
            except Exception:
                pass

    def _ensure_fullscreen(self, out):
        if not out.m or getattr(out, "_hidden", False) or out._windowed or out._headless:
            return                                             # a window-less output (NDI only) must never be sent fullscreen
        idx = self._screen_index(out)
        try:
            if idx is not None and not self._prop(out, "fullscreen", True):
                out.m.fs_screen = idx
                out.m.fullscreen = True
        except SHUTDOWN:
            raise
        except Exception:
            pass

    def _restore_start(self, out):
        item = self._cur_item(out)
        if out.m and item is not None:
            self._setp(out.m, "start", f"{item['in']:.3f}" if item["in"] > 0 else "none")

    def _rehide(self, out):
        """A hidden output ("Turn Off") must stay hidden when a new file loads (mpv may resize/show its window)."""
        if out.m and getattr(out, "_hidden", False):
            self._setp(out.m, "fullscreen", False)
            self._setp(out.m, "geometry", "1x1+10000+10000")

    def _on_video_event(self, out):
        """file-loaded / video-reconfig: streams (YouTube) may change format after start."""
        if not out.m:
            return
        if out._reset_start:
            out._reset_start = False
            self.root.after(1500, lambda o=out: self._restore_start(o))
        try:
            self._ensure_fullscreen(out)
            self._rehide(out)
            self._apply_output_format(out)
        except SHUTDOWN:
            self._output_lost(out)

    def _reapply_ytdl_display(self, out, item):
        """Re-aplica fullscreen e formato para streams do yt-dlp,
        que podem não carregar corretamente à primeira tentativa."""
        if not out.m or self._cur_item(out) is not item:
            return
        try:
            self._ensure_fullscreen(out)
            self._apply_output_format(out, item)
        except SHUTDOWN:
            self._output_lost(out)
        except Exception:
            pass

    def _idle_labels(self, out):
        out.time_lbl.configure(text="00:00 / 00:00")
        out.rem_lbl.configure(text="−00:00", style="Rem.TLabel")
        out.play_btn.configure(text="▶")
        if out.timeline:
            out.timeline.set(dur=0.0, pos=0.0)

    def stop_output(self, out):
        out._ndi_hold_until = time.time() + 2.0
        pos = 0.0
        if out.m and out.index >= 0:
            try:
                pos = float(self._prop(out, "time_pos", 0) or 0)
            except Exception:
                pos = 0.0
        out._saved_pos = pos
        t1 = time.time()
        self.stop_preview(out)
        breadcrumb(f"stop_output: preview closed in {time.time() - t1:.2f}s")
        if out.m:
            old, out.m = out.m, None                    # the NDI sender sees None first, then the mpv goes away
            try:
                if getattr(out, "ndi_sender", None) is not None:
                    out.ndi_sender.detach()
                mpv_terminate(old)
            except Exception:
                pass
        out.m = None
        out._headless = False
        self._drop_hframe(out)
        out._sig = {}
        self._highlight()
        self._set_output_ui(out)
        if out.index >= 0 and out.preview_on.get():
            self.root.after(100, lambda: self._start_standalone_preview(out))

    def _start_standalone_preview(self, out):
        if out.pv or out.index < 0:
            return
        item = self._cur_item(out)
        if not item:
            return
        try:
            self.root.update_idletasks()
            out.pv = new_pv(str(self._mon_frame(out).winfo_id()))
            self._preview_load(out, item, getattr(out, "_saved_pos", 0))
            out.label.place_forget()
        except Exception:
            out.pv = None
            if out.label:
                out.label.place(relx=0.5, rely=0.5, anchor="center")

    def toggle_output(self, out):
        if out.m and out._headless:                      # "Turn On": give the NDI-only output its window
            if self._screen_index(out) is None:
                self.status_t("st_no_screen", THEMES[self.theme_name]["RED"], n=out.num)
                return
            t0 = time.time()
            breadcrumb("turn-on: begin")
            self.stop_output(out)                        # keeps the position (resume), then reopens with a window
            breadcrumb(f"turn-on: old output stopped after {time.time() - t0:.2f}s")
            self.start_output(out, headless=False)
            breadcrumb(f"turn-on: new output started after {time.time() - t0:.2f}s")
            return
        if out.m and not getattr(out, "_hidden", False):
            self._hide_output(out)
        else:
            if out.m and getattr(out, "_hidden", False):
                idx = self._screen_index(out)
                if idx is None:                              # hidden because it has no screen: it needs one to show
                    self.status_t("st_no_screen", THEMES[self.theme_name]["RED"], n=out.num)
                    return
                try:
                    out.m.geometry = ""
                    if idx is not None:
                        out.m.fs_screen = idx
                    out.m.fullscreen = True
                except Exception:
                    pass
                out._hidden = False
                self._set_output_ui(out)
            else:
                self.start_output(out, headless=False)

    def _set_output_ui(self, out):
        on = out.m is not None and not getattr(out, "_hidden", False) and not out._headless
        out.btn.configure(text=self.t("out_off" if on else "out_on"),
                          style="Red.TButton" if on else "Accent.TButton")
        out.dot.configure(style="On.TLabel" if on else "Off.TLabel")

    def _output_lost(self, out):
        diag_log(f"output {out.num}: mpv instance lost (core shut down)")
        self.stop_preview(out)
        out.m = None
        out.index = -1
        self._highlight()
        self._set_output_ui(out)
        self._idle_labels(out)
        theme = THEMES[self.theme_name]
        self.status_t("st_output_lost", theme["RED"], n=out.num)

    def start_preview(self, out):
        if out.pv or not out.m:
            return
        try:
            self.root.update_idletasks()
            out.pv = new_pv(str(self._mon_frame(out).winfo_id()))
        except Exception as exc:
            out.pv = None
            out.preview_on.set(False)
            out.label.place(relx=0.5, rely=0.5, anchor="center")
            theme = THEMES[self.theme_name]
            self.status_t("st_mirror_fail", theme["RED"], n=out.num, err=exc)
            return
        out.label.place_forget()
        cur = self._cur_item(out)
        if cur is not None:
            self._preview_load(out, cur, self._prop(out, "time_pos", 0))

    # ---- Multiview output: a fullscreen window with live tiles (outputs and inputs) laid out freely ----------------------
    MV_LBL = 0.12            # fraction of the tile height reserved under the picture for «name / output»

    def _mvs(self):
        if getattr(self, "_mv_ctx", None) == "co":
            return self._co_state()                          # the engine below is running on the composite output
        if not hasattr(self, "mv"):
            self.mv = {"geo": None, "tiles": [], "on": False, "win": None, "ctl": None, "rt": {}, "uid": 0, "sel": None, "q": "auto", "fstate": {}}
        return self.mv

    def _mv_screen_size(self):
        g = self._mvs()["geo"]
        return (g[2], g[3]) if g else (1920, 1080)

    def _mv_source_name(self, t):
        if t["kind"] == "out":
            return self._out_name(t["ref"])
        it = next((i for i in self.items if i["id"] == t["ref"]), None)
        return (it.get("name") or os.path.basename(it["path"])) if it else "?"

    def _mv_add_tile(self, kind, ref, fx, fy):
        mv = self._mvs()
        sw, sh = self._mv_screen_size()
        w = 0.3
        h = min(0.9, w * (sw / sh) * 9 / 16 / (1 - self.MV_LBL))
        mv["uid"] += 1
        t = {"uid": mv["uid"], "kind": kind, "ref": ref, "x": max(0.0, min(1 - w, fx - w / 2)),
             "y": max(0.0, min(1 - h, fy - h / 2)), "w": w, "h": h}
        mv["tiles"].append(t)
        mv["sel"] = t["uid"]
        if mv["on"]:
            self._mv_build_tile(t)
        self._mv_redraw_editor()
        return t

    def _mv_remove_tile(self, t):
        mv = self._mvs()
        self._mv_drop_tile(t["uid"])
        mv["tiles"] = [x for x in mv["tiles"] if x is not t]
        self._mv_redraw_editor()

    # -- editor window
    def open_multiview(self):
        mv = self._mvs()
        ctl = mv.get("ctl")
        try:
            if ctl is not None and ctl.winfo_exists():
                ctl.deiconify()
                ctl.lift()
                return
        except tk.TclError:
            pass
        theme = THEMES[self.theme_name]
        win = tk.Toplevel(self.root)
        mv["ctl"] = win
        win.title(self.t("mv_title"))
        win.configure(bg=theme["BG"])
        win.geometry("940x560")
        win.minsize(760, 480)
        win.protocol("WM_DELETE_WINDOW", self._mv_close_editor)
        top = ttk.Frame(win, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text=self.t("mv_screen")).pack(side="left", padx=(0, 6))
        self.mv_pick = ttk.Combobox(top, state="readonly", width=34)
        self.mv_pick.pack(side="left")
        self.mv_pick.bind("<<ComboboxSelected>>", self._mv_pick_changed)
        self.mv_btn = ttk.Button(top, command=self.toggle_multiview, takefocus=False)
        self.mv_btn.pack(side="left", padx=10)
        ttk.Button(top, text=self.t("mv_clear"), command=self._mv_clear, takefocus=False).pack(side="left")
        ttk.Label(top, text=self.t("mv_quality")).pack(side="left", padx=(14, 6))
        self.mv_q = ttk.Combobox(top, state="readonly", width=7, values=[self.t("mv_q_auto")] + [f"{q}p" for q in MV_QUALITIES[1:]])
        self.mv_q.pack(side="left")
        self.mv_q.bind("<<ComboboxSelected>>", self._mv_q_changed)
        self._mv_q_show()
        ttk.Label(top, text=self.t("mv_hint"), style="Hint.TLabel").pack(side="right")
        body = ttk.Frame(win, padding=(10, 0, 10, 10))
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        left.pack(side="left", fill="y", padx=(0, 10))
        self.mv_lists = {}
        for kind, key in (("out", "mv_outputs"), ("in", "mv_inputs")):
            ttk.Label(left, text=self.t(key)).pack(anchor="w", pady=(4, 2))
            holder = ttk.Frame(left)
            holder.pack(fill="both", expand=True)
            lb = tk.Listbox(holder, width=30, height=8 if kind == "out" else 14, exportselection=False, selectmode="extended",
                            bg=theme["PANEL"], fg=theme["FG"], highlightthickness=0, bd=0,
                            selectbackground=theme["ACCENT"], activestyle="none")
            sb = ttk.Scrollbar(holder, orient="vertical", command=lb.yview, style="Vertical.TScrollbar")
            hb = ttk.Scrollbar(holder, orient="horizontal", command=lb.xview, style="Horizontal.TScrollbar")
            lb.configure(yscrollcommand=sb.set, xscrollcommand=hb.set)
            hb.pack(side="bottom", fill="x")
            lb.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            self.mv_lists[kind] = lb
            lb.bind("<ButtonPress-1>", lambda e, k=kind: self._mv_list_press(e, k) or "break")
            lb.bind("<B1-Motion>", self._mv_list_motion)
            lb.bind("<ButtonRelease-1>", self._mv_list_release)
            for ev in ("<B1-Leave>", "<B1-Enter>", "<Leave>"):         # Tk auto-scrolls the list (also sideways) when the pointer leaves it with the button down
                lb.bind(ev, lambda e: "break")
            for ev, d, sh in (("<Up>", -1, False), ("<Down>", 1, False), ("<Shift-Up>", -1, True), ("<Shift-Down>", 1, True)):
                lb.bind(ev, lambda e, k=kind, d=d, sh=sh: self._mv_list_key(k, d, sh))
            lb.bind("<Enter>", lambda e: e.widget.focus_set())
        self.mv_canvas = tk.Canvas(body, bg="#050505", highlightthickness=1, highlightbackground=theme["MUTED"])
        self.mv_canvas.pack(side="left", fill="both", expand=True)
        self.mv_canvas.bind("<Configure>", lambda _e: self._mv_redraw_editor())
        self.mv_canvas.bind("<ButtonPress-1>", self._mv_cv_press)
        self.mv_canvas.bind("<B1-Motion>", self._mv_cv_motion)
        self.mv_canvas.bind("<ButtonRelease-1>", self._mv_cv_release)
        self.mv_canvas.bind("<Button-3>", self._mv_cv_context)
        self._mv_fill_lists()
        self._mv_fill_pick()
        self._mv_style_btn()
        self._mv_poll()

    def _mv_signature(self):
        return (tuple((o.num, o.name) for o in self.outs),
                tuple((it["id"], it.get("name") or it["path"], it.get("is_group"), it["output"]) for it in self.items))

    def _mv_poll(self):
        """The Outputs / Inputs lists follow the app by themselves (outputs added, renamed or removed; items imported, renamed...)."""
        if not self._mv_editor_alive() or self._closing:
            return
        self.refresh_monitors()
        msig = getattr(self, "_mon_sig", None)
        if msig != getattr(self, "_mv_msig", None):
            self._mv_msig = msig
            self._mv_screens_changed()
        sig = self._mv_signature()
        if sig != getattr(self, "_mv_sig", None) and not getattr(self, "_mv_src", None):
            self._mv_sig = sig
            self._mv_fill_lists()
            self._mv_redraw_editor()
        self.root.after(700, self._mv_poll)

    def _mv_screens_changed(self):
        """Screens plugged / unplugged: the editor's list follows; a vanished Multiview screen switches it off."""
        mv = self._mvs()
        if mv["geo"] and not any(geo(mo) == mv["geo"] for mo in self.monitors):
            if mv["on"]:
                self.stop_multiview()
            mv["geo"] = None
        self._mv_fill_pick()
        self._mv_redraw_editor()

    def _mv_close_editor(self):
        mv = self._mvs()
        win, mv["ctl"] = mv.get("ctl"), None
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass

    def _mv_fill_lists(self):
        if not self._mv_editor_alive():
            return
        lo, li = self.mv_lists["out"], self.mv_lists["in"]
        top_o, top_i = lo.yview()[0], li.yview()[0]
        lo.delete(0, "end")
        li.delete(0, "end")
        self._mv_list_refs = {"out": [o.num for o in self.outs], "in": []}
        for o in self.outs:
            lo.insert("end", o.name)
        for k, it in enumerate(self.items):
            if it.get("is_group"):
                continue
            self._mv_list_refs["in"].append(it["id"])
            li.insert("end", f"{k + 1}  ·  {it.get('name') or os.path.basename(it['path'])}")
        lo.yview_moveto(top_o)
        li.yview_moveto(top_i)

    def _mv_editor_alive(self):
        ctl = self._mvs().get("ctl")
        try:
            return ctl is not None and ctl.winfo_exists()
        except tk.TclError:
            return False

    def _mv_q_show(self):
        q = self._mvs().get("q", "auto")
        try:
            self.mv_q.current(MV_QUALITIES.index(q) if q in MV_QUALITIES else 0)
        except (tk.TclError, AttributeError):
            pass

    def _mv_q_changed(self, _e=None):
        i = self.mv_q.current()
        self._mvs()["q"] = MV_QUALITIES[i] if 0 <= i < len(MV_QUALITIES) else "auto"     # the copies restart at the new size on the next tick

    def _mv_fill_pick(self):
        if not self._mv_editor_alive():
            return
        self.refresh_monitors()
        vals = [self.t("screen_none")] + [self._monitor_label(i) for i in range(len(self.monitors))]
        self.mv_pick.configure(values=vals)
        g = self._mvs()["geo"]
        idx = next((i for i, mo in enumerate(self.monitors) if geo(mo) == g), None)
        self.mv_pick.current(0 if idx is None else idx + 1)

    def _mv_pick_changed(self, _e=None):
        mv = self._mvs()
        self.refresh_monitors()
        i = self.mv_pick.current()
        was_on = mv["on"]
        if was_on:
            self.stop_multiview()
        mv["geo"] = geo(self.monitors[i - 1]) if i > 0 and i - 1 < len(self.monitors) else None
        taken = next((o for o in self.outs if o.sel_geo is not None and o.sel_geo == mv["geo"] and o.m is not None), None)
        if taken is not None:
            self.status_t("st_mv_taken", THEMES[self.theme_name]["RED"], o=taken.name)
        self._mv_redraw_editor()
        if was_on and mv["geo"]:
            self.start_multiview()
        self._mv_style_btn()

    def _mv_style_btn(self):
        on = self._mvs()["on"]
        btn = getattr(self, "mv_open_btn", None)
        if btn is not None:                                  # blue when off, red while the Multiview is on
            btn.configure(style="Red.TButton" if on else "Accent.TButton")
        if self._mv_editor_alive():
            self.mv_btn.configure(text=self.t("out_off" if on else "out_on"),
                                  style="Red.TButton" if on else "Accent.TButton")

    def _mv_clear(self):
        mv = self._mvs()
        for t in list(mv["tiles"]):
            self._mv_drop_tile(t["uid"])
        mv["tiles"] = []
        self._mv_redraw_editor()

    # -- simulated screen
    def _mv_area(self):
        """Rectangle of the simulated screen inside the canvas (screen aspect, centred)."""
        cw, ch = max(50, self.mv_canvas.winfo_width()), max(50, self.mv_canvas.winfo_height())
        sw, sh = self._mv_screen_size()
        sc = min((cw - 8) / sw, (ch - 8) / sh)
        w, h = sw * sc, sh * sc
        return (cw - w) / 2, (ch - h) / 2, w, h

    def _mv_redraw_editor(self):
        if not self._mv_editor_alive():
            return
        cv = self.mv_canvas
        cv.delete("all")
        ax, ay, aw, ah = self._mv_area()
        cv.create_rectangle(ax, ay, ax + aw, ay + ah, fill="#000000", outline="#555555")
        mv = self._mvs()
        for t in mv["tiles"]:
            x0, y0 = ax + t["x"] * aw, ay + t["y"] * ah
            x1, y1 = x0 + t["w"] * aw, y0 + t["h"] * ah
            ly = y1 - (y1 - y0) * self.MV_LBL
            sel = t["uid"] == mv["sel"]
            cv.create_rectangle(x0, y0, x1, y1, fill="#202020", outline="#ff8a3d" if sel else "#6a6a6a", width=2 if sel else 1)
            cv.create_rectangle(x0 + 3, y0 + 3, x1 - 3, ly - 1, fill="#101c2c", outline="")
            if t["kind"] == "out":
                o = next((o for o in self.outs if o.num == t["ref"]), None)
                sub = o.name if o else "?"
            else:
                it = next((i for i in self.items if i["id"] == t["ref"]), None)
                sub = self._out_name(it["output"]) if it else "?"
            cv.create_text((x0 + x1) / 2, (y0 + ly) / 2, text=("▶ " if t["kind"] == "in" else "▣ ") + self._mv_source_name(t),
                           fill="#cfd8e3", width=max(20, x1 - x0 - 10), font=("Segoe UI", 9))
            cv.create_text((x0 + x1) / 2, (ly + y1) / 2, text=sub, fill="white", font=("Segoe UI", 7))
            cv.create_rectangle(x1 - 10, y1 - 10, x1, y1, fill="#ff8a3d", outline="")

    def _mv_tile_at(self, x, y):
        ax, ay, aw, ah = self._mv_area()
        for t in reversed(self._mvs()["tiles"]):
            x0, y0 = ax + t["x"] * aw, ay + t["y"] * ah
            if x0 <= x <= x0 + t["w"] * aw and y0 <= y <= y0 + t["h"] * ah:
                return t
        return None

    def _mv_cv_press(self, e):
        mv = self._mvs()
        t = self._mv_tile_at(e.x, e.y)
        self._mv_drag = None
        if t is None:
            mv["sel"] = None
            self._mv_redraw_editor()
            return
        mv["tiles"].remove(t)
        mv["tiles"].append(t)                           # on top
        mv["sel"] = t["uid"]
        ax, ay, aw, ah = self._mv_area()
        x1, y1 = ax + (t["x"] + t["w"]) * aw, ay + (t["y"] + t["h"]) * ah
        mode = "size" if (x1 - e.x < 16 and y1 - e.y < 16) else "move"
        self._mv_drag = {"t": t, "mode": mode, "x": e.x, "y": e.y, "ox": t["x"], "oy": t["y"], "ow": t["w"], "oh": t["h"]}
        self._mv_redraw_editor()

    def _mv_cv_motion(self, e):
        d = getattr(self, "_mv_drag", None)
        if not d:
            return
        t = d["t"]
        ax, ay, aw, ah = self._mv_area()
        dx, dy = (e.x - d["x"]) / aw, (e.y - d["y"]) / ah
        if d["mode"] == "move":
            t["x"] = max(0.0, min(1 - t["w"], d["ox"] + dx))
            t["y"] = max(0.0, min(1 - t["h"], d["oy"] + dy))
        else:
            t["w"] = max(0.06, min(1 - t["x"], d["ow"] + dx))
            t["h"] = max(0.06, min(1 - t["y"], d["oh"] + dy))
        self._mv_redraw_editor()

    def _mv_cv_release(self, _e):
        d, self._mv_drag = getattr(self, "_mv_drag", None), None
        if d and self._mvs()["on"]:
            self._mv_place_tile(d["t"])

    def _mv_copy_size(self, t):
        self._mv_size_clip = (t["w"], t["h"])

    def _mv_paste_size(self, t):
        clip = getattr(self, "_mv_size_clip", None)
        if not clip:
            return
        t["w"], t["h"] = clip
        t["x"] = max(0.0, min(1 - t["w"], t["x"]))             # a bigger tile that would leave the screen is moved back in
        t["y"] = max(0.0, min(1 - t["h"], t["y"]))
        if self._mvs()["on"]:
            self._mv_place_tile(t)
        self._mv_redraw_editor()

    def _mv_copy_pos(self, t):
        self._mv_pos_clip = (t["x"], t["y"])

    def _mv_paste_pos(self, t):
        clip = getattr(self, "_mv_pos_clip", None)
        if not clip:
            return
        t["x"] = max(0.0, min(1 - t["w"], clip[0]))
        t["y"] = max(0.0, min(1 - t["h"], clip[1]))
        if self._mvs()["on"]:
            self._mv_place_tile(t)
        self._mv_redraw_editor()

    def _mv_cv_context(self, e):
        t = self._mv_tile_at(e.x, e.y)
        if t is None:
            return
        menu = self._make_menu(self.mv_canvas)
        clip = getattr(self, "_mv_size_clip", None)
        menu.add_command(label=self.t("mv_copy_size"), command=lambda: self._mv_copy_size(t))
        menu.add_command(label=self.t("mv_paste_size"), command=lambda: self._mv_paste_size(t),
                         state="normal" if clip else "disabled")
        pclip = getattr(self, "_mv_pos_clip", None)
        menu.add_command(label=self.t("mv_copy_pos"), command=lambda: self._mv_copy_pos(t))
        menu.add_command(label=self.t("mv_paste_pos"), command=lambda: self._mv_paste_pos(t),
                         state="normal" if pclip else "disabled")
        menu.add_separator()
        menu.add_command(label=self.t("mv_remove"), command=lambda: self._mv_remove_tile(t))
        self._popup(menu, e)

    # -- drag from the lists onto the simulated screen
    def _mv_list_press(self, e, kind):
        lb = self.mv_lists[kind]
        i = lb.nearest(e.y)
        refs = getattr(self, "_mv_list_refs", {}).get(kind, [])
        self._mv_src = None
        lb.focus_set()                                          # Shift + arrows work on the list that was clicked
        if 0 <= i < len(refs) and lb.size():
            shift, ctrl = bool(e.state & 0x1), bool(e.state & 0x4)
            cur = set(lb.curselection())
            if shift and getattr(self, "_mv_anchor", {}).get(kind) is not None:
                a, b = sorted((self._mv_anchor[kind], i))
                lb.selection_clear(0, "end")
                lb.selection_set(a, b)
            elif ctrl:
                (lb.selection_clear if i in cur else lb.selection_set)(i)
                self.__dict__.setdefault("_mv_anchor", {})[kind] = i
            else:
                if i not in cur:                                # pressing inside a selection keeps it (to drag all of it)
                    lb.selection_clear(0, "end")
                    lb.selection_set(i)
                self.__dict__.setdefault("_mv_anchor", {})[kind] = i
            lb.activate(i)
            self.__dict__.setdefault("_mv_cur", {})[kind] = i
            sel = [k for k in lb.curselection() if 0 <= k < len(refs)] or [i]
            if i not in sel:
                sel = [i]
            self._mv_src = (kind, refs[i], lb.get(i), [refs[k] for k in sel])
        return "break"                                          # Tk's own press / motion would select-extend and auto-scroll the list

    def _mv_list_key(self, kind, d, shift):
        """Up / Down move the selection, Shift + Up / Down extend it (own handling: Tk's needs the click it no longer sees)."""
        lb = self.mv_lists[kind]
        n = lb.size()
        if not n:
            return "break"
        cur = self.__dict__.setdefault("_mv_cur", {}).get(kind)
        anchor = self.__dict__.setdefault("_mv_anchor", {}).get(kind)
        if cur is None:
            sel = lb.curselection()
            cur = sel[-1] if sel else (-1 if d > 0 else n)
        new = max(0, min(n - 1, cur + d))
        if shift:
            anchor = anchor if anchor is not None else (cur if 0 <= cur < n else new)
            lb.selection_clear(0, "end")
            lb.selection_set(min(anchor, new), max(anchor, new))
        else:
            lb.selection_clear(0, "end")
            lb.selection_set(new)
            anchor = new
        self._mv_anchor[kind], self._mv_cur[kind] = anchor, new
        lb.activate(new)
        lb.see(new)
        return "break"

    def _mv_list_motion(self, e):
        if not getattr(self, "_mv_src", None):
            return "break"
        g = getattr(self, "_mv_ghost", None)
        if g is None:
            g = tk.Toplevel(self.root)
            g.overrideredirect(True)
            try:
                g.attributes("-topmost", True)
            except tk.TclError:
                pass
            tk.Label(g, text=self._mv_src[2] + (f"  (+{len(self._mv_src[3]) - 1})" if len(self._mv_src) > 3 and len(self._mv_src[3]) > 1 else ""), bg="#ff8a3d", fg="black", padx=8, pady=3).pack()
            self._mv_ghost = g
        g.geometry(f"+{e.x_root + 12}+{e.y_root + 8}")
        return "break"

    def _mv_list_release(self, e):
        g, self._mv_ghost = getattr(self, "_mv_ghost", None), None
        if g is not None:
            g.destroy()
        src, self._mv_src = getattr(self, "_mv_src", None), None
        if not src or g is None:
            return "break"
        cv = self.mv_canvas
        x, y = e.x_root - cv.winfo_rootx(), e.y_root - cv.winfo_rooty()
        if not (0 <= x <= cv.winfo_width() and 0 <= y <= cv.winfo_height()):
            return "break"
        ax, ay, aw, ah = self._mv_area()
        refs = src[3] if len(src) > 3 and src[3] else [src[1]]
        for n, ref in enumerate(refs):                          # several selected: staggered so they do not sit on each other
            self._mv_add_tile(src[0], ref, (x - ax) / aw + 0.04 * n, (y - ay) / ah + 0.04 * n)
        return "break"

    # -- the fullscreen window
    def toggle_multiview(self):
        if self._mvs()["on"]:
            self.stop_multiview()
        else:
            self.start_multiview()
        self._mv_style_btn()

    def start_multiview(self):
        mv = self._mvs()
        if mv["on"]:
            return
        if not mv["geo"]:
            self.status_t("st_mv_no_screen", THEMES[self.theme_name]["RED"])
            return
        x, y, w, h = mv["geo"]
        win = tk.Toplevel(self.root)
        win.configure(bg="black")
        win.overrideredirect(True)
        win.geometry(f"{w}x{h}+{x}+{y}")
        win.update_idletasks()
        for seq in ("<Escape>", "<Double-Button-1>"):         # a way out that never depends on the app being reachable
            win.bind(seq, lambda _e: (self.stop_multiview(), self._mv_style_btn()))
        mv["win"], mv["on"] = win, True
        ui = self._ui_monitor()
        same = ui is not None and geo(self.monitors[ui]) == mv["geo"]
        if same:
            self.status_t("st_mv_same", THEMES[self.theme_name]["RED"])
        else:                                                  # another screen than the app: above the output windows too
            try:
                win.attributes("-topmost", True)
            except tk.TclError:
                pass
        win.lift()
        diag_log(f"multiview on: geo={mv['geo']} tiles={len(mv['tiles'])} same_screen_as_ui={same}")
        for t in mv["tiles"]:
            self._mv_build_tile(t)
        win.update()
        self._mv_style_btn()
        self.root.after(300, self._mv_tick)
        self.status_t("st_mv_on")
        self.root.after(80, self._mv_raise_ui)

    def _mv_raise_ui(self):
        """The Multiview is not a topmost window: the app and the editor stay on top of it and reachable."""
        try:
            self.root.lift()
            ctl = self._mvs().get("ctl")
            if ctl is not None and ctl.winfo_exists():
                ctl.lift()
        except tk.TclError:
            pass

    def _mv_feeds_stop(self):
        for f in list(self.__dict__.get("_tf_feeds", [])):
            f.stop()

    def stop_multiview(self):
        mv = self._mvs()
        mv["on"] = False
        self._mv_feeds_stop()
        for t in list(mv["tiles"]):
            self._mv_drop_tile(t["uid"])
        win, mv["win"] = mv.get("win"), None
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass
        self._mv_style_btn()

    def _mv_players(self, item):
        """Players of the multiview tiles that currently show `item`."""
        out = []
        for st in (getattr(self, "mv", None), getattr(self, "co", None)):
            if st and st.get("on"):
                out += [rt["tile"].pv for rt in st["rt"].values() if rt["item"] is item and rt["tile"].pv is not None]
        return out

    def _mv_live_open(self, tile, item, rt, tries=0):
        """RTMP / NDI input in a tile: its own (5th) UDP copy of the signal, or the address slate while there is none."""
        pv = tile.pv
        if pv is None or rt["item"] is not item:
            return
        self._cap_ensure(item)
        listener = self._rtmp.get(item["id"])
        rt["lkey"] = (id(listener), getattr(listener, "state", ""), getattr(listener, "gen", 0))
        try:
            self._apply_aspect(pv, "fit")
            slot = rt.get("slot")
            if slot is None:
                used = {r.get("slot") for r in self._all_rts() if r is not rt and r.get("item") is item}
                slot = next((k for k in range(4, UDP_PORTS) if k not in used), None)
                rt["slot"] = slot
            if listener is not None and listener.state == "live" and slot is not None and len(getattr(listener, "udp_ports", ())) > slot:
                self._osd_overlay(pv, None)
                self._rtmp_props(pv, True, item.get("is_capture") or item.get("is_ndi"))
                pv.play(f"udp://127.0.0.1:{listener.udp_ports[slot]}?{udp_q(item)}")
                self._live_watch(lambda: tile.pv if rt["item"] is item else None, item,
                                 lambda t: self._mv_live_open(tile, item, rt, t), tries)
            else:
                pv.command("stop")
                self._osd_overlay(pv, self._rtmp_slate(item))
        except SHUTDOWN:
            raise
        except Exception:
            pass

    def _mv_tile_px(self, t):
        _x, _y, w, h = self._mvs()["geo"]
        return int(t["x"] * w), int(t["y"] * h), max(40, int(t["w"] * w)), max(30, int(t["h"] * h))

    def _mv_build_tile(self, t):
        mv = self._mvs()
        win = mv.get("win")
        if win is None or t["uid"] in mv["rt"]:
            return
        px, py, pw, ph = self._mv_tile_px(t)
        lbl_h = max(40, int(ph * self.MV_LBL))
        outer = tk.Frame(win, bg="#303030")
        vid = tk.Frame(outer, bg="black")
        lbl = tk.Label(win, bg="black", fg="white", justify="center", font=("Segoe UI", -max(10, int(lbl_h * 0.36))))
        note = tk.Label(vid, bg="black", fg="#888888", font=("Segoe UI", max(8, ph // 30)))
        note.place(relx=0.5, rely=0.5, anchor="center")
        rt = {"outer": outer, "vid": vid, "lbl": lbl, "note": note, "tile": Output(-t["uid"], "mv"), "item": None, "tally": None,
              "lbl_h": lbl_h, "txt": None}
        mv["rt"][t["uid"]] = rt
        self._mv_place_tile(t)
        win.update_idletasks()
        try:
            rt["tile"].pv = new_pv(str(vid.winfo_id()))
            rt["tile"].frame = vid
        except Exception as exc:
            diag_log(f"multiview: tile player failed: {exc}")
            rt["tile"].pv = None
            note.configure(text=str(exc)[:60])

    def _mv_place_tile(self, t):
        rt = self._mvs()["rt"].get(t["uid"])
        if rt is None:
            return
        px, py, pw, ph = self._mv_tile_px(t)
        lbl_h = max(40, int(ph * self.MV_LBL))
        rt["lbl_h"] = lbl_h
        try:
            rt["lbl"].configure(font=("Segoe UI", -max(10, int(lbl_h * 0.36))))
        except tk.TclError:
            pass
        rt["outer"].place(x=px, y=py, width=pw, height=ph - lbl_h)
        rt["vid"].place(x=3, y=3, relwidth=1, relheight=1, width=-6, height=-6)
        rt["lbl"].place(x=px, y=py + ph - lbl_h, width=pw, height=lbl_h)

    def _mv_drop_tile(self, uid):
        rt = self._mvs()["rt"].pop(uid, None)
        if rt is None:
            return
        pv = rt["tile"].pv
        rt["tile"].pv = None
        if pv is not None:
            try:
                mpv_terminate(pv)
            except Exception:
                pass
        for k in ("outer", "lbl"):
            try:
                rt[k].destroy()
            except tk.TclError:
                pass

    def _mv_tick(self):
        mv = self._mvs()
        if not mv["on"] or self._closing:
            return
        for t in list(mv["tiles"]):
            rt = mv["rt"].get(t["uid"])
            if rt is None:
                continue
            try:
                self._mv_update_tile(t, rt)
            except SHUTDOWN:
                rt["tile"].pv = None
            except Exception as exc:
                if not rt.get("err"):
                    rt["err"] = True
                    diag_log(f"multiview tile {t['uid']}: {exc}")
        self.root.after(300, self._mv_tick)

    def _mv_src_output(self, t):
        """(output, item) whose playback a file / YouTube tile copies; (None, None) when the tile is not showing such a playback."""
        if t["kind"] == "out":
            o = next((o for o in self.outs if o.num == t["ref"]), None)
            if o is None or o.m is None:
                return None, None
            cur = self._cur_item(o)
        else:
            cur = next((i for i in self.items if i["id"] == t["ref"]), None)
            o = next((o for o in self.outs if o.m is not None and self._cur_item(o) is cur), None) if cur is not None else None
        if o is None or cur is None or is_live(cur) or cur.get("is_slideshow") or cur.get("is_countdown") or cur.get("yt_audio"):
            return None, None
        return o, cur

    def _mv_feed_height(self, tile_h, big=False):
        q = self.mv.get("q", "auto") if hasattr(self, "mv") else "auto"
        if q in MV_QUALITIES and q != "auto" and not big:
            return int(q)
        steps = (240, 360, 480, 720, 1080) if big else (240, 360, 480, 720)
        return next((h for h in steps if h >= tile_h), steps[-1])

    def _mon_wants_feed(self, out, item):
        """The monitor window of an output shows the UDP copy (instead of opening the file / YouTube a second time) for these items."""
        if item is None or is_live(item) or self._is_live_stream(item) or item.get("is_slideshow") or item.get("is_countdown") or item.get("yt_audio"):
            return False
        if getattr(out, "_mon_nofeed", None) == item["id"] or not self._ffmpeg_path():
            return False
        if item.get("is_youtube"):
            return True
        ext = os.path.splitext(item.get("path") or "")[1].lower()
        return ext in VIDEO_EXT or ext in IMAGE_EXT

    def _feeds_tick(self):
        """Every ~0.3 s: the shared copy of each output that has Multiview tiles and / or a monitor window."""
        try:
            self._feeds_tick_inner()
        except SHUTDOWN:
            raise
        except Exception as exc:
            if not self.__dict__.get("_ferr"):
                self._ferr = True
                diag_log(f"tile / monitor copies: {exc}")

    def _feeds_tick_inner(self):
        mv = self._mvs()
        need = {}
        for o in self.outs:                                     # monitor windows
            if o.m is None or not o.pv:
                continue
            cur = self._cur_item(o)
            if getattr(o, "_mon_mode", None) == "feed" and self._mon_wants_feed(o, cur):
                try:
                    h = int(self._mon_frame(o).winfo_height())
                except (tk.TclError, AttributeError, ValueError):
                    h = 240
                need[o.num] = (o, max(h, 0))
        co_nums = set()
        for name in ("mv", "co"):
            with self._mv_as(name):
                st = self._mvs()
                for t in (st["tiles"] if st["on"] else []):
                    rt = st["rt"].get(t["uid"])
                    if rt is None or rt["tile"].pv is None:
                        continue
                    o, _cur = self._mv_src_output(t)
                    if o is None:
                        continue
                    h = self._mv_tile_px(t)[3] - rt.get("lbl_h", 0) - 6
                    need[o.num] = (o, max(h, need.get(o.num, (o, 0))[1]))
                    if name == "co":
                        co_nums.add(o.num)
        states = {}
        for num, (o, h) in need.items():
            states[num] = self._mv_feed_step(o, self._mv_feed_height(h, num in co_nums))
        mv["fstate"] = states
        self._co_state()["fstate"] = states
        self._fstate = states
        for o in self.outs:                                     # nobody shows this output any more: its copy stops
            tf = getattr(o, "_tf", None)
            if tf and tf["feed"] is not None and o.num not in need:
                tf["feed"].stop()
        feeds = self.__dict__.setdefault("_tf_feeds", [])
        for f in list(feeds):
            if f.owner not in self.outs:                        # the output was removed
                f.stop()
                feeds.remove(f)

    def _mv_feed_step(self, o, height):
        """-> "ok" (tiles read the copy), "wait" (being prepared: keep what the tile shows), "off" (tiles open the source themselves)."""
        tf = o.__dict__.setdefault("_tf", {"feed": None, "bad": set()})
        feed = tf["feed"]
        spec = self._ndi_ff_spec(o, for_tile=True)
        if spec is None or not spec.get("ffmpeg"):
            if feed is not None:
                feed.stop()
            return "off"
        if spec.get("pending"):
            return "wait"                                       # YouTube: the address is still being resolved
        if spec.get("src") and not spec["live"] and not spec["image"] and not TILE_COPY_NETWORK:
            if feed is not None:
                feed.stop()
            return "off"                                        # a video from the network: tiles / monitor open it themselves (faster)
        key = spec_key(spec, height)
        if (key[0], key[1], key[3]) in tf["bad"]:                 # a source the copy failed on stays failed whatever the size
            if feed is not None:
                feed.stop()
            return "off"
        try:
            if feed is None:
                feed = tf["feed"] = TileFeed(spec["ffmpeg"], free_udp_ports(TileFeed.PORTS))
                feed.owner = o
                self.__dict__.setdefault("_tf_feeds", []).append(feed)
            now, live, image = time.time(), spec["live"], spec["image"]
            if spec["paused"] and not live and not image:
                # paused: the last picture stays; the copy runs only long enough to give a tile that has none its picture
                if feed.running():
                    if feed.key != key:
                        feed.start(spec, spec["pos"], height)
                    elif feed.got and now - feed.t_start > 1.2:
                        feed.stop()
                elif feed.key != key or feed.need:
                    feed.start(spec, spec["pos"], height)
                return "ok"
            want = spec["pos"] + 0.45                           # the copy is shown ~0.4 s after it was made
            if feed.running():
                if feed.key != key:
                    feed.start(spec, want, height)
                elif not feed.got and now - feed.t_start > 8.0:
                    return self._mv_feed_bad(o, tf, feed, key, "no picture")
                elif feed.got and not (live or image) and now - feed.t_start > 3.0 and abs(feed.pos() - want) > 1.0:
                    if spec.get("src") and sum(1 for t in feed.starts if now - t < 40.0) >= 4:
                        # restarted over and over: this server does not seek, the copy cannot follow the output -> tiles open the source themselves
                        return self._mv_feed_bad(o, tf, feed, key, f"position not followed (copy at {feed.pos():.1f} s, output at {want:.1f} s)")
                    feed.start(spec, want, height)              # the output jumped (seek, loop, next file): follow it
                return "ok"
            if feed.key == key and feed.proc is not None and not feed.got:
                feed.fails += 1
                if feed.fails >= 2:
                    return self._mv_feed_bad(o, tf, feed, key, "ffmpeg ended")
            if now - feed.t_start < 1.5 and feed.key == key:
                return "wait"
            feed.start(spec, want, height)
            return "ok"
        except SHUTDOWN:
            raise
        except Exception as exc:
            tf["bad"].add((key[0], key[1], key[3]))
            diag_log(f"multiview: tile copy of {o.name} could not start ({exc}) -> tiles open the source themselves")
            return "off"

    def _mv_feed_note(self, o, feed, spec, height):
        diag_log(f"tile / monitor copy of {o.name}: started at {feed.start_pos:.1f}s, {height}p, "
                 f"{'live' if spec.get('live') else 'video'} {'(network)' if spec.get('src') else '(file)'} {str(spec.get('src') or spec.get('path'))[:70]}")

    def _mv_feed_bad(self, o, tf, feed, key, why):
        tf["bad"].add((key[0], key[1], key[3]))
        diag_log(f"multiview: tile copy of {o.name} failed ({why}: {' | '.join(feed.err)[:300]}) -> tiles open the source themselves")
        feed.stop()
        return "off"

    def _mv_feed_tile(self, t, rt, o, cur):
        """A tile shows the UDP copy of the output's playback. False: no free port for this tile (it opens the source itself)."""
        tile = rt["tile"]
        pv = tile.pv
        feed = getattr(o, "_tf", {}).get("feed")
        if feed is None:
            return True
        if rt.get("mode") != "feed" or rt.get("fsrc") is not feed or rt["item"] is not cur:
            rt.update(mode="feed", item=cur, fkey=None, fslot=None, fsrc=feed, ftries=0)
            k = self._find(cur["id"])
            tile.index = k if k is not None else -1
        if rt.get("fslot") is None:
            used = {r.get("fslot") for r in self._all_rts() if r is not rt and r.get("fsrc") is feed}
            rt["fslot"] = next((k for k in range(TileFeed.TILE_SLOTS) if k not in used), None)
            if rt["fslot"] is None:
                rt["mode"] = None
                return False
        self._feed_play(pv, rt, feed, feed.ports[rt["fslot"]], o, lambda: self._apply_aspect(pv, "fit"))
        return True

    def _feed_play(self, pv, st, feed, port, o, set_format):
        """Shows the UDP copy in a player (a Multiview tile or an output's monitor); `st` holds that viewer's own state."""
        if st.get("fpv") != id(pv):
            for k in ("fkey", "fopen", "ftries"):
                st.pop(k, None)
            st["fpv"] = id(pv)                                  # a new player (bigger window): it has to open the stream again
        key = (id(feed), feed.gen)
        try:
            if st.get("fkey") != key:
                if not feed.running():
                    feed.need = True                            # paused output and this viewer has no picture yet
                    return
                st["fkey"], st["fopen"] = key, time.time()
                set_format()
                self._osd_overlay(pv, None)
                self._rtmp_props(pv, True, True)
                self._setp(pv, "loop_file", "no")
                self._setp(pv, "pause", False)
                pv.play(f"udp://127.0.0.1:{port}?{UDP_Q_CAP}")
                return
            if feed.running() and pv.time_pos is None and time.time() - st.get("fopen", 0) > 3.0 and st.get("ftries", 0) < 4:
                st["ftries"] = st.get("ftries", 0) + 1          # a stream that joined between two keyframes: open it again
                st["fkey"] = None
                return
            paused = bool(self._prop(o, "pause", False)) and not feed.live
            if bool(pv.pause) != paused:
                self._setp(pv, "pause", paused)
        except SHUTDOWN:
            raise
        except Exception:
            pass

    def _mon_feed_sync(self, out, cur, pos):
        """Monitor window of an output on the UDP copy. True = handled (no seeking / speed nudging needed)."""
        if getattr(out, "_mon_mode", None) != "feed":
            return False
        if not self._mon_wants_feed(out, cur):
            out._mon_mode = None
            return False
        st = self.__dict__.get("_fstate", {}).get(out.num)
        feed = getattr(out, "_tf", {}).get("feed")
        if st in ("ok", "wait") and feed is not None:
            self._feed_play(out.pv, out._mon_st, feed, feed.ports[TileFeed.MON_PORT], out, lambda: self._mon_format(out, cur))
            return True
        if st == "off":                                         # ffmpeg could not make the copy: the monitor opens the source itself, as before
            out._mon_mode, out._mon_nofeed = None, cur["id"]
            self._preview_load(out, cur, pos)
            return True
        return True

    def _mon_format(self, out, cur):
        out._sig.pop("pv", None)
        self._apply_output_format(out, cur)

    def _mv_update_tile(self, t, rt):
        tile = rt["tile"]
        cur, out_name, pos, paused, playing = None, "", 0.0, False, False
        if t["kind"] == "out":
            o = next((o for o in self.outs if o.num == t["ref"]), None)
            out_name = o.name if o else f"Output {t['ref']}"
            if o is not None and o.m is not None:
                cur = self._cur_item(o)
                if cur is not None:
                    pos = float(self._prop(o, "time_pos", 0) or 0)
                    paused = bool(self._prop(o, "pause", False))
                    playing = not paused
        else:
            cur = next((i for i in self.items if i["id"] == t["ref"]), None)
            if cur is not None:
                out_name = self._out_name(cur["output"])
                playing = any(self._cur_item(o) is cur and o.m is not None and not bool(self._prop(o, "pause", False)) for o in self.outs)
        name = (cur.get("name") or os.path.basename(cur["path"])) if cur else ("—" if t["kind"] == "out" else self._mv_source_name(t))
        txt = out_name if t["kind"] == "out" else f"{name}\n{out_name}"
        if rt["txt"] != txt:
            rt["txt"] = txt
            rt["lbl"].configure(text=txt)
        if rt["tally"] != playing:
            rt["tally"] = playing
            rt["outer"].configure(bg="#d9362b" if playing else "#303030")
        pv = tile.pv
        if pv is None:
            return
        if (t["kind"] == "in" and cur is not None and (cur.get("is_slideshow") or is_ytlive(cur) or not is_live(cur))
                and not any(self._cur_item(o) is cur and o.m is not None for o in self.outs)):
            self._mv_thumb_tile(rt, cur, pv)                        # not playing on an output: a still picture, nothing is started
            return
        rt["thumb"] = None
        if cur is None:
            if rt["item"] is not None:
                try:
                    pv.command("stop")
                    self._osd_overlay(pv, None)
                except Exception:
                    pass
                rt["item"], rt["lkey"], tile.index = None, None, -1
            rt["note"].configure(text="")
            return
        if is_live(cur):
            rt["note"].configure(text="")
            listener = self._rtmp.get(cur["id"])
            key = (id(listener), getattr(listener, "state", ""), getattr(listener, "gen", 0))
            if rt["item"] is not cur or rt.get("lkey") != key:
                if rt["item"] is not cur:
                    rt["slot"] = None                      # another input: look for a free tile port again
                rt["item"] = cur
                k = self._find(cur["id"])
                tile.index = k if k is not None else -1
                if cur.get("is_slideshow") or cur.get("is_countdown"):
                    rt["lkey"] = key
                    self._rtmp_open(tile, cur, only="pv")          # drawn / fed by the countdown and slideshow tickers
                else:
                    self._mv_live_open(tile, cur, rt)
            return
        rt["note"].configure(text="")
        src_out, _c = self._mv_src_output(t)
        if src_out is not None and self._mvs().get("fstate", {}).get(src_out.num) in ("ok", "wait"):
            if self._mv_feed_tile(t, rt, src_out, cur):
                return
        elif rt.get("mode") == "feed":
            rt.update(mode=None, item=None, fkey=None, fslot=None, fsrc=None)      # the copy is gone: the tile opens the source itself
        if cur.get("yt_audio"):                     # audio-only item (Spotify / YouTube audio): its cover picture, there is no video to copy
            if rt.get("athumb") != cur["id"] and time.time() - rt.get("athumb_t", 0) > 1.0:
                rt["athumb_t"] = time.time()
                self._setp(pv, "image_display_duration", "inf")
                if self._pv_thumb(tile, cur):
                    rt["athumb"], rt["item"], rt["mode"] = cur["id"], cur, None
            return
        rt["athumb"] = None
        follow = t["kind"] == "out"
        if t["kind"] == "in":                       # an input that an output is playing: the tile follows that output's position
            src = next((o for o in self.outs if o.m is not None and self._cur_item(o) is cur), None)
            if src is not None:
                follow = True
                pos = float(self._prop(src, "time_pos", 0) or 0)
                paused = bool(self._prop(src, "pause", False))
        if rt["item"] is not cur:
            rt["item"] = cur
            tile.index = self._find(cur["id"]) if self._find(cur["id"]) is not None else -1
            self._preview_load(tile, dict(cur, aspect="fit"), pos if follow else 0)
            if not follow:
                self._setp(pv, "loop_file", "inf")
        elif follow:
            self._sync_preview(tile, pos, paused)

    def _mv_thumb_tile(self, rt, cur, pv):
        """Slideshow / live YouTube that no output plays: its thumbnail (first slide / video thumbnail) instead of starting it."""
        if rt["item"] is not None:
            try:
                pv.command("stop")
                self._osd_overlay(pv, None)
            except Exception:
                pass
            rt["item"], rt["lkey"] = None, None
        cache = self.__dict__.setdefault("_mv_thumbs", {})
        iid = cur["id"]
        ent = cache.get(iid)
        if ent is None:
            cache[iid] = "busy"
            ff = self._ffmpeg_path()
            cache_dir = os.path.join(self._app_dir(), "player_cache")

            def work():
                path = None
                try:
                    if cur.get("is_slideshow"):
                        for p in (cur.get("ss") or {}).get("media", []):
                            if ff and os.path.isfile(p):
                                path = ss_thumb(ff, p, cache_dir, (640, 360))
                                if path:
                                    break
                    elif not cur.get("is_youtube"):
                        if ff and os.path.isfile(cur["path"]):
                            path = ss_thumb(ff, cur["path"], cache_dir, (640, 360))
                    else:
                        _title, png = fetch_youtube_preview(cur["path"], ff)
                        if png:
                            os.makedirs(os.path.join(cache_dir, "thumbs"), exist_ok=True)
                            path = os.path.join(cache_dir, "thumbs", f"yt_{iid}.png")
                            with open(path, "wb") as fh:
                                fh.write(png)
                except Exception:
                    path = None
                self._uiq.put(lambda: cache.__setitem__(iid, path or ""))
            threading.Thread(target=work, daemon=True).start()
            ent = "busy"
        if ent == "busy" or rt.get("thumb") == (iid, ent):
            return
        rt["thumb"] = (iid, ent)
        try:
            if ent:
                rt["note"].configure(text="")
                self._apply_aspect(pv, "fit")
                self._rtmp_props(pv, False)
                self._setp(pv, "loop_file", "no")
                pv.play(ent)
            else:
                pv.command("stop")
                rt["note"].configure(text=(cur.get("name") or "")[:40])
        except SHUTDOWN:
            raise
        except Exception:
            pass

    # -- save / load
    def _mv_data(self):
        mv = self._mvs()
        idx = {it["id"]: k for k, it in enumerate(self.items)}
        tiles = []
        for t in mv["tiles"]:
            ref = t["ref"] if t["kind"] == "out" else idx.get(t["ref"])
            if ref is None:
                continue
            tiles.append({"kind": t["kind"], "ref": ref, **{k: round(t[k], 4) for k in ("x", "y", "w", "h")}})
        return {"geo": list(mv["geo"]) if mv["geo"] else None, "tiles": tiles, "quality": mv.get("q", "auto")}

    def _mv_load(self, raw):
        mv = self._mvs()
        if mv["on"]:
            self.stop_multiview()
        mv["tiles"], mv["sel"] = [], None
        mv["rt"] = {}
        mv["q"] = "auto"
        if not isinstance(raw, dict):
            mv["geo"] = None
            return
        if raw.get("quality") in MV_QUALITIES:
            mv["q"] = raw["quality"]
        self._mv_q_show()
        g = raw.get("geo")
        mv["geo"] = tuple(int(v) for v in g) if (isinstance(g, list) and len(g) == 4 and all(isinstance(v, int) for v in g)) else None
        for r in raw.get("tiles", []) if isinstance(raw.get("tiles"), list) else []:
            if not isinstance(r, dict) or r.get("kind") not in ("out", "in") or not isinstance(r.get("ref"), int):
                continue
            if r["kind"] == "in":
                if not (0 <= r["ref"] < len(self.items)):
                    continue
                ref = self.items[r["ref"]]["id"]
            else:
                ref = r["ref"]
            vals = {k: num(r.get(k), 0.1, 0.0, 1.0) for k in ("x", "y", "w", "h")}
            vals["w"], vals["h"] = max(0.06, vals["w"]), max(0.06, vals["h"])
            mv["uid"] += 1
            mv["tiles"].append({"uid": mv["uid"], "kind": r["kind"], "ref": ref, **vals})
        self._mv_fill_lists()
        self._mv_fill_pick()
        self._mv_redraw_editor()

    # ---- Main / Backup: two computers kept identical -------------------------------------------------------------------
    def _sy(self):
        if not hasattr(self, "sync"):
            self.sync = {"role": "off", "server": None, "client": None, "hash": None, "rev": 0, "files": {}, "fcache": {}, "queue": [],
                         "dl_total": 0, "dl_done": 0, "dl_busy": False, "hold_until": 0.0, "promoted": False, "applied": 0, "n": 0,
                         "last_apply": 0.0, "err_once": False, "dl_stop": False}
        return self.sync

    def _sync_cfg(self):
        c = self.cfg.setdefault("sync", {})
        c.setdefault("role", "off")
        c.setdefault("host", "")
        c.setdefault("port", SYNC_PORT)
        c.setdefault("key", "")
        c.setdefault("fail", 3)
        return c

    def _is_backup(self):
        s = getattr(self, "sync", None)
        return bool(s and s["role"] == "backup")

    def _sync_media_dir(self):
        d = os.path.join(os.path.expanduser("~"), ".leitor_sync", "media")
        os.makedirs(d, exist_ok=True)
        return d

    def _sync_style(self):
        s = self._sy()
        btn = getattr(self, "sync_btn", None)
        if btn is not None:
            style = "TButton"
            if s["role"] == "main":
                style = "Red.TButton"                              # Main: red
            elif s["role"] == "backup":
                style = "Green.TButton"                            # Backup: green
            try:
                btn.configure(style=style)
            except tk.TclError:
                pass
        self._sync_prog_update()
        self._update_title()

    def _sync_label(self, pct=None):
        role = self._sy()["role"]
        tag = {"main": " · Main", "backup": " · Backup"}.get(role, "")
        return self.t("btn_sync") + tag + (f" {pct}%" if pct is not None else "")

    def _sync_prog_update(self):
        """Sync button: «Sync · Main» / «Sync · Backup», and while files are being copied a bar under it and the percentage."""
        s = self._sy()
        done = total = 0
        if s["role"] == "backup" and (s["dl_busy"] or s["queue"]):
            done, total = s["dl_done"], s["dl_total"]
        elif s["role"] == "main" and s["server"] is not None:
            done, total = s["server"].progress()
        try:
            bar, btn = self.sync_bar, self.sync_btn
            if total > 0:
                pct = max(0, min(100, int(done * 100 / total)))
                bar["value"] = pct
                if not bar.winfo_ismapped():
                    bar.pack(side="top", fill="x", pady=(2, 0))
                text = self._sync_label(pct)
            else:
                if bar.winfo_ismapped():
                    bar.pack_forget()
                text = self._sync_label()
            if s.get("btn_text") != text:
                s["btn_text"] = text
                btn.configure(text=text)
        except (tk.TclError, AttributeError):
            pass

    def _sync_prog_tick(self):
        if self._closing:
            return
        s = self._sy()
        try:
            self._sync_prog_update()
            cl = s["client"]
            if s["role"] == "backup" and cl is not None and cl.connected:
                cl.send({"t": "prog", "done": s["dl_done"] if (s["dl_busy"] or s["queue"]) else 0,
                         "total": s["dl_total"] if (s["dl_busy"] or s["queue"]) else 0})
        except SHUTDOWN:
            raise
        except Exception:
            pass
        self.root.after(400, self._sync_prog_tick)

    def _sync_autostart(self):
        self._sync_prog_tick()
        c = self._sync_cfg()
        if c["role"] in ("main", "backup") and c["key"]:
            self._sync_start(c["role"])

    def _sync_start(self, role):
        self._sync_stop(quiet=True)
        c, s = self._sync_cfg(), self._sy()
        if not c["key"]:
            c["key"] = os.urandom(3).hex()
        c["role"] = role
        save_config(self.cfg)
        s.update(hash=None, rev=0, files={}, fcache={}, queue=[], dl_total=0, dl_done=0, promoted=False, applied=0, n=0, dl_stop=False)
        red = THEMES[self.theme_name]["RED"]
        if role == "main":
            try:
                s["server"] = SyncServer(c["key"], int(c["port"]), lambda: set(self._sy()["files"]))
            except OSError as exc:
                self.status_t("sy_err_listen", red, err=exc)
                c["role"] = "off"
                self._sync_style()
                return
            s["role"] = "main"
            diag_log(f"sync: Main listening on {c['port']} (files {int(c['port']) + 1})")
            self.root.after(300, self._sync_main_tick)
        elif role == "backup":
            if not c["host"].strip():
                self.status_t("sy_err_host", red)
                c["role"] = "off"
                self._sync_style()
                return
            s["role"] = "backup"
            s["client"] = SyncClient(c["host"].strip(), int(c["port"]), c["key"], lambda m: self._uiq.put(lambda: self._sync_on_msg(m)))
            diag_log(f"sync: Backup connecting to {c['host']}:{c['port']}")
            self.root.after(1000, self._sync_watch)
        self._sync_style()

    def _sync_stop(self, quiet=False):
        s = self._sy()
        s["dl_stop"] = True
        for k in ("server", "client"):
            obj, s[k] = s[k], None
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        s["role"] = "off"
        if not quiet:
            c = self._sync_cfg()
            c["role"] = "off"
            save_config(self.cfg)
        self._sync_style()

    # -- Main
    def _sync_main_tick(self):
        s = self._sy()
        if s["role"] != "main" or s["server"] is None or self._closing:
            return
        try:
            s["n"] += 1
            if s["n"] % 4 == 1:
                self._sync_main_state()
            s["server"].broadcast(self._sync_play_msg())
        except SHUTDOWN:
            raise
        except Exception as exc:
            if not s["err_once"]:
                s["err_once"] = True
                diag_log(f"sync Main: {exc}\n{traceback.format_exc()}")
        self.root.after(250, self._sync_main_tick)

    def _sync_main_state(self):
        s = self._sy()
        data = self._playlist_data(os.path.join(os.getcwd(), "sync.mmp"))
        for it in data["items"]:
            it["rel"] = None
        h = sync_struct_hash(data)
        if h == s["hash"]:
            return
        s["hash"] = h
        s["rev"] += 1
        s["fcache"] = {}
        s["files"] = sync_scan_files(data, s["fcache"])
        s["server"].broadcast({"t": "state", "rev": s["rev"], "hash": h, "data": data,
                               "files": [{"p": p, "s": z} for p, z in s["files"].items()]}, remember=True)
        diag_log(f"sync Main: show sent (rev {s['rev']}, {len(data['items'])} items, {len(s['files'])} files)")

    def _sync_play_msg(self):
        outs = []
        for o in self.outs:
            on = o.m is not None
            i, pos, paused = -1, 0.0, False
            if on and o.index >= 0:
                i = o.index
                pos = float(self._prop(o, "time_pos", 0) or 0)
                paused = bool(self._prop(o, "pause", False))
            outs.append({"n": o.num, "i": i, "on": on, "win": bool(on and not o._headless and not getattr(o, "_hidden", False)),
                         "pos": round(pos, 3), "p": paused, "v": int(round(float(o.vol.get()))),
                         "mu": bool(getattr(o, "out_mute", None) and o.out_mute.get())})
        g = {"mute": bool(self.mute.get()), "black": bool(self.blackout.get()),
             "mv": bool(getattr(self, "mv", {}).get("on")), "co": bool(getattr(self, "co", {}).get("on")),
             "shuf": self.shuffle_mode.get() if hasattr(self, "shuffle_mode") else "off",
             "lock": bool(getattr(self, "kb_locked", False))}
        return {"t": "play", "outs": outs, "g": g}

    # -- Backup
    def _sync_on_msg(self, msg):
        if not self._is_backup() or self._closing:
            return
        try:
            if msg.get("t") == "state":
                self._sync_apply_state(msg)
            elif msg.get("t") == "play":
                self._sync_apply_play(msg)
        except SHUTDOWN:
            raise
        except Exception as exc:
            s = self._sy()
            if not s["err_once"]:
                s["err_once"] = True
                diag_log(f"sync Backup: {exc}\n{traceback.format_exc()}")

    def _sync_localize(self, data):
        """The Backup keeps its own screens, audio devices and Multiview / Composite screens (they differ between computers)."""
        mine = {o.num: o for o in self.outs}
        for od in data.get("outputs", []):
            if not isinstance(od, dict):
                continue
            lo = mine.get(od.get("num"))
            if lo is not None:
                od["screen"] = list(lo.sel_geo) if lo.sel_geo is not None else None
                od["audio"] = json.loads(json.dumps(lo.ar))
            else:
                od.pop("screen", None)
                od.pop("audio", None)
        mv, co = data.get("multiview"), data.get("composite")
        if isinstance(mv, dict):
            g = getattr(self, "mv", {}).get("geo")
            mv["geo"] = list(g) if g else None
        if isinstance(co, dict):
            sc = getattr(self, "co", {}).get("screen")
            co["screen"] = list(sc) if sc else None
        return data

    def _sync_apply_state(self, msg):
        s = self._sy()
        data = msg.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return
        h = msg.get("hash") or sync_struct_hash(data)
        if h == s["hash"]:
            return
        s["hash"] = h
        folder = self._sync_media_dir()
        mapping, need = {}, []
        for f in msg.get("files", []):
            p, sz = f.get("p"), f.get("s")
            if not isinstance(p, str) or not isinstance(sz, int):
                continue
            try:
                if os.path.isfile(p) and os.path.getsize(p) == sz:
                    continue                                         # the same file is already at this path (shared drive / copied)
            except OSError:
                pass
            loc = sync_local_path(folder, p)
            mapping[p] = loc
            if not (os.path.isfile(loc) and os.path.getsize(loc) == sz):
                need.append((p, loc, sz))
        data = self._sync_localize(sync_rewrite(data, mapping))
        s["files"] = mapping
        self._sync_enqueue(need)
        self._apply_playlist_data(data, folder, None, keep=True)
        s["hold_until"] = time.time() + 1.5
        s["applied"] += 1
        s["last_apply"] = time.time()
        diag_log(f"sync Backup: show applied ({len(data['items'])} items, {len(need)} files to copy)")

    def _sync_enqueue(self, need):
        s = self._sy()
        queued = {q[0] for q in s["queue"]}
        for p, loc, sz in need:
            if p not in queued:
                s["queue"].append((p, loc, sz))
                s["dl_total"] += sz
        if s["queue"] and not s["dl_busy"]:
            s["dl_busy"], s["dl_stop"] = True, False
            threading.Thread(target=self._sync_dl_worker, daemon=True).start()

    def _sync_dl_worker(self):
        s, c = self._sy(), self._sync_cfg()
        host, port, key = c["host"].strip(), int(c["port"]), c["key"]
        fails = 0
        while s["queue"] and not s["dl_stop"] and not self._closing:
            p, loc, sz = s["queue"][0]

            def prog(n):
                s["dl_done"] += n
            ok = sync_download(host, port, key, p, loc, prog, lambda: s["dl_stop"] or self._closing)
            if ok:
                if s["queue"] and s["queue"][0][0] == p:
                    s["queue"].pop(0)
                fails = 0
                self._uiq.put(self._sync_files_ready)
            else:
                fails += 1
                time.sleep(min(10, 1 + fails))
        s["dl_busy"] = False
        if not s["queue"]:
            s["dl_total"] = s["dl_done"] = 0
            self._uiq.put(self._sync_files_ready)

    def _sync_files_ready(self):
        for it in self.items:
            if it.get("is_group") or it.get("is_youtube") or is_live(it) or it.get("is_slideshow") or str(it.get("path", "")).startswith(("http://", "https://")):
                continue
            miss = not os.path.exists(it["path"])
            if it.get("missing") != miss:
                it["missing"] = miss
                self._refresh_row(it)

    def _sync_apply_play(self, msg):
        s, now = self._sy(), time.time()
        if now < s["hold_until"]:
            return
        for m in msg.get("outs", []):
            lo = next((o for o in self.outs if o.num == m.get("n")), None)
            if lo is None:
                continue
            st = lo.__dict__.setdefault("_sy", {"t": 0.0, "sk": 0.0})
            i = m.get("i", -1)
            if i < 0 or i >= len(self.items):
                if lo.index >= 0 and lo.m is not None:
                    self.stop(lo)
                if not m.get("on") and lo.m is not None and now - st["t"] > 1.0:
                    st["t"] = now
                    self.stop_output(lo)
                continue
            item = self.items[i]
            if lo.index != i or lo.m is None:
                if now - st["t"] > 2.0 and not item.get("missing"):
                    st["t"] = now
                    self.play_index(i)
                continue
            if lo.m is not None and not is_live(item):
                lp = self._prop(lo, "time_pos", None)
                target = float(m.get("pos", 0.0))
                if lp is not None and abs(float(lp) - target) > 0.6 and now - st["sk"] > 1.5:
                    st["sk"] = now
                    try:
                        lo.m.seek(target, "absolute")
                    except SHUTDOWN:
                        raise
                    except Exception:
                        pass
            if lo.m is not None and bool(self._prop(lo, "pause", False)) != bool(m.get("p")):
                try:
                    lo.m.pause = bool(m.get("p"))
                except SHUTDOWN:
                    raise
                except Exception:
                    pass
            if m.get("win") and lo._headless and self._screen_index(lo) is not None and now - st["t"] > 1.5:
                st["t"] = now
                self.toggle_output(lo)
            elif not m.get("win") and lo.m is not None and not lo._headless and not getattr(lo, "_hidden", False) and now - st["t"] > 1.5:
                st["t"] = now
                self.toggle_output(lo)
        for m in msg.get("outs", []):
            lo = next((o for o in self.outs if o.num == m.get("n")), None)
            if lo is None:
                continue
            v = int(m.get("v", lo.vol.get()))
            if int(lo.vol.get()) != v:
                lo.vol.set(v)
                if lo.m is not None:
                    self._setp(lo.m, "volume", float(v) * lo._fade_f)
            mu = bool(m.get("mu"))
            if hasattr(lo, "out_mute") and bool(lo.out_mute.get()) != mu:
                lo.out_mute.set(mu)
                self._toggle_output_mute(lo)
        g = msg.get("g") or {}
        if bool(self.mute.get()) != bool(g.get("mute")):
            self.mute.set(bool(g.get("mute")))
            self.on_mute()
        if bool(self.blackout.get()) != bool(g.get("black")):
            self.blackout.set(bool(g.get("black")))
            self.toggle_blackout()
        if bool(getattr(self, "kb_locked", False)) != bool(g.get("lock")):
            self.toggle_kb_lock()
        if hasattr(self, "shuffle_mode") and g.get("shuf") in ("off", "all", "selected", "output") and self.shuffle_mode.get() != g["shuf"]:
            self.shuffle_mode.set(g["shuf"])
            self.shuffle_var.set(g["shuf"] != "off")
            self._update_shuffle_btn()
        mv, co = getattr(self, "mv", None), getattr(self, "co", None)
        if mv is not None and mv.get("geo") and bool(mv["on"]) != bool(g.get("mv")):
            (self.start_multiview if g.get("mv") else self.stop_multiview)()
        if co is not None and co.get("geo") and bool(co["on"]) != bool(g.get("co")):
            (self.start_composite if g.get("co") else self.stop_composite)()

    def _sync_watch(self):
        s, c = self._sy(), self._sync_cfg()
        if s["role"] != "backup" or self._closing:
            return
        cl = s["client"]
        try:
            if cl is not None and cl.ever and time.time() - cl.last_rx > float(c["fail"]):
                self._sync_promote(auto=True)
                return
            self._sync_style()
        except SHUTDOWN:
            raise
        except Exception as exc:
            diag_log(f"sync watch: {exc}")
        self.root.after(1000, self._sync_watch)

    def _sync_promote(self, auto=False):
        """The Backup becomes the Main (the Main stopped answering, or the user said so). It keeps playing from where it is."""
        s = self._sy()
        if s["role"] != "backup":
            return
        diag_log(f"sync: Backup promoted to Main ({'Main stopped answering' if auto else 'by the user'})")
        self._sync_stop(quiet=True)
        self._sync_start("main")
        s["promoted"] = True
        self.status_t("sy_promoted", THEMES[self.theme_name]["RED"])
        if auto:
            self.root.after(200, lambda: messagebox.showwarning(self.t("sy_title"), self.t("sy_promoted_msg"), parent=self.root))

    # -- window
    def open_sync(self):
        old = getattr(self, "_sy_win", None)
        try:
            if old is not None and old.winfo_exists():
                old.lift()
                return
        except tk.TclError:
            pass
        theme = THEMES[self.theme_name]
        c, s = self._sync_cfg(), self._sy()
        w = tk.Toplevel(self.root)
        self._sy_win = w
        w.title(self.t("sy_title"))
        w.geometry("560x470")
        w.configure(bg=theme["BG"])
        w.transient(self.root)
        pad = ttk.Frame(w, padding=14)
        pad.pack(fill="both", expand=True)
        role_v = tk.StringVar(value=s["role"] if s["role"] != "off" else c["role"] if c["role"] != "off" else "main")
        host_v, port_v, key_v, fail_v = tk.StringVar(value=c["host"]), tk.StringVar(value=str(c["port"])), tk.StringVar(value=c["key"]), tk.StringVar(value=str(c["fail"]))
        ttk.Label(pad, text=self.t("sy_role")).grid(row=0, column=0, sticky="w")
        rf = ttk.Frame(pad)
        rf.grid(row=0, column=1, sticky="w", pady=2)
        ttk.Radiobutton(rf, text="Main", value="main", variable=role_v, takefocus=False).pack(side="left", padx=(0, 14))
        ttk.Radiobutton(rf, text="Backup", value="backup", variable=role_v, takefocus=False).pack(side="left")
        rows = (("sy_host", host_v, 22), ("sy_port", port_v, 8), ("sy_key", key_v, 16), ("sy_fail", fail_v, 5))
        for n, (lab, var, wd) in enumerate(rows, start=1):
            ttk.Label(pad, text=self.t(lab)).grid(row=n, column=0, sticky="w", pady=2)
            ttk.Entry(pad, textvariable=var, width=wd).grid(row=n, column=1, sticky="w", pady=2)
        status = ttk.Label(pad, text="", wraplength=500, justify="left")
        status.grid(row=6, column=0, columnspan=2, sticky="w", pady=(12, 4))
        ttk.Label(pad, text=self.t("sy_hint"), style="Hint.TLabel", wraplength=500, justify="left").grid(row=8, column=0, columnspan=2, sticky="w", pady=(8, 0))
        bar = ttk.Frame(pad)
        bar.grid(row=7, column=0, columnspan=2, sticky="w", pady=6)

        def apply_cfg():
            try:
                c["port"] = max(1024, min(65000, int(port_v.get())))
                c["fail"] = max(2, min(60, int(fail_v.get())))
            except ValueError:
                pass
            c["host"], c["key"] = host_v.get().strip(), key_v.get().strip()

        def start():
            apply_cfg()
            if not c["key"]:
                key_v.set(os.urandom(3).hex())
                apply_cfg()
            self._sync_start(role_v.get())
            refresh()

        def stop():
            self._sync_stop()
            refresh()

        def promote():
            self._sync_promote()
            refresh()
        ttk.Button(bar, text=self.t("sy_start"), command=start, style="Accent.TButton", takefocus=False).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text=self.t("sy_stop"), command=stop, takefocus=False).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text=self.t("sy_promote"), command=promote, takefocus=False).pack(side="left")

        def refresh():
            try:
                if not w.winfo_exists():
                    return
            except tk.TclError:
                return
            st = self._sy()
            lines = []
            if st["role"] == "main":
                n = st["server"].count() if st["server"] else 0
                lines.append(self.t("sy_st_main", n=n, port=c["port"]))
                d, tt = st["server"].progress() if st["server"] else (0, 0)
                if tt:
                    lines.append(self.t("sy_st_bk_files", done=d // 1048576, total=tt // 1048576))
                if st["rev"]:
                    lines.append(self.t("sy_st_sent", n=st["rev"], f=len(st["files"])))
            elif st["role"] == "backup":
                cl = st["client"]
                if cl is not None and cl.denied:
                    lines.append(self.t("sy_st_denied"))
                elif cl is not None and cl.connected:
                    lines.append(self.t("sy_st_conn", host=c["host"]))
                else:
                    lines.append(self.t("sy_st_wait", host=c["host"], err=(cl.err if cl else "")))
                if st["last_apply"]:
                    lines.append(self.t("sy_st_applied", t=time.strftime("%H:%M:%S", time.localtime(st["last_apply"])), n=st["applied"]))
                if st["dl_total"]:
                    lines.append(self.t("sy_st_files", done=st["dl_done"] // 1048576, total=st["dl_total"] // 1048576, left=len(st["queue"])))
            else:
                lines.append(self.t("sy_st_off"))
            if st["promoted"]:
                lines.append(self.t("sy_st_promoted"))
            status.configure(text="\n".join(lines))
            w.after(700, refresh)
        refresh()

    # ---- Output Composto: one big virtual output (canvas) made of slices, shown fullscreen on a screen ---------------------
    def _co_state(self):
        if not hasattr(self, "co"):
            self.co = {"geo": None, "screen": None, "tiles": [], "on": False, "win": None, "ctl": None, "rt": {}, "uid": 0,
                       "sel": None, "q": "auto", "fstate": {}, "cw": 1920, "ch": 1080}
        return self.co

    @contextlib.contextmanager
    def _mv_as(self, name):
        """Runs the Multiview engine (tile update, copies, slots) on another state: "co" = the composite output."""
        prev = getattr(self, "_mv_ctx", None)
        self._mv_ctx = name
        try:
            yield
        finally:
            self._mv_ctx = prev

    def _all_rts(self):
        return [r for st in (getattr(self, "mv", None), getattr(self, "co", None)) if st for r in st["rt"].values()]

    def _co_screen(self):
        co = getattr(self, "co", None)
        return tuple(co["screen"]) if co and co.get("screen") else None

    def _co_set_geo(self):
        co = self._co_state()
        s = co["screen"]
        co["geo"] = (s[0], s[1], co["cw"], co["ch"]) if s else None

    def _co_add_tile(self, kind, ref, fx, fy, box=None):
        co = self._co_state()
        w = 0.3
        h = min(0.9, w * (co["cw"] / co["ch"]) * 9 / 16)
        co["uid"] += 1
        x, y, w, h = box if box else (max(0.0, min(1 - w, fx - w / 2)), max(0.0, min(1 - h, fy - h / 2)), w, h)
        t = {"uid": co["uid"], "kind": kind, "ref": ref, "x": x, "y": y, "w": w, "h": h, "crop": [0.0] * 4, "rot": 0,
             "fx": False, "fy": False, "pin": None, "blend": [0.0] * 4, "aspect": "fit", "co": True}
        co["tiles"].append(t)
        co["sel"] = t["uid"]
        if co["on"]:
            self._co_build_tile(t)
        self._co_props_load()
        self._co_redraw()
        return t

    def _co_remove_tile(self, t):
        co = self._co_state()
        with self._mv_as("co"):
            self._mv_drop_tile(t["uid"])
        co["tiles"] = [x for x in co["tiles"] if x is not t]
        if co["sel"] == t["uid"]:
            co["sel"] = None
        self._co_props_load()
        self._co_redraw()

    def _co_sel_tile(self):
        co = self._co_state()
        return next((t for t in co["tiles"] if t["uid"] == co["sel"]), None)

    # -- editor window
    def open_composite(self):
        co = self._co_state()
        ctl = co.get("ctl")
        try:
            if ctl is not None and ctl.winfo_exists():
                ctl.deiconify()
                ctl.lift()
                return
        except tk.TclError:
            pass
        theme = THEMES[self.theme_name]
        win = tk.Toplevel(self.root)
        co["ctl"] = win
        win.title(self.t("co_title"))
        win.configure(bg=theme["BG"])
        win.geometry("1180x640")
        win.minsize(980, 520)
        win.protocol("WM_DELETE_WINDOW", self._co_close_editor)
        top = ttk.Frame(win, padding=(10, 8, 10, 2))
        top.pack(fill="x")
        ttk.Label(top, text=self.t("mv_screen")).pack(side="left", padx=(0, 6))
        self.co_pick = ttk.Combobox(top, state="readonly", width=30)
        self.co_pick.pack(side="left")
        self.co_pick.bind("<<ComboboxSelected>>", self._co_pick_changed)
        self.co_btn = ttk.Button(top, command=self.toggle_composite, takefocus=False)
        self.co_btn.pack(side="left", padx=10)
        ttk.Label(top, text=self.t("co_res")).pack(side="left", padx=(8, 6))
        self.co_wv, self.co_hv = tk.StringVar(value=str(co["cw"])), tk.StringVar(value=str(co["ch"]))
        self.co_preset = ttk.Combobox(top, state="readonly", width=11, values=[f"{w}×{h}" for w, h in CO_PRESETS])
        self.co_preset.pack(side="left")
        self.co_preset.bind("<<ComboboxSelected>>", self._co_preset_picked)
        ttk.Entry(top, textvariable=self.co_wv, width=6).pack(side="left", padx=(6, 0))
        ttk.Label(top, text="×").pack(side="left", padx=2)
        ttk.Entry(top, textvariable=self.co_hv, width=6).pack(side="left")
        ttk.Button(top, text=self.t("co_apply"), command=self._co_apply_size, takefocus=False).pack(side="left", padx=6)
        ttk.Button(top, text=self.t("mv_clear"), command=self._co_clear, takefocus=False).pack(side="left")
        top2 = ttk.Frame(win, padding=(10, 0, 10, 4))
        top2.pack(fill="x")
        ttk.Label(top2, text=self.t("co_unite_lbl")).pack(side="left", padx=(0, 6))
        self.co_arr = ttk.Combobox(top2, state="readonly", width=11, values=[self.t("co_arr_h"), self.t("co_arr_v"), self.t("co_arr_g")])
        self.co_arr.current(0)
        self.co_arr.pack(side="left")
        ttk.Button(top2, text=self.t("co_unite"), command=self._co_unite, takefocus=False).pack(side="left", padx=8)
        ttk.Label(top2, text=self.t("co_hint"), style="Hint.TLabel").pack(side="right")
        body = ttk.Frame(win, padding=(10, 0, 10, 10))
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        left.pack(side="left", fill="y", padx=(0, 10))
        self.co_lists = {}
        for kind, key in (("out", "mv_outputs"), ("in", "mv_inputs")):
            ttk.Label(left, text=self.t(key)).pack(anchor="w", pady=(4, 2))
            holder = ttk.Frame(left)
            holder.pack(fill="both", expand=True)
            lb = tk.Listbox(holder, width=28, height=7 if kind == "out" else 12, exportselection=False, selectmode="extended",
                            bg=theme["PANEL"], fg=theme["FG"], highlightthickness=0, bd=0,
                            selectbackground=theme["ACCENT"], activestyle="none")
            sb = ttk.Scrollbar(holder, orient="vertical", command=lb.yview, style="Vertical.TScrollbar")
            hb = ttk.Scrollbar(holder, orient="horizontal", command=lb.xview, style="Horizontal.TScrollbar")
            lb.configure(yscrollcommand=sb.set, xscrollcommand=hb.set)
            hb.pack(side="bottom", fill="x")
            lb.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            self.co_lists[kind] = lb
            lb.bind("<ButtonPress-1>", lambda e, k=kind: self._co_swap(self._mv_list_press, e, k) or "break")
            lb.bind("<B1-Motion>", self._mv_list_motion)
            lb.bind("<ButtonRelease-1>", self._co_list_release)
            for ev in ("<B1-Leave>", "<B1-Enter>", "<Leave>"):
                lb.bind(ev, lambda e: "break")
            for ev, d, sh in (("<Up>", -1, False), ("<Down>", 1, False), ("<Shift-Up>", -1, True), ("<Shift-Down>", 1, True)):
                lb.bind(ev, lambda e, k=kind, d=d, sh=sh: self._co_swap(self._mv_list_key, k, d, sh))
            lb.bind("<Enter>", lambda e: e.widget.focus_set())
        self.co_canvas = tk.Canvas(body, bg="#050505", highlightthickness=1, highlightbackground=theme["MUTED"])
        self.co_canvas.pack(side="left", fill="both", expand=True)
        self.co_canvas.bind("<Configure>", lambda _e: self._co_redraw())
        self.co_canvas.bind("<ButtonPress-1>", self._co_cv_press)
        self.co_canvas.bind("<B1-Motion>", self._co_cv_motion)
        self.co_canvas.bind("<ButtonRelease-1>", self._co_cv_release)
        self.co_canvas.bind("<Button-3>", self._co_cv_context)
        self._co_build_props(body)
        self._co_fill_lists()
        self._co_fill_pick()
        self._co_style_btn()
        self._co_props_load()
        self._co_poll()

    def _co_swap(self, fn, *a):
        """The Multiview's list handlers, run on this window's lists."""
        saved = (self.__dict__.get("mv_lists"), self.__dict__.get("_mv_list_refs"))
        self.mv_lists, self._mv_list_refs = self.co_lists, getattr(self, "_co_list_refs", {})
        try:
            return fn(*a)
        finally:
            self.mv_lists = saved[0]
            self._mv_list_refs = saved[1]
            if saved[0] is None:
                self.__dict__.pop("mv_lists", None)
            if saved[1] is None:
                self.__dict__.pop("_mv_list_refs", None)

    def _co_build_props(self, body):
        pf = ttk.Frame(body, padding=(10, 0, 0, 0))
        pf.pack(side="right", fill="y")
        self.co_vars = {}
        self._co_loading = False
        self.co_title_lbl = ttk.Label(pf, text="", font=("Segoe UI", 10, "bold"), wraplength=210)
        self.co_title_lbl.pack(anchor="w", pady=(4, 6))

        def spin_row(parent, title, keys):
            ttk.Label(parent, text=title).pack(anchor="w", pady=(6, 0))
            row = ttk.Frame(parent)
            row.pack(anchor="w")
            for k, lab in zip(keys, ("co_l", "co_t", "co_r", "co_b")):
                ttk.Label(row, text=self.t(lab), style="Hint.TLabel").grid(row=0, column=("co_l", "co_t", "co_r", "co_b").index(lab), padx=2)
            for n, k in enumerate(keys):
                v = tk.StringVar(value="0")
                self.co_vars[k] = v
                sp = ttk.Spinbox(row, from_=0, to=50 if k.startswith("b") else 90, increment=1, width=4, textvariable=v, command=self._co_props_apply)
                sp.grid(row=1, column=n, padx=2)
                sp.bind("<Return>", lambda _e: self._co_props_apply())
                sp.bind("<FocusOut>", lambda _e: self._co_props_apply())
        spin_row(pf, self.t("co_crop"), ("c0", "c1", "c2", "c3"))
        ttk.Label(pf, text=self.t("co_rot")).pack(anchor="w", pady=(8, 0))
        self.co_rot = ttk.Combobox(pf, state="readonly", width=8, values=["0°", "90°", "180°", "270°"])
        self.co_rot.pack(anchor="w")
        self.co_rot.bind("<<ComboboxSelected>>", lambda _e: self._co_props_apply())
        for k, lab in (("fx", "co_flip_h"), ("fy", "co_flip_v")):
            v = tk.BooleanVar(value=False)
            self.co_vars[k] = v
            ttk.Checkbutton(pf, text=self.t(lab), variable=v, command=self._co_props_apply, takefocus=False).pack(anchor="w", pady=(4, 0))
        ttk.Label(pf, text=self.t("co_fit")).pack(anchor="w", pady=(8, 0))
        self.co_asp = ttk.Combobox(pf, state="readonly", width=12, values=[self.t("co_fit_fit"), self.t("co_fit_stretch"), self.t("co_fit_fill")])
        self.co_asp.pack(anchor="w")
        self.co_asp.bind("<<ComboboxSelected>>", lambda _e: self._co_props_apply())
        spin_row(pf, self.t("co_blend"), ("b0", "b1", "b2", "b3"))
        ttk.Button(pf, text=self.t("co_pin_reset"), command=self._co_pin_reset, takefocus=False).pack(anchor="w", pady=(12, 0))
        ttk.Button(pf, text=self.t("co_remove"), command=lambda: self._co_sel_tile() and self._co_remove_tile(self._co_sel_tile()),
                   takefocus=False).pack(anchor="w", pady=(6, 0))

    def _co_props_load(self):
        if not self._co_editor_alive() or not hasattr(self, "co_vars"):
            return
        t = self._co_sel_tile()
        self._co_loading = True
        try:
            self.co_title_lbl.configure(text=self._mv_source_name(t) if t else self.t("co_none_sel"))
            c, b = (t["crop"] if t else [0] * 4), (t["blend"] if t else [0] * 4)
            for n in range(4):
                self.co_vars[f"c{n}"].set(str(int(round(c[n] * 100))))
                self.co_vars[f"b{n}"].set(str(int(round(b[n] * 100))))
            self.co_vars["fx"].set(bool(t and t["fx"]))
            self.co_vars["fy"].set(bool(t and t["fy"]))
            self.co_rot.current([0, 90, 180, 270].index(t["rot"]) if t and t["rot"] in (0, 90, 180, 270) else 0)
            self.co_asp.current(["fit", "stretch", "fill"].index(t["aspect"]) if t and t["aspect"] in ("fit", "stretch", "fill") else 0)
        finally:
            self._co_loading = False

    def _co_props_apply(self):
        if getattr(self, "_co_loading", False):
            return
        t = self._co_sel_tile()
        if t is None:
            return

        def pct(k, hi):
            try:
                return max(0.0, min(hi, float(self.co_vars[k].get()) / 100.0))
            except (ValueError, tk.TclError):
                return 0.0
        t["crop"] = [pct(f"c{n}", 0.9) for n in range(4)]
        t["blend"] = [pct(f"b{n}", 0.5) for n in range(4)]
        t["fx"], t["fy"] = bool(self.co_vars["fx"].get()), bool(self.co_vars["fy"].get())
        t["rot"] = [0, 90, 180, 270][max(0, self.co_rot.current())]
        t["aspect"] = ["fit", "stretch", "fill"][max(0, self.co_asp.current())]
        self._co_redraw()

    def _co_pin_reset(self):
        t = self._co_sel_tile()
        if t is not None:
            t["pin"] = None
            self._co_redraw()

    def _co_editor_alive(self):
        ctl = self._co_state().get("ctl")
        try:
            return ctl is not None and ctl.winfo_exists()
        except tk.TclError:
            return False

    def _co_close_editor(self):
        co = self._co_state()
        win, co["ctl"] = co.get("ctl"), None
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass

    def _co_poll(self):
        if not self._co_editor_alive() or self._closing:
            return
        self.refresh_monitors()
        msig = getattr(self, "_mon_sig", None)
        if msig != getattr(self, "_co_msig", None):
            self._co_msig = msig
            co = self._co_state()
            if co["screen"] and not any(geo(mo) == co["screen"] for mo in self.monitors):
                if co["on"]:
                    self.stop_composite()
                co["screen"] = None
                self._co_set_geo()
            self._co_fill_pick()
            self._co_redraw()
        sig = self._mv_signature()
        if sig != getattr(self, "_co_sig", None) and not getattr(self, "_mv_src", None):
            self._co_sig = sig
            self._co_fill_lists()
            self._co_redraw()
        self.root.after(700, self._co_poll)

    def _co_fill_lists(self):
        if not self._co_editor_alive():
            return
        lo, li = self.co_lists["out"], self.co_lists["in"]
        top_o, top_i = lo.yview()[0], li.yview()[0]
        lo.delete(0, "end")
        li.delete(0, "end")
        self._co_list_refs = {"out": [o.num for o in self.outs], "in": []}
        for o in self.outs:
            lo.insert("end", o.name)
        for k, it in enumerate(self.items):
            if it.get("is_group"):
                continue
            self._co_list_refs["in"].append(it["id"])
            li.insert("end", f"{k + 1}  ·  {it.get('name') or os.path.basename(it['path'])}")
        lo.yview_moveto(top_o)
        li.yview_moveto(top_i)

    def _co_fill_pick(self):
        if not self._co_editor_alive():
            return
        self.refresh_monitors()
        self.co_pick.configure(values=[self.t("screen_none")] + [self._monitor_label(i) for i in range(len(self.monitors))])
        g = self._co_state()["screen"]
        idx = next((i for i, mo in enumerate(self.monitors) if geo(mo) == g), None)
        self.co_pick.current(0 if idx is None else idx + 1)

    def _co_pick_changed(self, _e=None):
        co = self._co_state()
        self.refresh_monitors()
        i = self.co_pick.current()
        was_on = co["on"]
        if was_on:
            self.stop_composite()
        co["screen"] = geo(self.monitors[i - 1]) if i > 0 and i - 1 < len(self.monitors) else None
        self._co_set_geo()
        taken = next((o for o in self.outs if o.sel_geo is not None and o.sel_geo == co["screen"] and o.m is not None), None)
        if taken is not None:
            self.status_t("st_mv_taken", THEMES[self.theme_name]["RED"], o=taken.name)
        if was_on and co["geo"]:
            self.start_composite()
        self._co_style_btn()

    def _co_preset_picked(self, _e=None):
        i = self.co_preset.current()
        if 0 <= i < len(CO_PRESETS):
            self.co_wv.set(str(CO_PRESETS[i][0]))
            self.co_hv.set(str(CO_PRESETS[i][1]))
            self._co_apply_size()

    def _co_apply_size(self, w=None, h=None):
        co = self._co_state()
        try:
            w = int(w if w is not None else self.co_wv.get())
            h = int(h if h is not None else self.co_hv.get())
        except (ValueError, tk.TclError):
            w, h = co["cw"], co["ch"]
        w, h = max(160, min(CO_MAX, w)), max(90, min(CO_MAX, h))
        was_on = co["on"]
        if was_on:
            self.stop_composite()
        co["cw"], co["ch"] = w, h
        self._co_set_geo()
        if self._co_editor_alive():
            self.co_wv.set(str(w))
            self.co_hv.set(str(h))
        self._co_redraw()
        if was_on and co["geo"]:
            self.start_composite()

    def _co_style_btn(self):
        on = self._co_state()["on"]
        btn = getattr(self, "co_open_btn", None)
        if btn is not None:
            btn.configure(style="Red.TButton" if on else "Accent.TButton")
        if self._co_editor_alive():
            self.co_btn.configure(text=self.t("out_off" if on else "out_on"), style="Red.TButton" if on else "Accent.TButton")

    def _co_clear(self):
        co = self._co_state()
        with self._mv_as("co"):
            for t in list(co["tiles"]):
                self._mv_drop_tile(t["uid"])
        co["tiles"], co["sel"] = [], None
        self._co_props_load()
        self._co_redraw()

    def _co_unite(self):
        """Canvas único: the selected outputs (all when none is selected) side by side, the canvas gets the size that holds them."""
        co = self._co_state()
        sel = [self._co_list_refs["out"][k] for k in self.co_lists["out"].curselection() if k < len(self._co_list_refs.get("out", []))]
        refs = sel or [o.num for o in self.outs]
        if not refs:
            self.status_t("co_unite_none", THEMES[self.theme_name]["RED"])
            return
        mode = ("h", "v", "g")[max(0, self.co_arr.current())]
        cw, ch, cells = co_arrange(len(refs), mode)
        if cw > CO_MAX or ch > CO_MAX:
            k = max(cw / CO_MAX, ch / CO_MAX)
            cw, ch = int(cw / k) // 2 * 2, int(ch / k) // 2 * 2
        was_on = co["on"]
        if was_on:
            self.stop_composite()
        self._co_clear()
        co["cw"], co["ch"] = cw, ch
        self._co_set_geo()
        for ref, box in zip(refs, cells):
            self._co_add_tile("out", ref, 0, 0, box=box)
        co["sel"] = None
        if self._co_editor_alive():
            self.co_wv.set(str(cw))
            self.co_hv.set(str(ch))
        self._co_props_load()
        self._co_redraw()
        if was_on and co["geo"]:
            self.start_composite()

    # -- simulated screen
    def _co_area(self):
        co = self._co_state()
        cw, ch = max(50, self.co_canvas.winfo_width()), max(50, self.co_canvas.winfo_height())
        sc = min((cw - 8) / co["cw"], (ch - 8) / co["ch"])
        w, h = co["cw"] * sc, co["ch"] * sc
        return (cw - w) / 2, (ch - h) / 2, w, h

    def _co_quad(self, t, ax, ay, aw, ah):
        """The four corners of a slice on the canvas (TL, TR, BL, BR) as pixels."""
        x0, y0, w, h = ax + t["x"] * aw, ay + t["y"] * ah, t["w"] * aw, t["h"] * ah
        p = t.get("pin") or [0, 0, 1, 0, 0, 1, 1, 1]
        return [(x0 + p[2 * k] * w, y0 + p[2 * k + 1] * h) for k in range(4)]

    def _co_redraw(self):
        if not self._co_editor_alive():
            return
        cv = self.co_canvas
        cv.delete("all")
        ax, ay, aw, ah = self._co_area()
        co = self._co_state()
        cv.create_rectangle(ax, ay, ax + aw, ay + ah, fill="#000000", outline="#555555")
        cv.create_text(ax + 6, ay + 4, anchor="nw", text=f"{co['cw']}×{co['ch']}", fill="#666666", font=("Segoe UI", 8))
        for t in co["tiles"]:
            x0, y0 = ax + t["x"] * aw, ay + t["y"] * ah
            x1, y1 = x0 + t["w"] * aw, y0 + t["h"] * ah
            sel = t["uid"] == co["sel"]
            cv.create_rectangle(x0, y0, x1, y1, fill="#101c2c", outline="#ff8a3d" if sel else "#6a6a6a", width=2 if sel else 1)
            if t.get("pin"):
                q = self._co_quad(t, ax, ay, aw, ah)
                cv.create_polygon(q[0] + q[1] + q[3] + q[2], fill="#1d3a5c", outline="#4fc3ff", width=1)
            if any(t["blend"]):
                bw_, bh_ = t["w"] * aw, t["h"] * ah
                cv.create_rectangle(x0 + t["blend"][0] * bw_, y0 + t["blend"][1] * bh_, x1 - t["blend"][2] * bw_, y1 - t["blend"][3] * bh_,
                                    outline="#7a8a9a", dash=(2, 3))
            flags = ("↻%d " % t["rot"] if t["rot"] else "") + ("⇆ " if t["fx"] else "") + ("⇅ " if t["fy"] else "") + ("✂ " if any(t["crop"]) else "")
            cv.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=("▶ " if t["kind"] == "in" else "▣ ") + self._mv_source_name(t) + (f"\n{flags}" if flags else ""),
                           fill="#cfd8e3", width=max(20, x1 - x0 - 10), font=("Segoe UI", 9), justify="center")
            cv.create_rectangle(x1 - 10, y1 - 10, x1, y1, fill="#ff8a3d", outline="")
            if sel:
                for px, py in self._co_quad(t, ax, ay, aw, ah):
                    cv.create_oval(px - 5, py - 5, px + 5, py + 5, fill="#4fc3ff", outline="white")

    def _co_tile_at(self, x, y):
        ax, ay, aw, ah = self._co_area()
        for t in reversed(self._co_state()["tiles"]):
            x0, y0 = ax + t["x"] * aw, ay + t["y"] * ah
            if x0 <= x <= x0 + t["w"] * aw and y0 <= y <= y0 + t["h"] * ah:
                return t
        return None

    def _co_cv_press(self, e):
        co = self._co_state()
        self._co_drag = None
        ax, ay, aw, ah = self._co_area()
        s = self._co_sel_tile()
        if s is not None:                                       # a corner-pin handle of the selected slice
            for k, (px, py) in enumerate(self._co_quad(s, ax, ay, aw, ah)):
                if abs(e.x - px) <= 8 and abs(e.y - py) <= 8:
                    self._co_drag = {"t": s, "mode": "pin", "k": k}
                    return
        t = self._co_tile_at(e.x, e.y)
        if t is None:
            co["sel"] = None
            self._co_props_load()
            self._co_redraw()
            return
        co["tiles"].remove(t)
        co["tiles"].append(t)
        changed = co["sel"] != t["uid"]
        co["sel"] = t["uid"]
        x1, y1 = ax + (t["x"] + t["w"]) * aw, ay + (t["y"] + t["h"]) * ah
        mode = "size" if (x1 - e.x < 16 and y1 - e.y < 16) else "move"
        self._co_drag = {"t": t, "mode": mode, "x": e.x, "y": e.y, "ox": t["x"], "oy": t["y"], "ow": t["w"], "oh": t["h"]}
        if changed:
            self._co_props_load()
        self._co_redraw()

    def _co_cv_motion(self, e):
        d = getattr(self, "_co_drag", None)
        if not d:
            return
        t = d["t"]
        ax, ay, aw, ah = self._co_area()
        if d["mode"] == "pin":
            if not t.get("pin"):
                t["pin"] = [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 1.0]
            fx_ = max(0.0, min(1.0, (e.x - (ax + t["x"] * aw)) / max(1.0, t["w"] * aw)))
            fy_ = max(0.0, min(1.0, (e.y - (ay + t["y"] * ah)) / max(1.0, t["h"] * ah)))
            t["pin"][2 * d["k"]], t["pin"][2 * d["k"] + 1] = fx_, fy_
        else:
            dx, dy = (e.x - d["x"]) / aw, (e.y - d["y"]) / ah
            if d["mode"] == "move":
                t["x"] = max(0.0, min(1 - t["w"], d["ox"] + dx))
                t["y"] = max(0.0, min(1 - t["h"], d["oy"] + dy))
            else:
                t["w"] = max(0.03, min(1 - t["x"], d["ow"] + dx))
                t["h"] = max(0.03, min(1 - t["y"], d["oh"] + dy))
        self._co_redraw()

    def _co_cv_release(self, _e):
        d, self._co_drag = getattr(self, "_co_drag", None), None
        if d and self._co_state()["on"]:
            self._co_place_tile(d["t"])

    def _co_cv_context(self, e):
        t = self._co_tile_at(e.x, e.y)
        if t is None:
            return
        menu = self._make_menu(self.co_canvas)
        menu.add_command(label=self.t("co_pin_reset"), command=lambda: (t.__setitem__("pin", None), self._co_redraw()))
        menu.add_separator()
        menu.add_command(label=self.t("co_remove"), command=lambda: self._co_remove_tile(t))
        self._popup(menu, e)

    def _co_list_release(self, e):
        g, self._mv_ghost = getattr(self, "_mv_ghost", None), None
        if g is not None:
            g.destroy()
        src, self._mv_src = getattr(self, "_mv_src", None), None
        if not src or g is None:
            return "break"
        cv = self.co_canvas
        x, y = e.x_root - cv.winfo_rootx(), e.y_root - cv.winfo_rooty()
        if not (0 <= x <= cv.winfo_width() and 0 <= y <= cv.winfo_height()):
            return "break"
        ax, ay, aw, ah = self._co_area()
        refs = src[3] if len(src) > 3 and src[3] else [src[1]]
        for n, ref in enumerate(refs):
            self._co_add_tile(src[0], ref, (x - ax) / aw + 0.04 * n, (y - ay) / ah + 0.04 * n)
        return "break"

    # -- the fullscreen window
    def toggle_composite(self):
        if self._co_state()["on"]:
            self.stop_composite()
        else:
            self.start_composite()
        self._co_style_btn()

    def start_composite(self):
        co = self._co_state()
        if co["on"]:
            return
        self._co_set_geo()
        if not co["geo"]:
            self.status_t("st_co_no_screen", THEMES[self.theme_name]["RED"])
            return
        x, y, w, h = co["geo"]
        win = tk.Toplevel(self.root)
        win.configure(bg="black")
        win.overrideredirect(True)
        win.geometry(f"{w}x{h}+{x}+{y}")
        win.update_idletasks()
        for seq in ("<Escape>", "<Double-Button-1>"):
            win.bind(seq, lambda _e: (self.stop_composite(), self._co_style_btn()))
        co["win"], co["on"] = win, True
        ui = self._ui_monitor()
        same = ui is not None and geo(self.monitors[ui]) == co["screen"]
        if same:
            self.status_t("st_mv_same", THEMES[self.theme_name]["RED"])
        else:
            try:
                win.attributes("-topmost", True)
            except tk.TclError:
                pass
        win.lift()
        diag_log(f"composite on: geo={co['geo']} slices={len(co['tiles'])} same_screen_as_ui={same}")
        for t in co["tiles"]:
            self._co_build_tile(t)
        win.update()
        self._co_style_btn()
        self.root.after(300, self._co_tick)
        self.status_t("st_co_on")
        self.root.after(80, self._co_raise_ui)

    def _co_raise_ui(self):
        try:
            self.root.lift()
            ctl = self._co_state().get("ctl")
            if ctl is not None and ctl.winfo_exists():
                ctl.lift()
        except tk.TclError:
            pass

    def stop_composite(self):
        co = self._co_state()
        co["on"] = False
        with self._mv_as("co"):
            for t in list(co["tiles"]):
                self._mv_drop_tile(t["uid"])
        win, co["win"] = co.get("win"), None
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass
        self._co_style_btn()

    def _co_build_tile(self, t):
        co = self._co_state()
        win = co.get("win")
        if win is None or t["uid"] in co["rt"]:
            return
        px, py, pw, ph = self._co_tile_px(t)
        outer = tk.Frame(win, bg="black")
        vid = tk.Frame(outer, bg="black")
        lbl = tk.Label(win)                                     # never shown: the engine writes the source name into it
        note = tk.Label(vid, bg="black", fg="#888888", font=("Segoe UI", max(8, ph // 30)))
        note.place(relx=0.5, rely=0.5, anchor="center")
        rt = {"outer": outer, "vid": vid, "lbl": lbl, "note": note, "tile": Output(-(100000 + t["uid"]), "mv"), "item": None,
              "tally": None, "lbl_h": 0, "txt": None}
        co["rt"][t["uid"]] = rt
        self._co_place_tile(t)
        win.update_idletasks()
        try:
            rt["tile"].pv = new_pv(str(vid.winfo_id()))
            rt["tile"].frame = vid
        except Exception as exc:
            diag_log(f"composite: slice player failed: {exc}")
            rt["tile"].pv = None
            note.configure(text=str(exc)[:60])

    def _co_tile_px(self, t):
        _x, _y, w, h = self._co_state()["geo"]
        return int(t["x"] * w), int(t["y"] * h), max(16, int(t["w"] * w)), max(16, int(t["h"] * h))

    def _co_place_tile(self, t):
        rt = self._co_state()["rt"].get(t["uid"])
        if rt is None:
            return
        px, py, pw, ph = self._co_tile_px(t)
        rt["outer"].place(x=px, y=py, width=pw, height=ph)
        rt["vid"].place(x=0, y=0, relwidth=1, relheight=1)
        rt["vfkey"] = None

    def _co_apply_vf(self, t, rt):
        pv = rt["tile"].pv
        if pv is None:
            return
        _px, _py, pw, ph = self._co_tile_px(t)
        key = (pw, ph, tuple(t["crop"]), t["rot"], t["fx"], t["fy"], tuple(t["pin"]) if t["pin"] else None, tuple(t["blend"]), t["aspect"])
        if rt.get("vfkey") == key:
            return
        rt["vfkey"] = key
        graph = co_slice_graph(t, pw, ph)
        try:
            if graph:
                self._setp(pv, "hwdec", "auto-copy")            # software filters need the frames in system memory
            pv.command("vf", "set", co_vf_spec(graph))
            if graph or rt.get("vf_on"):
                diag_log(f"composite slice {t['uid']}: filters {'set' if graph else 'cleared'} ({pw}x{ph})")
            rt["vf_on"] = bool(graph)
        except SHUTDOWN:
            raise
        except Exception as exc:
            diag_log(f"composite slice {t['uid']}: filters failed ({exc}); graph: {graph[:300]}")

    def _co_tick(self):
        co = self._co_state()
        if not co["on"] or self._closing:
            return
        with self._mv_as("co"):
            for t in list(co["tiles"]):
                rt = co["rt"].get(t["uid"])
                if rt is None:
                    continue
                try:
                    self._mv_update_tile(t, rt)
                    self._co_apply_vf(t, rt)
                except SHUTDOWN:
                    rt["tile"].pv = None
                except Exception as exc:
                    if not rt.get("err"):
                        rt["err"] = True
                        diag_log(f"composite slice {t['uid']}: {exc}")
        self.root.after(300, self._co_tick)

    def _co_data(self):
        co = self._co_state()
        idx = {it["id"]: k for k, it in enumerate(self.items)}
        tiles = []
        for t in co["tiles"]:
            ref = t["ref"] if t["kind"] == "out" else idx.get(t["ref"])
            if ref is None:
                continue
            tiles.append({"kind": t["kind"], "ref": ref, **{k: round(t[k], 5) for k in ("x", "y", "w", "h")},
                          "crop": [round(v, 4) for v in t["crop"]], "rot": t["rot"], "fx": t["fx"], "fy": t["fy"],
                          "pin": [round(v, 4) for v in t["pin"]] if t["pin"] else None,
                          "blend": [round(v, 4) for v in t["blend"]], "aspect": t["aspect"]})
        return {"screen": list(co["screen"]) if co["screen"] else None, "size": [co["cw"], co["ch"]], "tiles": tiles}

    def _co_load(self, raw):
        co = self._co_state()
        if co["on"]:
            self.stop_composite()
        co["tiles"], co["sel"], co["rt"] = [], None, {}
        co["cw"], co["ch"], co["screen"] = 1920, 1080, None
        if isinstance(raw, dict):
            s = raw.get("size")
            if isinstance(s, list) and len(s) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in s):
                co["cw"], co["ch"] = max(160, min(CO_MAX, s[0])), max(90, min(CO_MAX, s[1]))
            g = raw.get("screen")
            co["screen"] = tuple(g) if (isinstance(g, list) and len(g) == 4 and all(isinstance(v, int) for v in g)) else None

            def fl(v, lo, hi, d=0.0):
                return num(v, d, lo, hi)
            for r in raw.get("tiles", []) if isinstance(raw.get("tiles"), list) else []:
                if not isinstance(r, dict) or r.get("kind") not in ("out", "in") or not isinstance(r.get("ref"), int):
                    continue
                if r["kind"] == "in":
                    if not (0 <= r["ref"] < len(self.items)):
                        continue
                    ref = self.items[r["ref"]]["id"]
                else:
                    ref = r["ref"]
                co["uid"] += 1
                crop, blend, pin = r.get("crop"), r.get("blend"), r.get("pin")
                t = {"uid": co["uid"], "kind": r["kind"], "ref": ref, "co": True,
                     "x": fl(r.get("x"), 0.0, 1.0), "y": fl(r.get("y"), 0.0, 1.0),
                     "w": max(0.03, fl(r.get("w"), 0.0, 1.0, 0.3)), "h": max(0.03, fl(r.get("h"), 0.0, 1.0, 0.3)),
                     "crop": [fl(v, 0.0, 0.9) for v in crop[:4]] if isinstance(crop, list) and len(crop) == 4 else [0.0] * 4,
                     "blend": [fl(v, 0.0, 0.5) for v in blend[:4]] if isinstance(blend, list) and len(blend) == 4 else [0.0] * 4,
                     "rot": r.get("rot") if r.get("rot") in (0, 90, 180, 270) else 0,
                     "fx": bool(r.get("fx")), "fy": bool(r.get("fy")),
                     "pin": [fl(v, 0.0, 1.0) for v in pin[:8]] if isinstance(pin, list) and len(pin) == 8 else None,
                     "aspect": r.get("aspect") if r.get("aspect") in ("fit", "stretch", "fill") else "fit"}
                co["tiles"].append(t)
        self._co_set_geo()
        if self._co_editor_alive():
            self.co_wv.set(str(co["cw"]))
            self.co_hv.set(str(co["ch"]))
            self._co_fill_pick()
            self._co_props_load()
            self._co_redraw()

    # ---- "Bigger window": the monitor player is re-created inside a resizable window of its own
    def _mon_frame(self, out):
        big = getattr(out, "big_frame", None)
        try:
            if big is not None and big.winfo_exists():
                return big
        except tk.TclError:
            pass
        return out.frame

    def open_big_monitor(self, out):
        win = getattr(out, "big_win", None)
        try:
            if win is not None and win.winfo_exists():
                win.deiconify()
                win.lift()
                return
        except tk.TclError:
            pass
        win = tk.Toplevel(self.root)
        win.title(f"{out.name} — {self.t('big_title')}")
        win.configure(bg="black")
        win.geometry("960x540")
        win.minsize(320, 180)
        frame = tk.Frame(win, bg="black")
        frame.pack(fill="both", expand=True)
        out.big_win, out.big_frame = win, frame
        win.protocol("WM_DELETE_WINDOW", lambda o=out: self._close_big(o))
        win.update_idletasks()
        if not out.preview_on.get():
            out.preview_on.set(True)
            self.toggle_preview(out)               # the monitor is created inside the new window
        else:
            self._restart_monitor(out)

    def _restart_monitor(self, out):
        if out not in self.outs or not out.preview_on.get():
            return
        if out.pv:
            self.stop_preview(out)
        if out.m:
            self.start_preview(out)
        elif out.index >= 0:
            self._start_standalone_preview(out)

    def _close_big(self, out, restart=True):
        win, out.big_win, out.big_frame = getattr(out, "big_win", None), None, None
        if out.pv:
            self.stop_preview(out)                 # the player must stop drawing into the window before it is destroyed
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass
        if restart:
            self._restart_monitor(out)

    def stop_preview(self, out):
        if out.pv:
            try:
                mpv_terminate(out.pv)
            except Exception:
                pass
        out.pv = None
        if out.label is not None:
            out.label.place(relx=0.5, rely=0.5, anchor="center")

    def toggle_preview(self, out):
        if out.preview_on.get():
            self.start_preview(out)
        else:
            self.stop_preview(out)

    def _pv_thumb(self, out, item):
        """Monitor window: the item's thumbnail picture (audio-only items, and while the copy of a YouTube video starts).
        True when it is on screen."""
        pv = out.pv
        if pv is None or not item.get("is_youtube"):
            return False
        ent = self._yt_thumb(item, then=lambda: self._pv_thumb_later(out, item))
        if ent == "busy":
            try:
                self.root.after(800, lambda: self._pv_thumb_later(out, item))
            except Exception:
                pass
            return False
        if not ent:
            return False
        try:
            self._rtmp_props(pv, False)
            self._osd_overlay(pv, None)
            self._setp(pv, "start", "none")
            self._setp(pv, "end", "none")
            self._setp(pv, "pause", False)
            self._setp(pv, "loop_file", "no")
            pv.play(ent)
            return True
        except SHUTDOWN:
            raise
        except Exception:
            return False

    def _pv_thumb_later(self, out, item):
        if out.pv is None or self._cur_item(out) is not item:
            return
        if getattr(out, "_mon_mode", None) == "feed" and (getattr(out, "_mon_st", None) or {}).get("fkey") is not None:
            return                                              # the real picture is already there
        self._pv_thumb(out, item)

    def _preview_load(self, out, item, start=0):
        out._pv_reload_t = time.time()
        if not out.pv:
            return
        try:
            self._setp(out.pv, "pause", bool(item["f1"]))
            self._setp(out.pv, "loop_file", "inf" if (item["repeat"] and not out.cur_marked) else "no")
            begin = item["in"]
            if start and start > max(0.5, item["in"]) and not is_live(item):
                begin = start                                   # resume where it was (set before loading: a seek would fail)
            self._setp(out.pv, "start", f"{begin:.3f}" if begin > 0 else "none")
            self._setp(out.pv, "end", f"{item['out']:.3f}" if item["out"] else "none")
            self._setp(out.pv, "image_display_duration", self._img_dur(item))
            if item.get("is_youtube"):
                self._setp(out.pv, "ytdl_format", "worstaudio/worst" if item.get("yt_audio") else PV_YTDL_FORMAT)      # light stream: decodes smoothly in the small window
                self._setp(out.pv, "cache", "yes")
                self._setp(out.pv, "cache_secs", 10)
                self._setp(out.pv, "demuxer_max_bytes", 60000000)
                self._setp(out.pv, "demuxer_max_back_bytes", 15000000)
                self._setp(out.pv, "demuxer_readahead_secs", 10)
            self._apply_output_format(out, item)
            if is_live(item):
                out._mon_mode = None
                self._rtmp_open(out, item, only="pv")
                return
            if item.get("is_youtube") and item.get("yt_audio"):
                out._mon_mode = None                           # an audio item has no picture: the monitor shows its thumbnail
                self._rtmp_props(out.pv, False)                # (the muted monitor has nothing to play: it would end at once)
                self._osd_overlay(out.pv, None)
                if not self._pv_thumb(out, item):
                    out.pv.command("stop")
                return
            if out.m is not None and self._mon_wants_feed(out, item):      # the monitor shows the UDP copy of what the output plays; nothing is opened here
                                                                           # (a Multiview tile has no player of its own: it opens its source itself)
                out._mon_mode, out._mon_st = "feed", {}
                self._rtmp_props(out.pv, False)
                self._osd_overlay(out.pv, None)
                if not (item.get("is_youtube") and self._pv_thumb(out, item)):      # a picture at once, the copy takes a moment
                    out.pv.command("stop")
                return
            out._mon_mode = None
            self._rtmp_props(out.pv, False)
            self._osd_overlay(out.pv, None)
            self._yt_play(out.pv, item, "pv")
        except Exception:
            pass

    @staticmethod
    def _is_live_stream(item):
        """Live input / live YouTube stream: position and duration mean nothing across two connections."""
        if is_live(item):
            return True
        info = (item or {}).get("ytdl_info") or {}
        return bool(info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming", "post_live"))

    def _no_sync(self, out, item):
        """True when the monitor must NOT be kept in step by seeking (live streams, or YouTube without a duration)."""
        if item is None:
            return False
        if self._is_live_stream(item) or item.get("yt_audio"):
            return True
        dur = self._prop(out, "duration", 0) or 0
        if not dur and out.m is None and out.pv is not None:      # a Multiview tile has no output player of its own: ask its picture
            try:
                dur = float(out.pv.duration or 0)
            except SHUTDOWN:
                raise
            except Exception:
                dur = 0
        return bool(item.get("is_youtube")) and not dur > 0

    def _keep_live_monitor_alive(self, out, item):
        """A live YouTube stream can end/expire on the monitor's own connection: reload it (at most every 20 s)."""
        if not item.get("is_youtube") or item.get("yt_audio") or not out.pv or time.time() - out._pv_reload_t < 20:
            return
        try:
            dead = bool(out.pv.eof_reached) or bool(out.pv.idle_active)
        except Exception:
            dead = False
        if dead:
            diag_log(f"output {out.num}: monitor stream ended, reloading it")
            self._preview_load(out, item, 0)

    def _sync_preview(self, out, pos, paused):
        if not out.pv or out.index < 0:
            return
        cur = self._cur_item(out)
        if out.m is not None and cur is not None and self._mon_feed_sync(out, cur, pos):
            return
        if cur is not None and self._no_sync(out, cur):
            self._keep_live_monitor_alive(out, cur)
            return                      # live stream: the two connections have unrelated positions - never seek
        if (out.timeline is not None and out.timeline._drag) or time.time() < out._sync_hold:
            return                      # the progress bar is being dragged: do not fight the seeks
        try:
            pv_paused = bool(out.pv.pause)
            if pv_paused != paused:
                out.pv.pause = paused
                self._setp(out.pv, "speed", 1.0)
                if paused:
                    out.pv.seek(pos, "absolute+exact")
                    return
            if paused:
                return
            ppos = out.pv.time_pos
            if ppos is None:
                return
            drift = pos - ppos                      # > 0: the monitor is behind the fullscreen
            if abs(drift) > 1.5:                    # really lost: one jump (a seek is what makes it stutter)
                if time.time() - getattr(out, "_pv_seek_t", 0.0) > 6.0:      # a stream that cannot seek exactly would be seeked forever
                    out._pv_seek_t = time.time()
                    out.pv.seek(pos, "absolute")
                    self._setp(out.pv, "speed", 1.0)
                return
            # otherwise close the gap by playing slightly faster / slower: no seek, no visible jump
            want = 1.0 if abs(drift) < 0.06 else 1.0 + max(-0.2, min(0.2, drift * 0.5))
            try:
                cur = float(out.pv.speed)
            except Exception:
                cur = 1.0
            if abs(want - cur) > 0.01:
                self._setp(out.pv, "speed", round(want, 3))
        except Exception:
            pass

    def _find(self, iid):
        return next((k for k, it in enumerate(self.items) if it["id"] == iid), None)

    def _target_index(self, item):
        k = self._target_index_raw(item)
        return self._first_playable(k, -1 if item.get("next") == "prev" else 1)

    def _target_index_raw(self, item):
        mode, n, cur = item["next"], len(self.items), self._find(item["id"])
        if mode == "none" or not n or cur is None:
            return None
        if mode == "prev":
            shuf_mode = self.shuffle_mode.get() if hasattr(self, "shuffle_mode") else "off"
            if shuf_mode != "off":
                return self._history_back(item["id"])
            if cur - 1 >= 0:
                return cur - 1
            return n - 1
        if mode == "first":
            return 0
        if mode == "last":
            return n - 1
        if mode != "next":
            k = self._find(mode)
            if k is not None:
                return k
        shuf_mode = self.shuffle_mode.get() if hasattr(self, "shuffle_mode") else "off"
        if shuf_mode != "off":
            return self._shuffle_next(item["id"])
        if cur + 1 < n:
            return cur + 1
        return 0

    def _advance(self, out, item):
        """Index of the item that follows `item` on this output, or None (nothing to play / already playing elsewhere)."""
        nxt = self._target_index(item)
        if nxt is None:
            return None
        target = self.items[nxt]
        if any(o is not out and self._cur_item(o) is target for o in self.outs):
            return None
        return nxt

    def _note_played(self, item):
        if not self.history or self.history[-1] != item["id"]:
            self.history.append(item["id"])
            del self.history[:-200]
        self.played.add(item["id"])

    def _shuffle_next(self, cur_id, restrict=None):
        r = self._shuffle_next_raw(cur_id, restrict)
        return r if (r is None or not self.items[r].get("is_group")) else self._first_playable(r)

    def _shuffle_next_raw(self, cur_id, restrict=None):
        shuf_mode = self.shuffle_mode.get() if hasattr(self, "shuffle_mode") else "all"
        if shuf_mode == "selected":
            cand = [self._index_of(i) for i in self.sel] if self.sel else (list(range(len(self.items))) if restrict is None else list(restrict))
        elif shuf_mode == "output":
            out_target = self.shuffle_output.get() if hasattr(self, "shuffle_output") else 1
            cand = [k for k, it in enumerate(self.items) if it["output"] == out_target]
            if not cand:
                cand = list(range(len(self.items))) if restrict is None else list(restrict)
        else:
            cand = list(range(len(self.items))) if restrict is None else list(restrict)

        cand = [k for k in cand if not self.items[k].get("is_group")]
        if not cand:
            return None
        pool = [k for k in cand if self.items[k]["id"] not in self.played and self.items[k]["id"] != cur_id]
        if not pool:
            self.played = {cur_id} if cur_id else set()
            pool = [k for k in cand if self.items[k]["id"] != cur_id]
            if not pool:
                return cand[0]
        return random.choice(pool)

    def _history_back(self, cur_id, outnum=None):
        h = self.history
        if cur_id not in h:
            return None
        j = len(h) - 1 - h[::-1].index(cur_id) - 1
        while j >= 0:
            k = self._find(h[j])
            if k is not None and (outnum is None or self.items[k]["output"] == outnum):
                self.history = h[:j]
                return k
            j -= 1
        return None

    def _open_shuffle_dialog(self):
        w = tk.Toplevel(self.root)
        w.title(self.t("shuf_win_title"))
        w.geometry("340x260")
        w.resizable(False, False)
        theme = THEMES[self.theme_name]
        w.configure(bg=theme["BG"])
        w.transient(self.root)
        w.grab_set()

        if not hasattr(self, "shuffle_mode"):
            self.shuffle_mode = tk.StringVar(value="off")
        if not hasattr(self, "shuffle_output"):
            self.shuffle_output = tk.IntVar(value=1)
        mode_var = tk.StringVar(value=self.shuffle_mode.get())      # edited in the dialog
        output_var = tk.IntVar(value=self.shuffle_output.get())

        ttk.Label(w, text=self.t("shuf_head"), font=("Segoe UI", 10, "bold")).pack(pady=(15, 10))

        modes = [
            (self.t("shuf_off"), "off"),
            (self.t("shuf_all"), "all"),
            (self.t("shuf_sel"), "selected"),
            (self.t("shuf_out"), "output"),
        ]

        for text, val in modes:
            ttk.Radiobutton(w, text=text, variable=mode_var, value=val).pack(anchor="w", padx=30, pady=2)

        out_frame = ttk.Frame(w)
        out_frame.pack(pady=5)
        ttk.Label(out_frame, text=self.t("shuf_output_lbl")).pack(side="left", padx=5)
        out_values = [o.num for o in self.outs] or [1]
        out_cb = ttk.Combobox(out_frame, textvariable=output_var, values=out_values, state="readonly", width=6)
        out_cb.pack(side="left")

        btn_frame = ttk.Frame(w)
        btn_frame.pack(pady=15)

        def apply():
            mode = mode_var.get()
            is_active = (mode != "off")
            self.shuffle_mode.set(mode)
            self.shuffle_output.set(output_var.get())
            self.shuffle_var.set(is_active)
            self.played = {it["id"] for it in (self._cur_item(o) for o in self.outs) if it}
            self._update_shuffle_btn()
            self.status_t("st_shuffle_on" if is_active else "st_shuffle_off")
            w.destroy()

        ttk.Button(btn_frame, text=self.t("btn_apply"), command=apply, style="Accent.TButton").pack(side="left", padx=5)
        ttk.Button(btn_frame, text=self.t("btn_close"), command=w.destroy).pack(side="left", padx=5)

    def toggle_shuffle_btn(self):
        self._open_shuffle_dialog()

    def _update_shuffle_btn(self):
        """Shuffle button is red while any shuffle mode is active."""
        btn = getattr(self, "shuffle_btn", None)
        if btn is not None:
            on = self.shuffle_mode.get() != "off"
            btn.configure(style="Red.TButton" if on else "TButton")

    def play_index(self, i, _partner=False):
        if not (0 <= i < len(self.items)):
            return
        if self.items[i].get("is_group"):                  # a group header plays its first item
            first = next((k for k, m in enumerate(self.items) if m.get("group") == self.items[i]["id"]), None)
            if first is None:
                return
            i = first
        item = self.items[i]
        out = self._out_of(item)
        theme = THEMES[self.theme_name]
        if out is None:
            self.status_t("st_out_missing", theme["RED"], n=item["output"])
            return
        is_remote = (item.get("is_youtube") or is_live(item)
                     or item["path"].startswith(("http://", "https://", "ytdl://")))
        exists = True if is_remote else os.path.exists(item["path"])
        if item["missing"] == exists:
            item["missing"] = not exists
            self._refresh_row(item)
        if not exists:
            self.status_t("st_file_missing", theme["RED"], name=item.get("name") or os.path.basename(item["path"]))
            return
        # the fullscreen is optional: Play on an output that is off plays WITHOUT a window (NDI / audio / monitor);
        # the window only opens with "Turn On"
        was_off = out.m is None
        windowless = out.ndi_sender is not None or not PLAY_OPENS_FULLSCREEN
        if not self.start_output(out, headless=True if windowless else None):
            return
        if was_off and out._headless and out.ndi_sender is None:
            self.status_t("st_out_nowindow", theme["ACCENT"], n=out.num, btn=self.t("out_on"))
        out.index = i
        self._note_played(item)
        out.eof_handled = False
        out.fired = False
        out.ignore_until = time.time() + 0.8
        out.cur_in, out.cur_out = item["in"], item["out"]
        out.cur_marked = item["in"] > 0 or bool(item["out"])
        try:
            self._setp(out.m, "start", f"{item['in']:.3f}" if item["in"] > 0 else "none")
            self._setp(out.m, "end", f"{item['out']:.3f}" if item["out"] else "none")
            self._setp(out.m, "image_display_duration", self._img_dur(item))
            self._setp(out.m, "loop_file", "inf" if (item["repeat"] and not out.cur_marked) else "no")
            try:
                self._setp(out.m, "video_sync", "audio")
                self._setp(out.m, "cache", "auto")
            except Exception:
                pass
            if not is_live(item):
                self._rtmp_props(out.m, False)
            self._out_hw(out.m, item)
            if item.get("is_youtube"):
                try:
                    fmt = item.get("ytdl_format")
                    if fmt and fmt not in ("best", ""):
                        self._setp(out.m, "ytdl_format", fmt)
                    else:
                        self._setp(out.m, "ytdl_format", YT_DEFAULT_FORMAT)
                    self._setp(out.m, "cache", "yes")
                    self._setp(out.m, "cache_secs", 30)
                    self._setp(out.m, "demuxer_max_bytes", 150000000)
                    self._setp(out.m, "demuxer_max_back_bytes", 30000000)
                    self._setp(out.m, "demuxer_readahead_secs", 30)
                except Exception:
                    pass
            out.m.pause = bool(item["f1"]) and not is_live(item)
            
            # resolução / FPS / aspecto do item, tudo num só passo (canvas da resolução escolhida)
            self._apply_output_format(out, item)
            self._ensure_fullscreen(out)
            
            self._open_item(out, item)
            if item.get("is_countdown") and item["cd"].get("auto_start"):
                self._cd_control(item, "reset")
                self._cd_control(item, "start")
            self._rehide(out)
            if item.get("mute") or getattr(out, "out_mute", tk.BooleanVar()).get():
                try:
                    out.m.mute = True
                except Exception:
                    pass
            if item.get("is_youtube"):
                self.root.after(800, lambda o=out, it=item: self._reapply_ytdl_display(o, it))
                self.root.after(2000, lambda o=out, it=item: self._reapply_ytdl_display(o, it))
                self.root.after(4000, lambda o=out, it=item: self._reapply_ytdl_display(o, it))
                self.root.after(6000, lambda o=out, it=item: self._reapply_ytdl_display(o, it))
                self.root.after(10000, lambda o=out, it=item: self._reapply_ytdl_display(o, it))
        except SHUTDOWN:
            self._output_lost(out)
            return
        except Exception as exc:
            self.status_t("st_error", theme["RED"], err=exc)
        self._preview_load(out, item)
        self._highlight()
        self.tree.see(item["id"])
        self.status_t("st_playing", i=i + 1, n=len(self.items), o=out.num, name=item.get("name") or os.path.basename(item["path"]))
        if item["f1"]:
            self.status_t("st_paused_first")
        if item["wn"] and not _partner:
            self._start_partner(i, out)
        sim_id = item.get("sim", "none")
        if sim_id != "none" and not _partner:
            k = self._find(sim_id)
            if k is not None:
                partner = self.items[k]
                if self._out_of(partner) is not out:
                    if not any(self._cur_item(o) is partner for o in self.outs):
                        self.play_index(k, _partner=True)
                else:
                    self.status_t("st_wn_same_output", theme["RED"])

    def _start_partner(self, i, out):
        if i + 1 >= len(self.items):
            return
        theme = THEMES[self.theme_name]
        if self._out_of(self.items[i + 1]) is out:
            self.status_t("st_wn_same_output", theme["RED"])
            return
        self.play_index(i + 1, _partner=True)

    def _pick_for(self, out):
        if self.anchor:
            k = self._find(self.anchor)
            if k is not None and self.items[k]["output"] == out.num:
                return k
        return next((k for k, it in enumerate(self.items) if it["output"] == out.num), None)

    def toggle_pause(self, out):
        try:
            if not out.m or out.index < 0:
                k = self._pick_for(out)
                if k is not None:
                    self.play_index(k)
            elif self._prop(out, "eof_reached", False) and not (self._cur_item(out) or {}).get("is_slideshow"):
                self.play_index(out.index)
            else:
                out.m.pause = not out.m.pause
        except SHUTDOWN:
            self._output_lost(out)

    def toggle_pause_all(self):
        active = [o for o in self.outs if o.m and o.index >= 0]
        if not active:
            self._play_selected()
            return
        states = []
        for o in active:
            try:
                states.append(bool(self._prop(o, "pause", False)))
            except SHUTDOWN:
                self._output_lost(o)
                states.append(None)
        if all(s in (True, None) for s in states):
            for o, s in zip(active, states):
                if s is True:
                    self.toggle_pause(o)
        else:
            for o, s in zip(active, states):
                if s is False:
                    try:
                        o.m.pause = True
                    except Exception:
                        pass

    def stop(self, out):
        for p in (out.m, out.pv):
            if p:
                try:
                    p.command("stop")
                except Exception:
                    pass
                self._osd_overlay(p, None)
        out.index = -1
        self._highlight()

    def play_all(self):
        for out in self.outs:
            k = self._pick_for(out)
            if k is not None:
                self.play_index(k)

    def stop_all(self):
        for o in self.outs:
            self.stop(o)

    def step(self, out, delta):
        mine = [k for k, it in enumerate(self.items) if it["output"] == out.num and not it.get("is_group")]
        if not mine:
            return
        cur = self._cur_item(out)
        shuf_mode = self.shuffle_mode.get() if hasattr(self, "shuffle_mode") else "off"
        if shuf_mode != "off":
            if delta > 0:
                n = self._shuffle_next(cur["id"] if cur else None, restrict=mine)
            else:
                n = self._history_back(cur["id"], out.num) if cur else None
            if n is not None:
                self.play_index(n)
            return
        if out.index in mine:
            j = mine.index(out.index) + delta
        else:
            j = 0 if delta > 0 else len(mine) - 1
        if j >= len(mine) or j < 0:
            j %= len(mine)
        self.play_index(mine[j])

    def seek_rel(self, out, secs):
        if out.m:
            try:
                out.m.seek(secs, "relative")
            except Exception:
                pass

    def _key_out(self):
        k = self._find(self.anchor) if self.anchor else None
        if k is not None:
            return self._out_of(self.items[k])
        return self.outs[0] if self.outs else None

    def _key_call(self, fn, *args):
        out = self._key_out()
        if out is not None:
            fn(out, *args)

    def _apply_loop(self, it):
        for o in self.outs:
            if self._cur_item(o) is it:
                val = "inf" if (it["repeat"] and not o.cur_marked) else "no"
                for p in (o.m, o.pv):
                    if p:
                        self._setp(p, "loop_file", val)

    def _on_eof(self, out):
        if self._is_backup():
            return                                             # the Backup follows the Main: it does not move on by itself
        item = self._cur_item(out)
        if item is None:
            return
        if item["fl"]:
            return
        if item["repeat"]:
            if not out.cur_marked:
                return
            try:
                out.m.seek(out.cur_in, "absolute+exact")
                out.m.pause = False
            except Exception:
                pass
            out.eof_handled = False
            out.fired = False
            out.ignore_until = time.time() + 0.3
            return
        if out.fired:
            self.stop(out)
            return
        nxt = self._advance(out, item)
        diag_log(f"output {out.num}: end of '{item.get('name') or item.get('path')}' -> next={nxt}")
        if nxt is not None and self.items[nxt] is item:        # the same item again (stream ended / expired / failed)
            now = time.time()
            out._replay_t = [t for t in out._replay_t if now - t < 60] + [now]
            if len(out._replay_t) >= 4:                        # 4 restarts in a minute: it is failing, not looping
                diag_log(f"output {out.num}: replay loop detected, stopping")
                self.stop(out)
                self.status_t("st_replay_loop", THEMES[self.theme_name]["RED"], n=out.num)
                return
        if nxt is None:
            self.stop(out)
            return
        goes_elsewhere = self._out_of(self.items[nxt]) is not out
        self.play_index(nxt)
        if goes_elsewhere:
            self.stop(out)

    def _fade_factor(self, out, item, pos, dur):
        """0..1 gain from the item's Fade In / Fade Out (checkbox on + time in ms)."""
        if item is None:
            return 1.0
        fi = bool(item.get("fade_in_on")) and item.get("fade_in", 0) > 0
        fo = bool(item.get("fade_out_on")) and item.get("fade_out", 0) > 0
        if not (fi or fo):
            return 1.0
        pos = pos or 0.0
        if time.time() <= out.ignore_until:             # the position may still be the previous item's
            if not fi:
                return 1.0
            span = item["fade_in"] / 1000.0
            el = pos - (out.cur_in or 0.0)
            # a small position is the new file's own (keep the ramp continuous), a large one is stale -> silent
            return max(0.0, min(1.0, el / span)) if 0 <= el < span else 0.0
        f = 1.0
        if fi:
            span = item["fade_in"] / 1000.0
            el = pos - (out.cur_in or 0.0)
            if 0 <= el < span:
                f = min(f, el / span)
        end_t = out.cur_out or dur or 0
        if fo and end_t > 0:
            span = item["fade_out"] / 1000.0
            rem = end_t - pos
            if 0 <= rem < span:
                f = min(f, rem / span)
        return max(0.0, min(1.0, f))

    def _apply_fade(self, out, item, pos, dur):
        """Ramp the output volume (and dim the picture) while a fade is running; restore afterwards."""
        f = self._fade_factor(out, item, pos, dur)
        if f >= 1.0 and out._fade_f >= 1.0:
            return
        out._fade_f = f
        self._setp(out.m, "volume", float(out.vol.get()) * f)
        if FADE_VIDEO:
            dim = -100.0 * (1.0 - f)
            if not self.blackout.get():
                self._setp(out.m, "brightness", dim)
            if out.pv:                                        # the monitor shows the fade too (it has no audio)
                self._setp(out.pv, "brightness", dim)

    def _on_output_volume(self, out, v):
        if out.m:
            try:
                out.m.volume = float(v) * out._fade_f
            except Exception:
                pass

    def on_volume(self, v):
        for o in self.outs:
            if o.m:
                try:
                    o.m.volume = float(v) * o._fade_f
                except Exception:
                    pass

    def on_mute(self):
        for o in self.outs:
            if o.m:
                try:
                    o.m.mute = self.mute.get()
                except Exception:
                    pass

    def toggle_blackout(self):
        for o in self.outs:
            if o.m:
                try:
                    o.m.brightness = -100 if self.blackout.get() else 0
                except Exception:
                    pass

    def toggle_output_fullscreen(self, out):
        if out.m and not out._headless:
            try:
                new = not bool(out.m.fullscreen)
                out.m.fullscreen = new
                out._windowed = not new
            except Exception:
                pass

    def _out_seek_press(self, out):
        out.dragging = True

    def _out_seek_release(self, out):
        out.dragging = False
        if out.m and out.index >= 0:
            try:
                out.m.seek(out.timeline_var.get(), "absolute")
            except Exception:
                pass

    def _out_tl_seek(self, out, t, final):
        if out.m and out.index >= 0:
            try:
                out.m.seek(t, "absolute+exact" if final else "absolute+keyframes")
            except Exception:
                pass
        cur = self._cur_item(out)
        if out.pv and out.index >= 0 and not self._no_sync(out, cur):
            # the monitor is a second decoder: while dragging it only gets a light keyframe seek a few times a
            # second (seeking it on every mouse move made it stutter), and one exact seek on release
            now = time.time()
            if final or now - out._pv_seek_t > 0.3:
                out._pv_seek_t = now
                try:
                    out.pv.seek(t, "absolute+exact" if final else "absolute+keyframes")
                except Exception:
                    pass
        out._sync_hold = time.time() + 0.8              # let it settle before the drift correction resumes

    def _out_tl_marker(self, out, which, t, final):
        pass

    def _ip_start(self):
        if self.ip:
            return True
        try:
            self.root.update_idletasks()
            self.ip = new_mpv(
                wid=str(self.ip_frame.winfo_id()),
                vo="gpu",
                profile="fast",
                hwdec="auto-safe",
                aid="no",
                ao="null",
                keep_open=True,
                idle=True,
                force_window=True,
                osc=False,
                osd_level=0,
                input_default_bindings=False,
                input_vo_keyboard=False,
                image_display_duration="inf",
            )
        except Exception as exc:
            self.ip = None
            self.ip_label.configure(text=self.t("ip_unavailable"))
            theme = THEMES[self.theme_name]
            self.status_t("st_ip_fail", theme["RED"], err=exc)
            return False
        self.ip_label.place_forget()
        return True

    def ip_load(self, item):
        if not self._ip_start():
            return
        self.ip_item, self.ip_dur, self.ip_playing, self.ip_info_ready = item, 0.0, False, False
        self.ip_ss_on, self._ip_idle_key, self._ip_yt_shown = False, None, None
        self.ip_yl_on = False                                   # a YouTube live in the preview only runs after Play
        self.ip_yt_idle, self._ip_autoplay = False, None        # a YouTube video in the preview is only opened after Play
        self.timeline.set(dur=0.0, pos=0.0, t_in=item["in"], t_out=item["out"])
        self._update_trecho()
        self._update_aspect_label()
        if is_live(item):
            if is_ytlive(item):
                self.ip_play_btn.configure(text="▶")
                self.ip_time.configure(text="—")
            self.info_lbl.configure(text=self._rtmp_info(item))
            self._rtmp_open_ip(item)            # live feed (or address/key slate) in the preview window
            return
        self._osd_overlay(self.ip, None)        # leaving an RTMP input: drop its slate
        name = item.get("name") or os.path.basename(item["path"])
        is_remote = item.get("is_youtube") or item["path"].startswith(("http://", "https://"))
        if not is_remote and not os.path.exists(item["path"]):
            self.info_lbl.configure(text=f"{name}\n" + self.t("st_file_missing", name=name))
            try:
                self.ip.command("stop")
            except Exception:
                pass
            return
        if item.get("is_youtube") and not getattr(self, "_ip_force_load", False):
            # thumbnail only: the video is opened (time bar and IN / OUT markers become usable) when Play is pressed
            self.ip_yt_idle = True
            lines = [name]
            if item.get("dur"):
                lines.append(f"{self.t('inf_duration')}: {fmt_ms(item['dur'])}")
            lines.append(self.t("yt_press_play"))
            self.info_lbl.configure(text="\n".join(lines))
            self.ip_play_btn.configure(text="▶")
            self._ip_yt_idle(item)
            self._yt_prefetch(item, now=True)                     # the address is resolved while the user looks at the thumbnail
            return
        self.info_lbl.configure(text=f"{name}\n{self.t('info_loading')}")
        if item.get("is_youtube") and item.get("ytdl_info"):
            self._fill_info(item)
            self.ip_info_ready = True
        try:
            yt = bool(item.get("is_youtube"))
            if yt:
                diag_log(f"preview: loading YouTube item {item['path']}")
            self._rtmp_props(self.ip, False)
            self._apply_aspect(self.ip, item["aspect"])
            # the small preview never needs 4K / hardware decoding: a light stream, software decoder (stable)
            self._setp(self.ip, "ytdl_format", ("worstaudio/worst" if item.get("yt_audio") else PV_YTDL_FORMAT) if yt else YT_DEFAULT_FORMAT)
            self._setp(self.ip, "hwdec", "auto-safe" if (not yt or item.get("yt_hw")) else "no")
            self.ip.pause = True
            self._yt_play(self.ip, item, "ip")
            if yt:
                diag_log("preview: loadfile sent")
        except Exception as exc:
            self.info_lbl.configure(text=self.t("info_load_err", err=exc))
            return
        self.root.after(120, lambda: self._ip_wait(item, 0))

    def ip_clear(self):
        self.ip_item, self.ip_dur, self.ip_playing, self.ip_info_ready = None, 0.0, False, False
        self.ip_yt_idle, self._ip_autoplay = False, None
        if self.ip:
            try:
                self.ip.command("stop")
            except Exception:
                pass
            self._osd_overlay(self.ip, None)
        self.timeline.set(dur=0.0, pos=0.0, t_in=0.0, t_out=None)
        self.ip_time.configure(text="0:00.000 / 0:00.000")
        self.ip_play_btn.configure(text="▶")
        self.info_lbl.configure(text=self.t("info_select"))
        self.trecho_lbl.configure(text="")
        self.aspect_lbl.configure(text="")

    def _ip_wait(self, item, tries):
        if self.ip_item is not item or not self.ip:
            return
        try:
            dur = self.ip.duration
            vp = self.ip.video_params
        except Exception:
            dur, vp = None, None
        has_video = isinstance(vp, dict) and vp.get("w")
        if dur or (tries >= 4 and has_video) or tries >= (160 if item.get("is_youtube") else 25):
            self.ip_dur = float(dur or 0.0)
            self.timeline.set(dur=self.ip_dur, pos=0.0, t_in=item["in"], t_out=item["out"])
            if self.ip_dur > 0 and item["in"] > 0.02:
                try:
                    self.ip.seek(item["in"], "absolute+exact")
                except Exception:
                    pass
            self.ip_info_ready = True
            self._fill_info(item)
            self._update_trecho()
            if self._ip_autoplay is item:                       # Play was what opened it
                self._ip_autoplay = None
                try:
                    self.ip_from = self._ip_pos()
                    self.ip.pause = False
                    self.ip_playing = True
                except Exception:
                    pass
            self._ip_ui()
        else:
            self.root.after(120, lambda: self._ip_wait(item, tries + 1))

    def _fill_info(self, item):
        p = self.ip
        def g(name, default=None):
            try:
                v = getattr(p, name)
            except Exception:
                return default
            return default if v is None else v

        vp, ap = g("video_params", {}), g("audio_params", {})
        vp = vp if isinstance(vp, dict) else {}
        ap = ap if isinstance(ap, dict) else {}
        w, h = vp.get("w"), vp.get("h")
        fps, vcodec = g("container_fps"), g("video_format")
        acodec = g("audio_codec_name")
        sr, ch = ap.get("samplerate"), ap.get("channel-count") or ap.get("channels")
        try:
            size = fmt_size(os.path.getsize(item["path"])) if not (item.get("is_youtube") or item["path"].startswith("http")) else "stream"
        except OSError:
            size = "?"

        lines = [item.get("name") or os.path.basename(item["path"])]
        if item.get("is_youtube") and item.get("ytdl_info"):
            info = item["ytdl_info"]
            dur = info.get("duration")
            if dur:
                lines.append(f"{self.t('inf_duration')}: {fmt_ms(dur)}")
            w = info.get("width")
            h = info.get("height")
            if w and h:
                lines.append(f"{self.t('inf_video')}: {w}×{h} (original)")
            try:                                                   # what the output really plays (resolved by yt-dlp)
                ent = self._yt_play_cache.get((item.get("path"), self._yt_fmt_for(item, "out")))
                parts = ((ent or {}).get("meta") or "").split("|")
                if len(parts) >= 3 and parts[1] not in ("", "None", "NA"):
                    lines.append(f"Output: {parts[0].split('.')[0]} · {parts[1]}p" + (f" · {parts[2]} fps" if parts[2] not in ("", "None", "NA") else ""))
            except Exception:
                pass
            uploader = info.get("uploader") or info.get("channel")
            if uploader:
                lines.append(f"Canal: {uploader}")
            fmt = item.get("ytdl_format", "best")
            mh = re.search(r"height<=(\d+)", fmt or "")
            lines.append(f"Qualidade: {mh.group(1) + 'p' if mh else 'best'}")
            lines.append("YouTube stream")
            self.info_lbl.configure(text="\n".join(lines))
            return
        if w:
            kind = self.t("inf_video") if self.ip_dur > 0 else self.t("inf_image")
            parts = [vcodec, f"{w}×{h}", f"{fps:g} fps" if fps and self.ip_dur > 0 else None]
            lines.append(f"{kind}: " + " · ".join(x for x in parts if x))
        else:
            lines.append(self.t("inf_no_video"))
        if acodec:
            parts = [acodec, f"{sr / 1000:g} kHz" if sr else None,
                     self.t("inf_channels", n=ch) if ch else None]
            lines.append(f"{self.t('inf_audio')}: " + " · ".join(x for x in parts if x))
        else:
            lines.append(self.t("inf_no_audio"))
        lines.append(f"{self.t('inf_container')}: {g('file_format', '?')} · {size}")
        if self.ip_dur > 0:
            lines.append(f"{self.t('inf_duration')}: {fmt_ms(self.ip_dur)}")
        self.info_lbl.configure(text="\n".join(lines))

    def _refresh_info(self):
        if self.ip_item is None:
            self.info_lbl.configure(text=self.t("info_select"))
        elif self.ip_info_ready and self.ip:
            self._fill_info(self.ip_item)
        self._update_trecho()
        self._update_aspect_label()

    def _update_trecho(self):
        it = self.ip_item
        if not it:
            self.trecho_lbl.configure(text="")
            return
        if it["in"] <= 0 and not it["out"]:
            self.trecho_lbl.configure(text=self.t("tr_whole"))
            return
        end = it["out"] if it["out"] else self.ip_dur
        extra = self.t("tr_len", d=fmt_ms(end - it["in"])) if end else ""
        out_txt = fmt_ms(it["out"]) if it["out"] else self.t("tr_end")
        self.trecho_lbl.configure(text=self.t("tr_range", a=fmt_ms(it["in"]), b=out_txt, extra=extra))

    def _update_aspect_label(self):
        it = self.ip_item
        self.aspect_lbl.configure(
            text=self.t("aspect_line", mode=self.t("asp_" + it["aspect"])) if it else "")

    def _ip_pos(self):
        try:
            return float(self.ip.time_pos or 0.0)
        except Exception:
            return 0.0

    def _ip_ui(self):
        if not self.ip or self.ip_item is None:
            return
        if self.ip_item.get("is_slideshow"):
            self.ip_playing = self.ip_ss_on
            self.ip_play_btn.configure(text="⏸" if self.ip_playing else "▶")
            return
        if is_ytlive(self.ip_item):
            self.ip_playing = bool(getattr(self, "ip_yl_on", False))
            self.ip_play_btn.configure(text="⏸" if self.ip_playing else "▶")
            self.ip_time.configure(text="LIVE" if self.ip_playing else "—")
            return
        try:
            pos = self.ip.time_pos
            paused = bool(self.ip.pause)
        except Exception:
            return
        pos = float(pos or 0.0)
        self.ip_playing = not paused
        self.timeline.set(pos=pos)
        self.ip_time.configure(text=f"{fmt_ms(pos)} / {fmt_ms(self.ip_dur)}")
        self.ip_play_btn.configure(text="⏸" if self.ip_playing else "▶")

    def _ip_check_out(self):
        it = self.ip_item
        if not it["out"]:
            return
        try:
            pos = self.ip.time_pos
            if pos is not None and self.ip_from < it["out"] - 0.02 and pos >= it["out"]:
                self.ip.pause = True
                self.ip.seek(it["out"], "absolute+exact")
                self.ip_playing = False
        except Exception:
            pass

    def _ip_ready(self):
        return bool(self.ip and self.ip_item is not None and self.ip_dur > 0)

    def _ip_ss_set(self, on, restart=False):
        it = self.ip_item
        if restart:
            self._rtmp_stop(it["id"])
        self.ip_ss_on, self._ip_idle_key = on, None
        if not on and not restart:
            self._ip_idle_key = None
        self._rtmp_open_ip(it)
        self._ip_ui()

    def _ip_yl_set(self, on):
        self.ip_yl_on = on
        self._rtmp_open_ip(self.ip_item)
        self._ip_ui()

    def _ip_yt_open_play(self):
        """Play on a YouTube video that only shows its thumbnail: open it now and start it."""
        item = self.ip_item
        self._ip_force_load = True
        try:
            self.ip_load(item)
        finally:
            self._ip_force_load = False
        self._ip_autoplay = item

    def ip_toggle(self):
        if self.ip and self.ip_item is not None and getattr(self, "ip_yt_idle", False):
            self._ip_yt_open_play()
            return
        if self.ip and self.ip_item is not None and self.ip_item.get("is_slideshow"):
            self._ip_ss_set(not self.ip_ss_on)
            return
        if self.ip and self.ip_item is not None and is_ytlive(self.ip_item):
            self._ip_yl_set(not getattr(self, "ip_yl_on", False))      # a live cannot be paused: Play starts it, the same button stops it
            return
        if getattr(self, "_ip_autoplay", None) is self.ip_item and self.ip_item is not None:
            self._ip_autoplay = None                            # pressed again while it is opening: cancel the autoplay
            self.ip_play_btn.configure(text="▶")
            return
        if not self._ip_ready():
            return
        it = self.ip_item
        try:
            eof = bool(self.ip.eof_reached)
            if self.ip.pause or eof:
                pos = self._ip_pos()
                if eof or (it["out"] and pos >= it["out"] - 0.02):
                    pos = it["in"]
                    self.ip.seek(pos, "absolute+exact")
                self.ip_from = pos
                self.ip.pause = False
                self.ip_playing = True
            else:
                self.ip.pause = True
                self.ip_playing = False
        except Exception:
            pass
        self._ip_ui()

    def ip_stop(self):
        if getattr(self, "_ip_autoplay", None) is self.ip_item and self.ip_item is not None:
            self._ip_autoplay = None                            # Stop while the video is still opening: it must not start by itself
            try:
                self.ip.pause = True
            except Exception:
                pass
            self.ip_playing = False
            self.ip_play_btn.configure(text="▶")
        if self.ip and self.ip_item is not None and self.ip_item.get("is_slideshow"):
            self._ip_ss_set(False, restart=True)
            return
        if self.ip and self.ip_item is not None and is_ytlive(self.ip_item):
            self._ip_yl_set(False)
            return
        if not self._ip_ready():
            return
        try:
            self.ip.pause = True
            self.ip.seek(self.ip_item["in"], "absolute+exact")
        except Exception:
            pass
        self.ip_playing = False
        self._ip_ui()

    def ip_step(self, direction):
        if not self._ip_ready():
            return
        try:
            self.ip.command("frame-step" if direction > 0 else "frame-back-step")
        except Exception:
            pass
        self.ip_playing = False
        self.root.after(60, self._ip_ui)

    def ip_goto(self, which):
        if not self._ip_ready():
            return
        it = self.ip_item
        t = it["in"] if which == "in" else (it["out"] if it["out"] else max(0.0, self.ip_dur - MIN_GAP))
        try:
            self.ip.seek(t, "absolute+exact")
        except Exception:
            pass
        self.root.after(60, self._ip_ui)

    def _tl_seek(self, t, final):
        if not self._ip_ready():
            return
        try:
            self.ip.seek(t, "absolute+exact" if final else "absolute+keyframes")
        except Exception:
            pass
        if final:
            self.root.after(60, self._ip_ui)

    def _tl_marker(self, which, t, final):
        it = self.ip_item
        if not it:
            return
        it[which] = t
        self._marks_changed(it, seek=(t if t is not None else max(0.0, self.ip_dur - MIN_GAP)),
                            final=final)

    def _marks_changed(self, it, seek=None, final=True):
        self._refresh_row(it)
        self.timeline.set(t_in=it["in"], t_out=it["out"])
        self._update_trecho()
        if seek is not None and self._ip_ready():
            try:
                self.ip.seek(seek, "absolute+exact" if final else "absolute+keyframes")
            except Exception:
                pass
        if final:
            if self._is_playing(it):
                self.status_t("st_marks_saved")
            self.root.after(60, self._ip_ui)

    def _marks_ok(self):
        if not self._ip_ready():
            theme = THEMES[self.theme_name]
            self.status_t("yt_press_play_short" if getattr(self, "ip_yt_idle", False) else "st_marks_select", theme["RED"])
            return False
        return True

    def mark_in(self):
        if not self._marks_ok():
            return
        it, t = self.ip_item, self._ip_pos()
        theme = THEMES[self.theme_name]
        if t >= (it["out"] or self.ip_dur) - MIN_GAP:
            self.status_t("st_in_before_out", theme["RED"])
            return
        it["in"] = t if t > 0.02 else 0.0
        self.status_t("st_in_set", t=fmt_ms(it["in"]))
        self._marks_changed(it)

    def mark_out(self):
        if not self._marks_ok():
            return
        it, t = self.ip_item, self._ip_pos()
        theme = THEMES[self.theme_name]
        if t <= it["in"] + MIN_GAP:
            self.status_t("st_out_after_in", theme["RED"])
            return
        it["out"] = None if t >= self.ip_dur - 0.02 else t
        self.status_t("st_out_set", t=fmt_ms(it["out"]) if it["out"] else self.t("st_end_of_file"))
        self._marks_changed(it)

    def clear_marks(self):
        if not self._marks_ok():
            return
        it = self.ip_item
        it["in"], it["out"] = 0.0, None
        self.status_t("st_marks_cleared")
        self._marks_changed(it, seek=0.0, final=True)

    def _trecho_text(self, it):
        if it["in"] <= 0 and not it["out"]:
            return "—"
        return f"{fmt_ms(it['in'])} → {fmt_ms(it['out']) if it['out'] else self.t('tr_end')}"

    def _next_text(self, it):
        n = it["next"]
        if n == "none":
            return self.t("nx_none") + " ▾"
        if n == "prev":
            return self.t("nx_prev") + " ▾"
        if n in ("first", "last"):
            return self.t("nx_" + n) + " ▾"
        k = self._find(n) if n != "next" else None
        if k is not None:
            other = self.items[k]
            name = other.get("name") or os.path.basename(other["path"])
            short = name if len(name) <= 18 else name[:17] + "…"
            return f"→ {short} ▾"
        return self.t("nx_next") + " ▾"

    def _sim_text(self, it):
        s = it.get("sim", "none")
        if s == "none":
            return self.t("nx_none") + " ▾"
        k = self._find(s)
        if k is None:
            return self.t("nx_none") + " ▾"
        return f"→ {k + 1} ▾"

    def _sched_text(self, it):
        if not it.get("sched_on"):
            return "—"
        return it.get("sched") or "00:00:00"

    def _dur_text(self, it):
        if is_image(it["path"]):
            d = it.get("dur", 0.0) or 0.0
            return fmt_duration(d) if d > 0 else "0:00:00"
        else:
            try:
                out_pt = it.get("out")
                in_pt = it.get("in", 0.0)
                if out_pt and out_pt > in_pt:
                    return fmt_duration(out_pt - in_pt)
                elif it.get("is_youtube") and it.get("ytdl_info"):
                    dur = it["ytdl_info"].get("duration", 0)
                    if dur > 0:
                        return fmt_duration(dur - in_pt)
            except Exception:
                pass
            return self.t("dur_video")

    def _src_icon(self, it):
        """Badge in front of the name: Spotify (songs imported from Spotify) or YouTube; '' for everything else."""
        if it.get("is_group"):
            return ""
        kind = "spotify" if it.get("spotify") else ("youtube" if it.get("is_youtube") else "")
        if not kind and not (is_live(it) or it.get("is_slideshow") or it.get("is_countdown")):
            ext = os.path.splitext(str(it.get("path") or ""))[1].lower()
            kind = "audio" if ext in AUDIO_EXT else ("image" if ext in IMAGE_EXT or ext == ".gif" else ("video" if ext in VIDEO_EXT else ""))
        if not kind:
            return ""
        icons = self.__dict__.setdefault("_src_icons", {})
        if kind not in icons:
            try:
                import base64 as _b64
                icons[kind] = tk.PhotoImage(data=_b64.b64encode(source_icon_png(kind, 20)))
            except tk.TclError:
                icons[kind] = ""
        return icons[kind]

    def _row(self, it):
        if it.get("is_group"):
            n = sum(1 for o in self.items if o.get("group") == it["id"])
            vals = {cid: "" for cid, _w in COLUMNS}
            vals["nome"] = ("▸ " if it.get("collapsed") else "▾ ") + (it.get("name") or self.t("grp_default")) + f"   ({n})"
            return tuple(vals[cid] for cid, _ in COLUMNS)
        box = lambda v: "☑" if v else "☐"
        k = self._find(it["id"])
        display = it.get("name") or os.path.basename(it["path"])
        name = ("      " if it.get("group") else "") + ("⚠ " if it.get("missing") else "") + self._rtmp_mark(it) + display
        mute_str = box(it.get("mute"))
        sched_str = ("☑ " if it.get("sched_on") else "☐ ") + (it.get("sched") or "00:00:00")
        
        row_map = {
            "pos": str((len(self.items) if k is None else k) + 1),
            "nome": name,
            "out": f"{self._out_name(it['output'])} ▾",
            "next": self._next_text(it),
            "rep": box(it["repeat"]),
            "mute": mute_str,
            "fade_in": "",       # real checkbox + ms box are placed over these cells
            "fade_out": "",
            "f1": box(it["f1"]),
            "fl": box(it["fl"]),
            "sim": self._sim_text(it),
            "sched": sched_str,
            "dow": self._dow_text(it),
            "dur": self._dur_text(it),
            "trecho": self._trecho_text(it)
        }
        return tuple(row_map[cid] for cid, _ in COLUMNS)

    def _refresh_row(self, it):
        self.tree.item(it["id"], values=self._row(it), image=self._src_icon(it))
        self._schedule_fade_sync()

    def _refresh_all(self):
        for it in self.items:
            self._refresh_row(it)
        self._rtmp_reconcile()
        if hasattr(self, "mv") and self._mv_editor_alive():
            self._mv_fill_lists()
            self._mv_redraw_editor()
        if hasattr(self, "co") and self._co_editor_alive():
            self._co_fill_lists()
            self._co_redraw()

    def _index_of(self, iid):
        return next((k for k, it in enumerate(self.items) if it["id"] == iid), 0)

    def _new_item(self, path):
        self._next_id += 1
        return {"id": f"i{self._next_id}", "path": path, "name": None, "is_youtube": False, "is_rtmp": False, "rtmp_port": 0,
            "is_ndi": False, "ndi_source": "", "ndi_low_bw": False,
            "is_slideshow": False, "ss": None, "is_countdown": False, "cd": None, "is_capture": False, "cap": None,
                "output": self.outs[0].num if self.outs else 1,
                "next": "none", "repeat": False, "fade_in": 0, "fade_out": 0,
                "fade_in_on": False, "fade_out_on": False, "mute": False,
                "ms": 0, "f1": False, "fl": False, "wn": False, "sim": "none", "in": 0.0, "out": None,
                "aspect": "fit", "missing": False, "sched": "00:00:00", "sched_on": False, "sched_day": "", "sched_dow": [], "dur": 0.0,
                "is_group": False, "group": None, "collapsed": False}

    # ---- groups: a header row; the items that belong to it carry its id (it["group"]) and hide when it is collapsed
    def _hidden_ids(self):
        closed = {h["id"] for h in self.items if h.get("is_group") and h.get("collapsed")}
        return {it["id"] for it in self.items if not it.get("is_group") and it.get("group") in closed}

    def _tree_resync(self):
        """Tree rows follow self.items; the members of a collapsed group are detached (still in the list and playable)."""
        hidden = self._hidden_ids()
        shown = 0
        for it in self.items:
            if not self.tree.exists(it["id"]):
                continue
            if it["id"] in hidden:
                self.tree.detach(it["id"])
            else:
                self.tree.move(it["id"], "", shown)
                shown += 1
        self._schedule_fade_sync()

    def _regroup_order(self):
        """Each header is followed by its members (stable); members whose header is gone become ungrouped."""
        heads = {h["id"] for h in self.items if h.get("is_group")}
        for it in self.items:
            if it.get("group") not in heads:
                it["group"] = None
        out, done = [], set()
        for it in self.items:
            if it["id"] in done:
                continue
            out.append(it)
            done.add(it["id"])
            if it.get("is_group"):
                for m in self.items:
                    if m.get("group") == it["id"] and m["id"] not in done:
                        out.append(m)
                        done.add(m["id"])
        self.items = out

    def _toggle_group(self, head):
        head["collapsed"] = not head.get("collapsed")
        self._refresh_row(head)
        self._tree_resync()
        self.sel = [i for i in self.sel if i not in self._hidden_ids()]
        self._highlight()

    def add_group(self):
        """«+ Group»: a new header row. If items are selected they go inside it, otherwise it is an empty group at the end."""
        name = simpledialog.askstring(self.t("btn_group").lstrip("＋ ").strip(), self.t("ask_group_name"),
                                      initialvalue=self.t("grp_default"), parent=self.root)
        if name is None or not name.strip():
            return
        self._next_id += 1
        head = self._new_item("group://" + str(self._next_id))
        head.update(is_group=True, name=name.strip()[:60], output=self.outs[0].num if self.outs else 1)
        chosen = [it for it in self.items if it["id"] in set(self.sel) and not it.get("is_group")]
        at = min((self._find(it["id"]) for it in chosen), default=len(self.items))
        self.items.insert(at, head)
        self.tree.insert("", "end", iid=head["id"], values=self._row(head))
        for it in chosen:
            it["group"] = head["id"]
        self._regroup_order()
        self._refresh_all()
        self._tree_resync()
        self._highlight()
        self.status_t("st_group_made", name=head["name"], n=len(chosen))

    def _set_group(self, items, gid):
        for it in items:
            if not it.get("is_group"):
                it["group"] = gid
        self._regroup_order()
        self._refresh_all()
        self._tree_resync()
        self._highlight()

    def _ungroup(self, head):
        """Removes the header row only; its items stay in the list."""
        for it in self.items:
            if it.get("group") == head["id"]:
                it["group"] = None
        self.items = [it for it in self.items if it is not head]
        if self.tree.exists(head["id"]):
            self.tree.delete(head["id"])
        self.sel = [i for i in self.sel if i != head["id"]]
        self.anchor = self.sel[-1] if self.sel else None
        self._refresh_all()
        self._tree_resync()
        self._highlight()

    def _first_playable(self, k, step=1):
        """Index k, or the next item (in `step` direction, wrapping) that is not a group header; None if there is none."""
        n = len(self.items)
        if k is None or not n:
            return None
        for _ in range(n):
            if not self.items[k % n].get("is_group"):
                return k % n
            k = (k + step) % n
        return None

    def add_files(self, kind=None):
        """kind: None = any media, "video", "audio" or "image" (the Import menu splits them)."""
        exts = {"video": VIDEO_EXT, "audio": AUDIO_EXT, "image": IMAGE_EXT | {".gif"}}.get(kind, MEDIA_EXT)
        first = {"video": "ft_video", "audio": "ft_audio", "image": "ft_imgs"}.get(kind, "ft_media")
        title = {"video": "dlg_add_video", "audio": "dlg_add_audio", "image": "dlg_add_image"}.get(kind, "dlg_add_files")
        types = [(self.t(first), " ".join("*" + e for e in sorted(exts))), (self.t("ft_all"), "*.*")]
        self._add(filedialog.askopenfilenames(title=self.t(title), filetypes=types))

    def _dow_text(self, it):
        days = it.get("sched_dow") or []
        if not days or len(days) == 7:
            return self.t("dow_all")
        names = self.t("dow_names").split()
        return " ".join(names[d] for d in sorted(days) if 0 <= d < len(names))

    def _edit_dow(self, it):
        """Small window: tick the days of the week on which the scheduled item may start."""
        theme = THEMES[self.theme_name]
        win = tk.Toplevel(self.root)
        win.title(self.t("dow_title"))
        win.configure(bg=theme["BG"])
        win.transient(self.root)
        win.resizable(False, False)
        ttk.Label(win, text=self.t("dow_hint"), wraplength=260, justify="left").pack(padx=12, pady=(12, 6))
        vars_ = [tk.BooleanVar(value=(d in (it.get("sched_dow") or []))) for d in range(7)]
        for d, nm in enumerate(self.t("dow_names").split()):
            ttk.Checkbutton(win, text=nm, variable=vars_[d], takefocus=False).pack(anchor="w", padx=24)
        def ok():
            it["sched_dow"] = [d for d in range(7) if vars_[d].get()]
            self._refresh_row(it)
            win.destroy()
        row = ttk.Frame(win)
        row.pack(fill="x", padx=12, pady=10)
        ttk.Button(row, text=self.t("dow_all"), command=lambda: [v.set(False) for v in vars_]).pack(side="left")
        ttk.Button(row, text=self.t("btn_ok"), command=ok).pack(side="right")
        win.update_idletasks()
        win.geometry(f"+{self.root.winfo_rootx() + 120}+{self.root.winfo_rooty() + 120}")
        win.grab_set()

    def add_folder(self):
        folder = filedialog.askdirectory(title=self.t("dlg_add_folder"))
        if not folder:
            return
        names = sorted(os.listdir(folder), key=str.lower)
        self._add([os.path.join(folder, n) for n in names
                   if os.path.splitext(n)[1].lower() in MEDIA_EXT])

    # ---- RTMP input: ffmpeg in listen mode -> local UDP -> mpv ------------------------------------
    def _ffmpeg_path(self):
        if not self._ffmpeg:
            exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
            local = os.path.join(os.path.dirname(os.path.abspath(__file__)), exe)
            self._ffmpeg = local if os.path.isfile(local) else (shutil.which("ffmpeg") or "")
        return self._ffmpeg

    def _lan_ip(self):
        ip, stamp = self._rtmp_host
        if ip and time.time() - stamp < 30:
            return ip
        try:
            sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sk.connect(("10.255.255.255", 1))             # UDP connect: nothing is sent, just picks the NIC
            ip = sk.getsockname()[0]
            sk.close()
        except OSError:
            try:
                ip = socket.gethostbyname(socket.gethostname())
            except OSError:
                ip = "127.0.0.1"
        self._rtmp_host = (ip, time.time())
        return ip

    def _rtmp_addr(self, it):
        return f"rtmp://{self._lan_ip()}:{it['rtmp_port']}/live"

    def _rtmp_free_port(self, extra=()):
        used = {it.get("rtmp_port") for it in self.items if it.get("is_rtmp")} | set(extra)
        base = self.cfg.get("rtmp_port")
        p = base if isinstance(base, int) and not isinstance(base, bool) and 0 < base < 65000 else RTMP_BASE_PORT
        while p in used:
            p += 1
        return p

    def _unique_key(self, key, exclude=None):
        taken = {it["name"] for it in self.items if it.get("is_rtmp") and it["id"] != exclude}
        cand, n = key, 2
        while cand in taken:
            cand = f"{key}_{n}"
            n += 1
        return cand

    def _rtmp_set_identity(self, it, key):
        it["name"] = key
        it["path"] = f"{self._rtmp_addr(it)}/{key}"

    def add_rtmp(self):
        theme = THEMES[self.theme_name]
        if not self._ffmpeg_path():
            messagebox.showerror(self.t("dlg_rtmp"), self.t("rtmp_no_ffmpeg"), parent=self.root)
            return
        self._rtmp_dialog()

    def _rtmp_dialog(self, it=None):
        """Import > Capture > RTMP Input  /  Options of an RTMP item: the name (= key) and the address the sender must use."""
        theme = THEMES[self.theme_name]
        win = tk.Toplevel(self.root)
        win.title(self.t("dlg_rtmp") if it is None else self.t("ctx_options").rstrip("… "))
        win.configure(bg=theme["BG"])
        win.transient(self.root)
        win.geometry("540x270")
        body = ttk.Frame(win, padding=14)
        body.pack(fill="both", expand=True)
        key_var = tk.StringVar(value=it["name"] if it else self._unique_key("input1"))
        port = it["rtmp_port"] if it else self._rtmp_free_port()
        base = f"rtmp://{self._lan_ip()}:{port}/live"
        ttk.Label(body, text=self.t("rtmp_dlg_key")).pack(anchor="w")
        ent = ttk.Entry(body, textvariable=key_var, width=48)
        ent.pack(anchor="w", pady=(2, 10))
        ttk.Label(body, text=self.t("rtmp_dlg_url")).pack(anchor="w")
        url_var = tk.StringVar()
        url = ttk.Entry(body, textvariable=url_var, width=60, state="readonly")
        url.pack(anchor="w", pady=(2, 10))
        ttk.Label(body, text=self.t("rtmp_dlg_hint"), style="Hint.TLabel", wraplength=500, justify="left").pack(anchor="w")
        note = ttk.Label(body, text="", foreground=theme["RED"], wraplength=500, justify="left")
        note.pack(anchor="w", pady=(6, 0))

        def locked():
            listener = self._rtmp.get(it["id"]) if it else None
            return listener is not None and listener.state == "live"

        def refresh(*_a):
            url_var.set(f"{base}/{sanitize_key(key_var.get()) or ''}")
        key_var.trace_add("write", refresh)
        refresh()
        if it is not None and locked():
            ent.configure(state="disabled")
            note.configure(text=self.t("st_rtmp_locked"))

        def go(_e=None):
            key = sanitize_key(key_var.get())
            if not key:
                return
            if it is None:
                self._rtmp_create(key)
            elif not locked():
                if key != it["name"]:
                    self._rtmp_change_key(it, key)
            win.destroy()
        foot = ttk.Frame(body)
        foot.pack(side="bottom", fill="x")
        ttk.Button(foot, text=self.t("btn_close"), command=win.destroy, takefocus=False).pack(side="right")
        ttk.Button(foot, text=self.t("btn_apply_close") if it is not None else self.t("cap_add"), style="Accent.TButton",
                   takefocus=False, command=go).pack(side="right", padx=(0, 6))
        ent.bind("<Return>", go)
        ent.focus_set()

    def _rtmp_create(self, key):
        theme = THEMES[self.theme_name]
        it = self._new_item("")
        it["is_rtmp"] = True
        it["rtmp_port"] = self._rtmp_free_port()
        self._rtmp_set_identity(it, self._unique_key(key))
        self.items.append(it)
        self.tree.insert("", "end", iid=it["id"], values=self._row(it))
        self._rtmp_start(it)
        self._highlight()
        self.tree.see(it["id"])
        self.status_t("st_rtmp_added", theme["ACCENT"], addr=self._rtmp_addr(it), rk=it["name"])

    def _rtmp_start(self, it):
        """Start the feed for a live item: ffmpeg listener (RTMP) or NDI receiver."""
        if it["id"] in self._rtmp:
            return
        ff = self._ffmpeg_path()
        if not ff:
            return
        iid = it["id"]
        notify = lambda _l, i=iid: self._rtmp_thread_change(i)
        if it.get("is_slideshow"):
            listener = SlideshowRunner(ff, it.get("ss"), [], notify, os.path.join(self._app_dir(), "player_cache"))
        elif it.get("is_capture"):
            cap = cap_clean(it.get("cap"))
            if cap is None:
                return
            listener = CaptureSource(ff, cap, free_udp_ports(UDP_PORTS), notify)
        elif is_ytlive(it):
            args, path = self._yt_args(), it["path"]
            def resolve(i=it, a=args, pa=path):
                fmt = self._yt_fmt_for(i, "out")
                urls, meta = self._yt_resolve(pa, fmt, "out", a)
                if urls:                                  # the NDI sender (no output canvas = it opens the stream itself) uses the same addresses
                    self._yt_play_cache[(pa, fmt)] = {"t": time.time(), "urls": urls, "meta": meta}
                return urls
            listener = YtLiveSource(ff, resolve, free_udp_ports(UDP_PORTS), notify)
        elif it.get("is_ndi"):
            if not NDI.available:
                return
            listener = NdiReceiver(ff, it["ndi_source"], it.get("ndi_low_bw"), free_udp_ports(UDP_PORTS), notify)
        else:
            listener = RtmpListener(ff, it["name"], it["rtmp_port"], free_udp_ports(UDP_PORTS), notify)
        self._rtmp[iid] = listener
        listener.start()

    # ---- Capture inputs ------------------------------------------------------------------------------
    def _cap_ensure(self, item):
        """A capture runs only while somebody shows it (camera / encoder load): started on first use, stopped by _ss_gc."""
        if item is not None and (item.get("is_capture") or is_ytlive(item)) and item["id"] not in self._rtmp:
            self._rtmp_start(item)

    def _cap_add_item(self, cap, name=None):
        cap = cap_clean(cap)
        if cap is None:
            return None
        it = self._new_item(f"capture://{cap['kind']}/{cap_label(cap)}")
        it.update(is_capture=True, cap=cap, name=(name or cap_label(cap))[:80])
        self.items.append(it)
        self.tree.insert("", "end", iid=it["id"], values=self._row(it))
        self.sel, self.anchor = [it["id"]], it["id"]
        self._selection_changed()
        self._highlight()
        self.tree.see(it["id"])
        self.status_t("st_cap_added", name=it["name"])
        return it

    def _cap_describe(self, it):
        cap = it.get("cap") or {}
        k = cap.get("kind")
        bits = [self.t("cap_kind_" + k) if ("cap_kind_" + k) in STR[self.lang] else str(k)]
        if cap.get("size"):
            bits.append(cap["size"])
        if cap.get("fps"):
            bits.append(f"{cap['fps']} fps")
        return f"{cap_label(cap)}  ({', '.join(bits)})"

    def _cap_apply(self, it, cap):
        """Options of an existing capture item: new settings, the source restarts, whatever shows it re-opens."""
        cap = cap_clean(cap)
        if cap is None:
            return
        old = it.get("cap") or {}
        if (it.get("name") or "") == cap_label(old)[:80]:                   # still the automatic name: follow the source
            it["name"] = cap_label(cap)[:80]
        it["cap"], it["path"] = cap, f"capture://{cap['kind']}/{cap_label(cap)}"
        self._rtmp_stop(it["id"])
        self._rtmp_changed(it["id"])
        self.status_t("st_cap_applied", name=it["name"])

    def open_capture_dialog(self, kind, item=None):
        """Import > Capture > …  (item given: the Options of an existing capture, same window, «Apply» instead of «Add»)."""
        ff = self._ffmpeg_path()
        if not ff:
            messagebox.showerror(self.t("btn_capture"), self.t("rtmp_no_ffmpeg"), parent=self.root)
            return
        theme = THEMES[self.theme_name]
        editing = item is not None
        init = cap_clean(item.get("cap")) if editing else None
        w = tk.Toplevel(self.root)
        w.title(self.t("cap_kind_" + kind))
        w.configure(bg=theme["BG"])
        w.transient(self.root)
        w.geometry({"audio": "640x580", "video": "580x620", "screen": "580x560", "window": "580x660"}.get(kind, "580x560"))
        body = ttk.Frame(w, padding=12)
        body.pack(fill="both", expand=True)
        status = ttk.Label(body, text=self.t("cap_loading"), style="Hint.TLabel", wraplength=540, justify="left")
        foot = ttk.Frame(body)
        foot.pack(side="bottom", fill="x", pady=(10, 0))
        add_btn = ttk.Button(foot, text=self.t("cap_apply" if editing else "cap_add"), style="Accent.TButton", takefocus=False,
                             state="normal" if editing else "disabled")
        add_btn.pack(side="right")
        status.pack(side="bottom", anchor="w")
        lf = ttk.LabelFrame(body, text=self.t("cap_preview"), padding=6)
        lf.pack(side="bottom", fill="x", pady=(8, 0))
        pv_frame = tk.Frame(lf, bg="black", height=46 if kind == "audio" else 220)
        pv_frame.pack(fill="x")
        pv_frame.pack_propagate(False)

        def alive():
            try:
                return bool(w.winfo_exists())
            except tk.TclError:
                return False

        def set_status(text):
            if alive():
                status.configure(text=text)

        prev = CapPreview(self, pv_frame, kind, set_status)

        def close():
            prev.stop()
            w.destroy()
        w.protocol("WM_DELETE_WINDOW", close)
        ttk.Button(foot, text=self.t("btn_close"), command=close, takefocus=False).pack(side="right", padx=(0, 6))

        def later(fn):
            self._uiq.put(lambda: fn() if alive() else None)

        timer = {"id": None}
        build = {"fn": lambda: None}

        def preview_now():
            timer["id"] = None
            if alive():
                prev.show(build["fn"](True))

        def changed(*_a):
            if not alive():
                return
            if timer["id"] is not None:
                try:
                    w.after_cancel(timer["id"])
                except tk.TclError:
                    pass
            timer["id"] = w.after(500, preview_now)

        auto_txt = self.t("cap_auto")
        fps_var = tk.StringVar(value=(init or {}).get("fps") or ("30" if kind != "video" or not editing else auto_txt))
        fps_values = [auto_txt, "60", "50", "30", "29.97", "25", "24", "23.976", "15", "10", "5"]

        def fps_value():
            v = fps_var.get().strip()
            return cap_fps_norm("" if v == auto_txt else v)

        def fps_box(parent, values):
            row = ttk.Frame(parent)
            row.pack(anchor="w", pady=(0, 6))
            ttk.Label(row, text="FPS").pack(side="left", padx=(0, 6))
            cb = ttk.Combobox(row, textvariable=fps_var, values=values, width=9, state="normal")     # typed freely, e.g. 29.97
            cb.pack(side="left")
            cb.bind("<<ComboboxSelected>>", changed)
            fps_var.trace_add("write", changed)
            return row

        mouse_var = tk.BooleanVar(value=bool((init or {}).get("mouse", True)))

        def mouse_box(parent):
            ttk.Checkbutton(parent, text=self.t("cap_mouse"), variable=mouse_var, takefocus=False,
                            command=changed).pack(anchor="w", pady=(0, 6))

        if kind == "video":
            ttk.Label(body, text=self.t("cap_device")).pack(anchor="w")
            drow = ttk.Frame(body)
            drow.pack(anchor="w", pady=(2, 8), fill="x")
            dev = ttk.Combobox(drow, state="readonly", width=48)
            dev.pack(side="left")
            devbtn = ttk.Button(drow, text=self.t("cap_dev_opts"), takefocus=False, state="disabled")
            devbtn.pack(side="left", padx=(8, 0))
            ttk.Label(body, text=self.t("cap_mode")).pack(anchor="w")
            mode = ttk.Combobox(body, state="readonly", width=44)
            mode.pack(anchor="w", pady=(2, 8))
            fps_cb = fps_box(body, fps_values).winfo_children()[-1]
            modes = {"v": [], "want": ((init or {}).get("size", ""), (init or {}).get("vfmt", ""))}

            def mode_sel():
                k = mode.current()
                return modes["v"][k - 1] if 0 < k <= len(modes["v"]) else None

            def mode_picked(_e=None):
                m = mode_sel()
                fps_cb.configure(values=dshow_mode_fps(m) if m else fps_values)       # only the rates this mode offers
                if m:
                    cur = cap_fps_norm("" if fps_var.get().strip() == auto_txt else fps_var.get())
                    if cur in (None, "") or not (m["fmin"] - 0.01 <= float(cur) <= m["fmax"] + 0.01):
                        fps_var.set(f"{min(30.0, m['fmax']):g}")
                changed()

            def modes_for(_e=None):
                name = dev.get()
                mode.configure(values=[auto_txt])
                mode.current(0)
                add_btn.configure(state="normal" if name else "disabled")
                devbtn.configure(state="normal" if name else "disabled")
                changed()

                def work():
                    parsed = dshow_parse_modes(dshow_options(ff, "video", name))

                    def apply():
                        modes["v"] = parsed
                        mode.configure(values=[auto_txt] + [dshow_mode_label(m) for m in parsed])
                        want = modes.pop("want", None)
                        if want and want[0]:
                            for k, m in enumerate(parsed):
                                if m["size"] == want[0] and (not want[1] or m["fmt"] == want[1]):
                                    mode.current(k + 1)
                                    fps_cb.configure(values=dshow_mode_fps(m))
                                    break
                    later(apply)
                threading.Thread(target=work, daemon=True).start()
            dev.bind("<<ComboboxSelected>>", modes_for)
            mode.bind("<<ComboboxSelected>>", mode_picked)

            def build_video(quiet=False):
                if not dev.get():
                    return None
                fps = fps_value()
                if fps is None:
                    if not quiet:
                        set_status(self.t("cap_fps_bad"))
                    return None
                m = mode_sel()
                if m and fps and not (m["fmin"] - 0.01 <= float(fps) <= m["fmax"] + 0.01):
                    if not quiet:
                        set_status(self.t("cap_fps_range", lo=f"{m['fmin']:g}", hi=f"{m['fmax']:g}"))
                    return None
                return {"kind": "video", "device": dev.get(), "size": m["size"] if m else "", "vfmt": m["fmt"] if m else "",
                        "fps": fps}
            build["fn"] = build_video

            def dev_opts():
                name = dev.get()
                if not name:
                    return
                devbtn.configure(state="disabled")
                set_status(self.t("cap_dev_opts_busy"))
                prev.pause()                                    # the camera is exclusive: the preview lets go of it first

                def work():
                    err = ""
                    try:
                        r = subprocess.run([ff, "-hide_banner", "-f", "dshow", "-show_video_device_dialog", "true",
                                            "-i", "video=" + name, "-frames:v", "1", "-f", "null", "-"],
                                           capture_output=True, timeout=900, creationflags=_NOWIN, stdin=subprocess.DEVNULL)
                        if r.returncode != 0:
                            lines = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
                            err = (lines[-1] if lines else f"ffmpeg exit {r.returncode}")[:120]
                    except (OSError, subprocess.SubprocessError) as exc:
                        err = str(exc)[:120]

                    def done():
                        devbtn.configure(state="normal")
                        set_status(self.t("cap_dev_opts_fail", err=err) if err else "")
                        prev.resume()
                    later(done)
                threading.Thread(target=work, daemon=True).start()
            devbtn.configure(command=dev_opts)

            def found(devs):
                names = [n for n, k in devs if k == "video"]
                if init and init.get("device") and init["device"] not in names:
                    names.insert(0, init["device"])                 # unplugged right now: keep it selectable
                dev.configure(values=names)
                if names:
                    dev.current(names.index(init["device"]) if (init and init.get("device") in names) else 0)
                    modes_for()
                    status.configure(text="")
                else:
                    status.configure(text=self.t("cap_none"))
            threading.Thread(target=lambda: (lambda d: later(lambda: found(d)))(dshow_devices(ff)), daemon=True).start()
        elif kind == "audio":
            cols = ("dev", "ch", "bits", "rate")
            tv = ttk.Treeview(body, columns=cols, show="headings", height=8, selectmode="browse")
            for c, text, wd in (("dev", self.t("cap_col_dev"), 330), ("ch", self.t("cap_col_ch"), 70),
                                ("bits", self.t("cap_col_bits"), 70), ("rate", self.t("cap_col_rate"), 120)):
                tv.heading(c, text=text)
                tv.column(c, width=wd, anchor="w" if c == "dev" else "center")
            tv.pack(fill="both", expand=True)
            ttk.Label(body, text=self.t("cap_hint_audio"), style="Hint.TLabel", wraplength=600, justify="left").pack(anchor="w", pady=(6, 0))

            def on_sel(_e=None):
                add_btn.configure(state="normal" if (tv.selection() or editing) else "disabled")
                changed()
            tv.bind("<<TreeviewSelect>>", on_sel)

            def fmt_rng(rng, unit=""):
                lo, hi = rng
                return f"{lo}{unit}" if lo == hi else f"{lo}–{hi}{unit}"

            def details(names):
                for n in names:
                    info = dshow_parse_audio_modes(dshow_options(ff, "audio", n))
                    if info:
                        later(lambda n=n, i=info: tv.set(n, "ch", fmt_rng(i["ch"])) or tv.set(n, "bits", fmt_rng(i["bits"])) or
                              tv.set(n, "rate", fmt_rng(i["rate"], " Hz")))

            def found(devs):
                names = [n for n, k in devs if k == "audio"]
                if init and init.get("device") and init["device"] not in names:
                    names.insert(0, init["device"])
                for n in names:
                    tv.insert("", "end", iid=n, values=(n, "…", "…", "…"))
                if init and init.get("device") in names:
                    tv.selection_set(init["device"])
                    tv.see(init["device"])
                status.configure(text="" if names else self.t("cap_none"))
                threading.Thread(target=details, args=(names,), daemon=True).start()

            def build_audio(quiet=False):
                sel = tv.selection()
                return {"kind": "audio", "device": sel[0]} if sel else None
            build["fn"] = build_audio
            threading.Thread(target=lambda: (lambda d: later(lambda: found(d)))(dshow_devices(ff)), daemon=True).start()
        elif kind == "screen":
            self.refresh_monitors()
            ttk.Label(body, text=self.t("mv_screen")).pack(anchor="w")
            labels = [self.t("cap_screen_all")] + [self._monitor_label(i) for i in range(len(self.monitors))]
            scr = ttk.Combobox(body, state="readonly", values=labels, width=50)
            scr.current(1 if len(labels) > 1 else 0)
            if init and init.get("device") in labels:
                scr.current(labels.index(init["device"]))
            scr.pack(anchor="w", pady=(2, 8))
            scr.bind("<<ComboboxSelected>>", changed)
            fps_box(body, fps_values[1:])
            mouse_box(body)
            if sys.platform != "win32":
                status.configure(text=self.t("cap_windows_only"))
            else:
                status.configure(text="")
            add_btn.configure(state="normal")

            def build_screen(quiet=False):
                fps = fps_value()
                if fps is None:
                    if not quiet:
                        set_status(self.t("cap_fps_bad"))
                    return None
                i = scr.current()
                cap = {"kind": "screen", "device": labels[i] if i > 0 else self.t("cap_screen_all"), "fps": fps or "30",
                       "mouse": bool(mouse_var.get())}
                if i > 0 and i - 1 < len(self.monitors):
                    mo = self.monitors[i - 1]
                    cap.update(x=mo.x, y=mo.y, w=mo.width, h=mo.height)
                return cap
            build["fn"] = build_screen
            w.after(300, changed)
        else:                                                              # window
            lb = tk.Listbox(body, height=10, exportselection=False, bg=theme["PANEL"], fg=theme["FG"], highlightthickness=0,
                            selectbackground=theme["ACCENT"], activestyle="none")
            titles = []
            row = ttk.Frame(body)
            row.pack(anchor="w", pady=(6, 0), side="bottom")
            lb.pack(fill="both", expand=True)
            ttk.Label(row, text="FPS").pack(side="left", padx=(0, 6))
            fcb = ttk.Combobox(row, textvariable=fps_var, values=fps_values[1:], width=9, state="normal")
            fcb.pack(side="left")
            fcb.bind("<<ComboboxSelected>>", changed)
            fps_var.trace_add("write", changed)
            ttk.Checkbutton(row, text=self.t("cap_mouse"), variable=mouse_var, takefocus=False,
                            command=changed).pack(side="left", padx=14)

            def refresh():
                keep = lb.get(lb.curselection()[0]) if lb.curselection() else (init or {}).get("title")
                titles[:] = sorted({t for _h, t in list_windows()}, key=str.lower)
                if keep and keep not in titles:
                    titles.insert(0, keep)                               # closed right now: keep it selectable
                lb.delete(0, "end")
                for t in titles:
                    lb.insert("end", t)
                if keep in titles:
                    lb.selection_set(titles.index(keep))
                    lb.see(titles.index(keep))
                status.configure(text="" if (titles or sys.platform == "win32") else self.t("cap_windows_only"))
            ttk.Button(row, text=self.t("cap_refresh"), command=refresh, takefocus=False).pack(side="left")

            def on_pick(_e=None):
                add_btn.configure(state="normal" if lb.curselection() else "disabled")
                changed()
            lb.bind("<<ListboxSelect>>", on_pick)
            refresh()
            if init:
                w.after(300, changed)

            def build_window(quiet=False):
                sel = lb.curselection()
                if not sel:
                    return None
                fps = fps_value()
                if fps is None:
                    if not quiet:
                        set_status(self.t("cap_fps_bad"))
                    return None
                return {"kind": "window", "title": titles[sel[0]], "fps": fps or "30", "mouse": bool(mouse_var.get())}
            build["fn"] = build_window

        def add():
            cap = build["fn"](False)
            if cap is None:
                return
            if editing:
                self._cap_apply(item, cap)
                close()
            else:
                self._cap_add_item(cap)
        add_btn.configure(command=add)

    def _rtmp_stop(self, iid):
        listener = self._rtmp.pop(iid, None)
        if listener is not None:
            listener.stop()
            self._ss_audio(iid, False)


    # ---- Slideshow input -------------------------------------------------------------------------
    def _app_dir(self):
        return os.path.dirname(os.path.abspath(__file__))

    def _next_name(self, base):
        used = {it.get("name") for it in self.items}
        n = 1
        while f"{base} {n}" in used:
            n += 1
        return f"{base} {n}"

    def add_slideshow(self):
        if not self._ffmpeg_path():
            messagebox.showerror(self.t("btn_slideshow"), self.t("rtmp_no_ffmpeg"), parent=self.root)
            return
        it = self._new_item("slideshow://pending")
        it.update(is_slideshow=True, ss=ss_default_cfg(), name=self._next_name("Slideshow"))
        self.items.append(it)
        self.tree.insert("", "end", iid=it["id"], values=self._row(it))
        self.sel, self.anchor = [it["id"]], it["id"]
        self._selection_changed()
        self._highlight()
        self.tree.see(it["id"])
        self._open_slideshow_options(it)

    def _ss_apply(self, it, cfg):
        """New settings: restart the live source and re-open whatever shows it."""
        it["ss"] = cfg
        self._rtmp_stop(it["id"])
        for o in self.outs:
            if o.m and self._cur_item(o) is it:
                self._rtmp_open(o, it)
        if self.ip_item is it:
            self.info_lbl.configure(text=self._rtmp_info(it))
            self._rtmp_open_ip(it)
        self._refresh_row(it)

    # ---- slideshow driver: every tick, put the slide that is due on each player that shows the item ----
    def _ss_players(self, it):
        players = []
        for o in self.outs:
            if o.m and self._cur_item(o) is it:
                players += [p for p in (o.m, o.pv) if p]
        if self.ip and self.ip_item is it and self.ip_ss_on:
            players.append(self.ip)
        return players

    @staticmethod
    def _ss_overlay_text(kind, ovl):
        if not ovl:
            return None
        phase, p = ovl
        if kind in ("wipeleft", "wiperight"):
            w = int(1280 * p)
            if kind == "wipeleft":
                x0, x1 = (1280 - w, 1280) if phase == "out" else (0, 1280 - w)
            else:
                x0, x1 = (0, w) if phase == "out" else (w, 1280)
            if x1 - x0 < 1:
                return None
            return r"{\an7\pos(0,0)\bord0\shad0\p1\1c&H000000&}m %d 0 l %d 0 %d 720 %d 720{\p0}" % (x0, x1, x1, x0)
        a = p if phase == "out" else 1.0 - p                          # fade through black
        if a <= 0.01:
            return None
        return r"{\an7\pos(0,0)\bord0\shad0\p1\1c&H000000&\1a&H%02X&}m 0 0 l 1280 0 1280 720 0 720{\p0}" % int(round((1.0 - a) * 255))

    def _ss_show(self, player, view):
        start = f"{view['start']:.2f}" if view.get("start", 0) > 0 else "none"
        for name, value in (("aid", "no"), ("pause", False), ("loop_file", "inf" if (view["solo"] and view["kind"] == "video") else "no"),
                            ("image_display_duration", "inf"), ("start", start), ("end", "none")):
            self._setp(player, name, value)
        self._rtmp_props(player, False)
        player.play(view["path"])

    def _ss_tick(self):
        now = time.monotonic()
        kind = None
        for it in self.items:
            if not it.get("is_slideshow"):
                continue
            run = self._rtmp.get(it["id"])
            if run is None or run.state != "live":
                continue
            players = self._ss_players(it)
            self._ss_audio(it["id"], bool(players) and run._want_go, it)
            players = players + self._mv_players(it)               # multiview tiles show the slides too (audio: outputs only)
            if not players:
                continue
            view = run.advance(now)
            if view is None:
                continue
            kind = it["ss"].get("trans_type", "fade")
            text = self._ss_overlay_text(kind, view["ovl"])
            osig = (kind, text and (view["ovl"][0], int(view["ovl"][1] * 40)))
            for p in players:
                ent = self._ss_shown.get(id(p))
                try:
                    if ent is None or ent[0] is not p or ent[1] != view["token"]:
                        self._ss_show(p, view)
                        ent = (p, view["token"], None)
                    if ent[2] != osig:
                        self._osd_overlay(p, text)
                        ent = (p, ent[1], osig)
                except SHUTDOWN:
                    ent = None
                except Exception:
                    pass
                if ent is not None:
                    self._ss_shown[id(p)] = ent
        if self._n % 300 == 0 and len(self._ss_shown) > 20:
            self._ss_shown.clear()

    def _ss_audio(self, iid, want, it=None):
        """The slideshow's audio files: one mpv per slideshow, started with the first slide, looped when asked."""
        cur = self._ss_aud.get(iid)
        try:
            if not want:
                if cur is not None:
                    self._ss_aud.pop(iid, None)
                    cur.terminate()
                return
            if it is None:
                return
            if cur is None:
                files = [p for p in (it.get("ss") or {}).get("audio", []) if os.path.isfile(p)]
                if not files:
                    return
                cur = new_mpv(vo="null", vid="no", loop_playlist="inf" if it["ss"].get("loop_audio", True) else "no")
                self._ss_aud[iid] = cur
                cur.play(files[0])
                for f in files[1:]:
                    cur.command("loadfile", f, "append")
            if self._n % 30 == 0:
                vols = [o.vol.get() for o in self.outs if self._cur_item(o) is it and hasattr(o, "vol")]
                cur.volume = max(vols) if vols else 100
                cur.mute = bool(self.mute.get() or it.get("mute"))
        except SHUTDOWN:
            self._ss_aud.pop(iid, None)
        except Exception as exc:
            diag_log(f"slideshow audio failed: {exc}")

    def _ss_gc(self):
        """A slideshow nobody shows (no output, not in the preview) is stopped after a few seconds."""
        now = time.monotonic()
        for it in self.items:
            if not (it.get("is_slideshow") or it.get("is_capture") or is_ytlive(it)) or it["id"] not in self._rtmp:
                continue
            if ((self.ip_item is it and (getattr(self, "ip_yl_on", False) or not is_ytlive(it)))
                    or any(self._cur_item(o) is it for o in self.outs) or self._mv_players(it)):
                it.pop("_ss_idle", None)
            elif now - it.setdefault("_ss_idle", now) > 6:
                it.pop("_ss_idle", None)
                self._rtmp_stop(it["id"])
                self._refresh_row(it)

    def _open_slideshow_options(self, it):
        wins = self.__dict__.setdefault("_ss_wins", {})
        old = wins.get(it["id"])
        try:
            if old is not None and old.winfo_exists():
                old.lift()
                return
        except tk.TclError:
            pass
        theme = THEMES[self.theme_name]
        cfg = ss_clean_cfg(it.get("ss"))
        w = tk.Toplevel(self.root)
        wins[it["id"]] = w
        w.title(self.t("ss_win_title", name=it.get("name") or "Slideshow"))
        w.geometry("860x660")
        w.minsize(760, 560)
        w.configure(bg=theme["BG"])
        w.transient(self.root)
        ffprobe = ffprobe_path(self._ffmpeg_path())
        durs = {}                       # path -> seconds, filled by a worker thread
        media, audio = list(cfg["media"]), list(cfg["audio"])

        cache_dir = os.path.join(self._app_dir(), "player_cache")
        ffmpeg_bin = self._ffmpeg_path()

        def move_to(lst, s, d):
            lst.insert(d, lst.pop(s))
            fill()
            (mlb if lst is media else alb).selection_set(d)

        body = ttk.Frame(w, padding=10)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(1, weight=1)

        # ---- left: images and videos
        ttk.Label(body, text=self.t("ss_sec_media"), font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        mlb = SsListView(body, True, on_move=lambda s, d: move_to(media, s, d))
        mbox = mlb.box
        mbox.grid(row=1, column=0, sticky="nsew", padx=(0, 8), pady=(4, 4))
        mbar = ttk.Frame(body)
        mbar.grid(row=2, column=0, sticky="we", padx=(0, 8))
        # ---- right: audio
        ttk.Label(body, text=self.t("ss_sec_audio"), font=("Segoe UI", 10, "bold")).grid(row=0, column=1, sticky="w")
        alb = SsListView(body, False, on_move=lambda s, d: move_to(audio, s, d))
        abox = alb.box
        abox.grid(row=1, column=1, sticky="nsew", pady=(4, 4))
        abar = ttk.Frame(body)
        abar.grid(row=2, column=1, sticky="we")

        # ---- options
        opts = ttk.LabelFrame(body, text=self.t("ss_sec_opts"), padding=8)
        opts.grid(row=3, column=0, columnspan=2, sticky="we", pady=(10, 4))
        v_shuffle = tk.BooleanVar(value=cfg["shuffle"])
        v_img = tk.StringVar(value=f"{cfg['img_dur']:g}")
        v_tt = tk.StringVar(value=f"{cfg['trans_time']:g}")
        names = {k: self.t("ss_tr_" + k) for k in SS_TRANSITIONS}
        v_type = tk.StringVar(value=names[cfg["trans_type"]])
        v_fit = tk.StringVar(value=f"{cfg['fit_total']:g}")
        v_fit_audio = tk.BooleanVar(value=cfg["fit_audio"])
        v_loop = tk.BooleanVar(value=cfg["loop_audio"])
        v_res = tk.StringVar(value=cfg["res"])
        v_listloop = tk.BooleanVar(value=cfg["loop"])
        v_auto = tk.BooleanVar(value=cfg["autostart"])
        ttk.Checkbutton(opts, text=self.t("ss_shuffle"), variable=v_shuffle, takefocus=False).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(opts, text=self.t("ss_loop"), variable=v_listloop, takefocus=False).grid(row=4, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(opts, text=self.t("ss_autostart"), variable=v_auto, takefocus=False).grid(row=4, column=2, columnspan=2, sticky="w")
        def field(row, col, key, var, width=8):
            ttk.Label(opts, text=self.t(key)).grid(row=row, column=col, sticky="w", padx=(0, 6), pady=2)
            e = ttk.Entry(opts, textvariable=var, width=width)
            e.grid(row=row, column=col + 1, sticky="w", padx=(0, 18), pady=2)
            return e
        e_img = field(1, 0, "ss_img_dur", v_img)
        field(2, 0, "ss_trans_time", v_tt)
        ttk.Label(opts, text=self.t("ss_res")).grid(row=0, column=2, sticky="w", padx=(0, 6))
        ttk.Combobox(opts, textvariable=v_res, values=SS_RESOLUTIONS, state="readonly", width=10).grid(row=0, column=3, sticky="w")
        ttk.Label(opts, text=self.t("ss_trans_type")).grid(row=3, column=0, sticky="w", padx=(0, 6), pady=2)
        cb_type = ttk.Combobox(opts, textvariable=v_type, values=list(names.values()), state="readonly", width=14)
        cb_type.grid(row=3, column=1, sticky="w", pady=2)
        field(1, 2, "ss_fit_total", v_fit)
        ttk.Checkbutton(opts, text=self.t("ss_fit_audio"), variable=v_fit_audio, takefocus=False).grid(row=2, column=2, columnspan=2, sticky="w")
        ttk.Checkbutton(opts, text=self.t("ss_loop_audio"), variable=v_loop, takefocus=False).grid(row=3, column=2, columnspan=2, sticky="w")
        summary = ttk.Label(body, text="", style="Hint.TLabel")
        summary.grid(row=4, column=0, columnspan=2, sticky="w")
        foot = ttk.Frame(body)
        foot.grid(row=6, column=0, columnspan=2, sticky="e", pady=(6, 0))

        def alive():
            try:
                return bool(w.winfo_exists())
            except tk.TclError:
                return False

        def num(var, default, lo, hi):
            try:
                v = float(var.get().replace(",", "."))
            except ValueError:
                return default
            return min(hi, max(lo, v))

        def probe_missing():
            todo = [p for p in set(media + audio) if p not in durs and os.path.isfile(p) and (not is_image(p))]
            if not todo:
                return
            def work():
                for p in todo:
                    durs[p] = probe_duration(ffprobe, p)
                self._uiq.put(refresh_summary)
            threading.Thread(target=work, daemon=True).start()

        def current_cfg():
            c = ss_default_cfg()
            c.update(media=list(media), audio=list(audio), shuffle=v_shuffle.get(),
                     img_dur=num(v_img, 5.0, 0.5, 3600), trans_time=num(v_tt, 1.0, 0.0, 30),
                     trans_type=next((k for k, n in names.items() if n == v_type.get()), "fade"),
                     fit_total=num(v_fit, 0.0, 0.0, 86400), fit_audio=v_fit_audio.get(),
                     loop_audio=v_loop.get(), res=v_res.get(), loop=v_listloop.get(), autostart=v_auto.get())
            return ss_clean_cfg(c)

        def refresh_summary(*_):
            if not alive():
                return
            c = current_cfg()
            atotal = sum(durs.get(p, 0.0) for p in audio)
            plan = ss_plan(c, durs, atotal)
            auto = (c["fit_audio"] and atotal > 0) or c["fit_total"] > 0
            e_img.configure(state="disabled" if auto else "normal")
            txt = self.t("ss_summary", n=len(media), img=f"{plan['img_dur']:.2f}", total=fmt_duration(plan["total"]))
            if audio:
                txt += "   ·   " + self.t("ss_audio_total", total=fmt_duration(atotal))
            summary.configure(text=txt)

        pending = set()

        def set_thumb(p, png):
            if not alive():
                return
            try:
                mlb.set_image(p, tk.PhotoImage(file=png))
            except tk.TclError:
                pass

        def make_thumbs():
            todo = [p for p in media if p not in mlb.images and p not in pending and os.path.isfile(p)]
            if not todo or not ffmpeg_bin:
                return
            pending.update(todo)

            def work():
                for p in todo:
                    png = ss_thumb(ffmpeg_bin, p, cache_dir)
                    if png:
                        self._uiq.put(lambda p=p, png=png: set_thumb(p, png))
            threading.Thread(target=work, daemon=True).start()

        def fill():
            mlb.set_rows(media)
            alb.set_rows(audio)
            probe_missing()
            make_thumbs()
            refresh_summary()

        def add(lst, paths):
            for p in paths:
                if p and p not in lst:
                    lst.append(p)
            fill()

        def remove(lst, lb):
            for k in sorted(lb.curselection(), reverse=True):
                del lst[k]
            fill()

        def move(lst, lb, d):
            idx = list(lb.curselection())
            if not idx:
                return
            if d < 0 and idx[0] == 0 or d > 0 and idx[-1] == len(lst) - 1:
                return
            for k in (idx if d < 0 else reversed(idx)):
                lst[k], lst[k + d] = lst[k + d], lst[k]
            fill()
            for k in idx:
                lb.selection_set(k + d)

        def add_media():
            exts = sorted(IMAGE_EXT | VIDEO_EXT)
            add(media, filedialog.askopenfilenames(parent=w, title=self.t("ss_add_media"), filetypes=[
                (self.t("ft_media"), " ".join("*" + e for e in exts)), (self.t("ft_images"), " ".join("*" + e for e in sorted(IMAGE_EXT))),
                (self.t("ft_videos"), " ".join("*" + e for e in sorted(VIDEO_EXT))), (self.t("ft_all"), "*.*")]))

        def add_folder():
            folder = filedialog.askdirectory(parent=w, title=self.t("ss_add_folder"))
            if folder:
                add(media, [os.path.join(folder, n) for n in sorted(os.listdir(folder), key=str.lower)
                            if os.path.splitext(n)[1].lower() in (IMAGE_EXT | VIDEO_EXT)])

        def add_audio():
            add(audio, filedialog.askopenfilenames(parent=w, title=self.t("ss_add_audio"), filetypes=[
                (self.t("ft_audio_files"), " ".join("*" + e for e in sorted(AUDIO_EXT))), (self.t("ft_all"), "*.*")]))

        def dropped(lst, exts, e):
            paths = []
            for p in self.root.tk.splitlist(e.data):
                if os.path.isdir(p):
                    paths += [os.path.join(p, n) for n in sorted(os.listdir(p), key=str.lower)]
                else:
                    paths.append(p)
            add(lst, [p for p in paths if os.path.splitext(p)[1].lower() in exts and os.path.isfile(p)])
            return "copy"
        if TkinterDnD is not None:
            for lv, lst, exts in ((mlb, media, IMAGE_EXT | VIDEO_EXT), (alb, audio, AUDIO_EXT)):
                try:
                    lv.tv.drop_target_register(DND_FILES)
                    lv.tv.dnd_bind("<<Drop>>", lambda e, l=lst, x=exts: dropped(l, x, e))
                except Exception:
                    pass

        for parent, lst, lb, adder in ((mbar, media, mlb, add_media), (abar, audio, alb, add_audio)):
            ttk.Button(parent, text=self.t("ss_add_media" if lst is media else "ss_add_audio"), command=adder, takefocus=False).pack(side="left")
            if lst is media:
                ttk.Button(parent, text=self.t("ss_add_folder"), command=add_folder, takefocus=False).pack(side="left", padx=(4, 0))
            ttk.Button(parent, text=self.t("ss_remove"), command=lambda l=lst, b=lb: remove(l, b), takefocus=False).pack(side="left", padx=(4, 0))
            ttk.Button(parent, text="▼", width=3, command=lambda l=lst, b=lb: move(l, b, 1), takefocus=False).pack(side="right")
            ttk.Button(parent, text="▲", width=3, command=lambda l=lst, b=lb: move(l, b, -1), takefocus=False).pack(side="right", padx=(0, 4))

        for var in (v_shuffle, v_img, v_tt, v_type, v_fit, v_fit_audio, v_loop, v_res, v_listloop, v_auto):
            var.trace_add("write", lambda *_: refresh_summary())

        status = ttk.Label(foot, text="", style="Hint.TLabel", wraplength=520, justify="left")
        status.pack(side="left", padx=(0, 12))

        def apply():
            c = current_cfg()
            if not c["media"]:
                status.configure(text=self.t("ss_no_media"))
                return False
            self._ss_apply(it, c)
            status.configure(text=self.t("ss_applied"))
            return True

        def apply_close():
            if apply():
                w.destroy()

        ttk.Button(foot, text=self.t("ss_btn_apply"), style="Accent.TButton", command=apply, takefocus=False).pack(side="left", padx=(0, 6))
        ttk.Button(foot, text=self.t("ss_apply_close"), command=apply_close, takefocus=False).pack(side="left")
        fill()

    # ---- Countdown input ---------------------------------------------------------------------------
    def add_countdown(self):
        it = self._new_item("countdown://pending")
        it.update(is_countdown=True, cd=cd_default(), name=self._next_name("Countdown"))
        it["path"] = "countdown://" + it["id"]
        self.items.append(it)
        self.tree.insert("", "end", iid=it["id"], values=self._row(it))
        self.sel, self.anchor = [it["id"]], it["id"]
        self._selection_changed()
        self._highlight()
        self.tree.see(it["id"])
        self._open_cd_panel(it)

    def _cd_draw(self, player, item):
        cd = item.get("cd")
        if not cd or not player:
            return
        self._osd_overlay(player, cd_ass(cd, item.get("name") or "", time.monotonic(), "",
                                         wall=datetime.datetime.now(), days=self.t("cd_days"),
                                         months=self.t("cd_months"), full=self.t("cd_full_date")))

    def _cd_players(self, item):
        players = []
        for o in self.outs:
            if self._cur_item(o) is item:
                players += [p for p in (o.m, o.pv) if p]
        if self.ip_item is item and self.ip:
            players.append(self.ip)
        return players

    def _cd_background(self, player, item):
        """Black picture (stop) or the chosen background image, looped, under the drawn countdown."""
        cd = item.get("cd") or {}
        bg = cd.get("bg_image") or ""
        self._setp(player, "background", "color")
        self._setp(player, "background_color", "#00000000" if cd.get("transparent") else "#000000")
        if bg and not cd.get("transparent") and os.path.isfile(bg):
            video = os.path.splitext(bg)[1].lower() in (VIDEO_EXT | {".gif"})
            self._setp(player, "aid", "no" if video else "auto")          # a background video stays silent
            self._setp(player, "loop_file", "inf")
            self._setp(player, "image_display_duration", "inf")
            self._setp(player, "start", "none")
            self._setp(player, "end", "none")
            player.play(bg)
        else:
            self._setp(player, "aid", "auto")
            player.command("stop")

    def _cd_reopen(self, it):
        """Background changed: reload what every output / the preview shows for this countdown."""
        for o in self.outs:
            if o.m and self._cur_item(o) is it:
                self._rtmp_open(o, it)
        if self.ip_item is it:
            self._rtmp_open_ip(it)

    def _cd_stop_audio(self):
        try:
            if self._cd_audio is not None:
                self._cd_audio.command("stop")
        except SHUTDOWN:
            self._cd_audio = None
        except Exception:
            pass

    def _cd_run_sync(self, it):
        """Audio file played (looped) while the counter runs: on only when it runs, is on screen and zero is not reached."""
        cd, now = it["cd"], time.monotonic()
        path = cd.get("audio_run") or ""
        want = bool(cd["running"] and path and os.path.isfile(path) and self._cd_players(it)
                    and not (cd_value(cd, now)[1] and not cd.get("negative")))
        cur = getattr(self, "_cd_run_id", None)
        try:
            if want and cur != it["id"]:
                self._cd_run_start(it)
            elif not want and cur == it["id"]:
                self._cd_run_stop()
        except SHUTDOWN:
            self._cd_runp, self._cd_run_id = None, None
        except Exception as exc:
            diag_log(f"countdown running audio failed: {exc}")

    def _cd_run_start(self, it):
        if getattr(self, "_cd_runp", None) is None:
            self._cd_runp = new_mpv(vo="null", vid="no", loop_file="inf")
        vols = [o.vol.get() for o in self.outs if self._cur_item(o) is it and hasattr(o, "vol")]
        self._cd_runp.volume = max(vols) if vols else 100
        self._cd_runp.mute = bool(self.mute.get() or it.get("mute"))
        self._cd_runp.play(it["cd"]["audio_run"])
        self._cd_run_id = it["id"]

    def _cd_run_stop(self):
        self._cd_run_id = None
        try:
            if getattr(self, "_cd_runp", None) is not None:
                self._cd_runp.command("stop")
        except SHUTDOWN:
            self._cd_runp = None
        except Exception:
            pass

    def _cd_play_audio(self, it):
        path = (it.get("cd") or {}).get("audio") or ""
        if not path or not os.path.isfile(path):
            return
        try:
            if self._cd_audio is None:
                self._cd_audio = new_mpv(vo="null", vid="no")
            vols = [o.vol.get() for o in self.outs if self._cur_item(o) is it and hasattr(o, "vol")]
            self._cd_audio.volume = max(vols) if vols else 100
            self._cd_audio.mute = bool(self.mute.get() or it.get("mute"))
            self._cd_audio.play(path)
        except SHUTDOWN:
            self._cd_audio = None
        except Exception as exc:
            diag_log(f"countdown audio failed: {exc}")
            self._cd_audio = None

    def _cd_tick(self):
        """Redraw every countdown that is on screen (called a few times per second)."""
        now = time.monotonic()
        for it in self.items:
            cd = it.get("cd")
            if not it.get("is_countdown") or not cd:
                continue
            left, done = cd_value(cd, now)
            self._cd_run_sync(it)
            if done and not cd.get("_done"):
                cd["_done"] = True
                if self._cd_players(it):
                    self._cd_play_audio(it)
            if cd["running"] and cd["mode"] == "down" and done and not cd.get("negative"):
                cd["acc"], cd["running"] = cd["total"], False          # reached zero: stop on 00:00
                self._refresh_row(it)
            wall = time.strftime("%H%M%S%d") if (cd["show_hour"] or cd["show_date"] or cd["show_dow"] or cd["main"] != "counter") else ""
            stamp = (cd_fmt(left, cd["total"]), done and int(now * 2) % 2, cd["ring"], cd["warn"] >= left,
                     int(left * 10) // 3 if (cd["ring"] or cd["bar"] != "none") else 0, cd["scale"], cd["dx"], cd["dy"],
                     cd["bg_color"], cd["bar"], cd["show_name"], cd["show_hour"], cd["hour12"], cd["show_date"],
                     cd["show_dow"], cd["transparent"], wall, cd["counter_font"], cd["text_font"], cd["main"], cd["date_fmt"], cd["counter_size"], cd["text_size"],
                     cd["counter_color"], cd["text_color"], cd["zero_red"], cd["bg_opacity"])
            if stamp == it.get("_cd_stamp"):
                continue
            it["_cd_stamp"] = stamp
            for p in self._cd_players(it) + self._mv_players(it):
                self._cd_draw(p, it)

    def _cd_control(self, it, action, secs=None):
        cd, now = it["cd"], time.monotonic()
        if action == "start":
            if cd_value(cd, now)[1] and cd["mode"] == "down" and not cd.get("negative") and not cd["running"]:
                cd["acc"] = 0.0
            if not cd["running"]:
                cd["running"], cd["t0"] = True, now
        elif action == "pause":
            cd["acc"], cd["running"] = cd_elapsed(cd, now), False
        elif action == "reset":
            cd["acc"], cd["running"] = 0.0, False
        elif action == "total":
            cd["total"] = max(1.0, min(359999.0, float(secs)))
        elif action == "shift":
            cd["total"] = max(1.0, min(359999.0, cd["total"] + secs))
        cd["_done"] = cd_value(cd, time.monotonic())[1]          # the audio fires only when zero is crossed from now on
        it["_cd_stamp"] = None
        self._refresh_row(it)
        self._cd_run_sync(it)
        self._cd_tick()
        if self.ip_item is it:
            self.info_lbl.configure(text=self._rtmp_info(it))

    def _open_cd_panel(self, it):
        wins = self.__dict__.setdefault("_cd_wins", {})
        old = wins.get(it["id"])
        try:
            if old is not None and old.winfo_exists():
                old.lift()
                return
        except tk.TclError:
            pass
        theme = THEMES[self.theme_name]
        cd = it["cd"]
        w = tk.Toplevel(self.root)
        wins[it["id"]] = w
        w.title(self.t("cd_title", name=it.get("name") or "Countdown"))
        sh = max(480, min(900, self.root.winfo_screenheight() - 90))
        w.geometry(f"560x{sh}")
        w.minsize(420, 320)
        w.resizable(True, True)
        w.configure(bg=theme["BG"])
        w.transient(self.root)
        sb = ttk.Scrollbar(w, orient="vertical")
        sb.pack(side="right", fill="y")
        scv = tk.Canvas(w, bg=theme["BG"], highlightthickness=0, yscrollcommand=sb.set)
        scv.pack(side="left", fill="both", expand=True)
        sb.configure(command=scv.yview)
        body = ttk.Frame(scv, padding=14)
        body_id = scv.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: scv.configure(scrollregion=scv.bbox("all")))
        scv.bind("<Configure>", lambda e: scv.itemconfigure(body_id, width=e.width))
        w.bind("<MouseWheel>", lambda e: scv.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        big = tk.Label(body, text="00:00", font=("Segoe UI", 48, "bold"), bg=theme["BG"], fg=theme["FG"])
        big.pack(pady=(0, 6))
        row = ttk.Frame(body)
        row.pack(fill="x", pady=2)
        v_min = tk.StringVar(value=str(int(cd["total"]) // 60))
        v_sec = tk.StringVar(value=str(int(cd["total"]) % 60))
        ttk.Label(row, text=self.t("cd_minutes")).pack(side="left")
        ttk.Spinbox(row, from_=0, to=5999, width=6, textvariable=v_min).pack(side="left", padx=(4, 12))
        ttk.Label(row, text=self.t("cd_seconds")).pack(side="left")
        ttk.Spinbox(row, from_=0, to=59, width=4, textvariable=v_sec).pack(side="left", padx=(4, 12))

        def ival(var):
            try:
                return max(0, int(float(var.get())))
            except ValueError:
                return 0

        def set_time():
            secs = ival(v_min) * 60 + ival(v_sec)
            if secs > 0:
                self._cd_control(it, "total", secs)
        ttk.Button(row, text=self.t("cd_set"), command=set_time, takefocus=False).pack(side="left")
        ctl = ttk.Frame(body)
        ctl.pack(fill="x", pady=(10, 4))
        ttk.Button(ctl, text=self.t("cd_start"), style="Accent.TButton", takefocus=False,
                   command=lambda: self._cd_control(it, "start")).pack(side="left", padx=(0, 6))
        ttk.Button(ctl, text=self.t("cd_pause"), takefocus=False, command=lambda: self._cd_control(it, "pause")).pack(side="left", padx=(0, 6))
        ttk.Button(ctl, text=self.t("cd_reset"), takefocus=False, command=lambda: self._cd_control(it, "reset")).pack(side="left", padx=(0, 18))
        ttk.Button(ctl, text="−1 min", width=7, takefocus=False, command=lambda: self._cd_control(it, "shift", -60)).pack(side="left", padx=(0, 4))
        ttk.Button(ctl, text="+1 min", width=7, takefocus=False, command=lambda: self._cd_control(it, "shift", 60)).pack(side="left")

        mode = tk.StringVar(value=cd["mode"])
        mrow = ttk.Frame(body)
        mrow.pack(fill="x", pady=(8, 2))
        for val, key in (("down", "cd_mode_down"), ("up", "cd_mode_up")):
            ttk.Radiobutton(mrow, text=self.t(key), variable=mode, value=val, takefocus=False).pack(side="left", padx=(0, 14))
        v_neg = tk.BooleanVar(value=cd["negative"])
        v_auto = tk.BooleanVar(value=cd["auto_start"])
        v_ring = tk.BooleanVar(value=cd["ring"])
        for var, key in ((v_neg, "cd_negative"), (v_auto, "cd_auto_start"), (v_ring, "cd_ring")):
            ttk.Checkbutton(body, text=self.t(key), variable=var, takefocus=False).pack(anchor="w", pady=1)
        v_name = tk.BooleanVar(value=cd["show_name"])
        v_hour = tk.BooleanVar(value=cd["show_hour"])
        v_h12 = tk.StringVar(value="12" if cd["hour12"] else "24")
        v_date = tk.BooleanVar(value=cd["show_date"])
        v_dow = tk.BooleanVar(value=cd["show_dow"])
        v_bar = tk.StringVar(value=cd["bar"])
        v_transp = tk.BooleanVar(value=cd["transparent"])
        ttk.Checkbutton(body, text=self.t("cd_show_name"), variable=v_name, takefocus=False).pack(anchor="w", pady=1)
        shf = ttk.LabelFrame(body, text=self.t("cd_sec_show"), padding=6)
        shf.pack(fill="x", pady=(6, 0))
        r1 = ttk.Frame(shf)
        r1.pack(fill="x")
        ttk.Checkbutton(r1, text=self.t("cd_show_hour"), variable=v_hour, takefocus=False).pack(side="left")
        ttk.Radiobutton(r1, text="24h", variable=v_h12, value="24", takefocus=False).pack(side="left", padx=(10, 4))
        ttk.Radiobutton(r1, text="AM/PM", variable=v_h12, value="12", takefocus=False).pack(side="left")
        r2 = ttk.Frame(shf)
        r2.pack(fill="x", pady=(2, 0))
        ttk.Checkbutton(r2, text=self.t("cd_show_date"), variable=v_date, takefocus=False).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(r2, text=self.t("cd_show_dow"), variable=v_dow, takefocus=False).pack(side="left")
        dfmt_names = {"ymd": "YYYY/MM/DD", "mdy": "MM/DD/YYYY", "dmy": "DD/MM/YYYY", "full": self.t("cd_date_full")}
        v_dfmt = tk.StringVar(value=dfmt_names[cd["date_fmt"]])
        r3 = ttk.Frame(shf)
        r3.pack(fill="x", pady=(4, 0))
        ttk.Label(r3, text=self.t("cd_date_fmt")).pack(side="left")
        ttk.Combobox(r3, textvariable=v_dfmt, values=list(dfmt_names.values()), state="readonly", width=24).pack(side="left", padx=6)
        v_main = tk.StringVar(value=cd["main"])
        r4 = ttk.Frame(shf)
        r4.pack(fill="x", pady=(6, 0))
        ttk.Label(r4, text=self.t("cd_main")).pack(side="left")
        for val, key in (("counter", "cd_main_counter"), ("hour", "cd_show_hour"), ("date", "cd_show_date"), ("dow", "cd_show_dow")):
            ttk.Radiobutton(r4, text=self.t(key), variable=v_main, value=val, takefocus=False).pack(side="left", padx=(8, 0))
        barf = ttk.LabelFrame(body, text=self.t("cd_sec_bar"), padding=6)
        barf.pack(fill="x", pady=(6, 0))
        for val in CD_BARS:
            ttk.Radiobutton(barf, text=self.t("cd_bar_" + val), variable=v_bar, value=val, takefocus=False).pack(side="left", padx=(0, 10))
        # ---- text and counter look
        v_cfont = tk.StringVar(value=cd["counter_font"])
        v_tfont = tk.StringVar(value=cd["text_font"])
        v_csize = tk.StringVar(value=f"{cd['counter_size']:g}")
        v_tsize = tk.StringVar(value=f"{cd['text_size']:g}")
        v_zero = tk.BooleanVar(value=cd["zero_red"])
        txf = ttk.LabelFrame(body, text=self.t("cd_sec_text"), padding=6)
        txf.pack(fill="x", pady=(6, 0))
        try:
            families = sorted({f for f in tkfont.families(w) if not f.startswith("@")}, key=str.lower)
        except Exception:
            families = []
        ttk.Label(txf, text=self.t("cd_counter_font")).grid(row=0, column=0, sticky="w", pady=2)
        ttk.Combobox(txf, textvariable=v_cfont, values=[""] + families, width=22).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(txf, text=self.t("cd_text_font")).grid(row=0, column=2, sticky="w")
        ttk.Combobox(txf, textvariable=v_tfont, values=[""] + families, width=22).grid(row=0, column=3, sticky="w", padx=6)
        ttk.Label(txf, text=self.t("cd_counter_size")).grid(row=1, column=0, sticky="w", pady=2)
        ttk.Spinbox(txf, from_=20, to=400, increment=5, width=6, textvariable=v_csize).grid(row=1, column=1, sticky="w", padx=6)
        ttk.Label(txf, text=self.t("cd_text_size")).grid(row=1, column=2, sticky="w")
        ttk.Spinbox(txf, from_=10, to=150, increment=2, width=6, textvariable=v_tsize).grid(row=1, column=3, sticky="w", padx=6)
        sw = {}

        def color_btn(row, col, key, label):
            ttk.Label(txf, text=self.t(label)).grid(row=row, column=col, sticky="w", pady=2)
            holder = ttk.Frame(txf)
            holder.grid(row=row, column=col + 1, sticky="w", padx=6)
            sw[key] = tk.Label(holder, width=3, bg=cd[key], relief="solid", bd=1)
            sw[key].pack(side="left")

            def pick():
                rgb, hexv = colorchooser.askcolor(color=cd[key], parent=w)
                if hexv:
                    cd[key] = hexv.lower()
                    sw[key].configure(bg=cd[key])
                    changed()
            ttk.Button(holder, text="…", width=3, command=pick, takefocus=False).pack(side="left", padx=4)
        color_btn(2, 0, "counter_color", "cd_counter_color")
        color_btn(2, 2, "text_color", "cd_text_color")
        ttk.Checkbutton(txf, text=self.t("cd_zero_red"), variable=v_zero, takefocus=False).grid(row=3, column=0, columnspan=4, sticky="w")
        v_warn = tk.StringVar(value=f"{cd['warn']:g}")
        v_end = tk.StringVar(value=cd["end_text"])
        grid = ttk.Frame(body)
        grid.pack(fill="x", pady=(6, 0))
        ttk.Label(grid, text=self.t("cd_warn")).grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(grid, textvariable=v_warn, width=8).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(grid, text=self.t("cd_end_text")).grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(grid, textvariable=v_end, width=30).grid(row=1, column=1, sticky="w", padx=8)

        # ---- background
        bgf = ttk.LabelFrame(body, text=self.t("cd_sec_bg"), padding=6)
        bgf.pack(fill="x", pady=(10, 0))
        bg_lbl = ttk.Label(bgf, text="", style="Hint.TLabel")

        def bg_text():
            name = os.path.basename(cd["bg_image"]) if cd["bg_image"] else self.t("cd_audio_none")
            bg_lbl.configure(text=f"{cd['bg_color']}  ·  {name}")

        def pick_color():
            rgb, hexv = colorchooser.askcolor(color=cd["bg_color"], parent=w)
            if hexv:
                cd["bg_color"] = hexv.lower()
                bg_text()
                changed(reopen=True)

        def pick_image():
            p = filedialog.askopenfilename(parent=w, title=self.t("cd_bg_image"), filetypes=[
                (self.t("ft_images") + " / " + self.t("ft_videos"), " ".join("*" + e for e in sorted(IMAGE_EXT | VIDEO_EXT | {".gif"}))),
                (self.t("ft_all"), "*.*")])
            if p:
                cd["bg_image"] = p
                bg_text()
                changed(reopen=True)

        def clear_image():
            cd["bg_image"] = ""
            bg_text()
            changed(reopen=True)
        ttk.Button(bgf, text=self.t("cd_bg_color"), command=pick_color, takefocus=False).pack(side="left")
        ttk.Button(bgf, text=self.t("cd_bg_image"), command=pick_image, takefocus=False).pack(side="left", padx=4)
        ttk.Button(bgf, text=self.t("cd_bg_clear"), command=clear_image, takefocus=False).pack(side="left")
        bg_lbl.pack(side="left", padx=10)
        v_opac = tk.DoubleVar(value=cd["bg_opacity"])
        orow = ttk.Frame(body)
        orow.pack(fill="x", pady=(4, 0))
        ttk.Label(orow, text=self.t("cd_bg_opacity")).pack(side="left")
        o_lbl = ttk.Label(orow, text=f"{cd['bg_opacity']:.0f}%", width=5)
        ttk.Scale(orow, from_=0, to=100, variable=v_opac, orient="horizontal",
                  command=lambda _v: o_lbl.configure(text=f"{v_opac.get():.0f}%")).pack(side="left", fill="x", expand=True, padx=8)
        o_lbl.pack(side="left")
        ttk.Checkbutton(body, text=self.t("cd_transparent"), variable=v_transp, takefocus=False).pack(anchor="w", pady=(4, 0))
        ttk.Label(body, text=self.t("cd_transparent_hint"), style="Hint.TLabel", wraplength=480, justify="left").pack(anchor="w")

        # ---- audio at zero
        auf = ttk.LabelFrame(body, text=self.t("cd_sec_audio"), padding=6)
        auf.pack(fill="x", pady=(8, 0))
        au_lbl = ttk.Label(auf, text="", style="Hint.TLabel")

        def au_text():
            au_lbl.configure(text=os.path.basename(cd["audio"]) if cd["audio"] else self.t("cd_audio_none"))

        def pick_audio():
            p = filedialog.askopenfilename(parent=w, title=self.t("cd_sec_audio"), filetypes=[
                (self.t("ft_audio_files"), " ".join("*" + e for e in sorted(AUDIO_EXT))), (self.t("ft_all"), "*.*")])
            if p:
                cd["audio"] = p
                au_text()

        def clear_audio():
            cd["audio"] = ""
            au_text()
        ttk.Button(auf, text=self.t("cd_audio_pick"), command=pick_audio, takefocus=False).pack(side="left")
        ttk.Button(auf, text=self.t("cd_audio_clear"), command=clear_audio, takefocus=False).pack(side="left", padx=4)
        ttk.Button(auf, text=self.t("cd_audio_test"), command=lambda: self._cd_play_audio(it), takefocus=False).pack(side="left", padx=(12, 4))
        ttk.Button(auf, text=self.t("cd_audio_stop"), command=self._cd_stop_audio, takefocus=False).pack(side="left")
        au_lbl.pack(side="left", padx=10)
        aur = ttk.LabelFrame(body, text=self.t("cd_audio_run"), padding=6)
        aur.pack(fill="x", pady=(8, 0))
        aur2 = ttk.Frame(aur)
        aur2.pack(fill="x")
        ar_lbl = ttk.Label(aur2, text="", style="Hint.TLabel")

        def ar_text():
            ar_lbl.configure(text=os.path.basename(cd["audio_run"]) if cd["audio_run"] else self.t("cd_audio_none"))

        def pick_run():
            p = filedialog.askopenfilename(parent=w, title=self.t("cd_audio_run"), filetypes=[
                (self.t("ft_audio_files"), " ".join("*" + e for e in sorted(AUDIO_EXT))), (self.t("ft_all"), "*.*")])
            if p:
                cd["audio_run"] = p
                ar_text()
                self._cd_run_sync(it)

        def clear_run():
            cd["audio_run"] = ""
            ar_text()
            self._cd_run_sync(it)

        def test_run():
            if cd["audio_run"] and os.path.isfile(cd["audio_run"]):
                try:
                    self._cd_run_start(it)
                except SHUTDOWN:
                    self._cd_runp = None
                except Exception as exc:
                    diag_log(f"countdown running audio failed: {exc}")
        ttk.Button(aur2, text=self.t("cd_audio_pick"), command=pick_run, takefocus=False).pack(side="left")
        ttk.Button(aur2, text=self.t("cd_audio_clear"), command=clear_run, takefocus=False).pack(side="left", padx=4)
        ttk.Button(aur2, text=self.t("cd_audio_test"), command=test_run, takefocus=False).pack(side="left", padx=(12, 4))
        ttk.Button(aur2, text=self.t("cd_audio_stop"), command=self._cd_run_stop, takefocus=False).pack(side="left")
        ar_lbl.pack(side="left", padx=10)
        ar_text()

        # ---- position and size: a small frame of the picture, drag = move, wheel = size
        lf = ttk.LabelFrame(body, text=self.t("cd_sec_layout"), padding=6)
        lf.pack(fill="x", pady=(8, 0))
        PW, PH = 320, 180
        cv = tk.Canvas(lf, width=PW, height=PH, bg="#000000", highlightthickness=1, highlightbackground=theme["LINE"], cursor="fleur")
        cv.pack()
        v_scale = tk.StringVar(value=f"{cd['scale']:.0f}")
        v_x = tk.StringVar(value=f"{cd['dx']:.0f}")
        v_y = tk.StringVar(value=f"{cd['dy']:.0f}")

        def draw_prev():
            cv.delete("all")
            cv.configure(bg=cd["bg_color"])
            k = cd["scale"] / 100.0
            cx, cy = (640 + cd["dx"]) / 1280 * PW, (360 + cd["dy"]) / 720 * PH
            r = 250 * k / 1280 * PW
            if cd["bar"] != "none":
                t = 22 / 720 * PH
                box = {"top": (0, 0, PW, t), "bottom": (0, PH - t, PW, PH), "left": (0, 0, t, PH), "right": (PW - t, 0, PW, PH)}[cd["bar"]]
                cv.create_rectangle(*box, fill="#78C850", outline="")
            if cd["ring"]:
                cv.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#78C850", width=max(2, r * 0.15))
            cv.create_text(cx, cy, text="00:00", fill="#FFFFFF", font=("Segoe UI", max(6, int(150 * k / 720 * PH * 0.55)), "bold"))

        def place(e):
            v_x.set(f"{max(-640.0, min(640.0, (e.x / PW - 0.5) * 1280)):.0f}")
            v_y.set(f"{max(-360.0, min(360.0, (e.y / PH - 0.5) * 720)):.0f}")

        def wheel(e):
            cd["scale"] = max(20.0, min(300.0, cd["scale"] + (5 if e.delta > 0 else -5)))
            v_scale.set(f"{cd['scale']:.0f}")
            changed()
            return "break"
        cv.bind("<Button-1>", place)
        cv.bind("<B1-Motion>", place)
        cv.bind("<MouseWheel>", wheel)
        srow = ttk.Frame(lf)
        srow.pack(fill="x", pady=(6, 0))
        ttk.Label(srow, text=self.t("cd_scale")).pack(side="left")
        ttk.Entry(srow, textvariable=v_scale, width=6).pack(side="left", padx=6)

        prow = ttk.Frame(lf)
        prow.pack(fill="x", pady=(6, 0))
        ttk.Label(prow, text=self.t("cd_pos_x")).pack(side="left")
        ttk.Spinbox(prow, from_=-640, to=640, increment=5, width=6, textvariable=v_x).pack(side="left", padx=(4, 12))
        ttk.Label(prow, text=self.t("cd_pos_y")).pack(side="left")
        ttk.Spinbox(prow, from_=-360, to=360, increment=5, width=6, textvariable=v_y).pack(side="left", padx=4)

        def center():
            v_x.set("0")
            v_y.set("0")
        ttk.Button(prow, text=self.t("cd_center"), command=center, takefocus=False).pack(side="right")

        def reset_layout():
            v_scale.set("100")
            center()
        ttk.Button(srow, text=self.t("cd_layout_reset"), command=reset_layout, takefocus=False).pack(side="right")

        def changed(reopen=False, *_):
            it["_cd_stamp"] = None
            draw_prev()
            if reopen:
                self._cd_reopen(it)
            self._cd_tick()

        def sync_opts(*_):
            cd["mode"] = mode.get()
            cd["negative"], cd["auto_start"], cd["ring"] = v_neg.get(), v_auto.get(), v_ring.get()
            cd["show_name"], cd["show_hour"], cd["show_date"], cd["show_dow"] = v_name.get(), v_hour.get(), v_date.get(), v_dow.get()
            cd["hour12"], cd["bar"] = v_h12.get() == "12", v_bar.get()
            cd["counter_font"] = re.sub(r"[{}\\,;]", "", v_cfont.get())[:60]
            cd["text_font"] = re.sub(r"[{}\\,;]", "", v_tfont.get())[:60]
            cd["main"] = v_main.get()
            cd["date_fmt"] = next((k for k, n in dfmt_names.items() if n == v_dfmt.get()), "ymd")
            cd["zero_red"] = v_zero.get()
            cd["bg_opacity"] = max(0.0, min(100.0, float(v_opac.get())))
            for key, var, lim in (("counter_size", v_csize, (20.0, 400.0)), ("text_size", v_tsize, (10.0, 150.0))):
                try:
                    cd[key] = max(lim[0], min(lim[1], float(var.get().replace(",", "."))))
                except ValueError:
                    pass
            if cd["transparent"] != v_transp.get():
                cd["transparent"] = v_transp.get()
                self._cd_reopen(it)
            try:
                cd["warn"] = max(0.0, min(3600.0, float(v_warn.get().replace(",", "."))))
            except ValueError:
                pass
            for key, var, lim in (("scale", v_scale, (20.0, 300.0)), ("dx", v_x, (-640.0, 640.0)), ("dy", v_y, (-360.0, 360.0))):
                try:
                    cd[key] = max(lim[0], min(lim[1], float(var.get().replace(",", "."))))
                except ValueError:
                    pass
            cd["end_text"] = v_end.get()[:80]
            cd["_done"] = cd_value(cd, time.monotonic())[1]
            changed()
        for var in (mode, v_neg, v_auto, v_ring, v_warn, v_end, v_scale, v_x, v_y, v_name, v_hour, v_h12, v_date, v_dow, v_bar, v_transp, v_cfont, v_tfont, v_main, v_dfmt, v_csize, v_tsize, v_zero, v_opac):
            var.trace_add("write", sync_opts)

        def alive():
            try:
                return bool(w.winfo_exists())
            except tk.TclError:
                return False

        def upd():
            if not alive() or self._closing:
                return
            left, done = cd_value(cd, time.monotonic())
            big.configure(text=cd_fmt(left, cd["total"]),
                          fg=theme["RED"] if (done and cd["mode"] == "down") else theme["FG"])
            w.after(200, upd)
        bg_text()
        au_text()
        draw_prev()
        upd()

    def _rtmp_stop_all(self):
        for iid in list(self._rtmp):
            self._rtmp_stop(iid)

    def _rtmp_reconcile(self):
        """One feed per live item: start missing ones, stop orphans, keep RTMP ports unique."""
        ids = {it["id"] for it in self.items if is_live(it) and not it.get("is_countdown")}
        ids.add(NDI_PV_ID)                                 # the preview receiver of the NDI import window is not a list item
        for iid in list(self._rtmp):
            if iid not in ids:
                self._rtmp_stop(iid)
        seen = set()
        for it in self.items:
            if not is_live(it) or it.get("is_countdown") or it.get("is_slideshow") or it.get("is_capture") or is_ytlive(it):
                continue                     # a slideshow / capture / YouTube live starts when it is shown, not when it is added
            if it.get("is_rtmp"):
                if it["rtmp_port"] <= 0 or it["rtmp_port"] in seen:
                    self._rtmp_stop(it["id"])
                    it["rtmp_port"] = self._rtmp_free_port(seen)
                seen.add(it["rtmp_port"])
                self._rtmp_set_identity(it, it["name"])
            self._rtmp_start(it)

    def _rtmp_clone(self, dup):
        dup["rtmp_port"] = 0                         # reconcile gives it its own port + listener
        dup["name"] = self._unique_key(dup["name"])

    def _rtmp_thread_change(self, iid):
        try:
            self._uiq.put(lambda: self._rtmp_changed(iid))           # listener thread -> Tk thread (via queue)
        except Exception:
            pass

    def _rtmp_changed(self, iid):
        k = self._find(iid)
        if k is None:
            return
        it = self.items[k]
        self._refresh_row(it)
        for o in self.outs:
            if o.m and self._cur_item(o) is it:
                self._rtmp_open(o, it)
        if self.ip_item is it:
            self.info_lbl.configure(text=self._rtmp_info(it))
            self._rtmp_open_ip(it)

    def _rtmp_mark(self, it):
        if it.get("is_countdown"):
            return "⏱ "
        if it.get("is_slideshow"):
            listener = self._rtmp.get(it["id"])
            return "" if listener is None else ("● " if listener.state == "live" else ("⚠ " if listener.state == "error" else "○ "))
        if not is_live(it):
            return ""
        listener = self._rtmp.get(it["id"])
        if listener is None or listener.state == "error":
            return "⚠ "
        return "● " if listener.state == "live" else "○ "

    def _rtmp_info(self, it):
        if it.get("is_countdown"):
            cd = it["cd"]
            left, done = cd_value(cd, time.monotonic())
            state = self.t("cd_state_done" if done else ("cd_state_running" if cd["running"] else "cd_state_paused"))
            return self.t("cd_info", name=it["name"], total=cd_fmt(cd["total"], cd["total"]), state=state)
        listener = self._rtmp.get(it["id"])
        if it.get("is_slideshow"):
            n = len((it.get("ss") or {}).get("media", []))
            if listener is None:
                state = self.t("ss_state_idle")
            elif listener.state == "live":
                state = self.t("ss_state_live")
            elif listener.state == "error":
                state = self.t("rtmp_err", err=listener.error)
            else:
                state = self.t("ss_state_prep")
            return self.t("ss_info", name=it["name"], n=n, state=state)
        if is_ytlive(it):
            state = (self.t("rtmp_err", err=listener.error) if (listener is not None and listener.state == "error")
                     else self.t("rtmp_state_live") if (listener is not None and listener.state == "live") else self.t("ytl_waiting"))
            return f"{it['name']}\n{state}"
        if it.get("is_capture"):
            if listener is None:
                state = self.t("cap_waiting")
            elif listener.state == "live":
                state = self.t("rtmp_state_live")
            elif listener.state == "error":
                state = self.t("rtmp_err", err=listener.error)
            else:
                state = self.t("cap_waiting")
            return f"{it['name']}\n{self._cap_describe(it)}\n{state}"
        ndi_item = bool(it.get("is_ndi"))
        if listener is None:
            state = self.t("ndi_missing_lib") if (ndi_item and not NDI.available) else self.t("rtmp_no_ffmpeg")
        elif listener.state == "live":
            state = self.t("rtmp_state_live")
        elif listener.state == "error":
            state = self.t("rtmp_err", err=listener.error)
        else:
            state = self.t("ndi_waiting" if ndi_item else "rtmp_waiting")
        if ndi_item:
            tag = f"  ({self.t('ndi_low_bw')})" if it.get("ndi_low_bw") else ""
            return f"{it['name']}\n{self.t('ndi_source')}: {it['ndi_source']}{tag}\n{state}"
        return (f"{it['name']}\n{self.t('rtmp_address')}: {self._rtmp_addr(it)}\n"
                f"{self.t('rtmp_key')}: {it['name']}\n{state}")

    def _osd_overlay(self, player, text):
        if not player:
            return
        try:
            if text:
                player.command("osd-overlay", 1, "ass-events", text, 1280, 720)
            else:
                player.command("osd-overlay", 1, "none", "")
        except SHUTDOWN:
            raise
        except Exception:
            pass

    def _rtmp_slate(self, item):
        """What an output shows while its live input has no signal (RTMP: address + key, NDI: source)."""
        listener = self._rtmp.get(item["id"])
        ndi_item = bool(item.get("is_ndi"))
        dim, white, accent = r"{\c&HB4B4B4&}", r"{\c&HFFFFFF&}", r"{\c&H37C8FF&}"
        clean = lambda txt: re.sub(r"[{}\\]", "", str(txt))
        if item.get("is_slideshow"):
            status = self.t("rtmp_err", err=clean(listener.error)) if (listener and listener.state == "error") else self.t("ss_state_prep")
            return r"\N".join([r"{\an5\fs30\b0}" + dim + self.t("ss_title"), "",
                               r"{\fs56\b1}" + white + clean(item.get("name") or ""), "", r"{\fs30\b0}" + dim + status])
        if is_ytlive(item):
            status = self.t("rtmp_err", err=clean(listener.error)) if (listener and listener.state == "error") else self.t("ytl_waiting")
            return r"\N".join([r"{\an5\fs30\b0}" + dim + self.t("ytl_title"), "",
                               r"{\fs56\b1}" + white + clean(item.get("name") or ""), "", r"{\fs30\b0}" + dim + status])
        if item.get("is_capture"):
            status = self.t("rtmp_err", err=clean(listener.error)) if (listener and listener.state == "error") else self.t("cap_waiting")
            return r"\N".join([r"{\an5\fs30\b0}" + dim + self.t("cap_title"), "",
                               r"{\fs56\b1}" + white + clean(item.get("name") or ""), "",
                               r"{\fs28\b0}" + dim + clean(self._cap_describe(item)), "", r"{\fs30\b0}" + dim + status])
        if listener is None:
            status = self.t("ndi_missing_lib") if (ndi_item and not NDI.available) else self.t("rtmp_no_ffmpeg")
        elif listener.state == "error":
            status = self.t("rtmp_err", err=clean(listener.error))
        elif listener.rejected:
            status = self.t("rtmp_bad_key", rk=clean(listener.rejected))
        else:
            status = self.t("ndi_waiting" if ndi_item else "rtmp_waiting")
        if ndi_item:
            lines = [r"{\an5\fs30\b0}" + dim + self.t("ndi_title"), "",
                     r"{\fs28\b0}" + dim + self.t("ndi_source"),
                     r"{\fs56\b1}" + accent + clean(item["ndi_source"])]
            if item.get("ndi_low_bw"):
                lines.append(r"{\fs26\b0}" + dim + self.t("ndi_low_bw"))
            lines += ["", r"{\fs30\b0}" + dim + status]
            return r"\N".join(lines)
        lines = [r"{\an5\fs30\b0}" + dim + self.t("rtmp_title"), "",
                 r"{\fs28\b0}" + dim + self.t("rtmp_address"),
                 r"{\fs52\b1}" + white + self._rtmp_addr(item), "",
                 r"{\fs28\b0}" + dim + self.t("rtmp_key"),
                 r"{\fs60\b1}" + accent + item["name"], "",
                 r"{\fs30\b0}" + dim + status]
        return r"\N".join(lines)

    def _rtmp_props(self, player, live, lowlat=False):
        """Low latency for the UDP feed of a live input; defaults again for every other item."""
        if live:
            values = (("cache", "no"), ("demuxer_readahead_secs", 0.1),
                      ("demuxer_lavf_analyzeduration", 0.5), ("demuxer_lavf_probesize", 1000000),
                      ("vd_lavc_threads", 1 if lowlat else 0), ("video_latency_hacks", bool(lowlat)),
                      ("audio_buffer", 0.0 if lowlat else 0.2), ("cache_pause", False))
        else:
            values = (("demuxer_readahead_secs", 1.0), ("demuxer_lavf_analyzeduration", 0),
                      ("demuxer_lavf_probesize", 5000000), ("vd_lavc_threads", 0), ("video_latency_hacks", False),
                      ("audio_buffer", 0.2), ("cache_pause", True))
        for name, value in values:
            self._setp(player, name, value)
        if True:
            try:                                          # the demuxer hands packets on as they come, nothing is held for analysis
                player.command("change-list", "demuxer-lavf-o", "set" if (live and lowlat) else "clr", "fflags=+nobuffer")
            except SHUTDOWN:
                raise
            except Exception:
                pass

    def _rtmp_open(self, out, item, only=None, tries=0):
        """Show the live feed when there is signal, otherwise the address/key slate (output + monitor)."""
        if item.get("is_countdown"):
            for role, player in (("m", out.m), ("pv", out.pv)):
                if player and not (only and role != only):
                    try:
                        self._cd_background(player, item)
                    except SHUTDOWN:
                        if role == "m":
                            raise
                    except Exception:
                        pass
                    self._cd_draw(player, item)
            return
        if item.get("is_slideshow") and item["id"] not in self._rtmp:
            self._rtmp_start(item)
        self._cap_ensure(item)
        listener = self._rtmp.get(item["id"])
        if out.m:
            self._setp(out.m, "aid", "auto")
        if item.get("is_slideshow"):
            for role, player in (("m", out.m), ("pv", out.pv)):
                if not player or (only and role != only):
                    continue
                self._ss_shown.pop(id(player), None)           # _ss_tick puts the current slide on it
                try:
                    if listener is not None and listener.state == "live":
                        if ss_clean_cfg(item.get("ss")).get("autostart", True) or listener._want_go:
                            listener.go()
                            self._osd_overlay(player, None)
                        else:
                            self._ss_first_frame(player, item)      # «Auto-start» is off: stays stopped on slide 1 until started by hand
                    else:
                        player.command("stop")
                        self._osd_overlay(player, self._rtmp_slate(item))
                except SHUTDOWN:
                    if role == "m":
                        raise
                except Exception:
                    pass
            return
        for role, player in (("m", out.m), ("pv", out.pv)):
            if not player or (only and role != only):
                continue
            try:
                if listener is not None and listener.state == "live":
                    self._osd_overlay(player, None)
                    self._rtmp_props(player, True, item.get("is_capture") or item.get("is_ndi"))
                    port = listener.udp_ports[0 if role == "m" else 1]
                    if item.get("is_slideshow"):
                        listener.go()
                    player.play(f"udp://127.0.0.1:{port}?{udp_q(item)}")
                    self._live_watch(lambda r=role: (out.m if r == "m" else out.pv) if self._cur_item(out) is item else None,
                                     item, lambda t, r=role: self._rtmp_open(out, item, only=r, tries=t), tries)
                else:
                    player.command("stop")
                    self._osd_overlay(player, self._rtmp_slate(item))
            except SHUTDOWN:
                if role == "m":
                    raise
            except Exception:
                pass

    def _ss_first_frame(self, player, item):
        """A slideshow that waits for a manual start: its first picture (or the slate) on that player."""
        try:
            self._ss_shown.pop(id(player), None)
            self._apply_aspect(player, item.get("aspect", "fit"))
            self._rtmp_props(player, False)
            first = (item.get("ss") or {}).get("media", [None])[0]
            if first and is_image(first) and os.path.isfile(first):
                self._osd_overlay(player, None)
                self._setp(player, "pause", False)
                player.play(first)
            else:
                player.command("stop")
                self._osd_overlay(player, self._rtmp_slate(item))
        except SHUTDOWN:
            raise
        except Exception:
            pass

    def _ss_start_now(self, it):
        """Context menu «Start slideshow»: runs a slideshow that was waiting (Auto-start off) on every screen that shows it."""
        run = self._rtmp.get(it["id"])
        if run is None:
            self._rtmp_start(it)
            run = self._rtmp.get(it["id"])
        if run is None:
            return
        run.go()
        for p in self._ss_players(it) + self._mv_players(it):
            self._ss_shown.pop(id(p), None)
            self._osd_overlay(p, None)

    def _ss_idle_picture(self, player, item):
        """Preview of a slideshow that was not started: its first picture (or the slate) until Play is pressed."""
        key = (item["id"], (item.get("ss") or {}).get("media", [""])[:1] and item["ss"]["media"][0])
        if self._ip_idle_key == key:
            return
        self._ip_idle_key = key
        try:
            self._apply_aspect(player, item["aspect"])
            self._rtmp_props(player, False)
            first = (item.get("ss") or {}).get("media", [None])[0]
            if first and is_image(first) and os.path.isfile(first):
                self._osd_overlay(player, None)
                self._setp(player, "pause", False)
                player.play(first)
            else:
                player.command("stop")
                self._osd_overlay(player, self._rtmp_slate(item))
        except SHUTDOWN:
            raise
        except Exception:
            pass

    def _live_watch(self, get_player, item, reopen, tries):
        """A UDP feed can start late (waiting for a keyframe) or not at all: check and re-open it a few times."""
        def check():
            listener = self._rtmp.get(item["id"])
            player = get_player()
            if self._closing or player is None or listener is None or listener.state != "live" or tries >= 3:
                return
            try:
                got = player.time_pos is not None
            except Exception:
                got = False
            if not got:
                reopen(tries + 1)
        self.root.after(2500, check)

    def _yt_thumb(self, item, then=None):
        """Path of a YouTube item's thumbnail ("" = none, "busy" = being fetched, None = just asked); cached, fetched in the background."""
        cache = self.__dict__.setdefault("_mv_thumbs", {})
        iid = item["id"]
        ent = cache.get(iid)
        if ent is None:
            cache[iid] = "busy"
            ff, cache_dir = self._ffmpeg_path(), os.path.join(self._app_dir(), "player_cache")

            def work():
                path = None
                try:
                    _title, png = fetch_youtube_preview(item["path"], ff)
                    if png:
                        os.makedirs(os.path.join(cache_dir, "thumbs"), exist_ok=True)
                        path = os.path.join(cache_dir, "thumbs", f"yt_{iid}.png")
                        with open(path, "wb") as fh:
                            fh.write(png)
                except Exception:
                    path = None
                self._uiq.put(lambda: (cache.__setitem__(iid, path or ""), then() if then else None))
            threading.Thread(target=work, daemon=True).start()
        return ent

    def _ip_yt_idle(self, item):
        """Preview of a YouTube live that was not started: its thumbnail (nothing is opened or downloaded)."""
        player = self.ip
        if not player:
            return
        ent = self._yt_thumb(item, then=lambda: self._ip_yt_idle(item) if (self.ip_item is item and (getattr(self, "ip_yt_idle", False)
                                                                           or (is_ytlive(item) and not getattr(self, "ip_yl_on", False)))) else None)
        try:
            self._osd_overlay(player, None)
            self._rtmp_props(player, False)
            if ent and ent != "busy":
                if self.__dict__.get("_ip_yt_shown") != (id(player), item["id"], ent):
                    self._ip_yt_shown = (id(player), item["id"], ent)
                    self._apply_aspect(player, item["aspect"])
                    self._setp(player, "pause", False)
                    player.play(ent)
            else:
                self._ip_yt_shown = None
                player.command("stop")
        except SHUTDOWN:
            raise
        except Exception:
            pass

    def _rtmp_open_ip(self, item, tries=0):
        """Preview window (selected item): the input's own UDP copy when live, otherwise address + key."""
        player = self.ip
        if not player:
            return
        if is_ytlive(item) and not getattr(self, "ip_yl_on", False):
            self._ip_yt_idle(item)                      # stopped: thumbnail, and the live's copy is not started for the preview
            return
        self._ip_yt_shown = None
        if item.get("is_countdown"):
            try:
                self._apply_aspect(player, item["aspect"])
                self._cd_background(player, item)
            except Exception:
                pass
            self._cd_draw(player, item)
            return
        if item.get("is_slideshow") and item["id"] not in self._rtmp:
            self._rtmp_start(item)                     # warm-up: it waits on slide 1 until Play
        self._cap_ensure(item)
        listener = self._rtmp.get(item["id"])
        if item.get("is_slideshow") and not self.ip_ss_on:
            self._ss_idle_picture(player, item)
            return
        if item.get("is_slideshow"):
            self._ss_shown.pop(id(player), None)
            try:
                if listener is not None and listener.state == "live":
                    listener.go()
                    self._osd_overlay(player, None)
                else:
                    player.command("stop")
                    self._osd_overlay(player, self._rtmp_slate(item))
            except SHUTDOWN:
                raise
            except Exception:
                pass
            return
        try:
            self._apply_aspect(player, item["aspect"])
            if listener is not None and listener.state == "live" and len(listener.udp_ports) > 2:
                if item.get("is_slideshow"):
                    listener.go()
                self._osd_overlay(player, None)
                self._rtmp_props(player, True, item.get("is_capture") or item.get("is_ndi"))
                self._setp(player, "pause", False)
                player.play(f"udp://127.0.0.1:{listener.udp_ports[2]}?{udp_q(item)}")
                self._live_watch(lambda: self.ip if self.ip_item is item else None, item,
                                 lambda t: self._rtmp_open_ip(item, t), tries)
            else:
                player.command("stop")
                self._osd_overlay(player, self._rtmp_slate(item))
        except Exception:
            pass

    def _open_item(self, out, item):
        if is_live(item):
            self._rtmp_open(out, item)
        else:
            self._osd_overlay(out.m, None)
            self._setp(out.m, "aid", "auto")
            out._ndi_hold_until = max(getattr(out, "_ndi_hold_until", 0.0), time.time() + 0.4)
            with MPV_GRAB_LOCK:
                st, out._load_start = getattr(out, "_load_start", None), None
                self._yt_play(out.m, item, "out", start=st)
            try:
                if self.shuffle_mode.get() == "off":
                    k = self._target_index(item)
                    if k is not None and self.items[k] is not item:
                        self._yt_prefetch(self.items[k])
            except SHUTDOWN:
                raise
            except Exception:
                pass

    def _rtmp_rename(self, it):
        """Renaming an RTMP input = changing its key; refused while it is receiving signal."""
        theme = THEMES[self.theme_name]

        def locked():
            listener = self._rtmp.get(it["id"])
            return listener is not None and listener.state == "live"

        if locked():
            self.status_t("st_rtmp_locked", theme["RED"])
            return
        raw = simpledialog.askstring(self.t("ctx_rename"), self.t("ask_rtmp_rename"),
                                     initialvalue=it["name"], parent=self.root)
        key = sanitize_key(raw)
        if not key or key == it["name"]:
            return
        if locked():                                  # signal may have arrived while the dialog was open
            self.status_t("st_rtmp_locked", theme["RED"])
            return
        self._rtmp_change_key(it, key)

    def _rtmp_change_key(self, it, key):
        self._rtmp_stop(it["id"])
        self._rtmp_set_identity(it, self._unique_key(key, exclude=it["id"]))
        self._rtmp_start(it)
        self._refresh_row(it)
        for o in self.outs:
            if o.m and self._cur_item(o) is it:
                self._rtmp_open(o, it)
        if self.ip_item is it:
            self.info_lbl.configure(text=self._rtmp_info(it))
            self._rtmp_open_ip(it)
        self.status_t("st_rtmp_applied", name=it["name"])

    # ---- NDI input -----------------------------------------------------------------------------------
    def add_ndi(self, item=None):
        """Import > NDI Input: a window listing every NDI source on the network, with an Import button.
        item given = the Options of an NDI item: same window, pick the new source and «Apply and close»."""
        if not (NDI.available and NDI.init()):
            messagebox.showerror(self.t("btn_ndi"), self._ndi_missing_text("ndi_missing_lib"), parent=self.root)
            return
        if not self._ffmpeg_path():
            messagebox.showerror(self.t("btn_ndi"), self.t("rtmp_no_ffmpeg"), parent=self.root)
            return
        old = self._ndi_win
        if old is not None:
            try:
                if old.winfo_exists():
                    if item is None:
                        old.lift()
                        return
                    old.destroy()                                   # the Options window replaces an open import window
            except tk.TclError:
                pass
        theme = THEMES[self.theme_name]
        win = tk.Toplevel(self.root)
        self._ndi_win = win
        win.title(self.t("btn_ndi"))
        win.geometry("940x520")
        win.minsize(780, 400)
        win.configure(bg=theme["BG"])
        win.transient(self.root)

        right = ttk.Frame(win, padding=(0, 12, 14, 14))
        right.pack(side="right", fill="y")
        left = ttk.Frame(win)
        left.pack(side="left", fill="both", expand=True)
        ttk.Label(left, text=self.t("ndi_edit_hint" if item else "ndi_win_hint"), padding=(14, 12, 14, 4)).pack(anchor="w")
        box = ttk.Frame(left, padding=(14, 0, 14, 0))
        box.pack(fill="both", expand=True)
        low_var = tk.BooleanVar(value=bool(item.get("ndi_low_bw")) if item else False)
        lb = tk.Listbox(box, selectmode="browse" if item else "extended", exportselection=False, activestyle="none",
                        bg=theme["ENTRY_BG"], fg=theme["FG"], selectbackground=theme["ACCENT"],
                        selectforeground="white", highlightthickness=1, highlightbackground=theme["LINE"],
                        bd=0, font=("Segoe UI", 10))
        sb = ttk.Scrollbar(box, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        lb.pack(side="left", fill="both", expand=True)
        ttk.Checkbutton(left, text=self.t("ndi_low_bw"), variable=low_var, takefocus=False).pack(anchor="w", padx=14, pady=(6, 0))
        status = ttk.Label(left, text="", padding=(14, 6, 14, 0), wraplength=420)
        status.pack(anchor="w")
        diag = ttk.Label(left, text="", padding=(14, 2, 14, 0), wraplength=420)
        diag.pack(anchor="w")
        # preview of the clicked source: its own temporary receiver (not in the list) shown in a small player
        ttk.Label(right, text=self.t("ndi_preview")).pack(anchor="w")
        pv_frame = tk.Frame(right, width=480, height=270, bg="black", highlightthickness=1, highlightbackground=theme["LINE"])
        pv_frame.pack(pady=(4, 6))
        pv_frame.pack_propagate(False)
        pv_note = tk.Label(pv_frame, text=self.t("ndi_preview_pick"), bg="black", fg="#9aa4b0", wraplength=440, justify="center")
        pv_note.place(relx=0.5, rely=0.5, anchor="center")
        pv_name = ttk.Label(right, text="", style="Hint.TLabel", wraplength=480)
        pv_name.pack(anchor="w")
        pv = {"player": None, "name": None, "key": None, "low": None}
        state = {"busy": False}
        opened = time.time()

        def alive():
            try:
                return bool(win.winfo_exists())
            except tk.TclError:
                return False

        def pv_stop():
            self._rtmp_stop(NDI_PV_ID)
            pv["name"], pv["key"] = None, None
            if pv["player"] is not None:
                try:
                    pv["player"].command("stop")
                except Exception:
                    pass

        def pv_poll():
            if not alive() or self._closing or pv["name"] is None:
                return
            lst = self._rtmp.get(NDI_PV_ID)
            player = pv["player"]
            try:
                if lst is not None and player is not None:
                    key = (id(lst), lst.state, getattr(lst, "gen", 0))
                    if lst.state == "live" and len(lst.udp_ports) > 2:
                        if pv["key"] != key:
                            pv["key"] = key
                            pv_note.place_forget()
                            self._apply_aspect(player, "fit")
                            self._rtmp_props(player, True, True)
                            self._setp(player, "pause", False)
                            player.play(f"udp://127.0.0.1:{lst.udp_ports[2]}?{udp_q({'is_ndi': True})}")
                    elif pv["key"] != key:
                        pv["key"] = key
                        try:
                            player.command("stop")
                        except Exception:
                            pass
                        pv_note.configure(text=self.t("ndi_preview_wait") if lst.state != "error" else self.t("ndi_preview_err"))
                        pv_note.place(relx=0.5, rely=0.5, anchor="center")
            except SHUTDOWN:
                raise
            except Exception:
                pass
            win.after(500, pv_poll)

        def pv_start(name, force=False):
            low = bool(low_var.get())
            if not force and pv["name"] == name and pv["low"] == low:
                return
            if pv["player"] is None:
                try:
                    win.update_idletasks()
                    pv["player"] = new_pv(str(pv_frame.winfo_id()))
                except Exception as exc:
                    pv_note.configure(text=str(exc)[:80])
                    return
            pv_stop()
            pit = self._new_item("ndi://" + name)
            pit.update(id=NDI_PV_ID, is_ndi=True, ndi_source=name, ndi_low_bw=low, name=name)
            pv["name"], pv["low"] = name, low
            pv_name.configure(text=name)
            pv_note.configure(text=self.t("ndi_preview_wait"))
            pv_note.place(relx=0.5, rely=0.5, anchor="center")
            self._rtmp_start(pit)
            win.after(500, pv_poll)

        def on_pick(_e=None):
            sel = lb.curselection()
            if sel:
                pv_start(lb.get(sel[-1]))

        def on_destroy(e):
            if e.widget is not win:
                return
            pv_stop()
            player, pv["player"] = pv["player"], None
            if player is not None:
                try:
                    mpv_terminate(player)
                except Exception:
                    pass
        win.bind("<Destroy>", on_destroy, add="+")
        low_var.trace_add("write", lambda *_a: pv["name"] and pv_start(pv["name"], force=True))

        def fill(found, err):
            diag.configure(text=NDI.diagnostics(self.t))
            mine = [o.ndi_sender.name for o in self.outs if o.ndi_sender is not None]
            found = [n for n in found if not any(n.endswith(f"({m})") for m in mine)]   # not our own outputs
            keep = {lb.get(i) for i in lb.curselection()}
            if item is not None:
                if item["ndi_source"] not in found:
                    found = found + [item["ndi_source"]]              # offline right now: still the current choice
                if not state.get("seeded"):
                    state["seeded"] = True
                    keep = {item["ndi_source"]}
            lb.delete(0, "end")
            for name in sorted(found, key=str.lower):
                lb.insert("end", name)
            for i in range(lb.size()):
                if lb.get(i) in keep:
                    lb.selection_set(i)
            if err:
                status.configure(text=self.t("ndi_err_item", err=err.strip().splitlines()[-1][:90]))
            elif found:
                status.configure(text=self.t("ndi_win_found", n=len(found)))
            elif time.time() - opened > 8:
                status.configure(text=self.t("ndi_win_none_hint"))      # still nothing: most likely the firewall
            else:
                status.configure(text=self.t("ndi_win_none"))

        def scan(wait_ms, quiet=False):
            if state["busy"]:
                return
            state["busy"] = True
            if not quiet:
                status.configure(text=self.t("ndi_win_searching"))

            def work():
                found, err = [], ""
                try:
                    found = [name for name, _src in NDI.sources(wait_ms=wait_ms)]
                    self._ndi_cache = (time.time(), list(found))
                except Exception:
                    err = traceback.format_exc()
                    print(err, file=sys.stderr)               # visible in the console for diagnosis
                self._uiq.put(lambda: done(found, err))
            threading.Thread(target=work, daemon=True).start()

        def done(found, err):
            state["busy"] = False
            if alive():
                fill(found, err)

        def auto():                                          # keep the list up to date while the window is open
            if alive() and not self._closing:
                scan(300, quiet=True)
                win.after(3000, auto)

        def do_import(_e=None):
            names = [lb.get(i) for i in lb.curselection()]
            if not names:
                status.configure(text=self.t("ndi_win_pick"))
                return
            if item is not None:
                self._ndi_apply(item, names[0], bool(low_var.get()))
                win.destroy()
                return
            for name in names:
                self._ndi_add_source(name, bool(low_var.get()))
            status.configure(text=self.t("ndi_win_imported", n=len(names)))

        row = ttk.Frame(win, padding=(14, 10, 14, 14))
        row.pack(fill="x")
        ttk.Button(row, text=self.t("ndi_btn_close"), takefocus=False, command=win.destroy).pack(side="right")
        ttk.Button(row, text=self.t("ndi_manual"), takefocus=False,
                   command=lambda: self._ndi_add_manual(item, bool(low_var.get()), win)).pack(side="right", padx=(0, 6))
        ttk.Button(row, text=self.t("btn_apply_close") if item else self.t("ndi_btn_import"), style="Accent.TButton",
                   takefocus=False, command=do_import).pack(side="left")
        lb.bind("<Double-Button-1>", do_import)
        lb.bind("<<ListboxSelect>>", on_pick)
        scan(5000 if NDI._find is None else 2500)
        win.after(3000, auto)

    def _open_import_menu(self):
        """«Import»: every way of adding an input, in one menu."""
        menu = self._make_menu(self.root)
        plain = lambda key: self.t(key).lstrip("＋ ").strip()
        menu.add_command(label=self.t("mi_video"), command=lambda: self.add_files("video"))
        menu.add_command(label=self.t("mi_audio"), command=lambda: self.add_files("audio"))
        menu.add_command(label=self.t("mi_image"), command=lambda: self.add_files("image"))
        menu.add_command(label=plain("btn_add_folder"), command=self.add_folder)
        menu.add_separator()
        menu.add_command(label=self.t("btn_youtube"), command=self.add_youtube)
        menu.add_command(label=self.t("btn_spotify"), command=self.add_spotify)
        cap = self._make_menu(menu)
        for k in ("video", "audio", "screen", "window"):
            cap.add_command(label=self.t("cap_mi_" + k), command=lambda kk=k: self.open_capture_dialog(kk))
        cap.add_separator()
        cap.add_command(label=self.t("btn_rtmp"), command=self.add_rtmp)
        cap.add_command(label=self.t("btn_ndi"), command=self.add_ndi)
        menu.add_cascade(label=self.t("btn_capture"), menu=cap)
        menu.add_separator()
        menu.add_command(label=self.t("btn_slideshow"), command=self.add_slideshow)
        menu.add_command(label=self.t("btn_countdown"), command=self.add_countdown)
        btn = self.import_btn
        try:
            menu.tk_popup(btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height())
        finally:
            menu.grab_release()

    def _ndi_missing_text(self, key):
        """Install hint + the real import/initialisation problem, so it can be diagnosed."""
        extra = list(NDI_IMPORT_ERRORS)
        if ndi is not None and np is not None and NDI._ok is False:
            extra.append(self.t("ndi_init_false"))
        return self.t(key) + ("\n\n" + "\n".join(extra) if extra else "")

    def _ndi_warm_up(self):
        if NDI.available and not self._closing:
            NDI.warm_up()
            self._ndi_refresh_cache()

    def _ndi_refresh_cache(self, wait_ms=400):
        """Looks for NDI sources in the background; the item context menu reads the last result."""
        if not NDI.available or self.__dict__.get("_ndi_scanning"):
            return
        self._ndi_scanning = True

        def work():
            try:
                found = [name for name, _src in NDI.sources(wait_ms=wait_ms)]
                self._ndi_cache = (time.time(), found)
            except Exception:
                pass
            finally:
                self._ndi_scanning = False
        threading.Thread(target=work, daemon=True).start()

    def _ndi_menu_names(self):
        t, found = self.__dict__.get("_ndi_cache", (0.0, []))
        if time.time() - t > 4.0:
            self._ndi_refresh_cache()
        mine = [o.ndi_sender.name for o in self.outs if o.ndi_sender is not None]
        return sorted([n for n in found if not any(n.endswith(f"({m})") for m in mine)], key=str.lower)

    def _ndi_ctx_menu(self, menu, chosen):
        """Item context menu > «NDI Inputs»: the NDI sources on the network. On a single NDI item a click switches it to that source;
        anywhere else it imports the source as a new input."""
        sub = self._make_menu(menu)
        names = self._ndi_menu_names()
        cur = chosen[0].get("ndi_source") if len(chosen) == 1 and chosen[0].get("is_ndi") else None
        if not names:
            sub.add_command(label=self.t("ndi_ctx_none"), state="disabled")
        for n in names:
            if cur is not None:
                sub.add_command(label=("✓  " if n == cur else "     ") + n,
                                command=lambda nn=n, it=chosen[0]: self._ndi_apply(it, nn, bool(it.get("ndi_low_bw"))))
            else:
                sub.add_command(label="     " + n, command=lambda nn=n: self._ndi_add_source(nn))
        sub.add_separator()
        sub.add_command(label=self.t("ndi_ctx_refresh"), command=lambda: self._ndi_refresh_cache(1500))
        sub.add_command(label=self.t("ndi_ctx_window"), command=self.add_ndi)
        menu.add_cascade(label=self.t("ndi_ctx_inputs"), menu=sub)

    def _ndi_add_manual(self, item=None, low=False, win=None):
        name = simpledialog.askstring(self.t("btn_ndi"), self.t("ask_ndi_manual"), parent=win or self.root)
        name = (name or "").strip()
        if name:                                       # "HOST (Source)" or an address like 192.168.1.10[:5961]
            if item is not None:
                self._ndi_apply(item, name[:200], low)
                if win is not None:
                    win.destroy()
            else:
                self._ndi_add_source(name[:200], low)

    def _ndi_apply(self, it, source, low):
        """Options of an NDI item: another source and/or Low Bandwidth; the receiver reconnects."""
        if (it.get("name") or "") == it.get("ndi_source"):             # still the automatic name: follow the source
            it["name"] = source
        it["ndi_source"], it["path"], it["ndi_low_bw"] = source, "ndi://" + source, bool(low)
        self._rtmp_stop(it["id"])
        self._rtmp_start(it)
        self._rtmp_changed(it["id"])
        self.status_t("st_ndi_applied", name=it["name"])

    def _ndi_set_low_bw(self, items, value):
        """Low Bandwidth is a property of the NDI item: change it and reconnect the receiver."""
        for it in items:
            it["ndi_low_bw"] = bool(value)
            self._rtmp_stop(it["id"])
            self._rtmp_start(it)
            self._refresh_row(it)
            self._rtmp_changed(it["id"])            # outputs / preview fall back to the slate until the new feed is live

    def _ndi_add_source(self, name, low_bw=False):
        theme = THEMES[self.theme_name]
        it = self._new_item("ndi://" + name)
        it.update(is_ndi=True, ndi_source=name, ndi_low_bw=bool(low_bw), name=name)
        self.items.append(it)
        self.tree.insert("", "end", iid=it["id"], values=self._row(it))
        self._rtmp_start(it)
        self.sel, self.anchor = [it["id"]], it["id"]                 # select it: its feed shows in the preview
        self._selection_changed()
        self.tree.see(it["id"])
        self.status_t("st_ndi_added", theme["ACCENT"], src=name)

    # ---- NDI output: what the output window shows, sent as an NDI source named after the output --------
    def _ndi_out_fps(self, out):
        var = getattr(out, "fps_var", None)
        return parse_fps(var.get()) if var is not None else None        # None = "Original": follow the file

    def _style_ndi_btn(self, out):
        """Keep the checkbox in step with the real state of the sender."""
        if out.ndi_var is not None:
            out.ndi_var.set(out.ndi_sender is not None)

    def _yt_fmt_for(self, item, role):
        """The yt-dlp format string the given mpv ('out', 'pv', 'ip') would have used for this YouTube item."""
        if role in ("pv", "ip"):
            return "worstaudio/worst" if item.get("yt_audio") else PV_YTDL_FORMAT
        if item.get("id") in self.__dict__.get("_yt_muxed", ()):
            # a video + audio pair that stalled: one stream that already carries both (no separate audio address to wait for)
            return "best[height<=720][vcodec^=avc1][acodec!=none]/best[height<=720][acodec!=none]/best[acodec!=none]"
        fmt = item.get("ytdl_format")
        return YT_DEFAULT_FORMAT if (not fmt or fmt == "best") else yt_prefer_h264(fmt)

    def _yt_play(self, player, item, role, start=None, _try=0):
        """Loads a YouTube item into an mpv WITHOUT mpv's own ytdl hook (Lua / JS inside libmpv: it crashed the process,
        mostly when the output, the monitor and the preview loaded the same video together). yt-dlp.exe resolves the
        stream addresses in a worker thread; mpv then opens the plain addresses. Live streams / any failure: the old way."""
        path = item["path"]
        if not item.get("is_youtube") or not YT_DIRECT_PLAY:
            player.play(path)
            return
        sopt = f",start={start:.3f}" if start else ""
        live = float(item.get("dur") or 0) <= 0                    # live streams: HLS addresses, they go stale sooner
        fmt = self._yt_fmt_for(item, role)
        key = (path, fmt)
        tok = object()
        player._yt_tok = tok
        player._yt_t0, player._yt_src = time.time(), "yt-dlp"
        player._yt_wait_until = time.time() + 100.0                 # the previous file's "end reached" must not skip this item meanwhile
        if _try >= 1:
            # the address from yt-dlp was refused (HTTP 403: some streams need the headers yt-dlp reports): mpv's own loader sends them
            diag_log(f"YouTube {path[-11:]} [{role}]: the resolved address did not play -> mpv's own loader")
            player._yt_wait_until = time.time() + 30.0
            try:
                with MPV_GRAB_LOCK:
                    if start:
                        player.command("loadfile", path, "replace", -1, f"start={start:.3f}")
                    else:
                        player.play(path)
            except SHUTDOWN:
                pass
            except Exception as exc:
                diag_log(f"YouTube own loader failed ({exc})")
            return

        def check():
            """9 s after the load: an mpv that is still idle never opened the address (403 / refused): resolve it again."""
            if getattr(player, "_yt_tok", None) is not tok or _try >= 1:
                return
            try:
                dead = bool(player.idle_active)
            except SHUTDOWN:
                return
            except Exception:
                return
            if dead:
                self._yt_play_cache.pop(key, None)
                diag_log(f"YouTube {path[-11:]} [{role}]: mpv did not start it, resolving the address again (try {_try + 2})")
                self._yt_play(player, item, role, start, _try + 1)

        def load(urls):
            if getattr(player, "_yt_tok", None) is not tok:
                return                                              # something else was loaded meanwhile
            if urls and item.get("yt_audio") and AUDIO_COVER:
                try:
                    self.root.after(1500, lambda: self._yt_cover_apply(player, item, tok))
                except Exception:
                    pass
            player._yt_wait_until = time.time() + 2.0
            if urls and _try < 1:
                try:
                    self.root.after(9000, check)
                except Exception:
                    pass
            try:
                with MPV_GRAB_LOCK:
                    if not urls:
                        player.play(path)
                        return
                    opts = "ytdl=no" + sopt
                    if len(urls) > 1:
                        opts += f",audio-file=%{len(urls[1])}%{urls[1]}"
                    try:
                        player.command("loadfile", urls[0], "replace", -1, opts)
                    except SHUTDOWN:
                        raise
                    except Exception:
                        player.command("loadfile", urls[0], "replace", opts)        # mpv without the index argument
            except SHUTDOWN:
                pass
            except Exception as exc:
                diag_log(f"YouTube direct load failed ({exc})")

        ent = self._yt_play_cache.get(key)
        if ent and ent["urls"] and time.time() - ent["t"] < (900 if live else 3 * 3600):
            player._yt_src = "saved address"
            load(ent["urls"])
            return
        args = self._yt_args()

        def work():
            urls, got_meta = self._yt_resolve(path, fmt, role, args)
            self._yt_play_cache[key] = {"t": time.time(), "urls": urls, "meta": got_meta}
            self._uiq.put(lambda: load(urls))

        threading.Thread(target=work, daemon=True).start()

    def _yt_cover_apply(self, player, item, tok, tries=0):
        """Audio-only item: the video's thumbnail is added as a picture track once the stream is open (no black window)."""
        if getattr(player, "_yt_tok", None) is not tok or getattr(player, "_cover_tok", None) is tok:
            return
        try:
            ready = not bool(player.idle_active)
        except SHUTDOWN:
            return
        except Exception:
            return
        ent = self._yt_thumb(item) if ready else None
        if ent and ent != "busy":
            player._cover_tok = tok
            try:
                player.command("video-add", ent, "select")
            except SHUTDOWN:
                pass
            except Exception as exc:
                diag_log(f"YouTube cover picture failed ({exc})")
            return
        if tries < 14:
            try:
                self.root.after(1500, lambda: self._yt_cover_apply(player, item, tok, tries + 1))
            except Exception:
                pass

    def _yt_args(self):
        """(yt-dlp executable or None, cookie args, extra args): read on the main thread, used by the workers."""
        cookie_args = yt_cookie_opts()[1]
        extra_args = []
        for part in (yt_extra_opts()[1] or "").split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                extra_args += ["--" + k, v]
        return yt_dlp_exe(), cookie_args, extra_args

    def _yt_prefetch(self, item, now=False):
        """Resolves the addresses of an item that is about to play (output + monitor formats) so that switching to it
        does not wait for yt-dlp (the screen stayed black for those seconds)."""
        try:
            if not YT_DIRECT_PLAY or not item or not item.get("is_youtube"):
                return
            if float(item.get("dur") or 0) <= 0:
                return
            path = item["path"]
            pf = self.__dict__.setdefault("_yt_pf", set())
            for role in ("out", "pv"):
                fmt = self._yt_fmt_for(item, role)
                key = (path, fmt)
                ent = self._yt_play_cache.get(key)
                if (ent and ent["urls"] and time.time() - ent["t"] < 3 * 3600) or key in pf:
                    continue
                pf.add(key)
                args = self._yt_args()

                def bg(path=path, fmt=fmt, role=role, key=key, args=args):
                    try:
                        if not now:
                            time.sleep(10.0)                    # the item that is starting resolves its own address first
                        with YT_PREFETCH_LOCK:                  # one background yt-dlp at a time (several outputs start together)
                            ent = self._yt_play_cache.get(key)
                            if ent and ent["urls"] and time.time() - ent["t"] < 3 * 3600:
                                return
                            urls, meta = self._yt_resolve(path, fmt, role, args)
                        if urls:
                            self._yt_play_cache[key] = {"t": time.time(), "urls": urls, "meta": meta}
                    finally:
                        pf.discard(key)
                threading.Thread(target=bg, daemon=True).start()
        except SHUTDOWN:
            raise
        except Exception:
            pass

    def _yt_resolve(self, path, fmt, role, args, _r=0):
        """yt-dlp -g in a worker thread -> ([addresses], 'codec|height|fps|id')."""
        exe, cookie_args, extra_args = args
        if True:
            urls, err, got_meta = [], "", ""
            t_res = time.time()
            try:
                # fallbacks keep the item's height limit and keep AV1 for the very end (software AV1 cannot keep up)
                m = re.search(r"height<=(\d+)", fmt or "")
                hf = f"[height<={m.group(1)}]" if m else ""
                fb = (f"bestvideo{hf}[vcodec!^=av01]+bestaudio/best{hf}[vcodec!^=av01]/"
                      f"bestvideo{hf}+bestaudio/best{hf}/best")
                for f, meta_on in ((fmt, True), (fmt, False), (fb, False)):
                    cmd = ([exe] if exe else [sys.executable, "-m", "yt_dlp"]) + ["-g", "--no-playlist", "--no-warnings", "-f", f] \
                        + (["--print", "%(vcodec)s|%(height)s|%(fps)s|%(format_id)s"] if meta_on else []) \
                        + cookie_args + extra_args + [path]
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, encoding="utf-8", errors="replace",
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    urls = [u.strip() for u in r.stdout.splitlines() if u.strip().startswith("http")][:2]
                    if urls:
                        meta = [ln.strip() for ln in r.stdout.splitlines() if "|" in ln and not ln.startswith("http")]
                        diag_log(f"YouTube {path[-11:]} [{role}] format '{f[:90]}' -> codec|height|fps|id = {meta[0] if meta else '?'}")
                        got_meta = meta[0] if meta else ""
                        break
                    err = ((r.stderr or "").strip().splitlines() or ["no address returned"])[-1]
            except Exception as exc:
                err = str(exc)
            diag_log(f"YouTube {path[-11:]} [{role}]: yt-dlp took {time.time() - t_res:.1f} s ({'ok' if urls else 'no address'})")
            if not urls and _r < 2 and cookie_args and yt_is_drm_error(err):
                self._yt_set_anon(True, err)
                return self._yt_resolve(path, fmt, role, (exe, [], extra_args), _r + 1)
            if not urls and _r < 2 and not cookie_args and YT_ANON["on"] and yt_is_bot_error(err) and YT_COOKIES["mode"] != "none":
                self._yt_set_anon(False, err)
                return self._yt_resolve(path, fmt, role, (exe, yt_cookie_opts()[1], extra_args), _r + 1)
            if not urls:
                diag_log(f"YouTube: yt-dlp gave no address ({err[:160]}): using mpv's own loader")
            return urls, got_meta

    def _yt_direct_url(self, item):
        """(stream URL, None) of the video stream the OUTPUT mpv plays for a YouTube item, taken from the addresses that
        _yt_play resolved. "pending" while they are being resolved; None when yt-dlp could not resolve them."""
        ent = self._yt_play_cache.get((item.get("path"), self._yt_fmt_for(item, "out")))
        live = float(item.get("dur") or 0) <= 0
        if ent is None or time.time() - ent["t"] >= (900 if live else 3 * 3600):
            return "pending"
        if ent["urls"]:
            return (ent["urls"][0], None)
        return None if time.time() - ent["t"] < 120 else "pending"

    def _ndi_ff_spec(self, out, for_tile=False):
        """Describes the local video file this output is playing so the NDI sender can decode it with ffmpeg
        (main thread only). None = use the mpv capture (YouTube, NDI / RTMP inputs, slideshow, countdown, images, audio)."""
        try:
            item = self._cur_item(out)
            if not item or out.m is None or item.get("is_countdown"):
                return None
            path = item.get("path") or ""
            src, ua = None, None
            live = False
            image = False
            udp = False
            ff = self._ffmpeg_path()
            if not ff:
                return None
            if is_live(item) and not (is_ytlive(item) and not for_tile and not self._canvas(out)):
                # RTMP / NDI inputs: ffmpeg reads the 4th local UDP copy of the signal (the others feed the windows)
                self._cap_ensure(item)
                lst = self._rtmp.get(item.get("id"))
                if (item.get("is_slideshow") or lst is None or getattr(lst, "state", "") != "live"
                        or len(getattr(lst, "udp_ports", ())) < 4):
                    return None
                src = f"udp://127.0.0.1:{lst.udp_ports[3]}?{udp_q(item)}"
                path = f"{path}#g{getattr(lst, 'gen', 0)}"
                live = udp = True
                if not for_tile and not self._canvas(out):
                    return None
            elif item.get("is_youtube"):
                if item.get("yt_audio"):
                    return None
                got = self._yt_direct_url(item)
                if got == "pending":
                    # the addresses are being resolved: hold the last picture (no mpv capture while the video is loading)
                    return dict(pending=True, path=path, ffmpeg=ff, key=("pending", path), paused=False, pos=0.0, t=time.time())
                if not got:
                    return None                                    # yt-dlp could not resolve it: the mpv capture is used
                src, ua = got
                live = float(item.get("dur") or 0) <= 0
            else:
                ext = os.path.splitext(path)[1].lower()
                image = ext in IMAGE_EXT
                if (ext not in VIDEO_EXT and not image) or not os.path.isfile(path):
                    return None
            if for_tile:                                       # the Multiview copy only needs the source, not the output's canvas / fps
                pos = float(self._prop(out, "time_pos", 0) or 0)
                return dict(path=path, src=src, ua=ua, pos=pos, paused=bool(self._prop(out, "pause", False)) and not live, live=live,
                            image=image, udp=udp, ffmpeg=ff, t=time.time())
            canvas = self._ndi_canvas(out)
            if canvas and (canvas[0] % 2 or canvas[1] % 2):
                return None
            div = self._q_div(out) if not self._canvas(out) else 1
            fps = self._ndi_out_fps(out)
            vf = build_vf(canvas, item.get("aspect", "fit"), fps, div)
            vf = vf[len("lavfi=["):-1] if vf.startswith("lavfi=[") else ""
            pos = float(self._prop(out, "time_pos", 0) or 0)
            paused = bool(self._prop(out, "pause", False))
            ytcmd = None
            if item.get("is_youtube") and not live and not image:
                # last resort for the NDI feed: yt-dlp downloads the video stream itself (its own headers / chunking) into ffmpeg
                exe, ca, xa = self._yt_args()
                vfmt = (self._yt_fmt_for(item, "out").split("+")[0] or "bestvideo") + "/bestvideo/best"
                ytcmd = ([exe] if exe else [sys.executable, "-m", "yt_dlp"]) + ["-q", "--no-warnings", "--no-playlist", "-f", vfmt, "-o", "-"] + ca + xa + [path]
            return dict(ytcmd=ytcmd, path=path, src=src, ua=ua, pos=pos, paused=paused and not live, vf=vf, canvas=canvas, fps=fps,
                        t=time.time(), ffmpeg=ff, ffprobe=ffprobe_path(ff), key=(path, vf, canvas, fps, src, div), live=live, image=image, udp=udp, div=div)
        except SHUTDOWN:
            raise
        except Exception:
            return None

    def _ndi_out_start(self, out, silent=False):
        if out.ndi_sender is not None:
            return
        if not (NDI.available and NDI.init()):
            if not silent:
                messagebox.showerror(self.t("btn_ndi_out"), self._ndi_missing_text("ndi_out_missing"), parent=self.root)
            return
        out.ndi_sender = NdiSender(out.name, lambda o=out: o.m, lambda o=out: self._ndi_canvas(o),
                                   lambda o=out: self._ndi_out_fps(o))
        out.ndi_sender.hold_fn = lambda o=out: time.time() < getattr(o, "_ndi_hold_until", 0.0)
        out.ndi_sender.spec_fn = lambda o=out: self._ndi_ff_spec(o)
        out.ndi_sender.refresh()
        out.ndi_sender.start()
        self._style_ndi_btn(out)
        if not silent:
            self.status_t("st_ndi_out_on", name=out.name)

    def _ndi_out_stop(self, out, silent=True):
        sender, out.ndi_sender = out.ndi_sender, None
        if sender is not None:
            sender.stop()
            self._style_ndi_btn(out)
            if not silent:
                self.status_t("st_ndi_out_off", name=out.name)
            # a window-less output keeps playing (Turn Off stops it): switching NDI off never interrupts playback

    def _ndi_out_rename(self, out):
        """The NDI source carries the output's name: renaming the output renames the source."""
        if out.ndi_sender is not None and out.ndi_sender.name != out.name:
            self._ndi_out_stop(out)
            self._ndi_out_start(out, silent=True)

    def _ndi_check_senders(self):
        """A sender that died (NDI runtime, old python-mpv...) is switched off and the reason is shown."""
        for o in self.outs:
            snd = o.ndi_sender
            if snd is not None:
                snd.refresh()
            if snd is not None and snd.error:
                err = snd.error
                self._ndi_out_stop(o)
                self.status_t("st_ndi_out_err", THEMES[self.theme_name]["RED"], name=o.name, err=err)

    def _toggle_ndi_out(self, out):
        want = out.ndi_var.get() if out.ndi_var is not None else out.ndi_sender is None
        if want:
            self._ndi_out_start(out)
        else:
            self._ndi_out_stop(out, silent=False)
        self._style_ndi_btn(out)                       # a failed start (missing libs) unticks the box again

    def _ask_youtube_url(self, initial=""):
        """Window to paste a YouTube link. Shows the title + thumbnail; for a playlist link it asks whether to
        import only the video or the entire playlist and lists the videos that would be imported.
        Returns {"mode": "video", "url"} / {"mode": "playlist", "url", "title", "entries"} / None."""
        theme = THEMES[self.theme_name]
        win = tk.Toplevel(self.root)
        win.title(self.t("dlg_youtube"))
        win.configure(bg=theme["BG"])
        win.transient(self.root)
        win.resizable(False, False)
        try:
            win.geometry(f"+{self.root.winfo_rootx() + 140}+{self.root.winfo_rooty() + 60}")
        except Exception:
            pass
        ttk.Label(win, text=self.t("ask_youtube_url"), padding=(14, 12, 14, 4)).pack(anchor="w")
        var = tk.StringVar()
        entry = ttk.Entry(win, textvariable=var, width=60)
        entry.pack(fill="x", padx=14)
        _acc = self.account_line("youtube")
        ttk.Label(win, text=self.t("lg_using", name=_acc) if _acc else self.t("lg_none_hint"), style="Hint.TLabel",
                  padding=(14, 3, 14, 0)).pack(anchor="w")
        ctx = tk.Menu(win, tearoff=0, bg=theme["PANEL"], fg=theme["FG"], activebackground=theme["ACCENT"], activeforeground="#ffffff")
        ctx.add_command(label=self.t("yt_ctx_cut"), command=lambda: entry.event_generate("<<Cut>>"))
        ctx.add_command(label=self.t("yt_ctx_copy"), command=lambda: entry.event_generate("<<Copy>>"))
        ctx.add_command(label=self.t("yt_ctx_paste"), command=lambda: entry.event_generate("<<Paste>>"))
        ctx.add_separator()

        def sel_all():
            entry.focus_set()
            entry.selection_range(0, "end")
            entry.icursor("end")
        ctx.add_command(label=self.t("yt_ctx_all"), command=sel_all)

        def popup(e):
            entry.focus_set()
            try:
                has_sel = entry.selection_present()
            except tk.TclError:
                has_sel = False
            try:
                paste_ok = bool(win.clipboard_get())
            except tk.TclError:
                paste_ok = False
            ctx.entryconfigure(0, state="normal" if has_sel else "disabled")
            ctx.entryconfigure(1, state="normal" if has_sel else "disabled")
            ctx.entryconfigure(2, state="normal" if paste_ok else "disabled")
            try:
                ctx.tk_popup(e.x_root, e.y_root)
            finally:
                ctx.grab_release()
        entry.bind("<Button-3>", popup)
        holder = tk.Frame(win, width=320, height=180, bg=theme["PANEL"], highlightthickness=1,
                          highlightbackground=theme["LINE"])
        holder.pack(pady=(12, 0))
        holder.pack_propagate(False)
        thumb = tk.Label(holder, bg=theme["PANEL"], fg=theme["MUTED"], text="▶", font=("Segoe UI", 28))
        thumb.pack(fill="both", expand=True)
        title_lbl = ttk.Label(win, text="", wraplength=380, justify="center", padding=(14, 8, 14, 0))
        title_lbl.pack()
        row = ttk.Frame(win, padding=(14, 12, 14, 14))
        row.pack(fill="x", side="bottom")
        ck_lbl = ttk.Label(row, text=self._yt_cookie_summary(), style="Hint.TLabel")

        def open_cookies():
            self._yt_consent_flow(win)
            ck_lbl.configure(text=self._yt_cookie_summary())

        def open_adv():
            self._open_yt_cookies(parent=win)
            ck_lbl.configure(text=self._yt_cookie_summary())
        ttk.Button(row, text=self.t("yt_cookies_btn"), takefocus=False, command=open_cookies).pack(side="left")
        ttk.Button(row, text="⚙", width=3, takefocus=False, command=open_adv).pack(side="left", padx=(4, 0))
        ck_lbl.pack(side="left", padx=8)

        # quality tabs (shown once a link is pasted)
        nb = ttk.Notebook(win)
        tab_v = ttk.Frame(nb, padding=10)
        tab_a = ttk.Frame(nb, padding=10)
        nb.add(tab_v, text=self.t("yt_tab_video"))
        nb.add(tab_a, text=self.t("yt_tab_audio"))
        v_labels = [self.t("yt_q_best")] + [f"{h}p" for h in YT_VIDEO_HEIGHTS[1:]]
        a_labels = [self.t("yt_q_best")] + [self.t("yt_q_abr", n=a) for a in YT_AUDIO_ABR[1:]]
        v_q = tk.StringVar(value=v_labels[0])
        a_q = tk.StringVar(value=a_labels[0])
        v_hw = tk.BooleanVar(value=False)
        ttk.Label(tab_v, text=self.t("yt_q_video")).grid(row=0, column=0, sticky="w")
        ttk.Combobox(tab_v, textvariable=v_q, values=v_labels, state="readonly", width=20).grid(row=0, column=1, padx=8, sticky="w")
        ttk.Checkbutton(tab_v, text=self.t("yt_hw"), variable=v_hw, takefocus=False).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(tab_v, text=self.t("yt_hw_hint"), style="Hint.TLabel", wraplength=340).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(tab_a, text=self.t("yt_q_audio")).grid(row=0, column=0, sticky="w")
        ttk.Combobox(tab_a, textvariable=a_q, values=a_labels, state="readonly", width=20).grid(row=0, column=1, padx=8, sticky="w")

        def chosen():
            if nb.index(nb.select()) == 1:                      # "Audio only" tab: the item is just the sound of the link
                ai = a_labels.index(a_q.get()) if a_q.get() in a_labels else 0
                return {"fmt": yt_audio_format(YT_AUDIO_ABR[ai]), "hw": False, "audio_only": True}
            vi = v_labels.index(v_q.get()) if v_q.get() in v_labels else 0
            return {"fmt": yt_build_format(YT_VIDEO_HEIGHTS[vi], 0), "hw": bool(v_hw.get()), "audio_only": False}

        # playlist section (only shown for a playlist link)
        plbox = ttk.Frame(win, padding=(14, 10, 14, 0))
        mode = tk.StringVar(value="video")
        rb_video = ttk.Radiobutton(plbox, text=self.t("yt_opt_video"), variable=mode, value="video",
                                   takefocus=False, command=lambda: refresh_button())
        rb_video.pack(anchor="w")
        rb_pl = ttk.Radiobutton(plbox, text=self.t("yt_opt_playlist"), variable=mode, value="playlist",
                                takefocus=False, command=lambda: refresh_button())
        rb_pl.pack(anchor="w")
        pl_title = ttk.Label(plbox, text="", wraplength=380, padding=(0, 8, 0, 2))
        pl_title.pack(anchor="w")
        lbf = ttk.Frame(plbox)
        lbf.pack(fill="x")
        pl_lb = tk.Listbox(lbf, height=8, activestyle="none", exportselection=False, bg=theme["ENTRY_BG"],
                           fg=theme["FG"], selectbackground=theme["ACCENT"], highlightthickness=1,
                           highlightbackground=theme["LINE"], bd=0, font=("Segoe UI", 9))
        pl_sb = ttk.Scrollbar(lbf, orient="vertical", command=pl_lb.yview)
        pl_lb.configure(yscrollcommand=pl_sb.set)
        pl_sb.pack(side="right", fill="y")
        pl_lb.pack(side="left", fill="x", expand=True)
        pl_count = ttk.Label(plbox, text="", padding=(0, 4, 0, 0))
        pl_count.pack(anchor="w")

        result = {"value": None}
        state = {"seq": 0, "img": None, "pl": None, "vid": None, "plid": None}

        def alive():
            try:
                return bool(win.winfo_exists())
            except tk.TclError:
                return False

        def refresh_button():
            n = len(state["pl"]["entries"]) if state["pl"] else 0
            if state["plid"] and mode.get() == "playlist":
                btn_import.configure(text=self.t("yt_btn_import_n", n=n) if n else self.t("yt_btn_import"),
                                     state="normal" if n else "disabled")
            else:
                btn_import.configure(text=self.t("yt_btn_import"), state="normal")

        def show(seq, title, png):
            if seq != state["seq"] or not alive():
                return
            title_lbl.configure(text=title or self.t("yt_no_title"))
            if png:
                try:
                    state["img"] = tk.PhotoImage(data=base64.b64encode(png))     # keep a reference
                    thumb.configure(image=state["img"], text="")
                except Exception:
                    pass

        def fetch(seq, url):
            if seq != state["seq"] or not alive():
                return
            title_lbl.configure(text=self.t("yt_loading"))
            ff = self._ffmpeg_path()

            def work():
                title, png = fetch_youtube_preview(url, ff)
                self._uiq.put(lambda: show(seq, title, png))
            threading.Thread(target=work, daemon=True).start()

        def show_pl(seq, data, err):
            if seq != state["seq"] or not alive():
                return
            if err or not data["entries"]:
                state["pl"] = None
                pl_title.configure(text=self.t("yt_pl_err", err=(err or self.t("yt_pl_empty"))[:90]))
                refresh_button()
                return
            state["pl"] = data
            pl_title.configure(text=self.t("yt_pl_title", title=data["title"]))
            pl_lb.delete(0, "end")
            for k, e in enumerate(data["entries"], 1):
                pl_lb.insert("end", f"{k}. {e['title']}")
            pl_count.configure(text=self.t("yt_pl_count", n=len(data["entries"])))
            refresh_button()

        def fetch_pl(seq, url):
            if seq != state["seq"] or not alive():
                return
            pl_title.configure(text=self.t("yt_pl_loading"))

            def work():
                data, err = None, ""
                try:
                    data = fetch_youtube_playlist(url)
                except Exception as exc:
                    err = str(exc)
                self._uiq.put(lambda: show_pl(seq, data, err))
            threading.Thread(target=work, daemon=True).start()

        def changed(*_a):
            state["seq"] += 1
            seq, url = state["seq"], var.get().strip()
            state["img"], state["pl"] = None, None
            vid, plid = youtube_video_id(url), youtube_playlist_id(url)
            state["vid"], state["plid"] = vid, plid
            thumb.configure(image="", text="▶")
            title_lbl.configure(text="")
            pl_title.configure(text="")
            pl_count.configure(text="")
            pl_lb.delete(0, "end")
            nb.pack_forget()
            plbox.pack_forget()
            if vid or plid:
                nb.pack(fill="x", padx=14, pady=(10, 0), before=row)
            if plid:
                plbox.pack(fill="x", before=row)
                rb_video.configure(state="normal" if vid else "disabled")
                mode.set("video" if vid else "playlist")        # asks first when there is a single video too
            else:
                mode.set("video")
            refresh_button()
            if vid:
                win.after(350, lambda: fetch(seq, url))          # debounce: wait for the paste / typing to settle
            if plid:
                win.after(350, lambda: fetch_pl(seq, url))

        def ok(_e=None):
            url = var.get().strip()
            if not url:
                return
            plid, vid = state["plid"], state["vid"]
            if plid and mode.get() == "playlist":
                pl = state["pl"]
                if not pl or not pl["entries"]:
                    return                                       # still loading / failed: nothing to import yet
                result["value"] = {"mode": "playlist", "url": youtube_playlist_url(plid),
                                   "title": pl["title"], "entries": pl["entries"], **chosen()}
            elif plid and vid:
                result["value"] = {"mode": "video", "url": youtube_watch_url(vid), **chosen()}     # only this video, not the list
            else:
                result["value"] = {"mode": "video", "url": url, **chosen()}
            win.destroy()

        def cancel(_e=None):
            win.destroy()

        btn_cancel = ttk.Button(row, text=self.t("yt_btn_cancel"), takefocus=False, command=cancel)
        btn_cancel.pack(side="right")
        btn_import = ttk.Button(row, text=self.t("yt_btn_import"), style="Accent.TButton", takefocus=False, command=ok)
        btn_import.pack(side="right", padx=(0, 6))
        var.trace_add("write", changed)
        win.bind("<Return>", ok)
        win.bind("<Escape>", cancel)
        entry.focus_set()
        if initial:
            var.set(initial)
        win.grab_set()
        self.root.wait_window(win)
        return result["value"]

    def _yt_cookie_summary(self):
        c = YT_COOKIES
        if c["mode"] == "browser":
            return self.t("yt_cookies_st_browser", b=c["browser"])
        if c["mode"] == "file":
            return self.t("yt_cookies_st_file", f=os.path.basename(c["file"]))
        return self.t("yt_cookies_st_none")

    def _open_yt_cookies(self, prefer_file=False, parent=None):
        """Window to choose where YouTube cookies come from. True when the settings were saved."""
        theme = THEMES[self.theme_name]
        w = tk.Toplevel(self.root)
        w.title(self.t("yt_cookies_title"))
        w.geometry("560x400")
        w.configure(bg=theme["BG"])
        w.transient(parent or self.root)
        body = ttk.Frame(w, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=self.t("yt_cookies_help"), style="Hint.TLabel", wraplength=520, justify="left").pack(anchor="w")
        cur = dict(YT_COOKIES)
        if prefer_file and cur["mode"] == "browser" and cur["browser"] != "firefox":
            cur["mode"] = "file"
        mode = tk.StringVar(value=cur["mode"])
        browser = tk.StringVar(value=cur["browser"])
        path = tk.StringVar(value=cur["file"])
        ttk.Radiobutton(body, text=self.t("yt_cookies_none"), variable=mode, value="none", takefocus=False).pack(anchor="w", pady=(10, 2))
        r1 = ttk.Frame(body)
        r1.pack(fill="x", pady=2)
        ttk.Radiobutton(r1, text=self.t("yt_cookies_browser"), variable=mode, value="browser", takefocus=False).pack(side="left")
        ttk.Combobox(r1, textvariable=browser, values=YT_BROWSERS, state="readonly", width=12).pack(side="left", padx=8)
        r2 = ttk.Frame(body)
        r2.pack(fill="x", pady=2)
        ttk.Radiobutton(r2, text=self.t("yt_cookies_file"), variable=mode, value="file", takefocus=False).pack(side="left")
        ttk.Entry(r2, textvariable=path, width=34).pack(side="left", padx=8)

        def browse():
            p = filedialog.askopenfilename(parent=w, title=self.t("yt_cookies_file"), filetypes=[("cookies.txt", "*.txt"), (self.t("ft_all"), "*.*")])
            if p:
                path.set(p)
                mode.set("file")
        ttk.Button(r2, text="…", width=3, command=browse, takefocus=False).pack(side="left")
        ttk.Label(body, text=self.t("yt_cookies_note"), style="Hint.TLabel", wraplength=520, justify="left").pack(anchor="w", pady=(12, 0))
        saved = {"ok": False}

        def login():
            res = self._yt_login_window(w)
            if res:
                path.set(res[0])
                mode.set("file")
                save()
                messagebox.showinfo(self.t("yt_login_title"), self.t("yt_login_ok", n=res[1]), parent=parent or self.root)
        ttk.Button(body, text=self.t("yt_login_btn"), style="Accent.TButton", command=login, takefocus=False).pack(anchor="w", pady=(10, 0))

        def save():
            YT_COOKIES.update(yt_cookie_settings({"mode": mode.get(), "browser": browser.get(), "file": path.get().strip()}))
            self.cfg["yt_cookies"] = dict(YT_COOKIES)
            save_config(self.cfg)
            raw = yt_cookie_opts()[2]
            for p in [pl for o in self.outs for pl in (o.m, o.pv)] + [self.ip]:       # players already running pick it up too
                if p:
                    self._setp(p, "ytdl_raw_options", raw)
            saved["ok"] = True
            w.destroy()
        row = ttk.Frame(body)
        row.pack(fill="x", side="bottom", pady=(14, 0))
        ttk.Button(row, text=self.t("btn_close"), command=w.destroy, takefocus=False).pack(side="right")
        ttk.Button(row, text=self.t("yt_cookies_save"), style="Accent.TButton", command=save, takefocus=False).pack(side="right", padx=(0, 6))
        w.grab_set()
        self.root.wait_window(w)
        if parent is not None:
            try:
                parent.grab_set()                                  # back to the dialog that opened this window
            except tk.TclError:
                pass
        return saved["ok"]

    def _yt_consent_flow(self, parent):
        """'Cookies' button of the import dialog: YouTube opens in a browser window, the user clicks
        'Accept all', the cookies are stored in one file that every later video uses."""
        res = self._yt_login_window(parent, consent=True)
        if res:
            YT_COOKIES.update(yt_cookie_settings({"mode": "file", "browser": YT_COOKIES["browser"], "file": res[0]}))
            self.cfg["yt_cookies"] = dict(YT_COOKIES)
            save_config(self.cfg)
            raw = yt_cookie_opts()[2]
            for p in [pl for o in self.outs for pl in (o.m, o.pv)] + [self.ip]:
                if p:
                    self._setp(p, "ytdl_raw_options", raw)
        return bool(res)

    def _yt_login_window(self, parent, consent=False, service="youtube"):
        """Opens a private browser window, waits for the YouTube (or Spotify) sign-in and stores it (YouTube: cookies.txt;
        Spotify: the sp_dc cookie), together with the account's name and picture. (path, n, account) or None."""
        sp = service == "spotify"
        try:
            br = YtLoginBrowser(url=SP_LOGIN_URL if sp else ("https://www.youtube.com/" if consent else None))
        except FileNotFoundError:
            messagebox.showwarning(self.t("yt_login_title"), self.t("yt_login_nobrowser"), parent=parent)
            return None
        except Exception as exc:
            messagebox.showerror(self.t("yt_login_title"), self.t("yt_login_fail", err=exc), parent=parent)
            return None
        theme = THEMES[self.theme_name]
        w = tk.Toplevel(self.root)
        w.title(self.t("yt_login_title"))
        w.geometry("460x230")
        w.configure(bg=theme["BG"])
        w.transient(parent or self.root)
        body = ttk.Frame(w, padding=14)
        body.pack(fill="both", expand=True)
        has_ok = sp_has_login if sp else (yt_has_consent if consent else yt_has_login)
        ttk.Label(body, text=self.t("sp_login_help" if sp else ("yt_consent_help" if consent else "yt_login_help")), style="Hint.TLabel", wraplength=420, justify="left").pack(anchor="w")
        status = tk.StringVar(value=self.t("yt_consent_wait" if consent else "yt_login_wait"))
        ttk.Label(body, textvariable=status).pack(anchor="w", pady=(12, 0))
        st = {"result": None, "busy": False, "found_at": None, "done": False, "err": None, "manual": False, "account": None}

        def work():
            try:
                ck = br.fetch()
                if st["found_at"] is not None or st["manual"]:
                    if not has_ok(ck):
                        st["err"] = "nologin"
                    elif sp:
                        dc = next(c["value"] for c in ck if c.get("name") == "sp_dc" and c.get("value"))
                        sp_save_session(dc)
                        acc = None
                        try:
                            tok = spotify_token(force=True)
                            if tok:
                                acc = sp_account_info(tok)
                        except Exception as exc:
                            diag_log(f"spotify: account info failed ({exc})")
                        for _try in range(5 if not acc else 0):         # the web player shows the name and picture
                            try:
                                acc = sp_parse_dom(cdp_eval(br.port, SP_ACCOUNT_JS, "open.spotify.com"))
                                if acc:
                                    break
                            except Exception as exc:
                                diag_log(f"spotify: page account try {_try + 1} failed ({exc})")
                            time.sleep(2.5)
                        st["account"] = self._fetch_photo(acc)
                        st["result"] = (SP_SESSION_FILE, 1, st["account"])
                    else:
                        txt, n = cookies_to_netscape(ck)
                        with open(YT_COOKIE_FILE, "w", encoding="utf-8", newline="\n") as f:
                            f.write(txt)
                        try:
                            os.chmod(YT_COOKIE_FILE, 0o600)
                        except OSError:
                            pass
                        acc = None
                        if not consent:
                            for _try in range(5):                       # the tab may still be on the Google sign-in page
                                try:
                                    acc = yt_parse_account(cdp_eval(br.port, YT_ACCOUNT_JS, "youtube.com"))
                                    if acc:
                                        break
                                except Exception as exc:
                                    diag_log(f"youtube: account info try {_try + 1} failed ({exc})")
                                time.sleep(2.5)
                        st["account"] = self._fetch_photo(acc)
                        st["result"] = (YT_COOKIE_FILE, n, st["account"])
                elif has_ok(ck):
                    st["found_at"] = time.time()
            except Exception as exc:
                st["err"] = str(exc)
            st["busy"] = False

        def finish():
            if st["done"]:
                return
            st["done"] = True
            br.close()
            try:
                w.destroy()
            except tk.TclError:
                pass

        def poll():
            if st["done"]:
                return
            if st["result"] or (not br.alive() and not st["busy"]):
                finish()
                return
            if st["err"] == "nologin":
                st["err"] = None
                st["manual"] = False
                st["found_at"] = None
                status.set(self.t("yt_login_nologin"))
            if not st["busy"]:
                if st["found_at"] is not None:
                    status.set(self.t("yt_login_saving"))
                if st["found_at"] is None or time.time() - st["found_at"] >= 4 or st["manual"]:
                    st["busy"] = True
                    threading.Thread(target=work, daemon=True).start()
            w.after(1500, poll)

        def manual():
            st["manual"] = True
        row = ttk.Frame(body)
        row.pack(fill="x", side="bottom")
        ttk.Button(row, text=self.t("yt_btn_cancel"), command=finish, takefocus=False).pack(side="right")
        ttk.Button(row, text=self.t("yt_login_now"), command=manual, takefocus=False).pack(side="right", padx=(0, 6))
        w.protocol("WM_DELETE_WINDOW", finish)
        w.after(2000, poll)
        w.grab_set()
        self.root.wait_window(w)
        br.close()
        if parent is not None:
            try:
                parent.grab_set()
            except tk.TclError:
                pass
        if st["result"] and not consent:
            self._store_account(service, st["account"])
        return st["result"]

    def _fetch_photo(self, acc):
        """Adds the downloaded picture bytes ('photo_png') to an account dict (worker thread)."""
        if acc and acc.get("photo"):
            try:
                acc["photo_png"] = avatar_png(download_bytes(acc["photo"]), 64, getattr(self, "ffmpeg", None))
            except Exception as exc:
                diag_log(f"account picture failed ({exc})")
        return acc

    def _store_account(self, service, acc):
        """Keeps the account's name / handle / picture of a service in the settings (the picture as a small file)."""
        accs = self.cfg.setdefault("accounts", {})
        if not acc:
            acc = {"name": self.t("lg_unknown_name"), "handle": ""}
        rec = {"name": acc.get("name", ""), "handle": acc.get("handle", ""), "product": acc.get("product", ""), "ts": time.time(), "photo": ""}
        png = acc.get("photo_png")
        if png:
            try:
                os.makedirs(ACCOUNTS_DIR, exist_ok=True)
                rec["photo"] = os.path.join(ACCOUNTS_DIR, service + ".png")
                with open(rec["photo"], "wb") as f:
                    f.write(png)
            except OSError:
                rec["photo"] = ""
        accs[service] = rec
        save_config(self.cfg)
        self._refresh_account_bar()
        diag_log(f"accounts: {service} signed in as '{rec['name']}' {rec['handle']} {rec['product']}")

    def _forget_account(self, service):
        accs = self.cfg.setdefault("accounts", {})
        rec = accs.pop(service, None)
        if rec and rec.get("photo"):
            try:
                os.remove(rec["photo"])
            except OSError:
                pass
        if service == "spotify":
            sp_clear_session()
        else:
            if YT_COOKIES.get("mode") == "file" and YT_COOKIES.get("file") == YT_COOKIE_FILE:
                YT_COOKIES.update(yt_cookie_settings({"mode": "none"}))
                self.cfg["yt_cookies"] = dict(YT_COOKIES)
                for p in [pl for o in self.outs for pl in (o.m, o.pv)] + [self.ip]:
                    if p:
                        self._setp(p, "ytdl_raw_options", "")
            try:
                os.remove(YT_COOKIE_FILE)
            except OSError:
                pass
        save_config(self.cfg)
        self._refresh_account_bar()

    def _yt_set_anon(self, on, why=""):
        """Sets the cookies aside (or back): signed in, YouTube may offer an account only DRM-protected streams, which
        nothing can play; anonymous requests work. Players already running follow."""
        if YT_ANON["on"] == on:
            return
        YT_ANON["on"] = on
        self.cfg["yt_anon"] = bool(on)
        save_config(self.cfg)
        diag_log("YouTube: " + ("cookies set aside (DRM-only formats for the signed-in account): anonymous requests" if on
                                else "cookies used again") + (f" [{why[:80]}]" if why else ""))

        def apply():
            raw = yt_cookie_opts()[2]
            for p in [pl for o in self.outs for pl in (o.m, o.pv)] + [self.ip]:
                if p:
                    self._setp(p, "ytdl_raw_options", raw)
        self._uiq.put(apply)

    def _use_yt_cookie_file(self, path):
        YT_ANON["on"] = False                                   # a new sign-in is tried with the cookies again
        self.cfg["yt_anon"] = False
        YT_COOKIES.update(yt_cookie_settings({"mode": "file", "browser": YT_COOKIES["browser"], "file": path}))
        self.cfg["yt_cookies"] = dict(YT_COOKIES)
        save_config(self.cfg)
        raw = yt_cookie_opts()[2]
        for p in [pl for o in self.outs for pl in (o.m, o.pv)] + [self.ip]:
            if p:
                self._setp(p, "ytdl_raw_options", raw)

    def _refresh_account_bar(self):
        """Picture, name and platform of every signed-in account, to the right of the Logins button."""
        bar = getattr(self, "acc_bar", None)
        if bar is None:
            return
        try:
            for c in bar.winfo_children():
                c.destroy()
            self._acc_imgs = []
            for svc, label in (("youtube", "YouTube"), ("spotify", "Spotify")):
                rec = (self.cfg.get("accounts") or {}).get(svc)
                if not rec:
                    continue
                img = None
                if rec.get("photo") and os.path.exists(rec["photo"]):
                    try:
                        img = tk.PhotoImage(file=rec["photo"]).subsample(3)         # 64 px -> 21 px
                        self._acc_imgs.append(img)
                    except tk.TclError:
                        img = None
                lbl = ttk.Label(bar, text=f"{rec.get('name', '')} · {label}", image=img if img else "", compound="left", style="Panel.TLabel")
                lbl.pack(side="left", padx=(0, 14))
                lbl.bind("<Button-1>", lambda _e: self.open_logins())
        except tk.TclError:
            pass

    def account_line(self, service):
        """Short text of the signed-in account of a service, or ''."""
        rec = (self.cfg.get("accounts") or {}).get(service)
        if not rec:
            return ""
        extra = " · Premium" if rec.get("product") == "premium" else ""
        return rec.get("name", "") + extra

    def open_logins(self):
        """'Logins' window: sign in to YouTube and Spotify; the account's name and picture are kept and used by the imports."""
        if getattr(self, "_logins_win", None) is not None:
            try:
                if self._logins_win.winfo_exists():
                    self._logins_win.lift()
                    return
            except tk.TclError:
                pass
        theme = THEMES[self.theme_name]
        w = tk.Toplevel(self.root)
        self._logins_win = w
        w.title(self.t("lg_title"))
        w.configure(bg=theme["BG"])
        w.transient(self.root)
        w.resizable(False, False)
        try:
            w.geometry(f"+{self.root.winfo_rootx() + 120}+{self.root.winfo_rooty() + 60}")
        except Exception:
            pass
        body = ttk.Frame(w, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=self.t("lg_help"), style="Hint.TLabel", wraplength=470, justify="left").pack(anchor="w", pady=(0, 8))
        rows = {}
        imgs = {}

        def render(service):
            r = rows[service]
            rec = (self.cfg.get("accounts") or {}).get(service)
            img = None
            if rec and rec.get("photo") and os.path.exists(rec["photo"]):
                try:
                    img = tk.PhotoImage(file=rec["photo"])
                except tk.TclError:
                    img = None
            imgs[service] = img
            r["pic"].configure(image=img if img else "", text="" if img else "👤")
            if rec:
                r["name"].configure(text=rec.get("name", ""))
                if service == "spotify":
                    kind = self.t("lg_premium") if rec.get("product") == "premium" else (self.t("lg_free") if rec.get("product") else "")
                    detail = " · ".join(x for x in (kind, rec.get("handle", "")) if x)
                else:
                    detail = rec.get("handle", "")
                r["detail"].configure(text=detail or self.t("lg_signed_in"))
                r["btn"].configure(text=self.t("lg_relogin"))
                r["out"].state(["!disabled"])
            else:
                r["name"].configure(text=self.t("lg_none"))
                r["detail"].configure(text="")
                r["btn"].configure(text=self.t("lg_login"))
                r["out"].state(["disabled"])

        def login(service):
            res = self._yt_login_window(w, service=service)
            if res:
                if service == "youtube":
                    self._use_yt_cookie_file(res[0])
                render(service)
                messagebox.showinfo(self.t("lg_title"), self.t("lg_ok_" + service), parent=w)
            try:
                w.grab_set()
            except tk.TclError:
                pass

        def logout(service):
            if messagebox.askyesno(self.t("lg_title"), self.t("lg_out_ask"), parent=w):
                self._forget_account(service)
                render(service)

        for service, label in (("youtube", "YouTube"), ("spotify", "Spotify")):
            fr = ttk.Frame(body, padding=(0, 6))
            fr.pack(fill="x")
            pic = ttk.Label(fr, width=7, anchor="center", font=("Segoe UI", 20))
            pic.pack(side="left", padx=(0, 10))
            mid = ttk.Frame(fr)
            mid.pack(side="left", fill="x", expand=True)
            ttk.Label(mid, text=label, font=("Segoe UI", 10, "bold")).pack(anchor="w")
            nm = ttk.Label(mid, text="", font=("Segoe UI", 10))
            nm.pack(anchor="w")
            dt = ttk.Label(mid, text="", style="Hint.TLabel")
            dt.pack(anchor="w")
            b2 = ttk.Button(fr, text=self.t("lg_logout"), takefocus=False, command=lambda sv=service: logout(sv))
            b2.pack(side="right", padx=(6, 0))
            b1 = ttk.Button(fr, text="", style="Accent.TButton", takefocus=False, command=lambda sv=service: login(sv))
            b1.pack(side="right")
            rows[service] = {"pic": pic, "name": nm, "detail": dt, "btn": b1, "out": b2}
            render(service)
            ttk.Separator(body).pack(fill="x", pady=2)
        ttk.Label(body, text=self.t("lg_note"), style="Hint.TLabel", wraplength=470, justify="left").pack(anchor="w", pady=(6, 0))
        ttk.Button(body, text=self.t("btn_close"), takefocus=False, command=w.destroy).pack(anchor="e", pady=(10, 0))

        def verify():
            """Checks the Spotify sign-in in the background and refreshes the name / picture."""
            if not (self.cfg.get("accounts") or {}).get("spotify"):
                return
            try:
                tok = spotify_token()
                acc = self._fetch_photo(sp_account_info(tok)) if tok else None
            except Exception as exc:
                diag_log(f"spotify: refresh failed ({exc})")
                acc = None

            def apply():
                if not w.winfo_exists():
                    return
                if acc:
                    self._store_account("spotify", acc)
                    render("spotify")
            self._uiq.put(apply)
        threading.Thread(target=verify, daemon=True).start()

    def _yt_extract(self, url):
        """(title, info, error) for ONE video (a playlist link is never expanded here)."""
        title, info, err = self._yt_extract_once(url)
        if err and yt_cookie_opts()[1] and yt_is_drm_error(err):
            self._yt_set_anon(True, err)
            title, info, err = self._yt_extract_once(url)
        elif err and YT_ANON["on"] and yt_is_bot_error(err) and YT_COOKIES["mode"] != "none":
            self._yt_set_anon(False, err)
            title, info, err = self._yt_extract_once(url)
        return title, info, err

    def _yt_extract_once(self, url):
        title = info = err = None
        try:
            try:
                import yt_dlp
                opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": False,
                        "noplaylist": True}
                opts.update(yt_cookie_opts()[0])
                opts.update(yt_extra_opts()[0])
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    title = info.get("title")
            except ImportError:
                res = subprocess.run(["yt-dlp", "--get-title", "--no-playlist", "--no-warnings"] + yt_cookie_opts()[1] + [url],
                                     capture_output=True, text=True, timeout=40, encoding="utf-8", errors="replace")
                if res.returncode == 0 and res.stdout.strip():
                    title = res.stdout.strip().split("\n")[0]
                elif res.returncode != 0:
                    err = ((res.stderr or "").strip().splitlines() or ["yt-dlp failed"])[-1]
        except Exception as exc:
            err = str(exc)
        return title, info, err

    @staticmethod
    def _yt_formats(info):
        formats, seen = [], set()
        for f in (info or {}).get("formats") or []:
            h = f.get("height")
            if h and h not in seen and f.get("vcodec") != "none":
                seen.add(h)
                formats.append({"height": h, "format_id": f.get("format_id"), "ext": f.get("ext")})
        formats.sort(key=lambda x: x["height"], reverse=True)
        return formats

    @staticmethod
    def _yt_apply_opts(it, res):
        if res and res.get("fmt"):
            it["ytdl_format"] = res["fmt"]
        it["yt_hw"] = bool(res and res.get("hw"))
        it["yt_audio"] = bool(res and res.get("audio_only"))

    def _out_hw(self, player, item):
        """Hardware decoder on / off for the item about to load (YouTube items follow their own checkbox)."""
        if not player:
            return
        want = OUT_HWDEC if (not item.get("is_youtube") or item.get("yt_hw")) else "no"
        if getattr(player, "_hw_cur", OUT_HWDEC) != want:
            with MPV_GRAB_LOCK:                              # re-initialises the decoder: not while a picture is being taken
                self._setp(player, "hwdec", want)
            try:
                player._hw_cur = want
            except Exception:
                pass

    def _yt_new_item(self, url, name, info=None, dur=0.0):
        it = self._new_item(url)
        it["name"] = name
        it["is_youtube"] = True
        it["ytdl_info"] = info or {}
        it["yt_live"] = yt_info_is_live(info)
        it["ytdl_formats"] = self._yt_formats(info)
        it["ytdl_format"] = "best"
        it["dur"] = float((info or {}).get("duration") or dur or 0)
        self.items.append(it)
        self.tree.insert("", "end", iid=it["id"], values=self._row(it))
        return it

    def add_youtube(self, initial=""):
        res = self._ask_youtube_url(initial)
        if not res:
            return
        if res["mode"] == "playlist":
            self._import_playlist(res)
            return
        url = res["url"].strip()
        theme = THEMES[self.theme_name]
        self.status_t("st_youtube_importing", theme["ACCENT"])

        def do_extract():
            title, info, err = self._yt_extract(url)

            def finish():
                if err:
                    self.status_t("st_youtube_err", theme["RED"], err=err[:90])
                    if yt_is_dpapi_error(err):
                        messagebox.showinfo(self.t("yt_cookies_title"), self.t("yt_dpapi_msg"), parent=self.root)
                        if self._open_yt_cookies(prefer_file=True):
                            self.add_youtube(initial=url)
                    elif yt_is_formats_error(err):
                        if messagebox.askyesno(self.t("yt_cookies_title"), self.t("yt_fmt_ask", rt="" if yt_js_runtimes() else self.t("yt_fmt_rt")), parent=self.root):
                            self.status_t("st_youtube_importing", theme["ACCENT"])

                            def upd():
                                ok, last = yt_update_ytdlp()
                                self._uiq.put(lambda: (messagebox.showinfo(self.t("yt_cookies_title"), self.t("yt_upd_ok"), parent=self.root) if ok
                                                       else messagebox.showerror(self.t("yt_cookies_title"), self.t("yt_upd_fail", err=last), parent=self.root)))
                            threading.Thread(target=upd, daemon=True).start()
                    elif yt_is_bot_error(err) and messagebox.askyesno(self.t("yt_cookies_title"), self.t("yt_bot_ask"), parent=self.root):
                        if self._open_yt_cookies():
                            self.add_youtube(initial=url)              # try again with the cookies just chosen
                    return
                final_title = (clean_text(title) if title else "") or url
                it = self._yt_new_item(url, final_title, info)
                self._yt_apply_opts(it, res)
                self._highlight()
                self.tree.see(it["id"])
                self.status_t("st_youtube_ready", title=final_title[:50])

            self._uiq.put(finish)

        threading.Thread(target=do_extract, daemon=True).start()

    def _yt_search(self, query, n=5):
        """YouTube search -> [{'id', 'title', 'duration', 'channel'}] (flat: no stream addresses, fast)."""
        try:
            import yt_dlp
            opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": "in_playlist"}
            opts.update(yt_cookie_opts()[0])
            opts.update(yt_extra_opts()[0])
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
            return [e for e in (info or {}).get("entries") or [] if e]
        except ImportError:
            exe, cookie_args, extra_args = self._yt_args()
            cmd = ([exe] if exe else ["yt-dlp"]) + ["--flat-playlist", "--no-warnings", "--print",
                                                     "%(id)s\t%(duration)s\t%(channel)s\t%(title)s"] + cookie_args + extra_args + [f"ytsearch{n}:{query}"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            out = []
            for ln in r.stdout.splitlines():
                parts = ln.split("\t")
                if len(parts) >= 4 and parts[0]:
                    try:
                        d = float(parts[1])
                    except ValueError:
                        d = 0.0
                    out.append({"id": parts[0], "duration": d, "channel": parts[2], "title": parts[3]})
            return out

    def add_spotify(self, initial=""):
        """Import > Spotify: a track, album or playlist link. Spotify's audio is DRM protected, so only the song names
        come from Spotify; each song is found on YouTube (same title / artist / length) and imported as an audio item."""
        theme = THEMES[self.theme_name]
        win = tk.Toplevel(self.root)
        win.title(self.t("dlg_spotify"))
        win.configure(bg=theme["BG"])
        win.transient(self.root)
        win.resizable(False, False)
        try:
            win.geometry(f"+{self.root.winfo_rootx() + 140}+{self.root.winfo_rooty() + 60}")
        except Exception:
            pass
        ttk.Label(win, text=self.t("ask_spotify_url"), padding=(14, 12, 14, 4)).pack(anchor="w")
        var = tk.StringVar(value=initial)
        entry = ttk.Entry(win, textvariable=var, width=64)
        entry.pack(fill="x", padx=14)
        ctx = tk.Menu(win, tearoff=0, bg=theme["PANEL"], fg=theme["FG"], activebackground=theme["ACCENT"], activeforeground="#ffffff")
        for key, ev in (("yt_ctx_cut", "<<Cut>>"), ("yt_ctx_copy", "<<Copy>>"), ("yt_ctx_paste", "<<Paste>>")):
            ctx.add_command(label=self.t(key), command=lambda e=ev: entry.event_generate(e))
        ctx.add_command(label=self.t("yt_ctx_all"), command=lambda: (entry.focus_set(), entry.selection_range(0, "end")))
        entry.bind("<Button-3>", lambda e: (entry.focus_set(), ctx.tk_popup(e.x_root, e.y_root)))
        acc_row = ttk.Frame(win, padding=(14, 6, 14, 0))
        acc_row.pack(fill="x")
        _acc = self.account_line("spotify")
        ttk.Label(acc_row, text=self.t("lg_using", name=_acc) if _acc else self.t("lg_none_hint"), style="Hint.TLabel").pack(side="left")

        def pick_playlist():
            tok = spotify_token()
            if not tok:
                messagebox.showwarning(self.t("dlg_spotify"), self.t("lg_expired"), parent=win)
                return
            info.configure(text=self.t("sp_reading"))

            def w2():
                try:
                    items, err = sp_my_playlists(tok), None
                except Exception as exc:
                    items, err = None, exc
                    diag_log(f"spotify: playlists failed ({exc})")
                self._uiq.put(lambda: choose(items, err) if win.winfo_exists() else None)
            threading.Thread(target=w2, daemon=True).start()

        def choose(items, err):
            if err or not items:
                info.configure(text=self.t("sp_err", err=str(err or "-")[:160]))
                return
            info.configure(text=self.t("sp_hint"))
            dlg = tk.Toplevel(win)
            dlg.title(self.t("lg_my_playlists"))
            dlg.configure(bg=theme["BG"])
            dlg.transient(win)
            lb2 = tk.Listbox(dlg, height=14, width=52, exportselection=False, activestyle="none", bg=theme["PANEL"], fg=theme["FG"],
                             selectbackground=theme["ACCENT"], highlightthickness=0, bd=0)
            for _id, nm, n in items:
                lb2.insert("end", f"{nm}  ({n})")
            lb2.pack(fill="both", expand=True, padx=10, pady=10)

            def ok(_e=None):
                c = lb2.curselection()
                if c:
                    var.set("https://open.spotify.com/playlist/" + items[c[0]][0])
                    dlg.destroy()
            lb2.bind("<Double-Button-1>", ok)
            lb2.bind("<Return>", ok)
            lb2.focus_set()
            dlg.grab_set()
        if _acc:
            ttk.Button(acc_row, text=self.t("lg_my_playlists"), takefocus=False, command=pick_playlist).pack(side="right")
            ttk.Button(acc_row, text=self.t("lg_liked"), takefocus=False, command=lambda: var.set("spotify:collection:tracks")).pack(side="right", padx=(0, 6))
        info = ttk.Label(win, text=self.t("sp_hint"), style="Hint.TLabel", padding=(14, 6, 14, 2), wraplength=460, justify="left")
        info.pack(anchor="w")
        box = ttk.Frame(win, padding=(14, 2, 14, 4))
        box.pack(fill="both", expand=True)
        lb = tk.Listbox(box, height=10, width=70, exportselection=False, activestyle="none", selectmode="extended",
                        bg=theme["PANEL"], fg=theme["FG"], selectbackground=theme["ACCENT"], highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(box, orient="vertical", command=lb.yview, style="Vertical.TScrollbar")
        lb.configure(yscrollcommand=sb.set)
        lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        video_var = tk.BooleanVar(value=False)
        self._tx(ttk.Checkbutton(win, variable=video_var, takefocus=False), "sp_video").pack(anchor="w", padx=14, pady=(2, 0))
        row = ttk.Frame(win, padding=(14, 8, 14, 12))
        row.pack(fill="x")
        st = {"res": None, "tok": 0}

        def show(res, err=None):
            lb.delete(0, "end")
            st["res"] = res
            if err:
                info.configure(text=self.t("sp_err", err=str(err)[:160]))
                imp_btn.state(["disabled"])
                return
            for k, t in enumerate(res["tracks"], 1):
                lb.insert("end", f"{k:>3}. {t['artist']} – {t['title']}" + (f"   ({fmt_ms(t['dur'])[:-4]})" if t.get("dur") else ""))
            lb.selection_set(0, "end")
            info.configure(text=self.t("sp_found", title=res["title"][:70], n=len(res["tracks"])) +
                           (self.t("lg_via") if res.get("via") == "account" else ""))
            imp_btn.state(["!disabled"])

        def look(*_a):
            link = var.get().strip()
            if not spotify_parse_link(link):
                lb.delete(0, "end")
                info.configure(text=self.t("sp_hint") if not link else self.t("sp_not_link"))
                imp_btn.state(["disabled"])
                return
            st["tok"] += 1
            tok = st["tok"]
            info.configure(text=self.t("sp_reading"))

            def work():
                try:
                    res, err = fetch_spotify(link), None
                except Exception as exc:
                    res, err = None, exc
                    diag_log(f"spotify: could not read {link[:80]} ({exc})")
                self._uiq.put(lambda: show(res, err) if st["tok"] == tok and win.winfo_exists() else None)
            threading.Thread(target=work, daemon=True).start()

        def do_import(*_a):
            res = st["res"]
            if not res:
                return
            sel = lb.curselection() or range(len(res["tracks"]))
            tracks = [res["tracks"][k] for k in sel if k < len(res["tracks"])]
            if not tracks:
                return
            link = var.get().strip()
            win.destroy()
            self._spotify_import(tracks, res["title"], link, audio_only=not video_var.get())

        imp_btn = ttk.Button(row, text=self.t("sp_import"), style="Accent.TButton", takefocus=False, command=do_import)
        imp_btn.pack(side="right")
        imp_btn.state(["disabled"])
        ttk.Button(row, text=self.t("yt_btn_cancel"), takefocus=False, command=win.destroy).pack(side="right", padx=(0, 6))
        var.trace_add("write", lambda *_a: win.after(250, look))
        entry.bind("<Return>", do_import)
        lb.bind("<Double-Button-1>", do_import)
        entry.focus_set()
        if initial:
            look()

    def _spotify_import(self, tracks, title, link, audio_only=True):
        """Finds every track on YouTube (one search at a time, in a worker) and adds it to the list in the original order."""
        theme = THEMES[self.theme_name]
        n = len(tracks)
        self.status_t("st_sp_searching", theme["ACCENT"], k=0, n=n)
        res_opts = {"fmt": yt_audio_format(0), "hw": False, "audio_only": True} if audio_only else {"fmt": None, "hw": False, "audio_only": False}

        def add_one(track, entry, k):
            if entry is not None:
                url = f"https://www.youtube.com/watch?v={entry['id']}"
                name = f"{track['artist']} – {track['title']}" if track.get("artist") else track["title"]
                it = self._yt_new_item(url, name, None, float(entry.get("duration") or track.get("dur") or 0))
                self._yt_apply_opts(it, res_opts)
                it["spotify"] = {"link": link, "title": track["title"], "artist": track.get("artist", "")}
                self._refresh_row(it)
                added.append(it["id"])
            self.status_t("st_sp_searching", theme["ACCENT"], k=k, n=n)

        added, missed = [], []

        def work():
            for k, track in enumerate(tracks, 1):
                if self._closing:
                    return
                q = f"{track.get('artist', '')} - {track['title']}".strip(" -")
                try:
                    best = spotify_pick(self._yt_search(q), track)
                except Exception as exc:
                    diag_log(f"spotify: search failed for '{q}' ({exc})")
                    best = None
                if best is None:
                    missed.append(q)
                else:
                    diag_log(f"spotify: '{q}' ({track.get('dur', 0):.0f} s) -> YouTube {best['id']} '{(best.get('title') or '')[:60]}' ({best.get('duration')} s)")
                self._uiq.put(lambda t=track, b=best, kk=k: add_one(t, b, kk))

            def done():
                self._highlight()
                if added:
                    self.tree.see(added[0])
                    for part in (added[0::3], added[1::3], added[2::3]):                # 3 yt-dlp at a time, each in list order
                        if part:
                            threading.Thread(target=self._yt_fill_worker, args=(list(part),), daemon=True).start()
                    threading.Thread(target=self._yt_cover_prefetch, args=(list(added),), daemon=True).start()
                if missed:
                    self.status_t("st_sp_done_missing", theme["RED"], n=len(added), m=len(missed))
                else:
                    self.status_t("st_sp_done", theme["ACCENT"], n=len(added), title=title[:40])
            self._uiq.put(done)

        threading.Thread(target=work, daemon=True).start()

    def _yt_cover_prefetch(self, ids):
        """Thumbnails of the imported songs, one at a time in the background (they are the covers of the audio items)."""
        for iid in ids:
            if self._closing:
                return
            k = self._find(iid)
            if k is not None:
                self._uiq.put(lambda it=self.items[k]: self._yt_thumb(it))
            time.sleep(0.4)

    def _import_playlist(self, res):
        """Every video of the playlist becomes its own item (titles are known at once; formats / duration
        are filled in the background, one video at a time)."""
        theme = THEMES[self.theme_name]
        new = [self._yt_new_item(e["url"], e["title"], None, e.get("duration", 0.0)) for e in res["entries"]]
        for it in new:
            self._yt_apply_opts(it, res)
        self._highlight()
        if new:
            self.tree.see(new[0]["id"])
        self.status_t("st_youtube_playlist", theme["ACCENT"], title=res["title"][:40], n=len(new))
        threading.Thread(target=self._yt_fill_worker, args=([it["id"] for it in new],), daemon=True).start()

    def _yt_fill_worker(self, ids):
        for iid in ids:
            if self._closing:
                return
            k = self._find(iid)
            if k is None:                                        # removed meanwhile
                continue
            _title, info, err = self._yt_extract(self.items[k]["path"])
            if info and not err:
                self._uiq.put(lambda i=iid, inf=info: self._yt_apply_info(i, inf))

    def _yt_cache_audio(self, it, info):
        """Audio-only items: the stream addresses are already in the info that was fetched at import, so Play opens them at
        once instead of running yt-dlp again (a few seconds)."""
        try:
            if not it.get("yt_audio") or not info or float(info.get("duration") or 0) <= 0:
                return
            for role, worst in (("out", False), ("pv", True)):
                f = yt_pick_audio(info, worst)
                if f:
                    self._yt_play_cache[(it["path"], self._yt_fmt_for(it, role))] = {
                        "t": time.time(), "urls": [f["url"]], "meta": f"{f.get('acodec')}|None|None|{f.get('format_id')}"}
        except Exception as exc:
            diag_log(f"YouTube: address cache from info failed ({exc})")

    def _yt_apply_info(self, iid, info):
        k = self._find(iid)
        if k is None:
            return
        it = self.items[k]
        was_live = is_ytlive(it)
        self._yt_cache_audio(it, info)
        it["ytdl_info"] = info
        it["ytdl_formats"] = self._yt_formats(info)
        it["yt_live"], it["_ylk"] = yt_info_is_live(info), True
        if info.get("duration"):
            it["dur"] = float(info["duration"])
        if was_live != is_ytlive(it) and iid in self._rtmp:
            self._rtmp_stop(iid)                               # it turned out not to be a live (or the other way round)
        self._refresh_row(it)

    def _setup_dnd(self):
        """Drop files / folders / links from outside onto the list (needs: pip install tkinterdnd2)."""
        if TkinterDnD is None:
            self.status_t("st_dnd_missing")
            return
        try:
            TkinterDnD._require(self.root)
            self.tree.drop_target_register(DND_FILES, DND_TEXT)
            self.tree.dnd_bind("<<DropEnter>>", self._dnd_over)
            self.tree.dnd_bind("<<DropPosition>>", self._dnd_over)
            self.tree.dnd_bind("<<DropLeave>>", self._dnd_leave)
            self.tree.dnd_bind("<<Drop>>", self._dnd_drop)
        except Exception:
            traceback.print_exc()

    def _dnd_y(self, e):
        return e.y_root - self.tree.winfo_rooty()

    def _dnd_over(self, e):
        _slot, ypos = self._drop_slot(self._dnd_y(e))
        self.drop_line.place(x=0, y=max(0, ypos - 1), relwidth=1, height=3)
        self.drop_line.lift()
        return "copy"

    def _dnd_leave(self, _e=None):
        self.drop_line.place_forget()
        return "copy"

    def _dnd_drop(self, e):
        self.drop_line.place_forget()
        slot, _ = self._drop_slot(self._dnd_y(e))
        self.root.after(10, lambda d=e.data, s=slot: self._handle_drop(d, s))     # return to the OS first
        return "copy"

    def _collect_dropped(self, paths):
        """(media files in drop order, number of ignored entries). A folder contributes its media files."""
        files, skipped = [], 0
        for p in paths:
            if os.path.isdir(p):
                names = sorted(os.listdir(p), key=str.lower)
                found = [os.path.join(p, n) for n in names if os.path.splitext(n)[1].lower() in MEDIA_EXT]
                files += found
                skipped += 0 if found else 1
            elif os.path.isfile(p) and os.path.splitext(p)[1].lower() in MEDIA_EXT:
                files.append(p)
            else:
                skipped += 1
        return files, skipped

    def _handle_drop(self, data, slot):
        theme = THEMES[self.theme_name]
        try:
            parts = list(self.root.tk.splitlist(data))
        except Exception:
            parts = [data]
        paths = [p for p in parts if os.path.exists(p)]
        if paths:
            files, skipped = self._collect_dropped(paths)
            if not files:
                self.status_t("st_drop_none", theme["RED"])
                return
            self._add_at(files, slot)
            self.status_t("st_dropped", theme["ACCENT"], n=len(files), skipped=skipped)
            return
        m = re.search(r"https?://\S+", data or "")
        if m and spotify_parse_link(m.group(0)):
            self.add_spotify(initial=m.group(0))
            return
        if m:                                                    # a link: the YouTube window (playlist aware) takes it
            self.add_youtube(initial=m.group(0))
            return
        self.status_t("st_drop_none", theme["RED"])

    def _add_at(self, paths, slot):
        """Add files and put them at `slot` (a position in the list) instead of the end."""
        before = len(self.items)
        self._add(paths)
        new_ids = {it["id"] for it in self.items[before:]}
        if new_ids and slot < before:
            self._reorder(new_ids, slot)

    def _add(self, paths):
        for p in paths:
            it = self._new_item(p)
            self.items.append(it)
            self.tree.insert("", "end", iid=it["id"], values=self._row(it))
        self._highlight()

    def remove_selected(self):
        if not self.sel:
            return
        ids = set(self.sel)
        chosen = [it for it in self.items if it["id"] in ids]
        names = [it.get("name") or os.path.basename(it["path"]) for it in chosen]
        if len(names) == 1:
            msg = self.t("ask_remove_one", name=names[0])
        else:
            shown = "\n".join("• " + (n if len(n) <= 60 else n[:59] + "…") for n in names[:8])
            if len(names) > 8:
                shown += "\n…"
            msg = self.t("ask_remove_many", n=len(names), names=shown)
        if not messagebox.askyesno(self.t("dlg_remove_items"), msg, icon="warning", parent=self.root):
            return
        snap = self._snap()
        for iid in ids:
            self.tree.delete(iid)
        self.items = [it for it in self.items if it["id"] not in ids]
        for it in self.items:
            if it.get("group") in ids:
                it["group"] = None
        self._restore(snap)
        relinked = 0
        for it in self.items:
            if it["next"] in ids:
                it["next"] = "next"
                relinked += 1
            if it.get("sim") in ids:
                it["sim"] = "none"
        self.sel, self.anchor = [], None
        if self.ip_item is not None and not any(it is self.ip_item for it in self.items):
            self.ip_clear()
        self._refresh_all()
        self._tree_resync()
        self._highlight()
        if relinked:
            self.status_t("st_relinked", n=relinked)

    def clear_list_ask(self):
        """Toolbar "Clear": asks first (load_playlist calls clear_list directly, without asking)."""
        if not self.items:
            return
        if not messagebox.askyesno(self.t("dlg_clear_list"), self.t("ask_clear_all", n=len(self.items)),
                                   icon="warning", parent=self.root):
            return
        self.clear_list()

    def clear_list(self):
        for o in self.outs:
            self.stop(o)
        self.items.clear()
        self._rtmp_reconcile()
        self.tree.delete(*self.tree.get_children())
        self._schedule_fade_sync()
        self.sel, self.anchor = [], None
        self.playlist_path = None
        self.played.clear()
        self.history.clear()
        self._update_title()
        self.ip_clear()

    def duplicate_selected(self):
        if not self.sel:
            return
        chosen_ids = set(self.sel)
        chosen = [it for it in self.items if it["id"] in chosen_ids and not it.get("is_group")]
        snap = self._snap()
        copies = []
        for it in chosen:
            dup = dict(it)
            dup["ss"] = json.loads(json.dumps(it["ss"])) if it.get("ss") else None
            if it.get("cap"):
                dup["cap"] = dict(it["cap"])
            if it.get("cd"):
                dup["cd"] = dict(it["cd"], running=False, acc=0.0)
            self._next_id += 1
            dup["id"] = f"i{self._next_id}"
            if dup.get("is_rtmp"):
                self._rtmp_clone(dup)
            k = self._find(it["id"])
            self.items.insert(k + 1, dup)
            self.tree.insert("", k + 1, iid=dup["id"], values=self._row(dup))
            copies.append(dup["id"])
        self._restore(snap)
        if not copies:
            return
        self._tree_resync()
        self.sel, self.anchor = copies, copies[-1]
        self._refresh_all()
        self._selection_changed()
        self.tree.see(self.anchor)
        self.status_t("st_duplicated", n=len(copies))

    def set_aspect(self, mode):
        chosen_ids = set(self.sel)
        chosen = [it for it in self.items if it["id"] in chosen_ids]
        for it in chosen:
            it["aspect"] = mode
        for o in self.outs:
            cur = self._cur_item(o)
            if cur is not None and any(it is cur for it in chosen):
                self._apply_output_format(o, cur)
        if self.ip_item is not None and any(it is self.ip_item for it in chosen):
            self._apply_aspect(self.ip, mode)
            self._update_aspect_label()
        self.status_t("st_aspect_set", mode=self.t("asp_" + mode), n=len(chosen))

    def _rename_item(self, it):
        if not it:
            return
        if it.get("is_rtmp"):
            self._rtmp_rename(it)
            return
        current = it.get("name") or os.path.basename(it["path"])
        new = simpledialog.askstring(self.t("ctx_rename"), self.t("ask_new_name"), initialvalue=current, parent=self.root)
        if new is not None and new.strip():
            it["name"] = new.strip()
            self._refresh_row(it)
            if self.ip_item is it:
                self._refresh_info()

    def _set_youtube_quality(self, it, ytdl_format):
        it["ytdl_format"] = ytdl_format
        if is_ytlive(it) and it["id"] in self._rtmp:
            self._rtmp_stop(it["id"])                 # the live's copy is restarted at the new quality
        self._refresh_row(it)
        self.status_t("st_ready_short")

        # Usar o after para libertar a UI e não bloquear o Tkinter
        def restart():
            for o in self.outs:
                if self._cur_item(o) is it:
                    idx = self.items.index(it)
                    self.play_index(idx)
                    break
            if self.ip_item is it:
                self.ip_load(it)

        self.root.after(100, restart)

    def set_output(self, num, ids=None):
        chosen_ids = set(self.sel if ids is None else ids)
        chosen = [it for it in self.items if it["id"] in chosen_ids]
        for it in chosen:
            it["output"] = num
            self._refresh_row(it)
        if chosen:
            self.status_t("st_output_set", n=len(chosen), o=num)

    def _highlight(self):
        chosen = set(self.sel)
        playing = {it["id"] for it in self._snap() if it is not None}
        for it in self.items:
            if it.get("is_group"):
                tags = ("selected", "group") if it["id"] in chosen else ("group",)
            elif it["id"] in playing:
                tags = ("playing",)
            elif it["id"] in chosen:
                tags = ("selected",)
            else:
                tags = ()
            self.tree.item(it["id"], tags=tags, image=self._src_icon(it))
        self._schedule_fade_sync()

    def _selection_changed(self):
        self._highlight()
        if self.anchor and self.anchor in self.sel:
            it = self.items[self._index_of(self.anchor)]
            if it is not self.ip_item and not it.get("is_group"):
                self.ip_load(it)

    def _select_click(self, iid, e):
        ctrl, shift = bool(e.state & 0x4), bool(e.state & 0x1)
        if shift and self.anchor:
            a, b = sorted((self._index_of(self.anchor), self._index_of(iid)))
            self.sel = [it["id"] for it in self.items[a:b + 1]]
        elif ctrl:
            if iid in self.sel:
                self.sel.remove(iid)
            else:
                self.sel.append(iid)
            self.anchor = iid
        elif iid not in self.sel:
            self.sel, self.anchor = [iid], iid
        else:
            self.anchor = iid
        self._selection_changed()

    def select_all(self):
        if not self.items:
            return
        self.sel = [it["id"] for it in self.items]
        if self.anchor not in self.sel:
            self.anchor = self.sel[0]
        self._selection_changed()
        self.status_t("st_selected", n=len(self.sel))

    def _move_selection(self, d):
        if not self.items:
            return
        base = self._index_of(self.anchor) if self.anchor else (-1 if d > 0 else len(self.items))
        n = max(0, min(len(self.items) - 1, base + d))
        hidden = self._hidden_ids()
        while self.items[n]["id"] in hidden and 0 < n < len(self.items) - 1:
            n += d
        iid = self.items[n]["id"]
        self.sel, self.anchor, self._sel_end = [iid], iid, None
        self._selection_changed()
        self.tree.see(iid)

    def _extend_selection(self, d):
        """Shift + Up / Down: the selection grows (or shrinks) from the anchor, like Shift + click."""
        if not self.items:
            return
        if not self.anchor or self._find(self.anchor) is None:
            self._move_selection(d)
            return
        a = self._index_of(self.anchor)
        end = self.__dict__.get("_sel_end")
        e = self._index_of(end) if end and self._find(end) is not None and end in self.sel else a
        hidden = self._hidden_ids()
        n = max(0, min(len(self.items) - 1, e + d))
        while self.items[n]["id"] in hidden and 0 < n < len(self.items) - 1:
            n += d
        self._sel_end = self.items[n]["id"]
        lo, hi = sorted((a, n))
        self.sel = [it["id"] for it in self.items[lo:hi + 1] if it["id"] not in hidden]
        self._selection_changed()
        self.tree.see(self._sel_end)

    def _play_selected(self):
        if self.anchor:
            self.play_index(self._index_of(self.anchor))
        elif self.items:
            self.play_index(0)

    def _col_name(self, x):
        col = self.tree.identify_column(x)
        return COLUMNS[int(col[1:]) - 1][0] if col and col != "#0" else ""

    def _on_tree_click(self, e):
        self._commit_edit()
        self.root.focus_set()
        region = self.tree.identify_region(e.x, e.y)
        if region in ("heading", "separator"):
            return None
        iid = self.tree.identify_row(e.y)
        if region != "cell" or not iid:
            self._press = None
            self.sel, self.anchor = [], None
            self._highlight()
            if self.ip_item is not None:                       # nothing selected: the preview and the information beside it are emptied
                self.ip_clear()
                self._refresh_info()
            return "break"
        self._select_click(iid, e)
        self._press = {"iid": iid, "name": self._col_name(e.x), "y": e.y,
                       "drag": False, "mod": bool(e.state & 0x5)}
        return "break"

    def _on_tree_double(self, e):
        iid = self.tree.identify_row(e.y)
        col = self._col_name(e.x)
        if iid and col == "nome":
            self._rename_item(self.items[self._index_of(iid)])
            return "break"
        if iid and col in ("pos",):
            self.play_index(self._index_of(iid))
            return "break"
        return self._on_tree_click(e)

    def _on_tree_motion(self, e):
        p = self._press
        if not p:
            return
        if not p["drag"]:
            if abs(e.y - p["y"]) < 6:
                return
            p["drag"] = True
            self.tree.configure(cursor="hand2")
        if e.y < 28:
            self.tree.yview_scroll(-1, "units")
        elif e.y > self.tree.winfo_height() - 28:
            self.tree.yview_scroll(1, "units")
        _, ypos = self._drop_slot(e.y)
        self.drop_line.place(x=0, y=max(0, ypos - 1), relwidth=1, height=3)
        self.drop_line.lift()

    def _on_tree_release(self, e):
        p, self._press = self._press, None
        self.drop_line.place_forget()
        self.tree.configure(cursor="")
        if not p:
            return None
        if p["drag"]:
            slot, _ = self._drop_slot(e.y)
            ids = set(self.sel) if p["iid"] in self.sel else {p["iid"]}
            self._reorder(ids, slot)
            return "break"
        if p["mod"] or self._find(p["iid"]) is None:
            return "break"
        it = self.items[self._find(p["iid"])]
        name = p["name"]
        if it.get("is_group"):
            if name == "nome":
                self._toggle_group(it)                       # a click on the header's name folds / unfolds it
            return "break"
        box = self.tree.bbox(it["id"], name)
        
        if name in FADE_COLS:
            pass        # handled by the checkbox / ms widgets placed over the cell
        elif name == "dow":
            self._edit_dow(it)
        elif name == "sched":
            if box and e.x < box[0] + 28:
                it["sched_on"] = not it.get("sched_on", False)
                self._refresh_row(it)
            else:
                self._edit_cell(it, name)
        elif name == "dur":
            if is_image(it["path"]):
                self._edit_cell(it, "dur")
        elif name == "mute":
            it["mute"] = not it.get("mute", False)
            self._refresh_row(it)
            for o in self.outs:
                if self._cur_item(o) is it and o.m:
                    try:
                        o.m.mute = it["mute"]
                    except Exception:
                        pass
        elif name == "next":
            self._menu_next(it, e)
        elif name == "out":
            self._menu_output(it, e)
        elif name == "sim":
            self._menu_sim(it, e)
        elif name in ("rep", "f1", "fl", "wn"):
            self._toggle(it, name)
        return "break"

    def _targets(self, scope):
        if scope == "sel":
            ids = set(self.sel)
            return [it for it in self.items if it["id"] in ids]
        return list(self.items)

    def _bulk_flag(self, col, scope):
        """Fade In/Out, Freeze first/last: switch on for the targets (or off if they are all on already)."""
        targets = self._targets(scope)
        if not targets:
            return
        key = col + "_on" if col in FADE_COLS else col
        value = not all(bool(it.get(key)) for it in targets)
        for it in targets:
            it[key] = value
            if col in FADE_COLS:
                if value and not it.get(col):
                    it[col] = 1000          # a fade needs a duration to do anything
            elif value and col == "fl":
                it["next"], it["repeat"], it["ms"] = "none", False, 0
            self._refresh_row(it)
            if col == "fl":
                self._apply_loop(it)
        self._highlight()
        self.status_t("st_hdr_applied", n=len(targets))

    def _bulk_next(self, key, scope):
        targets = self._targets(scope)
        for it in targets:
            self._set_next(it, key)
        if targets:
            self.status_t("st_hdr_applied", n=len(targets))

    def _on_header_context(self, e):
        col = self._col_name(e.x)
        if col not in BULK_FLAG_COLS and col != "next":
            return "break"
        menu = self.next_menu
        menu.delete(0, "end")
        has_sel = bool(self.sel)
        if col == "next":
            for k, key in enumerate(("none", "prev", "next", "first", "last")):
                if k:
                    menu.add_separator()
                menu.add_command(label=self.t(f"nx_hd_{key}_all"), command=lambda kk=key: self._bulk_next(kk, "all"))
                menu.add_command(label=self.t(f"nx_hd_{key}_sel"), command=lambda kk=key: self._bulk_next(kk, "sel"),
                                 state="normal" if has_sel else "disabled")
        else:
            menu.add_command(label=self.t("ctx_scope_all"), command=lambda: self._bulk_flag(col, "all"))
            menu.add_command(label=self.t("ctx_scope_sel"), command=lambda: self._bulk_flag(col, "sel"),
                             state="normal" if has_sel else "disabled")
        self._popup(menu, e)
        return "break"

    def _on_tree_context(self, e):
        self._commit_edit()
        self.root.focus_set()
        if self.tree.identify_region(e.x, e.y) == "heading":
            return self._on_header_context(e)
        iid = self.tree.identify_row(e.y)
        if not iid:
            return "break"
        if iid not in self.sel:
            self.sel, self.anchor = [iid], iid
            self._selection_changed()
        menu, sub, sub_out = self.ctx_menu, self.ctx_aspect, self.ctx_output
        for m in (menu, sub, sub_out):
            m.delete(0, "end")
        chosen = [it for it in self.items if it["id"] in set(self.sel)]
        menu.add_command(label=self.t("ctx_play"), command=lambda: self.play_index(self._index_of(iid)))
        menu.add_command(label=self.t("ctx_duplicate"), command=self.duplicate_selected)
        menu.add_command(label=self.t("ctx_rename"), command=lambda: self._rename_item(self.items[self._index_of(iid)]))
        menu.add_command(label=self.t("ctx_remove"), command=self.remove_selected)
        menu.add_separator()
        outs = {it["output"] for it in chosen}
        for o in self.outs:
            mark = "✓  " if outs == {o.num} else "     "
            sub_out.add_command(label=mark + self._out_name(o.num),
                                command=lambda n=o.num: self.set_output(n))
        menu.add_cascade(label=self.t("ctx_output"), menu=sub_out, state="normal" if self.outs else "disabled")
        modes = {it["aspect"] for it in chosen}
        for mode in ASPECTS:
            mark = "✓  " if modes == {mode} else "     "
            sub.add_command(label=mark + self.t("asp_" + mode), command=lambda m=mode: self.set_aspect(m))
        menu.add_cascade(label=self.t("ctx_aspect"), menu=sub)
        if len(chosen) == 1 and chosen[0].get("is_slideshow"):
            menu.add_command(label=self.t("ctx_options"), command=lambda it=chosen[0]: self._open_slideshow_options(it))
            menu.add_command(label=self.t("ctx_ss_start"), command=lambda it=chosen[0]: self._ss_start_now(it))
        if len(chosen) == 1 and chosen[0].get("is_capture") and (chosen[0].get("cap") or {}).get("kind") in ("video", "audio", "screen", "window"):
            menu.add_command(label=self.t("ctx_options"),
                             command=lambda it=chosen[0]: self.open_capture_dialog(it["cap"]["kind"], it))
        if len(chosen) == 1 and chosen[0].get("is_ndi"):
            menu.add_command(label=self.t("ctx_options"), command=lambda it=chosen[0]: self.add_ndi(it))
        if NDI.available:
            self._ndi_ctx_menu(menu, chosen)
        if len(chosen) == 1 and chosen[0].get("is_rtmp"):
            menu.add_command(label=self.t("ctx_options"), command=lambda it=chosen[0]: self._rtmp_dialog(it))
        if len(chosen) == 1 and chosen[0].get("is_countdown"):
            menu.add_command(label=self.t("ctx_cd_panel"), command=lambda it=chosen[0]: self._open_cd_panel(it))
        heads = [it for it in chosen if it.get("is_group")]
        members = [it for it in chosen if not it.get("is_group")]
        if len(heads) == 1:
            menu.add_separator()
            menu.add_command(label=self.t("ctx_group_toggle"), command=lambda h=heads[0]: self._toggle_group(h))
            menu.add_command(label=self.t("ctx_group_ungroup"), command=lambda h=heads[0]: self._ungroup(h))
        glist = [h for h in self.items if h.get("is_group")]
        if members and glist:
            gm = self._make_menu(menu)
            gm.add_command(label=self.t("ctx_group_none"), command=lambda its=members: self._set_group(its, None))
            gm.add_separator()
            for h in glist:
                gm.add_command(label=h.get("name") or self.t("grp_default"), command=lambda its=members, g=h["id"]: self._set_group(its, g))
            menu.add_cascade(label=self.t("ctx_group_add"), menu=gm)
        ndi_items = [it for it in chosen if it.get("is_ndi")]
        if ndi_items:
            low = all(it.get("ndi_low_bw") for it in ndi_items)
            menu.add_command(label=("✓  " if low else "     ") + self.t("ndi_low_bw"),
                             command=lambda its=ndi_items, v=not low: self._ndi_set_low_bw(its, v))
        if len(chosen) == 1 and chosen[0].get("is_youtube") and not chosen[0].get("ytdl_formats"):
            menu.add_command(label=self.t("ctx_quality") + " …", state="disabled")
            if not chosen[0].get("_fmt_req"):                  # the list of qualities is still missing: fetch it now
                chosen[0]["_fmt_req"] = True
                threading.Thread(target=self._yt_fill_worker, args=([chosen[0]["id"]],), daemon=True).start()
        if len(chosen) == 1 and chosen[0].get("is_youtube") and chosen[0].get("ytdl_formats"):
            qmenu = self._make_menu(menu)
            it = chosen[0]
            cur = it.get("ytdl_format", "best")
            qmenu.add_command(label=("✓  " if cur == "best" else "     ") + self.t("quality_best"),
                              command=lambda: self._set_youtube_quality(it, "best"))
            for fmt in it["ytdl_formats"][:12]:
                h = fmt["height"]
                label = f"{h}p"
                ytdl_sel = yt_build_format(h, None)
                mark = "✓  " if cur == ytdl_sel else "     "
                qmenu.add_command(label=mark + label,
                                  command=lambda sel=ytdl_sel, ii=it: self._set_youtube_quality(ii, sel))
            menu.add_cascade(label=self.t("ctx_quality"), menu=qmenu)
        self._popup(menu, e)
        return "break"

    @staticmethod
    def _popup(menu, e):
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def _drop_slot(self, y):
        if not self.items:
            return 0, y
        iid = self.tree.identify_row(y)
        if iid:
            _bx, by, _bw, bh = self.tree.bbox(iid)
            k = self._index_of(iid)
            return (k, by) if y < by + bh / 2 else (k + 1, by + bh)
        first = self.tree.bbox(self.items[0]["id"])
        if first and y < first[1]:
            return 0, first[1]
        last = self.tree.bbox(self.items[-1]["id"])
        return len(self.items), (last[1] + last[3] if last else y)

    def _reorder(self, ids, slot):
        snap = self._snap()
        before = sum(1 for k, it in enumerate(self.items) if k < slot and it["id"] in ids)
        chosen = [it for it in self.items if it["id"] in ids]
        rest = [it for it in self.items if it["id"] not in ids]
        pos = slot - before
        self.items = rest[:pos] + chosen + rest[pos:]
        self._tree_resync()
        self._restore(snap)
        self._refresh_all()
        self._highlight()

    def move_selected(self, d):
        if not self.sel:
            return
        idxs = sorted(self._index_of(i) for i in self.sel)
        if d < 0:
            if idxs[0] == 0:
                return
            slot = idxs[0] - 1
        else:
            if idxs[-1] >= len(self.items) - 1:
                return
            slot = idxs[-1] + 2
        self._reorder(set(self.sel), slot)
        if self.anchor:
            self.tree.see(self.anchor)

    def _menu_next(self, it, e):
        menu = self.next_menu
        menu.delete(0, "end")
        cur = it["next"]
        def add(label, key):
            menu.add_command(label=("✓  " if cur == key else "     ") + label,
                             command=lambda: self._set_next(it, key))
        add(self.t("nx_menu_none"), "none")
        add(self.t("nx_prev"), "prev")
        add(self.t("nx_next"), "next")
        add(self.t("nx_first"), "first")
        add(self.t("nx_last"), "last")
        menu.add_separator()
        for k, other in enumerate(self.items):
            name = other.get("name") or os.path.basename(other["path"])
            add(f"{k + 1}   ·   {name if len(name) <= 34 else name[:33] + '…'}", other["id"])
        self._popup(menu, e)

    def _menu_sim(self, it, e):
        menu = self.sim_menu
        menu.delete(0, "end")
        cur = it.get("sim", "none")
        def add(label, key):
            menu.add_command(label=("✓  " if cur == key else "     ") + label,
                             command=lambda: self._set_sim(it, key))
        add(self.t("nx_none"), "none")
        menu.add_separator()
        for k, other in enumerate(self.items):
            if other["output"] == it["output"] or other is it:
                continue
            name = other.get("name") or os.path.basename(other["path"])
            add(f"{k + 1}   ·   {name if len(name) <= 34 else name[:33] + '…'}", other["id"])
        self._popup(menu, e)

    def _set_sim(self, it, key):
        it["sim"] = key
        self._refresh_row(it)

    def _menu_output(self, it, e):
        menu = self.out_menu
        menu.delete(0, "end")
        ids = list(self.sel) if it["id"] in self.sel else [it["id"]]
        if not self.outs:
            menu.add_command(label=self.t("outs_none"), state="disabled")
        for o in self.outs:
            mark = "✓  " if it["output"] == o.num else "     "
            menu.add_command(label=mark + self._out_name(o.num),
                             command=lambda n=o.num: self.set_output(n, ids))
        self._popup(menu, e)

    def _set_next(self, it, key):
        it["next"] = key
        if key == "none":
            it["ms"] = 0
        else:
            it["repeat"] = it["fl"] = False
        self._refresh_row(it)
        self._apply_loop(it)

    def _toggle(self, it, name):
        key = {"rep": "repeat"}.get(name, name)
        new = not it[key]
        it[key] = new
        if new and name == "rep":
            it["next"], it["fl"], it["ms"] = "none", False, 0
        elif new and name == "fl":
            it["next"], it["repeat"], it["ms"] = "none", False, 0
        self._refresh_row(it)
        self._highlight()
        if name == "rep":
            self._apply_loop(it)
            self.status_t("st_ready_short")

    # ---- Fade In / Fade Out cells: independent checkbox + "ms" box placed over each visible cell ----
    def _schedule_fade_sync(self):
        if self._fade_sync_pending:
            return
        self._fade_sync_pending = True
        if time.time() - self.__dict__.get("_resize_t", 0.0) < 0.4:
            self.root.after(160, self._sync_fade_widgets)       # while the window is being resized: once it settles, not on every step
        else:
            self.root.after_idle(self._sync_fade_widgets)

    def _row_bg(self, iid):
        theme = THEMES[self.theme_name]
        tags = self.tree.item(iid, "tags") or ()
        if "playing" in tags:
            return theme["ROW_PLAYING"]
        if "selected" in tags:
            return theme["ROW_SELECTED"]
        return theme["TREE_BG"]

    def _destroy_fade_widgets(self):
        for w in self._fade_w.values():
            try:
                w["frame"].destroy()
            except tk.TclError:
                pass
        self._fade_w.clear()
        self._schedule_fade_sync()

    def _make_fade_widget(self, it, col):
        iid = it["id"]
        theme = THEMES[self.theme_name]
        bg = self._row_bg(iid)
        on_key = col + "_on"
        frame = tk.Frame(self.tree, bg=bg, bd=0, highlightthickness=0)
        on = tk.BooleanVar(value=bool(it.get(on_key)))
        ms = tk.StringVar(value=str(int(it.get(col, 0) or 0)))
        cb = tk.Checkbutton(frame, variable=on, bg=bg, activebackground=bg, selectcolor="#ffffff",
                            bd=0, highlightthickness=0, takefocus=0, cursor="hand2")
        ent = tk.Entry(frame, textvariable=ms, width=6, justify="center", font=("Segoe UI", 10),
                       bg=theme["ENTRY_BG"], fg=theme["FG"], insertbackground=theme["FG"],
                       relief="flat", highlightthickness=1, highlightbackground=theme["LINE"],
                       highlightcolor=theme["ACCENT"])
        lbl = tk.Label(frame, text="ms", bg=bg, fg=theme["MUTED"], font=("Segoe UI", 9))
        cb.pack(side="left", padx=(6, 6))
        ent.pack(side="left", padx=(0, 4), pady=3, fill="y")
        lbl.pack(side="left")
        w = {"frame": frame, "cb": cb, "ent": ent, "lbl": lbl, "on": on, "ms": ms,
             "bg": bg, "geo": None, "last": None}

        def toggle(i=iid, c=col, v=on):
            # only flips the on/off flag: never starts editing the ms box
            idx = self._find(i)
            if idx is not None:
                self.items[idx][c + "_on"] = bool(v.get())
                w["last"] = (bool(v.get()), str(int(self.items[idx].get(c, 0) or 0)))
            self.root.focus_set()

        def commit(_e=None, i=iid, c=col, var=ms):
            idx = self._find(i)
            if idx is None:
                return
            try:
                val = max(0, min(int(float(var.get().strip().replace(",", ".") or 0)), 60000))
            except ValueError:
                val = int(self.items[idx].get(c, 0) or 0)
            self.items[idx][c] = val
            var.set(str(val))
            w["last"] = (bool(on.get()), str(val))

        def done(_e=None):
            commit()
            self.root.focus_set()
            return "break"

        def revert(_e=None, i=iid, c=col, var=ms):
            idx = self._find(i)
            if idx is not None:
                var.set(str(int(self.items[idx].get(c, 0) or 0)))
            self.root.focus_set()
            return "break"

        def pick(e, i=iid):
            self._select_click(i, e)
            self._press = {"iid": i, "name": "fade", "y": e.y_root - self.tree.winfo_rooty(), "drag": False, "mod": True}
            return "break"

        def drag(e):
            self._on_tree_motion(_Ev(y=e.y_root - self.tree.winfo_rooty()))
            return "break"

        def drop(e):
            self._on_tree_release(_Ev(y=e.y_root - self.tree.winfo_rooty()))
            return "break"

        def wheel(e):
            self.tree.yview_scroll(-1 if e.delta > 0 else 1, "units")
            self._schedule_fade_sync()
            return "break"

        cb.configure(command=toggle)
        ent.bind("<Return>", done)
        ent.bind("<KP_Enter>", done)
        ent.bind("<Escape>", revert)
        ent.bind("<FocusOut>", commit)
        ent.bind("<FocusIn>", lambda _e: ent.select_range(0, "end"))
        for widget in (frame, lbl):
            widget.bind("<Button-1>", pick)
            widget.bind("<B1-Motion>", drag)
            widget.bind("<ButtonRelease-1>", drop)
        for widget in (frame, cb, ent, lbl):
            widget.bind("<MouseWheel>", wheel)
        return w

    def _sync_fade_widgets(self):
        self._fade_sync_pending = False
        try:
            if not self.tree.winfo_exists():
                return
        except tk.TclError:
            return
        cols = [c for c in FADE_COLS if any(cid == c for cid, _w in COLUMNS)]
        live = {it["id"]: it for it in self.items if not it.get("is_group")}
        for key in list(self._fade_w):
            if key[0] not in live or key[1] not in cols:
                try:
                    self._fade_w.pop(key)["frame"].destroy()
                except tk.TclError:
                    pass
        try:
            focused = self.root.focus_get()
        except (KeyError, tk.TclError):
            focused = None
        for iid, it in live.items():
            if not self.tree.exists(iid):
                continue
            for col in cols:
                box = self.tree.bbox(iid, col)
                w = self._fade_w.get((iid, col))
                if not box:
                    if w is not None and w["geo"] is not None:
                        w["frame"].place_forget()
                        w["geo"] = None
                    continue
                if w is None:
                    w = self._make_fade_widget(it, col)
                    self._fade_w[(iid, col)] = w
                bg = self._row_bg(iid)
                if w["bg"] != bg:
                    w["bg"] = bg
                    for part in ("frame", "cb", "lbl"):
                        w[part].configure(bg=bg)
                    w["cb"].configure(activebackground=bg)
                state = (bool(it.get(col + "_on")), str(int(it.get(col, 0) or 0)))
                if w["last"] != state and focused is not w["ent"]:
                    w["on"].set(state[0])
                    w["ms"].set(state[1])
                    w["last"] = state
                if w["geo"] != box:
                    x, y, wd, h = box
                    w["frame"].place(x=x + 1, y=y + 1, width=max(1, wd - 2), height=max(1, h - 2))
                    w["frame"].lift()
                    w["geo"] = box

    def _edit_cell(self, it, col):
        box = self.tree.bbox(it["id"], col)
        if not box:
            return
        x, y, w, h = box
        theme = THEMES[self.theme_name]
        ent = ttk.Entry(self.tree, justify="center")
        if col == "fade_in":
            ent.insert(0, str(it.get("fade_in", "")) if it.get("fade_in") else "")
        elif col == "fade_out":
            ent.insert(0, str(it.get("fade_out", "")) if it.get("fade_out") else "")
        elif col == "sched":
            ent.insert(0, it.get("sched", ""))
        else:
            d = it.get("dur", 0.0) or 0.0
            ent.insert(0, fmt_duration(d) if d > 0 else "")
        ent.place(x=x, y=y, width=w, height=h)
        ent.focus_set()
        ent.select_range(0, "end")
        ent.bind("<Return>", lambda _e: self._commit_edit())
        ent.bind("<FocusOut>", lambda _e: self._commit_edit())
        ent.bind("<Escape>", lambda _e: self._commit_edit(cancel=True))
        self._edit = (ent, it, col)

    def _commit_edit(self, cancel=False):
        if not self._edit:
            return
        ent, it, col = self._edit
        self._edit = None
        text = ent.get().strip()
        ent.destroy()
        if cancel:
            return
        theme = THEMES[self.theme_name]
        if col == "sched":
            if not text:
                it["sched"], it["sched_on"], it["sched_day"] = "00:00:00", False, ""
            else:
                secs = parse_clock(text)
                if secs is None:
                    self.status_t("st_sched_invalid", theme["RED"])
                else:
                    it["sched"], it["sched_on"], it["sched_day"] = fmt_clock(secs), True, ""
        elif col == "dur":
            d = parse_duration(text)
            if d is None:
                self.status_t("st_dur_invalid", theme["RED"])
            else:
                it["dur"] = d
        elif col in ("fade_in", "fade_out"):
            if text == "" or text == "—":
                it[col] = 0
            else:
                try:
                    ms = max(0, min(int(text or 0), 60000))
                except ValueError:
                    ms = 0
                it[col] = ms
        self._refresh_row(it)
        self.root.focus_set()

    def _apply_column_order(self, order):
        global COLUMNS
        if not isinstance(order, list):
            return
        widths = {c[0]: c[1] for c in COLUMNS}
        new_cols = []
        for cid in order:
            if cid in widths:
                new_cols.append((cid, widths[cid]))
        for cid, w in COLUMNS:
            if cid not in [c[0] for c in new_cols]:
                new_cols.append((cid, w))
        COLUMNS[:] = new_cols
        if hasattr(self, "tree"):
            self.tree["columns"] = [c[0] for c in COLUMNS]
            for cid, width in COLUMNS:
                self.tree.heading(cid, text=self.t("col_" + cid))
                self.tree.column(cid, width=width, minwidth=COL_MIN_W, stretch=False,
                                 anchor="w" if cid == "nome" else "center")
            self._refresh_all()

    def _sync_col_widths(self):
        """Copy the widths the user dragged in the Treeview back into COLUMNS (kept across reorders/saves)."""
        for k, (cid, w) in enumerate(COLUMNS):
            try:
                COLUMNS[k] = (cid, max(COL_MIN_W, int(self.tree.column(cid, "width"))))
            except (tk.TclError, ValueError):
                pass

    def _apply_col_widths(self, d):
        if not isinstance(d, dict):
            return
        for k, (cid, w) in enumerate(COLUMNS):
            v = d.get(cid)
            if isinstance(v, int) and not isinstance(v, bool) and COL_MIN_W <= v <= 1200:
                COLUMNS[k] = (cid, v)
                if hasattr(self, "tree"):
                    self.tree.column(cid, width=v)
        self._schedule_fade_sync()

    def _on_heading_press(self, e):
        region = self.tree.identify_region(e.x, e.y)
        self._col_resizing = region == "separator"
        if region != "heading":
            self._col_drag = None
            return
        col = self.tree.identify_column(e.x)
        if col:
            self._col_drag = {"col": col, "x": e.x}

    def _on_heading_motion(self, e):
        if getattr(self, "_col_resizing", False):
            self._schedule_fade_sync()          # keep the Fade checkboxes glued to their cells
            return
        if not hasattr(self, "_col_drag") or not self._col_drag:
            return

    def _on_heading_release(self, e):
        if getattr(self, "_col_resizing", False):
            self._col_resizing = False
            self._col_drag = None
            self._sync_col_widths()
            self._schedule_fade_sync()
            return
        self._sync_col_widths()
        if not hasattr(self, "_col_drag") or not self._col_drag:
            return
        start_col = self._col_drag.get("col")
        self._col_drag = None
        end_col = self.tree.identify_column(e.x)
        if not start_col or not end_col or start_col == end_col:
            return
        try:
            si = int(start_col[1:]) - 1
            ei = int(end_col[1:]) - 1
            if 0 <= si < len(COLUMNS) and 0 <= ei < len(COLUMNS):
                col = COLUMNS.pop(si)
                COLUMNS.insert(ei, col)
                self._apply_column_order([c[0] for c in COLUMNS])
        except Exception:
            pass

    def _playlist_data(self, path):
        self._sync_col_widths()
        pos = {it["id"]: k + 1 for k, it in enumerate(self.items)}
        base = os.path.dirname(os.path.abspath(path))
        items = []
        for it in self.items:
            nxt = it["next"]
            if nxt not in ("none", "prev", "next", "first", "last"):
                nxt = pos.get(nxt, "next")
            sim = it.get("sim", "none")
            if sim not in ("none",):
                sim = pos.get(sim, "none")
            try:
                rel = os.path.relpath(it["path"], base)
            except ValueError:
                rel = None
            items.append({
                "path": it["path"], "rel": rel, "name": it.get("name"), "is_youtube": it.get("is_youtube", False),
                "rtmp": it.get("is_rtmp", False), "rtmp_port": it.get("rtmp_port", 0),
                "ndi": it.get("is_ndi", False), "ndi_source": it.get("ndi_source", ""),
                "ndi_low_bw": it.get("ndi_low_bw", False),
                "capture": it.get("cap") if it.get("is_capture") else None,
                "slideshow": it.get("ss") if it.get("is_slideshow") else None,
                "countdown": ({k: v for k, v in it["cd"].items() if k not in ("running", "acc", "t0") and not k.startswith("_")}
                              if it.get("is_countdown") and it.get("cd") else None),
                "ytdl_format": it.get("ytdl_format"), "ytdl_heights": [f["height"] for f in (it.get("ytdl_formats") or [])][:30],
                "yt_hw": bool(it.get("yt_hw")), "yt_audio": bool(it.get("yt_audio")), "duration": it.get("dur", 0.0),
                "yt_live": it.get("yt_live") is True, "spotify": it.get("spotify"),
                "output": it["output"], "next": nxt,
                "repeat": it["repeat"], "fade_in": it.get("fade_in", 0), "fade_out": it.get("fade_out", 0),
                "fade_in_on": it.get("fade_in_on", False), "fade_out_on": it.get("fade_out_on", False),
                "mute": it.get("mute", False),
                "ms": it["ms"], "freeze_first": it["f1"],
                "freeze_last": it["fl"], "with_next": it["wn"], "sim": sim,
                "in": it["in"], "out": it["out"],
                "aspect": it["aspect"],
                "schedule": it.get("sched", "00:00:00"),
                "schedule_on": it.get("sched_on", False),
                "schedule_days": list(it.get("sched_dow") or []),
                "group_header": bool(it.get("is_group")), "gid": it["id"] if it.get("is_group") else None,
                "group": it.get("group"), "collapsed": bool(it.get("collapsed")),
            })
        return {"app": APP_ID, "version": 4,
                "settings": {"shuffle": bool(self.shuffle_var.get() if hasattr(self, "shuffle_var") else False)},
                "outputs": self._outputs_data(),
                "multiview": self._mv_data(),
                "composite": self._co_data(),
                "columns": [c[0] for c in COLUMNS],
                "col_widths": {c[0]: c[1] for c in COLUMNS},
                "items": items}

    def save_playlist(self, as_new=False):
        path = self.playlist_path
        theme = THEMES[self.theme_name]
        if as_new or not path or os.path.splitext(path)[1].lower() != PL_EXT:
            kw = {}
            if self.playlist_path and os.path.isdir(os.path.dirname(self.playlist_path)):
                kw["initialdir"] = os.path.dirname(self.playlist_path)
            stem = os.path.splitext(os.path.basename(self.playlist_path))[0] if self.playlist_path else "playlist"
            path = filedialog.asksaveasfilename(
                title=self.t("dlg_save_pl"), defaultextension=PL_EXT,
                filetypes=[(self.t("ft_playlist"), "*" + PL_EXT), (self.t("ft_all"), "*.*")],
                initialfile=stem + PL_EXT, **kw)
            if not path:
                return
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._playlist_data(path), f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError as exc:
            self.status_t("st_pl_save_err", theme["RED"], err=exc)
            return
        self.playlist_path = path
        self._update_title()
        self.cfg["last_playlist"] = path
        save_config(self.cfg)
        self.status_t("st_pl_saved", name=os.path.basename(path))
        messagebox.showinfo(self.t("pl_save"), self.t("pl_saved_ok", name=os.path.splitext(os.path.basename(path))[0], path=path),
                            parent=self.root)

    def open_playlist(self):
        theme = THEMES[self.theme_name]
        if self.items and not messagebox.askyesno(self.t("dlg_open_pl"), self.t("ask_replace")):
            return
        path = filedialog.askopenfilename(
            title=self.t("dlg_open_pl"),
            filetypes=[(self.t("ft_playlist_any"), "*.mmp *.json"), (self.t("ft_playlist"), "*.mmp"),
                       (self.t("ft_playlist_old"), "*.json"), (self.t("ft_all"), "*.*")])
        if path:
            self.load_playlist(path)

    def load_playlist(self, path):
        theme = THEMES[self.theme_name]
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or data.get("app") != APP_ID or not isinstance(data.get("items"), list):
                raise ValueError(self.t("pl_invalid"))
        except (OSError, ValueError) as exc:
            self.status_t("st_pl_open_err", theme["RED"], err=exc)
            return
        self._apply_playlist_data(data, os.path.dirname(os.path.abspath(path)), path)

    def _sync_outputs_same(self, data):
        def sig(o):
            return (o.get("num"), o.get("name"), o.get("resolution"), o.get("fps"), bool(o.get("ndi")), o.get("ndi_quality"))
        return [sig(o) for o in self._outputs_data()] == [sig(o) for o in data.get("outputs", []) if isinstance(o, dict)]

    def _apply_playlist_data(self, data, base, path, keep=False):
        """Builds the list (and outputs, Multiview, Composite) from show data. path=None: a show received from the Main (nothing is saved);
        keep=True: outputs and views that did not change are left running."""
        theme = THEMES[self.theme_name]
        self.clear_list()
        if isinstance(data.get("columns"), list):
            self._apply_column_order(data["columns"])
        self._apply_col_widths(data.get("col_widths"))
        new, links, sim_links, missing = [], [], [], 0
        gid_map = {}
        for raw in data["items"]:
            if not isinstance(raw, dict) or not raw.get("path"):
                continue
            p = str(raw["path"])
            ss_raw = raw.get("slideshow") if isinstance(raw.get("slideshow"), dict) else None
            cd_raw = raw.get("countdown") if isinstance(raw.get("countdown"), dict) else None
            if not os.path.exists(p) and raw.get("rel") and not ss_raw and not cd_raw:
                alt = os.path.normpath(os.path.join(base, str(raw["rel"])))
                if os.path.exists(alt):
                    p = alt
            it = self._new_item(p)
            if raw.get("name"):
                it["name"] = str(raw["name"])
            if raw.get("group_header") is True:
                it["is_group"], it["collapsed"] = True, raw.get("collapsed") is True
                it["path"] = "group://" + it["id"]
                gid_map[str(raw.get("gid"))] = it["id"]
                new.append(it)
                links.append("none")
                sim_links.append("none")
                continue
            it["_gref"] = raw.get("group")
            if raw.get("is_youtube"):
                it["is_youtube"] = True
            if raw.get("rtmp") is True and sanitize_key(raw.get("name")):
                it["is_rtmp"] = True
                it["name"] = sanitize_key(raw.get("name"))
                pnum = raw.get("rtmp_port")
                it["rtmp_port"] = pnum if isinstance(pnum, int) and not isinstance(pnum, bool) and 0 < pnum < 65536 else 0
            src = raw.get("ndi_source")
            if raw.get("ndi") is True and isinstance(src, str) and src.strip():
                it["is_ndi"] = True
                it["ndi_source"] = src.strip()[:200]
                it["ndi_low_bw"] = raw.get("ndi_low_bw") is True
                it["name"] = it.get("name") or it["ndi_source"]
                it["path"] = "ndi://" + it["ndi_source"]
            cap_raw = cap_clean(raw.get("capture"))
            if cap_raw is not None:
                it["is_capture"], it["cap"] = True, cap_raw
                it["name"] = it.get("name") or cap_label(cap_raw)
                it["path"] = f"capture://{cap_raw['kind']}/{cap_label(cap_raw)}"
            if ss_raw is not None:
                it["is_slideshow"], it["ss"] = True, ss_clean_cfg(ss_raw)
                it["name"] = it.get("name") or "Slideshow"
            if cd_raw is not None:
                it["is_countdown"], it["cd"] = True, cd_clean(cd_raw)
                it["path"] = "countdown://" + it["id"]
                it["name"] = it.get("name") or "Countdown"
            if raw.get("ytdl_format"):
                it["ytdl_format"] = str(raw["ytdl_format"])
            hs = raw.get("ytdl_heights")
            if it.get("is_youtube") and isinstance(hs, list):
                it["ytdl_formats"] = [{"height": h, "format_id": None, "ext": None} for h in hs
                                      if isinstance(h, int) and not isinstance(h, bool) and 100 <= h <= 8640][:30]
            it["yt_hw"] = bool(raw.get("yt_hw"))
            it["yt_audio"] = bool(raw.get("yt_audio"))
            it["yt_live"] = raw.get("yt_live") is True
            if isinstance(raw.get("spotify"), dict):
                it["spotify"] = {k: str(v) for k, v in raw["spotify"].items() if k in ("link", "title", "artist")}
            it["_ylk"] = "yt_live" in raw                      # shows saved before this flag existed: YouTube is asked once, quietly
            o_num = raw.get("output")
            it["output"] = o_num if isinstance(o_num, int) and not isinstance(o_num, bool) and o_num >= 1 else 1
            it["repeat"] = raw.get("repeat") is True
            it["fade_in"] = int(num(raw.get("fade_in"), 0, 0, 60000))
            it["fade_out"] = int(num(raw.get("fade_out"), 0, 0, 60000))
            it["fade_in_on"] = raw.get("fade_in_on") is True
            it["fade_out_on"] = raw.get("fade_out_on") is True
            it["mute"] = raw.get("mute") is True
            it["ms"] = int(num(raw.get("ms"), 0, 0, 600000))
            it["f1"] = raw.get("freeze_first") is True
            it["fl"] = raw.get("freeze_last") is True
            it["wn"] = raw.get("with_next") is True
            it["sim"] = "none"
            it["in"] = num(raw.get("in"), 0.0, 0.0)
            out = raw.get("out")
            it["out"] = num(out, None, it["in"] + MIN_GAP) if out is not None else None
            it["aspect"] = raw.get("aspect") if raw.get("aspect") in ASPECTS else "fit"
            sched = parse_clock(raw["schedule"]) if isinstance(raw.get("schedule"), str) else None
            it["sched"] = fmt_clock(sched) if sched is not None else "00:00:00"
            it["sched_on"] = raw.get("schedule_on") is True
            days = raw.get("schedule_days")
            it["sched_dow"] = sorted({d for d in days if isinstance(d, int) and 0 <= d <= 6}) if isinstance(days, list) else []
            it["dur"] = num(raw.get("duration"), 0.0, 0.0, 86400.0)
            it["missing"] = False if (it.get("is_youtube") or is_live(it) or it.get("is_slideshow")
                                      or p.startswith(("http://", "https://"))) else not os.path.exists(p)
            missing += it["missing"]
            new.append(it)
            links.append(raw.get("next", "none"))
            sim_links.append(raw.get("sim", "none"))
        for it, nx, sx in zip(new, links, sim_links):
            if nx in ("none", "prev", "next", "first", "last"):
                it["next"] = nx
            elif isinstance(nx, int) and not isinstance(nx, bool) and 1 <= nx <= len(new):
                it["next"] = new[nx - 1]["id"]
            if isinstance(sx, int) and not isinstance(sx, bool) and 1 <= sx <= len(new):
                it["sim"] = new[sx - 1]["id"]
            else:
                it["sim"] = "none"
            if it["repeat"]:
                it["next"], it["fl"], it["ms"] = "none", False, 0
            elif it["fl"]:
                it["next"], it["ms"] = "none", 0
            elif it["ms"] > 0 and it["next"] == "none":
                it["next"] = "next"
            elif it["next"] == "none":
                it["ms"] = 0
        for it in new:
            ref = it.pop("_gref", None)
            it["group"] = gid_map.get(str(ref)) if ref is not None else None
        self.items = new
        self._regroup_order()
        new = self.items
        for it in new:
            self.tree.insert("", "end", iid=it["id"], values=self._row(it))
        self._tree_resync()
        def same(a, b):
            return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
        if not (keep and same(self._mv_data(), data.get("multiview"))):
            self._mv_load(data.get("multiview"))              # first: its screen is then not available to the outputs
        if not (keep and same(self._co_data(), data.get("composite"))):
            self._co_load(data.get("composite"))
        if keep and self._sync_outputs_same(data):
            for o in self.outs:
                o.index = -1
        else:
            self._load_outputs(data, new)
        self._refresh_all()
        need = [it["id"] for it in new if it.get("is_youtube") and (not it.get("ytdl_formats") or it.get("_ylk") is False)]
        if need:                                              # shows saved before the qualities were stored: ask YouTube again, quietly
            threading.Thread(target=self._yt_fill_worker, args=(need,), daemon=True).start()
        settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
        if hasattr(self, "shuffle_var"):
            on = settings.get("shuffle") is True
            self.shuffle_var.set(on)
            if hasattr(self, "shuffle_mode"):
                if not on:
                    self.shuffle_mode.set("off")
                elif self.shuffle_mode.get() == "off":
                    self.shuffle_mode.set("all")
            self._update_shuffle_btn()
        if path is None:
            return
        self.playlist_path = path
        self._update_title()
        self.cfg["last_playlist"] = path
        save_config(self.cfg)
        name = os.path.basename(path)
        if missing:
            self.status_t("st_pl_missing", theme["RED"], name=name, n=len(new), m=missing)
        else:
            self.status_t("st_pl_opened", name=name, n=len(new))

    def _update_out_name(self, out, item):
        """Name of the item the output is playing, between the monitor and the time bar (cut with … to the width)."""
        lbl = getattr(out, "name_lbl", None)
        if lbl is None:
            return
        name = ""
        if item is not None:
            name = str(item.get("name") or os.path.basename(str(item.get("path") or "")) or "")
        key = (name, self.theme_name)
        if getattr(out, "_nm_shown", None) == key:
            return
        out._nm_shown = key
        try:
            f = tkfont.Font(font=lbl.cget("font") or ("Segoe UI", 9, "bold"))
            room = PV_W - 12
            txt = name
            if f.measure(txt) > room:
                while txt and f.measure(txt + "…") > room:
                    txt = txt[:-1]
                txt = txt.rstrip() + "…"
            lbl.configure(text=txt, foreground=THEMES[self.theme_name]["FG"])
        except (tk.TclError, TypeError, ValueError):
            pass

    def _tick_output(self, out):
        item = self._cur_item(out)
        self._update_out_name(out, item)
        t0 = getattr(out.m, "_yt_t0", None) if out.m is not None else None
        if t0 and item is not None and item.get("is_youtube"):
            tp = self._prop(out, "time_pos", None)
            if tp is not None and time.time() - t0 > 0.2:
                out.m._yt_t0 = None
                diag_log(f"YouTube {str(item.get('path'))[-11:]} [out]: first picture / sound {time.time() - t0:.1f} s after Play "
                         f"(address: {getattr(out.m, '_yt_src', '?')})")
        self._diag_play(out, item)
        live_in = item is not None and bool(item.get("is_capture") or item.get("is_rtmp") or item.get("is_ndi") or is_ytlive(item))
        if live_in:                                            # a live feed has no position: the progress bar stands still
            pos = dur = 0.0
        else:
            pos = self._prop(out, "time_pos", 0)
            dur = self._prop(out, "duration", 0)
        paused = bool(self._prop(out, "pause", False))
        eof = bool(self._prop(out, "eof_reached", False))
        if self._n % 10 == 3 and self._yt_stall_watch(out, item, pos, paused):
            return
        if eof and out.m is not None and time.time() < getattr(out.m, "_yt_wait_until", 0.0):
            eof = False                                        # a YouTube address is still being resolved: eof is the old file's
            if getattr(out, "_eof_logged", None) is not item:
                out._eof_logged = item
                diag_log(f"{out.name}: end of file ignored for {max(0.0, out.m._yt_wait_until - time.time()):.0f} s (YouTube address still loading)")
        show_pos = show_dur = None
        if item is not None and item.get("is_slideshow"):
            run = self._rtmp.get(item["id"])       # mpv plays the slides one file at a time: a file ending is not the show ending
            show_pos, show_dur = run.position(time.monotonic()) if run is not None else (0.0, 0.0)
            pos = dur = 0.0
            eof = False
        self._apply_fade(out, item, pos, dur)
        if item is not None and time.time() > out.ignore_until:
            end_t = out.cur_out or dur
            if (item["next"] != "none" and item["ms"] > 0 and not item["repeat"] and not item["fl"]
                    and not out.fired and not paused and end_t > 0
                    and pos > out.cur_in + 0.2 and (end_t - pos) * 1000 <= item["ms"]):
                out.fired = True
                nxt = self._advance(out, item) if not self._is_backup() else None
                if nxt is not None:
                    self.play_index(nxt)
                    return
            if eof and not out.eof_handled:
                out.eof_handled = True
                self._on_eof(out)
                return
        if show_dur is not None:
            pos, dur = show_pos, show_dur                      # the progress bar and the times show the slideshow's own length
        if live_in and self._n % UI_EVERY == 0:
            if getattr(out, "_live_ui", None) != item["id"]:       # drawn once per item, not every tick
                out._live_ui = item["id"]
                if out.timeline:
                    out.timeline.set(dur=1, pos=0.0, t_in=0.0, t_out=None)
                cfg_if(out.time_lbl, text="LIVE")
                cfg_if(out.rem_lbl, text="", style="Rem.TLabel")
            cfg_if(out.play_btn, text="▶" if paused else "⏸")
        elif self._n % UI_EVERY == 0:
            out._live_ui = None
            if out.timeline:
                out.timeline.set(dur=max(dur, 1), pos=pos, t_in=out.cur_in if item is not None else 0.0,
                                 t_out=out.cur_out if item is not None else None)
            if item is not None and (out.cur_in > 0 or out.cur_out):
                start = out.cur_in
                end = out.cur_out if out.cur_out else dur
                playable = max(0.0, end - start)
                rel_pos = max(0.0, pos - start)
                cfg_if(out.time_lbl, text=f"{fmt(rel_pos)} / {fmt(playable)}")
            else:
                cfg_if(out.time_lbl, text=f"{fmt(pos)} / {fmt(dur)}")
            cfg_if(out.play_btn, text="▶" if paused else "⏸")
            end_t = (out.cur_out if item is not None and out.cur_out else dur) or 0
            if end_t > 0:
                rem = max(0.0, end_t - pos)
                warn = rem <= 10 and not paused and item is not None
                cfg_if(out.rem_lbl, text=f"−{fmt(rem)}", style="RemWarn.TLabel" if warn else "Rem.TLabel")
            else:
                cfg_if(out.rem_lbl, text="−00:00", style="Rem.TLabel")
        if self._n % PREVIEW_EVERY == 0:
            self._sync_preview(out, pos, paused)

    def _yt_stall_watch(self, out, item, pos, paused):
        """A YouTube video that stops advancing (nothing played, or it froze half way): after 15 s it is dumped to the log and
        loaded again at the same point; the second time with ONE muxed stream (no separate audio address); the third time the
        output moves on. True when it took over this tick."""
        if item is None or out.m is None or not item.get("is_youtube") or is_live(item):
            out._yt_wd = None
            return False
        now, p = time.time(), float(pos or 0.0)
        wd = out.__dict__.get("_yt_wd")
        if wd is None or wd["id"] != item["id"]:
            out._yt_wd = {"id": item["id"], "t0": now, "p": p, "tries": 0, "adv": now}
            return False
        if paused or abs(p - wd["p"]) > 0.05:                   # playing (or paused by the user): nothing to do
            wd["p"], wd["t0"] = p, now
            if wd["tries"] and not paused:
                wd["adv"] = wd.get("adv") or now
                if now - wd["adv"] > 10.0:                      # it has been playing for a while again: forget the earlier attempts
                    wd["tries"], wd["adv"] = 0, None
            return False
        wd["adv"] = None
        if now - wd["t0"] < 15.0:
            return False
        wd["tries"] += 1
        wd["t0"] = now
        name = (item.get("name") or item["path"])[:50]
        props = []
        for label, prop in (("pause", "pause"), ("core_idle", "core_idle"), ("paused_for_cache", "paused_for_cache"),
                            ("buffering", "cache_buffering_state"), ("cache_s", "demuxer_cache_duration"),
                            ("idle_active", "idle_active"), ("eof", "eof_reached"), ("seekable", "seekable"),
                            ("hwdec", "hwdec_current"), ("audio_pts", "audio_pts"), ("vid", "vid"), ("aid", "aid")):
            try:
                v = self._prop(out, prop, None)
                props.append(f"{label}={round(v, 1) if isinstance(v, float) else v}")
            except Exception:
                pass
        diag_log(f"output {out.num}: '{name}' stuck at {p:.1f} s for 15 s (attempt {wd['tries']}): " + " ".join(props))
        if wd["tries"] <= 2:
            for role in ("out", "pv"):
                self._yt_play_cache.pop((item["path"], self._yt_fmt_for(item, role)), None)
            if wd["tries"] == 2:
                self.__dict__.setdefault("_yt_muxed", set()).add(item["id"])
                diag_log(f"output {out.num}: loading '{name}' as one muxed stream")
                for role in ("out", "pv"):
                    self._yt_play_cache.pop((item["path"], self._yt_fmt_for(item, role)), None)
            out._load_start = p if p > 1.0 else None
            try:
                self._open_item(out, item)
            except SHUTDOWN:
                raise
            except Exception as exc:
                diag_log(f"output {out.num}: reload failed ({exc})")
            return False
        diag_log(f"output {out.num}: '{name}' still not playing -> next item")
        out._yt_wd = None
        out.eof_handled = True
        self._on_eof(out)
        return True

    def _diag_play(self, out, item):
        """Once per item and output, 5 s after it started: what the output really plays (for the log)."""
        key = (item["id"], id(out.m)) if item is not None else None
        if key is None or getattr(out, "_diag_key", None) == key:
            return
        if getattr(out, "_diag_pending", None) != key:
            out._diag_pending, out._diag_t = key, time.time()
            return
        if time.time() - out._diag_t < 5.0:
            return
        out._diag_key = key
        try:
            st = self.__dict__.get("_fstate", {}).get(out.num)
            tf = getattr(out, "_tf", {}).get("feed")
            diag_log(f"output {out.num} plays '{(item.get('name') or item['path'])[:50]}': yt={bool(item.get('is_youtube'))} yt_live={item.get('yt_live')} "
                     f"live_input={is_live(item)} dur_item={item.get('dur')} mpv_duration={self._prop(out, 'duration', None)} "
                     f"time_pos={self._prop(out, 'time_pos', None)} in/out={out.cur_in}/{out.cur_out} copy={st} "
                     f"copy_running={bool(tf and tf.running())} copy_got={bool(tf and tf.got)} monitor={getattr(out, '_mon_mode', None)}")
        except SHUTDOWN:
            raise
        except Exception as exc:
            diag_log(f"output {out.num}: diagnostic failed ({exc})")

    def _toggle_output_mute(self, out):
        if out.m:
            try:
                out.m.mute = out.out_mute.get()
            except Exception:
                pass

    # ---- Audio routing (Dante Controller style) -----------------------------------------------------------------------
    def _ar_device_list(self, out):
        """[(name, description)] of the audio devices libmpv offers (every audio output API this build has)."""
        player = out.m or out.pv or getattr(self, "ip", None)
        temp = None
        try:
            if player is None:
                temp = player = new_mpv(vo="null", force_window=False, idle=True)
            lst = getattr(player, "audio_device_list", None) or []
            res = [(d.get("name", ""), d.get("description", "") or d.get("name", "")) for d in lst if isinstance(d, dict) and d.get("name")]
            return res + [(pa_dev_name(api, name), f"{name}   ({ch} ch)") for api, name, ch, _sr in pa_devices()]
        except SHUTDOWN:
            raise
        except Exception as exc:
            diag_log(f"audio routing: device list failed ({exc})")
            return []
        finally:
            if temp is not None:
                try:
                    mpv_terminate(temp)
                except Exception:
                    pass

    @staticmethod
    def _ar_pa_channels():
        """Output channels per device name when `sounddevice` (PortAudio) happens to be installed; {} otherwise."""
        try:
            import sounddevice as sd
            res = {}
            for d in sd.query_devices():
                if d.get("max_output_channels", 0) > 0:
                    res[d["name"]] = max(res.get(d["name"], 0), int(d["max_output_channels"]))
            return res
        except Exception:
            return {}

    def _audio_apply(self, out):
        """Sends the output's routing (device, channel count, matrix) to its mpv."""
        m = out.m
        if m is None:
            return
        ar = out.ar
        if ar["dev"].startswith("pa|"):
            self._pa_start(out)
            return
        self._pa_stop(out)
        try:
            self._setp(m, "audio_device", ar["dev"] or "auto")
            nd = ar["n_dst"]
            if ar_is_default(ar) or (nd == 2 and not ar["map"]):
                self._setp(m, "audio_channels", "auto")
            else:
                self._setp(m, "audio_channels", ar_dst_layout(nd) if nd in AR_SRC_LAYOUTS else f"unknown{nd}")
            m.command("af", "set", ar_af_spec(ar))
            diag_log(f"audio routing {out.name}: device={ar['dev']} {ar['n_src']}->{nd} ch, {len(ar['map'])} crosspoints")
        except SHUTDOWN:
            raise
        except Exception as exc:
            diag_log(f"audio routing {out.name}: could not apply ({exc})")

    # -- PortAudio engine (ASIO / DirectSound / MME / WDM-KS): the output's sound is decoded by ffmpeg and played by sounddevice
    def _pa_stop(self, out):
        r, out._pa = getattr(out, "_pa", None), None
        out._pa_key = None
        if r is not None:
            r.close()

    def _pa_start(self, out):
        ar = out.ar
        info = pa_find(ar["dev"])
        ff = self._ffmpeg_path()
        if info is None or not ff or sd_mod is None:
            diag_log(f"audio routing {out.name}: PortAudio device '{ar['dev']}' not available (sounddevice={sd_mod is not None}, ffmpeg={bool(ff)}) -> mpv plays normally")
            self._pa_stop(out)
            self.status_t("ar_pa_missing", THEMES[self.theme_name]["RED"])
            return
        idx, maxch, sr = info
        if ar["n_dst"] > maxch:
            ar["n_dst"] = maxch
            ar["map"] = [p for p in ar["map"] if p[0] < maxch]
        key = (ar["dev"], ar["n_src"], ar["n_dst"], tuple(map(tuple, ar["map"])))
        if getattr(out, "_pa", None) is None or out._pa_key != key:
            self._pa_stop(out)
            try:
                out._pa = PaRouter(ff, idx, sr, ar, out.name)
                out._pa_key = key
                diag_log(f"audio routing {out.name}: PortAudio '{ar['dev']}' {ar['n_src']}->{ar['n_dst']} ch @ {sr} Hz, latency {out._pa.latency * 1000:.0f} ms")
            except SHUTDOWN:
                raise
            except Exception as exc:
                out._pa = None
                diag_log(f"audio routing {out.name}: PortAudio stream failed ({exc}) -> mpv plays normally")
                self.status_t("ar_pa_failed", THEMES[self.theme_name]["RED"], err=str(exc)[:80])
                return
        out._pa_fb = None
        if not getattr(out, "_pa_tick_on", False):
            out._pa_tick_on = True
            self.root.after(250, lambda o=out: self._pa_tick(o))

    def _pa_source(self, out):
        """(address, live) of the audio the PortAudio engine decodes for what the output plays; None = it cannot (mpv plays it)."""
        item = self._cur_item(out)
        if item is None or out.m is None or item.get("is_slideshow") or item.get("is_countdown") or is_live(item) and not is_ytlive(item):
            return None
        path = item.get("path") or ""
        if item.get("is_youtube"):
            ent = self._yt_play_cache.get((path, self._yt_fmt_for(item, "out")))
            if ent and ent.get("urls") and time.time() - ent["t"] < 3 * 3600:
                return ent["urls"][-1], float(item.get("dur") or 0) <= 0
            return None
        if os.path.isfile(path) and os.path.splitext(path)[1].lower() in MEDIA_EXT and not is_image(path):
            return path, False
        return None

    def _pa_tick(self, out):
        r = getattr(out, "_pa", None)
        if r is None or out not in self.outs or not out.ar["dev"].startswith("pa|") or self._closing:
            out._pa_tick_on = False
            if out not in self.outs:
                self._pa_stop(out)
            return
        try:
            got = self._pa_source(out) if out.m is not None else None
            if got is None:
                if out.m is not None and out._pa_fb is not True:
                    out._pa_fb = True                           # nothing the engine can decode: mpv plays this one itself
                    m = out.m
                    m.command("af", "set", "")
                    diag_log(f"audio routing {out.name}: this input is played by mpv (PortAudio engine cannot decode it)")
                r.update(None, 0.0, False, 0.0)
            else:
                if out._pa_fb is not False:
                    out._pa_fb = False
                    out.m.command("af", "set", "lavfi=graph=%8%volume=0")        # mpv keeps the clock and the picture, the sound goes to the device
                muted = bool(self.mute.get()) or bool(getattr(out, "out_mute", None) and out.out_mute.get())
                gain = 0.0 if muted else (float(out.vol.get()) / 100.0) ** 3 * float(out._fade_f)      # mpv's own volume curve
                r.update(got[0], float(self._prop(out, "time_pos", 0) or 0), bool(self._prop(out, "pause", False)), gain, got[1])
        except SHUTDOWN:
            raise
        except Exception as exc:
            diag_log(f"audio routing {out.name}: tick failed ({exc})")
        self.root.after(250, lambda o=out: self._pa_tick(o))

    def _open_audio_routing(self, out):
        theme = THEMES[self.theme_name]
        old = getattr(out, "_ar_win", None)
        try:
            if old is not None and old.winfo_exists():
                old.lift()
                return
        except tk.TclError:
            pass
        w = tk.Toplevel(self.root)
        out._ar_win = w
        w.title(self.t("route_win_title", name=out.name))
        w.geometry("1020x640")
        w.minsize(820, 480)
        w.configure(bg=theme["BG"])
        ar = ar_clean(json.loads(json.dumps(out.ar)))
        pa = self._ar_pa_channels()
        ttk.Label(w, text=self.t("route_head", name=out.name), style="Header.TLabel").pack(pady=(10, 4))
        body = ttk.Frame(w, padding=(10, 0, 10, 4))
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        left.pack(side="left", fill="y", padx=(0, 10))
        ttk.Label(left, text=self.t("ar_devices")).pack(anchor="w", pady=(0, 2))
        tree = ttk.Treeview(left, show="tree", selectmode="browse", height=18)
        tree.column("#0", width=300)
        sb = ttk.Scrollbar(left, orient="vertical", command=tree.yview, style="Vertical.TScrollbar")
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="y")
        sb.pack(side="left", fill="y")
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)
        bar = ttk.Frame(right)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text=self.t("ar_src_ch")).pack(side="left")
        src_cb = ttk.Combobox(bar, state="readonly", width=12, values=[f"{AR_SRC_LAYOUTS[n][0]} ({n})" for n in AR_SRC_LAYOUTS])
        src_cb.pack(side="left", padx=(4, 12))
        ttk.Label(bar, text=self.t("ar_dst_ch")).pack(side="left")
        dst_cb = ttk.Combobox(bar, state="readonly", width=5, values=[str(n) for n in AR_COUNTS])
        dst_cb.pack(side="left", padx=(4, 12))
        dev_lbl = ttk.Label(right, text="", style="Hint.TLabel", wraplength=640)
        dev_lbl.pack(anchor="w")
        info_lbl = ttk.Label(right, text="", style="Hint.TLabel", wraplength=640)
        info_lbl.pack(anchor="w", pady=(0, 4))
        holder = ttk.Frame(right)
        holder.pack(fill="both", expand=True)
        cv = tk.Canvas(holder, bg=theme["PANEL"], highlightthickness=0)
        vs = ttk.Scrollbar(holder, orient="vertical", command=cv.yview, style="Vertical.TScrollbar")
        hs = ttk.Scrollbar(holder, orient="horizontal", command=cv.xview, style="Horizontal.TScrollbar")
        cv.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        hs.pack(side="bottom", fill="x")
        vs.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)
        CELL, HEAD_W, HEAD_H = 30, 110, 54
        hover = {"r": -1, "c": -1}
        devices = {"list": []}

        def redraw(_e=None):
            cv.delete("all")
            ns, nd = ar["n_src"], ar["n_dst"]
            labels = AR_SRC_LAYOUTS[ns][1]
            fg, mut, acc = theme["FG"], theme["MUTED"], "#2fb45a"
            cv.create_text(6, HEAD_H - 6, anchor="sw", text=self.t("ar_corner"), fill=mut, font=("Segoe UI", 8))
            for c in range(ns):
                x = HEAD_W + c * CELL
                cv.create_rectangle(x, 0, x + CELL, HEAD_H, fill="#26405e" if hover["c"] == c else "#1b2c42", outline=theme["BG"])
                cv.create_text(x + CELL / 2, HEAD_H / 2 - 7, text=str(c + 1), fill="white", font=("Segoe UI", 9, "bold"))
                cv.create_text(x + CELL / 2, HEAD_H / 2 + 9, text=labels[c], fill="#9db4cf", font=("Segoe UI", 7))
            for r in range(nd):
                y = HEAD_H + r * CELL
                cv.create_rectangle(0, y, HEAD_W, y + CELL, fill="#26405e" if hover["r"] == r else "#1b2c42", outline=theme["BG"])
                cv.create_text(8, y + CELL / 2, anchor="w", text=f"{self.t('ar_ch')} {r + 1}", fill="white", font=("Segoe UI", 9))
                for c in range(ns):
                    x = HEAD_W + c * CELL
                    hot = hover["r"] == r or hover["c"] == c
                    cv.create_rectangle(x, y, x + CELL, y + CELL, fill="#2a2f36" if hot else ("#202328" if (r + c) % 2 else "#1a1d21"), outline=theme["BG"])
                    if [r, c] in ar["map"]:
                        cv.create_oval(x + 7, y + 7, x + CELL - 7, y + CELL - 7, fill=acc, outline="")
            cv.configure(scrollregion=(0, 0, HEAD_W + ns * CELL + 2, HEAD_H + nd * CELL + 2))

        def cell_at(e):
            x, y = cv.canvasx(e.x), cv.canvasy(e.y)
            c, r = int((x - HEAD_W) // CELL), int((y - HEAD_H) // CELL)
            return (r, c) if (0 <= r < ar["n_dst"] and 0 <= c < ar["n_src"]) else (-1, -1)

        def on_click(e):
            r, c = cell_at(e)
            if r < 0:
                return
            if [r, c] in ar["map"]:
                ar["map"].remove([r, c])
            else:
                ar["map"].append([r, c])
            redraw()

        def on_motion(e):
            r, c = cell_at(e)
            x, y = cv.canvasx(e.x), cv.canvasy(e.y)
            if r < 0 and x < HEAD_W:
                r = int((y - HEAD_H) // CELL) if 0 <= (y - HEAD_H) // CELL < ar["n_dst"] else -1
            if (r, c) != (hover["r"], hover["c"]):
                hover["r"], hover["c"] = r, c
                redraw()
        cv.bind("<Button-1>", on_click)
        cv.bind("<Motion>", on_motion)
        cv.bind("<Leave>", lambda _e: (hover.update(r=-1, c=-1), redraw()))

        def identity():
            ar["map"] = [[k, k] for k in range(min(ar["n_src"], ar["n_dst"]))]
            redraw()

        def clear():
            ar["map"] = []
            redraw()

        def spread():                                           # every source channel to the same device channel number, then repeating
            ar["map"] = [[d, d % ar["n_src"]] for d in range(ar["n_dst"])]
            redraw()

        def mono_all():
            ar["map"] = [[d, 0] for d in range(ar["n_dst"])]
            redraw()

        def dst_values():
            pai = pa_find(ar["dev"]) if ar["dev"].startswith("pa|") else None
            if pai:
                top = min(pai[1], PA_MAX_CH)
                return sorted({n for n in AR_COUNTS if n <= top} | {top, ar["n_dst"]} - {0})
            return list(AR_COUNTS)

        def sync_boxes():
            src_cb.current(list(AR_SRC_LAYOUTS).index(ar["n_src"]))
            dst_cb.configure(values=[str(n) for n in dst_values()])
            dst_cb.set(str(ar["n_dst"]))
            label = ar["dev_label"] or (self.t("ar_default_dev") if ar["dev"] == "auto" else ar["dev"])
            dev_lbl.configure(text=self.t("ar_current", dev=label))
            m = out.m
            params = None
            try:
                params = getattr(m, "audio_out_params", None) if m is not None else None
            except Exception:
                params = None
            pa_r = getattr(out, "_pa", None) if ar["dev"].startswith("pa|") else None
            if pa_r is not None:
                info_lbl.configure(text=self.t("ar_pa_live", n=pa_r.nd, r=pa_r.sr, ms=int(pa_r.latency * 1000)))
            elif ar["dev"].startswith("pa|"):
                info_lbl.configure(text=self.t("ar_pa_idle"))
            else:
                info_lbl.configure(text=(self.t("ar_live", n=params.get("channel-count", "?"), r=params.get("samplerate", "?"))
                                         if isinstance(params, dict) else self.t("ar_idle")))

        def counts_changed(_e=None):
            ar["n_src"] = list(AR_SRC_LAYOUTS)[max(0, src_cb.current())]
            try:
                ar["n_dst"] = int(dst_cb.get())
            except ValueError:
                ar["n_dst"] = 2
            ar["map"] = [p for p in ar["map"] if p[0] < ar["n_dst"] and p[1] < ar["n_src"]]
            redraw()
        src_cb.bind("<<ComboboxSelected>>", counts_changed)
        dst_cb.bind("<<ComboboxSelected>>", counts_changed)
        for key, fn in (("ar_identity", identity), ("ar_spread", spread), ("ar_mono_all", mono_all), ("ar_clear", clear)):
            ttk.Button(bar, text=self.t(key), command=fn, takefocus=False).pack(side="left", padx=2)

        def fill_tree():
            tree.delete(*tree.get_children())
            groups = ar_group_devices(devices["list"])
            have = {g[0] for g in groups}
            for api, label, devs in groups:
                node = tree.insert("", "end", iid=f"api|{api}", text=label, open=True)
                for name, desc in devs:
                    mark = "●  " if name == ar["dev"] else "     "
                    tree.insert(node, "end", iid=f"dev|{name}", text=mark + desc)
            if sys.platform == "win32":
                pa_names = {"winmm": "MME", "dsound": "Windows DirectSound", "wasapi": "Windows WASAPI", "wdmks": "Windows WDM-KS", "asio": "ASIO"}
                for api in AR_WIN_APIS:
                    if api not in have and f"pa|{pa_names[api]}" not in have:
                        if api == "asio":
                            why = self.t("ar_asio_nodrv" if sd_mod is not None else "ar_asio_nosd")
                        else:
                            why = self.t("ar_na")
                        tree.insert("", "end", iid=f"na|{api}", text=f"{AR_API_NAMES[api]}  —  {why}")
        devices["list"] = self._ar_device_list(out)
        fill_tree()

        def pick(_e=None):
            sel = tree.selection()
            if not sel or not sel[0].startswith("dev|"):
                return
            name = sel[0][4:]
            desc = next((d for n, d in devices["list"] if n == name), name)
            ar["dev"], ar["dev_label"] = name, ("" if name == "auto" else desc)
            hint = next((v for k, v in pa.items() if k and (k in desc or desc in k)), 0)      # PortAudio knows the channel count of the device
            if name.startswith("pa|"):
                pai = pa_find(name)
                hint = min(pai[1], PA_MAX_CH) if pai else 0
                if hint:
                    ar["n_dst"] = hint
                    ar["map"] = [[k, k] for k in range(min(ar["n_src"], hint))]
                    hint = 0
            elif ar["n_dst"] not in AR_COUNTS:
                ar["n_dst"] = 2
                ar["map"] = [p for p in ar["map"] if p[0] < 2]
            if hint:
                ar["n_dst"] = max([n for n in AR_COUNTS if n <= hint] or [2])
                ar["map"] = [[k, k] for k in range(min(ar["n_src"], ar["n_dst"]))]
            fill_tree()
            sync_boxes()
            redraw()
        tree.bind("<ButtonRelease-1>", pick)
        tree.bind("<Double-Button-1>", pick)
        foot = ttk.Frame(w, padding=(10, 4, 10, 10))
        foot.pack(fill="x")
        ttk.Label(foot, text=self.t("ar_hint"), style="Hint.TLabel", wraplength=640).pack(side="left")

        def refresh_devices():
            devices["list"] = self._ar_device_list(out)
            fill_tree()

        def apply():
            out.ar = ar_clean(json.loads(json.dumps(ar)))
            self._audio_apply(out)
            sync_boxes()
            self.status_t("st_ready_short")
        ttk.Button(foot, text=self.t("btn_close"), command=w.destroy, takefocus=False).pack(side="right", padx=(6, 0))
        ttk.Button(foot, text=self.t("btn_apply"), command=apply, style="Accent.TButton", takefocus=False).pack(side="right", padx=(6, 0))
        ttk.Button(foot, text=self.t("ar_refresh"), command=refresh_devices, takefocus=False).pack(side="right")
        sync_boxes()
        redraw()

    def _check_schedule(self):
        if self._is_backup():
            return
        now = datetime.datetime.now()
        today = now.strftime("%Y-%m-%d")
        secs = now.hour * 3600 + now.minute * 60 + now.second
        for k, it in enumerate(list(self.items)):
            if not it.get("sched_on"):
                continue
            s = it.get("sched")
            if not s or it.get("sched_day") == today:
                continue
            if it.get("sched_dow") and now.weekday() not in it["sched_dow"]:
                continue
            t = parse_clock(s)
            if t is not None and 0 <= secs - t < 10:
                it["sched_day"] = today
                self.play_index(k)

    def _on_tk_error(self, kind, value, tb):
        text = "".join(traceback.format_exception(kind, value, tb))
        diag_log("TK CALLBACK EXCEPTION\n" + text)
        print(text, file=sys.stderr)

    def _health_log(self):
        """Every 5 minutes: memory / handles / GDI objects / threads / queue / what each output is doing.
        If the app ever disappears, the last lines of player_logs/player.log show what was growing."""
        if self._closing:
            return
        try:
            parts = [f"{k}={v}" for k, v in process_stats().items()]
            parts += [f"py_threads={threading.active_count()}", f"uiq={self._uiq.qsize()}", f"items={len(self.items)}"]
            for out in self.outs:
                if out.m is None:
                    continue
                kind = "headless" if out._headless else ("hidden" if getattr(out, "_hidden", False) else "window")
                item = self._cur_item(out)
                bit = [f"out{out.num}[{kind}]"]
                if item is not None:
                    bit.append("live" if self._is_live_stream(item) else "file")
                for label, prop in (("pos", "time_pos"), ("cache_s", "demuxer_cache_duration"),
                                    ("dropped", "frame_drop_count"), ("idle", "core_idle")):
                    try:
                        value = self._prop(out, prop, None)
                        bit.append(f"{label}={round(value, 1) if isinstance(value, float) else value}")
                    except Exception:
                        pass
                if out.pv:
                    try:
                        bit.append(f"monitor_pos={round(float(out.pv.time_pos), 1)}")
                    except Exception:
                        bit.append("monitor_pos=-")
                parts.append(" ".join(bit))
            caps = [(iid, l) for iid, l in self._rtmp.items() if isinstance(l, CaptureSource)]
            for iid, l in caps:
                k = self._find(iid)
                nm = self.items[k].get("name") if k is not None else iid
                parts.append(f"capture[{nm}] {l.state} {l.progress}")
            diag_log("health " + " | ".join(parts))
        except Exception:
            caps = []
        self.root.after(60_000 if caps else 300_000, self._health_log)

    def _drain_uiq(self):
        for _ in range(50):
            try:
                fn = self._uiq.get_nowait()
            except queue.Empty:
                return
            try:
                fn()
            except Exception:
                traceback.print_exc()

    def _tick(self):
        """One pass of the main loop. An exception in any part of it is logged once and the loop goes on (it used to end the loop:
        no progress bars, no tile / monitor updates, nothing, until the app was restarted)."""
        try:
            self._tick_body()
        except SHUTDOWN:
            pass
        except Exception:
            msg = traceback.format_exc()
            seen = self.__dict__.setdefault("_tick_errs", set())
            if msg not in seen and len(seen) < 20:
                seen.add(msg)
                diag_log("ERROR in the main loop (it keeps running):\n" + msg)
        if not self._closing:
            self.root.after(TICK_MS, self._tick)

    def _tick_body(self):
        self._n += 1
        self._drain_uiq()
        if _MPV_LOGQ and self._n % 5 == 0:
            flush_mpv_log()
        for out in self.outs:
            if out.m:
                try:
                    self._tick_output(out)
                except SHUTDOWN:
                    self._output_lost(out)
                except Exception:
                    msg = f"output {out.num}: " + traceback.format_exc()
                    seen = self.__dict__.setdefault("_tick_errs", set())
                    if msg not in seen and len(seen) < 20:
                        seen.add(msg)
                        diag_log("ERROR in the output loop (it keeps running):\n" + msg)
            elif self._n % UI_EVERY == 0:
                self._idle_labels(out)

        if self._n % SCHED_EVERY == 0:
            self._check_schedule()
        if self._n % 10 == 0:
            self._schedule_fade_sync()
        if self._n % 5 == 0:
            for o in self.outs:
                if o.ndi_sender is not None:
                    o.ndi_sender.refresh()                  # fps / canvas follow the output and the file ("Original")
        if self._n % 20 == 0:
            self._ndi_check_senders()
        if self._n % 6 == 0:
            self._cd_tick()
        if self._n % 10 == 5:
            self._feeds_tick()
        if self._n % 60 == 0:
            self._ss_gc()
        self._ss_tick()

        if self._ip_ready():
            if self.ip_playing:
                self._ip_check_out()
            if self._n % (2 if self.ip_playing else UI_EVERY) == 0:
                self._ip_ui()

    def _style(self):
        theme = THEMES[self.theme_name]
        r = self.root
        r.configure(bg=theme["BG"])
        s = ttk.Style()
        s.theme_use("clam")
        s.configure(".", background=theme["BG"], foreground=theme["FG"], fieldbackground=theme["PANEL"], bordercolor=theme["PANEL"],
                    lightcolor=theme["PANEL"], darkcolor=theme["PANEL"], font=("Segoe UI", 9))
        s.configure("TFrame", background=theme["BG"])
        s.configure("Panel.TFrame", background=theme["PANEL"])
        s.configure("TLabel", background=theme["BG"], foreground=theme["FG"])
        s.configure("Panel.TLabel", background=theme["PANEL"], foreground=theme["MUTED"])
        s.configure("PanelBold.TLabel", background=theme["PANEL"], foreground=theme["FG"], font=("Segoe UI", 9, "bold"))
        s.configure("Hint.TLabel", background=theme["BG"], foreground=theme["MUTED"], font=("Segoe UI", 8))
        s.configure("Header.TLabel", background=theme["BG"], foreground=theme["MUTED"], font=("Segoe UI", 8, "bold"))
        s.configure("Info.TLabel", background=theme["BG"], foreground=theme["FG"], font=("Segoe UI", 9))
        s.configure("Trecho.TLabel", background=theme["BG"], foreground=theme["GREEN"], font=("Segoe UI", 8, "bold"))
        s.configure("Mono.TLabel", background=theme["BG"], foreground=theme["FG"], font=("Consolas", 10, "bold"))
        s.configure("Time.TLabel", background=theme["BG"], foreground=theme["FG"], font=("Segoe UI", 11, "bold"))
        s.configure("Rem.TLabel", background=theme["BG"], foreground=theme["ORANGE"], font=("Segoe UI", 11, "bold"))
        s.configure("RemWarn.TLabel", background=theme["BG"], foreground=theme["RED"], font=("Segoe UI", 11, "bold"))
        s.configure("On.TLabel", background=theme["PANEL"], foreground=theme["RED"], font=("Segoe UI", 12))
        s.configure("Off.TLabel", background=theme["PANEL"], foreground=theme["GREEN"], font=("Segoe UI", 12))
        s.configure("TButton", background=theme["PANEL"], foreground=theme["FG"], padding=(8, 4), borderwidth=0)
        s.map("TButton", background=[("active", theme["PICKER_ACTIVE"])])
        s.configure("Accent.TButton", background=theme["ACCENT"], foreground="white", padding=(12, 6))
        s.map("Accent.TButton", background=[("active", "#6aa0ff")])
        s.configure("Green.TButton", background=theme["GREEN"], foreground="white", padding=(12, 6))
        s.map("Green.TButton", background=[("active", "#5edda0")])
        s.configure("Red.TButton", background=theme["RED"], foreground="white", padding=(12, 6))
        s.map("Red.TButton", background=[("active", "#f07068")])
        s.configure("Transport.TButton", font=("Segoe UI Symbol", 13), padding=(12, 6))
        s.configure("Small.TButton", font=("Segoe UI Symbol", 11), padding=(8, 4))
        s.configure("Close.TButton", background=theme["RED"], foreground="white", font=("Segoe UI", 9, "bold"),
                    padding=(6, 3))
        s.map("Close.TButton", background=[("active", "#f07068")])
        s.configure("TCheckbutton", background=theme["BG"], foreground=theme["FG"])
        s.map("TCheckbutton", background=[("active", theme["BG"])])
        s.configure("TCombobox", arrowcolor=theme["FG"], background=theme["PANEL"], foreground=theme["FG"], fieldbackground=theme["PANEL"],
                    selectbackground=theme["PANEL"], selectforeground=theme["FG"], bordercolor=theme["LINE"],
                    lightcolor=theme["PANEL"], darkcolor=theme["PANEL"])
        s.map("TCombobox",
              fieldbackground=[("readonly", "focus", theme["PICKER_BG"]), ("readonly", theme["PANEL"]), ("disabled", theme["PANEL"])],
              foreground=[("readonly", "focus", theme["FG"]), ("readonly", theme["FG"]), ("disabled", theme["MUTED"])],
              selectbackground=[("readonly", "focus", theme["PICKER_BG"]), ("readonly", theme["PANEL"])],
              selectforeground=[("readonly", "focus", theme["FG"]), ("readonly", theme["FG"])],
              background=[("active", theme["PICKER_ACTIVE"]), ("pressed", theme["PICKER_ACTIVE"]), ("readonly", theme["PANEL"])],
              arrowcolor=[("disabled", theme["MUTED"])])
        s.configure("TEntry", insertcolor=theme["FG"], fieldbackground=theme["PANEL"], foreground=theme["FG"])
        s.configure("Picker.TMenubutton", background=theme["PICKER_BG"], foreground=theme["FG"], arrowcolor=theme["FG"],
                    padding=(8, 5), relief="flat", borderwidth=0, anchor="w")
        s.map("Picker.TMenubutton", background=[("active", theme["PICKER_ACTIVE"]), ("pressed", theme["PICKER_ACTIVE"])],
              foreground=[("disabled", theme["MUTED"])])
        s.configure("Horizontal.TScale", background=theme["BG"], troughcolor=theme["PANEL"], sliderlength=20, sliderrelief="raised")
        s.configure("Horizontal.TScrollbar", background=theme["SCROLL_BG"], troughcolor=theme["SCROLL_BG"], bordercolor=theme["SCROLL_BG"], arrowcolor=theme["FG"])
        s.map("Horizontal.TScrollbar", background=[("active", theme["PICKER_ACTIVE"]), ("pressed", theme["PICKER_ACTIVE"])])
        s.configure("Vertical.TScrollbar", background=theme["SCROLL_BG"], troughcolor=theme["SCROLL_BG"], bordercolor=theme["SCROLL_BG"], arrowcolor=theme["FG"])
        s.map("Vertical.TScrollbar", background=[("active", theme["PICKER_ACTIVE"]), ("pressed", theme["PICKER_ACTIVE"])])
        s.configure("Treeview", background=theme["TREE_BG"], fieldbackground=theme["TREE_BG"], foreground=theme["FG"],
                    rowheight=32, borderwidth=0, font=("Segoe UI", 11))
        s.configure("Treeview.Heading", background=theme["TREE_HEAD"], foreground=theme["MUTED"], relief="flat",
                    font=("Segoe UI", 9, "bold"), padding=(6, 6))
        s.map("Treeview.Heading", background=[("active", theme["PICKER_ACTIVE"])])

        def fixed_map(option):
            return [e for e in s.map("Treeview", query_opt=option) if e[:2] != ("!disabled", "!selected")]
        s.map("Treeview", foreground=fixed_map("foreground"), background=fixed_map("background"))

        r.option_add("*TCombobox*Listbox.background", theme["PANEL"])
        r.option_add("*TCombobox*Listbox.foreground", theme["FG"])
        r.option_add("*TCombobox*Listbox.selectBackground", theme["ACCENT"])

    def _btn(self, parent, key, command, style="TButton", **kw):
        return self._tx(ttk.Button(parent, style=style, takefocus=False, command=command, **kw), key)

    def _make_menu(self, parent, **kw):
        theme = THEMES[self.theme_name]
        return tk.Menu(parent, tearoff=0, bg=theme["PANEL"], fg=theme["FG"], activebackground=theme["ACCENT"],
                       activeforeground="white", bd=0, font=("Segoe UI", 10), **kw)

    def _build_output_column(self, parent, out, before=None):
        theme = THEMES[self.theme_name]
        col = ttk.Frame(parent, style="Panel.TFrame", padding=6)
        if before is not None:
            col.pack(side="top", anchor="w", pady=(0, 8), before=before)
        else:
            col.pack(side="top", anchor="w", pady=(0, 8))
        out.col = col
        out.panel = col

        sel = ttk.Frame(col, style="Panel.TFrame")
        sel.pack(fill="x", pady=(0, 4))
        out.name_var = tk.StringVar(value=out.name)
        name_ent = ttk.Entry(sel, textvariable=out.name_var, width=12, justify="center")
        name_ent.pack(side="left", padx=(0, 6))
        name_ent.bind("<Return>", lambda e, o=out: self._commit_output_name(o))
        name_ent.bind("<FocusOut>", lambda e, o=out: self._commit_output_name(o))
        out.pick_var = tk.StringVar(value=self._picker_text(out))
        out.pick_btn = ttk.Menubutton(sel, textvariable=out.pick_var, style="Picker.TMenubutton",
                                      width=15, takefocus=False)
        out.pick_menu = self._make_menu(out.pick_btn)
        out.pick_menu.configure(postcommand=lambda o=out: self._fill_pick_menu(o))
        out.pick_btn["menu"] = out.pick_menu
        out.pick_btn.pack(side="left")
        out.btn = ttk.Button(sel, style="Accent.TButton", takefocus=False, width=8,
                             command=lambda o=out: self.toggle_output(o))
        out.btn.pack(side="left", padx=(6, 0))
        out.dot = ttk.Label(sel, text="●", style="Off.TLabel")
        out.dot.pack(side="left", padx=(6, 0))
        ttk.Button(sel, text="✕", style="Close.TButton", takefocus=False, width=2,
                   command=lambda o=out: self.remove_output(o)).pack(side="left", padx=(8, 0))
        self._set_output_ui(out)

        row = ttk.Frame(col, style="Panel.TFrame")
        row.pack(fill="x")

        left = ttk.Frame(row, style="Panel.TFrame")
        left.pack(side="left")
        out.frame = tk.Frame(left, bg="black", width=PV_W, height=PV_H)
        out.frame.pack()
        out.frame.pack_propagate(False)
        out.label = self._tx(tk.Label(out.frame, bg="black", fg=THEMES[self.theme_name]["MUTED"], font=("Segoe UI", 8)), "mon_title")
        out.label.place(relx=0.5, rely=0.5, anchor="center")

        fmtf = ttk.Frame(left, style="Panel.TFrame")
        fmtf.pack(fill="x", pady=(4, 0))
        res_lbl = self._tx(ttk.Label(fmtf, style="Hint.TLabel"), "res_label")
        res_lbl.configure(background=theme["PANEL"])
        res_lbl.pack(side="left", padx=(0, 4))
        out.res_var = tk.StringVar(value=self.t("opt_original"))
        out.res_cb = ttk.Combobox(fmtf, textvariable=out.res_var,
                                  values=[self.t("opt_original")] + RES_OPTIONS[1:],
                                  state="normal", width=10)
        out.res_cb.pack(side="left", padx=(0, 6))
        out.res_cb.bind("<<ComboboxSelected>>", lambda e, o=out: self._on_format_change(o))
        out.res_cb.bind("<Return>", lambda e, o=out: self._on_format_change(o))
        out.res_cb.bind("<FocusOut>", lambda e, o=out: self._on_format_change(o))
        fps_lbl = self._tx(ttk.Label(fmtf, style="Hint.TLabel"), "fps_label")
        fps_lbl.configure(background=theme["PANEL"])
        fps_lbl.pack(side="left", padx=(0, 4))
        out.fps_var = tk.StringVar(value=self.t("opt_original"))
        out.fps_cb = ttk.Combobox(fmtf, textvariable=out.fps_var,
                                  values=[self.t("opt_original")] + FPS_OPTIONS[1:],
                                  state="normal", width=7)
        out.fps_cb.pack(side="left")
        out.fps_cb.bind("<<ComboboxSelected>>", lambda e, o=out: self._on_format_change(o))
        out.fps_cb.bind("<Return>", lambda e, o=out: self._on_format_change(o))
        out.fps_cb.bind("<FocusOut>", lambda e, o=out: self._on_format_change(o))
        q_lbl = self._tx(ttk.Label(fmtf, style="Hint.TLabel"), "quality_label")
        q_lbl.configure(background=theme["PANEL"])
        q_lbl.pack(side="left", padx=(8, 4))
        out.q_var = tk.StringVar(value="1")
        out.q_cb = ttk.Combobox(fmtf, textvariable=out.q_var, values=list(self.NDI_QUALITIES), state="readonly", width=4)
        out.q_cb.pack(side="left")
        out.q_cb.bind("<<ComboboxSelected>>", lambda e, o=out: self._on_ndi_quality(o))
        for cb in (out.res_cb, out.fps_cb, out.q_cb):
            self._wheel_to_panel(cb)

        nm_box = ttk.Frame(left, style="Panel.TFrame", width=PV_W, height=20)       # fixed size: a long name never resizes the panel
        nm_box.pack(fill="x", pady=(3, 0), before=fmtf)             # between the monitor and the resolution / fps / quality row
        nm_box.pack_propagate(False)
        out.name_lbl = ttk.Label(nm_box, text="", style="Panel.TLabel", anchor="center", font=("Segoe UI", 9, "bold"))
        out.name_lbl.configure(foreground=theme["FG"])
        out.name_lbl.pack(fill="both", expand=True)
        out._nm_shown = None
        out.timeline = Timeline(left, lambda t, f, o=out: self._out_tl_seek(o, t, f), lambda w, t, f, o=out: self._out_tl_marker(o, w, t, f), self, show_markers=False, windowed=True)
        out.timeline.pack(fill="x", pady=(4, 0))

        self._update_output_frame_aspect(out)

        trow = ttk.Frame(left, style="Panel.TFrame")
        trow.pack(pady=(2, 0))
        out.time_lbl = ttk.Label(trow, text="00:00 / 00:00", style="Time.TLabel")
        out.time_lbl.configure(background=theme["PANEL"])
        out.time_lbl.pack(side="left")
        out.rem_lbl = ttk.Label(trow, text="−00:00", style="Rem.TLabel")
        out.rem_lbl.configure(background=theme["PANEL"])
        out.rem_lbl.pack(side="left", padx=(8, 0))

        btns = ttk.Frame(left, style="Panel.TFrame")
        btns.pack(pady=(4, 0))
        for text, cmd in (("⏮", lambda o=out: self.step(o, -1)), ("▶", lambda o=out: self.toggle_pause(o)),
                          ("⏹", lambda o=out: self.stop(o)), ("⏭", lambda o=out: self.step(o, 1))):
            b = ttk.Button(btns, text=text, style="Small.TButton", takefocus=False, command=cmd, width=3)
            b.pack(side="left", padx=1)
            if text == "▶":
                out.play_btn = b

        out.preview_on = tk.BooleanVar(value=True)
        chkrow = ttk.Frame(left, style="Panel.TFrame")
        chkrow.pack(pady=(2, 0))
        chk = self._tx(ttk.Checkbutton(chkrow, variable=out.preview_on, takefocus=False,
                                 command=lambda o=out: self.toggle_preview(o)), "mon_enabled")
        chk.configure(style="TCheckbutton")
        chk.pack(side="left")
        out.ndi_var = tk.BooleanVar(value=False)
        out.ndi_btn = self._tx(ttk.Checkbutton(chkrow, variable=out.ndi_var, takefocus=False,
                                               command=lambda o=out: self._toggle_ndi_out(o)), "btn_ndi_out")
        out.ndi_btn.configure(style="TCheckbutton")
        out.ndi_btn.pack(side="left", padx=(10, 0))
        out.big_btn = ttk.Button(chkrow, text="⛶", style="Small.TButton", takefocus=False, width=3,
                                 command=lambda o=out: self.open_big_monitor(o))
        out.big_btn.pack(side="left", padx=(10, 0))

        volf = ttk.Frame(row, style="Panel.TFrame")
        volf.pack(side="left", padx=(8, 0), fill="y")
        vol_lbl = self._tx(ttk.Label(volf, style="Hint.TLabel"), "out_vol_lbl")
        vol_lbl.configure(background=theme["PANEL"])
        vol_lbl.pack()
        vol_scale = ttk.Scale(volf, from_=100, to=0, variable=out.vol, orient="vertical",
                              length=PV_H, takefocus=False,
                              command=lambda v, o=out: self._on_output_volume(o, v))
        vol_scale.pack()
        vol_val_lbl = ttk.Label(volf, textvariable=out.vol, style="Hint.TLabel")
        vol_val_lbl.configure(background=theme["PANEL"])
        vol_val_lbl.pack()

        out.out_mute = tk.BooleanVar(value=False)
        mute_btn = self._tx(ttk.Checkbutton(volf, variable=out.out_mute, takefocus=False,
                                   command=lambda o=out: self._toggle_output_mute(o)), "out_mute_lbl")
        mute_btn.pack(pady=(6, 0))

        self._btn(volf, "btn_audio_routing", lambda o=out: self._open_audio_routing(o), "Small.TButton").pack(pady=(4, 0))

    def _commit_output_name(self, out):
        if out not in self.outs:
            return
        name = out.name_var.get().strip() or f"Output {out.num}"
        if name != out.name:
            out.name = name
            out.name_var.set(name)
            self._ndi_out_rename(out)
            self._refresh_all()

    def _build_outputs_panel(self, body):
        theme = THEMES[self.theme_name]
        self.out_panel = ttk.Frame(body, style="Panel.TFrame", padding=(8, 6, 8, 6))
        self.out_panel.pack(side="right", fill="y")
        head = ttk.Frame(self.out_panel, style="Panel.TFrame")
        head.pack(side="top", anchor="w", pady=(0, 6))
        self._btn(head, "add_output", self.add_output, "Green.TButton").pack(side="left", padx=(0, 6))
        self._btn(head, "identify", self.identify, "Accent.TButton").pack(side="left", padx=(0, 6))
        self.mv_open_btn = self._btn(head, "btn_multiview", self.open_multiview, "Accent.TButton")
        self.mv_open_btn.pack(side="left", padx=(0, 6))
        self.co_open_btn = self._btn(head, "btn_composite", self.open_composite, "Accent.TButton")
        self.co_open_btn.pack(side="left")
        
        foot = ttk.Frame(self.out_panel, style="Panel.TFrame")
        foot.pack(side="bottom", anchor="center", pady=(8, 0))
        self._btn(foot, "btn_play", self.play_all, style="Green.TButton").pack(side="left", padx=(0, 6))
        self._btn(foot, "btn_stop", self.stop_all, style="Red.TButton").pack(side="left")

        wrap = ttk.Frame(self.out_panel, style="Panel.TFrame")
        wrap.pack(side="top", fill="both", expand=True)
        self.out_wrap = wrap
        sb = ttk.Scrollbar(wrap, orient="vertical", style="Vertical.TScrollbar")
        sb.pack(side="right", fill="y")
        self.out_sb = sb
        self.out_canvas = tk.Canvas(wrap, bg=theme["PANEL"], highlightthickness=0, width=OUT_PANEL_W,
                                    yscrollincrement=20, yscrollcommand=sb.set)
        self.out_canvas.pack(side="left", fill="both", expand=False)
        sb.configure(command=self.out_canvas.yview)
        self.out_inner = ttk.Frame(self.out_canvas, style="Panel.TFrame")
        self.out_win = self.out_canvas.create_window((0, 0), window=self.out_inner, anchor="nw")
        self.out_inner.bind("<Configure>", self._on_outs_configure)
        self.outs_empty_lbl = self._tx(ttk.Label(self.out_inner, style="Hint.TLabel", justify="center"),
                                       "outs_empty")
        self.outs_empty_lbl.configure(background=theme["PANEL"])
        self.outs_empty_lbl.pack(pady=40, padx=10)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.root.bind_all(seq, self._on_outs_wheel, add="+")

    def _on_outs_configure(self, _e=None):
        c = self.out_canvas
        c.configure(scrollregion=c.bbox("all"))
        need = max(OUT_PANEL_W, self.out_inner.winfo_reqwidth())
        if int(c.cget("width")) != need:
            c.configure(width=need)
        c.itemconfigure(self.out_win, width=need)
        try:
            # the panel keeps a fixed width: resizing the window never re-flows the outputs
            w = need + self.out_sb.winfo_reqwidth() + 16 + 4
            if self.__dict__.get("_out_panel_w") != w:
                self._out_panel_w = w
                self.out_panel.configure(width=w)
                self.out_panel.pack_propagate(False)
        except tk.TclError:
            pass

    def _over_outs(self, e):
        try:
            w = self.root.winfo_containing(e.x_root, e.y_root)
        except Exception:
            return False
        return w is not None and str(w).startswith(str(self.out_wrap))

    def _on_outs_wheel(self, e):
        if not self._over_outs(e):
            return None
        c = self.out_canvas
        if c.yview() != (0.0, 1.0):
            num_ = getattr(e, "num", None)
            if num_ == 4:
                step = -1
            elif num_ == 5:
                step = 1
            else:
                step = -1 if e.delta > 0 else 1
            c.yview_scroll(step * 3, "units")
        return "break"

    def _wheel_to_panel(self, widget):
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            widget.bind(seq, self._on_outs_wheel)

    def _next_output_num(self):
        used = {o.num for o in self.outs}
        n = 1
        while n in used:
            n += 1
        return n

    def add_output(self, num=None, name=None, res=None, fps=None, vol=None, ndi=False, q=None, screen="auto", audio=None):
        if num is None or any(o.num == num for o in self.outs):
            num = self._next_output_num()
        out = Output(num, name)
        if audio is not None:
            out.ar = ar_clean(audio)
        if screen == "auto":
            free = self._free_screen_for(out)
            out.sel_geo = geo(self.monitors[free]) if free is not None else None
        else:
            out.sel_geo = screen                            # restored from a show: its saved screen, or none
        later = [o for o in self.outs if o.num > num]
        before = min(later, key=lambda o: o.num).col if later else None
        self.outs.append(out)
        self.outs.sort(key=lambda o: o.num)
        self.outs_empty_lbl.pack_forget()
        self._build_output_column(self.out_inner, out, before=before)
        if res:
            out.res_var.set(res)
        if fps:
            out.fps_var.set(fps)
        if vol is not None:
            out.vol.set(int(round(vol)))
        if q:
            out.q_var.set(q)
        if ndi:
            self._ndi_out_start(out, silent=True)
        self._update_output_frame_aspect(out)
        self._update_pickers()
        self._refresh_all()
        self._check_overlap()
        return out

    def remove_output(self, out, ask=True):
        if out not in self.outs:
            return
        theme = THEMES[self.theme_name]
        if ask and out.m is not None and not messagebox.askyesno(
                self.t("dlg_remove_out"), self.t("ask_remove_out", name=out.name)):
            return
        self._pa_stop(out)
        self._ndi_out_stop(out)
        self._close_big(out, restart=False)
        self.stop_preview(out)
        if out.m:
            old, out.m = out.m, None
            try:
                mpv_terminate(old)
            except Exception:
                pass
        out.index = -1
        self.outs.remove(out)
        col, out.col = out.col, None
        if col is not None:
            prefix = str(col)
            self._i18n = [e for e in self._i18n
                          if str(e[0]) != prefix and not str(e[0]).startswith(prefix + ".")]
            col.destroy()
        if not self.outs:
            self.outs_empty_lbl.pack(pady=40, padx=10)
        self._highlight()
        self._refresh_all()
        self._check_overlap()
        self.status_t("st_out_removed", name=out.name)

    @staticmethod
    def _clean_fmt(value):
        if not isinstance(value, str):
            return None
        v = value.strip()[:24]
        return None if (not v or v.lower() == "original") else v

    def _outputs_data(self):
        data = []
        for o in self.outs:
            data.append({"num": o.num, "name": o.name,
                         "resolution": self._clean_fmt(o.res_var.get()) or "",
                         "fps": self._clean_fmt(o.fps_var.get()) or "",
                         "volume": int(round(float(o.vol.get()))),
                         "ndi": o.ndi_sender is not None, "audio": getattr(o, "ar", None) or ar_default(),
                         "ndi_quality": o.q_var.get() if hasattr(o, "q_var") else "1",
                         "screen": list(o.sel_geo) if o.sel_geo is not None else None})
        return data

    def _load_outputs(self, data, items):
        spec = data.get("outputs")
        if isinstance(spec, list):
            for o in list(self.outs):
                self.remove_output(o, ask=False)
            seen = set()
            for od in spec:
                if not isinstance(od, dict):
                    continue
                n = od.get("num")
                if not isinstance(n, int) or isinstance(n, bool) or n < 1 or n in seen:
                    continue
                seen.add(n)
                name = od.get("name") if isinstance(od.get("name"), str) and od["name"].strip() else None
                screen = "auto"                                 # shows saved before the screen was stored: first free screen, as before
                if "screen" in od:
                    g = od["screen"]
                    valid = isinstance(g, list) and len(g) == 4 and all(isinstance(v, int) and not isinstance(v, bool) for v in g)
                    taken = {o.sel_geo for o in self.outs if o.sel_geo is not None}
                    if self._mv_geo() is not None:
                        taken.add(self._mv_geo())
                    if self._co_screen() is not None:
                        taken.add(self._co_screen())
                    self.refresh_monitors()
                    screen = tuple(g) if (valid and tuple(g) in {geo(mo) for mo in self.monitors} and tuple(g) not in taken) else None
                self.add_output(num=n, name=name[:40] if name else None, screen=screen,
                                res=self._clean_fmt(od.get("resolution")), fps=self._clean_fmt(od.get("fps")),
                                vol=num(od.get("volume"), None, 0, 100), ndi=od.get("ndi") is True, audio=od.get("audio"),
                                q=od.get("ndi_quality") if od.get("ndi_quality") in self.NDI_QUALITIES else None)
        else:
            for n in sorted({it["output"] for it in items if not it.get("is_group")}):
                if not any(o.num == n for o in self.outs):
                    self.add_output(num=n)

    def _out_name(self, num):
        for o in self.outs:
            if o.num == num:
                return o.name
        return f"Output {num}"

    def _build(self):
        theme = THEMES[self.theme_name]
        r = self.root
        r.geometry("1320x780")
        r.minsize(1200, 720)
        r.bind_all("<Control-Shift-G>", lambda e: self.run_gdi_probe())
        r.bind_all("<Control-Shift-g>", lambda e: self.run_gdi_probe())
        r.bind("<Configure>", lambda e: setattr(self, "_resize_t", time.time()) if e.widget is r else None, add="+")

        top = ttk.Frame(r, style="Panel.TFrame", padding=(8, 6))
        top.pack(side="top", fill="x")
        name_lbl = self._tx(ttk.Label(top, style="PanelBold.TLabel", font=("Segoe UI", 11, "bold")), "credit")
        name_lbl.pack(side="left", padx=(0, 6))
        self.sync_box = ttk.Frame(top, style="Panel.TFrame")
        self.sync_box.pack(side="left", padx=(0, 6))
        self.sync_btn = self._btn(self.sync_box, "btn_sync", self.open_sync)
        self.sync_btn.pack(side="top", fill="x")
        self.sync_bar = ttk.Progressbar(self.sync_box, mode="determinate", maximum=100, length=60)
        self._btn(top, "btn_logins", self.open_logins).pack(side="left", padx=(0, 10))
        self.root.after(2500, self._sync_autostart)
        self.acc_bar = ttk.Frame(top, style="Panel.TFrame")
        self.acc_bar.pack(side="left")
        self._refresh_account_bar()
        
        self.lang_combo = ttk.Combobox(top, values=[name for _c, name in LANGS], state="readonly", width=10)
        self.lang_combo.current([c for c, _n in LANGS].index(self.lang))
        self.lang_combo.pack(side="right", padx=(4, 0))
        self.lang_combo.bind("<<ComboboxSelected>>", self.on_lang_change)
        
        self.theme_btn = ttk.Button(top, text=self.t("opt_theme_light" if self.theme_name == "light" else "opt_theme_dark"), command=self.toggle_theme, takefocus=False)
        self.theme_btn.pack(side="right", padx=(4, 4))
        self.kb_locked = False
        self.lock_btn = ttk.Button(top, text=self.t("btn_lock_kb"), command=self.toggle_kb_lock, takefocus=False)
        self.lock_btn.pack(side="right", padx=(4, 4))

        self._btn(top, "pl_save_as", lambda: self.save_playlist(True)).pack(side="right", padx=(0, 4))
        self._btn(top, "pl_save", self.save_playlist).pack(side="right", padx=(0, 4))
        self._btn(top, "pl_open", self.open_playlist).pack(side="right", padx=(0, 4))

        body = ttk.Frame(r)
        body.pack(side="top", fill="both", expand=True)

        self._build_outputs_panel(body)

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)

        mid = ttk.Frame(left, padding=(8, 6, 8, 0))
        mid.pack(side="top", fill="both", expand=True)
        bar = ttk.Frame(mid)
        bar.pack(fill="x", pady=(0, 4))
        info_btn = ttk.Button(bar, text="ℹ", style="Small.TButton", width=3, takefocus=False,
                              command=self._show_hint)
        info_btn.pack(side="left", padx=(0, 6))
        self.import_btn = self._btn(bar, "btn_import", self._open_import_menu)
        self.import_btn.pack(side="left", padx=(0, 4))
        self._btn(bar, "btn_group", self.add_group).pack(side="left", padx=(0, 4))
        for key, cmd in (("btn_remove", self.remove_selected), ("btn_clear", self.clear_list_ask)):
            self._btn(bar, key, cmd).pack(side="left", padx=(0, 4))
        self._btn(bar, "btn_down", lambda: self.move_selected(1)).pack(side="right")
        self._btn(bar, "btn_up", lambda: self.move_selected(-1)).pack(side="right", padx=(0, 4))

        holder = ttk.Frame(mid)
        holder.pack(fill="both", expand=True)
        holder.grid_rowconfigure(0, weight=1)
        holder.grid_columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(holder, columns=[c[0] for c in COLUMNS], show="tree headings",
                                 selectmode="none", takefocus=False, height=8)
        self.tree.heading("#0", text="")
        self.tree.column("#0", width=34, minwidth=34, stretch=False, anchor="center")          # the YouTube / Spotify badge
        for cid, width in COLUMNS:
            self.tree.heading(cid, text=self.t("col_" + cid))
            self.tree.column(cid, width=width, minwidth=COL_MIN_W, stretch=False,
                             anchor="w" if cid == "nome" else "center")
        self.tree.tag_configure("playing", background=theme["ROW_PLAYING"], foreground=theme["FG"])
        self.tree.tag_configure("selected", background=theme["ROW_SELECTED"], foreground=theme["FG"])
        self.tree.tag_configure("group", foreground=theme["ACCENT"])

        vsb = ttk.Scrollbar(holder, orient="vertical", command=self.tree.yview, style="Vertical.TScrollbar")
        hsb = ttk.Scrollbar(holder, orient="horizontal", command=self.tree.xview, style="Horizontal.TScrollbar")
        self.tree.configure(
            yscrollcommand=lambda a, b: (vsb.set(a, b), self._schedule_fade_sync()),
            xscrollcommand=lambda a, b: (hsb.set(a, b), self._schedule_fade_sync()))
        self.tree.bind("<Configure>", lambda _e: self._schedule_fade_sync(), add="+")

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-Button-1>", self._on_tree_double)
        self.tree.bind("<B1-Motion>", self._on_tree_motion)
        self.tree.bind("<ButtonRelease-1>", self._on_tree_release)
        self.tree.bind("<ButtonPress-1>", self._on_heading_press, add="+")
        self.tree.bind("<B1-Motion>", self._on_heading_motion, add="+")
        self.tree.bind("<ButtonRelease-1>", self._on_heading_release, add="+")
        for seq in ("<Button-3>",) + (("<Button-2>", "<Control-Button-1>") if sys.platform == "darwin" else ()):
            self.tree.bind(seq, self._on_tree_context)
        self.drop_line = tk.Frame(self.tree, bg=theme["FG"])

        row = ttk.Frame(left, padding=(8, 4, 8, 2))
        row.pack(side="top", fill="x")
        self.mute = tk.BooleanVar(value=False)
        self._tx(ttk.Checkbutton(row, variable=self.mute, takefocus=False, command=self.on_mute),
                 "opt_mute").pack(side="right", padx=8)
        self.blackout = tk.BooleanVar(value=False)
        self._tx(ttk.Checkbutton(row, variable=self.blackout, takefocus=False,
                                 command=self.toggle_blackout), "opt_blackout").pack(side="right", padx=8)
        self.shuffle_var = tk.BooleanVar(value=False)
        self.shuffle_mode = tk.StringVar(value="off")
        self.shuffle_output = tk.IntVar(value=1)
        self.shuffle_btn = self._btn(row, "opt_shuffle", self.toggle_shuffle_btn)
        self.shuffle_btn.pack(side="right", padx=8)

        bottom = ttk.Frame(left, padding=(8, 2, 8, 2))
        bottom.pack(side="top", fill="x")
        pcol = ttk.Frame(bottom)
        pcol.pack(side="left", anchor="n", fill="x", expand=True)
        
        prev_row = ttk.Frame(pcol)
        prev_row.pack(fill="x", expand=True)
        self.ip_frame = tk.Frame(prev_row, bg="black", width=IP_W, height=IP_H)
        self.ip_frame.pack(side="left")
        self.ip_frame.pack_propagate(False)
        self.ip_label = self._tx(tk.Label(self.ip_frame, bg="black", fg=theme["MUTED"], font=("Segoe UI", 8)),
                                 "ip_placeholder")
        self.ip_label.place(relx=0.5, rely=0.5, anchor="center")

        icol = ttk.Frame(prev_row, padding=(8, 0, 0, 0), height=INFO_H)
        icol.pack(side="left", anchor="n", fill="both", expand=True)
        icol.pack_propagate(False)                      # fixed height: a longer / shorter description never moves the rest of the window
        icol.bind("<Configure>", lambda e: self.info_lbl.configure(wraplength=max(120, e.width - 16)), add="+")
        self.trecho_lbl = ttk.Label(icol, text="", style="Trecho.TLabel", justify="left")
        self.trecho_lbl.pack(anchor="w")
        self.info_lbl = ttk.Label(icol, text=self.t("info_select"), style="Info.TLabel",
                                  justify="left")
        self.info_lbl.pack(anchor="w", pady=(2, 0), fill="x", expand=True)
        self.aspect_lbl = ttk.Label(icol, text="", style="Hint.TLabel")
        self.aspect_lbl.pack(anchor="w", pady=(2, 0))

        self.timeline = Timeline(pcol, self._tl_seek, self._tl_marker, self, show_markers=True)
        self.timeline.pack(fill="x", pady=(4, 0))
        self.ip_time = ttk.Label(pcol, text="0:00.000 / 0:00.000", style="Mono.TLabel", anchor="center")
        self.ip_time.pack(fill="x")

        tr = ttk.Frame(pcol)
        tr.pack(pady=(2, 0), fill="x")
        self.ip_play_btn = ttk.Button(tr, text="▶", style="Small.TButton", takefocus=False,
                                      command=self.ip_toggle, width=3)
        for text, cmd in (("⇤", lambda: self.ip_goto("in")), ("◀|", lambda: self.ip_step(-1))):
            ttk.Button(tr, text=text, style="Small.TButton", takefocus=False, command=cmd,
                       width=3).pack(side="left", padx=1)
        self.ip_play_btn.pack(side="left", padx=1)
        for text, cmd in (("⏹", self.ip_stop), ("|▶", lambda: self.ip_step(1)),
                          ("⇥", lambda: self.ip_goto("out"))):
            ttk.Button(tr, text=text, style="Small.TButton", takefocus=False, command=cmd,
                       width=3).pack(side="left", padx=1)
        self._btn(tr, "btn_mark_in", self.mark_in, "Small.TButton").pack(side="left", padx=(6, 1))
        self._btn(tr, "btn_mark_out", self.mark_out, "Small.TButton").pack(side="left", padx=1)
        self._btn(tr, "btn_marks_clear", self.clear_marks, "Small.TButton").pack(side="left", padx=1)

        self.next_menu = self._make_menu(r)
        self.out_menu = self._make_menu(r)
        self.sim_menu = self._make_menu(r)
        self.ctx_menu = self._make_menu(r)
        self.ctx_aspect = self._make_menu(self.ctx_menu)
        self.ctx_output = self._make_menu(self.ctx_menu)

        self.status_t("st_ready")

    def run_gdi_probe(self):
        """Ctrl+Shift+G: runs a few UI operations 200 times each and reports how many GDI objects each one leaves behind
        (a result of 0 = clean). The numbers go to the log and to a dialog."""
        if sys.platform != "win32":
            return
        theme = THEMES[self.theme_name]
        w = tk.Toplevel(self.root)
        w.title("GDI probe")
        w.geometry("360x200+60+60")
        box = ttk.Frame(w)
        box.pack(fill="both", expand=True)
        tl = Timeline(box, lambda *a: None, lambda *a: None, self, show_markers=True)
        tl.pack(fill="x")
        tl.set(dur=100.0, pos=1.0, t_in=0.0, t_out=None)
        tlab, ulab = ttk.Label(box, text="x", style="Time.TLabel"), tk.Label(box, text="x")
        tlab.pack()
        ulab.pack()
        w.update()
        results = []

        def run(name, fn, n=200):
            for _ in range(5):
                fn(0)
            w.update()
            before = gdi_count()
            for i in range(n):
                fn(i)
                if i % 10 == 0:
                    w.update_idletasks()
            w.update()
            results.append((name, gdi_count() - before))

        run("screeninfo.get_monitors()", lambda i: list(get_monitors()), 100)
        run("native_monitors()", lambda i: native_monitors(), 100)
        run("ttk.Label text", lambda i: tlab.configure(text=f"00:{i % 60:02d} / 01:00"))
        run("ttk.Label style", lambda i: tlab.configure(style="RemWarn.TLabel" if i % 2 else "Rem.TLabel"))
        run("tk.Label text", lambda i: ulab.configure(text=str(i)))
        run("Timeline redraw", lambda i: tl.set(pos=float(i % 100)))
        run("place / place_forget", lambda i: (ulab.place(x=5, y=5), ulab.place_forget()) if True else None)
        w.destroy()
        line = " | ".join(f"{n}: {d:+d}" for n, d in results)
        diag_log("GDI probe (objects left behind per 200 runs): " + line)
        messagebox.showinfo("GDI probe", "\n".join(f"{n}: {d:+d}" for n, d in results) + "\n\n(0 = clean)", parent=self.root)

    def _show_hint(self):
        theme = THEMES[self.theme_name]
        w = tk.Toplevel(self.root)
        w.title(self.t("app_title"))
        w.geometry("700x320")
        w.transient(self.root)
        txt = tk.Text(w, wrap="word", bg=theme["PANEL"], fg=theme["FG"], font=("Segoe UI", 9), padx=12, pady=12,
                      relief="flat", borderwidth=0)
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", self.t("hint"))
        txt.configure(state="disabled")
        ttk.Button(w, text="OK", command=w.destroy).pack(pady=8)

    def _bind_keys(self):
        r = self.root
        def bind(keys, fn):
            def handler(e, f=fn):
                if getattr(self, "kb_locked", False):
                    return "break"                    # keyboard locked: shortcuts do nothing
                if isinstance(e.widget, (tk.Entry, tk.Listbox)):
                    return None
                f()
                return "break"
            for k in keys:
                r.bind_all(k, handler)

        mod = "Command" if sys.platform == "darwin" else "Control"
        bind([f"<{mod}-s>"], self.save_playlist)
        bind([f"<{mod}-o>"], self.open_playlist)
        bind([f"<{mod}-d>"], self.duplicate_selected)
        bind(["<space>"], self.toggle_pause_all)
        bind(["<Return>"], self._play_selected)
        bind(["<Delete>"], self.remove_selected)
        bind([f"<{mod}-a>", f"<{mod}-A>"], self.select_all)
        bind(["<Alt-Up>"], lambda: self.move_selected(-1))
        bind(["<Alt-Down>"], lambda: self.move_selected(1))
        bind(["<Shift-Up>"], lambda: self._extend_selection(-1))
        bind(["<Shift-Down>"], lambda: self._extend_selection(1))
        bind(["<Up>"], lambda: self._move_selection(-1))
        bind(["<Down>"], lambda: self._move_selection(1))
        bind(["<Left>"], lambda: self._key_call(self.seek_rel, -5))
        bind(["<Right>"], lambda: self._key_call(self.seek_rel, 5))
        bind(["<Shift-Left>"], lambda: self._key_call(self.seek_rel, -30))
        bind(["<Shift-Right>"], lambda: self._key_call(self.seek_rel, 30))
        bind(["<Prior>", "<p>"], lambda: self._key_call(self.step, -1))
        bind(["<Next>", "<n>"], lambda: self._key_call(self.step, 1))
        bind(["<f>"], lambda: self._key_call(self.toggle_output_fullscreen))
        bind(["<s>"], self.stop_all)
        bind(["<m>"], lambda: (self.mute.set(not self.mute.get()), self.on_mute()))
        bind(["<b>"], lambda: (self.blackout.set(not self.blackout.get()), self.toggle_blackout()))
        bind(["<i>"], self.mark_in)
        bind(["<o>"], self.mark_out)
        bind(["<comma>"], lambda: self.ip_step(-1))
        bind(["<period>"], lambda: self.ip_step(1))

    def quit(self):
        """Close at once: the window disappears immediately and everything is torn down behind it, with a
        hard time limit so no mpv / NDI / ffmpeg call can keep the process hanging."""
        if self._closing:
            return
        self._closing = True
        try:
            self._sync_stop(quiet=True)
        except Exception:
            pass
        diag_log("quit() called from:\n" + "".join(traceback.format_stack(limit=8)))
        try:
            self.root.withdraw()
        except Exception:
            pass
        outs, ip = list(self.outs), self.ip
        self.ip = None
        tfeeds = list(self.__dict__.get("_tf_feeds", []))

        def cleanup():                      # no Tk calls in here (a worker thread must never wait for the Tk thread)
            for f in tfeeds:
                f.stop()
            for out in outs:
                sender, out.ndi_sender = out.ndi_sender, None
                if sender is not None:
                    sender.stop()
            self._rtmp_stop_all()
            # the mpv instances are NOT terminated one by one: libmpv's teardown crashed (access violation) on exit;
            # os._exit below releases them together with the process

        worker = threading.Thread(target=cleanup, daemon=True)
        worker.start()
        worker.join(4.0)
        try:
            self.root.destroy()
        except Exception:
            pass
        try:
            if _LOG.get("state"):
                with open(_LOG["state"], "w", encoding="utf-8") as fh:
                    fh.write("clean exit")
        except Exception:
            pass
        os._exit(0)

if __name__ == "__main__":
    setup_diagnostics()
    root = tk.Tk()
    App(root)
    root.mainloop()
