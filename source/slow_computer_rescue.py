#!/usr/bin/env python3
"""Slow Computer Rescue — simple Linux cleanup helper.

A safe-first Tkinter utility that checks storage/RAM pressure, scans common
cleanup areas, previews what will be cleaned, and deletes only after user
confirmation.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

try:
    import tkinter as tk
    from tkinter import messagebox, scrolledtext
except Exception:  # pragma: no cover - handled by self-test/runtime
    tk = None
    messagebox = None
    scrolledtext = None

APP_NAME = "Slow Computer Rescue"
APP_ID = "slow-computer-rescue"
LOG_PATH = Path.home() / ".local/state/slow-computer-rescue/cleanup.log"
DOWNLOADS_AGE_DAYS = 7
TEMP_AGE_HOURS = 24
STORAGE_WARNING_PERCENT = 80.0
RAM_WARNING_PERCENT = 85.0


@dataclass
class CleanupItem:
    path: Path
    size: int
    category: str
    note: str = ""


@dataclass
class ScanResult:
    category: str
    display_name: str
    items: list[CleanupItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        return sum(item.size for item in self.items)

    @property
    def count(self) -> int:
        return len(self.items)


@dataclass
class CleanSummary:
    deleted_count: int = 0
    deleted_bytes: int = 0
    skipped_count: int = 0
    user_messages: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def safe_stat_size(path: Path) -> int:
    try:
        if path.is_symlink():
            return 0
        if path.is_file():
            return path.stat().st_size
        if path.is_dir():
            total = 0
            for root, dirs, files in os.walk(path, topdown=True, followlinks=False):
                # Avoid following symlinked dirs.
                root_path = Path(root)
                kept_dirs = []
                for d in dirs:
                    child = root_path / d
                    if not child.is_symlink():
                        kept_dirs.append(d)
                dirs[:] = kept_dirs
                for name in files:
                    child = root_path / name
                    try:
                        if not child.is_symlink():
                            total += child.stat().st_size
                    except OSError:
                        continue
            return total
    except OSError:
        return 0
    return 0


def is_older_than(path: Path, seconds: float) -> bool:
    try:
        return (time.time() - path.stat().st_mtime) >= seconds
    except OSError:
        return False


def collect_children(paths: Iterable[Path], category: str, only_old_seconds: float | None = None) -> ScanResult:
    result = ScanResult(category=category, display_name=CATEGORY_LABELS[category])
    for base in paths:
        try:
            if not base.exists():
                continue
            for child in base.iterdir():
                try:
                    if child.is_symlink():
                        continue
                    if only_old_seconds is not None and not is_older_than(child, only_old_seconds):
                        continue
                    result.items.append(CleanupItem(child, safe_stat_size(child), category))
                except OSError as exc:
                    result.errors.append(f"Could not inspect {child}: {exc}")
        except OSError as exc:
            result.errors.append(f"Could not read {base}: {exc}")
    return result


def trash_paths(home: Path) -> list[Path]:
    return [home / ".local/share/Trash/files", home / ".local/share/Trash/info"]


def browser_cache_paths(home: Path) -> list[Path]:
    cache = home / ".cache"
    candidates = [
        cache / "mozilla/firefox",
        cache / "google-chrome/Default/Cache",
        cache / "google-chrome/Default/Code Cache",
        cache / "chromium/Default/Cache",
        cache / "chromium/Default/Code Cache",
        cache / "BraveSoftware/Brave-Browser/Default/Cache",
        cache / "BraveSoftware/Brave-Browser/Default/Code Cache",
        cache / "microsoft-edge/Default/Cache",
        cache / "microsoft-edge/Default/Code Cache",
    ]
    return candidates


def system_temp_paths() -> list[Path]:
    return [Path(tempfile.gettempdir())]


def scan_trash(home: Path) -> ScanResult:
    return collect_children(trash_paths(home), "trash")


def scan_browser_cache(home: Path) -> ScanResult:
    return collect_children(browser_cache_paths(home), "browser")


def scan_system_temp() -> ScanResult:
    result = ScanResult(category="temp", display_name=CATEGORY_LABELS["temp"])
    uid = os.getuid() if hasattr(os, "getuid") else None
    old_seconds = TEMP_AGE_HOURS * 60 * 60
    for base in system_temp_paths():
        try:
            if not base.exists():
                continue
            for child in base.iterdir():
                try:
                    if child.is_symlink() or not is_older_than(child, old_seconds):
                        continue
                    if uid is not None and child.stat().st_uid != uid:
                        continue
                    result.items.append(CleanupItem(child, safe_stat_size(child), "temp"))
                except OSError as exc:
                    result.errors.append(f"Could not inspect {child}: {exc}")
        except OSError as exc:
            result.errors.append(f"Could not read {base}: {exc}")
    return result


def scan_downloads(home: Path) -> ScanResult:
    downloads = home / "Downloads"
    old_seconds = DOWNLOADS_AGE_DAYS * 24 * 60 * 60
    return collect_children([downloads], "downloads", only_old_seconds=old_seconds)


CATEGORY_LABELS = {
    "trash": "Empty Trash",
    "browser": "Clear browser cache",
    "temp": "Clear system temp files",
    "downloads": f"Clean Downloads older than {DOWNLOADS_AGE_DAYS} days",
}

SCAN_FUNCTIONS: dict[str, Callable[[Path], ScanResult]] = {
    "trash": scan_trash,
    "browser": scan_browser_cache,
    "temp": lambda home: scan_system_temp(),
    "downloads": scan_downloads,
}


def delete_path(path: Path) -> int:
    """Delete a file/folder and return its pre-delete size.

    Browser caches can change while the app is cleaning, especially if the
    browser is still open. Retry directory removal a few times so temporary
    Cache_Data races do not become scary user-facing failures.
    """
    size = safe_stat_size(path)
    if path.is_symlink():
        return 0
    if path.is_dir():
        last_error: OSError | None = None
        for _ in range(3):
            try:
                shutil.rmtree(path)
                return size
            except FileNotFoundError:
                return size
            except OSError as exc:
                last_error = exc
                time.sleep(0.15)
        if path.exists():
            raise last_error or OSError(f"Could not delete {path}")
    elif path.exists():
        path.unlink()
    return size


def clean_results(results: Iterable[ScanResult]) -> CleanSummary:
    summary = CleanSummary()
    browser_skip_message_added = False
    for result in results:
        for item in result.items:
            try:
                deleted_size = delete_path(item.path)
                summary.deleted_count += 1
                summary.deleted_bytes += deleted_size
            except OSError as exc:
                summary.skipped_count += 1
                if result.category == "browser":
                    if not browser_skip_message_added:
                        summary.user_messages.append(
                            "Some browser cache files were skipped because your browser may still be open. "
                            "Close the browser and run cleanup again to remove more browser cache."
                        )
                        browser_skip_message_added = True
                    summary.errors.append(f"Browser cache skipped: {item.path} — {exc}")
                else:
                    summary.user_messages.append(
                        f"Some items could not be cleaned: {item.path.name}. You can try closing apps and running cleanup again."
                    )
                    summary.errors.append(f"Could not delete {item.path}: {exc}")
    return summary


def write_cleanup_log(summary: CleanSummary) -> None:
    if not summary.errors:
        return
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Cleanup technical details\n")
            for err in summary.errors:
                handle.write(f"- {err}\n")
    except OSError:
        # Logging should never make cleanup look failed to the user.
        pass


def storage_status(path: Path) -> tuple[int, int, int, float]:
    usage = shutil.disk_usage(path)
    percent_used = (usage.used / usage.total * 100.0) if usage.total else 0.0
    return usage.total, usage.used, usage.free, percent_used


def ram_status() -> tuple[int, int, float] | None:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return None
    values: dict[str, int] = {}
    for line in meminfo.read_text(errors="ignore").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                values[parts[0].rstrip(":")] = int(parts[1]) * 1024
            except ValueError:
                continue
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not total or available is None:
        return None
    used = total - available
    percent_used = used / total * 100.0
    return total, used, percent_used


class SlowComputerRescueApp:
    def __init__(self, root: "tk.Tk") -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("820x680")
        self.home = Path.home()
        self.selected_vars: dict[str, tk.BooleanVar] = {}
        self.scan_results: dict[str, ScanResult] = {}

        self.status_var = tk.StringVar(value="Checking computer health...")
        self.summary_var = tk.StringVar(value="Click Check & Clean My Computer to see what can be cleaned.")
        self.ready_to_clean = False

        self._build_ui()
        self.refresh_health()

    def _build_ui(self) -> None:
        main = tk.Frame(self.root, padx=16, pady=14)
        main.pack(fill=tk.BOTH, expand=True)

        title = tk.Label(main, text=APP_NAME, font=("TkDefaultFont", 20, "bold"))
        title.pack(anchor="w")

        subtitle = tk.Label(
            main,
            text="Computer running slow? Storage that is almost full can make things feel worse. This tool checks common cleanup areas and lets you clean them safely.",
            wraplength=760,
            justify="left",
        )
        subtitle.pack(anchor="w", pady=(4, 12))

        health_box = tk.LabelFrame(main, text="Speed Check", padx=10, pady=8)
        health_box.pack(fill=tk.X)
        tk.Label(health_box, textvariable=self.status_var, justify="left", anchor="w").pack(fill=tk.X)

        options_box = tk.LabelFrame(main, text="Select cleanup options", padx=10, pady=8)
        options_box.pack(fill=tk.X, pady=(12, 8))

        for key in ["trash", "browser", "temp", "downloads"]:
            var = tk.BooleanVar(value=True)
            self.selected_vars[key] = var
            tk.Checkbutton(options_box, text=CATEGORY_LABELS[key], variable=var).pack(anchor="w")

        button_row = tk.Frame(main)
        button_row.pack(fill=tk.X, pady=(6, 8))
        self.main_action_button = tk.Button(
            button_row,
            text="Check & Clean My Computer",
            command=self.main_action,
            font=("TkDefaultFont", 12, "bold"),
            padx=18,
            pady=8,
        )
        self.main_action_button.pack(side=tk.LEFT)

        tk.Label(main, textvariable=self.summary_var, font=("TkDefaultFont", 11, "bold"), anchor="w").pack(fill=tk.X)

        results_box = tk.LabelFrame(main, text="Preview", padx=8, pady=8)
        results_box.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.preview = scrolledtext.ScrolledText(results_box, height=20, wrap=tk.WORD)
        self.preview.pack(fill=tk.BOTH, expand=True)
        self.preview.insert(tk.END, "No scan yet. Click Scan Selected Areas.\n")
        self.preview.configure(state=tk.DISABLED)

    def refresh_health(self) -> None:
        try:
            total, used, free, percent = storage_status(self.home)
            storage_line = f"Storage: {percent:.0f}% full ({human_size(free)} free)"
            if percent >= STORAGE_WARNING_PERCENT:
                storage_line += " — Needs attention. Full storage can make your computer feel slow."
            else:
                storage_line += " — Looks okay."

            ram = ram_status()
            if ram:
                ram_total, ram_used, ram_percent = ram
                ram_line = f"RAM usage: {ram_percent:.0f}% used ({human_size(ram_used)} of {human_size(ram_total)})"
                if ram_percent >= RAM_WARNING_PERCENT:
                    ram_line += " — High usage. Closing heavy apps may help."
                else:
                    ram_line += " — Looks okay."
            else:
                ram_line = "RAM usage: not available on this system."

            self.status_var.set(storage_line + "\n" + ram_line)
        except Exception as exc:
            self.status_var.set(f"Could not read computer health: {exc}")

    def main_action(self) -> None:
        if self.ready_to_clean:
            self.clean_selected()
        else:
            self.scan_selected()

    def selected_categories(self) -> list[str]:
        return [key for key, var in self.selected_vars.items() if var.get()]

    def scan_selected(self) -> None:
        categories = self.selected_categories()
        if not categories:
            messagebox.showinfo(APP_NAME, "Select at least one cleanup option first.")
            return
        self.ready_to_clean = False
        self.main_action_button.configure(text="Scanning...", state=tk.DISABLED)
        self.root.update_idletasks()
        self.refresh_health()
        self.scan_results = {}
        lines = []
        total = 0
        for key in categories:
            result = SCAN_FUNCTIONS[key](self.home)
            self.scan_results[key] = result
            total += result.total_size
            lines.append(f"{result.display_name}: {human_size(result.total_size)} across {result.count} item(s)")
            for err in result.errors:
                lines.append(f"  Warning: {err}")
            if key == "downloads" and result.items:
                lines.append("  Downloads files that would be deleted:")
                for item in sorted(result.items, key=lambda i: str(i.path).lower())[:200]:
                    lines.append(f"   - {item.path.name} ({human_size(item.size)})")
                if len(result.items) > 200:
                    lines.append(f"   ... and {len(result.items) - 200} more item(s)")
        self.summary_var.set(f"Possible cleanup: {human_size(total)}")
        self._set_preview("\n".join(lines) if lines else "Nothing found to clean.")
        self.ready_to_clean = total > 0
        if self.ready_to_clean:
            self.main_action_button.configure(text="Clean Selected Items", state=tk.NORMAL)
        else:
            self.main_action_button.configure(text="Check & Clean My Computer", state=tk.NORMAL)
        self.refresh_health()

    def clean_selected(self) -> None:
        if not self.scan_results:
            self.ready_to_clean = False
            self.main_action_button.configure(text="Check & Clean My Computer", state=tk.NORMAL)
            messagebox.showinfo(APP_NAME, "Run a scan first so you can preview what will be cleaned.")
            return
        selected_results = [self.scan_results[key] for key in self.selected_categories() if key in self.scan_results]
        total = sum(result.total_size for result in selected_results)
        count = sum(result.count for result in selected_results)
        if count == 0:
            self.ready_to_clean = False
            self.main_action_button.configure(text="Check & Clean My Computer", state=tk.NORMAL)
            messagebox.showinfo(APP_NAME, "Nothing selected was found to clean.")
            return
        warning = (
            f"This will delete {count} item(s) and may free about {human_size(total)}.\n\n"
            "Downloads files listed in the preview will be deleted if selected.\n"
            "Trash items will be permanently removed.\n\n"
            "Do you want to continue?"
        )
        if not messagebox.askyesno(APP_NAME, warning):
            return
        self.main_action_button.configure(text="Cleaning...", state=tk.DISABLED)
        self.root.update_idletasks()
        before = storage_status(self.home)
        summary = clean_results(selected_results)
        write_cleanup_log(summary)
        after = storage_status(self.home)
        freed_message = (
            f"Cleanup complete.\n\n"
            f"Deleted: {summary.deleted_count} item(s)\n"
            f"Skipped: {summary.skipped_count} item(s)\n"
            f"Freed during cleanup: {human_size(summary.deleted_bytes)}\n"
            f"Before: {human_size(before[2])} free ({before[3]:.0f}% full)\n"
            f"After: {human_size(after[2])} free ({after[3]:.0f}% full)"
        )
        if summary.user_messages:
            unique_messages = list(dict.fromkeys(summary.user_messages))
            freed_message += "\n\nNotes:\n" + "\n".join(f"- {msg}" for msg in unique_messages[:5])
        if summary.errors:
            freed_message += "\n\nTechnical details were logged for troubleshooting."
        messagebox.showinfo(APP_NAME, freed_message)
        self.scan_results = {}
        self.ready_to_clean = False
        self.main_action_button.configure(text="Check & Clean My Computer", state=tk.NORMAL)
        self.summary_var.set("Cleanup complete. Click Check & Clean My Computer to scan again.")
        self._set_preview(freed_message)
        self.refresh_health()

    def _set_preview(self, text: str) -> None:
        self.preview.configure(state=tk.NORMAL)
        self.preview.delete("1.0", tk.END)
        self.preview.insert(tk.END, text + "\n")
        self.preview.configure(state=tk.DISABLED)


def run_self_test() -> int:
    assert human_size(0) == "0 B"
    assert human_size(1024).endswith("KB")
    total, used, free, percent = storage_status(Path.home())
    assert total > 0 and used >= 0 and free >= 0 and 0 <= percent <= 100
    ram = ram_status()
    if ram is not None:
        ram_total, ram_used, ram_percent = ram
        assert ram_total > 0 and ram_used >= 0 and 0 <= ram_percent <= 100

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        old_file = root / "old.txt"
        old_file.write_text("junk" * 100)
        old_time = time.time() - (DOWNLOADS_AGE_DAYS + 1) * 24 * 60 * 60
        os.utime(old_file, (old_time, old_time))
        result = collect_children([root], "downloads", only_old_seconds=DOWNLOADS_AGE_DAYS * 24 * 60 * 60)
        assert result.count == 1
        summary = clean_results([result])
        assert summary.deleted_count == 1
        assert not old_file.exists()
    print("SELF-TEST PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--self-test", action="store_true", help="Run non-GUI integrity checks and exit")
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    if tk is None:
        print("Tkinter is required to run the graphical app.", file=sys.stderr)
        return 1
    root = tk.Tk()
    app = SlowComputerRescueApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
