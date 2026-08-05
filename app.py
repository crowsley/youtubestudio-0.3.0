from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# Windowed PyInstaller apps have no console streams, but Kokoro configures
# Loguru with stderr during import.
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")

import customtkinter as ctk
import numpy as np
import soundfile as sf
from connections import CredentialStore, detect_ffmpeg, detect_python, test_comfyui, test_ffmpeg, test_kling_api, test_kokoro, validate_workflow
from settings import DATA_DIR, SettingsStore, redact, validate_directory

APP_NAME = "YouTube AI Studio"
BASE_DIR = Path(__file__).resolve().parent
USER_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", BASE_DIR)) / APP_NAME
PROJECTS_DIR = USER_DATA_DIR / "projects"
OUTPUT_DIR = USER_DATA_DIR / "output"
VERSION_FILE = BASE_DIR / "version.json"

VOICE_OPTIONS = {
    "British Female - Emma": ("b", "bf_emma"),
    "British Female - Isabella": ("b", "bf_isabella"),
    "British Male - George": ("b", "bm_george"),
    "British Male - Lewis": ("b", "bm_lewis"),
    "American Female - Heart": ("a", "af_heart"),
    "American Female - Bella": ("a", "af_bella"),
    "American Male - Michael": ("a", "am_michael"),
    "American Male - Puck": ("a", "am_puck"),
}

PROJECT_FOLDERS = [
    "01_script",
    "02_voice",
    "03_kling",
    "04_images",
    "05_music",
    "06_davinci",
    "07_thumbnail",
    "08_export",
]

def safe_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*]+', "", value.strip())
    value = re.sub(r"\s+", "_", value)
    return value[:80] or "untitled_project"

class Studio(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")

        self.version = self.load_version()
        self.title(f"{APP_NAME} v{self.version}")
        self.geometry("1240x800")
        self.minsize(1050, 700)

        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        self.settings_store = SettingsStore()
        self.settings = self.settings_store.load()
        self.credentials = CredentialStore()
        self.setting_entries: dict[str, ctk.CTkEntry] = {}
        self.setting_vars: dict[str, object] = {}
        self.connection_status: dict[str, ctk.CTkLabel] = {}
        self.test_logs: dict[str, str] = {}
        self.current_project_path: Path | None = None
        self.pipeline_cache: dict[str, object] = {}
        self.generating = False

        self.build_ui()
        self.schedule_auto_save()
        if self.settings["general"].get("check_updates_on_startup"):
            self.after(1500, self.check_updates)
        last_project = self.settings["general"].get("last_project")
        if self.settings["general"].get("open_last_project") and last_project:
            self.after(500, lambda: self.load_project_path(Path(last_project), quiet=True))
        if not self.settings_store.path.exists():
            self.after(300, self.show_setup_wizard)

    def load_version(self) -> str:
        try:
            return json.loads(VERSION_FILE.read_text(encoding="utf-8"))["version"]
        except Exception:
            return "0.0.0"

    def build_ui(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(self, width=230, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        ctk.CTkLabel(
            sidebar,
            text="YouTube\nAI Studio",
            justify="left",
            font=ctk.CTkFont(size=28, weight="bold"),
        ).pack(anchor="w", padx=22, pady=(24, 4))

        ctk.CTkLabel(
            sidebar,
            text=f"Local production workspace\nVersion {self.version}",
            justify="left",
            text_color=("gray40", "gray70"),
        ).pack(anchor="w", padx=22, pady=(0, 22))

        for text, command in [
            ("New project", self.new_project),
            ("Open project", self.open_project),
            ("Save project", self.save_project),
            ("Open project folder", self.open_project_folder),
            ("Check for updates", self.check_updates),
        ]:
            ctk.CTkButton(
                sidebar, text=text, command=command, height=38
            ).pack(fill="x", padx=18, pady=5)

        ctk.CTkLabel(
            sidebar,
            text="Workflow",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", padx=22, pady=(26, 6))

        ctk.CTkLabel(
            sidebar,
            text="1. Plan the video\n2. Build scenes\n3. Generate voice\n4. Create Kling clips\n5. Export to DaVinci",
            justify="left",
            text_color=("gray35", "gray70"),
        ).pack(anchor="w", padx=22)

        main = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(main)
        top.grid(row=0, column=0, padx=18, pady=(18, 10), sticky="ew")
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(top, text="Project title").grid(
            row=0, column=0, padx=(16, 8), pady=14
        )
        self.project_title = ctk.CTkEntry(top, placeholder_text="My first YouTube video")
        self.project_title.grid(row=0, column=1, padx=(0, 12), pady=14, sticky="ew")

        self.status = ctk.CTkLabel(top, text="No project loaded", width=220)
        self.status.grid(row=0, column=2, padx=(0, 16), pady=14)

        self.tabs = ctk.CTkTabview(main)
        self.tabs.grid(row=1, column=0, padx=18, pady=(0, 18), sticky="nsew")

        self.create_dashboard_tab()
        self.create_script_tab()
        self.create_scenes_tab()
        self.create_voice_tab()
        self.create_prompts_tab()
        self.create_export_tab()
        self.create_settings_tab()

    def create_dashboard_tab(self) -> None:
        tab = self.tabs.add("Dashboard")
        tab.grid_columnconfigure((0, 1, 2), weight=1)

        cards = [
            ("Script", "Write or paste the full narration."),
            ("Scenes", "Split the story into manageable visual sections."),
            ("Voice", "Generate local Kokoro narration files."),
            ("Kling", "Store copy-ready video-generation prompts."),
            ("ComfyUI", "Store image and thumbnail prompts."),
            ("DaVinci", "Prepare organised files for editing."),
        ]
        for i, (title, body) in enumerate(cards):
            card = ctk.CTkFrame(tab)
            card.grid(row=i // 3, column=i % 3, padx=10, pady=10, sticky="nsew")
            ctk.CTkLabel(
                card, text=title, font=ctk.CTkFont(size=19, weight="bold")
            ).pack(anchor="w", padx=16, pady=(16, 5))
            ctk.CTkLabel(
                card, text=body, wraplength=260, justify="left"
            ).pack(anchor="w", padx=16, pady=(0, 18))

    def create_script_tab(self) -> None:
        tab = self.tabs.add("Script")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        bar.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            bar, text="Master narration script",
            font=ctk.CTkFont(size=18, weight="bold")
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            bar, text="Split into scenes", command=self.split_script_into_scenes
        ).grid(row=0, column=1)

        self.script_box = ctk.CTkTextbox(tab, wrap="word", font=ctk.CTkFont(size=15))
        self.script_box.grid(row=1, column=0, padx=8, pady=(0, 8), sticky="nsew")

    def create_scenes_tab(self) -> None:
        tab = self.tabs.add("Scenes")
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(tab, width=260)
        left.grid(row=0, column=0, padx=(8, 6), pady=8, sticky="ns")
        left.grid_propagate(False)

        ctk.CTkLabel(
            left, text="Scene list", font=ctk.CTkFont(size=17, weight="bold")
        ).pack(anchor="w", padx=14, pady=(14, 8))

        self.scene_list = ctk.CTkScrollableFrame(left)
        self.scene_list.pack(fill="both", expand=True, padx=8, pady=5)

        ctk.CTkButton(left, text="Add scene", command=self.add_scene).pack(
            fill="x", padx=12, pady=(6, 12)
        )

        right = ctk.CTkFrame(tab)
        right.grid(row=0, column=1, padx=(6, 8), pady=8, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(right, text="Scene title").grid(
            row=0, column=0, padx=14, pady=(14, 4), sticky="w"
        )
        self.scene_title = ctk.CTkEntry(right)
        self.scene_title.grid(row=1, column=0, padx=14, pady=(0, 8), sticky="ew")

        ctk.CTkLabel(right, text="Narration").grid(
            row=2, column=0, padx=14, pady=(4, 4), sticky="w"
        )
        self.scene_narration = ctk.CTkTextbox(right, wrap="word")
        self.scene_narration.grid(row=3, column=0, padx=14, pady=(0, 8), sticky="nsew")

        controls = ctk.CTkFrame(right, fg_color="transparent")
        controls.grid(row=4, column=0, padx=14, pady=(4, 14), sticky="ew")
        ctk.CTkButton(
            controls, text="Update selected scene", command=self.update_selected_scene
        ).pack(side="left")
        ctk.CTkButton(
            controls, text="Delete scene", command=self.delete_selected_scene
        ).pack(side="left", padx=8)

        self.scenes: list[dict] = []
        self.selected_scene_index: int | None = None

    def create_voice_tab(self) -> None:
        tab = self.tabs.add("Voice")
        tab.grid_columnconfigure(0, weight=1)

        settings = ctk.CTkFrame(tab)
        settings.grid(row=0, column=0, padx=10, pady=10, sticky="ew")

        ctk.CTkLabel(settings, text="Voice").grid(row=0, column=0, padx=14, pady=14)
        self.voice_menu = ctk.CTkOptionMenu(
            settings, values=list(VOICE_OPTIONS), width=230
        )
        self.voice_menu.set("British Female - Emma")
        self.voice_menu.grid(row=0, column=1, padx=8, pady=14)

        ctk.CTkLabel(settings, text="Speed").grid(row=0, column=2, padx=(20, 8))
        self.speed = ctk.DoubleVar(value=1.0)
        ctk.CTkSlider(
            settings, from_=0.7, to=1.3, number_of_steps=12,
            variable=self.speed, width=190
        ).grid(row=0, column=3, padx=8)
        self.speed_readout = ctk.CTkLabel(settings, textvariable=self.speed, width=60)
        self.speed_readout.grid(row=0, column=4, padx=8)

        buttons = ctk.CTkFrame(tab)
        buttons.grid(row=1, column=0, padx=10, pady=10, sticky="ew")

        ctk.CTkButton(
            buttons, text="Generate selected scene",
            command=self.generate_selected_scene
        ).pack(side="left", padx=10, pady=12)

        ctk.CTkButton(
            buttons, text="Generate all scenes",
            command=self.generate_all_scenes
        ).pack(side="left", padx=10, pady=12)

        ctk.CTkButton(
            buttons, text="Open voice folder",
            command=self.open_voice_folder
        ).pack(side="left", padx=10, pady=12)

        self.voice_status = ctk.CTkLabel(
            tab, text="Create or open a project before generating narration."
        )
        self.voice_status.grid(row=2, column=0, padx=14, pady=14, sticky="w")

    def create_prompts_tab(self) -> None:
        tab = self.tabs.add("Kling & Images")
        tab.grid_columnconfigure((0, 1), weight=1)
        tab.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            tab, text="Kling video prompt",
            font=ctk.CTkFont(size=17, weight="bold")
        ).grid(row=0, column=0, padx=10, pady=(12, 4), sticky="w")
        ctk.CTkLabel(
            tab, text="ComfyUI image prompt",
            font=ctk.CTkFont(size=17, weight="bold")
        ).grid(row=0, column=1, padx=10, pady=(12, 4), sticky="w")

        self.kling_prompt = ctk.CTkTextbox(tab, wrap="word")
        self.kling_prompt.grid(row=1, column=0, padx=10, pady=8, sticky="nsew")

        self.image_prompt = ctk.CTkTextbox(tab, wrap="word")
        self.image_prompt.grid(row=1, column=1, padx=10, pady=8, sticky="nsew")

        controls = ctk.CTkFrame(tab, fg_color="transparent")
        controls.grid(row=2, column=0, columnspan=2, padx=10, pady=10, sticky="ew")
        ctk.CTkButton(
            controls, text="Save prompts to selected scene",
            command=self.save_scene_prompts
        ).pack(side="left")
        ctk.CTkButton(
            controls, text="Copy Kling prompt",
            command=lambda: self.copy_text(self.kling_prompt.get("1.0", "end-1c"))
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            controls, text="Copy image prompt",
            command=lambda: self.copy_text(self.image_prompt.get("1.0", "end-1c"))
        ).pack(side="left")

    def create_export_tab(self) -> None:
        tab = self.tabs.add("Export")
        tab.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            tab, text="Production handoff",
            font=ctk.CTkFont(size=22, weight="bold")
        ).grid(row=0, column=0, padx=16, pady=(20, 8), sticky="w")

        ctk.CTkLabel(
            tab,
            text="Export scene narration, Kling prompts and ComfyUI prompts into organised files ready for DaVinci Resolve.",
            wraplength=800,
            justify="left",
        ).grid(row=1, column=0, padx=16, pady=(0, 18), sticky="w")

        ctk.CTkButton(
            tab, text="Export production pack",
            command=self.export_production_pack, height=44, width=220
        ).grid(row=2, column=0, padx=16, pady=8, sticky="w")

        ctk.CTkButton(
            tab, text="Open project folder",
            command=self.open_project_folder, width=220
        ).grid(row=3, column=0, padx=16, pady=8, sticky="w")

        self.export_status = ctk.CTkLabel(tab, text="")
        self.export_status.grid(row=4, column=0, padx=16, pady=16, sticky="w")

    def create_settings_tab(self) -> None:
        tab = self.tabs.add("Settings")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        page = ctk.CTkScrollableFrame(tab)
        page.grid(row=0, column=0, padx=8, pady=8, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)

        general = self._settings_section(page, "General", 0, "Application paths and startup behaviour")
        for row, (label, key) in enumerate([
            ("Application data directory", "general.app_data_dir"),
            ("Default project directory", "general.project_dir"),
            ("Logs directory", "general.logs_dir"),
            ("Temporary files directory", "general.temp_dir"),
        ], 1):
            self._setting_entry(general, row, label, key, browse="directory")
        for column, (label, key) in enumerate([
            ("Auto-save", "general.auto_save"),
            ("Open last project", "general.open_last_project"),
            ("Check updates on startup", "general.check_updates_on_startup"),
        ]):
            self._setting_check(general, 5, column, label, key)
        self._setting_entry(general, 6, "Auto-save interval (seconds)", "general.auto_save_interval")

        kokoro = self._settings_section(page, "Kokoro", 1, "A Connected badge requires a real playable WAV")
        self._status_badge(kokoro, "kokoro")
        self._setting_entry(kokoro, 1, "Installation directory", "kokoro.install_dir", browse="directory")
        self._setting_entry(kokoro, 2, "Python executable", "kokoro.python", browse="file")
        self._setting_entry(kokoro, 3, "Model repository", "kokoro.model_repo")
        self._setting_entry(kokoro, 4, "Language code", "kokoro.language")
        self._setting_entry(kokoro, 5, "Default voice", "kokoro.voice")
        self._setting_entry(kokoro, 6, "Speech speed", "kokoro.speed")
        self._setting_entry(kokoro, 7, "Generation timeout", "kokoro.timeout")
        self._setting_entry(kokoro, 8, "Hugging Face token", "secret.huggingface", secret=True)
        self._button_row(kokoro, 9, [
            ("Auto-detect", self.auto_detect_kokoro),
            ("Test Python", self.test_python_connection),
            ("Test Kokoro", lambda: self.run_kokoro_test(False)),
            ("Generate Test Voice", lambda: self.run_kokoro_test(True)),
            ("Open folder", lambda: self.open_setting_path("kokoro.install_dir")),
            ("View test log", lambda: self.view_test_log("kokoro")),
        ])

        comfy = self._settings_section(page, "ComfyUI", 2, "Connected requires a successful generated image")
        self._status_badge(comfy, "comfyui")
        fields = [
            ("Base URL", "comfyui.base_url", None), ("Workflow JSON", "comfyui.workflow_file", "file"),
            ("Positive prompt node", "comfyui.positive_node", None), ("Negative prompt node", "comfyui.negative_node", None),
            ("Seed node", "comfyui.seed_node", None), ("Width node", "comfyui.width_node", None),
            ("Height node", "comfyui.height_node", None), ("Output node", "comfyui.output_node", None),
            ("Request timeout", "comfyui.timeout", None), ("Output directory", "comfyui.output_dir", "directory"),
        ]
        for row, (label, key, browse) in enumerate(fields, 1):
            self._setting_entry(comfy, row, label, key, browse=browse)
        self._button_row(comfy, 11, [
            ("Test server", lambda: self.run_comfyui_test(False)),
            ("Validate workflow", self.validate_comfyui_workflow),
            ("Generate Test Image", lambda: self.run_comfyui_test(True)),
            ("Open ComfyUI", lambda: webbrowser.open(self._entry_value("comfyui.base_url"))),
            ("Open output", lambda: self.open_setting_path("comfyui.output_dir")),
            ("View test log", lambda: self.view_test_log("comfyui")),
        ])

        kling = self._settings_section(page, "Kling", 3, "Manual Import never reports Connected")
        self._status_badge(kling, "kling", "Manual mode")
        self._setting_option(kling, 1, "Mode", "kling.mode", ["Manual Import", "Official API"])
        for row, (label, key, secret) in enumerate([
            ("Kling project URL", "kling.project_url", False), ("Download/import folder", "kling.import_dir", False),
            ("API base URL", "kling.api_base_url", False), ("API key", "secret.kling_api_key", True),
            ("Workspace identifier", "kling.workspace", False), ("Model", "kling.model", False),
            ("Default duration", "kling.duration", False), ("Aspect ratio", "kling.aspect_ratio", False),
        ], 2):
            self._setting_entry(kling, row, label, key, secret=secret, browse="directory" if key == "kling.import_dir" else None)
        self._button_row(kling, 10, [
            ("Open Kling website", lambda: webbrowser.open(self._entry_value("kling.project_url"))),
            ("Test API", self.run_kling_test),
            ("Open import folder", lambda: self.open_setting_path("kling.import_dir")),
        ])

        ffmpeg = self._settings_section(page, "FFmpeg", 4, "Connected requires creating and probing a real MP4")
        self._status_badge(ffmpeg, "ffmpeg")
        self._setting_entry(ffmpeg, 1, "FFmpeg executable", "ffmpeg.ffmpeg", browse="file")
        self._setting_entry(ffmpeg, 2, "FFprobe executable", "ffmpeg.ffprobe", browse="file")
        self._button_row(ffmpeg, 3, [
            ("Auto-detect", self.auto_detect_ffmpeg),
            ("Test FFmpeg", self.run_ffmpeg_test),
            ("Open installation folder", lambda: self.open_setting_parent("ffmpeg.ffmpeg")),
            ("View test log", lambda: self.view_test_log("ffmpeg")),
        ])

        output = self._settings_section(page, "Output", 5, "Production defaults")
        output_fields = [
            ("Project root", "output.project_root", "directory"), ("Export directory", "output.export_dir", "directory"),
            ("Cache directory", "output.cache_dir", "directory"), ("Temporary directory", "output.temp_dir", "directory"),
            ("Resolution", "output.resolution", None), ("Frame rate", "output.frame_rate", None),
            ("Transition duration", "output.transition_duration", None), ("Video codec", "output.video_codec", None),
            ("Hardware encoder", "output.hardware_encoder", None), ("Audio codec", "output.audio_codec", None),
            ("Output quality", "output.quality", None), ("Overwrite behaviour", "output.overwrite", None),
        ]
        for row, (label, key, browse) in enumerate(output_fields, 1):
            self._setting_entry(output, row, label, key, browse=browse)

        diagnostics = self._settings_section(page, "Diagnostics", 6, "Truthful service status and redacted logs")
        self.diagnostics_box = ctk.CTkTextbox(diagnostics, height=180)
        self.diagnostics_box.grid(row=1, column=0, columnspan=3, padx=10, pady=8, sticky="ew")
        self._button_row(diagnostics, 2, [
            ("Save settings", self.save_settings_ui),
            ("Run all diagnostics", self.run_all_diagnostics),
            ("Copy report", self.copy_diagnostics),
            ("Open logs folder", lambda: self.open_setting_path("general.logs_dir")),
            ("Clear temporary files", self.clear_temporary_files),
        ])
        self.refresh_diagnostics()

    def _settings_section(self, parent, title: str, row: int, description: str):
        frame = ctk.CTkFrame(parent)
        frame.grid(row=row, column=0, padx=8, pady=8, sticky="ew")
        frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(size=19, weight="bold")).grid(row=0, column=0, padx=12, pady=(12, 2), sticky="w")
        ctk.CTkLabel(frame, text=description, text_color=("gray40", "gray70")).grid(row=0, column=1, padx=8, pady=(12, 2), sticky="w")
        return frame

    def _setting_entry(self, parent, row: int, label: str, key: str, secret: bool = False, browse: str | None = None) -> None:
        ctk.CTkLabel(parent, text=label).grid(row=row, column=0, padx=12, pady=4, sticky="w")
        entry = ctk.CTkEntry(parent, show="*" if secret else "")
        value = ""
        if secret:
            try:
                value = self.credentials.get(key.split(".", 1)[1])
            except Exception:
                pass
        else:
            section, name = key.split(".", 1)
            value = str(self.settings[section].get(name, ""))
        entry.insert(0, value)
        entry.grid(row=row, column=1, padx=8, pady=4, sticky="ew")
        self.setting_entries[key] = entry
        if browse:
            ctk.CTkButton(parent, text="Browse", width=75, command=lambda k=key, mode=browse: self.browse_setting(k, mode)).grid(row=row, column=2, padx=(0, 10), pady=4)

    def _setting_option(self, parent, row: int, label: str, key: str, values: list[str]) -> None:
        ctk.CTkLabel(parent, text=label).grid(row=row, column=0, padx=12, pady=4, sticky="w")
        section, name = key.split(".", 1)
        variable = ctk.StringVar(value=str(self.settings[section].get(name, values[0])))
        ctk.CTkOptionMenu(parent, values=values, variable=variable).grid(row=row, column=1, padx=8, pady=4, sticky="w")
        self.setting_vars[key] = variable

    def _setting_check(self, parent, row: int, column: int, label: str, key: str) -> None:
        section, name = key.split(".", 1)
        variable = ctk.BooleanVar(value=bool(self.settings[section].get(name)))
        ctk.CTkCheckBox(parent, text=label, variable=variable).grid(row=row, column=column, padx=12, pady=6, sticky="w")
        self.setting_vars[key] = variable

    def _status_badge(self, parent, name: str, initial: str = "Not configured") -> None:
        badge = ctk.CTkLabel(parent, text=initial, corner_radius=8, fg_color=("gray75", "gray30"), width=120)
        badge.grid(row=0, column=2, padx=10, pady=(12, 2), sticky="e")
        self.connection_status[name] = badge

    def _button_row(self, parent, row: int, buttons: list[tuple[str, object]]) -> None:
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.grid(row=row, column=0, columnspan=3, padx=8, pady=10, sticky="ew")
        for label, command in buttons:
            ctk.CTkButton(bar, text=label, command=command, width=120).pack(side="left", padx=4)

    def _entry_value(self, key: str) -> str:
        return self.setting_entries[key].get().strip()

    def browse_setting(self, key: str, mode: str) -> None:
        selected = filedialog.askdirectory() if mode == "directory" else filedialog.askopenfilename()
        if selected:
            entry = self.setting_entries[key]
            entry.delete(0, "end")
            entry.insert(0, selected)

    def save_settings_ui(self) -> bool:
        errors = []
        for key, entry in self.setting_entries.items():
            if key.startswith("secret."):
                name = key.split(".", 1)[1]
                try:
                    if entry.get():
                        self.credentials.set(name, entry.get())
                except Exception as exc:
                    errors.append(f"Could not store {name} securely: {exc}")
                continue
            section, name = key.split(".", 1)
            value: object = entry.get().strip()
            original = self.settings[section].get(name)
            try:
                if isinstance(original, bool): value = str(value).lower() in {"1", "true", "yes"}
                elif isinstance(original, int): value = int(value)
                elif isinstance(original, float): value = float(value)
            except ValueError:
                errors.append(f"Invalid value for {key}: {value}")
            self.settings[section][name] = value
        for key, variable in self.setting_vars.items():
            section, name = key.split(".", 1)
            self.settings[section][name] = variable.get()
        for key in ("general.app_data_dir", "general.project_dir", "general.logs_dir", "general.temp_dir", "output.project_root", "output.export_dir", "output.cache_dir", "output.temp_dir"):
            section, name = key.split(".", 1)
            ok, reason = validate_directory(str(self.settings[section][name]))
            if not ok: errors.append(reason)
        if errors:
            messagebox.showerror(APP_NAME, "\n".join(errors))
            return False
        self.settings_store.save(self.settings)
        self.status.configure(text="Settings saved")
        self.refresh_diagnostics()
        return True

    def set_connection_status(self, name: str, state: str, reason: str = "") -> None:
        colors = {"Connected": "#238636", "Failed": "#b42318", "Checking": "#9a6700", "Manual mode": "#4b5563", "Not configured": "#4b5563"}
        self.connection_status[name].configure(text=state, fg_color=colors.get(state, "#4b5563"))
        if reason:
            self.test_logs[name] = reason

    def run_background_test(self, name: str, function) -> None:
        self.set_connection_status(name, "Checking")
        def worker() -> None:
            try:
                result = function()
            except Exception as exc:
                result = {"ok": False, "reason": str(exc)}
            self.after(0, lambda: self.finish_connection_test(name, result))
        threading.Thread(target=worker, daemon=True).start()

    def finish_connection_test(self, name: str, result: dict) -> None:
        state = "Connected" if result.get("connected", result.get("ok")) else "Failed"
        if name == "kling" and self.setting_vars["kling.mode"].get() == "Manual Import":
            state = "Manual mode"
        detail = redact(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        self.set_connection_status(name, state, detail)
        self.settings.setdefault("status", {})[name] = {"state": state, "reason": result.get("reason", ""), "checked_at": datetime.now().isoformat(timespec="seconds")}
        self.settings_store.save(self.settings)
        self.refresh_diagnostics()
        if not result.get("ok"):
            messagebox.showerror(f"{name.title()} test", result.get("reason", "Test failed"))
        else:
            messagebox.showinfo(f"{name.title()} test", result.get("reason", "Connected"))

    def auto_detect_kokoro(self) -> None:
        install = self._entry_value("kokoro.install_dir") or str(Path.home() / "Downloads" / "Kokoro-TTS")
        python = detect_python(install)
        for key, value in (("kokoro.install_dir", install), ("kokoro.python", python)):
            entry = self.setting_entries[key]
            entry.delete(0, "end")
            entry.insert(0, value)
        self.set_connection_status("kokoro", "Not configured", f"Detected Python: {python or 'none'}; generate a test voice to connect.")

    def test_python_connection(self) -> None:
        self.save_settings_ui()
        config = self.settings["kokoro"]
        def test() -> dict:
            from connections import ProcessRunner
            result = ProcessRunner().run([config["python"], "--version"], 20)
            return {"ok": result.ok, "connected": False, "reason": ((result.stdout or result.stderr).strip() + "; generate a playable test voice to connect."), "command": result.command, "exit_code": result.exit_code, "stdout": result.stdout, "stderr": result.stderr}
        self.run_background_test("kokoro", test)

    def run_kokoro_test(self, generate: bool) -> None:
        if not self.save_settings_ui(): return
        logs = Path(self.settings["general"]["logs_dir"])
        self.run_background_test("kokoro", lambda: test_kokoro(self.settings["kokoro"], logs, generate))

    def validate_comfyui_workflow(self) -> None:
        if not self.save_settings_ui(): return
        ok, reason, _ = validate_workflow(self.settings["comfyui"]["workflow_file"], self.settings["comfyui"])
        self.test_logs["comfyui"] = reason
        messagebox.showinfo("ComfyUI workflow" if ok else "ComfyUI workflow error", reason)

    def run_comfyui_test(self, generate: bool) -> None:
        if not self.save_settings_ui(): return
        self.run_background_test("comfyui", lambda: test_comfyui(self.settings["comfyui"], generate))

    def run_kling_test(self) -> None:
        if not self.save_settings_ui(): return
        if self.settings["kling"]["mode"] == "Manual Import":
            self.finish_connection_test("kling", {"ok": False, "reason": "Manual Import: generation occurs on the Kling website and files are imported here."})
            return
        try:
            key = self.credentials.get("kling_api_key")
        except Exception:
            key = ""
        self.run_background_test("kling", lambda: test_kling_api(self.settings["kling"], key))

    def auto_detect_ffmpeg(self) -> None:
        ffmpeg, ffprobe = detect_ffmpeg(self._entry_value("ffmpeg.ffmpeg"))
        for key, value in (("ffmpeg.ffmpeg", ffmpeg), ("ffmpeg.ffprobe", ffprobe)):
            entry = self.setting_entries[key]
            entry.delete(0, "end")
            entry.insert(0, value)
        self.set_connection_status("ffmpeg", "Not configured", f"Detected FFmpeg: {ffmpeg or 'none'}; run the real MP4 test.")

    def run_ffmpeg_test(self) -> None:
        if not self.save_settings_ui(): return
        temp_dir = Path(self.settings["general"]["temp_dir"])
        self.run_background_test("ffmpeg", lambda: test_ffmpeg(self.settings["ffmpeg"], temp_dir))

    def open_setting_path(self, key: str) -> None:
        path = Path(self._entry_value(key))
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def open_setting_parent(self, key: str) -> None:
        path = Path(self._entry_value(key))
        os.startfile(path.parent if path.suffix else path)

    def view_test_log(self, name: str) -> None:
        text = self.test_logs.get(name, "No test has been run in this session.")
        window = ctk.CTkToplevel(self)
        window.title(f"{name.title()} test log")
        window.geometry("850x500")
        box = ctk.CTkTextbox(window)
        box.pack(fill="both", expand=True, padx=10, pady=10)
        box.insert("1.0", redact(text))

    def refresh_diagnostics(self) -> None:
        if not hasattr(self, "diagnostics_box"): return
        statuses = self.settings.get("status", {})
        report = [
            f"Application version: {self.version}", f"Operating system: {platform.platform()}",
            f"Application data: {self.settings['general']['app_data_dir']}", f"Project path: {self.settings['general']['project_dir']}",
            f"Python: {self.settings['kokoro']['python']}",
            f"Kokoro: {statuses.get('kokoro', {}).get('state', 'Not configured')} - {statuses.get('kokoro', {}).get('reason', '')}",
            f"ComfyUI: {statuses.get('comfyui', {}).get('state', 'Not configured')} - {statuses.get('comfyui', {}).get('reason', '')}",
            f"Kling: {statuses.get('kling', {}).get('state', 'Manual mode')} - {statuses.get('kling', {}).get('reason', '')}",
            f"FFmpeg: {statuses.get('ffmpeg', {}).get('state', 'Not configured')} - {statuses.get('ffmpeg', {}).get('reason', '')}",
            f"Logs: {self.settings['general']['logs_dir']}",
        ]
        self.diagnostics_box.delete("1.0", "end")
        self.diagnostics_box.insert("1.0", redact("\n".join(report)))

    def run_all_diagnostics(self) -> None:
        if not self.save_settings_ui(): return
        self.run_kokoro_test(True)
        if self.settings["comfyui"].get("workflow_file"):
            self.run_comfyui_test(True)
        else:
            self.finish_connection_test("comfyui", {"ok": False, "reason": "Optional: configure a workflow JSON before test generation."})
        self.run_kling_test()
        self.run_ffmpeg_test()

    def copy_diagnostics(self) -> None:
        self.copy_text(redact(self.diagnostics_box.get("1.0", "end-1c")))

    def clear_temporary_files(self) -> None:
        import shutil
        temp = Path(self.settings["general"]["temp_dir"]).resolve()
        app_data = Path(self.settings["general"]["app_data_dir"]).resolve()
        if temp == app_data or app_data not in temp.parents:
            messagebox.showerror(APP_NAME, "Temporary directory must be inside the application data directory.")
            return
        if temp.exists():
            for child in temp.iterdir():
                if child.is_dir(): shutil.rmtree(child)
                else: child.unlink()
        self.status.configure(text="Temporary files cleared")

    def show_setup_wizard(self) -> None:
        window = ctk.CTkToplevel(self)
        window.title("First-launch setup")
        window.geometry("680x500")
        window.transient(self)
        ctk.CTkLabel(window, text="YouTube AI Studio setup", font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w", padx=20, pady=(20, 6))
        python = detect_python(self.settings["kokoro"]["install_dir"])
        ffmpeg, _ = detect_ffmpeg()
        checks = []
        for label, path in (("Application data", self.settings["general"]["app_data_dir"]), ("Project directory", self.settings["general"]["project_dir"]), ("Output directory", self.settings["output"]["export_dir"])):
            ok, reason = validate_directory(path)
            checks.append(f"{'Ready' if ok else 'Failed'} - {label}: {reason}")
        checks.extend([
            f"{'Ready' if python else 'Failed'} - Python: {python or 'not detected'}",
            "Manual setup required - Kokoro: generate a test voice in Settings",
            "Optional - ComfyUI: configure URL and workflow in Settings",
            "Manual setup required - Kling: defaults to Manual Import",
            f"{'Manual setup required' if not ffmpeg else 'Ready for test'} - FFmpeg: {ffmpeg or 'not detected'}",
        ])
        box = ctk.CTkTextbox(window, height=280)
        box.pack(fill="both", expand=True, padx=20, pady=10)
        box.insert("1.0", "\n\n".join(checks))
        bar = ctk.CTkFrame(window, fg_color="transparent")
        bar.pack(fill="x", padx=20, pady=(0, 20))
        ctk.CTkButton(bar, text="Open Settings", command=lambda: (self.tabs.set("Settings"), window.destroy())).pack(side="left")
        ctk.CTkButton(bar, text="Skip optional services", command=lambda: (self.settings_store.save(self.settings), window.destroy())).pack(side="right")

    def new_project(self) -> None:
        title = self.project_title.get().strip() or "My YouTube Video"
        project_root = Path(self.settings["general"]["project_dir"])
        project_root.mkdir(parents=True, exist_ok=True)
        folder = project_root / safe_name(title)
        counter = 2
        original = folder
        while folder.exists():
            folder = Path(f"{original}_{counter}")
            counter += 1

        folder.mkdir(parents=True)
        for name in PROJECT_FOLDERS:
            (folder / name).mkdir(exist_ok=True)

        self.current_project_path = folder
        self.project_title.delete(0, "end")
        self.project_title.insert(0, title)
        self.scenes = []
        self.refresh_scene_list()
        self.script_box.delete("1.0", "end")
        self.status.configure(text=f"Project: {folder.name}")
        self.save_project()

    def open_project(self) -> None:
        selected = filedialog.askdirectory(
            title="Open YouTube AI Studio project",
            initialdir=self.settings["general"]["project_dir"],
        )
        if not selected:
            return
        self.load_project_path(Path(selected))

    def load_project_path(self, path: Path, quiet: bool = False) -> None:
        project_file = path / "project.json"
        if not project_file.exists():
            if not quiet:
                messagebox.showerror(APP_NAME, "This folder does not contain project.json.")
            return
        try:
            data = json.loads(project_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            if not quiet:
                messagebox.showerror(APP_NAME, f"Could not open project: {exc}")
            return
        self.current_project_path = path
        self.project_title.delete(0, "end")
        self.project_title.insert(0, data.get("title", path.name))
        self.script_box.delete("1.0", "end")
        self.script_box.insert("1.0", data.get("script", ""))
        self.scenes = data.get("scenes", [])
        self.refresh_scene_list()
        self.status.configure(text=f"Project: {path.name}")
        self.settings["general"]["last_project"] = str(path)
        self.settings_store.save(self.settings)

    def save_project(self) -> None:
        if not self.current_project_path:
            self.new_project()
            return
        self.capture_selected_scene()
        data = {
            "version": self.version,
            "title": self.project_title.get().strip(),
            "script": self.script_box.get("1.0", "end-1c"),
            "scenes": self.scenes,
            "voice": self.voice_menu.get(),
            "speed": float(self.speed.get()),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        (self.current_project_path / "project.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.current_project_path / "01_script" / "master_script.txt").write_text(
            data["script"], encoding="utf-8"
        )
        self.status.configure(text=f"Saved: {self.current_project_path.name}")
        self.settings["general"]["last_project"] = str(self.current_project_path)
        self.settings_store.save(self.settings)

    def schedule_auto_save(self) -> None:
        interval = max(15, int(self.settings["general"].get("auto_save_interval", 60)))
        if self.settings["general"].get("auto_save") and self.current_project_path:
            self.save_project()
        self.after(interval * 1000, self.schedule_auto_save)

    def split_script_into_scenes(self) -> None:
        text = self.script_box.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showwarning(APP_NAME, "Add a script first.")
            return
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        self.scenes = []
        for i, paragraph in enumerate(paragraphs, 1):
            self.scenes.append({
                "title": f"Scene {i:02d}",
                "narration": paragraph,
                "kling_prompt": "",
                "image_prompt": "",
            })
        self.refresh_scene_list()
        self.tabs.set("Scenes")

    def refresh_scene_list(self) -> None:
        for child in self.scene_list.winfo_children():
            child.destroy()
        for index, scene in enumerate(self.scenes):
            ctk.CTkButton(
                self.scene_list,
                text=f"{index+1:02d}  {scene.get('title', 'Untitled')}",
                anchor="w",
                command=lambda idx=index: self.select_scene(idx),
            ).pack(fill="x", pady=3)
        if self.scenes:
            self.select_scene(0)
        else:
            self.selected_scene_index = None
            self.clear_scene_fields()

    def select_scene(self, index: int) -> None:
        if self.selected_scene_index is not None:
            self.capture_selected_scene()
        self.selected_scene_index = index
        scene = self.scenes[index]
        self.scene_title.delete(0, "end")
        self.scene_title.insert(0, scene.get("title", ""))
        self.scene_narration.delete("1.0", "end")
        self.scene_narration.insert("1.0", scene.get("narration", ""))
        self.kling_prompt.delete("1.0", "end")
        self.kling_prompt.insert("1.0", scene.get("kling_prompt", ""))
        self.image_prompt.delete("1.0", "end")
        self.image_prompt.insert("1.0", scene.get("image_prompt", ""))

    def clear_scene_fields(self) -> None:
        for widget in [self.scene_narration, self.kling_prompt, self.image_prompt]:
            widget.delete("1.0", "end")
        self.scene_title.delete(0, "end")

    def capture_selected_scene(self) -> None:
        if self.selected_scene_index is None:
            return
        if self.selected_scene_index >= len(self.scenes):
            return
        scene = self.scenes[self.selected_scene_index]
        scene["title"] = self.scene_title.get().strip() or f"Scene {self.selected_scene_index+1:02d}"
        scene["narration"] = self.scene_narration.get("1.0", "end-1c").strip()
        scene["kling_prompt"] = self.kling_prompt.get("1.0", "end-1c").strip()
        scene["image_prompt"] = self.image_prompt.get("1.0", "end-1c").strip()

    def add_scene(self) -> None:
        self.capture_selected_scene()
        self.scenes.append({
            "title": f"Scene {len(self.scenes)+1:02d}",
            "narration": "",
            "kling_prompt": "",
            "image_prompt": "",
        })
        self.refresh_scene_list()
        self.select_scene(len(self.scenes)-1)

    def update_selected_scene(self) -> None:
        self.capture_selected_scene()
        self.refresh_scene_list()
        self.save_project()

    def delete_selected_scene(self) -> None:
        if self.selected_scene_index is None:
            return
        del self.scenes[self.selected_scene_index]
        self.selected_scene_index = None
        self.refresh_scene_list()
        self.save_project()

    def save_scene_prompts(self) -> None:
        self.capture_selected_scene()
        self.save_project()

    def copy_text(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        self.status.configure(text="Copied to clipboard")

    def get_pipeline(self, language: str):
        from kokoro import KPipeline

        if language not in self.pipeline_cache:
            self.pipeline_cache[language] = KPipeline(
                lang_code=language,
                repo_id="hexgrad/Kokoro-82M",
            )
        return self.pipeline_cache[language]

    def generate_selected_scene(self) -> None:
        self.capture_selected_scene()
        if self.selected_scene_index is None:
            messagebox.showwarning(APP_NAME, "Select a scene first.")
            return
        self.start_voice_generation([self.selected_scene_index])

    def generate_all_scenes(self) -> None:
        self.capture_selected_scene()
        if not self.scenes:
            messagebox.showwarning(APP_NAME, "Create some scenes first.")
            return
        self.start_voice_generation(list(range(len(self.scenes))))

    def start_voice_generation(self, indexes: list[int]) -> None:
        if self.generating:
            return
        if not self.current_project_path:
            messagebox.showwarning(APP_NAME, "Create or open a project first.")
            return
        self.generating = True
        self.voice_status.configure(text="Generating narration...")
        thread = threading.Thread(
            target=self.voice_worker, args=(indexes,), daemon=True
        )
        thread.start()

    def voice_worker(self, indexes: list[int]) -> None:
        try:
            voice_label = self.voice_menu.get()
            language, voice_id = VOICE_OPTIONS[voice_label]
            pipeline = self.get_pipeline(language)
            voice_dir = self.current_project_path / "02_voice"
            voice_dir.mkdir(exist_ok=True)

            for index in indexes:
                scene = self.scenes[index]
                text = scene.get("narration", "").strip()
                if not text:
                    continue
                parts = []
                for _, _, audio in pipeline(
                    text, voice=voice_id, speed=float(self.speed.get())
                ):
                    parts.append(np.asarray(audio, dtype=np.float32))
                if parts:
                    filename = f"{index+1:02d}_{safe_name(scene.get('title','scene'))}.wav"
                    sf.write(voice_dir / filename, np.concatenate(parts), 24000)

            self.after(0, lambda: self.voice_finished("Narration generated successfully."))
        except Exception as exc:
            traceback.print_exc()
            self.after(0, lambda: self.voice_finished(f"Generation failed: {exc}"))

    def voice_finished(self, message: str) -> None:
        self.generating = False
        self.voice_status.configure(text=message)

    def export_production_pack(self) -> None:
        if not self.current_project_path:
            messagebox.showwarning(APP_NAME, "Create or open a project first.")
            return
        self.capture_selected_scene()
        self.save_project()

        kling_lines = []
        image_lines = []
        production_rows = ["scene,title,narration_file,kling_prompt_file,image_prompt_file"]

        for i, scene in enumerate(self.scenes, 1):
            base = f"{i:02d}_{safe_name(scene.get('title','scene'))}"
            kling_file = self.current_project_path / "03_kling" / f"{base}.txt"
            image_file = self.current_project_path / "04_images" / f"{base}.txt"
            kling_file.write_text(scene.get("kling_prompt", ""), encoding="utf-8")
            image_file.write_text(scene.get("image_prompt", ""), encoding="utf-8")
            production_rows.append(
                f'{i},"{scene.get("title","")}","02_voice/{base}.wav",'
                f'"03_kling/{base}.txt","04_images/{base}.txt"'
            )

        (self.current_project_path / "06_davinci" / "production_sheet.csv").write_text(
            "\n".join(production_rows), encoding="utf-8"
        )
        self.export_status.configure(
            text="Production pack exported into the project folders."
        )

    def open_voice_folder(self) -> None:
        if not self.current_project_path:
            messagebox.showwarning(APP_NAME, "Create or open a project first.")
            return
        os.startfile(self.current_project_path / "02_voice")

    def open_project_folder(self) -> None:
        if not self.current_project_path:
            root = Path(self.settings["general"]["project_dir"])
            root.mkdir(parents=True, exist_ok=True)
            os.startfile(root)
        else:
            os.startfile(self.current_project_path)

    def check_updates(self) -> None:
        updater_exe = BASE_DIR / "YouTubeAIStudioUpdater.exe"
        updater_script = BASE_DIR / "check_for_updates.bat"
        if updater_exe.exists():
            subprocess.Popen([str(updater_exe)], cwd=BASE_DIR)
        elif updater_script.exists():
            subprocess.Popen(["cmd.exe", "/c", "start", "", str(updater_script)], cwd=BASE_DIR)
        else:
            messagebox.showerror(APP_NAME, "Updater files are missing.")

if __name__ == "__main__":
    if os.environ.get("YOUTUBE_AI_STUDIO_SMOKE_TEST") == "1":
        marker = os.environ.get("YOUTUBE_AI_STUDIO_SMOKE_MARKER")
        if marker:
            Path(marker).write_text("ok", encoding="utf-8")
        raise SystemExit(0)
    elif os.environ.get("YOUTUBE_AI_STUDIO_UI_SMOKE_TEST") == "1":
        studio = Studio()
        studio.update()
        studio.destroy()
    else:
        Studio().mainloop()
