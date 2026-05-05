"""
Система умной ротации прокси с автоматическим переключением и статистикой.
Автоматически банит проблемные прокси и выбирает лучшие варианты.
"""

import random
import time
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from datetime import datetime, timedelta
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class ProxyStats:
    """
    Статистика по одному прокси.
    
    Attributes:
        proxy_url: URL прокси
        total_requests: Общее количество запросов
        success_requests: Количество успешных запросов
        failed_requests: Количество неудачных запросов
        captcha_triggers: Количество срабатываний капчи
        ban_triggers: Количество банов
        last_used: Время последнего использования
        banned_until: Время до которого прокси забанен
        consecutive_failures: Количество последовательных ошибок
    """
    proxy_url: str
    total_requests: int = 0
    success_requests: int = 0
    failed_requests: int = 0
    captcha_triggers: int = 0
    ban_triggers: int = 0
    last_used: Optional[datetime] = None
    banned_until: Optional[datetime] = None
    consecutive_failures: int = 0
    
    @property
    def success_rate(self) -> float:
        """
        Вычисляет процент успешных запросов.
        
        Returns:
            Процент успешных запросов (0.0 - 1.0)
        """
        if self.total_requests == 0:
            return 1.0
        return self.success_requests / self.total_requests
    
    @property
    def is_banned(self) -> bool:
        """
        Проверяет, забанен ли прокси в данный момент.
        
        Returns:
            True если прокси забанен, False если нет
        """
        if self.banned_until is None:
            return False
        return datetime.now() < self.banned_until
    
    @property
    def is_available(self) -> bool:
        """
        Проверяет, доступен ли прокси для использования.
        
        Returns:
            True если прокси доступен, False если забанен
        """
        return not self.is_banned
    
    def ban(self, duration_minutes: int = 15, reason: str = "unknown") -> None:
        """
        Временно банит прокси.
        
        Args:
            duration_minutes: Длительность бана в минутах
            reason: Причина бана
        """
        self.banned_until = datetime.now() + timedelta(minutes=duration_minutes)
        self.ban_triggers += 1
        logger.warning(
            f"🔴 Прокси {self._mask_proxy()} забанен на {duration_minutes} мин. "
            f"Причина: {reason}"
        )
    
    def record_success(self) -> None:
        """Записывает успешный запрос."""
        self.total_requests += 1
        self.success_requests += 1
        self.consecutive_failures = 0
        logger.debug(f"✓ Успешный запрос через {self._mask_proxy()}")
    
    def record_failure(self, error_type: str = "unknown") -> None:
        """
        Записывает неудачный запрос и применяет политику банов.
        
        Args:
            error_type: Тип ошибки (captcha, ban, 429, connection, etc.)
        """
        self.total_requests += 1
        self.failed_requests += 1
        self.consecutive_failures += 1
        
        logger.warning(
            f"✗ Ошибка через {self._mask_proxy()}: {error_type} "
            f"(подряд: {self.consecutive_failures})"
        )
        
        # Политика банов в зависимости от типа ошибки
        if error_type == "captcha":
            self.captcha_triggers += 1
            if self.captcha_triggers >= 2:
                self.ban(duration_minutes=30, reason="Частая капча")
        
        elif error_type in ("ban", "429", "403"):
            self.ban(duration_minutes=15, reason=f"HTTP {error_type}")
        
        elif error_type == "connection":
            # При ошибках соединения банить на меньшее время
            if self.consecutive_failures >= 3:
                self.ban(duration_minutes=5, reason="Проблемы с соединением")
        
        # Автобан при 5 ошибках подряд (любого типа)
        if self.consecutive_failures >= 5:
            self.ban(duration_minutes=60, reason="5 ошибок подряд")
    
    def _mask_proxy(self) -> str:
        """
        Маскирует пароль в URL прокси для безопасного логирования.
        
        Returns:
            Замаскированный URL прокси
        """
        try:
            parsed = urlparse(self.proxy_url)
            if parsed.username and parsed.password:
                masked_password = "*" * len(parsed.password)
                return f"{parsed.scheme}://{parsed.username}:{masked_password}@{parsed.hostname}:{parsed.port}"
            return self.proxy_url
        except Exception:
            return self.proxy_url[:20] + "..."


class ProxyManager:
    """
    Управляет пулом прокси с автоматической ротацией и статистикой.
    
    Особенности:
    - Автоматическое переключение при ошибках
    - Временный бан проблемных прокси
    - Статистика по каждому прокси
    - Выбор лучших прокси на основе статистики
    """
    
    def __init__(self, proxy_list: Optional[List[str]] = None):
        """
        Инициализация менеджера прокси.
        
        Args:
            proxy_list: Список прокси в формате ['http://user:pass@host:port', ...]
                       Если None или пустой список - работа без прокси
        """
        self.proxy_list = proxy_list or []
        self.stats: Dict[str, ProxyStats] = {}
        self.current_proxy: Optional[str] = None
        self.no_proxy_mode = not bool(proxy_list)
        
        # Инициализируем статистику для каждого прокси
        for proxy_url in self.proxy_list:
            self.stats[proxy_url] = ProxyStats(proxy_url=proxy_url)
        
        if self.no_proxy_mode:
            logger.warning("⚠️  ProxyManager работает БЕЗ прокси (режим no_proxy)")
        else:
            logger.info(f"✓ ProxyManager инициализирован с {len(self.proxy_list)} прокси")
    
    def get_proxy(self, worker_id: Optional[int] = None) -> Optional[Dict[str, str]]:
        """
        Возвращает доступный прокси в формате для Playwright.
        Автоматически пропускает забаненные прокси.
        
        Args:
            worker_id: ID воркера (для распределения прокси между воркерами)
        
        Returns:
            Словарь с параметрами прокси для Playwright или None
        """
        # Режим без прокси
        if self.no_proxy_mode:
            return None
        
        # Фильтруем доступные прокси
        available = [
            p for p in self.proxy_list
            if self.stats[p].is_available
        ]
        
        if not available:
            logger.error("🔴 Нет доступных прокси! Все забанены.")
            
            # Ждём ближайшего разбана
            min_ban_time = min(
                (s.banned_until for s in self.stats.values() if s.banned_until),
                default=None
            )
            
            if min_ban_time:
                wait_seconds = (min_ban_time - datetime.now()).total_seconds()
                if wait_seconds > 0:
                    logger.info(f"⏳ Ожидание разблокировки прокси: {wait_seconds:.0f} сек")
                    time.sleep(min(wait_seconds, 60))
                    # Рекурсивно пробуем снова после ожидания
                    return self.get_proxy(worker_id)
            
            return None
        
        # Если указан worker_id и есть достаточно прокси, привязываем воркер к прокси
        if worker_id is not None and len(available) > 1:
            # Используем worker_id для детерминированного выбора прокси
            # Это гарантирует, что разные воркеры получат разные прокси при старте
            proxy_index = (worker_id - 1) % len(available)
            best_proxy = available[proxy_index]
            logger.info(f"[Worker {worker_id}] Привязан к прокси #{proxy_index + 1}")
        else:
            # Если worker_id не указан или прокси мало, выбираем по статистике
            # Добавляем случайность для избежания коллизий при одинаковой статистике
            best_proxy = min(
                available,
                key=lambda p: (
                    self.stats[p].consecutive_failures,
                    -self.stats[p].success_rate,
                    random.random()  # Случайный фактор для разрешения коллизий
                )
            )
        
        self.current_proxy = best_proxy
        self.stats[best_proxy].last_used = datetime.now()
        
        logger.debug(f"🔄 Выбран прокси: {self.stats[best_proxy]._mask_proxy()}")
        
        # Парсим URL прокси для Playwright
        return self._parse_proxy_url(best_proxy)
    
    def _parse_proxy_url(self, proxy_url: str) -> Dict[str, str]:
        """
        Парсит URL прокси в формат Playwright.
        
        Args:
            proxy_url: URL прокси в формате http://user:pass@host:port
            
        Returns:
            Словарь с параметрами прокси для Playwright
        """
        try:
            parsed = urlparse(proxy_url)
            
            proxy_dict = {
                'server': f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
            }
            
            if parsed.username and parsed.password:
                proxy_dict['username'] = parsed.username
                proxy_dict['password'] = parsed.password
            
            return proxy_dict
            
        except Exception as e:
            logger.error(f"Ошибка парсинга прокси URL {proxy_url}: {e}")
            # Fallback: пробуем простой формат
            return {'server': proxy_url}
    
    def mark_success(self) -> None:
        """Отмечает успешный запрос через текущий прокси."""
        if self.current_proxy and self.current_proxy in self.stats:
            self.stats[self.current_proxy].record_success()
    
    def mark_failure(self, error_type: str = "unknown") -> None:
        """
        Отмечает неудачный запрос и при необходимости банит прокси.
        
        Args:
            error_type: Тип ошибки (captcha, ban, 429, 403, connection, etc.)
        """
        if self.current_proxy and self.current_proxy in self.stats:
            self.stats[self.current_proxy].record_failure(error_type)
    
    def switch_proxy(self, reason: str = "manual") -> None:
        """
        Принудительно переключает прокси.
        
        Args:
            reason: Причина переключения
        """
        if self.current_proxy:
            logger.info(f"🔄 Переключение прокси. Причина: {reason}")
            self.mark_failure(reason)
        
        # Следующий вызов get_proxy() вернёт другой прокси
        self.current_proxy = None
    
    def get_stats_report(self) -> str:
        """
        Возвращает детальный отчёт по всем прокси.
        
        Returns:
            Форматированная строка со статистикой
        """
        lines = ["=" * 70, "📊 СТАТИСТИКА ПРОКСИ", "=" * 70]
        
        if not self.stats:
            lines.append("Режим без прокси")
        else:
            # Сортируем по success_rate (лучшие сверху)
            sorted_stats = sorted(
                self.stats.items(),
                key=lambda x: x[1].success_rate,
                reverse=True
            )
            
            for proxy_url, stats in sorted_stats:
                status = "🟢" if stats.is_available else "🔴"
                masked_url = stats._mask_proxy()
                
                lines.append(
                    f"{status} {masked_url}"
                )
                lines.append(
                    f"   Запросов: {stats.total_requests} | "
                    f"Успешно: {stats.success_requests} | "
                    f"Ошибок: {stats.failed_requests}"
                )
                lines.append(
                    f"   Успешность: {stats.success_rate:.1%} | "
                    f"Капча: {stats.captcha_triggers} | "
                    f"Банов: {stats.ban_triggers}"
                )
                
                if stats.is_banned:
                    remaining = (stats.banned_until - datetime.now()).total_seconds() / 60
                    lines.append(f"   ⏳ Забанен ещё на {remaining:.1f} мин")
                
                lines.append("")
        
        lines.append("=" * 70)
        return "\n".join(lines)
    
    def get_short_stats(self) -> str:
        """
        Возвращает краткую статистику (для периодического вывода).
        
        Returns:
            Краткая строка со статистикой
        """
        if not self.stats:
            return "Режим без прокси"
        
        available_count = sum(1 for s in self.stats.values() if s.is_available)
        total_count = len(self.stats)
        
        total_requests = sum(s.total_requests for s in self.stats.values())
        total_success = sum(s.success_requests for s in self.stats.values())
        
        avg_success_rate = total_success / total_requests if total_requests > 0 else 0
        
        return (
            f"Прокси: {available_count}/{total_count} доступно | "
            f"Запросов: {total_requests} | "
            f"Успешность: {avg_success_rate:.1%}"
        )
