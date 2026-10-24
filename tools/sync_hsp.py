#!/usr/bin/env python3
"""
sync_hsp.py - Unduh data HSP (Harga Satuan Pekerjaan) terbaru dari Bangun Jakarta
https://dcktrp.jakarta.go.id/bangunjakarta/hsp

Yang diambil:
  - daftar HSP        : uraian pekerjaan, satuan, kategori, harga
  - detail analisa    : uraian, kode, satuan, koefisien, harga satuan, jumlah harga
  - harga acuan       : daftar unik upah / bahan / alat (diturunkan dari detail analisa)

Hasil disimpan ke public/data/hsp.json (dipakai oleh web RAB).

Cara pakai (dari akar repo):
  pip install -r tools/requirements.txt
  playwright install chromium
  python tools/sync_hsp.py                 # sinkron penuh
  python tools/sync_hsp.py --debug         # simpan screenshot, HTML, dan daftar URL XHR ke debug/
  python tools/sync_hsp.py --headed        # tampilkan jendela browser (untuk melihat prosesnya)
  python tools/sync_hsp.py --limit 5       # hanya 5 HSP pertama (untuk uji coba)

Catatan: halaman memuat tabel lewat JavaScript, jadi script ini memakai browser
otomatis dan membaca tabel apa adanya (termasuk pagination dan modal Detail).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

DEFAULT_URL = "https://dcktrp.jakarta.go.id/bangunjakarta/hsp"
ROOT = Path(__file__).resolve().parent.parent          # akar repo
OUT_FILE = ROOT / "public" / "data" / "hsp.json"        # ikut ter-deploy ke Vercel
DEBUG_DIR = ROOT / "debug"                              # tidak ikut ter-deploy (.gitignore)
WIB = timezone(timedelta(hours=7))


# --------------------------------------------------------------------------
# util
# --------------------------------------------------------------------------
def log(msg: str) -> None:
    print(f"[{datetime.now(WIB):%H:%M:%S}] {msg}", flush=True)


def parse_idr(text: str | None):
    """'1.234.567,89' -> 1234567.89 ; '0,0250' -> 0.025 ; '' -> None"""
    if text is None:
        return None
    s = re.sub(r"[^0-9,.\-]", "", str(text).strip())
    if not s or s in {"-", ".", ","}:
        return None
    if "," in s:                       # format Indonesia: titik ribuan, koma desimal
        s = s.replace(".", "").replace(",", ".")
    else:
        parts = s.split(".")
        # '1.234.567' = ribuan; '0.025' = desimal
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3 and parts[0] not in ("0", "")):
            s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def group_of(heading: str) -> str | None:
    h = heading.lower()
    if "tenaga" in h or "upah" in h:
        return "upah"
    if "bahan" in h or "material" in h:
        return "bahan"
    if "alat" in h or "peralatan" in h:
        return "alat"
    return None


# --------------------------------------------------------------------------
# pembacaan tabel
# --------------------------------------------------------------------------
JS_TABLE_ROWS = """
(root) => Array.from(root.querySelectorAll('tbody tr')).map(tr => ({
    cells: Array.from(tr.querySelectorAll('th,td')).map(td => (td.innerText || '').trim()),
    hasButton: !!tr.querySelector('button, a.btn, a[data-bs-toggle], a[data-toggle], .btn')
}))
"""


def parse_detail_rows(raw_rows: list[dict]) -> list[dict]:
    """Ubah baris tabel modal Detail HSP menjadi komponen terstruktur."""
    out, grup = [], None
    for r in raw_rows:
        c = [clean(x) for x in r["cells"]]
        if not any(c):
            continue
        # urutan kolom: No, Uraian, Kode, Satuan, Koefisien, Harga Satuan, Jumlah Harga
        c += [""] * (7 - len(c))
        no, uraian, kode, satuan, koef, harga, jumlah = c[:7]
        koef_v, harga_v, jumlah_v = parse_idr(koef), parse_idr(harga), parse_idr(jumlah)

        # baris judul grup / subtotal (tanpa koefisien)
        if koef_v is None:
            judul = uraian or no
            g = group_of(judul)
            if g and jumlah_v is None and harga_v is None:
                grup = g
            out.append({
                "tipe": "baris", "grup": grup, "no": no, "uraian": judul,
                "jumlah_harga": jumlah_v if jumlah_v is not None else harga_v,
            })
            continue

        out.append({
            "tipe": "komponen", "grup": grup, "no": no, "uraian": uraian,
            "kode": kode, "satuan": satuan, "koefisien": koef_v,
            "harga_satuan": harga_v, "jumlah_harga": jumlah_v,
        })
    return out


def build_harga_acuan(items: list[dict]) -> list[dict]:
    """Daftar unik upah/bahan/alat dari seluruh analisa -> tabel harga acuan."""
    seen: dict[str, dict] = {}
    for it in items:
        for d in it.get("detail", []):
            if d.get("tipe") != "komponen":
                continue
            key = d.get("kode") or d["uraian"]
            if key not in seen:
                seen[key] = {
                    "kode": d.get("kode", ""), "uraian": d["uraian"], "satuan": d.get("satuan", ""),
                    "harga": d.get("harga_satuan"), "grup": d.get("grup"),
                }
    return sorted(seen.values(), key=lambda x: (x["grup"] or "z", x["kode"] or "", x["uraian"]))


# --------------------------------------------------------------------------
# scraper
# --------------------------------------------------------------------------
def scrape(url: str, headed: bool, debug: bool, limit: int | None, delay: float):
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    xhr_log: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        ctx = browser.new_context(locale="id-ID", viewport={"width": 1400, "height": 900})
        page = ctx.new_page()
        page.set_default_timeout(20000)

        def on_response(resp):
            try:
                ct = resp.headers.get("content-type", "")
                if resp.request.resource_type in ("xhr", "fetch") or "json" in ct:
                    xhr_log.append({"url": resp.url, "status": resp.status,
                                    "method": resp.request.method, "type": ct})
            except Exception:
                pass

        page.on("response", on_response)

        log(f"Membuka {url}")
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(1500)

        if debug:
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(DEBUG_DIR / "hsp_awal.png"), full_page=True)
            (DEBUG_DIR / "hsp_awal.html").write_text(page.content(), encoding="utf-8")

        # --- tabel utama = tabel pertama di halaman (bukan yang di dalam modal)
        main_table = "table:not(.modal table) >> nth=0"

        def set_page_length_max():
            sel = page.locator("select[name$='_length']")
            if sel.count():
                opts = sel.first.locator("option").evaluate_all("o => o.map(x => x.value)")
                nums = [int(v) for v in opts if re.fullmatch(r"-?\d+", v)]
                if nums:
                    best = "-1" if "-1" in opts else str(max(nums))
                    sel.first.select_option(best)
                    page.wait_for_timeout(800)

        def read_main_rows():
            return page.locator(main_table).evaluate(JS_TABLE_ROWS.replace("(root) =>", "(root) =>"))

        # --- kumpulkan daftar kategori dari filter (jika tabel kosong sebelum filter dipilih)
        set_page_length_max()
        rows0 = read_main_rows()
        rows0 = [r for r in rows0 if any(clean(x) for x in r["cells"]) and
                 not re.search(r"no data|tidak ada data|loading|memuat", " ".join(r["cells"]), re.I)]

        filter_values: list[tuple[str, str]] = []  # (selector, value)
        if not rows0:
            log("Tabel kosong di awal; mencoba iterasi lewat filter kategori...")
            sels = page.locator("select:not([name$='_length'])")
            if sels.count():
                vals = sels.first.locator("option").evaluate_all(
                    "o => o.map(x => x.value).filter(v => v !== '' && v !== null)")
                filter_values = [("select:not([name$='_length']) >> nth=0", v) for v in vals]

        scopes = filter_values or [None]
        items: list[dict] = []
        seen_keys: set[str] = set()

        for scope in scopes:
            if scope:
                page.locator(scope[0]).select_option(scope[1])
                page.wait_for_timeout(1200)
                set_page_length_max()

            while True:
                rows = read_main_rows()
                for r in rows:
                    cells = [clean(x) for x in r["cells"]]
                    if len(cells) < 5 or re.search(r"no data|tidak ada data", " ".join(cells), re.I):
                        continue
                    # No | Uraian Pekerjaan | Satuan | Kategori | Harga | Detail
                    no, uraian, satuan, kategori, harga = cells[:5]
                    key = f"{uraian}|{satuan}|{kategori}"
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    items.append({"no": no, "uraian": uraian, "satuan": satuan,
                                  "kategori": kategori, "harga": parse_idr(harga),
                                  "_hasDetail": r["hasButton"], "detail": []})

                nxt = page.locator(".paginate_button.next:not(.disabled), li.next:not(.disabled) a, "
                                   "a.next:not(.disabled)")
                if nxt.count() == 0:
                    break
                nxt.first.click()
                page.wait_for_timeout(500)
                if limit and len(items) >= limit:
                    break

        if limit:
            items = items[:limit]
        log(f"Ditemukan {len(items)} HSP. Mengambil detail koefisien...")

        # --- detail per HSP: kembali ke halaman 1 lalu klik tombol Detail pada baris yang cocok
        def goto_first_page():
            first = page.locator(".paginate_button.first:not(.disabled), li.first:not(.disabled) a")
            if first.count():
                first.first.click()
                page.wait_for_timeout(400)

        def close_modal():
            btn = page.locator(".modal.show .btn-close, .modal.show [data-bs-dismiss='modal'], "
                               ".modal.show button.close, .modal.in button.close, .modal.show .btn-secondary")
            if btn.count():
                btn.first.click()
            else:
                page.keyboard.press("Escape")
            page.wait_for_timeout(300)

        if not scopes[0] is None:
            # dengan filter kategori, detail diambil per kategori
            pass

        done = 0
        for scope in scopes:
            if scope:
                page.locator(scope[0]).select_option(scope[1])
                page.wait_for_timeout(1200)
                set_page_length_max()
            goto_first_page()

            while True:
                body_rows = page.locator("table:not(.modal table)").first.locator("tbody tr")
                n_rows = body_rows.count()
                for i in range(n_rows):
                    tr = body_rows.nth(i)
                    cells = [clean(x) for x in tr.locator("td").all_inner_texts()]
                    if len(cells) < 5:
                        continue
                    key = f"{cells[1]}|{cells[2]}|{cells[3]}"
                    item = next((x for x in items if f"{x['uraian']}|{x['satuan']}|{x['kategori']}" == key), None)
                    if item is None or item["detail"]:
                        continue
                    btn = tr.locator("button, a.btn, a[data-bs-toggle], a[data-toggle], .btn")
                    if btn.count() == 0:
                        continue
                    try:
                        btn.last.click()
                        page.wait_for_selector(".modal.show table, .modal.in table", timeout=8000)
                        page.wait_for_timeout(max(int(delay * 1000), 250))
                        raw = page.locator(".modal.show table, .modal.in table").first.evaluate(JS_TABLE_ROWS)
                        raw = [r for r in raw if not re.search(r"loading|memuat", " ".join(r["cells"]), re.I)]
                        item["detail"] = parse_detail_rows(raw)
                    except PWTimeout:
                        log(f"  ! modal tidak muncul: {item['uraian'][:60]}")
                    finally:
                        close_modal()
                    done += 1
                    if done % 20 == 0:
                        log(f"  detail {done}/{len(items)}")
                    if limit and done >= limit:
                        break

                if limit and done >= limit:
                    break
                nxt = page.locator(".paginate_button.next:not(.disabled), li.next:not(.disabled) a, "
                                   "a.next:not(.disabled)")
                if nxt.count() == 0:
                    break
                nxt.first.click()
                page.wait_for_timeout(500)

        if debug:
            (DEBUG_DIR / "xhr_urls.json").write_text(json.dumps(xhr_log, indent=2, ensure_ascii=False),
                                                    encoding="utf-8")
            log(f"Daftar URL XHR disimpan ke {DEBUG_DIR / 'xhr_urls.json'}")

        browser.close()

    for it in items:
        it.pop("_hasDetail", None)
    return items, xhr_log


def main() -> int:
    ap = argparse.ArgumentParser(description="Sinkron data HSP Bangun Jakarta")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--out", default=str(OUT_FILE))
    ap.add_argument("--headed", action="store_true", help="tampilkan browser")
    ap.add_argument("--debug", action="store_true", help="simpan screenshot/HTML/URL XHR")
    ap.add_argument("--limit", type=int, default=None, help="batasi jumlah HSP (uji coba)")
    ap.add_argument("--delay", type=float, default=0.4, help="jeda (detik) setelah modal terbuka")
    a = ap.parse_args()

    t0 = time.time()
    try:
        items, _ = scrape(a.url, a.headed, a.debug, a.limit, a.delay)
    except Exception as e:  # jaringan, browser belum terpasang, dsb.
        msg = str(e).splitlines()[0] if str(e) else type(e).__name__
        log(f"GAGAL: {msg}")
        if "Executable doesn't exist" in str(e) or "playwright install" in str(e):
            log("Browser belum terpasang. Jalankan: playwright install chromium")
        elif "ERR_" in str(e):
            log("Tidak bisa membuka situs. Periksa koneksi internet / akses ke dcktrp.jakarta.go.id.")
        return 1
    if not items:
        log("GAGAL: tidak ada baris HSP yang terbaca. Jalankan dengan --debug --headed dan cek "
            "debug/hsp_awal.html (struktur halaman mungkin berubah).")
        return 1

    n_detail = sum(1 for i in items if i["detail"])
    data = {
        "meta": {
            "sumber": a.url,
            "diambil_pada": datetime.now(WIB).isoformat(timespec="seconds"),
            "jumlah_hsp": len(items),
            "jumlah_hsp_dengan_detail": n_detail,
        },
        "items": items,
        "harga_acuan": build_harga_acuan(items),
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out)
    log(f"Selesai: {len(items)} HSP ({n_detail} dengan detail), {len(data['harga_acuan'])} harga acuan "
        f"-> {out}  [{time.time() - t0:.0f} dtk]")
    if n_detail < len(items):
        log(f"Peringatan: {len(items) - n_detail} HSP belum punya detail koefisien. Coba ulang dengan --delay 1.0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
