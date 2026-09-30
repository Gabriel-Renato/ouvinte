"""Ouvinte — transcreve áudio e vídeo no Windows."""

from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import tkinter.filedialog as filedialog
from pathlib import Path
from tkinter import messagebox


def _ensure_stdio() -> None:
    """No .exe sem console, stdout e stderr vêm vazios e bibliotecas quebram ao avisar."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        folder = Path(os.environ["LOCALAPPDATA"]) / "Ouvinte"
    else:
        folder = Path(__file__).resolve().parent / ".dados"
    folder.mkdir(parents=True, exist_ok=True)
    handle = open(folder / "ouvinte.log", "a", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = handle
    if sys.stderr is None:
        sys.stderr = handle


_ensure_stdio()

import customtkinter as ctk

import engine

APP_NAME = "Ouvinte"


def setup_logging() -> None:
    try:
        log_path = engine.data_dir() / "ouvinte.log"
        logging.basicConfig(
            filename=log_path,
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            encoding="utf-8",
        )
    except Exception:
        logging.basicConfig(level=logging.INFO)


def enable_dpi() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        return


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{num_bytes} B"


def load_settings() -> dict:
    path = engine.data_dir() / "config.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    path = engine.data_dir() / "config.json"
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logging.getLogger("ouvinte").exception("Não salvou as preferências")


class OuvinteApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.configure(fg_color="#0f1419")

        self.files: list[Path] = []
        self.transcripts: list[engine.Transcript] = []
        self.transcriber = engine.Transcriber()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.events: queue.Queue = queue.Queue()
        self.settings = load_settings()

        self._build()
        self._fit_screen()
        self._restore_settings()
        self.after(200, self._hook_drop)
        self.after(100, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _fit_screen(self) -> None:
        left, top, right, bottom = self._work_area()
        work_w = max(800, right - left)
        work_h = max(600, bottom - top)
        width = min(1040, max(640, work_w - 24))
        height = min(900, max(560, work_h - 72))
        x = left + max(0, (work_w - width) // 2)
        y = top + max(0, (work_h - height) // 2)
        self.minsize(min(760, width), min(560, height))
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.update()
        overflow = (self.winfo_rooty() + self.winfo_height()) - bottom
        if overflow > 0:
            height = max(560, height - overflow - 12)
            y = top + max(0, (work_h - height) // 2)
            self.minsize(min(760, width), min(560, height))
            self.geometry(f"{width}x{height}+{x}+{y}")

    def _work_area(self) -> tuple[int, int, int, int]:
        if os.name != "nt":
            return 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()
        import ctypes

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        rect = RECT()
        ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0)
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()
        return rect.left, rect.top, rect.right, rect.bottom

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(12, 4))
        ctk.CTkLabel(
            header,
            text="Ouvinte",
            font=ctk.CTkFont(family="Segoe UI", size=24, weight="bold"),
            text_color="#f4f7fb",
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text="Arraste áudio ou vídeo para cá. A transcrição roda neste computador.",
            font=ctk.CTkFont(family="Segoe UI", size=14),
            text_color="#93a1b5",
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        self.drop = ctk.CTkFrame(
            self,
            height=58,
            fg_color="#162033",
            border_width=2,
            border_color="#3d8bfd",
            corner_radius=16,
        )
        self.drop.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 6))
        self.drop.grid_propagate(False)
        self.drop_label = ctk.CTkLabel(
            self.drop,
            text="Solte áudio ou vídeo aqui, ou clique para escolher",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color="#d7e6ff",
            justify="center",
        )
        self.drop_label.place(relx=0.5, rely=0.5, anchor="center")
        for widget in (self.drop, self.drop_label):
            widget.bind("<Button-1>", lambda _event: self.choose_files())
            widget.configure(cursor="hand2")

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 6))
        ctk.CTkButton(
            actions,
            text="Escolher arquivos",
            command=self.choose_files,
            width=160,
            height=36,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
        ).pack(side="left")
        ctk.CTkButton(
            actions,
            text="Limpar lista",
            command=self.clear_files,
            width=120,
            height=36,
            fg_color="#243044",
            hover_color="#314158",
        ).pack(side="left", padx=(8, 0))
        self.count_label = ctk.CTkLabel(
            actions,
            text="Nenhum arquivo",
            text_color="#93a1b5",
            font=ctk.CTkFont(family="Segoe UI", size=13),
        )
        self.count_label.pack(side="left", padx=16)

        self.files_box = ctk.CTkTextbox(
            self,
            height=48,
            fg_color="#121a27",
            text_color="#e7ecf3",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            wrap="none",
            activate_scrollbars=True,
        )
        self.files_box.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 6))
        self.files_box.insert("1.0", "Os arquivos selecionados aparecem aqui.")
        self.files_box.configure(state="disabled")

        options = ctk.CTkFrame(self, fg_color="#121a27", corner_radius=12)
        options.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 6))
        options.grid_columnconfigure(1, weight=1)
        options.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(options, text="Idioma", text_color="#93a1b5").grid(
            row=0, column=0, padx=(14, 8), pady=(8, 2), sticky="w"
        )
        self.language_box = ctk.CTkComboBox(
            options,
            values=[label for label, _code in engine.LANGUAGE_CHOICES],
            state="readonly",
            width=220,
            command=lambda _value: self._save_preferences(),
        )
        self.language_box.grid(row=0, column=1, padx=(0, 16), pady=(8, 2), sticky="ew")

        ctk.CTkLabel(options, text="Modelo", text_color="#93a1b5").grid(
            row=0, column=2, padx=(8, 8), pady=(8, 2), sticky="w"
        )
        self.model_box = ctk.CTkComboBox(
            options,
            values=[label for label, _model, _hint in engine.MODEL_CHOICES],
            state="readonly",
            width=280,
            command=self._on_model_change,
        )
        self.model_box.grid(row=0, column=3, padx=(0, 14), pady=(8, 2), sticky="ew")

        ctk.CTkLabel(options, text="Palavras importantes", text_color="#93a1b5").grid(
            row=1, column=0, padx=(14, 8), pady=(4, 4), sticky="w"
        )
        self.hotwords_entry = ctk.CTkEntry(
            options,
            placeholder_text="Nomes, siglas ou termos do áudio. Ex.: Unimed, João Silva",
        )
        self.hotwords_entry.grid(row=1, column=1, columnspan=3, padx=(0, 14), pady=(4, 4), sticky="ew")

        self.model_hint = ctk.CTkLabel(
            options,
            text="",
            text_color="#93a1b5",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            anchor="w",
            justify="left",
        )
        self.model_hint.grid(row=2, column=0, columnspan=4, padx=14, pady=(0, 8), sticky="ew")

        run_row = ctk.CTkFrame(self, fg_color="transparent")
        run_row.grid(row=5, column=0, sticky="nsew", padx=20, pady=(0, 4))
        run_row.grid_columnconfigure(0, weight=1)
        run_row.grid_rowconfigure(2, weight=1)

        buttons = ctk.CTkFrame(run_row, fg_color="transparent")
        buttons.grid(row=0, column=0, sticky="ew")
        self.start_button = ctk.CTkButton(
            buttons,
            text="Transcrever",
            command=self.start,
            width=160,
            height=40,
            fg_color="#16a34a",
            hover_color="#15803d",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
        )
        self.start_button.pack(side="left")
        self.cancel_button = ctk.CTkButton(
            buttons,
            text="Cancelar",
            command=self.cancel,
            width=120,
            height=40,
            fg_color="#243044",
            hover_color="#314158",
            state="disabled",
        )
        self.cancel_button.pack(side="left", padx=(8, 0))

        self.progress = ctk.CTkProgressBar(run_row, height=10, progress_color="#3d8bfd")
        self.progress.grid(row=1, column=0, sticky="ew", pady=(10, 4))
        self.progress.set(0)

        self.result_box = ctk.CTkTextbox(
            run_row,
            fg_color="#121a27",
            text_color="#f4f7fb",
            font=ctk.CTkFont(family="Segoe UI", size=15),
            wrap="word",
        )
        self.result_box.grid(row=2, column=0, sticky="nsew", pady=(8, 0))

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=6, column=0, sticky="ew", padx=20, pady=(4, 10))
        self.copy_button = ctk.CTkButton(footer, text="Copiar", width=100, command=self.copy_text, fg_color="#243044", hover_color="#314158")
        self.copy_button.pack(side="left")
        self.save_txt_button = ctk.CTkButton(footer, text="Salvar TXT", width=120, command=self.save_txt, fg_color="#243044", hover_color="#314158")
        self.save_txt_button.pack(side="left", padx=(8, 0))
        self.save_srt_button = ctk.CTkButton(footer, text="Salvar SRT", width=120, command=self.save_srt, fg_color="#243044", hover_color="#314158")
        self.save_srt_button.pack(side="left", padx=(8, 0))
        self.status_label = ctk.CTkLabel(
            footer,
            text="Pronto.",
            text_color="#93a1b5",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            anchor="e",
        )
        self.status_label.pack(side="right", fill="x", expand=True, padx=(16, 0))

    def _restore_settings(self) -> None:
        language = self.settings.get("language", "Português")
        model = self.settings.get("model", engine.MODEL_CHOICES[0][0])
        if language not in self.language_box.cget("values"):
            language = "Português"
        if model not in self.model_box.cget("values"):
            model = engine.MODEL_CHOICES[0][0]
        self.language_box.set(language)
        self.model_box.set(model)
        self.hotwords_entry.insert(0, self.settings.get("hotwords", ""))
        self._on_model_change(model)

    def _selected_language(self) -> str | None:
        label = self.language_box.get()
        for name, code in engine.LANGUAGE_CHOICES:
            if name == label:
                return code
        return "pt"

    def _selected_model(self) -> str:
        label = self.model_box.get()
        for name, model_id, _hint in engine.MODEL_CHOICES:
            if name == label:
                return model_id
        return "large-v3"

    def _on_model_change(self, label: str) -> None:
        hint = ""
        for name, _model_id, text in engine.MODEL_CHOICES:
            if name == label:
                hint = text
                break
        self.model_hint.configure(text=hint)
        self._save_preferences()

    def _save_preferences(self) -> None:
        save_settings(
            {
                "language": self.language_box.get(),
                "model": self.model_box.get(),
                "hotwords": self.hotwords_entry.get(),
            }
        )

    def _hook_drop(self) -> None:
        if os.name != "nt":
            return
        try:
            import windnd

            windnd.hook_dropfiles(self, self._on_drop, force_unicode=True)
        except Exception:
            logging.getLogger("ouvinte").exception("Arrastar e soltar indisponível")
            self._set_status("Arrastar e soltar indisponível. Use Escolher arquivos.")

    def _on_drop(self, dropped) -> None:
        paths: list[Path] = []
        for item in dropped:
            if isinstance(item, bytes):
                text = item.decode("utf-8", errors="replace")
            else:
                text = str(item)
            paths.append(Path(text))
        self.after(0, lambda: self.add_paths(paths))

    def choose_files(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in sorted(engine.MEDIA_EXTENSIONS))
        selected = filedialog.askopenfilenames(
            title="Escolher áudio ou vídeo",
            filetypes=[("Áudio e vídeo", patterns), ("Todos os arquivos", "*.*")],
        )
        if selected:
            self.add_paths([Path(item) for item in selected])

    def add_paths(self, paths: list[Path]) -> None:
        found, skipped = engine.collect_media(paths)
        known = {path.resolve() for path in self.files}
        added = 0
        for path in found:
            if path not in known:
                self.files.append(path)
                known.add(path)
                added += 1
        self._refresh_file_list()
        if added == 0 and skipped:
            self._set_status("Nenhum áudio ou vídeo reconhecido.")
        elif skipped:
            self._set_status(f"{added} arquivo(s) adicionado(s). {skipped} ignorado(s).")
        elif added:
            self._set_status(f"{added} arquivo(s) adicionado(s).")

    def clear_files(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.files.clear()
        self._refresh_file_list()
        self._set_status("Lista limpa.")

    def _refresh_file_list(self) -> None:
        lines = []
        for path in self.files:
            try:
                size = human_size(path.stat().st_size)
            except OSError:
                size = "?"
            lines.append(f"{path.name}   ·   {size}   ·   {path}")
        self.files_box.configure(state="normal")
        self.files_box.delete("1.0", "end")
        self.files_box.insert("1.0", "\n".join(lines) if lines else "Os arquivos selecionados aparecem aqui.")
        self.files_box.configure(state="disabled")
        count = len(self.files)
        if count == 0:
            self.count_label.configure(text="Nenhum arquivo")
        elif count == 1:
            self.count_label.configure(text="1 arquivo")
        else:
            self.count_label.configure(text=f"{count} arquivos")

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.files:
            self._set_status("Escolha ou arraste pelo menos um arquivo.")
            return
        self._save_preferences()
        self.transcripts = []
        self.result_box.delete("1.0", "end")
        self.progress.set(0)
        self.cancel_event = threading.Event()
        self._set_running(True)
        files = list(self.files)
        language = self._selected_language()
        model_name = self._selected_model()
        hotwords = self.hotwords_entry.get()
        self.worker = threading.Thread(
            target=self._run,
            args=(files, language, model_name, hotwords),
            daemon=True,
        )
        self.worker.start()

    def cancel(self) -> None:
        self.cancel_event.set()
        self._set_status("Cancelando...")

    def _run(self, files: list[Path], language: str | None, model_name: str, hotwords: str) -> None:
        total = len(files)
        try:
            for index, path in enumerate(files, start=1):
                if self.cancel_event.is_set():
                    raise engine.Cancelled()
                self.events.put(("status", f"({index}/{total}) {path.name}"))

                def on_status(message: str, *, file_index=index) -> None:
                    self.events.put(("status", f"({file_index}/{total}) {message}"))

                def on_progress(fraction: float, *, file_index=index) -> None:
                    overall = ((file_index - 1) + fraction) / total
                    self.events.put(("progress", overall))

                transcript = self.transcriber.transcribe(
                    path,
                    model_name=model_name,
                    language=language,
                    hotwords=hotwords,
                    cancel_event=self.cancel_event,
                    on_status=on_status,
                    on_progress=on_progress,
                )
                self.events.put(("transcript", transcript))
            self.events.put(("done", f"Concluído. {total} arquivo(s)."))
        except engine.Cancelled:
            self.events.put(("done", "Cancelado. O texto já gerado foi mantido."))
        except Exception as exc:
            logging.getLogger("ouvinte").exception("Falha na transcrição")
            self.events.put(("error", str(exc)))

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self._set_status(payload)
                elif kind == "progress":
                    self.progress.set(float(payload))
                elif kind == "transcript":
                    self._append_transcript(payload)
                elif kind == "done":
                    self.progress.set(1 if not str(payload).startswith("Cancelado") else self.progress.get())
                    self._set_running(False)
                    self._set_status(payload)
                elif kind == "error":
                    self._set_running(False)
                    self._set_status("Não foi possível transcrever.")
                    messagebox.showerror(APP_NAME, payload)
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _append_transcript(self, transcript: engine.Transcript) -> None:
        self.transcripts.append(transcript)
        probability = transcript.language_probability * 100
        header = (
            f"— {transcript.source.name} —\n"
            f"Idioma: {engine.language_label(transcript.language)} ({probability:.0f}%)\n\n"
        )
        body = transcript.text or "Nenhuma fala detectada neste arquivo."
        self.result_box.insert("end", header + body + "\n\n")
        self.result_box.see("end")

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self.start_button.configure(state=state)
        self.cancel_button.configure(state="normal" if running else "disabled")

    def _set_status(self, text: str) -> None:
        self.status_label.configure(text=text)

    def copy_text(self) -> None:
        text = self.result_box.get("1.0", "end").strip()
        if not text:
            self._set_status("Nada para copiar.")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("Texto copiado.")

    def save_txt(self) -> None:
        text = self.result_box.get("1.0", "end").strip()
        if not text:
            self._set_status("Nada para salvar.")
            return
        default = "transcricao.txt"
        if len(self.transcripts) == 1:
            default = self.transcripts[0].source.stem + ".txt"
        path = filedialog.asksaveasfilename(
            title="Salvar texto",
            defaultextension=".txt",
            initialfile=default,
            filetypes=[("Texto", "*.txt")],
        )
        if not path:
            return
        Path(path).write_text(text + "\n", encoding="utf-8-sig")
        self._set_status(f"Salvo em {path}")

    def save_srt(self) -> None:
        ready = [item for item in self.transcripts if item.segments]
        if not ready:
            self._set_status("Nenhuma legenda para salvar.")
            return
        if len(ready) == 1:
            transcript = ready[0]
            path = filedialog.asksaveasfilename(
                title="Salvar legenda",
                defaultextension=".srt",
                initialfile=transcript.source.stem + ".srt",
                filetypes=[("Legenda SubRip", "*.srt")],
            )
            if not path:
                return
            Path(path).write_text(engine.to_srt(transcript.segments), encoding="utf-8-sig")
            self._set_status(f"Legenda salva em {path}")
            return

        folder = filedialog.askdirectory(title="Pasta para salvar as legendas")
        if not folder:
            return
        destination = Path(folder)
        for transcript in ready:
            target = destination / f"{transcript.source.stem}.srt"
            target.write_text(engine.to_srt(transcript.segments), encoding="utf-8-sig")
        self._set_status(f"{len(ready)} legendas salvas em {folder}")

    def _on_close(self) -> None:
        self.cancel_event.set()
        self._save_preferences()
        self.destroy()


def run_cli(args: list[str]) -> int:
    model_name = "large-v3"
    language: str | None = "pt"
    files: list[str] = []
    index = 0
    while index < len(args):
        item = args[index]
        if item == "--model" and index + 1 < len(args):
            model_name = args[index + 1]
            index += 2
            continue
        if item == "--lang" and index + 1 < len(args):
            language = None if args[index + 1] == "auto" else args[index + 1]
            index += 2
            continue
        files.append(item)
        index += 1
    if not files:
        print("Uso: python main.py --cli [--model large-v3] [--lang pt] arquivo.mp4")
        return 2
    setup_logging()
    transcriber = engine.Transcriber()
    cancel_event = threading.Event()
    for raw in files:
        path = Path(raw)
        print(f"Transcrevendo {path}...", flush=True)
        transcript = transcriber.transcribe(
            path,
            model_name=model_name,
            language=language,
            hotwords="",
            cancel_event=cancel_event,
            on_status=lambda message: print(message, flush=True),
            on_progress=lambda _fraction: None,
        )
        print(transcript.text or "(sem fala)")
    return 0


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        raise SystemExit(run_cli(sys.argv[2:]))
    enable_dpi()
    setup_logging()
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    app = OuvinteApp()
    app.mainloop()


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()
