from abc import ABCMeta, abstractmethod

from aiogram.utils.markdown import hbold

# Add other languages and their corresponding codes as needed.
# You can also keep only one language by removing the line with the unwanted language.
SUPPORTED_LANGUAGES = {
    "ru": "🇷🇺 Русский",
    "en": "🇺🇸 English",
}


class Text(metaclass=ABCMeta):
    """
    Abstract base class for handling text data in different languages.
    """

    def __init__(self, language_code: str) -> None:
        """
        Initializes the Text instance with the specified language code.

        :param language_code: The language code (e.g., "ru" or "en").
        """
        self.language_code = language_code if language_code in SUPPORTED_LANGUAGES.keys() else "en"

    @property
    @abstractmethod
    def data(self) -> dict:
        """
        Abstract property to be implemented by subclasses. Represents the language-specific text data.

        :return: Dictionary containing language-specific text data.
        """
        raise NotImplementedError

    def get(self, code: str) -> str:
        """
        Retrieves the text corresponding to the provided code in the current language.

        :param code: The code associated with the desired text.
        :return: The text in the current language.
        """
        return self.data[self.language_code][code]


class TextMessage(Text):
    """
    Subclass of Text for managing text messages in different languages.
    """

    @property
    def data(self) -> dict:
        """
        Provides language-specific text data for text messages.

        :return: Dictionary containing language-specific text data for text messages.
        """
        return {
            "en": {
                "select_language": f"👋 <b>Hello</b>, {hbold('{full_name}')}!\n\nSelect language:",
                "change_language": "<b>Select language:</b>",
                "main_menu": "<b>Hello!</b> Write your question and we will reply as soon as possible.\n\nIf you would like to add a channel to the aggregator, please read the rules: https://t.me/clear_blog/14\n\nChange language — /language",
                "message_sent": "<b>Message sent!</b> Expect a response.",
                "message_edited": (
                    "<b>The message was edited only in your chat.</b> "
                    "To send an edited message, send it as a new message."
                ),
                "source": (
                    "Source code available at "
                    "<a href=\"https://github.com/nessshon/support-bot\">GitHub</a>"
                ),
                "user_started_bot": (
                    f"User {hbold('{name}')} started the bot!\n\n"
                    "List of available commands:\n\n"
                    "• /ban\n"
                    "Block/Unblock user"
                    "<blockquote>Block the user if you do not want to receive messages from him.</blockquote>\n\n"
                    "• /silent\n"
                    "Activate/Deactivate silent mode"
                    "<blockquote>When silent mode is enabled, messages are not sent to the user.</blockquote>\n\n"
                    "• /information\n"
                    "User information"
                    "<blockquote>Receive a message with basic information about the user.</blockquote>"
                ),
                "user_restarted_bot": f"User {hbold('{name}')} restarted the bot!",
                "user_stopped_bot": f"User {hbold('{name}')} stopped the bot!",
                "user_blocked": "<b>User blocked!</b> Messages from the user are not accepted.",
                "user_unblocked": "<b>User unblocked!</b> Messages from the user are being accepted again.",
                "blocked_by_user": "<b>Message not sent!</b> The bot has been blocked by the user.",
                "user_information": (
                    "<b>ID:</b>\n"
                    "- <code>{id}</code>\n"
                    "<b>Name:</b>\n"
                    "- {full_name}\n"
                    "<b>Status:</b>\n"
                    "- {state}\n"
                    "<b>Username:</b>\n"
                    "- {username}\n"
                    "<b>Blocked:</b>\n"
                    "- {is_banned}\n"
                    "<b>Registration date:</b>\n"
                    "- {created_at}"
                ),
                "message_not_sent": "<b>Message not sent!</b> An unexpected error occurred.",
                "message_sent_to_user": "<b>Message sent to user!</b>",
                "silent_mode_enabled": (
                    "<b>Silent mode activated!</b> Messages will not be delivered to the user."
                ),
                "silent_mode_disabled": (
                    "<b>Silent mode deactivated!</b> The user will receive all messages."
                ),
                "policy_disabled": "Policy engine is disabled.",
                "template_usage": "Usage: /template &lt;key&gt;",
                "template_not_found": "Template {key} not found.",
                "template_sent": "🤖 Sent to the user:\n{text}",
                "auto_reply_sent": "🤖 Auto-reply sent to the user:\n{text}",
                "ai_draft_header": "🤖 Suggested reply:",
                "ai_draft_send": "✅ Send",
                "ai_draft_skip": "🗑 Skip",
                "escalated": "Escalated.",
                "escalated_dev": "Escalated: {full_name} (id {id})",
                "draft_sent": "Sent",
                "draft_send_failed": "Failed to send",
                "draft_send_blocked": "Not sent: the user has blocked the bot",
                "draft_expired": "Draft expired",
                "draft_stale": "This draft is outdated: a newer one replaced it",
                "draft_skipped": "Skipped",
                "new_user_general": "🆕 New user: {name}",
                "ai_draft_category": "Category: {category}",
                "ai_auto_sent_header": "🤖 Auto-reply sent to the user (category {category}):",
                "category_admin_notice": "New conversation, {category}: {name}\n{link}",
                "ai_admins_only": "Only admins can do this.",
                "ai_stats_disabled": "The draft log is off (ai.log_drafts in the policy).",
                "ai_stats_usage": "Usage: /ai_stats [days], 1 to 3650",
                "ai_stats_empty": "No drafts logged for this period.",
                "ai_stats_header_all": "<b>AI drafts by category, all time:</b>",
                "ai_stats_header_days": "<b>AI drafts by category, last {days} days:</b>",
                "ai_stats_row": (
                    "{category}: {total} total, sent {sent}, skipped {skipped}, "
                    "manager replied {manager_replied}, auto {auto_sent}; sent share {rate}"
                ),
                "ai_stats_note": "Sent share = sent / (sent + skipped + manager replied).",
                "no_category": "no category",
                "ai_auto_no_categories": "No categories are configured (ai.categories in the policy).",
                "ai_auto_usage": "Usage: /ai_auto [&lt;key&gt; on|off]",
                "ai_auto_list_header": "<b>Automatic replies by category:</b>",
                "ai_auto_list_row": "{category} <code>{key}</code>: {mode}; {stats}",
                "ai_auto_on": "on",
                "ai_auto_off": "off",
                "ai_auto_human_only": "human only",
                "ai_auto_stats": "reviewed {reviewed}, sent {sent}, sent share {rate}",
                "ai_auto_unknown": "Unknown category: <code>{key}</code>.",
                "ai_auto_needs_human": (
                    "{category} needs a human (needs_human: true), so automatic replies "
                    "cannot be turned on for it."
                ),
                "ai_auto_confirm_on": (
                    "Turn on automatic replies for {category}? Its drafts will be sent "
                    "to users without review.\n\nStats: {stats}"
                ),
                "ai_auto_confirm_off": "Turn off automatic replies for {category}?\n\nStats: {stats}",
                "ai_auto_warning": (
                    "⚠️ Below the bar: sent share {rate} (needs {threshold}), "
                    "reviewed drafts {reviewed} (needs {min_drafts})."
                ),
                "ai_auto_confirm": "✅ Confirm",
                "ai_auto_cancel": "✖️ Cancel",
                "ai_auto_enabled": "Automatic replies for {category} are on.",
                "ai_auto_disabled": "Automatic replies for {category} are off.",
                "ai_auto_cancelled": "Cancelled.",
                "reply_reminder": "⏰ The user has been waiting for a reply for {hours} h",
            },
            "ru": {
                "select_language": f"👋 <b>Привет</b>, {hbold('{full_name}')}!\n\nВыберите язык:",
                "change_language": "<b>Выберите язык:</b>",
                "main_menu": "<b>Здравствуйте!</b> Напишите ваш вопрос — ответим в ближайшее время.\n\nЕсли хотите добавить канал в агрегатор, ознакомьтесь с правилами: https://t.me/clear_blog/14\n\nСменить язык — /language",
                "message_sent": "<b>Сообщение отправлено!</b> Ожидайте ответа.",
                "message_edited": (
                    "<b>Сообщение отредактировано только в вашем чате.</b> "
                    "Чтобы отправить отредактированное сообщение, отправьте его как новое сообщение."
                ),
                "source": (
                    "Исходный код доступен на "
                    "<a href=\"https://github.com/nessshon/support-bot\">GitHub</a>"
                ),
                "user_started_bot": (
                    f"Пользователь {hbold('{name}')} запустил(а) бота!\n\n"
                    "Список доступных команд:\n\n"
                    "• /ban\n"
                    "Заблокировать/Разблокировать пользователя"
                    "<blockquote>Заблокируйте пользователя, если не хотите получать от него сообщения.</blockquote>\n\n"
                    "• /silent\n"
                    "Активировать/Деактивировать тихий режим"
                    "<blockquote>При включенном тихом режиме сообщения не отправляются пользователю.</blockquote>\n\n"
                    "• /information\n"
                    "Информация о пользователе"
                    "<blockquote>Получить сообщение с основной информацией о пользователе.</blockquote>"
                ),
                "user_restarted_bot": f"Пользователь {hbold('{name}')} перезапустил(а) бота!",
                "user_stopped_bot": f"Пользователь {hbold('{name}')} остановил(а) бота!",
                "user_blocked": "<b>Пользователь заблокирован!</b> Сообщения от пользователя не принимаются.",
                "user_unblocked": "<b>Пользователь разблокирован!</b> Сообщения от пользователя вновь принимаются.",
                "blocked_by_user": "<b>Сообщение не отправлено!</b> Бот был заблокирован пользователем.",
                "user_information": (
                    "<b>ID:</b>\n"
                    "- <code>{id}</code>\n"
                    "<b>Имя:</b>\n"
                    "- {full_name}\n"
                    "<b>Статус:</b>\n"
                    "- {state}\n"
                    "<b>Username:</b>\n"
                    "- {username}\n"
                    "<b>Заблокирован:</b>\n"
                    "- {is_banned}\n"
                    "<b>Дата регистрации:</b>\n"
                    "- {created_at}"
                ),
                "message_not_sent": "<b>Сообщение не отправлено!</b> Произошла неожиданная ошибка.",
                "message_sent_to_user": "<b>Сообщение отправлено пользователю!</b>",
                "silent_mode_enabled": (
                    "<b>Тихий режим активирован!</b> Сообщения не будут доставлены пользователю."
                ),
                "silent_mode_disabled": (
                    "<b>Тихий режим деактивирован!</b> Пользователь будет получать все сообщения."
                ),
                "policy_disabled": "Движок политик отключён.",
                "template_usage": "Использование: /template &lt;ключ&gt;",
                "template_not_found": "Шаблон {key} не найден.",
                "template_sent": "🤖 Отправлено пользователю:\n{text}",
                "auto_reply_sent": "🤖 Бот отправил автоответ пользователю:\n{text}",
                "ai_draft_header": "🤖 Черновик ответа:",
                "ai_draft_send": "✅ Отправить",
                "ai_draft_skip": "🗑 Пропустить",
                "escalated": "Эскалировано.",
                "escalated_dev": "Эскалация: {full_name} (id {id})",
                "draft_sent": "Отправлено",
                "draft_send_failed": "Не удалось отправить",
                "draft_send_blocked": "Не отправлено: клиент заблокировал бота",
                "draft_expired": "Черновик устарел",
                "draft_stale": "Черновик устарел: его заменил более новый",
                "draft_skipped": "Пропущено",
                "new_user_general": "🆕 Новый пользователь: {name}",
                "ai_draft_category": "Категория: {category}",
                "ai_auto_sent_header": "🤖 Пользователю отправлен автоответ (категория {category}):",
                "category_admin_notice": "Новый диалог, {category}: {name}\n{link}",
                "ai_admins_only": "Доступно только админам.",
                "ai_stats_disabled": "Журнал черновиков выключен (ai.log_drafts в политике).",
                "ai_stats_usage": "Использование: /ai_stats [дней], от 1 до 3650",
                "ai_stats_empty": "За этот период черновиков нет.",
                "ai_stats_header_all": "<b>Черновики ИИ по категориям, за всё время:</b>",
                "ai_stats_header_days": "<b>Черновики ИИ по категориям, за {days} дн.:</b>",
                "ai_stats_row": (
                    "{category}: всего {total}, отправлено {sent}, пропущено {skipped}, "
                    "ответил менеджер {manager_replied}, автоответ {auto_sent}; доля отправленных {rate}"
                ),
                "ai_stats_note": "Доля отправленных = отправлено / (отправлено + пропущено + ответил менеджер).",
                "no_category": "без категории",
                "ai_auto_no_categories": "Категории не настроены (ai.categories в политике).",
                "ai_auto_usage": "Использование: /ai_auto [&lt;ключ&gt; on|off]",
                "ai_auto_list_header": "<b>Автоответы по категориям:</b>",
                "ai_auto_list_row": "{category} <code>{key}</code>: {mode}; {stats}",
                "ai_auto_on": "вкл",
                "ai_auto_off": "выкл",
                "ai_auto_human_only": "только человек",
                "ai_auto_stats": "проверено {reviewed}, отправлено {sent}, доля отправленных {rate}",
                "ai_auto_unknown": "Неизвестная категория: <code>{key}</code>.",
                "ai_auto_needs_human": (
                    "{category} требует человека (needs_human: true), автоответы для неё "
                    "включить нельзя."
                ),
                "ai_auto_confirm_on": (
                    "Включить автоответы для {category}? Её черновики будут уходить "
                    "пользователям без проверки.\n\nСтатистика: {stats}"
                ),
                "ai_auto_confirm_off": "Выключить автоответы для {category}?\n\nСтатистика: {stats}",
                "ai_auto_warning": (
                    "⚠️ Ниже порога: доля отправленных {rate} (нужно {threshold}), "
                    "проверено черновиков {reviewed} (нужно {min_drafts})."
                ),
                "ai_auto_confirm": "✅ Подтвердить",
                "ai_auto_cancel": "✖️ Отмена",
                "ai_auto_enabled": "Автоответы для {category} включены.",
                "ai_auto_disabled": "Автоответы для {category} выключены.",
                "ai_auto_cancelled": "Отменено.",
                "reply_reminder": "⏰ Клиент ждёт ответа {hours} ч",
            },
        }
