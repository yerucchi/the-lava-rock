from dotenv import load_dotenv
load_dotenv()
import discord
from discord.ext import commands, tasks
import sqlite3
import random
import time
import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from flask import Flask
from threading import Thread

# ════════════════════════════════════════════════════════════
#  LOGLAMA KURULUMU
# ════════════════════════════════════════════════════════════

log_formatter = logging.Formatter(
    "[%(asctime)s] %(levelname)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

file_handler = RotatingFileHandler(
    "bot.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.WARNING)

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)

bot_logger = logging.getLogger("bot")
bot_logger.setLevel(logging.INFO)
bot_logger.addHandler(file_handler)
bot_logger.addHandler(console_handler)


# ════════════════════════════════════════════════════════════
#  AYARLAR (CONFIG) — Burayı kendi sunucuna göre düzenle
# ════════════════════════════════════════════════════════════

# Botun token'ı. Önce Replit "Secrets" bölümündeki DISCORD_TOKEN'ı arar,
# bulamazsa buraya yazdığın değeri kullanır.
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN ayarlanmamış!")

# Komut ön eki (jwork, jpara gibi)
PREFIX = "j"

# Para birimi
CURRENCY_NAME = "JL"
CURRENCY_FULL = "JoJo Lirası"

# jparabas ve jhver komutlarını kullanabilecek rol
PRESIDENT_ROLE = "👑  •  President Valentine"

# Maaş alabilecek roller ve haftalık maaş tutarları
# Bir kullanıcının birden fazla rolü varsa en yüksek maaşlı rol esas alınır.
SALARY_ROLES = {
    "👑  •  President Valentine": 2100,
    "💸  •  Ekonomi Bakanı": 1750,
    "🏛️  •  Meclis Üyesi": 1400,
    "❤️‍🔥  •  Passione": 1400,
}

# jwork ayarları
WORK_MIN = 50              # en az kazanç
WORK_MAX = 200             # en çok kazanç
WORK_TAX_RATE = 0.10       # %10 vergi -> hazineye gider
WORK_COOLDOWN = 3600       # saniye (1 saat)

# jver (kullanıcıdan kullanıcıya transfer) vergisi
TRANSFER_TAX_RATE = 0.02   # %2

# jkumar vergisi
GAMBLE_TAX_RATE = 0.05     # %5

# jmaaş bekleme süresi
SALARY_COOLDOWN = 7 * 24 * 3600  # 7 gün

# Ekonominin başlangıç değerleri
INITIAL_MONEY_POOL = 100_000     # piyasadaki (basılı) para havuzu
INITIAL_TREASURY = 50_000         # devlet hazinesinin başlangıç parası

# Cowoncy kuru için başlangıç değeri (1 Cowoncy = ? JL)
BASE_EXCHANGE_RATE = 0.5

# Kur, her saat enflasyona göre + rastgele dalgalanmayla güncellenir
EXCHANGE_UPDATE_HOURS = 1
EXCHANGE_FLUCTUATION = 0.03   # %3 rastgele dalgalanma


# ════════════════════════════════════════════════════════════
#  VERİTABANI
# ════════════════════════════════════════════════════════════

conn = sqlite3.connect("ekonomi.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    balance INTEGER NOT NULL DEFAULT 0,
    last_work REAL NOT NULL DEFAULT 0,
    last_salary REAL NOT NULL DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS economy (
    key TEXT PRIMARY KEY,
    value REAL NOT NULL
)
""")

_defaults = {
    "total_pool": float(INITIAL_MONEY_POOL),
    "treasury": float(INITIAL_TREASURY),
    "exchange_rate": float(BASE_EXCHANGE_RATE),
}
for _k, _v in _defaults.items():
    cursor.execute("INSERT OR IGNORE INTO economy (key, value) VALUES (?, ?)", (_k, _v))
conn.commit()


# ════════════════════════════════════════════════════════════
#  YARDIMCI FONKSİYONLAR
# ════════════════════════════════════════════════════════════

def get_user(user_id: int):
    cursor.execute("SELECT user_id, balance, last_work, last_salary FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row is None:
        cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        return (user_id, 0, 0.0, 0.0)
    return row


def get_economy(key: str) -> float:
    cursor.execute("SELECT value FROM economy WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row[0] if row else 0.0


def set_economy(key: str, value: float):
    cursor.execute("UPDATE economy SET value = ? WHERE key = ?", (value, key))
    conn.commit()


def update_balance(user_id: int, amount: int):
    get_user(user_id)  # kayıt yoksa oluştur
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()


def set_last_work(user_id: int, ts: float):
    cursor.execute("UPDATE users SET last_work = ? WHERE user_id = ?", (ts, user_id))
    conn.commit()


def set_last_salary(user_id: int, ts: float):
    cursor.execute("UPDATE users SET last_salary = ? WHERE user_id = ?", (ts, user_id))
    conn.commit()


def format_money(amount) -> str:
    return f"{amount:,.0f} {CURRENCY_NAME}".replace(",", ".")


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} gün")
    if hours:
        parts.append(f"{hours} saat")
    if minutes or not parts:
        parts.append(f"{minutes} dakika")
    return " ".join(parts)


# ════════════════════════════════════════════════════════════
#  BOT KURULUMU
# ════════════════════════════════════════════════════════════

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)


@bot.event
async def on_ready():
    bot_logger.info(f"✅ {bot.user} (ID: {bot.user.id}) olarak giriş yapıldı!")
    if not update_exchange_rate.is_running():
        update_exchange_rate.start()


@bot.event
async def on_disconnect():
    bot_logger.warning(f"⚠️ Discord bağlantısı kesildi — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


@bot.event
async def on_resumed():
    bot_logger.info(f"🔄 Bağlantı yeniden kuruldu — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


@bot.event
async def on_error(event: str, *args, **kwargs):
    bot_logger.error(f"❌ Event hatası [{event}]: args={args}", exc_info=True)


# ════════════════════════════════════════════════════════════
#  KUR / ENFLASYON ARKA PLAN GÖREVİ
# ════════════════════════════════════════════════════════════

@tasks.loop(hours=EXCHANGE_UPDATE_HOURS)
async def update_exchange_rate():
    pool = get_economy("total_pool")
    inflation = (pool - INITIAL_MONEY_POOL) / INITIAL_MONEY_POOL
    fluctuation = random.uniform(-EXCHANGE_FLUCTUATION, EXCHANGE_FLUCTUATION)

    new_rate = BASE_EXCHANGE_RATE * (1 + inflation) * (1 + fluctuation)
    new_rate = max(new_rate, 0.1)

    set_economy("exchange_rate", new_rate)


# ════════════════════════════════════════════════════════════
#  KOMUTLAR
# ════════════════════════════════════════════════════════════

@bot.command(name="para")
async def para(ctx, member: discord.Member = None):
    """jpara veya jpara @kişi -> bakiye gösterir"""
    member = member or ctx.author
    user = get_user(member.id)

    embed = discord.Embed(
        title=f"💰 {member.display_name}",
        description=f"Bakiye: **{format_money(user[1])}**",
        color=0xf1c40f,
    )
    await ctx.send(embed=embed)


@bot.command(name="sıralama", aliases=["siralama"])
async def siralama(ctx):
    """jsıralama -> en zengin 10 kullanıcı"""
    cursor.execute("SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT 10")
    rows = cursor.fetchall()

    if not rows:
        await ctx.send("Henüz hiç kayıtlı kullanıcı yok.")
        return

    lines = []
    for i, (user_id, balance) in enumerate(rows, start=1):
        member = ctx.guild.get_member(user_id)
        name = member.display_name if member else f"Bilinmeyen Kullanıcı ({user_id})"
        lines.append(f"**{i}.** {name} — {format_money(balance)}")

    embed = discord.Embed(
        title="🏆 Zenginlik Sıralaması",
        description="\n".join(lines),
        color=0x3498db,
    )
    await ctx.send(embed=embed)


@bot.command(name="work")
async def work(ctx):
    """jwork -> saatlik gelir, vergi alınır, para havuzundan düşülür"""
    user_id = ctx.author.id
    user = get_user(user_id)
    now = time.time()
    last_work = user[2]

    elapsed = now - last_work
    if elapsed < WORK_COOLDOWN:
        remaining = WORK_COOLDOWN - elapsed
        await ctx.send(f"⏳ Tekrar çalışmak için **{format_duration(remaining)}** beklemen gerekiyor.")
        return

    pool = get_economy("total_pool")
    if pool <= 0:
        await ctx.send("⚠️ Piyasada dolaşımda hiç para kalmadı! Devletin para basması gerekiyor (`jparabas`).")
        return

    amount = random.randint(WORK_MIN, WORK_MAX)
    if pool < amount:
        amount = int(pool)

    tax = round(amount * WORK_TAX_RATE)
    net = amount - tax

    update_balance(user_id, net)
    set_economy("total_pool", pool - amount)
    set_economy("treasury", get_economy("treasury") + tax)
    set_last_work(user_id, now)

    embed = discord.Embed(
        title="💼 Çalıştın!",
        description=(
            f"Kazanılan: **{format_money(amount)}**\n"
            f"Vergi (%{WORK_TAX_RATE*100:.0f}): **{format_money(tax)}**\n"
            f"Hesabına geçen: **{format_money(net)}**"
        ),
        color=0x2ecc71,
    )
    await ctx.send(embed=embed)


@bot.command(name="maaş", aliases=["maas"])
async def maas(ctx):
    """jmaaş -> haftalık devlet memuru maaşı, hazineden ödenir"""
    user_id = ctx.author.id
    user = get_user(user_id)
    now = time.time()
    last_salary = user[3]

    elapsed = now - last_salary
    if elapsed < SALARY_COOLDOWN:
        remaining = SALARY_COOLDOWN - elapsed
        await ctx.send(f"⏳ Maaşını tekrar almak için **{format_duration(remaining)}** beklemen gerekiyor.")
        return

    salary = 0
    role_used = None
    for role in ctx.author.roles:
        amount = SALARY_ROLES.get(role.name)
        if amount and amount > salary:
            salary = amount
            role_used = role.name

    if salary == 0:
        await ctx.send("❌ Maaş alabileceğin bir devlet memuru rolün yok.")
        return

    treasury = get_economy("treasury")
    if treasury < salary:
        await ctx.send(f"⚠️ Devlet hazinesinde yeterli para yok! (Hazine: {format_money(treasury)})")
        return

    update_balance(user_id, salary)
    set_economy("treasury", treasury - salary)
    set_last_salary(user_id, now)

    embed = discord.Embed(
        title="🏛️ Maaş Ödendi",
        description=f"**{role_used}** rolün için **{format_money(salary)}** maaşın hazineden ödendi.",
        color=0x9b59b6,
    )
    await ctx.send(embed=embed)


@bot.command(name="parabas")
async def parabas(ctx, amount: int):
    """jparabas [miktar] -> sadece Başkan, para havuzunu büyütür (enflasyon yaratır)"""
    if PRESIDENT_ROLE not in [r.name for r in ctx.author.roles]:
        await ctx.send(f"❌ Bu komutu sadece **{PRESIDENT_ROLE}** rolü kullanabilir.")
        return

    if amount <= 0:
        await ctx.send("⚠️ Geçerli (pozitif) bir miktar gir.")
        return

    pool = get_economy("total_pool")
    new_pool = pool + amount
    set_economy("total_pool", new_pool)

    embed = discord.Embed(
        title="🖨️ Para Basıldı",
        description=(
            f"**{format_money(amount)}** piyasaya sürüldü.\n"
            f"Yeni para havuzu: **{format_money(new_pool)}**\n\n"
            f"⚠️ Bu işlem enflasyonu artırır ve JL'nin değerini düşürür."
        ),
        color=0xe74c3c,
    )
    await ctx.send(embed=embed)


@bot.command(name="ekonomi")
async def ekonomi(ctx):
    """jekonomi -> enflasyon, kur ve para arzı bilgileri"""
    pool = get_economy("total_pool")
    treasury = get_economy("treasury")
    exchange_rate = get_economy("exchange_rate")

    inflation = ((pool - INITIAL_MONEY_POOL) / INITIAL_MONEY_POOL) * 100

    cursor.execute("SELECT COALESCE(SUM(balance), 0) FROM users")
    total_players = cursor.fetchone()[0]

    total_money = total_players + treasury + pool

    embed = discord.Embed(title=f"📊 {CURRENCY_FULL} Ekonomisi", color=0x1abc9c)
    embed.add_field(name="📈 Enflasyon", value=f"%{inflation:.2f}", inline=False)
    embed.add_field(
        name="💱 Döviz Kuru",
        value=f"1 Cowoncy ≈ **{exchange_rate:.2f} {CURRENCY_NAME}**",
        inline=False,
    )
    embed.add_field(name="💰 Piyasadaki Toplam Para", value=format_money(total_money), inline=False)
    embed.add_field(name="👥 Oyunculardaki Para", value=format_money(total_players), inline=True)
    embed.add_field(name="🏛️ Devlet Hazinesi", value=format_money(treasury), inline=True)
    embed.add_field(name="🖨️ Dolaşımdaki Basılı Para", value=format_money(pool), inline=True)

    await ctx.send(embed=embed)


@bot.command(name="hver")
async def hver(ctx, member: discord.Member, amount: int):
    """jhver @kişi [miktar] -> sadece Başkan, hazineden birine para verir"""
    if PRESIDENT_ROLE not in [r.name for r in ctx.author.roles]:
        await ctx.send(f"❌ Bu komutu sadece **{PRESIDENT_ROLE}** rolü kullanabilir.")
        return

    if amount <= 0:
        await ctx.send("⚠️ Geçerli (pozitif) bir miktar gir.")
        return

    treasury = get_economy("treasury")
    if treasury < amount:
        await ctx.send(f"⚠️ Hazinede yeterli para yok! (Hazine: {format_money(treasury)})")
        return

    set_economy("treasury", treasury - amount)
    update_balance(member.id, amount)

    embed = discord.Embed(
        title="🏛️ Hazineden Ödeme",
        description=f"{member.mention} adlı kullanıcıya hazineden **{format_money(amount)}** verildi.",
        color=0x9b59b6,
    )
    await ctx.send(embed=embed)


@bot.command(name="ver")
async def ver(ctx, hedef: str, amount: int):
    """jver @kişi|hazine [miktar] -> kişiye veya hazineye para gönder"""
    if amount <= 0:
        await ctx.send("⚠️ Geçerli (pozitif) bir miktar gir.")
        return

    sender = get_user(ctx.author.id)
    if sender[1] < amount:
        await ctx.send("❌ Yeterli bakiyen yok.")
        return

    # ── Hazineye gönder ──────────────────────────────────────
    if hedef.lower() == "hazine":
        update_balance(ctx.author.id, -amount)
        set_economy("treasury", get_economy("treasury") + amount)

        embed = discord.Embed(
            title="🏛️ Hazineye Bağış",
            description=(
                f"{ctx.author.mention} devlet hazinesine **{format_money(amount)}** gönderdi.\n"
                f"Yeni hazine: **{format_money(get_economy('treasury'))}**"
            ),
            color=0x2ecc71,
        )
        await ctx.send(embed=embed)
        return

    # ── Kullanıcıya gönder ───────────────────────────────────
    try:
        converter = commands.MemberConverter()
        member = await converter.convert(ctx, hedef)
    except commands.MemberNotFound:
        await ctx.send("❌ Kullanıcı bulunamadı. Bahsetme (@) ile dene ya da `hazine` yaz.")
        return

    if member.id == ctx.author.id:
        await ctx.send("❌ Kendine para gönderemezsin.")
        return

    if member.bot:
        await ctx.send("❌ Bir bota para gönderemezsin.")
        return

    tax = round(amount * TRANSFER_TAX_RATE)
    net = amount - tax

    update_balance(ctx.author.id, -amount)
    update_balance(member.id, net)
    set_economy("treasury", get_economy("treasury") + tax)

    embed = discord.Embed(
        title="💸 Para Gönderildi",
        description=(
            f"{ctx.author.mention} → {member.mention}\n"
            f"Gönderilen: **{format_money(amount)}**\n"
            f"Vergi (%{TRANSFER_TAX_RATE*100:.0f}): **{format_money(tax)}**\n"
            f"Karşı tarafa geçen: **{format_money(net)}**"
        ),
        color=0x3498db,
    )
    await ctx.send(embed=embed)


@bot.command(name="kumar")
async def kumar(ctx, amount: int):
    """jkumar [miktar] -> %50 ihtimalle parayı ikiye katlar, her oyundan vergi alınır"""
    if amount <= 0:
        await ctx.send("⚠️ Geçerli (pozitif) bir miktar gir.")
        return

    user = get_user(ctx.author.id)
    tax = round(amount * GAMBLE_TAX_RATE)

    if user[1] < amount + tax:
        await ctx.send(
            f"❌ Bu bahis için (vergi dahil) en az **{format_money(amount + tax)}** bakiyen olmalı."
        )
        return

    # vergiyi hemen al
    update_balance(ctx.author.id, -tax)
    set_economy("treasury", get_economy("treasury") + tax)

    pool = get_economy("total_pool")

    if random.random() < 0.5:
        win_amount = min(amount, int(pool))
        update_balance(ctx.author.id, win_amount)
        set_economy("total_pool", pool - win_amount)
        embed = discord.Embed(
            title="🎉 Kazandın!",
            description=(
                f"Bahsin ikiye katlandı! **+{format_money(win_amount)}** kâr ettin.\n"
                f"Vergi: {format_money(tax)}"
            ),
            color=0x2ecc71,
        )
    else:
        update_balance(ctx.author.id, -amount)
        set_economy("total_pool", get_economy("total_pool") + amount)
        embed = discord.Embed(
            title="💀 Kaybettin!",
            description=(
                f"**-{format_money(amount)}** kaybettin, para piyasa havuzuna döndü.\n"
                f"Vergi: {format_money(tax)}"
            ),
            color=0xe74c3c,
        )

    await ctx.send(embed=embed)


@bot.command(name="log")
async def log(ctx, satir: int = 15):
    """jlog [satır] -> sadece Başkan, son hata loglarını gösterir"""
    if PRESIDENT_ROLE not in [r.name for r in ctx.author.roles]:
        await ctx.send(f"❌ Bu komutu sadece **{PRESIDENT_ROLE}** rolü kullanabilir.")
        return

    satir = max(1, min(satir, 30))

    try:
        with open("bot.log", "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        await ctx.send("📭 Henüz hiç log kaydı yok.")
        return

    if not lines:
        await ctx.send("📭 Log dosyası boş.")
        return

    son_satirlar = lines[-satir:]
    icerik = "".join(son_satirlar).strip()

    if len(icerik) > 1900:
        icerik = "..." + icerik[-1900:]

    embed = discord.Embed(
        title=f"📋 Son {len(son_satirlar)} Log Kaydı",
        description=f"```\n{icerik}\n```",
        color=0xe67e22,
    )
    embed.set_footer(text="Sadece WARNING ve üzeri seviyedeki olaylar kaydedilir.")
    await ctx.send(embed=embed)


@bot.command(name="komutlar", aliases=["yardim", "yardım", "jhelp"])
async def komutlar(ctx):
    """jkomutlar -> tüm komutları listeler"""
    embed = discord.Embed(title=f"📖 {CURRENCY_FULL} Bot Komutları", color=0x95a5a6)
    embed.add_field(name=f"{PREFIX}para [@kişi]", value="Bakiye gösterir", inline=False)
    embed.add_field(name=f"{PREFIX}sıralama", value="En zengin 10 kişi", inline=False)
    embed.add_field(name=f"{PREFIX}work", value="Saatlik gelir kazan", inline=False)
    embed.add_field(name=f"{PREFIX}maaş", value="Haftalık devlet memuru maaşı", inline=False)
    embed.add_field(name=f"{PREFIX}ver @kişi|hazine [miktar]", value="Birine veya hazineye para gönder", inline=False)
    embed.add_field(name=f"{PREFIX}kumar [miktar]", value="Kumar oyna (%50 şans)", inline=False)
    embed.add_field(name=f"{PREFIX}ekonomi", value="Enflasyon, kur ve para arzı", inline=False)
    embed.add_field(
        name=f"{PREFIX}parabas [miktar] (sadece {PRESIDENT_ROLE})",
        value="Para bas, piyasaya sür",
        inline=False,
    )
    embed.add_field(
        name=f"{PREFIX}hver @kişi [miktar] (sadece {PRESIDENT_ROLE})",
        value="Hazineden birine para ver",
        inline=False,
    )
    embed.add_field(
        name=f"{PREFIX}log [satır] (sadece {PRESIDENT_ROLE})",
        value="Son hata loglarını gösterir (varsayılan: 15 satır)",
        inline=False,
    )
    await ctx.send(embed=embed)


# ════════════════════════════════════════════════════════════
#  HATA YÖNETİMİ
# ════════════════════════════════════════════════════════════

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Belirttiğin kullanıcıyı bulamadım. Bahsetme (@) ile kullanmayı dene.")
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ Eksik bilgi girdin. Kullanım: `{PREFIX}{ctx.command.qualified_name} {ctx.command.signature}`")
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send("⚠️ Girdiğin değer geçersiz. Miktar için sadece sayı kullan.")
        return

    bot_logger.error(f"Beklenmeyen hata [{ctx.command}]: {error}", exc_info=True)
    await ctx.send("⚠️ Bir hata oluştu, komutu kontrol et.")


# ════════════════════════════════════════════════════════════
#  HTTP SERVER (UptimeRobot için)
# ════════════════════════════════════════════════════════════

app = Flask(__name__)

@app.route('/')
def health():
    return "Bot aktif!", 200

def run_server():
    app.run(host='0.0.0.0', port=8080, debug=False)


# ════════════════════════════════════════════════════════════
#  BOTU ÇALIŞTIR
# ════════════════════════════════════════════════════════════

# HTTP server'ı arka planda çalıştır
server_thread = Thread(target=run_server, daemon=True)
server_thread.start()

# Discord botu çalıştır
bot.run(TOKEN)
