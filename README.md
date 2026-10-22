# PC Temperature Guard

A lightweight Linux desktop tool for CPU, NVIDIA GPU, memory and storage temperature monitoring, desktop alerts, and cleaning reminders. Python standard library + Tkinter; no cloud account, API key or paid service.

**Temperature sensors cannot detect dust.** Sustained heat or a rise above a learned low-load baseline can suggest inspecting airflow, fans, dust filters and room temperature. These are heuristics, not a diagnosis. High load can legitimately increase temperatures, and GPU fans may stop at idle.

## Features

- Live dashboard with per-sensor temperatures and warning/critical thresholds.
- Background service starts at graphical login; closing the dashboard keeps monitoring active.
- Desktop and application-menu shortcut.
- Warning after 60 seconds above the configured temperature; immediate critical notification.
- Low-load cooling alert after a 15°C increase above the learned baseline persists for 10 minutes. Learning needs at least 20 low-load samples; GPU/CPU utilization must be below 20%.
- Alert if a previously seen sensor disappears for three samples.
- Scheduled 90-day cleaning reminder and **I cleaned the PC** button. The first schedule starts at installation, not a fictitious cleaning date.
- Notification repeat limits: 30 minutes for warnings, five minutes for critical alerts, one day for cleaning reminders.
- Seven days of local temperature history in SQLite. History is pruned hourly.
- Ignores implausible hardware thresholds, including zero-valued DIMM alarm limits.

## Install

Requires a graphical Linux session with systemd user services, Python 3 with Tkinter, `notify-send`, `xdg-user-dir` and `gio`. `nvidia-smi` is optional for NVIDIA GPUs. CPU/storage sensors are read directly from `/sys/class/hwmon` without root.

On Debian/Ubuntu, install dependencies if missing:

```bash
sudo apt install python3 python3-tk libnotify-bin xdg-user-dirs libglib2.0-bin
```

From the repository:

```bash
python3 install.py
```

The installer copies the app into your user directory, creates launchers, and enables `pc-temperature-guard.service` for graphical login. It does not modify firmware, fan speed, power limits, drivers, or system services. Run it as your normal desktop user, not root.

## Use

```bash
pc-temperature-guard                  # dashboard
pc-temperature-guard --once           # current sensor readings
pc-temperature-guard --notify-test    # explicitly labeled test notification
systemctl --user status pc-temperature-guard.service
journalctl --user -u pc-temperature-guard.service
```

Desktop notifications are local messages, not email or SMS. Your desktop's Do Not Disturb settings can hide them. Monitoring runs while you are logged into the graphical session; it cannot warn while the PC is off or suspended.

## Settings and thresholds

The Settings button adjusts sample interval and cleaning reminder frequency. Advanced settings can be edited in `~/.config/pc-temperature-guard/config.json`; changes are picked up on the next sample. The monitor uses a 20-second interval by default (minimum five seconds).

Default warning/critical thresholds in °C:

| Sensor | Warning | Critical |
| --- | ---: | ---: |
| CPU | 85 | 95 |
| NVIDIA GPU | 80 | 90 |
| Storage | 70 | 80 |
| Memory | 75 | 90 |
| Other | 85 | 100 |

These are conservative configurable software defaults, not universal manufacturer specifications. Valid lower hardware limits take precedence; implausible hardware values are ignored. Consult the specifications for your hardware when adjusting settings. Room temperature, cooler changes, workload and sensor availability can affect the baseline. No fan-failure diagnosis is made from a zero fan reading.

Example configuration:

```json
{
  "interval": 20,
  "cleaning_days": 90,
  "sustain_seconds": 60,
  "cooling_seconds": 600,
  "rise_celsius": 15,
  "warning": {"cpu": 85, "gpu": 80, "disk": 70},
  "critical": {"cpu": 95, "gpu": 90, "disk": 80}
}
```

State, live status, cleaning timestamps and history stay under `~/.local/state/pc-temperature-guard/`. No data is uploaded. A cleaning acknowledgment resets the learned baseline on the next sample. Old readings expire automatically.

## Test

```bash
python3 -m unittest discover
```

Tests cover transient and sustained heat, critical alerts, low-load baseline rises, high-load suppression, normal zero-RPM fans, sensor loss, notification repeat limits/retry, cleaning reminders, invalid hardware thresholds and CPU load calculation. Tests use synthetic data and do not heat the hardware.

## Disable or remove

```bash
systemctl --user disable --now pc-temperature-guard.service
```

Remove `~/.config/systemd/user/pc-temperature-guard.service`, the installed application directory, and its launcher/desktop entries if uninstalling. Keep or remove your local state separately. Run `systemctl --user daemon-reload` after removing the service file.

## Limits

Only readable Linux hwmon sensors and NVIDIA GPUs are supported today. AMD/Intel GPUs are shown only when their drivers expose hwmon temperature sensors. No automatic shutdown or fan control is performed. A missing CPU load reading disables low-load baseline analysis for that sample. Sensor replacement or renaming may require learning a new baseline.
