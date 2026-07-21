# MIDI Mixer for Linux

A small MIDI mixer for Linux, inspired by MIDI Mixer on Windows.

It lets you use a MIDI controller — for example a **Behringer X‑Touch Mini** — to:

- Control Spotify, Discord, Firefox, VLC, Steam, or any other application's volume independently
- Control the currently focused application's volume
- Control the global output volume
- Mute / unmute an application, the focused window, the microphone, or global audio
- Toggle between two audio outputs, such as headphones and speakers
- Display KDE on-screen feedback for volume, mute state, and output switching

> [!WARNING]
> This project was designed and tested on **KDE Plasma running on Wayland**, with PipeWire and WirePlumber.
> The focused-window feature relies on `kdotool`, which targets KDE Plasma / KWin.

## Requirements

- Linux with PipeWire and WirePlumber
- KDE Plasma on Wayland
- Python 3
- A MIDI controller
- `wpctl`, provided by WirePlumber
- `pactl`, provided by PipeWire-Pulse, for audio-output switching
- `kdotool`, used to identify the focused window on KDE Wayland

## Installation

### Clone the repository

```bash
git clone https://github.com/GuillaumSora/Midi-Mixer-for-linux.git
cd Midi-Mixer-for-linux
```

### Install Arch / CachyOS dependencies

```bash
sudo pacman -Syu python python-mido python-rtmidi pipewire wireplumber pipewire-pulse alsa-utils jq
```

Install `kdotool` from the AUR:

```bash
yay -S kdotool-bin
```

> If you do not use `yay`, install it first or use another compatible AUR helper.

### Check PipeWire

```bash
systemctl --user status pipewire pipewire-pulse wireplumber
```

All services should be active.

## Manual launch

Make the script executable:

```bash
chmod +x midi_mixer.py
```

List the available MIDI devices:

```bash
python midi_mixer.py --list-midi
```

Then start the script:

```bash
python midi_mixer.py
```

The terminal should display something similar to:

```text
Listening to MIDI: X-TOUCH MINI MIDI 1
Turn a knob or press a button; Ctrl+C to stop.
```

Use `Ctrl+C` to stop the script.

## Script configuration

All configuration is located near the top of `midi_mixer.py`.

Main sections:

- `MIDI_PORT_MATCH`: name, or a unique part of the name, of your MIDI controller
- `MAPPINGS`: knobs and faders assigned to volume controls
- `BUTTONS`: buttons assigned to mute actions, output switching, and more
- `OUTPUTS`: audio outputs used for headphone / speaker switching
- `MAX_VOLUME`: maximum allowed volume

Example:

```python
MIDI_PORT_MATCH = "X-TOUCH MINI"

MAPPINGS = {
    16: {"label": "Spotify", "match": ["spotify"]},
    17: {"label": "Discord", "match": ["discord"]},
    18: {"label": "Firefox", "match": ["firefox"]},
    19: {"label": "Steam / game", "match": ["steam"]},
    20: {"label": "VLC", "match": ["vlc"]},
    21: {"label": "Focused window", "active_window": True},
    22: {"label": "Master volume", "default_sink": True},
    23: {"label": "Focused window (fader)", "active_window": True},
}

BUTTONS = {
    0: {"label": "Master mute", "action": "mute_default_sink"},
    1: {"label": "Spotify mute", "action": "mute_app", "match": ["spotify"]},
    2: {"label": "Discord mute", "action": "mute_app", "match": ["discord"]},
    3: {"label": "Focused window mute", "action": "mute_active_window"},
    4: {"label": "Toggle headphones / speakers", "action": "toggle_output"},
    5: {"label": "Microphone mute", "action": "mute_default_source"},
}
```

The values `16`, `17`, `0`, `1`, and so on are examples. Replace them with the MIDI values sent by your own controller.

## Find knobs and buttons

Knobs, faders, and buttons send MIDI events.

- **Knobs and faders** usually send `control_change` messages, containing a controller number (`CC`)
- **Buttons** usually send `note_on` messages, containing a note number (`note`)

### Print MIDI events

In the main MIDI loop of the script, temporarily add this line immediately after:

```python
for message in midi:
```

Add:

```python
print(message)
```

The block should look like this:

```python
with mido.open_input(port) as midi:
    for message in midi:
        print(message)

        if message.type == "note_on" and message.velocity > 0:
            handle_button(message.note)
            continue

        if message.type != "control_change":
            continue

        handle(message.control, message.value)
```

Stop the service if it is currently running:

```bash
systemctl --user stop midi-mixer.service
```

Start the script manually:

```bash
python midi_mixer.py
```

Turn a knob, move the fader, or press a button. You should see output similar to:

```text
control_change channel=0 control=16 value=85 time=0
note_on channel=0 note=1 velocity=127 time=0
```

In this example:

- The knob or fader uses CC `16`
- The button uses note `1`

You can now update `MAPPINGS` and `BUTTONS`:

```python
MAPPINGS = {
    16: {"label": "Spotify", "match": ["spotify"]},
}
```

```python
BUTTONS = {
    1: {"label": "Spotify mute", "action": "mute_app", "match": ["spotify"]},
}
```

When your configuration is complete, remove or comment out:

```python
print(message)
```

## Find applications

The script targets PipeWire audio streams by application name or process binary, never by numeric ID. IDs change after a reboot or when an application restarts.

Start the application and make it play audio, then run:

```bash
pw-dump | jq -r '
  .[] |
  select(.type == "PipeWire:Interface:Node") |
  select(.info.props["media.class"] == "Stream/Output/Audio") |
  [
    .id,
    .info.props["application.name"],
    .info.props["application.process.binary"],
    .info.props["application.process.id"],
    .info.props["node.name"]
  ] |
  @tsv
'
```

Example output:

```text
72      Spotify         spotify         14556   Spotify
94      Firefox         firefox         16783   Firefox
```

Use relevant values in lowercase in the `match` list:

```python
MAPPINGS = {
    16: {"label": "Spotify", "match": ["spotify"]},
    18: {"label": "Firefox", "match": ["firefox"]},
}
```

> [!NOTE]
> An application must have an active audio stream to be detected.
> For example, Spotify must be playing music and Firefox must be playing a video or audio file.

## Focused window

The special target:

```python
{"label": "Focused window", "active_window": True}
```

controls the application currently in the foreground.

This is useful for games: you do not need one dedicated mapping for every game. Focus the game, VLC, Spotify, Firefox, or another application, then use the knob or fader mapped with `active_window`.

The script first tries to match the focused window's PID with the PipeWire stream PID. If that fails — which is common with browsers, Spotify, Proton, and other multi-process applications — it falls back to matching the focused window name and class.

Test `kdotool` with:

```bash
kdotool getactivewindow getwindowpid
kdotool getactivewindow getwindowclassname
kdotool getactivewindow getwindowname
```

Focus the target application before running these commands.

## Headphone / speaker switching

List PipeWire-Pulse audio outputs:

```bash
pactl list short sinks
```

Example:

```text
45  alsa_output.usb-Headset_USB_analog-stereo
51  alsa_output.pci-0000_0c_00.6.analog-stereo
```

Copy the name from the second column into the script configuration:

```python
OUTPUTS = {
    "headphones": "alsa_output.usb-Headset_USB_analog-stereo",
    "speakers": "alsa_output.pci-0000_0c_00.6.analog-stereo",
}
```

The `toggle_output` button action:

- Sets the new default audio output
- Immediately moves currently active audio streams to that output
- Displays KDE visual feedback

## Mute / unmute

Available button actions:

| Action | Effect |
|---|---|
| `mute_default_sink` | Mute or unmute the master output |
| `mute_default_source` | Mute or unmute the default microphone |
| `mute_app` | Mute or unmute the configured application streams |
| `mute_active_window` | Mute or unmute the focused window's audio stream |
| `toggle_output` | Toggle between headphones and speakers |

Example:

```python
BUTTONS = {
    0: {"label": "Master mute", "action": "mute_default_sink"},
    1: {"label": "Spotify mute", "action": "mute_app", "match": ["spotify"]},
    2: {"label": "Discord mute", "action": "mute_app", "match": ["discord"]},
    3: {"label": "Focused window mute", "action": "mute_active_window"},
    4: {"label": "Toggle headphones / speakers", "action": "toggle_output"},
    5: {"label": "Microphone mute", "action": "mute_default_source"},
}
```

For focused-window mute, the KDE OSD displays the window name and actual state:

```text
Spotify: Mute
Spotify: Unmute
```

## Volume limits

By default:

```python
MAX_VOLUME = 1.0
```

means 100%.

To allow gain above 100%:

```python
MAX_VOLUME = 1.5
```

> [!WARNING]
> Volume above 100% can cause clipping or distortion. Use it carefully.

The script can snap values close to MIDI endpoints to exactly 0% or 100%, preventing a knob at its maximum physical position from applying only 99%.

## systemd user service

A user service starts the mixer automatically after you log in.

Create the user-unit directory:

```bash
mkdir -p ~/.config/systemd/user
```

Create the service file:

```bash
nano ~/.config/systemd/user/midi-mixer.service
```

Paste the following:

```ini
[Unit]
Description=MIDI Mixer PipeWire
After=pipewire.service wireplumber.service

[Service]
Type=simple
ExecStart=/usr/bin/python %h/Projects/Midi-Mixer-for-linux/midi_mixer.py
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
```

> Update the path after `ExecStart=` if the repository is located elsewhere.
> For example, replace `%h/Projects/` with `%h/Projets/` if your repository is under `~/Projets`.

Reload systemd, enable, and start the service:

```bash
systemctl --user daemon-reload
systemctl --user enable --now midi-mixer.service
```

Check its status:

```bash
systemctl --user status midi-mixer.service
```

Show live logs:

```bash
journalctl --user -fu midi-mixer.service
```

Stop the service:

```bash
systemctl --user stop midi-mixer.service
```

Restart it after changing the script:

```bash
systemctl --user restart midi-mixer.service
```

Disable automatic startup:

```bash
systemctl --user disable --now midi-mixer.service
```

## Troubleshooting

### MIDI controller not found

List MIDI ports:

```bash
python midi_mixer.py --list-midi
```

Then change:

```python
MIDI_PORT_MATCH = "X-TOUCH MINI"
```

to a unique part of the displayed MIDI port name.

### An application is not controlled

- Check that it is actually playing audio
- Run the `pw-dump` command shown above
- Check `application.name`, `application.process.binary`, and `node.name`
- Add a relevant value to its `match` list

Example:

```python
{"label": "Discord", "match": ["discord", "Discord"]}
```

### Focused window is not detected

Check that `kdotool` works:

```bash
kdotool getactivewindow getwindowpid
```

Then check your session type:

```bash
echo $XDG_SESSION_TYPE
```

The command should return:

```text
wayland
```

### The script does not work through systemd

Read the service logs:

```bash
journalctl --user -u midi-mixer.service --no-pager -n 100
```

Also test it manually from the project directory:

```bash
python midi_mixer.py
```

## License

To be defined.
