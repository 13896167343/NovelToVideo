"""日志工具模块"""
import logging
import sys
import threading
from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener
from pathlib import Path
from queue import Queue

# 全局队列监听器
_queue_listener = None
_listener_thread = None


def _create_file_handlers():
    """
    创建文件处理器（不在线程中创建）
    """
    from config import DefaultConfig
    
    log_dir = Path(DefaultConfig.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
    )
    
    handlers = []
    
    file_handler = RotatingFileHandler(
        log_dir / 'app.log',
        maxBytes=DefaultConfig.LOG_MAX_BYTES,
        backupCount=DefaultConfig.LOG_BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    handlers.append(file_handler)
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    handlers.append(console_handler)
    
    error_handler = RotatingFileHandler(
        log_dir / 'error.log',
        maxBytes=DefaultConfig.LOG_MAX_BYTES,
        backupCount=DefaultConfig.LOG_BACKUP_COUNT,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    handlers.append(error_handler)
    
    return handlers


def _get_queue_listener():
    """
    获取或创建队列监听器（单例模式）
    确保所有日志记录器共享同一个队列监听器
    """
    global _queue_listener, _listener_thread
    
    if _queue_listener is not None:
        return _queue_listener
    
    handlers = _create_file_handlers()
    log_queue = Queue(-1)
    
    _queue_listener = QueueListener(log_queue, *handlers, respect_handler_level=True)
    _queue_listener.start()
    
    return _queue_listener


def get_logger(name):
    """
    获取日志记录器的便捷方法
    
    Args:
        name: 日志记录器名称
        
    Returns:
        配置好的日志记录器
    """
    return setup_logger(name)


def setup_logger(name='novel_to_video', log_level='INFO'):
    """
    设置日志记录器
    
    Args:
        name: 日志记录器名称
        log_level: 日志级别
        
    Returns:
        配置好的日志记录器
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level))
    
    if logger.handlers:
        return logger
    
    queue_listener = _get_queue_listener()
    queue_handler = QueueHandler(queue_listener.queue)
    queue_handler.setLevel(getattr(logging, log_level))
    
    logger.addHandler(queue_handler)
    logger.propagate = False
    
    return logger


def shutdown_logger():
    """
    关闭日志系统
    在程序退出前调用
    """
    global _queue_listener, _listener_thread
    
    if _queue_listener is not None:
        _queue_listener.stop()
        _queue_listener = None
        _listener_thread = None
