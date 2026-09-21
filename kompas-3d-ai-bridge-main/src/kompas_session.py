from __future__ import annotations
from pathlib import Path
import time


class KompasSession:
    def __init__(self, root):
        self.root = Path(root)
        self.app = None
        self.last_document_path = None
        self.visibility_snapshot = None
        # Кэш версии КОМПАСа: заполняется только когда действие под защитой
        # матрицы версий, чтобы не платить COM-обращением за каждый вызов.
        self.kompas_version = None

    def connect(self, visible=True):
        """
        Attach ONLY to an already-running interactive KOMPAS instance.

        Safety rule:
        a failed attach must never silently launch a second KOMPAS process.
        Explicit application launch, if ever needed, must be a separate action.
        """
        if self.app is not None:
            try:
                _ = self.app.Documents
                return self.app
            except Exception:
                self.app = None

        try:
            import pythoncom
            import win32com.client as wc
            try:
                pythoncom.CoInitialize()
            except Exception:
                pass

            last_error = None
            for _ in range(8):
                try:
                    self.app = wc.GetActiveObject("KOMPAS.Application.7")
                    break
                except Exception as exc:
                    last_error = exc
                    time.sleep(0.25)

            if self.app is None:
                pids = []
                try:
                    from winui import kompas_pids
                    pids = sorted(kompas_pids())
                except Exception:
                    pass
                raise RuntimeError(
                    "kompas_active_instance_not_found: bridge intentionally refused "
                    "to launch a second KOMPAS instance; "
                    f"running_kompas_pids={pids}; GetActiveObject={last_error}. "
                    "Leave exactly one intended KOMPAS instance open with the model active, "
                    "close modal dialogs/extra blank KOMPAS windows, and make sure "
                    "KOMPAS and the bridge run under the same Windows user/elevation."
                )

            if visible:
                try:
                    self.app.Visible = True
                except Exception:
                    pass
            return self.app
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"kompas_com_attach_failed: {e}")

    def api5(self):
        # API5 must attach to the same already-running interactive KOMPAS.
        # Never Dispatch here: view operations must not launch/select another instance.
        self.connect()
        import win32com.client as wc
        try:
            return wc.GetActiveObject("KOMPAS.Application.5")
        except Exception as exc:
            raise RuntimeError(f"kompas_api5_active_instance_not_found: {exc}")

    def document_path(self, d=None):
        # KOMPAS API7 IKompasDocument full path is PathName.
        if d is None:
            d = self.active()
        if d is None:
            return self.last_document_path

        for attr in ("PathName", "FullPath", "FileName"):
            try:
                value = getattr(d, attr, "")
                if value:
                    return str(value)
            except Exception:
                pass

        for method in ("GetFullPath", "GetPathName"):
            try:
                value = getattr(d, method)()
                if value:
                    return str(value)
            except Exception:
                pass

        try:
            folder = str(getattr(d, "Path", "") or "")
            name = str(getattr(d, "Name", "") or "")
            if folder and name:
                return str(Path(folder) / name)
        except Exception:
            pass
        return self.last_document_path

    def documents(self):
        app = self.connect()
        docs = []
        try:
            col = app.Documents
            for i in range(col.Count):
                d = col.Item(i)
                docs.append({
                    "index": i,
                    "name": str(getattr(d, "Name", "")),
                    "file_name": self.document_path(d) or "",
                    "path_name": self.document_path(d) or "",
                    "type": str(getattr(d, "DocumentType", "")),
                })
        except Exception as e:
            docs.append({"enumeration_error": str(e)})
        return docs

    def active(self):
        app = self.connect()
        return getattr(app, "ActiveDocument", None)

    def active_path(self):
        d = self.active()
        if d is None:
            return None
        return self.document_path(d)

    def status(self):
        app = self.connect()
        active = self.active()
        pid = getattr(app, "ProcessID", None)
        window = None
        try:
            from winui import find_kompas_window
            window = find_kompas_window()
            if not pid:
                pid = window.get("pid")
        except Exception:
            pass
        return {
            "progid": "KOMPAS.Application.7",
            "version": str(getattr(app, "Version", "")),
            "pid": pid,
            "process_id": pid,
            "window": window,
            "documents": self.documents(),
            "active_document": ((self.document_path(active) or str(getattr(active, "Name", "") or "")) if active else None),
            "com_alive": True,
        }