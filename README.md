# REKAP DULU — Sistem Manajemen Tiket & Profit Agensi

Aplikasi web untuk agensi travel mencatat invoice tiket pesawat (domestik & internasional), menghitung profit per transaksi, mengelola refund, deposit agent, dan merekap laporan keuangan. Dibangun dengan Flask (Python) dan Tailwind CSS.

## Fitur Utama

**Invoice & Transaksi**
- Buat invoice tiket domestik dan internasional (form terpisah, wizard multi-step)
- Input multi penumpang (dewasa/anak/infant) dan multi segmen penerbangan per invoice
- Perhitungan otomatis: harga jual, harga nett, biaya bagasi, diskon, keep, hingga profit per transaksi
- Invoice internasional mendukung mata uang asing dengan kurs konversi ke IDR
- Cetak invoice ke PDF
- Edit dan hapus invoice (dengan pengecekan hak akses per user)

**Refund**
- Buat, edit, hapus permintaan refund terkait invoice
- Cetak bukti refund ke PDF (mode customer/nett/keep)
- Arsip refund terpisah dari daftar aktif

**Deposit & Pelunasan Agent**
- Top up saldo deposit agent
- Riwayat pelunasan per agent
- Halaman pelunasan lunas untuk transaksi yang sudah settle

**Rekapan & Laporan**
- Halaman rekapan profit dengan detail per invoice dan per refund
- Export laporan profit ke PDF
- Export data transaksi ke Excel, per agent maupun per kategori agent

**Panel Admin**
- Kelola akun user (admin/staff)
- Kelola kriteria/kategori agent
- Kelola data agent
- Kelola maskapai dan jadwal penerbangan (domestik/internasional)
- Update kurs mata uang manual
- Pengaturan profil agensi (nama, alamat, kontak, logo) yang tampil di invoice

**Sistem Pendukung**
- Login dengan role berbeda (admin vs staff), akses fitur dibatasi sesuai role
- Lupa password lewat OTP yang dikirim ke email
- Auto-fetch kurs mata uang setiap 1 jam dari exchangerate-api.com
- Auto-fetch berita seputar penerbangan setiap 30 menit (Google News RSS) untuk widget dashboard
- Log aktivitas user

## Tech Stack

- **Backend**: Flask 3, Flask-SQLAlchemy, Flask-Login, Flask-APScheduler
- **Database**: SQLite (file lokal, otomatis dibuat saat pertama dijalankan)
- **Frontend**: Tailwind CSS (sudah di-build, ada di `static/dist/output.css`)
- **PDF**: fpdf2
- **Excel**: pandas + openpyxl
- **Gambar**: Pillow (untuk hapus background logo otomatis)

## Cara Menjalankan (lokal)

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Buka `http://127.0.0.1:8080` di browser. Database (`instance/database.db`) dan folder upload logo akan dibuat/diisi otomatis saat aplikasi pertama kali jalan.

## Akun Demo

| Role  | Email             | Password |
|-------|-------------------|----------|
| Admin | admin@agensi.com  | admin123 |
| Staff | staff@agensi.com  | staff123 |

## Konfigurasi Opsional (Email OTP)

Fitur lupa password mengirim kode OTP lewat email. Kalau environment variable SMTP di bawah **tidak diisi**, OTP tetap bisa dipakai karena akan otomatis tercetak di console/terminal tempat `app.py` berjalan (tidak benar-benar terkirim ke email).

```bash
export MAIL_SERVER=smtp.gmail.com
export MAIL_PORT=587
export MAIL_USERNAME=email-pengirim@gmail.com
export MAIL_PASSWORD=app-password-gmail
```

## Struktur Folder

```
rekap/
├─ app.py                  → seluruh logic backend (routes, model, PDF/Excel export, scheduler)
├─ requirements.txt        → dependency Python
├─ Dockerfile              → build multi-stage (Tailwind + Python) untuk deployment
├─ package.json            → script build Tailwind CSS
├─ static/
│  ├─ input.css / dist/output.css   → Tailwind CSS
│  └─ uploads/logos/                → logo maskapai & agensi
├─ templates/              → seluruh halaman HTML (Jinja2)
└─ instance/database.db    → database SQLite (dibuat otomatis)
```

## Deployment

Sudah disediakan `Dockerfile` (multi-stage: build Tailwind CSS lalu jalankan lewat Gunicorn di port 8080). Untuk build manual:

```bash
docker build -t rekapdulu .
docker run -p 8080:8080 rekapdulu
```
