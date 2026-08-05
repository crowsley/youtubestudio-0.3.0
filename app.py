from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import traceback
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
from kokoro import KPipeline

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

        self.current_project_path: Path | None = None
        self.pipeline_cache: dict[str, KPipeline] = {}
        self.generating = False

        self.build_ui()

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

    def new_project(self) -> None:
        title = self.project_title.get().strip() or "My YouTube Video"
        folder = PROJECTS_DIR / safe_name(title)
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
            initialdir=str(PROJECTS_DIR),
        )
        if not selected:
            return
        path = Path(selected)
        project_file = path / "project.json"
        if not project_file.exists():
            messagebox.showerror(APP_NAME, "This folder does not contain project.json.")
            return
        data = json.loads(project_file.read_text(encoding="utf-8"))
        self.current_project_path = path
        self.project_title.delete(0, "end")
        self.project_title.insert(0, data.get("title", path.name))
        self.script_box.delete("1.0", "end")
        self.script_box.insert("1.0", data.get("script", ""))
        self.scenes = data.get("scenes", [])
        self.refresh_scene_list()
        self.status.configure(text=f"Project: {path.name}")

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

    def get_pipeline(self, language: str) -> KPipeline:
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
            os.startfile(PROJECTS_DIR)
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
    studio = Studio()
    if os.environ.get("YOUTUBE_AI_STUDIO_SMOKE_TEST") == "1":
        studio.update()
        studio.destroy()
    else:
        studio.mainloop()
