## X-Touch Mini button LEDs

The script can keep X-Touch Mini button LEDs synchronized with mute state:

- LED **on**: the associated application or master output is muted
- LED **off**: the associated target is not muted
- The audio-output switch does not use an LED in this project

### Important limitations

In Standard mode, the X-Touch Mini only exposes controllable button LEDs for MIDI notes `0` to `15`:

| Physical row | LED-controllable notes |
|---|---|
| Top row, buttons 1–8 | `0` to `7` |
| Bottom row, buttons 9–16 | `8` to `15` |

If a button is configured with note `16` or above, it can still trigger an action in the script, but its LED cannot be controlled through the standard X-Touch Mini LED protocol.

The Layer A and Layer B LEDs are also not assignable.

### Configure the Global MIDI channel

Button LEDs receive MIDI feedback on the **Global MIDI Channel** configured in X-Touch Editor.

In `midi_mixer.py`, configure:

```python
LED_CHANNEL = 0
```

Mido uses channels from `0` to `15`, while X-Touch Editor shows them as `1` to `16`.

| X-Touch Editor Global CH | `LED_CHANNEL` in Python |
|---:|---:|
| 1 | `0` |
| 2 | `1` |
| 11 | `10` |
| 16 | `15` |

For example, if X-Touch Editor shows **Global CH 11**, use:

```python
LED_CHANNEL = 10
```

### Configure buttons as Toggle

In **X-Touch Editor**, configure the mute buttons as **Toggle**, not Momentary.

- **Momentary** buttons may turn their LED off when the physical button is released
- **Toggle** buttons preserve their state between presses and work correctly with MIDI LED feedback

Apply this setting to every button assigned to:

- Application mute
- Focused-window mute
- Master mute
- Microphone mute

### Test LEDs

Stop the user service first:

```bash
systemctl --user stop midi-mixer.service
```

Then run:

```bash
python midi_mixer.py --test-leds
```

Each configured button LED with a note from `0` to `15` should briefly turn on, then off.

If no LED reacts:

1. Check that the controller is in **Standard mode**, not MC mode
2. Check that `LED_CHANNEL` matches the Global MIDI Channel in X-Touch Editor
3. Try another `LED_CHANNEL` value if the Global Channel is unknown
4. Make sure the button note is between `0` and `15`

Start the script normally after a successful test:

```bash
python midi_mixer.py
```

## Mute / unmute

Available button actions:

| Action | Effect | LED behavior |
|---|---|---|
| `mute_default_sink` | Mute or unmute the master output | On when master output is muted |
| `mute_default_source` | Mute or unmute the default microphone | On when microphone is muted |
| `mute_app` | Mute or unmute configured application streams | On when the application is muted |
| `mute_active_window` | Mute or unmute the focused application's stream | On when the focused target is muted |
| `toggle_output` | Switch between headphones and speakers | No LED feedback |

Example:

```python
BUTTONS = {
    8: {"label": "Spotify", "action": "mute_app", "match": ["spotify"]},
    9: {"label": "Discord", "action": "mute_app", "match": ["discord"]},
    10: {"label": "Firefox", "action": "mute_app", "match": ["firefox"]},
    14: {"label": "Focused window", "action": "mute_active_window"},
    15: {"label": "Master", "action": "mute_default_sink"},
    16: {"label": "Headphones / speakers", "action": "toggle_output"},
}
```

The script synchronizes mute LEDs when it starts. It also updates the matching LED after every mute or unmute action.

For focused-window mute, KDE OSD displays the title of the focused window and the final state:

```text
Spotify : Mute
Spotify : Unmute
```

## systemd user service

A systemd user service starts the mixer automatically after login.

Create the unit directory:

```bash
mkdir -p ~/.config/systemd/user
```

Create the service:

```bash
nano ~/.config/systemd/user/midi-mixer.service
```

Paste the following configuration:

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

> Update both paths if the repository is not stored in `~/Projets/Midi-Mixer-for-linux`.

`Restart=on-failure` restarts the script only if it crashes or exits with an error. This is preferable to `Restart=always` here because the mixer is a persistent process and unnecessary restarts can reset MIDI controller feedback. [web:478][web:459]

Reload systemd, enable the service, and start it:

```bash
systemctl --user daemon-reload
systemctl --user enable --now midi-mixer.service
```

Check that it is running:

```bash
systemctl --user is-active midi-mixer.service
```

Expected result:

```text
active
```

Read recent logs without following them continuously:

```bash
journalctl --user -u midi-mixer.service -n 100 --no-pager
```

Restart the service after changing the Python script:

```bash
systemctl --user restart midi-mixer.service
```

Stop it temporarily, for example before using `--test-leds`:

```bash
systemctl --user stop midi-mixer.service
```