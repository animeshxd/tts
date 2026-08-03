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
        normalized = advance_normalize_text(text, lookup)
        try:
            import time
            start_time = time.time()
            first_chunk = True
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

def run_daemon(device, voice, virtual=False):
    module_id = None
    if virtual:
        try:
            res = subprocess.run(
                ["pactl", "load-module", "module-null-sink", "sink_name=TTS_Virtual_Sink", "sink_properties=device.description=TTS_Virtual_Sink"],
                capture_output=True,
                text=True,
                check=True
            )
            module_id = res.stdout.strip()
            print(f"Loaded virtual null sink 'TTS_Virtual_Sink' (module ID: {module_id})")
        except Exception as e:
            print(f"Error loading virtual null sink: {e}")

    if os.path.exists(SOCKET_PATH):
        try:
            # Check if it's a dead socket
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(SOCKET_PATH)
            client.close()
            print("Daemon is already running.")
            if module_id:
                subprocess.run(["pactl", "unload-module", module_id], check=False)
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
        if module_id:
            try:
                subprocess.run(["pactl", "unload-module", module_id], check=False)
                print(f"Unloaded virtual null sink (module ID: {module_id})")
            except Exception as e:
                print(f"Error unloading virtual sink: {e}")

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
    from gi.repository import Gdk, Gtk, Gtk4LayerShell

    class TTSApp(Gtk.Application):
        def __init__(self, args):
            super().__init__(application_id='com.example.TTSOverlay', flags=gi.repository.Gio.ApplicationFlags.FLAGS_NONE)
            self.args = args

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
            
            self.entry = Gtk.Entry()
            self.entry.set_placeholder_text("Type to speak... (Esc to quit)")
            self.entry.set_width_chars(40)
            self.entry.connect("activate", self.on_enter_pressed)
            
            box.append(self.entry)
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

        def on_key_pressed(self, controller, keyval, keycode, state):
            if Gdk.keyval_name(keyval) == 'Escape':
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
                except Exception as e:
                    print(f"Failed to send to daemon: {e}")
                entry.set_text("")

    app = TTSApp(args)
    app.run(None)

def main():
    args = parse_args()
    
    if args.list_voices:
        list_voices()
        sys.exit(0)
        
    if args.daemon:
        run_daemon(args.device, args.voice, args.virtual)
    else:
        run_gui(args)

if __name__ == "__main__":
    main()
