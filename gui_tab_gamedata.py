"""Tab 2: Game Data Modder — rename enemies, items, furniture in game data."""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
import logging

from save_manager import is_game_running
from gpak_manager import find_gpak, is_valid_gpak
from game_data_manager import (
    LANGUAGES, LANGUAGE_LABELS,
    load_entity_names, load_overrides, save_overrides,
    get_category_display_order,
    build_merge_csvs, write_loose_files, write_mewtator_meta,
    remove_loose_files, has_loose_files,
)


class GameDataTab:
    def __init__(self, parent, set_status, mewtator_var=None):
        self.parent = parent
        self._set_status = set_status
        self.mewtator_var = mewtator_var

        self.gpak_path = None
        self.game_dir = None
        self.entities = {}        # {cat_id: [GameEntity]}
        self.all_entities = []    # flat
        self.entity_map = {}      # tree iid -> GameEntity
        self.overrides = {}       # {key: {lang: name}}
        self.selected_entity = None
        self._search_job = None
        self._busy = False

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

        self.gpak_var = tk.StringVar(value="Not found \u2014 use Browse")
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
        lang_vals = [f"{code} \u2014 {label}" for code, label in LANGUAGE_LABELS.items()]
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
            self.entities = load_entity_names(self._get_source_gpak())
            self.overrides = load_overrides(self.gpak_path)
            logging.info(f"[GD] loaded gpak={self.gpak_path}, game_dir={self.game_dir}, overrides={len(self.overrides)}")

            self.all_entities = [e for ents in self.entities.values() for e in ents]

            self._refresh_tree()
            self._sync_buttons()
            total = len(self.all_entities)
            self._set_status(f"Loaded {total} entities from GPAK")
        except Exception as e:
            logging.exception("[GD] _load_gpak FAILED")
            messagebox.showerror("Error", f"Failed to load GPAK:\n{e}")

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
            try:
                save_overrides(self.overrides, self.gpak_path)
            except Exception as e:
                logging.exception("[GD] Failed to save overrides")
                messagebox.showerror("Save failed",
                    f"Could not save overrides to disk:\n{e}\n\n"
                    f"Your changes are still in memory but may be lost if you close the app.")
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

    def _mewtator_dir(self):
        """Return Mewtator mod dir path (regardless of checkbox state)."""
        if self.game_dir:
            return Path(self.game_dir) / "Mewtator" / "mods" / "MewgenicsRenamer"
        return None

    def _get_output_dir(self):
        """Return Mewtator mod dir if enabled, else None."""
        if self.mewtator_var and self.mewtator_var.get():
            return self._mewtator_dir()
        return None

    def _migrate_files(self, output_dir):
        """Silently re-apply entity CSV overrides to output_dir. Returns file count."""
        if not self.gpak_path or not self.overrides:
            return 0
        source = self._get_source_gpak()
        modifications = build_merge_csvs(source, self.overrides)
        written = write_loose_files(self.game_dir, modifications, output_dir=output_dir)
        return len(written)

    def _has_any_loose(self):
        """Check if loose files exist in EITHER location (direct or Mewtator)."""
        if has_loose_files(self.game_dir, output_dir=None):
            return True
        mew = self._mewtator_dir()
        return mew is not None and has_loose_files(self.game_dir, output_dir=mew)

    def _sync_buttons(self):
        has = self.gpak_path is not None
        loose = has and self._has_any_loose()
        ovr = bool(self.overrides)
        apply_state = tk.NORMAL if has and ovr else tk.DISABLED
        self.apply_btn.config(state=apply_state)
        self.restore_btn.config(state=tk.NORMAL if loose else tk.DISABLED)
        logging.info(f"[GD] _sync_buttons: has={has}, ovr={ovr}, loose={loose}, apply={apply_state}")

    # ---- apply / restore ----

    def _get_source_gpak(self):
        """Return the best source GPAK (backup if valid, else current)."""
        source = self.gpak_path.with_name(self.gpak_path.name + ".original")
        if not source.exists() or not is_valid_gpak(source):
            source = self.gpak_path
        return source

    def _clean_stale_location(self, output_dir):
        """Remove loose files from the opposite location (Mewtator vs direct)."""
        if output_dir:
            # Switching to Mewtator: clean stale direct-mode files
            if has_loose_files(self.game_dir, output_dir=None):
                remove_loose_files(self.game_dir, output_dir=None)
                logging.info("[Apply] Cleaned stale direct-mode files")
        else:
            # Switching to direct: clean stale Mewtator files
            mew = self._mewtator_dir()
            if mew and has_loose_files(self.game_dir, output_dir=mew):
                remove_loose_files(self.game_dir, output_dir=mew)
                logging.info(f"[Apply] Cleaned stale Mewtator files from {mew}")

    def _apply(self):
        logging.info(f"[Apply] CALLED! gpak_path={self.gpak_path}, game_dir={self.game_dir}, overrides={len(self.overrides)} keys")
        if self._busy or not self.gpak_path or not self.overrides:
            return
        if is_game_running():
            messagebox.showwarning("Game running",
                                   "Close Mewgenics before modifying game data.")
            return
        n = len(self.overrides)
        out = self._get_output_dir()
        if out and not (Path(self.game_dir) / "Mewtator" / "Mewtator.exe").exists():
            messagebox.showwarning("Mewtator not found",
                "Mewtator does not appear to be installed.\n\n"
                "Files will be written but the game won't see them.\n"
                "Uncheck 'Mewtator mod loader' to write directly to the game directory.")
            return
        mode = "Mewtator mod folder" if out else "game directory"
        if not messagebox.askyesno("Apply Overrides",
                f"Apply {n} override(s) as loose files?\n\n"
                f"Target: {mode}\n"
                f"The original GPAK is never modified."):
            return

        self._busy = True
        self.apply_btn.config(state=tk.DISABLED)
        try:
            source = self._get_source_gpak()
            logging.info(f"[Apply] Source: {source}, output_dir: {out}")

            self._clean_stale_location(out)

            self._set_status("Building merge CSVs...")
            modifications = build_merge_csvs(source, self.overrides)

            self._set_status("Writing files...")
            written = write_loose_files(self.game_dir, modifications, output_dir=out)
            logging.info(f"[Apply] Wrote {len(written)} files")

            if out:
                write_mewtator_meta(out)

            self._sync_buttons()
            self._set_status(f"Applied {n} override(s) as {len(written)} file(s)!")
            file_list = "\n".join(str(p) for p in written)
            messagebox.showinfo("Success",
                f"{n} name override(s) applied!\n"
                f"{len(written)} file(s) written to:\n{file_list}\n\n"
                f"Restart the game to see the changes.")
        except Exception as e:
            logging.exception("[Apply] Failed")
            self._set_status(f"Error: {e}")
            messagebox.showerror("Error", f"Failed to apply:\n{e}")
        finally:
            self._busy = False
            self._sync_buttons()

    def _restore(self):
        if not self.gpak_path:
            return
        if not self._has_any_loose():
            messagebox.showinfo("No loose files",
                                "No override files found to remove.")
            return
        if is_game_running():
            messagebox.showwarning("Game running",
                                   "Close Mewgenics before removing overrides.")
            return
        if not messagebox.askyesno("Remove Overrides",
                "Delete override files?\n\n"
                "The game will use original names from the GPAK.\n"
                "Override list will be kept (not applied)."):
            return
        try:
            removed = []
            # Clean BOTH locations (direct + Mewtator)
            if has_loose_files(self.game_dir, output_dir=None):
                removed += remove_loose_files(self.game_dir, output_dir=None)
            mew = self._mewtator_dir()
            if mew and has_loose_files(self.game_dir, output_dir=mew):
                removed += remove_loose_files(self.game_dir, output_dir=mew)
            self._sync_buttons()
            self._set_status(f"Removed {len(removed)} override file(s)!")
            messagebox.showinfo("Removed",
                f"{len(removed)} override file(s) deleted.\n"
                f"Game will use original names.")
        except Exception as e:
            messagebox.showerror("Error", f"Remove failed:\n{e}")
