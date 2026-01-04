"""Logging middleware for bot"""
import traceback
from typing import Any, Dict

from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import Update, Message, CallbackQuery

from bot.core.logger import get_logger

logger = get_logger(__name__)


class LoggingMiddleware(BaseMiddleware):
    """Middleware to log all user actions and errors"""
    
    async def __call__(
        self,
        handler,
        event,
        data: Dict[str, Any],
    ) -> Any:
        user_id = None
        username = None
        chat_id = None
        chat_type = None
        action = None
        
        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
            username = event.from_user.username if event.from_user else None
            chat_id = event.chat.id
            chat_type = event.chat.type
            if event.text:
                action = f"message: {event.text[:100]}"
            elif event.caption:
                action = f"message with caption: {event.caption[:100]}"
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None
            username = event.from_user.username if event.from_user else None
            chat_id = event.message.chat.id if event.message else None
            chat_type = event.message.chat.type if event.message else None
            action = f"callback: {event.data}"
        elif isinstance(event, Update):
            if event.message:
                user_id = event.message.from_user.id if event.message.from_user else None
                username = event.message.from_user.username if event.message.from_user else None
                chat_id = event.message.chat.id
                chat_type = event.message.chat.type
                if event.message.text:
                    action = f"message: {event.message.text[:100]}"
                elif event.message.caption:
                    action = f"message with caption: {event.message.caption[:100]}"
            elif event.callback_query:
                user_id = event.callback_query.from_user.id if event.callback_query.from_user else None
                username = event.callback_query.from_user.username if event.callback_query.from_user else None
                chat_id = event.callback_query.message.chat.id if event.callback_query.message else None
                chat_type = event.callback_query.message.chat.type if event.callback_query.message else None
                action = f"callback: {event.callback_query.data}"
        
        if user_id:
            logger.info(f"USER ACTION | user_id={user_id} | username=@{username} | chat_id={chat_id} | chat_type={chat_type} | {action}")
        
        try:
            result = await handler(event, data)
            return result
        except Exception as e:
            error_msg = f"ERROR | user_id={user_id} | username=@{username} | chat_id={chat_id} | {action} | {type(e).__name__}: {str(e)}"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            raise

