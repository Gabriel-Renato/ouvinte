"""Monta o Ouvinte para Windows, com as bibliotecas que o outro computador não tem."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "Ouvinte"
ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist" / APP_NAME

# O python3xx.dll não abre sem estas DLLs. No PC que tem Python elas estão
# instaladas no sistema; no outro computador o Windows não as encontra.
RUNTIME_DLLS = (
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "concrt140.dll",
    "vcomp140.dll",
)
REQUIRED_RUNTIME = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")

COLLECT_ALL = (
    "customtkinter",
    "faster_whisper",
    "ctranslate2",
    "imageio_ffmpeg",
    "tokenizers",
    "huggingface_hub",
    "av",
    "numpy",
    "onnxruntime",
)


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def search_dirs() -> list[Path]:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return [
        Path(sys.base_prefix),
        Path(sys.base_prefix) / "DLLs",
        system_root / "System32",
    ]


def find_runtime_dlls() -> list[Path]:
    found: list[Path] = []
    missing: list[str] = []
    for name in RUNTIME_DLLS:
        match = next((folder / name for folder in search_dirs() if (folder / name).is_file()), None)
        if match is None:
            if name in REQUIRED_RUNTIME:
                missing.append(name)
            else:
                print(f"  Aviso: {name} não está neste Python. Seguindo sem ela.")
            continue
        print(f"  Incluindo {name}")
        found.append(match)
    if missing:
        fail(
            "Não encontrei "
            + ", ".join(missing)
            + ". Instale o Python em python.org, marque Add python.exe to PATH "
            "e rode este arquivo de novo."
        )
    return found


def python_bundle_dir(app_dir: Path) -> Path:
    for folder in (app_dir / "_internal", app_dir):
        if any(folder.glob("python3*.dll")):
            return folder
    fail(f"O pacote em {app_dir} saiu sem python3xx.dll.")
    raise AssertionError


def copy_runtime(dlls: list[Path], bundle_dir: Path) -> None:
    for dll in dlls:
        target = bundle_dir / dll.name
        shutil.copy2(dll, target)
        if not target.is_file() or target.stat().st_size == 0:
            fail(f"Não consegui gravar {target}.")


def write_open_instructions(app_dir: Path) -> None:
    text = """\
Como abrir o Ouvinte
====================

1. Extraia este ZIP inteiro.
2. Entre na pasta Ouvinte.
3. Abra Ouvinte.exe.

A pasta _internal precisa continuar ao lado do Ouvinte.exe.
Se mover só o .exe, o Windows mostra "Failed to load Python DLL".

Não é preciso instalar Python neste computador.
Na primeira transcrição o modelo de voz baixa para %LOCALAPPDATA%\\Ouvinte.
"""
    (app_dir / "COMO-ABRIR.txt").write_text(text, encoding="utf-8-sig")


def build(dlls: list[Path]) -> None:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onedir",
        "--name",
        APP_NAME,
    ]
    for package in COLLECT_ALL:
        command.extend(["--collect-all", package])
    command.extend(["--hidden-import", "windnd"])
    for dll in dlls:
        command.extend(["--add-binary", f"{dll};."])
    command.append(str(ROOT / "main.py"))
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> None:
    if sys.platform != "win32":
        fail("Gere o programa no Windows, com dois cliques em build_exe.bat.")

    print()
    print("  Bibliotecas do Windows que o outro computador precisa")
    dlls = find_runtime_dlls()
    print()
    print("  Montando dist\\Ouvinte")
    build(dlls)

    bundle_dir = python_bundle_dir(DIST)
    copy_runtime(dlls, bundle_dir)
    missing = [name for name in REQUIRED_RUNTIME if not (bundle_dir / name).is_file()]
    if missing:
        fail("O pacote ainda está sem " + ", ".join(missing) + ".")
    if not any(bundle_dir.glob("python3*.dll")):
        fail("O pacote ainda está sem a DLL do Python.")

    write_open_instructions(DIST)
    zip_path = ROOT / "dist" / "Ouvinte-Windows.zip"
    if zip_path.exists():
        zip_path.unlink()
    shutil.make_archive(str(zip_path.with_suffix("")), "zip", root_dir=ROOT / "dist", base_dir=APP_NAME)

    print()
    print("  Pronto.")
    print(f"  Neste computador: {DIST / 'Ouvinte.exe'}")
    print(f"  Envie este arquivo: {zip_path}")
    print("  No outro computador, extraia o ZIP inteiro e abra Ouvinte.exe.")
    print("  O modelo de voz baixa na primeira transcrição e fica em %LOCALAPPDATA%\\Ouvinte")
    print()


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError:
        fail("A geração falhou. Veja as mensagens acima.")
