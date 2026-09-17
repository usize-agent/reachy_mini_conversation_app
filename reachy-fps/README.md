# reachy-fps

First-person teleoperation for Reachy Mini: a single static binary that serves a browser UI with live camera, mouse-look, and two-way audio. No Hugging Face account is needed; it talks to the daemon directly over your LAN (or localhost for a Lite).

## Run (macOS, Lite): daemon + UI in one command

```sh
./start.sh
```

This works from any terminal, tmux included. It launches the daemon through `dist/Reachy Mini Daemon.app`, a background app with no Dock icon that owns the camera and microphone permission. The daemon's log streams into your terminal with a `[daemon]` prefix (and is saved to `~/.reachy/daemon.log`), and Ctrl-C stops both. The first run shows a macOS Camera/Microphone prompt for "Reachy Mini Daemon" **on the Mac's screen**, not in your SSH session. Allow it once, in person or over Screen Sharing; after that it's remembered.

Over SSH, run it inside tmux so it survives disconnects. `./stop.sh` from any shell stops the daemon and UI, and the daemon puts the robot to sleep as it exits. If you're not in tmux, dropping the SSH connection stops everything. The browser only auto-opens when not over SSH.

## Run the UI on its own

```sh
go build -o reachy-fps . && ./reachy-fps            # Lite: daemon on this machine
./reachy-fps -daemon http://reachy-mini.local:8000  # Wireless
```

It listens on `0.0.0.0:8080` and opens http://localhost:8080.

**Remote use (e.g. Tailscale):** video and controls work over plain `http://<tailscale-ip>:8080`. Voice passthrough needs HTTPS because browsers only allow the microphone on localhost or HTTPS. `./start.sh` handles this: if Tailscale is running, it publishes the UI at `https://<machine>.<tailnet>.ts.net/` with `tailscale serve` for as long as it runs, and prints the URL. That requires HTTPS Certificates to be enabled once in the [Tailscale admin console](https://login.tailscale.com/admin/dns); the script tells you if they aren't. Set `REACHY_TAILSCALE=0` to skip it.

There's no authentication: anyone who can reach the port can drive the robot. Use `-listen 127.0.0.1:8080` to keep it local.

Flags: `-listen` (default `0.0.0.0:8080`), `-daemon` (default `http://localhost:8000`), `-signalling` (default: daemon host, port 8443), `-open` (default `true`).

## Controls

| Input | Action |
| --- | --- |
| Click video | Capture the mouse (Esc releases it) |
| Mouse | Head yaw and pitch. Turning past the neck limit rotates the base; keep going and it wraps around. |
| Q / E | Spin the base left / right |
| Space / C | Raise / lower the head (-30 mm to +20 mm) |
| R | Recenter head and base |
| M | Voice passthrough on/off (starts off) |
| Antenna pane | Drag a tip to set the angle; "mirror" moves both symmetrically |

**On a phone** (touch devices get these automatically): drag the video to look around, use the joystick (left/right spins the base, up/down raises or lowers the head), and use the buttons on the right to toggle voice 🎙, recenter ⟲ and show the antenna pane 📡. Voice needs the HTTPS Tailscale URL.

## Safety

- **Soft limits** are well inside the measured range: head yaw ±40° from the base, pitch ±20°, height -20 to +12 mm, base ±149° (the hardware stops at ~160°), antennas ±160°. Yaw and pitch share an elliptical envelope, so the extremes can't be combined, and that envelope shrinks by up to 40% as the head moves away from neutral height. Combined extremes can strain the Stewart platform even when each axis alone is fine.
- **Every command is speed-limited.** Mouse flicks, R recenters and reconnects all move the robot smoothly instead of jumping.
- **Base wrap:** if you keep turning into the base limit (about 35° of extra push), the base unwinds the long way to the opposite limit and the HUD shows "Unwinding base…". Turn input is ignored until the unwind finishes.
- Released keys can't get stuck: all held keys clear when the window loses focus, and the joystick recenters when you let go.
- **Antenna stall guard:** if an antenna stays more than ~17° from its target for 0.4 s (blocked by the head, the other antenna, or a finger), the command backs off to where it actually is, and "Antenna blocked" flashes on screen.
- The daemon's own IK clamps apply on top of all this.

**Staged startup.** `start.sh` never uses the daemon's built-in wake-up emote. It brings the robot up in stages so power draw doesn't spike all at once: daemon, then camera stream (waits up to 20 s and warns if it doesn't appear), then motors enabled while holding their current pose, then one slow 4-second glide to neutral. The UI's "Wake up" button does the same glide. To leave the motors off after startup, run `REACHY_WAKE=0 ./start.sh`.

## How it works

```
Browser ──/api/*───────► binary ──► daemon :8000   (set_target + state WebSockets)
        ──/signalling──► binary ──► webrtcsink :8443 (SDP/ICE only)
        ◄════ WebRTC video+audio, mic back ════► robot (peer-to-peer)
```

The binary embeds `web/` and proxies both upstreams onto one origin. Media never passes through it.

## Build portable binaries

```sh
./build.sh   # dist/reachy-fps-{darwin,linux,windows}-{amd64,arm64}
```

These are pure-Go static binaries (`CGO_ENABLED=0`) with no runtime dependencies.

## Troubleshooting

- **"No stream" for video:** the daemon isn't publishing a WebRTC producer, usually because it can't open the camera. On macOS, the terminal app that launches the daemon needs Camera and Microphone permission. **Don't start the daemon directly inside tmux or screen:** their server process is detached from the terminal app, so macOS denies camera access without ever prompting. Use `./start.sh`, which launches it through the wrapper app.
- **Video missing or freezing with a closed MacBook lid:** the daemon only publishes the stream while *both* the camera and the robot's mic deliver data. With the lid closed, macOS keeps dropping into DarkWake ("Clamshell Sleep"): SSH and the network keep working, but CoreAudio delivers nothing, so the stream freezes. Power assertions (`caffeinate`) can't prevent this. So `start.sh` runs `sudo pmset -a disablesleep 1` (it asks for your password) and a small root guard turns sleep back on as soon as `start.sh` exits, however it exits. If sleep was already disabled, it leaves it alone. `REACHY_NOSLEEP=0` skips this and falls back to `caffeinate`, which only wakes the Mac back up about 30 s after each drop. `pmset -g log | grep DarkWake` shows the drops.
- **Robot ignores movement:** the daemon drops `set_target` while a move (wake up/sleep) is running, or while the motors are disabled. Press "Wake up".
- **Antenna pane moves the wrong way:** flip `SIGN` in `web/antennas.js`.
