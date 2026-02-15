"""Tab 1: Save Renamer — rename cats in existing save files."""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
import logging
import os

from constants import MAX_NAME_LEN, MIN_NAME_LEN, BACKUP_EXTENSION
from save_manager import (
    discover_saves, open_save, get_all_entries, create_backup,
    restore_backup, write_blob, is_game_running, list_backups,
    cleanup_old_backups,
)
from entity_registry import ENTITY_CATEGORIES
from name_modifier import replace_display_name, verify_modified_blob, validate_new_name


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
        self._busy = False

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
        ttk.Button(r3, text="Restore Backup...", command=self._on_restore).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Separator(r3, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        ttk.Button(r3, text="Open Save Folder",
                   command=self._open_save_folder).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(r3, text="Open Backup Folder",
                   command=self._open_backup_folder).pack(side=tk.LEFT)

    # ---- save discovery ----

    def _discover_saves(self):
        try:
            self.saves = discover_saves()
        except Exception as e:
            logging.exception("Failed to discover saves")
            self.saves = []
            self._set_status(f"Could not scan save directory: {e}")
            return
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
            try:
                raw = get_all_entries(conn)
            finally:
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
        if self._busy or not self.selected_entry or not self.current_save_path:
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

        self._busy = True
        self.rename_btn.config(state=tk.DISABLED)
        try:
            self._set_status("Creating backup...")
            bp = create_backup(self.current_save_path)
            self.last_backup = bp

            self._set_status("Modifying name...")
            new_blob = replace_display_name(entry["blob"], new_name)
            ok, msg = verify_modified_blob(entry["blob"], new_blob, new_name)
            if not ok:
                messagebox.showerror("Verification failed", msg)
                return
            write_blob(self.current_save_path, entry["source"], entry["key"], new_blob)

            self._load_save(self.current_save_path)
            cleanup_old_backups(self.current_save_path)
            self._set_status(f'Renamed to "{new_name}". Backup: {bp.name}')
            messagebox.showinfo("Success", f'Renamed to "{new_name}"!')
        except Exception as e:
            messagebox.showerror("Rename failed", str(e))
        finally:
            self._busy = False

    # ---- restore ----

    def _on_restore(self):
        if not self.current_save_path:
            messagebox.showinfo("No save", "Load a save file first.")
            return
        if is_game_running():
            messagebox.showwarning("Game running", "Close Mewgenics before restoring.")
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

        if not messagebox.askyesno("Confirm",
                f"Restore backup:\n{bp.name}\n\nOverwrite current save?\n\n"
                f"This cannot be undone."):
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
        try:
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
            dlg.bind("<Return>", lambda _: on_ok())
            dlg.bind("<Escape>", lambda _: dlg.destroy())
            dlg.wait_window()
        except Exception:
            dlg.destroy()
            raise
        return result[0]

    def _open_save_folder(self):
        if not self.current_save_path:
            messagebox.showinfo("No save", "Load a save file first.")
            return
        folder = self.current_save_path.parent
        if folder.exists():
            try:
                os.startfile(folder)
            except OSError as e:
                messagebox.showerror("Error", f"Could not open folder:\n{e}")

    def _open_backup_folder(self):
        if not self.current_save_path:
            messagebox.showinfo("No save", "Load a save file first.")
            return
        folder = self.current_save_path.parent / "backups"
        if folder.exists():
            try:
                os.startfile(folder)
            except OSError as e:
                messagebox.showerror("Error", f"Could not open folder:\n{e}")
        else:
            messagebox.showinfo("No backups", "No backup folder yet — make a change first to create one.")
