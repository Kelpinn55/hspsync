# RAB-HSP Otomatis

Web untuk menyusun RAB berdasarkan HSP (Harga Satuan Pekerjaan) DKI Jakarta. Data HSP, koefisien analisa,
dan harga acuan diambil dari <https://dcktrp.jakarta.go.id/bangunjakarta/hsp>. Bisa juga dilengkapi
secara manual lewat unggah Excel/CSV.

## Struktur

```
rab-hsp/
├── public/                     # <- yang di-deploy ke Vercel (situs statis)
│   ├── index.html              #    aplikasi RAB
│   ├── vendor/xlsx.full.min.js #    SheetJS (baca/tulis Excel), disajikan lokal
│   └── data/
│       ├── hsp.sample.json     #    DATA CONTOH (bukan harga resmi)
│       └── hsp.json            #    data asli, dibuat oleh sync (belum ada sebelum sync pertama)
├── tools/                      # alat bantu, tidak ikut di-deploy
│   ├── sync_hsp.py             #    unduh HSP + koefisien + harga acuan -> public/data/hsp.json
│   ├── server.py               #    server lokal + tombol "Sinkronkan data terbaru"
│   └── requirements.txt
├── .github/workflows/sync-hsp.yml  # sinkron otomatis tiap minggu -> commit -> Vercel deploy ulang
├── vercel.json  .vercelignore  .gitignore
```

## Deploy ke Vercel lewat GitHub

1. Buat repo kosong di GitHub, lalu dari folder ini:
   ```bash
   git init
   git add .
   git commit -m "RAB-HSP awal"
   git branch -M main
   git remote add origin https://github.com/<akun>/<repo>.git
   git push -u origin main
   ```
2. Di Vercel: **Add New → Project → Import** repo itu. Tidak perlu mengubah pengaturan apa pun
   (`vercel.json` sudah mengatur *Output Directory* = `public`, tanpa build). Klik **Deploy**.
3. Isi data asli: di GitHub buka tab **Actions → Sinkron data HSP → Run workflow**. Workflow mengambil
   data, meng-commit `public/data/hsp.json`, dan Vercel otomatis deploy ulang. Setelah itu workflow
   berjalan sendiri tiap Senin 03:00 WIB (ubah `cron` di `sync-hsp.yml` bila perlu).

Sebelum sync pertama selesai, web menampilkan **data contoh** dengan banner peringatan.

> Catatan: Vercel hanya menyajikan berkas statis, jadi sinkronisasi dilakukan oleh GitHub Actions
> (memakai browser otomatis Playwright), bukan oleh Vercel. Bila situs sumber memblokir server GitHub,
> workflow akan gagal dan menyimpan bukti di artifact `debug-sync-hsp`. Alternatifnya jalankan
> `python tools/sync_hsp.py` di komputermu lalu commit `public/data/hsp.json`.

## Menjalankan lokal

```bash
pip install -r tools/requirements.txt
playwright install chromium

python tools/server.py          # buka http://localhost:8000
```

Di mode lokal, tombol **Sinkronkan data terbaru** menjalankan `tools/sync_hsp.py` dan memuat ulang data.
Atau jalankan langsung: `python tools/sync_hsp.py` (opsi: `--debug`, `--headed`, `--limit 5`).

## Fitur

- **Katalog HSP**: cari/filter uraian, kategori, satuan; detail koefisien (upah, bahan, alat).
- **RAB**: tambah pekerjaan, volume, harga otomatis dari HSP (bisa diubah manual), PPN, total,
  pembulatan, terbilang. Tersimpan otomatis di browser.
- **Impor RAB (Excel/CSV)**: unggah daftar pekerjaan + volume. Baris yang uraian dan satuannya sama
  dengan katalog memakai harga HSP; yang tidak cocok dimasukkan sebagai item manual dengan harga dari berkas.
  Kolom: `Uraian Pekerjaan | Satuan | Volume | Harga Satuan (opsional)`. Tombol **Template** menyediakan contoh.
- **Item manual**: tombol **+ Item manual** (satu item), atau **Impor item (Excel/CSV)** untuk banyak item.
  Lembar `Item`: `Uraian Pekerjaan | Satuan | Kategori | Harga`. Lembar `Analisa` (opsional):
  `Pekerjaan | Kode | Uraian | Grup | Satuan | Koefisien | Harga Satuan` — bila Harga di lembar Item
  kosong, harga dihitung dari koefisien × harga satuan. Item manual tersimpan di browser dan bisa dihapus.
- **Rekap Upah & Bahan**: kebutuhan = volume × koefisien, dijumlah per kode, lengkap dengan biayanya.
- **Harga Acuan**: daftar unik upah/bahan/alat dari seluruh analisa.
- **Ekspor Excel** (RAB, Analisa HSP, Rekap Sumber Daya, Harga Acuan), CSV, dan cetak.
- CSV boleh berpemisah `;` atau `,`; angka boleh berformat Indonesia (`1.234.567,89`).

## Jika sinkronisasi gagal / hasilnya kosong

Struktur halaman sumber dibaca dari jauh dan scraper-nya baru diuji pada halaman tiruan, jadi selector
mungkin perlu penyesuaian. Jalankan:

```bash
python tools/sync_hsp.py --debug --headed --limit 5
```

Ini menyimpan `debug/hsp_awal.html`, tangkapan layar, dan `debug/xhr_urls.json` (endpoint data yang
dipanggil halaman) — dari situ selector bisa dibetulkan, atau dibuat versi yang memanggil endpoint langsung.

## Catatan

- Data yang diunggah manual hanya tersimpan di browser pengguna tersebut (tidak dikirim ke server).
- Gunakan sinkronisasi secukupnya dan patuhi ketentuan situs sumber.
- Periksa ulang harga terhadap dokumen resmi yang berlaku sebelum dipakai untuk dokumen pengadaan.
