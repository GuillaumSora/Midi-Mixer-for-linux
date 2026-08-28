# MIDI Mixer for Linux — X-Touch Mini

`midi_mixer.py` connects a Behringer X-Touch Mini to PipeWire, WirePlumber, and KDE Plasma. It provides:

- Per-application volume and mute control
- Master-volume control
- Focused-window audio control
- Headphones / speakers / DAC output cycling
- Spotify transport buttons through MPRIS and `playerctl`
- Default application volumes
- X-Touch Mini Layer A encoder-ring feedback
- Spotify volume recovery when Spotify resets its stream volume

## Requirements

Install the Python and audio dependencies. On an Arch-based distribution:

```bash
sudo pacman -S python-mido playerctl pipewire wireplumber pipewire-pulse
```

The script also expects these commands to be available:

```text
wpctl
pw-dump
pactl
qdbus6
kdotool
```

`playerctl` is only required for Spotify previous/play-pause/next commands.

Check that Spotify is available through MPRIS:

```bash
playerctl --list-all
```

The list should include `spotify`.

## X-Touch Editor setup

Use the controller in **Standard mode**.

- Knobs and the fader must emit `control_change` messages.
- Buttons must emit `note_on` messages.
- Configure mute buttons as **Toggle**, not Momentary.
- Spotify transport buttons can be ordinary buttons; their LEDs are not managed by the script.
- Apply and store the preset in X-Touch Editor after editing it.

### MIDI channel numbering

X-Touch Editor displays channels from **1 to 16**. Mido uses channels from **0 to 15**.

| X-Touch Editor channel | Mido/Python channel |
|---:|---:|
| 1 | 0 |
| 2 | 1 |
| 11 | 10 |
| 16 | 15 |

`LED_CHANNEL` is the controller's **Global MIDI Channel** and is used for mute-button LED feedback.

`LAYER_A_KNOB_CHANNEL` is the actual MIDI channel used by your Layer A encoder knobs. It is used for encoder-ring feedback and defaults.

## Find the Layer A channel

The encoder rings are updated by returning the **same CC on the same MIDI channel** used by the active Layer A encoder. Do not assume that the Layer A channel is necessarily the Global MIDI Channel.

Stop the service first:

```bash
systemctl --user stop midi-mixer.service
```

Then print raw MIDI messages:

```bash
python midi_mixer.py --print-midi
```

Turn the Spotify knob while **Layer A** is active. Example:

```text
control_change channel=10 control=1 value=61 time=0
```

This means:

- Layer A is using Mido channel `10`
- The physical Spotify knob sends CC `1`
- X-Touch Editor displays this as MIDI CH `11`

Set the result at the top of the script:

```python
LAYER_A_KNOB_CHANNEL = 10
```

## Output configuration

Run this command with all desired devices connected:

```bash
pactl list short sinks
```

Copy the sink names exactly into `OUTPUTS`:

```python
OUTPUTS = {
    "casque": "alsa_output.usb-Logitech_PRO_X_000000000000-00.analog-stereo",
    "enceintes": "alsa_output.pci-0000_12_00.6.analog-stereo",
    "dac": "alsa_output.usb-FIIO_JadeAudio_JA11-00.analog-stereo",
}

OUTPUT_ORDER = ["casque", "enceintes", "dac"]
```

The output-switch button cycles through the configured outputs in this order:

```text
Casque → Enceintes → DAC → Casque
```

An unavailable output, such as a disconnected DAC, is skipped automatically. Existing application streams are moved to the newly selected output.

## Buttons

The following configuration matches the script:

```python
BUTTONS = {
    8: {"label": "Spotify", "action": "mute_app", "match": SPOTIFY_MATCH},
    9: {"label": "Discord", "action": "mute_app", "match": ["discord"]},
    10: {"label": "Firefox", "action": "mute_app", "match": ["firefox"]},
    11: {"label": "Steam", "action": "mute_app", "match": ["steam"]},
    12: {"label": "VLC", "action": "mute_app", "match": ["vlc"]},
    14: {"label": "Fenêtre active", "action": "mute_active_window"},
    15: {"label": "Général", "action": "mute_default_sink"},
    16: {"label": "Switch casque / enceintes / DAC", "action": "toggle_output"},
    18: {"label": "Spotify previous", "action": "spotify_previous"},
    19: {"label": "Spotify next", "action": "spotify_next"},
    22: {"label": "Spotify play/pause", "action": "spotify_play_pause"},
}
```

| Action | Effect | LED feedback |
|---|---|---|
| `mute_app` | Mutes/unmutes every matching application stream | On while muted |
| `mute_active_window` | Mutes/unmutes the focused KDE window stream | On while muted |
| `mute_default_sink` | Mutes/unmutes the current default audio output | On while muted |
| `mute_default_source` | Mutes/unmutes the current default microphone | On while muted |
| `toggle_output` | Cycles available headphones, speakers, and DAC sinks | Not managed |
| `spotify_previous` | Selects the previous Spotify track | Not managed |
| `spotify_play_pause` | Toggles Spotify playback | Not managed |
| `spotify_next` | Selects the next Spotify track | Not managed |

Spotify transport commands explicitly use `playerctl --player=spotify`, so an active browser or another player is not controlled by accident.

### Button LED limitation

In Standard mode, only button MIDI notes `0` through `15` have controllable LEDs. Notes `16`, `18`, `19`, and `22` can trigger actions but cannot have their button LEDs controlled by this script.

## Knobs and defaults

`MAPPINGS` maps knobs/fader CC numbers to streams. `default_volume` is a scalar between `0.0` and `1.0`.

```python
MAPPINGS = {
    1: {
        "label": "Spotify",
        "match": SPOTIFY_MATCH,
        "default_volume": 0.30,
        "midi_channel": LAYER_A_KNOB_CHANNEL,
    },
    2: {
        "label": "Discord",
        "match": ["discord"],
        "default_volume": 0.35,
        "midi_channel": LAYER_A_KNOB_CHANNEL,
    },
    3: {
        "label": "Firefox",
        "match": ["firefox"],
        "default_volume": 0.50,
        "midi_channel": LAYER_A_KNOB_CHANNEL,
    },
    4: {
        "label": "Steam / jeu",
        "match": ["steam"],
        "default_volume": 0.60,
        "midi_channel": LAYER_A_KNOB_CHANNEL,
    },
    5: {
        "label": "VLC",
        "match": ["vlc"],
        "default_volume": 0.40,
        "midi_channel": LAYER_A_KNOB_CHANNEL,
    },
    7: {
        "label": "Fenêtre active",
        "active_window": True,
        "midi_channel": LAYER_A_KNOB_CHANNEL,
    },
    8: {
        "label": "Volume général",
        "default_sink": True,
        "default_volume": 0.50,
        "midi_channel": LAYER_A_KNOB_CHANNEL,
    },
    9: {
        "label": "Fenêtre active (fader)",
        "active_window": True,
        "midi_channel": LAYER_A_KNOB_CHANNEL,
    },
}
```

At startup, the script:

1. Applies each configured default volume to streams already available.
2. Stores those defaults as the current target volumes.
3. Sends the matching CC value back to Layer A so the encoder ring is synchronized.
4. Starts the background volume worker.

After turning a knob, its latest value replaces the startup default for the current script session.

The fader on CC `9` has no encoder LED ring, so it does not receive ring feedback.

## Test encoder rings

Stop the service before the test:

```bash
systemctl --user stop midi-mixer.service
python midi_mixer.py --test-knob-leds
```

Each configured encoder CC from `1` to `8` receives, in order:

```text
0% → 50% → 100%
```

The test waits two seconds at each value. If the rings do not move:

1. Run `python midi_mixer.py --print-midi`.
2. Turn a Layer A knob.
3. Set `LAYER_A_KNOB_CHANNEL` to the reported Mido `channel` value.
4. Run the ring test again.
5. Confirm that the controller is still in Standard mode and Layer A is active.

## Spotify volume recovery

Spotify can reset its stream volume when changing tracks. The script remembers the last requested volume for every tracked application and runs a background worker:

```python
APP_VOLUME_SYNC_INTERVAL = 0.5
NODE_VOLUME_EPSILON = 0.005
```

Every 0.5 seconds, it checks the actual PipeWire stream volume with `wpctl get-volume` and reapplies the target if needed.

This works even if no MIDI message is received. A very short volume jump can still be audible because the script corrects Spotify after Spotify has performed its reset.

## Running

```bash
# List MIDI input and output ports
python midi_mixer.py --list-midi

# Normal launch
python midi_mixer.py

# Test mute-button LEDs
python midi_mixer.py --test-leds

# Test Layer A encoder rings
python midi_mixer.py --test-knob-leds

# Show raw MIDI messages
python midi_mixer.py --print-midi

# Show PipeWire matching diagnostics
python midi_mixer.py --debug-matching

# Show matching diagnostics and background volume synchronization
python midi_mixer.py --debug-matching --debug-sync
```

Before using a manual test mode, stop the systemd service to avoid two processes opening the same controller port.

## systemd user service

Create the service directory:

```bash
mkdir -p ~/.config/systemd/user
```

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

Update the paths if your project lives somewhere else. Then reload and enable it:

```bash
systemctl --user daemon-reload
systemctl --user enable --now midi-mixer.service
```

Useful commands:

```bash
# Service state
systemctl --user is-active midi-mixer.service

# Restart after changing midi_mixer.py
systemctl --user restart midi-mixer.service

# Recent service logs
journalctl --user -u midi-mixer.service -n 100 --no-pager

# Follow logs live
journalctl --user -u midi-mixer.service -f
```
