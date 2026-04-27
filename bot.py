import sys, os
sys.path.insert(0, os.getcwd())  # Загружать config.py из текущей папки (для мульти-офисного режима)
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ConversationHandler, ContextTypes, CallbackQueryHandler
)
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import uuid, re, base64, json, httpx

# ========= CONFIG =========
VERSION = "2.6.4 | 2026-04-27"
from config import TOKEN, OPENAI_API_KEY, ADMINS, PHOTOS_CHANNEL_ID, SHEET_NAME

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
creds  = ServiceAccountCredentials.from_json_keyfile_name("creds.json", scope)
gclient = gspread.authorize(creds)
sheet   = gclient.open(SHEET_NAME).sheet1

# ========= STATES =========
(
    CHOOSE_METHOD,
    SCAN_ID, CONFIRM_ID,
    EDIT_NAME, EDIT_IDNUM,
    CHOOSE_BANK_METHOD,
    SCAN_BANK, CONFIRM_BANK,
    EDIT_BANK, EDIT_ACCOUNT,
    NAME, PHONE, OPERATOR, IDNUM, BANK, ACCOUNT,
    REVIEW, REVIEW_FIELD,
    REVIEW_NAME, REVIEW_IDNUM, REVIEW_PHONE, REVIEW_OPERATOR, REVIEW_BANK, REVIEW_ACCOUNT,
    ADD_MORE,
    ADMIN_MENU, ADD_OP_ID, ADD_OP_NAME, ADD_OP_WINDOW, ADD_OP_ROLE,
    VIEW_OPS, DELETE_OP, EDIT_OP_SELECT, EDIT_OP_CHOOSE, EDIT_OP_FIELD, EDIT_OP_VALUE,
    MANUAL_NAME, MANUAL_IDNUM,
) = range(38)

# ========= KEYBOARDS =========
main_keyboard = ReplyKeyboardMarkup(
    [["📋 Встать в очередь"], ["📊 Проверить очередь"]],
    resize_keyboard=True,
)
add_more_keyboard = ReplyKeyboardMarkup(
    [["➕ Добавить ещё", "🏁 Завершить"]],
    resize_keyboard=True,
)
admin_keyboard = ReplyKeyboardMarkup(
    [["➡️ Следующий", "📋 Вся очередь"],
     ["📝 Записать вручную", "⚙️ Админ-панель"]],
    resize_keyboard=True,
)
admin_menu_keyboard = ReplyKeyboardMarkup([
    ["➕ Добавить оператора", "👥 Просмотреть операторов"],
    ["✏️ Редактировать оператора", "❌ Удалить оператора"],
    ["📊 Статистика", "⬅️ Выход"],
], resize_keyboard=True)
role_keyboard = InlineKeyboardMarkup([
    [InlineKeyboardButton("👤 Оператор", callback_data="role_operator")],
    [InlineKeyboardButton("⚙️ Администратор", callback_data="role_admin")],
])
operator_keyboard = ReplyKeyboardMarkup(
    [["SKT", "KT", "LG U+"],
     ["SKT KADAFON", "KT KADAFON", "LG KADAFON"]],
    resize_keyboard=True,
)
bank_keyboard = ReplyKeyboardMarkup(
    [["기업은행 · Айбикей", "국민은행 · Кукмин"],
     ["우리은행 · Урибанк", "신한은행 · Шинхан"],
     ["하나은행 · Хана",   "농협은행 · Нонхеп"],
     ["ДРУГОЙ"]],
    resize_keyboard=True,
)
method_keyboard = InlineKeyboardMarkup([
    [InlineKeyboardButton("📷 Сканировать документы", callback_data="scan")],
    [InlineKeyboardButton("✏️ Ввести вручную",        callback_data="manual")],
])
bank_method_keyboard = InlineKeyboardMarkup([
    [InlineKeyboardButton("📷 Сканировать банковскую книжку", callback_data="scan_bank")],
    [InlineKeyboardButton("✏️ Ввести данные банка вручную",  callback_data="manual_bank")],
])
confirm_keyboard = InlineKeyboardMarkup([
    [InlineKeyboardButton("✅ Верно",             callback_data="confirm")],
    [InlineKeyboardButton("✏️ Исправить вручную", callback_data="edit")],
])

# Клавиатура для редактирования данных ID
def make_id_edit_keyboard(name, idnum):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✏️ ФИО: {name[:20]}", callback_data="edit_name")],
        [InlineKeyboardButton(f"✏️ ID: {idnum}",      callback_data="edit_idnum")],
        [InlineKeyboardButton("✅ Всё верно — продолжить", callback_data="confirm_id_edit")],
    ])

# Клавиатура для редактирования данных банка
def make_bank_edit_keyboard(bank, account):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✏️ Банк: {bank}",     callback_data="edit_bank")],
        [InlineKeyboardButton(f"✏️ Счёт: {account[:16]}", callback_data="edit_account")],
        [InlineKeyboardButton("✅ Всё верно — продолжить", callback_data="confirm_bank_edit")],
    ])

# ========= BANK INFO =========
BANK_INFO = {
    "기업은행 · Айбикей": {},
    "국민은행 · Кукмин":  {},
    "우리은행 · Урибанк": {},
    "신한은행 · Шинхан":  {},
    "하나은행 · Хана":    {},
    "농협은행 · Нонхеп":  {},
    "ДРУГОЙ":            {},
}
VALID_BANKS = list(BANK_INFO.keys())

CONSENT_TEXT = (
    "📄 СОГЛАСИЕ НА ОБРАБОТКУ ДАННЫХ\n\n"
    "Нажимая «Встать в очередь», вы соглашаетесь на обработку персональных данных "
    "(телефон, банковские реквизиты, данные удостоверения личности) "
    "исключительно в целях формирования очереди и оказания запрошенных услуг. "
    "Данные не передаются третьим лицам."
)

# ========= VALIDATION =========

def validate_name(text):
    words = text.strip().split()
    if len(words) < 2:
        return False, "Введите Фамилию Имя Отчество как в ID"
    if not re.match(r"^[A-Za-z\s'\-]+$", text):
        return False, "Введите Фамилию Имя Отчество как в ID"
    return True, ""

def validate_phone(digits):
    return digits.startswith("010") and len(digits) == 11

def format_phone(digits):
    return f"010-{digits[3:7]}-{digits[7:]}"

def validate_korean_id(digits):
    if not re.match(r"^\d{13}$", digits):
        return False
    mm, dd, g = int(digits[2:4]), int(digits[4:6]), int(digits[6])
    return (1 <= mm <= 12) and (1 <= dd <= 31) and (1 <= g <= 8)

def format_korean_id(digits):
    return f"{digits[:6]}-{digits[6:]}"

def is_duplicate_id(idnum, rows):
    return any(r[5] == idnum and r[8] == "ожидание" for r in rows[1:])

def validate_account(digits, bank):
    # Проверяем только что введены цифры и минимум 8 символов
    return bool(re.match(r"^\d{8,20}$", digits))

# ========= HELPERS =========

def get_next_id(rows):
    """Номер очереди сбрасывается каждый день в 00:00."""
    today = datetime.now().strftime("%Y-%m-%d")
    today_ids = []
    for r in rows[1:]:
        if len(r) > 1 and r[1].startswith(today):
            try:
                today_ids.append(int(r[0]))
            except (ValueError, TypeError):
                pass
    return 1 if not today_ids else max(today_ids) + 1

def get_today_groups(rows):
    today = datetime.now().strftime("%Y-%m-%d")
    groups = {}
    for r in rows[1:]:
        if r[1].startswith(today) and r[8] == "ожидание":
            groups[r[10]] = groups.get(r[10], 0) + 1
    return groups

def get_order(rows):
    """Возвращает GID групп в порядке номера очереди (queue_number)."""
    today = datetime.now().strftime("%Y-%m-%d")
    seen = set()
    pairs = []  # (queue_number, gid)
    for r in rows[1:]:
        if len(r) > 10 and r[1].startswith(today) and r[8] == "ожидание":
            if r[10] not in seen:
                seen.add(r[10])
                try:
                    pairs.append((int(r[0]), r[10]))
                except (ValueError, TypeError):
                    pass
    pairs.sort()  # сортируем по номеру очереди
    return [g for _, g in pairs]

def user_already_in_queue(tg_id, rows):
    return any(r[9] == str(tg_id) and r[8] == "ожидание" for r in rows[1:])

# ========= ОТПРАВКА ФОТО В КАНАЛ =========

async def forward_photos_to_channel(bot, person, queue_id):
    """Отправляем фото документов в приватный канал, возвращаем кликабельные ссылки."""
    today = datetime.now().strftime("%Y-%m-%d")
    caption = (
        f"📋 Очередь #{queue_id} · 📅 {today}\n"
        f"👤 {person['name']}\n"
        f"🪪 {person['idnum']}\n"
        f"🏦 {person['bank']} · {person['account']}\n"
        f"📱 {person['phone']}"
    )
    # ID канала без минуса и первых четырёх цифр для формирования ссылки
    channel_id_str = str(PHOTOS_CHANNEL_ID).replace("-100", "")
    id_link   = ""
    bank_link = ""
    try:
        if person.get("id_file_id"):
            msg = await bot.send_photo(
                PHOTOS_CHANNEL_ID,
                person["id_file_id"],
                caption=f"🪪 ID документ\n{caption}"
            )
            id_link = f"https://t.me/c/{channel_id_str}/{msg.message_id}"
            print(f"[PHOTO] ✅ ID фото → {id_link}")
        if person.get("bank_file_id"):
            msg = await bot.send_photo(
                PHOTOS_CHANNEL_ID,
                person["bank_file_id"],
                caption=f"🏦 Банковская книжка\n{caption}"
            )
            bank_link = f"https://t.me/c/{channel_id_str}/{msg.message_id}"
            print(f"[PHOTO] ✅ Банк фото → {bank_link}")
        if not person.get("id_file_id") and not person.get("bank_file_id"):
            print("[PHOTO] ℹ️ Фото не было — данные введены вручную")
    except Exception as e:
        print(f"[PHOTO] ❌ Ошибка: {e}")
    return id_link, bank_link

# ========= GPT-4o VISION =========

async def gpt_extract_id(photo_bytes):
    b64 = base64.b64encode(photo_bytes).decode()
    prompt = (
        "You are a careful OCR system reading a Korean ID document. "
        "Read EVERY character very carefully. Common confusions to AVOID: "
        "  - digit '0' vs letter 'O' — in number fields it's ALWAYS '0' (zero) "
        "  - digit '1' vs letter 'I' or 'l' — in number fields it's ALWAYS '1' "
        "  - digit '8' vs letter 'B' — in number fields it's ALWAYS '8' "
        "  - digit '4' vs digit '1' — look at the shape carefully (4 has a closed top, 1 is straight) "
        "  - digit '5' vs digit '6' — '5' has a flat top, '6' has a curved top "
        "  - digit '6' vs digit '8' — '6' is open at top, '8' is closed "
        "Look at each digit at least TWICE before deciding. "
        "\n\n"
        "Extract two fields: "
        "1) Full name in Latin letters (romanized). "
        "   If the name is split across two lines with a hyphen at the end of the first line "
        "   (e.g. 'VISS-' / 'ARIONOVNA'), MERGE them WITHOUT the hyphen → 'VISSARIONOVNA'. "
        "   Result must contain only Latin letters and SPACES — no hyphens inside a word. "
        "2) ID number in format XXXXXX-XXXXXXX (6 digits, dash, 7 digits). "
        "   The first 6 digits are date YYMMDD (year, month 01-12, day 01-31). "
        "   If you read MM>12 or DD>31 or DD=00, you misread — re-check those digits. "
        "\n\n"
        "Return ONLY a JSON object, no explanation, no markdown:\n"
        '{"name": "LATIN NAME HERE", "idnum": "XXXXXX-XXXXXXX"}\n'
        "If you cannot read a value, set it to null."
    )
    async with httpx.AsyncClient(timeout=40) as h:
        resp = await h.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": "gpt-4o", "max_tokens": 300,
                "temperature": 0,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/jpeg;base64,{b64}", "detail": "high"
                    }},
                ]}],
            },
        )
    result = resp.json()
    print("[GPT ID RAW]", result)
    if "error" in result:
        raise ValueError(f"OpenAI error: {result['error']}")
    msg = result["choices"][0]["message"]
    # Обрабатываем отказ GPT
    if msg.get("refusal") or not msg.get("content"):
        raise ValueError(f"GPT refused: {msg.get('refusal')}")
    raw = msg["content"]
    print("[GPT ID CONTENT]", raw)
    raw = re.sub(r"```json|```", "", raw).strip()
    match = re.search(r"\{.*?\}", raw, re.DOTALL)
    if match:
        raw = match.group(0)
    data = json.loads(raw)
    # Проверяем что оба поля не null
    if not data.get("name") or not data.get("idnum"):
        raise ValueError("Fields are null")
    # Пост-обработка: убираем дефисы переноса слов в имени
    # (если GPT всё-таки оставил дефис между буквами, например VISS-ARIONOVNA)
    if data.get("name"):
        # Удаляем дефис если он окружён буквами (а не пробелами)
        data["name"] = re.sub(r"(?<=[A-Za-zА-Яа-я])-(?=[A-Za-zА-Яа-я])", "", data["name"])
        # Также убираем повторяющиеся пробелы
        data["name"] = re.sub(r"\s+", " ", data["name"]).strip()
    return data

async def gpt_extract_bank(photo_bytes):
    b64 = base64.b64encode(photo_bytes).decode()
    prompt = (
        "You are a careful OCR system reading a Korean bank passbook (통장). "
        "Find the bank name and account number. "
        "\n\n"
        "When reading the ACCOUNT NUMBER (digits only), be EXTREMELY careful with these confusions: "
        "  - digit '0' vs letter 'O' — it's ALWAYS '0' "
        "  - digit '1' vs letter 'I' / 'l' — it's ALWAYS '1' "
        "  - digit '8' vs letter 'B' — it's ALWAYS '8' "
        "  - digit '4' vs digit '1' — '4' has a closed shape, '1' is straight "
        "  - digit '5' vs digit '6' — '5' has a flat top, '6' has a curved top "
        "  - digit '6' vs digit '8' — '6' is open at top, '8' is closed "
        "  - digit '7' vs digit '1' — '7' has a horizontal top stroke "
        "Look at each digit at least TWICE. "
        "Account number must contain ONLY digits, no dashes, no spaces. "
        "\n\n"
        "Map Korean bank names to these EXACT values: "
        "기업->기업은행 · Айбикей, 국민->국민은행 · Кукмин, 우리->우리은행 · Урибанк, "
        "신한->신한은행 · Шинхан, 하나->하나은행 · Хана, 농협->농협은행 · Нонхеп. "
        "If bank is not in this list, return ДРУГОЙ. "
        "\n\n"
        "Return ONLY a JSON object, no explanation, no markdown:\n"
        '{"bank": "국민은행 · Кукмин", "account": "digits only no dashes"}\n'
        "If you cannot read a field, set it to null."
    )
    async with httpx.AsyncClient(timeout=40) as h:
        resp = await h.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": "gpt-4o", "max_tokens": 300,
                "temperature": 0,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/jpeg;base64,{b64}", "detail": "high"
                    }},
                ]}],
            },
        )
    result = resp.json()
    print("[GPT BANK RAW]", result)
    if "error" in result:
        raise ValueError(f"OpenAI error: {result['error']}")
    msg = result["choices"][0]["message"]
    if msg.get("refusal") or not msg.get("content"):
        raise ValueError(f"GPT refused: {msg.get('refusal')}")
    raw = msg["content"]
    print("[GPT BANK CONTENT]", raw)
    raw = re.sub(r"```json|```", "", raw).strip()
    match = re.search(r"\{.*?\}", raw, re.DOTALL)
    if match:
        raw = match.group(0)
    return json.loads(raw)

# ========= START =========


# ========= ПРОВЕРКА РОЛЕЙ =========

def is_admin(user_id):
    """Проверка: админ ли пользователь (через config или таблицу)."""
    if user_id in ADMINS:
        return True
    try:
        ops_sheet = gclient.open(SHEET_NAME).worksheet("Операторы")
        ops = ops_sheet.get_all_values()
        for op in ops[1:]:
            if len(op) >= 4 and str(op[0]) == str(user_id) and op[3] == "Администратор":
                return True
    except Exception as e:
        print(f"[ROLE] Ошибка проверки админа: {e}")
    return False

def is_operator(user_id):
    """Проверка: оператор или админ ли пользователь."""
    if is_admin(user_id):
        return True
    try:
        ops_sheet = gclient.open(SHEET_NAME).worksheet("Операторы")
        ops = ops_sheet.get_all_values()
        for op in ops[1:]:
            if len(op) >= 4 and str(op[0]) == str(user_id):
                return True
    except Exception as e:
        print(f"[ROLE] Ошибка проверки оператора: {e}")
    return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_operator(update.effective_user.id):
        await update.message.reply_text("Панель оператора", reply_markup=admin_keyboard)
    else:
        await update.message.reply_text(CONSENT_TEXT, reply_markup=main_keyboard)

# ========= JOIN =========

async def join_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = sheet.get_all_values()
    if user_already_in_queue(update.effective_user.id, rows):
        await update.message.reply_text(
            "Вы уже стоите в очереди!\n\n"
            "📊 Нажмите «Проверить очередь» чтобы узнать вашу позицию.",
            reply_markup=main_keyboard,
        )
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["group"]    = []
    context.user_data["group_id"] = str(uuid.uuid4())[:8]
    await update.message.reply_text(
        "Как хотите заполнить данные?",
        reply_markup=ReplyKeyboardRemove()
    )
    await update.message.reply_text(
        "👇 Выберите способ:",
        reply_markup=method_keyboard
    )
    return CHOOSE_METHOD

async def choose_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "scan":
        await query.edit_message_text(
            "📷 Шаг 1 из 2: отправьте фото удостоверения личности (주민등록증)\n\n"
            "Прикрепите фото через скрепку 📎 — камера или галерея."
        )
        return SCAN_ID
    else:
        # Очищаем распознанные данные — пользователь будет вводить заново
        context.user_data.pop("name", None)
        context.user_data.pop("idnum", None)
        context.user_data.pop("id_file_id", None)
        await query.message.reply_text(
            "Введите данные вручную:\n\nФамилия Имя Отчество как в ID:\n(только латиница · пример: KIM MINJUN SUNGHO)",
            reply_markup=ReplyKeyboardRemove()
        )
        return NAME

# ========= SCAN ID =========

async def scan_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Пожалуйста, отправьте фото документа 📷")
        return SCAN_ID
    await update.message.reply_text("⏳ Распознаю данные из ID...")
    file        = await context.bot.get_file(update.message.photo[-1].file_id)
    photo_bytes = bytes(await file.download_as_bytearray())
    try:
        data  = await gpt_extract_id(photo_bytes)
        name  = (data.get("name")  or "").strip().upper()
        idnum = (data.get("idnum") or "").strip()
        if not name or not idnum:
            raise ValueError("empty")
    except Exception:
        await update.message.reply_text(
            "❌ Не удалось распознать документ. Попробуйте ещё раз или введите вручную.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Ещё раз",        callback_data="retry_id")],
                [InlineKeyboardButton("✏️ Ввести вручную", callback_data="manual_id")],
            ]),
        )
        return SCAN_ID
    context.user_data["name"]        = name
    context.user_data["idnum"]       = idnum
    context.user_data["id_file_id"]  = update.message.photo[-1].file_id
    await update.message.reply_text(
        f"Данные из ID:\n\n👤 ФИО: *{name}*\n🪪 ID: *{idnum}*\n\nВсё верно?",
        parse_mode="Markdown",
        reply_markup=confirm_keyboard,
    )
    return CONFIRM_ID

async def confirm_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "confirm":
        await query.edit_message_text(
            "Как добавить данные банка?",
            reply_markup=bank_method_keyboard,
        )
        return CHOOSE_BANK_METHOD
    else:
        # Показываем данные для редактирования — без полного перевода
        name  = context.user_data.get("name",  "—")
        idnum = context.user_data.get("idnum", "—")
        await query.edit_message_text(
            f"Исправьте нужные данные:\n👤 ФИО: {name}\n🪪 ID: {idnum}",
            reply_markup=make_id_edit_keyboard(name, idnum)
        )
        return CONFIRM_ID

async def handle_id_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатия кнопок в режиме редактирования ID данных."""
    query = update.callback_query
    await query.answer()

    if query.data == "edit_name":
        name = context.user_data.get("name", "")
        await query.message.reply_text(
            f"Текущее ФИО: *{name}*\nВведите исправленное ФИО:\n(только латиница · пример: KIM MINJUN SUNGHO)",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        return EDIT_NAME

    elif query.data == "edit_idnum":
        idnum = context.user_data.get("idnum", "")
        await query.message.reply_text(
            f"Текущий ID: *{idnum}*\nВведите исправленный ID номер (13 цифр):",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        return EDIT_IDNUM

    elif query.data == "confirm_id_edit":
        await query.edit_message_text(
            "Как добавить данные банка?",
            reply_markup=bank_method_keyboard,
        )
        return CHOOSE_BANK_METHOD

async def save_edited_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    ok, msg = validate_name(text)
    if not ok:
        await update.message.reply_text(
            f"❌ {msg}\n\nВведите ФИО ещё раз:",
            reply_markup=ReplyKeyboardRemove()
        )
        return EDIT_NAME
    context.user_data["name"] = text.upper()
    name  = context.user_data["name"]
    idnum = context.user_data.get("idnum", "—")
    await update.message.reply_text(
        f"✅ ФИО обновлено\n\nПроверьте данные:\n\n👤 ФИО: {name}\n🪪 ID: {idnum}",
        reply_markup=make_id_edit_keyboard(name, idnum)
    )
    return CONFIRM_ID

async def save_edited_idnum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    digits = re.sub(r"[^0-9]", "", update.message.text.strip())
    rows   = sheet.get_all_values()
    if not validate_korean_id(digits):
        await update.message.reply_text(
            "❌ Неверный ID. Введите 13 цифр:\nПример: 9012311234567",
            reply_markup=ReplyKeyboardRemove()
        )
        return EDIT_IDNUM
    formatted = format_korean_id(digits)
    if is_duplicate_id(formatted, rows):
        await update.message.reply_text(
            "❌ Этот ID уже в очереди. Введите другой:",
            reply_markup=ReplyKeyboardRemove()
        )
        return EDIT_IDNUM
    context.user_data["idnum"] = formatted
    name  = context.user_data.get("name", "—")
    idnum = formatted
    await update.message.reply_text(
        f"✅ ID обновлён\n\nПроверьте данные:\n\n👤 ФИО: {name}\n🪪 ID: {idnum}",
        reply_markup=make_id_edit_keyboard(name, idnum)
    )
    return CONFIRM_ID

async def choose_bank_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "scan_bank":
        await query.edit_message_text(
            "📷 Отправьте фото банковской книжки (통장)\n\n"
            "Прикрепите фото через скрепку 📎 — камера или галерея."
        )
        return SCAN_BANK
    else:
        await query.edit_message_text("Выберите банк:", reply_markup=None)
        await query.message.reply_text("Банк:", reply_markup=bank_keyboard)
        return BANK

# ========= SCAN BANK =========

async def scan_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Пожалуйста, отправьте фото банковской книжки 📷")
        return SCAN_BANK
    await update.message.reply_text("⏳ Распознаю данные банка...")
    file        = await context.bot.get_file(update.message.photo[-1].file_id)
    photo_bytes = bytes(await file.download_as_bytearray())
    try:
        data    = await gpt_extract_bank(photo_bytes)
        bank    = (data.get("bank")    or "").strip()
        account = re.sub(r"[^0-9]", "", data.get("account") or "")
        if not bank or bank not in VALID_BANKS or not account:
            raise ValueError("empty")
    except Exception:
        await update.message.reply_text(
            "❌ Не удалось распознать банковскую книжку. Попробуйте ещё раз или введите вручную.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Ещё раз",        callback_data="retry_bank")],
                [InlineKeyboardButton("✏️ Ввести вручную", callback_data="manual_bank_from_scan")],
            ]),
        )
        return SCAN_BANK
    context.user_data["bank"]          = bank
    context.user_data["account"]       = account
    context.user_data["bank_file_id"]  = update.message.photo[-1].file_id
    # Возвращаем обычную клавиатуру
    await update.message.reply_text("✅ Фото получено!", reply_markup=main_keyboard)
    await update.message.reply_text(
        f"Данные банка:\n\n🏦 Банк: *{bank}*\n💳 Счёт: *{account}*\n\nВсё верно?",
        parse_mode="Markdown",
        reply_markup=confirm_keyboard,
    )
    return CONFIRM_BANK

async def confirm_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "confirm":
        await query.message.reply_text(
            "📱 Номер телефона\nФормат: 010-XXXX-XXXX\n\nВведите цифры — дефисы поставим сами",
            reply_markup=ReplyKeyboardRemove()
        )
        return PHONE
    else:
        bank    = context.user_data.get("bank",    "—")
        account = context.user_data.get("account", "—")
        await query.edit_message_text(
            f"Исправьте нужные данные:\n\n🏦 Банк: {bank}\n💳 Счёт: {account}",
            reply_markup=make_bank_edit_keyboard(bank, account)
        )
        return CONFIRM_BANK

async def handle_bank_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "edit_bank":
        await query.message.reply_text("Выберите банк:", reply_markup=bank_keyboard)
        return EDIT_BANK

    elif query.data == "edit_account":
        bank = context.user_data.get("bank", "")
        info = BANK_INFO.get(bank, {})
        hint = f"Начинается с {info.get('prefix','?')} · всего {info.get('total','?')} цифр" if info else "Введите номер счёта"
        account = context.user_data.get("account", "")
        await query.message.reply_text(
            f"Текущий счёт: *{account}*\n\n{hint}",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        return EDIT_ACCOUNT

    elif query.data == "confirm_bank_edit":
        await query.message.reply_text(
            "📱 Номер телефона\nФормат: 010-XXXX-XXXX\n\nВведите цифры — дефисы поставим сами",
            reply_markup=ReplyKeyboardRemove()
        )
        return PHONE

async def save_edited_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bank = update.message.text
    if bank not in BANK_INFO:
        await update.message.reply_text("❌ Выберите банк из списка:", reply_markup=bank_keyboard)
        return EDIT_BANK
    context.user_data["bank"] = bank
    account = context.user_data.get("account", "—")
    await update.message.reply_text(
        f"✅ Банк обновлён\n\nПроверьте данные:\n\n🏦 Банк: {bank}\n💳 Счёт: {account}",
        reply_markup=make_bank_edit_keyboard(bank, account)
    )
    return CONFIRM_BANK

async def save_edited_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    digits = re.sub(r"[^0-9]", "", update.message.text.strip())
    bank   = context.user_data.get("bank", "")
    if not validate_account(digits, bank):
        info = BANK_INFO.get(bank, {})
        hint = f"Начинается с {info.get('prefix','?')} · всего {info.get('total','?')} цифр" if info else ""
        await update.message.reply_text(f"❌ Неверный счёт. {hint}", reply_markup=ReplyKeyboardRemove())
        return EDIT_ACCOUNT
    context.user_data["account"] = digits
    account = digits
    await update.message.reply_text(
        f"✅ Счёт обновлён\n\nПроверьте данные:\n\n🏦 Банк: {bank}\n💳 Счёт: {account}",
        reply_markup=make_bank_edit_keyboard(bank, account)
    )
    return CONFIRM_BANK

# ========= INLINE CALLBACKS =========

async def inline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "retry_id":
        await query.edit_message_text("📷 Отправьте фото ID ещё раз:")
        return SCAN_ID
    elif query.data == "manual_id":
        await query.message.reply_text(
            "Фамилия Имя Отчество как в ID:\n"
            "(только латиница · пример: KIM MINJUN SUNGHO)",
            reply_markup=ReplyKeyboardRemove()
        )
        return NAME
    elif query.data == "retry_bank":
        await query.edit_message_text("📷 Отправьте фото банковской книжки ещё раз:")
        return SCAN_BANK
    elif query.data in ("manual_bank", "manual_bank_from_scan"):
        await query.edit_message_text("Выберите банк:", reply_markup=None)
        await query.message.reply_text("Банк:", reply_markup=bank_keyboard)
        return BANK

# ========= MANUAL INPUT =========

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    ok, msg = validate_name(text)
    if not ok:
        await update.message.reply_text(
            f"❌ {msg}\n\nФамилия Имя Отчество как в ID:\n"
            "(только латиница · пример: KIM MINJUN SUNGHO)"
        )
        return NAME
    context.user_data["name"] = text.upper()
    await update.message.reply_text(
        "📱 Номер телефона\n"
        "Формат: 010-XXXX-XXXX\n\n"
        "Введите цифры — дефисы поставим сами",
        reply_markup=ReplyKeyboardRemove()
    )
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    digits = re.sub(r"[^0-9]", "", update.message.text.strip())
    if not validate_phone(digits):
        await update.message.reply_text(
            "❌ Неверный номер\n\n"
            "📱 Номер телефона\nФормат: 010-XXXX-XXXX\n\n"
            "Пример: 01012345678 или 010-1234-5678"
        )
        return PHONE
    formatted = format_phone(digits)
    context.user_data["phone"] = formatted
    await update.message.reply_text(f"✅ *{formatted}*", parse_mode="Markdown")
    await update.message.reply_text("Оператор:", reply_markup=operator_keyboard)
    return OPERATOR

VALID_OPERATORS = ["SKT", "KT", "LG U+", "SKT KADAFON", "LG KADAFON", "KT KADAFON"]

async def get_operator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text not in VALID_OPERATORS:
        await update.message.reply_text(
            "❌ Пожалуйста, выберите оператора из списка:",
            reply_markup=operator_keyboard
        )
        return OPERATOR
    context.user_data["operator"] = text
    if context.user_data.get("idnum"):
        if context.user_data.get("bank") and context.user_data.get("account"):
            return await _save_person_ask_more(update, context)
        await update.message.reply_text("Банк:", reply_markup=bank_keyboard)
        return BANK
    await update.message.reply_text(
        "🪪 ID номер\nФормат: XXXXXX-XXXXXXX\n\n"
        "Введите цифры — дефис поставим сами",
        reply_markup=ReplyKeyboardRemove()
    )
    return IDNUM

async def get_idnum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    digits = re.sub(r"[^0-9]", "", update.message.text.strip())
    rows   = sheet.get_all_values()
    if not validate_korean_id(digits):
        await update.message.reply_text(
            "❌ Неверный ID\n\n🪪 ID номер\n"
            "Формат: XXXXXX-XXXXXXX (13 цифр)\n\nПример: 9012311234567"
        )
        return IDNUM
    formatted = format_korean_id(digits)
    if is_duplicate_id(formatted, rows):
        await update.message.reply_text(
            "❌ Этот ID уже зарегистрирован в очереди\n\n🪪 Введите 13 цифр ID:"
        )
        return IDNUM
    context.user_data["idnum"] = formatted
    await update.message.reply_text(f"✅ *{formatted}*", parse_mode="Markdown")
    await update.message.reply_text("Банк:", reply_markup=bank_keyboard)
    return BANK

async def get_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bank = update.message.text
    if bank not in BANK_INFO:
        await update.message.reply_text(
            "❌ Пожалуйста, выберите банк из списка:",
            reply_markup=bank_keyboard
        )
        return BANK
    context.user_data["bank"] = bank
    await update.message.reply_text(
        f"💳 Введите номер счёта {bank}:",
        reply_markup=ReplyKeyboardRemove()
    )
    return ACCOUNT

async def get_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    digits = re.sub(r"[^0-9]", "", update.message.text.strip())
    bank   = context.user_data["bank"]
    if not validate_account(digits, bank):
        await update.message.reply_text(
            f"❌ Неверный номер счёта\n\n"
            f"💳 Введите номер счёта {bank}:\n"
            f"_(только цифры, от 8 до 20 символов)_",
            parse_mode="Markdown"
        )
        return ACCOUNT
    context.user_data["account"] = digits
    await update.message.reply_text(f"✅ *{digits}*", parse_mode="Markdown")
    # Если телефона ещё нет (scan-флоу с ручным банком) → переход к телефону
    if not context.user_data.get("phone"):
        await update.message.reply_text(
            "📱 Номер телефона\nФормат: 010-XXXX-XXXX\n\nВведите цифры — дефисы поставим сами",
            reply_markup=ReplyKeyboardRemove()
        )
        return PHONE
    return await _save_person_ask_more(update, context)

async def _save_person_ask_more(update, context):
    """Сначала показываем экран проверки, и только после ✅ — сохраняем в группу."""
    return await show_review_screen(update, context)

async def show_review_screen(update, context):
    """Показывает сводку всех данных текущего человека."""
    text = (
        "📋 ПРОВЕРЬТЕ ДАННЫЕ\n\n"
        f"👤 {context.user_data.get('name', '—')}\n"
        f"🪪 {context.user_data.get('idnum', '—')}\n"
        f"📱 {context.user_data.get('phone', '—')} ({context.user_data.get('operator', '—')})\n"
        f"🏦 {context.user_data.get('bank', '—')}\n"
        f"💳 {context.user_data.get('account', '—')}"
    )
    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Всё верно", callback_data="review_ok")],
        [InlineKeyboardButton("✏️ Исправить", callback_data="review_edit")],
    ])
    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=kbd)
    else:
        await update.message.reply_text(text, reply_markup=kbd, reply_markup_remove=False) if False else await update.message.reply_text(text, reply_markup=kbd)
    return REVIEW

async def review_action(update, context):
    """Обработка кнопок на экране проверки."""
    query = update.callback_query
    await query.answer()
    if query.data == "review_ok":
        # Сохраняем в группу
        context.user_data["group"].append({
            "name":        context.user_data["name"],
            "phone":       context.user_data["phone"],
            "operator":    context.user_data["operator"],
            "idnum":       context.user_data["idnum"],
            "bank":        context.user_data["bank"],
            "account":     context.user_data["account"],
            "id_file_id":  context.user_data.pop("id_file_id",   None),
            "bank_file_id":context.user_data.pop("bank_file_id", None),
        })
        await query.edit_message_text("✅ Данные сохранены")
        await query.message.reply_text(
            "Добавить ещё одного человека?", reply_markup=add_more_keyboard
        )
        return ADD_MORE
    elif query.data == "review_edit":
        await query.edit_message_text(
            "Что исправить?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👤 ФИО", callback_data="rf_name")],
                [InlineKeyboardButton("🪪 ID номер", callback_data="rf_idnum")],
                [InlineKeyboardButton("📱 Телефон", callback_data="rf_phone")],
                [InlineKeyboardButton("📞 Оператор", callback_data="rf_operator")],
                [InlineKeyboardButton("🏦 Банк", callback_data="rf_bank")],
                [InlineKeyboardButton("💳 Номер счёта", callback_data="rf_account")],
                [InlineKeyboardButton("⬅️ Назад к проверке", callback_data="rf_back")],
            ])
        )
        return REVIEW_FIELD

async def review_field(update, context):
    """Запрос новой версии конкретного поля."""
    query = update.callback_query
    await query.answer()
    field = query.data
    if field == "rf_back":
        await query.message.delete()
        return await show_review_screen(update, context)
    if field == "rf_name":
        await query.edit_message_text(f"Текущее ФИО: {context.user_data.get('name','—')}\n\nВведите новое ФИО (только латиница):")
        return REVIEW_NAME
    if field == "rf_idnum":
        await query.edit_message_text(f"Текущий ID: {context.user_data.get('idnum','—')}\n\nВведите новый ID (13 цифр):")
        return REVIEW_IDNUM
    if field == "rf_phone":
        await query.edit_message_text(f"Текущий телефон: {context.user_data.get('phone','—')}\n\nВведите новый номер (010-XXXX-XXXX):")
        return REVIEW_PHONE
    if field == "rf_operator":
        await query.edit_message_text(f"Текущий оператор: {context.user_data.get('operator','—')}")
        await query.message.reply_text("Выберите оператора:", reply_markup=operator_keyboard)
        return REVIEW_OPERATOR
    if field == "rf_bank":
        await query.edit_message_text(f"Текущий банк: {context.user_data.get('bank','—')}")
        await query.message.reply_text("Выберите банк:", reply_markup=bank_keyboard)
        return REVIEW_BANK
    if field == "rf_account":
        await query.edit_message_text(f"Текущий счёт: {context.user_data.get('account','—')}\n\nВведите новый номер счёта:")
        return REVIEW_ACCOUNT

async def review_save_name(update, context):
    text = update.message.text.strip()
    ok, msg = validate_name(text)
    if not ok:
        await update.message.reply_text(f"❌ {msg}\n\nВведите ФИО ещё раз:")
        return REVIEW_NAME
    context.user_data["name"] = text.upper()
    await update.message.reply_text("✅ Обновлено")
    return await show_review_screen(update, context)

async def review_save_idnum(update, context):
    digits = re.sub(r"[^0-9]", "", update.message.text.strip())
    if not validate_korean_id(digits):
        await update.message.reply_text("❌ Неверный ID. Введите 13 цифр:")
        return REVIEW_IDNUM
    context.user_data["idnum"] = format_korean_id(digits)
    await update.message.reply_text("✅ Обновлено")
    return await show_review_screen(update, context)

async def review_save_phone(update, context):
    digits = re.sub(r"[^0-9]", "", update.message.text.strip())
    if not (digits.startswith("010") and len(digits) == 11):
        await update.message.reply_text("❌ Неверный формат. Введите 010-XXXX-XXXX:")
        return REVIEW_PHONE
    context.user_data["phone"] = f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    await update.message.reply_text("✅ Обновлено")
    return await show_review_screen(update, context)

async def review_save_operator(update, context):
    op = update.message.text.strip()
    if op not in VALID_OPERATORS:
        await update.message.reply_text("❌ Выберите оператора из списка:", reply_markup=operator_keyboard)
        return REVIEW_OPERATOR
    context.user_data["operator"] = op
    await update.message.reply_text("✅ Обновлено", reply_markup=ReplyKeyboardRemove())
    return await show_review_screen(update, context)

async def review_save_bank(update, context):
    bank = update.message.text.strip()
    if bank not in BANK_INFO:
        await update.message.reply_text("❌ Выберите банк из списка:", reply_markup=bank_keyboard)
        return REVIEW_BANK
    context.user_data["bank"] = bank
    await update.message.reply_text("✅ Обновлено", reply_markup=ReplyKeyboardRemove())
    return await show_review_screen(update, context)

async def review_save_account(update, context):
    digits = re.sub(r"[^0-9]", "", update.message.text.strip())
    bank = context.user_data.get("bank", "")
    if not validate_account(digits, bank):
        await update.message.reply_text("❌ Неверный счёт. Введите ещё раз:")
        return REVIEW_ACCOUNT
    context.user_data["account"] = digits
    await update.message.reply_text("✅ Обновлено")
    return await show_review_screen(update, context)

# ========= SAVE GROUP =========

async def add_more(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text not in ["➕ Добавить ещё", "🏁 Завершить"]:
        await update.message.reply_text(
            "❌ Пожалуйста, нажмите одну из кнопок:",
            reply_markup=add_more_keyboard
        )
        return ADD_MORE
    if update.message.text == "➕ Добавить ещё":
        for key in ["name", "phone", "operator", "idnum", "bank", "account"]:
            context.user_data.pop(key, None)
        await update.message.reply_text(
            "Как заполнить данные следующего?",
            reply_markup=ReplyKeyboardRemove()
        )
        await update.message.reply_text(
            "👇 Выберите способ:",
            reply_markup=method_keyboard
        )
        return CHOOSE_METHOD

    group    = context.user_data["group"]
    group_id = context.user_data["group_id"]
    now      = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows     = sheet.get_all_values()
    queue_id = get_next_id(rows)

    await update.message.reply_text("⏳ Сохраняю данные...")

    for p in group:
        # Загружаем фото в Google Drive
        id_link   = ""
        bank_link = ""
        id_link, bank_link = await forward_photos_to_channel(context.bot, p, queue_id)

        sheet.append_row([
            queue_id, now,
            p["name"], p["phone"], p["operator"],
            p["idnum"], p["bank"], p["account"],
            "ожидание",
            str(update.effective_user.id),
            group_id,
            id_link,
            bank_link,
        ])

    # Считаем сколько человек впереди
    updated_rows = sheet.get_all_values()
    order  = get_order(updated_rows)
    groups = get_today_groups(updated_rows)
    ahead  = sum(groups.get(g, 0) for g in order[:order.index(group_id)] if group_id in order)

    context.user_data.clear()
    await update.message.reply_text(
        f"✅ Вы записаны в очередь!\n"
        f"📍 Ваш номер: {queue_id}\n"
        f"👥 Перед вами: {ahead} человек",
        reply_markup=main_keyboard,
    )
    return ConversationHandler.END

# ========= CHECK =========

async def check_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows  = sheet.get_all_values()
    order = get_order(rows)
    user_gid = user_id = None
    for r in rows[1:]:
        if str(update.effective_user.id) == r[9] and r[8] == "ожидание":
            user_gid, user_id = r[10], r[0]
            break
    if not user_gid:
        await update.message.reply_text("Вы не в очереди", reply_markup=main_keyboard)
        return
    position = order.index(user_gid) + 1 if user_gid in order else 0
    groups   = get_today_groups(rows)
    ahead    = sum(groups.get(g, 0) for g in order[:order.index(user_gid)])
    await update.message.reply_text(
        f"📍 Ваш номер в очереди: {user_id}\n"
        f"👥 Перед вами: {ahead} человек",
        reply_markup=main_keyboard,
    )

# ========= ADMIN =========

async def show_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_operator(update.effective_user.id):
        return
    rows   = sheet.get_all_values()
    order  = get_order(rows)
    groups = get_today_groups(rows)
    # Для каждой группы запоминаем имя первого человека и НОМЕР ОЧЕРЕДИ
    first_names    = {}
    queue_numbers  = {}
    for r in rows[1:]:
        if len(r) > 10 and r[8] == "ожидание" and r[10] not in first_names:
            first_names[r[10]]   = r[2]
            queue_numbers[r[10]] = r[0]
    text  = "📋 ОЧЕРЕДЬ:\n\n"
    total = 0
    for g in order:
        count  = groups.get(g, 0)
        total += count
        name   = first_names.get(g, "—")
        qnum   = queue_numbers.get(g, "?")
        text  += f"№{qnum}. {name}" + (f" +{count-1} чел.\n" if count > 1 else "\n")
    text += f"\n👥 ИТОГО: {total}"
    await update.message.reply_text(text)

# ========= QUEUE NOTIFICATIONS =========

async def send_queue_notifications(context, rows):
    order  = get_order(rows)
    groups = get_today_groups(rows)
    group_tgid = {}
    for r in rows[1:]:
        gid = r[10]
        if r[8] == "ожидание" and gid not in group_tgid:
            group_tgid[gid] = r[9]
    ahead = 0
    for gid in order:
        if ahead in (1, 2):
            tg_id = group_tgid.get(gid)
            if tg_id:
                word = "человек" if ahead == 1 else "человека"
                msg  = (
                    "\u23f3 \u041f\u0435\u0440\u0435\u0434 \u0432\u0430\u043c\u0438 "
                    "\u043e\u0441\u0442\u0430\u043b\u043e\u0441\u044c "
                    + str(ahead) + " " + word + "\n"
                    "\u041f\u043e\u0436\u0430\u043b\u0443\u0439\u0441\u0442\u0430, "
                    "\u0431\u0443\u0434\u044c\u0442\u0435 \u0433\u043e\u0442\u043e\u0432\u044b!"
                )
                try:
                    await context.bot.send_message(int(tg_id), msg)
                except Exception:
                    pass
        ahead += groups.get(gid, 0)

# ========= NEXT CLIENT =========

async def next_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_operator(update.effective_user.id):
        return
    rows  = sheet.get_all_values()
    order = get_order(rows)
    if not order:
        await update.message.reply_text("Очередь пуста")
        return
    gid = order[0]
    # Собираем ВСЕХ людей в группе с полными данными
    group_members = []  # (row_index, queue_number, name, tg_id, full_row)
    for i, r in enumerate(rows[1:], 2):
        if len(r) > 10 and r[10] == gid:
            try:
                qn = int(r[0])
            except (ValueError, TypeError):
                qn = 0
            group_members.append((i, qn, r[2], r[9], r))
    # Сортируем по queue_number
    group_members.sort(key=lambda x: x[1])
    if not group_members:
        await update.message.reply_text("Очередь пуста")
        return
    main_row, queue_number, name, telegram_id, main_data = group_members[0]
    group_size = len(group_members)
    extra = group_size - 1
    # Помечаем всех "вызван"
    for row_idx, _, _, _, _ in group_members:
        sheet.update_cell(row_idx, 9, "вызван")
    # Определяем стол оператора, который вызывает
    operator_id = update.effective_user.id
    operator_window = None
    operator_name   = None
    try:
        ops_sheet = gclient.open(SHEET_NAME).worksheet("Операторы")
        ops = ops_sheet.get_all_values()
        for op in ops[1:]:
            if len(op) >= 3 and str(op[0]) == str(operator_id):
                operator_name   = op[1]
                operator_window = op[2]
                break
    except Exception as e:
        print(f"[NEXT] Не удалось определить стол оператора: {e}")
    # Уведомление клиенту (если у него есть Telegram, т.е. TG_ID != 0)
    extra_text = f"\n👥 Вместе с вами: {extra} человек" if extra > 0 else ""
    table_text = f"\n🪟 Подойдите к столу №{operator_window}" if operator_window else ""
    has_telegram = str(telegram_id) not in ("0", "", "—")
    if has_telegram:
        try:
            await context.bot.send_message(
                int(telegram_id),
                f"🔔 Вас вызывают!\n№{queue_number} — {name}{table_text}{extra_text}"
            )
        except Exception as e:
            print(f"[NEXT] Ошибка уведомления: {e}")
    else:
        print(f"[NEXT] Клиент №{queue_number} без Telegram — голосовой вызов")
    # Уведомление оператору — с полной информацией о клиенте(ах)
    def format_person(row):
        # row: № | Время | ФИО | Телефон | Оператор | ID | Банк | Счёт | Статус | TG_ID | GID | ...
        parts = []
        if len(row) > 2 and row[2]: parts.append(f"👤 {row[2]}")
        if len(row) > 3 and row[3]: parts.append(f"📱 {row[3]}")
        if len(row) > 4 and row[4]: parts.append(f"📞 {row[4]}")
        if len(row) > 5 and row[5]: parts.append(f"🪪 {row[5]}")
        if len(row) > 6 and row[6]: parts.append(f"🏦 {row[6]}")
        if len(row) > 7 and row[7]: parts.append(f"💳 {row[7]}")
        return "\n".join(parts)
    msg = f"➡️ Вызван №{queue_number}"
    if extra > 0:
        msg += f"  ·  Группа: {group_size} чел"
    if not has_telegram:
        msg += "\n\n📢 БЕЗ TELEGRAM — ПОЗОВИТЕ ГОЛОСОМ"
    msg += "\n\n" + format_person(main_data)
    if extra > 0:
        for idx, (_, _, _, _, row) in enumerate(group_members[1:], 2):
            extra_tg = row[9] if len(row) > 9 else "0"
            extra_no_tg = str(extra_tg) in ("0", "", "—")
            label = " (без Telegram)" if extra_no_tg else ""
            msg += f"\n\n— {idx}-й человек{label} —\n" + format_person(row)
    await update.message.reply_text(msg)
    updated_rows = sheet.get_all_values()
    await send_queue_notifications(context, updated_rows)

# ========= RUN =========


# ========= ADMIN PANEL =========



async def cancel_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена действия и возврат в меню админки."""
    context.user_data.pop("op_id", None)
    context.user_data.pop("op_name", None)
    context.user_data.pop("op_window", None)
    await update.message.reply_text(
        "❌ Действие отменено",
        reply_markup=admin_menu_keyboard
    )
    return ADMIN_MENU

async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вход в админ-панель."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа")
        return ConversationHandler.END
    await update.message.reply_text(
        "⚙️ АДМИНИСТРИРОВАНИЕ\n\nВыберите действие:",
        reply_markup=admin_menu_keyboard
    )
    return ADMIN_MENU

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главное меню админа."""
    text = update.message.text
    
    if text == "➕ Добавить оператора":
        await update.message.reply_text(
            "➕ ДОБАВЛЕНИЕ ОПЕРАТОРА\n\nШаг 1/4: Введите Telegram ID оператора\n\n💡 Нажмите /cancel чтобы отменить",
            reply_markup=ReplyKeyboardMarkup([["❌ Отмена"]], resize_keyboard=True)
        )
        return ADD_OP_ID
    
    elif text == "👥 Просмотреть операторов":
        rows = sheet.get_all_values()
        ops_sheet = gclient.open(SHEET_NAME).worksheet("Операторы")
        ops = ops_sheet.get_all_values()
        if len(ops) <= 1:
            await update.message.reply_text("📭 Операторов нет")
            return ADMIN_MENU
        msg = "👥 СПИСОК ОПЕРАТОРОВ\n\n"
        for op in ops[1:]:
            if len(op) >= 4:
                msg += f"👤 {op[1]} · 🪟 Стол {op[2]} · {op[3]}\n"
        await update.message.reply_text(msg, reply_markup=admin_menu_keyboard)
        return ADMIN_MENU
    
    elif text == "❌ Удалить оператора":
        ops_sheet = gclient.open(SHEET_NAME).worksheet("Операторы")
        ops = ops_sheet.get_all_values()
        if len(ops) <= 1:
            await update.message.reply_text("📭 Операторов нет")
            return ADMIN_MENU
        btns = [[InlineKeyboardButton(f"{op[1]} (Стол {op[2]})", callback_data=f"del_op_{op[0]}")] 
                for op in ops[1:] if len(op) >= 2]
        await update.message.reply_text(
            "Выберите оператора для удаления:",
            reply_markup=InlineKeyboardMarkup(btns)
        )
        return DELETE_OP
    
    elif text == "✏️ Редактировать оператора":
        try:
            ops_sheet = gclient.open(SHEET_NAME).worksheet("Операторы")
            ops = ops_sheet.get_all_values()
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}", reply_markup=admin_menu_keyboard)
            return ADMIN_MENU
        if len(ops) <= 1:
            await update.message.reply_text("📭 Операторов нет", reply_markup=admin_menu_keyboard)
            return ADMIN_MENU
        btns = [[InlineKeyboardButton(f"{op[1]} (Стол {op[2]})", callback_data=f"edit_op_{op[0]}")]
                for op in ops[1:] if len(op) >= 2]
        btns.append([InlineKeyboardButton("❌ Отмена", callback_data="edit_cancel")])
        await update.message.reply_text(
            "Выберите оператора для редактирования:",
            reply_markup=InlineKeyboardMarkup(btns)
        )
        return EDIT_OP_SELECT

    elif text == "📊 Статистика":
        try:
            rows = sheet.get_all_values()
            today = datetime.now().strftime("%Y-%m-%d")
            today_rows = [r for r in rows[1:] if len(r) > 1 and r[1].startswith(today)]
            served = sum(1 for r in today_rows if len(r) > 8 and r[8] == "обслужен")
            waiting = sum(1 for r in today_rows if len(r) > 8 and r[8] == "ожидание")
            total_month = len([r for r in rows[1:] if len(r) > 1 and r[1][:7] == today[:7]])
            ops_sheet = gclient.open(SHEET_NAME).worksheet("Операторы")
            ops_count = len(ops_sheet.get_all_values()) - 1
            msg = (
                f"📊 СТАТИСТИКА\n\n"
                f"📅 Сегодня ({today}):\n"
                f"   ✅ Обслужено: {served}\n"
                f"   ⏳ В ожидании: {waiting}\n\n"
                f"📆 За месяц: {total_month}\n"
                f"👥 Операторов: {ops_count}"
            )
            await update.message.reply_text(msg, reply_markup=admin_menu_keyboard)
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}", reply_markup=admin_menu_keyboard)
        return ADMIN_MENU

    elif text == "⬅️ Выход":
        user_id = update.effective_user.id
        kbd = admin_keyboard if is_operator(user_id) else main_keyboard
        await update.message.reply_text(
            "Вы вышли из админ-панели",
            reply_markup=kbd
        )
        return ConversationHandler.END
    
    return ADMIN_MENU

async def add_op_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ввод ID оператора."""
    text = update.message.text.strip()
    if text == "❌ Отмена":
        return await cancel_to_admin(update, context)
    try:
        op_id = int(text)
        context.user_data["op_id"] = op_id
        await update.message.reply_text(
            f"✅ ID: {op_id}\n\nШаг 2/4: Введите имя и фамилию\nПример: Иван Иванов",
            reply_markup=ReplyKeyboardMarkup([["❌ Отмена"]], resize_keyboard=True)
        )
        return ADD_OP_NAME
    except ValueError:
        await update.message.reply_text(
            "❌ Введите корректный ID (только цифры)",
            reply_markup=ReplyKeyboardMarkup([["❌ Отмена"]], resize_keyboard=True)
        )
        return ADD_OP_ID

async def add_op_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ввод имени оператора."""
    name = update.message.text.strip()
    if name == "❌ Отмена":
        return await cancel_to_admin(update, context)
    if len(name.split()) < 2:
        await update.message.reply_text(
            "❌ Введите имя и фамилию (минимум 2 слова)",
            reply_markup=ReplyKeyboardMarkup([["❌ Отмена"]], resize_keyboard=True)
        )
        return ADD_OP_NAME
    context.user_data["op_name"] = name
    await update.message.reply_text(
        f"✅ Имя: {name}\n\nШаг 3/4: Введите номер стола\nПример: 1",
        reply_markup=ReplyKeyboardMarkup([["❌ Отмена"]], resize_keyboard=True)
    )
    return ADD_OP_WINDOW

async def add_op_window(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ввод номера стола."""
    window = update.message.text.strip()
    if window == "❌ Отмена":
        return await cancel_to_admin(update, context)
    # Проверка занятости стола
    try:
        ops_sheet = gclient.open(SHEET_NAME).worksheet("Операторы")
        ops = ops_sheet.get_all_values()
        for op in ops[1:]:
            if len(op) >= 3 and op[2] == window:
                await update.message.reply_text(
                    f"❌ Стол {window} уже занят оператором {op[1]}\n\nВведите другой номер:",
                    reply_markup=ReplyKeyboardMarkup([["❌ Отмена"]], resize_keyboard=True)
                )
                return ADD_OP_WINDOW
    except Exception as e:
        print(f"[CHECK] Ошибка проверки стола: {e}")
    context.user_data["op_window"] = window
    await update.message.reply_text(
        f"✅ Стол: {window}\n\nШаг 4/4: Выберите роль",
        reply_markup=ReplyKeyboardRemove()
    )
    await update.message.reply_text("Роль:", reply_markup=role_keyboard)
    return ADD_OP_ROLE

async def add_op_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор роли оператора."""
    query = update.callback_query
    await query.answer()
    
    role = "Оператор" if query.data == "role_operator" else "Администратор"
    
    op_id = context.user_data["op_id"]
    op_name = context.user_data["op_name"]
    op_window = context.user_data["op_window"]
    
    try:
        ops_sheet = gclient.open(SHEET_NAME).worksheet("Операторы")
        ops_sheet.append_row([op_id, op_name, op_window, role, "Активный", datetime.now().strftime("%Y-%m-%d")])
        
        await query.edit_message_text(
            f"✅ Оператор добавлен!\n"
            f"👤 {op_name}\n"
            f"🪟 Стол {op_window}\n"
            f"📍 Роль: {role}"
        )
        context.user_data.clear()
        await query.message.reply_text("⚙️ АДМИНИСТРИРОВАНИЕ", reply_markup=admin_menu_keyboard)
        return ADMIN_MENU
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")
        return ADMIN_MENU

async def delete_op(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаление оператора."""
    query = update.callback_query
    await query.answer()
    
    op_id = query.data.replace("del_op_", "")
    
    try:
        ops_sheet = gclient.open(SHEET_NAME).worksheet("Операторы")
        ops = ops_sheet.get_all_values()
        for i, op in enumerate(ops[1:], 2):
            if op[0] == op_id:
                ops_sheet.delete_rows(i)
                await query.edit_message_text(f"✅ Оператор удалён")
                await query.message.reply_text("⚙️ АДМИНИСТРИРОВАНИЕ", reply_markup=admin_menu_keyboard)
                return ADMIN_MENU
        await query.edit_message_text("❌ Оператор не найден")
        return ADMIN_MENU
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")
        return ADMIN_MENU

app = ApplicationBuilder().token(TOKEN).build()


async def edit_op_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор оператора для редактирования."""
    query = update.callback_query
    await query.answer()
    if query.data == "edit_cancel":
        await query.edit_message_text("❌ Отменено")
        await query.message.reply_text("⚙️ АДМИНИСТРИРОВАНИЕ", reply_markup=admin_menu_keyboard)
        return ADMIN_MENU
    op_id = query.data.replace("edit_op_", "")
    context.user_data["edit_op_id"] = op_id
    try:
        ops_sheet = gclient.open(SHEET_NAME).worksheet("Операторы")
        ops = ops_sheet.get_all_values()
        row = None
        for op in ops[1:]:
            if op[0] == op_id:
                row = op
                break
        if not row:
            await query.edit_message_text("❌ Оператор не найден")
            await query.message.reply_text("⚙️ АДМИНИСТРИРОВАНИЕ", reply_markup=admin_menu_keyboard)
            return ADMIN_MENU
        context.user_data["edit_op_row"] = row
        await query.edit_message_text(
            f"👤 {row[1]}\n🪟 Стол {row[2]}\n📍 {row[3]}\n\nЧто изменить?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ Имя",  callback_data="edit_field_name")],
                [InlineKeyboardButton("🪟 Стол", callback_data="edit_field_window")],
                [InlineKeyboardButton("📍 Роль", callback_data="edit_field_role")],
                [InlineKeyboardButton("❌ Отмена", callback_data="edit_cancel_field")],
            ])
        )
        return EDIT_OP_FIELD
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")
        return ADMIN_MENU

async def edit_op_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор поля для редактирования."""
    query = update.callback_query
    await query.answer()
    if query.data == "edit_cancel_field":
        await query.edit_message_text("❌ Отменено")
        await query.message.reply_text("⚙️ АДМИНИСТРИРОВАНИЕ", reply_markup=admin_menu_keyboard)
        return ADMIN_MENU
    field = query.data.replace("edit_field_", "")
    context.user_data["edit_field"] = field
    if field == "role":
        await query.edit_message_text("Выберите новую роль:", reply_markup=role_keyboard)
        return EDIT_OP_VALUE
    prompts = {"name": "Введите новое имя и фамилию:", "window": "Введите новый номер стола:"}
    await query.edit_message_text(prompts[field])
    await query.message.reply_text(
        "Введите значение:",
        reply_markup=ReplyKeyboardMarkup([["❌ Отмена"]], resize_keyboard=True)
    )
    return EDIT_OP_VALUE

async def edit_op_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение нового значения."""
    # Если роль — это callback
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        new_value = "Оператор" if query.data == "role_operator" else "Администратор"
        col = 4  # колонка Роль
    else:
        text = update.message.text.strip()
        if text == "❌ Отмена":
            return await cancel_to_admin(update, context)
        new_value = text
        field = context.user_data.get("edit_field")
        if field == "name":
            if len(new_value.split()) < 2:
                await update.message.reply_text("❌ Введите имя и фамилию")
                return EDIT_OP_VALUE
            col = 2
        elif field == "window":
            # Проверка занятости стола
            try:
                ops_sheet_check = gclient.open(SHEET_NAME).worksheet("Операторы")
                ops_check = ops_sheet_check.get_all_values()
                edit_op_id = context.user_data.get("edit_op_id")
                for op in ops_check[1:]:
                    if len(op) >= 3 and op[2] == new_value and op[0] != edit_op_id:
                        await update.message.reply_text(
                            f"❌ Стол {new_value} уже занят оператором {op[1]}\n\nВведите другой номер:",
                            reply_markup=ReplyKeyboardMarkup([["❌ Отмена"]], resize_keyboard=True)
                        )
                        return EDIT_OP_VALUE
            except Exception as e:
                print(f"[CHECK] Ошибка проверки стола: {e}")
            col = 3
        else:
            col = 2
    op_id = context.user_data.get("edit_op_id")
    try:
        ops_sheet = gclient.open(SHEET_NAME).worksheet("Операторы")
        ops = ops_sheet.get_all_values()
        for i, op in enumerate(ops[1:], 2):
            if op[0] == op_id:
                ops_sheet.update_cell(i, col, new_value)
                break
        msg = f"✅ Обновлено: {new_value}"
        if update.callback_query:
            await update.callback_query.edit_message_text(msg)
            await update.callback_query.message.reply_text("⚙️ АДМИНИСТРИРОВАНИЕ", reply_markup=admin_menu_keyboard)
        else:
            await update.message.reply_text(msg, reply_markup=admin_menu_keyboard)
    except Exception as e:
        if update.callback_query:
            await update.callback_query.message.reply_text(f"❌ Ошибка: {e}", reply_markup=admin_menu_keyboard)
        else:
            await update.message.reply_text(f"❌ Ошибка: {e}", reply_markup=admin_menu_keyboard)
    context.user_data.pop("edit_op_id", None)
    context.user_data.pop("edit_field", None)
    context.user_data.pop("edit_op_row", None)
    return ADMIN_MENU


conv_handler = ConversationHandler(
    entry_points=[
        MessageHandler(filters.Regex("^📋 Встать в очередь$"), join_queue),
        CommandHandler("admin", admin_start),
        MessageHandler(filters.Regex("^⚙️ Админ-панель$"), admin_start),
    ],
    states={
        CHOOSE_METHOD: [
            CallbackQueryHandler(choose_method, pattern="^(scan|manual)$"),
        ],
        SCAN_ID: [
            MessageHandler(filters.PHOTO, scan_id),
            CallbackQueryHandler(inline_callback, pattern="^(retry_id|manual_id)$"),
        ],
        CONFIRM_ID: [
            CallbackQueryHandler(confirm_id, pattern="^(confirm|edit)$"),
            CallbackQueryHandler(handle_id_edit, pattern="^(edit_name|edit_idnum|confirm_id_edit)$"),
        ],
        EDIT_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, save_edited_name)],
        EDIT_IDNUM: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_edited_idnum)],
        CHOOSE_BANK_METHOD: [
            CallbackQueryHandler(choose_bank_method, pattern="^(scan_bank|manual_bank)$"),
        ],
        SCAN_BANK: [
            MessageHandler(filters.PHOTO, scan_bank),
            CallbackQueryHandler(inline_callback, pattern="^(retry_bank|manual_bank_from_scan)$"),
        ],
        CONFIRM_BANK: [
            CallbackQueryHandler(confirm_bank, pattern="^(confirm|edit)$"),
            CallbackQueryHandler(handle_bank_edit, pattern="^(edit_bank|edit_account|confirm_bank_edit)$"),
        ],
        EDIT_BANK:    [MessageHandler(filters.TEXT & ~filters.COMMAND, save_edited_bank)],
        EDIT_ACCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_edited_account)],
        NAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
        PHONE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
        OPERATOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_operator)],
        IDNUM:    [MessageHandler(filters.TEXT & ~filters.COMMAND, get_idnum)],
        BANK:     [MessageHandler(filters.TEXT & ~filters.COMMAND, get_bank)],
        ACCOUNT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, get_account)],
        REVIEW:           [CallbackQueryHandler(review_action, pattern="^review_")],
        REVIEW_FIELD:     [CallbackQueryHandler(review_field,  pattern="^rf_")],
        REVIEW_NAME:      [MessageHandler(filters.TEXT & ~filters.COMMAND, review_save_name)],
        REVIEW_IDNUM:     [MessageHandler(filters.TEXT & ~filters.COMMAND, review_save_idnum)],
        REVIEW_PHONE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, review_save_phone)],
        REVIEW_OPERATOR:  [MessageHandler(filters.TEXT & ~filters.COMMAND, review_save_operator)],
        REVIEW_BANK:      [MessageHandler(filters.TEXT & ~filters.COMMAND, review_save_bank)],
        REVIEW_ACCOUNT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, review_save_account)],
        ADD_MORE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_more)],
        ADMIN_MENU:     [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_menu)],
        ADD_OP_ID:      [MessageHandler(filters.TEXT & ~filters.COMMAND, add_op_id)],
        ADD_OP_NAME:    [MessageHandler(filters.TEXT & ~filters.COMMAND, add_op_name)],
        ADD_OP_WINDOW:  [MessageHandler(filters.TEXT & ~filters.COMMAND, add_op_window)],
        ADD_OP_ROLE:    [CallbackQueryHandler(add_op_role, pattern="^role_")],
        DELETE_OP:      [CallbackQueryHandler(delete_op, pattern="^del_op_")],
        EDIT_OP_SELECT: [CallbackQueryHandler(edit_op_select, pattern="^(edit_op_|edit_cancel$)")],
        EDIT_OP_FIELD:  [CallbackQueryHandler(edit_op_field,  pattern="^(edit_field_|edit_cancel_field$)")],
        EDIT_OP_VALUE:  [
            MessageHandler(filters.TEXT & ~filters.COMMAND, edit_op_value),
            CallbackQueryHandler(edit_op_value, pattern="^role_"),
        ],
    },
    fallbacks=[CommandHandler("cancel", cancel_to_admin)],
    per_message=False,
    allow_reentry=True,
)


async def manual_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало ручной записи в очередь (для клиентов без Telegram)."""
    if not is_operator(update.effective_user.id):
        await update.message.reply_text("❌ Только для операторов")
        return ConversationHandler.END
    await update.message.reply_text(
        "📝 РУЧНАЯ ЗАПИСЬ В ОЧЕРЕДЬ\n\n"
        "Шаг 1/2: Введите ФИО клиента\n"
        "(только латиница · пример: KIM MINJUN)\n\n"
        "💡 Нажмите ❌ Отмена чтобы выйти",
        reply_markup=ReplyKeyboardMarkup([["❌ Отмена"]], resize_keyboard=True)
    )
    return MANUAL_NAME

async def manual_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принимаем ФИО."""
    text = update.message.text.strip()
    if text == "❌ Отмена":
        await update.message.reply_text("❌ Действие отменено", reply_markup=admin_keyboard)
        return ConversationHandler.END
    if len(text.split()) < 2:
        await update.message.reply_text(
            "❌ Введите минимум имя и фамилию",
            reply_markup=ReplyKeyboardMarkup([["❌ Отмена"]], resize_keyboard=True)
        )
        return MANUAL_NAME
    context.user_data["manual_name"] = text.upper()
    await update.message.reply_text(
        f"✅ ФИО: {text.upper()}\n\nШаг 2/2: Введите ID-номер (13 цифр)\nПример: 9012311234567",
        reply_markup=ReplyKeyboardMarkup([["❌ Отмена"]], resize_keyboard=True)
    )
    return MANUAL_IDNUM

async def manual_idnum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принимаем ID и сохраняем в очередь."""
    text = update.message.text.strip()
    if text == "❌ Отмена":
        context.user_data.pop("manual_name", None)
        await update.message.reply_text("❌ Действие отменено", reply_markup=admin_keyboard)
        return ConversationHandler.END
    digits = re.sub(r"[^0-9]", "", text)
    if len(digits) != 13:
        await update.message.reply_text(
            "❌ Неверный ID. Введите 13 цифр:",
            reply_markup=ReplyKeyboardMarkup([["❌ Отмена"]], resize_keyboard=True)
        )
        return MANUAL_IDNUM
    formatted_id = f"{digits[:6]}-{digits[6:]}"
    name = context.user_data.get("manual_name", "")
    # Сохраняем в Sheets
    rows = sheet.get_all_values()
    queue_id = get_next_id(rows)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    gid = f"M{queue_id}"  # M = manual
    sheet.append_row([
        queue_id, now, name, "—", "—", formatted_id,
        "—", "—", "ожидание", "0", gid, "", ""
    ])
    context.user_data.pop("manual_name", None)
    await update.message.reply_text(
        f"✅ ЗАПИСАН В ОЧЕРЕДЬ\n\n"
        f"📍 №{queue_id}\n"
        f"👤 {name}\n"
        f"🪪 {formatted_id}\n\n"
        f"💡 У клиента нет Telegram — позовите его сами по имени когда подойдёт очередь.",
        reply_markup=admin_keyboard
    )
    return ConversationHandler.END

async def version_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🤖 KOMPASS QueueBot\nВерсия: {VERSION}")

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("version", version_cmd))
app.add_handler(MessageHandler(filters.Regex("^📊 Проверить очередь$"), check_queue))
app.add_handler(MessageHandler(filters.Regex("^📋 Вся очередь$"), show_queue))
app.add_handler(MessageHandler(filters.Regex("^➡️ Следующий$"), next_client))

# ConversationHandler для ручной записи в очередь (без Telegram у клиента)
manual_handler = ConversationHandler(
    entry_points=[
        MessageHandler(filters.Regex("^📝 Записать вручную$"), manual_start),
    ],
    states={
        MANUAL_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_name)],
        MANUAL_IDNUM: [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_idnum)],
    },
    fallbacks=[],
    allow_reentry=True,
)
app.add_handler(manual_handler)
app.add_handler(conv_handler)


# ========= INIT =========

def init_sheets():
    """Инициализация Google Sheets при запуске."""
    try:
        doc = gclient.open(SHEET_NAME)
        try:
            doc.worksheet("Операторы")
        except:
            ops_sheet = doc.add_worksheet("Операторы", 100, 6)
            ops_sheet.append_row(["ID", "Имя", "Окно", "Роль", "Статус", "Дата"])
            print("[INIT] Лист 'Операторы' создан")
    except Exception as e:
        print(f"[INIT] Ошибка: {e}")

if __name__ == "__main__":
    init_sheets()
    print(f"Бот запущен... Версия {VERSION}")
    app.run_polling()
