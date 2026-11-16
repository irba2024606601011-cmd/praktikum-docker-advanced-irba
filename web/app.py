import os
import io
import csv
import time
from datetime import date, datetime, timedelta
from flask import (
    Flask, request, redirect, url_for, render_template_string,
    flash, make_response, abort
)
import mysql.connector
import redis

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "silab-praktikum-secret-change-me")

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "db"),
    "user": os.getenv("DB_USER", "silab_user"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "silab_db"),
}
REDIS_HOST = os.getenv("REDIS_HOST", "redis")

ADMIN_NAME = "Irba Anggraini"
ADMIN_ROLE = "Administrator"
TUJUAN_OPTIONS = [
    "Laboratorium Komputer",
    "Laboratorium Jaringan",
    "Laboratorium Multimedia",
    "Laboratorium Sistem Informasi",
    "Lainnya",
]

def get_db():
    return mysql.connector.connect(**DB_CONFIG)

def get_redis():
    return redis.Redis(
        host=REDIS_HOST, port=6379, decode_responses=True,
        socket_connect_timeout=2, socket_timeout=2
    )

def initialize_database():
    for attempt in range(30):
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS kunjungan (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    nama VARCHAR(100) NOT NULL,
                    identitas VARCHAR(50) NOT NULL,
                    tujuan VARCHAR(100) NOT NULL,
                    keperluan TEXT NOT NULL,
                    waktu TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            cur.close()
            conn.close()
            print("Database SILAB siap.")
            return
        except Exception as exc:
            print(f"Menunggu database ({attempt + 1}/30): {exc}")
            time.sleep(2)
    raise RuntimeError("Database tidak berhasil dihubungi.")

def db_fetch(sql, params=(), one=False):
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(sql, params)
    result = cur.fetchone() if one else cur.fetchall()
    cur.close()
    conn.close()
    return result

def db_execute(sql, params=()):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    last_id = cur.lastrowid
    affected = cur.rowcount
    cur.close()
    conn.close()
    return last_id, affected

def get_system_status():
    db_status = "Online"
    redis_status = "Online"
    try:
        db_fetch("SELECT 1 AS ok", one=True)
    except Exception:
        db_status = "Offline"
    try:
        get_redis().ping()
    except redis.RedisError:
        redis_status = "Offline"
    return db_status, redis_status

def build_filters(args):
    search = args.get("q", "").strip()
    tujuan = args.get("tujuan", "").strip()
    tanggal = args.get("tanggal", "").strip()
    mulai = args.get("mulai", "").strip()
    akhir = args.get("akhir", "").strip()
    clauses, params = [], []
    if search:
        clauses.append("(nama LIKE %s OR identitas LIKE %s OR keperluan LIKE %s)")
        like = f"%{search}%"
        params.extend([like, like, like])
    if tujuan in TUJUAN_OPTIONS:
        clauses.append("tujuan = %s")
        params.append(tujuan)
    if tanggal:
        clauses.append("DATE(waktu) = %s")
        params.append(tanggal)
    if mulai:
        clauses.append("DATE(waktu) >= %s")
        params.append(mulai)
    if akhir:
        clauses.append("DATE(waktu) <= %s")
        params.append(akhir)
    return ("WHERE " + " AND ".join(clauses) if clauses else ""), tuple(params)

def dashboard_data():
    summary = db_fetch("""
        SELECT COUNT(*) total,
               SUM(DATE(waktu)=CURDATE()) today_total,
               COUNT(DISTINCT tujuan) lab_total,
               COUNT(DISTINCT identitas) visitor_total
        FROM kunjungan
    """, one=True) or {}
    summary = {k: int(v or 0) for k, v in summary.items()}
    recent = db_fetch("SELECT * FROM kunjungan ORDER BY waktu DESC, id DESC LIMIT 6")
    daily = db_fetch("""
        SELECT DATE(waktu) hari, COUNT(*) jumlah
        FROM kunjungan
        WHERE waktu >= CURDATE() - INTERVAL 6 DAY
        GROUP BY DATE(waktu) ORDER BY hari
    """)
    purpose = db_fetch("""
        SELECT tujuan, COUNT(*) jumlah FROM kunjungan
        GROUP BY tujuan ORDER BY jumlah DESC
    """)
    daily_map = {str(row["hari"]): int(row["jumlah"]) for row in daily}
    labels, values = [], []
    for offset in range(6, -1, -1):
        day = date.today() - timedelta(days=offset)
        labels.append(day.strftime("%d %b"))
        values.append(daily_map.get(day.isoformat(), 0))
    try:
        cache = get_redis()
        cache.set("total_kunjungan", summary["total"], ex=300)
        redis_status = "Online" if cache.ping() else "Offline"
    except redis.RedisError:
        redis_status = "Offline"
    return summary, recent, labels, values, purpose, redis_status

BASE = r"""
<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }} | SILAB</title>
<style>
:root{--navy:#173b66;--blue:#2878c8;--cyan:#23a7bd;--teal:#16a6a1;--ink:#24364b;--muted:#8090a4;--line:#e8eef5;--bg:#f4f7fb;--white:#fff;--green:#21966b;--red:#d95365;--shadow:0 12px 34px rgba(31,69,112,.075)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,"Segoe UI",Arial,sans-serif;font-size:14px}a{color:inherit;text-decoration:none}button,input,select,textarea{font:inherit}
.shell{min-height:100vh}.sidebar{position:fixed;inset:0 auto 0 0;width:252px;padding:25px 17px;background:linear-gradient(165deg,#173d6b 0%,#17658b 58%,#148c98 100%);color:#fff;display:flex;flex-direction:column;z-index:5}
.brand{display:flex;align-items:center;gap:12px;padding:0 10px 25px;border-bottom:1px solid #ffffff26}.brand-logo{width:43px;height:43px;border-radius:14px;background:#fff;color:#17618d;display:grid;place-items:center;font-size:22px;font-weight:900;box-shadow:0 7px 20px #062b4933}.brand b{font-size:20px;letter-spacing:1.5px}.brand small{display:block;color:#c7e2f1;font-size:9px;letter-spacing:1.4px;margin-top:3px}
.nav-caption{margin:26px 12px 9px;color:#a9d1e4;font-size:9px;font-weight:800;letter-spacing:1.5px}.nav-link{display:flex;align-items:center;gap:12px;padding:12px 13px;margin:3px 0;border-radius:11px;color:#e5f3fa;font-size:12px;transition:.2s}.nav-link .ico{width:20px;text-align:center;font-size:16px}.nav-link:hover,.nav-link.active{background:#ffffff20;color:#fff;transform:translateX(2px)}.side-bottom{margin-top:auto;background:#ffffff16;border:1px solid #ffffff1c;border-radius:14px;padding:14px;font-size:10px;line-height:1.65;color:#d8edf7}.side-bottom strong{display:block;color:white;font-size:11px;margin-bottom:4px}.main{margin-left:252px;padding:25px 32px 24px;max-width:1800px}.topbar{display:flex;align-items:center;justify-content:space-between;gap:15px;margin-bottom:24px}.crumb{font-size:10px;color:var(--teal);font-weight:800;letter-spacing:1.5px;text-transform:uppercase}.page-title{font-size:25px;letter-spacing:-.5px;color:var(--navy);margin:5px 0}.subtitle{font-size:12px;color:var(--muted);margin:0}.profile{display:flex;align-items:center;gap:10px;padding:8px 13px;background:#fff;border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow)}.avatar{width:36px;height:36px;border-radius:12px;background:linear-gradient(135deg,#e6f2ff,#d8f7f3);color:#216ca5;display:grid;place-items:center;font-weight:900}.profile strong{font-size:11px;display:block}.profile small{font-size:9px;color:var(--muted)}
.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:17px}.stat{background:#fff;border:1px solid var(--line);border-radius:16px;padding:17px 18px;box-shadow:var(--shadow);position:relative;overflow:hidden}.stat:after{content:"";position:absolute;right:-24px;top:-30px;width:90px;height:90px;border-radius:50%;background:var(--tint)}.stat-head{display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:11px;font-weight:700}.stat-icon{width:36px;height:36px;border-radius:12px;display:grid;place-items:center;background:var(--tint);color:var(--accent);font-size:17px}.stat-value{font-size:29px;font-weight:850;color:var(--navy);margin-top:11px;letter-spacing:-1px}.stat-foot{font-size:9px;color:var(--muted);margin-top:3px}
.grid-main{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(280px,.85fr);gap:16px;margin-bottom:16px}.grid-equal{display:grid;grid-template-columns:1fr 1fr;gap:16px}.panel{background:#fff;border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow);overflow:hidden;min-width:0}.panel-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:17px 19px;border-bottom:1px solid var(--line)}.panel-head h3{font-size:13px;color:var(--navy);margin:0}.panel-head p{font-size:10px;color:var(--muted);margin:4px 0 0}.panel-body{padding:18px 19px}.chart-wrap{height:220px;position:relative}.chart-wrap canvas{width:100%;height:100%}.legend{display:flex;gap:14px;font-size:9px;color:var(--muted);margin-top:8px}.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:5px;background:#2786cf}.purpose-list{display:flex;flex-direction:column;gap:14px}.purpose-row{display:grid;grid-template-columns:minmax(0,1fr) 38px;gap:9px;align-items:center}.purpose-name{font-size:10px;color:#53677e;margin-bottom:6px}.track{height:7px;background:#eef3f8;border-radius:10px;overflow:hidden}.bar{height:100%;border-radius:10px;background:linear-gradient(90deg,#2878c8,#27b4b1)}.purpose-count{text-align:right;font-size:10px;font-weight:800;color:var(--navy)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:9px 13px;border:0;border-radius:9px;font-size:10px;font-weight:800;cursor:pointer;transition:.2s}.btn:hover{transform:translateY(-1px);filter:brightness(.98)}.primary{color:#fff;background:linear-gradient(120deg,#2878c8,#20a4b5);box-shadow:0 5px 13px #2878c82b}.soft{color:#2878c8;background:#eaf4ff}.teal{color:#12847f;background:#e7f8f5}.danger{color:#c9485a;background:#fff0f2}.neutral{color:#52677e;background:#f0f3f7}.btn-sm{padding:7px 9px;font-size:9px}
.toolbar{padding:14px 17px;border-bottom:1px solid var(--line)}.filters{display:grid;grid-template-columns:1.4fr 1fr 1fr auto;gap:8px;align-items:end}.field{margin-bottom:13px}.field label{display:block;font-size:10px;font-weight:800;color:#5c7088;margin-bottom:6px}.field input,.field select,.field textarea,.filters input,.filters select{width:100%;border:1px solid #dfe7f0;border-radius:9px;background:#fbfdff;padding:10px 11px;font-size:11px;color:var(--ink);outline:none;transition:.2s}.field input:focus,.field select:focus,.field textarea:focus,.filters input:focus,.filters select:focus{border-color:#76b4df;box-shadow:0 0 0 3px #2878c815;background:#fff}.field textarea{min-height:95px;resize:vertical}.filters label{display:block;font-size:9px;color:#72849a;font-weight:800;margin-bottom:5px}.filter-buttons{display:flex;gap:6px}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;white-space:nowrap}th{background:#f8fafd;color:#8796a8;text-transform:uppercase;letter-spacing:.8px;font-size:8px;text-align:left;padding:12px 13px}td{padding:12px 13px;border-top:1px solid #eff3f7;font-size:10px;color:#53677d}tbody tr:hover{background:#fbfdff}.person{display:flex;align-items:center;gap:9px}.person-avatar{width:30px;height:30px;border-radius:10px;background:#eaf4ff;color:#2878c8;display:grid;place-items:center;font-size:10px;font-weight:900}.person strong{display:block;font-size:10px;color:#2d425a}.person small{display:block;font-size:9px;color:#98a6b5;margin-top:3px}.pill{display:inline-flex;padding:5px 8px;border-radius:20px;background:#edf6ff;color:#347ab5;font-size:8px;font-weight:800}.muted{color:var(--muted)}.actions{display:flex;gap:5px}.empty{text-align:center;padding:35px;color:#91a0b2;font-size:11px}
.flash{padding:11px 14px;border-radius:10px;margin:0 0 14px;font-size:11px}.flash.success{background:#eaf8f1;color:#20845e}.flash.error{background:#fff0f2;color:#bd4053}.flash.info{background:#eaf4ff;color:#286da8}
.form-layout{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(230px,.75fr);gap:16px}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 13px}.full{grid-column:1/-1}.info-card{padding:16px;border-radius:13px;background:linear-gradient(145deg,#edf6ff,#e9faf7);color:#42627d;font-size:10px;line-height:1.7}.info-card strong{display:block;color:var(--navy);font-size:12px;margin-bottom:5px}.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:13px}.detail-item{padding:13px;border:1px solid var(--line);border-radius:11px}.detail-item small{display:block;color:var(--muted);font-size:9px;margin-bottom:5px}.detail-item strong{font-size:12px;color:var(--navy);overflow-wrap:anywhere}.footer{text-align:center;padding:22px 5px 0;color:#9ba8b8;font-size:9px}.status{display:inline-flex;align-items:center;gap:6px;font-size:10px;font-weight:800}.status:before{content:"";width:7px;height:7px;border-radius:50%;background:var(--green)}.status.off:before{background:var(--red)}.system-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:13px}.system-card{border:1px solid var(--line);border-radius:13px;padding:17px}.system-card strong{display:block;color:var(--navy);font-size:12px;margin:8px 0 5px}.system-card p{font-size:10px;color:var(--muted);margin:0;line-height:1.6}
@media(max-width:1150px){.sidebar{width:220px}.main{margin-left:220px;padding:23px}.stats{grid-template-columns:repeat(2,1fr)}.grid-main{grid-template-columns:1fr}.filters{grid-template-columns:1fr 1fr}.filter-buttons{align-self:end}}
@media(max-width:760px){.sidebar{position:relative;width:auto;inset:auto;padding:14px}.brand{padding:0 5px 12px}.sidebar nav,.nav-caption,.side-bottom{display:none}.main{margin:0;padding:17px 12px}.topbar{align-items:flex-start}.profile{padding:6px}.profile small{display:none}.page-title{font-size:21px}.stats{gap:8px}.stat{padding:12px}.stat-value{font-size:23px}.grid-equal,.form-layout{grid-template-columns:1fr}.filters{grid-template-columns:1fr 1fr}.filters .search{grid-column:1/-1}.filter-buttons{grid-column:1/-1}.form-grid{grid-template-columns:1fr}.full{grid-column:auto}.system-grid{grid-template-columns:1fr}.detail-grid{grid-template-columns:1fr 1fr}}
</style>
</head>
<body>
<div class="shell">
<aside class="sidebar">
 <div class="brand"><div class="brand-logo">S</div><div><b>SILAB</b><small>LABORATORY MANAGEMENT</small></div></div>
 <nav>
  <div class="nav-caption">MENU UTAMA</div>
  <a class="nav-link {{ 'active' if active=='dashboard' else '' }}" href="{{ url_for('index') }}"><span class="ico">▦</span> Dashboard</a>
  <a class="nav-link {{ 'active' if active=='visits' else '' }}" href="{{ url_for('visits') }}"><span class="ico">▤</span> Data Kunjungan</a>
  <a class="nav-link {{ 'active' if active=='add' else '' }}" href="{{ url_for('add_visit') }}"><span class="ico">＋</span> Tambah Kunjungan</a>
  <a class="nav-link {{ 'active' if active=='reports' else '' }}" href="{{ url_for('reports') }}"><span class="ico">▥</span> Laporan & Analitik</a>
  <div class="nav-caption">SISTEM</div>
  <a class="nav-link {{ 'active' if active=='system' else '' }}" href="{{ url_for('system_page') }}"><span class="ico">◈</span> Status Infrastruktur</a>
 </nav>
 <div class="side-bottom"><strong>✦ Sistem Informasi Laboratorium</strong>Kelola kunjungan, pantau aktivitas, dan susun laporan laboratorium dalam satu dashboard terpadu.</div>
</aside>
<main class="main">
<header class="topbar"><div><div class="crumb">SILAB / {{ section }}</div><h1 class="page-title">{{ title }}</h1><p class="subtitle">{{ subtitle }}</p></div><div class="profile"><div class="avatar">IA</div><div><strong>{{ admin_name }}</strong><small>{{ admin_role }}</small></div></div></header>
{% with messages=get_flashed_messages(with_categories=true) %}{% for category,message in messages %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}{% endwith %}
{{ content|safe }}
<footer class="footer">© {{ current_year }} SILAB · Sistem Informasi Kunjungan Laboratorium · Administrator {{ admin_name }}</footer>
</main></div>
<script>
document.querySelectorAll('form[data-confirm]').forEach(f=>f.addEventListener('submit',e=>{if(!confirm(f.dataset.confirm))e.preventDefault()}));
</script>
</body></html>
"""

def page(title, subtitle, section, active, content, **context):
    return render_template_string(
        BASE, title=title, subtitle=subtitle, section=section, active=active,
        content=render_template_string(content, **context),
        admin_name=ADMIN_NAME, admin_role=ADMIN_ROLE,
        current_year=datetime.now().year
    )

def visit_form(item=None):
    editing = item is not None
    return r"""
<div class="form-layout">
 <section class="panel"><div class="panel-head"><div><h3>{{ 'Perbarui data pengunjung' if editing else 'Formulir kunjungan baru' }}</h3><p>Lengkapi informasi berikut dengan benar.</p></div><span class="pill">{{ 'EDIT DATA' if editing else 'INPUT DATA' }}</span></div>
 <div class="panel-body"><form method="POST" action="{{ url_for('edit_visit', visit_id=item.id) if editing else url_for('add_visit') }}">
  <div class="form-grid">
   <div class="field"><label>Nama lengkap *</label><input name="nama" maxlength="100" required value="{{ item.nama if item else '' }}" placeholder="Contoh: Irba Anggraini"></div>
   <div class="field"><label>NIM / Identitas *</label><input name="identitas" maxlength="50" required value="{{ item.identitas if item else '' }}" placeholder="Masukkan NIM atau identitas"></div>
   <div class="field full"><label>Tujuan laboratorium *</label><select name="tujuan" required><option value="">Pilih laboratorium</option>{% for opt in options %}<option value="{{ opt }}" {{ 'selected' if item and item.tujuan==opt else '' }}>{{ opt }}</option>{% endfor %}</select></div>
   <div class="field full"><label>Keperluan kunjungan *</label><textarea name="keperluan" required maxlength="2000" placeholder="Tuliskan keperluan kunjungan secara jelas...">{{ item.keperluan if item else '' }}</textarea></div>
  </div><button class="btn primary" type="submit">{{ '✓ Simpan perubahan' if editing else '＋ Simpan kunjungan' }}</button> <a class="btn neutral" href="{{ url_for('visits') }}">Batal</a>
 </form></div></section>
 <aside class="info-card"><strong>ⓘ Informasi pengisian</strong>Pastikan nama dan identitas sesuai data pengunjung. Pilih laboratorium yang dituju dan jelaskan keperluan kunjungan agar riwayat mudah ditelusuri.<br><br><strong>Keamanan data</strong>Data disimpan pada MySQL dan tetap tersedia selama volume database tidak dihapus.</aside>
</div>
"""

@app.route("/")
def index():
    try:
        summary, recent, labels, values, purposes, redis_status = dashboard_data()
        db_status = "Online"
    except mysql.connector.Error as exc:
        app.logger.exception("Dashboard database error")
        flash("Database belum dapat diakses. Periksa status layanan MySQL.", "error")
        summary = {"total":0,"today_total":0,"lab_total":0,"visitor_total":0}
        recent, labels, values, purposes, redis_status, db_status = [], [], [], [], "Tidak diketahui", "Offline"
    content = r"""
<section class="stats">
 <div class="stat" style="--tint:#eaf4ff;--accent:#2878c8"><div class="stat-head">Total kunjungan <span class="stat-icon">▤</span></div><div class="stat-value">{{ summary.total }}</div><div class="stat-foot">Seluruh data yang tersimpan</div></div>
 <div class="stat" style="--tint:#e7f8f5;--accent:#16a6a1"><div class="stat-head">Kunjungan hari ini <span class="stat-icon">◷</span></div><div class="stat-value">{{ summary.today_total }}</div><div class="stat-foot">Aktivitas pada hari ini</div></div>
 <div class="stat" style="--tint:#f1edff;--accent:#8064c9"><div class="stat-head">Jenis laboratorium <span class="stat-icon">⌖</span></div><div class="stat-value">{{ summary.lab_total }}</div><div class="stat-foot">Kategori tujuan yang digunakan</div></div>
 <div class="stat" style="--tint:#eaf8f0;--accent:#29966b"><div class="stat-head">Pengunjung unik <span class="stat-icon">♙</span></div><div class="stat-value">{{ summary.visitor_total }}</div><div class="stat-foot">Identitas pengunjung berbeda</div></div>
</section>
<section class="grid-main">
 <div class="panel"><div class="panel-head"><div><h3>Tren kunjungan</h3><p>Aktivitas tujuh hari terakhir</p></div><span class="pill">7 HARI</span></div><div class="panel-body"><div class="chart-wrap"><canvas id="trendChart"></canvas></div><div class="legend"><span><i class="dot"></i>Jumlah kunjungan</span><span class="muted">Sumber data: MySQL</span></div></div></div>
 <div class="panel"><div class="panel-head"><div><h3>Distribusi tujuan</h3><p>Kunjungan berdasarkan laboratorium</p></div><a class="btn btn-sm soft" href="{{ url_for('reports') }}">Lihat laporan</a></div><div class="panel-body"><div class="purpose-list">{% for p in purposes %}<div class="purpose-row"><div><div class="purpose-name">{{ p.tujuan }}</div><div class="track"><div class="bar" style="width:{{ (p.jumlah / max_purpose * 100) if max_purpose else 0 }}%"></div></div></div><div class="purpose-count">{{ p.jumlah }}</div></div>{% else %}<div class="empty">Belum ada data kunjungan.</div>{% endfor %}</div></div></div>
</section>
<section class="panel"><div class="panel-head"><div><h3>Aktivitas terbaru</h3><p>Enam data kunjungan terakhir yang tercatat</p></div><div><a class="btn btn-sm soft" href="{{ url_for('visits') }}">Semua data →</a> <a class="btn btn-sm primary" href="{{ url_for('add_visit') }}">＋ Tambah</a></div></div>
<div class="table-wrap"><table><thead><tr><th>Pengunjung</th><th>Tujuan</th><th>Keperluan</th><th>Waktu</th><th></th></tr></thead><tbody>
{% for v in recent %}<tr><td><div class="person"><div class="person-avatar">{{ v.nama[:1]|upper }}</div><div><strong>{{ v.nama }}</strong><small>{{ v.identitas }}</small></div></div></td><td><span class="pill">{{ v.tujuan }}</span></td><td>{{ v.keperluan|truncate(48) }}</td><td>{{ v.waktu }}</td><td><a class="btn btn-sm soft" href="{{ url_for('visit_detail', visit_id=v.id) }}">Detail</a></td></tr>{% else %}<tr><td colspan="5" class="empty">Belum ada kunjungan. Tambahkan data pertama.</td></tr>{% endfor %}
</tbody></table></div></section>
<script>
(()=>{const c=document.getElementById('trendChart');if(!c)return;const ctx=c.getContext('2d'),labels={{ labels|tojson }},data={{ values|tojson }};function draw(){const w=c.clientWidth,h=c.clientHeight,dpr=window.devicePixelRatio||1;c.width=w*dpr;c.height=h*dpr;ctx.scale(dpr,dpr);const pad={l:32,r:12,t:16,b:30},cw=w-pad.l-pad.r,ch=h-pad.t-pad.b,max=Math.max(4,...data),step=max/4;ctx.font='10px Segoe UI';for(let i=0;i<=4;i++){let y=pad.t+ch-(i/4)*ch;ctx.strokeStyle='#edf1f6';ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(w-pad.r,y);ctx.stroke();ctx.fillStyle='#94a2b2';ctx.fillText(Math.round(step*i),3,y+3)}let pts=data.map((v,i)=>({x:pad.l+(labels.length===1?cw/2:i*cw/(labels.length-1)),y:pad.t+ch-(v/max)*ch}));if(pts.length){let grad=ctx.createLinearGradient(0,pad.t,0,h);grad.addColorStop(0,'rgba(39,137,207,.22)');grad.addColorStop(1,'rgba(39,137,207,0)');ctx.beginPath();ctx.moveTo(pts[0].x,pad.t+ch);pts.forEach(p=>ctx.lineTo(p.x,p.y));ctx.lineTo(pts[pts.length-1].x,pad.t+ch);ctx.closePath();ctx.fillStyle=grad;ctx.fill();ctx.beginPath();pts.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));ctx.strokeStyle='#2789cf';ctx.lineWidth=2.5;ctx.stroke();pts.forEach((p,i)=>{ctx.beginPath();ctx.arc(p.x,p.y,3.5,0,Math.PI*2);ctx.fillStyle='#fff';ctx.fill();ctx.strokeStyle='#2789cf';ctx.lineWidth=2;ctx.stroke();ctx.fillStyle='#8392a4';ctx.font='9px Segoe UI';ctx.textAlign='center';ctx.fillText(labels[i],p.x,h-8)})}}draw();window.addEventListener('resize',draw)})();
</script>
"""
    return page("Dashboard", "Ringkasan aktivitas dan kondisi sistem laboratorium.", "OVERVIEW", "dashboard", content,
                summary=summary, recent=recent, labels=labels, values=values, purposes=purposes,
                max_purpose=max([int(p["jumlah"]) for p in purposes], default=0))

@app.route("/kunjungan")
def visits():
    where, params = build_filters(request.args)
    page_no = max(1, request.args.get("page", 1, type=int))
    per_page = 10
    total_row = db_fetch(f"SELECT COUNT(*) total FROM kunjungan {where}", params, one=True)
    total = int(total_row["total"] or 0)
    pages = max(1, (total + per_page - 1) // per_page)
    page_no = min(page_no, pages)
    rows = db_fetch(f"SELECT * FROM kunjungan {where} ORDER BY waktu DESC,id DESC LIMIT %s OFFSET %s",
                    params + (per_page, (page_no-1)*per_page))
    content = r"""
<section class="panel"><div class="panel-head"><div><h3>Direktori kunjungan</h3><p>{{ total }} data ditemukan · halaman {{ page_no }} dari {{ pages }}</p></div><a class="btn primary" href="{{ url_for('add_visit') }}">＋ Tambah kunjungan</a></div>
<div class="toolbar"><form class="filters" method="GET"><div class="search"><label>Cari nama, identitas, atau keperluan</label><input name="q" value="{{ request.args.get('q','') }}" placeholder="Ketik kata kunci..."></div><div><label>Tujuan</label><select name="tujuan"><option value="">Semua tujuan</option>{% for opt in options %}<option {{ 'selected' if request.args.get('tujuan')==opt else '' }}>{{ opt }}</option>{% endfor %}</select></div><div><label>Tanggal</label><input type="date" name="tanggal" value="{{ request.args.get('tanggal','') }}"></div><div class="filter-buttons"><button class="btn primary">Terapkan</button><a class="btn neutral" href="{{ url_for('visits') }}">Reset</a></div></form></div>
<div class="table-wrap"><table><thead><tr><th>Pengunjung</th><th>Tujuan</th><th>Keperluan</th><th>Waktu</th><th>Aksi</th></tr></thead><tbody>{% for v in rows %}<tr><td><div class="person"><div class="person-avatar">{{ v.nama[:1]|upper }}</div><div><strong>{{ v.nama }}</strong><small>{{ v.identitas }}</small></div></div></td><td><span class="pill">{{ v.tujuan }}</span></td><td>{{ v.keperluan|truncate(52) }}</td><td>{{ v.waktu }}</td><td><div class="actions"><a class="btn btn-sm soft" href="{{ url_for('visit_detail',visit_id=v.id) }}">Detail</a><a class="btn btn-sm teal" href="{{ url_for('edit_visit',visit_id=v.id) }}">Edit</a><form method="POST" action="{{ url_for('delete_visit',visit_id=v.id) }}" data-confirm="Yakin menghapus data {{ v.nama }}?"><button class="btn btn-sm danger">Hapus</button></form></div></td></tr>{% else %}<tr><td colspan="5" class="empty">Tidak ada data yang sesuai dengan pencarian.</td></tr>{% endfor %}</tbody></table></div>
<div class="panel-body" style="display:flex;justify-content:space-between;align-items:center"><span class="muted" style="font-size:10px">Menampilkan {{ rows|length }} dari {{ total }} data</span><div>{% if page_no>1 %}<a class="btn btn-sm soft" href="{{ url_for('visits',page=page_no-1,**filters) }}">← Sebelumnya</a>{% endif %} {% if page_no<pages %}<a class="btn btn-sm soft" href="{{ url_for('visits',page=page_no+1,**filters) }}">Berikutnya →</a>{% endif %}</div></div>
</section>
"""
    filters = {k: request.args.get(k, "") for k in ("q", "tujuan", "tanggal") if request.args.get(k)}
    return page("Data Kunjungan", "Cari, tinjau, dan kelola seluruh catatan kunjungan.", "MANAJEMEN DATA", "visits", content,
                rows=rows, total=total, page_no=page_no, pages=pages, options=TUJUAN_OPTIONS,
                filters=filters, request=request)

@app.route("/kunjungan/tambah", methods=["GET", "POST"])
def add_visit():
    if request.method == "POST":
        values = [request.form.get(k, "").strip() for k in ("nama", "identitas", "tujuan", "keperluan")]
        nama, identitas, tujuan, keperluan = values
        if not all(values) or tujuan not in TUJUAN_OPTIONS:
            flash("Lengkapi semua kolom dan pilih tujuan yang tersedia.", "error")
        else:
            db_execute("INSERT INTO kunjungan (nama,identitas,tujuan,keperluan) VALUES (%s,%s,%s,%s)", tuple(values))
            try:
                get_redis().delete("total_kunjungan")
            except redis.RedisError:
                pass
            flash("Data kunjungan berhasil disimpan.", "success")
            return redirect(url_for("visits"))
    return page("Tambah Kunjungan", "Catat pengunjung baru ke dalam sistem SILAB.", "INPUT DATA", "add",
                visit_form(), item=None, editing=False, options=TUJUAN_OPTIONS)

@app.route("/kunjungan/<int:visit_id>")
def visit_detail(visit_id):
    item = db_fetch("SELECT * FROM kunjungan WHERE id=%s", (visit_id,), one=True)
    if not item:
        abort(404)
    content = r"""
<section class="panel"><div class="panel-head"><div><h3>Informasi lengkap kunjungan</h3><p>Nomor data #{{ item.id }}</p></div><div><a class="btn teal" href="{{ url_for('edit_visit',visit_id=item.id) }}">✎ Edit data</a><a class="btn neutral" href="{{ url_for('visits') }}">Kembali</a></div></div><div class="panel-body"><div class="detail-grid"><div class="detail-item"><small>Nama pengunjung</small><strong>{{ item.nama }}</strong></div><div class="detail-item"><small>NIM / Identitas</small><strong>{{ item.identitas }}</strong></div><div class="detail-item"><small>Tujuan laboratorium</small><strong>{{ item.tujuan }}</strong></div><div class="detail-item"><small>Waktu pencatatan</small><strong>{{ item.waktu }}</strong></div><div class="detail-item" style="grid-column:1/-1"><small>Keperluan kunjungan</small><strong>{{ item.keperluan }}</strong></div></div></div></section>
"""
    return page("Detail Kunjungan", "Rincian catatan pengunjung yang dipilih.", "DETAIL DATA", "visits", content, item=item)

@app.route("/kunjungan/edit/<int:visit_id>", methods=["GET", "POST"])
def edit_visit(visit_id):
    item = db_fetch("SELECT * FROM kunjungan WHERE id=%s", (visit_id,), one=True)
    if not item:
        abort(404)
    if request.method == "POST":
        values = [request.form.get(k, "").strip() for k in ("nama", "identitas", "tujuan", "keperluan")]
        if not all(values) or values[2] not in TUJUAN_OPTIONS:
            flash("Lengkapi semua kolom dan pilih tujuan yang tersedia.", "error")
        else:
            db_execute("UPDATE kunjungan SET nama=%s,identitas=%s,tujuan=%s,keperluan=%s WHERE id=%s", tuple(values)+(visit_id,))
            flash("Data kunjungan berhasil diperbarui.", "success")
            return redirect(url_for("visit_detail", visit_id=visit_id))
    return page("Edit Kunjungan", "Perbarui informasi kunjungan yang sudah tercatat.", "EDIT DATA", "visits",
                visit_form(item), item=item, editing=True, options=TUJUAN_OPTIONS)

@app.route("/kunjungan/hapus/<int:visit_id>", methods=["POST"])
def delete_visit(visit_id):
    _, affected = db_execute("DELETE FROM kunjungan WHERE id=%s", (visit_id,))
    if affected:
        flash("Data kunjungan berhasil dihapus.", "success")
    else:
        flash("Data tidak ditemukan atau sudah dihapus.", "error")
    return redirect(url_for("visits"))

@app.route("/laporan")
def reports():
    where, params = build_filters(request.args)
    total = db_fetch(f"SELECT COUNT(*) total FROM kunjungan {where}", params, one=True)["total"]
    by_purpose = db_fetch(f"SELECT tujuan,COUNT(*) jumlah FROM kunjungan {where} GROUP BY tujuan ORDER BY jumlah DESC", params)
    by_month = db_fetch("""
        SELECT DATE_FORMAT(waktu,'%Y-%m') bulan, COUNT(*) jumlah
        FROM kunjungan GROUP BY DATE_FORMAT(waktu,'%Y-%m')
        ORDER BY bulan DESC LIMIT 6
    """)
    content = r"""
<section class="stats"><div class="stat" style="--tint:#eaf4ff;--accent:#2878c8"><div class="stat-head">Total data sesuai filter <span class="stat-icon">▤</span></div><div class="stat-value">{{ total }}</div><div class="stat-foot">Jumlah catatan kunjungan</div></div><div class="stat" style="--tint:#e7f8f5;--accent:#16a6a1"><div class="stat-head">Kategori aktif <span class="stat-icon">⌖</span></div><div class="stat-value">{{ by_purpose|length }}</div><div class="stat-foot">Tujuan laboratorium</div></div><div class="stat" style="--tint:#f1edff;--accent:#8064c9"><div class="stat-head">Periode tersedia <span class="stat-icon">▦</span></div><div class="stat-value">{{ by_month|length }}</div><div class="stat-foot">Bulan dengan catatan terbaru</div></div><div class="stat" style="--tint:#eaf8f0;--accent:#29966b"><div class="stat-head">Format laporan <span class="stat-icon">⇩</span></div><div class="stat-value" style="font-size:22px">CSV</div><div class="stat-foot">Siap dibuka di spreadsheet</div></div></section>
<section class="panel"><div class="panel-head"><div><h3>Filter laporan</h3><p>Atur rentang tanggal dan kategori untuk ringkasan.</p></div><a class="btn teal" href="{{ url_for('export_csv',**request.args) }}">⇩ Unduh CSV</a></div><div class="panel-body"><form class="filters" method="GET"><div><label>Mulai tanggal</label><input type="date" name="mulai" value="{{ request.args.get('mulai','') }}"></div><div><label>Sampai tanggal</label><input type="date" name="akhir" value="{{ request.args.get('akhir','') }}"></div><div><label>Tujuan</label><select name="tujuan"><option value="">Semua tujuan</option>{% for opt in options %}<option {{ 'selected' if request.args.get('tujuan')==opt else '' }}>{{ opt }}</option>{% endfor %}</select></div><div class="filter-buttons"><button class="btn primary">Terapkan</button><a class="btn neutral" href="{{ url_for('reports') }}">Reset</a></div></form></div></section>
<section class="grid-equal" style="margin-top:16px"><div class="panel"><div class="panel-head"><div><h3>Ringkasan per laboratorium</h3><p>Distribusi jumlah kunjungan sesuai filter</p></div></div><div class="panel-body"><div class="purpose-list">{% for p in by_purpose %}<div class="purpose-row"><div><div class="purpose-name">{{ p.tujuan }}</div><div class="track"><div class="bar" style="width:{{ (p.jumlah/max_count*100) if max_count else 0 }}%"></div></div></div><div class="purpose-count">{{ p.jumlah }}</div></div>{% else %}<div class="empty">Belum ada data untuk periode ini.</div>{% endfor %}</div></div></div>
<div class="panel"><div class="panel-head"><div><h3>Aktivitas bulanan</h3><p>Enam bulan pencatatan terakhir</p></div></div><div class="panel-body"><div class="purpose-list">{% for m in by_month %}<div class="purpose-row"><div><div class="purpose-name">{{ m.bulan }}</div><div class="track"><div class="bar" style="width:{{ (m.jumlah/month_max*100) if month_max else 0 }}%"></div></div></div><div class="purpose-count">{{ m.jumlah }}</div></div>{% else %}<div class="empty">Belum ada data bulanan.</div>{% endfor %}</div></div></div></section>
"""
    return page("Laporan & Analitik", "Ringkasan data untuk membantu evaluasi aktivitas laboratorium.", "ANALITIK", "reports", content,
                total=total, by_purpose=by_purpose, by_month=by_month, options=TUJUAN_OPTIONS,
                max_count=max([int(x["jumlah"]) for x in by_purpose], default=0),
                month_max=max([int(x["jumlah"]) for x in by_month], default=0), request=request)

@app.route("/export")
def export_csv():
    where, params = build_filters(request.args)
    rows = db_fetch(f"SELECT id,nama,identitas,tujuan,keperluan,waktu FROM kunjungan {where} ORDER BY waktu DESC,id DESC", params)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Nama", "NIM / Identitas", "Tujuan", "Keperluan", "Waktu"])
    for row in rows:
        writer.writerow([row["id"], row["nama"], row["identitas"], row["tujuan"], row["keperluan"], row["waktu"]])
    response = make_response("\ufeff" + output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = f"attachment; filename=laporan_silab_{date.today().isoformat()}.csv"
    return response

@app.route("/sistem")
def system_page():
    db_status, redis_status = get_system_status()
    content = r"""
<section class="system-grid"><div class="system-card"><span class="pill">DATABASE</span><strong>MySQL 8.4</strong><span class="status {{ 'off' if db_status!='Online' else '' }}">{{ db_status }}</span><p>Penyimpanan utama data kunjungan laboratorium. Data tersimpan secara persisten pada Docker volume.</p></div><div class="system-card"><span class="pill">CACHE</span><strong>Redis 7</strong><span class="status {{ 'off' if redis_status!='Online' else '' }}">{{ redis_status }}</span><p>Layanan cache dan penghitung kunjungan. Aplikasi tetap dapat digunakan saat Redis tidak tersedia.</p></div><div class="system-card"><span class="pill">WEB APPLICATION</span><strong>Flask / Python</strong><span class="status">Online</span><p>Antarmuka SILAB yang menangani formulir, pengelolaan data, dan laporan.</p></div></section>
<section class="panel" style="margin-top:16px"><div class="panel-head"><div><h3>Informasi administrator</h3><p>Identitas pengelola sistem</p></div><span class="pill">ADMIN</span></div><div class="panel-body"><div class="detail-grid"><div class="detail-item"><small>Nama administrator</small><strong>{{ admin_name }}</strong></div><div class="detail-item"><small>Hak akses tampilan</small><strong>{{ admin_role }}</strong></div><div class="detail-item"><small>Nama sistem</small><strong>SILAB — Sistem Informasi Kunjungan Laboratorium</strong></div><div class="detail-item"><small>Waktu server</small><strong>{{ now }}</strong></div></div><p class="muted" style="font-size:10px;margin-top:15px">Halaman ini menampilkan status koneksi aplikasi. Status container Docker secara menyeluruh tetap dapat diperiksa melalui Docker Compose.</p></div></section>
"""
    return page("Status Infrastruktur", "Pantau koneksi komponen utama yang digunakan aplikasi.", "MONITORING", "system", content,
                db_status=db_status, redis_status=redis_status, admin_name=ADMIN_NAME,
                admin_role=ADMIN_ROLE, now=datetime.now().strftime("%d-%m-%Y %H:%M:%S"))

@app.route("/health")
def health():
    db_status, redis_status = get_system_status()
    status = "healthy" if db_status == "Online" else "degraded"
    return {"status": status, "database": db_status.lower(), "redis": redis_status.lower()}, (200 if status == "healthy" else 503)

@app.errorhandler(404)
def not_found(_error):
    return page("Halaman tidak ditemukan", "Halaman yang kamu cari tidak tersedia.", "404", "", """
<section class="panel"><div class="panel-body empty"><div style="font-size:42px;color:#2878c8">404</div><h3>Halaman tidak ditemukan</h3><p>Alamat mungkin salah atau data sudah tidak tersedia.</p><a class="btn primary" href="{{ url_for('index') }}">Kembali ke dashboard</a></div></section>
"""), 404

if __name__ == "__main__":
    initialize_database()
    app.run(host="0.0.0.0", port=5000)
