from . import ai, callback_query, command, message, template

routers = [
    command.router,
    command.router_id,
    template.router,
    ai.router,
    callback_query.router,
    message.router,
]
