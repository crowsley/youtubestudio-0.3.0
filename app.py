from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

if getattr(sys, "frozen", False):
    os.environ["TCL_LIBRARY"] = str(Path(sys._MEIPASS) / "_tcl_data")
    os.environ["TK_LIBRARY"] = str(Path(sys._MEIPASS) / "_tk_data")

from tkinter import filedialog, messagebox, ttk

# Windowed PyInstaller apps have no console streams, but Kokoro configures
# Loguru with stderr during import.
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")

import customtkinter as ctk
from connections import CredentialStore, detect_ffmpeg, detect_python, test_comfyui, test_ffmpeg, test_kling_api, test_kokoro, validate_workflow
from narration import CloneNarrationProvider, KokoroNarrationProvider, MicRecorder, VibeVoiceNarrationProvider, WindowsAudioPlayer, combine_wavs, clean_text, validate_wav
from settings import DATA_DIR, SettingsStore, redact, validate_directory
import urllib.error
import urllib.request
import shutil

APP_NAME = "AtoZ Voice Studio"
PROJECT_URL = "https://github.com/crowsley/atoz-voice-studio"
BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
USER_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", BASE_DIR)) / APP_NAME
PROJECTS_DIR = USER_DATA_DIR / "projects"
OUTPUT_DIR = USER_DATA_DIR / "output"
VERSION_FILE = BASE_DIR / "version.json"

# Grades from Kokoro VOICES.md — best English first for audiobook narration.
VOICE_OPTIONS = {
    "American Female - Heart (A)": ("a", "af_heart"),
    "American Female - Bella (A-)": ("a", "af_bella"),
    "American Female - Nicole (B-)": ("a", "af_nicole"),
    "American Female - Aoede (C+)": ("a", "af_aoede"),
    "American Female - Kore (C+)": ("a", "af_kore"),
    "American Female - Sarah (C+)": ("a", "af_sarah"),
    "American Female - Alloy (C)": ("a", "af_alloy"),
    "American Female - Nova (C)": ("a", "af_nova"),
    "American Female - Sky (C-)": ("a", "af_sky"),
    "American Female - Jessica (D)": ("a", "af_jessica"),
    "American Female - River (D)": ("a", "af_river"),
    "American Male - Fenrir (C+)": ("a", "am_fenrir"),
    "American Male - Michael (C+)": ("a", "am_michael"),
    "American Male - Puck (C+)": ("a", "am_puck"),
    "American Male - Echo (D)": ("a", "am_echo"),
    "American Male - Eric (D)": ("a", "am_eric"),
    "American Male - Liam (D)": ("a", "am_liam"),
    "American Male - Onyx (D)": ("a", "am_onyx"),
    "American Male - Adam (F+)": ("a", "am_adam"),
    "American Male - Santa (D-)": ("a", "am_santa"),
    "British Female - Emma (B-)": ("b", "bf_emma"),
    "British Female - Isabella (C)": ("b", "bf_isabella"),
    "British Female - Alice (D)": ("b", "bf_alice"),
    "British Female - Lily (D)": ("b", "bf_lily"),
    "British Male - Fable (C)": ("b", "bm_fable"),
    "British Male - George (C)": ("b", "bm_george"),
    "British Male - Daniel (D)": ("b", "bm_daniel"),
    "British Male - Lewis (D+)": ("b", "bm_lewis"),
}
VIBEVOICE_OPTIONS = {name.title(): ("", name) for name in ("alloy", "echo", "fable", "onyx", "nova", "shimmer")}
CLONE_OPTIONS = {
    "Reference sample (clone)": ("", "reference"),
    "Alloy": ("", "alloy"),
    "Echo": ("", "echo"),
    "Fable": ("", "fable"),
    "Onyx": ("", "onyx"),
    "Nova": ("", "nova"),
    "Shimmer": ("", "shimmer"),
}
LANGUAGE_CODES = {"American English": "a", "British English": "b"}
LANGUAGE_LABELS = {code: label for label, code in LANGUAGE_CODES.items()}
ENGINE_OPTIONS = ["Kokoro", "VibeVoice Realtime", "Clone (OpenAI API)"]

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

WORKFLOW_STEPS = [
    ("1. Plan the video", "Script"),
    ("2. Build scenes", "Scenes"),
    ("3. Generate voice", "Voice"),
    ("4. Create Kling clips", "Scenes"),
    ("5. Export to DaVinci", "Export"),
]

def safe_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*]+', "", value.strip())
    value = re.sub(r"\s+", "_", value)
    return value[:80] or "untitled_project"

def estimate_seconds(text: str, speed: float = 1.0) -> float:
    words = len(re.findall(r"\w+", text or ""))
    # ponytail: ~150 wpm heuristic; replace with measured audioDuration when available
    return round(words / max(0.1, 2.5 * max(0.5, speed)), 2)

def format_srt_time(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"

def csv_cell(value: object) -> str:
    text = str(value or "")
    return f'"{text.replace(chr(34), chr(34)+chr(34))}"'

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
        self.voice_provider = None
        self.failed_voice_indexes: list[int] = []
        self.audio_player = WindowsAudioPlayer()
        self.mic_recorder = MicRecorder()
        self.project_narration: dict = {}

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
            text="AtoZ\nVoice Studio",
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

        self.workflow_buttons: list[ctk.CTkButton] = []
        for label, tab in WORKFLOW_STEPS:
            button = ctk.CTkButton(
                sidebar,
                text=label,
                anchor="w",
                height=32,
                fg_color="transparent",
                text_color=("gray20", "gray80"),
                hover_color=("gray80", "gray30"),
                command=lambda t=tab: self.goto_tab(t),
            )
            button.pack(fill="x", padx=14, pady=2)
            self.workflow_buttons.append(button)

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
        self.project_title = ctk.CTkEntry(top, placeholder_text="My first project")
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
        self.refresh_progress()

    def goto_tab(self, name: str) -> None:
        try:
            self.tabs.set(name)
        except Exception:
            pass
        self.refresh_progress()

    def create_dashboard_tab(self) -> None:
        tab = self.tabs.add("Dashboard")
        tab.grid_columnconfigure((0, 1, 2), weight=1)
        self.dashboard_status = ctk.CTkLabel(
            tab, text="Create or open a project to track progress.",
            font=ctk.CTkFont(size=15), justify="left",
        )
        self.dashboard_status.grid(row=0, column=0, columnspan=3, padx=14, pady=(14, 6), sticky="w")

        cards = [
            ("Script", "Script", "Write or paste the full narration."),
            ("Scenes", "Scenes", "Split and edit scenes with prompts."),
            ("Voice", "Voice", "Generate local narration files."),
            ("Kling", "Scenes", "Per-scene video prompts (on Scenes)."),
            ("Images", "Scenes", "Per-scene image prompts (on Scenes)."),
            ("Export", "Export", "SRT, duration report and production pack."),
        ]
        self.dashboard_cards: dict[str, ctk.CTkLabel] = {}
        for i, (title, tab_name, body) in enumerate(cards):
            card = ctk.CTkFrame(tab)
            card.grid(row=1 + i // 3, column=i % 3, padx=10, pady=10, sticky="nsew")
            ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=19, weight="bold")).pack(
                anchor="w", padx=16, pady=(16, 5)
            )
            status = ctk.CTkLabel(card, text="—", text_color=("gray35", "gray70"))
            status.pack(anchor="w", padx=16, pady=(0, 4))
            self.dashboard_cards[title] = status
            ctk.CTkLabel(card, text=body, wraplength=260, justify="left").pack(
                anchor="w", padx=16, pady=(0, 8)
            )
            ctk.CTkButton(
                card, text="Open", width=90, command=lambda t=tab_name: self.goto_tab(t)
            ).pack(anchor="w", padx=16, pady=(0, 16))

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

        list_actions = ctk.CTkFrame(left, fg_color="transparent")
        list_actions.pack(fill="x", padx=8, pady=(4, 12))
        ctk.CTkButton(list_actions, text="Add", command=self.add_scene, width=70).pack(side="left", padx=2)
        ctk.CTkButton(list_actions, text="Up", command=lambda: self.move_scene(-1), width=50).pack(side="left", padx=2)
        ctk.CTkButton(list_actions, text="Down", command=lambda: self.move_scene(1), width=55).pack(side="left", padx=2)

        right = ctk.CTkFrame(tab)
        right.grid(row=0, column=1, padx=(6, 8), pady=8, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(3, weight=2)
        right.grid_rowconfigure(5, weight=1)
        right.grid_rowconfigure(7, weight=1)

        ctk.CTkLabel(right, text="Scene title").grid(
            row=0, column=0, padx=14, pady=(14, 4), sticky="w"
        )
        self.scene_title = ctk.CTkEntry(right)
        self.scene_title.grid(row=1, column=0, padx=14, pady=(0, 8), sticky="ew")

        ctk.CTkLabel(right, text="Narration").grid(
            row=2, column=0, padx=14, pady=(4, 4), sticky="w"
        )
        self.scene_narration = ctk.CTkTextbox(right, wrap="word", height=120)
        self.scene_narration.grid(row=3, column=0, padx=14, pady=(0, 4), sticky="nsew")
        self.scene_estimate = ctk.CTkLabel(right, text="Est. 0.0s", text_color=("gray40", "gray70"))
        self.scene_estimate.grid(row=4, column=0, padx=14, pady=(0, 6), sticky="w")

        prompts = ctk.CTkFrame(right, fg_color="transparent")
        prompts.grid(row=5, column=0, padx=10, pady=4, sticky="nsew")
        prompts.grid_columnconfigure((0, 1), weight=1)
        prompts.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(prompts, text="Kling video prompt", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, padx=4, pady=(0, 4), sticky="w"
        )
        ctk.CTkLabel(prompts, text="ComfyUI image prompt", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=1, padx=4, pady=(0, 4), sticky="w"
        )
        self.kling_prompt = ctk.CTkTextbox(prompts, wrap="word", height=90)
        self.kling_prompt.grid(row=1, column=0, padx=4, pady=0, sticky="nsew")
        self.image_prompt = ctk.CTkTextbox(prompts, wrap="word", height=90)
        self.image_prompt.grid(row=1, column=1, padx=4, pady=0, sticky="nsew")

        controls = ctk.CTkFrame(right, fg_color="transparent")
        controls.grid(row=6, column=0, padx=14, pady=(8, 14), sticky="ew")
        ctk.CTkButton(controls, text="Update scene", command=self.update_selected_scene).pack(side="left")
        ctk.CTkButton(controls, text="Delete", command=self.delete_selected_scene).pack(side="left", padx=8)
        ctk.CTkButton(controls, text="AI prompts", command=self.ai_fill_selected_prompts).pack(side="left", padx=4)
        ctk.CTkButton(controls, text="AI all scenes", command=self.ai_fill_all_prompts).pack(side="left", padx=4)
        ctk.CTkButton(
            controls, text="Copy Kling",
            command=lambda: self.copy_text(self.kling_prompt.get("1.0", "end-1c")),
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            controls, text="Copy image",
            command=lambda: self.copy_text(self.image_prompt.get("1.0", "end-1c")),
        ).pack(side="left", padx=4)

        self.scenes: list[dict] = []
        self.selected_scene_index: int | None = None
        self.scene_narration.bind("<KeyRelease>", lambda _e: self.update_scene_estimate())

    def create_voice_tab(self) -> None:
        tab = self.tabs.add("Voice")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(7, weight=1)

        guide = ctk.CTkFrame(tab)
        guide.grid(row=0, column=0, padx=10, pady=(10, 4), sticky="ew")
        ctk.CTkLabel(guide, text="Quick guide", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", padx=12, pady=(10, 2)
        )
        self.voice_guide = ctk.CTkLabel(
            guide,
            text="",
            justify="left",
            wraplength=980,
            text_color=("gray30", "gray75"),
        )
        self.voice_guide.pack(anchor="w", padx=12, pady=(0, 10))

        settings = ctk.CTkFrame(tab)
        settings.grid(row=1, column=0, padx=10, pady=6, sticky="ew")

        self.voice_badge = ctk.CTkLabel(settings, text="Not generated", corner_radius=8, fg_color=("gray75", "gray30"), width=120)
        self.voice_badge.grid(row=0, column=0, padx=12, pady=12)
        ctk.CTkLabel(settings, text="Engine").grid(row=0, column=1, padx=(8, 4))
        self.voice_engine = ctk.CTkOptionMenu(settings, values=ENGINE_OPTIONS, command=self.change_voice_engine, width=160)
        self.voice_engine.set("Kokoro")
        self.voice_engine.grid(row=0, column=2, padx=4, pady=12)
        ctk.CTkLabel(settings, text="Voice").grid(row=0, column=3, padx=(8, 4))
        self.voice_menu = ctk.CTkOptionMenu(
            settings, values=list(VOICE_OPTIONS), width=250
        )
        self.voice_menu.set("American Female - Heart (A)")
        self.voice_menu.grid(row=0, column=4, padx=4, pady=12)

        ctk.CTkLabel(settings, text="Language").grid(row=0, column=5, padx=(12, 4))
        self.voice_language = ctk.CTkOptionMenu(settings, values=list(LANGUAGE_CODES), width=150)
        self.voice_language.set("American English")
        self.voice_language.grid(row=0, column=6, padx=4)
        ctk.CTkLabel(settings, text="Speed").grid(row=0, column=7, padx=(12, 4))
        self.speed = ctk.DoubleVar(value=1.0)
        self.speed.trace_add("write", lambda *_: self.refresh_duration_estimate())
        ctk.CTkSlider(
            settings, from_=0.7, to=1.3, number_of_steps=12,
            variable=self.speed, width=140
        ).grid(row=0, column=8, padx=4)
        self.speed_readout = ctk.CTkLabel(settings, textvariable=self.speed, width=60)
        self.speed_readout.grid(row=0, column=9, padx=4)

        sample_bar = ctk.CTkFrame(tab)
        sample_bar.grid(row=2, column=0, padx=10, pady=(2, 4), sticky="ew")
        ctk.CTkLabel(sample_bar, text="Clone sample", font=ctk.CTkFont(weight="bold")).pack(
            side="left", padx=(10, 8), pady=8
        )
        self.record_button = ctk.CTkButton(sample_bar, text="Record mic", command=self.toggle_mic_recording, width=110)
        self.record_button.pack(side="left", padx=4, pady=8)
        ctk.CTkButton(sample_bar, text="Play sample", command=self.play_clone_sample, width=110).pack(side="left", padx=4, pady=8)
        ctk.CTkButton(sample_bar, text="Browse WAV", command=self.browse_clone_sample, width=110).pack(side="left", padx=4, pady=8)
        ctk.CTkButton(sample_bar, text="Use for clone", command=self.use_clone_sample_now, width=120).pack(side="left", padx=4, pady=8)
        self.clone_sample_label = ctk.CTkLabel(sample_bar, text="No sample yet — record 10–20s of clear speech")
        self.clone_sample_label.pack(side="left", padx=10)

        generate_bar = ctk.CTkFrame(tab)
        generate_bar.grid(row=3, column=0, padx=10, pady=(4, 2), sticky="ew")
        self.voice_action_buttons = []
        for label, command in [
            ("Preview text", self.preview_selected_text),
            ("Generate scene", self.generate_selected_scene),
            ("Generate all", self.generate_all_scenes),
            ("Full narration", self.generate_full_narration),
            ("Retry failed", self.retry_failed_generation),
            ("Cancel", self.cancel_voice_generation),
        ]:
            button = ctk.CTkButton(generate_bar, text=label, command=command, width=120)
            button.pack(side="left", padx=4, pady=8)
            self.voice_action_buttons.append(button)

        utility_bar = ctk.CTkFrame(tab)
        utility_bar.grid(row=4, column=0, padx=10, pady=(2, 6), sticky="ew")
        for label, command in [
            ("Open folder", self.open_voice_folder),
            ("Open log", self.open_voice_log),
        ]:
            button = ctk.CTkButton(utility_bar, text=label, command=command, width=120)
            button.pack(side="left", padx=4, pady=6)
            self.voice_action_buttons.append(button)
        self.duration_estimate = ctk.CTkLabel(utility_bar, text="Estimated total: 0.0s")
        self.duration_estimate.pack(side="left", padx=12)

        self.voice_status = ctk.CTkLabel(
            tab, text="Create or open a project before generating narration."
        )
        self.voice_status.grid(row=5, column=0, padx=14, pady=(6, 2), sticky="w")
        self.voice_progress = ctk.CTkProgressBar(tab)
        self.voice_progress.set(0)
        self.voice_progress.grid(row=6, column=0, padx=14, pady=6, sticky="ew")
        self.voice_log = ctk.CTkTextbox(tab, height=140, wrap="word")
        self.voice_log.grid(row=7, column=0, padx=14, pady=6, sticky="nsew")
        player = ctk.CTkFrame(tab)
        player.grid(row=8, column=0, padx=10, pady=(4, 10), sticky="ew")
        for label, command in [("Play", self.play_audio), ("Pause", self.pause_audio), ("Stop", self.stop_audio), ("Replay", self.replay_audio), ("Delete scene audio", self.delete_selected_audio)]:
            ctk.CTkButton(player, text=label, command=command, width=105).pack(side="left", padx=4, pady=8)
        self.audio_seek = ctk.CTkSlider(player, from_=0, to=1, command=self.seek_audio, width=170)
        self.audio_seek.pack(side="left", padx=8)
        self.audio_volume = ctk.CTkSlider(player, from_=0, to=1, command=self.set_audio_volume, width=100)
        self.audio_volume.set(1)
        self.audio_volume.pack(side="left", padx=8)
        self.audio_details = ctk.CTkLabel(player, text="No audio loaded")
        self.audio_details.pack(side="left", padx=8)
        self.refresh_clone_sample_label()
        self.refresh_voice_guide()

    def change_voice_engine(self, engine: str) -> None:
        if engine == "VibeVoice Realtime":
            options = VIBEVOICE_OPTIONS
        elif engine == "Clone (OpenAI API)":
            options = CLONE_OPTIONS
        else:
            options = VOICE_OPTIONS
        self.voice_menu.configure(values=list(options))
        self.voice_menu.set(next(iter(options)))
        self.voice_language.configure(state="disabled" if engine != "Kokoro" else "normal")
        self.refresh_voice_guide()

    def refresh_voice_guide(self) -> None:
        if not hasattr(self, "voice_guide"):
            return
        engine = self.voice_engine.get() if hasattr(self, "voice_engine") else "Kokoro"
        if engine == "Clone (OpenAI API)":
            text = (
                "1. Click Record mic and speak clearly for 10–20 seconds (or Browse WAV).\n"
                "2. Click Use for clone (engine + reference sample are set).\n"
                "3. In Settings → Clone, set your clone server Base URL, then Generate scene.\n"
                "Note: cloning needs a separate OpenAI-compatible TTS/clone server — recording alone is not enough."
            )
        elif engine == "VibeVoice Realtime":
            text = (
                "1. Start your VibeVoice server (run_vibevoice_realtime.bat).\n"
                "2. Pick a voice → Preview text or Generate scene.\n"
                "3. Export production pack, then import WAVs + subtitles.srt in DaVinci Resolve."
            )
        else:
            text = (
                "Easiest path (audiobooks / shorts): 1) Pick a Kokoro voice (Heart/Bella/Emma are strongest) → "
                "2) Generate scene or Generate all → 3) Full narration → 4) Export → open DaVinci folder.\n"
                "To clone your own voice, switch Engine to Clone (OpenAI API) and follow the steps shown there."
            )
        self.voice_guide.configure(text=text)

    def clone_sample_path(self) -> Path:
        configured = Path(str(self.settings.get("clone", {}).get("reference_wav", "") or ""))
        if configured.is_file():
            return configured
        return USER_DATA_DIR / "voice_samples" / "clone-reference.wav"

    def refresh_clone_sample_label(self) -> None:
        if not hasattr(self, "clone_sample_label"):
            return
        path = Path(str(self.settings.get("clone", {}).get("reference_wav", "") or ""))
        if path.is_file():
            self.clone_sample_label.configure(text=f"Sample: {path.name}")
        else:
            self.clone_sample_label.configure(text="No sample yet — record 10–20s of clear speech")

    def toggle_mic_recording(self) -> None:
        if self.mic_recorder.recording:
            self.stop_mic_recording()
        else:
            self.start_mic_recording()

    def start_mic_recording(self) -> None:
        if self.generating:
            messagebox.showwarning(APP_NAME, "Wait for narration to finish before recording.")
            return
        try:
            self.mic_recorder.start()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not start microphone: {exc}")
            return
        self.record_button.configure(text="Stop recording", fg_color="#b42318")
        self.clone_sample_label.configure(text="Recording… speak clearly, then Stop")
        self.status.configure(text="Recording microphone sample")
        self.append_voice_log("Microphone recording started")

    def stop_mic_recording(self) -> None:
        output = USER_DATA_DIR / "voice_samples" / f"clone-reference-{datetime.now().strftime('%Y%m%d-%H%M%S')}.wav"
        try:
            info = self.mic_recorder.stop(output)
        except Exception as exc:
            self.mic_recorder.cancel()
            self.record_button.configure(text="Record mic", fg_color=["#3B8ED0", "#1F6AA5"])
            messagebox.showerror(APP_NAME, str(exc))
            return
        self.settings.setdefault("clone", {})["reference_wav"] = str(output)
        self.settings_store.save(self.settings)
        if "clone.reference_wav" in self.setting_entries:
            entry = self.setting_entries["clone.reference_wav"]
            entry.delete(0, "end")
            entry.insert(0, str(output))
        self.record_button.configure(text="Record mic", fg_color=["#3B8ED0", "#1F6AA5"])
        self.refresh_clone_sample_label()
        self.append_voice_log(f"Saved clone sample {output.name} ({info['duration']:.1f}s)")
        self.status.configure(text=f"Saved clone sample ({info['duration']:.1f}s)")
        try:
            self.load_audio(output, autoplay=True)
        except Exception:
            pass

    def play_clone_sample(self) -> None:
        path = Path(str(self.settings.get("clone", {}).get("reference_wav", "") or ""))
        if not path.is_file():
            messagebox.showwarning(APP_NAME, "Record or browse a sample first.")
            return
        self.load_audio(path, autoplay=True)

    def browse_clone_sample(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose clone reference WAV",
            filetypes=[("WAV audio", "*.wav"), ("All files", "*.*")],
        )
        if not selected:
            return
        path = Path(selected)
        try:
            validate_wav(path)
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        self.settings.setdefault("clone", {})["reference_wav"] = str(path)
        self.settings_store.save(self.settings)
        if "clone.reference_wav" in self.setting_entries:
            entry = self.setting_entries["clone.reference_wav"]
            entry.delete(0, "end")
            entry.insert(0, str(path))
        self.refresh_clone_sample_label()
        self.status.configure(text=f"Clone sample set: {path.name}")

    def use_clone_sample_now(self) -> None:
        path = Path(str(self.settings.get("clone", {}).get("reference_wav", "") or ""))
        if not path.is_file():
            messagebox.showwarning(APP_NAME, "Record or browse a WAV sample first.")
            return
        self.voice_engine.set("Clone (OpenAI API)")
        self.change_voice_engine("Clone (OpenAI API)")
        if "Reference sample (clone)" in CLONE_OPTIONS:
            self.voice_menu.set("Reference sample (clone)")
        self.status.configure(text="Clone engine ready — set server URL in Settings if needed, then Generate")
        messagebox.showinfo(
            APP_NAME,
            "Clone sample is ready.\n\n"
            "Next: Settings → Clone → set Base URL to your clone TTS server.\n"
            "Then Generate scene.\n\n"
            "Recording alone does not clone — the server does the voice cloning.",
        )

    def create_prompts_tab(self) -> None:
        tab = self.tabs.add("Kling & Images")
        tab.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            tab, text="Prompts live on the Scenes tab",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, padx=16, pady=(24, 8), sticky="w")
        ctk.CTkLabel(
            tab,
            text="Edit Kling and ComfyUI prompts next to each scene's narration. Use the buttons below for the selected scene.",
            wraplength=720, justify="left",
        ).grid(row=1, column=0, padx=16, pady=(0, 16), sticky="w")
        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.grid(row=2, column=0, padx=16, pady=8, sticky="w")
        ctk.CTkButton(bar, text="Open Scenes", command=lambda: self.goto_tab("Scenes")).pack(side="left")
        ctk.CTkButton(
            bar, text="Copy Kling prompt",
            command=lambda: self.copy_text(self.kling_prompt.get("1.0", "end-1c")),
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            bar, text="Copy image prompt",
            command=lambda: self.copy_text(self.image_prompt.get("1.0", "end-1c")),
        ).pack(side="left")
        ctk.CTkButton(bar, text="Save selected scene", command=self.save_scene_prompts).pack(side="left", padx=8)

    def create_export_tab(self) -> None:
        tab = self.tabs.add("Export")
        tab.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            tab, text="Production handoff",
            font=ctk.CTkFont(size=22, weight="bold")
        ).grid(row=0, column=0, padx=16, pady=(20, 8), sticky="w")

        ctk.CTkLabel(
            tab,
            text="For YouTube / TikTok / audiobooks: generate narration here, then Import the WAV + subtitles.srt into DaVinci Resolve and place audio on the timeline under your clips. This app does not edit video inside DaVinci.",
            wraplength=800,
            justify="left",
        ).grid(row=1, column=0, padx=16, pady=(0, 18), sticky="w")

        ctk.CTkButton(
            tab, text="Export production pack",
            command=self.export_production_pack, height=44, width=220
        ).grid(row=2, column=0, padx=16, pady=8, sticky="w")

        ctk.CTkButton(
            tab, text="Open DaVinci folder",
            command=self.open_davinci_folder, width=220
        ).grid(row=3, column=0, padx=16, pady=8, sticky="w")

        ctk.CTkButton(
            tab, text="Open project folder",
            command=self.open_project_folder, width=220
        ).grid(row=4, column=0, padx=16, pady=8, sticky="w")

        self.export_status = ctk.CTkLabel(tab, text="")
        self.export_status.grid(row=5, column=0, padx=16, pady=16, sticky="w")

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

        vibevoice = self._settings_section(page, "VibeVoice Realtime", 2, "Optional expressive local TTS server; designed for low-VRAM CUDA")
        self._setting_entry(vibevoice, 1, "Base URL", "vibevoice.base_url")
        self._setting_entry(vibevoice, 2, "Model", "vibevoice.model")
        self._setting_entry(vibevoice, 3, "Default voice", "vibevoice.voice")
        self._setting_entry(vibevoice, 4, "Request timeout", "vibevoice.timeout")
        self._button_row(vibevoice, 5, [("Open server", lambda: webbrowser.open(self._entry_value("vibevoice.base_url")))])

        clone = self._settings_section(page, "Clone (OpenAI-compatible)", 3, "1) Record or browse a WAV on the Voice tab  2) Set Base URL to a clone TTS server  3) Generate with Clone engine")
        self._setting_entry(clone, 1, "Base URL", "clone.base_url")
        self._setting_entry(clone, 2, "Model", "clone.model")
        self._setting_entry(clone, 3, "Default voice id", "clone.voice")
        self._setting_entry(clone, 4, "Reference WAV sample", "clone.reference_wav", browse="file")
        self._setting_entry(clone, 5, "API key (optional)", "secret.clone_api_key", secret=True)
        self._setting_entry(clone, 6, "Request timeout", "clone.timeout")
        self._button_row(clone, 7, [("Open Voice tab to record", lambda: self.goto_tab("Voice"))])

        ollama = self._settings_section(page, "Ollama (AI prompts)", 4, "Local LLM for Kling/image prompt assist from scene narration")
        self._setting_entry(ollama, 1, "Base URL", "ollama.base_url")
        self._setting_entry(ollama, 2, "Model", "ollama.model")
        self._setting_entry(ollama, 3, "Request timeout", "ollama.timeout")
        self._button_row(ollama, 4, [("Test Ollama", self.test_ollama)])

        comfy = self._settings_section(page, "ComfyUI", 5, "Connected requires a successful generated image")
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

        kling = self._settings_section(page, "Kling", 6, "Manual Import never reports Connected")
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

        ffmpeg = self._settings_section(page, "FFmpeg", 7, "Connected requires creating and probing a real MP4")
        self._status_badge(ffmpeg, "ffmpeg")
        self._setting_entry(ffmpeg, 1, "FFmpeg executable", "ffmpeg.ffmpeg", browse="file")
        self._setting_entry(ffmpeg, 2, "FFprobe executable", "ffmpeg.ffprobe", browse="file")
        self._button_row(ffmpeg, 3, [
            ("Auto-detect", self.auto_detect_ffmpeg),
            ("Test FFmpeg", self.run_ffmpeg_test),
            ("Open installation folder", lambda: self.open_setting_parent("ffmpeg.ffmpeg")),
            ("View test log", lambda: self.view_test_log("ffmpeg")),
        ])

        output = self._settings_section(page, "Output", 8, "Folders and narration gap (video encode settings removed — app does not render video yet)")
        output_fields = [
            ("Project root", "output.project_root", "directory"),
            ("Export directory", "output.export_dir", "directory"),
            ("Cache directory", "output.cache_dir", "directory"),
            ("Temporary directory", "output.temp_dir", "directory"),
            ("Silence between scenes (seconds)", "narration.silence", None),
        ]
        for row, (label, key, browse) in enumerate(output_fields, 1):
            self._setting_entry(output, row, label, key, browse=browse)

        diagnostics = self._settings_section(page, "Diagnostics", 9, "Truthful service status and redacted logs")
        self.diagnostics_box = ctk.CTkTextbox(diagnostics, height=180)
        self.diagnostics_box.grid(row=1, column=0, columnspan=3, padx=10, pady=8, sticky="ew")
        self._button_row(diagnostics, 2, [
            ("Save settings", self.save_settings_ui),
            ("Run all diagnostics", self.run_all_diagnostics),
            ("Copy report", self.copy_diagnostics),
            ("Open logs folder", lambda: self.open_setting_path("general.logs_dir")),
            ("Clear temporary files", self.clear_temporary_files),
        ])

        about = self._settings_section(page, "About", 10, f"{APP_NAME} v{self.version}")
        ctk.CTkLabel(
            about,
            text="Free community software under the MIT License.\nA local production studio for YouTube videos, narration and audiobooks. Built with Kokoro, VibeVoice and other open-source components; third-party components retain their own licences.",
            justify="left", wraplength=760,
        ).grid(row=1, column=0, columnspan=3, padx=12, pady=8, sticky="w")
        self._button_row(about, 2, [
            ("Open GitHub", lambda: webbrowser.open(PROJECT_URL)),
            ("Releases", lambda: webbrowser.open(f"{PROJECT_URL}/releases")),
            ("Report an issue", lambda: webbrowser.open(f"{PROJECT_URL}/issues")),
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
        install = self._entry_value("kokoro.install_dir")
        if not install or not Path(install).is_dir():
            install = next(
                (
                    str(path)
                    for path in (
                        Path.home() / "Downloads" / "Kokoro-TTS",
                        Path.home() / "Kokoro-TTS",
                        DATA_DIR / "Kokoro-TTS",
                    )
                    if path.is_dir()
                ),
                "",
            )
        python = detect_python(install)
        for key, value in (("kokoro.install_dir", install), ("kokoro.python", python)):
            entry = self.setting_entries[key]
            entry.delete(0, "end")
            entry.insert(0, value)
        note = (
            f"Detected Python: {python}; generate a test voice to connect."
            if python
            else "Kokoro not found. Browse to your Kokoro-TTS folder (often Downloads\\Kokoro-TTS), then Test Kokoro."
        )
        self.set_connection_status("kokoro", "Not configured", note)

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
        ctk.CTkLabel(window, text="AtoZ Voice Studio setup", font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w", padx=20, pady=(20, 6))
        python = detect_python(self.settings["kokoro"]["install_dir"])
        ffmpeg, _ = detect_ffmpeg()
        checks = []
        for label, path in (("Application data", self.settings["general"]["app_data_dir"]), ("Project directory", self.settings["general"]["project_dir"]), ("Output directory", self.settings["output"]["export_dir"])):
            ok, reason = validate_directory(path)
            checks.append(f"{'Ready' if ok else 'Failed'} - {label}: {reason}")
        checks.extend([
            f"{'Ready' if python else 'Failed'} - Python: {python or 'not detected'}",
            "Manual setup required - Kokoro: click Auto-detect or Browse to your Kokoro-TTS folder, then Generate Test Voice",
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
        title = self.project_title.get().strip() or "My Voice Project"
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
        self.project_narration = {}
        self.refresh_scene_list()
        self.script_box.delete("1.0", "end")
        self.status.configure(text=f"Project: {folder.name}")
        self.save_project()
        self.refresh_progress()

    def open_project(self) -> None:
        root = Path(self.settings["general"]["project_dir"])
        root.mkdir(parents=True, exist_ok=True)
        projects = sorted(
            [p for p in root.iterdir() if p.is_dir() and (p / "project.json").exists()],
            key=lambda p: (p / "project.json").stat().st_mtime,
            reverse=True,
        )
        if not projects:
            selected = filedialog.askdirectory(
                title="Open AtoZ Voice Studio project",
                initialdir=str(root),
            )
            if selected:
                self.load_project_path(Path(selected))
            return

        window = ctk.CTkToplevel(self)
        window.title("Open project")
        window.geometry("520x420")
        window.transient(self)
        ctk.CTkLabel(window, text="Recent projects", font=ctk.CTkFont(size=18, weight="bold")).pack(
            anchor="w", padx=16, pady=(16, 8)
        )
        list_frame = ctk.CTkScrollableFrame(window)
        list_frame.pack(fill="both", expand=True, padx=16, pady=8)

        def choose(path: Path) -> None:
            window.destroy()
            self.load_project_path(path)

        for path in projects[:20]:
            ctk.CTkButton(
                list_frame, text=path.name, anchor="w", command=lambda p=path: choose(p)
            ).pack(fill="x", pady=3)

        bar = ctk.CTkFrame(window, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(4, 16))

        def browse() -> None:
            selected = filedialog.askdirectory(title="Open AtoZ Voice Studio project", initialdir=str(root))
            if selected:
                window.destroy()
                self.load_project_path(Path(selected))

        ctk.CTkButton(bar, text="Browse…", command=browse).pack(side="left")
        ctk.CTkButton(bar, text="Cancel", command=window.destroy).pack(side="right")

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
        self.scenes = [self.migrate_scene(scene) for scene in data.get("scenes", [])]
        self.project_narration = data.get("narration", {})
        engine = data.get("voice_engine", "Kokoro")
        if engine not in ENGINE_OPTIONS:
            engine = "Kokoro"
        self.voice_engine.set(engine)
        self.change_voice_engine(engine)
        if engine == "VibeVoice Realtime":
            options = VIBEVOICE_OPTIONS
        elif engine == "Clone (OpenAI API)":
            options = CLONE_OPTIONS
        else:
            options = VOICE_OPTIONS
        if data.get("voice") in options:
            self.voice_menu.set(data["voice"])
        self.speed.set(float(data.get("speed", 1.0)))
        self.repair_audio_state()
        self.refresh_scene_list()
        self.status.configure(text=f"Project: {path.name}")
        self.settings["general"]["last_project"] = str(path)
        self.settings_store.save(self.settings)
        self.refresh_progress()

    def save_project(self) -> None:
        if not self.current_project_path:
            self.new_project()
            return
        self.capture_selected_scene()
        data = {
            "schema_version": 2,
            "version": self.version,
            "title": self.project_title.get().strip(),
            "script": self.script_box.get("1.0", "end-1c"),
            "scenes": self.scenes,
            "voice_engine": self.voice_engine.get(),
            "voice": self.voice_menu.get(),
            "speed": float(self.speed.get()),
            "narration": self.project_narration,
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
        self.refresh_progress()

    def migrate_scene(self, scene: dict) -> dict:
        migrated = dict(scene)
        migrated.setdefault("narrationText", migrated.get("narration", ""))
        migrated.setdefault("voiceId", "")
        migrated.setdefault("voiceSpeed", 1.0)
        migrated.setdefault("audioPath", "")
        migrated.setdefault("audioDuration", 0)
        migrated.setdefault("audioFileSize", 0)
        migrated.setdefault("audioStatus", "Not generated")
        migrated.setdefault("audioGeneratedAt", "")
        migrated.setdefault("audioError", "")
        return migrated

    def repair_audio_state(self) -> None:
        for scene in self.scenes:
            path = Path(scene.get("audioPath", "")) if scene.get("audioPath") else None
            if scene.get("audioStatus") == "Complete" and (not path or not path.is_file()):
                scene["audioStatus"] = "Not generated"
                scene["audioError"] = "Referenced narration file is missing"
        path = Path(self.project_narration.get("combinedNarrationPath", "")) if self.project_narration.get("combinedNarrationPath") else None
        if self.project_narration.get("narrationStatus") == "Complete" and (not path or not path.is_file()):
            self.project_narration["narrationStatus"] = "Not generated"

    def narration_summary(self) -> str:
        complete = sum(scene.get("audioStatus") == "Complete" for scene in self.scenes)
        return f"Narration: {complete} of {len(self.scenes)} scenes complete"

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
            self.scenes.append(self.migrate_scene({
                "title": f"Scene {i:02d}",
                "narration": paragraph,
                "kling_prompt": "",
                "image_prompt": "",
            }))
        self.refresh_scene_list(prefer=0)
        self.tabs.set("Scenes")
        self.refresh_progress()

    def refresh_scene_list(self, prefer: int | None = None) -> None:
        keep = self.selected_scene_index if prefer is None else prefer
        self.selected_scene_index = None
        for child in self.scene_list.winfo_children():
            child.destroy()
        for index, scene in enumerate(self.scenes):
            mark = "●" if scene.get("audioStatus") == "Complete" else "○"
            ctk.CTkButton(
                self.scene_list,
                text=f"{index+1:02d} {mark}  {scene.get('title', 'Untitled')}",
                anchor="w",
                command=lambda idx=index: self.select_scene(idx),
            ).pack(fill="x", pady=3)
        if self.scenes:
            index = keep if keep is not None and 0 <= keep < len(self.scenes) else 0
            self.select_scene(index)
        else:
            self.clear_scene_fields()
        self.refresh_duration_estimate()

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
        self.voice_badge.configure(text=scene.get("audioStatus", "Not generated"))
        self.update_scene_estimate()
        if scene.get("audioPath") and Path(scene["audioPath"]).is_file():
            self.load_audio(Path(scene["audioPath"]))

    def clear_scene_fields(self) -> None:
        for widget in [self.scene_narration, self.kling_prompt, self.image_prompt]:
            widget.delete("1.0", "end")
        self.scene_title.delete(0, "end")
        if hasattr(self, "scene_estimate"):
            self.scene_estimate.configure(text="Est. 0.0s")

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

    def update_scene_estimate(self) -> None:
        if not hasattr(self, "scene_estimate"):
            return
        text = self.scene_narration.get("1.0", "end-1c") if hasattr(self, "scene_narration") else ""
        speed = float(self.speed.get()) if hasattr(self, "speed") else 1.0
        measured = 0.0
        if self.selected_scene_index is not None and self.selected_scene_index < len(self.scenes):
            measured = float(self.scenes[self.selected_scene_index].get("audioDuration") or 0)
        seconds = measured or estimate_seconds(text, speed)
        label = "Audio" if measured else "Est."
        self.scene_estimate.configure(text=f"{label} {seconds:.1f}s")

    def refresh_duration_estimate(self) -> None:
        if not hasattr(self, "duration_estimate"):
            return
        speed = float(self.speed.get()) if hasattr(self, "speed") else 1.0
        total = 0.0
        for scene in self.scenes:
            measured = float(scene.get("audioDuration") or 0)
            total += measured or estimate_seconds(scene.get("narration", ""), speed)
        silence = float(self.settings.get("narration", {}).get("silence", 0.25)) * max(0, len(self.scenes) - 1)
        self.duration_estimate.configure(text=f"Estimated total: {total + silence:.1f}s")
        self.update_scene_estimate()

    def refresh_progress(self) -> None:
        if not hasattr(self, "dashboard_cards"):
            return
        script_ok = bool(self.script_box.get("1.0", "end-1c").strip()) if hasattr(self, "script_box") else False
        scenes_n = len(self.scenes)
        voice_done = sum(scene.get("audioStatus") == "Complete" for scene in self.scenes)
        kling_done = sum(bool(scene.get("kling_prompt", "").strip()) for scene in self.scenes)
        image_done = sum(bool(scene.get("image_prompt", "").strip()) for scene in self.scenes)
        exported = False
        if self.current_project_path:
            exported = (self.current_project_path / "06_davinci" / "production_sheet.csv").exists()
        statuses = {
            "Script": "Ready" if script_ok else "Missing",
            "Scenes": f"{scenes_n} scenes" if scenes_n else "None",
            "Voice": f"{voice_done}/{scenes_n} done" if scenes_n else "—",
            "Kling": f"{kling_done}/{scenes_n} prompts" if scenes_n else "—",
            "Images": f"{image_done}/{scenes_n} prompts" if scenes_n else "—",
            "Export": "Exported" if exported else "Not exported",
        }
        for key, text in statuses.items():
            self.dashboard_cards[key].configure(text=text)
        summary = f"{'Project open' if self.current_project_path else 'No project'} · script {'✓' if script_ok else '—'} · scenes {scenes_n} · voice {voice_done}/{scenes_n} · prompts {kling_done}/{scenes_n}"
        self.dashboard_status.configure(text=summary)
        done_flags = [script_ok, scenes_n > 0, scenes_n > 0 and voice_done == scenes_n, scenes_n > 0 and kling_done == scenes_n, exported]
        for button, done in zip(self.workflow_buttons, done_flags):
            button.configure(text_color=("#0f7b3a" if done else ("gray20", "gray80")))

    def add_scene(self) -> None:
        self.capture_selected_scene()
        self.scenes.append(self.migrate_scene({
            "title": f"Scene {len(self.scenes)+1:02d}",
            "narration": "",
            "kling_prompt": "",
            "image_prompt": "",
        }))
        self.refresh_scene_list(prefer=len(self.scenes) - 1)
        self.refresh_progress()

    def move_scene(self, delta: int) -> None:
        if self.selected_scene_index is None:
            return
        self.capture_selected_scene()
        index = self.selected_scene_index
        target = index + delta
        if target < 0 or target >= len(self.scenes):
            return
        self.scenes[index], self.scenes[target] = self.scenes[target], self.scenes[index]
        self.refresh_scene_list(prefer=target)
        self.save_project()

    def update_selected_scene(self) -> None:
        self.capture_selected_scene()
        self.refresh_scene_list(prefer=self.selected_scene_index)
        self.save_project()

    def delete_selected_scene(self) -> None:
        if self.selected_scene_index is None:
            return
        index = self.selected_scene_index
        del self.scenes[index]
        prefer = min(index, len(self.scenes) - 1) if self.scenes else None
        self.selected_scene_index = None
        self.refresh_scene_list(prefer=prefer)
        self.save_project()

    def save_scene_prompts(self) -> None:
        self.capture_selected_scene()
        self.save_project()

    def copy_text(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        self.status.configure(text="Copied to clipboard")

    def generate_selected_scene(self) -> None:
        self.capture_selected_scene()
        if self.selected_scene_index is None:
            messagebox.showwarning(APP_NAME, "Select a scene first.")
            return
        scene = self.scenes[self.selected_scene_index]
        if scene.get("audioStatus") == "Complete" and Path(scene.get("audioPath", "")).is_file():
            if not messagebox.askyesno(APP_NAME, "Narration already exists. Regenerate it?"):
                return
        self.start_voice_generation([self.selected_scene_index], regenerate=True)

    def generate_all_scenes(self) -> None:
        self.capture_selected_scene()
        self.start_voice_generation(list(range(len(self.scenes))))

    def validate_voice_request(self, indexes: list[int]) -> bool:
        errors = []
        if not self.current_project_path:
            errors.append("Create or open a project first.")
        if not self.project_title.get().strip():
            errors.append("Project title is required.")
        if not self.scenes:
            errors.append("Add or split scenes first.")
        has_scene_text = indexes and any(clean_text(self.scenes[i].get("narration", "")) for i in indexes)
        has_master = bool(self.script_box.get("1.0", "end-1c").strip())
        if not has_scene_text and not has_master:
            errors.append("Add narration text to the selected scenes (or a master script).")
        elif indexes and not has_scene_text:
            errors.append("The selected scenes contain no narration text.")
        if errors:
            messagebox.showerror("Narration validation", "\n".join(errors))
            return False
        return True

    def start_voice_generation(self, indexes: list[int], regenerate: bool = False, preview_text: str = "", combine: bool = False) -> None:
        if self.generating:
            return
        if not preview_text and not combine and not self.validate_voice_request(indexes):
            return
        engine = self.voice_engine.get()
        if engine == "VibeVoice Realtime":
            config = dict(self.settings["vibevoice"])
            options = VIBEVOICE_OPTIONS
        elif engine == "Clone (OpenAI API)":
            config = dict(self.settings["clone"])
            try:
                config["api_key"] = self.credentials.get("clone_api_key")
            except Exception:
                config["api_key"] = ""
            options = CLONE_OPTIONS
        else:
            config = dict(self.settings["kokoro"])
            options = VOICE_OPTIONS
        label = self.voice_menu.get()
        language, voice_id = options[label]
        lang_code = LANGUAGE_CODES.get(self.voice_language.get(), self.voice_language.get())
        language, speed = language or lang_code, float(self.speed.get())
        self.generating = True
        self.failed_voice_indexes = []
        self.set_voice_busy(True, "Validating", 0)
        self.voice_log.delete("1.0", "end")
        thread = threading.Thread(
            target=self.voice_worker, args=(indexes, config, engine, voice_id, language, speed, regenerate, preview_text, combine), daemon=True
        )
        thread.start()

    def voice_worker(self, indexes: list[int], config: dict, engine: str, voice_id: str, language: str, speed: float, regenerate: bool, preview_text: str, combine: bool) -> None:
        project = self.current_project_path
        log_file = project / "logs" / "voice-generation.log"
        if engine == "VibeVoice Realtime":
            provider_type = VibeVoiceNarrationProvider
        elif engine == "Clone (OpenAI API)":
            provider_type = CloneNarrationProvider
        else:
            provider_type = KokoroNarrationProvider
        self.voice_provider = provider_type(config, log_file)
        started, completed, skipped = time.monotonic(), 0, 0
        try:
            if preview_text:
                output = project / "audio" / "previews" / f"preview-{datetime.now().strftime('%Y%m%d-%H%M%S')}.wav"
                result = self.voice_provider.generate(preview_text[:500], voice_id, language, speed, output, self.voice_event)
                self.after(0, lambda: self.finish_voice_run(result, [], True))
                return
            if combine:
                paths = [Path(scene["audioPath"]) for scene in self.scenes if scene.get("audioStatus") == "Complete"]
                if len(paths) != len([s for s in self.scenes if clean_text(s.get("narration", ""))]): raise ValueError("Generate all required scene narration before combining.")
                self.after(0, lambda: self.set_voice_busy(True, "Verifying", .9))
                info = combine_wavs(paths, project / "audio" / "narration.wav", float(self.settings.get("narration", {}).get("silence", .25)))
                self.project_narration = {"combinedNarrationPath": info["path"], "combinedNarrationDuration": info["duration"], "narrationStatus": "Complete", "narrationUpdatedAt": datetime.now().isoformat(timespec="seconds")}
                self.after(0, lambda: self.finish_combination(info))
                return
            required = [i for i in indexes if clean_text(self.scenes[i].get("narration", ""))]
            queued = []
            for index in required:
                scene = self.scenes[index]
                if scene.get("audioStatus") == "Complete" and not regenerate and Path(scene.get("audioPath", "")).is_file():
                    skipped += 1; continue
                scene["audioStatus"], scene["audioError"] = "Queued", ""
                output = project / "audio" / "scenes" / f"scene-{index+1:03d}.wav"
                queued.append({"index": index, "text": scene.get("narration", ""), "output": str(output)})
            if len(queued) > 1:
                results = self.voice_provider.generate_batch(queued, voice_id, language, speed, self.voice_event)
            else:
                results = {job["index"]: self.voice_provider.generate(job["text"], voice_id, language, speed, Path(job["output"]), self.voice_event) for job in queued}
            for job in queued:
                index, result = job["index"], results[job["index"]]
                scene = self.scenes[index]
                if not result.success:
                    scene["audioStatus"] = "Cancelled" if result.cancelled else "Failed"
                    scene["audioError"] = result.error
                    self.failed_voice_indexes.append(index)
                    if result.cancelled: break
                    continue
                scene.update({"narrationText": clean_text(scene.get("narration", "")), "voiceId": voice_id, "voiceSpeed": speed, "audioPath": result.output, "audioDuration": result.duration, "audioFileSize": result.file_size, "audioStatus": "Complete", "audioGeneratedAt": datetime.now().isoformat(timespec="seconds"), "audioError": ""})
                completed += 1
                self.after(0, self.save_project)
            message = f"Complete: {completed}; skipped: {skipped}; failed: {len(self.failed_voice_indexes)}; elapsed: {time.monotonic()-started:.1f}s"
            self.after(0, lambda: self.voice_finished(message, bool(self.failed_voice_indexes)))
        except Exception as exc:
            self.after(0, lambda: self.voice_finished(f"Generation failed: {exc}", True))

    def voice_event(self, event: dict) -> None:
        if event.get("event") == "item_start":
            current, total, index = event["current"], event["total"], event["index"]
            self.after(0, lambda: self.set_voice_busy(True, f"Generating scene {index+1} ({current}/{total})", (current-1)/max(1, total)))
        stage = str(event.get("stage", "")).replace("_", " ").title()
        message = stage or event.get("message") or json.dumps(event)
        self.after(0, lambda: self.append_voice_log(message))

    def append_voice_log(self, message: str) -> None:
        self.voice_log.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n")
        self.voice_log.see("end")

    def set_voice_busy(self, active: bool, status: str, progress: float) -> None:
        self.generating = active
        self.voice_status.configure(text=f"{status} — {self.narration_summary()}")
        self.voice_badge.configure(text=status, fg_color="#9a6700" if active else ("gray75", "gray30"))
        self.voice_progress.set(max(0, min(1, progress)))
        for button in self.voice_action_buttons:
            button.configure(state="normal" if not active or button.cget("text") == "Cancel" else "disabled")

    def voice_finished(self, message: str, failed: bool = False) -> None:
        self.generating = False
        state = "Failed" if failed else "Complete"
        self.set_voice_busy(False, state, 1 if not failed else self.voice_progress.get())
        self.voice_status.configure(text=message)
        self.append_voice_log(message)
        self.save_project()
        self.refresh_scene_list(prefer=self.selected_scene_index)
        self.refresh_progress()

    def finish_voice_run(self, result, indexes: list[int], autoplay: bool = False) -> None:
        if result.success:
            self.voice_finished(f"Complete — {result.output} — {result.duration:.2f}s, {result.file_size} bytes")
            self.load_audio(Path(result.output), autoplay)
        else:
            state = "Cancelled" if result.cancelled else "Failed"
            self.set_voice_busy(False, state, 0)
            self.append_voice_log(result.error or result.stderr)
            messagebox.showerror("Narration error", result.error or result.stderr or "Generation failed")

    def preview_selected_text(self) -> None:
        try: text = self.script_box.get("sel.first", "sel.last")
        except Exception: text = self.scenes[self.selected_scene_index].get("narration", "") if self.selected_scene_index is not None else ""
        text = clean_text(text)
        if not text: messagebox.showwarning(APP_NAME, "Select script text or choose a scene with narration."); return
        self.start_voice_generation([], preview_text=text[:500])

    def generate_full_narration(self) -> None:
        if not self.validate_voice_request(list(range(len(self.scenes)))): return
        self.start_voice_generation([], combine=True)

    def finish_combination(self, info: dict) -> None:
        self.voice_finished(f"Combined narration complete — {info['duration']:.2f}s, {info['file_size']} bytes")
        self.load_audio(Path(info["path"]), False)

    def cancel_voice_generation(self) -> None:
        if self.generating and self.voice_provider:
            self.voice_provider.cancel(); self.append_voice_log("Cancellation requested")

    def retry_failed_generation(self) -> None:
        indexes = self.failed_voice_indexes or [i for i, scene in enumerate(self.scenes) if scene.get("audioStatus") in {"Failed", "Cancelled"}]
        if not indexes: messagebox.showinfo(APP_NAME, "There are no failed scenes to retry."); return
        self.start_voice_generation(indexes, regenerate=True)

    def delete_selected_audio(self) -> None:
        if self.selected_scene_index is None: return
        scene = self.scenes[self.selected_scene_index]
        path = Path(scene.get("audioPath", "")) if scene.get("audioPath") else None
        if path and path.exists() and messagebox.askyesno(APP_NAME, f"Delete {path.name}?"):
            path.unlink(); scene.update({"audioPath": "", "audioStatus": "Not generated", "audioDuration": 0, "audioFileSize": 0}); self.save_project()

    def load_audio(self, path: Path, autoplay: bool = False) -> None:
        try:
            info = validate_wav(path); self.audio_player.load(path); self.audio_seek.configure(to=max(1, info["duration"])); self.audio_details.configure(text=f"{path.name} — {info['duration']:.2f}s — {info['file_size']} bytes")
            if autoplay: self.audio_player.play()
        except Exception as exc: messagebox.showerror("Audio playback", str(exc))

    def selected_audio_path(self) -> Path | None:
        if self.selected_scene_index is not None and self.scenes[self.selected_scene_index].get("audioPath"): return Path(self.scenes[self.selected_scene_index]["audioPath"])
        if self.project_narration.get("combinedNarrationPath"): return Path(self.project_narration["combinedNarrationPath"])
        return None

    def play_audio(self) -> None:
        try:
            if not self.audio_player.path:
                path = self.selected_audio_path()
                if not path: raise ValueError("No generated audio is selected")
                self.load_audio(path)
            self.audio_player.play(); self.update_audio_position()
        except Exception as exc: messagebox.showerror("Audio playback", str(exc))
    def pause_audio(self) -> None:
        try: self.audio_player.pause()
        except Exception as exc: messagebox.showerror("Audio playback", str(exc))
    def stop_audio(self) -> None:
        try: self.audio_player.stop(); self.audio_seek.set(0)
        except Exception as exc: messagebox.showerror("Audio playback", str(exc))
    def replay_audio(self) -> None: self.stop_audio(); self.play_audio()
    def seek_audio(self, seconds: float) -> None:
        if self.audio_player.path:
            try: self.audio_player.seek(int(float(seconds)*1000)); self.audio_player.play()
            except Exception: pass
    def set_audio_volume(self, value: float) -> None:
        if self.audio_player.path:
            try: self.audio_player.volume(float(value))
            except Exception: pass
    def update_audio_position(self) -> None:
        try:
            if self.audio_player.path: self.audio_seek.set(self.audio_player.position()/1000); self.after(500, self.update_audio_position)
        except Exception: pass

    def open_voice_log(self) -> None:
        if not self.current_project_path: return
        path = self.current_project_path / "logs" / "voice-generation.log"; path.parent.mkdir(parents=True, exist_ok=True); path.touch(exist_ok=True); os.startfile(path)

    def export_production_pack(self) -> None:
        if not self.current_project_path:
            messagebox.showwarning(APP_NAME, "Create or open a project first.")
            return
        self.capture_selected_scene()
        self.save_project()

        warnings = []
        missing_audio = [i + 1 for i, s in enumerate(self.scenes) if clean_text(s.get("narration", "")) and s.get("audioStatus") != "Complete"]
        missing_kling = [i + 1 for i, s in enumerate(self.scenes) if not str(s.get("kling_prompt", "")).strip()]
        missing_image = [i + 1 for i, s in enumerate(self.scenes) if not str(s.get("image_prompt", "")).strip()]
        if missing_audio:
            warnings.append(f"Scenes without narration audio: {', '.join(map(str, missing_audio))}")
        if missing_kling:
            warnings.append(f"Scenes without Kling prompts: {', '.join(map(str, missing_kling))}")
        if missing_image:
            warnings.append(f"Scenes without image prompts: {', '.join(map(str, missing_image))}")
        if warnings:
            detail = "\n".join(warnings) + "\n\nExport anyway?"
            if not messagebox.askyesno("Export validation", detail):
                return

        production_rows = ["scene,title,narration_file,duration_seconds,kling_prompt_file,image_prompt_file"]
        duration_rows = ["scene,title,duration_seconds,source,word_count"]
        srt_blocks: list[str] = []
        cursor = 0.0
        speed = float(self.speed.get())
        silence = float(self.settings.get("narration", {}).get("silence", 0.25))
        davinci = self.current_project_path / "06_davinci"
        voice_out = self.current_project_path / "02_voice"
        davinci.mkdir(parents=True, exist_ok=True)
        voice_out.mkdir(parents=True, exist_ok=True)

        for i, scene in enumerate(self.scenes, 1):
            base = f"{i:02d}_{safe_name(scene.get('title','scene'))}"
            kling_file = self.current_project_path / "03_kling" / f"{base}.txt"
            image_file = self.current_project_path / "04_images" / f"{base}.txt"
            kling_file.write_text(scene.get("kling_prompt", ""), encoding="utf-8")
            image_file.write_text(scene.get("image_prompt", ""), encoding="utf-8")
            narration_file = scene.get("audioPath", "")
            if narration_file and Path(narration_file).is_file():
                dest = voice_out / f"{base}.wav"
                shutil.copy2(narration_file, dest)
                narration_file = str(dest)
            measured = float(scene.get("audioDuration") or 0)
            text = scene.get("narration", "")
            duration = measured or estimate_seconds(text, speed)
            source = "audio" if measured else "estimate"
            production_rows.append(
                ",".join([
                    str(i),
                    csv_cell(scene.get("title", "")),
                    csv_cell(narration_file),
                    f"{duration:.2f}",
                    csv_cell(f"03_kling/{base}.txt"),
                    csv_cell(f"04_images/{base}.txt"),
                ])
            )
            duration_rows.append(
                ",".join([
                    str(i),
                    csv_cell(scene.get("title", "")),
                    f"{duration:.2f}",
                    source,
                    str(len(re.findall(r"\w+", text))),
                ])
            )
            if clean_text(text):
                start, end = cursor, cursor + max(0.4, duration)
                srt_blocks.append(
                    f"{len(srt_blocks) + 1}\n{format_srt_time(start)} --> {format_srt_time(end)}\n{text.strip()}\n"
                )
                cursor = end + silence

        combined = Path(self.project_narration.get("combinedNarrationPath", "") or "")
        if combined.is_file():
            shutil.copy2(combined, davinci / "full_narration.wav")
            shutil.copy2(combined, voice_out / "full_narration.wav")

        (davinci / "production_sheet.csv").write_text("\n".join(production_rows), encoding="utf-8")
        (davinci / "duration_report.csv").write_text("\n".join(duration_rows), encoding="utf-8")
        (davinci / "subtitles.srt").write_text("\n".join(srt_blocks).strip() + ("\n" if srt_blocks else ""), encoding="utf-8")
        (davinci / "README_IMPORT.txt").write_text(
            "DaVinci Resolve import\n"
            "1. File > Import > Media: bring in 02_voice/*.wav (or full_narration.wav) and your video clips.\n"
            "2. Drop clips on V1, narration on A1; trim to match duration_report.csv.\n"
            "3. File > Import > Subtitle: subtitles.srt for captions (YouTube/TikTok).\n"
            "4. For audiobook-only uploads, export audio from the timeline or use full_narration.wav directly.\n",
            encoding="utf-8",
        )
        note = "Exported WAV copies, production_sheet.csv, duration_report.csv, subtitles.srt and README_IMPORT.txt."
        if warnings:
            note += " Some scenes were incomplete."
        self.export_status.configure(text=note)
        self.refresh_progress()

    def open_davinci_folder(self) -> None:
        if not self.current_project_path:
            messagebox.showwarning(APP_NAME, "Create or open a project first.")
            return
        folder = self.current_project_path / "06_davinci"
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def test_ollama(self) -> None:
        if not self.save_settings_ui():
            return
        def worker() -> None:
            try:
                text = self.ollama_generate("Reply with exactly: OK")
                self.after(0, lambda: messagebox.showinfo("Ollama", f"Connected. Model replied: {text[:120]}"))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Ollama", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def ollama_generate(self, prompt: str) -> str:
        config = self.settings.get("ollama", {})
        base = str(config.get("base_url", "http://127.0.0.1:11434")).rstrip("/")
        payload = json.dumps({
            "model": config.get("model", "llama3.2"),
            "prompt": prompt,
            "stream": False,
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{base}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=float(config.get("timeout", 120))) as response:
            data = json.loads(response.read().decode("utf-8"))
        text = str(data.get("response", "")).strip()
        if not text:
            raise RuntimeError("Ollama returned an empty response. Is the model pulled?")
        return text

    def ai_prompt_for_scene(self, narration: str) -> tuple[str, str]:
        prompt = (
            "You help faceless YouTube/TikTok/audiobook producers.\n"
            "Given scene narration, write TWO prompts.\n"
            "Return exactly this format (no markdown):\n"
            "KLING: <one cinematic video prompt, 1-2 sentences>\n"
            "IMAGE: <one still/thumbnail image prompt, 1-2 sentences>\n\n"
            f"Narration:\n{narration[:1200]}"
        )
        raw = self.ollama_generate(prompt)
        kling, image = "", ""
        for line in raw.splitlines():
            upper = line.upper()
            if upper.startswith("KLING:"):
                kling = line.split(":", 1)[1].strip()
            elif upper.startswith("IMAGE:"):
                image = line.split(":", 1)[1].strip()
        if not kling and not image:
            kling = raw.strip()
        return kling, image

    def ai_fill_selected_prompts(self) -> None:
        self.capture_selected_scene()
        if self.selected_scene_index is None:
            messagebox.showwarning(APP_NAME, "Select a scene first.")
            return
        index = self.selected_scene_index
        narration = self.scenes[index].get("narration", "")
        if not clean_text(narration):
            messagebox.showwarning(APP_NAME, "Scene has no narration text.")
            return
        self.status.configure(text="Asking Ollama for prompts…")

        def worker() -> None:
            try:
                kling, image = self.ai_prompt_for_scene(narration)
                def apply() -> None:
                    self.scenes[index]["kling_prompt"] = kling
                    self.scenes[index]["image_prompt"] = image
                    if self.selected_scene_index == index:
                        self.kling_prompt.delete("1.0", "end")
                        self.kling_prompt.insert("1.0", kling)
                        self.image_prompt.delete("1.0", "end")
                        self.image_prompt.insert("1.0", image)
                    self.save_project()
                    self.status.configure(text="AI prompts filled")
                self.after(0, apply)
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Ollama", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def ai_fill_all_prompts(self) -> None:
        self.capture_selected_scene()
        targets = [i for i, s in enumerate(self.scenes) if clean_text(s.get("narration", ""))]
        if not targets:
            messagebox.showwarning(APP_NAME, "No scenes with narration.")
            return
        if not messagebox.askyesno(APP_NAME, f"Generate Kling + image prompts for {len(targets)} scenes with Ollama?"):
            return
        self.status.configure(text="Ollama: filling scene prompts…")

        def worker() -> None:
            errors = []
            for i in targets:
                try:
                    kling, image = self.ai_prompt_for_scene(self.scenes[i].get("narration", ""))
                    self.scenes[i]["kling_prompt"] = kling
                    self.scenes[i]["image_prompt"] = image
                except Exception as exc:
                    errors.append(f"Scene {i+1}: {exc}")
                    break
            def done() -> None:
                self.save_project()
                if self.selected_scene_index is not None:
                    self.select_scene(self.selected_scene_index)
                if errors:
                    messagebox.showerror("Ollama", "\n".join(errors))
                else:
                    self.status.configure(text=f"AI prompts filled for {len(targets)} scenes")
            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def open_voice_folder(self) -> None:
        if not self.current_project_path:
            messagebox.showwarning(APP_NAME, "Create or open a project first.")
            return
        folder = self.current_project_path / "audio"
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def open_project_folder(self) -> None:
        if not self.current_project_path:
            root = Path(self.settings["general"]["project_dir"])
            root.mkdir(parents=True, exist_ok=True)
            os.startfile(root)
        else:
            os.startfile(self.current_project_path)

    def check_updates(self) -> None:
        updater_exe = BASE_DIR / "AtoZVoiceStudioUpdater.exe"
        updater_script = BASE_DIR / "check_for_updates.bat"
        if not messagebox.askyesno(APP_NAME, "Check GitHub for an update? If one is available, the project will be saved and the app will close so Windows can replace its files."):
            return
        if self.current_project_path:
            self.save_project()
        if updater_exe.exists():
            subprocess.Popen([str(updater_exe)], cwd=BASE_DIR)
            self.after(750, self.destroy)
        elif updater_script.exists():
            subprocess.Popen(["cmd.exe", "/c", "start", "", str(updater_script)], cwd=BASE_DIR)
            self.after(750, self.destroy)
        else:
            messagebox.showerror(APP_NAME, "Updater files are missing.")

if __name__ == "__main__":
    if os.environ.get("ATOZ_VOICE_STUDIO_SMOKE_TEST") == "1":
        marker = os.environ.get("ATOZ_VOICE_STUDIO_SMOKE_MARKER")
        if marker:
            Path(marker).write_text(json.dumps({"version": Studio.load_version(None), "updater": (BASE_DIR / "AtoZVoiceStudioUpdater.exe").is_file()}), encoding="utf-8")
        raise SystemExit(0)
    elif os.environ.get("ATOZ_VOICE_STUDIO_UI_SMOKE_TEST") == "1":
        studio = Studio()
        studio.update()
        studio.destroy()
    else:
        Studio().mainloop()
