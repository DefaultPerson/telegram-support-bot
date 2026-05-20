from . import callback_query, command, message, template

routers = [
    command.router,
    command.router_id,
    template.router,
    callback_query.router,
    message.router,
]
