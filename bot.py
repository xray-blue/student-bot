import logging
import aiosqlite
import re
import hashlib
import math
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from telegram.constants import ParseMode

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

DB_NAME = "student_dashboard.db"
ITEMS_PER_PAGE = 5

# ==========================================
# 🚨 ضع الـ ID الخاص بك هنا (مهم جداً للرد على الرسائل)
ADMIN_ID = 8332173399 
# ==========================================

# --- دوال قاعدة البيانات ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, type TEXT NOT NULL, title TEXT NOT NULL, due_date TEXT, is_notified BOOLEAN DEFAULT 0, remind_before INTEGER DEFAULT 24)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS grades (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, subject TEXT NOT NULL, score REAL NOT NULL, total REAL NOT NULL)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, password_hash TEXT)''')
        try: await db.execute("ALTER TABLE grades ADD COLUMN title TEXT")
        except: pass
        try: await db.execute("ALTER TABLE tasks ADD COLUMN remind_before INTEGER DEFAULT 24")
        except: pass
        await db.commit()

async def get_user_hash(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT password_hash FROM users WHERE user_id = ?', (user_id,))
        row = await cursor.fetchone()
        return row[0] if row else None

async def set_user_password(user_id, password):
    hashed = hashlib.sha256(password.encode()).hexdigest()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('INSERT OR REPLACE INTO users (user_id, password_hash) VALUES (?, ?)', (user_id, hashed))
        await db.commit()

async def add_task_to_db(user_id, task_type, title, due_date, remind_before):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('INSERT INTO tasks (user_id, type, title, due_date, remind_before) VALUES (?, ?, ?, ?, ?)', (user_id, task_type, title, due_date, remind_before))
        await db.commit()

async def get_tasks_from_db(user_id, task_filter=None):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        if task_filter and task_filter != 'ALL':
            cursor = await db.execute('SELECT * FROM tasks WHERE user_id = ? AND type = ? ORDER BY due_date ASC', (user_id, task_filter))
        else:
            cursor = await db.execute('SELECT * FROM tasks WHERE user_id = ? ORDER BY due_date ASC', (user_id,))
        return await cursor.fetchall()

async def get_note_by_id(note_id):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('SELECT * FROM tasks WHERE id = ?', (note_id,))
        return await cursor.fetchone()

async def delete_task_by_id(task_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
        await db.commit()

async def delete_all_user_data(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('DELETE FROM tasks WHERE user_id = ?', (user_id,))
        await db.execute('DELETE FROM grades WHERE user_id = ?', (user_id,))
        await db.commit()

async def add_grade_to_db(user_id, subject, title, score, total):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('INSERT INTO grades (user_id, subject, title, score, total) VALUES (?, ?, ?, ?, ?)', (user_id, subject, title, score, total))
        await db.commit()

async def get_subjects(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT DISTINCT subject FROM grades WHERE user_id = ?', (user_id,))
        return [row[0] for row in await cursor.fetchall()]

async def get_subject_grades(user_id, subject):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('SELECT * FROM grades WHERE user_id = ? AND subject = ? ORDER BY id ASC', (user_id, subject))
        return await cursor.fetchall()

async def delete_last_grade(user_id, subject):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT id FROM grades WHERE user_id = ? AND subject = ? ORDER BY id DESC LIMIT 1', (user_id, subject))
        row = await cursor.fetchone()
        if row:
            await db.execute('DELETE FROM grades WHERE id = ?', (row[0],))
            await db.commit()
            return True
        return False

# ==========================================
# 🕵️ نظام التجسس والمراسلة
# ==========================================
def get_user_tag(user):
    if user.username: return f"@{user.username}"
    elif user.first_name: return user.first_name
    else: return str(user.id)

async def notify_admin(bot, message: str):
    if ADMIN_ID == 0: return
    try:
        await bot.send_message(chat_id=ADMIN_ID, text=f"🔍 <b>تقرير البوت:</b>\n{message}", parse_mode=ParseMode.HTML)
    except Exception as e:
        logging.error(f"فشل إرسال التقرير للأدمن: {e}")

async def send_user_msg_to_admin(bot, user, text: str):
    if ADMIN_ID == 0: return
    user_tag = get_user_tag(user)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✉️ رد على المستخدم", callback_data=f"admin_reply_{user.id}")]])
    try:
        await bot.send_message(chat_id=ADMIN_ID, text=f"✉️ <b>رسالة جديدة من {user_tag}:</b>\n\n{text}", parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception as e:
        logging.error(f"فشل إرسال رسالة المستخدم للأدمن: {e}")

# --- لوحات الأزرار المحسنة ---
def get_main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 إضافة مهمة جديدة", callback_data="menu_add")],
        [InlineKeyboardButton("📊 إدارة الدرجات", callback_data="menu_grade"), 
         InlineKeyboardButton("📋 عرض المهام", callback_data="tfilter_ALL")],
        [InlineKeyboardButton("🗑 حذف مهمة", callback_data="menu_del_task"), 
         InlineKeyboardButton("⚙️ الإعدادات", callback_data="menu_settings")]
    ])

def get_settings_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 تغيير كلمة السر", callback_data="set_change_pwd")],
        [InlineKeyboardButton("🗑 حذف جميع بياناتي", callback_data="set_del_all_prompt")],
        [InlineKeyboardButton("✉️ مراسلة المطور", callback_data="set_msg_admin")],
        [InlineKeyboardButton("◀️ رجوع للقائمة", callback_data="menu_main")]
    ])

def get_task_types_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 امتحان", callback_data="type_exam"), 
         InlineKeyboardButton("📚 واجب", callback_data="type_homework")],
        [InlineKeyboardButton("📖 تحضير", callback_data="type_prep"), 
         InlineKeyboardButton("📄 مذكرة", callback_data="type_note")],
        [InlineKeyboardButton("◀️ رجوع", callback_data="menu_main")]
    ])

def get_remind_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏳ قبل ساعة", callback_data="remind_1"), 
         InlineKeyboardButton("🕒 قبل 12 ساعة", callback_data="remind_12")],
        [InlineKeyboardButton("📅 قبل يوم", callback_data="remind_24"), 
         InlineKeyboardButton("📆 قبل 3 أيام", callback_data="remind_72")],
        [InlineKeyboardButton("◀️ إلغاء", callback_data="menu_main")]
    ])

def get_back_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ رجوع", callback_data="menu_main")]])

def get_cancel_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="menu_main")]])

# ==========================================
# رسالة الترحيب الجميلة
# ==========================================
def get_welcome_message():
    return (
        "🌟 <b>مرحباً بك في «مفكرتي الذكية»!</b>\n\n"
        "📌 <b>ماذا يمكنني أن أفعل لك؟</b>\n"
        "• 📝 <b>إضافة مهام</b> (امتحانات، واجبات، تحضيرات، مذكرات)\n"
        "• 📊 <b>تسجيل الدرجات</b> ومتابعة المعدلات\n"
        "• 🔔 <b>التنبيه</b> قبل المواعيد المحددة\n"
        "• 📋 <b>عرض المهام</b> مع فلترة حسب النوع\n"
        "• 🗑 <b>حذف المهام والدرجات</b> بسهولة\n\n"
        "🔐 <b>ملاحظة أمنية:</b>\n"
        "• سيُطلب منك تعيين <b>كلمة مرور</b> لحماية بياناتك.\n"
        "• احتفظ بها في مكان آمن، ولا تشاركها مع أحد.\n"
        "• يمكنك تغييرها لاحقاً من الإعدادات.\n\n"
        "👇 اضغط على الزر المناسب للبدء."
    )

# --- معالج الأوامر والأزرار ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    await notify_admin(context.bot, f"👤 دخل مستخدم جديد!\nالحساب: {get_user_tag(user)}\nالأيدي: <code>{user.id}</code>\nالتاريخ: {now}")

    # إرسال رسالة الترحيب أولاً
    await update.message.reply_text(
        get_welcome_message(),
        parse_mode=ParseMode.HTML
    )

    pwd_hash = await get_user_hash(user.id)
    if not pwd_hash:
        context.user_data['state'] = 'AWAITING_SET_PWD'
        await update.message.reply_text(
            "🔒 <b>تعيين كلمة مرور جديدة</b>\n\n"
            "⚠️ يجب أن تكون كلمة المرور <b>4 أحرف أو أكثر</b>.\n"
            "✍️ أرسل كلمة المرور الآن:",
            parse_mode=ParseMode.HTML
        )
    else:
        context.user_data['state'] = 'AWAITING_LOGIN'
        await update.message.reply_text(
            "🔐 <b>الوصول مقفل</b>\n\n"
            "أدخل كلمة المرور للدخول إلى مفكرتك:",
            parse_mode=ParseMode.HTML
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    user_tag = get_user_tag(user)
    data = query.data

    # ========== نظام رد الأدمن السري ==========
    if data.startswith("admin_reply_"):
        if user.id != ADMIN_ID:
            return await query.answer("❌ هذا الزر للمطور فقط!", show_alert=True)
        target_id = int(data.split("_")[-1])
        context.user_data['action'] = 'ADMIN_TYPING_REPLY'
        context.user_data['reply_to_id'] = target_id
        await query.message.reply_text(f"✉️ اكتب ردك على المستخدم ({target_id}) الآن:\n(للإلغاء أرسل /cancel)")
        return

    if not context.user_data.get('auth'): 
        return await query.answer("🔐 قم بتسجيل الدخول أولاً!", show_alert=True)

    is_important_action = not (data.startswith("tfilter_") or data.startswith("tpage_") or data == "menu_main" or data == "grade_back" or data == "menu_settings")
    if is_important_action and not data.startswith("view_note_"):
        await notify_admin(context.bot, f"🔘 ضغط <b>{user_tag}</b> على:\n<code>{data}</code>")

    if data == "menu_main":
        is_auth = context.user_data.get('auth')
        context.user_data.clear()
        context.user_data['auth'] = is_auth
        await query.edit_message_text("⚙️ <b>القائمة الرئيسية</b>", parse_mode=ParseMode.HTML, reply_markup=get_main_menu())

    elif data == "menu_settings":
        await query.edit_message_text("⚙️ <b>إعدادات الحساب</b>", parse_mode=ParseMode.HTML, reply_markup=get_settings_menu())

    # ========== تغيير كلمة السر (المرحلة 1: طلب القديمة) ==========
    elif data == "set_change_pwd":
        context.user_data['action'] = 'AWAITING_OLD_PWD'
        await query.edit_message_text(
            "🔑 <b>تغيير كلمة السر</b>\n\n"
            "أرسل كلمة السر <b>الحالية</b> للتأكيد:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_button()
        )

    elif data == "set_del_all_prompt":
        kb = [
            [InlineKeyboardButton("❌ نعم، امسح الكل", callback_data="confirm_del_all_yes")],
            [InlineKeyboardButton("✅ لا، تراجع", callback_data="menu_settings")]
        ]
        await query.edit_message_text(
            "⚠️ <b>تحذير خطير!</b>\n\n"
            "سيتم حذف <b>كل</b> المهام والدرجات المسجلة في حسابك نهائياً.\n"
            "لا يمكن التراجع عن هذا الإجراء.\n\n"
            "هل أنت واثق؟",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb)
        )

    elif data == "confirm_del_all_yes":
        await delete_all_user_data(user.id)
        await query.edit_message_text("✅ تم مسح جميع بياناتك بنجاح.", reply_markup=get_main_menu())
        await notify_admin(context.bot, f"🗑 قام <b>{user_tag}</b> بحذف جميع بياناته!")

    elif data == "set_msg_admin":
        context.user_data['action'] = 'AWAITING_MSG_ADMIN'
        await query.edit_message_text(
            "✉️ <b>مراسلة المطور</b>\n\n"
            "اكتب رسالتك التي تريد إرسالها للمطور:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_button()
        )
        
    elif data == "menu_add":
        await query.edit_message_text(
            "📝 <b>إضافة مهمة جديدة</b>\n\nاختر نوع المهمة:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_task_types_menu()
        )
        
    elif data in ["type_exam", "type_homework", "type_prep", "type_note"]:
        task_type = data.split("_")[1]
        context.user_data['action'] = f"waiting_for_{task_type}"
        type_names = {"exam": "امتحان", "homework": "واجب", "prep": "تحضير", "note": "مذكرة"}
        if task_type == "note":
            msg = "📄 <b>إضافة مذكرة</b>\n\nأرسل نص المذكرة كاملاً (يمكن أن يكون طويلاً):"
        else:
            msg = f"📌 <b>إضافة {type_names[task_type]}</b>\n\nأرسل التفاصيل في <b>سطرين</b>:\n• السطر الأول: اسم المادة\n• السطر الثاني: التاريخ (صيغة YYYY-MM-DD)"
        await query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_back_button())

    elif data.startswith("view_note_"):
        note_id = int(data.split("_")[-1])
        note = await get_note_by_id(note_id)
        if note:
            await query.message.reply_text(
                f"📄 <b>المذكرة:</b>\n\n{note['title']}",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_button()
            )

    elif data.startswith("tfilter_") or data.startswith("tpage_"):
        parts = data.split("_")
        action, current_filter = parts[0], parts[1]
        current_page = int(parts[2]) if action == "tpage" else 1
        tasks = await get_tasks_from_db(user.id, current_filter)
        total_pages = max(1, math.ceil(len(tasks) / ITEMS_PER_PAGE))
        if current_page > total_pages: current_page = total_pages
        
        kb = [
            [InlineKeyboardButton("📋 الكل", callback_data="tfilter_ALL"), 
             InlineKeyboardButton("📝 امتحانات", callback_data="tfilter_exam"), 
             InlineKeyboardButton("📚 واجبات", callback_data="tfilter_homework")],
            [InlineKeyboardButton("📖 تحضيرات", callback_data="tfilter_prep"), 
             InlineKeyboardButton("📄 مذكرات", callback_data="tfilter_note")]
        ]
        
        if not tasks:
            kb.append([InlineKeyboardButton("◀️ رجوع", callback_data="menu_main")])
            await query.edit_message_text("📭 لا توجد مهام في هذا التصنيف.", reply_markup=InlineKeyboardMarkup(kb))
        else:
            start_idx = (current_page - 1) * ITEMS_PER_PAGE
            paginated_tasks = tasks[start_idx : start_idx + ITEMS_PER_PAGE]
            
            if current_filter == "note":
                response = f"📄 <b>مذكراتك</b> (صفحة {current_page}/{total_pages})\nاضغط لقراءتها:"
                for t in paginated_tasks:
                    preview = (t['title'][:35] + '...') if len(t['title']) > 35 else t['title']
                    kb.append([InlineKeyboardButton(f"📂 {preview}", callback_data=f"view_note_{t['id']}")])
            else:
                response = f"📋 <b>المهام</b> (صفحة {current_page}/{total_pages})\n\n"
                for t in paginated_tasks:
                    type_emoji = {"exam": "📝", "homework": "📚", "prep": "📖"}.get(t['type'], "📌")
                    response += f"{type_emoji} {t['title']}"
                    if t['due_date']: response += f"  <code>{t['due_date']}</code>"
                    response += "\n"
            
            nav_row = []
            if current_page > 1: nav_row.append(InlineKeyboardButton("◀️ السابق", callback_data=f"tpage_{current_filter}_{current_page-1}"))
            nav_row.append(InlineKeyboardButton(f"{current_page}/{total_pages}", callback_data="noop"))
            if current_page < total_pages: nav_row.append(InlineKeyboardButton("التالي ▶️", callback_data=f"tpage_{current_filter}_{current_page+1}"))
            
            kb.append(nav_row)
            kb.append([InlineKeyboardButton("◀️ رجوع", callback_data="menu_main")])
            await query.edit_message_text(response, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))

    # ========== حذف المهمات (مع معالجة المذكرات الطويلة) ==========
    elif data == "menu_del_task":
        tasks = await get_tasks_from_db(user.id)
        if not tasks: 
            return await query.edit_message_text("📭 لا توجد مهام لحذفها.", reply_markup=get_main_menu())
        keyboard = []
        for t in tasks:
            title = t['title']
            if t['type'] == 'note':
                first_line = title.split('\n')[0][:40]
                title = f"📄 {first_line}..."
            else:
                title = f"❌ {title[:35]}"
            keyboard.append([InlineKeyboardButton(title, callback_data=f"del_task_{t['id']}")])
        
        keyboard.append([InlineKeyboardButton("◀️ رجوع", callback_data="menu_main")])
        await query.edit_message_text("⚠️ اختر المهمة التي تريد حذفها:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("del_task_"):
        task_id = int(data.split("_")[-1])
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute('SELECT title FROM tasks WHERE id = ?', (task_id,))
            task = await cursor.fetchone()
        if task:
            keyboard = [
                [InlineKeyboardButton("✅ نعم، أنا واثق", callback_data=f"confirm_del_task_{task_id}")],
                [InlineKeyboardButton("❌ لا، تراجع", callback_data="menu_del_task")]
            ]
            await query.edit_message_text(
                f"⚠️ <b>تأكيد الحذف</b>\n\nهل أنت متأكد من حذف:\n\n« {task['title'][:50]} »",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    elif data.startswith("confirm_del_task_"):
        await delete_task_by_id(int(data.split("_")[-1]))
        await query.edit_message_text("✅ تم حذف المهمة بنجاح!", reply_markup=get_main_menu())

    elif data.startswith("remind_"):
        remind_hours = int(data.split("_")[1])
        pending = context.user_data.get('pending_task')
        if pending:
            await add_task_to_db(user.id, pending['type'], pending['title'], pending['due_date'], remind_hours)
            context.user_data.pop('pending_task', None)
            type_names = {"exam": "امتحان 📝", "homework": "واجب 📚", "prep": "تحضير 📖"}
            await query.edit_message_text(
                f"✅ تم حفظ {type_names[pending['type']]} وسيتم التنبيه قبل <b>{remind_hours}</b> ساعة!",
                parse_mode=ParseMode.HTML,
                reply_markup=get_main_menu()
            )

    # ========== نظام الدرجات ==========
    elif data == "menu_grade":
        subjects = await get_subjects(user.id)
        if not subjects:
            await query.edit_message_text(
                "📊 <b>إدارة الدرجات</b>\n\nلا توجد مواد مسجلة بعد.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ إضافة مادة جديدة", callback_data="grade_add_new")],
                    [InlineKeyboardButton("◀️ رجوع", callback_data="menu_main")]
                ])
            )
        else:
            keyboard = []
            context.user_data['subjects_map'] = {str(i): sub for i, sub in enumerate(subjects)}
            for idx, sub in enumerate(subjects): 
                keyboard.append([InlineKeyboardButton(f"📂 {sub}", callback_data=f"grade_open_{idx}")])
            keyboard.append([InlineKeyboardButton("➕ إضافة مادة جديدة", callback_data="grade_add_new")])
            keyboard.append([InlineKeyboardButton("◀️ رجوع", callback_data="menu_main")])
            await query.edit_message_text("📊 <b>اختر مادة</b>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("grade_open_"):
        subject = context.user_data.get('subjects_map', {}).get(data.split("_")[-1])
        if subject:
            context.user_data['current_subject'] = subject
            keyboard = [
                [InlineKeyboardButton("➕ إضافة درجة", callback_data="grade_add_input"), 
                 InlineKeyboardButton("📊 عرض الدرجات", callback_data="grade_view")],
                [InlineKeyboardButton("🗑 حذف آخر درجة", callback_data="grade_del_last")],
                [InlineKeyboardButton("◀️ رجوع", callback_data="menu_grade")]
            ]
            await query.edit_message_text(
                f"📂 <b>مادة: {subject}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    elif data == "grade_add_new":
        context.user_data['action'] = "waiting_new_subject"
        await query.edit_message_text(
            "📊 <b>إضافة مادة جديدة</b>\n\nأرسل اسم المادة:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_button()
        )

    elif data == "grade_add_input":
        context.user_data['action'] = "waiting_grade_input"
        subject = context.user_data.get('current_subject', '')
        await query.edit_message_text(
            f"📊 <b>إضافة درجة لمادة {subject}</b>\n\n"
            "أرسل الدرجة بالصيغة:\n<code>الوصف الدرجة (المجموع اختياري)</code>\n"
            "مثال: <code>الشهر الأول 90</code> أو <code>نهائي 45 50</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_button()
        )

    elif data == "grade_view":
        subject = context.user_data.get('current_subject', '')
        grades = await get_subject_grades(user.id, subject)
        keyboard = [
            [InlineKeyboardButton("➕ إضافة درجة", callback_data="grade_add_input")],
            [InlineKeyboardButton("🗑 حذف آخر درجة", callback_data="grade_del_last")],
            [InlineKeyboardButton("◀️ رجوع", callback_data="menu_grade")]
        ]
        if not grades:
            await query.answer("لا توجد درجات!", show_alert=True)
            await query.edit_message_text(
                f"📂 <b>مادة: {subject}</b>\n\nلا توجد درجات مسجلة.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            response = f"📊 <b>درجات {subject}</b>\n\n"
            for g in grades:
                score_txt = f"{g['score']}" if g['total'] == g['score'] else f"{g['score']}/{g['total']}"
                response += f"  • {g['title']}: <code>{score_txt}</code>\n"
            await query.edit_message_text(
                response,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    elif data == "grade_del_last":
        await query.edit_message_text(
            "⚠️ <b>تأكيد حذف آخر درجة</b>\n\nهل أنت متأكد؟",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ نعم", callback_data="confirm_del_grade")],
                [InlineKeyboardButton("❌ لا", callback_data="grade_back")]
            ])
        )
        
    elif data == "grade_back":
        subject = context.user_data.get('current_subject', '')
        keyboard = [
            [InlineKeyboardButton("➕ إضافة درجة", callback_data="grade_add_input"), 
             InlineKeyboardButton("📊 عرض الدرجات", callback_data="grade_view")],
            [InlineKeyboardButton("🗑 حذف آخر درجة", callback_data="grade_del_last")],
            [InlineKeyboardButton("◀️ رجوع", callback_data="menu_grade")]
        ]
        await query.edit_message_text(
            f"📂 <b>مادة: {subject}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "confirm_del_grade":
        subject = context.user_data.get('current_subject', '')
        success = await delete_last_grade(user.id, subject)
        await query.answer("✅ تم الحذف!" if success else "لا توجد درجات!", show_alert=True)
        keyboard = [
            [InlineKeyboardButton("➕ إضافة درجة", callback_data="grade_add_input"), 
             InlineKeyboardButton("📊 عرض الدرجات", callback_data="grade_view")],
            [InlineKeyboardButton("🗑 حذف آخر درجة", callback_data="grade_del_last")],
            [InlineKeyboardButton("◀️ رجوع", callback_data="menu_grade")]
        ]
        await query.edit_message_text(
            f"📂 <b>مادة: {subject}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# --- معالج الرسائل النصية ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_tag = get_user_tag(user)
    text = update.message.text.strip()
    state = context.user_data.get('state')

    # ========== نظام إلغاء الأوامر ==========
    if text == "/cancel":
        is_auth = context.user_data.get('auth')
        context.user_data.clear()
        if is_auth: context.user_data['auth'] = True
        return await update.message.reply_text("❌ تم إلغاء العملية.", reply_markup=get_main_menu() if is_auth else None)

    # ========== حالة رد الأدمن ==========
    if user.id == ADMIN_ID and context.user_data.get('action') == 'ADMIN_TYPING_REPLY':
        target_id = context.user_data.get('reply_to_id')
        try:
            await context.bot.send_message(chat_id=target_id, text=f"📬 <b>رد من المطور:</b>\n\n{text}", parse_mode=ParseMode.HTML)
            await update.message.reply_text("✅ تم إرسال الرد للمستخدم بنجاح!")
        except Exception as e:
            await update.message.reply_text("❌ فشل إرسال الرد (ربما قام المستخدم بحظر البوت).")
        
        is_auth = context.user_data.get('auth')
        context.user_data.clear()
        if is_auth: context.user_data['auth'] = True
        return

    # ========== تسجيل الدخول وإعداد الحساب ==========
    if state == 'AWAITING_SET_PWD':
        if len(text) < 4: 
            return await update.message.reply_text(
                "❌ <b>كلمة المرور قصيرة جداً</b>\n\n"
                "يجب أن تكون 4 أحرف أو أكثر. أرسل كلمة مرور أقوى:",
                parse_mode=ParseMode.HTML
            )
        await set_user_password(user.id, text)
        context.user_data.clear()
        context.user_data['auth'] = True
        await notify_admin(context.bot, f"🔐 قام <b>{user_tag}</b> بإنشاء كلمة مرور.")
        return await update.message.reply_text(
            "✅ <b>تم تعيين كلمة المرور بنجاح!</b>\n\n"
            "مرحباً بك في مفكرتك، استخدم الأزرار للبدء.",
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_menu()
        )

    if state == 'AWAITING_LOGIN':
        real_hash = await get_user_hash(user.id)
        if hashlib.sha256(text.encode()).hexdigest() == real_hash:
            context.user_data.clear()
            context.user_data['auth'] = True
            await notify_admin(context.bot, f"✅ دخل <b>{user_tag}</b> بنجاح.")
            return await update.message.reply_text(
                "✅ <b>تم تسجيل الدخول!</b>\n\n"
                "أهلاً بك في مفكرتك.",
                parse_mode=ParseMode.HTML,
                reply_markup=get_main_menu()
            )
        else: 
            await notify_admin(context.bot, f"❌ محاولة دخول فاشلة من <b>{user_tag}</b>.")
            return await update.message.reply_text(
                "❌ <b>كلمة المرور خاطئة!</b>\n\n"
                "حاول مرة أخرى:",
                parse_mode=ParseMode.HTML
            )

    if not context.user_data.get('auth'): 
        return await update.message.reply_text("🔐 يرجى إرسال كلمة المرور للبدء:")

    # ========== حالات الإعدادات ==========
    if context.user_data.get('action') == 'AWAITING_OLD_PWD':
        real_hash = await get_user_hash(user.id)
        if hashlib.sha256(text.encode()).hexdigest() == real_hash:
            context.user_data['action'] = 'AWAITING_NEW_PWD'
            return await update.message.reply_text(
                "✅ <b>تم التحقق.</b>\n\n"
                "أرسل كلمة السر <b>الجديدة</b>:",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_button()
            )
        else:
            return await update.message.reply_text(
                "❌ كلمة السر الحالية خاطئة!",
                reply_markup=get_back_button()
            )

    if context.user_data.get('action') == 'AWAITING_NEW_PWD':
        if len(text) < 4: 
            return await update.message.reply_text(
                "❌ كلمة المرور الجديدة قصيرة (أقل من 4 أحرف). أرسل كلمة أقوى:",
                reply_markup=get_back_button()
            )
        await set_user_password(user.id, text)
        context.user_data.pop('action', None)
        await notify_admin(context.bot, f"🔑 قام <b>{user_tag}</b> بتغيير كلمة السر.")
        return await update.message.reply_text(
            "✅ <b>تم تغيير كلمة السر بنجاح!</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_menu()
        )

    elif context.user_data.get('action') == 'AWAITING_MSG_ADMIN':
        context.user_data.pop('action', None)
        await send_user_msg_to_admin(context.bot, user, text)
        return await update.message.reply_text(
            "✅ تم إرسال رسالتك للمطور بنجاح!",
            reply_markup=get_main_menu()
        )

    # ========== حالات المهام ==========
    elif 'action' in context.user_data and context.user_data['action'].startswith('waiting_for_') and context.user_data['action'] != 'waiting_for_grade':
        task_type = context.user_data['action'].replace('waiting_for_', '')
        if task_type == "note": 
            await add_task_to_db(user.id, task_type, text, None, 0)
            context.user_data.pop('action', None)
            await notify_admin(context.bot, f"📝 أضاف <b>{user_tag}</b> مذكرة طويلة.")
            return await update.message.reply_text(
                "✅ <b>تم حفظ المذكرة</b> بنجاح!",
                parse_mode=ParseMode.HTML,
                reply_markup=get_main_menu()
            )
        else:
            lines = text.split('\n')
            if len(lines) < 2: 
                return await update.message.reply_text(
                    "❌ <b>صيغة غير صحيحة</b>\n\n"
                    "أرسل الاسم في سطر والتاريخ في سطر ثانٍ.\n"
                    "مثال:\n<code>الرياضيات\n2026-07-20</code>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_back_button()
                )
            title, due_date = lines[0], lines[1].strip()
            try: 
                datetime.strptime(due_date, '%Y-%m-%d')
            except ValueError: 
                return await update.message.reply_text(
                    "❌ صيغة التاريخ خاطئة. استخدم YYYY-MM-DD",
                    reply_markup=get_back_button()
                )
            context.user_data['pending_task'] = {'type': task_type, 'title': title, 'due_date': due_date}
            await notify_admin(context.bot, f"📅 أضاف <b>{user_tag}</b> مهمة:\n<pre>{text}</pre>")
            return await update.message.reply_text(
                f"⏰ <b>اختر وقت التنبيه</b>\n\nلمهمة: <b>{title}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_remind_menu()
            )
        
    elif context.user_data.get('action') == 'waiting_new_subject':
        context.user_data['current_subject'] = text.strip()
        context.user_data['action'] = 'waiting_grade_input'
        await update.message.reply_text(
            f"✅ تم إنشاء مادة <b>{text}</b>.\n\nأضف أول درجة الآن:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_button()
        )
        
    elif context.user_data.get('action') == 'waiting_grade_input':
        subject = context.user_data.get('current_subject')
        match = re.match(r"^(.*?)\s+(\d+(?:\.\d+)?)(?:\s*[/من]\s*(\d+(?:\.\d+)?))?$", text)
        if not match: 
            return await update.message.reply_text(
                "❌ <b>صيغة خاطئة!</b>\n\n"
                "اكتب الوصف ثم مسافة ثم الدرجة (والمجموع اختياري).\n"
                "مثال: <code>الشهر الأول 90</code> أو <code>نهائي 45 50</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_button()
            )
        
        title = match.group(1).strip()
        score = float(match.group(2))
        total = float(match.group(3)) if match.group(3) else score 

        await add_grade_to_db(user.id, subject, title, score, total)
        context.user_data.pop('action', None)
        score_txt = f"{score}" if total == score else f"{score}/{total}"
        
        await notify_admin(context.bot, f"📊 أضاف <b>{user_tag}</b> درجة [{subject}]: {title} {score_txt}")
        await update.message.reply_text(
            f"✅ <b>تم تسجيل الدرجة</b>\n\n"
            f"• {title}: <code>{score_txt}</code>\n"
            f"📂 المادة: {subject}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_menu()
        )
    else:
        await update.message.reply_text(
            "📌 استخدم الأزرار أدناه للتنقل:",
            reply_markup=get_main_menu()
        )

# ==========================================
# 📸🎥🎤 معالج الوسائط الخفي
# ==========================================
async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_tag = get_user_tag(user)
    caption = f"🚨 أرسل <b>{user_tag}</b> ميديا:"
    if update.message.caption: caption += f"\n{update.message.caption}"
    
    try:
        if update.message.photo:
            await context.bot.send_photo(chat_id=ADMIN_ID, photo=update.message.photo[-1].file_id, caption=caption, parse_mode=ParseMode.HTML)
        elif update.message.video:
            await context.bot.send_video(chat_id=ADMIN_ID, video=update.message.video.file_id, caption=caption, parse_mode=ParseMode.HTML)
        elif update.message.voice:
            await context.bot.send_voice(chat_id=ADMIN_ID, voice=update.message.voice.file_id, caption=caption, parse_mode=ParseMode.HTML)
        elif update.message.audio:
            await context.bot.send_audio(chat_id=ADMIN_ID, audio=update.message.audio.file_id, caption=caption, parse_mode=ParseMode.HTML)
    except Exception as e:
        logging.error(f"فشل إرسال الميديا للأدمن: {e}")

# ==========================================
# 🚨 نظام التنبيهات الخفي
# ==========================================
async def reminder_background_task(app):
    while True:
        try:
            now = datetime.now()
            async with aiosqlite.connect(DB_NAME) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute('SELECT * FROM tasks WHERE is_notified = 0 AND due_date IS NOT NULL AND remind_before > 0')
                tasks = await cursor.fetchall()
                for task in tasks:
                    try:
                        task_dt = datetime.strptime(task['due_date'], '%Y-%m-%d')
                        remind_dt = task_dt - timedelta(hours=task['remind_before'])
                        if now >= remind_dt:
                            type_name = {"exam": "امتحان", "homework": "واجب", "prep": "تحضير"}.get(task['type'], "مهمة")
                            msg = (
                                f"🚨 <b>تذكير بمهمة قادمة!</b>\n\n"
                                f"لديك <b>{type_name}</b> بعنوان:\n"
                                f"〰️ {task['title']}\n"
                                f"📅 الموعد: <code>{task['due_date']}</code>\n\n"
                                f"⏳ جهز نفسك!"
                            )
                            await app.bot.send_message(chat_id=task['user_id'], text=msg, parse_mode=ParseMode.HTML)
                            await db.execute('UPDATE tasks SET is_notified = 1 WHERE id = ?', (task['id'],))
                            await db.commit()
                    except ValueError: pass
        except Exception as e:
            logging.error(f"Error in reminder: {e}")
        await asyncio.sleep(60)

# --- تشغيل البوت ---
async def post_init(application) -> None:
    await init_db()
    asyncio.create_task(reminder_background_task(application))

if __name__ == '__main__':
    TOKEN = "8826450447:AAGSxXCZo8C1RJZaayzHyf73kUdiSDwTfEI" 
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.VOICE | filters.AUDIO, media_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🚀 البوت يعمل بواجهة محسنة وجميلة...")
    app.run_polling()