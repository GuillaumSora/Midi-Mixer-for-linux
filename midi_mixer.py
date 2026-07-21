#!/usr/bin/env python3
import json
import subprocess
import sys
import threading
import time

import mido


# =============================================================================
# CONFIGURATION
# =============================================================================

MIDI_PORT_MATCH = "X-TOUCH MINI"

# Global MIDI Channel configured in X-Touch Editor.
# IMPORTANT: Mido uses 0-15, while X-Touch Editor displays channels 1-16.
# Example: "Global CH 1" in X-Touch Editor = 0 here.
LED_CHANNEL = 10

# Output names from: pactl list short sinks
OUTPUTS = {
    "casque": "alsa_output.usb-Logitech_PRO_X_000000000000-00.analog-stereo",
    "enceintes": "alsa_output.pci-0000_12_00.6.analog-stereo",
}

SPOTIFY_MATCH = ["spotify", "spotify-launcher"]

# Button NOTE numbers.
# Notes 0-15 have controllable LEDs on a standard X-Touch Mini.
BUTTONS = {
    8: {"label": "Spotify", "action": "mute_app", "match": SPOTIFY_MATCH},
    9: {"label": "Discord", "action": "mute_app", "match": ["discord"]},
    10: {"label": "Firefox", "action": "mute_app", "match": ["firefox"]},
    11: {"label": "Steam", "action": "mute_app", "match": ["steam"]},
    12: {"label": "VLC", "action": "mute_app", "match": ["vlc"]},
    14: {"label": "Fenêtre active", "action": "mute_active_window"},
    15: {"label": "Général", "action": "mute_default_sink"},
    16: {"label": "Switch casque / enceintes", "action": "toggle_output"},
    18: {"label": "Spotify previous", "action": "spotify_previous"},
    19: {"label": "Spotify next", "action": "spotify_next"},
    22: {"label": "Spotify play/pause", "action": "spotify_play_pause"},
}

# Knobs / fader CC numbers.
MAPPINGS = {
    1: {"label": "Spotify", "match": SPOTIFY_MATCH},
    2: {"label": "Discord", "match": ["discord"]},
    3: {"label": "Firefox", "match": ["firefox"]},
    4: {"label": "Steam / jeu", "match": ["steam"]},
    5: {"label": "VLC", "match": ["vlc"]},
    7: {"label": "Fenêtre active", "active_window": True},
    8: {"label": "Volume général", "default_sink": True},
    9: {"label": "Fenêtre active (fader)", "active_window": True},
}

MAX_VOLUME = 1.0
MIN_DELTA = 1
APP_VOLUME_SYNC_INTERVAL = 0.5
NODE_VOLUME_EPSILON = 0.005
DEBUG_MATCHING = "--debug-matching" in sys.argv
DEBUG_EVERY_SYNC = "--debug-sync" in sys.argv

LED_OFF = 0
LED_ON = 1
LED_BLINK = 2

LAST_VOLUME_BY_CC = {}
LAST_APPLIED_BY_NODE = {}
STATE_LOCK = threading.Lock()
STOP_EVENT = threading.Event()


# =============================================================================
# GENERIC HELPERS
# =============================================================================

def run(*args):
    """Run a command and return its stdout, without printing command errors."""
    return subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    ).stdout.strip()



def command(*args):
    """Run a command silently."""
    subprocess.run(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )



def debug(message):
    if DEBUG_MATCHING:
        print(f"[debug] {message}")


# =============================================================================
# PIPEWIRE / WINDOW DETECTION
# =============================================================================

def get_nodes():
    """Return currently active PipeWire application audio streams."""
    try:
        data = json.loads(run("pw-dump"))
    except json.JSONDecodeError:
        return []

    nodes = []

    for item in data:
        if item.get("type") != "PipeWire:Interface:Node":
            continue

        props = item.get("info", {}).get("props", {})

        if props.get("media.class") != "Stream/Output/Audio":
            continue

        nodes.append(
            {
                "id": str(item["id"]),
                "app": props.get("application.name", ""),
                "binary": props.get("application.process.binary", ""),
                "pid": str(props.get("application.process.id", "")),
                "node": props.get("node.name", ""),
                "media_name": props.get("media.name", ""),
            }
        )

    return nodes



def active_window_info():
    """Get PID, class and title of the focused KDE window."""
    window_id = run("kdotool", "getactivewindow")

    if not window_id:
        return {"pid": "", "class": "", "name": ""}

    return {
        "pid": run("kdotool", "getwindowpid", window_id),
        "class": run("kdotool", "getwindowclassname", window_id).lower(),
        "name": run("kdotool", "getwindowname", window_id).lower(),
    }



def debug_dump_nodes(label, nodes):
    debug(f"{label}: {len(nodes)} node(s)")
    for node in nodes:
        debug(
            "  "
            f"id={node['id']} "
            f"app={node['app']!r} "
            f"binary={node['binary']!r} "
            f"node={node['node']!r} "
            f"media_name={node['media_name']!r} "
            f"pid={node['pid']!r}"
        )



def node_matches_terms(node, terms):
    candidates = [
        node["app"],
        node["binary"],
        node["node"],
        node["media_name"],
    ]

    for term in terms:
        term = term.lower()
        for candidate in candidates:
            candidate_lower = candidate.lower()
            if candidate_lower and (term in candidate_lower or candidate_lower in term):
                return True

    return False



def find_nodes(mapping):
    """Find PipeWire streams corresponding to a configured mapping."""
    nodes = get_nodes()

    if mapping.get("active_window"):
        window = active_window_info()
        debug(f"active window pid={window['pid']!r} class={window['class']!r} name={window['name']!r}")

        pid_matches = [node for node in nodes if node["pid"] == window["pid"]]

        if pid_matches:
            debug_dump_nodes("active_window pid matches", pid_matches)
            return pid_matches

        search_terms = {
            value.lower()
            for value in (window["class"], window["name"])
            if value
        }

        matches = [node for node in nodes if node_matches_terms(node, search_terms)]
        debug_dump_nodes("active_window fallback matches", matches)
        return matches

    matches = [value.lower() for value in mapping.get("match", [])]
    found = [node for node in nodes if node_matches_terms(node, matches)]

    if DEBUG_MATCHING and (found or any(term in SPOTIFY_MATCH for term in matches) or DEBUG_EVERY_SYNC):
        debug(f"mapping={mapping.get('label', '?')!r} terms={matches}")
        debug_dump_nodes("matching nodes", found)

        if not found:
            spotifyish = [
                node
                for node in nodes
                if node_matches_terms(node, ["spotify", "spotify-launcher"])
            ]
            if spotifyish:
                debug_dump_nodes("spotify-like nodes seen but not matched", spotifyish)

    return found



def node_is_muted(node_id):
    return "[MUTED]" in run("wpctl", "get-volume", str(node_id))



def target_is_muted(target):
    return "[MUTED]" in run("wpctl", "get-volume", target)



def get_current_volume(node_id):
    output = run("wpctl", "get-volume", str(node_id))
    parts = output.split()

    if len(parts) < 2:
        return None

    try:
        return float(parts[1])
    except ValueError:
        return None


# =============================================================================
# KDE OSD
# =============================================================================

def show_text_osd(text, icon="audio-volume-high"):
    command(
        "qdbus6",
        "org.kde.plasmashell",
        "/org/kde/osdService",
        "org.kde.osdService.showText",
        icon,
        text,
    )



def show_volume_osd(percent, label):
    volume = round(percent * 100)
    icon = "audio-volume-muted" if volume == 0 else "audio-volume-high"
    show_text_osd(f"{label} : {volume} %", icon)


# =============================================================================
# VOLUME
# =============================================================================

def set_volume(node_id, value):
    command(
        "wpctl",
        "set-volume",
        "-l",
        str(MAX_VOLUME),
        node_id,
        f"{value:.3f}",
    )



def apply_volume_to_mapping(mapping, percent, force=False):
    """Apply the requested volume to all currently matching nodes."""
    nodes = find_nodes(mapping)

    if not nodes:
        return []

    applied_nodes = []

    for node in nodes:
        node_id = node["id"]
        current = get_current_volume(node_id)

        with STATE_LOCK:
            previous = LAST_APPLIED_BY_NODE.get(node_id)

        should_apply = force

        if current is None:
            should_apply = True
        elif abs(current - percent) > NODE_VOLUME_EPSILON:
            should_apply = True
        elif previous is None:
            should_apply = True
        elif abs(previous - percent) > NODE_VOLUME_EPSILON:
            should_apply = True

        if should_apply:
            debug(
                f"set-volume node={node_id} label={mapping.get('label')} "
                f"target={percent:.3f} current={current if current is not None else 'unknown'}"
            )
            set_volume(node_id, percent)
            with STATE_LOCK:
                LAST_APPLIED_BY_NODE[node_id] = percent
        elif DEBUG_EVERY_SYNC:
            debug(
                f"skip-volume node={node_id} label={mapping.get('label')} "
                f"target={percent:.3f} current={current:.3f}"
            )

        applied_nodes.append(node)

    return applied_nodes



def prune_stale_node_cache():
    current_ids = {node["id"] for node in get_nodes()}

    with STATE_LOCK:
        stale_ids = [node_id for node_id in LAST_APPLIED_BY_NODE if node_id not in current_ids]
        for node_id in stale_ids:
            debug(f"drop stale node cache id={node_id}")
            LAST_APPLIED_BY_NODE.pop(node_id, None)



def sync_tracked_app_volumes():
    prune_stale_node_cache()

    with STATE_LOCK:
        tracked = {
            cc: LAST_VOLUME_BY_CC[cc]
            for cc, mapping in MAPPINGS.items()
            if cc in LAST_VOLUME_BY_CC
            and not mapping.get("default_sink")
            and not mapping.get("active_window")
        }

    for cc, percent in tracked.items():
        mapping = MAPPINGS[cc]
        debug(f"background sync cc={cc} label={mapping['label']} target={percent:.3f}")
        apply_volume_to_mapping(mapping, percent)



def sync_worker():
    debug("background sync thread started")
    while not STOP_EVENT.wait(APP_VOLUME_SYNC_INTERVAL):
        try:
            sync_tracked_app_volumes()
        except Exception as exc:
            debug(f"background sync error: {exc}")
    debug("background sync thread stopped")



def handle_volume(cc, value):
    mapping = MAPPINGS.get(cc)

    if not mapping:
        return

    if value >= 126:
        percent = MAX_VOLUME
    elif value <= 1:
        percent = 0.0
    else:
        percent = value / 127 * MAX_VOLUME

    with STATE_LOCK:
        LAST_VOLUME_BY_CC[cc] = percent

    debug(f"midi volume cc={cc} label={mapping['label']} raw={value} percent={percent:.3f}")

    if mapping.get("default_sink"):
        set_volume("@DEFAULT_SINK@", percent)
        show_volume_osd(percent, "Volume général")
        print(f"Volume général : {round(percent * 100)} %")
        return

    nodes = apply_volume_to_mapping(mapping, percent, force=True)

    if not nodes:
        print(f"{mapping['label']} : aucun flux audio trouvé")
        return

    label = mapping["label"]

    if mapping.get("active_window"):
        window = active_window_info()
        label = window["name"] or window["class"] or "Fenêtre active"

    show_volume_osd(percent, label)
    print(f"{label} : {round(percent * 100)} % ({len(nodes)} flux)")


# =============================================================================
# AUDIO OUTPUT SWITCHING
# =============================================================================

def current_default_sink():
    for line in run("pactl", "info").splitlines():
        if line.startswith("Default Sink:"):
            return line.split(":", 1)[1].strip()
    return ""



def speakers_are_selected():
    return current_default_sink() == OUTPUTS["enceintes"]



def toggle_output():
    current = current_default_sink()

    if current == OUTPUTS["casque"]:
        target = OUTPUTS["enceintes"]
        label = "Enceintes"
    else:
        target = OUTPUTS["casque"]
        label = "Casque"

    command("pactl", "set-default-sink", target)

    for line in run("pactl", "list", "short", "sink-inputs").splitlines():
        parts = line.split()
        if parts:
            command("pactl", "move-sink-input", parts[0], target)

    show_text_osd(f"Sortie active : {label}", "audio-headphones")
    print(f"Sortie active : {label}")
    return target == OUTPUTS["enceintes"]


# =============================================================================
# X-TOUCH MINI BUTTON LEDS
# =============================================================================

def set_button_led(midi_out, note, state):
    if not 0 <= note <= 15:
        print(
            f"LED ignorée : note {note} hors plage. "
            "Les LED de boutons X-Touch Mini utilisent uniquement les notes 0 à 15."
        )
        return

    midi_out.send(
        mido.Message(
            "note_on",
            channel=LED_CHANNEL,
            note=note,
            velocity=state,
        )
    )



def button_label(button):
    if button["action"] == "mute_active_window":
        window = active_window_info()
        return window["name"] or window["class"] or "Fenêtre active"
    return button["label"]



def update_button_led_for_mute(midi_out, note, muted):
    set_button_led(midi_out, note, LED_ON if muted else LED_OFF)



def sync_button_leds(midi_out):
    for note, button in BUTTONS.items():
        action = button["action"]

        if action in {
            "toggle_output",
            "spotify_previous",
            "spotify_play_pause",
            "spotify_next",
        }:
            continue

        if action == "mute_default_sink":
            muted = node_is_muted("@DEFAULT_SINK@")
        elif action == "mute_default_source":
            muted = node_is_muted("@DEFAULT_SOURCE@")
        elif action == "mute_active_window":
            nodes = find_nodes({"active_window": True})
            muted = bool(nodes) and all(node_is_muted(node["id"]) for node in nodes)
        else:
            nodes = find_nodes(button)
            muted = bool(nodes) and all(node_is_muted(node["id"]) for node in nodes)

        set_button_led(midi_out, note, LED_ON if muted else LED_OFF)



def test_button_leds(midi_out):
    print("Test des LED...")

    for note in BUTTONS:
        if 0 <= note <= 15:
            print(f"Test LED note {note}")
            set_button_led(midi_out, note, LED_ON)
            time.sleep(0.5)
            set_button_led(midi_out, note, LED_OFF)

    print("Test terminé.")


# =============================================================================
# BUTTON ACTIONS
# =============================================================================

def handle_button(note, midi_out):
    button = BUTTONS.get(note)

    if not button:
        return

    action = button["action"]

    if action == "toggle_output":
        toggle_output()
        return
    
    if action == "spotify_previous":
        command("playerctl", "--player=spotify", "previous")
        show_text_osd("Spotify : piste précédente", "media-skip-backward")
        print("Spotify : piste précédente")
        return

    if action == "spotify_play_pause":
        command("playerctl", "--player=spotify", "play-pause")
        show_text_osd("Spotify : lecture / pause", "media-playback-pause")
        print("Spotify : lecture / pause")
        return

    if action == "spotify_next":
        command("playerctl", "--player=spotify", "next")
        show_text_osd("Spotify : piste suivante", "media-skip-forward")
        print("Spotify : piste suivante")
        return

    if action == "mute_default_sink":
        command("wpctl", "set-mute", "@DEFAULT_SINK@", "toggle")
        time.sleep(0.08)
        muted = target_is_muted("@DEFAULT_SINK@")
        label = button["label"]

    elif action == "mute_default_source":
        command("wpctl", "set-mute", "@DEFAULT_SOURCE@", "toggle")
        time.sleep(0.08)
        muted = target_is_muted("@DEFAULT_SOURCE@")
        label = button["label"]

    else:
        if action == "mute_active_window":
            nodes = find_nodes({"active_window": True})
        else:
            nodes = find_nodes(button)

        if not nodes:
            print(f"{button['label']} : aucun flux audio trouvé")
            return

        for node in nodes:
            command("wpctl", "set-mute", node["id"], "toggle")

        time.sleep(0.08)
        muted = all(node_is_muted(node["id"]) for node in nodes)
        label = button_label(button)

    update_button_led_for_mute(midi_out, note, muted)

    state = "Mute" if muted else "Unmute"
    icon = "audio-volume-muted" if muted else "audio-volume-high"
    show_text_osd(f"{label} : {state}", icon)
    print(f"{label} : {state}")


# =============================================================================
# MIDI
# =============================================================================

def matching_port(ports):
    return next(
        (
            port
            for port in ports
            if MIDI_PORT_MATCH.lower() in port.lower()
        ),
        None,
    )



def main():
    input_ports = mido.get_input_names()
    output_ports = mido.get_output_names()

    if "--list-midi" in sys.argv:
        print("Ports MIDI en entrée :")
        print("\n".join(input_ports) or "(aucun)")

        print("\nPorts MIDI en sortie :")
        print("\n".join(output_ports) or "(aucun)")
        return

    input_port = matching_port(input_ports)
    output_port = matching_port(output_ports)

    if not input_port or not output_port:
        print("Port MIDI d'entrée ou de sortie introuvable.")

        print("\nEntrées disponibles :")
        print("\n".join(input_ports) or "(aucune)")

        print("\nSorties disponibles :")
        print("\n".join(output_ports) or "(aucune)")

        sys.exit(1)

    print(f"Écoute MIDI : {input_port}")
    print(f"Retour MIDI / LED : {output_port}")

    if DEBUG_MATCHING:
        print("[debug] Debug matching activé")
    if DEBUG_EVERY_SYNC:
        print("[debug] Debug sync détaillé activé")

    last_values = {}
    worker = threading.Thread(target=sync_worker, daemon=True)

    with mido.open_input(input_port) as midi, mido.open_output(output_port) as midi_out:
        if "--test-leds" in sys.argv:
            test_button_leds(midi_out)
            return

        sync_button_leds(midi_out)
        worker.start()

        print("Tourne un knob, bouge le fader ou appuie sur un bouton. Ctrl+C pour arrêter.")

        try:
            for message in midi:
                if message.type == "note_on" and message.velocity > 0:
                    handle_button(message.note, midi_out)
                    time.sleep(0.12)
                    sync_button_leds(midi_out)
                    continue

                if message.type != "control_change":
                    continue

                previous = last_values.get(message.control)

                if previous is not None and abs(message.value - previous) < MIN_DELTA:
                    continue

                last_values[message.control] = message.value
                handle_volume(message.control, message.value)
        finally:
            STOP_EVENT.set()
            worker.join(timeout=1.5)


if __name__ == "__main__":
    main()
