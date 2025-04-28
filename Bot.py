import asyncio
import json
import logging
import os
import re
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, ConversationHandler, MessageHandler,
                          filters)
from telethon import TelegramClient, events
from telethon.tl.functions.channels import LeaveChannelRequest
from telethon.tl.functions.contacts import BlockRequest
from telethon.tl.functions.account import UpdateNotifySettingsRequest
from telethon.tl.types import InputNotifyPeer, InputPeerNotifySettings

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния диалога
START, PHONE, CODE, CODE_INPUT, OPERATIONAL = range(5)

# Константы для API Telegram
TELEGRAM_BOT_TOKEN = os.environ.get('6260969950:AAHCrisYmgFrxKvO-PzlMdU03IUF4aX6fYM')
API_ID = int(os.environ.get('TELEGRAM_API_ID', '26540663'))  # With default value
API_HASH = os.environ.get('38d8a00867f4e1b561c108eb487bcea7')

# Основная директория для хранения данных пользователей
DATA_DIR = Path('data')
if not DATA_DIR.exists():
    DATA_DIR.mkdir()

# Словарь для отслеживания активных клиентов
active_clients = {}

# Функции для работы с хранилищем

def get_user_config_path(user_id):
    """Получить путь к файлу конфигурации пользователя"""
    return DATA_DIR / f"user_{user_id}_config.json"

def get_session_path(user_id):
    """Получить путь к файлу сессии Telethon для пользователя"""
    return str(DATA_DIR / f"user_{user_id}")

def save_session(user_id):
    """Пометить, что у пользователя есть действительная сессия"""
    config_path = get_user_config_path(user_id)
    config = {}
    
    if config_path.exists():
        with open(config_path, 'r') as f:
            try:
                config = json.load(f)
            except json.JSONDecodeError:
                config = {}
    
    config['has_session'] = True
    
    with open(config_path, 'w') as f:
        json.dump(config, f)

def load_session(user_id):
    """Проверить, есть ли у пользователя действительная сессия"""
    config_path = get_user_config_path(user_id)
    
    if not config_path.exists():
        return False
    
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
            return config.get('has_session', False)
    except (json.JSONDecodeError, FileNotFoundError):
        return False

def save_user_mode(user_id, mode):
    """Сохранить выбранный пользователем режим работы"""
    config_path = get_user_config_path(user_id)
    config = {}
    
    if config_path.exists():
        with open(config_path, 'r') as f:
            try:
                config = json.load(f)
            except json.JSONDecodeError:
                config = {}
    
    config['mode'] = mode
    
    with open(config_path, 'w') as f:
        json.dump(config, f)

def get_user_mode(user_id):
    """Получить выбранный пользователем режим работы"""
    config_path = get_user_config_path(user_id)
    
    if not config_path.exists():
        return 1  # По умолчанию режим 1
    
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
            return config.get('mode', 1)
    except (json.JSONDecodeError, FileNotFoundError):
        return 1

# Функции для работы с Telethon клиентом

async def init_telethon_client(user_id, phone=None):
    """Инициализировать Telethon клиент для пользователя"""
    session_path = get_session_path(user_id)
    
    # Проверка, существует ли клиент и подключен ли он
    if user_id in active_clients and active_clients[user_id].is_connected():
        logger.info(f"Используется существующий клиент для пользователя {user_id}")
        return active_clients[user_id]
    
    logger.info(f"Создание нового клиента для пользователя {user_id}")
    
    # Создание нового клиента с более надежными настройками
    client = TelegramClient(
        session_path, 
        API_ID, 
        API_HASH,
        connection_retries=10,           # Более активные повторные попытки соединения
        auto_reconnect=True,             # Автоматическое переподключение
        retry_delay=1,                   # Задержка между повторными попытками в секундах
        request_retries=10,              # Повторные попытки для запросов API
        sequential_updates=True          # Обработка обновлений в последовательном порядке
    )
    
    # Важно: Для загрузки сохраненных настроек устройства
    client.session.set_dc(2, '149.154.167.51', 443)
    
    # Подключение с логикой повторных попыток
    for attempt in range(3):
        try:
            await client.connect()
            if await client.is_user_authorized():
                logger.info(f"Пользователь {user_id} авторизован с сохраненной сессией")
                
                # Проверка сессии
                me = await client.get_me()
                if me:
                    logger.info(f"Сессия подтверждена для пользователя {me.first_name} (ID: {me.id})")
                else:
                    logger.warning("Не удалось получить информацию о пользователе, хотя сессия активна")
            break
        except Exception as e:
            logger.error(f"Ошибка при подключении клиента (попытка {attempt+1}/3): {e}")
            if attempt == 2:  # Последняя попытка не удалась
                raise
    
    # Сохранение клиента в словаре
    active_clients[user_id] = client
    
    return client

async def login_with_code(client, phone, code):
    """Вход с использованием предоставленного кода подтверждения"""
    try:
        # Специальный флаг для авторизации без вытеснения других сессий
        # Учитывая, что стандартного решения нет, можно экспериментально попробовать
        # следующие параметры:
        
        # 1. Вход со специальными параметрами
        await client.sign_in(
            phone=phone, 
            code=code,
            # Пытаемся указать, что хотим сохранить существующие сессии 
            settings=None,  
            # Не отключать другие устройства
            sign_up=False
        )
        
        # Проверка, что успешно вошли
        if await client.is_user_authorized():
            logger.info(f"Успешный вход с номера {phone} с сохранением других сессий")
            return True
        else:
            logger.error(f"Не удалось авторизоваться с номера {phone} после sign_in")
            return False
    except Exception as e:
        logger.error(f"Ошибка при входе через код подтверждения: {e}")
        raise

async def setup_telethon_event_handlers(client, telegram_chat_id, context):
    """Настройка обработчиков событий для Telethon клиента"""
    
    @client.on(events.NewMessage())
    async def handle_new_message(event):
        """Обработка входящих сообщений в зависимости от выбранного режима"""
        # Пропуск исходящих сообщений
        if event.out:
            return
        
        # Получение режима пользователя
        user_id = context.user_data.get('user_id', telegram_chat_id)
        mode = get_user_mode(user_id)
        
        try:
            # Получение информации об отправителе
            if event.is_channel:
                sender = await event.get_chat()
                sender_name = sender.title
                is_channel = True
            else:
                sender = await event.get_sender()
                sender_name = f"{getattr(sender, 'first_name', '')} {getattr(sender, 'last_name', '')}".strip()
                if not sender_name and hasattr(sender, 'title'):
                    sender_name = sender.title
                is_channel = False
            
            if is_channel:
                # Выход из канала в обоих режимах
                try:
                    await client(LeaveChannelRequest(event.chat_id))
                    await context.bot.send_message(
                        telegram_chat_id,
                        f"Автоматический выход из канала: {sender_name}"
                    )
                except Exception as e:
                    logger.error(f"Ошибка при выходе из канала: {e}")
                    await context.bot.send_message(
                        telegram_chat_id,
                        f"Не удалось выйти из канала {sender_name}: {str(e)}"
                    )
            elif mode == 1:
                # Режим 1: Просто подтверждение
                try:
                    await event.reply("Ваше сообщение получено. Отвечу позже.")
                    await context.bot.send_message(
                        telegram_chat_id,
                        f"Получено сообщение от {sender_name}. Автоматически отправлен ответ с подтверждением."
                    )
                except Exception as e:
                    logger.error(f"Ошибка в обработке Режима 1: {e}")
                    await context.bot.send_message(
                        telegram_chat_id,
                        f"Ошибка при обработке сообщения от {sender_name}: {str(e)}"
                    )
            else:  # Режим 2
                # Проверка, является ли отправитель ботом
                if hasattr(sender, 'bot') and sender.bot:
                    # Проверка наличия сообщения /start в истории
                    messages = await client.get_messages(sender, limit=20)
                    found_start_message = False
                    
                    for msg in messages:
                        if msg.out and msg.text and msg.text.startswith('/start'):
                            found_start_message = True
                            break
                    
                    if not found_start_message:
                        # Блокировка бота
                        try:
                            await client(BlockRequest(sender.id))
                            await context.bot.send_message(
                                telegram_chat_id,
                                f"Заблокирован бот: {sender_name} (не найдено сообщение /start)"
                            )
                            return
                        except Exception as e:
                            logger.error(f"Ошибка при блокировке бота: {e}")
                            await context.bot.send_message(
                                telegram_chat_id,
                                f"Не удалось заблокировать бота {sender_name}: {str(e)}"
                            )
                
                # Отключение уведомлений для этого чата
                try:
                    peer = await event.get_input_chat()
                    await client(UpdateNotifySettingsRequest(
                        peer=InputNotifyPeer(peer=peer),
                        settings=InputPeerNotifySettings(
                            show_previews=False,
                            silent=True,
                            mute_until=2147483647  # Очень далеко в будущем
                        )
                    ))
                    
                    logger.info(f"Отключены уведомления для: {sender_name}")
                    
                    # Архивирование через альтернативный метод
                    try:
                        # В текущей версии telethon нет прямого метода ArchiveRequest
                        # Можно использовать client.edit_folder для архивирования
                        await client.edit_folder([peer], 1)  # 1 = архивная папка
                        logger.info(f"Чат отправлен в архив: {sender_name}")
                    except Exception as e:
                        # Если метод edit_folder не работает, просто логируем это
                        logger.error(f"Не удалось архивировать чат: {e}")
                except Exception as e:
                    logger.error(f"Ошибка при отключении уведомлений: {e}")
                
                # Пересылка сообщения боту
                try:
                    await event.reply("Ваше сообщение получено.")
                    
                    # Пересылка содержимого сообщения
                    message_text = event.message.text or event.message.message or "[Нет текстового содержимого]"
                    await context.bot.send_message(
                        telegram_chat_id,
                        f"Сообщение от {sender_name}:\n\n{message_text}"
                    )
                except Exception as e:
                    logger.error(f"Ошибка в обработке Режима 2: {e}")
                    await context.bot.send_message(
                        telegram_chat_id,
                        f"Ошибка при пересылке сообщения от {sender_name}: {str(e)}"
                    )
        
        except Exception as e:
            logger.error(f"Общая ошибка в обработчике событий: {e}")
            await context.bot.send_message(
                telegram_chat_id,
                f"Ошибка обработки входящего сообщения: {str(e)}"
            )
    
    @client.on(events.ChatAction())
    async def handle_chat_action(event):
        """Обработка действий в чате, например, добавление в группу/канал"""
        if event.user_added and event.user_id == client.get_me().id:
            # Пользователь был добавлен в группу/канал, выходим из нее
            try:
                await client(LeaveChannelRequest(event.chat_id))
                await context.bot.send_message(
                    telegram_chat_id,
                    f"Автоматический выход из группы/канала, в который вас добавили"
                )
            except Exception as e:
                logger.error(f"Ошибка при выходе из группы после добавления: {e}")
                await context.bot.send_message(
                    telegram_chat_id,
                    f"Не удалось выйти из группы, в которую вас добавили: {str(e)}"
                )
    
    # Установка user_id в context для ссылки в обработчиках событий
    context.user_data['user_id'] = telegram_chat_id
    
    # Сохранение обработчиков для очистки при необходимости
    if 'handlers' not in context.user_data:
        context.user_data['handlers'] = []
    
    context.user_data['handlers'].append(handle_new_message)
    context.user_data['handlers'].append(handle_chat_action)
    
    logger.info(f"Telethon обработчики событий настроены для пользователя {telegram_chat_id}")

# Вспомогательные функции

async def block_user(client, user_id):
    """Блокировать пользователя с помощью клиента Telethon"""
    try:
        await client(BlockRequest(user_id))
        return True
    except Exception as e:
        logger.error(f"Ошибка при блокировке пользователя {user_id}: {e}")
        return False

async def leave_channel(client, channel_id):
    """Покинуть канал с помощью клиента Telethon"""
    try:
        await client(LeaveChannelRequest(channel_id))
        return True
    except Exception as e:
        logger.error(f"Ошибка при выходе из канала {channel_id}: {e}")
        return False

def parse_message_content(message):
    """Разбор содержимого сообщения с учетом различных типов сообщений"""
    if hasattr(message, 'text') and message.text:
        return message.text
    elif hasattr(message, 'caption') and message.caption:
        return message.caption
    elif hasattr(message, 'message') and message.message:
        return message.message
    else:
        return "[Нет текстового содержимого]"

def get_sender_name(sender):
    """Извлечение читаемого имени из объекта отправителя"""
    if hasattr(sender, 'first_name'):
        if hasattr(sender, 'last_name') and sender.last_name:
            return f"{sender.first_name} {sender.last_name}"
        return sender.first_name
    elif hasattr(sender, 'title'):
        return sender.title
    else:
        return "Неизвестный отправитель"

# Обработчики команд и сообщений

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало диалога и запрос телефонного номера."""
    user = update.effective_user
    
    if load_session(user.id):
        # Пользователь уже авторизован, предлагаем выбрать режим
        keyboard = [
            [InlineKeyboardButton("Режим 1: Отвечать автоматически", callback_data="1")],
            [InlineKeyboardButton("Режим 2: Пересылать сообщения мне", callback_data="2")]
        ]
        await update.message.reply_text(
            "Выберите режим работы бота:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return OPERATIONAL
    else:
        await request_phone(update, context)
        return PHONE

async def request_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос у пользователя номера телефона."""
    keyboard = [
        [InlineKeyboardButton("Поделиться номером телефона", request_contact=True)]
    ]
    await update.message.reply_text(
        "Для авторизации в Telegram необходим ваш номер телефона. "
        "Нажмите на кнопку ниже, чтобы поделиться своим номером, "
        "или отправьте его вручную в международном формате (например, +79991234567).",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return PHONE

async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохранение номера телефона и запрос кода подтверждения."""
    user = update.effective_user
    
    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = update.message.text.strip()
    
    # Проверка формата номера телефона
    if not phone.startswith('+'):
        phone = '+' + phone
    
    # Сохранение номера телефона в контексте
    context.user_data['phone'] = phone
    logger.info(f"Получен номер телефона от пользователя {user.id}")
    
    # Инициализация клиента Telethon
    try:
        client = await init_telethon_client(user.id, phone)
        
        # Проверка, авторизован ли уже пользователь
        if await client.is_user_authorized():
            save_session(user.id)
            await setup_telethon_event_handlers(client, user.id, context)
            
            await update.message.reply_text(
                "Вы уже авторизованы в Telegram. Выберите режим работы бота:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Режим 1: Отвечать автоматически", callback_data="1")],
                    [InlineKeyboardButton("Режим 2: Пересылать сообщения мне", callback_data="2")]
                ])
            )
            return OPERATIONAL
        
        # Запрос кода подтверждения
        await client.send_code_request(phone)
        
        # Переход к запросу кода
        await request_code(update, context)
        return CODE
        
    except Exception as e:
        logger.error(f"Ошибка при инициализации Telethon клиента: {e}")
        await update.message.reply_text(
            f"Произошла ошибка при подключении к Telegram API: {str(e)}\n"
            "Пожалуйста, проверьте номер телефона и попробуйте снова."
        )
        return PHONE

async def request_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос у пользователя кода подтверждения."""
    # Создание пустой клавиатуры для ввода кода
    keyboard = []
    for i in range(0, 10, 3):
        row = []
        for j in range(1, 4):
            num = i + j
            if num == 10:
                break
            row.append(InlineKeyboardButton(str(num), callback_data=f"num_{num}"))
        keyboard.append(row)
    
    # Добавление 0 и кнопок управления
    keyboard.append([
        InlineKeyboardButton("0", callback_data="num_0"),
        InlineKeyboardButton("⌫", callback_data="backspace"),
        InlineKeyboardButton("✓", callback_data="confirm")
    ])
    
    # Отправка сообщения с клавиатурой
    message = await update.message.reply_text(
        "Вам был отправлен код подтверждения в Telegram. "
        "Введите его с помощью клави
