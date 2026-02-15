"""Tab 3: Cat Name Pools — edit the name pools for random cat generation."""
import logging
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path

from save_manager import is_game_running
from gpak_manager import find_gpak, is_valid_gpak
from game_data_manager import (
    CATNAME_POOLS,
    load_catname_pools, load_catname_overrides, save_catname_overrides,
    build_catname_files, write_loose_files, write_mewtator_meta,
    remove_loose_files, has_loose_files,
)


class CatNamePoolTab:
    def __init__(self, parent, set_status, mewtator_var=None):
        self.parent = parent
        self._set_status = set_status
        self.mewtator_var = mewtator_var

        self.gpak_path = None
        self.game_dir = None
        self.pools = {}            # {label: [name, ...]}  originals
        self.catname_overrides = {} # {label: {"added": [...], "removed": [...]}}
        self.current_pool = None   # selected pool label
        self._search_job = None
        self._busy = False

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

        self.gpak_var = tk.StringVar(value="Not found \u2014 use Browse")
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
            source = self.gpak_path.with_name(self.gpak_path.name + ".original")
            if not source.exists() or not is_valid_gpak(source):
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
            else:
                self._set_status("No cat name pools found in GPAK.")
            self._sync_buttons()
        except Exception as e:
            logging.exception("[CatNames] _load_gpak FAILED")
            messagebox.showerror("Error", f"Failed to load GPAK:\n{e}")

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
        self._listbox_data = []  # parallel array: {name, state} per listbox index
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
                self._listbox_data.append({"name": name, "state": "added"})
            else:
                self.listbox.insert(tk.END, f"  {name}")
                self._listbox_data.append({"name": name, "state": "original"})

        # Show removed names at end if no search
        if not q:
            for name in sorted(removed, key=str.lower):
                self.listbox.insert(tk.END, f"- {name}")
                self._listbox_data.append({"name": name, "state": "removed"})

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
            if idx >= len(self._listbox_data):
                continue
            entry = self._listbox_data[idx]
            name = entry["name"]
            state = entry["state"]

            if state == "removed":
                continue
            elif state == "added":
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
            try:
                # Clean up empty overrides
                for label in list(self.catname_overrides):
                    ovr = self.catname_overrides[label]
                    if not ovr.get("added") and not ovr.get("removed"):
                        del self.catname_overrides[label]
                save_catname_overrides(self.catname_overrides, self.gpak_path)
            except Exception as e:
                logging.exception("[CatNames] Failed to save overrides")
                messagebox.showerror("Save failed",
                    f"Could not save overrides to disk:\n{e}\n\n"
                    f"Your changes are still in memory but may be lost if you close the app.")
        self._refresh_list(self.search_var.get())
        self._sync_buttons()

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
        """Silently re-apply catname pool overrides to output_dir. Returns file count."""
        if not self.gpak_path or not self.catname_overrides:
            return 0
        source = self.gpak_path.with_name(self.gpak_path.name + ".original")
        if not source.exists() or not is_valid_gpak(source):
            source = self.gpak_path
        modifications = build_catname_files(source, self.catname_overrides)
        written = write_loose_files(self.game_dir, modifications, output_dir=output_dir)
        return len(written)

    def _has_any_loose_catnames(self):
        """Check if catname pool files exist in EITHER location."""
        if self.game_dir is None:
            return False
        for base in [Path(self.game_dir), self._mewtator_dir()]:
            if base and any((base / p["gpak_path"]).exists() for p in CATNAME_POOLS):
                return True
        return False

    def _sync_buttons(self):
        has = self.gpak_path is not None
        loose = has and self._has_any_loose_catnames()
        has_changes = bool(self.catname_overrides)
        self.apply_btn.config(state=tk.NORMAL if has and has_changes else tk.DISABLED)
        self.restore_btn.config(state=tk.NORMAL if loose else tk.DISABLED)

    # ---- apply / restore ----

    def _apply(self):
        if self._busy or not self.gpak_path or not self.catname_overrides:
            return
        if is_game_running():
            messagebox.showwarning("Game running",
                                   "Close Mewgenics before modifying game data.")
            return

        # Validate no pool becomes empty
        for label, orig_names in self.pools.items():
            ovr = self.catname_overrides.get(label, {})
            removed = set(ovr.get("removed", []))
            remaining = [n for n in orig_names if n not in removed]
            remaining.extend(ovr.get("added", []))
            if not remaining:
                messagebox.showerror("Empty pool",
                    f'The "{label}" pool would be empty after changes.\n\n'
                    f"Add at least one name or undo some removals.")
                return

        total_added = sum(len(o.get("added", [])) for o in self.catname_overrides.values())
        total_removed = sum(len(o.get("removed", [])) for o in self.catname_overrides.values())
        out = self._get_output_dir()
        if out and not (Path(self.game_dir) / "Mewtator" / "Mewtator.exe").exists():
            messagebox.showwarning("Mewtator not found",
                "Mewtator does not appear to be installed.\n\n"
                "Files will be written but the game won't see them.\n"
                "Uncheck 'Mewtator mod loader' to write directly to the game directory.")
            return
        mode = "Mewtator mod folder" if out else "game directory"
        if not messagebox.askyesno("Apply Cat Name Pools",
                f"Apply changes as loose files?\n\n"
                f"{total_added} name(s) added, {total_removed} name(s) removed.\n"
                f"Target: {mode}\n"
                f"Only affects newly generated cats."):
            return

        self._busy = True
        self.apply_btn.config(state=tk.DISABLED)
        try:
            # Clean the OTHER location to avoid stale files after toggling Mewtator
            if out:
                if has_loose_files(self.game_dir, output_dir=None):
                    remove_loose_files(self.game_dir, output_dir=None)
            else:
                mew = self._mewtator_dir()
                if mew and has_loose_files(self.game_dir, output_dir=mew):
                    remove_loose_files(self.game_dir, output_dir=mew)

            source = self.gpak_path.with_name(self.gpak_path.name + ".original")
            if not source.exists() or not is_valid_gpak(source):
                source = self.gpak_path

            self._set_status("Building cat name files...")
            modifications = build_catname_files(source, self.catname_overrides)

            self._set_status("Writing loose files...")
            written = write_loose_files(self.game_dir, modifications, output_dir=out)

            if out:
                write_mewtator_meta(out)

            self._sync_buttons()
            self._set_status(f"Applied cat name pool changes ({len(written)} files)!")
            messagebox.showinfo("Success",
                f"Cat name pools updated!\n"
                f"{len(written)} file(s) written.\n"
                f"New cats will use the modified name pools.")
        except Exception as e:
            logging.exception("[CatNames] Apply failed")
            self._set_status(f"Error: {e}")
            messagebox.showerror("Error", f"Failed to apply:\n{e}")
        finally:
            self._busy = False
            self._sync_buttons()

    def _restore(self):
        if not self.gpak_path:
            return
        if not self._has_any_loose_catnames():
            messagebox.showinfo("No files", "No cat name pool files to remove.")
            return
        if is_game_running():
            messagebox.showwarning("Game running",
                                   "Close Mewgenics before removing files.")
            return
        if not messagebox.askyesno("Remove Cat Name Pools",
                "Delete all loose cat name file(s)?\n\n"
                "The game will use original name pools from the GPAK.\n"
                "Your changes list will be kept."):
            return
        try:
            removed = 0
            # Clean BOTH locations (direct + Mewtator)
            for base in [Path(self.game_dir), self._mewtator_dir()]:
                if base is None:
                    continue
                for p in CATNAME_POOLS:
                    target = base / p["gpak_path"]
                    if target.exists():
                        target.unlink()
                        removed += 1
                # Clean empty dirs
                for subdir in ["data", ""]:
                    d = base / subdir if subdir else base
                    if d.exists() and d != Path(self.game_dir) and not any(d.iterdir()):
                        d.rmdir()
            self._sync_buttons()
            self._set_status(f"Removed {removed} cat name file(s)!")
            messagebox.showinfo("Removed", f"{removed} file(s) deleted.")
        except Exception as e:
            messagebox.showerror("Error", f"Remove failed:\n{e}")
