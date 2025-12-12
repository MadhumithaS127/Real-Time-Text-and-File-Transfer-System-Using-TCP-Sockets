# client.py
import socket
import threading
import json
import os
import io
import sounddevice as sd
import soundfile as sf

SERVER_HOST = '127.0.0.1'   # change to server IP on LAN
SERVER_PORT = 5000

TYPE_LEN = 6
LEN_LEN = 10
HEADER_LEN = TYPE_LEN + LEN_LEN
METALEN_LEN = 10

def recv_all(sock, n):
    data = b''
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            return None
        data += chunk
    return data

def build_header(mtype, payload_length):
    type_field = mtype.ljust(TYPE_LEN)[:TYPE_LEN].encode('utf-8')
    length_field = str(payload_length).zfill(LEN_LEN).encode('utf-8')
    return type_field + length_field

def send_message(sock, mtype, payload_bytes):
    sock.sendall(build_header(mtype, len(payload_bytes)) + payload_bytes)

def receive_loop(sock):
    try:
        while True:
            hdr = recv_all(sock, HEADER_LEN)
            if not hdr:
                print("*** Disconnected from server ***")
                break
            mtype = hdr[:TYPE_LEN].decode('utf-8').strip()
            length = int(hdr[TYPE_LEN:HEADER_LEN].decode('utf-8'))
            payload = recv_all(sock, length)
            if payload is None:
                print("*** Disconnected from server ***")
                break

            if mtype == 'TEXT':
                print(payload.decode('utf-8', errors='ignore'), end='')

            elif mtype == 'SYS':
                text = payload.decode('utf-8', errors='ignore')
                print(text, end='')

            elif mtype == 'VOICE':
                # payload layout: METALEN_LEN + meta_json + file_bytes
                meta_len = int(payload[:METALEN_LEN].decode('utf-8'))
                meta_bytes = payload[METALEN_LEN:METALEN_LEN+meta_len]
                file_bytes = payload[METALEN_LEN+meta_len:]
                try:
                    meta = json.loads(meta_bytes.decode('utf-8', errors='ignore'))
                except Exception:
                    meta = {}
                fname = meta.get('filename', 'voice.wav')
                # save to temp and play
                tmp = f"received_{fname}"
                with open(tmp, 'wb') as f:
                    f.write(file_bytes)
                print(f"[VOICE] Playing voice message from {meta.get('username','unknown')} ...")
                try:
                    data, sr = sf.read(tmp, dtype='float32')
                    sd.play(data, sr)
                    sd.wait()
                    print("*** Finished playing ***")
                except Exception as e:
                    print("Failed to play voice:", e)

            elif mtype == 'FILE':
                meta_len = int(payload[:METALEN_LEN].decode('utf-8'))
                meta_bytes = payload[METALEN_LEN:METALEN_LEN+meta_len]
                file_bytes = payload[METALEN_LEN+meta_len:]
                try:
                    meta = json.loads(meta_bytes.decode('utf-8', errors='ignore'))
                except Exception:
                    meta = {}
                fname = meta.get('filename', 'file.bin')
                ftype = meta.get('filetype', 'FILE').upper()
                save_name = f"received_{fname}"
                with open(save_name, 'wb') as f:
                    f.write(file_bytes)
                if ftype == 'IMAGE':
                    print(f"[IMAGE] Received image from {meta.get('username','unknown')}: saved -> {save_name}")
                else:
                    print(f"[FILE] Received {ftype} from {meta.get('username','unknown')}: saved -> {save_name}")
            else:
                # unknown
                pass
    except Exception as e:
        print("Receive error:", e)
    finally:
        try:
            sock.close()
        except:
            pass
        os._exit(0)

def record_voice_bytes(duration=4, samplerate=44100, channels=1):
    print(f"Recording for {duration} seconds...")
    rec = sd.rec(int(duration * samplerate), samplerate=samplerate, channels=channels, dtype='float32')
    sd.wait()
    bio = io.BytesIO()
    sf.write(bio, rec, samplerate, format='WAV')
    bio.seek(0)
    return bio.read(), os.path.getsize(bio.name) if hasattr(bio, 'name') else len(bio.getvalue())

def send_voice(sock, username, seconds=4):
    try:
        rec_bytes_io = io.BytesIO()
        fs = 44100
        rec = sd.rec(int(seconds * fs), samplerate=fs, channels=1, dtype='float32')
        sd.wait()
        sf.write(rec_bytes_io, rec, fs, format='WAV')
        rec_bytes = rec_bytes_io.getvalue()
        # metadata
        meta = {"username": username, "filename": f"{username}_voice.wav", "filetype": "VOICE"}
        meta_bytes = json.dumps(meta).encode('utf-8')
        payload = str(len(meta_bytes)).zfill(METALEN_LEN).encode('utf-8') + meta_bytes + rec_bytes
        send_message(sock, 'VOICE', payload)
        print("*** Voice message sent ***")
    except Exception as e:
        print("Failed to send voice:", e)

def send_file(sock, username, path, ftype):
    if not os.path.exists(path):
        print("File not found:", path)
        return
    fname = os.path.basename(path)
    try:
        with open(path, 'rb') as f:
            file_bytes = f.read()
        meta = {"username": username, "filename": fname, "filetype": ftype.upper()}
        meta_bytes = json.dumps(meta).encode('utf-8')
        payload = str(len(meta_bytes)).zfill(METALEN_LEN).encode('utf-8') + meta_bytes + file_bytes
        send_message(sock, 'FILE', payload)
        print(f"*** {ftype.upper()} sent: {fname} ***")
    except Exception as e:
        print("Failed to send file:", e)

def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((SERVER_HOST, SERVER_PORT))
    except Exception as e:
        print("Connection error:", e)
        return

    # Authentication
    username = input("Username: ").strip()
    password = input("Password: ").strip()
    auth_payload = f"{username}:{password}".encode('utf-8')
    send_message(sock, 'AUTH', auth_payload)
    # wait for server response
    hdr = recv_all(sock, HEADER_LEN)
    if not hdr:
        print("No response from server.")
        sock.close()
        return
    mtype = hdr[:TYPE_LEN].decode('utf-8').strip()
    length = int(hdr[TYPE_LEN:HEADER_LEN].decode('utf-8'))
    payload = recv_all(sock, length)
    if payload is None:
        print("No response from server.")
        sock.close()
        return
    text = payload.decode('utf-8', errors='ignore')
    if mtype == 'SYS' and text == 'AUTH_OK':
        print("*** Authentication successful. Joined chat. ***")
    else:
        print("*** Authentication failed or server error:", text)
        sock.close()
        return

    # start receiver thread
    t = threading.Thread(target=receive_loop, args=(sock,), daemon=True)
    t.start()

    print("Commands:")
    print(" - Type messages and press Enter to send")
    print(" - /quit               -> exit")
    print(" - /voice [seconds]    -> record and send voice (default 4s)")
    print(" - /image <path>       -> send image file (jpg/png)")
    print(" - /pdf <path>         -> send pdf file")

    try:
        while True:
            line = input()
            if not line:
                continue
            parts = line.strip().split(maxsplit=1)
            cmd = parts[0]
            if cmd == '/quit':
                send_message(sock, 'TEXT', b'/quit')
                break
            elif cmd == '/voice':
                secs = 4
                if len(parts) > 1:
                    try:
                        secs = int(parts[1])
                    except:
                        secs = 4
                send_voice(sock, username, seconds=secs)
            elif cmd == '/image':
                if len(parts) < 2:
                    print("Usage: /image <path>")
                    continue
                path = parts[1].strip('"')
                send_file(sock, username, path, 'IMAGE')
            elif cmd == '/pdf':
                if len(parts) < 2:
                    print("Usage: /pdf <path>")
                    continue
                path = parts[1].strip('"')
                send_file(sock, username, path, 'PDF')
            else:
                # send text
                send_message(sock, 'TEXT', line.encode('utf-8'))
    except KeyboardInterrupt:
        try:
            send_message(sock, 'TEXT', b'/quit')
        except:
            pass
    finally:
        try:
            sock.close()
        except:
            pass
        print("Disconnected.")

if __name__ == '__main__':
    main()
