import os
import logging
from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('myanydesk-relay')

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'myanydesk-cloud-secret-key-2026')

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    max_http_buffer_size=25 * 1024 * 1024,
    ping_timeout=30,
    ping_interval=10
)

# Active connected users:
# { id: { 'sid': sid, 'password': pw, 'unattended': bool, 'unattended_pw': str, 'resolution': res, 'partners': set() } }
users = {}

@app.route('/')
def home():
    return """
    <html>
        <head><title>MyAnyDesk 24/7 Cloud Relay</title></head>
        <body style="font-family: Arial, sans-serif; background: #1e1e2e; color: #cdd6f4; text-align: center; padding-top: 60px;">
            <h1 style="color: #a6e3a1;">&#9989; MyAnyDesk 24/7 Cloud Relay is Live!</h1>
            <p style="font-size: 18px; color: #bac2de;">Connected Devices Online: <b>""" + str(len(users)) + """</b></p>
            <p style="color: #6c7086; font-size: 14px;">This server operates 24/7 across the globe without requiring any local PC host.</p>
        </body>
    </html>
    """

@app.route('/status')
def status():
    return jsonify({
        "status": "online",
        "active_devices": len(users),
        "users": list(users.keys())
    })

@socketio.on('register')
def handle_register(data):
    uid = str(data.get('id', '')).strip()
    pw = str(data.get('password', '')).strip()
    unattended = bool(data.get('unattended', False))
    unattended_pw = str(data.get('unattended_pw', '')).strip()
    res = data.get('resolution', [1920, 1080])

    if uid:
        users[uid] = {
            'sid': request.sid,
            'password': pw,
            'unattended': unattended,
            'unattended_pw': unattended_pw,
            'partners': set(),
            'resolution': res
        }
        logger.info(f"[+] Device Registered: {uid} (SID: {request.sid})")
        emit('registered', {'id': uid, 'status': 'success'})

@socketio.on('call')
def handle_call(data):
    caller = str(data.get('caller', '')).strip()
    target = str(data.get('target', '')).strip()
    entered_pw = str(data.get('password', '')).strip()

    if target not in users:
        emit('call_failed', {'msg': f'Device ID {target} is offline or not found.'}, room=request.sid)
        return

    t_info = users[target]

    # Check 1: UNATTENDED ACCESS MATCH
    if t_info['unattended'] and t_info['unattended_pw'] and entered_pw == t_info['unattended_pw']:
        caller_sid = request.sid
        target_sid = t_info['sid']

        users[caller]['partners'].add(target)
        users[target]['partners'].add(caller)

        host_res = t_info.get('resolution', [1920, 1080])
        emit('connected', {'partner': target, 'role': 'client', 'resolution': host_res}, room=caller_sid)
        emit('connected_unattended', {'partner': caller, 'role': 'host'}, room=target_sid)
        logger.info(f"[*] Unattended Session Established: {caller} <---> {target}")
        return

    # Check 2: Session Password check
    if t_info['password'] and t_info['password'] != entered_pw:
        emit('call_failed', {'msg': 'Incorrect Password! Check session or unattended password.'}, room=request.sid)
        return

    # Standard confirmation prompt to host
    emit('incoming', {'caller': caller}, room=t_info['sid'])

@socketio.on('accept')
def handle_accept(data):
    host = str(data.get('host', '')).strip()
    caller = str(data.get('caller', '')).strip()
    host_res = data.get('resolution', [1920, 1080])

    if caller in users and host in users:
        users[caller]['partners'].add(host)
        users[host]['partners'].add(caller)
        users[host]['resolution'] = host_res

        emit('connected', {'partner': host, 'role': 'client', 'resolution': host_res}, room=users[caller]['sid'])
        emit('connected', {'partner': caller, 'role': 'host'}, room=users[host]['sid'])
        logger.info(f"[*] Session Accepted: {caller} <---> {host}")

@socketio.on('reject')
def handle_reject(data):
    caller = str(data.get('caller', '')).strip()
    if caller in users:
        emit('call_failed', {'msg': 'Connection was rejected by remote device.'}, room=users[caller]['sid'])

@socketio.on('stream')
def handle_stream(data):
    target = data.get('target')
    if target and target in users:
        emit('frame', {'from': data.get('from'), 'img': data.get('img')}, room=users[target]['sid'])

@socketio.on('control')
def handle_control(data):
    target = data.get('target')
    if target and target in users:
        emit('control_event', data, room=users[target]['sid'])

@socketio.on('clipboard')
def handle_clipboard(data):
    target = data.get('target')
    if target and target in users:
        emit('clipboard_event', data, room=users[target]['sid'])

@socketio.on('disconnect_partner')
def handle_disconnect_partner(data):
    sender_id = data.get('id')
    target_id = data.get('partner')

    if sender_id in users:
        users[sender_id]['partners'].discard(target_id)
    if target_id in users:
        users[target_id]['partners'].discard(sender_id)
        emit('partner_disconnected', {'partner': sender_id}, room=users[target_id]['sid'])

@socketio.on('heartbeat')
def handle_heartbeat(data):
    uid = data.get('id')
    if uid and uid in users:
        users[uid]['sid'] = request.sid

@socketio.on('disconnect')
def handle_disconnect():
    for uid, info in list(users.items()):
        if info['sid'] == request.sid:
            for pid in list(info.get('partners', set())):
                if pid in users:
                    users[pid]['partners'].discard(uid)
                    emit('partner_disconnected', {'partner': uid}, room=users[pid]['sid'])
            del users[uid]
            logger.info(f"[-] Device Disconnected: {uid}")
            break

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting server on port {port}...")
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)
