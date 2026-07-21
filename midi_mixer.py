#!/usr/bin/env python3
import json
import subprocess
import sys
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

# Button NOTE numbers.
# Notes 0-15 have controllable LEDs on a standard X-Touch Mini.
BUTTONS = {
    8: {"label": "Spotify", "action": "mute_app", "match": ["spotify"]},
    9: {"label": "Discord", "action": "mute_app", "match": ["discord"]},
    10: {"label": "Firefox", "action": "mute_app", "match": ["firefox"]},
    11: {"label": "Steam", "action": "mute_app", "match": ["steam"]},
    12: {"label": "VLC", "action": "mute_app", "match": ["vlc"]},
    14: {"label": "Fenêtre active", "action": "mute_active_window"},
    15: {"label": "Général", "action": "mute_default_sink"},

    # If this button sends note 16, it can switch outputs,
    # but it does NOT have a controllable LED in Standard mode.
    16: {"label": "Switch casque / enceintes", "action": "toggle_output"},
}

# Knobs / fader CC numbers.
MAPPINGS = {
    1: {"label": "Spotify", "match": ["spotify"]},
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

LED_OFF = 0
LED_ON = 1
LED_BLINK = 2


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


def find_nodes(mapping):
    """Find PipeWire streams corresponding to a configured mapping."""
    nodes = get_nodes()

    if mapping.get("active_window"):
        window = active_window_info()

        # Best case: the PipeWire stream has the same PID as the focused window.
        pid_matches = [node for node in nodes if node["pid"] == window["pid"]]

        if pid_matches:
            return pid_matches

        # Fallback for Spotify, browsers, Proton games, etc.
        search_terms = {
            value.lower()
            for value in (window["class"], window["name"])
            if value
        }

        return [
            node
            for node in nodes
            if any(
                term in candidate.lower() or candidate.lower() in term
                for term in search_terms
                for candidate in (node["app"], node["binary"], node["node"])
                if candidate
            )
        ]

    matches = [value.lower() for value in mapping.get("match", [])]

    return [
        node
        for node in nodes
        if any(
            match in candidate.lower()
            for match in matches
            for candidate in (node["app"], node["binary"], node["node"])
            if candidate
        )
    ]

def node_is_muted(node_id):
    """
    Retourne True si le flux PipeWire est actuellement muted.
    Exemple de sortie : 'Volume: 0.65 [MUTED]'
    """
    return "[MUTED]" in run("wpctl", "get-volume", str(node_id))


def target_is_muted(target):
    """Read mute state for @DEFAULT_SINK@ or @DEFAULT_SOURCE@."""
    return "[MUTED]" in run("wpctl", "get-volume", target)


# =============================================================================
# KDE OSD
# =============================================================================

def show_text_osd(text, icon="audio-volume-high"):
    """Show KDE Plasma text OSD."""
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


def handle_volume(cc, value):
    """Handle knobs and fader."""
    mapping = MAPPINGS.get(cc)

    if not mapping:
        return

    # Snap near MIDI endpoints to exact values.
    if value >= 126:
        percent = MAX_VOLUME
    elif value <= 1:
        percent = 0.0
    else:
        percent = value / 127 * MAX_VOLUME

    if mapping.get("default_sink"):
        set_volume("@DEFAULT_SINK@", percent)

        show_volume_osd(percent, "Volume général")
        print(f"Volume général : {round(percent * 100)} %")
        return

    nodes = find_nodes(mapping)

    if not nodes:
        print(f"{mapping['label']} : aucun flux audio trouvé")
        return

    for node in nodes:
        set_volume(node["id"], percent)

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
    """Return the PipeWire-Pulse default sink name."""
    for line in run("pactl", "info").splitlines():
        if line.startswith("Default Sink:"):
            return line.split(":", 1)[1].strip()

    return ""


def speakers_are_selected():
    return current_default_sink() == OUTPUTS["enceintes"]


def toggle_output():
    """Switch between headphones and speakers; move active audio streams."""
    current = current_default_sink()

    if current == OUTPUTS["casque"]:
        target = OUTPUTS["enceintes"]
        label = "Enceintes"
    else:
        target = OUTPUTS["casque"]
        label = "Casque"

    command("pactl", "set-default-sink", target)

    # Move streams already playing.
    for line in run("pactl", "list", "short", "sink-inputs").splitlines():
        parts = line.split()

        if parts:
            command("pactl", "move-sink-input", parts[0], target)

    show_text_osd(f"Sortie active : {label}", "audio-headphones")
    print(f"Sortie active : {label}")

    # Do not re-read pactl here: it may update asynchronously.
    return target == OUTPUTS["enceintes"]


# =============================================================================
# X-TOUCH MINI BUTTON LEDS
# =============================================================================

def set_button_led(midi_out, note, state):
    """
    Set an X-Touch Mini button LED.

    state:
      LED_OFF   = 0
      LED_ON    = 1
      LED_BLINK = 2

    Notes 0-15 only are supported by the X-Touch Mini LED protocol.
    """
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
    """Return a dynamic label for the active-window button."""
    if button["action"] == "mute_active_window":
        window = active_window_info()
        return window["name"] or window["class"] or "Fenêtre active"

    return button["label"]


def update_button_led_for_mute(midi_out, note, muted):
    """LED on means the associated target is muted."""
    set_button_led(midi_out, note, LED_ON if muted else LED_OFF)


def sync_button_leds(midi_out):
    """
    Synchronise les LED de mute au démarrage.
    LED allumée = cible mutée.
    LED éteinte = cible non mutée ou application sans flux audio.
    """
    for note, button in BUTTONS.items():
        action = button["action"]

        # Le switch audio est note 16 : aucune LED gérée.
        if action == "toggle_output":
            continue

        if action == "mute_default_sink":
            muted = node_is_muted("@DEFAULT_SINK@")

        elif action == "mute_default_source":
            muted = node_is_muted("@DEFAULT_SOURCE@")

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


def test_button_leds(midi_out):
    """Briefly turn each configured LED on, then off."""
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

    # Master mute.
    if action == "mute_default_sink":
        command("wpctl", "set-mute", "@DEFAULT_SINK@", "toggle")
        time.sleep(0.08)

        muted = target_is_muted("@DEFAULT_SINK@")
        label = button["label"]

    # Microphone mute.
    elif action == "mute_default_source":
        command("wpctl", "set-mute", "@DEFAULT_SOURCE@", "toggle")
        time.sleep(0.08)

        muted = target_is_muted("@DEFAULT_SOURCE@")
        label = button["label"]

    # Application mute or active-window mute.
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

        # Give PipeWire time to publish its new mute state.
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

    last_values = {}

    with mido.open_input(input_port) as midi, mido.open_output(output_port) as midi_out:
        if "--test-leds" in sys.argv:
            test_button_leds(midi_out)
            return

        # At launch, restore LED state from actual PipeWire state.
        sync_button_leds(midi_out)

        print("Tourne un knob, bouge le fader ou appuie sur un bouton. Ctrl+C pour arrêter.")

        for message in midi:
            # Uncomment this line temporarily if you need to identify MIDI values.
            # print(message)

            if message.type == "note_on" and message.velocity > 0:
                handle_button(message.note, midi_out)

                # Le X-Touch traite ensuite le relâchement et peut éteindre
                # sa propre LED. On renvoie donc l'état réel après ce relâchement.
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


if __name__ == "__main__":
    main()