# MIDI Mixer for Linux — X-Touch Mini

`midi_mixer.py` connects a Behringer X-Touch Mini to PipeWire/WirePlumber and KDE Plasma. It provides per-application volume and mute controls, master volume, output switching, and Spotify media controls.

## Requirements

```bash
sudo pacman -S python-mido playerctl pipewire wireplumber pipewire-pulse
```

The script also uses `wpctl`, `pactl`, `pw-dump`, `qdbus6`, and `kdotool`. `playerctl` is required for Spotify commands through MPRIS. Verify that Spotify is available:

```bash
playerctl --list-all
```

The output should contain `spotify`. [web:34][web:43]

## X-Touch Editor

Use the controller in **Standard mode**. Knobs and fader must send `control_change` messages; buttons must send `note_on` messages.

Configure **mute** buttons as **Toggle**, not Momentary. Spotify transport buttons can be regular buttons because their LED state is not managed by the script.

### LED channel

In the script, `LED_CHANNEL` uses values from 0 to 15, while X-Touch Editor shows channels 1 to 16.

| X-Touch Editor Global CH | `LED_CHANNEL` |
|---:|---:|
| 1 | 0 |
| 11 | 10 |
| 16 | 15 |

In Standard mode, only MIDI notes `0` through `15` have controllable button LEDs. A note `16` or higher can still trigger an action, but its LED cannot be controlled.

## Configuration

At minimum, adapt `OUTPUTS`, `LED_CHANNEL`, `BUTTONS` note numbers, and `MAPPINGS` CC values to match your X-Touch Editor configuration.

```python
SPOTIFY_MATCH = ["spotify", "spotify-launcher"]

BUTTONS = {
    # Pick unused note numbers in X-Touch Editor.
    0: {"label": "Spotify previous", "action": "spotify_previous"},
    1: {"label": "Spotify play / pause", "action": "spotify_play_pause"},
    2: {"label": "Spotify next", "action": "spotify_next"},

    8: {"label": "Spotify", "action": "mute_app", "match": SPOTIFY_MATCH},
    9: {"label": "Discord", "action": "mute_app", "match": ["discord"]},
    10: {"label": "Firefox", "action": "mute_app", "match": ["firefox"]},
    11: {"label": "Steam", "action": "mute_app", "match": ["steam"]},
    12: {"label": "VLC", "action": "mute_app", "match": ["vlc"]},
    14: {"label": "Active window", "action": "mute_active_window"},
    15: {"label": "Master", "action": "mute_default_sink"},
    16: {"label": "Headphones / speakers", "action": "toggle_output"},
}

MAPPINGS = {
    1: {"label": "Spotify", "match": SPOTIFY_MATCH},
    2: {"label": "Discord", "match": ["discord"]},
    3: {"label": "Firefox", "match": ["firefox"]},
    4: {"label": "Steam / game", "match": ["steam"]},
    5: {"label": "VLC", "match": ["vlc"]},
    7: {"label": "Active window", "active_window": True},
    8: {"label": "Master volume", "default_sink": True},
    9: {"label": "Active window (fader)", "active_window": True},
}
```

`SPOTIFY_MATCH` supports both the Spotify client and `spotify-launcher`. The script searches `application.name`, `application.process.binary`, `node.name`, and `media.name` to identify PipeWire streams.

## Button actions

| Action | Effect | LED |
|---|---|---|
| `mute_app` | Mutes/unmutes all matching application streams | On while muted |
| `mute_active_window` | Mutes/unmutes the focused KDE window stream | On while muted |
| `mute_default_sink` | Mutes/unmutes the default audio output | On while muted |
| `mute_default_source` | Mutes/unmutes the default microphone | On while muted |
| `toggle_output` | Switches between headphones and speakers, moving current streams | Not managed |
| `spotify_previous` | Previous Spotify track (`playerctl --player=spotify previous`) | Not managed |
| `spotify_play_pause` | Spotify play/pause (`playerctl --player=spotify play-pause`) | Not managed |
| `spotify_next` | Next Spotify track (`playerctl --player=spotify next`) | Not managed |

MPRIS commands explicitly target Spotify, preventing another active media player from being controlled instead. [web:35][web:43]

### Spotify transport LEDs

In `sync_button_leds()`, place this block immediately after:

```python
action = button["action"]
```

```python
if action in {
    "toggle_output",
    "spotify_previous",
    "spotify_play_pause",
    "spotify_next",
}:
    continue
```

Those actions are not mute actions, so they must not enter the LED synchronization logic.

## Spotify volume persistence

Spotify may reset its stream volume to 100% when switching tracks. The script remembers the last volume sent by each application knob and uses an independent monitoring thread.

```python
APP_VOLUME_SYNC_INTERVAL = 0.5
NODE_VOLUME_EPSILON = 0.005
```

Every 0.5 seconds, the worker reads the actual stream volume with `wpctl get-volume` and reapplies the target volume if it differs. It works even when no new MIDI message arrives. A brief volume jump may still occur because the script corrects Spotify after the change happens. WirePlumber can persist stream properties in `~/.local/state/wireplumber/stream-properties`. [web:20][web:26]

## Running and debugging

```bash
# List MIDI ports
python midi_mixer.py --list-midi

# Normal start
python midi_mixer.py

# LED test (stop the service first)
systemctl --user stop midi-mixer.service
python midi_mixer.py --test-leds

# Show detected PipeWire streams
python midi_mixer.py --debug-matching

# Show streams and detailed volume synchronization
python midi_mixer.py --debug-matching --debug-sync
```

Useful debug output looks like:

```text
[debug] set-volume node=103 label=Spotify target=0.480 current=1.0
```

## systemd user service

Create `~/.config/systemd/user/midi-mixer.service`:

```ini
[Unit]
Description=MIDI Mixer PipeWire
After=graphical-session.target pipewire.service pipewire-pulse.service wireplumber.service
Wants=pipewire.service pipewire-pulse.service wireplumber.service

[Service]
Type=simple
WorkingDirectory=%h/Projets/Midi-Mixer-for-linux
ExecStartPre=/usr/bin/sleep 10
ExecStart=/usr/bin/python -u %h/Projets/Midi-Mixer-for-linux/midi_mixer.py
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

Update both paths if the repository is stored somewhere else, then enable the service:

```bash
systemctl --user daemon-reload
systemctl --user enable --now midi-mixer.service
```

Useful commands:

```bash
systemctl --user is-active midi-mixer.service
systemctl --user restart midi-mixer.service
journalctl --user -u midi-mixer.service -n 100 --no-pager
journalctl --user -u midi-mixer.service -f
```