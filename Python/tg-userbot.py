from pyrogram import Client  # type: ignore
from pyrogram.errors import PeerIdInvalid  # type: ignore
from pyrogram.enums import ChatType  # Імпортуємо перерахування типів чатів
from pyrogram.raw.functions.messages import GetDialogs
from pyrogram.raw.types import InputPeerEmpty
import telegram
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from datetime import datetime
import json
import asyncio
import os
import markdown # type: ignore
import bleach # type: ignore
allowed_tags = ['b', 'i', 'u', 'code', 'pre', 'a', 'blockquote']
from google import genai

# ДЛЯ ЛОКАЛЬНОГО ЗАПУСКУ
from config import admin_id, TG_api_id, TG_api_hash, TGbot_token, AI_api_key

# ДЛЯ ЗАПУСКУ В HEROKU
# import os
# admin_id = int(os.getenv("admin_id"))
# TG_api_id = os.getenv("TG_api_id")
# TG_api_hash = os.getenv("TG_api_hash")
# TGbot_token = os.getenv("TGbot_token")
# AI_api_key = os.getenv("AI_api_key")

if not all([admin_id, TG_api_id, TG_api_hash, TGbot_token, AI_api_key]):
    raise ValueError("One or more configuration variables are missing!")

AI_default_prompt = "Summarize the main topics, ideas, and decisions in this chat history very briefly as a bullet list, without styling."
lines_crop = 10  # Кількість рядків для відображення в скороченій версії історії чату.
delay_TG = 0.5  # Затримка між запитами для запобігання перевищенню ліміту API Telegram

# Ініціалізація клієнтів
AI_client = genai.Client(api_key=AI_api_key)
userbotTG_client = Client("my_userbot", api_id=TG_api_id, api_hash=TG_api_hash)
botTG_client = Application.builder().token(TGbot_token).build()

# Допоміжна функція для генерації контенту з автоматичним переключенням на стабільні моделі
def generate_ai_content(contents, **kwargs):
    models_to_try = ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-2.0-flash"]
    if "model" in kwargs:
        requested_model = kwargs.pop("model")
        if requested_model in models_to_try:
            models_to_try.remove(requested_model)
        models_to_try.insert(0, requested_model)

    last_error = None
    for model in models_to_try:
        try:
            print(f"🤖 Trying Gemini model: {model}...")
            return AI_client.models.generate_content(
                model=model,
                contents=contents,
                **kwargs
            )
        except Exception as e:
            last_error = e
            print(f"⚠️ Error with Gemini model {model}: {e}. Retrying with fallback...")
            continue
    raise last_error

# Сховище історії діалогів
dialog_history = {}
my_chat_histoty = "No chat history available."

# команда /start
async def start(update: Update, context: CallbackContext) -> None:
    log_any_user(update)
    await update.message.reply_text('Hello World!')

# команда /ping
async def ping(update: Update, context: CallbackContext) -> None:
    log_any_user(update)
    print("update.message.from_user.id: "+str(update.message.from_user.id))
    print("admin_id: "+str(admin_id))
    # Перевіряємо, що команда відправлена адміністратором
    if update.message.from_user.id == admin_id:

        results = []
        processing_message = await update.message.reply_text("\n".join(results) + "⏳ Running diagnostics...")

        # Check Telegram bot connectivity
        try:
            await context.bot.get_me()
            results.append("✅ Telegram bot is working correctly.")
        except Exception as e:
            results.append(f"❌ Telegram bot error: {e}")

        # Check Pyrogram userbot connectivity
        try:
            await userbotTG_client.get_me()  # Убрано использование async with
            results.append("✅ Pyrogram userbot is working correctly.")
        except Exception as e:
            results.append(f"❌ Pyrogram userbot error: {e}")

        # Check AI client connectivity
        try:
            AI_client.models.list()
            results.append("✅ Gemini AI client is working correctly.")
        except Exception as e:
            results.append(f"❌ Gemini AI client error: {e}")

        # Send diagnostic results
        diagnostic_results = "\n".join(results)
        await processing_message.edit_text(
            "Bot is working! 👌<blockquote expandable>" + diagnostic_results + "</blockquote>",
            parse_mode="HTML"
        )

# Отримуємо іконку та посилання чату
def get_chat_icon_and_link(chat):
    # Визначаємо іконку в залежності від типу чату
    if chat.type == ChatType.PRIVATE:
        icon = "👤"
        if chat.username: # Якщо є юзернейм
            direct_link = f"https://t.me/{chat.username}"
        else:
            direct_link = f"tg://user?id={chat.id}"
    elif chat.type == ChatType.GROUP:
        icon = "🫂"
        direct_link = f"https://t.me/joinchat/{chat.invite_link}" if chat.invite_link else ""
    elif chat.type == ChatType.SUPERGROUP:
        icon = "👥"
        if chat.username: # Якщо є юзернейм
            direct_link = f"https://t.me/{chat.username}"
        else:
            direct_link = f"https://t.me/c/{str(chat.id)[3:]}/-1"
    elif chat.type == ChatType.CHANNEL:
        icon = "📢"
        if chat.username: # Якщо є юзернейм
            direct_link = f"https://t.me/{chat.username}"
        else:
            direct_link = f"https://t.me/c/{str(chat.id)[3:]}/-1"
    elif chat.type == ChatType.BOT:
        icon = "🤖"
        direct_link = f"https://t.me/{chat.username}" if chat.username else ""
    else:
        icon = "❓"
        direct_link = ""
    return icon, direct_link

# команда /list
async def list_chats(update: Update, context: CallbackContext) -> None:
    log_any_user(update)
    if update.message.from_user.id == admin_id:
        try:
            limit = int(context.args[0]) if len(context.args) > 0 else 5
            if limit <= 0:
                limit = 5
        except ValueError:
            limit = 5

        # Визначаємо фільтри
        filter_mapping = {
            "p": "private",
            "private": "private",
            "особисті": "private",
            "особистий": "private",
            "приватні": "private",
            "приватний": "private",
            "дірект": "private",
            "директ": "private",
            "dm": "private",
            "pm": "private",
            

            "g": "group",
            "group": "group",
            "groups": "group",
            "chat": "group",
            "chats": "group",
            "група": "group",
            "групи": "group",
            "чат": "group",
            "чати": "group",

            "c": "channel",
            "channel": "channel",
            "channels": "channel",
            "канал": "channel",
            "канали": "channel",
            "тгк": "channel"
            }
        filter_type = context.args[1].lower() if len(context.args) > 1 else None
        filter_type = filter_mapping.get(filter_type, None)  # Перетворюємо значення через словник
        
        try:
            dialogs = []
            fetched_count = 0  # Лічильник отриманих діалогів
            async for dialog in userbotTG_client.get_dialogs():
                # Фільтр за типом чату
                if filter_type:
                    if filter_type == "private" and dialog.chat.type != ChatType.PRIVATE:
                        continue
                    elif filter_type == "group" and dialog.chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
                        continue
                    elif filter_type == "channel" and dialog.chat.type != ChatType.CHANNEL:
                        continue

                # Додаємо відповідний діалог до списку
                display_name = dialog.chat.title if dialog.chat.title else (dialog.chat.first_name or '') + ' ' + (dialog.chat.last_name or '')
                icon, direct_link = get_chat_icon_and_link(dialog.chat)
                
                dialogs.append(
                    "<a href='{direct_link}'>🆔 </a><code>{chat_id}</code>\n"
                    "<a href='https://docs.pyrogram.org/api/enums/ChatType#pyrogram.enums.{chat_type}'>{icon}</a> {display_name}"
                    "{username_link}\n".format(
                        direct_link=direct_link,
                        chat_id=dialog.chat.id,
                        chat_type=dialog.chat.type,
                        icon=icon,
                        display_name=display_name,
                        username_link=f"\n🔗 @{dialog.chat.username}" if dialog.chat.username else ""
                    )
                )

                fetched_count += 1

                # Перериваємо, якщо досягли ліміту
                if len(dialogs) >= limit:
                    break

            # Формуємо результат
            if dialogs:
                result = f"Recent {limit} {filter_type + ' 'if filter_type else ''}chats:\n\n" + "\n".join(dialogs)
            else:
                result = "⚠️ No available chats"
        except Exception as e:
            result = f"⚠️ An error occurred: {e}"
            print(result)

        # Відправляємо результат назад у Telegram-чат
        await update.message.reply_text(result, parse_mode="HTML", disable_web_page_preview=True)

# команда /ai
async def ai_query(update: Update, context: CallbackContext) -> None:
    log_any_user(update)
    user_id = update.message.from_user.id  # Унікальний ідентифікатор користувача

    if update.message.from_user.id == admin_id:
        # Перевіряємо, чи є запит після команди
        if context.args:
            query = " ".join(context.args)  # Об'єднуємо аргументи в рядок
            processing_message = await update.message.reply_text("⏳ Processing answer...")

            # Ініціалізуємо історію діалогу, якщо її немає
            if user_id not in dialog_history:
                dialog_history[user_id] = []

            # Додаємо запит користувача в історію
            dialog_history[user_id].append(f"User: {query}")

            try:
                # Формуємо повний контекст для ШІ
                context_for_ai = "\n".join(dialog_history[user_id])

                # Відправляємо запит у Gemini (з автоматичним fallback)
                ai_response = generate_ai_content(
                    model="gemini-3.5-flash",
                    contents=context_for_ai,
                )

                # Перевіряємо, чи є response рядком чи об'єктом
                response = ai_response if isinstance(ai_response, str) else ai_response.text

                # Додаємо відповідь ШІ в історію
                dialog_history[user_id].append(f"AI: {response}")

                # Обмежуємо довжину історії до 20 повідомлень
                if len(dialog_history[user_id]) > 20:
                    dialog_history[user_id] = dialog_history[user_id][-20:]

                # Відправляємо відповідь користувачу
                await processing_message.edit_text(
                    f"🤖 AI Response:\n<blockquote>{bleach.clean(markdown.markdown(response), tags=allowed_tags, strip=True)}</blockquote>",
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception as e:
                await processing_message.edit_text(f"⚠️ An error occurred while processing your query: {e}")
        else:
            await update.message.reply_text("⚠️ Please provide a query after the /ai command.")

# команда /ai_clean
async def ai_clean(update: Update, context: CallbackContext) -> None:
    print("dialog_history:\n"+str(dialog_history))
    user_id = update.message.from_user.id
    if user_id in dialog_history:
        del dialog_history[user_id]
    await update.message.reply_text("🗑️ AI dialogue history cleared.")
    print("🗑️ AI dialogue history cleared.")

# команда /json
async def send_json(update: Update, context: CallbackContext) -> None:
    log_any_user(update)
    test_json = {
        "name": "Test User",
        "age": 25,
        "email": "testuser@example.com",
        "is_admin": False,
        "preferences": {
            "theme": "dark",
            "notifications": True
        }
    }
    # Зберігаємо JSON у файл
    file_path = "test_data.json"
    with open(file_path, "w", encoding="utf-8") as file:
        import json
        json.dump(test_json, file, indent=4, ensure_ascii=False)

    # Відправляємо файл користувачу
    await context.bot.send_document(
        chat_id=update.message.chat_id,
        document=open(file_path, "rb"),
        filename="test_data.json",
        caption="📄 Here is your test JSON file."
    )

# команда /id
async def reply_id(update: Update, context: CallbackContext) -> None:
    log_any_user(update)
    if update.message.from_user.id == admin_id:

        if update.message.reply_to_message:
            replied_message_id = update.message.reply_to_message.message_id
            await update.message.reply_text(f"🆔 The ID of the replied message is: {replied_message_id}")
        else:
            await update.message.reply_text("⚠️ Please reply to a message to use this command.")

# команда /track
async def track_chats(update: Update, context: CallbackContext) -> None:
    log_any_user(update)
    if update.message.from_user.id != admin_id:
        return

    # Parse arguments
    hours = 24
    topic = "Birthday Party"
    
    if context.args:
        try:
            hours = int(context.args[0])
            if len(context.args) > 1:
                topic = " ".join(context.args[1:])
        except ValueError:
            topic = " ".join(context.args)

    processing_message = await update.message.reply_text(f"⏳ Scanning private chats active in the last {hours} hours for topic: '{topic}'...")

    from datetime import timezone, timedelta
    time_threshold = datetime.now(timezone.utc) - timedelta(hours=hours)

    try:
        active_dialogs = []
        userbot_me = await userbotTG_client.get_me()
        userbot_id = userbot_me.id
        
        # Get all recent dialogs (up to 100)
        dialog_limit = 100
        fetched_dialogs = 0
        async for dialog in userbotTG_client.get_dialogs():
            fetched_dialogs += 1
            if fetched_dialogs > dialog_limit:
                break
                
            # Filter: private chat only
            if dialog.chat.type != ChatType.PRIVATE:
                continue
            
            # Filter: must have activity in the last N hours
            last_msg_date = dialog.top_message.date if dialog.top_message else None
            if not last_msg_date:
                continue
            
            last_msg_date_utc = last_msg_date.replace(tzinfo=timezone.utc)
            if last_msg_date_utc < time_threshold:
                continue
            
            active_dialogs.append(dialog)

        if not active_dialogs:
            await processing_message.edit_text(f"No private chats with activity in the last {hours} hours.")
            return

        await processing_message.edit_text(f"Found {len(active_dialogs)} active chats. Collecting message history...")

        collected_chats = []
        for dialog in active_dialogs:
            chat_id = dialog.chat.id
            display_name = dialog.chat.title if dialog.chat.title else (dialog.chat.first_name or '') + ' ' + (dialog.chat.last_name or '')
            username = dialog.chat.username or ""
            
            # Fetch message history
            chat_messages = []
            userbot_sent_msg = False
            
            async for msg in userbotTG_client.get_chat_history(chat_id):
                msg_date_utc = msg.date.replace(tzinfo=timezone.utc)
                if msg_date_utc < time_threshold:
                    break
                
                # Check if userbot (admin) sent a message
                if msg.from_user and msg.from_user.id == userbot_id:
                    userbot_sent_msg = True
                
                # Determine content
                if msg.text:
                    content = msg.text
                elif msg.photo:
                    content = f"[Photo] {msg.caption or ''}"
                elif msg.sticker:
                    content = f"[{msg.sticker.emoji or ''} Sticker]"
                elif msg.voice:
                    content = f"[Voice, {msg.voice.duration}s]"
                elif msg.video:
                    content = f"[Video]"
                else:
                    content = f"[Message]"
                
                sender = "Userbot (Me)" if msg.from_user and msg.from_user.id == userbot_id else "Friend"
                chat_messages.append(f"[{sender} at {msg.date.strftime('%H:%M')}]: {content}")

            # Keep only chats where we actually initiated/sent a message
            if userbot_sent_msg and chat_messages:
                chat_messages.reverse()
                collected_chats.append({
                    "name": display_name,
                    "username": username,
                    "chat_id": chat_id,
                    "history": "\n".join(chat_messages)
                })

        if not collected_chats:
            await processing_message.edit_text(f"No private chats found where you sent messages in the last {hours} hours.")
            return

        await processing_message.edit_text(f"Analyzing {len(collected_chats)} chats with Gemini AI...")

        # Construct prompt for Gemini
        chat_logs_formatted = []
        for c in collected_chats:
            chat_logs_formatted.append(
                f"Participant: {c['name']} (username: @{c['username']}, ID: {c['chat_id']})\n"
                f"Chat Log:\n{c['history']}\n"
                f"---"
            )
        
        prompt = (
            f"You are a tracking assistant. The user is tracking invitations or responses regarding: '{topic}'.\n"
            f"Analyze the following {len(collected_chats)} chat logs. For each participant, determine:\n"
            f"1. Did the user invite or ask them about this topic? (Yes/No)\n"
            f"2. If Yes, what is their current response status? (Agreed, Declined, or Pending/Waiting)\n"
            f"   - Agreed: They clearly said yes, expressed enthusiasm, or confirmed attendance.\n"
            f"   - Declined: They said no, cannot make it, or refused.\n"
            f"   - Pending: They were asked but haven't replied yet, said they will answer later, are unsure, or the conversation ended without a decision.\n"
            f"3. Select the appropriate emoji:\n"
            f"   - Agreed: ✅\n"
            f"   - Declined: ❌\n"
            f"   - Pending: ⏳\n\n"
            f"Format the output strictly as a clean Markdown list of participants who were asked. "
            f"For each person, include their name, clickable telegram link (using tg://user?id=ID), status emoji, and a very short explanation of their response (in English).\n"
            f"Format:\n"
            f"- [Emoji] [{c['name']}](tg://user?id=ID) (@username): [Short reason/explanation]\n\n"
            f"Do not include people who were not invited or asked about this topic.\n\n"
            f"Chats to analyze:\n"
            f"{chr(10).join(chat_logs_formatted)}"
        )

        ai_response = generate_ai_content(
            model="gemini-3.5-flash",
            contents=prompt,
        )
        response_text = ai_response.text if hasattr(ai_response, 'text') else str(ai_response)

        # Convert to HTML and sanitize
        html_report = markdown.markdown(response_text)
        sanitized_report = bleach.clean(html_report, tags=allowed_tags, strip=True)

        result_message = f"📋 <b>Tracking Report for: '{topic}'</b> (last {hours}h):\n\n{sanitized_report}"
        
        if len(result_message) <= 4096:
            await processing_message.edit_text(result_message, parse_mode="HTML")
        else:
            file_path = f"tracking_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(result_message)
            
            await processing_message.edit_text("Report is too long. Sending as a file...")
            await context.bot.send_document(
                chat_id=admin_id,
                document=open(file_path, "rb"),
                filename=file_path,
                caption=f"📄 Tracking report for '{topic}'"
            )
            os.remove(file_path)

    except Exception as e:
        await processing_message.edit_text(f"⚠️ Error running track analysis: {e}")

# Основні повідомлення
async def echo(update: Update, context: CallbackContext) -> None:
    log_any_user(update)
    # Перевірка, що повідомлення відправлено мною
    if update.message.from_user.id == admin_id:
        global my_chat_histoty  # Вказуємо, що будемо використовувати глобальну змінну

        # ВІДПОВІДНЕ ПОВІДОМЛЕННЯ
        if update.message.reply_to_message:
            await AI_answer(update, context, AI_question=update.message.text)  # Виклик функції AI_answer для обробки відповіді на повідомлення
                
        # ПОВІДОМЛЕННЯ ЗАПИТ
        else:
            # Відправляємо повідомлення
            processing_message = await update.message.reply_text("⏳ Loading...", parse_mode="HTML", disable_web_page_preview=True)

            # Читаємо повідомлення користувача
            try:
                lines = update.message.text.split("\n")
                chat_id = lines[0].strip()
                try:
                    msg_count = int(lines[1].strip()) # Читаємо вміст другого рядка
                except (IndexError, ValueError):
                    msg_count = 10  # Якщо другого рядка немає або він некоректний, використовуємо значення за замовчуванням

                try:
                    AI_question = lines[2].strip()  # Читаємо вміст третього рядка
                except IndexError:
                    AI_question = AI_default_prompt  # Якщо третього рядка немає, встановлюємо значення за замовчуванням


            except (IndexError, ValueError):
                await processing_message.edit_text("⚠️ Error: Invalid input format. Please provide chat_id on the first line, msg_count on the second line, and optionally a third line.")
                return


            # Основна логіка Pyrogram
            try:
                # Отримуємо інформацію про чат
                chat = await userbotTG_client.get_chat(chat_id)  # Прибрано використання async with
                icon, direct_link = get_chat_icon_and_link(chat)
                result = f"<a href='{direct_link}'>{icon} ''{chat.title or chat.first_name}''</a>\n🆔 <code>{chat_id}</code>\n#️⃣ last {msg_count} messages:\n"
                await processing_message.edit_text(result + '\n⏳ Loading...', parse_mode="HTML", disable_web_page_preview=True)

                # Отримуємо останні повідомлення з чату
                messages = []
                async for msg in userbotTG_client.get_chat_history(chat_id, limit=msg_count):  # Асинхронна ітерація
                    messages.append(msg)

                    # Формуємо ASCII прогрес-бар
                    progress_bar_length = 30  # Довжина прогрес-бара
                    progress = int((len(messages) / msg_count) * progress_bar_length)  # 20 символів у прогрес-барі
                    progress_bar = f"{'█' * progress}{'░' * (progress_bar_length - progress)}" 
                    
                    remaining_sec = int((msg_count - len(messages)) * delay_TG)  # Час, що залишився, у секундах
                    if remaining_sec <= 60:
                        remaining_time_str = f"{remaining_sec} sec"
                    else:
                         remaining_time_str = f"{remaining_sec // 60} min {remaining_sec % 60} sec"
                    

                    # Оновлюємо повідомлення з прогресом
                    await processing_message.edit_text(
                        result + f"\n⏳ Loading...\n {len(messages)}/{msg_count} done ~{remaining_time_str} left\n<code>{progress_bar}</code> {round(len(messages) / msg_count * 100, 1)}%",
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    await asyncio.sleep(delay_TG)  # Затримка між запитами для запобігання перевищенню ліміту API

                if messages:
                    my_chat_histoty = []  # Змінна для зберігання історії чату у вигляді списку
                    for msg in reversed(messages):  # Перевертаємо список для виведення в порядку від старих до нових
                        sender_name = msg.from_user.first_name if msg.from_user else "Unknown_user"
                        message_time = msg.date.strftime('%Y-%m-%d %H:%M') if msg.date else "Unknown time"

                        # Визначаємо тип повідомлення
                        if msg.text:
                            content = msg.text
                        elif msg.photo:
                            content = f"(image) {msg.caption or ''}"
                        elif msg.sticker:
                            content = f"({msg.sticker.emoji or ''} sticker)"
                        elif msg.video:
                            content = f"(video) {msg.caption or ''}"
                        elif msg.voice:
                            content = f"(voice message, {msg.voice.duration} sec long) {msg.caption or ''}"
                        elif msg.video_note:
                            content = f"(video message, {msg.video_note.duration} sec long)"
                        elif msg.document:
                            content = f"(document) {msg.document.file_name or ''}"
                        elif msg.animation:
                            content = "(GIF animation)"
                        elif msg.location:
                            content = f"(location: {msg.location.latitude}, {msg.location.longitude} )"
                        elif msg.poll:
                            options = ", ".join([f'"{option.text}"' for option in msg.poll.options])  # Вилучаємо текст із кожного варіанту
                            content = f"(poll ''{msg.poll.question}'', with options: {options})"
                        elif msg.new_chat_members:
                            content = f"({', '.join([member.first_name for member in msg.new_chat_members])} joined the chat)"
                        elif msg.left_chat_member:
                            content = f"({msg.left_chat_member.first_name} left the chat)"
                        else:
                            content = "(unknown message type)"

                        # Додаємо повідомлення до списку
                        my_chat_histoty.append({
                            "sender": sender_name,
                            "time": message_time,
                            "content": content
                        })

                    try:
                        simple_chat_name = generate_ai_content(
                            model="gemini-3.5-flash",
                            contents=f"answer only without problematic characters to write this: {chat.title or chat.first_name}",
                        ).text.strip()
                    except Exception as e:
                        result += f"⚠️ Error: {e}"
                        simple_chat_name = "chat"
                    print(f"\nsimple_chat_name: {simple_chat_name}")

                    # Ім'я json файлу історії чату
                    file_path = f"tg_{msg_count}-msgs-from-{simple_chat_name}.json"
                    
                    # Формуємо дані для збереження
                    chat_json_data = {
                        "chat_title": chat.title or chat.first_name or "Unknown Chat",
                        "chat_type": chat.type.name if chat.type else "Unknown Type",
                        "link": direct_link,
                        "messages": my_chat_histoty
                        # "participants": []
                    }

                    
                    # Зберігаємо дані в JSON-файл
                    with open(file_path, "w", encoding="utf-8") as file:
                        json.dump(chat_json_data, file, indent=4, ensure_ascii=False)
                    
                    print(f"💾 Chat history '{file_path}' saved!\n")


                    first_message = messages[-1]
                    first_message_link = f"https://t.me/c/{str(chat.id)[3:]}/{first_message.id}" if chat.type in [ChatType.SUPERGROUP, ChatType.CHANNEL] else ""
                    time_since_first_message = datetime.now() - first_message.date
                
                    # Форматуємо час в залежності від тривалості
                    if time_since_first_message.days > 0:
                        time_since_str = f"{time_since_first_message.days} days ago"
                    elif time_since_first_message.seconds >= 3600:
                        time_since_str = f"{time_since_first_message.seconds // 3600} hours ago"
                    elif time_since_first_message.seconds >= 60:
                        time_since_str = f"{time_since_first_message.seconds // 60} minutes ago"
                    else:
                        time_since_str = "just now"
                
                    result += f"🔝 <a href='{first_message_link}'>First message</a> {time_since_str}" if first_message_link else f"🔝 First message was sent {time_since_str}"
    

                    chat_history_preview = ""
                    lines_count = 1
                    print(f"is {len(result)} + {len(my_chat_histoty)} < 4096 ?")
                    if len(result) + len(my_chat_histoty) < 4096:
                        print("yes\n")
                        chat_history_preview = "\n".join([
                            f"[{msg['sender']} at {msg['time']}]:\n{msg['content']}\n"
                            for msg in my_chat_histoty
                        ])
                    else:
                        print("no")
                        while len(result)+len(chat_history_preview) < 4096:
                            chat_history_preview = "\n".join([
                                f"[{msg['sender']} at {msg['time']}]:\n{msg['content']}\n"
                                for msg in my_chat_histoty[:lines_count]
                                ]) + f"\n... and {len(my_chat_histoty) - (lines_count * 2)} more lines ...\n\n" + "\n".join([
                                f"[{msg['sender']} at {msg['time']}]:\n{msg['content']}\n"
                                for msg in my_chat_histoty[-lines_count:]
                            ])
                            lines_count += 1
                            print(f"lines_count: {lines_count}")
                            print(f"shortened_history len: {len(chat_history_preview)}")

                    print(f"{len(result)} + {len(my_chat_histoty)} < 4096\n")
                    result += f"<blockquote expandable>{chat_history_preview}</blockquote>"

                else:
                    result = f"⚠️ The chat with ID {chat_id} is empty or unavailable."
                    print(result)
            except PeerIdInvalid:
                result = f"⚠️ Error: The chat with ID {chat_id} is unavailable."
                print(result)
            except Exception as e:
                result = f"⚠️ An error occurred: {e}"
                print(result)

            await processing_message.edit_text(result, parse_mode="HTML", disable_web_page_preview=True)
            print("\n💬 [ chat preview ]")

            await context.bot.send_document(
                chat_id=admin_id,
                document=open(file_path, "rb"),
                filename=file_path,
                caption=f"📄 Chat history from  '{chat.title or chat.first_name}'"
            )
            print("💬 [ chat history file ]")


            # Видаляємо файл з робочої папки
            try:
                os.remove(file_path)
                print(f"🗑️ File '{file_path}' has been deleted from working folder")
            except Exception as e:
                print(f"⚠️ Error deleting file '{file_path}': {e}")

            await AI_answer(update, context, AI_question=AI_question)


# AI відповідь на повідомлення
async def AI_answer(update: Update, context: CallbackContext, AI_question) -> None:
    global my_chat_histoty  # Вказуємо, що будемо використовувати глобальну змінну

    # AI_prompt_in_message = update.message.text
    print("💬 [ AI answer ]")
    result = "🤖 AI answer:\n"
    processing_message = await update.message.reply_text(result+"⏳ Loading...", parse_mode="HTML") #, reply_to_message_id=update.message.message_id

    # Відправляємо запит у Gemini
    try:
        ai_response = generate_ai_content(
            model="gemini-3.5-flash",
            contents=f"{AI_question}\n\n{my_chat_histoty}",
        )
        response = ai_response if isinstance(ai_response, str) else ai_response.text
        result += f"<blockquote>{bleach.clean(markdown.markdown(response), tags=allowed_tags, strip=True)}</blockquote>"
    except Exception as e:
        result += f"⚠️ Error: {e}"
    
    # Редагуємо повідомлення після завершення обробки
    await processing_message.edit_text(result, parse_mode="HTML", disable_web_page_preview=True)

# всі вхідні повідомлення
async def log_message(update: Update, context: CallbackContext) -> None:

    log_any_user(update)
    
# Логування повідомлень у консоль
def log_any_user(update: Update) -> None:
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    username = f"@{update.message.from_user.username}" if update.message.from_user.username else "(no username)"
    message_text = update.message.text if update.message.text else "(non-text message)"
    user_id = update.message.from_user.id if update.message.from_user else "(Unknown user ID)"
    user_name = update.message.from_user.first_name or '' + ' ' + (update.message.from_user.last_name or '')

    # Виводимо в консоль
    print(f"\n🗨️ [{current_time} {username}]\n{message_text}")

    if update.message.from_user.id != admin_id:
        asyncio.create_task(send_message(f"⚠️ Message from an unknown user!\n 👤 {user_name}\n{username}\n🆔 <code>{user_id}</code>\nmessage:"))
        asyncio.create_task(forward_message_to_admin(update))

# Функція для пересилання повідомлення адміністратору
async def forward_message_to_admin(update: Update):
    bot = telegram.Bot(token=TGbot_token)
    try:
        # Використовуємо copy_message для пересилання повідомлення в тому ж вигляді
        await bot.copy_message(
            chat_id=admin_id,  # ID адміністратора
            from_chat_id=update.message.chat_id,  # ID чата, откуда пришло сообщение
            message_id=update.message.message_id  # ID сообщения для копирования
        )
        print(f"💬 Message forwarded to admin.")
    except Exception as e:
        print(f"⚠️ Error forwarding message to admin: {e}")

# Функція для відправки повідомлення адміністратору
async def send_message(text: str):
    bot = telegram.Bot(token=TGbot_token)
    try:
        await bot.send_message(chat_id=admin_id, text=f"{text}", parse_mode="HTML")
        print(f"💬 Message sent to admin: {text}")
    except Exception as e:
        print(f"⚠️ Error sending message to admin: {e}")
        

async def preload_dialogs():
    try:
        print("⏳ Preloading dialogs...")
        async for _ in userbotTG_client.get_dialogs():
            pass  # Просто ітеруємося, щоб завантажити всі діалоги в кеш
        print("✅ Dialogs preloaded successfully!")
    except Exception as e:
        print(f"⚠️ Error preloading dialogs: {e}")

# Основна функція для запуску Telegram-бота
def main() -> None:
    # Реєструємо обробники
    botTG_client.add_handler(CommandHandler("start", start))
    botTG_client.add_handler(CommandHandler("ping", ping))
    botTG_client.add_handler(CommandHandler("list", list_chats))
    botTG_client.add_handler(CommandHandler("ai", ai_query))
    botTG_client.add_handler(CommandHandler("ai_clean", ai_clean))
    botTG_client.add_handler(CommandHandler("json", send_json))
    botTG_client.add_handler(CommandHandler("id", reply_id))
    botTG_client.add_handler(CommandHandler("track", track_chats))
    botTG_client.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    botTG_client.add_handler(MessageHandler(filters.ALL, log_message))
    
    ''' Для телеграм бота команди:
    list - <n> <private/group/channel> show recent chats
    ping - check the bot's connectivity
    start - test
    id - test. get the ID of the replied message
    ai -  test. Ask AI Gemini directly
    ai_clean -  test. Clear the AI dialogue history
    track - <hours> <topic> scan recent chats and analyze status using AI
    '''

    try:
        # Попередня загрузка діалогів
        # Запускаємо бота
        botTG_client.run_polling()
    finally:
        # Закриваємо клієнта Pyrogram при завершенні роботи
        userbotTG_client.stop()

if __name__ == '__main__':
    print("🚀 Script started!")

    # Створюємо цикл подій
    loop = asyncio.get_event_loop()

    # Відправляємо початкове повідомлення
    loop.run_until_complete(send_message("🚀 Script updated and started!"))

    # Запускаємо клієнта Pyrogram
    userbotTG_client.start()  # Відкриваємо з'єднання з Pyrogram

    loop.run_until_complete(preload_dialogs())

    # Запускаємо основного бота
    main()