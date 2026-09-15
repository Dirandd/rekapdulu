import os, random, string, io, json, requests, re, secrets, smtplib
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from fpdf import FPDF
import pandas as pd
from PIL import Image
import numpy as np
from flask_apscheduler import APScheduler

app = Flask(__name__)
app.config['SECRET_KEY'] = 'rahasia-kuat-rekapdulu'
# Ensure instance folder exists before setting DB URI
os.makedirs(os.path.join(app.root_path, 'instance'), exist_ok=True)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(app.root_path, 'instance', 'database.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads', 'logos')
app.config['SCHEDULER_API_ENABLED'] = True
# SMTP Email Configuration (set via environment variables)
app.config['MAIL_SERVER']   = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT']     = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', '')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', '')
app.config['MAIL_FROM']     = os.environ.get('MAIL_FROM', os.environ.get('MAIL_USERNAME', 'noreply@rekapdulu.com'))
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()

# ================== HELPERS ==================
def remove_white_background(image_path):
    try:
        img = Image.open(image_path).convert("RGBA")
        data = np.array(img)
        white = (data[:,:,0]>240)&(data[:,:,1]>240)&(data[:,:,2]>240)
        data[white,3] = 0
        Image.fromarray(data).save(image_path, "PNG")
        return True
    except Exception as e:
        print(f"Gagal hapus background: {e}")
        return False

def clean_price(value):
    if not value:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace('.','').replace(',','.')
    try:
        return float(cleaned)
    except ValueError:
        return 0.0

def fetch_aviation_news():
    try:
        url = "https://news.google.com/rss/search?q=penerbangan+maskapai+aviasi&hl=id&gl=ID&ceid=ID:id"
        resp = requests.get(url, timeout=10)
        root = ET.fromstring(resp.content)
        items = root.findall('.//item')[:15]
        for item in items:
            title = item.find('title').text or ''
            source = item.find('source').text if item.find('source') is not None else 'News'
            link_el = item.find('link')
            source_url = link_el.text if link_el is not None else None
            if not source_url:
                guid_el = item.find('guid')
                source_url = guid_el.text if guid_el is not None else None
            desc_el = item.find('description')
            raw_content = desc_el.text if desc_el is not None else ''
            content = re.sub(r'<[^>]+>', '', raw_content or '')[:300]
            if title and not News.query.filter_by(title=title).first():
                db.session.add(News(title=title, content=content, source=source, source_url=source_url))
        db.session.commit()
        return True
    except Exception as e:
        print(f'Gagal fetch berita: {e}')
        return False

def generate_invoice_number():
    now = datetime.utcnow()
    prefix = f"INV{now.strftime('%Y.%m')}."
    last = Invoice.query.filter(Invoice.invoice_number.like(f"{prefix}%")).order_by(Invoice.invoice_number.desc()).first()
    num = int(last.invoice_number.split('.')[-1]) + 1 if last else 1
    return f"{prefix}{num:03d}"

def generate_refund_number():
    now = datetime.utcnow()
    prefix = f"REF{now.strftime('%Y.%m')}."
    last = Refund.query.filter(Refund.refund_number.like(f"{prefix}%")).order_by(Refund.refund_number.desc()).first()
    num = int(last.refund_number.split('.')[-1]) + 1 if last else 1
    return f"{prefix}{num:03d}"

def terbilang(angka):
    if angka == 0: return "Nol Rupiah"
    satuan = ["","Satu","Dua","Tiga","Empat","Lima","Enam","Tujuh","Delapan","Sembilan"]
    level = ["","Ribu","Juta","Miliar","Triliun"]
    def rb(n):
        n=int(n)
        if n==0: return ""
        elif n<10: return satuan[n]
        elif n<20: return ["Sepuluh","Sebelas","Dua Belas","Tiga Belas","Empat Belas","Lima Belas","Enam Belas","Tujuh Belas","Delapan Belas","Sembilan Belas"][n-10]
        elif n<100:
            p=n//10; s=n%10
            return satuan[p]+" Puluh"+(" "+satuan[s] if s else "")
        else:
            r=n//100; s=n%100
            d="Seratus" if r==1 else satuan[r]+" Ratus"
            return d+(" "+rb(s) if s else "")
    if angka<0: return "Minus "+terbilang(abs(angka))
    angka_int=int(angka); teks=""
    for i in range(len(level)):
        bagian=(angka_int//(1000**i))%1000
        if bagian>0:
            teks=("Seribu " if i==1 and bagian==1 else rb(bagian)+" "+level[i]+" ")+teks
    return teks.strip().capitalize()+" Rupiah"

def _build_invoice_pdf(invoice, mode='customer'):
    """Generate invoice PDF matching DSM invoice layout."""
    from fpdf import FPDF
    profile = AgencyProfile.query.first()
    if not profile:
        profile = AgencyProfile()

    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(15, 10, 15)

    # ── HEADER ──────────────────────────────────────────────────────────
    logo_path = None
    if profile.logo_filename:
        p = os.path.join(app.root_path, 'static', 'uploads', 'logos', profile.logo_filename)
        if os.path.exists(p): logo_path = p

    if logo_path:
        pdf.image(logo_path, x=15, y=10, h=22)
        pdf.set_xy(50, 10)
    else:
        pdf.set_xy(15, 10)

    # Company info block
    pdf.set_font('Helvetica', 'B', 11)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 6, profile.company_name, ln=True)
    pdf.set_font('Helvetica', '', 8)
    pdf.set_x(50 if logo_path else 15)
    pdf.cell(0, 4.5, f"{profile.brand_name} {profile.address} - {profile.city.split(',')[0]}", ln=True)
    pdf.set_x(50 if logo_path else 15)
    pdf.cell(0, 4.5, profile.city, ln=True)
    pdf.set_x(50 if logo_path else 15)
    pdf.cell(0, 4.5, f"Phone : {profile.phone} - WA: {profile.whatsapp} - Fax : {profile.fax}", ln=True)
    pdf.set_x(50 if logo_path else 15)
    pdf.cell(0, 4.5, f"Email : {profile.email}", ln=True)

    # INVOICE label top-right
    pdf.set_font('Helvetica', 'B', 22)
    pdf.set_text_color(80, 80, 80)
    pdf.set_xy(140, 10)
    pdf.cell(55, 12, 'INVOICE', align='R')

    # Mode label below INVOICE
    pdf.set_font('Helvetica', '', 7)
    pdf.set_text_color(130, 130, 130)
    mode_label = {'customer': '', 'nett': '(Harga Nett Maskapai)', 'keep': '(Keep / Admin Fee)'}
    pdf.set_xy(140, 22)
    pdf.cell(55, 5, mode_label.get(mode, ''), align='R')

    # Separator line
    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.5)
    pdf.line(15, 38, 195, 38)

    # ── INFO BOX (kiri: invoice info | kanan: kepada) ───────────────────
    pdf.set_text_color(0, 0, 0)
    pdf.set_font('Helvetica', '', 8)
    y_box = 42

    # Agent/ID info (left)
    pdf.set_xy(15, y_box)
    def info_row(label, val, bold_val=False):
        pdf.set_font('Helvetica', '', 8)
        pdf.cell(22, 5.5, label, ln=False)
        pdf.cell(3, 5.5, ':', ln=False)
        if bold_val:
            pdf.set_font('Helvetica', 'B', 8)
        pdf.cell(60, 5.5, str(val), ln=True)
        pdf.set_x(15)

    info_row('Id', invoice.agent_name.split()[0] if invoice.agent_name else '-')
    info_row('Ref. TBOS', invoice.pnr or '-')

    from datetime import datetime as dt
    tgl = invoice.created_at.strftime('%A, %d %B %Y') if invoice.created_at else '-'
    # Indonesian day names
    days_id = {'Monday':'Senin','Tuesday':'Selasa','Wednesday':'Rabu',
               'Thursday':'Kamis','Friday':'Jumat','Saturday':'Sabtu','Sunday':'Minggu'}
    months_id = {'January':'Januari','February':'Februari','March':'Maret','April':'April',
                 'May':'Mei','June':'Juni','July':'Juli','August':'Agustus',
                 'September':'September','October':'Oktober','November':'November','December':'Desember'}
    for en, id_ in {**days_id, **months_id}.items():
        tgl = tgl.replace(en, id_)
    info_row('Tanggal', tgl)

    pdf.set_font('Helvetica', 'B', 11)
    pdf.set_text_color(30, 60, 180)
    pdf.set_x(15)
    pdf.cell(5, 7, 'Nomor', ln=False)
    pdf.set_x(15)
    pdf.cell(25, 7, 'Nomor', ln=False)
    pdf.cell(3, 7, ':', ln=False)
    pdf.cell(0, 7, f'IN. {invoice.invoice_number.replace("INV","").replace(".",".").strip(".")}', ln=True)

    pdf.set_text_color(0, 0, 0)
    pdf.set_x(15)
    pdf.set_font('Helvetica', 'B', 8)
    pdf.cell(25, 5.5, 'Jatuh Tempo', ln=False)
    pdf.cell(3, 5.5, ':', ln=False)
    jt = invoice.created_at.strftime('%A, %d %B %Y') if invoice.created_at else '-'
    for en, id_ in {**days_id, **months_id}.items():
        jt = jt.replace(en, id_)
    pdf.cell(0, 5.5, jt, ln=True)

    # Right box: Kepada
    pdf.set_xy(105, y_box)
    pdf.set_font('Helvetica', '', 8)
    pdf.cell(0, 5.5, 'Kepada :', ln=True)

    # Find customer name from passengers
    first_pax = invoice.passengers[0].name if invoice.passengers else invoice.agent_name
    pdf.set_xy(105, y_box + 7)
    pdf.set_font('Helvetica', 'B', 12)
    pdf.set_text_color(30, 60, 180)
    pdf.cell(0, 7, (first_pax or '').upper(), ln=True)

    pdf.set_text_color(0, 0, 0)
    pdf.set_font('Helvetica', '', 8)
    pdf.set_x(105)
    pdf.cell(0, 4.5, profile.city, ln=True)
    pdf.set_x(105)
    # Find phone from agent
    agent_phone = invoice.agent.phone if invoice.agent else ''
    pdf.cell(0, 4.5, f'PonSel : {agent_phone}' if agent_phone else '', ln=True)

    # Border around info box
    pdf.set_draw_color(180, 180, 180)
    pdf.set_line_width(0.3)
    pdf.rect(15, 40, 85, 40)
    pdf.rect(102, 40, 93, 40)

    # ── FLIGHT SECTIONS ──────────────────────────────────────────────────
    y_flights = 88
    pdf.set_xy(15, y_flights)

    for f in invoice.flights:
        airline_name = f.airline or ''
        route_label = f"{f.route_from or ''} - {f.route_to or ''}"

        # Format date helper
        def _fmtd(v):
            if not v: return ''
            d = re.sub(r'\D','',str(v))
            return f"{d[:2]}/{d[2:4]}/{d[4:]}" if len(d)==8 else (v or '')
        def _fmtt(v):
            if not v: return ''
            d = re.sub(r'\D','',str(v))
            return f"{d[:2]}:{d[2:]}" if len(d)==4 else (v or '')

        # Airline name header (with color)
        pdf.set_fill_color(240, 255, 240)
        pdf.set_font('Helvetica', 'B', 9)
        pdf.set_text_color(0, 120, 0)
        pdf.cell(0, 6, f"  {airline_name}  {route_label}", ln=True, fill=True)

        # Flight detail row
        pdf.set_font('Helvetica', '', 8)
        pdf.set_text_color(0, 0, 0)
        flight_line = (
            f"  {(f.flight_no or '').upper()}   "
            f"{(f.route_from or '').upper()}  {(f.route_to or '').upper()}  "
            f"{f.flight_class or ''}  {_fmtd(f.departure_date)}  "
            f"{_fmtt(f.departure_time)}  {_fmtt(f.arrival_time)}  OK  "
            f"{invoice.pnr or ''}"
        )
        pdf.cell(0, 5.5, flight_line, ln=True)

        # Passenger rows
        sub_total = 0
        for idx, p in enumerate(invoice.passengers, 1):
            if mode == 'customer':   price = p.sell_price or 0
            elif mode == 'nett':     price = p.nett_price or 0
            else:                    price = (invoice.keep_amount or 0) / max(len(invoice.passengers), 1)
            sub_total += price

            pdf.set_font('Helvetica', '', 8)
            # Row: index, type, name, ticket, price
            pdf.cell(8, 5.5, f"  {idx}.", ln=False)
            pdf.set_font('Helvetica', 'B', 8)
            pdf.set_text_color(0, 60, 160)
            pdf.cell(12, 5.5, p.pax_type or 'Adult', ln=False)
            pdf.cell(70, 5.5, (p.name or '').upper(), ln=False)
            pdf.set_font('Helvetica', '', 8)
            pdf.set_text_color(0, 0, 0)
            ticket_no = p.ticket_number or '000'
            # Extract numeric part
            ticket_display = ticket_no.replace('-','').strip()
            if len(ticket_display) > 6:
                ticket_display = ticket_display[-6:]
            pdf.cell(40, 5.5, ticket_display + '  ' + (invoice.pnr or ''), ln=False)
            pdf.set_font('Helvetica', '', 8)
            pdf.cell(0, 5.5, f"{price:,.2f}", align='R', ln=True)

        # Sub-total per flight (right-aligned, bold)
        pdf.set_font('Helvetica', 'B', 9)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 6, f"{sub_total:,.2f}", align='R', ln=True)
        pdf.ln(3)

    # ── TOTAL SECTION ───────────────────────────────────────────────────
    pdf.set_line_width(0.4)
    pdf.line(15, pdf.get_y(), 195, pdf.get_y())
    pdf.ln(2)

    if mode == 'customer':   total_val = invoice.total_sell or 0
    elif mode == 'nett':     total_val = invoice.total_nett or 0
    else:                    total_val = invoice.keep_amount or 0

    # Terbilang + Total on same row
    terbilang_str = terbilang(total_val)
    pdf.set_font('Helvetica', '', 8)
    pdf.set_text_color(0, 0, 0)
    y_total = pdf.get_y()

    # Left: IDR terbilang
    pdf.set_xy(15, y_total)
    pdf.set_font('Helvetica', '', 8)
    pdf.multi_cell(90, 5, f"IDR : {terbilang_str}", border=1)

    # Right: Total IDR bold
    pdf.set_xy(110, y_total)
    pdf.set_font('Helvetica', 'B', 9)
    pdf.cell(30, 10, 'Total :', align='R', border=0)
    pdf.set_font('Helvetica', 'B', 12)
    pdf.set_text_color(30, 60, 180)
    pdf.cell(55, 10, f"IDR  {total_val:,.2f}", align='R', border=0, ln=True)

    # ── FOOTER / SIGNATURE ───────────────────────────────────────────────
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)
    y_sig = pdf.get_y()

    # Left: HO info
    pdf.set_font('Helvetica', '', 7.5)
    pdf.set_fill_color(245, 245, 245)
    creator_name = invoice.creator.name.upper() if invoice.creator else ''
    from datetime import timezone
    created_local = invoice.created_at.strftime('%A, %d %b %Y / %I:%M:%S %p WIB') if invoice.created_at else ''
    for en, id_ in days_id.items():
        created_local = created_local.replace(en, id_)
    pdf.set_xy(15, y_sig)
    pdf.multi_cell(85, 5, f"HO / {creator_name} / {created_local}", border=1, fill=True)

    # Right: signature boxes
    pdf.set_xy(107, y_sig)
    pdf.set_font('Helvetica', '', 7.5)
    pdf.cell(40, 5, 'Diterima Oleh', border=1, align='C')
    pdf.cell(48, 5, profile.company_name[:28], border=1, align='C', ln=True)

    sig_h = 18
    pdf.set_xy(107, y_sig + 5)
    pdf.cell(40, sig_h, '', border=1)
    pdf.set_xy(147, y_sig + 5)
    pdf.set_font('Helvetica', 'B', 6.5)
    pdf.multi_cell(48, 4, f"{profile.company_name}\n\n\nKantor Pusat", border=1, align='C')

    buf = io.BytesIO()
    pdf.output(buf)
    buf.seek(0)
    return buf

def generate_professional_pdf(invoice, mode='customer'):
    buf = _build_invoice_pdf(invoice, mode)
    suffix = {'customer':'Pelanggan','nett':'Nett','keep':'Keep'}.get(mode,'')
    return send_file(buf, as_attachment=True,
        download_name=f'Invoice_{invoice.invoice_number}_{suffix}.pdf',
        mimetype='application/pdf')


def generate_refund_pdf(refund, mode='customer'):
    from fpdf import FPDF
    pdf = FPDF(); pdf.add_page(); pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_fill_color(220,38,38); pdf.rect(0,0,210,36,'F')
    pdf.set_fill_color(37,99,235); pdf.rect(0,30,210,5,'F')
    pdf.set_fill_color(22,163,74);  pdf.rect(0,35,210,3,'F')
    pdf.set_text_color(255,255,255); pdf.set_font('Helvetica','B',20)
    pdf.set_y(7); pdf.cell(0,10,'REKAP DULU',align='C'); pdf.ln(10)
    pdf.set_font('Helvetica','',8)
    mode_label = {'customer':'Bukti Refund Pelanggan','nett':'Bukti Refund Nett Maskapai','keep':'Bukti Keep/Admin Fee Refund'}
    pdf.cell(0,5,mode_label.get(mode,'Bukti Refund'),align='C')
    pdf.set_text_color(30,41,59); pdf.set_y(46)
    inv = refund.invoice
    pdf.set_font('Helvetica','B',10)
    for label,val in [('No Refund',refund.refund_number),('Tanggal',refund.created_at.strftime('%d %B %Y')),('PNR',refund.pnr),('No Invoice',inv.invoice_number if inv else '-'),('Agent',inv.agent_name if inv else '-')]:
        pdf.cell(55,7,label,0,0); pdf.set_font('Helvetica','',10)
        pdf.cell(0,7,f': {str(val).upper()}',0,1); pdf.set_font('Helvetica','B',10)
    pdf.ln(5)
    pdf.set_fill_color(243,244,246); pdf.cell(0,7,'RINCIAN REFUND',0,1,'L',1)
    pdf.set_font('Helvetica','',10)
    if mode == 'customer':
        rows = [('Harga Invoice Asal', f"Rp {inv.total_sell:,.0f}" if inv else '-'),
                ('Dikembalikan ke Pelanggan', f"Rp {refund.customer_refund:,.0f}")]
    elif mode == 'nett':
        rows = [('Pengembalian dari Maskapai', f"Rp {refund.airline_refund:,.0f}"),
                ('Total Nett', f"Rp {inv.total_nett:,.0f}" if inv else '-')]
    else:
        rows = [('Keep / Admin Fee', f"Rp {refund.keep:,.0f}"),
                ('Profit Refund', f"Rp {refund.profit:,.0f}")]
    for label,val in rows:
        pdf.cell(90,8,label,0,0); pdf.cell(0,8,val,0,1,'R')
    if refund.notes:
        pdf.ln(3); pdf.set_font('Helvetica','I',8)
        pdf.cell(0,6,f'Catatan: {refund.notes}',0,1)
    pdf.ln(18); pdf.set_font('Helvetica','',9)
    pdf.cell(80,5,'Penerima,',align='C'); pdf.cell(50,5,'',0,0)
    pdf.cell(80,5,'Kantor Pusat,',align='C'); pdf.ln(22)
    pdf.cell(80,5,'(................................)',align='C'); pdf.cell(50,5,'',0,0)
    pdf.cell(80,5,'(................................)',align='C')
    buf = io.BytesIO(); pdf.output(buf); buf.seek(0)
    suffix = {'customer':'Pelanggan','nett':'Nett','keep':'Keep'}.get(mode,'')
    return send_file(buf, as_attachment=True,
        download_name=f'Refund_{refund.refund_number}_{suffix}.pdf',
        mimetype='application/pdf')


@scheduler.task('interval', id='fetch_rates', seconds=3600, misfire_grace_time=900)
def scheduled_fetch():
    with app.app_context():
        fetch_exchange_rates()

@scheduler.task('interval', id='fetch_news', seconds=1800, misfire_grace_time=900)
def scheduled_news():
    with app.app_context():
        fetch_aviation_news()

# ================== MODELS ==================
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(10), nullable=False, default='staff')
    last_login = db.Column(db.DateTime)
    phone = db.Column(db.String(20), default='')
    activities = db.relationship('ActivityLog', backref='user', lazy=True)
    def set_password(self, p): self.password_hash = generate_password_hash(p)
    def check_password(self, p): return check_password_hash(self.password_hash, p)

class ActivityLog(db.Model):
    __tablename__ = 'activity_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    action = db.Column(db.String(255))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Invoice(db.Model):
    __tablename__ = 'invoices'
    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(20), unique=True, nullable=False, default='')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    agency_name = db.Column(db.String(100), nullable=False)
    agent_name = db.Column(db.String(100), nullable=False)
    pnr = db.Column(db.String(6), nullable=False)
    adult_count = db.Column(db.Integer, default=0)
    child_count = db.Column(db.Integer, default=0)
    infant_count = db.Column(db.Integer, default=0)
    adult_sell = db.Column(db.Float, default=0)
    adult_nett = db.Column(db.Float, default=0)
    child_sell = db.Column(db.Float, default=0)
    child_nett = db.Column(db.Float, default=0)
    infant_sell = db.Column(db.Float, default=0)
    infant_nett = db.Column(db.Float, default=0)
    service_fee = db.Column(db.Float, default=0)
    baggage_customer_price = db.Column(db.Float, default=0)
    baggage_nett_price = db.Column(db.Float, default=0)
    discount_type = db.Column(db.String(20), default='Manual')
    discount_amount = db.Column(db.Float, default=0)
    keep_type = db.Column(db.String(20), default='Manual')
    keep_amount = db.Column(db.Float, default=0)
    subtotal_sell = db.Column(db.Float, default=0)
    total_sell = db.Column(db.Float, default=0)
    total_nett = db.Column(db.Float, default=0)
    profit = db.Column(db.Float, default=0)
    avg_price_pax = db.Column(db.Float, default=0)
    total_pax = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    flights = db.relationship('FlightSegment', backref='invoice', lazy=True, cascade='all, delete-orphan')
    passengers = db.relationship('Passenger', backref='invoice', lazy=True, cascade='all, delete-orphan')
    creator = db.relationship('User', backref='invoices')
    agent_id = db.Column(db.Integer, db.ForeignKey('agents.id'))
    agency_category_id = db.Column(db.Integer, db.ForeignKey('agency_categories.id'))
    invoice_type = db.Column(db.String(20), default='domestic')
    foreign_currency_id = db.Column(db.Integer, db.ForeignKey('currencies.id'))
    foreign_nett = db.Column(db.Float, default=0)
    exchange_rate = db.Column(db.Float, default=0)
    agent = db.relationship('Agent', backref='invoices')
    agency_category = db.relationship('AgencyCategory', backref='invoices')
    foreign_currency = db.relationship('Currency', backref='invoices')

class FlightSegment(db.Model):
    __tablename__ = 'flight_segments'
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoices.id'), nullable=False)
    airline = db.Column(db.String(50))
    flight_no = db.Column(db.String(20))
    route_from = db.Column(db.String(50))
    route_to = db.Column(db.String(50))
    flight_class = db.Column(db.String(20))
    departure_date = db.Column(db.String(20))
    arrival_date = db.Column(db.String(20))
    departure_time = db.Column(db.String(10))
    arrival_time = db.Column(db.String(10))

class Passenger(db.Model):
    __tablename__ = 'passengers'
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoices.id'), nullable=False)
    name = db.Column(db.String(100))
    pax_type = db.Column(db.String(10))
    ticket_number = db.Column(db.String(50))
    sell_price = db.Column(db.Float, default=0)
    nett_price = db.Column(db.Float, default=0)

class News(db.Model):
    __tablename__ = 'news'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200))
    content = db.Column(db.Text)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    source = db.Column(db.String(100))
    source_url = db.Column(db.String(500))
    image_url = db.Column(db.String(500))

class Refund(db.Model):
    __tablename__ = 'refunds'
    id = db.Column(db.Integer, primary_key=True)
    refund_number = db.Column(db.String(20), unique=True, nullable=False, default='')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoices.id'), nullable=True)
    pnr = db.Column(db.String(20), nullable=False)
    airline_refund = db.Column(db.Float, default=0)   # dari maskapai ke agen
    customer_refund = db.Column(db.Float, default=0)  # dari agen ke pelanggan
    keep = db.Column(db.Float, default=0)             # opsional, biaya admin
    profit = db.Column(db.Float, default=0)           # airline_refund - customer_refund
    notes = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    creator = db.relationship('User', backref='refunds')
    invoice = db.relationship('Invoice', backref='refunds', foreign_keys=[invoice_id])

class AgencyProfile(db.Model):
    __tablename__ = 'agency_profile'
    id = db.Column(db.Integer, primary_key=True)
    company_name  = db.Column(db.String(150), default='PT. Contoh Tour and Travel')
    brand_name    = db.Column(db.String(100), default='TIKET PROFIT')
    address       = db.Column(db.String(255), default='Jl. Contoh Raya No. 1')
    city          = db.Column(db.String(100), default='Denpasar, Bali, Indonesia')
    phone         = db.Column(db.String(50),  default='0361 0000000')
    whatsapp      = db.Column(db.String(50),  default='081200000000')
    fax           = db.Column(db.String(100), default='skype - contoh.agensi')
    email         = db.Column(db.String(100), default='agensi@contohtravel.com')
    logo_filename = db.Column(db.String(200), default='')
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow)

class AgencyCategory(db.Model):
    __tablename__ = 'agency_categories'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(255))

class Agent(db.Model):
    __tablename__ = 'agents'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    agency_category_id = db.Column(db.Integer, db.ForeignKey('agency_categories.id'), nullable=False)
    email = db.Column(db.String(100))
    phone = db.Column(db.String(50))
    address = db.Column(db.String(255))
    credit_balance = db.Column(db.Float, default=0)  # saldo kredit / deposit
    category = db.relationship('AgencyCategory', backref='agents')

class CreditTransaction(db.Model):
    __tablename__ = 'credit_transactions'
    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey('agents.id'), nullable=False)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoices.id'), nullable=True)
    type = db.Column(db.String(10), nullable=False)  # 'debit' or 'credit'
    amount = db.Column(db.Float, nullable=False)
    balance_after = db.Column(db.Float, default=0)
    description = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    agent = db.relationship('Agent', backref='credit_transactions')
    invoice = db.relationship('Invoice', backref='credit_transactions')

class InvoicePayment(db.Model):
    __tablename__ = 'invoice_payments'
    id           = db.Column(db.Integer, primary_key=True)
    invoice_id   = db.Column(db.Integer, db.ForeignKey('invoices.id'), nullable=True)
    refund_id    = db.Column(db.Integer, db.ForeignKey('refunds.id'), nullable=True)
    agent_id     = db.Column(db.Integer, db.ForeignKey('agents.id'), nullable=True)
    amount       = db.Column(db.Float, nullable=False, default=0)
    transfer_no  = db.Column(db.String(100))
    notes        = db.Column(db.String(255))
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    created_by   = db.Column(db.Integer, db.ForeignKey('users.id'))
    invoice = db.relationship('Invoice', backref='payments')
    refund  = db.relationship('Refund',  backref='payments')
    agent   = db.relationship('Agent',   backref='payments')

class Airline(db.Model):
    __tablename__ = 'airlines'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    code = db.Column(db.String(10))
    logo = db.Column(db.String(255))
    type = db.Column(db.String(20), default='domestic')

class AirlineSchedule(db.Model):
    __tablename__ = 'airline_schedules'
    id = db.Column(db.Integer, primary_key=True)
    airline_id = db.Column(db.Integer, db.ForeignKey('airlines.id'), nullable=False)
    flight_no = db.Column(db.String(20), nullable=False)
    route_from = db.Column(db.String(50))
    route_to = db.Column(db.String(50))
    departure_time = db.Column(db.String(10))
    arrival_time = db.Column(db.String(10))
    flight_class = db.Column(db.String(20))
    notes = db.Column(db.String(255))
    airline = db.relationship('Airline', backref='schedules')

class Currency(db.Model):
    __tablename__ = 'currencies'
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(3), unique=True, nullable=False)
    name = db.Column(db.String(50))
    symbol = db.Column(db.String(10))

class ExchangeRate(db.Model):
    __tablename__ = 'exchange_rates'
    id = db.Column(db.Integer, primary_key=True)
    currency_id = db.Column(db.Integer, db.ForeignKey('currencies.id'), nullable=False)
    rate = db.Column(db.Float, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)
    currency = db.relationship('Currency', backref='rates')

class PasswordResetOTP(db.Model):
    __tablename__ = 'password_reset_otps'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), nullable=False)
    otp = db.Column(db.String(6), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)

    def is_valid(self):
        return not self.used and datetime.utcnow() < self.expires_at

# ================== INIT DB (startup - tidak di before_request) ==================
def init_db():
    instance_dir = os.path.join(app.root_path, 'instance')
    os.makedirs(instance_dir, exist_ok=True)
    db.create_all()
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)

    def add_col_if_missing(table, col, col_def):
        try:
            cols = [c['name'] for c in inspector.get_columns(table)]
            if col not in cols:
                with db.engine.connect() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}"))
                    conn.commit()
        except Exception as e:
            print(f"Migrasi {table}.{col}: {e}")

    try:
        add_col_if_missing('news', 'source_url', 'VARCHAR(500)')
        add_col_if_missing('news', 'image_url', 'VARCHAR(500)')
        add_col_if_missing('refunds', 'refund_number', 'VARCHAR(20) DEFAULT ""')
        add_col_if_missing('refunds', 'invoice_id', 'INTEGER REFERENCES invoices(id)')
        add_col_if_missing('refunds', 'airline_refund', 'FLOAT DEFAULT 0')
        add_col_if_missing('refunds', 'profit', 'FLOAT DEFAULT 0')
        add_col_if_missing('agents', 'credit_balance', 'FLOAT DEFAULT 0')
        db.create_all()  # catch any missing tables
    except Exception as e:
        print(f"Migrasi: {e}")

    if not User.query.filter_by(email='admin@agensi.com').first():
        admin = User(email='admin@agensi.com', name='Administrator', role='admin', phone='081200000000')
        admin.set_password('admin123')
        staff = User(email='staff@agensi.com', name='Andi Staff', role='staff')
        staff.set_password('staff123')
        db.session.add_all([admin, staff])
        db.session.commit()

    if News.query.count() == 0:
        samples = [
            News(title='Penerbangan Domestik Naik 20%', content='Jumlah penumpang pesawat domestik meningkat tajam pada kuartal ini seiring pemulihan pariwisata nasional.', source='Aviation News', source_url='#'),
            News(title='Maskapai Buka Rute Baru', content='Rute Jakarta - Labuan Bajo akan beroperasi mulai bulan depan dengan kapasitas penuh.', source='Traveloka', source_url='#'),
            News(title='Tips Hemat Beli Tiket', content='Pesan tiket di hari Selasa atau Rabu untuk mendapatkan harga lebih murah hingga 30%.', source='Kompas Travel', source_url='#'),
        ]
        db.session.add_all(samples)
        db.session.commit()

    if Currency.query.count() == 0:
        currencies = [Currency(code='USD',name='US Dollar',symbol='$'), Currency(code='SGD',name='Singapore Dollar',symbol='S$'), Currency(code='MYR',name='Malaysian Ringgit',symbol='RM'), Currency(code='EUR',name='Euro',symbol='€'), Currency(code='AUD',name='Australian Dollar',symbol='A$')]
        db.session.add_all(currencies)
        db.session.commit()
        defaults = {'USD':15800,'SGD':11700,'MYR':3300,'EUR':17200,'AUD':10200}
        for cur in currencies:
            db.session.add(ExchangeRate(currency_id=cur.id, rate=defaults.get(cur.code,15000)))
        db.session.commit()

    if AgencyProfile.query.count() == 0:
        db.session.add(AgencyProfile())
        db.session.commit()

# Jalankan init DB saat module di-load
with app.app_context():
    try:
        init_db()
        print("[DB] Database berhasil diinisialisasi.")
    except Exception as e:
        print(f"[DB ERROR] Gagal inisialisasi database: {e}")
        import traceback; traceback.print_exc()


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.template_filter('fmt_date')
def fmt_date_filter(val):
    """Format tanggal dari database ke DD/MM/YYYY"""
    if not val: return ''
    digits = re.sub(r'\D', '', str(val))
    if len(digits) == 8:
        return f"{digits[:2]}/{digits[2:4]}/{digits[4:]}"
    return val  # sudah terformat atau format lain

@app.template_filter('fmt_time')
def fmt_time_filter(val):
    """Format jam dari database ke HH:MM"""
    if not val: return ''
    digits = re.sub(r'\D', '', str(val))
    if len(digits) == 4:
        return f"{digits[:2]}:{digits[2:]}"
    if len(digits) == 3:
        return f"0{digits[0]}:{digits[1:]}"
    return val  # sudah terformat

def generate_pnr():
    while True:
        pnr = ''.join(random.choices(string.ascii_uppercase+string.digits, k=6))
        if not Invoice.query.filter_by(pnr=pnr).first():
            return pnr

def fetch_exchange_rates():
    try:
        response = requests.get('https://api.exchangerate-api.com/v4/latest/IDR', timeout=10)
        if response.status_code == 200:
            rates = response.json().get('rates', {})
            for cur in Currency.query.all():
                if cur.code in rates:
                    rate = 1/rates[cur.code]
                    existing = ExchangeRate.query.filter_by(currency_id=cur.id).first()
                    if existing:
                        existing.rate = rate; existing.updated_at = datetime.utcnow()
                    else:
                        db.session.add(ExchangeRate(currency_id=cur.id, rate=rate))
            db.session.commit()
            return True
    except Exception as e:
        print(f"Error fetching rates: {e}")
    return False

# ================== EMAIL HELPER ==================
def send_email(to_email, subject, html_body):
    """Send email via SMTP. Falls back to console print if not configured."""
    mail_user = app.config.get('MAIL_USERNAME', '')
    mail_pass = app.config.get('MAIL_PASSWORD', '')
    if not mail_user or not mail_pass:
        print(f"[EMAIL - no SMTP config] TO: {to_email} | SUBJECT: {subject}\n{html_body}")
        return True
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = app.config['MAIL_FROM']
        msg['To'] = to_email
        msg.attach(MIMEText(html_body, 'html'))
        server = smtplib.SMTP(app.config['MAIL_SERVER'], app.config['MAIL_PORT'])
        server.ehlo()
        server.starttls()
        server.login(mail_user, mail_pass)
        server.sendmail(app.config['MAIL_FROM'], to_email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")
        return False

# ================== AUTH ==================
@app.route('/', methods=['GET'])
def landing():
    from flask_login import current_user as cu
    if cu.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('landing.html')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form.get('email')).first()
        if user and user.check_password(request.form.get('password')):
            login_user(user)
            user.last_login = datetime.utcnow()
            db.session.add(ActivityLog(user_id=user.id, action='Login'))
            db.session.commit()
            flash('Login berhasil', 'success')
            return redirect(url_for('dashboard'))
        flash('Email atau password salah', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/forgot-password', methods=['GET','POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email','').strip().lower()
        user = User.query.filter_by(email=email).first()
        if user:
            # Invalidate old OTPs
            PasswordResetOTP.query.filter_by(email=email, used=False).update({'used': True})
            db.session.commit()
            otp_code = ''.join([str(secrets.randbelow(10)) for _ in range(6)])
            otp_obj = PasswordResetOTP(
                email=email, otp=otp_code,
                expires_at=datetime.utcnow() + timedelta(minutes=10)
            )
            db.session.add(otp_obj)
            db.session.commit()
            html_body = f"""
            <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;padding:32px;background:#f9fafb;border-radius:12px;">
              <div style="text-align:center;margin-bottom:24px;">
                <span style="font-size:1.5rem;font-weight:900;color:#1D4ED8;letter-spacing:.06em;">REKAP DULU</span>
              </div>
              <h2 style="color:#1e293b;font-size:1.2rem;margin-bottom:12px;">Reset Password</h2>
              <p style="color:#475569;font-size:.9rem;margin-bottom:20px;">Gunakan kode OTP berikut untuk mereset password Anda. Kode berlaku selama <strong>10 menit</strong>.</p>
              <div style="background:#1D4ED8;color:#fff;font-size:2.5rem;font-weight:900;letter-spacing:.3em;text-align:center;padding:20px 16px;border-radius:10px;margin:20px 0;">
                {otp_code}
              </div>
              <p style="color:#94a3b8;font-size:.75rem;text-align:center;margin-top:16px;">Jika Anda tidak meminta reset password, abaikan email ini.</p>
            </div>"""
            sent = send_email(email, 'Kode OTP Reset Password — REKAP DULU', html_body)
            if not sent:
                flash('Gagal mengirim email. Hubungi administrator.', 'warning')
        # Always show success to prevent email enumeration
        session['otp_email'] = email
        flash('Jika email terdaftar, kode OTP telah dikirim ke email Anda.', 'info')
        return redirect(url_for('verify_otp'))
    return render_template('forgot_password.html')

@app.route('/verify-otp', methods=['GET','POST'])
def verify_otp():
    email = session.get('otp_email')
    if not email:
        return redirect(url_for('forgot_password'))
    if request.method == 'POST':
        entered = ''.join([
            request.form.get(f'otp{i}','').strip() for i in range(1,7)
        ])
        otp_obj = PasswordResetOTP.query.filter_by(email=email, used=False)\
            .order_by(PasswordResetOTP.created_at.desc()).first()
        if otp_obj and otp_obj.is_valid() and otp_obj.otp == entered:
            otp_obj.used = True
            db.session.commit()
            session['reset_verified_email'] = email
            session.pop('otp_email', None)
            return redirect(url_for('reset_password'))
        flash('Kode OTP tidak valid atau sudah kadaluarsa.', 'danger')
    return render_template('verify_otp.html', email=email)

@app.route('/reset-password', methods=['GET','POST'])
def reset_password():
    email = session.get('reset_verified_email')
    if not email:
        return redirect(url_for('forgot_password'))
    if request.method == 'POST':
        pw = request.form.get('password','')
        pw2 = request.form.get('confirm_password','')
        if len(pw) < 6:
            flash('Password minimal 6 karakter.', 'danger')
        elif pw != pw2:
            flash('Konfirmasi password tidak cocok.', 'danger')
        else:
            user = User.query.filter_by(email=email).first()
            if user:
                user.set_password(pw)
                db.session.commit()
                session.pop('reset_verified_email', None)
                flash('Password berhasil direset. Silakan login.', 'success')
                return redirect(url_for('login'))
            flash('User tidak ditemukan.', 'danger')
    return render_template('reset_password.html')

# ================== DASHBOARD ==================
@app.route('/dashboard')
@login_required
def dashboard():
    news = News.query.order_by(News.date.desc()).limit(12).all()
    rates = []
    for cur in Currency.query.all():
        latest = ExchangeRate.query.filter_by(currency_id=cur.id).order_by(ExchangeRate.updated_at.desc()).first()
        if latest:
            rates.append({'code': cur.code, 'symbol': cur.symbol, 'rate': latest.rate, 'updated_at': latest.updated_at})
    return render_template('dashboard.html', news=news, rates=rates)

@app.route('/api/agents/by_category/<int:cat_id>')
@login_required
def api_agents_by_category(cat_id):
    agents = Agent.query.filter_by(agency_category_id=cat_id).order_by(Agent.name).all()
    return jsonify([{
        'id': a.id, 'name': a.name,
        'email': a.email or '', 'phone': a.phone or '', 'address': a.address or ''
    } for a in agents])

@app.route('/api/refresh-news', methods=['POST'])
@login_required
def api_refresh_news():
    success = fetch_aviation_news()
    return jsonify({'status': 'success' if success else 'error'})

# ================== INVOICE ==================
@app.route('/invoice')
@login_required
def invoice_list():
    if current_user.role == 'admin':
        invoices = Invoice.query.order_by(Invoice.created_at.desc()).all()
    else:
        invoices = Invoice.query.filter_by(user_id=current_user.id).order_by(Invoice.created_at.desc()).all()
    return render_template('invoice_list.html', invoices=invoices)

@app.route('/invoice/domestik/create', methods=['GET','POST'])
@login_required
def invoice_domestik_create():
    if request.method == 'POST':
        return process_invoice(None)
    return render_template('invoice_wizard.html', invoice=None, flights=[], passengers=[],
        type='domestic', categories=AgencyCategory.query.all(),
        agents=Agent.query.all(), airlines=Airline.query.filter_by(type='domestic').all(),
        currencies=Currency.query.all())

@app.route('/invoice/internasional/create', methods=['GET','POST'])
@login_required
def invoice_internasional_create():
    if request.method == 'POST':
        return process_invoice(None)
    return render_template('invoice_wizard.html', invoice=None, flights=[], passengers=[],
        type='international', categories=AgencyCategory.query.all(),
        agents=Agent.query.all(), airlines=Airline.query.filter_by(type='international').all(),
        currencies=Currency.query.all())

@app.route('/invoice/edit/<int:id>', methods=['GET','POST'])
@login_required
def edit_invoice(id):
    inv = Invoice.query.get_or_404(id)
    if inv.user_id != current_user.id:
        flash('Akses ditolak', 'danger')
        return redirect(url_for('arsip'))
    if request.method == 'POST':
        return process_invoice(inv)
    return render_template('invoice_wizard.html', invoice=inv, flights=inv.flights, passengers=inv.passengers,
        type=inv.invoice_type, categories=AgencyCategory.query.all(),
        agents=Agent.query.all(), airlines=Airline.query.all(), currencies=Currency.query.all())

# *** FIX: Route delete invoice yang sebelumnya tidak ada ***
@app.route('/invoice/delete/<int:id>', methods=['POST'])
@login_required
def delete_invoice(id):
    inv = Invoice.query.get_or_404(id)
    if inv.user_id != current_user.id and current_user.role != 'admin':
        return jsonify({'status': 'error', 'message': 'Akses ditolak'}), 403
    # Jika ada agent, kembalikan credit
    if inv.agent_id:
        agent = Agent.query.get(inv.agent_id)
        if agent:
            agent.credit_balance += inv.total_sell
            db.session.add(CreditTransaction(
                agent_id=agent.id, invoice_id=inv.id, type='credit',
                amount=inv.total_sell, balance_after=agent.credit_balance,
                description=f'VOID Invoice {inv.invoice_number}'
            ))
    db.session.delete(inv)
    db.session.commit()
    return jsonify({'status': 'success', 'message': f'Invoice {inv.invoice_number} berhasil di-void'})

def process_invoice(invoice):
    form = request.form
    action = form.get('action', 'save')
    agent_id = form.get('agent_id')
    agency_category_id = form.get('agency_category_id')
    invoice_type = form.get('invoice_type', 'domestic')
    def uc(v): return (v or '').strip().upper()
    pnr = uc(form.get('pnr')) or generate_pnr()

    agent_obj = Agent.query.get(int(agent_id)) if agent_id and agent_id.isdigit() else None
    agency_name = agent_obj.category.name if (agent_obj and agent_obj.category) else current_user.name
    agent_name = agent_obj.name if agent_obj else current_user.name

    airlines    = form.getlist('airline[]')
    flight_nos  = form.getlist('flight_no[]')
    route_froms = form.getlist('route_from[]')
    route_tos   = form.getlist('route_to[]')
    classes     = form.getlist('class[]')
    dep_dates   = form.getlist('dep_date[]')
    arr_dates   = form.getlist('arr_date[]')
    dep_times   = form.getlist('dep_time[]')
    arr_times   = form.getlist('arr_time[]')
    pax_names   = form.getlist('pax_name[]')
    pax_types   = form.getlist('pax_type[]')
    ticket_nums = form.getlist('ticket_number[]')

    adult_count = child_count = infant_count = 0
    for i, name in enumerate(pax_names):
        if name.strip():
            pt = pax_types[i] if i < len(pax_types) else 'Adult'
            if pt == 'Adult':  adult_count  += 1
            elif pt == 'Child': child_count += 1
            elif pt == 'Infant': infant_count += 1

    adult_sell  = clean_price(form.get('adult_sell', 0))
    child_sell  = clean_price(form.get('child_sell', 0))
    infant_sell = clean_price(form.get('infant_sell', 0))
    service_fee      = clean_price(form.get('service_fee', 0))
    baggage_customer = clean_price(form.get('baggage_customer', 0))
    baggage_nett     = clean_price(form.get('baggage_nett', 0))
    discount_amount  = clean_price(form.get('discount_amount', 0))
    keep_amount      = clean_price(form.get('keep_amount', 0))
    discount_type    = form.get('discount_type', 'Manual')
    keep_type        = form.get('keep_type', 'Manual')
    foreign_currency_id = form.get('foreign_currency_id')
    exchange_rate       = clean_price(form.get('exchange_rate', 0))

    # Semua harga selalu dalam Rupiah (internasional maupun domestik)
    adult_nett  = clean_price(form.get('adult_nett', 0))
    child_nett  = clean_price(form.get('child_nett', 0))
    infant_nett = clean_price(form.get('infant_nett', 0))

    seat_sell    = adult_count*adult_sell  + child_count*child_sell  + infant_count*infant_sell
    seat_nett    = adult_count*adult_nett  + child_count*child_nett  + infant_count*infant_nett
    subtotal_sell = seat_sell + service_fee + baggage_customer
    total_sell   = subtotal_sell - discount_amount - keep_amount
    total_nett   = seat_nett + baggage_nett
    profit       = total_sell - total_nett
    total_pax    = adult_count + child_count + infant_count
    avg_price    = total_sell / total_pax if total_pax else 0

    is_new = (invoice is None)

    try:
        if is_new:
            invoice = Invoice(
                user_id=current_user.id,
                invoice_number=generate_invoice_number(),
                pnr=pnr.upper() if pnr else '',
                agency_name=(agency_name.upper() if agency_name else current_user.name.upper()),
                agent_name=(agent_name.upper() if agent_name else current_user.name.upper()),
            )
            db.session.add(invoice)
            db.session.flush()  # get invoice.id before adding children

        # Auto-uppercase text fields
        invoice.agency_name    = agency_name.upper() if agency_name else current_user.name.upper()
        invoice.agent_name     = agent_name.upper() if agent_name else current_user.name.upper()
        invoice.agent_id       = int(agent_id) if agent_id and agent_id.isdigit() else None
        invoice.agency_category_id = int(agency_category_id) if agency_category_id and agency_category_id.isdigit() else None
        invoice.invoice_type   = invoice_type
        invoice.pnr            = pnr.upper() if pnr else ''
        invoice.adult_count    = adult_count
        invoice.child_count    = child_count
        invoice.infant_count   = infant_count
        invoice.adult_sell     = adult_sell;  invoice.adult_nett  = adult_nett
        invoice.child_sell     = child_sell;  invoice.child_nett  = child_nett
        invoice.infant_sell    = infant_sell; invoice.infant_nett = infant_nett
        invoice.service_fee    = service_fee
        invoice.baggage_customer_price = baggage_customer
        invoice.baggage_nett_price     = baggage_nett
        invoice.discount_type  = discount_type; invoice.discount_amount = discount_amount
        invoice.keep_type      = keep_type;     invoice.keep_amount     = keep_amount
        invoice.subtotal_sell  = subtotal_sell
        invoice.total_sell     = total_sell
        invoice.total_nett     = total_nett
        invoice.profit         = profit
        invoice.avg_price_pax  = avg_price
        invoice.total_pax      = total_pax
        if invoice_type == 'international':
            invoice.foreign_currency_id = int(foreign_currency_id) if foreign_currency_id and foreign_currency_id.isdigit() else None
            invoice.exchange_rate = exchange_rate

        # Replace flights and passengers
        FlightSegment.query.filter_by(invoice_id=invoice.id).delete()
        Passenger.query.filter_by(invoice_id=invoice.id).delete()
        db.session.flush()

        def _uc(lst, idx):
            return lst[idx].strip().upper() if idx < len(lst) and lst[idx] else ''

        for i in range(len(airlines)):
            if airlines[i] and airlines[i].strip():
                db.session.add(FlightSegment(
                    invoice_id=invoice.id,
                    airline=_uc(airlines, i),
                    flight_no=_uc(flight_nos, i),
                    route_from=_uc(route_froms, i),
                    route_to=_uc(route_tos, i),
                    flight_class=_uc(classes, i),
                    departure_date=_uc(dep_dates, i),
                    arrival_date=_uc(arr_dates, i),
                    departure_time=_uc(dep_times, i),
                    arrival_time=_uc(arr_times, i)
                ))
                # Auto-save ke jadwal maskapai jika belum ada
                fn_upper = _uc(flight_nos, i)
                rf_upper = _uc(route_froms, i)
                rt_upper = _uc(route_tos, i)
                if fn_upper:
                    airline_obj = Airline.query.filter(db.func.upper(Airline.name) == _uc(airlines, i)).first()
                    existing_sched = AirlineSchedule.query.filter(
                        db.func.upper(AirlineSchedule.flight_no) == fn_upper
                    ).first()
                    if not existing_sched and airline_obj:
                        db.session.add(AirlineSchedule(
                            airline_id=airline_obj.id,
                            flight_no=fn_upper,
                            route_from=rf_upper,
                            route_to=rt_upper,
                            departure_time=_uc(dep_times, i),
                            arrival_time=_uc(arr_times, i),
                            flight_class=_uc(classes, i)
                        ))

        for i, name in enumerate(pax_names):
            if name.strip():
                pt  = pax_types[i]   if i < len(pax_types)   else 'Adult'
                sp  = adult_sell  if pt=='Adult' else (child_sell  if pt=='Child' else infant_sell)
                np_ = adult_nett  if pt=='Adult' else (child_nett  if pt=='Child' else infant_nett)
                db.session.add(Passenger(
                    invoice_id=invoice.id,
                    name=name.strip().upper(),
                    pax_type=pt,
                    ticket_number=(ticket_nums[i].strip().upper() if i < len(ticket_nums) else ''),
                    sell_price=sp, nett_price=np_
                ))

        # Debit agent credit balance for new invoices only
        if is_new and agent_obj:
            agent_obj.credit_balance -= total_sell
            db.session.add(CreditTransaction(
                agent_id=agent_obj.id, invoice_id=invoice.id, type='debit',
                amount=total_sell, balance_after=agent_obj.credit_balance,
                description=f'Invoice {invoice.invoice_number}'
            ))

        db.session.commit()

        if action == 'pdf':
            return generate_professional_pdf(invoice)

        flash(f'Invoice {invoice.invoice_number} berhasil disimpan ✓', 'success')
        return redirect(url_for('edit_invoice', id=invoice.id))

    except Exception as e:
        db.session.rollback()
        import traceback
        err_detail = traceback.format_exc()
        print(f'[INVOICE SAVE ERROR] {e}\n{err_detail}')
        flash(f'Gagal menyimpan invoice: {str(e)[:200]}', 'danger')
        # Redirect back to the form if it was a new invoice
        if invoice is None or not hasattr(invoice, 'id') or not invoice.id:
            return redirect(url_for('invoice_list'))
        return redirect(url_for('invoice_list'))


@app.route('/invoice/cetak/<int:id>')
@login_required
def cetak_invoice_pdf(id):
    inv = Invoice.query.get_or_404(id)
    if inv.user_id != current_user.id and current_user.role != 'admin':
        flash('Akses ditolak', 'danger')
        return redirect(url_for('arsip'))
    mode = request.args.get('mode','customer')  # customer | nett | keep
    if mode == 'keep' and not inv.keep_amount:
        flash('Invoice ini tidak memiliki keep amount','danger')
        return redirect(url_for('arsip'))
    return generate_professional_pdf(inv, mode)

# ================== REFUND ==================
@app.route('/refund')
@login_required
def refund_list():
    if current_user.role == 'admin':
        refunds = Refund.query.order_by(Refund.created_at.desc()).all()
        invoices = Invoice.query.order_by(Invoice.created_at.desc()).all()
    else:
        refunds = Refund.query.filter_by(user_id=current_user.id).order_by(Refund.created_at.desc()).all()
        invoices = Invoice.query.filter_by(user_id=current_user.id).order_by(Invoice.created_at.desc()).all()
    return render_template('refund_list.html', refunds=refunds, invoices=invoices)

@app.route('/refund/create', methods=['GET','POST'])
@login_required
def create_refund():
    invoices = Invoice.query.filter_by(user_id=current_user.id).order_by(Invoice.created_at.desc()).all()
    if request.method == 'POST':
        invoice_id = request.form.get('invoice_id')
        inv = Invoice.query.get(int(invoice_id)) if invoice_id else None
        pnr = inv.pnr if inv else request.form.get('pnr', '')
        airline_refund = clean_price(request.form.get('airline_refund', 0))
        customer_refund = clean_price(request.form.get('customer_refund', 0))
        keep = clean_price(request.form.get('keep', 0))
        profit = airline_refund - customer_refund
        refund = Refund(
            refund_number=generate_refund_number(),
            user_id=current_user.id, invoice_id=inv.id if inv else None,
            pnr=pnr, airline_refund=airline_refund,
            customer_refund=customer_refund, keep=keep, profit=profit,
            notes=request.form.get('notes', '')
        )
        db.session.add(refund)
        db.session.commit()
        flash('Refund berhasil dicatat', 'success')
        return redirect(url_for('refund_list'))
    return render_template('refund_form.html', refund=None, invoices=invoices)

@app.route('/api/invoice-detail/<int:id>')
@login_required
def api_invoice_detail(id):
    inv = Invoice.query.get_or_404(id)
    return jsonify({
        'pnr': inv.pnr,
        'invoice_number': inv.invoice_number,
        'agent_name': inv.agent_name,
        'total_sell': inv.total_sell,
        'total_nett': inv.total_nett,
        'profit': inv.profit,
        'passengers': [{'name': p.name, 'pax_type': p.pax_type, 'ticket_number': p.ticket_number, 'sell_price': p.sell_price} for p in inv.passengers]
    })

@app.route('/refund/edit/<int:id>', methods=['GET','POST'])
@login_required
def edit_refund(id):
    refund = Refund.query.get_or_404(id)
    if refund.user_id != current_user.id:
        flash('Akses ditolak', 'danger')
        return redirect(url_for('refund_list'))
    invoices = Invoice.query.filter_by(user_id=current_user.id).order_by(Invoice.created_at.desc()).all()
    if request.method == 'POST':
        invoice_id = request.form.get('invoice_id')
        inv = Invoice.query.get(int(invoice_id)) if invoice_id else refund.invoice
        refund.invoice_id = inv.id if inv else None
        refund.pnr = inv.pnr if inv else request.form.get('pnr', '')
        refund.airline_refund = clean_price(request.form.get('airline_refund', 0))
        refund.customer_refund = clean_price(request.form.get('customer_refund', 0))
        refund.keep = clean_price(request.form.get('keep', 0))
        refund.profit = refund.airline_refund - refund.customer_refund
        refund.notes = request.form.get('notes', '')
        db.session.commit()
        flash('Refund diperbarui', 'success')
        return redirect(url_for('refund_list'))
    return render_template('refund_form.html', refund=refund, invoices=invoices)

@app.route('/refund/delete/<int:id>', methods=['POST'])
@login_required
def delete_refund(id):
    refund = Refund.query.get_or_404(id)
    if refund.user_id != current_user.id:
        return jsonify({'status':'error','message':'Akses ditolak'}),403
    db.session.delete(refund)
    db.session.commit()
    return jsonify({'status':'success','message':'Refund dihapus'})

@app.route('/refund/cetak/<int:id>')
@login_required
def cetak_refund_pdf(id):
    refund = Refund.query.get_or_404(id)
    if refund.user_id != current_user.id and current_user.role != 'admin':
        flash('Akses ditolak', 'danger')
        return redirect(url_for('refund_list'))
    mode = request.args.get('mode','customer')
    if mode == 'keep' and not refund.keep:
        flash('Refund ini tidak memiliki keep amount','danger')
        return redirect(url_for('refund_list'))
    return generate_refund_pdf(refund, mode)

@app.route('/arsip-refund')
@login_required
def arsip_refund():
    if current_user.role == 'admin':
        refunds = Refund.query.order_by(Refund.created_at.desc()).all()
    else:
        refunds = Refund.query.filter_by(user_id=current_user.id).order_by(Refund.created_at.desc()).all()
    grouped = {}
    for r in refunds:
        key = r.created_at.strftime('%B %Y')
        grouped.setdefault(key, []).append(r)
    return render_template('arsip_refund.html', grouped=grouped)

# ================== ARSIP INVOICE ==================
@app.route('/arsip')
@login_required
def arsip():
    invoices = Invoice.query.all()
    agency_groups = {}
    agent_groups = {}
    for inv in invoices:
        agency_groups.setdefault(inv.agency_name, []).append(inv)
        agent_groups.setdefault(inv.agent_name, []).append(inv)
    return render_template('arsip.html', agency_groups=agency_groups, agent_groups=agent_groups)

# ================== PELUNASAN ==================
@app.route('/pelunasan')
@login_required
def pelunasan():
    agents = Agent.query.order_by(Agent.name).all()
    return render_template('pelunasan.html', agents=agents)

@app.route('/pelunasan/topup', methods=['POST'])
@login_required
def pelunasan_topup():
    agent_id = request.form.get('agent_id')
    amount = clean_price(request.form.get('amount', 0))
    notes = request.form.get('notes', '')
    if not agent_id or amount <= 0:
        return jsonify({'status': 'error', 'message': 'Data tidak valid'}), 400
    agent = Agent.query.get_or_404(int(agent_id))
    agent.credit_balance += amount
    db.session.add(CreditTransaction(
        agent_id=agent.id, type='credit', amount=amount,
        balance_after=agent.credit_balance,
        description=notes or f'Top-up deposit'
    ))
    db.session.commit()
    return jsonify({'status': 'success', 'message': f'Top-up Rp {amount:,.0f} berhasil', 'balance': agent.credit_balance})

@app.route('/pelunasan/history/<int:agent_id>')
@login_required
def pelunasan_history(agent_id):
    agent = Agent.query.get_or_404(agent_id)
    transactions = CreditTransaction.query.filter_by(agent_id=agent_id).order_by(CreditTransaction.created_at.desc()).limit(50).all()
    return render_template('pelunasan_history.html', agent=agent, transactions=transactions)

# ================== PROFIL ==================
@app.route('/profil', methods=['GET','POST'])
@login_required
def profil():
    if request.method == 'POST':
        name = request.form.get('name','').strip()
        phone = request.form.get('phone','').strip()
        if not name:
            flash('Nama tidak boleh kosong','danger')
        else:
            current_user.name = name
            if phone: current_user.phone = phone
            db.session.commit()
            flash('Profil berhasil diperbarui','success')
        return redirect(url_for('profil'))
    return render_template('profile.html')

@app.route('/settings')
@login_required
def settings():
    profile = AgencyProfile.query.first() or AgencyProfile()
    return render_template('settings.html', profile=profile)

@app.route('/settings/profile', methods=['POST'])
@login_required
def settings_profile():
    if current_user.role != 'admin':
        flash('Hanya admin yang bisa mengubah profil', 'danger')
        return redirect(url_for('settings'))
    profile = AgencyProfile.query.first()
    if not profile:
        profile = AgencyProfile(); db.session.add(profile)
    profile.company_name = request.form.get('company_name', profile.company_name)
    profile.brand_name   = request.form.get('brand_name',   profile.brand_name)
    profile.address      = request.form.get('address',      profile.address)
    profile.city         = request.form.get('city',         profile.city)
    profile.phone        = request.form.get('phone',        profile.phone)
    profile.whatsapp     = request.form.get('whatsapp',     profile.whatsapp)
    profile.fax          = request.form.get('fax',          profile.fax)
    profile.email        = request.form.get('email',        profile.email)
    # Logo upload
    if 'logo' in request.files and request.files['logo'].filename:
        f = request.files['logo']
        ext = os.path.splitext(f.filename)[1].lower()
        if ext in ['.png', '.jpg', '.jpeg', '.gif']:
            fname = f'agency_logo{ext}'
            fpath = os.path.join(app.config['UPLOAD_FOLDER'], fname)
            f.save(fpath)
            profile.logo_filename = fname
    profile.updated_at = datetime.utcnow()
    db.session.commit()
    flash('Profil perusahaan berhasil disimpan', 'success')
    return redirect(url_for('settings'))

# ================== REKAPAN ==================
@app.route('/pelunasan/lunas', methods=['GET','POST'])
@login_required
def pelunasan_lunas():
    if request.method == 'POST':
        ref_type   = request.form.get('ref_type','invoice')  # invoice | refund
        ref_id     = request.form.get('ref_id')
        amount     = clean_price(request.form.get('amount',0))
        transfer_no = (request.form.get('transfer_no') or '').strip().upper()
        notes      = request.form.get('notes','').strip()
        if not ref_id:
            return jsonify({'status':'error','message':'Pilih invoice/refund terlebih dahulu'})
        inv_obj = refund_obj = agent_obj = None
        if ref_type == 'invoice':
            inv_obj = Invoice.query.get(int(ref_id))
            if inv_obj: agent_obj = inv_obj.agent
        else:
            refund_obj = Refund.query.get(int(ref_id))
            if refund_obj and refund_obj.invoice: agent_obj = refund_obj.invoice.agent
        payment = InvoicePayment(
            invoice_id=inv_obj.id if inv_obj else None,
            refund_id=refund_obj.id if refund_obj else None,
            agent_id=agent_obj.id if agent_obj else None,
            amount=amount, transfer_no=transfer_no, notes=notes,
            created_by=current_user.id
        )
        db.session.add(payment)
        db.session.commit()
        return jsonify({'status':'success','message':f'Pembayaran Rp {amount:,.0f} dicatat dengan no transfer {transfer_no}'})

    # GET: search + recent invoices list
    q = request.args.get('q','').strip().upper()
    invoices = refunds = []
    if q:
        invoices = Invoice.query.filter(
            db.or_(Invoice.invoice_number.ilike(f'%{q}%'),
                   Invoice.pnr.ilike(f'%{q}%'))
        ).order_by(Invoice.created_at.desc()).limit(10).all()
        refunds = Refund.query.filter(
            db.or_(Refund.refund_number.ilike(f'%{q}%'),
                   Refund.pnr.ilike(f'%{q}%'))
        ).order_by(Refund.created_at.desc()).limit(10).all()
    # Always load recent invoices for the scrollable list
    recent_invoices = Invoice.query.order_by(Invoice.created_at.desc()).limit(50).all()
    return render_template('pelunasan_lunas.html', invoices=invoices, refunds=refunds, q=q, recent_invoices=recent_invoices)

@app.route('/api/search-inv')
@login_required
def api_search_inv():
    q = request.args.get('q','').strip().upper()
    if not q: return jsonify([])
    invs = Invoice.query.filter(
        db.or_(Invoice.invoice_number.ilike(f'%{q}%'), Invoice.pnr.ilike(f'%{q}%'))
    ).limit(8).all()
    refs = Refund.query.filter(
        db.or_(Refund.refund_number.ilike(f'%{q}%'), Refund.pnr.ilike(f'%{q}%'))
    ).limit(5).all()
    results = []
    for inv in invs:
        results.append({'type':'invoice','id':inv.id,'number':inv.invoice_number,'pnr':inv.pnr,'agent':inv.agent_name,'amount':inv.total_sell,'label':f'{inv.invoice_number} | {inv.pnr} | {inv.agent_name}'})
    for r in refs:
        results.append({'type':'refund','id':r.id,'number':r.refund_number,'pnr':r.pnr,'amount':r.customer_refund,'label':f'{r.refund_number} | {r.pnr} (REFUND)'})
    return jsonify(results)

@app.route('/rekapan')
@login_required
def rekapan():
    if current_user.role != 'admin':
        flash('Hanya admin','danger')
        return redirect(url_for('dashboard'))

    start_str = request.args.get('start','')
    end_str   = request.args.get('end','')
    today     = datetime.utcnow().date()
    week_start  = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    # Invoice query with optional filter
    iq = Invoice.query
    if start_str: iq = iq.filter(db.func.date(Invoice.created_at) >= start_str)
    if end_str:   iq = iq.filter(db.func.date(Invoice.created_at) <= end_str)
    all_invs = iq.all()

    # Refund query with optional filter
    rq = Refund.query
    if start_str: rq = rq.filter(db.func.date(Refund.created_at) >= start_str)
    if end_str:   rq = rq.filter(db.func.date(Refund.created_at) <= end_str)
    all_refs = rq.all()

    def inv_period(invs):
        return dict(jual=sum(i.total_sell for i in invs),
                    nett=sum(i.total_nett for i in invs),
                    profit=sum(i.profit for i in invs), count=len(invs))

    def ref_period(refs):
        return dict(airline=sum(r.airline_refund for r in refs),
                    customer=sum(r.customer_refund for r in refs),
                    profit=sum(r.profit for r in refs), count=len(refs))

    # Invoice period rows
    def inv_rows_for(invs_all):
        today_invs = [i for i in invs_all if i.created_at.date() == today]
        week_invs  = [i for i in invs_all if i.created_at.date() >= week_start]
        month_invs = [i for i in invs_all if i.created_at.date() >= month_start]
        rows = []
        for label, subset in [('Hari Ini', today_invs),('Minggu Ini', week_invs),('Bulan Ini', month_invs)]:
            d = inv_period(subset); d['label'] = label; rows.append(d)
        return rows

    def ref_rows_for(refs_all):
        today_refs = [r for r in refs_all if r.created_at.date() == today]
        week_refs  = [r for r in refs_all if r.created_at.date() >= week_start]
        month_refs = [r for r in refs_all if r.created_at.date() >= month_start]
        rows = []
        for label, subset in [('Hari Ini', today_refs),('Minggu Ini', week_refs),('Bulan Ini', month_refs)]:
            d = ref_period(subset); d['label'] = label; rows.append(d)
        return rows

    # 7-day chart — separate inv and ref
    daily_labels, daily_inv_profits, daily_ref_profits = [], [], []
    for i in range(6,-1,-1):
        day = today - timedelta(days=i)
        d_invs = [x for x in all_invs if x.created_at.date() == day]
        d_refs = [x for x in all_refs if x.created_at.date() == day]
        daily_labels.append(day.strftime('%a %d/%m'))
        daily_inv_profits.append(sum(x.profit for x in d_invs))
        daily_ref_profits.append(sum(x.profit for x in d_refs))

    # Aggregate totals
    inv_total = inv_period(all_invs)
    ref_total = ref_period(all_refs)

    today_invs_d = [i for i in all_invs if i.created_at.date() == today]
    today_refs_d = [r for r in all_refs if r.created_at.date() == today]
    week_invs_d  = [i for i in all_invs if i.created_at.date() >= week_start]
    week_refs_d  = [r for r in all_refs if r.created_at.date() >= week_start]

    return render_template('rekapan.html',
        start=start_str, end=end_str,
        inv_total_sell=inv_total['jual'],
        inv_total_nett=inv_total['nett'],
        inv_total_profit=inv_total['profit'],
        ref_total_airline=ref_total['airline'],
        ref_total_customer=ref_total['customer'],
        ref_total_profit=ref_total['profit'],
        inv_periods=inv_rows_for(all_invs),
        ref_periods=ref_rows_for(all_refs),
        today_profit=sum(i.profit for i in today_invs_d)+sum(r.profit for r in today_refs_d),
        week_profit=sum(i.profit for i in week_invs_d)+sum(r.profit for r in week_refs_d),
        month_profit=inv_total['profit']+ref_total['profit'],
        daily_labels=daily_labels,
        daily_inv_profits=daily_inv_profits,
        daily_ref_profits=daily_ref_profits)

@app.route('/rekapan/export-pdf', methods=['POST'])
@login_required
def export_pdf():
    if current_user.role != 'admin': return redirect(url_for('dashboard'))
    start = request.form.get('start_date'); end = request.form.get('end_date')
    q = Invoice.query
    if start: q=q.filter(db.func.date(Invoice.created_at)>=start)
    if end: q=q.filter(db.func.date(Invoice.created_at)<=end)
    invoices = q.order_by(Invoice.created_at.desc()).all()
    pdf = FPDF(); pdf.add_page()
    pdf.set_font('Arial','B',14)
    pdf.cell(0,10,'Laporan Profit — REKAP DULU',ln=1,align='C')
    pdf.ln(5); pdf.set_font('Arial','',10)
    for w,h,t in [(40,8,'PNR'),(40,8,'Tanggal'),(30,8,'Total Jual'),(30,8,'Total Nett'),(30,8,'Profit')]:
        pdf.cell(w,h,t,1)
    pdf.ln()
    tj=tn=tp=0
    for inv in invoices:
        pdf.cell(40,8,inv.pnr,1); pdf.cell(40,8,inv.created_at.strftime('%Y-%m-%d'),1)
        pdf.cell(30,8,f'{inv.total_sell:,.0f}',1); pdf.cell(30,8,f'{inv.total_nett:,.0f}',1)
        pdf.cell(30,8,f'{inv.profit:,.0f}',1); pdf.ln()
        tj+=inv.total_sell; tn+=inv.total_nett; tp+=inv.profit
    pdf.set_font('Arial','B',10)
    pdf.cell(40,8,'TOTAL',1); pdf.cell(40,8,'',1)
    pdf.cell(30,8,f'{tj:,.0f}',1); pdf.cell(30,8,f'{tn:,.0f}',1); pdf.cell(30,8,f'{tp:,.0f}',1)
    buf=io.BytesIO(); pdf.output(buf); buf.seek(0)
    return send_file(buf,as_attachment=True,download_name='laporan_profit.pdf',mimetype='application/pdf')

@app.route('/rekapan/invoice-detail')
@login_required
def rekapan_invoice_detail():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    start = request.args.get('start', '')
    end   = request.args.get('end', '')
    q = Invoice.query
    if start: q = q.filter(db.func.date(Invoice.created_at) >= start)
    if end:   q = q.filter(db.func.date(Invoice.created_at) <= end)
    invoices = q.order_by(Invoice.created_at.desc()).all()
    total_sell   = sum(i.total_sell for i in invoices)
    total_nett   = sum(i.total_nett for i in invoices)
    total_profit = sum(i.profit for i in invoices)
    return render_template('rekapan_invoice_detail.html',
        invoices=invoices, total_sell=total_sell,
        total_nett=total_nett, total_profit=total_profit,
        start=start, end=end)

@app.route('/rekapan/refund-detail')
@login_required
def rekapan_refund_detail():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    start = request.args.get('start', '')
    end   = request.args.get('end', '')
    q = Refund.query
    if start: q = q.filter(db.func.date(Refund.created_at) >= start)
    if end:   q = q.filter(db.func.date(Refund.created_at) <= end)
    refunds = q.order_by(Refund.created_at.desc()).all()
    total_airline  = sum(r.airline_refund for r in refunds)
    total_customer = sum(r.customer_refund for r in refunds)
    total_profit   = sum(r.profit for r in refunds)
    return render_template('rekapan_refund_detail.html',
        refunds=refunds, total_airline=total_airline,
        total_customer=total_customer, total_profit=total_profit,
        start=start, end=end)

@app.route('/debug/invoice-test', methods=['GET','POST'])
@login_required
def debug_invoice_test():
    if current_user.role != 'admin':
        return 'Admin only', 403
    if request.method == 'POST':
        # Try a minimal save to see what error occurs
        try:
            inv_num = generate_invoice_number()
            test = Invoice(
                user_id=current_user.id,
                invoice_number=inv_num + '_TEST',
                pnr='TEST123',
                invoice_type='domestic',
                agency_name='TEST',
                agent_name='TEST',
                total_sell=0, total_nett=0, profit=0,
                subtotal_sell=0, adult_count=0, child_count=0, infant_count=0,
                total_pax=0, avg_price_pax=0, adult_sell=0, adult_nett=0,
                child_sell=0, child_nett=0, infant_sell=0, infant_nett=0,
                service_fee=0, baggage_customer_price=0, baggage_nett_price=0,
                discount_amount=0, keep_amount=0, discount_type='Manual', keep_type='Manual'
            )
            db.session.add(test)
            db.session.flush()
            inv_id = test.id
            db.session.rollback()  # Don't actually save test data
            return f'OK - DB test save worked. Invoice ID would be {inv_id}. invoice_number={inv_num}'
        except Exception as e:
            import traceback
            return f'GAGAL: {e}<br><pre>{traceback.format_exc()}</pre>', 500
    cols = [c.name for c in Invoice.__table__.columns]
    return f'<h2>Invoice Columns ({len(cols)}):</h2><ul>' + ''.join(f'<li>{c}</li>' for c in cols) + '</ul><br><form method=POST><button type=submit>Test Save</button></form>'

# ================== AGENT ==================
@app.route('/agent')
@login_required
def agent():
    if current_user.role != 'admin':
        flash('Hanya admin','danger'); return redirect(url_for('dashboard'))
    invoices = Invoice.query.all()
    grouped = {}
    for inv in invoices:
        agent_obj = inv.agent
        cat_id = agent_obj.agency_category_id if agent_obj else 0
        cat_name = agent_obj.category.name if (agent_obj and agent_obj.category) else 'Tanpa Kategori'
        ag_name = agent_obj.name if agent_obj else inv.agent_name
        if cat_id not in grouped:
            grouped[cat_id] = {'name': cat_name, 'agents': {}}
        if ag_name not in grouped[cat_id]['agents']:
            grouped[cat_id]['agents'][ag_name] = []
        grouped[cat_id]['agents'][ag_name].append(inv)
    return render_template('agent.html', grouped=grouped)

@app.route('/agent/export-excel/by-agent/<int:agent_id>')
@login_required
def export_agent_excel(agent_id):
    if current_user.role != 'admin': return redirect(url_for('dashboard'))
    agent_obj = Agent.query.get_or_404(agent_id)
    invoices = Invoice.query.filter_by(agent_id=agent_id).order_by(Invoice.created_at.desc()).all()
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = agent_obj.name[:31]
    # Title
    ws.merge_cells('A1:K1')
    ws['A1'] = f'DATA TIKET AGENT: {agent_obj.name.upper()}'
    ws['A1'].font = Font(bold=True, size=14, color='FFFFFF')
    ws['A1'].fill = PatternFill('solid', fgColor='1D4ED8')
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 28
    ws.merge_cells('A2:K2')
    ws['A2'] = f'Dicetak: {datetime.utcnow().strftime("%d %B %Y %H:%M")} | Kategori: {agent_obj.category.name if agent_obj.category else "-"}'
    ws['A2'].font = Font(italic=True, size=9, color='6B7280')
    ws['A2'].alignment = Alignment(horizontal='center')
    # Headers row 3
    headers = ['No','No Invoice','PNR','Tanggal','Maskapai','Penerbangan','Kelas','Nama Penumpang','Tipe Pax','No Tiket','Harga Pelanggan']
    ws.append([''])  # row 3 blank
    ws.append(headers)
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=4, column=col_idx)
        cell.value = h
        cell.font = Font(bold=True, color='FFFFFF', size=9)
        cell.fill = PatternFill('solid', fgColor='1E3A5F')
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[4].height = 22
    # Data
    row_num = 5
    serial = 1
    thin = Side(style='thin', color='D1D5DB')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for inv in invoices:
        flight_str = ', '.join([f"{f.airline} {f.flight_no}" for f in inv.flights]) if inv.flights else '-'
        route_str  = ', '.join([f"{f.route_from}->{f.route_to}" for f in inv.flights]) if inv.flights else '-'
        kelas_str  = ', '.join([f.flight_class for f in inv.flights if f.flight_class]) if inv.flights else '-'
        for pax in inv.passengers:
            row_data = [serial, inv.invoice_number, inv.pnr,
                        inv.created_at.strftime('%d/%m/%Y'),
                        flight_str, route_str, kelas_str,
                        pax.name, pax.pax_type,
                        pax.ticket_number or '-',
                        pax.sell_price]
            ws.append(row_data)
            for col_idx in range(1, len(row_data)+1):
                cell = ws.cell(row=row_num, column=col_idx)
                cell.border = border
                cell.font = Font(size=9)
                cell.alignment = Alignment(vertical='center', wrap_text=True)
                if col_idx == 11:  # price column
                    cell.number_format = '"Rp "#,##0'
                if row_num % 2 == 0:
                    cell.fill = PatternFill('solid', fgColor='F8FAFC')
            row_num += 1
            serial += 1
        # PNR subtotal row
        ws.merge_cells(f'A{row_num}:J{row_num}')
        ws[f'A{row_num}'] = f'Subtotal PNR {inv.pnr} ({len(inv.passengers)} pax)'
        ws[f'A{row_num}'].font = Font(bold=True, italic=True, size=9, color='1D4ED8')
        ws[f'K{row_num}'] = inv.total_sell
        ws[f'K{row_num}'].number_format = '"Rp "#,##0'
        ws[f'K{row_num}'].font = Font(bold=True, size=9, color='16A34A')
        ws[f'K{row_num}'].fill = PatternFill('solid', fgColor='F0FDF4')
        row_num += 1

    # Grand total
    ws.merge_cells(f'A{row_num}:J{row_num}')
    ws[f'A{row_num}'] = f'GRAND TOTAL — {len(invoices)} Invoice'
    ws[f'A{row_num}'].font = Font(bold=True, size=10, color='FFFFFF')
    ws[f'A{row_num}'].fill = PatternFill('solid', fgColor='DC2626')
    ws[f'A{row_num}'].alignment = Alignment(horizontal='right')
    ws[f'K{row_num}'] = sum(inv.total_sell for inv in invoices)
    ws[f'K{row_num}'].number_format = '"Rp "#,##0'
    ws[f'K{row_num}'].font = Font(bold=True, size=10, color='FFFFFF')
    ws[f'K{row_num}'].fill = PatternFill('solid', fgColor='DC2626')

    # Column widths
    widths = [5,16,8,11,22,22,8,28,8,18,18]
    for i,w in enumerate(widths,1):
        ws.column_dimensions[get_column_letter(i)].width = w

    output = io.BytesIO()
    wb.save(output); output.seek(0)
    safe_name = agent_obj.name.upper().replace(' ','_')
    return send_file(output, as_attachment=True,
        download_name=f'{safe_name}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/agent/export-excel/by-category/<int:category_id>')
@login_required
def export_category_excel(category_id):
    if current_user.role != 'admin': return redirect(url_for('dashboard'))
    agents = Agent.query.filter_by(agency_category_id=category_id).all() if category_id else Agent.query.all()
    cat_name = AgencyCategory.query.get(category_id).name if category_id else 'Semua'
    invoices = Invoice.query.filter(Invoice.agent_id.in_([a.id for a in agents])).all()
    data = [{'PNR':inv.pnr,'Agent':inv.agent_name,'Total Jual':inv.total_sell,'Profit':inv.profit} for inv in invoices]
    df = pd.DataFrame(data); output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as w: df.to_excel(w,index=False)
    output.seek(0)
    return send_file(output,as_attachment=True,download_name=f'kategori_{cat_name}.xlsx',mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

# ================== ADMIN: MANAJEMEN USER ==================
@app.route('/admin/users', methods=['GET','POST'])
@login_required
def admin_users():
    if current_user.role != 'admin': return redirect(url_for('dashboard'))
    if request.method == 'POST':
        name = request.form.get('name','').strip()
        email = request.form.get('email','').strip().lower()
        password = request.form.get('password','').strip()
        role = request.form.get('role','staff')
        phone = request.form.get('phone','')
        if not name:
            flash('Nama wajib diisi','danger')
            return redirect(url_for('admin_users'))
        if not email:
            # Auto-generate email if not provided
            email = name.lower().replace(' ','.')+'@agensi.com'
            while User.query.filter_by(email=email).first():
                email = name.lower().replace(' ','.')+str(random.randint(10,99))+'@agensi.com'
        if User.query.filter_by(email=email).first():
            flash(f'Email {email} sudah terdaftar','danger')
            return redirect(url_for('admin_users'))
        if not password:
            password = ''.join(random.choices(string.ascii_letters+string.digits, k=8))
        user = User(name=name, email=email, role=role, phone=phone)
        user.set_password(password)
        db.session.add(user); db.session.commit()
        flash(f'User {name} ditambahkan. Email: {email}, Password: {password}','success')
        return redirect(url_for('admin_users'))
    return render_template('admin_users.html', users=User.query.order_by(User.name).all())

@app.route('/admin/users/delete/<int:id>', methods=['POST'])
@login_required
def delete_user(id):
    if current_user.role != 'admin': return jsonify({'status':'error'}),403
    user = User.query.get_or_404(id)
    if user.id == current_user.id: return jsonify({'status':'error','message':'Tidak bisa hapus diri sendiri'}),400
    db.session.delete(user); db.session.commit()
    return jsonify({'status':'success'})

@app.route('/admin/kriteria-agent', methods=['GET','POST'])
@login_required
def admin_agency_categories():
    if current_user.role != 'admin': return redirect(url_for('dashboard'))
    if request.method == 'POST':
        name = request.form.get('name')
        if AgencyCategory.query.filter_by(name=name).first():
            flash('Kategori sudah ada','danger')
        else:
            db.session.add(AgencyCategory(name=name, description=request.form.get('description'))); db.session.commit()
            flash('Kategori ditambahkan','success')
    return render_template('admin_kriteria.html', categories=AgencyCategory.query.all())

@app.route('/admin/kriteria-agent/delete/<int:id>', methods=['POST'])
@login_required
def delete_agency_category(id):
    if current_user.role != 'admin': return jsonify({'status':'error'}),403
    cat = AgencyCategory.query.get_or_404(id)
    db.session.delete(cat); db.session.commit()
    return jsonify({'status':'success'})

@app.route('/admin/agent', methods=['GET','POST'])
@login_required
def admin_agents():
    if current_user.role != 'admin': return redirect(url_for('dashboard'))
    if request.method == 'POST':
        db.session.add(Agent(name=request.form.get('name'), agency_category_id=request.form.get('category_id'),
            email=request.form.get('email'), phone=request.form.get('phone'), address=request.form.get('address')))
        db.session.commit(); flash('Agent ditambahkan','success')
    return render_template('admin_agent.html', agents=Agent.query.order_by(Agent.name).all(), categories=AgencyCategory.query.all())

@app.route('/admin/agent/delete/<int:id>', methods=['POST'])
@login_required
def delete_agent(id):
    if current_user.role != 'admin': return jsonify({'status':'error'}),403
    agent = Agent.query.get_or_404(id)
    db.session.delete(agent); db.session.commit()
    return jsonify({'status':'success'})

@app.route('/admin/airline', methods=['GET','POST'])
@login_required
def admin_airlines():
    if current_user.role != 'admin': return redirect(url_for('dashboard'))
    if request.method == 'POST':
        logo_file = request.files.get('logo'); logo_filename = None
        if logo_file and logo_file.filename:
            filename = secure_filename(logo_file.filename)
            logo_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            logo_file.save(logo_path)
            if filename.lower().endswith('.png'): remove_white_background(logo_path)
            logo_filename = filename
        db.session.add(Airline(name=request.form.get('name'), code=request.form.get('code'),
            type=request.form.get('type','domestic'), logo=logo_filename))
        db.session.commit(); flash('Maskapai ditambahkan','success')
    return render_template('admin_airline.html', airlines=Airline.query.order_by(Airline.name).all())

@app.route('/admin/airline/delete/<int:id>', methods=['POST'])
@login_required
def delete_airline(id):
    if current_user.role != 'admin': return jsonify({'status':'error'}),403
    airline = Airline.query.get_or_404(id)
    if airline.logo:
        try: os.remove(os.path.join(app.config['UPLOAD_FOLDER'], airline.logo))
        except: pass
    db.session.delete(airline); db.session.commit()
    return jsonify({'status':'success'})

@app.route('/api/flight-schedule')
@login_required
def api_flight_schedule():
    flight_no = request.args.get('flight_no', '').upper().strip()
    if not flight_no:
        return jsonify(None)
    sched = AirlineSchedule.query.filter(
        db.func.upper(AirlineSchedule.flight_no) == flight_no
    ).first()
    if sched:
        return jsonify({
            'airline': sched.airline.name if sched.airline else '',
            'airline_logo': sched.airline.logo if sched.airline else '',
            'airline_id': sched.airline_id,
            'route_from': sched.route_from or '',
            'route_to': sched.route_to or '',
            'departure_time': sched.departure_time or '',
            'arrival_time': sched.arrival_time or '',
            'flight_class': sched.flight_class or '',
        })
    return jsonify(None)

@app.route('/api/flight-schedules-list')
@login_required
def api_flight_schedules_list():
    q = request.args.get('q', '').upper().strip()
    scheds = AirlineSchedule.query
    if q:
        scheds = scheds.filter(AirlineSchedule.flight_no.ilike(f'%{q}%'))
    results = []
    for s in scheds.limit(15).all():
        results.append({
            'flight_no': s.flight_no,
            'airline': s.airline.name if s.airline else '',
            'route_from': s.route_from or '',
            'route_to': s.route_to or '',
            'departure_time': s.departure_time or '',
            'arrival_time': s.arrival_time or '',
            'flight_class': s.flight_class or '',
        })
    return jsonify(results)

@app.route('/admin/airline-schedule', methods=['GET','POST'])
@login_required
def admin_airline_schedule():
    if current_user.role != 'admin': return redirect(url_for('dashboard'))
    if request.method == 'POST':
        action = request.form.get('action', 'add')
        if action == 'delete':
            sid = request.form.get('id')
            s = AirlineSchedule.query.get_or_404(int(sid))
            db.session.delete(s); db.session.commit()
            flash('Jadwal dihapus', 'success')
        else:
            airline_id = request.form.get('airline_id')
            flight_no = (request.form.get('flight_no') or '').strip().upper()
            if not flight_no or not airline_id:
                flash('No penerbangan & maskapai wajib diisi', 'danger')
            else:
                existing = AirlineSchedule.query.filter(
                    db.func.upper(AirlineSchedule.flight_no) == flight_no
                ).first()
                if existing:
                    existing.airline_id = int(airline_id)
                    existing.route_from = (request.form.get('route_from') or '').strip().upper()
                    existing.route_to = (request.form.get('route_to') or '').strip().upper()
                    existing.departure_time = (request.form.get('departure_time') or '').strip()
                    existing.arrival_time = (request.form.get('arrival_time') or '').strip()
                    existing.flight_class = (request.form.get('flight_class') or '').strip().upper()
                    existing.notes = request.form.get('notes', '')
                    flash(f'Jadwal {flight_no} diperbarui', 'success')
                else:
                    db.session.add(AirlineSchedule(
                        airline_id=int(airline_id),
                        flight_no=flight_no,
                        route_from=(request.form.get('route_from') or '').strip().upper(),
                        route_to=(request.form.get('route_to') or '').strip().upper(),
                        departure_time=(request.form.get('departure_time') or '').strip(),
                        arrival_time=(request.form.get('arrival_time') or '').strip(),
                        flight_class=(request.form.get('flight_class') or '').strip().upper(),
                        notes=request.form.get('notes', '')
                    ))
                    flash(f'Jadwal {flight_no} ditambahkan', 'success')
                db.session.commit()
        return redirect(url_for('admin_airline_schedule'))
    schedules = AirlineSchedule.query.order_by(AirlineSchedule.flight_no).all()
    airlines = Airline.query.order_by(Airline.name).all()
    return render_template('admin_airline_schedule.html', schedules=schedules, airlines=airlines)

@app.route('/admin/update-kurs')
@login_required
def update_kurs():
    if current_user.role != 'admin': return redirect(url_for('dashboard'))
    rates = [{'currency': cur, 'rate': ExchangeRate.query.filter_by(currency_id=cur.id).order_by(ExchangeRate.updated_at.desc()).first().rate if ExchangeRate.query.filter_by(currency_id=cur.id).first() else 0, 'updated_at': ExchangeRate.query.filter_by(currency_id=cur.id).order_by(ExchangeRate.updated_at.desc()).first().updated_at if ExchangeRate.query.filter_by(currency_id=cur.id).first() else None} for cur in Currency.query.all()]
    return render_template('admin_kurs.html', rates=rates)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)
