# 11 · Nama default agen (random name pool)

Status: spesifikasi halaman, belum kontrak. Konflik: lihat peta dokumen.

Tanggal: 24 September 2026 · Dipakai di: modal **Buat agen** (Dashboard → Agen → "+ Buat agen"), onboarding workspace baru, dan agen yang dibuat lewat API tanpa nama.

## Tujuan
Setiap kali pengguna membuat agen, sistem langsung memberi **nama acak yang enak dipanggil**, supaya:
1. Pengguna tidak berhenti di kolom nama ("namain apa ya?").
2. Nama agen konsisten dengan brand (Kaki, Ayame, Matcha): pendek, hangat, dan terasa seperti rekan kerja, bukan "Agent-3".
3. Mention mudah diketik: `@kunyit`, `@klepon`, `@sencha`.

## Kriteria nama yang bagus
| Kriteria | Aturan |
|---|---|
| Panjang | 3–10 huruf, idealnya 2–3 suku kata |
| Satu kata | Tanpa spasi atau tanda hubung, supaya `@mention` tidak putus |
| Mudah dieja | Bisa diketik orang Indonesia tanpa ragu, tanpa huruf aneh |
| Netral & positif | Tidak bermakna negatif, kasar, atau tabu di bahasa daerah besar (Jawa, Sunda, Minang, Batak, Bugis, Bali) |
| Bukan nama orang umum | Hindari nama manusia populer (Melati, Mawar, Putri, Intan, Dewi, Sari, Bintang, Fajar, Bulan, Hana, Yuki) supaya tidak tertukar dengan anggota tim |
| Bukan merek | Hindari nama brand terdaftar (Fuji, Oreo, Pocky, Aqua, Indomie, dll.) |
| Bukan SARA / politik | Tanpa nama tokoh, agama, suku, partai |
| Tema | Makanan, rempah, tanaman, alam, hewan, dan kain Nusantara + sedikit kosakata Jepang, keluarga yang sama dengan Kaki, Ayame, Matcha |

## Nama yang dipesan (tidak boleh diacak)
`Kaki`, `Ayame`, `Matcha` adalah agen bawaan. Juga dipesan: `Admin`, `Sanji`, `Bot`, `Agen`, `System`, `Owner`, `Kamu`, `Semua`, `Everyone`, `Here`, `Channel`, karena bentrok dengan mention khusus Zulip.

---

## Pool nama: 300 nama dalam 9 kategori

> Format untuk kode: `slug` huruf kecil. Tampilan UI memakai huruf kapital di awal.

### 1. Rempah & dapur (30) · warna default: Oranye `#FF6A3D`
Kunyit, Jahe, Kencur, Lengkuas, Serai, Pala, Cengkih, Kapulaga, Adas, Jintan, Ketumbar, Lada, Kemiri, Asam, Salam, Pandan, Kemangi, Selasih, Andaliman, Kluwek, Wijen, Kecombrang, Secang, Rosela, Temulawak, Bangle, Sunti, Kayumanis, Sambal, Kecap

### 2. Jajanan & kue (40) · warna default: Kuning `#FFD84D`
Klepon, Onde, Lemper, Serabi, Cenil, Getuk, Lupis, Wajik, Dodol, Bika, Lapis, Nastar, Putu, Apem, Cucur, Pukis, Cakwe, Bakpia, Pastel, Risol, Lumpia, Cilok, Cireng, Seblak, Batagor, Siomay, Pempek, Kerupuk, Peyek, Rengginang, Opak, Kolak, Dawet, Cendol, Wedang, Ronde, Bajigur, Bandrek, Sekoteng, Martabak

### 3. Buah Nusantara (40) · warna default: Oranye `#FF6A3D`
Salak, Duku, Langsat, Rambutan, Manggis, Durian, Nangka, Cempedak, Sawo, Srikaya, Sirsak, Belimbing, Jambu, Kedondong, Markisa, Matoa, Kepundung, Menteng, Gandaria, Kweni, Kemang, Pepaya, Pisang, Nanas, Mangga, Jeruk, Delima, Semangka, Alpukat, Kelengkeng, Lai, Kawista, Buni, Juwet, Ciplukan, Bisbul, Namnam, Mundu, Kecapi, Lontar

### 4. Bunga, pohon & tanaman (40) · warna default: Hijau `#16C784`
Kenanga, Cempaka, Kamboja, Teratai, Anggrek, Soka, Asoka, Bakung, Seruni, Dahlia, Aster, Tulip, Sakura, Kaktus, Pakis, Lumut, Bambu, Jati, Mahoni, Cemara, Beringin, Randu, Waru, Trembesi, Ketapang, Flamboyan, Kana, Keladi, Talas, Palem, Nipah, Rotan, Gaharu, Cendana, Kemuning, Tanjung, Lotus, Edelweis, Lavender, Kelor

### 5. Teh, kopi & minuman (25) · warna default: Hijau `#16C784`
Sencha, Hojicha, Genmai, Oolong, Chai, Rooibos, Tubruk, Arabika, Robusta, Liberika, Gayo, Toraja, Kintamani, Kerinci, Latte, Mocca, Tarik, Uwuh, Boba, Yuzu, Kombu, Espresso, Affogato, Cortado, Lassi

### 6. Kosakata Jepang ringan (30) · warna default: Ungu `#8B74FF`
Sora, Kumo, Umi, Mochi, Dango, Onigiri, Nori, Miso, Tofu, Udon, Soba, Wasabi, Shiso, Ume, Momo, Ringo, Mikan, Kinako, Anko, Taiyaki, Dorayaki, Tsuki, Hoshi, Kaze, Mori, Kiku, Nami, Sumi, Kome, Tamago

### 7. Alam & cuaca (35) · warna default: Ungu `#8B74FF`
Gerimis, Pelangi, Embun, Kabut, Senja, Mega, Awan, Angin, Ombak, Pasir, Karang, Tebing, Lembah, Bukit, Muara, Telaga, Danau, Kerikil, Batu, Gunung, Rimba, Savana, Oase, Delta, Laguna, Selat, Teluk, Pulau, Nusa, Semilir, Rinai, Teduh, Surya, Komet, Aurora

### 8. Hewan (35) · warna default: Kuning `#FFD84D`
Kancil, Tarsius, Kukang, Trenggiling, Bekantan, Anoa, Tapir, Pesut, Dugong, Penyu, Kakatua, Jalak, Murai, Kutilang, Merpati, Bangau, Pelikan, Rangkong, Kepodang, Tupai, Landak, Kelinci, Panda, Rusa, Kijang, Lumba, Belibis, Kenari, Nuri, Camar, Walet, Gajah, Komodo, Cupang, Kunang

### 9. Warna, kain & batu (25) · warna default: Ungu `#8B74FF`
Nila, Jingga, Lembayung, Marun, Tosca, Sepia, Parang, Kawung, Truntum, Lurik, Songket, Tenun, Ulos, Tapis, Jumputan, Indigo, Terakota, Gading, Arang, Zamrud, Mutiara, Kuarsa, Giok, Safir, Ambar

**Total: 300 nama.**

---

## Algoritma pengacakan

```
pickAgentName(workspace, role?):
  used = lowercase(semua nama anggota + agen + undangan di workspace)
  pool = NAME_POOL - RESERVED - used - BLOCKLIST_WORKSPACE
  if role == "Perencana": beri bobot 2× untuk kategori Rempah & Alam
  if role == "Pembangun": beri bobot 2× untuk Jajanan & Hewan
  if role == "Peninjau":  beri bobot 2× untuk Teh/kopi & Tanaman
  hindari kategori yang sama dengan agen terakhir yang dibuat (supaya bervariasi)
  name = weightedRandom(pool)
  shape = random(lingkaran, cincin, kotak)
  color = warna default kategori
  jika kombinasi (shape, color) sudah dipakai agen lain → ganti shape
  return { name, category, shape, color }
```

**Jika pool habis** (lebih dari 300 agen di satu workspace): tambahkan angka Jawa-halus di belakang, misalnya `Klepon Dua`. Bentuk ini tidak dipakai di MVP. Tampilkan pesan "Nama bawaan habis, tulis nama sendiri".

## UX di modal "Buat agen"
| Elemen | Perilaku |
|---|---|
| Buka modal | Kolom **Nama** langsung terisi nama acak. Bentuk & warna ikut acak sesuai kategori. Pratinjau di atas langsung menampilkan hasilnya |
| Tombol **🎲 Acak** di samping nama | Ganti ke nama acak lain (tanpa mengulang 10 nama terakhir). Bentuk & warna ikut berganti. Animasi ringan: pratinjau "pop" |
| Teks kecil di bawah kolom | "Dari kategori *Jajanan*. Mention dengan @klepon" |
| Pengguna mengetik sendiri | Acak berhenti. Validasi: 2–20 karakter, satu kata, tidak bentrok nama yang ada (error inline: "Nama ini sudah dipakai di workspace") |
| Mode **Atur** agen yang sudah ada | Tombol Acak tetap ada, tapi nama **tidak** diganti otomatis |

## Kebutuhan backend
| Kebutuhan | Detail |
|---|---|
| Sumber data | `sanji/agent_names.json` → `[{ slug, display, category, color }]`. Di-versioning supaya bisa ditambah tanpa migrasi |
| Endpoint | `GET /api/v1/sanji/agents/suggest-name?role=` → `{ name, category, shape, color }`. Endpoint yang sama menerima `exclude[]` untuk tombol Acak |
| Validasi | `POST /agents` menolak nama yang ada di RESERVED atau bentrok (case-insensitive) dengan user/bot aktif di realm |
| Bot user Zulip | `full_name` = nama tampilan. `email` = `{slug}-bot@{realm}.sanji.space`. Jika slug sudah dipakai bot yang dinonaktifkan, tambahkan sufiks acak 3 karakter di email saja, nama tampilan tetap |
| Blocklist per workspace | Admin bisa menambah kata terlarang di Pengaturan → Agen & runner (fase berikutnya) |

## Review sebelum rilis
- Minta 3–5 orang dari daerah berbeda mengecek daftar ini untuk makna yang tidak diinginkan di bahasa daerah.
- Nama berpotensi ambigu yang perlu dicek khusus: `Asam`, `Batu`, `Arang`, `Lai`, `Buni`, `Sunti`, `Opak`, `Kali`.
