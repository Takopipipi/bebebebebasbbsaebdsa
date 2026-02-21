#!/usr/bin/env python3
"""
💍 Telegram Wedding Bot
pip install python-telegram-bot Pillow
"""

import logging
import sqlite3
import os
import io
import math
from datetime import datetime
from typing import Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from PIL import Image, ImageDraw, ImageFont

# ══════════════════════════════════════════════════════════
#  КОНФИГУРАЦИЯ
# ══════════════════════════════════════════════════════════

BOT_TOKEN = "8554157768:AAESt7ZiNLsNrWif9gxP-9kSDGIh5NyN2VU"
DB_PATH = "weddings.db"
START_IMAGE = "start.png"
# Промпт для генерации start.png:
# "Cute cartoon illustration, two golden wedding rings
#  intertwined with a glowing pink heart above them,
#  surrounded by soft rose petals and sparkles,
#  pastel pink and lavender gradient background,
#  gentle bokeh lights, romantic atmosphere,
#  flat design style, clean and minimal, no text, 16:9"

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO
)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════
#  БАЗА ДАННЫХ
# ══════════════════════════════════════════════════════════

def init_db():
    with sqlite3.connect(DB_PATH) as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS known_users (
            user_id    INTEGER PRIMARY KEY,
            username   TEXT,
            first_name TEXT,
            last_name  TEXT
        );
        CREATE TABLE IF NOT EXISTS marriages (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id        INTEGER NOT NULL,
            user1_id       INTEGER NOT NULL,
            user1_name     TEXT    NOT NULL,
            user1_un       TEXT,
            user2_id       INTEGER NOT NULL,
            user2_name     TEXT    NOT NULL,
            user2_un       TEXT,
            married_at     TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pending (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id        INTEGER NOT NULL,
            initiator_id   INTEGER NOT NULL,
            u1_id          INTEGER NOT NULL,
            u1_name        TEXT,
            u1_un          TEXT,
            u2_id          INTEGER NOT NULL,
            u2_name        TEXT,
            u2_un          TEXT,
            u1_ok          INTEGER,
            u2_ok          INTEGER,
            msg_id         INTEGER,
            created_at     TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS msg_cnt (
            user_id INTEGER,
            chat_id INTEGER,
            cnt     INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, chat_id)
        );
        """)


def _db():
    return sqlite3.connect(DB_PATH)

# ══════════════════════════════════════════════════════════
#  ХЕЛПЕРЫ
# ══════════════════════════════════════════════════════════

def cache_user(u):
    if not u or u.is_bot:
        return
    with _db() as c:
        c.execute(
            "INSERT OR REPLACE INTO known_users VALUES(?,?,?,?)",
            (u.id, (u.username or "").lower(), u.first_name, u.last_name or ""),
        )


def find_user(username: str) -> Optional[dict]:
    un = username.lower().lstrip("@")
    if not un:
        return None
    with _db() as c:
        r = c.execute(
            "SELECT user_id,username,first_name FROM known_users "
            "WHERE LOWER(username)=?", (un,)
        ).fetchone()
    return {"id": r[0], "un": r[1], "name": r[2]} if r else None


def get_marriage(uid: int, cid: int) -> Optional[dict]:
    with _db() as c:
        r = c.execute(
            "SELECT * FROM marriages WHERE chat_id=? "
            "AND (user1_id=? OR user2_id=?)", (cid, uid, uid)
        ).fetchone()
    if not r:
        return None
    return dict(
        id=r[0], cid=r[1],
        u1=r[2], u1n=r[3], u1u=r[4],
        u2=r[5], u2n=r[6], u2u=r[7],
        date=r[8],
    )


def pending_for(uid: int, cid: int) -> bool:
    with _db() as c:
        c.execute(
            "DELETE FROM pending WHERE created_at<datetime('now','-1 day')"
        )
        return c.execute(
            "SELECT 1 FROM pending WHERE chat_id=? "
            "AND (u1_id=? OR u2_id=?)", (cid, uid, uid)
        ).fetchone() is not None


def inc_msg(uid: int, cid: int):
    with _db() as c:
        c.execute(
            "INSERT INTO msg_cnt VALUES(?,?,1) "
            "ON CONFLICT DO UPDATE SET cnt=cnt+1", (uid, cid)
        )


def msg_cnt(uid: int, cid: int) -> int:
    with _db() as c:
        r = c.execute(
            "SELECT cnt FROM msg_cnt WHERE user_id=? AND chat_id=?",
            (uid, cid),
        ).fetchone()
    return r[0] if r else 0


def mn(name, un):
    """mention helper"""
    return f"@{un}" if un else name


def parse_dt(s):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return datetime.now()

# ══════════════════════════════════════════════════════════
#  ГЕНЕРАЦИЯ КАРТИНКИ
# ══════════════════════════════════════════════════════════

def _font(sz):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, sz)
        except OSError:
            continue
    try:
        return ImageFont.load_default(sz)
    except TypeError:
        return ImageFont.load_default()


async def _avatar(bot, uid) -> Optional[Image.Image]:
    try:
        ph = await bot.get_user_profile_photos(uid, limit=1)
        if not ph.photos:
            return None
        f = await bot.get_file(ph.photos[0][-1].file_id)
        ba = await f.download_as_bytearray()
        return Image.open(io.BytesIO(ba)).convert("RGBA")
    except Exception:
        return None


def _placeholder(sz):
    img = Image.new("RGBA", (sz, sz), (180, 170, 210, 255))
    d = ImageDraw.Draw(img)
    cx, cy = sz // 2, sz // 2
    r = sz // 5
    d.ellipse((cx-r, cy-r-sz//8, cx+r, cy+r-sz//8), fill=(140, 130, 170))
    d.ellipse((cx-sz//3, cy+sz//10, cx+sz//3, cy+sz//2+sz//6),
              fill=(140, 130, 170))
    return img


def _crop_circle(img, sz):
    img = img.resize((sz, sz), Image.LANCZOS)
    mask = Image.new("L", (sz, sz), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, sz, sz), fill=255)
    out = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    out.paste(img, mask=mask)
    bsz = sz + 10
    frm = Image.new("RGBA", (bsz, bsz), (0, 0, 0, 0))
    ImageDraw.Draw(frm).ellipse((0, 0, bsz-1, bsz-1), fill="white")
    frm.paste(out, (5, 5), out)
    return frm


def _heart(draw, cx, cy, size, color=(255, 70, 80)):
    pts = []
    for deg in range(360):
        t = math.radians(deg)
        x = 16 * math.sin(t) ** 3
        y = -(13*math.cos(t) - 5*math.cos(2*t) -
              2*math.cos(3*t) - math.cos(4*t))
        pts.append((cx + x * size / 17, cy + y * size / 17))
    draw.polygon(pts, fill=color)


def build_card(av1, av2, n1, n2, days, msgs, wdate) -> io.BytesIO:
    W, H = 900, 500
    img = Image.new("RGBA", (W, H))
    d = ImageDraw.Draw(img)

    # градиент розовый → фиолетовый
    for y in range(H):
        t = y / H
        r = int(210*(1-t) + 75*t)
        g = int(130*(1-t) + 35*t)
        b = int(210*(1-t) + 175*t)
        d.line([(0, y), (W, y)], fill=(r, g, b))

    # декоративные сердечки
    for hx, hy, hs in [
        (60, 55, 9), (840, 45, 7), (80, 430, 8),
        (820, 410, 6), (450, 15, 6), (750, 240, 5),
        (150, 250, 5),
    ]:
        _heart(d, hx, hy, hs, (255, 220, 230))

    # аватарки
    SZ = 150
    a1 = _crop_circle(av1 if av1 else _placeholder(SZ), SZ)
    a2 = _crop_circle(av2 if av2 else _placeholder(SZ), SZ)
    gap = 90
    x1 = W // 2 - SZ - gap // 2
    x2 = W // 2 + gap // 2
    AY = 30
    img.paste(a1, (x1, AY), a1)
    img.paste(a2, (x2, AY), a2)
    d = ImageDraw.Draw(img)

    # сердце между аватарками
    _heart(d, W // 2, AY + SZ // 2 + 5, 16, (255, 80, 90))

    # имена
    fn = _font(22)
    for name, ax in [(n1, x1), (n2, x2)]:
        bb = d.textbbox((0, 0), name, font=fn)
        tw = bb[2] - bb[0]
        nx = ax + (SZ + 10) // 2 - tw // 2
        d.text((nx, AY + SZ + 18), name, fill="white", font=fn)

    # линия
    LY = AY + SZ + 58
    d.line([(W//4, LY), (3*W//4, LY)], fill=(255, 255, 255), width=2)

    # статистика
    bf = _font(28)
    sf = _font(22)
    lines = [
        (f"Вместе: {days} дней", bf),
        (f"Сообщений вместе: {msgs}", bf),
        (f"Дата свадьбы: {wdate}", sf),
    ]
    sy = LY + 25
    for txt, fnt in lines:
        bb = d.textbbox((0, 0), txt, font=fnt)
        d.text(((W - bb[2] + bb[0]) // 2, sy), txt, fill="white", font=fnt)
        sy += 52

    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    buf.seek(0)
    return buf

# ══════════════════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cache_user(update.effective_user)
    me = await ctx.bot.get_me()
    text = (
        "💍 <b>Свадебный бот</b> 💍\n\n"
        "Привет! Я помогу заключить браки\n"
        "прямо в вашем чате!\n\n"
        "Добавь меня в группу и жени друзей,\n"
        "или предложи руку и сердце! 💒\n\n"
        "Нажми <b>«Команды»</b> чтобы узнать что я умею."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "➕ Добавить в группу",
            url=f"https://t.me/{me.username}?startgroup=true")],
        [InlineKeyboardButton("📜 Команды", callback_data="cmds")],
    ])
    if os.path.exists(START_IMAGE):
        with open(START_IMAGE, "rb") as f:
            await update.message.reply_photo(
                f, caption=text, parse_mode="HTML", reply_markup=kb)
    else:
        await update.message.reply_text(
            text, parse_mode="HTML", reply_markup=kb)

# ══════════════════════════════════════════════════════════
#  /tomarry @user1 @user2
# ══════════════════════════════════════════════════════════

async def cmd_tomarry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cache_user(update.effective_user)
    m = update.message
    cid = update.effective_chat.id

    if update.effective_chat.type == "private":
        return await m.reply_text("❌ Эта команда только для групп!")
    if len(ctx.args) < 2:
        return await m.reply_text(
            "❌ Формат: <code>/tomarry @ник1 @ник2</code>",
            parse_mode="HTML")

    un1 = ctx.args[0].lstrip("@")
    un2 = ctx.args[1].lstrip("@")

    if un1.lower() == un2.lower():
        return await m.reply_text(
            "❌ Нельзя женить человека на самом себе 😅")

    u1, u2 = find_user(un1), find_user(un2)
    if not u1:
        return await m.reply_text(
            f"❌ @{un1} не найден.\n"
            "Пусть напишет хотя бы одно сообщение в чат.")
    if not u2:
        return await m.reply_text(
            f"❌ @{un2} не найден.\n"
            "Пусть напишет хотя бы одно сообщение в чат.")
    if get_marriage(u1["id"], cid):
        return await m.reply_text(
            f"❌ {mn(u1['name'], u1['un'])} уже в браке!")
    if get_marriage(u2["id"], cid):
        return await m.reply_text(
            f"❌ {mn(u2['name'], u2['un'])} уже в браке!")
    if pending_for(u1["id"], cid) or pending_for(u2["id"], cid):
        return await m.reply_text(
            "❌ Уже есть активное предложение для одного из них!")

    with _db() as c:
        cur = c.execute(
            "INSERT INTO pending"
            "(chat_id,initiator_id,u1_id,u1_name,u1_un,"
            "u2_id,u2_name,u2_un,u1_ok,u2_ok,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,NULL,NULL,datetime('now'))",
            (cid, update.effective_user.id,
             u1["id"], u1["name"], u1["un"],
             u2["id"], u2["name"], u2["un"]))
        pid = cur.lastrowid

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(
             f"✅ {u1['name']}: Согласен",
             callback_data=f"yes_{pid}_{u1['id']}"),
         InlineKeyboardButton(
             f"❌ {u1['name']}: Отказ",
             callback_data=f"no_{pid}_{u1['id']}")],
        [InlineKeyboardButton(
             f"✅ {u2['name']}: Согласен",
             callback_data=f"yes_{pid}_{u2['id']}"),
         InlineKeyboardButton(
             f"❌ {u2['name']}: Отказ",
             callback_data=f"no_{pid}_{u2['id']}")],
    ])

    sent = await m.reply_text(
        f"💒 <b>{update.effective_user.first_name}</b> хочет поженить "
        f"<b>{u1['name']}</b> и <b>{u2['name']}</b>!\n\n"
        f"Оба должны дать согласие! 💍",
        parse_mode="HTML", reply_markup=kb)

    with _db() as c:
        c.execute("UPDATE pending SET msg_id=? WHERE id=?",
                  (sent.message_id, pid))

# ══════════════════════════════════════════════════════════
#  /marry @user
# ══════════════════════════════════════════════════════════

async def cmd_marry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cache_user(update.effective_user)
    m = update.message
    cid = update.effective_chat.id
    me = update.effective_user

    if update.effective_chat.type == "private":
        return await m.reply_text("❌ Эта команда только для групп!")
    if len(ctx.args) < 1:
        return await m.reply_text(
            "❌ Формат: <code>/marry @ник</code>", parse_mode="HTML")

    tun = ctx.args[0].lstrip("@")

    if tun.lower() == (me.username or "").lower():
        return await m.reply_text("❌ Нельзя жениться на себе 😅")

    target = find_user(tun)
    if not target:
        return await m.reply_text(
            f"❌ @{tun} не найден.\n"
            "Пусть напишет хотя бы одно сообщение в чат.")
    if get_marriage(me.id, cid):
        return await m.reply_text("❌ Ты уже в браке!")
    if get_marriage(target["id"], cid):
        return await m.reply_text(
            f"❌ {mn(target['name'], target['un'])} уже в браке!")
    if pending_for(me.id, cid) or pending_for(target["id"], cid):
        return await m.reply_text("❌ Уже есть активное предложение!")

    with _db() as c:
        cur = c.execute(
            "INSERT INTO pending"
            "(chat_id,initiator_id,u1_id,u1_name,u1_un,"
            "u2_id,u2_name,u2_un,u1_ok,u2_ok,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,1,NULL,datetime('now'))",
            (cid, me.id,
             me.id, me.first_name, me.username or "",
             target["id"], target["name"], target["un"]))
        pid = cur.lastrowid

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ Согласен(на)!",
            callback_data=f"yes_{pid}_{target['id']}"),
        InlineKeyboardButton(
            "❌ Отказать",
            callback_data=f"no_{pid}_{target['id']}"),
    ]])

    tmn = mn(target["name"], target["un"])
    sent = await m.reply_text(
        f"💍 <b>{me.first_name}</b> предлагает руку и сердце "
        f"<b>{target['name']}</b>!\n\n"
        f"{tmn}, ты согласен(на)? 💒",
        parse_mode="HTML", reply_markup=kb)

    with _db() as c:
        c.execute("UPDATE pending SET msg_id=? WHERE id=?",
                  (sent.message_id, pid))

# ══════════════════════════════════════════════════════════
#  /marriages
# ══════════════════════════════════════════════════════════

async def cmd_marriages(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cache_user(update.effective_user)
    if update.effective_chat.type == "private":
        return await update.message.reply_text(
            "❌ Эта команда только для групп!")

    with _db() as c:
        rows = c.execute(
            "SELECT * FROM marriages WHERE chat_id=? ORDER BY married_at",
            (update.effective_chat.id,)).fetchall()

    if not rows:
        return await update.message.reply_text(
            "💔 В этом чате пока нет ни одной пары...")

    lines = ["💍 <b>Браки в этом чате:</b>\n"]
    for i, r in enumerate(rows, 1):
        days = (datetime.now() - parse_dt(r[8])).days
        lines.append(
            f"{i}. {mn(r[3],r[4])} ❤️ {mn(r[6],r[7])} — "
            f"<i>{days} дн.</i>")

    await update.message.reply_text(
        "\n".join(lines), parse_mode="HTML")

# ══════════════════════════════════════════════════════════
#  /divorce
# ══════════════════════════════════════════════════════════

async def cmd_divorce(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cache_user(update.effective_user)
    if update.effective_chat.type == "private":
        return await update.message.reply_text(
            "❌ Эта команда только для групп!")

    uid = update.effective_user.id
    cid = update.effective_chat.id
    mar = get_marriage(uid, cid)
    if not mar:
        return await update.message.reply_text("❌ Ты не в браке 🤷")

    partner = (mn(mar["u2n"], mar["u2u"])
               if mar["u1"] == uid
               else mn(mar["u1n"], mar["u1u"]))

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ Да, развод",
            callback_data=f"dyes_{mar['id']}_{uid}"),
        InlineKeyboardButton(
            "❌ Нет, передумал(а)",
            callback_data=f"dno_{uid}"),
    ]])

    await update.message.reply_text(
        f"⚠️ <b>{update.effective_user.first_name}</b>, "
        f"ты точно хочешь развестись с <b>{partner}</b>?\n\n"
        f"Это действие нельзя отменить!",
        parse_mode="HTML", reply_markup=kb)

# ══════════════════════════════════════════════════════════
#  /couple
# ══════════════════════════════════════════════════════════

async def cmd_couple(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cache_user(update.effective_user)
    if update.effective_chat.type == "private":
        return await update.message.reply_text(
            "❌ Эта команда только для групп!")

    uid = update.effective_user.id
    cid = update.effective_chat.id
    mar = get_marriage(uid, cid)
    if not mar:
        return await update.message.reply_text(
            "❌ Ты не в браке! Используй /marry 💍")

    dt = parse_dt(mar["date"])
    days = (datetime.now() - dt).days
    msgs = msg_cnt(mar["u1"], cid) + msg_cnt(mar["u2"], cid)

    wait = await update.message.reply_text("🎨 Генерирую картинку...")

    av1 = await _avatar(ctx.bot, mar["u1"])
    av2 = await _avatar(ctx.bot, mar["u2"])

    n1 = mn(mar["u1n"], mar["u1u"])
    n2 = mn(mar["u2n"], mar["u2u"])

    buf = build_card(av1, av2, n1, n2, days, msgs,
                     dt.strftime("%d.%m.%Y"))

    await update.message.reply_photo(
        buf,
        caption=(
            f"💍 <b>{n1}</b> ❤️ <b>{n2}</b>\n"
            f"Вместе <b>{days}</b> дн. | "
            f"💬 <b>{msgs}</b> сообщ."),
        parse_mode="HTML")

    try:
        await wait.delete()
    except Exception:
        pass

# ══════════════════════════════════════════════════════════
#  CALLBACK — КНОПКИ
# ══════════════════════════════════════════════════════════

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    user = q.from_user
    cache_user(user)

    # ── показать команды ──
    if data == "cmds":
        await q.answer()
        return await q.message.reply_text(
            "📜 <b>Команды:</b>\n\n"
            "💍 /marry <code>@ник</code> — предложить руку и сердце\n"
            "💒 /tomarry <code>@ник1 @ник2</code> — поженить двоих\n"
            "📋 /marriages — все пары чата\n"
            "📊 /couple — картинка-статистика пары\n"
            "💔 /divorce — подать на развод",
            parse_mode="HTML")

    # ── согласие ──
    if data.startswith("yes_"):
        parts = data.split("_")
        pid, tuid = int(parts[1]), int(parts[2])

        if user.id != tuid:
            return await q.answer(
                "Эта кнопка не для тебя!", show_alert=True)

        with _db() as c:
            row = c.execute(
                "SELECT * FROM pending WHERE id=?", (pid,)
            ).fetchone()
            if not row:
                return await q.answer(
                    "Предложение устарело!", show_alert=True)

            # 0:id 1:cid 2:init 3:u1id 4:u1n 5:u1un
            # 6:u2id 7:u2n 8:u2un 9:u1ok 10:u2ok 11:msgid
            p = dict(id=row[0], cid=row[1], init=row[2],
                     u1=row[3], u1n=row[4], u1u=row[5],
                     u2=row[6], u2n=row[7], u2u=row[8],
                     ok1=row[9], ok2=row[10])

            if user.id == p["u1"]:
                c.execute(
                    "UPDATE pending SET u1_ok=1 WHERE id=?", (pid,))
                p["ok1"] = 1
            else:
                c.execute(
                    "UPDATE pending SET u2_ok=1 WHERE id=?", (pid,))
                p["ok2"] = 1

        await q.answer("✅ Принято!")

        # оба согласны → свадьба
        if p["ok1"] == 1 and p["ok2"] == 1:
            with _db() as c:
                c.execute(
                    "INSERT INTO marriages"
                    "(chat_id,user1_id,user1_name,user1_un,"
                    "user2_id,user2_name,user2_un,married_at) "
                    "VALUES(?,?,?,?,?,?,?,datetime('now'))",
                    (p["cid"],
                     p["u1"], p["u1n"], p["u1u"],
                     p["u2"], p["u2n"], p["u2u"]))
                c.execute(
                    "DELETE FROM pending WHERE id=?", (pid,))

            try:
                await q.edit_message_text(
                    f"🎊💒 <b>Совет да любовь!</b>\n\n"
                    f"{mn(p['u1n'],p['u1u'])} и "
                    f"{mn(p['u2n'],p['u2u'])} теперь в браке! 💍\n\n"
                    f"📅 {datetime.now().strftime('%d.%m.%Y')}\n\n"
                    f"Используйте /couple для статистики 💕",
                    parse_mode="HTML")
            except Exception:
                pass
        else:
            # ждём второго
            oid = p["u2"] if user.id == p["u1"] else p["u1"]
            onm = p["u2n"] if user.id == p["u1"] else p["u1n"]
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "✅ Согласен(на)!",
                    callback_data=f"yes_{pid}_{oid}"),
                InlineKeyboardButton(
                    "❌ Отказать",
                    callback_data=f"no_{pid}_{oid}"),
            ]])
            try:
                await q.edit_message_text(
                    f"✅ <b>{user.first_name}</b> согласен(на)!\n\n"
                    f"Ждём ответа от <b>{onm}</b>... 💒",
                    parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
        return

    # ── отказ ──
    if data.startswith("no_"):
        parts = data.split("_")
        pid, tuid = int(parts[1]), int(parts[2])

        if user.id != tuid:
            return await q.answer(
                "Эта кнопка не для тебя!", show_alert=True)

        with _db() as c:
            row = c.execute(
                "SELECT * FROM pending WHERE id=?", (pid,)
            ).fetchone()
            if not row:
                return await q.answer(
                    "Предложение устарело!", show_alert=True)

            init_id = row[2]
            u1n, u2n = row[4], row[7]
            u1id, u2id = row[3], row[6]
            c.execute("DELETE FROM pending WHERE id=?", (pid,))

        await q.answer()

        # кому сочувствовать — инициатору
        if init_id == u1id:
            comfort = u1n
        elif init_id == u2id:
            comfort = u2n
        else:
            comfort = u1n  # /tomarry — сочувствуем всем

        try:
            await q.edit_message_text(
                f"💔 <b>{user.first_name}</b> отказал(а)...\n\n"
                f"<b>{comfort}</b>, не расстраивайся, "
                f"всё ещё будет! 🫂",
                parse_mode="HTML")
        except Exception:
            pass
        return

    # ── развод: да ──
    if data.startswith("dyes_"):
        parts = data.split("_")
        mid, tuid = int(parts[1]), int(parts[2])

        if user.id != tuid:
            return await q.answer(
                "Эта кнопка не для тебя!", show_alert=True)

        with _db() as c:
            row = c.execute(
                "SELECT * FROM marriages WHERE id=?", (mid,)
            ).fetchone()
            if not row:
                return await q.answer(
                    "Брак уже расторгнут!", show_alert=True)

            days = (datetime.now() - parse_dt(row[8])).days
            u1m = mn(row[3], row[4])
            u2m = mn(row[6], row[7])
            c.execute("DELETE FROM marriages WHERE id=?", (mid,))

        await q.answer()
        try:
            await q.edit_message_text(
                f"📜 Брак между <b>{u1m}</b> и <b>{u2m}</b> "
                f"расторгнут.\nБыли вместе <b>{days}</b> дн. 💔",
                parse_mode="HTML")
        except Exception:
            pass
        return

    # ── развод: нет ──
    if data.startswith("dno_"):
        tuid = int(data.split("_")[1])
        if user.id != tuid:
            return await q.answer(
                "Эта кнопка не для тебя!", show_alert=True)
        await q.answer()
        try:
            await q.edit_message_text(
                f"❤️ <b>{user.first_name}</b> сохранил(а) брак!\n"
                f"Любовь победила! 🎉",
                parse_mode="HTML")
        except Exception:
            pass

# ══════════════════════════════════════════════════════════
#  СЧЁТЧИК СООБЩЕНИЙ + КЕШ ЮЗЕРОВ
# ══════════════════════════════════════════════════════════

async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if (not update.effective_user
            or not update.effective_chat
            or update.effective_chat.type == "private"):
        return
    cache_user(update.effective_user)
    inc_msg(update.effective_user.id, update.effective_chat.id)

# ══════════════════════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════════════════════

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("tomarry", cmd_tomarry))
    app.add_handler(CommandHandler("marry", cmd_marry))
    app.add_handler(CommandHandler("marriages", cmd_marriages))
    app.add_handler(CommandHandler("divorce", cmd_divorce))
    app.add_handler(CommandHandler("couple", cmd_couple))
    app.add_handler(CallbackQueryHandler(on_callback))

    # group=1 → работает ПАРАЛЛЕЛЬНО с остальными хендлерами,
    # считает ВСЕ сообщения и кеширует юзеров
    app.add_handler(
        MessageHandler(filters.ALL & filters.ChatType.GROUPS,
                       on_message),
        group=1,
    )

    log.info("🚀 Бот запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
