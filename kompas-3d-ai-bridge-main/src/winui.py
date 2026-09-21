from __future__ import annotations
import ctypes, csv, io, subprocess, time
from ctypes import wintypes

from dialogs import DIALOG_CLASS

USER32 = ctypes.windll.user32


def kompas_pids():
    """Legacy process discovery. Kept only as a final UI fallback."""
    raw = subprocess.check_output(
        ["tasklist", "/FI", "IMAGENAME eq KOMPAS.exe", "/FO", "CSV", "/NH"],
        text=True, encoding="oem", errors="ignore"
    )
    return {int(row[1]) for row in csv.reader(io.StringIO(raw)) if len(row) > 1 and row[1].isdigit()}


def window_info(hwnd: int, source: str = "win32"):
    hwnd = int(hwnd or 0)
    if not hwnd or not USER32.IsWindow(wintypes.HWND(hwnd)):
        raise RuntimeError(f"invalid_window_handle: {hwnd}")
    r = wintypes.RECT()
    if not USER32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r)):
        raise RuntimeError(f"GetWindowRect_failed: hwnd={hwnd}")
    pid = wintypes.DWORD()
    USER32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    width, height = int(r.right-r.left), int(r.bottom-r.top)
    return {
        "hwnd": hwnd,
        "pid": int(pid.value),
        "rect": [int(r.left), int(r.top), int(r.right), int(r.bottom)],
        "width": width,
        "height": height,
        "visible": bool(USER32.IsWindowVisible(wintypes.HWND(hwnd))),
        "minimized": bool(USER32.IsIconic(wintypes.HWND(hwnd))),
        "source": source,
    }


def main_window_handle(session):
    """Use KOMPAS API7 as the authoritative source of the application HWND."""
    app = session.connect()
    errors = []
    for name in ("MainWindowHandle",):
        try:
            value = int(getattr(app, name))
            if value and USER32.IsWindow(wintypes.HWND(value)):
                return value
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    for name in ("GetMainWindowHandle",):
        try:
            value = int(getattr(app, name)())
            if value and USER32.IsWindow(wintypes.HWND(value)):
                return value
        except Exception as exc:
            errors.append(f"{name}(): {exc}")
    raise RuntimeError("KOMPAS_API7_main_window_handle_unavailable: " + " | ".join(errors))


def document_frame_windows(session):
    """
    Get active-document windows through API7 IKompasDocument.DocumentFrames.
    KOMPAS collections are zero-based. We return only real Win32 HWNDs.
    """
    doc = session.active()
    if doc is None:
        raise RuntimeError("no_active_document")
    frames = getattr(doc, "DocumentFrames", None)
    if frames is None:
        try:
            frames = doc.GetDocumentFrames()
        except Exception as exc:
            raise RuntimeError(f"DocumentFrames_unavailable: {exc}")

    count = None
    for getter in (
        lambda: int(frames.Count),
        lambda: int(frames.GetCount()),
    ):
        try:
            count = getter()
            break
        except Exception:
            pass
    if count is None:
        count = 1
    if count < 1:
        raise RuntimeError("active_document_has_no_frames")

    rows = []
    errors = []
    for i in range(count):
        frame = None
        for getter in (
            lambda i=i: frames.Item(i),
            lambda i=i: frames.GetItem(i),
        ):
            try:
                frame = getter()
                break
            except Exception as exc:
                errors.append(f"frame[{i}]: {exc}")
        if frame is None:
            continue
        hwnd = 0
        for getter in (
            lambda frame=frame: int(frame.GetHWND()),
            lambda frame=frame: int(frame.HWND),
        ):
            try:
                hwnd = getter()
                if hwnd:
                    break
            except Exception:
                pass
        if not hwnd:
            continue
        try:
            info = window_info(hwnd, f"API7.DocumentFrames[{i}].GetHWND")
            info["frame_index"] = i
            rows.append(info)
        except Exception as exc:
            errors.append(f"frame[{i}] hwnd={hwnd}: {exc}")

    if not rows:
        raise RuntimeError("no_valid_API7_document_frame_window: " + " | ".join(errors))
    return rows


def active_document_window(session, min_width=250, min_height=180):
    rows = document_frame_windows(session)
    usable = [r for r in rows if r["width"] >= min_width and r["height"] >= min_height]
    if not usable:
        usable = rows
    return max(usable, key=lambda r: max(1,r["width"]) * max(1,r["height"]))


def find_kompas_window(min_width=300, min_height=200):
    """Legacy PID/EnumWindows fallback; not the primary KOMPAS window locator."""
    pids = kompas_pids()
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not USER32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        r = wintypes.RECT()
        USER32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right-r.left, r.bottom-r.top
        if r.left > -30000 and w >= min_width and h >= min_height:
            found.append((w*h, int(hwnd), int(pid.value), r))
        return True

    USER32.EnumWindows(cb, 0)
    if not found:
        raise RuntimeError("KOMPAS.exe_window_not_found")
    _, hwnd, pid, r = max(found, key=lambda x: x[0])
    return {
        "hwnd": hwnd, "pid": pid,
        "rect": [r.left,r.top,r.right,r.bottom],
        "width": r.right-r.left, "height": r.bottom-r.top,
        "visible": True, "minimized": False,
        "source": "legacy_tasklist_EnumWindows",
    }


def _window_text(hwnd) -> str:
    handle = wintypes.HWND(int(hwnd))
    length = USER32.GetWindowTextLengthW(handle)
    buffer = ctypes.create_unicode_buffer(length + 1)
    USER32.GetWindowTextW(handle, buffer, length + 1)
    return buffer.value


def _window_class(hwnd) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    USER32.GetClassNameW(wintypes.HWND(int(hwnd)), buffer, 256)
    return buffer.value


def enumerate_dialogs(pid: int = 0, dialog_class: str = DIALOG_CLASS):
    """Перечислить окна-диалоги Win32, принадлежащие процессу КОМПАСа.

    Ограничение по классу окна намеренное: сторож работает только с
    настоящими диалогами (#32770), а не со всеми окнами процесса, иначе
    под раздачу попали бы панели и доки самого КОМПАСа.
    """
    rows = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        owner_pid = wintypes.DWORD()
        USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if pid and int(owner_pid.value) != int(pid):
            return True
        class_name = _window_class(hwnd)
        if dialog_class and class_name != dialog_class:
            return True
        rows.append({
            "hwnd": int(hwnd),
            "pid": int(owner_pid.value),
            "title": _window_text(hwnd),
            "class_name": class_name,
            "visible": bool(USER32.IsWindowVisible(wintypes.HWND(hwnd))),
            "enabled": bool(USER32.IsWindowEnabled(wintypes.HWND(hwnd))),
            "has_owner": bool(USER32.GetWindow(wintypes.HWND(hwnd), 4)),  # GW_OWNER
        })
        return True

    USER32.EnumWindows(cb, 0)
    return rows


def close_dialog(hwnd: int, wait_seconds: float = 0.4):
    """Закрыть диалог через WM_CLOSE и подтвердить, что окно действительно ушло."""
    handle = wintypes.HWND(int(hwnd))
    if not USER32.IsWindow(handle):
        raise RuntimeError(f"invalid_window_handle: {hwnd}")
    WM_CLOSE = 0x0010
    posted = bool(USER32.PostMessageW(handle, WM_CLOSE, 0, 0))
    deadline = time.time() + max(0.0, float(wait_seconds))
    while time.time() < deadline:
        if not USER32.IsWindow(handle):
            return {"hwnd": int(hwnd), "closed": True, "method": "WM_CLOSE", "posted": posted}
        time.sleep(0.05)
    return {
        "hwnd": int(hwnd),
        "closed": False,
        "method": "WM_CLOSE",
        "posted": posted,
        "reason": "window_still_exists_after_WM_CLOSE",
    }


def bring_to_front(hwnd: int):
    hwnd = wintypes.HWND(int(hwnd))
    if USER32.IsIconic(hwnd):
        USER32.ShowWindow(hwnd, 9)  # SW_RESTORE
        time.sleep(0.18)
    else:
        USER32.ShowWindow(hwnd, 5)  # SW_SHOW
    USER32.SetForegroundWindow(hwnd)
    time.sleep(0.15)


def _key(vk, down=True):
    KEYEVENTF_KEYUP = 0x0002
    USER32.keybd_event(vk, 0, 0 if down else KEYEVENTF_KEYUP, 0)


def ctrl_arrow(direction: str, steps: int, hwnd: int | None = None):
    keys = {"left":0x25, "up":0x26, "right":0x27, "down":0x28}
    if direction not in keys:
        raise ValueError(f"unsupported_direction: {direction}")
    if hwnd is None:
        hwnd = find_kompas_window()["hwnd"]
    bring_to_front(hwnd)
    _key(0x11, True)
    try:
        for _ in range(abs(int(steps))):
            vk = keys[direction]
            _key(vk, True); time.sleep(0.025)
            _key(vk, False); time.sleep(0.055)
    finally:
        _key(0x11, False)
    return {"hwnd":int(hwnd), "direction":direction, "steps":int(steps), "method":"Ctrl+Arrow"}