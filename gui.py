import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
import logging, sys

def _setup_logging():
    if getattr(sys, 'frozen', False):
        log_path = Path(sys.executable).parent / "debug.log"
    else:
        log_path = Path(__file__).parent / "debug.log"
    logging.basicConfig(filename=str(log_path), level=logging.DEBUG,
                        format="%(asctime)s %(message)s", encoding="utf-8")
    logging.info("=== App started ===")
_setup_logging()

from constants import APP_NAME, APP_VERSION, MAX_NAME_LEN, MIN_NAME_LEN, BACKUP_EXTENSION
from save_manager import (
    discover_saves, open_save, get_all_entries, create_backup,
    restore_backup, write_blob, is_game_running, list_backups,
)
from entity_registry import ENTITY_CATEGORIES, CATEGORY_MAP
from blob_parser import get_parser
from name_modifier import replace_display_name, verify_modified_blob, validate_new_name

# ── Main Application ────────────────────────────────────────────────

class MewgenicsRenamerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry("780x620")
        self.root.minsize(660, 520)
        self.root.resizable(True, True)

        self._build_ui()

    def _build_ui(self):
        # Status bar first (tabs call _set_status during init)
        self.status_var = tk.StringVar(value="Ready")
        status_frame = ttk.Frame(self.root)
        status_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(2, 8))
        ttk.Label(status_frame, textvariable=self.status_var,
                  relief=tk.SUNKEN, anchor=tk.W).pack(fill=tk.X)

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=6, pady=(6, 0))

        # Tab 1 — Save Renamer
        save_frame = ttk.Frame(notebook)
        notebook.add(save_frame, text="  Save Renamer  ")
        self.save_tab = SaveRenamerTab(save_frame, self._set_status)

        # Tab 2 — Game Data Modder
        gd_frame = ttk.Frame(notebook)
        notebook.add(gd_frame, text="  Game Data Modder  ")
        self.gamedata_tab = GameDataTab(gd_frame, self._set_status)

        # Tab 3 — Cat Name Pools
        cn_frame = ttk.Frame(notebook)
        notebook.add(cn_frame, text="  Cat Name Pools  ")
        self.catname_tab = CatNamePoolTab(cn_frame, self._set_status)

    def _set_status(self, text):
        self.status_var.set(text)
        self.root.update_idletasks()

    def run(self):
        self.root.mainloop()


# ── Tab 1 : Save Renamer ────────────────────────────────────────────

class SaveRenamerTab:
    def __init__(self, parent, set_status):
        self.parent = parent
        self._set_status = set_status

        self.saves = []
        self.current_save_path = None
        self.entries = []
        self.entry_map = {}
        self.selected_entry = None
        self.last_backup = None

        self._build(parent)
        self._discover_saves()

    # ---- build ----

    def _build(self, parent):
        # Description
        ttk.Label(parent,
                  text="Rename your existing cats directly in save files. "
                       "Changes are instant and affect cats you already own.",
                  foreground="#555555", wraplength=700).pack(
                      fill=tk.X, padx=12, pady=(8, 0))

        # Save selection
        sf = ttk.LabelFrame(parent, text="Save File", padding=8)
        sf.pack(fill=tk.X, padx=10, pady=(10, 5))

        self.save_var = tk.StringVar()
        self.save_combo = ttk.Combobox(sf, textvariable=self.save_var,
                                       state="readonly", width=60)
        self.save_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.save_combo.bind("<<ComboboxSelected>>", self._on_save_selected)
        ttk.Button(sf, text="Browse...", command=self._browse_save).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(sf, text="Refresh", command=self._discover_saves).pack(side=tk.LEFT)

        # Entry list
        lf = ttk.LabelFrame(parent, text="Renameable Entries", padding=8)
        lf.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        cols = ("name", "size")
        self.tree = ttk.Treeview(lf, columns=cols, show="tree headings", height=10)
        self.tree.heading("#0", text="Source", anchor=tk.W)
        self.tree.heading("name", text="Display Name", anchor=tk.W)
        self.tree.heading("size", text="Size", anchor=tk.W)
        self.tree.column("#0", width=160, minwidth=100)
        self.tree.column("name", width=220, minwidth=100)
        self.tree.column("size", width=90, minwidth=60, stretch=False)
        self.tree.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        self.tree.bind("<<TreeviewSelect>>", self._on_entry_selected)

        self.tree.tag_configure("empty_category", foreground="#999999")
        self.tree.tag_configure("active_category", foreground="#000000")
        self.tree.tag_configure("read_only", foreground="#888888")
        self.tree.tag_configure("parse_error", foreground="#cc0000")

        sb = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(fill=tk.Y, side=tk.RIGHT)

        # Rename
        rf = ttk.LabelFrame(parent, text="Rename", padding=8)
        rf.pack(fill=tk.X, padx=10, pady=5)

        r1 = ttk.Frame(rf); r1.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(r1, text="Current name:").pack(side=tk.LEFT)
        self.cur_name_var = tk.StringVar(value="\u2014")
        ttk.Label(r1, textvariable=self.cur_name_var,
                  font=("Consolas", 11, "bold")).pack(side=tk.LEFT, padx=(5, 0))

        r2 = ttk.Frame(rf); r2.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(r2, text="New name:").pack(side=tk.LEFT)
        self.new_name_var = tk.StringVar()
        self.new_name_var.trace_add("write", self._on_name_changed)
        self.name_entry = ttk.Entry(r2, textvariable=self.new_name_var,
                                    width=30, font=("Consolas", 11))
        self.name_entry.pack(side=tk.LEFT, padx=5)
        self.char_var = tk.StringVar()
        self.char_lbl = ttk.Label(r2, textvariable=self.char_var)
        self.char_lbl.pack(side=tk.LEFT)

        r3 = ttk.Frame(rf); r3.pack(fill=tk.X)
        self.rename_btn = ttk.Button(r3, text="Rename",
                                     command=self._on_rename, state=tk.DISABLED)
        self.rename_btn.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(r3, text="Restore Backup...", command=self._on_restore).pack(side=tk.LEFT)

    # ---- save discovery ----

    def _discover_saves(self):
        self.saves = discover_saves()
        names = [
            f"{s['steam_id']} / {s['save_name']}  ({s['modified']:%Y-%m-%d %H:%M})"
            for s in self.saves
        ]
        self.save_combo["values"] = names
        if names:
            self.save_combo.current(0)
            self._load_save(self.saves[0]["path"])
        else:
            self._set_status("No save files found. Use Browse to select one manually.")

    def _on_save_selected(self, _e=None):
        idx = self.save_combo.current()
        if 0 <= idx < len(self.saves):
            self._load_save(self.saves[idx]["path"])

    def _browse_save(self):
        p = filedialog.askopenfilename(
            title="Select Mewgenics save file",
            filetypes=[("Save files", "*.sav"), ("All files", "*.*")],
        )
        if p:
            self._load_save(Path(p))

    # ---- loading ----

    def _iid(self, entry):
        return f"{entry['source']}:{entry['key']}"

    def _load_save(self, path):
        try:
            conn = open_save(path)
            raw = get_all_entries(conn)
            conn.close()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open save:\n{e}")
            return
        self.current_save_path = Path(path)
        self.entries = raw
        self.entry_map = {self._iid(e): e for e in self.entries}
        self._refresh_tree()
        name = path.name if hasattr(path, "name") else path
        self._set_status(f"Loaded {len(self.entries)} entry(ies) from {name}")

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.selected_entry = None
        self.cur_name_var.set("\u2014")
        self.new_name_var.set("")
        self.rename_btn.config(state=tk.DISABLED)

        by_cat = {}
        for e in self.entries:
            by_cat.setdefault(e["category_id"], []).append(e)

        for cat in sorted(ENTITY_CATEGORIES, key=lambda c: c.sort_order):
            items = by_cat.get(cat.id, [])
            cid = f"__cat__{cat.id}"
            if not items:
                self.tree.insert("", tk.END, iid=cid,
                                 text=f"{cat.display_name} (0)",
                                 values=(cat.description, ""),
                                 open=False, tags=("empty_category",))
            else:
                self.tree.insert("", tk.END, iid=cid,
                                 text=f"{cat.display_name} ({len(items)})",
                                 values=("", ""), open=True,
                                 tags=("active_category",))
                for entry in items:
                    iid = self._iid(entry)
                    lbl = f"#{entry['key']}" if isinstance(entry['key'], int) else entry['key']
                    tag = ""
                    if entry.get("parse_error"):
                        tag = "parse_error"
                    elif entry.get("read_only"):
                        tag = "read_only"
                    self.tree.insert(cid, tk.END, iid=iid, text=lbl,
                                     values=(entry["name"], f"{entry['blob_size']} B"),
                                     tags=(tag,) if tag else ())

    # ---- selection / rename ----

    def _on_entry_selected(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            self.selected_entry = None
            return
        entry = self.entry_map.get(sel[0])
        if entry is None:
            self.selected_entry = None
            self.cur_name_var.set("\u2014")
            self.new_name_var.set("")
            self.rename_btn.config(state=tk.DISABLED)
            return
        if entry.get("read_only"):
            self.selected_entry = None
            reason = entry.get("parse_error") or "read-only"
            self.cur_name_var.set(f"{entry['name']}  ({reason})")
            self.new_name_var.set("")
            self.rename_btn.config(state=tk.DISABLED)
            return
        self.selected_entry = entry
        self.cur_name_var.set(entry["name"])
        self.new_name_var.set("")
        self.name_entry.focus()

    def _on_name_changed(self, *_):
        n = self.new_name_var.get()
        length = len(n)
        self.char_var.set(f"{length}/{MAX_NAME_LEN} chars")
        self.char_lbl.config(foreground="red" if length > MAX_NAME_LEN else "")
        ok = (self.selected_entry is not None
              and MIN_NAME_LEN <= length <= MAX_NAME_LEN
              and n != self.selected_entry["name"])
        self.rename_btn.config(state=tk.NORMAL if ok else tk.DISABLED)

    def _on_rename(self):
        if not self.selected_entry or not self.current_save_path:
            return
        entry = self.selected_entry
        new_name = self.new_name_var.get().strip()

        errors = [e for e in validate_new_name(new_name) if not e.startswith("Warning")]
        if errors:
            messagebox.showerror("Invalid name", "\n".join(errors))
            return
        warns = [w for w in validate_new_name(new_name) if w.startswith("Warning")]
        if warns and not messagebox.askyesno("Warning", "\n".join(warns) + "\n\nContinue?"):
            return
        if is_game_running():
            messagebox.showwarning("Game running",
                                   "Close Mewgenics before modifying save files.")
            return
        if not messagebox.askyesno("Confirm",
                f'Rename "{entry["name"]}" to "{new_name}"?\n'
                f'A backup will be created.'):
            return

        try:
            self._set_status("Creating backup...")
            bp = create_backup(self.current_save_path)
            self.last_backup = bp
        except Exception as e:
            messagebox.showerror("Backup failed", str(e))
            return

        try:
            self._set_status("Modifying name...")
            new_blob = replace_display_name(entry["blob"], new_name)
            ok, msg = verify_modified_blob(entry["blob"], new_blob, new_name)
            if not ok:
                messagebox.showerror("Verification failed", msg)
                return
            write_blob(self.current_save_path, entry["source"], entry["key"], new_blob)
        except Exception as e:
            messagebox.showerror("Rename failed", str(e))
            return

        self._load_save(self.current_save_path)
        self._set_status(f'Renamed to "{new_name}". Backup: {bp.name}')
        messagebox.showinfo("Success", f'Renamed to "{new_name}"!')

    # ---- restore ----

    def _on_restore(self):
        if not self.current_save_path:
            messagebox.showinfo("No save", "Load a save file first.")
            return
        backups = list_backups(self.current_save_path)
        if not backups:
            p = filedialog.askopenfilename(
                title="Select backup",
                initialdir=self.current_save_path.parent / "backups",
                filetypes=[("Backup", f"*{BACKUP_EXTENSION}"), ("All", "*.*")])
            if not p:
                return
            bp = Path(p)
        else:
            bp = self._pick_backup(backups)
            if bp is None:
                return

        if is_game_running():
            messagebox.showwarning("Game running", "Close Mewgenics before restoring.")
            return
        if not messagebox.askyesno("Confirm",
                f"Restore backup:\n{bp.name}\n\nOverwrite current save?"):
            return

        try:
            restore_backup(bp, self.current_save_path)
            self._load_save(self.current_save_path)
            self._set_status(f"Restored from {bp.name}")
            messagebox.showinfo("Restored", "Backup restored!")
        except Exception as e:
            messagebox.showerror("Restore failed", str(e))

    def _pick_backup(self, backups):
        dlg = tk.Toplevel(self.parent)
        dlg.title("Select Backup")
        dlg.geometry("450x250")
        dlg.transient(self.parent)
        dlg.grab_set()

        ttk.Label(dlg, text="Available backups (newest first):").pack(padx=10, pady=(10, 5))
        lb = tk.Listbox(dlg, height=8)
        for b in backups:
            lb.insert(tk.END, b.name)
        lb.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        if backups:
            lb.selection_set(0)

        result = [None]

        def on_ok():
            s = lb.curselection()
            if s:
                result[0] = backups[s[0]]
            dlg.destroy()

        bf = ttk.Frame(dlg)
        bf.pack(pady=(0, 10))
        ttk.Button(bf, text="Restore", command=on_ok).pack(side=tk.LEFT, padx=5)
        ttk.Button(bf, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT, padx=5)
        dlg.wait_window()
        return result[0]


# ── Tab 2 : Game Data Modder ────────────────────────────────────────

class GameDataTab:
    def __init__(self, parent, set_status):
        self.parent = parent
        self._set_status = set_status

        self.gpak_path = None
        self.game_dir = None
        self.entities = {}        # {cat_id: [GameEntity]}
        self.all_entities = []    # flat
        self.entity_map = {}      # tree iid -> GameEntity
        self.overrides = {}       # {key: {lang: name}}
        self.selected_entity = None
        self._search_job = None

        self._build(parent)
        self._auto_detect_gpak()

    def _build(self, parent):
        # Description
        ttk.Label(parent,
                  text="Rename enemies, bosses, familiars, items, and furniture in game data. "
                       "Affects all players and all save files. The original GPAK is never modified.",
                  foreground="#555555", wraplength=700).pack(
                      fill=tk.X, padx=12, pady=(8, 0))

        # GPAK path
        gf = ttk.LabelFrame(parent, text="Game Data (resources.gpak)", padding=8)
        gf.pack(fill=tk.X, padx=10, pady=(10, 5))

        self.gpak_var = tk.StringVar(value="Not found — use Browse")
        ttk.Label(gf, textvariable=self.gpak_var, width=60,
                  anchor=tk.W).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(gf, text="Browse...", command=self._browse_gpak).pack(side=tk.LEFT, padx=(5, 0))

        # Search + language selector
        sf = ttk.Frame(parent)
        sf.pack(fill=tk.X, padx=10, pady=(5, 2))

        ttk.Label(sf, text="Search:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._on_search)
        ttk.Entry(sf, textvariable=self.search_var, width=28).pack(side=tk.LEFT, padx=5)

        ttk.Label(sf, text="Language:").pack(side=tk.LEFT, padx=(15, 0))
        self.lang_var = tk.StringVar(value="en")
        from game_data_manager import LANGUAGE_LABELS
        lang_vals = [f"{code} — {label}" for code, label in LANGUAGE_LABELS.items()]
        lang_cb = ttk.Combobox(sf, textvariable=self.lang_var,
                               state="readonly", width=18, values=lang_vals)
        lang_cb.current(0)
        lang_cb.pack(side=tk.LEFT, padx=5)
        lang_cb.bind("<<ComboboxSelected>>", self._on_lang)

        # --- Pack bottom sections FIRST so they're always visible ---

        # Action buttons (pack at bottom first)
        af = ttk.Frame(parent)
        af.pack(fill=tk.X, side=tk.BOTTOM, padx=10, pady=(2, 8))

        def _apply_clicked():
            logging.info("[BUTTON] Apply Overrides clicked!")
            self._apply()

        self.apply_btn = ttk.Button(af, text="Apply Overrides",
                                    command=_apply_clicked, state=tk.DISABLED)
        self.apply_btn.pack(side=tk.LEFT, padx=(0, 5))
        self.restore_btn = ttk.Button(af, text="Remove Overrides",
                                      command=self._restore, state=tk.DISABLED)
        self.restore_btn.pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(af, text="Reset All Overrides",
                   command=self._reset_all).pack(side=tk.LEFT)

        # Edit panel (pack at bottom, above buttons)
        ef = ttk.LabelFrame(parent, text="Edit Name", padding=8)
        ef.pack(fill=tk.X, side=tk.BOTTOM, padx=10, pady=5)

        r1 = ttk.Frame(ef); r1.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(r1, text="Original:").pack(side=tk.LEFT)
        self.orig_var = tk.StringVar(value="\u2014")
        ttk.Label(r1, textvariable=self.orig_var,
                  font=("Consolas", 11)).pack(side=tk.LEFT, padx=(5, 0))

        r2 = ttk.Frame(ef); r2.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(r2, text="New name:").pack(side=tk.LEFT)
        self.new_var = tk.StringVar()
        self.new_entry = ttk.Entry(r2, textvariable=self.new_var,
                                   width=30, font=("Consolas", 11))
        self.new_entry.pack(side=tk.LEFT, padx=5)
        self.new_entry.bind("<Return>", lambda _: self._set_override())

        self.all_lang_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(r2, text="All languages",
                        variable=self.all_lang_var).pack(side=tk.LEFT, padx=(10, 0))

        r3 = ttk.Frame(ef); r3.pack(fill=tk.X)
        self.set_btn = ttk.Button(r3, text="Set Override",
                                  command=self._set_override, state=tk.DISABLED)
        self.set_btn.pack(side=tk.LEFT, padx=(0, 5))
        self.reset_btn = ttk.Button(r3, text="Reset This",
                                    command=self._reset_override, state=tk.DISABLED)
        self.reset_btn.pack(side=tk.LEFT, padx=(0, 15))

        self.ovr_count_var = tk.StringVar(value="Overrides: 0")
        ttk.Label(r3, textvariable=self.ovr_count_var).pack(side=tk.LEFT, padx=(10, 0))

        # --- Now pack the tree (fills remaining space) ---
        tf = ttk.Frame(parent)
        tf.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        cols = ("name", "override")
        self.tree = ttk.Treeview(tf, columns=cols, show="tree headings", height=12)
        self.tree.heading("#0", text="Key", anchor=tk.W)
        self.tree.heading("name", text="Current Name", anchor=tk.W)
        self.tree.heading("override", text="New Name", anchor=tk.W)
        self.tree.column("#0", width=220, minwidth=120)
        self.tree.column("name", width=180, minwidth=80)
        self.tree.column("override", width=180, minwidth=80)
        self.tree.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        self.tree.bind("<<TreeviewSelect>>", self._on_entity_sel)

        self.tree.tag_configure("overridden", foreground="#0066cc")
        self.tree.tag_configure("empty_cat", foreground="#999999")
        self.tree.tag_configure("active_cat", foreground="#000000")

        sb = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(fill=tk.Y, side=tk.RIGHT)

    # ---- GPAK ----

    def _auto_detect_gpak(self):
        from gpak_manager import find_gpak
        p = find_gpak()
        logging.info(f"[GD] find_gpak => {p}")
        if p:
            self._load_gpak(p)

    def _browse_gpak(self):
        p = filedialog.askopenfilename(
            title="Select resources.gpak",
            filetypes=[("GPAK", "*.gpak"), ("All", "*.*")])
        if p:
            self._load_gpak(Path(p))

    def _load_gpak(self, path):
        self.gpak_path = Path(path)
        self.game_dir = self.gpak_path.parent
        self.gpak_var.set(str(self.gpak_path))
        self._set_status("Loading entity names from GPAK...")

        try:
            from game_data_manager import load_entity_names, load_overrides

            # Read originals from backup if present (v4 migration), else current gpak
            source = self.gpak_path.with_name(self.gpak_path.name + ".original")
            if not source.exists():
                source = self.gpak_path

            self.entities = load_entity_names(source)
            self.overrides = load_overrides(self.gpak_path)
            logging.info(f"[GD] loaded gpak={self.gpak_path}, game_dir={self.game_dir}, overrides={len(self.overrides)}")

            self.all_entities = [e for ents in self.entities.values() for e in ents]

            self._refresh_tree()
            self._sync_buttons()
            total = len(self.all_entities)
            self._set_status(f"Loaded {total} entities from GPAK")
        except Exception as e:
            logging.exception(f"[GD] _load_gpak FAILED")
            messagebox.showerror("Error", f"Failed to load GPAK:\n{e}")
            import traceback; traceback.print_exc()

    # ---- tree ----

    def _lang(self):
        v = self.lang_var.get()
        return v.split(" \u2014 ")[0] if " \u2014 " in v else v

    def _refresh_tree(self, search=""):
        self.tree.delete(*self.tree.get_children())
        self.entity_map.clear()
        self.selected_entity = None
        self.orig_var.set("\u2014")
        self.new_var.set("")
        self.set_btn.config(state=tk.DISABLED)
        self.reset_btn.config(state=tk.DISABLED)

        lang = self._lang()
        q = search.lower()

        from game_data_manager import get_category_display_order
        for cat_id, display in get_category_display_order():
            ents = self.entities.get(cat_id, [])
            if q:
                ents = [e for e in ents
                        if q in e.names.get(lang, "").lower() or q in e.key.lower()]

            cid = f"__gd__{cat_id}"
            if not ents:
                self.tree.insert("", tk.END, iid=cid,
                                 text=f"{display} (0)", values=("", ""),
                                 open=False, tags=("empty_cat",))
            else:
                self.tree.insert("", tk.END, iid=cid,
                                 text=f"{display} ({len(ents)})", values=("", ""),
                                 open=bool(q), tags=("active_cat",))
                for ent in ents:
                    iid = f"gd:{ent.key}"
                    name = ent.names.get(lang, "\u2014")
                    ovr = self.overrides.get(ent.key, {}).get(lang, "")
                    tag = "overridden" if ent.key in self.overrides else ""
                    self.tree.insert(cid, tk.END, iid=iid, text=ent.key,
                                     values=(name, ovr),
                                     tags=(tag,) if tag else ())
                    self.entity_map[iid] = ent

        self._update_count()

    # ---- search / lang ----

    def _on_search(self, *_):
        if self._search_job:
            self.parent.after_cancel(self._search_job)
        self._search_job = self.parent.after(
            200, lambda: self._refresh_tree(self.search_var.get()))

    def _on_lang(self, _=None):
        self._refresh_tree(self.search_var.get())

    # ---- entity selection ----

    def _on_entity_sel(self, _=None):
        sel = self.tree.selection()
        if not sel:
            self.selected_entity = None
            return
        ent = self.entity_map.get(sel[0])
        if ent is None:
            self.selected_entity = None
            self.orig_var.set("\u2014")
            self.new_var.set("")
            self.set_btn.config(state=tk.DISABLED)
            self.reset_btn.config(state=tk.DISABLED)
            return

        self.selected_entity = ent
        lang = self._lang()
        self.orig_var.set(ent.names.get(lang, "\u2014"))
        self.new_var.set(self.overrides.get(ent.key, {}).get(lang, ""))
        self.set_btn.config(state=tk.NORMAL)
        self.reset_btn.config(
            state=tk.NORMAL if ent.key in self.overrides else tk.DISABLED)
        self.new_entry.focus()

    # ---- overrides ----

    def _set_override(self):
        ent = self.selected_entity
        if not ent:
            return
        new = self.new_var.get().strip()
        if not new:
            self._reset_override()
            return

        from game_data_manager import LANGUAGES
        if self.all_lang_var.get():
            self.overrides[ent.key] = {l: new for l in LANGUAGES}
        else:
            self.overrides.setdefault(ent.key, {})[self._lang()] = new

        self._persist()
        self._reselect(ent.key)
        self._set_status(f'Override: {ent.key} -> "{new}"')

    def _reset_override(self):
        ent = self.selected_entity
        if not ent or ent.key not in self.overrides:
            return
        del self.overrides[ent.key]
        self._persist()
        self._reselect(ent.key)
        self._set_status(f"Override removed: {ent.key}")

    def _reset_all(self):
        if not self.overrides:
            return
        if not messagebox.askyesno("Confirm",
                f"Reset all {len(self.overrides)} override(s)?"):
            return
        self.overrides.clear()
        self._persist()
        self._set_status("All overrides cleared")

    def _persist(self):
        if self.gpak_path:
            from game_data_manager import save_overrides
            save_overrides(self.overrides, self.gpak_path)
        self._refresh_tree(self.search_var.get())
        self._update_count()
        self._sync_buttons()

    def _reselect(self, key):
        iid = f"gd:{key}"
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.see(iid)

    def _update_count(self):
        self.ovr_count_var.set(f"Overrides: {len(self.overrides)}")

    def _sync_buttons(self):
        has = self.gpak_path is not None
        from game_data_manager import has_loose_files
        loose = has and has_loose_files(self.game_dir)
        ovr = bool(self.overrides)
        apply_state = tk.NORMAL if has and ovr else tk.DISABLED
        self.apply_btn.config(state=apply_state)
        self.restore_btn.config(state=tk.NORMAL if loose else tk.DISABLED)
        logging.info(f"[GD] _sync_buttons: has={has}, ovr={ovr}, loose={loose}, apply={apply_state}")

    # ---- apply / restore ----

    def _apply(self):
        logging.info(f"[Apply] CALLED! gpak_path={self.gpak_path}, game_dir={self.game_dir}, overrides={len(self.overrides)} keys")
        if not self.gpak_path or not self.overrides:
            logging.info("[Apply] Aborted: no gpak_path or no overrides")
            return
        if is_game_running():
            messagebox.showwarning("Game running",
                                   "Close Mewgenics before modifying game data.")
            return
        n = len(self.overrides)
        if not messagebox.askyesno("Apply Overrides",
                f"Apply {n} override(s) as loose files?\n\n"
                f"Modified CSVs will be written next to the game exe.\n"
                f"The original GPAK is never modified."):
            logging.info("[Apply] User cancelled")
            return

        try:
            from game_data_manager import build_all_csvs, write_loose_files

            # Read originals from backup if present (v4 migration), else gpak
            source = self.gpak_path.with_name(self.gpak_path.name + ".original")
            if not source.exists():
                source = self.gpak_path
            logging.info(f"[Apply] Source: {source}")

            self._set_status("Building modified CSVs...")
            modifications = build_all_csvs(source, self.overrides)
            logging.info(f"[Apply] Built {len(modifications)} CSVs: {list(modifications.keys())}")

            self._set_status("Writing loose files...")
            written = write_loose_files(self.game_dir, modifications)
            logging.info(f"[Apply] Wrote {len(written)} files: {written}")

            # Verify files exist
            from pathlib import Path
            verified = [str(p) for p in written if p.exists()]
            logging.info(f"[Apply] Verified {len(verified)}/{len(written)} files exist")

            self._sync_buttons()
            self._set_status(f"Applied {n} override(s) as {len(written)} loose file(s)!")
            file_list = "\n".join(str(p) for p in written)
            messagebox.showinfo("Success",
                f"{n} name override(s) applied!\n"
                f"{len(written)} file(s) written to:\n{file_list}\n\n"
                f"Restart the game to see the changes.")
        except Exception as e:
            self._set_status(f"Error: {e}")
            messagebox.showerror("Error", f"Failed to apply:\n{e}")
            import traceback; traceback.print_exc()

    def _restore(self):
        if not self.gpak_path:
            return
        from game_data_manager import has_loose_files, remove_loose_files
        if not has_loose_files(self.game_dir):
            messagebox.showinfo("No loose files",
                                "No override files found to remove.")
            return
        if is_game_running():
            messagebox.showwarning("Game running",
                                   "Close Mewgenics before removing overrides.")
            return
        if not messagebox.askyesno("Remove Overrides",
                "Delete loose CSV override files?\n\n"
                "The game will use original names from the GPAK.\n"
                "Override list will be kept (not applied)."):
            return
        try:
            removed = remove_loose_files(self.game_dir)
            self._sync_buttons()
            self._set_status(f"Removed {len(removed)} override file(s)!")
            messagebox.showinfo("Removed",
                f"{len(removed)} override file(s) deleted.\n"
                f"Game will use original names.")
        except Exception as e:
            messagebox.showerror("Error", f"Remove failed:\n{e}")


# ── Tab 3 : Cat Name Pools ─────────────────────────────────────────

class CatNamePoolTab:
    def __init__(self, parent, set_status):
        self.parent = parent
        self._set_status = set_status

        self.gpak_path = None
        self.game_dir = None
        self.pools = {}            # {label: [name, ...]}  originals
        self.catname_overrides = {} # {label: {"added": [...], "removed": [...]}}
        self.current_pool = None   # selected pool label
        self._search_job = None

        self._build(parent)
        self._auto_detect_gpak()

    def _build(self, parent):
        # Description
        ttk.Label(parent,
                  text="Edit the name pools used to randomly generate cat names. "
                       "Add or remove names from the Female, Male, and Neutral pools. "
                       "Only affects newly generated cats, not existing ones.",
                  foreground="#555555", wraplength=700).pack(
                      fill=tk.X, padx=12, pady=(8, 0))

        # GPAK path
        gf = ttk.LabelFrame(parent, text="Game Data (resources.gpak)", padding=8)
        gf.pack(fill=tk.X, padx=10, pady=(10, 5))

        self.gpak_var = tk.StringVar(value="Not found — use Browse")
        ttk.Label(gf, textvariable=self.gpak_var, width=60,
                  anchor=tk.W).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(gf, text="Browse...", command=self._browse_gpak).pack(side=tk.LEFT, padx=(5, 0))

        # Pool selector + search
        sf = ttk.Frame(parent)
        sf.pack(fill=tk.X, padx=10, pady=(5, 2))

        ttk.Label(sf, text="Pool:").pack(side=tk.LEFT)
        self.pool_var = tk.StringVar()
        self.pool_combo = ttk.Combobox(sf, textvariable=self.pool_var,
                                       state="readonly", width=12)
        self.pool_combo.pack(side=tk.LEFT, padx=5)
        self.pool_combo.bind("<<ComboboxSelected>>", self._on_pool_changed)

        ttk.Label(sf, text="Search:").pack(side=tk.LEFT, padx=(15, 0))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._on_search)
        ttk.Entry(sf, textvariable=self.search_var, width=25).pack(side=tk.LEFT, padx=5)

        self.count_var = tk.StringVar(value="")
        ttk.Label(sf, textvariable=self.count_var).pack(side=tk.LEFT, padx=(15, 0))

        # Name list
        lf = ttk.Frame(parent)
        lf.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.listbox = tk.Listbox(lf, font=("Consolas", 10), selectmode=tk.EXTENDED)
        self.listbox.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        sb = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        sb.pack(fill=tk.Y, side=tk.RIGHT)

        # Add / Remove panel
        ef = ttk.LabelFrame(parent, text="Edit Pool", padding=8)
        ef.pack(fill=tk.X, padx=10, pady=5)

        r1 = ttk.Frame(ef); r1.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(r1, text="New name:").pack(side=tk.LEFT)
        self.new_name_var = tk.StringVar()
        self.new_entry = ttk.Entry(r1, textvariable=self.new_name_var,
                                   width=30, font=("Consolas", 11))
        self.new_entry.pack(side=tk.LEFT, padx=5)
        self.new_entry.bind("<Return>", lambda _: self._add_name())

        self.add_btn = ttk.Button(r1, text="Add Name",
                                  command=self._add_name, state=tk.DISABLED)
        self.add_btn.pack(side=tk.LEFT, padx=(5, 0))

        r2 = ttk.Frame(ef); r2.pack(fill=tk.X)
        self.remove_btn = ttk.Button(r2, text="Remove Selected",
                                     command=self._remove_names, state=tk.DISABLED)
        self.remove_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.changes_var = tk.StringVar(value="Changes: 0 added, 0 removed")
        ttk.Label(r2, textvariable=self.changes_var).pack(side=tk.LEFT, padx=(10, 0))

        # Action buttons
        af = ttk.Frame(parent)
        af.pack(fill=tk.X, padx=10, pady=(2, 8))
        self.apply_btn = ttk.Button(af, text="Apply to Game",
                                    command=self._apply, state=tk.DISABLED)
        self.apply_btn.pack(side=tk.LEFT, padx=(0, 5))
        self.restore_btn = ttk.Button(af, text="Remove from Game",
                                      command=self._restore, state=tk.DISABLED)
        self.restore_btn.pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(af, text="Reset All Changes",
                   command=self._reset_all).pack(side=tk.LEFT)

        # Bind listbox selection for remove button
        self.listbox.bind("<<ListboxSelect>>", self._on_list_select)
        self.new_name_var.trace_add("write", self._on_new_name_changed)

    # ---- GPAK ----

    def _auto_detect_gpak(self):
        from gpak_manager import find_gpak
        p = find_gpak()
        if p:
            self._load_gpak(p)

    def _browse_gpak(self):
        p = filedialog.askopenfilename(
            title="Select resources.gpak",
            filetypes=[("GPAK", "*.gpak"), ("All", "*.*")])
        if p:
            self._load_gpak(Path(p))

    def _load_gpak(self, path):
        self.gpak_path = Path(path)
        self.game_dir = self.gpak_path.parent
        self.gpak_var.set(str(self.gpak_path))
        self._set_status("Loading cat name pools from GPAK...")

        try:
            from game_data_manager import load_catname_pools, load_catname_overrides

            source = self.gpak_path.with_name(self.gpak_path.name + ".original")
            if not source.exists():
                source = self.gpak_path

            self.pools = load_catname_pools(source)
            self.catname_overrides = load_catname_overrides(self.gpak_path)

            labels = list(self.pools.keys())
            self.pool_combo["values"] = labels
            if labels:
                self.pool_combo.current(0)
                self.current_pool = labels[0]
                self._refresh_list()

            total = sum(len(v) for v in self.pools.values())
            self._set_status(f"Loaded {total} cat names from {len(labels)} pools")
            self._sync_buttons()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load GPAK:\n{e}")
            import traceback; traceback.print_exc()

    # ---- pool switching ----

    def _on_pool_changed(self, _=None):
        self.current_pool = self.pool_var.get()
        self.search_var.set("")
        self._refresh_list()

    # ---- list display ----

    def _get_effective_names(self):
        """Get the current pool names with overrides applied."""
        if not self.current_pool or self.current_pool not in self.pools:
            return []
        names = list(self.pools[self.current_pool])
        ovr = self.catname_overrides.get(self.current_pool, {})
        removed = set(ovr.get("removed", []))
        names = [n for n in names if n not in removed]
        names.extend(ovr.get("added", []))
        names = sorted(set(names), key=str.lower)
        return names

    def _refresh_list(self, search=""):
        self.listbox.delete(0, tk.END)
        names = self._get_effective_names()
        q = search.lower()
        if q:
            names = [n for n in names if q in n.lower()]

        ovr = self.catname_overrides.get(self.current_pool, {})
        added = set(ovr.get("added", []))
        removed = set(ovr.get("removed", []))

        for name in names:
            if name in added:
                self.listbox.insert(tk.END, f"+ {name}")
            else:
                self.listbox.insert(tk.END, f"  {name}")

        # Show removed names at end if no search
        if not q:
            for name in sorted(removed, key=str.lower):
                self.listbox.insert(tk.END, f"- {name}")

        self._update_counts()

    def _update_counts(self):
        names = self._get_effective_names()
        self.count_var.set(f"{len(names)} names")

        total_added = sum(len(o.get("added", [])) for o in self.catname_overrides.values())
        total_removed = sum(len(o.get("removed", [])) for o in self.catname_overrides.values())
        self.changes_var.set(f"Changes: {total_added} added, {total_removed} removed")

    # ---- search ----

    def _on_search(self, *_):
        if self._search_job:
            self.parent.after_cancel(self._search_job)
        self._search_job = self.parent.after(
            200, lambda: self._refresh_list(self.search_var.get()))

    # ---- selection ----

    def _on_list_select(self, _=None):
        sel = self.listbox.curselection()
        self.remove_btn.config(state=tk.NORMAL if sel else tk.DISABLED)

    def _on_new_name_changed(self, *_):
        has_pool = self.current_pool is not None
        has_text = bool(self.new_name_var.get().strip())
        self.add_btn.config(state=tk.NORMAL if has_pool and has_text else tk.DISABLED)

    # ---- add / remove ----

    def _add_name(self):
        if not self.current_pool:
            return
        name = self.new_name_var.get().strip()
        if not name:
            return

        # Check if already exists
        existing = self._get_effective_names()
        if name in existing:
            messagebox.showinfo("Exists", f'"{name}" is already in the pool.')
            return

        ovr = self.catname_overrides.setdefault(self.current_pool, {})

        # If it was previously removed, un-remove it instead of adding
        if name in ovr.get("removed", []):
            ovr["removed"].remove(name)
            if not ovr["removed"]:
                del ovr["removed"]
        else:
            ovr.setdefault("added", []).append(name)

        self._persist()
        self.new_name_var.set("")
        self._set_status(f'Added "{name}" to {self.current_pool} pool')

    def _remove_names(self):
        if not self.current_pool:
            return
        sel = self.listbox.curselection()
        if not sel:
            return

        ovr = self.catname_overrides.setdefault(self.current_pool, {})
        count = 0

        for idx in sel:
            text = self.listbox.get(idx)
            # Parse the display text (prefix: "+ ", "- ", "  ")
            name = text[2:]

            if text.startswith("- "):
                # Already removed, skip
                continue
            elif text.startswith("+ "):
                # Was added, just remove from added list
                added = ovr.get("added", [])
                if name in added:
                    added.remove(name)
                    if not added:
                        del ovr["added"]
                    count += 1
            else:
                # Original name, mark as removed
                ovr.setdefault("removed", []).append(name)
                count += 1

        if count:
            self._persist()
            self._set_status(f"Removed {count} name(s) from {self.current_pool} pool")

    def _reset_all(self):
        if not self.catname_overrides:
            return
        if not messagebox.askyesno("Confirm", "Reset all cat name pool changes?"):
            return
        self.catname_overrides.clear()
        self._persist()
        self._set_status("All cat name pool changes cleared")

    def _persist(self):
        if self.gpak_path:
            from game_data_manager import save_catname_overrides
            # Clean up empty overrides
            for label in list(self.catname_overrides):
                ovr = self.catname_overrides[label]
                if not ovr.get("added") and not ovr.get("removed"):
                    del self.catname_overrides[label]
            save_catname_overrides(self.catname_overrides, self.gpak_path)
        self._refresh_list(self.search_var.get())
        self._sync_buttons()

    def _sync_buttons(self):
        has = self.gpak_path is not None
        from game_data_manager import has_loose_files, CATNAME_POOLS
        loose = has and any(
            (Path(self.game_dir) / p["gpak_path"]).exists()
            for p in CATNAME_POOLS
        )
        has_changes = bool(self.catname_overrides)
        self.apply_btn.config(state=tk.NORMAL if has and has_changes else tk.DISABLED)
        self.restore_btn.config(state=tk.NORMAL if loose else tk.DISABLED)

    # ---- apply / restore ----

    def _apply(self):
        if not self.gpak_path or not self.catname_overrides:
            return
        if is_game_running():
            messagebox.showwarning("Game running",
                                   "Close Mewgenics before modifying game data.")
            return

        total_added = sum(len(o.get("added", [])) for o in self.catname_overrides.values())
        total_removed = sum(len(o.get("removed", [])) for o in self.catname_overrides.values())
        if not messagebox.askyesno("Apply Cat Name Pools",
                f"Apply changes as loose files?\n\n"
                f"{total_added} name(s) added, {total_removed} name(s) removed.\n"
                f"Only affects newly generated cats."):
            return

        try:
            from game_data_manager import build_catname_files, write_loose_files

            source = self.gpak_path.with_name(self.gpak_path.name + ".original")
            if not source.exists():
                source = self.gpak_path

            self._set_status("Building cat name files...")
            modifications = build_catname_files(source, self.catname_overrides)

            self._set_status("Writing loose files...")
            written = write_loose_files(self.game_dir, modifications)

            self._sync_buttons()
            self._set_status(f"Applied cat name pool changes ({len(written)} files)!")
            messagebox.showinfo("Success",
                f"Cat name pools updated!\n"
                f"{len(written)} file(s) written.\n"
                f"New cats will use the modified name pools.")
        except Exception as e:
            self._set_status(f"Error: {e}")
            messagebox.showerror("Error", f"Failed to apply:\n{e}")
            import traceback; traceback.print_exc()

    def _restore(self):
        if not self.gpak_path:
            return
        from game_data_manager import CATNAME_POOLS
        loose = [p for p in CATNAME_POOLS
                 if (Path(self.game_dir) / p["gpak_path"]).exists()]
        if not loose:
            messagebox.showinfo("No files", "No cat name pool files to remove.")
            return
        if is_game_running():
            messagebox.showwarning("Game running",
                                   "Close Mewgenics before removing files.")
            return
        if not messagebox.askyesno("Remove Cat Name Pools",
                f"Delete {len(loose)} loose cat name file(s)?\n\n"
                f"The game will use original name pools from the GPAK.\n"
                f"Your changes list will be kept."):
            return
        try:
            removed = 0
            for p in CATNAME_POOLS:
                target = Path(self.game_dir) / p["gpak_path"]
                if target.exists():
                    target.unlink()
                    removed += 1
            # Clean empty dirs
            data_dir = Path(self.game_dir) / "data"
            if data_dir.exists() and not any(data_dir.iterdir()):
                data_dir.rmdir()
            self._sync_buttons()
            self._set_status(f"Removed {removed} cat name file(s)!")
            messagebox.showinfo("Removed", f"{removed} file(s) deleted.")
        except Exception as e:
            messagebox.showerror("Error", f"Remove failed:\n{e}")
