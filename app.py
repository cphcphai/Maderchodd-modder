import os
import random
import string
import time
from datetime import datetime, timedelta, timezone

import requests
from flask import Flask, request, jsonify


BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "change-me")

FIREBASE_DB_URL = os.environ.get("FIREBASE_DB_URL", "")
FIREBASE_API_KEY = os.environ.get("FIREBASE_API_KEY", "")
FIREBASE_ADMIN_EMAIL = os.environ.get("FIREBASE_ADMIN_EMAIL", "")
FIREBASE_ADMIN_PASSWORD = os.environ.get("FIREBASE_ADMIN_PASSWORD", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "").strip()

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
ROOT = "DPMods_Security"


DURATIONS = {
    "1d": ("1 Day", timedelta(days=1)),
    "3d": ("3 Days", timedelta(days=3)),
    "7d": ("7 Days", timedelta(days=7)),
    "10d": ("10 Days", timedelta(days=10)),
    "15d": ("15 Days", timedelta(days=15)),
    "1m": ("1 Month", timedelta(days=30)),
}


app = Flask(__name__)


_token_cache = {
    "id_token": "",
    "refresh_token": "",
    "expires_at": 0,
}


def _firebase_sign_in():
    r = requests.post(
        "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword",
        params={"key": FIREBASE_API_KEY},
        json={
            "email": FIREBASE_ADMIN_EMAIL,
            "password": FIREBASE_ADMIN_PASSWORD,
            "returnSecureToken": True,
        },
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    _token_cache["id_token"] = data["idToken"]
    _token_cache["refresh_token"] = data["refreshToken"]
    _token_cache["expires_at"] = time.time() + int(data["expiresIn"]) - 60
    return _token_cache["id_token"]


def _firebase_refresh():
    r = requests.post(
        "https://securetoken.googleapis.com/v1/token",
        params={"key": FIREBASE_API_KEY},
        data={
            "grant_type": "refresh_token",
            "refresh_token": _token_cache["refresh_token"],
        },
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    _token_cache["id_token"] = data["id_token"]
    _token_cache["refresh_token"] = data["refresh_token"]
    _token_cache["expires_at"] = time.time() + int(data["expires_in"]) - 60
    return _token_cache["id_token"]


def _get_id_token():
    if not _token_cache["id_token"]:
        return _firebase_sign_in()
    if time.time() >= _token_cache["expires_at"]:
        try:
            return _firebase_refresh()
        except requests.RequestException:
            return _firebase_sign_in()
    return _token_cache["id_token"]


def fb_get(path):
    r = requests.get(
        f"{FIREBASE_DB_URL}/{path}.json",
        params={"auth": _get_id_token()},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def fb_set(path, data):
    r = requests.put(
        f"{FIREBASE_DB_URL}/{path}.json",
        params={"auth": _get_id_token()},
        json=data,
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def fb_delete(path):
    r = requests.delete(
        f"{FIREBASE_DB_URL}/{path}.json",
        params={"auth": _get_id_token()},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def fb_patch(path, data):
    r = requests.patch(
        f"{FIREBASE_DB_URL}/{path}.json",
        params={"auth": _get_id_token()},
        json=data,
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def tg_call(method, payload):
    try:
        if method == "sendMessage":
            payload.pop("parse_mode", None)
        r = requests.post(
            f"{TELEGRAM_API}/{method}",
            json=payload,
            timeout=10,
        )
        return r.json()
    except requests.RequestException as e:
        print(f"[telegram] {method} failed: {e}")
        return {}


def send_message(chat_id, text, reply_markup=None, parse_mode="Markdown"):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return tg_call("sendMessage", payload)


def edit_message(chat_id, message_id, text, reply_markup=None, parse_mode="Markdown"):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return tg_call("editMessageText", payload)


def delete_telegram_message(chat_id, message_id):
    return tg_call("deleteMessage", {
        "chat_id": chat_id,
        "message_id": message_id,
    })


def answer_callback(callback_id, text=None, show_alert=False):
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
        payload["show_alert"] = show_alert
    return tg_call("answerCallbackQuery", payload)


MAIN_MENU_KB = {
    "keyboard": [
        [{"text": "🔑 Generate License Key"}],
        [{"text": "📋 Keys List"}],
        [{"text": "🏚️ Logout"}],
    ],
    "resize_keyboard": True,
}


REMOVE_KB = {"remove_keyboard": True}


def key_type_inline_kb():
    return {
        "inline_keyboard": [[
            {"text": "🤖 Manual Key", "callback_data": "type:manual"},
            {"text": "✏️ Name Key", "callback_data": "type:name"},
        ]]
    }


def duration_inline_kb():
    buttons = [
        {"text": label, "callback_data": f"dur:{code}"}
        for code, (label, _delta) in DURATIONS.items()
    ]
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    return {"inline_keyboard": rows}


def utc_now():
    return datetime.now(timezone.utc)


def parse_datetime(value):
    if value is None:
        return None
    value = str(value).strip()
    formats = [
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def get_key_status(key_data):
    if not isinstance(key_data, dict):
        return "UNKNOWN"
    if key_data.get("Banned") is True:
        return "BANNED"
    expires_at = parse_datetime(key_data.get("ExpiresAt"))
    if expires_at:
        return "EXPIRED" if expires_at <= utc_now() else "ACTIVE"
    expiry_date = parse_datetime(key_data.get("ExpiryDate"))
    if expiry_date:
        return "EXPIRED" if expiry_date.date() < utc_now().date() else "ACTIVE"
    return "UNKNOWN"


def get_used_device_count(key_data):
    devices = key_data.get("Devices")
    if not isinstance(devices, dict):
        return 0
    count = 0
    for device_id in devices.keys():
        if device_id == "dummy":
            continue
        count += 1
    return count


def get_session(chat_id):
    data = fb_get(f"{ROOT}/Bot_Sessions/{chat_id}")
    if not data:
        return None
    exp_date = parse_datetime(data.get("ExpiryDate"))
    if exp_date and exp_date.date() < utc_now().date():
        delete_session(chat_id)
        return None
    key = data.get("Key")
    if not key:
        delete_session(chat_id)
        return None
    key_data = fb_get(f"{ROOT}/Bot_Access/{key}")
    if not key_data:
        delete_session(chat_id)
        return None
    key_exp = parse_datetime(key_data.get("ExpiryDate"))
    if key_exp and key_exp.date() < utc_now().date():
        delete_session(chat_id)
        return None
    if str(key_data.get("UsedBy", "")) != str(chat_id):
        delete_session(chat_id)
        return None
    return data


def create_session(chat_id, key, expire_date):
    fb_set(f"{ROOT}/Bot_Sessions/{chat_id}", {
        "Key": key,
        "ExpiryDate": expire_date,
    })


def delete_session(chat_id):
    fb_delete(f"{ROOT}/Bot_Sessions/{chat_id}")


def set_pending(chat_id, pending):
    fb_patch(f"{ROOT}/Bot_Sessions/{chat_id}", {"Pending": pending})


def clear_pending(chat_id):
    fb_delete(f"{ROOT}/Bot_Sessions/{chat_id}/Pending")


def validate_access_key(key, chat_id):
    key = (key or "").strip()
    if not key:
        return None, "not_found"
    data = fb_get(f"{ROOT}/Bot_Access/{key}")
    if not data:
        return None, "not_found"
    exp_date = parse_datetime(data.get("ExpiryDate"))
    if exp_date and exp_date.date() < utc_now().date():
        return None, "expired"
    used_by = data.get("UsedBy")
    if used_by and str(used_by) != str(chat_id):
        return None, "already_used"
    if not used_by:
        fb_patch(f"{ROOT}/Bot_Access/{key}", {"UsedBy": chat_id})
    return data.get("ExpiryDate"), "ok"


_BASE36 = string.digits + string.ascii_lowercase


def gen_license_key():
    rand = "".join(random.choice(_BASE36) for _ in range(6)).upper()
    return f"DP-VIP-{rand}"


def valid_custom_key(key):
    if not key or len(key) < 3 or len(key) > 40:
        return False
    allowed = string.ascii_letters + string.digits + "-_"
    return all(char in allowed for char in key)


def create_license(chat_id, from_user, duration_code, device_limit, custom_key=None):
    label, delta = DURATIONS[duration_code]
    expires_at = datetime.utcnow() + delta

    if not custom_key:
        key_id = gen_license_key()
        for _ in range(10):
            if not fb_get(f"{ROOT}/Keys/{key_id}"):
                break
            key_id = gen_license_key()
    else:
        key_id = custom_key.strip()
        if fb_get(f"{ROOT}/Keys/{key_id}"):
            raise ValueError("KEY_ALREADY_EXISTS")

    username = None
    if from_user:
        tg_username = from_user.get("username")
        username = f"@{tg_username}" if tg_username else (
            from_user.get("first_name") or f"tg_{chat_id}"
        )

    payload = {
        "Username": username,
        "DeviceLimit": device_limit,
        "ExpiryDate": expires_at.strftime("%Y-%m-%d"),
        "ExpiresAt": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Devices": {},
        "Banned": False,
    }

    fb_set(f"{ROOT}/Keys/{key_id}", payload)
    return key_id, label, expires_at


def get_all_keys():
    data = fb_get(f"{ROOT}/Keys")
    if not data:
        return []
    result = []
    for key_id, key_data in data.items():
        if isinstance(key_data, dict):
            result.append((key_id, key_data))
    return result


def save_log_message(chat_id, message_id):
    path = f"{ROOT}/Bot_Sessions/{chat_id}/LogMessages"
    current = fb_get(path)
    if not isinstance(current, dict):
        current = {}
    current[str(message_id)] = {
        "MessageId": message_id,
        "CreatedAt": utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    fb_set(path, current)


def send_keys_list(chat_id):
    all_keys = get_all_keys()
    if not all_keys:
        result = send_message(
            chat_id,
            "📋 *Keys List\n\nNo license keys found.\n\n🧹 `/deletelog msg`",
            MAIN_MENU_KB,
        )
        if result.get("ok"):
            save_log_message(chat_id, result["result"]["message_id"])
        return

    lines = ["📋 Keys List", ""]
    for key_id, data in all_keys:
        status = get_key_status(data)
        used_devices = get_used_device_count(data)
        device_limit = data.get("DeviceLimit", 0)
        lines.append(f"🔑 `{key_id}`")
        lines.append(f"📱 Devices: `{used_devices}/{device_limit}`")
        lines.append(f"📌 Status: `{status}`")
        lines.append("")

    lines.append("🗑️ Remove Key:")
    lines.append("`/removekey DP-VIP-XXXXXX`")
    lines.append("")
    lines.append("🧹 Delete Keys List messages:")
    lines.append("`/deletelog msg`")

    result = send_message(chat_id, "\n".join(lines), MAIN_MENU_KB)
    if result.get("ok"):
        save_log_message(chat_id, result["result"]["message_id"])


def delete_log_messages(chat_id):
    path = f"{ROOT}/Bot_Sessions/{chat_id}/LogMessages"
    logs = fb_get(path)

    if not isinstance(logs, dict) or not logs:
        send_message(
            chat_id,
            "🧹 No saved Keys List messages found.",
            MAIN_MENU_KB,
        )
        return

    deleted = 0
    for _, log_data in logs.items():
        if not isinstance(log_data, dict):
            continue
        message_id = log_data.get("MessageId")
        if not message_id:
            continue
        result = delete_telegram_message(chat_id, int(message_id))
        if result.get("ok"):
            deleted += 1

    fb_delete(path)
    send_message(
        chat_id,
        f"🧹 Deleted `{deleted}` Keys List message(s).",
        MAIN_MENU_KB,
    )


def remove_license_key(chat_id, key):
    key = (key or "").strip()
    if not key:
        send_message(
            chat_id,
            "⚠️ Usage:\n`/removekey DP-VIP-XXXXXX`",
            MAIN_MENU_KB,
        )
        return

    key_data = fb_get(f"{ROOT}/Keys/{key}")
    if not key_data:
        send_message(
            chat_id,
            f"❌ Key `{key}` was not found.",
            MAIN_MENU_KB,
        )
        return

    fb_delete(f"{ROOT}/Keys/{key}")
    send_message(
        chat_id,
        f"✅ *Key Removed Successfully!*\n\n"
        f"🔑 `{key}`\n\n"
        "The license has been deleted from Firebase and can no longer be used.",
        MAIN_MENU_KB,
    )


START_MESSAGE = (
    "⚡ 𝘎𝘢𝘭𝘷𝘯𝘪𝘤𝘌𝘯𝘨𝘪𝘯𝘦 𝘈𝘥𝘮𝘪𝘯 𝘗𝘢𝘯𝘦𝘭\n"
    "🔐 𝘙𝘦𝘧𝘦𝘳𝘳𝘢𝘭 𝘒𝘦𝘺 𝘙𝘦𝘲𝘶𝘪𝘳𝘦𝘥\n\n"
    "👤 𝘼𝙙𝙢𝙞𝙣 𝙋𝙖𝙣𝙚𝙡 𝙖𝙘𝙘𝙚𝙨𝙨 𝙠𝙚 𝙡𝙞𝙮𝙚 𝙘𝙤𝙣𝙩𝙖𝙘𝙩:\n"
    "👉 @GVM_TRUST\n"
    "🔑 𝙄𝙛 𝙮𝙤𝙪 𝙝𝙖𝙫𝙚 𝙖 𝙍𝙚𝙛𝙚𝙧𝙧𝙖𝙡 𝙆𝙚𝙮, 𝙥𝙖𝙨𝙩𝙚 𝙞𝙩 𝙝𝙚𝙧𝙚:\n"
    "📥 Sᴇɴᴅ ʏᴏᴜʀ Rᴇꜰᴇʀʀᴀʟ Kᴇʏ ʙᴇʟᴏᴡ ᴛᴏ ᴄᴏɴᴛɪɴᴜᴇ👇 ."
)


# ADDED: Help command
HELP_MESSAGE = (
    "🛠️ GalvnicEngine Admin Bot — Help\n\n"
    "🔑 /start\n"
    "Bot start karke Referral Key se access activate karein.\n\n"
    "❓ /help\n"
    "Bot ke commands aur unka kaam dikhata hai.\n\n"
    "📢 /broadcast\n"
    "Admin ke liye: /start kar chuke users ko message bhejne ke liye.\n\n"
    "🗑️ /removekey KEY\n"
    "Firebase se license key permanently remove karta hai.\n\n"
    "🧹 /deletelog msg\n"
    "Saved Keys List messages delete karta hai.\n\n"
    "🔑 Generate License Key\n"
    "Manual ya Custom Name License Key create karta hai.\n\n"
    "📋 Keys List\n"
    "Active, Expired aur Banned license keys ke saath device usage dikhata hai.\n\n"
    "🏚️ Logout\n"
    "Current bot access session logout karta hai."
)


# ADDED: Save every user who sends /start (even without Referral Key)
def track_start_user(message):
    text = (message.get("text") or "").strip()
    if text != "/start":
        return

    chat_id = message["chat"]["id"]
    from_user = message.get("from") or {}

    path = f"{ROOT}/Bot_Users/{chat_id}"
    current = fb_get(path)

    fb_set(path, {
        "FirstSeen": (
            current.get("FirstSeen")
            if isinstance(current, dict)
            else None
        ) or utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Username": from_user.get("username"),
        "FirstName": from_user.get("first_name"),
    })


def get_start_users():
    data = fb_get(f"{ROOT}/Bot_Users")
    if not isinstance(data, dict):
        return []
    return list(data.keys())


# ADDED: Admin-only broadcast
def broadcast_message(chat_id, text):
    if not ADMIN_CHAT_ID or str(chat_id) != str(ADMIN_CHAT_ID):
        send_message(
            chat_id,
            "🚫 You are not authorized to use /broadcast.",
            MAIN_MENU_KB,
        )
        return

    if not text.strip():
        send_message(
            chat_id,
            "📢 Broadcast message empty hai.\n\n"
            "Use `/broadcast Your message here`",
            MAIN_MENU_KB,
        )
        return

    users = get_start_users()

    success = 0
    failed = 0

    for user_id in users:
        result = send_message(user_id, text, None)
        if result.get("ok"):
            success += 1
        else:
            failed += 1
        time.sleep(0.05)

    send_message(
        chat_id,
        "📢 Broadcast Completed!\n\n"
        f"👥 Total /start Users: `{len(users)}`\n"
        f"✅ Sent: `{success}`\n"
        f"❌ Failed: `{failed}`",
        MAIN_MENU_KB,
    )


def handle_message(message):
    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()

    # ADDED: /start users are stored for future broadcast.
    try:
        track_start_user(message)
    except Exception as e:
        print(f"[tracking] error: {e}")

    # ADDED: /help
    if text.lower() == "/help":
        send_message(chat_id, HELP_MESSAGE, MAIN_MENU_KB)
        return

    # ADDED: /broadcast command (admin only)
    if text.lower() == "/broadcast":
        if str(chat_id) != str(ADMIN_CHAT_ID):
            send_message(
                chat_id,
                "🚫 You are not authorized to use /broadcast.",
                MAIN_MENU_KB,
            )
            return

        set_pending(chat_id, {"Type": "awaiting_broadcast"})
        send_message(
            chat_id,
            "📢 *Broadcast Mode*\n\n"
            "Ab jo message bhejoge, woh un sabhi users ko send kiya jayega "
            "jinhone bot par /start kiya hai.\n\n"
            "❌ Cancel karne ke liye `/cancel` bhejo.",
            MAIN_MENU_KB,
        )
        return

    if text == "/start":
        session = get_session(chat_id)
        if session:
            send_message(
                chat_id,
                "✅ Welcome back!\n\nYou're already activated.\nUse the menu below.",
                MAIN_MENU_KB,
            )
        else:
            send_message(chat_id, START_MESSAGE, REMOVE_KB)
        return

    if text.lower() == "/deletelog msg":
        session = get_session(chat_id)
        if not session:
            send_message(
                chat_id,
                "🚫 Your bot access is not active.\n"
                "Send /start and enter a valid Referral Key.",
                REMOVE_KB,
            )
            return
        delete_log_messages(chat_id)
        return

    if text.lower().startswith("/removekey"):
        session = get_session(chat_id)
        if not session:
            send_message(
                chat_id,
                "🚫 Your bot access is not active.\n"
                "Send /start and enter a valid Referral Key.",
                REMOVE_KB,
            )
            return

        parts = text.split(maxsplit=1)
        remove_license_key(chat_id, parts[1] if len(parts) >= 2 else "")
        return

    session = get_session(chat_id)

    if not session:
        expire, status = validate_access_key(text, chat_id)

        if status == "ok":
            create_session(chat_id, text, expire)
            send_message(
                chat_id,
                f"✅ Activated Successfully!\n\n"
                f"Referral access valid until:\n`{expire}`\n\n"
                "Use the menu below.",
                MAIN_MENU_KB,
            )
        elif status == "already_used":
            send_message(
                chat_id,
                "🚫 *Referral Key Already Used*\n\n"
                "This key is already linked to another account.",
            )
        elif status == "expired":
            send_message(
                chat_id,
                "⌛ Referral Key Expired\n\n"
                "Please request a new Referral Key.",
            )
        else:
            send_message(
                chat_id,
                "❌ Invalid Referral Key\n\n"
                "Please check the key and try again.",
            )
        return

    pending = session.get("Pending") or {}

    # ADDED: broadcast message receiving state
    if pending.get("Type") == "awaiting_broadcast":
        if str(chat_id) != str(ADMIN_CHAT_ID):
            clear_pending(chat_id)
            send_message(
                chat_id,
                "🚫 You are not authorized to use /broadcast.",
                MAIN_MENU_KB,
            )
            return

        if text.lower() == "/cancel":
            clear_pending(chat_id)
            send_message(
                chat_id,
                "❌ Broadcast cancelled.",
                MAIN_MENU_KB,
            )
            return

        clear_pending(chat_id)
        broadcast_message(chat_id, text)
        return

    if pending.get("Type") == "awaiting_custom_key":
        custom_key = text.strip()

        if not valid_custom_key(custom_key):
            send_message(
                chat_id,
                "⚠️ Invalid key name.\n\n"
                "Use only letters, numbers, `-` or `_`.\n"
                "Length: 3 to 40 characters.",
            )
            return

        if fb_get(f"{ROOT}/Keys/{custom_key}"):
            send_message(
                chat_id,
                "❌ This key already exists.\nPlease send another key name.",
            )
            return

        set_pending(chat_id, {
            "Type": "awaiting_devices",
            "DurationCode": pending.get("DurationCode"),
            "CustomKey": custom_key,
        })

        send_message(
            chat_id,
            "✅ Custom Key Accepted\n\n"
            f"🔑 Key: `{custom_key}`\n\n"
            "📱 How many devices should this license support?\n\n"
            "Reply with a number, e.g. `1`, `2`, `5`.",
        )
        return

    if pending.get("Type") == "awaiting_devices":
        if not text.isdigit() or int(text) < 1:
            send_message(
                chat_id,
                "⚠️ Please send a valid number of devices, e.g. `1`, `2`, `5`.",
            )
            return

        device_limit = min(int(text), 100)
        duration_code = pending.get("DurationCode")
        custom_key = pending.get("CustomKey")

        if duration_code not in DURATIONS:
            clear_pending(chat_id)
            send_message(
                chat_id,
                "⚠️ Something went wrong.\n"
                "Please tap Generate License Key again.",
                MAIN_MENU_KB,
            )
            return

        try:
            key_id, label, expires_at = create_license(
                chat_id,
                message.get("from"),
                duration_code,
                device_limit,
                custom_key,
            )
        except ValueError as e:
            if str(e) == "KEY_ALREADY_EXISTS":
                send_message(
                    chat_id,
                    "❌ This key already exists.\nPlease choose another name.",
                )
                return
            raise

        clear_pending(chat_id)

        reply = (
            "✅ License Created!\n\n"
            f"🔑 Key: `{key_id}`\n"
            f"⏳ Duration: {label}\n"
            f"📅 Expires: `{expires_at.strftime('%Y-%m-%d %H:%M UTC')}`\n"
            f"📱 Devices: `{device_limit}`\n\n"
            "_Tap the key above to copy it._"
        )

        send_message(chat_id, reply, MAIN_MENU_KB)
        return

    if text == "🔑 Generate License Key":
        send_message(chat_id, "🔑 Choose Key Type:", key_type_inline_kb())
    elif text == "📋 Keys List":
        send_keys_list(chat_id)
    elif text == "🏚️ Logout":
        delete_session(chat_id)
        send_message(
            chat_id,
            "👋 Logged Out Successfully\n\n"
            "Send /start and enter a Referral Key to activate again.",
            REMOVE_KB,
        )
    else:
        send_message(
            chat_id,
            "Please use the menu buttons below.",
            MAIN_MENU_KB,
        )


def handle_callback(callback):
    chat_id = callback["message"]["chat"]["id"]
    message_id = callback["message"]["message_id"]
    data = callback.get("data", "")
    callback_id = callback["id"]

    if data.startswith("type:"):
        session = get_session(chat_id)
        if not session:
            edit_message(
                chat_id,
                message_id,
                "⚠️ Your access has expired or was revoked.\n\n"
                "Send /start to activate again.",
            )
            answer_callback(callback_id)
            return

        key_type = data.split(":", 1)[1]

        if key_type == "manual":
            set_pending(chat_id, {"Type": "awaiting_manual_duration"})
            edit_message(
                chat_id,
                message_id,
                "🤖 *Manual Key Selected*\n\n📅 Select license duration:",
                duration_inline_kb(),
            )
            answer_callback(callback_id, "Manual Key selected")
            return

        if key_type == "name":
            set_pending(chat_id, {"Type": "awaiting_name_duration"})
            edit_message(
                chat_id,
                message_id,
                "✏️ *Name Key Selected*\n\n📅 Select license duration:",
                duration_inline_kb(),
            )
            answer_callback(callback_id, "Name Key selected")
            return

        answer_callback(callback_id, "Unknown option.")
        return

    if data.startswith("dur:"):
        session = get_session(chat_id)
        if not session:
            edit_message(
                chat_id,
                message_id,
                "⚠️ Your access has expired or was revoked.\n\n"
                "Send /start to activate again.",
            )
            answer_callback(callback_id)
            return

        code = data.split(":", 1)[1]
        if code not in DURATIONS:
            answer_callback(callback_id, "Unknown duration.")
            return

        pending = session.get("Pending") or {}
        pending_type = pending.get("Type")
        label = DURATIONS[code][0]

        if pending_type == "awaiting_manual_duration":
            set_pending(chat_id, {
                "Type": "awaiting_devices",
                "DurationCode": code,
            })
            edit_message(
                chat_id,
                message_id,
                f"⏳ Duration: {label}\n\n"
                "📱 How many devices should this license support?\n\n"
                "Reply with a number, e.g. `1`, `2`, `5`.",
            )
            answer_callback(callback_id, f"{label} selected")
            return

        if pending_type == "awaiting_name_duration":
            set_pending(chat_id, {
                "Type": "awaiting_custom_key",
                "DurationCode": code,
            })
            edit_message(
                chat_id,
                message_id,
                f"⏳ Duration: {label}\n\n"
                "✏️ Enter your custom key name.\n\n"
                "Examples:\n"
                "`TRUSTVIP`\n"
                "`TRUST-7D`\n"
                "`VIP_2026`",
            )
            answer_callback(callback_id, f"{label} selected")
            return

        answer_callback(
            callback_id,
            "Please start Generate License Key again.",
            True,
        )
        return

    answer_callback(callback_id)


@app.route("/", methods=["GET"])
def health():
    return "Bot is running.", 200


@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    try:
        if "message" in update:
            handle_message(update["message"])
        elif "callback_query" in update:
            handle_callback(update["callback_query"])
    except Exception as e:
        print(f"[webhook] error handling update: {e}")
    return jsonify(ok=True)


@app.route(f"/register-webhook/{WEBHOOK_SECRET}", methods=["GET"])
def register_webhook():
    base_url = request.url_root.rstrip("/")
    target = f"{base_url}/webhook/{WEBHOOK_SECRET}"
    result = tg_call("setWebhook", {"url": target})
    return jsonify({
        "webhook_url": target,
        "telegram_response": result,
    })


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
    )
