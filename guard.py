#!/usr/bin/env python3
"""Local Linux temperature alerts. Cooling warnings are not dust detection."""
import argparse
import csv
import fcntl
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import statistics
import subprocess
import time
from dataclasses import dataclass, asdict

CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))/'pc-temperature-guard'
DATA = Path(os.environ.get('XDG_STATE_HOME', Path.home()/'.local/state'))/'pc-temperature-guard'
DEFAULTS = {'interval':20, 'cleaning_days':90, 'sustain_seconds':60,
            'cooling_seconds':600, 'rise_celsius':15,
            'warning':{'cpu':85,'gpu':80,'disk':70,'memory':75,'other':85},
            'critical':{'cpu':95,'gpu':90,'disk':80,'memory':90,'other':100}}

@dataclass
class Sensor:
    key: str
    name: str
    kind: str
    temperature: float
    warning: float
    critical: float
    utilization: float | None = None
    fan: float | None = None

def read_json(path, fallback):
    try:return json.loads(Path(path).read_text())
    except (OSError, ValueError):return fallback

def write_json(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(data,indent=2)+'\n');temp.replace(path)

def config():
    saved=read_json(CONFIG/'config.json',{})
    if not isinstance(saved,dict) or any(not isinstance(saved.get(k,{}),dict) for k in ['warning','critical']):raise ValueError('Configuration must contain valid settings tables')
    result={**DEFAULTS,**saved}
    for key in ['warning','critical']:result[key]={**DEFAULTS[key],**saved.get(key,{})}
    for kind in result['warning']:
        if not 20 <= float(result['warning'][kind]) < float(result['critical'][kind]) <= 130:
            raise ValueError('Warning thresholds must be below critical thresholds (20–130°C).')
    for key in ['interval','cleaning_days','sustain_seconds','cooling_seconds','rise_celsius']:
        if not math.isfinite(float(result[key])) or float(result[key]) <= 0:raise ValueError(f'{key} must be positive')
    result['interval']=max(5,float(result['interval']))
    return result

def number(path, divisor=1):
    try:
        value=float(Path(path).read_text())/divisor
        return value if math.isfinite(value) else None
    except (OSError, ValueError):return None

def collect(settings, cpu_load=None, root=Path('/sys/class/hwmon'), gpu=True):
    sensors=[];errors=[]
    for chip in sorted(root.glob('hwmon*')):
        try:name=(chip/'name').read_text().strip()
        except OSError:continue
        kind=('cpu' if name in ['coretemp','k10temp','zenpower'] else 'disk' if name in ['nvme','drivetemp'] else 'memory' if name=='jc42' else 'other')
        for source in sorted(chip.glob('temp*_input')):
            value=number(source,1000)
            if value is None or not -20 <= value <= 150:continue
            prefix=source.name.removesuffix('_input')
            try:label=(chip/(prefix+'_label')).read_text().strip()
            except OSError:label=prefix
            warning=float(settings['warning'][kind]);critical=float(settings['critical'][kind])
            hwcrit=number(chip/(prefix+'_crit'),1000);hwhigh=number(chip/(prefix+'_max'),1000)
            # Some DIMM/SSD drivers expose 0°C or 65261°C alarm limits.
            if hwcrit is not None and 40 <= hwcrit <= 130:critical=min(critical,hwcrit)
            if hwhigh is not None and 40 <= hwhigh < critical:warning=min(warning,hwhigh)
            warning=min(warning,critical-1)
            sensors.append(Sensor(f'{name}:{(chip/"device").resolve().name}:{prefix}',f'{name} · {label}',kind,value,warning,critical,cpu_load if kind=='cpu' else None))
    if gpu and shutil.which('nvidia-smi'):
        try:
            result=subprocess.run(['nvidia-smi','--query-gpu=index,temperature.gpu,utilization.gpu,fan.speed','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=8,check=True)
            for row in csv.reader(result.stdout.splitlines()):
                index,temp,load,fan=[v.strip() for v in row]
                sensors.append(Sensor('gpu:'+index,'NVIDIA GPU '+index,'gpu',float(temp),float(settings['warning']['gpu']),float(settings['critical']['gpu']),float(load),None if fan=='[N/A]' or fan=='N/A' else float(fan)))
        except (OSError,subprocess.SubprocessError,ValueError):errors.append('NVIDIA temperature query failed')
    return sensors,errors

def cpu_times(path=Path('/proc/stat')):
    try:
        values=[int(v) for v in path.read_text().splitlines()[0].split()[1:9]]
        return sum(values),values[3]+(values[4] if len(values)>4 else 0)
    except (OSError,ValueError,IndexError):return None

def load_between(before,after):
    if not before or not after or after[0]<=before[0]:return None
    return max(0,min(100,100*(1-(after[1]-before[1])/(after[0]-before[0]))))

class Alerts:
    def __init__(self,state):self.state=state;self.since={};self.notified={};self.seen=set();self.missing={}
    def evaluate(self,sensors,settings,now):
        events=[];active=set();present={s.key for s in sensors}
        def sustained(key,condition,delay):
            active.add(key)
            if not condition:self.since.pop(key,None);return False
            self.since.setdefault(key,now)
            return now-self.since[key]>=delay
        def event(key,title,body,critical=False):
            events.append({'key':key,'title':title,'body':body,'critical':critical})
        for s in sensors:
            hot=s.temperature>=s.warning;critical=s.temperature>=s.critical
            if critical or sustained(s.key+':hot',hot,settings['sustain_seconds']):
                event(s.key+':heat','High '+s.kind+' temperature',f'{s.name}: {s.temperature:.1f}°C (warning {s.warning:g}°C, critical {s.critical:g}°C). Check workload, airflow and cooling.',critical)
            idle=s.utilization is not None and s.utilization<20
            baselines=self.state.setdefault('baselines',{})
            entry=baselines.setdefault(s.key,[])
            baseline=statistics.median(entry) if len(entry)>=20 else None
            rising=idle and baseline is not None and s.temperature>=baseline+settings['rise_celsius']
            if sustained(s.key+':rise',rising,settings['cooling_seconds']):
                event(s.key+':cooling','Possible cooling issue',f'{s.name} is {s.temperature-baseline:.0f}°C above its learned low-load baseline. Check vents, fans and ambient temperature; inspect for dust. Sensors cannot confirm dust.')
            if idle and len(entry)<30 and not hot:entry.append(s.temperature)
        for key in self.seen-present:
            self.missing[key]=self.missing.get(key,0)+1
            if self.missing[key]>=3:event(key+':missing','Temperature sensor unavailable','A previously monitored sensor stopped responding: '+key)
        for key in present:self.missing.pop(key,None)
        self.seen|=present
        for key in set(self.since)-active:self.since.pop(key,None)
        started=self.state.setdefault('schedule_started',now)
        last=self.state.get('last_cleaned',started)
        if now-last>=settings['cleaning_days']*86400:
            event('cleaning','PC cleaning reminder',f'It has been {int((now-last)/86400)} days since the last recorded cleaning or the start of monitoring. Inspect vents and filters. This is a scheduled reminder, not dust detection.')
        return events
    def notify(self,events,now,sender):
        for event in events:
            interval=86400 if event['key']=='cleaning' else 300 if event['critical'] else 1800
            if now-self.notified.get(event['key'],-1e20)>=interval:
                if sender(event):self.notified[event['key']]=now

def notify(event):
    try:
        result=subprocess.run(['notify-send','--app-name=PC Temperature Guard','--urgency='+('critical' if event['critical'] else 'normal'),event['title'],event['body']],timeout=5)
        return result.returncode==0
    except (OSError,subprocess.SubprocessError):return False

def database():
    DATA.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(DATA/'history.sqlite3',timeout=5)
    db.execute('CREATE TABLE IF NOT EXISTS readings (timestamp REAL, name TEXT, kind TEXT, temperature REAL)')
    db.execute('CREATE INDEX IF NOT EXISTS readings_time ON readings(timestamp)');return db

def monitor():
    runtime=Path(os.environ.get('XDG_RUNTIME_DIR','/tmp'))/f'pc-temperature-guard-{os.getuid()}.lock'
    lock=runtime.open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    state=read_json(DATA/'state.json',{});engine=Alerts(state);previous=cpu_times();db=database();last_prune=0
    while True:
        now=time.time();settings_error=None
        try:settings=config()
        except (ValueError,TypeError) as exc:settings=DEFAULTS;settings_error=str(exc)
        # GUI sends cleaning acknowledgments separately, avoiding state-file races.
        cleaned=read_json(DATA/'cleaned.json',{}).get('timestamp',0)
        if cleaned>state.get('last_cleaned',0):state['last_cleaned']=cleaned;state['baselines']={}
        current=cpu_times();load=load_between(previous,current);previous=current
        sensors,errors=collect(settings,load)
        if settings_error:errors.append('Invalid configuration: '+settings_error)
        events=engine.evaluate(sensors,settings,now)
        if not sensors:events.append({'key':'no-sensors','title':'Temperature monitoring unavailable','body':'No usable temperature sensors could be read. Open PC Temperature Guard to check status.','critical':False})
        engine.notify(events,now,notify)
        write_json(DATA/'status.json',{'timestamp':now,'cpu_load':load,'sensors':[asdict(s) for s in sensors],'events':events,'errors':errors,'last_cleaned':state.get('last_cleaned'),'schedule_started':state.get('schedule_started',now)})
        write_json(DATA/'state.json',state)
        db.executemany('INSERT INTO readings VALUES (?,?,?,?)',[(now,s.name,s.kind,s.temperature) for s in sensors])
        if now-last_prune>3600:db.execute('DELETE FROM readings WHERE timestamp < ?',(now-7*86400,));last_prune=now
        db.commit();time.sleep(settings['interval'])

def gui():
    import tkinter as tk
    from tkinter import ttk,messagebox
    window=tk.Tk();window.title('PC Temperature Guard');window.geometry('900x650')
    title=ttk.Label(window,text='PC Temperature Guard',font=('',20,'bold'));title.pack(pady=12)
    status=ttk.Label(window,text='Loading…');status.pack()
    table=ttk.Treeview(window,columns=('sensor','temperature','limits','load'),show='headings',height=12)
    for key,label,width in [('sensor','Sensor',320),('temperature','Temperature',130),('limits','Warning / Critical',160),('load','Load / Fan',160)]:table.heading(key,text=label);table.column(key,width=width)
    table.tag_configure('critical',foreground='#b00020');table.tag_configure('warning',foreground='#986000');table.pack(fill='both',expand=True,padx=16,pady=10)
    detail=tk.StringVar();ttk.Label(window,textvariable=detail,wraplength=850,justify='left').pack(padx=16,anchor='w')
    clean=tk.StringVar();ttk.Label(window,textvariable=clean).pack(pady=8)
    ttk.Label(window,text='Dust cannot be detected by temperature sensors. Cooling alerts suggest an inspection.',wraplength=850).pack()
    row=ttk.Frame(window);row.pack(pady=14)
    def cleaned():
        write_json(DATA/'cleaned.json',{'timestamp':time.time()});messagebox.showinfo('Cleaning recorded','Cleaning recorded. The monitor will reset its low-load baseline on the next sample.')
    def test():
        if notify({'title':'PC Temperature Guard test','body':'Desktop notifications are working. This is a test, not a temperature warning.','critical':False}):messagebox.showinfo('Notification sent','A test notification was sent to your desktop.')
        else:messagebox.showerror('Notification failed','Check your desktop notification service.')
    def settings_dialog():
        settings=config();dialog=tk.Toplevel(window);dialog.title('Alert settings');fields={}
        for i,(key,label) in enumerate([('cleaning_days','Cleaning reminder (days)'),('interval','Sample interval (seconds)')]):
            ttk.Label(dialog,text=label).grid(row=i,column=0,padx=12,pady=8);entry=ttk.Entry(dialog);entry.insert(0,str(settings[key]));entry.grid(row=i,column=1);fields[key]=entry
        def save():
            try:
                for key,entry in fields.items():
                    value=float(entry.get())
                    if not math.isfinite(value) or value<=0:raise ValueError()
                    settings[key]=value
                settings['interval']=max(5,settings['interval']);write_json(CONFIG/'config.json',settings);dialog.destroy()
            except ValueError:messagebox.showerror('Invalid setting','Enter positive numbers.',parent=dialog)
        ttk.Button(dialog,text='Save',command=save).grid(row=3,column=0,columnspan=2,pady=12)
    for label,command in [('I cleaned the PC',cleaned),('Test notification',test),('Settings',settings_dialog)]:ttk.Button(row,text=label,command=command).pack(side='left',padx=6)
    def refresh():
        data=read_json(DATA/'status.json',{});age=time.time()-data.get('timestamp',0)
        try:interval=config()['interval']
        except (ValueError,TypeError):interval=DEFAULTS['interval']
        status.configure(text='Monitoring active · sampled every '+str(interval)+' seconds' if age<max(90,interval*3) else 'Monitor is not updating — check the login service')
        table.delete(*table.get_children())
        for s in data.get('sensors',[]):
            tag='critical' if s['temperature']>=s['critical'] else 'warning' if s['temperature']>=s['warning'] else ''
            load='—' if s.get('utilization') is None else f"{s['utilization']:.0f}% load"
            if s.get('fan') is not None:load+=f" / {s['fan']:.0f}% fan"
            table.insert('', 'end',values=(s['name'],f"{s['temperature']:.1f}°C",f"{s['warning']:g} / {s['critical']:g}°C",load),tags=(tag,))
        messages=[e['title']+': '+e['body'] for e in data.get('events',[])]+data.get('errors',[])
        detail.set('\n'.join(messages[:3]) if messages else 'No sustained temperature or cooling warnings detected.')
        last=data.get('last_cleaned');clean.set('Last cleaning recorded: '+time.strftime('%Y-%m-%d',time.localtime(last)) if last else 'No cleaning recorded yet; reminders count from when monitoring started.')
        window.after(3000,refresh)
    refresh();window.mainloop()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--monitor',action='store_true');parser.add_argument('--once',action='store_true');parser.add_argument('--notify-test',action='store_true');args=parser.parse_args()
    if args.monitor:monitor()
    elif args.once:print(json.dumps([asdict(s) for s in collect(config())[0]],indent=2))
    elif args.notify_test:raise SystemExit(0 if notify({'title':'PC Temperature Guard','body':'Desktop alerts are ready. This is a test notification.','critical':False}) else 1)
    else:gui()
