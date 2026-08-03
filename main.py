import argparse
import os
import queue
import re
import socket
import subprocess
import sys
import threading

SOCKET_PATH = "/tmp/tts_daemon.sock"

def load_tts_model():
    from kittentts import KittenTTS
    return KittenTTS("KittenML/kitten-tts-nano-0.8")

def parse_args():
    parser = argparse.ArgumentParser(description="GTK4 TTS Overlay")
    parser.add_argument("-d", "--daemon", action="store_true", help="Run the TTS daemon")
    parser.add_argument("--device", type=str, default="pipewire", help="ALSA sound device to use (e.g. pipewire, pulse, default)")
    parser.add_argument("--voice", type=str, default="Rosie", help="Voice to use for TTS (default: Rosie)")
    parser.add_argument("--virtual", action="store_true", help="Start TTS on a virtual null PulseAudio sink and unload it on close")
    parser.add_argument("--feedback", action="store_true", help="Enable audio feedback loopback from virtual sink monitor to default sink")
    parser.add_argument("--list-voices", action="store_true", help="List available voices and exit")
    
    # GUI config
    parser.add_argument("--anchor-top", action="store_true", default=True, help="Anchor to top edge (default: True)")
    parser.add_argument("--no-anchor-top", action="store_false", dest="anchor_top", help="Do not anchor to top edge")
    parser.add_argument("--anchor-bottom", action="store_true", help="Anchor to bottom edge")
    parser.add_argument("--anchor-left", action="store_true", help="Anchor to left edge")
    parser.add_argument("--anchor-right", action="store_true", help="Anchor to right edge")
    
    parser.add_argument("--margin-top", type=int, default=10, help="Top margin in pixels (default: 10)")
    parser.add_argument("--margin-bottom", type=int, default=0, help="Bottom margin in pixels")
    parser.add_argument("--margin-left", type=int, default=0, help="Left margin in pixels")
    parser.add_argument("--margin-right", type=int, default=0, help="Right margin in pixels")
    
    return parser.parse_args()

def list_voices():
    print("Loading model to fetch available voices...")
    model = load_tts_model()
    voices = getattr(model, 'available_voices', [])
    if voices:
        print("\nAvailable voices:")
        for v in voices:
            print(f" - {v}")
    else:
        print("\nNo voices found or 'available_voices' property not present.")

def tts_worker(text_queue, device, voice):
    import alsaaudio
    from kittentts import normalize_text

    from load_clean_save import load_clean_json
    
    pcm = alsaaudio.PCM(
        type=alsaaudio.PCM_PLAYBACK,
        device=device,
        rate=24000,
        channels=1,
        format=alsaaudio.PCM_FORMAT_FLOAT_LE,
    )
    print("Loading TTS model...")
    model = load_tts_model()
    lookup = load_clean_json()
    
    pattern = '[a-zA-Z0-9]+'
    def advance_normalize_text(text, lookup) -> str:
        normalized = normalize_text(text)
        return re.sub(pattern, lambda match: lookup.get(match.group(), match.group()), normalized)

    print("TTS daemon ready to speak.")
    while True:
        text = text_queue.get()
        if text is None:
            break
        print(f"Speaking: {text}")
        try:
            import time
            start_time = time.time()
            first_chunk = True
            normalized = advance_normalize_text(text, lookup)
            for audio in model.generate_stream(normalized, voice=voice, speed=1.3):
                if first_chunk:
                    diff = time.time() - start_time
                    print(f"Time to first audio chunk: {diff:.3f}s")
                    first_chunk = False
                pcm.write(audio.squeeze().tobytes())
                pcm.drain()
            # pcm.close()
        except Exception as e:
            print(f"Error playing audio: {e}")
        print(f"task done: {text}")
        text_queue.task_done()
        
    print("Closing PCM device...")
    try:
        pcm.close()
    except Exception as e:
        print(f"Error closing PCM: {e}")

def run_daemon(device, voice, virtual=False, feedback=False):
    loaded_modules = []
    if feedback:
        virtual = True

    if virtual:
        device = "pulse:TTS_Virtual_Sink"
        try:
            res = subprocess.run(
                ["pactl", "load-module", "module-null-sink", "sink_name=TTS_Virtual_Sink", "sink_properties=device.description=TTS_Virtual_Sink"],
                capture_output=True,
                text=True,
                check=True
            )
            mod_id = res.stdout.strip()
            loaded_modules.append(mod_id)
            print(f"Loaded virtual null sink 'TTS_Virtual_Sink' (module ID: {mod_id})")
        except Exception as e:
            print(f"Error loading virtual null sink: {e}")

    if feedback:
        try:
            res = subprocess.run(
                ["pactl", "load-module", "module-loopback", "source=TTS_Virtual_Sink.monitor", "sink=@DEFAULT_SINK@"],
                capture_output=True,
                text=True,
                check=True
            )
            mod_id = res.stdout.strip()
            loaded_modules.append(mod_id)
            print(f"Loaded loopback feedback module (module ID: {mod_id})")
        except Exception as e:
            print(f"Error loading loopback feedback module: {e}")

    if os.path.exists(SOCKET_PATH):
        try:
            # Check if it's a dead socket
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(SOCKET_PATH)
            client.close()
            print("Daemon is already running.")
            for mod_id in reversed(loaded_modules):
                subprocess.run(["pactl", "unload-module", mod_id], check=False)
            sys.exit(1)
        except ConnectionRefusedError:
            os.remove(SOCKET_PATH)
    
    text_queue = queue.Queue()
    worker = threading.Thread(target=tts_worker, args=(text_queue, device, voice), daemon=True)
    worker.start()
    
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    server.listen()
    
    print(f"Daemon listening on {SOCKET_PATH}")
    try:
        while True:
            conn, _ = server.accept()
            data = conn.recv(4096)
            if data:
                text = data.decode('utf-8').strip()
                if text:
                    text_queue.put(text)
            conn.close()
    except KeyboardInterrupt:
        print("Shutting down daemon...")
    finally:
        print("Sending shutdown signal to worker...")
        text_queue.put(None)
        worker.join(timeout=2.0)
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)
        for mod_id in reversed(loaded_modules):
            try:
                subprocess.run(["pactl", "unload-module", mod_id], check=False)
                print(f"Unloaded module (ID: {mod_id})")
            except Exception as e:
                print(f"Error unloading module {mod_id}: {e}")

def run_gui(args):
    # Check if daemon is running
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(SOCKET_PATH)
        client.close()
    except Exception as e:
        sys.stderr.write(f"Error: Daemon not running ({e}). Start with '-d'.\n")
        sys.exit(1)

    from ctypes import CDLL
    CDLL('libgtk4-layer-shell.so')
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Gtk4LayerShell', '1.0')
    from gi.repository import Gdk, GLib, Gtk, Gtk4LayerShell

    class HistoryManager:
        def __init__(self, file_path=None, max_size=1000):
            if file_path is None:
                application_dir = os.path.dirname(os.path.abspath(__file__))
                self.file_path = os.path.join(application_dir, "history.txt")
            else:
                self.file_path = file_path
            self.max_size = max_size
            self.history = []
            self.load()

        def load(self):
            if os.path.exists(self.file_path):
                try:
                    with open(self.file_path, "r", encoding="utf-8") as f:
                        lines = [line.rstrip("\r\n") for line in f if line.strip()]
                        self.history = lines[-self.max_size:]
                except Exception as e:
                    print(f"Error loading history: {e}")

        def save(self):
            try:
                os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
                with open(self.file_path, "w", encoding="utf-8") as f:
                    f.writelines(f"{item}\n" for item in self.history)
            except Exception as e:
                print(f"Error saving history: {e}")

        def add(self, text):
            text = text.strip()
            if not text:
                return
            if self.history and self.history[-1] == text:
                return
            if text in self.history:
                self.history.remove(text)
            self.history.append(text)
            if len(self.history) > self.max_size:
                self.history = self.history[-self.max_size:]
            self.save()

        def find_completion(self, prefix):
            if not prefix:
                return None
            for item in reversed(self.history):
                if item.startswith(prefix) and item != prefix:
                    return item[len(prefix):]
            return None

    class TTSApp(Gtk.Application):
        def __init__(self, args):
            super().__init__(application_id='com.example.TTSOverlay', flags=gi.repository.Gio.ApplicationFlags.FLAGS_NONE)
            self.args = args
            self.history_manager = HistoryManager()
            self.current_completion = ""

        def do_activate(self):
            window = Gtk.ApplicationWindow(application=self, title="TTS Overlay")
            Gtk4LayerShell.init_for_window(window)
            Gtk4LayerShell.set_namespace(window, "tts-overlay")
            
            # Layer and keyboard
            Gtk4LayerShell.set_layer(window, Gtk4LayerShell.Layer.OVERLAY)
            Gtk4LayerShell.set_keyboard_mode(window, Gtk4LayerShell.KeyboardMode.ON_DEMAND)
            
            # Anchors
            anchors = {
                Gtk4LayerShell.Edge.TOP: self.args.anchor_top,
                Gtk4LayerShell.Edge.BOTTOM: self.args.anchor_bottom,
                Gtk4LayerShell.Edge.LEFT: self.args.anchor_left,
                Gtk4LayerShell.Edge.RIGHT: self.args.anchor_right,
            }
            has_anchor = False
            for edge, enabled in anchors.items():
                if enabled:
                    Gtk4LayerShell.set_anchor(window, edge, True)
                    has_anchor = True
                    
            if not has_anchor:
                Gtk4LayerShell.set_anchor(window, Gtk4LayerShell.Edge.TOP, True)
                
            # Margins
            Gtk4LayerShell.set_margin(window, Gtk4LayerShell.Edge.TOP, self.args.margin_top)
            Gtk4LayerShell.set_margin(window, Gtk4LayerShell.Edge.BOTTOM, self.args.margin_bottom)
            Gtk4LayerShell.set_margin(window, Gtk4LayerShell.Edge.LEFT, self.args.margin_left)
            Gtk4LayerShell.set_margin(window, Gtk4LayerShell.Edge.RIGHT, self.args.margin_right)
            
            # UI
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            box.set_margin_top(12)
            box.set_margin_bottom(12)
            box.set_margin_start(12)
            box.set_margin_end(12)
            
            overlay = Gtk.Overlay()

            self.entry = Gtk.Entry()
            self.entry.set_placeholder_text("Type to speak... (Esc to quit)")
            self.entry.set_width_chars(40)
            self.entry.connect("activate", self.on_enter_pressed)
            self.entry.connect("changed", self.on_entry_changed)
            self.entry.connect("notify::cursor-position", self.on_cursor_moved)
            
            completion_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            completion_box.set_can_target(False)
            completion_box.set_valign(Gtk.Align.CENTER)
            completion_box.set_halign(Gtk.Align.START)
            completion_box.add_css_class("completion-box")

            self.lbl_prefix = Gtk.Label()
            self.lbl_prefix.add_css_class("completion-prefix")

            self.lbl_suffix = Gtk.Label()
            self.lbl_suffix.add_css_class("completion-suffix")

            completion_box.append(self.lbl_prefix)
            completion_box.append(self.lbl_suffix)
            completion_box.set_visible(False)
            self.completion_box = completion_box

            overlay.set_child(self.entry)
            overlay.add_overlay(completion_box)

            box.append(overlay)
            window.set_child(box)
            
            # CSS Styling
            provider = Gtk.CssProvider()
            provider.load_from_data(b"""
            window {
                background-color: rgba(30, 30, 30, 0.95);
                border-radius: 8px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.5);
            }
            entry {
                background-color: rgba(50, 50, 50, 0.8);
                color: white;
                border: 1px solid rgba(100, 100, 100, 0.5);
                border-radius: 4px;
                padding: 6px 12px;
                font-size: 16px;
            }
            entry:focus {
                border-color: #3584e4;
                box-shadow: 0 0 0 2px rgba(53, 132, 228, 0.5);
            }
            .completion-box {
                margin-left: 13px;
            }
            .completion-prefix {
                color: transparent;
                font-size: 16px;
                padding: 0;
                margin: 0;
            }
            .completion-suffix {
                color: rgba(160, 160, 160, 0.65);
                font-size: 16px;
                padding: 0;
                margin: 0;
            }
            """)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), 
                provider, 
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

            # Key event controller for Escape
            key_ctrl = Gtk.EventControllerKey.new()
            key_ctrl.connect("key-pressed", self.on_key_pressed)
            window.add_controller(key_ctrl)

            window.present()
            self.entry.grab_focus()

        def update_completion(self):
            typed_text = self.entry.get_text()
            pos = self.entry.get_position()
            if pos == len(typed_text) and typed_text:
                suffix = self.history_manager.find_completion(typed_text)
                if suffix:
                    self.lbl_prefix.set_text(typed_text)
                    self.lbl_suffix.set_text(suffix)
                    self.completion_box.set_visible(True)
                    self.current_completion = suffix
                    return
            self.lbl_prefix.set_text("")
            self.lbl_suffix.set_text("")
            self.completion_box.set_visible(False)
            self.current_completion = ""

        def on_entry_changed(self, entry):
            self.update_completion()

        def on_cursor_moved(self, entry, pspec):
            self.update_completion()

        def on_key_pressed(self, controller, keyval, keycode, state):
            key_name = Gdk.keyval_name(keyval)
            if key_name in ('Tab', 'ISO_Left_Tab') and self.current_completion:
                typed_text = self.entry.get_text()
                new_text = typed_text + self.current_completion
                self.entry.set_text(new_text)
                self.entry.set_position(len(new_text))
                self.update_completion()
                return True
            elif key_name == 'Escape':
                self.quit()
                return True
            return False

        def on_enter_pressed(self, entry):
            text = entry.get_text().strip()
            if text:
                try:
                    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    client.connect(SOCKET_PATH)
                    client.sendall(text.encode('utf-8'))
                    client.close()
                    self.history_manager.add(text)
                except Exception as e:
                    print(f"Failed to send to daemon: {e}")
                entry.set_text("")
                self.update_completion()

    app = TTSApp(args)
    app.run(None)

def main():
    args = parse_args()
    
    if args.list_voices:
        list_voices()
        sys.exit(0)
        
    if args.daemon:
        run_daemon(args.device, args.voice, args.virtual, args.feedback)
    else:
        run_gui(args)

if __name__ == "__main__":
    main()
