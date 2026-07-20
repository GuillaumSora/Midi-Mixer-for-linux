#!/usr/bin/env python3
import json
import subprocess
import sys
import time

import mido

MIDI_PORT_MATCH = "X-TOUCH MINI"

OUTPUTS = {
    "casque": "alsa_output.usb-Logitech_PRO_X_000000000000-00.analog-stereo",
    "enceintes": "alsa_output.pci-0000_12_00.6.analog-stereo"
}

BUTTONS = {
    8: {"label": "Spotify", "action": "mute_app", "match": ["spotify"]},
    9: {"label": "Discord", "action": "mute_app", "match": ["discord"]},
    10: {"label": "Firefox", "action": "mute_app", "match": ["firefox"]},
    11: {"label": "Steam", "action": "mute_app", "match": ["steam"]},
    12: {"label": "VLC", "action": "mute_app", "match": ["vlc"]},
    14: {"label": "Fenêtre active", "action": "mute_active_window"},
    15: {"label": "Général", "action": "mute_default_sink"},
    16: {"label": "Switch casque / enceintes", "action": "toggle_output"}
}

# Adapte les CC après le test `python midi_mixer.py --list-midi`.
# Les noms ci-dessous doivent correspondre à `application.name` ou
# `application.process.binary` vus dans ta sortie pw-dump.
MAPPINGS = {
    1: {"label": "Spotify", "match": ["spotify"]},
    2: {"label": "Discord", "match": ["discord", "Discord"]},
    3: {"label": "Firefox", "match": ["firefox", "Firefox"]},
    4: {"label": "Steam / jeu", "match": ["steam", "Steam"]},
    5: {"label": "VLC", "match": ["vlc", "VLC"]},
    7: {"label": "Fenêtre active", "active_window": True},
    8: {"label": "Volume général", "default_sink": True},
    9: {"label": "Fenêtre active (fader)", "active_window": True},
}

MAX_VOLUME = 1.0       # 1.0 = 100 %, mets 1.5 pour autoriser 150 %
MIN_DELTA = 1          # Ignore les micro-variations de certains contrôleurs


def run(*args):
    return subprocess.run(
        args, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    ).stdout.strip()


def get_nodes():
    raw = run("pw-dump")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    nodes = []
    for item in data:
        if item.get("type") != "PipeWire:Interface:Node":
            continue
        props = item.get("info", {}).get("props", {})
        if props.get("media.class") != "Stream/Output/Audio":
            continue
        nodes.append({
            "id": str(item["id"]),
            "app": props.get("application.name", ""),
            "binary": props.get("application.process.binary", ""),
            "pid": str(props.get("application.process.id", "")),
            "node": props.get("node.name", ""),
        })
    return nodes


def active_window_info():
    window_id = run("kdotool", "getactivewindow")
    if not window_id:
        return {"pid": "", "class": "", "name": ""}

    return {
        "pid": run("kdotool", "getwindowpid", window_id),
        "class": run("kdotool", "getwindowclassname", window_id).lower(),
        "name": run("kdotool", "getwindowname", window_id).lower(),
    }


def find_nodes(mapping):
    nodes = get_nodes()

    if mapping.get("active_window"):
        window = active_window_info()

        # Cas idéal : PID exact de la fenêtre et du flux PipeWire.
        by_pid = [n for n in nodes if n["pid"] == window["pid"]]
        if by_pid:
            return by_pid

        # Repli nécessaire pour les applis dont le flux audio est produit
        # par un processus enfant (Spotify, navigateurs, jeux/Proton...).
        terms = {
            value.lower()
            for value in (window["class"], window["name"])
            if value
        }

        return [
            n for n in nodes
            if any(
                term in value.lower() or value.lower() in term
                for term in terms
                for value in (n["app"], n["binary"], n["node"])
                if value
            )
        ]

    matches = [m.lower() for m in mapping["match"]]
    return [
        n for n in nodes
        if any(
            match in value.lower()
            for match in matches
            for value in (n["app"], n["binary"], n["node"])
            if value
        )
    ]

def show_volume_osd(percent, label="Volume"):
    volume = round(percent * 100)

    show_text_osd(
        f"{label} : {volume} %",
        "audio-volume-high" if volume > 0 else "audio-volume-muted",
    )

def set_volume(node_id, value):
    subprocess.run(
        ["wpctl", "set-volume", "-l", str(MAX_VOLUME), node_id, f"{value:.3f}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def handle(cc, value):
    # Force les positions proches des butées à être des valeurs exactes.
    if value >= 126:
        percent = MAX_VOLUME
    elif value <= 1:
        percent = 0.0
    else:
        percent = value / 127 * MAX_VOLUME

    mapping = MAPPINGS.get(cc)
    if not mapping:
        return

    if mapping.get("default_sink"):
        set_volume("@DEFAULT_SINK@", percent)
        show_volume_osd(percent, "Volume général")
        print(f"{mapping['label']}: {round(percent * 100)} %")
        return

    nodes = find_nodes(mapping)
    if not nodes:
        print(f"{mapping['label']}: aucun flux audio trouvé")
        return

    for node in nodes:
        set_volume(node["id"], percent)

    label = mapping["label"]

    if mapping.get("active_window"):
        window = active_window_info()
        label = window["name"] or window["class"] or "Fenêtre active"

    show_volume_osd(percent, label)
    print(f"{label}: {round(percent * 100)} % ({len(nodes)} flux)")

def current_default_sink():
    info = run("pactl", "info")
    for line in info.splitlines():
        if line.startswith("Default Sink:"):
            return line.split(":", 1)[1].strip()
    return ""


def toggle_output():
    current = current_default_sink()

    if current == OUTPUTS["casque"]:
        target = OUTPUTS["enceintes"]
        label = "Enceintes"
    else:
        target = OUTPUTS["casque"]
        label = "Casque"

    # Sortie par défaut pour les nouveaux flux.
    subprocess.run(
        ["pactl", "set-default-sink", target],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )

    # Déplace immédiatement Spotify, Firefox, Discord, VLC, jeux, etc.
    # qui jouent déjà du son.
    streams = run("pactl", "list", "short", "sink-inputs")
    for line in streams.splitlines():
        stream_id = line.split()[0]
        subprocess.run(
            ["pactl", "move-sink-input", stream_id, target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    print(f"Sortie active : {label}")

def show_text_osd(text, icon="audio-volume-muted"):
    subprocess.run(
        [
            "qdbus6",
            "org.kde.plasmashell",
            "/org/kde/osdService",
            "org.kde.osdService.showText",
            icon,
            text,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )

def node_is_muted(node_id):
    raw = run("pw-dump")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False

    for item in data:
        if str(item.get("id")) != str(node_id):
            continue

        props_list = item.get("info", {}).get("params", {}).get("Props", [])
        for props in props_list:
            if "mute" in props:
                return bool(props["mute"])

    return False

def handle_button(note):
    button = BUTTONS.get(note)
    if not button:
        return

    action = button["action"]

    if action == "mute_default_sink":
        subprocess.run(["wpctl", "set-mute", "@DEFAULT_SINK@", "toggle"])
        print("Mute général basculé")
        return

    if action == "mute_default_source":
        subprocess.run(["wpctl", "set-mute", "@DEFAULT_SOURCE@", "toggle"])
        print("Mute micro basculé")
        return

    if action == "toggle_output":
        toggle_output()
        return

    if action == "mute_active_window":
        nodes = find_nodes({"active_window": True})
    else:
        nodes = find_nodes(button)

    if not nodes:
        print(f"{button['label']}: aucun flux trouvé")
        return

    for node in nodes:
        subprocess.run(
            ["wpctl", "set-mute", node["id"], "toggle"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    # Laisse à PipeWire quelques millisecondes pour publier le nouvel état.
    time.sleep(0.05)

    muted = all(node_is_muted(node["id"]) for node in nodes)

    if button.get("action") == "mute_active_window":
        window = active_window_info()
        label = window["name"] or window["class"] or "Fenêtre active"
    else:
        label = button["label"]

    state = "Mute" if muted else "Unmute"
    icon = "audio-volume-muted" if muted else "audio-volume-high"

    show_text_osd(f"{label} : {state}", icon)
    print(f"{label} : {state}")

def main():
    if "--list-midi" in sys.argv:
        for port in mido.get_input_names():
            print(port)
        return

    ports = mido.get_input_names()
    port = next((p for p in ports if MIDI_PORT_MATCH.lower() in p.lower()), None)
    if not port:
        print("X-TOUCH MINI introuvable. Ports MIDI disponibles :")
        print("\n".join(ports))
        sys.exit(1)

    print(f"Écoute MIDI : {port}")
    print("Tourne un knob ou le fader ; Ctrl+C pour arrêter.")

    last_values = {}
    with mido.open_input(port) as midi:
        for message in midi:
            if message.type == "note_on" and message.velocity > 0:
                handle_button(message.note)
                continue
            if message.type != "control_change":
                continue
            previous = last_values.get(message.control)
            if previous is not None and abs(message.value - previous) < MIN_DELTA:
                continue
            last_values[message.control] = message.value
            handle(message.control, message.value)


if __name__ == "__main__":
    main()
