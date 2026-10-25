import os
import sqlite3
import random
import json
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
GROUP_CHAT_ID = -1002405443100
TOPIC_ID = 512577
DB_NAME = "loto.db"


def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS participants (
                game_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                ticket_count INTEGER NOT NULL,
                PRIMARY KEY (game_id, username),
                FOREIGN KEY (game_id) REFERENCES games(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                ticket_no INTEGER NOT NULL,
                username TEXT NOT NULL,
                ticket_index INTEGER NOT NULL,
                grid_json TEXT NOT NULL,
                UNIQUE (game_id, ticket_no),
                UNIQUE (game_id, username, ticket_index),
                FOREIGN KEY (game_id) REFERENCES games(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_chats (
                username TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS drawn_numbers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                number INTEGER NOT NULL,
                draw_order INTEGER NOT NULL,
                drawn_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (game_id, number),
                UNIQUE (game_id, draw_order),
                FOREIGN KEY (game_id) REFERENCES games(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS draw_state (
                game_id INTEGER PRIMARY KEY,
                mode TEXT NOT NULL DEFAULT 'batch',
                active INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (game_id) REFERENCES games(id)
            )
        """)
        columns = [
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(draw_state)"
            ).fetchall()
        ]

        if "paused_until" not in columns:
            conn.execute(
                "ALTER TABLE draw_state "
                "ADD COLUMN paused_until TEXT"
            )

def generate_ticket():
    while True:
        grid = [[None for _ in range(9)] for _ in range(3)]

        # В каждой строке ровно 5 чисел
        for row in range(3):
            columns = random.sample(range(9), 5)
            for col in columns:
                grid[row][col] = 0

        counts = [
            sum(grid[row][col] == 0 for row in range(3))
            for col in range(9)
        ]

        # В каждом столбце от 1 до 3 чисел
        if all(1 <= count <= 3 for count in counts):
            break

    for col in range(9):
        if col == 0:
            available = list(range(1, 10))
        elif col == 8:
            available = list(range(80, 91))
        else:
            available = list(range(col * 10, col * 10 + 10))

        numbers = sorted(random.sample(available, counts[col]))
        rows = [
            row for row in range(3)
            if grid[row][col] == 0
        ]

        for row, number in zip(rows, numbers):
            grid[row][col] = number

    return grid

def current_game():
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT * FROM games
            WHERE status IN ('draft', 'confirmed', 'running')
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()


def get_participants(game_id):
    with sqlite3.connect(DB_NAME) as conn:
        rows = conn.execute(
            """
            SELECT username, ticket_count
            FROM participants
            WHERE game_id = ?
            ORDER BY username
            """,
            (game_id,),
        ).fetchall()
    return dict(rows)


def is_admin(update: Update) -> bool:
    user = update.effective_user
    print(
        "ПРОВЕРКА ВЕДУЩЕЙ:",
        "user_id =", user.id if user else None,
        "ADMIN_ID =", ADMIN_ID
    )
    return user is not None and user.id == ADMIN_ID


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    if (
        user
        and chat
        and chat.type == "private"
        and user.username
    ):
        username = "@" + user.username.lower()

        with sqlite3.connect(DB_NAME) as conn:
            conn.execute(
                """
                INSERT INTO user_chats (username, user_id)
                VALUES (?, ?)
                ON CONFLICT(username)
                DO UPDATE SET user_id = excluded.user_id
                """,
                (username, user.id)
            )

    await update.effective_message.reply_text(
        "Привет! 🎱 Я бот русского лото.\n\n"
        "/newgame — создать игру\n"
        "/editlist — заменить черновик\n"
        "/confirmlist — зафиксировать список\n"
        "/status — состояние игры\n"
        "/myid — узнать Telegram ID"
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    message = update.effective_message

    if user:
        await message.reply_text(
            f"Твой Telegram ID: {user.id}"
        )

    if chat:
        text = f"ID чата: {chat.id}\nТип чата: {chat.type}"

        if message and message.message_thread_id:
            text += f"\nID темы: {message.message_thread_id}"

        await message.reply_text(text)

    if user and chat and chat.type == "private":
        if user.username:
            username = "@" + user.username.lower()

            with sqlite3.connect(DB_NAME) as conn:
                row = conn.execute(
                    "SELECT user_id FROM user_chats WHERE username = ?",
                    (username,)
                ).fetchone()

            if row:
                await message.reply_text(
                    f"✅ Регистрация для личной рассылки есть.\n"
                    f"Ник: {username}\n"
                    f"ID сохранён: {row[0]}"
                )
            else:
                await message.reply_text(
                    "⚠️ Твой Telegram ID пока не найден в базе. "
                    "Отправь /start ещё раз."
                )
        else:
            await message.reply_text(
                "⚠️ У тебя не установлен Telegram username. "
                "Для рассылки по никам он необходим."
            )


async def newgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.effective_message.reply_text(
            "⛔ Эта команда доступна только ведущей."
        )
        return

    existing = current_game()
    if existing:
        await update.effective_message.reply_text(
            f"Игра №{existing['id']} уже существует со статусом "
            f"«{existing['status']}». Сначала заверши её."
        )
        return

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.execute(
            "INSERT INTO games (status) VALUES ('draft')"
        )
        game_id = cursor.lastrowid

    context.user_data["waiting_for_list"] = True
    context.user_data["game_id"] = game_id

    await update.effective_message.reply_text(
        f"🎱 Создана игра №{game_id}.\n\n"
        "Отправь список оплативших, по одному на строку:\n\n"
        "@anna 3\n"
        "@maria 2\n"
        "@elena 1\n\n"
        "Число после ника — количество билетов."
    )

async def finishgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.effective_message.reply_text(
            "⛔️ Эта команда доступна только ведущей."
        )
        return

    game = current_game()

    if not game:
        await update.effective_message.reply_text(
            "Активной игры нет."
        )
        return

    game_id = game["id"]

    with sqlite3.connect(DB_NAME) as conn:
        conn.execute(
            "UPDATE draw_state SET active = 0 WHERE game_id = ?",
            (game_id,)
        )
        conn.execute(
            "UPDATE games SET status = 'finished' WHERE id = ?",
            (game_id,)
        )

    jobs = context.job_queue.get_jobs_by_name(
        f"draw_{game_id}"
    )
    for job in jobs:
        job.schedule_removal()

    await update.effective_message.reply_text(
        f"🏁 Игра №{game_id} завершена.\n"
        "Билеты и история чисел сохранены."
    )

async def receive_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    if not context.user_data.get("waiting_for_list"):
        return

    game_id = context.user_data.get("game_id")
    message = update.effective_message
    text = message.text if message else ""

    with sqlite3.connect(DB_NAME) as conn:
        row = conn.execute(
            "SELECT status FROM games WHERE id = ?", (game_id,)
        ).fetchone()

    if not row or row[0] != "draft":
        context.user_data["waiting_for_list"] = False
        await message.reply_text("Этот список уже нельзя редактировать.")
        return

    participants = {}

    try:
        for line in text.strip().splitlines():
            parts = line.split()
            if len(parts) != 2:
                raise ValueError

            username = parts[0].lower()
            nick = username[1:] if username.startswith("@") else ""

            if not nick or not all(c.isalnum() or c == "_" for c in nick):
                raise ValueError

            if not parts[1].isdigit():
                raise ValueError

            count = int(parts[1])
            if count < 1 or username in participants:
                raise ValueError

            participants[username] = count

        if not participants:
            raise ValueError

    except ValueError:
        await message.reply_text(
            "❌ Ошибка формата.\n\n"
            "Пример:\n@anna 3\n@maria 2\n\n"
            "Ники не должны повторяться, количество билетов — "
            "целое число больше нуля."
        )
        return

    with sqlite3.connect(DB_NAME) as conn:
        conn.execute(
            "DELETE FROM participants WHERE game_id = ?", (game_id,)
        )
        conn.executemany(
            """
            INSERT INTO participants (game_id, username, ticket_count)
            VALUES (?, ?, ?)
            """,
            [
                (game_id, username, count)
                for username, count in participants.items()
            ],
        )

    context.user_data["waiting_for_list"] = False

    lines = [
        f"{username} — {count} билет(ов)"
        for username, count in participants.items()
    ]

    await message.reply_text(
        f"📋 ЧЕРНОВИК ИГРЫ №{game_id}\n\n"
        + "\n".join(lines)
        + f"\n\nУчастников: {len(participants)}"
        + f"\nВсего билетов: {sum(participants.values())}"
        + "\n\n/confirmlist — зафиксировать"
        + "\n/editlist — заменить список"
    )


async def editlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.effective_message.reply_text(
            "⛔ Эта команда доступна только ведущей."
        )
        return

    game = current_game()
    if not game or game["status"] != "draft":
        await update.effective_message.reply_text(
            "Редактировать можно только черновик."
        )
        return

    context.user_data["game_id"] = game["id"]
    context.user_data["waiting_for_list"] = True

    await update.effective_message.reply_text(
        "Отправь новый список целиком. Он заменит предыдущий."
    )


async def confirmlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.effective_message.reply_text(
            "⛔ Подтверждать список может только ведущая."
        )
        return

    game = current_game()
    if not game or game["status"] != "draft":
        await update.effective_message.reply_text(
            "Нет черновика для подтверждения."
        )
        return

    participants = get_participants(game["id"])
    if not participants:
        await update.effective_message.reply_text(
            "Список пуст. Сначала отправь список участников."
        )
        return

    with sqlite3.connect(DB_NAME) as conn:
        conn.execute(
            "UPDATE games SET status = 'confirmed' WHERE id = ?",
            (game["id"],),
        )

    context.user_data["waiting_for_list"] = False

    await update.effective_message.reply_text(
        f"🔒 Игра №{game['id']}: список зафиксирован!\n\n"
        f"Участников: {len(participants)}\n"
        f"Билетов: {sum(participants.values())}\n\n"
        "Данные сохранены в базе loto.db."
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game = current_game()
    if not game:
        await update.effective_message.reply_text("Активной игры нет.")
        return

    game_id = game["id"]
    participants = get_participants(game_id)

    with sqlite3.connect(DB_NAME) as conn:
        drawn_count = conn.execute(
            "SELECT COUNT(*) FROM drawn_numbers WHERE game_id = ?",
            (game_id,)
        ).fetchone()[0]

        state = conn.execute(
            "SELECT mode, active FROM draw_state WHERE game_id = ?",
            (game_id,)
        ).fetchone()

    if state:
        mode = "по одному числу каждые 10 секунд" if state[0] == "single" else "по 10 чисел каждые 40 секунд"
        active = "активна" if state[1] else "остановлена"
    else:
        mode = "не установлен"
        active = "не запущена"

    await update.effective_message.reply_text(
        f"Игра №{game_id}\n"
        f"Статус: {game['status']}\n"
        f"Участников: {len(participants)}\n"
        f"Билетов: {sum(participants.values())}\n"
        f"Выпало чисел: {drawn_count} из 90\n"
        f"Выдача: {active}\n"
        f"Режим: {mode}"
    )
async def generatetickets(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    print("DEBUG: функция generatetickets запущена")

    if not is_admin(update):
        await update.effective_message.reply_text(
            "⛔️ Генерировать билеты может только ведущая."
        )
        return

    game = current_game()

    if not game or game["status"] != "confirmed":
        await update.effective_message.reply_text(
            "Сначала зафиксируй список участников "
            "командой /confirmlist."
        )
        return

    game_id = game["id"]
    participants = get_participants(game_id)

    if not participants:
        await update.effective_message.reply_text(
            "Список участников пуст."
        )
        return

    with sqlite3.connect(DB_NAME) as conn:
        existing = conn.execute(
            "SELECT COUNT(*) FROM tickets WHERE game_id = ?",
            (game_id,)
        ).fetchone()[0]

        if existing:
            await update.effective_message.reply_text(
                f"Для игры №{game_id} билеты уже созданы: "
                f"{existing} шт.\nПовторная генерация запрещена."
            )
            return

        ticket_no = 1

        for username, count in participants.items():
            for ticket_index in range(1, count + 1):
                grid = generate_ticket()

                conn.execute(
                    """
                    INSERT INTO tickets (
                        game_id,
                        ticket_no,
                        username,
                        ticket_index,
                        grid_json
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        game_id,
                        ticket_no,
                        username,
                        ticket_index,
                        json.dumps(grid)
                    )
                )

                ticket_no += 1

    total = ticket_no - 1

    await update.effective_message.reply_text(
        f"🎱 БИЛЕТЫ СОЗДАНЫ!\n\n"
        f"Игра №{game_id}\n"
        f"Участников: {len(participants)}\n"
        f"Всего билетов: {total}\n\n"
        f"Билеты сохранены в loto.db.\n"
        f"Список участников не изменён."
    )

async def publishtickets(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update):
        await update.effective_message.reply_text(
            "⛔️ Публиковать билеты может только ведущая."
        )
        return

    game = current_game()
    if not game:
        await update.effective_message.reply_text(
            "Активная игра не найдена."
        )
        return

    game_id = game["id"]

    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        tickets = conn.execute(
            """
            SELECT ticket_no, username, grid_json
            FROM tickets
            WHERE game_id = ?
            ORDER BY ticket_no
            """,
            (game_id,)
        ).fetchall()

    if not tickets:
        await update.effective_message.reply_text(
            "В этой игре пока нет созданных билетов. "
            "Сначала проверь команду /generatetickets."
        )
        return

    send_options = {}
    if TOPIC_ID is not None:
        send_options["message_thread_id"] = TOPIC_ID

    await context.bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=(
            f"🎱 БИЛЕТЫ ИГРЫ №{game_id}\n"
            f"Всего билетов: {len(tickets)}"
        ),
        **send_options
    )

    sent_private = 0
    not_registered = []
    send_errors = []

    for ticket in tickets:
        grid = json.loads(ticket["grid_json"])

        rows = []
        for row in grid:
            cells = [
                f"{number:2d}" if number is not None else " ·"
                for number in row
            ]
            rows.append(" ".join(cells))

        ticket_text = (
            f"🎫 Билет №{ticket['ticket_no']}\n"
            f"Владелец: {ticket['username']}\n\n"
            + "\n".join(rows)
        )

        # Публикуем билет в общем чате
        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=f"<pre>{ticket_text}</pre>",
            parse_mode="HTML",
            **send_options
        )

        # Ищем Telegram ID владельца билета
        username = str(ticket["username"]).strip().lstrip("@").lower()

        with sqlite3.connect(DB_NAME) as conn:
            row = conn.execute(
                """
                SELECT user_id
                FROM user_chats
                WHERE lower(ltrim(username, '@')) = ?
                """,
                (username,)
            ).fetchone()

        if row is None:
            not_registered.append(ticket["username"])
            continue

        # Отправляем билет владельцу в личные сообщения
        try:
            await context.bot.send_message(
                chat_id=row[0],
                text=f"<pre>{ticket_text}</pre>",
                parse_mode="HTML"
            )
            sent_private += 1

        except Exception as error:
            print(
                f"Не удалось отправить билет "
                f"№{ticket['ticket_no']} пользователю "
                f"{ticket['username']}: {error}"
            )
            send_errors.append(ticket["username"])

    result = (
        f"✅ Билеты опубликованы в общем чате.\n"
        f"📩 Личных сообщений отправлено: {sent_private}"
    )

    if not_registered:
        result += (
            "\n\n⚠️ Не зарегистрировались через /start:\n"
            + "\n".join(sorted(set(not_registered)))
        )

    if send_errors:
        result += (
            "\n\n❗ Ошибка личной отправки:\n"
            + "\n".join(sorted(set(send_errors)))
        )

    await update.effective_message.reply_text(result)

async def publish_draw_numbers(context, game_id, quantity):
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row

        drawn = conn.execute(
            """
            SELECT number
            FROM drawn_numbers
            WHERE game_id = ?
            """,
            (game_id,)
        ).fetchall()

        already_drawn = {row["number"] for row in drawn}
        available = [
            number for number in range(1, 91)
            if number not in already_drawn
        ]

        if not available:
            conn.execute(
                "UPDATE draw_state SET active = 0 WHERE game_id = ?",
                (game_id,)
            )
            conn.execute(
                "UPDATE games SET status = 'finished' WHERE id = ?",
                (game_id,)
            )
            finished = True
            numbers = []
        else:
            numbers = random.sample(
                available,
                min(quantity, len(available))
            )

            next_order = conn.execute(
                """
                SELECT COALESCE(MAX(draw_order), 0) + 1
                FROM drawn_numbers
                WHERE game_id = ?
                """,
                (game_id,)
            ).fetchone()[0]

            for offset, number in enumerate(numbers):
                conn.execute(
                    """
                    INSERT INTO drawn_numbers
                    (game_id, number, draw_order)
                    VALUES (?, ?, ?)
                    """,
                    (game_id, number, next_order + offset)
                )

            finished = len(numbers) == len(available)

            if finished:
                conn.execute(
                    "UPDATE draw_state SET active = 0 WHERE game_id = ?",
                    (game_id,)
                )
                conn.execute(
                    "UPDATE games SET status = 'finished' WHERE id = ?",
                    (game_id,)
                )

    send_options = {}
    if TOPIC_ID is not None:
        send_options["message_thread_id"] = TOPIC_ID

    if numbers:
        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=(
                f"🎱 Числа игры №{game_id}:\n\n"
                + " · ".join(map(str, numbers))
            ),
            **send_options
        )

    if finished:
        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=f"🏁 Выдача чисел в игре №{game_id} завершена.",
            **send_options
        )


async def draw_next(context):
    game_id = context.job.data

    with sqlite3.connect(DB_NAME) as conn:
        row = conn.execute(
            """
            SELECT
                mode,
                active,
                paused_until,
                CAST(
                    (julianday(paused_until) - julianday('now'))
                    * 86400 AS INTEGER
                )
            FROM draw_state
            WHERE game_id = ?
            """,
            (game_id,)
        ).fetchone()

        drawn_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM drawn_numbers
            WHERE game_id = ?
            """,
            (game_id,)
        ).fetchone()[0]

    if not row or not row[1]:
        return

    mode, active, paused_until, remaining = row

    # Если пауза ещё не закончилась — ждём
    if paused_until and remaining is not None and remaining > 0:
        context.job_queue.run_once(
            draw_next,
            when=remaining + 1,
            data=game_id,
            name=f"draw_{game_id}"
        )
        return

    # Пауза закончилась — разрешаем продолжить игру
    if paused_until:
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute(
                """
                UPDATE draw_state
                SET paused_until = NULL
                WHERE game_id = ?
                """,
                (game_id,)
            )

    # Выбираем количество чисел для следующей партии
    if mode == "single":
        quantity = 1
    elif drawn_count < 30:
        quantity = 10
    else:
        quantity = 5

    await publish_draw_numbers(context, game_id, quantity)

    with sqlite3.connect(DB_NAME) as conn:
        row = conn.execute(
            """
            SELECT mode, active, paused_until
            FROM draw_state
            WHERE game_id = ?
            """,
            (game_id,)
        ).fetchone()

    if not row or not row[1] or row[2]:
        return

    mode = row[0]

    # Интервал между партиями
    delay = 20 if mode == "single" else 60

    jobs = context.job_queue.get_jobs_by_name(
        f"draw_{game_id}"
    )

    for job in jobs:
        job.schedule_removal()

    context.job_queue.run_once(
        draw_next,
        when=delay,
        data=game_id,
        name=f"draw_{game_id}"
    )


async def startdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.effective_message.reply_text(
            "⛔️ Запустить розыгрыш может только ведущая."
        )
        return

    game = current_game()

    if not game or game["status"] != "confirmed":
        await update.effective_message.reply_text(
            "Сначала нужно зафиксировать список участников."
        )
        return

    game_id = game["id"]

    with sqlite3.connect(DB_NAME) as conn:
        ticket_count = conn.execute(
            "SELECT COUNT(*) FROM tickets WHERE game_id = ?",
            (game_id,)
        ).fetchone()[0]

        drawn_count = conn.execute(
            "SELECT COUNT(*) FROM drawn_numbers WHERE game_id = ?",
            (game_id,)
        ).fetchone()[0]

        state = conn.execute(
            "SELECT active FROM draw_state WHERE game_id = ?",
            (game_id,)
        ).fetchone()

        if state and state[0]:
            await update.effective_message.reply_text(
                "Розыгрыш уже запущен."
            )
            return

        if drawn_count:
            await update.effective_message.reply_text(
                "В этой игре уже есть выпавшие числа. "
                "Повторный запуск заблокирован."
            )
            return

        if not ticket_count:
            await update.effective_message.reply_text(
                "Сначала создай билеты и опубликуй их."
            )
            return

        conn.execute(
            """
            INSERT INTO draw_state (game_id, mode, active)
            VALUES (?, 'batch', 1)
            ON CONFLICT(game_id)
            DO UPDATE SET mode = 'batch', active = 1
            """,
            (game_id,)
        )
        conn.execute(
            "UPDATE games SET status = 'running' WHERE id = ?",
            (game_id,)
        )

    await update.effective_message.reply_text(
        "🎱 Розыгрыш начался! Первые 10 чисел отправляю в общий чат."
    )

    await publish_draw_numbers(context, game_id, 10)

    with sqlite3.connect(DB_NAME) as conn:
        state = conn.execute(
            "SELECT active FROM draw_state WHERE game_id = ?",
            (game_id,)
        ).fetchone()

    if state and state[0]:
        context.job_queue.run_once(
            draw_next,
            when=40,
            data=game_id,
            name=f"draw_{game_id}"
        )

async def handle_game_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    message = update.effective_message
    chat = update.effective_chat

    if not message or not message.text or not chat:
        return

    if chat.id != GROUP_CHAT_ID:
        print(
            f"ИГРА: сообщение из чужого чата "
            f"{chat.id}, ожидался {GROUP_CHAT_ID}"
        )
        return

    print(
        f"ИГРА: получено сообщение "
        f"{message.text!r} в чате {chat.id}"
    )

    if message.text.strip().lower() == "бинго":
        await check_bingo(update, context)
        return

    if message.text.strip() != "3":
        return

    game = current_game()

    if not game or game["status"] != "running":
        return

    game_id = game["id"]

    with sqlite3.connect(DB_NAME) as conn:
        state = conn.execute(
            """
            SELECT mode, active, paused_until
            FROM draw_state
            WHERE game_id = ?
            """,
            (game_id,)
        ).fetchone()

        if (
            not state
            or not state[1]
            or state[2]
            or state[0] == "single"
        ):
            return

        conn.execute(
            """
            UPDATE draw_state
            SET mode = 'single'
            WHERE game_id = ?
            """,
            (game_id,)
        )

    jobs = context.job_queue.get_jobs_by_name(
        f"draw_{game_id}"
    )

    for job in jobs:
        job.schedule_removal()

    context.job_queue.run_once(
        draw_next,
        when=20,
        data=game_id,
        name=f"draw_{game_id}"
    )
    await message.reply_text(
        "🔔 Игроку осталось 3 числа! "
        "Переходим на выдачу по одному числу "
        "каждые 20 секунд для всех участников."
    )

async def check_bingo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    message = update.effective_message
    user = update.effective_user

    if not message or not user or not user.username:
        return

    if message.text.strip().lower() != "бинго":
        return

    username = "@" + user.username.lower()
    game = current_game()

    if not game or game["status"] != "running":
        await message.reply_text("Сейчас активной игры нет.")
        return

    game_id = game["id"]

    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row

        drawn_rows = conn.execute(
            """
            SELECT number
            FROM drawn_numbers
            WHERE game_id = ?
            """,
            (game_id,)
        ).fetchall()

        drawn_numbers = {row["number"] for row in drawn_rows}

        tickets = conn.execute(
            """
            SELECT ticket_no, grid_json
            FROM tickets
            WHERE game_id = ? AND lower(username) = ?
            ORDER BY ticket_no
            """,
            (game_id, username)
        ).fetchall()

    if not tickets:
        await message.reply_text(
            f"Не нашла билеты для {username}."
        )
        return

    winning_ticket = None

    for ticket in tickets:
        grid = json.loads(ticket["grid_json"])

        ticket_numbers = {
            number
            for row in grid
            for number in row
            if number is not None
        }

        if ticket_numbers and ticket_numbers.issubset(drawn_numbers):
            winning_ticket = ticket["ticket_no"]
            break

    if winning_ticket is not None:
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute(
                "UPDATE draw_state SET active = 0 WHERE game_id = ?",
                (game_id,)
            )
            conn.execute(
                "UPDATE games SET status = 'finished' WHERE id = ?",
                (game_id,)
            )

        jobs = context.job_queue.get_jobs_by_name(
            f"draw_{game_id}"
        )
        for job in jobs:
            job.schedule_removal()

        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=(
                f"🏆 БИНГО!\n"
                f"Победитель: {username}\n"
                f"Выигрышный билет: №{winning_ticket}\n"
                f"Игра №{game_id} остановлена."
            ),
            message_thread_id=TOPIC_ID
            if TOPIC_ID is not None else None
        )
    else:
        await message.reply_text(
            "🔎 Проверила твои билеты. "
            "Пока нет билета, в котором выпали все числа."
        )

async def checknumbers(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update):
        await update.effective_message.reply_text(
            "⛔️ Проверять выпавшие числа может только ведущая."
        )
        return

    game = current_game()

    if not game or game["status"] != "running":
        await update.effective_message.reply_text(
            "Сейчас нет активной игры."
        )
        return

    game_id = game["id"]

    with sqlite3.connect(DB_NAME) as conn:
        state = conn.execute(
            """
            SELECT active, paused_until
            FROM draw_state
            WHERE game_id = ?
            """,
            (game_id,)
        ).fetchone()

        if not state or not state[0]:
            await update.effective_message.reply_text(
                "Выдача чисел уже остановлена."
            )
            return

        if state[1]:
            await update.effective_message.reply_text(
                "Пауза уже установлена."
            )
            return

        conn.execute(
            """
            UPDATE draw_state
            SET paused_until = datetime('now', '+3 minutes')
            WHERE game_id = ?
            """,
            (game_id,)
        )

        numbers = conn.execute(
            """
            SELECT number
            FROM drawn_numbers
            WHERE game_id = ?
            ORDER BY draw_order
            """,
            (game_id,)
        ).fetchall()

    jobs = context.job_queue.get_jobs_by_name(
        f"draw_{game_id}"
    )

    for job in jobs:
        job.schedule_removal()

    if numbers:
        numbers_text = ", ".join(
            str(row[0]) for row in numbers
        )
    else:
        numbers_text = "Пока не выпало ни одного числа."

    await update.effective_message.reply_text(
        "⏸️ ИГРА НА ПАУЗЕ НА 3 МИНУТЫ\n\n"
        "🎱 Все выпавшие числа по порядку:\n"
        f"{numbers_text}\n\n"
        "Через 3 минуты выдача продолжится."
    )

    context.job_queue.run_once(
        draw_next,
        when=180,
        data=game_id,
        name=f"draw_{game_id}"
    )
async def remainingnumbers(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update):
        await update.effective_message.reply_text(
            "⛔️ Просматривать невыпавшие числа может только ведущая."
        )
        return

    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row

        game = conn.execute(
            """
            SELECT *
            FROM games
            WHERE status = 'finished'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

        if not game:
            await update.effective_message.reply_text(
                "Не нашла завершённую игру."
            )
            return

        game_id = game["id"]

        rows = conn.execute(
            """
            SELECT number
            FROM drawn_numbers
            WHERE game_id = ?
            """,
            (game_id,)
        ).fetchall()

    drawn = {row["number"] for row in rows}
    remaining = sorted(set(range(1, 91)) - drawn)

    if not remaining:
        await update.effective_message.reply_text(
            "🎉 Все числа от 1 до 90 уже выпали!"
        )
        return

    numbers_text = ", ".join(map(str, remaining))

    await update.effective_message.reply_text(
        f"🎱 НЕВЫПАВШИЕ ЧИСЛА\n"
        f"Игра №{game_id}\n"
        f"Всего: {len(remaining)}\n\n"
        f"{numbers_text}"
    )

async def restore_draw_jobs(app):
    with sqlite3.connect(DB_NAME) as conn:
        rows = conn.execute(
            """
            SELECT
                ds.game_id,
                ds.mode,
                ds.paused_until,
                CAST(
                    (julianday(ds.paused_until) - julianday('now'))
                    * 86400 AS INTEGER
                )
            FROM draw_state AS ds
            JOIN games AS g ON g.id = ds.game_id
            WHERE ds.active = 1
              AND g.status = 'running'
            """
        ).fetchall()

    for game_id, mode, paused_until, remaining in rows:
        if paused_until:
            delay = max(1, remaining + 1) if remaining is not None else 1
        else:
            delay = 10 if mode == "single" else 40

        app.job_queue.run_once(
            draw_next,
            when=delay,
            data=game_id,
            name=f"draw_{game_id}"
        )

def main():
    if not TOKEN:
        raise RuntimeError("Не найден BOT_TOKEN в .env")
    if ADMIN_ID == 0:
        raise RuntimeError("Не найден ADMIN_ID в .env")

    init_db()

    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(restore_draw_jobs)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .get_updates_connect_timeout(30)
        .get_updates_read_timeout(30)
        .get_updates_write_timeout(30)
        .get_updates_pool_timeout(30)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("newgame", newgame))
    app.add_handler(CommandHandler("editlist", editlist))
    app.add_handler(CommandHandler("confirmlist", confirmlist))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("startdraw", startdraw))
    app.add_handler(CommandHandler("finishgame", finishgame))
    app.add_handler(
        CommandHandler("checknumbers", checknumbers)
    )
    app.add_handler(
        CommandHandler("remainingnumbers", remainingnumbers)
    )
    app.add_handler(CommandHandler("publishtickets", publishtickets))
    app.add_handler(CommandHandler("generatetickets", generatetickets))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            receive_list
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_game_message
        ),
        group=1
    )

    async def debug_messages(update, context):
        print("DEBUG: получено обновление")
        message = update.effective_message
        chat = update.effective_chat

        if message and chat:
            print(
                f"DEBUG: chat_id={chat.id}, "
                f"text={message.text!r}, "
                f"sender_chat={message.sender_chat.id if message.sender_chat else None}"
            )

    app.add_handler(
        MessageHandler(filters.ALL, debug_messages),
        group=99
    )

    print("База данных подключена. Запускаю Telegram-бота...")
    print("DEBUG: polling запускается")
    print("DEBUG: GROUP_CHAT_ID =", GROUP_CHAT_ID)
    print("DEBUG: TOKEN загружен =", bool(TOKEN))

    app.run_polling(
        drop_pending_updates=False,
        allowed_updates=Update.ALL_TYPES
    )

if __name__ == "__main__":
    main()