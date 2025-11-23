from flask import Flask, request, jsonify, render_template, send_from_directory, redirect, session
import json
import time
import requests
import zhzj
import os
import sqlite3
import smtplib
from email.mime.text import MIMEText

app = Flask(__name__)
app.url_map.strict_slashes = False
app.secret_key = os.environ.get('SECRET_KEY', 'dev')

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(__file__), 'site.db'))
HOME_DOMAIN = os.environ.get('HOME_DOMAIN', 'mikusignal.top')
LOGIN_DOMAIN = os.environ.get('LOGIN_DOMAIN', 'login.mikusignal.top')
# Default to DEV locally; set environment PROD=1 to switch to production cookie policy
DEV = os.environ.get('PROD') != '1'
app.config.update(
    SESSION_COOKIE_NAME='session',
    SESSION_COOKIE_DOMAIN=(None if DEV else f'.{HOME_DOMAIN}'),
    SESSION_COOKIE_SAMESITE=('Lax' if DEV else 'None'),
    SESSION_COOKIE_SECURE=(False if DEV else True),
)

def _hash_pw(pw):
    import hashlib
    return hashlib.sha256(('salt:' + pw).encode('utf-8')).hexdigest()

def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def db_init():
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL,
      email TEXT,
      email_verified INTEGER DEFAULT 0,
      email_token TEXT,
      email_token_expire INTEGER,
      role TEXT NOT NULL DEFAULT 'student',
      created_at INTEGER NOT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS visits (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts INTEGER NOT NULL,
      ip TEXT,
      ua TEXT,
      ref TEXT,
      coords TEXT,
      ip_loc TEXT,
      geo INTEGER,
      site TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS task_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts INTEGER NOT NULL,
      uid INTEGER,
      username TEXT,
      student_no TEXT,
      ip TEXT,
      ip_loc TEXT,
      site TEXT,
      action TEXT,
      course_id TEXT,
      course_info_id TEXT,
      class_id TEXT,
      section_id TEXT,
      ids TEXT,
      study_time INTEGER,
      success INTEGER
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
      key TEXT PRIMARY KEY,
      value TEXT
    )
    """)
    # migrate users table if columns missing
    try:
        cur.execute('PRAGMA table_info(users)')
        cols = [r[1] for r in cur.fetchall()]
        if 'email' not in cols:
            cur.execute('ALTER TABLE users ADD COLUMN email TEXT')
        if 'email_verified' not in cols:
            cur.execute('ALTER TABLE users ADD COLUMN email_verified INTEGER DEFAULT 0')
        if 'email_token' not in cols:
            cur.execute('ALTER TABLE users ADD COLUMN email_token TEXT')
        if 'email_token_expire' not in cols:
            cur.execute('ALTER TABLE users ADD COLUMN email_token_expire INTEGER')
        if 'role' not in cols:
            cur.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'student'")
        if 'student_no' not in cols:
            cur.execute('ALTER TABLE users ADD COLUMN student_no TEXT')
        if 'nickname' not in cols:
            cur.execute('ALTER TABLE users ADD COLUMN nickname TEXT')
        if 'signature' not in cols:
            cur.execute('ALTER TABLE users ADD COLUMN signature TEXT')
        if 'reset_token' not in cols:
            cur.execute('ALTER TABLE users ADD COLUMN reset_token TEXT')
        if 'reset_token_expire' not in cols:
            cur.execute('ALTER TABLE users ADD COLUMN reset_token_expire INTEGER')
        if 'school' not in cols:
            cur.execute('ALTER TABLE users ADD COLUMN school TEXT')
    except Exception:
        pass
    cur.execute('SELECT id, role FROM users WHERE username=?', ('admin',))
    row = cur.fetchone()
    if row:
        if (row['role'] or '') != 'admin':
            cur.execute('UPDATE users SET role=? WHERE id=?', ('admin', row['id']))
    else:
        cur.execute('INSERT INTO users (username, password_hash, email, email_verified, role, created_at) VALUES (?, ?, ?, ?, ?, ?)', ('admin', _hash_pw('123456'), 'admin@local', 1, 'admin', int(time.time())))
    conn.commit()
    conn.close()

db_init()

@app.route('/')
def index():
    return send_from_directory('首页', 'index.html')

@app.route('/img/<path:filename>')
def serve_img(filename):
    return send_from_directory('img', filename)

@app.route('/首页/')
def serve_home_index():
    return send_from_directory('首页', 'index.html')

@app.route('/首页/<path:filename>')
def serve_home_static(filename):
    return send_from_directory('首页', filename)

@app.after_request
def add_security_headers(resp):
    csp = "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com data:; script-src 'self'; connect-src 'self' https://ipapi.co https://" + HOME_DOMAIN + " https://" + LOGIN_DOMAIN + "; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    resp.headers['Content-Security-Policy'] = csp
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['X-Frame-Options'] = 'DENY'
    resp.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    resp.headers['Permissions-Policy'] = 'geolocation=(self)'
    origin = request.headers.get('Origin')
    allowed = {f'https://{HOME_DOMAIN}', f'https://{LOGIN_DOMAIN}'}
    if origin in allowed:
        resp.headers['Access-Control-Allow-Origin'] = origin
        resp.headers['Access-Control-Allow-Credentials'] = 'true'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return resp

@app.before_request
def handle_options():
    if request.method == 'OPTIONS':
        resp = app.make_response(('', 204))
        origin = request.headers.get('Origin')
        allowed = {f'https://{HOME_DOMAIN}', f'https://{LOGIN_DOMAIN}'}
        if origin in allowed:
            resp.headers['Access-Control-Allow-Origin'] = origin
            resp.headers['Access-Control-Allow-Credentials'] = 'true'
            resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
            resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        return resp

def _client_ip():
    ip = request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
    if not ip:
        ip = request.remote_addr or ''
    return ip

def _geo_ip(ip):
    try:
        r = requests.get(f'https://ipapi.co/{ip}/json/', timeout=5)
        if r.ok:
            j = r.json()
            return {
                'city': j.get('city'),
                'region': j.get('region'),
                'country': j.get('country_name'),
                'latitude': j.get('latitude'),
                'longitude': j.get('longitude')
            }
    except Exception:
        pass
    return {}

def _log_task_event(payload):
    try:
        conn = db_conn(); cur = conn.cursor()
        cur.execute('SELECT username, student_no FROM users WHERE id=?', (payload.get('uid'),))
        ru = cur.fetchone()
        uname = payload.get('username') or (ru['username'] if ru else None)
        sno = (ru['student_no'] if ru else None)
        cur.execute(
            'INSERT INTO task_events (ts, uid, username, student_no, ip, ip_loc, site, action, course_id, course_info_id, class_id, section_id, ids, study_time, success) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                int(time.time()),
                payload.get('uid'),
                uname,
                sno,
                payload.get('ip'),
                json.dumps(payload.get('ip_loc') or {}, ensure_ascii=False),
                payload.get('site') or 'blue',
                payload.get('action'),
                payload.get('course_id'),
                payload.get('course_info_id'),
                payload.get('class_id'),
                payload.get('section_id'),
                json.dumps(payload.get('ids') or [], ensure_ascii=False),
                int(payload.get('study_time') or 0),
                1 if payload.get('success') else 0
            )
        )
        conn.commit(); conn.close()
    except Exception:
        pass

@app.route('/api/visit', methods=['POST'])
def api_visit():
    ip = _client_ip()
    data = request.get_json() or {}
    ua = data.get('ua') or request.headers.get('User-Agent')
    ref = data.get('ref') or request.headers.get('Referer')
    coords = data.get('coords') or {}
    geo_ip = _geo_ip(ip)
    site = data.get('site') or 'home'
    rec = {
        'ts': int(time.time()),
        'ip': ip,
        'ua': ua,
        'ref': ref,
        'coords': coords,
        'ip_loc': geo_ip,
        'geo': bool(data.get('geo')),
        'site': site
    }
    try:
        with open('visits.log', 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    except Exception:
        pass
    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO visits (ts, ip, ua, ref, coords, ip_loc, geo, site) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (rec['ts'], rec['ip'], rec['ua'], rec['ref'], json.dumps(rec['coords'], ensure_ascii=False), json.dumps(rec['ip_loc'], ensure_ascii=False), 1 if rec['geo'] else 0, rec['site'])
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
    return jsonify({'ok': True})

@app.route('/api/visits', methods=['GET'])
def api_visits():
    items = []
    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute('SELECT ts, ip, ua, ref, coords, ip_loc, geo, site FROM visits ORDER BY id DESC LIMIT 200')
        rows = cur.fetchall()
        for r in rows:
            item = {
                'ts': r['ts'],
                'ip': r['ip'],
                'ua': r['ua'],
                'ref': r['ref'],
                'coords': json.loads(r['coords']) if r['coords'] else {},
                'ip_loc': json.loads(r['ip_loc']) if r['ip_loc'] else {},
                'geo': bool(r['geo']),
                'site': r['site']
            }
            items.append(item)
        conn.close()
    except Exception:
        pass
    return jsonify({'items': items})

 

def _smtp_config():
    cfg = {}
    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute('SELECT value FROM settings WHERE key=?', ('smtp',))
        r = cur.fetchone()
        conn.close()
        if r and r['value']:
            cfg = json.loads(r['value'])
    except Exception:
        pass
    return cfg

def _templates_config():
    cfg = {}
    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute('SELECT value FROM settings WHERE key=?', ('email_templates',))
        r = cur.fetchone()
        conn.close()
        if r and r['value']:
            cfg = json.loads(r['value'])
    except Exception:
        pass
    return cfg

def _render_template(html, mapping):
    s = html or ''
    for k, v in (mapping or {}).items():
        s = s.replace('{' + k + '}', str(v))
    return s

def _send_mail(to_email, subject, body_html):
    cfg = _smtp_config()
    host = cfg.get('host')
    port = int(cfg.get('port') or 0)
    user = cfg.get('user')
    password = cfg.get('password')
    from_addr = cfg.get('from') or user
    use_ssl = bool(cfg.get('ssl'))
    if not host or not port or not user or not password or not from_addr:
        return False
    msg = MIMEText(body_html, 'html', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = from_addr
    msg['To'] = to_email
    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port)
        else:
            server = smtplib.SMTP(host, port)
            server.starttls()
        server.login(user, password)
        server.sendmail(from_addr, [to_email], msg.as_string())
        server.quit()
        return True
    except Exception:
        return False

def _send_mail_cfg(cfg, to_email, subject, body_html):
    host = cfg.get('host')
    port = int(cfg.get('port') or 0)
    user = cfg.get('user')
    password = cfg.get('password')
    from_addr = cfg.get('from') or user
    use_ssl = bool(cfg.get('ssl'))
    if not host or not port or not user or not password or not from_addr:
        return False
    msg = MIMEText(body_html, 'html', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = from_addr
    msg['To'] = to_email
    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port)
        else:
            server = smtplib.SMTP(host, port)
            server.starttls()
        server.login(user, password)
        server.sendmail(from_addr, [to_email], msg.as_string())
        server.quit()
        return True
    except Exception:
        return False

@app.route('/api/auth/register', methods=['POST'])
def api_register():
    data = request.get_json() or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    email = (data.get('email') or '').strip()
    student_no = (data.get('student_no') or '').strip()
    school = (data.get('school') or '').strip()
    if not username or not password or not email or not student_no or not school:
        return jsonify({'error': 'invalid_params'}), 400
    cfg = _smtp_config()
    if not cfg:
        return jsonify({'error': 'smtp_not_configured'}), 503
    token = _hash_pw(username + ':' + str(int(time.time())))
    expire = int(time.time()) + 3600
    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute('INSERT INTO users (username, password_hash, email, email_verified, email_token, email_token_expire, role, created_at, student_no, school) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (username, _hash_pw(password), email, 0, token, expire, 'student', int(time.time()), student_no, school))
        conn.commit()
        conn.close()
    except sqlite3.IntegrityError:
        return jsonify({'error': 'user_exists'}), 409
    link = request.host_url.rstrip('/') + '/api/auth/verify?token=' + token
    tmpls = _templates_config()
    reg_subj = (tmpls.get('reg') or {}).get('subject') or '邮箱验证'
    reg_html_tpl = (tmpls.get('reg') or {}).get('html') or '<div>请点击链接完成邮箱验证（1小时内有效）：<a href="{verify_link}">{verify_link}</a></div>'
    body = _render_template(reg_html_tpl, {'username': username, 'verify_link': link})
    ok = _send_mail(email, reg_subj, body)
    if not ok:
        return jsonify({'error': 'smtp_send_failed'}), 502
    return jsonify({'ok': True})

@app.route('/api/auth/login', methods=['POST'])
def api_login():
    data = request.get_json() or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    if not username or not password:
        return jsonify({'error': 'invalid_params'}), 400
    conn = db_conn()
    cur = conn.cursor()
    cur.execute('SELECT id, password_hash, role, email_verified FROM users WHERE username=?', (username,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'not_found'}), 404
    if row['password_hash'] != _hash_pw(password):
        return jsonify({'error': 'bad_password'}), 403
    if row['role'] != 'admin' and int(row['email_verified'] or 0) != 1:
        return jsonify({'error': 'email_unverified'}), 403
    session['uid'] = row['id']
    session['username'] = username
    session['role'] = row['role']
    return jsonify({'ok': True, 'username': username})

@app.route('/api/me', methods=['GET'])
def api_me():
    if 'uid' in session:
        # fetch email_verified
        ev = 1
        try:
            conn = db_conn(); cur = conn.cursor(); cur.execute('SELECT email_verified FROM users WHERE id=?', (session.get('uid'),)); row = cur.fetchone(); conn.close(); ev = int(row['email_verified'] or 0)
        except Exception:
            ev = 1
        return jsonify({'ok': True, 'username': session.get('username'), 'role': session.get('role'), 'is_admin': session.get('role') == 'admin', 'email_verified': ev == 1})
    return jsonify({'ok': False}), 401

@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/auth/verify', methods=['GET'])
def api_verify():
    token = request.args.get('token') or ''
    if not token:
        return jsonify({'error': 'invalid_token'}), 400
    conn = db_conn()
    cur = conn.cursor()
    cur.execute('SELECT id, email_token_expire FROM users WHERE email_token=?', (token,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'invalid_token'}), 404
    if int(row['email_token_expire'] or 0) < int(time.time()):
        conn.close()
        return jsonify({'error': 'token_expired'}), 410
    cur.execute('UPDATE users SET email_verified=1, email_token=NULL, email_token_expire=NULL WHERE id=?', (row['id'],))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

def _require_admin():
    return ('uid' in session) and (session.get('role') == 'admin')

def _require_student():
    return ('uid' in session) and (session.get('role') == 'student')

@app.route('/api/admin/users', methods=['GET'])
def api_admin_users():
    if not _require_admin():
        return jsonify({'error': 'unauthorized'}), 401
    conn = db_conn()
    cur = conn.cursor()
    cur.execute('SELECT id, username, email, email_verified, role, created_at, student_no, school FROM users ORDER BY id DESC LIMIT 500')
    rows = cur.fetchall()
    conn.close()
    items = []
    for r in rows:
        items.append({
            'id': r['id'], 'username': r['username'], 'email': r['email'], 'email_verified': int(r['email_verified'] or 0) == 1, 'role': r['role'], 'created_at': r['created_at'], 'student_no': r['student_no'], 'school': r['school']
        })
    return jsonify({'items': items})

@app.route('/api/admin/account', methods=['GET'])
def api_admin_account():
    if not _require_admin():
        return jsonify({'error': 'unauthorized'}), 401
    conn = db_conn(); cur = conn.cursor()
    cur.execute('SELECT username, email FROM users WHERE id=?', (session.get('uid'),))
    row = cur.fetchone(); conn.close()
    return jsonify({'username': row['username'] if row else '', 'email': row['email'] if row else ''})

@app.route('/api/admin/account/update_password', methods=['POST'])
def api_admin_account_update_password():
    if not _require_admin():
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json() or {}
    new_password = data.get('new_password') or ''
    if not new_password:
        return jsonify({'error': 'invalid_params'}), 400
    try:
        conn = db_conn(); cur = conn.cursor()
        cur.execute('UPDATE users SET password_hash=? WHERE id=?', (_hash_pw(new_password), session.get('uid')))
        conn.commit(); conn.close()
        return jsonify({'ok': True})
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return jsonify({'error': 'store_failed'}), 500

@app.route('/api/admin/account/update_email', methods=['POST'])
def api_admin_account_update_email():
    if not _require_admin():
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json() or {}
    email = (data.get('email') or '').strip()
    if not email:
        return jsonify({'error': 'invalid_params'}), 400
    try:
        conn = db_conn(); cur = conn.cursor()
        cur.execute('UPDATE users SET email=?, email_verified=? WHERE id=?', (email, 1, session.get('uid')))
        conn.commit(); conn.close()
        return jsonify({'ok': True})
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return jsonify({'error': 'store_failed'}), 500

@app.route('/api/admin/user/verify', methods=['POST'])
def api_admin_user_verify():
    if not _require_admin():
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json() or {}
    uid = int(data.get('user_id') or 0)
    conn = db_conn()
    cur = conn.cursor()
    cur.execute('UPDATE users SET email_verified=1, email_token=NULL, email_token_expire=NULL WHERE id=?', (uid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/admin/user/delete', methods=['POST'])
def api_admin_user_delete():
    if not _require_admin():
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json() or {}
    uid = int(data.get('user_id') or 0)
    conn = db_conn()
    cur = conn.cursor()
    cur.execute('DELETE FROM users WHERE id=? AND role!="admin"', (uid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/admin/smtp', methods=['GET'])
def api_admin_smtp():
    if not _require_admin():
        return jsonify({'error': 'unauthorized'}), 401
    return jsonify({'cfg': _smtp_config()})

@app.route('/api/admin/smtp/update', methods=['POST'])
def api_admin_smtp_update():
    if not _require_admin():
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json() or {}
    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', ('smtp', json.dumps(data, ensure_ascii=False)))
        conn.commit()
        conn.close()
    except Exception:
        return jsonify({'error': 'store_failed'}), 500
    return jsonify({'ok': True})

@app.route('/api/admin/smtp/test', methods=['POST'])
def api_admin_smtp_test():
    if not _require_admin():
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json() or {}
    to_email = (data.get('to') or '').strip()
    cfg = data.get('cfg') or _smtp_config()
    if not to_email:
        return jsonify({'error': 'invalid_params'}), 400
    need = ['host', 'port', 'user', 'password']
    if not all(cfg.get(k) for k in need):
        return jsonify({'error': 'smtp_not_configured'}), 503
    ok = _send_mail_cfg(cfg, to_email, 'SMTP 测试邮件', '<div>这是一封测试邮件</div>')
    if not ok:
        return jsonify({'error': 'smtp_send_failed'}), 502
    return jsonify({'ok': True})

@app.route('/api/admin/templates', methods=['GET'])
def api_admin_templates():
    if not _require_admin():
        return jsonify({'error': 'unauthorized'}), 401
    return jsonify({'templates': _templates_config()})

@app.route('/api/admin/templates/update', methods=['POST'])
def api_admin_templates_update():
    if not _require_admin():
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json() or {}
    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', ('email_templates', json.dumps(data, ensure_ascii=False)))
        conn.commit()
        conn.close()
    except Exception:
        return jsonify({'error': 'store_failed'}), 500
    return jsonify({'ok': True})

@app.route('/api/token', methods=['POST'])
def api_token():
    if 'uid' not in session:
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json() or {}
    token = data.get('token', '').strip()
    save = bool(data.get('save', False))
    if not token:
        return jsonify({"error": "missing_token"}), 400
    zhzj.set_token(token, save=save)
    return jsonify({"ok": True})

@app.route('/api/courses', methods=['GET'])
def api_courses():
    items = zhzj.fetch_courses_current_term()
    return jsonify(items)

@app.route('/api/collect', methods=['GET'])
def api_collect():
    courseId = request.args.get('courseId')
    courseInfoId = request.args.get('courseInfoId')
    classId = request.args.get('classId')
    if not courseId or not courseInfoId or not classId:
        return jsonify({"error": "missing_params"}), 400
    ids = zhzj.get_project(courseId, courseInfoId, classId)
    return jsonify({"ids": ids})

@app.route('/api/tree', methods=['GET'])
def api_tree():
    courseId = request.args.get('courseId')
    courseInfoId = request.args.get('courseInfoId')
    classId = request.args.get('classId')
    if not courseId or not courseInfoId or not classId:
        return jsonify({"error": "missing_params"}), 400
    items = zhzj.get_types(courseId, courseInfoId, classId)
    return jsonify({"items": items})

@app.route('/api/partition', methods=['GET'])
def api_partition():
    courseId = request.args.get('courseId')
    courseInfoId = request.args.get('courseInfoId')
    classId = request.args.get('classId')
    if not courseId or not courseInfoId or not classId:
        return jsonify({"error": "missing_params"}), 400
    studyable_ids, interactive_items = zhzj.partition_items(courseId, courseInfoId, classId)
    return jsonify({"studyableIds": studyable_ids, "interactiveItems": interactive_items})

@app.route('/api/course/detect', methods=['GET'])
def api_course_detect():
    courseId = request.args.get('courseId')
    courseInfoId = request.args.get('courseInfoId')
    classId = request.args.get('classId')
    if not courseId or not courseInfoId or not classId:
        return jsonify({"error": "missing_params"}), 400
    info = zhzj.detect_course_type(courseId, courseInfoId, classId)
    return jsonify(info)

@app.route('/api/interactive/complete', methods=['POST'])
def api_interactive_complete():
    if 'uid' not in session:
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json() or {}
    courseInfoId = data.get('courseInfoId')
    classId = data.get('classId')
    items = data.get('items') or []
    fast = bool(data.get('fast', False))
    if not courseInfoId or not classId or not items:
        return jsonify({"error": "invalid_params"}), 400
    okc, result = zhzj.complete_interactive_batch(courseInfoId, classId, items, fast=fast)
    try:
        ip = _client_ip(); ip_loc = _geo_ip(ip)
        _log_task_event({'uid': session.get('uid'), 'username': session.get('username'), 'ip': ip, 'ip_loc': ip_loc, 'site': 'blue', 'action': 'interactive', 'course_info_id': courseInfoId, 'class_id': classId, 'ids': items, 'study_time': 0, 'success': okc})
    except Exception:
        pass
    try:
        if ('uid' in session):
            tmpls = _templates_config()
            tpl = tmpls.get('course') or {}
            subj = tpl.get('subject')
            html_tpl = tpl.get('html')
            if subj and html_tpl and okc and len(items) > 0 and (len([x for x in (result or []) if x.get('ok')]) == len(items)):
                conn = db_conn(); cur = conn.cursor(); cur.execute('SELECT username, email FROM users WHERE id=?', (session.get('uid'),)); row = cur.fetchone(); conn.close()
                if row and row['email']:
                    body = _render_template(html_tpl, {'username': row['username']})
                    _send_mail(row['email'], subj, body)
    except Exception:
        pass
    return jsonify({"ok": True, "success": okc, "total": len(items), "items": result})

@app.route('/api/auth/reset/request', methods=['POST'])
def api_reset_request():
    data = request.get_json() or {}
    username = (data.get('username') or '').strip()
    email = (data.get('email') or '').strip()
    if not username and not email:
        return jsonify({'error': 'invalid_params'}), 400
    conn = db_conn(); cur = conn.cursor()
    if username:
        cur.execute('SELECT id, username, email FROM users WHERE username=?', (username,))
    else:
        cur.execute('SELECT id, username, email FROM users WHERE email=?', (email,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'not_found'}), 404
    token = _hash_pw(row['username'] + ':reset:' + str(int(time.time())))
    expire = int(time.time()) + 3600
    cur.execute('UPDATE users SET reset_token=?, reset_token_expire=? WHERE id=?', (token, expire, row['id']))
    conn.commit(); conn.close()
    tmpls = _templates_config()
    tpl = tmpls.get('reset') or {}
    subj = tpl.get('subject') or '重置密码'
    base = f'https://{LOGIN_DOMAIN}/login/?reset_token='
    link = base + token
    html_tpl = tpl.get('html') or '<div>请点击链接重置密码（1小时内有效）：<a href="{reset_link}">{reset_link}</a></div>'
    body = _render_template(html_tpl, {'username': row['username'], 'reset_link': link})
    ok = _send_mail(row['email'], subj, body)
    if not ok:
        return jsonify({'error': 'smtp_send_failed'}), 502
    return jsonify({'ok': True})

@app.route('/api/auth/reset/confirm', methods=['POST'])
def api_reset_confirm():
    data = request.get_json() or {}
    token = (data.get('token') or '').strip()
    new_password = data.get('new_password') or ''
    if not token or not new_password:
        return jsonify({'error': 'invalid_params'}), 400
    conn = db_conn(); cur = conn.cursor()
    cur.execute('SELECT id, reset_token_expire FROM users WHERE reset_token=?', (token,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'invalid_token'}), 404
    if int(row['reset_token_expire'] or 0) < int(time.time()):
        conn.close()
        return jsonify({'error': 'token_expired'}), 410
    cur.execute('UPDATE users SET password_hash=?, reset_token=NULL, reset_token_expire=NULL WHERE id=?', (_hash_pw(new_password), row['id']))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/submit', methods=['POST'])
def api_submit():
    if 'uid' not in session:
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json() or {}
    courseInfoId = data.get('courseInfoId')
    classId = data.get('classId')
    ids = data.get('ids') or []
    study_time = int(data.get('studyTime') or 0)
    if not courseInfoId or not classId or not ids or study_time <= 0:
        return jsonify({"error": "invalid_params"}), 400
    count = zhzj.submit_progress_for_ids(courseInfoId, classId, ids, study_time)
    try:
        ip = _client_ip(); ip_loc = _geo_ip(ip)
        _log_task_event({'uid': session.get('uid'), 'username': session.get('username'), 'ip': ip, 'ip_loc': ip_loc, 'site': 'blue', 'action': 'submit', 'course_info_id': courseInfoId, 'class_id': classId, 'ids': ids, 'study_time': study_time, 'success': count >= 1})
    except Exception:
        pass
    return jsonify({"submitted": count})

@app.route('/api/submit_one', methods=['POST'])
def api_submit_one():
    if 'uid' not in session:
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json() or {}
    courseInfoId = data.get('courseInfoId')
    classId = data.get('classId')
    sid = data.get('id')
    study_time = int(data.get('studyTime') or 0)
    if not courseInfoId or not classId or not sid or study_time <= 0:
        return jsonify({"error": "invalid_params"}), 400
    try:
        ok, resp = zhzj.submit_single(courseInfoId, classId, sid, study_time)
    except Exception:
        return jsonify({"error": "submit_failed"}), 500
    try:
        ip = _client_ip(); ip_loc = _geo_ip(ip)
        _log_task_event({'uid': session.get('uid'), 'username': session.get('username'), 'ip': ip, 'ip_loc': ip_loc, 'site': 'blue', 'action': 'submit_one', 'course_info_id': courseInfoId, 'class_id': classId, 'section_id': sid, 'ids': [sid], 'study_time': study_time, 'success': ok})
    except Exception:
        pass
    return jsonify({"ok": ok, "resp": resp})

@app.route('/api/admin/tasks', methods=['GET'])
def api_admin_tasks():
    if not _require_admin():
        return jsonify({'error': 'unauthorized'}), 401
    items = []
    try:
        conn = db_conn(); cur = conn.cursor()
        cur.execute('SELECT ts, uid, username, student_no, ip, ip_loc, site, action, course_id, course_info_id, class_id, section_id, ids, study_time, success FROM task_events ORDER BY id DESC LIMIT 1000')
        rows = cur.fetchall(); conn.close()
        for r in rows:
            items.append({
                'ts': r['ts'],
                'uid': r['uid'],
                'username': r['username'],
                'student_no': r['student_no'],
                'ip': r['ip'],
                'ip_loc': json.loads(r['ip_loc']) if r['ip_loc'] else {},
                'site': r['site'],
                'action': r['action'],
                'courseId': r['course_id'],
                'courseInfoId': r['course_info_id'],
                'classId': r['class_id'],
                'sectionId': r['section_id'],
                'ids': json.loads(r['ids']) if r['ids'] else [],
                'studyTime': r['study_time'],
                'success': int(r['success'] or 0) == 1
            })
    except Exception:
        pass
    return jsonify({'items': items})

@app.route('/api/student/me', methods=['GET'])
def api_student_me():
    if not _require_student():
        return jsonify({'error': 'unauthorized'}), 401
    try:
        conn = db_conn(); cur = conn.cursor()
        cur.execute('SELECT username, student_no, nickname, signature, email, email_verified FROM users WHERE id=?', (session.get('uid'),))
        r = cur.fetchone(); conn.close()
        if not r:
            return jsonify({'error': 'not_found'}), 404
        return jsonify({'profile': {
            'username': r['username'],
            'student_no': r['student_no'],
            'nickname': r['nickname'],
            'signature': r['signature'],
            'email': r['email'],
            'email_verified': int(r['email_verified'] or 0) == 1
        }})
    except Exception:
        return jsonify({'error': 'failed'}), 500

@app.route('/api/student/profile/update', methods=['POST'])
def api_student_profile_update():
    if not _require_student():
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json() or {}
    student_no = (data.get('student_no') or '').strip()
    nickname = (data.get('nickname') or '').strip()
    signature = (data.get('signature') or '').strip()
    try:
        conn = db_conn(); cur = conn.cursor()
        cur.execute('UPDATE users SET student_no=?, nickname=?, signature=? WHERE id=?', (student_no or None, nickname or None, signature or None, session.get('uid')))
        conn.commit(); conn.close()
        return jsonify({'ok': True})
    except Exception:
        return jsonify({'error': 'store_failed'}), 500

@app.route('/api/student/password/update', methods=['POST'])
def api_student_password_update():
    if not _require_student():
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json() or {}
    old_pw = data.get('old_password') or ''
    new_pw = data.get('new_password') or ''
    if not old_pw or not new_pw:
        return jsonify({'error': 'invalid_params'}), 400
    conn = db_conn(); cur = conn.cursor()
    cur.execute('SELECT password_hash FROM users WHERE id=?', (session.get('uid'),))
    r = cur.fetchone()
    if not r or r['password_hash'] != _hash_pw(old_pw):
        conn.close(); return jsonify({'error': 'bad_password'}), 403
    cur.execute('UPDATE users SET password_hash=? WHERE id=?', (_hash_pw(new_pw), session.get('uid')))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/student/email/update', methods=['POST'])
def api_student_email_update():
    if not _require_student():
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json() or {}
    email = (data.get('email') or '').strip()
    if not email:
        return jsonify({'error': 'invalid_params'}), 400
    cfg = _smtp_config()
    if not cfg:
        return jsonify({'error': 'smtp_not_configured'}), 503
    token = _hash_pw(session.get('username') + ':' + str(int(time.time())))
    expire = int(time.time()) + 3600
    try:
        conn = db_conn(); cur = conn.cursor()
        cur.execute('UPDATE users SET email=?, email_verified=0, email_token=?, email_token_expire=? WHERE id=?', (email, token, expire, session.get('uid')))
        conn.commit(); conn.close()
    except Exception:
        return jsonify({'error': 'store_failed'}), 500
    link = request.host_url.rstrip('/') + '/api/auth/verify?token=' + token
    tmpls = _templates_config()
    reg_subj = (tmpls.get('reg') or {}).get('subject') or '邮箱验证'
    reg_html_tpl = (tmpls.get('reg') or {}).get('html') or '<div>请点击链接完成邮箱验证（1小时内有效）：<a href="{verify_link}">{verify_link}</a></div>'
    body = _render_template(reg_html_tpl, {'username': session.get('username'), 'verify_link': link})
    ok = _send_mail(email, reg_subj, body)
    if not ok:
        return jsonify({'error': 'smtp_send_failed'}), 502
    return jsonify({'ok': True})

@app.route('/api/student/tasks', methods=['GET'])
def api_student_tasks():
    if not _require_student():
        return jsonify({'error': 'unauthorized'}), 401
    items = []
    try:
        conn = db_conn(); cur = conn.cursor()
        cur.execute('SELECT ts, ip, ip_loc, site, action, course_id, course_info_id, class_id, section_id, ids, study_time, success FROM task_events WHERE uid=? ORDER BY id DESC LIMIT 200', (session.get('uid'),))
        rows = cur.fetchall(); conn.close()
        for r in rows:
            items.append({
                'ts': r['ts'],
                'ip': r['ip'],
                'ip_loc': json.loads(r['ip_loc']) if r['ip_loc'] else {},
                'site': r['site'],
                'action': r['action'],
                'courseId': r['course_id'],
                'courseInfoId': r['course_info_id'],
                'classId': r['class_id'],
                'sectionId': r['section_id'],
                'ids': json.loads(r['ids']) if r['ids'] else [],
                'studyTime': r['study_time'],
                'success': int(r['success'] or 0) == 1
            })
    except Exception:
        pass
    return jsonify({'items': items})

@app.route('/api/filter', methods=['POST'])
def api_filter():
    data = request.get_json() or {}
    courseInfoId = data.get('courseInfoId')
    classId = data.get('classId')
    ids = data.get('ids') or []
    if not courseInfoId or not classId or not ids:
        return jsonify({"error": "invalid_params"}), 400
    learnable, failed = zhzj.filter_learnable_ids(courseInfoId, classId, ids)
    return jsonify({"learnable": learnable, "failed": failed})

@app.route('/admin/')
def serve_admin_index():
    if not _require_admin():
        return redirect('/login/', code=302)
    return send_from_directory('后台', 'index.html')

@app.route('/admin')
def serve_admin_index_no_slash():
    return send_from_directory('后台', 'index.html')

@app.route('/admin/<path:filename>')
def serve_admin_static(filename):
    return send_from_directory('后台', filename)

# Chinese alias
@app.route('/后台/')
def serve_admin_index_cn():
    if not _require_admin():
        return redirect('/login/', code=302)
    return send_from_directory('后台', 'index.html')

@app.route('/后台')
def serve_admin_index_cn_no_slash():
    return send_from_directory('后台', 'index.html')

@app.route('/后台/<path:filename>')
def serve_admin_static_cn(filename):
    return send_from_directory('后台', filename)

# aliases under /首页 for convenience
@app.route('/首页/admin')
def alias_home_admin():
    return redirect('/admin/', code=302)

@app.route('/首页/admin/')
def alias_home_admin_slash():
    return redirect('/admin/', code=302)

@app.route('/首页/后台')
def alias_home_admin_cn():
    return redirect('/后台/', code=302)

@app.route('/首页/后台/')
def alias_home_admin_cn_slash():
    return redirect('/后台/', code=302)

 
@app.route('/登录/')
def serve_login_index():
    return send_from_directory('登录', 'index.html')

@app.route('/登录/<path:filename>')
def serve_login_static(filename):
    return send_from_directory('登录', filename)

@app.route('/蓝圈/')
def serve_blue_index():
    return send_from_directory('templates', 'index.html')

@app.route('/蓝圈/<path:filename>')
def serve_blue_static(filename):
    return send_from_directory('templates', filename)

@app.route('/学生/')
def serve_student_index():
    if not _require_student():
        return redirect('/login/', code=302)
    return send_from_directory('学生', 'index.html')

@app.route('/学生/<path:filename>')
def serve_student_static(filename):
    return send_from_directory('学生', filename)

# ASCII aliases for login
@app.route('/login/')
def serve_login_index_ascii():
    return send_from_directory('登录', 'index.html')

@app.route('/login/<path:filename>')
def serve_login_static_ascii(filename):
    return send_from_directory('登录', filename)
 
if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)
