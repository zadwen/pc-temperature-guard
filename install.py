#!/usr/bin/env python3
"""Install for the current Linux user without administrator privileges."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
source=Path(__file__).resolve().parent
home=Path.home();target=home/'.local/share/pc-temperature-guard';target.mkdir(parents=True,exist_ok=True)
shutil.copy2(source/'guard.py',target/'guard.py')
bin_dir=home/'.local/bin';bin_dir.mkdir(parents=True,exist_ok=True)
launcher=bin_dir/'pc-temperature-guard'
# Use execv arguments, not shell interpolation, for paths with spaces.
launcher.write_text('#!/usr/bin/env python3\nimport os, sys\nos.execv('+repr(sys.executable)+', ['+repr(sys.executable)+', '+repr(str(target/'guard.py'))+', *sys.argv[1:]])\n');launcher.chmod(0o755)
service_dir=home/'.config/systemd/user';service_dir.mkdir(parents=True,exist_ok=True)
service_dir.joinpath('pc-temperature-guard.service').write_text('''[Unit]
Description=PC temperature monitoring and desktop alerts
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart="'''+str(launcher)+'''" --monitor
Restart=on-failure
RestartSec=10

[Install]
WantedBy=graphical-session.target
''')
entry='''[Desktop Entry]
Type=Application
Name=PC Temperature Guard
Comment=CPU, GPU and storage temperatures with cooling alerts
Exec="'''+str(launcher)+'''"
Icon=utilities-system-monitor
Terminal=false
Categories=System;Monitor;
'''
apps=home/'.local/share/applications';apps.mkdir(parents=True,exist_ok=True);(apps/'pc-temperature-guard.desktop').write_text(entry)
desktop=subprocess.check_output(['xdg-user-dir','DESKTOP'],text=True).strip()
shortcut=Path(desktop)/'pc-temperature-guard.desktop';shortcut.write_text(entry);shortcut.chmod(0o755)
subprocess.run(['gio','set',str(shortcut),'metadata::trusted','true'],check=False)
keys=['DISPLAY','WAYLAND_DISPLAY','DBUS_SESSION_BUS_ADDRESS','XAUTHORITY']
subprocess.run(['systemctl','--user','import-environment',*[k for k in keys if k in os.environ]],check=True)
subprocess.run(['systemctl','--user','daemon-reload'],check=True)
subprocess.run(['systemctl','--user','enable','--now','pc-temperature-guard.service'],check=True)
print('Installed dashboard, desktop shortcut and graphical-login service.')
