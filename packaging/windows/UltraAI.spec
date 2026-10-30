# PyInstaller spec for the Windows app. Build on Windows with:
#   pyinstaller packaging\windows\UltraAI.spec --noconfirm
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent.parent
a = Analysis(
    [str(root / "run.py")],
    pathex=[str(root)],
    datas=[(str(root / "ultra" / "web"), "ultra/web"), (str(root / "plugins"), "plugins")],
    hiddenimports=collect_submodules("uvicorn") + ["ultra.server", "webview", "clr"],
    excludes=["tkinter", "matplotlib", "numpy"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="UltraAI", console=False,
          icon=str(root / "packaging" / "windows" / "icon.ico"))
coll = COLLECT(exe, a.binaries, a.datas, name="UltraAI")
