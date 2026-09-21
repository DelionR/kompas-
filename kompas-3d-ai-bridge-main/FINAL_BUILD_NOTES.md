# FINAL BUILD NOTES — V1 / runtime 1.1.8

This package collapses the successful 1.1.4 -> 1.1.8 host fixes into one distribution.

Fixed in the final line:
1. PyMuPDF import uses `pymupdf`, not legacy/conflicting `fitz`.
2. Python resolver preserves the proven Windows launcher path and avoids fragile inline `python -c` dependency checks.
3. Installer/static tests do not require installer files inside the installed bridge.
4. COM attach never silently launches a second KOMPAS.
5. Runtime must match KOMPAS elevation; final install/bootstrap default is non-admin.
6. API7 full document path uses `PathName`.
7. PowerShell structured payloads use UTF-8 JSON files instead of command-line JSON.
8. COPY open can suppress rebuild prompt with YES only for the disposable COPY.
9. Viewport uses API7 native HWND (`DocumentFrames`) rather than PID window guessing.
10. GPU/OpenGL viewport capture prefers screen pixels of that exact document frame; PrintWindow remains fallback.
11. SELF_TEST records the actual worker version from `ping`.

The working bridge on the user's machine should not be overwritten merely to obtain this archive. Use this ZIP as the clean golden distribution and run SELF_TEST after any future install/update.
