#!/usr/bin/env python3
"""gui.py: Modern graphical user interface for screenstudio-to-mp4 desktop app."""

import os
import sys
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from exporter import RenderExporter, find_ffmpeg
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from exporter import RenderExporter, find_ffmpeg


class ScreenStudioApp(tk.Tk):
    """Modern Dark-Themed Desktop Application for screenstudio-to-mp4."""

    def __init__(self):
        super().__init__()

        self.title("screenstudio-to-mp4 — macOS Exporter")
        self.geometry("720x680")
        self.minsize(680, 600)

        # Apply Catppuccin Mocha / Dark Theme Palette
        self.BG_COLOR = "#1E1E2E"
        self.CARD_BG = "#2A2A3C"
        self.TEXT_COLOR = "#CDD6F4"
        self.SUBTEXT_COLOR = "#A6ADC8"
        self.ACCENT_COLOR = "#89B4FA"
        self.SUCCESS_COLOR = "#A6E3A1"
        self.ERROR_COLOR = "#F38BA8"
        self.ENTRY_BG = "#11111B"

        self.configure(bg=self.BG_COLOR)

        # Apply ttk Styles
        self._init_styles()

        # UI State Variables
        self.bundle_path_var = tk.StringVar()
        self.output_path_var = tk.StringVar()
        self.frame_path_var = tk.StringVar()
        self.screen_frac_var = tk.DoubleVar(value=0.78)
        self.webcam_var = tk.StringVar(value="auto")
        self.zooms_var = tk.StringVar(value="on")
        self.cursor_var = tk.StringVar(value="auto")
        self.audio_cleanup_var = tk.StringVar(value="loudnorm")
        self.preset_var = tk.StringVar(value="slow")

        self.is_rendering = False

        self._create_widgets()
        self._check_ffmpeg()

    def _init_styles(self):
        self.style = ttk.Style(self)
        self.style.theme_use("clam")

        self.style.configure(".", background=self.BG_COLOR, foreground=self.TEXT_COLOR, font=("SF Pro Text", 12))
        self.style.configure("TFrame", background=self.BG_COLOR)
        self.style.configure("Card.TFrame", background=self.CARD_BG, relief="flat", borderwidth=0)
        
        self.style.configure("TLabel", background=self.BG_COLOR, foreground=self.TEXT_COLOR)
        self.style.configure("CardLabel.TLabel", background=self.CARD_BG, foreground=self.TEXT_COLOR)
        self.style.configure("Header.TLabel", background=self.BG_COLOR, foreground=self.ACCENT_COLOR, font=("SF Pro Display", 20, "bold"))
        self.style.configure("SubHeader.TLabel", background=self.BG_COLOR, foreground=self.SUBTEXT_COLOR, font=("SF Pro Text", 11))
        
        self.style.configure("TButton", background=self.CARD_BG, foreground=self.TEXT_COLOR, borderwidth=0, padding=8, font=("SF Pro Text", 11, "bold"))
        self.style.map("TButton", background=[("active", self.ACCENT_COLOR), ("disabled", "#45475A")])

        self.style.configure("Accent.TButton", background=self.ACCENT_COLOR, foreground="#11111B", borderwidth=0, padding=10, font=("SF Pro Text", 13, "bold"))
        self.style.map("Accent.TButton", background=[("active", "#B4BEFE"), ("disabled", "#45475A")])

        self.style.configure("TProgressbar", thickness=14, troughcolor=self.CARD_BG, background=self.ACCENT_COLOR)

    def _create_widgets(self):
        # Container padding frame
        main_frame = ttk.Frame(self, padding=24)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Header Title & Subtitle
        header_lbl = ttk.Label(main_frame, text="🎬 screenstudio-to-mp4", style="Header.TLabel")
        header_lbl.pack(anchor="w", pady=(0, 2))

        sub_lbl = ttk.Label(main_frame, text="Export macOS Screen Studio recordings to MP4 — free, offline, no subscription.", style="SubHeader.TLabel")
        sub_lbl.pack(anchor="w", pady=(0, 16))

        # -------------------------------------------------------------
        # File Selection Card
        # -------------------------------------------------------------
        file_card = ttk.Frame(main_frame, style="Card.TFrame", padding=16)
        file_card.pack(fill=tk.X, pady=(0, 16))

        # Bundle Input Selection
        ttk.Label(file_card, text="📁 Select .screenstudio Recording:", style="CardLabel.TLabel", font=("SF Pro Text", 11, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
        bundle_entry = tk.Entry(file_card, textvariable=self.bundle_path_var, bg=self.ENTRY_BG, fg=self.TEXT_COLOR, insertbackground=self.TEXT_COLOR, relief="flat", font=("SF Pro Text", 11))
        bundle_entry.grid(row=1, column=0, sticky="ew", padx=(0, 8), ipady=6)
        
        browse_btn = ttk.Button(file_card, text="Browse Bundle...", command=self._browse_bundle)
        browse_btn.grid(row=1, column=1, sticky="e")

        # Output Path Selection
        ttk.Label(file_card, text="💾 Output MP4 Destination:", style="CardLabel.TLabel", font=("SF Pro Text", 11, "bold")).grid(row=2, column=0, sticky="w", pady=(12, 4))
        output_entry = tk.Entry(file_card, textvariable=self.output_path_var, bg=self.ENTRY_BG, fg=self.TEXT_COLOR, insertbackground=self.TEXT_COLOR, relief="flat", font=("SF Pro Text", 11))
        output_entry.grid(row=3, column=0, sticky="ew", padx=(0, 8), ipady=6)

        save_btn = ttk.Button(file_card, text="Save As...", command=self._browse_output)
        save_btn.grid(row=3, column=1, sticky="e")

        file_card.columnconfigure(0, weight=1)

        # -------------------------------------------------------------
        # Customization Options Card
        # -------------------------------------------------------------
        opts_card = ttk.Frame(main_frame, style="Card.TFrame", padding=16)
        opts_card.pack(fill=tk.X, pady=(0, 16))

        ttk.Label(opts_card, text="⚙️ Export Options & Composition", style="CardLabel.TLabel", font=("SF Pro Text", 12, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        # Custom Frame Background Image
        ttk.Label(opts_card, text="Custom Background Frame Image (Optional):", style="CardLabel.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        frame_frame = ttk.Frame(opts_card, style="Card.TFrame")
        frame_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        
        frame_entry = tk.Entry(frame_frame, textvariable=self.frame_path_var, bg=self.ENTRY_BG, fg=self.TEXT_COLOR, insertbackground=self.TEXT_COLOR, relief="flat", font=("SF Pro Text", 11))
        frame_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8), ipady=4)
        ttk.Button(frame_frame, text="Select Frame Image...", command=self._browse_frame).pack(side=tk.RIGHT)

        # Option Selectors: Screen Scale & Audio Cleanup
        row_f = ttk.Frame(opts_card, style="Card.TFrame")
        row_f.grid(row=3, column=0, columnspan=2, sticky="ew", pady=4)

        ttk.Label(row_f, text="Screen Scale Ratio:", style="CardLabel.TLabel").pack(side=tk.LEFT, padx=(0, 8))
        scale_slider = ttk.Scale(row_f, from_=0.5, to=1.0, variable=self.screen_frac_var, orient="horizontal", length=140)
        scale_slider.pack(side=tk.LEFT, padx=(0, 16))

        ttk.Label(row_f, text="Audio Cleanup:", style="CardLabel.TLabel").pack(side=tk.LEFT, padx=(0, 8))
        audio_combo = ttk.Combobox(row_f, textvariable=self.audio_cleanup_var, values=["loudnorm", "none", "voice"], state="readonly", width=10)
        audio_combo.pack(side=tk.LEFT)

        opts_card.columnconfigure(0, weight=1)

        # -------------------------------------------------------------
        # Progress & Action Section
        # -------------------------------------------------------------
        self.status_lbl = ttk.Label(main_frame, text="Ready for export.", font=("SF Pro Text", 11, "italic"))
        self.status_lbl.pack(anchor="w", pady=(0, 4))

        self.progress = ttk.Progressbar(main_frame, mode="determinate", value=0)
        self.progress.pack(fill=tk.X, pady=(0, 16))

        # Main Action Button
        self.export_btn = ttk.Button(main_frame, text="🚀 Export to MP4", style="Accent.TButton", command=self._start_export)
        self.export_btn.pack(fill=tk.X, ipady=4)

    def _check_ffmpeg(self):
        ffmpeg_bin = find_ffmpeg()
        if not ffmpeg_bin or (ffmpeg_bin == "ffmpeg" and not shutil.which("ffmpeg")):
            self.status_lbl.configure(text="⚠️ Warning: ffmpeg not detected on PATH.", foreground=self.ERROR_COLOR)

    def _browse_bundle(self):
        path = filedialog.askdirectory(title="Select .screenstudio Recording Package")
        if path:
            self.bundle_path_var.set(path)
            # Auto-set output destination if not set
            if not self.output_path_var.get():
                name = os.path.basename(path).replace(".screenstudio", "") + ".mp4"
                downloads = os.path.expanduser("~/Downloads")
                self.output_path_var.set(os.path.join(downloads, name))

    def _browse_output(self):
        path = filedialog.asksaveasfilename(title="Save MP4 Video As...", defaultextension=".mp4", filetypes=[("MP4 Video", "*.mp4")])
        if path:
            self.output_path_var.set(path)

    def _browse_frame(self):
        path = filedialog.askopenfilename(title="Select Background Image", filetypes=[("Image Files", "*.png *.jpg *.jpeg *.webp")])
        if path:
            self.frame_path_var.set(path)

    def _start_export(self):
        bundle = self.bundle_path_var.get().strip()
        output = self.output_path_var.get().strip()

        if not bundle or not os.path.exists(bundle):
            messagebox.showerror("Error", "Please select a valid .screenstudio recording directory.")
            return

        if not output:
            messagebox.showerror("Error", "Please specify an output destination MP4 path.")
            return

        self.is_rendering = True
        self.export_btn.configure(state="disabled")
        self.progress["value"] = 0
        self.status_lbl.configure(text="Starting render engine...", foreground=self.ACCENT_COLOR)

        options = {
            "screen_frac": round(self.screen_frac_var.get(), 2),
            "webcam": self.webcam_var.get(),
            "zooms": self.zooms_var.get(),
            "cursor": self.cursor_var.get(),
            "audio_cleanup": self.audio_cleanup_var.get(),
            "preset": self.preset_var.get(),
            "frame": self.frame_path_var.get().strip() or None,
        }

        # Run pipeline in a non-blocking background thread
        thread = threading.Thread(target=self._run_export_thread, args=(bundle, output, options), daemon=True)
        thread.start()

    def _run_export_thread(self, bundle: str, output: str, options: dict):
        def update_ui(msg: str, pct: float):
            self.after(0, self._on_progress_update, msg, pct)

        try:
            exporter = RenderExporter(bundle, output, options=options)
            exporter.run_pipeline(progress_callback=update_ui)

            self.after(0, self._on_export_success, output)
        except Exception as e:
            self.after(0, self._on_export_failure, str(e))

    def _on_progress_update(self, msg: str, pct: float):
        if pct >= 0:
            self.progress["value"] = pct
        self.status_lbl.configure(text=msg, foreground=self.TEXT_COLOR)

    def _on_export_success(self, output_path: str):
        self.is_rendering = False
        self.export_btn.configure(state="normal")
        self.progress["value"] = 100
        self.status_lbl.configure(text="🎉 Export completed successfully!", foreground=self.SUCCESS_COLOR)

        ans = messagebox.askyesno("Export Complete", f"Your MP4 has been exported successfully to:\n\n{output_path}\n\nWould you like to open the output folder?")
        if ans:
            subprocess.run(["open", "-R", output_path])

    def _on_export_failure(self, error_msg: str):
        self.is_rendering = False
        self.export_btn.configure(state="normal")
        self.status_lbl.configure(text="❌ Export failed.", foreground=self.ERROR_COLOR)
        messagebox.showerror("Export Failed", f"An error occurred during rendering:\n\n{error_msg}")


def main():
    app = ScreenStudioApp()
    app.mainloop()


if __name__ == "__main__":
    main()
