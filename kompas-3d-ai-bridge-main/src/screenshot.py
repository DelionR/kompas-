from __future__ import annotations
import ctypes,time
from ctypes import wintypes
from pathlib import Path
from PIL import Image,ImageGrab,ImageStat
from winui import USER32,active_document_window,main_window_handle,window_info

GDI32=ctypes.windll.gdi32
try:
    DWMAPI=ctypes.windll.dwmapi
except Exception:
    DWMAPI=None


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_=[
        ("biSize",wintypes.DWORD),
        ("biWidth",wintypes.LONG),
        ("biHeight",wintypes.LONG),
        ("biPlanes",wintypes.WORD),
        ("biBitCount",wintypes.WORD),
        ("biCompression",wintypes.DWORD),
        ("biSizeImage",wintypes.DWORD),
        ("biXPelsPerMeter",wintypes.LONG),
        ("biYPelsPerMeter",wintypes.LONG),
        ("biClrUsed",wintypes.DWORD),
        ("biClrImportant",wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_=[
        ("bmiHeader",BITMAPINFOHEADER),
        ("bmiColors",wintypes.DWORD*3),
    ]


def _printwindow(hwnd,w,h):
    src=USER32.GetWindowDC(int(hwnd))
    if not src:
        raise RuntimeError(f"GetWindowDC_failed:{hwnd}")

    mem=GDI32.CreateCompatibleDC(src)
    bmp=GDI32.CreateCompatibleBitmap(src,w,h)
    old=GDI32.SelectObject(mem,bmp)

    try:
        ok=USER32.PrintWindow(int(hwnd),mem,2)

        info=BITMAPINFO()
        info.bmiHeader.biSize=ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth=w
        info.bmiHeader.biHeight=-h
        info.bmiHeader.biPlanes=1
        info.bmiHeader.biBitCount=32

        buf=ctypes.create_string_buffer(w*h*4)

        lines=GDI32.GetDIBits(
            mem,
            bmp,
            0,
            h,
            buf,
            ctypes.byref(info),
            0,
        )

        if not ok or lines!=h:
            raise RuntimeError(
                f"PrintWindow_failed ok={ok} lines={lines}/{h}"
            )

        return Image.frombuffer(
            "RGB",
            (w,h),
            buf,
            "raw",
            "BGRX",
            0,
            1,
        ).copy()

    finally:
        GDI32.SelectObject(mem,old)
        GDI32.DeleteObject(bmp)
        GDI32.DeleteDC(mem)
        USER32.ReleaseDC(int(hwnd),src)


def _score(im):
    g=im.convert("L")
    st=ImageStat.Stat(g)
    lo,hi=g.getextrema()

    return {
        "stddev":float(
            st.stddev[0]
            if st.stddev
            else 0
        ),
        "range":int(hi)-int(lo),
        "min":int(lo),
        "max":int(hi),
    }


def _usable(s):
    return (
        float(s.get("stddev",0))>=2
        and int(s.get("range",0))>=12
    )


def _pid(hwnd):
    if not hwnd:
        return 0

    p=wintypes.DWORD()

    USER32.GetWindowThreadProcessId(
        wintypes.HWND(int(hwnd)),
        ctypes.byref(p),
    )

    return int(p.value)


def _fg():
    h=int(USER32.GetForegroundWindow() or 0)

    return {
        "hwnd":h,
        "pid":_pid(h) if h else 0,
    }


def _redraw(hwnd):
    RDW_INVALIDATE=0x0001
    RDW_FRAME=0x0400
    RDW_UPDATENOW=0x0100
    RDW_ALLCHILDREN=0x0080

    flags=(
        RDW_INVALIDATE
        | RDW_FRAME
        | RDW_UPDATENOW
        | RDW_ALLCHILDREN
    )

    try:
        USER32.RedrawWindow(
            wintypes.HWND(int(hwnd)),
            None,
            0,
            flags,
        )
    except Exception:
        pass

    try:
        USER32.UpdateWindow(
            wintypes.HWND(int(hwnd))
        )
    except Exception:
        pass

    if DWMAPI is not None:
        try:
            DWMAPI.DwmFlush()
        except Exception:
            pass


def _prepare_topmost(main_hwnd,kpid):
    SW_SHOW=5
    SW_RESTORE=9

    SWP_NOSIZE=0x0001
    SWP_NOMOVE=0x0002
    SWP_NOACTIVATE=0x0010
    SWP_SHOWWINDOW=0x0040

    GWL_EXSTYLE=-20
    WS_EX_TOPMOST=0x00000008

    HWND_TOPMOST=wintypes.HWND(-1)

    if _pid(main_hwnd)!=int(kpid):
        raise RuntimeError(
            "main_hwnd_pid_changed_before_capture "
            f"wanted_pid={kpid} actual_pid={_pid(main_hwnd)}"
        )

    try:
        exstyle=int(
            USER32.GetWindowLongW(
                wintypes.HWND(int(main_hwnd)),
                GWL_EXSTYLE,
            )
        )
    except Exception:
        exstyle=0

    was_topmost=bool(exstyle & WS_EX_TOPMOST)

    if USER32.IsIconic(
        wintypes.HWND(int(main_hwnd))
    ):
        USER32.ShowWindow(
            wintypes.HWND(int(main_hwnd)),
            SW_RESTORE,
        )
    else:
        USER32.ShowWindow(
            wintypes.HWND(int(main_hwnd)),
            SW_SHOW,
        )

    ok=USER32.SetWindowPos(
        wintypes.HWND(int(main_hwnd)),
        HWND_TOPMOST,
        0,0,0,0,
        (
            SWP_NOMOVE
            | SWP_NOSIZE
            | SWP_NOACTIVATE
            | SWP_SHOWWINDOW
        ),
    )

    if not ok:
        raise RuntimeError(
            "SetWindowPos(HWND_TOPMOST)_failed"
        )

    _redraw(main_hwnd)
    time.sleep(.35)

    visible=bool(
        USER32.IsWindowVisible(
            wintypes.HWND(int(main_hwnd))
        )
    )

    minimized=bool(
        USER32.IsIconic(
            wintypes.HWND(int(main_hwnd))
        )
    )

    if not visible or minimized:
        raise RuntimeError(
            "kompas_not_visible_after_topmost_prepare "
            f"visible={visible} minimized={minimized}"
        )

    return {
        "was_topmost":was_topmost,
        "visible":visible,
        "minimized":minimized,
        "foreground":_fg(),
    }


def _restore_topmost(main_hwnd,was_topmost):
    if was_topmost:
        return

    SWP_NOSIZE=0x0001
    SWP_NOMOVE=0x0002
    SWP_NOACTIVATE=0x0010
    SWP_SHOWWINDOW=0x0040

    HWND_NOTOPMOST=wintypes.HWND(-2)

    try:
        USER32.SetWindowPos(
            wintypes.HWND(int(main_hwnd)),
            HWND_NOTOPMOST,
            0,0,0,0,
            (
                SWP_NOMOVE
                | SWP_NOSIZE
                | SWP_NOACTIVATE
                | SWP_SHOWWINDOW
            ),
        )
    except Exception:
        pass


def _grid_points(rect,cols=5,rows=5):
    l,t,r,b=map(int,rect)
    w=r-l
    h=b-t

    pts=[]

    for yi in range(rows):
        y=int(
            t
            + ((yi+1)*h)/(rows+1)
        )

        for xi in range(cols):
            x=int(
                l
                + ((xi+1)*w)/(cols+1)
            )

            pts.append((x,y))

    return pts


def _verify_screen_ownership(rect,kpid):
    samples=[]
    foreign=[]

    for x,y in _grid_points(rect):
        pt=wintypes.POINT(x,y)

        hit=int(
            USER32.WindowFromPoint(pt)
            or 0
        )

        hit_pid=_pid(hit) if hit else 0

        row={
            "x":x,
            "y":y,
            "hwnd":hit,
            "pid":hit_pid,
        }

        samples.append(row)

        if hit_pid!=int(kpid):
            foreign.append(row)

    return {
        "verified":len(foreign)==0,
        "sample_count":len(samples),
        "foreign_count":len(foreign),
        "foreign":foreign[:8],
        "samples":samples,
    }


def capture(
    root,
    viewport_only=True,
    label=None,
    session=None,
):
    if session is None:
        raise RuntimeError(
            "verified_viewport_requires_live_kompas_session"
        )

    root=Path(root)
    diagnostics=[]

    mh=int(main_window_handle(session))

    main=window_info(
        mh,
        "API7.IApplication.MainWindowHandle",
    )

    kpid=int(main["pid"])

    if viewport_only:
        win=active_document_window(
            session,
            min_width=250,
            min_height=180,
        )

        source=str(
            win.get("source","")
        )

        if not source.startswith(
            "API7.DocumentFrames"
        ):
            raise RuntimeError(
                f"unverified_viewport_target:{source}"
            )

    else:
        win=main
        source=str(
            win.get("source","")
        )

    if int(win.get("pid",0))!=kpid:
        raise RuntimeError(
            "kompas_window_pid_mismatch "
            f"main={kpid} target={win.get('pid')}"
        )

    l,t,r,b=map(
        int,
        win["rect"],
    )

    w=r-l
    h=b-t

    if w<50 or h<50:
        raise RuntimeError(
            f"capture_window_too_small:{win}"
        )

    pim=None
    ps=None
    pe=None

    try:
        pim=_printwindow(
            int(win["hwnd"]),
            w,
            h,
        )
        ps=_score(pim)

    except Exception as exc:
        pe=str(exc)
        diagnostics.append(
            "PrintWindow:"
            + str(exc)
        )

    fg_before=_fg()
    fg=fg_before

    post_redraw_printwindow_signal=None
    screen_verification_before=None
    screen_verification_after=None
    topmost_prepare=None

    if (
        pim is not None
        and _usable(ps)
    ):
        im=pim
        sig=ps
        method="PrintWindow_verified_hwnd"

    else:
        topmost_prepare=_prepare_topmost(
            mh,
            kpid,
        )

        try:
            _redraw(
                int(win["hwnd"])
            )

            time.sleep(.30)

            try:
                pim2=_printwindow(
                    int(win["hwnd"]),
                    w,
                    h,
                )

                post_redraw_printwindow_signal=_score(
                    pim2
                )

            except Exception as exc:
                diagnostics.append(
                    "PrintWindowAfterRedraw:"
                    + str(exc)
                )
                pim2=None

            if (
                pim2 is not None
                and _usable(
                    post_redraw_printwindow_signal
                )
            ):
                im=pim2
                sig=post_redraw_printwindow_signal
                method=(
                    "PrintWindow_verified_hwnd_"
                    "after_topmost_redraw"
                )

            else:
                im=None
                sig={
                    "stddev":0.0,
                    "range":0,
                    "min":0,
                    "max":0,
                }

                last_error=None

                for attempt in range(8):
                    screen_verification_before=(
                        _verify_screen_ownership(
                            (l,t,r,b),
                            kpid,
                        )
                    )

                    if not screen_verification_before[
                        "verified"
                    ]:
                        last_error=(
                            "foreign_window_over_viewport_before_capture "
                            + repr(
                                screen_verification_before[
                                    "foreign"
                                ]
                            )
                        )

                        _redraw(mh)
                        _redraw(
                            int(win["hwnd"])
                        )

                        time.sleep(.25)
                        continue

                    try:
                        candidate=ImageGrab.grab(
                            bbox=(l,t,r,b),
                            all_screens=True,
                        ).convert("RGB")

                        candidate_sig=_score(
                            candidate
                        )

                    except Exception as exc:
                        last_error=str(exc)
                        time.sleep(.25)
                        continue

                    screen_verification_after=(
                        _verify_screen_ownership(
                            (l,t,r,b),
                            kpid,
                        )
                    )

                    if not screen_verification_after[
                        "verified"
                    ]:
                        last_error=(
                            "foreign_window_over_viewport_after_capture "
                            + repr(
                                screen_verification_after[
                                    "foreign"
                                ]
                            )
                        )
                        time.sleep(.25)
                        continue

                    im=candidate
                    sig=candidate_sig

                    if _usable(candidate_sig):
                        break

                    last_error=(
                        "insufficient_signal:"
                        + repr(candidate_sig)
                    )

                    _redraw(mh)
                    _redraw(
                        int(win["hwnd"])
                    )

                    time.sleep(.25)

                if im is None:
                    raise RuntimeError(
                        "verified_viewport_screen_capture_failed:"
                        + str(last_error)
                    )

                if not _usable(sig):
                    raise RuntimeError(
                        "verified_viewport_image_has_insufficient_signal "
                        f"signal={sig} "
                        f"rect={(l,t,r,b)} "
                        f"ownership_before={screen_verification_before} "
                        f"ownership_after={screen_verification_after}"
                    )

                if (
                    not screen_verification_before
                    or not screen_verification_before[
                        "verified"
                    ]
                    or not screen_verification_after
                    or not screen_verification_after[
                        "verified"
                    ]
                ):
                    raise RuntimeError(
                        "viewport_screen_ownership_not_verified"
                    )

                method=(
                    "ImageGrab_verified_topmost_"
                    "pid_grid"
                )

        finally:
            _restore_topmost(
                mh,
                bool(
                    topmost_prepare.get(
                        "was_topmost",
                        False,
                    )
                ),
            )

        fg=_fg()

    out=root/"work"/"screenshots"
    out.mkdir(
        parents=True,
        exist_ok=True,
    )

    stamp=time.strftime(
        "%Y%m%d_%H%M%S"
    )

    ms=int(
        (time.time()%1)*1000
    )

    lab=(
        ""
        if not label
        else "_"
        + "".join(
            x
            if x.isalnum()
            or x in "-_"
            else "_"
            for x in str(label)
        )[:50]
    )

    path=out/f"kompas_{kpid}_{stamp}_{ms:03d}{lab}.png"

    im.save(
        path,
        optimize=True,
    )

    return {
        "path":str(path),
        "pid":int(win["pid"]),
        "kompas_pid":kpid,
        "hwnd":int(win["hwnd"]),
        "main_hwnd":mh,
        "window_rect":list(
            map(
                int,
                win["rect"],
            )
        ),
        "window_source":source,
        "capture_target_verified":True,
        "foreground_hwnd":int(
            fg.get("hwnd",0)
        ),
        "foreground_pid":int(
            fg.get("pid",0)
        ),
        "foreground_before":fg_before,
        "size":list(im.size),
        "bytes":path.stat().st_size,
        "method":method,
        "signal":sig,
        "printwindow_signal":ps,
        "post_redraw_printwindow_signal":
            post_redraw_printwindow_signal,
        "viewport_only":bool(
            viewport_only
        ),
        "crop":None,
        "diagnostics":diagnostics,
        "printwindow_error":pe,
        "topmost_prepare":topmost_prepare,
        "screen_verification_before":
            screen_verification_before,
        "screen_verification_after":
            screen_verification_after,
    }