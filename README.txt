PERBAIKAN SMS FORWARDER AUGESTEL -> TELEGRAM

File yang perlu menggantikan file lama:
- bot.py
- database.py
- sms_forwarder.py

File config.py, augestel.py, requirements.txt, dan file .env tetap gunakan milik project Anda.
Jangan mengganti atau menghapus file .env. Pastikan nilai API_KEY, BOT_TOKEN, MESSAGES_URL, dan MESSAGES_TYPE benar.

CARA KERJA
1. Jalankan bot seperti biasa: python bot.py
2. Pengguna harus membuka bot dan menekan /start terlebih dahulu.
3. Pengguna memilih negara, lalu menekan tombol PILIH pada nomor yang ingin digunakan.
4. Nomor dicatat di jing_otp.db dan dikaitkan dengan Telegram user_id.
5. Bot mengambil SMS dari endpoint MESSAGES_URL setiap POLL_INTERVAL (minimal 30 detik), lalu meneruskannya ke pemilik nomor.

CATATAN
- Polling pertama hanya menandai SMS yang sudah ada; SMS lama tidak dikirim massal.
- SMS untuk nomor yang belum memiliki pemilik tidak diteruskan.
- Jika pengiriman Telegram gagal, SMS tidak ditandai selesai dan akan dicoba lagi pada polling berikutnya.
- Satu nomor hanya dapat diklaim oleh satu user_id. Jika nomor dipakai ulang oleh provider, pemetaan lama perlu dibersihkan secara manual dari tabel number_owners setelah memastikan nomor memang sudah tidak dimiliki user lama.
- Forwarder memakai GET `/api/v1/iprn/messages` dengan `start_date`, `end_date`, `type=a2p`, `per_page`, dan `page`, serta membaca `data` dan `pagination.last_page` sesuai contoh dokumentasi Augestel. Default rentang tanggal: kemarin sampai hari ini (UTC); dapat diatur melalui `MESSAGES_START_DATE` dan `MESSAGES_END_DATE` di `.env`.
- GET `/api/v1/iprn/statistics` adalah endpoint terpisah untuk laporan pendapatan/statistik; endpoint ini tidak diperlukan untuk meneruskan SMS.
- Jalankan bot.py, bukan poll.py. poll.py lama bukan komponen forwarder Telegram.
