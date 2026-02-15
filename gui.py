import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
import logging, sys, json

def _setup_logging():
    from logging.handlers import RotatingFileHandler
    if getattr(sys, 'frozen', False):
        log_path = Path(sys.executable).parent / "debug.log"
    else:
        log_path = Path(__file__).parent / "debug.log"
    handler = RotatingFileHandler(
        str(log_path), maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(handler)
    logging.info("=== App started ===")
_setup_logging()

from constants import APP_NAME, APP_VERSION
from save_manager import is_game_running
from gui_tab_renamer import SaveRenamerTab
from gui_tab_gamedata import GameDataTab
from gui_tab_catnames import CatNamePoolTab

# ── Main Application ────────────────────────────────────────────────

class MewgenicsRenamerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry("780x620")
        self.root.minsize(700, 550)
        self.root.resizable(True, True)

        self._busy = False
        self._build_ui()

    def _build_ui(self):
        # Bottom bar: Mewtator toggle + status
        bottom = ttk.Frame(self.root)
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(2, 8))

        saved = self._load_settings()
        self.mewtator_var = tk.BooleanVar(value=saved.get("mewtator", False))
        ttk.Checkbutton(bottom, text="Mewtator mod loader",
                        variable=self.mewtator_var).pack(side=tk.LEFT, padx=(0, 10))

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(bottom, textvariable=self.status_var,
                  relief=tk.SUNKEN, anchor=tk.W).pack(side=tk.LEFT, fill=tk.X, expand=True)

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=6, pady=(6, 0))

        # Tab 1 — Save Renamer
        save_frame = ttk.Frame(notebook)
        notebook.add(save_frame, text="  Save Renamer  ")
        self.save_tab = SaveRenamerTab(save_frame, self._set_status)

        # Tab 2 — Game Data Modder
        gd_frame = ttk.Frame(notebook)
        notebook.add(gd_frame, text="  Game Data Modder  ")
        self.gamedata_tab = GameDataTab(gd_frame, self._set_status, self.mewtator_var)

        # Tab 3 — Cat Name Pools
        cn_frame = ttk.Frame(notebook)
        notebook.add(cn_frame, text="  Cat Name Pools  ")
        self.catname_tab = CatNamePoolTab(cn_frame, self._set_status, self.mewtator_var)

        # Refresh button states when Mewtator checkbox is toggled
        self.mewtator_var.trace_add("write", self._on_mewtator_toggle)

    def _on_mewtator_toggle(self, *_args):
        """Refresh buttons and offer to migrate files when Mewtator is toggled."""
        self._save_settings()
        self.gamedata_tab._sync_buttons()
        self.catname_tab._sync_buttons()

        gd = self.gamedata_tab
        if not gd.game_dir:
            return

        from game_data_manager import has_loose_files, remove_loose_files, write_mewtator_meta

        now_mewtator = self.mewtator_var.get()
        mew_dir = gd._mewtator_dir()

        # Old location = opposite of current checkbox
        old_out = None if now_mewtator else mew_dir
        if not has_loose_files(gd.game_dir, output_dir=old_out):
            return  # nothing in old location

        target = "Mewtator mod folder" if now_mewtator else "game directory"
        source_label = "game directory" if now_mewtator else "Mewtator mod folder"
        if not messagebox.askyesno("Migrate overrides",
                f"Override files are installed in the {source_label}.\n\n"
                f"Move them to the {target}?"):
            return

        if is_game_running():
            messagebox.showwarning("Game running",
                                   "Close Mewgenics before migrating files.")
            return

        if now_mewtator and not (Path(gd.game_dir) / "Mewtator" / "Mewtator.exe").exists():
            messagebox.showwarning("Mewtator not found",
                "Mewtator does not appear to be installed.\n\n"
                "Files cannot be migrated.")
            return

        if self._busy:
            return
        self._busy = True
        try:
            # Remove from old location
            remove_loose_files(gd.game_dir, output_dir=old_out)

            # Re-apply to new location
            new_out = mew_dir if now_mewtator else None
            n = 0
            n += gd._migrate_files(new_out)
            n += self.catname_tab._migrate_files(new_out)

            if new_out and n > 0:
                write_mewtator_meta(new_out)

            gd._sync_buttons()
            self.catname_tab._sync_buttons()
            self._set_status(f"Migrated {n} file(s) to {target}!")
        except Exception as e:
            self._set_status(f"Migration error: {e}")
            messagebox.showerror("Error", f"Migration failed:\n{e}")
        finally:
            self._busy = False

    def _on_closing(self):
        """Cancel pending debounced callbacks before destroying the window."""
        self._save_settings()
        for tab in (self.gamedata_tab, self.catname_tab):
            job = getattr(tab, '_search_job', None)
            if job:
                try:
                    tab.parent.after_cancel(job)
                except Exception:
                    pass
                tab._search_job = None
        self.root.destroy()

    # ── Settings persistence ──────────────────────────────────────────

    def _settings_path(self):
        if getattr(sys, 'frozen', False):
            return Path(sys.executable).parent / "settings.json"
        return Path(__file__).parent / "settings.json"

    def _load_settings(self):
        p = self._settings_path()
        if not p.exists():
            return {}
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logging.warning(f"Could not load settings: {e}")
            return {}

    def _save_settings(self):
        data = {"mewtator": self.mewtator_var.get()}
        try:
            with open(self._settings_path(), "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError as e:
            logging.warning(f"Could not save settings: {e}")

    def _set_status(self, text):
        self.status_var.set(text)
        self.root.update_idletasks()

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.root.mainloop()
