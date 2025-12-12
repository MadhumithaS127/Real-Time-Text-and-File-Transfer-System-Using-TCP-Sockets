# server.py
import socket
import threading
import json
import os

HOST = '0.0.0.0'
PORT = 5000

TYPE_LEN = 6
LEN_LEN = 10
HEADER_LEN = TYPE_LEN + LEN_LEN
METALEN_LEN = 10

users_file = 'users.json'
clients = []  # list of (sock, username)
clients_lock = threading.Lock()

def load_users():
    if not os.path.exists(users_file):
        default = {"alice": "1234", "bob": "abcd"}
        with open(users_file, 'w') as f:
            json.dump(default, f)
        return default
    with open(users_file, 'r') as f:
        return json.load(f)

def recv_all(sock, n):
    data = b''
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            return None
        data += chunk
    return data

def send_message(sock, mtype, payload_bytes):
    try:
        type_field = mtype.ljust(TYPE_LEN)[:TYPE_LEN].encode('utf-8')
        length_field = str(len(payload_bytes)).zfill(LEN_LEN).encode('utf-8')
        sock.sendall(type_field + length_field + payload_bytes)
    except Exception:
        # caller will handle cleanup
        pass

def broadcast(mtype, payload_bytes, exclude_sock=None):
    dead = []
    with clients_lock:
        for sock, uname in clients:
            if sock is exclude_sock:
                continue
            try:
                send_message(sock, mtype, payload_bytes)
            except Exception:
                dead.append(sock)
        # cleanup dead sockets
        for ds in dead:
            for i, (s, u) in enumerate(clients):
                if s is ds:
                    clients.pop(i)
                    break

def handle_client(client_sock, addr, users_db):
    username = None
    try:
        # Expect AUTH payload: "username:password"
        hdr = recv_all(client_sock, HEADER_LEN)
        if not hdr:
            client_sock.close()
            return
        mtype = hdr[:TYPE_LEN].decode('utf-8').strip()
        length = int(hdr[TYPE_LEN:HEADER_LEN].decode('utf-8'))
        payload = recv_all(client_sock, length)
        if payload is None:
            client_sock.close()
            return

        if mtype != 'AUTH':
            send_message(client_sock, 'SYS', b'Authentication required. Closing.')
            client_sock.close()
            return

        creds = payload.decode('utf-8', errors='ignore')
        if ':' not in creds:
            send_message(client_sock, 'SYS', b'Bad auth format. Closing.')
            client_sock.close()
            return
        username, password = creds.split(':', 1)
        username = username.strip()
        password = password.strip()

        if username in users_db and users_db[username] == password:
            send_message(client_sock, 'SYS', b'AUTH_OK')
            with clients_lock:
                clients.append((client_sock, username))
            join_msg = f"*** {username} joined the chat ***\n".encode('utf-8')
            broadcast('SYS', join_msg, exclude_sock=None)
            print(f"[{addr}] {username} connected.")
        else:
            send_message(client_sock, 'SYS', b'AUTH_FAIL')
            client_sock.close()
            return

        # Main loop
        while True:
            hdr = recv_all(client_sock, HEADER_LEN)
            if not hdr:
                break
            mtype = hdr[:TYPE_LEN].decode('utf-8').strip()
            length = int(hdr[TYPE_LEN:HEADER_LEN].decode('utf-8'))
            payload = recv_all(client_sock, length)
            if payload is None:
                break

            if mtype == 'TEXT':
                text = payload.decode('utf-8', errors='ignore').strip()
                if text == '/quit':
                    break
                message = f"[{username}] {text}\n".encode('utf-8')
                broadcast('TEXT', message, exclude_sock=None)

            elif mtype in ('VOICE', 'FILE'):
                # payload = METALEN(10) + metadata_json + file_bytes
                if len(payload) < METALEN_LEN:
                    continue
                meta_len = int(payload[:METALEN_LEN].decode('utf-8'))
                if len(payload) < METALEN_LEN + meta_len:
                    # not enough data (shouldn't happen with recv_all), skip
                    continue
                meta_bytes = payload[METALEN_LEN:METALEN_LEN + meta_len]
                file_bytes = payload[METALEN_LEN + meta_len:]
                try:
                    meta = json.loads(meta_bytes.decode('utf-8', errors='ignore'))
                except Exception:
                    meta = {}
                # Announce
                if mtype == 'VOICE':
                    tag = f"*** Voice message from {username} ***\n".encode('utf-8')
                    broadcast('SYS', tag, exclude_sock=None)
                    # Broadcast the voice payload to others
                    broadcast('VOICE', payload, exclude_sock=None)
                else:  # FILE
                    filetype = meta.get('filetype', 'FILE').upper()
                    fname = meta.get('filename', 'file.bin')
                    tag = f"*** {filetype} from {username}: {fname} ***\n".encode('utf-8')
                    broadcast('SYS', tag, exclude_sock=None)
                    broadcast('FILE', payload, exclude_sock=None)
            else:
                # Unknown type: ignore
                continue

    except Exception as e:
        print("Client handler error:", e)
    finally:
        with clients_lock:
            for i, (s, u) in enumerate(clients):
                if s is client_sock:
                    clients.pop(i)
                    left_user = u
                    break
            else:
                left_user = None
        if left_user:
            leave_msg = f"*** {left_user} left the chat ***\n".encode('utf-8')
            broadcast('SYS', leave_msg, exclude_sock=None)
            print(f"{left_user} disconnected.")
        try:
            client_sock.close()
        except:
            pass

def main():
    users_db = load_users()
    print("Loaded users:", ", ".join(users_db.keys()))
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(100)
    print(f"Server listening on {HOST}:{PORT}")

    try:
        while True:
            client_sock, addr = srv.accept()
            t = threading.Thread(target=handle_client, args=(client_sock, addr, users_db), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("Shutting down server...")
    finally:
        with clients_lock:
            for sock, _ in clients:
                try:
                    send_message(sock, 'SYS', b'Server is shutting down')
                    sock.close()
                except:
                    pass
        srv.close()

if __name__ == '__main__':
    main()
