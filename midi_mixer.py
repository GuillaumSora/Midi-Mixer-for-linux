#!/usr/bin/env python3
"""X-Touch Mini mixer for PipeWire, WirePlumber and KDE Plasma.

Requirements:
  - Python package: mido
  - Commands: wpctl, pw-dump, pactl, kdotool, qdbus6
  - playerctl for Spotify media transport buttons

Important MIDI note:
  Mido channels are 0..15; X-Touch Editor channels are 1..16.
"""

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

# Global MIDI Channel from X-Touch Editor, using Mido numbering.
# Example: Global CH 11 in X-Touch Editor means LED_CHANNEL = 10 here.
LED_CHANNEL = 10

# MIDI channel used by the encoder knobs in Layer A, using Mido numbering.
# This MUST be the channel emitted by your Layer A knobs in X-Touch Editor.
# Run the script with --print-midi, turn a Layer A knob, then use the displayed
# `channel` value here. It is 10 if the editor shows MIDI CH 11.
MIDI_CHANNEL = 10

# Exact sink names from: pactl list short sinks
OUTPUTS = {
    "casque": "alsa_output.usb-Logitech_PRO_X_000000000000-00.analog-stereo",
    "enceintes": "alsa_output.pci-0000_12_00.6.analog-stereo",
    "dac": "alsa_output.usb-FIIO_JadeAudio_JA11_2020-02-20-0000-0000-0000-00.analog-stereo",
}

OUTPUT_ORDER = ["casque", "enceintes", "dac"]
OUTPUT_LABELS = {
    "casque": "Casque",
    "enceintes": "Enceintes",
    "dac": "DAC",
}

FEISHIN_MATCH = ["Feishin"]
SPOTIFY_MATCH = ["spotify", "spotify-launcher"]

# Button MIDI notes.
# La clé est maintenant : (canal_mido, note_midi)
BUTTONS = {
    # -------------------------------------------------------------------------
    # Layer A : Feishin + mixeur habituel
    # -------------------------------------------------------------------------
    (MIDI_CHANNEL, 8): {
        "label": "Feishin",
        "action": "mute_app",
        "match": FEISHIN_MATCH,
    },
    (MIDI_CHANNEL, 9): {
        "label": "Discord",
        "action": "mute_app",
        "match": ["discord"],
    },
    (MIDI_CHANNEL, 10): {
        "label": "Firefox",
        "action": "mute_app",
        "match": ["firefox"],
    },
    (MIDI_CHANNEL, 11): {
        "label": "Steam",
        "action": "mute_app",
        "match": ["steam"],
    },
    (MIDI_CHANNEL, 12): {
        "label": "VLC",
        "action": "mute_app",
        "match": ["vlc"],
    },
    (MIDI_CHANNEL, 14): {
        "label": "Fenêtre active",
        "action": "mute_active_window",
    },
    (MIDI_CHANNEL, 15): {
        "label": "Général",
        "action": "mute_default_sink",
    },
    (MIDI_CHANNEL, 16): {
        "label": "Switch casque / enceintes / DAC",
        "action": "toggle_output",
    },

    (MIDI_CHANNEL, 18): {
        "label": "Feishin : piste précédente",
        "action": "player_previous",
        "player": "Feishin",
    },
    (MIDI_CHANNEL, 19): {
        "label": "Feishin : piste suivante",
        "action": "player_next",
        "player": "Feishin",
    },
    (MIDI_CHANNEL, 22): {
        "label": "Feishin : lecture / pause",
        "action": "player_play_pause",
        "player": "Feishin",
    },

    # -------------------------------------------------------------------------
    # Layer B : Spotify
    # -------------------------------------------------------------------------
    (MIDI_CHANNEL, 32): {
        "label": "Spotify",
        "action": "mute_app",
        "match": SPOTIFY_MATCH,
    },

    (MIDI_CHANNEL, 42): {
        "label": "Spotify : piste précédente",
        "action": "player_previous",
        "player": "spotify",
    },
    (MIDI_CHANNEL, 43): {
        "label": "Spotify : piste suivante",
        "action": "player_next",
        "player": "spotify",
    },
    (MIDI_CHANNEL, 46): {
        "label": "Spotify : lecture / pause",
        "action": "player_play_pause",
        "player": "spotify",
    },
}

MAPPINGS = {
    # Layer A : Feishin
    (10, 1): {
        "label": "Feishin",
        "match": FEISHIN_MATCH,
        "default_volume": 0.30,
        "midi_channel": 10,
    },

    (10, 2): {
        "label": "Discord",
        "match": ["discord"],
        "default_volume": 0.50,
        "midi_channel": 10,
    },
    (10, 3): {
        "label": "Firefox",
        "match": ["firefox"],
        "default_volume": 0.50,
        "midi_channel": 10,
    },
    (10, 4): {
        "label": "Steam",
        "match": ["steam"],
        "default_volume": 0.50,
        "midi_channel": 10,
    },
    (10, 5): {
        "label": "VLC",
        "match": ["vlc"],
        "default_volume": 0.50,
        "midi_channel": 10,
    },

    # Layer B : Spotify
    (10, 11): {
        "label": "Spotify",
        "match": SPOTIFY_MATCH,
        "default_volume": 0.30,
        "midi_channel": 10,
    },

    (10, 7): {
        "label": "Fenêtre active",
        "active_window": True,
        "midi_channel": 10,
    },
    (10, 8): {
        "label": "Volume général",
        "default_sink": True,
        "default_volume": 0.50,
        "midi_channel": 10,
    },
    (10, 9): {
        "label": "Fenêtre active (fader)",
        "active_window": True,
        "midi_channel": 10,
    },
}

MAX_VOLUME = 1.0
MIN_DELTA = 1
APP_VOLUME_SYNC_INTERVAL = 0.5
NODE_VOLUME_EPSILON = 0.005

LED_OFF = 0
LED_ON = 1
LED_BLINK = 2

DEBUG_MATCHING = "--debug-matching" in sys.argv
DEBUG_EVERY_SYNC = "--debug-sync" in sys.argv
PRINT_MIDI = "--print-midi" in sys.argv

LAST_VOLUME_BY_CC = {}
LAST_APPLIED_BY_NODE = {}
STATE_LOCK = threading.Lock()
STOP_EVENT = threading.Event()


# =============================================================================
# COMMAND HELPERS
# =============================================================================

def run(*args):
    """Run a command and return stdout without printing command errors."""
    return subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    ).stdout.strip()


def command(*args):
    """Run a command silently and return its process status."""
    return subprocess.run(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode


def debug(message):
    if DEBUG_MATCHING:
        print(f"[debug] {message}")


# =============================================================================
# PIPEWIRE AND KDE WINDOW DETECTION
# =============================================================================

def get_nodes():
    """Return active PipeWire application audio-output streams."""
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
    """Return PID, class and title of the currently focused KDE window."""
    window_id = run("kdotool", "getactivewindow")
    if not window_id:
        return {"pid": "", "class": "", "name": ""}

    return {
        "pid": run("kdotool", "getwindowpid", window_id),
        "class": run("kdotool", "getwindowclassname", window_id).lower(),
        "name": run("kdotool", "getwindowname", window_id).lower(),
    }


def node_matches_terms(node, terms):
    candidates = [node["app"], node["binary"], node["node"], node["media_name"]]
    for term in terms:
        term = term.lower()
        for candidate in candidates:
            candidate = candidate.lower()
            if candidate and (term in candidate or candidate in term):
                return True
    return False


def debug_dump_nodes(label, nodes):
    debug(f"{label}: {len(nodes)} node(s)")
    for node in nodes:
        debug(
            f"  id={node['id']} app={node['app']!r} binary={node['binary']!r} "
            f"node={node['node']!r} media_name={node['media_name']!r} "
            f"pid={node['pid']!r}"
        )


def find_nodes(mapping):
    """Find PipeWire nodes corresponding to a configured mapping."""
    nodes = get_nodes()

    if mapping.get("active_window"):
        window = active_window_info()
        pid_matches = [node for node in nodes if node["pid"] == window["pid"]]
        if pid_matches:
            debug_dump_nodes("active-window PID matches", pid_matches)
            return pid_matches

        terms = {term for term in (window["class"], window["name"]) if term}
        found = [node for node in nodes if node_matches_terms(node, terms)]
        debug_dump_nodes("active-window fallback matches", found)
        return found

    terms = [term.lower() for term in mapping.get("match", [])]
    found = [node for node in nodes if node_matches_terms(node, terms)]
    if DEBUG_MATCHING and (found or DEBUG_EVERY_SYNC or "spotify" in terms):
        debug(f"mapping={mapping.get('label', '?')!r} terms={terms}")
        debug_dump_nodes("matching nodes", found)
    return found


def node_is_muted(node_id):
    return "[MUTED]" in run("wpctl", "get-volume", str(node_id))


def target_is_muted(target):
    return "[MUTED]" in run("wpctl", "get-volume", target)


def get_current_volume(node_id):
    parts = run("wpctl", "get-volume", str(node_id)).split()
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
# X-TOUCH MINI LED FEEDBACK
# =============================================================================

def set_button_led(midi_out, note, state):
    """Set a Standard-mode button LED using the configured global channel."""
    if not 0 <= note <= 15:
        return
    midi_out.send(
        mido.Message(
            "note_on",
            channel=LED_CHANNEL,
            note=note,
            velocity=state,
        )
    )


def set_knob_led(midi_out, control_cc, percent, midi_channel):
    """Update a Layer A encoder ring by sending its own CC on its own channel.

    This intentionally does NOT use CC 9..16 on the global channel. It sends
    the same CC that the configured physical encoder emits. For example, Layer
    A knob CC 1 receives CC 1 back on its Layer A MIDI channel.
    """
    if not 0 <= control_cc <= 127:
        return

    percent = max(0.0, min(1.0, percent))
    midi_value = round(percent * 127)

    midi_out.send(
        mido.Message(
            "control_change",
            channel=midi_channel,
            control=control_cc,
            value=midi_value,
        )
    )
    debug(
        f"knob LED: channel={midi_channel} cc={control_cc} "
        f"percent={percent:.3f} midi_value={midi_value}"
    )


def test_button_leds(midi_out):
    print("Test des LED de boutons...")
    for note in BUTTONS:
        if 0 <= note <= 15:
            set_button_led(midi_out, note, LED_ON)
            time.sleep(0.4)
            set_button_led(midi_out, note, LED_OFF)
    print("Test terminé.")

def test_knob_leds(midi_out):
    """Teste les anneaux LED de tous les knobs configurés."""
    print("Test des anneaux LED...")

    for (midi_channel, cc), mapping in MAPPINGS.items():
        if not 0 <= cc <= 127:
            continue

        print(f"Test knob CC {cc}, canal Mido {midi_channel}")

        for percent in (0.0, 0.5, 1.0):
            set_knob_led(
                midi_out,
                cc,
                percent,
                mapping["midi_channel"],
            )
            time.sleep(2)

    print("Test terminé.")


def button_label(button):
    if button["action"] == "mute_active_window":
        window = active_window_info()
        return window["name"] or window["class"] or "Fenêtre active"
    return button["label"]


def sync_button_leds(midi_out, active_channel=MIDI_CHANNEL):
    """Synchronise les LED de mute du layer MIDI actuellement utilisé."""
    ignored = {
        "toggle_output",
        "player_previous",
        "player_play_pause",
        "player_next",
    }

    for (button_channel, note), button in BUTTONS.items():
        if button_channel != active_channel:
            continue

        action = button["action"]

        if action in ignored:
            continue

        if action == "mute_default_sink":
            muted = target_is_muted("@DEFAULT_SINK@")

        elif action == "mute_default_source":
            muted = target_is_muted("@DEFAULT_SOURCE@")

        elif action == "mute_active_window":
            nodes = find_nodes({"active_window": True})
            muted = bool(nodes) and all(
                node_is_muted(node["id"]) for node in nodes
            )

        else:
            nodes = find_nodes(button)
            muted = bool(nodes) and all(
                node_is_muted(node["id"]) for node in nodes
            )

        set_button_led(midi_out, note, LED_ON if muted else LED_OFF)


# =============================================================================
# VOLUME
# =============================================================================

def set_volume(node_id, value):
    command("wpctl", "set-volume", "-l", str(MAX_VOLUME), str(node_id), f"{value:.3f}")


def apply_volume_to_mapping(mapping, percent, force=False):
    nodes = find_nodes(mapping)
    if not nodes:
        return []

    for node in nodes:
        node_id = node["id"]
        current = get_current_volume(node_id)
        with STATE_LOCK:
            previous = LAST_APPLIED_BY_NODE.get(node_id)

        should_apply = (
            force
            or current is None
            or abs(current - percent) > NODE_VOLUME_EPSILON
            or previous is None
            or abs(previous - percent) > NODE_VOLUME_EPSILON
        )

        if should_apply:
            debug(
                f"set-volume node={node_id} label={mapping['label']} "
                f"target={percent:.3f} current={current}"
            )
            set_volume(node_id, percent)
            with STATE_LOCK:
                LAST_APPLIED_BY_NODE[node_id] = percent

    return nodes


def apply_default_volumes(midi_out):
    """Applique les volumes par défaut et synchronise les anneaux LED."""
    for (midi_channel, cc), mapping in MAPPINGS.items():
        default = mapping.get("default_volume")

        if default is None:
            continue

        default = max(0.0, min(MAX_VOLUME, float(default)))

        set_knob_led(
            midi_out,
            cc,
            default,
            mapping["midi_channel"],
        )

        with STATE_LOCK:
            LAST_VOLUME_BY_CC[(midi_channel, cc)] = default

        if mapping.get("default_sink"):
            set_volume("@DEFAULT_SINK@", default)
            print(f"Volume général par défaut : {round(default * 100)} %")
            continue

        if mapping.get("active_window"):
            continue

        nodes = apply_volume_to_mapping(mapping, default, force=True)

        if nodes:
            print(
                f"Volume par défaut {mapping['label']} : "
                f"{round(default * 100)} % ({len(nodes)} flux)"
            )


def prune_stale_node_cache():
    current_ids = {node["id"] for node in get_nodes()}
    with STATE_LOCK:
        for node_id in list(LAST_APPLIED_BY_NODE):
            if node_id not in current_ids:
                LAST_APPLIED_BY_NODE.pop(node_id, None)


def sync_tracked_app_volumes():
    """Réapplique le dernier volume demandé à chaque application suivie."""
    prune_stale_node_cache()

    with STATE_LOCK:
        targets = {
            mapping_key: LAST_VOLUME_BY_CC.get(
                mapping_key,
                mapping.get("default_volume"),
            )
            for mapping_key, mapping in MAPPINGS.items()
            if not mapping.get("default_sink")
            and not mapping.get("active_window")
        }

    for mapping_key, target in targets.items():
        if target is not None:
            apply_volume_to_mapping(
                MAPPINGS[mapping_key],
                target,
            )


def sync_worker():
    while not STOP_EVENT.wait(APP_VOLUME_SYNC_INTERVAL):
        try:
            sync_tracked_app_volumes()
        except Exception as exc:
            debug(f"background sync error: {exc}")


def handle_volume(cc, value, midi_out, midi_channel):
    mapping_key = (midi_channel, cc)
    mapping = MAPPINGS.get(mapping_key)

    if not mapping:
        return

    if value >= 126:
        percent = MAX_VOLUME
    elif value <= 1:
        percent = 0.0
    else:
        percent = value / 127 * MAX_VOLUME

    set_knob_led(midi_out, cc, percent, midi_channel)

    with STATE_LOCK:
        LAST_VOLUME_BY_CC[mapping_key] = percent

    if mapping.get("default_sink"):
        set_volume("@DEFAULT_SINK@", percent)
        show_volume_osd(percent, "Volume général")
        print(f"Volume général : {round(percent * 100)} %")
        return

    nodes = apply_volume_to_mapping(mapping, percent, force=True)

    if not nodes:
        print(f"{mapping['label']} : aucun flux audio trouvé")
        return

    show_volume_osd(percent, mapping["label"])
    print(
        f"{mapping['label']} : "
        f"{round(percent * 100)} % ({len(nodes)} flux)"
    )


# =============================================================================
# OUTPUT SWITCHING
# =============================================================================

def current_default_sink():
    for line in run("pactl", "info").splitlines():
        if line.startswith("Default Sink:"):
            return line.split(":", 1)[1].strip()
    return ""


def sink_exists(sink_name):
    for line in run("pactl", "list", "short", "sinks").splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1] == sink_name:
            return True
    return False


def toggle_output():
    """Cycle through available headphones, speakers and DAC outputs."""
    current = current_default_sink()
    try:
        current_key = next(key for key, sink in OUTPUTS.items() if sink == current)
        start_index = OUTPUT_ORDER.index(current_key)
    except (StopIteration, ValueError):
        start_index = -1

    target_key = None
    for offset in range(1, len(OUTPUT_ORDER) + 1):
        candidate = OUTPUT_ORDER[(start_index + offset) % len(OUTPUT_ORDER)]
        if sink_exists(OUTPUTS[candidate]):
            target_key = candidate
            break
        print(f"Sortie indisponible ignorée : {OUTPUT_LABELS[candidate]}")

    if target_key is None:
        show_text_osd("Aucune sortie audio disponible", "audio-volume-muted")
        print("Aucune sortie audio configurée n'est disponible")
        return

    target = OUTPUTS[target_key]
    if command("pactl", "set-default-sink", target) != 0:
        show_text_osd("Impossible de changer de sortie", "audio-volume-muted")
        print(f"Impossible de sélectionner : {target}")
        return

    for line in run("pactl", "list", "short", "sink-inputs").splitlines():
        fields = line.split()
        if fields:
            command("pactl", "move-sink-input", fields[0], target)

    label = OUTPUT_LABELS[target_key]
    icon = "audio-headphones" if target_key == "casque" else "audio-volume-high"
    show_text_osd(f"Sortie active : {label}", icon)
    print(f"Sortie active : {label}")


# =============================================================================
# BUTTON ACTIONS
# =============================================================================

def player_transport(button):
    actions = {
        "player_previous": (
            "previous",
            button["label"],
            "media-skip-backward",
        ),
        "player_play_pause": (
            "play-pause",
            button["label"],
            "media-playback-pause",
        ),
        "player_next": (
            "next",
            button["label"],
            "media-skip-forward",
        ),
    }

    playerctl_action, text, icon = actions[button["action"]]

    if command(
        "playerctl",
        f"--player={button['player']}",
        playerctl_action,
    ) != 0:
        player = button["player"]
        show_text_osd(
            f"{player} indisponible",
            "audio-volume-muted",
        )
        print(f"{player} indisponible via playerctl")
        return

    show_text_osd(text, icon)
    print(text)


def handle_button(note, midi_out, midi_channel):
    button = BUTTONS.get((midi_channel, note))
    if not button:
        return

    action = button["action"]

    if action == "toggle_output":
        toggle_output()
        return

    if action in {"player_previous", "player_play_pause", "player_next"}:
        player_transport(button)
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
        nodes = (
            find_nodes({"active_window": True})
            if action == "mute_active_window"
            else find_nodes(button)
        )

        if not nodes:
            print(f"{button['label']} : aucun flux audio trouvé")
            return

        for node in nodes:
            command("wpctl", "set-mute", node["id"], "toggle")

        time.sleep(0.08)
        muted = all(node_is_muted(node["id"]) for node in nodes)
        label = button_label(button)

    set_button_led(midi_out, note, LED_ON if muted else LED_OFF)

    state = "Mute" if muted else "Unmute"
    icon = "audio-volume-muted" if muted else "audio-volume-high"
    show_text_osd(f"{label} : {state}", icon)
    print(f"{label} : {state}")


# =============================================================================
# MIDI MAIN LOOP
# =============================================================================

def matching_port(ports):
    return next(
        (port for port in ports if MIDI_PORT_MATCH.lower() in port.lower()),
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
    if PRINT_MIDI:
        print("Affichage brut des événements MIDI activé.")

    last_values = {}
    worker = threading.Thread(target=sync_worker, daemon=True)

    with mido.open_input(input_port) as midi, mido.open_output(output_port) as midi_out:
        if "--test-leds" in sys.argv:
            test_button_leds(midi_out)
            return
        if "--test-knob-leds" in sys.argv:
            test_knob_leds(midi_out)
            return

        sync_button_leds(midi_out)
        apply_default_volumes(midi_out)
        worker.start()

        print("Tourne un knob, bouge le fader ou appuie sur un bouton. Ctrl+C pour arrêter.")

        try:
            for message in midi:
                if PRINT_MIDI:
                    print(message)

                if message.type == "note_on" and message.velocity > 0:
                    handle_button(message.note, midi_out, message.channel)
                    time.sleep(0.12)
                    sync_button_leds(midi_out, message.channel)
                    continue

                if message.type != "control_change":
                    continue

                previous = last_values.get((message.channel, message.control))
                if previous is not None and abs(message.value - previous) < MIN_DELTA:
                    continue

                last_values[(message.channel, message.control)] = message.value
                handle_volume(
                    message.control,
                    message.value,
                    midi_out,
                    message.channel,
                )
        except KeyboardInterrupt:
            print("Arrêt demandé.")
        finally:
            STOP_EVENT.set()
            worker.join(timeout=1.5)


if __name__ == "__main__":
    main()
