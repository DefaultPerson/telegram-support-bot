from . import callback_query, command, message, my_chat_member

routers = [
    command.router,
    message.router,
    callback_query.router,
    my_chat_member.router,
]
